; Установщик Tegr.
;
; Раньше программа называлась TGPlayer. AppId оставлен прежним нарочно:
; по нему установщик узнаёт уже поставленную программу и обновляет её на
; месте. Смени его — и Windows завела бы вторую установку рядом с первой,
; а у человека остались бы два значка в меню «Пуск» и два ключа удаления.
;
; Права администратора запрашиваются ВСЕГДА.
;
; Раньше здесь стояло PrivilegesRequired=lowest: установка шла без UAC, а
; если прав не хватало, [Code] перезапускал установщик с повышением. На
; деле это давало сбои: на части систем запрос прав не доходил, и
; установщик молча закрывался, не создав даже временную папку. Диагностики
; никакой — просто «ничего не произошло».
;
; Теперь запрос прав один и сразу при запуске: Windows показывает обычное
; окно UAC, и после согласия установщик работает с полными правами. Так
; он может писать и в Program Files, и на другой диск, и в профиль.
;
; Что НЕ попадает в поставку: файл сессии Telegram, история просмотра,
; настройки и лог — это данные того компьютера, где собирался установщик.
; proxy.txt, наоборот, кладём: это шаблон с подсказками, без секретов.

; Имена должны совпадать с тем, что в tgplayer/core/version.py и куда
; собирает _build.ps1. Папка выпуска и имя установщика подставляются
; сборщиком (_build_inst.ps1) — здесь только значения по умолчанию.
#ifndef AppName
  #define AppName "Tegr"
#endif
#ifndef AppVersion
  #define AppVersion "3.1.0"
#endif
#ifndef AppExe
  #define AppExe "Tegr.exe"
#endif
#ifndef SrcDir
  #define SrcDir "H:\deepseek projects\tgplayer\dist\Tegr"
#endif

[Setup]
AppId={{8F3C1A64-2B7E-4D91-9C55-6E0A7B4D2F18}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
; Путь по умолчанию: Program Files для всех пользователей, когда установка
; идёт с правами администратора. {autopf} при таком режиме раскрывается
; именно туда — и теперь это работает, потому что права уже есть.
;
; Папка — старая, TGPlayer, хотя программа теперь называется Tegr. Так
; обновление ложится поверх прежней установки и втягивает новое имя: без
; этого в Program Files оказались бы две папки, старая с прошлой версией
; и новая, а старая так и оставалась бы рядом. После первого же
; обновления папка станет Tegr — она задаётся значением по умолчанию, и
; при следующей установке уже выбрана новая.
DefaultDirName={autopf}\TGPlayer
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Права администратора запрашиваются всегда. Это главное изменение:
; установщик не пытается работать без прав и не перезапускается потом.
PrivilegesRequired=admin
; Разрешаем менять папку: кеш видео занимает гигабайты, и человек вправе
; поставить программу на другой диск. Права на любую папку уже есть.
DisableDirPage=no
UsePreviousLanguage=no
; OutputDir задаёт _build_inst.ps1: установщик кладётся в releases\<версия>.
OutputBaseFilename={#AppName}_Setup
SetupIconFile=H:\deepseek projects\tgplayer\tgplayer.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Плеер держит этот мьютекс всё время работы: установщик по нему поймёт,
; что программа запущена, и не станет перезаписывать занятые файлы.
AppMutex=TGPlayerSingleInstance

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительно:"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; \
    Excludes: "my_player_session.session,*.session,*.session-journal,*.session-wal,*.session-shm,history.json,settings.json,tgplayer.log,stream_cache\*"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; Обычная установка: галочка «Запустить {#AppName}» на последней странице.
; В тихом режиме (обновление из самого плеера) этот пункт пропускается —
; вместо него работает [Code].
Filename: "{app}\{#AppExe}"; Description: "Запустить {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Кеш роликов может занимать гигабайты — при удалении его убираем.
; Настройки, историю и вход НЕ трогаем: после переустановки человек
; останется в своём аккаунте и с привычными настройками.
; Журнал тоже не удаляем: по нему разбирают сбои, и он нужен уже после
; того, как программу сняли.
Type: filesandordirs; Name: "{app}\stream_cache"

[Code]
var
  RunAfterUpdate: Boolean;

{ Запоминаем, что это обновление из плеера, ДО того как установщик начнёт
  работу.

  Порядок тут важен. Если спросить реестр в CurStepChanged при ssDone —
  последнем шаге установки, — он ответит, что программа установлена, но
  это уже будет запись от НОВОЙ версии, которую только что записали.
  Разницы между первой установкой и обновлением тогда нет. А в
  InitializeSetup реестр ещё хранит предыдущую версию: запись есть —
  значит, обновляемся.

  Тихий режим и есть признак обновления из плеера: обычный запуск
  двойным щелчком тихим не бывает. Вручную установленный в тихом режиме
  установщик тоже запустит плеер — это безвредно.

  Готовой функции «программа установлена» в Inno Setup нет, поэтому
  смотрим на файл удаления в папке программы: он появляется при первой
  установке и перезаписывается при каждой следующей. }
function InitializeSetup(): Boolean;
var
  UninstallKey: String;
begin
  // Обычно папка программы — {autopf}\TGPlayer (у тех, кто ещё не
  // обновился) или {autopf}\Tegr, но человек вправе поставить программу
  // куда угодно, поэтому вдобавок смотрим в реестр. Скобки в имени
  // раздела удваиваем: в Pascal Script они служебные, и без удвоения
  // ключ выходит несуществующим.
  UninstallKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\'
                  + '{{8F3C1A64-2B7E-4D91-9C55-6E0A7B4D2F18}}_is1';
  RunAfterUpdate := WizardSilent() and
                    (FileExists(ExpandConstant('{autopf}\TGPlayer\unins000.exe'))
                     or FileExists(ExpandConstant('{autopf}\Tegr\unins000.exe'))
                     or RegKeyExists(HKLM, UninstallKey));
  Log('RunAfterUpdate=' + IntToStr(Ord(RunAfterUpdate))
      + ' key=' + UninstallKey);
  Result := True;
end;

{ Запуск плеера после обновления.

  Делается здесь, а не через [Run] с флагом runasoriginaluser. При
  повышении прав установщик создаёт для этого невидимый процесс-
  посредник, который ждёт, пока установщик завершится, и только потом
  запускает программу. Обычно это работает, но без обратной связи: если
  посредник не сработал, со стороны это выглядит как «установилось и
  молчит». Прямой запуск из установщика работает всегда, а запуск от
  администратора плееру ничем не мешает: данные он хранит в профиле
  пользователя, а не рядом с собой. }
procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrCode: Integer;
  ExePath: String;
begin
  if CurStep <> ssDone then
    Exit;
  Log('ssDone: RunAfterUpdate=' + IntToStr(Ord(RunAfterUpdate)));
  if not RunAfterUpdate then
    Exit;
  ExePath := ExpandConstant('{app}\{#AppExe}');
  Log('Обновление из плеера: запускаю ' + ExePath);
  if Exec(ExePath, '', ExpandConstant('{app}'), SW_SHOWNORMAL, ewNoWait,
          ErrCode) then
    Log('Плеер запущен')
  else
    Log('Запуск не удался, код ' + IntToStr(ErrCode));
end;




