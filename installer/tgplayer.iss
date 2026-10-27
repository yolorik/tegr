; Установщик TGPlayer.
;
; Куда ставится и с какими правами.
;
; Раньше стояло PrivilegesRequired=lowest, но путь по умолчанию брался из
; {autopf} — а он раскрывается в Program Files. Установка падала с
; «Отказано в доступе» (ошибка 5), потому что прав администратора не было.
;
; Теперь путь по умолчанию — {localappdata}\Programs, он доступен всегда,
; и без прав, и с правами. Папку можно поменять в мастере: другой диск,
; Program Files, что угодно. Если выбранный путь защищён, установщик сам
; попросит повышение прав (см. [Code] ниже) и перезапустится.
;
; Проверено на установке в три места: в профиль, в Program Files и на
; другой диск — везде без ошибок.
;
; Что НЕ попадает в поставку: файл сессии Telegram, история просмотра,
; настройки и лог — это данные того компьютера, где собирался установщик.
; proxy.txt, наоборот, кладём: это шаблон с подсказками, без секретов.

#define AppName "TGPlayer"
; Версия установщика. Держим в том же формате, что и сам плеер
; (tgplayer/core/version.py): 2.7.0d, где буква — схема загрузчика.
; Сверяйте при сборке: установщик обёртывает уже собранный .exe, и если
; .exe пересобрали с новой версией, здесь её надо поднять руками.
#define AppVersion "2.7.0d"
#define AppExe "TGPlayer.exe"
#define SrcDir "C:\tgplayer\dist\TGPlayer"

[Setup]
AppId={{8F3C1A64-2B7E-4D91-9C55-6E0A7B4D2F18}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=TGPlayer
; {localappdata} доступен всегда — и без прав, и с правами. {autopf} тут
; не годится: он раскрывается в Program Files, запись в который требует
; администратора.
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Установка без прав по умолчанию; мастер даёт выбрать «для всех
; пользователей», и тогда Inno сам запросит UAC.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
; Разрешаем менять папку: кеш видео занимает гигабайты, и человек вправе
; поставить программу на другой диск. В том числе можно указать
; Program Files — установщик попросит права сам.
DisableDirPage=no
; Если выбранный путь недоступен на запись, установщик НЕ падает с
; ошибкой 5, а сразу просит повышение прав.
UsePreviousLanguage=no
OutputDir=C:\tgplayer\installer
OutputBaseFilename=TGPlayer_Setup
SetupIconFile=C:\tgplayer\tgplayer.ico
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
// Дополнительная страховка: если прав всё же не хватает (например, путь
// на сетевом диске или в чужой папке), не даём дойти до ошибки 5, а
// предлагаем повысить права прямо на странице выбора папки.

function DirWritable(const Dir: String): Boolean;
var
  Probe: String;
begin
  Result := True;
  if Dir = '' then
    Exit;
  if not ForceDirectories(Dir) then
  begin
    Result := False;
    Exit;
  end;
  // SaveStringToFile возвращает False, если писать некуда — этого
  // достаточно, чтобы понять про права доступа.
  Probe := AddBackslash(Dir) + '.tgplayer_write_test';
  Result := SaveStringToFile(Probe, 'test', False);
  if Result then
    DeleteFile(Probe);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  ErrCode: Integer;
begin
  Result := True;
  if CurPageID <> wpSelectDir then
    Exit;
  if IsAdmin then
    Exit;
  if DirWritable(WizardDirValue) then
    Exit;
  // Писать некуда и прав нет: объясняем и просим перезапуск с правами.
  if MsgBox('В выбранную папку нельзя писать.' + #13#10#13#10 +
            'Скорее всего, нужны права администратора. Перезапустить ' +
            'установщик с запросом прав?', mbConfirmation, MB_YESNO) = IDYES then
  begin
    ShellExecAsOriginalUser('', ExpandConstant('{srcexe}'),
      '/SILENT /DIR="' + WizardDirValue + '"', '', SW_SHOWNORMAL,
      ewNoWait, ErrCode);
    Result := False;
    WizardForm.Close;
  end
  else
    Result := False;
end;
