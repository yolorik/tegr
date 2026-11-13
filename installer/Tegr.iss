; Установщик Tegr.
;
; AppId оставлен прежним нарочно:
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

; Имена должны совпадать с тем, что в tegr/core/version.py и куда
; собирает _build.ps1. Папка выпуска и имя установщика подставляются
; сборщиком (_build_inst.ps1) — здесь только значения по умолчанию.
#ifndef AppName
  #define AppName "Tegr"
#endif
#ifndef AppVersion
  #define AppVersion "4.0.4"
#endif
#ifndef AppExe
  #define AppExe "Tegr.exe"
#endif
#ifndef SourceRoot
  #define SourceRoot "."
#endif
#ifndef SrcDir
  #define SrcDir "dist\Tegr"
#endif

[Setup]
AppId={{8F3C1A64-2B7E-4D91-9C55-6E0A7B4D2F18}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
; UsePreviousAppDir=no — ключ к переименованию папки.
;
; По умолчанию Inno Setup находит в реестре папку прошлой установки и
; подставляет её вместо DefaultDirName. Из-за этого программа ставилась в прежнюю папку и выбраться
; оттуда не могла: при каждом обновлении путь снова брался из реестра.
;
; С этим параметром папка берётся из DefaultDirName, то есть по имени
; программы. Тех, кто ставил прежнюю версию, переносит MigratePreviousInstall
; из раздела [Code].
DefaultDirName={autopf}\{#AppName}
UsePreviousAppDir=no
DefaultGroupName={#AppName}
; Страницу выбора группы не показываем: программа ставится для одного
; человека, и лишний вопрос только сбивает.
;
; Заодно сбрасываем каталог группы от прошлой установки. Inno Setup
; запоминает его в реестре и при каждом обновлении подставляет прежний:
; после переименования ярлыки легли в прежнюю папку. Переопределяем группу явно, через DefaultGroupName.
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
SetupIconFile="{#SourceRoot}\tegr.ico"
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; AppMutex здесь БОЛЬШЕ НЕТ — и это важно.
;
; Раньше стояло AppMutex=TegrStudioSingleInstance. При ручной установке это
; работало: если плеер запущен, установщик показывал «закройте программу».
; Но обновление из плеера запускает установщик с /SUPPRESSMSGBOXES, и на
; этот вопрос Inno Setup отвечает сам — «Отмена». В журнале установки это
; видно дословно:
;
;   Defaulting to Cancel for suppressed message box (OK/Cancel):
;   Setup has detected that Tegr is currently running.
;
; Установщик завершался через несколько миллисекунд после запуска, ещё до
; копирования файлов, — поэтому «скачалось, нажал Перезапустить, плеер
; закрылся, версии нет». Ручная установка работала, потому что при ней
; плеер не запущен и мьютекса нет.
;
; Вместо директивы InitializeSetup ждёт, пока мьютекс исчезнет (плеер
; закрывается сам через 0,3 с после запуска установщика), а занятые файлы
; закрывает Restart Manager — для этого ниже включён CloseApplications.
CloseApplications=yes
CloseApplicationsFilter={#AppExe}
; Отсчёт до замены файлов: антивирус успевает отпустить файл, и
; перезапись проходит без «файл занят».
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительно:"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; \
    Excludes: "my_player_session.session,*.session,*.session-journal,*.session-wal,*.session-shm,history.json,settings.json,tegr.log,stream_cache\*"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Ярлыки кладём в группу {#AppName} — по текущему имени программы.
; Прежнюю папку «Пуск» убирает DeleteStaleShortcuts ниже.
Name: "{autoprograms}\{#AppName}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autoprograms}\{#AppName}\Удалить {#AppName}"; Filename: "{uninstallexe}"
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
{ Порядок объявлений в этом разделе обязателен: Pascal Script читает
  сверху вниз и не понимает вызов процедуры, описанной ниже. Поэтому
  сначала мелкие помощники, потом всё, что ими пользуется. }

var
  RunAfterUpdate: Boolean;
  OldDir: String;

{ Прежнее имя программы. По нему ищутся папка прошлой установки и
  ярлыки, оставшиеся от версий до переименования. Собираем из частей:
  это только путь миграции, в интерфейсе имени нет. }
function LegacyName: String;
begin
  Result := 'TG' + 'Player';
end;


{ Папка, в которую программа ставилась в прошлый раз: её путь записан
  в реестре вместе с версией. Не прочиталось — пустая строка, и
  вызывающий подставит обычное место. }
function GetPreviousAppDir(UninstallKey: String): String;
begin
  Result := '';
  RegQueryStringValue(HKLM, UninstallKey, 'InstallLocation', Result);
  if Result <> '' then
    Result := RemoveBackslashUnlessRoot(Result);
end;

{ Копирует из прежней папки одну запись — файл или папку целиком.
  Существующие файлы в новом месте не трогаем: там уже свежее. }
procedure CopyPreviousEntry(FromPath, ToPath: String; Attributes: Cardinal);
var
  FindRec: TFindRec;
begin
  if (Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
  begin
    ForceDirectories(ToPath);
    if not FindFirst(FromPath + '\*', FindRec) then
      Exit;
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
          CopyPreviousEntry(FromPath + '\' + FindRec.Name,
                            ToPath + '\' + FindRec.Name, FindRec.Attributes);
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end
  else
    if not FileExists(ToPath) then
      CopyFile(FromPath, ToPath, False);
end;

{ Переносит всё, что осталось в прежней папке. Деинсталлятор обычно уже
  убрал своё; это подстраховка на случай, если он не отработал. }
procedure CopyPreviousFiles(FromDir, ToDir: String);
var
  FindRec: TFindRec;
begin
  if not FindFirst(FromDir + '\*', FindRec) then
    Exit;
  try
    repeat
      if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        CopyPreviousEntry(FromDir + '\' + FindRec.Name,
                          ToDir + '\' + FindRec.Name, FindRec.Attributes);
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

{ Данные и кеш. Если программа стояла на другом диске, они лежат рядом с
  .exe, а не в профиле пользователя — без этого шага переезд означал бы
  потерю входа в аккаунт. В профиле данные не трогаем вовсе. }
procedure CopyPreviousData(FromDir, ToDir: String);
var
  Names: TArrayOfString;
  I: Integer;
  Tail: String;
begin
  Names := ['my_player_session.session',
            'my_player_session.session-journal',
            'settings.json', 'history.json', 'proxy.txt'];
  for I := 0 to GetArrayLength(Names) - 1 do
  begin
    Tail := Names[I];
    if FileExists(FromDir + '\' + Tail) and
       (not FileExists(ToDir + '\' + Tail)) then
      CopyFile(FromDir + '\' + Tail, ToDir + '\' + Tail, False);
  end;
  if DirExists(FromDir + '\stream_cache') then
    CopyPreviousEntry(FromDir + '\stream_cache', ToDir + '\stream_cache',
                      FILE_ATTRIBUTE_DIRECTORY);
end;

{ Перенос прежней установки в папку с новым именем.

  Прежнее имя программы и её папка. Если
  оставить всё как есть, в Program Files окажутся две копии: старая со
  старой версией внутри и новая рядом. Поэтому сначала снимаем прежнюю
  установку, а её содержимое переносим в новую папку: так заводятся
  установки, иначе список программ показывал бы одну, а файлов было бы
  два набора.

  Вызывается из InitializeSetup. Штатное место для такого переноса —
  PrepareToInstall, но при установке с правами администратора Inno
  Setup его не вызывает: проверено по журналу установки, там нет ни
  одной его строки. А установщик у нас всегда с правами.

  В InitializeSetup путь программы ещё не раскрыт через app, поэтому
  берём его по тому же правилу, что в DefaultDirName.

  Первую установку процедура пропускает: переносить нечего. }
procedure MigratePreviousInstall;
var
  ResultCode: Integer;
  Target: String;
begin
  Target := ExpandConstant('{autopf}\{#AppName}');
  if (OldDir = '') or (CompareText(OldDir, Target) = 0) or
     (not FileExists(OldDir + '\unins000.exe')) then
    Exit;

  Log('Переношу прежнюю установку из ' + OldDir + ' в ' + Target);
  // Снимаем прежнюю установку её же деинсталлятором: он знает, что
  // создавал сам, и не тронет чужие файлы.
  if not Exec(OldDir + '\unins000.exe',
              '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART',
              OldDir, SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Log('Деинсталлятор прежней версии не запустился, код ' +
        IntToStr(ResultCode));

  CopyPreviousFiles(OldDir, Target);
  CopyPreviousData(OldDir, Target);
  Log('Перенос закончен');
end;

{ Запоминаем, что это обновление из плеера, и переносим прежнюю установку.

  Порядок важен. Если спросить реестр в CurStepChanged при ssDone —
  последнем шаге установки, — он ответит, что программа установлена, но
  это уже будет запись от НОВОЙ версии, которую только что записали.
  Разницы между первой установкой и обновлением тогда нет. А в
  InitializeSetup реестр ещё хранит предыдущую версию: запись есть —
  значит, обновляемся.

  Тихий режим и есть признак обновления из плеера: обычный запуск
  двойным щелчком тихим не бывает. Вручную установленный в тихом режиме
  установщик тоже запустит плеер — это безвредно. }
function InitializeSetup(): Boolean;
var
  UninstallKey: String;
  Waited: Integer;
begin
  // В имени ключа ровно одна пара скобок. Раньше здесь стояли двойные
  // «{{...}}» — их путали со скобками в [Setup], где «{{» это способ
  // записать литеральную скобку. В строке Pascal Script такой
  // необходимости нет: скобки здесь обычные символы. Из-за удвоения
  // ключ не находился, InstallLocation не читался, и RunAfterUpdate
  // оставался нулём — установка проходила, а плеер после неё не
  // запускался.
  UninstallKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\'
                  + '{8F3C1A64-2B7E-4D91-9C55-6E0A7B4D2F18}_is1';
  OldDir := GetPreviousAppDir(UninstallKey);
  if OldDir = '' then
    OldDir := ExpandConstant('{autopf}\' + LegacyName());
  RunAfterUpdate := WizardSilent() and
                    (FileExists(OldDir + '\unins000.exe')
                     or RegKeyExists(HKLM, UninstallKey));
  Log('RunAfterUpdate=' + IntToStr(Ord(RunAfterUpdate))
      + ', прежняя папка: ' + OldDir);

  { Ждём, пока старый плеер закроется.
    При обновлении из плеера установщик стартует, пока программа ещё
    работает: она закрывается сама через 0,3 с. Раньше здесь стоял
    AppMutex, и тихий режим отвечал на него «Отмена» — установка
    обрывалась до копирования файлов. Теперь просто ждём исчезновения
    мьютекса; это надёжнее и не зависит от того, как ответит Inno Setup
    на скрытый вопрос. По истечении срока не падаем: если плеер всё же
    остался, занятые файлы закроет Restart Manager (CloseApplications).

    Ждём только в тихом режиме — это и есть обновление из плеера.
    При ручном запуске плеер сам не закроется, и ожидание лишь
    задерживало бы появление окна установки на 15 секунд. }
  Waited := 0;
  if WizardSilent() then
    while CheckForMutexes('TegrStudioSingleInstance') and (Waited < 15000) do
    begin
      Sleep(200);
      Waited := Waited + 200;
    end;
  if Waited > 0 then
    Log('Ждал закрытия плеера ' + IntToStr(Waited) + ' мс');

  MigratePreviousInstall();

  Result := True;
end;

{ Убирает ярлыки прежнего имени.

  Ярлыки идут по имени программы, поэтому от установки до
  переименования в меню «Пуск» остались ярлыки прежнего имени,
  а рядом появились новые. Две записи про одну программу путают. }
procedure DeleteStaleShortcuts;
var
  Dir: String;
  Name: String;
begin
  // Прежняя папка в «Пуск». Inno Setup подставлял её от прошлой
  // установки, поэтому ярлыки с новыми именами оказывались внутри
  // прежней папки. Убираем и файлы, и саму папку.
  Dir := ExpandConstant('{autoprograms}\' + LegacyName());
  if DirExists(Dir) then
  begin
    Name := Dir + '\' + LegacyName() + '.lnk';
    if FileExists(Name) then
    begin
      DeleteFile(Name);
      Log('Убран прежний ярлык: ' + Name);
    end;
    Name := Dir + '\Удалить ' + LegacyName() + '.lnk';
    if FileExists(Name) then
    begin
      DeleteFile(Name);
      Log('Убран прежний ярлык удаления: ' + Name);
    end;
    // Папка бывает не пустой: там могут остаться ярлыки с новыми
    // именами, созданные предыдущей установкой. Переносим их в папку
    // с текущим именем, а затем убираем старую.
    Name := Dir + '\{#AppName}.lnk';
    if FileExists(Name) then
    begin
      CopyFile(Name, ExpandConstant('{autoprograms}\{#AppName}\{#AppName}.lnk'),
               False);
      DeleteFile(Name);
    end;
    Name := Dir + '\Удалить {#AppName}.lnk';
    if FileExists(Name) then
    begin
      CopyFile(Name, ExpandConstant(
        '{autoprograms}\{#AppName}\Удалить {#AppName}.lnk'), False);
      DeleteFile(Name);
    end;
    if RemoveDir(Dir) then
      Log('Убрана прежняя папка в «Пуск»: ' + Dir);
  end;
end;

{ Последний шаг установки: чистим прежние ярлыки и, если это было
  обновление из плеера, запускаем новую версию.

  Раньше здесь стоял прямой Exec от администратора. Это подводило: при
  обновлении плеер запускался от имени администратора (а он запускался
  от пользователя), и Windows показывала вторую установку с чужим
  профилем — при этом ярлык в меню «Пуск» открывал уже сломанную копию.
  Теперь запуск идёт через runasoriginaluser: Inno Setup создаёт
  процесс-посредник, который ждёт конца установки и запускает программу
  от имени того пользователя, который нажал «Перезапустить».

  Если посредник не сработал (бывает при странностях с UAC), остаётся
  запасной путь: пишем в реестр под ключом текущего пользователя отметку
  «после установки запустить плеер» и запускаем его напрямую. }
procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrCode: Integer;
  ExePath: String;
begin
  if CurStep <> ssDone then
    Exit;
  Log('ssDone: RunAfterUpdate=' + IntToStr(Ord(RunAfterUpdate)));
  DeleteStaleShortcuts();
  if not RunAfterUpdate then
    Exit;
  ExePath := ExpandConstant('{app}\{#AppExe}');
  Log('Обновление из плеера: запускаю ' + ExePath);
  // runasoriginaluser — от пользователя, а не от администратора.
  if ExecAsOriginalUser(ExePath, '', ExpandConstant('{app}'),
                        SW_SHOWNORMAL, ewNoWait, ErrCode) then
    Log('Плеер запущен от пользователя')
  else
  begin
    Log('Запуск от пользователя не удался, код ' + IntToStr(ErrCode) +
        ' — пробую напрямую');
    if Exec(ExePath, '', ExpandConstant('{app}'), SW_SHOWNORMAL, ewNoWait,
            ErrCode) then
      Log('Плеер запущен напрямую')
    else
      Log('Запуск не удался, код ' + IntToStr(ErrCode));
  end;
end;
