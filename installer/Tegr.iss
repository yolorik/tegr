; Установщик Tegr — оформление в стилистике плеера.
;
; Главное отличие от прежней версии: НИКАКИХ имитированных кликов.
; Кнопки «Установить» и «Отмена» — это штатные NextButton и CancelButton
; мастера, только перенесённые в нашу раскладку. Поле пути — тоже штатный
; DirEdit, перенесённый на своё окно. Поэтому клик мыши попадает прямо в
; настоящий контрол, и установка начинается с первого нажатия.
;
; Раньше кнопка была своя, а нажатие пересылалось скрытой штатной кнопке
; сообщением BM_CLICK. Сообщение обрабатывается позже, когда контрол уже
; снова спрятан, — Inno его игнорировал, и человек нажимал по нескольку
; раз. Отрицательные координаты и смена слоёв усугубляли потерю кликов.

#ifndef AppName
  #define AppName "Tegr"
#endif
#ifndef AppVersion
  #define AppVersion "4.1.2"
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
DefaultDirName={autopf}\{#AppName}
UsePreviousAppDir=no
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
DisableDirPage=no
UsePreviousLanguage=no
OutputBaseFilename={#AppName}_Setup
SetupIconFile="{#SourceRoot}\tegr.ico"
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
CloseApplicationsFilter={#AppExe}
RestartApplications=no
; Лишние страницы мастера не показываем: после выбора папки сразу
; начинается копирование. Заодно убирает промежуточные «Задачи» и
; «Готово к установке».
DisableReadyPage=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительно:"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; \
    Excludes: "my_player_session.session,*.session,*.session-journal,*.session-wal,*.session-shm,history.json,settings.json,tegr.log,stream_cache\*"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autoprograms}\{#AppName}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

; Пункта [Run] здесь НЕТ нарочно. Сочетание postinstall + наш запуск давало
; двойной старт: Inno сам выполнял запись при завершении мастера, и плеер
; открывался даже по кнопке «Выход». Запуск делает наша логика: RunClick
; («Запустить Tegr») при обычной установке и CurStepChanged(ssDone) при
; тихом обновлении из плеера. Хук NextButtonClick на финальной странице
; оставлен вторым путём — с флагом PlayerLaunched, чтобы не было двух стартов.

[UninstallDelete]
Type: filesandordirs; Name: "{app}\stream_cache"


[Code]
const
  WIN_W = 620;
  WIN_H = 230;              { окно низкое: заголовок, текст, поле пути,
                              линия хода и кнопки помещаются в два ряда }
  HEAD_H = 34;              { высота своей шапки }
  PAD = 22;                 { поля слева и справа }
  BTN_H = 34;               { высота кнопок }

var
  UiBg, UiPanel, UiText, UiDim, UiField, UiBorder, UiAccent: TColor;
  HeaderPanel, TrackPanel, FillPanel: TPanel;
  VerLab, HeadLab, SubLab, StepLab, ErrLab: TLabel;
  DeskChk: TCheckBox;
  AnimTimerId: Longword;
  AnimPos, AnimTarget: Integer;
  OldDir: String;
  RunAfterUpdate: Boolean;
  WantRun: Boolean;
  SkipRun: Boolean;
  WasSilent: Boolean;
  { Штатный обработчик NextButton, снятый до подмены. Именно он
    закрывает мастер по нажатию «Запустить Tegr»; его же зовёт «Выход»,
    чтобы обе кнопки закрывались одинаково. }
  NextOrig: TNotifyEvent;
  NextCaptured: Boolean;
  PlayerLaunched: Boolean;

{ ==================================================================
  Цвета
  ================================================================== }

{ Наложение цвета To на From с долей T (0..256). Считаем один раз:
  полупрозрачные слои в Qt копились, и заливка светлела от касания к
  касанию. Здесь та же ошибка была бы видна сразу. }
function MixColor(FromColor, ToColor: TColor; T: Integer): TColor;
var
  FR, FG, FB, TR, TG, TB: Integer;
begin
  if T < 0 then T := 0;
  if T > 256 then T := 256;
  FR := FromColor and $FF;
  FG := (FromColor shr 8) and $FF;
  FB := (FromColor shr 16) and $FF;
  TR := ToColor and $FF;
  TG := (ToColor shr 8) and $FF;
  TB := (ToColor shr 16) and $FF;
  Result := TColor(
    (FR + ((TR - FR) * T) div 256)
    or ((FG + ((TG - FG) * T) div 256) shl 8)
    or ((FB + ((TB - FB) * T) div 256) shl 16));
end;

{ '#RRGGBB' -> TColor (Windows хранит цвет как $BBGGRR). }
function HexColor(H: String; Fallback: TColor): TColor;
var
  R, G, B: Integer;
begin
  Result := Fallback;
  if Length(H) < 6 then
    Exit;
  R := StrToIntDef('$' + Copy(H, 1, 2), -1);
  G := StrToIntDef('$' + Copy(H, 3, 2), -1);
  B := StrToIntDef('$' + Copy(H, 5, 2), -1);
  if (R < 0) or (G < 0) or (B < 0) then
    Exit;
  Result := TColor(R or (G shl 8) or (B shl 16));
end;

{ Палитра: «Графит» — тёмная, «Бумага» — светлая. Промежуточные темы
  плеера выглядят уместно в одном из двух видов. }
procedure LoadPalette(Dark: Boolean);
begin
  if Dark then
  begin
    UiBg := HexColor('101214', $000000);
    UiPanel := HexColor('171A1D', $000000);
    UiField := HexColor('1F2428', $000000);
    UiBorder := HexColor('2B3137', $606060);
    UiText := HexColor('EDE9E2', clWhite);
    UiDim := HexColor('8E8A82', clGray);
    UiAccent := HexColor('F2B84B', clYellow);
  end
  else
  begin
    UiBg := HexColor('F5F2EC', clWhite);
    UiPanel := HexColor('FFFDF9', clWhite);
    UiField := HexColor('FAF6EF', clWhite);
    UiBorder := HexColor('E7DFD2', clSilver);
    UiText := HexColor('211D19', clBlack);
    UiDim := HexColor('82796F', clGray);
    UiAccent := HexColor('E4572E', clRed);
  end;
end;

{ Какую тему выбрал человек в плеере: читаем settings.json рядом с уже
  установленной программой. Файла нет — берём светлую, она же по
  умолчанию в плеере. }
function PlayerPrefersDark: Boolean;
var
  Json: AnsiString;
  P: Integer;
  Tail: String;
begin
  Result := False;
  if not LoadStringFromFile(OldDir + '\settings.json', Json) then
    Exit;
  P := Pos('"theme"', Json);
  if P = 0 then
    Exit;
  Tail := Copy(Json, P, 200);
  if Pos('graphite', Tail) > 0 then
    Result := True;
end;

{ ==================================================================
  Анимация полосы

  В Pascal Script нет TTimer, поэтому берём системный SetTimer: 16 мс —
  примерно 60 кадров в секунду.
  ================================================================== }

function SetTimer(hWnd, nIDEvent, uElapse, lpTimerFunc: Longword): Longword;
external 'SetTimer@user32.dll stdcall';
function KillTimer(hWnd, nIDEvent: Longword): Bool;
external 'KillTimer@user32.dll stdcall';

procedure AnimStop; forward;
procedure AnimStep; forward;
procedure LayoutFor(PageID: Integer); forward;
procedure HidePageChildren(Page: TNewNotebookPage); forward;
procedure LaunchPlayer; forward;
procedure CloseWizard; forward;
procedure RunClick(Sender: TObject); forward;
procedure ExitClick(Sender: TObject); forward;

procedure AnimStop;
begin
  if AnimTimerId <> 0 then
  begin
    KillTimer(0, AnimTimerId);
    AnimTimerId := 0;
  end;
end;

{ Полоса едет к цели по четверти остатка: движение замедляется к концу,
  как сглаживание в плеере. }
procedure AnimStep;
var
  Delta: Integer;
begin
  if (FillPanel = nil) or (TrackPanel = nil) then
    Exit;
  Delta := AnimTarget - AnimPos;
  if Delta = 0 then
  begin
    if AnimTarget >= 256 then
      AnimStop;
    Exit;
  end;
  AnimPos := AnimPos + (Delta div 4);
  if Delta > 0 then
    AnimPos := AnimPos + 1;
  FillPanel.Width := TrackPanel.Width * AnimPos div 256;
end;

procedure AnimTickProc(hwnd, msg, idEvent, dwTime: Longword);
begin
  AnimStep;
end;

procedure AnimTo(Target: Integer);
begin
  AnimTarget := Target;
  if Target < AnimPos then
    AnimPos := Target;
  if AnimTimerId = 0 then
    AnimTimerId := SetTimer(0, 0, 16, CreateCallback(@AnimTickProc));
end;

{ Ход установки 0..256. Ширину ставим сразу по факту, а плавность даёт
  таймер: полоса не зависит от того, успел ли сработать callback. }
procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
begin
  if (MaxProgress <= 0) or (TrackPanel = nil) then
    Exit;
  AnimTo(CurProgress * 256 div MaxProgress);
  FillPanel.Width := TrackPanel.Width * CurProgress div MaxProgress;
  TrackPanel.Update;
end;

{ ==================================================================
  Раскладка
  ================================================================== }

{ Прячем всё штатное, кроме перенесённых себе контролов. }
procedure HideWizardChrome;
var
  I: Integer;
begin
  { Сами страницы мастера: их белый фон и был причиной неровного цвета.
    Содержимое страниц сюда не входит — оно нужно логике установки. }
  WizardForm.OuterNotebook.Visible := False;
  WizardForm.InnerNotebook.Visible := False;
  WizardForm.PageNameLabel.Visible := False;
  WizardForm.PageDescriptionLabel.Visible := False;
  WizardForm.WizardSmallBitmapImage.Visible := False;
  WizardForm.Bevel.Visible := False;
  WizardForm.BeveledLabel.Visible := False;
  WizardForm.BackButton.Visible := False;
  WizardForm.ProgressGauge.Visible := False;
  WizardForm.StatusLabel.Visible := False;
  WizardForm.FilenameLabel.Visible := False;
  { Надписи и картинки внутри страниц: имена зависят от версии Inno, }
  for I := 0 to WizardForm.ComponentCount - 1 do
    if WizardForm.Components[I] is TNewNotebookPage then
      HidePageChildren(TNewNotebookPage(WizardForm.Components[I]));
end;

{ Прячет содержимое страницы, кроме перенесённых контролов. }
procedure HidePageChildren(Page: TNewNotebookPage);
var
  J: Integer;
  C: TControl;
begin
  for J := 0 to Page.ControlCount - 1 do
  begin
    C := Page.Controls[J];
    if (C = WizardForm.DirEdit) or (C = WizardForm.DirBrowseButton) then
      Continue;
    C.Visible := False;
  end;
end;

{ Наша шапка: тонкая панель, версия и заголовок. }
procedure StyleHeader;
begin
  HeaderPanel.Color := UiBg;
  VerLab.Font.Color := UiDim;
  HeadLab.Font.Color := UiText;
  SubLab.Font.Color := UiDim;
  StepLab.Font.Color := UiDim;
  ErrLab.Font.Color := HexColor('D9534F', clRed);
  DeskChk.Font.Color := UiText;
  DeskChk.Color := UiBg;
  WizardForm.Color := UiBg;
end;

{ Раскладка под страницу мастера. Кнопки и поле пути — штатные контролы:
  двигаем их, а не подменяем собой. }
procedure LayoutFor(PageID: Integer);
var
  BtnY: Integer;
begin
  BtnY := WIN_H - PAD - BTN_H;

  { Кнопки: «Установить» справа, «Отмена» слева от неё. }
  with WizardForm.NextButton do
  begin
    SetBounds(WIN_W - PAD - 150, BtnY, 150, BTN_H);
    Enabled := True;
  end;
  with WizardForm.CancelButton do
  begin
    SetBounds(WIN_W - PAD - 150 - 12 - 116, BtnY, 116, BTN_H);
    Enabled := True;
  end;

  { Поле пути и «Обзор» — только на странице выбора папки, и только
    пока идёт выбор: во время копирования они не нужны. }
  if PageID = wpSelectDir then
  begin
    WizardForm.DirEdit.Visible := True;
    WizardForm.DirBrowseButton.Visible := True;
    WizardForm.DirEdit.SetBounds(PAD, HEAD_H + 80, WIN_W - PAD * 2 - 104, 26);
    WizardForm.DirBrowseButton.SetBounds(WIN_W - PAD - 96, HEAD_H + 80, 96, 26);
    WizardForm.DirEdit.BringToFront;
    WizardForm.DirBrowseButton.BringToFront;
    WizardForm.NextButton.Caption := 'Установить';
    WizardForm.NextButton.Width := 150;
    WizardForm.NextButton.Left := WIN_W - PAD - 150;
    WizardForm.NextButton.Visible := True;
    WizardForm.CancelButton.Caption := 'Отмена';
    WizardForm.CancelButton.Visible := True;
    HeadLab.Caption := 'Установка {#AppName}';
    SubLab.Caption := 'Видеоплеер для Telegram. Программа поставится ' +
                      'и сразу будет готова к работе.';
    StepLab.Caption := 'Папка установки';
  end
  else
  begin
    WizardForm.DirEdit.Visible := False;
    WizardForm.DirBrowseButton.Visible := False;
  end;

  { Ход установки. }
  if PageID = wpInstalling then
  begin
    HeadLab.Caption := 'Ставим {#AppName}';
    SubLab.Caption := 'Копируем файлы. Это займёт несколько секунд.';
    StepLab.Caption := '';
    WizardForm.NextButton.Visible := False;
    WizardForm.CancelButton.Caption := 'Отмена';
    WizardForm.CancelButton.Visible := True;
    { Во время установки «Отмена» стоит у правого края — с тем же
      отступом, что и другие кнопки. }
    WizardForm.CancelButton.SetBounds(WIN_W - PAD - 116, BtnY, 116, BTN_H);
    WizardForm.CancelButton.BringToFront;
    TrackPanel.Visible := True;
    FillPanel.Visible := True;
    TrackPanel.BringToFront;
    FillPanel.BringToFront;
  end
  { Готово: используем ШТАТНЫЕ кнопки Inno (NextButton и CancelButton):
    реальные клики мыши доходят до них, а до самодельных TNewButton —
    почему-то нет. «Выход» — это CancelButton со своим рабочим
    обработчиком Inno. }
  else if PageID = wpFinished then
  begin
    HeadLab.Caption := '{#AppName} установлен';
    SubLab.Caption := '';
    StepLab.Caption := '';
    WizardForm.NextButton.Visible := True;
    WizardForm.NextButton.Caption := 'Запустить {#AppName}';
    WizardForm.NextButton.SetBounds(WIN_W - PAD - 200, BtnY, 200, BTN_H);
    WizardForm.NextButton.BringToFront;
    WizardForm.CancelButton.Visible := True;
    WizardForm.CancelButton.Caption := 'Выход';
    WizardForm.CancelButton.SetBounds(WIN_W - PAD - 200 - 12 - 100, BtnY, 100, BTN_H);
    WizardForm.CancelButton.Visible := True;
    WizardForm.CancelButton.Enabled := True;
    WizardForm.CancelButton.BringToFront;
    TrackPanel.Visible := True;
    FillPanel.Visible := True;
    TrackPanel.BringToFront;
    FillPanel.BringToFront;
  end
  else
  begin
    TrackPanel.Visible := False;
    FillPanel.Visible := False;
  end;

  DeskChk.Visible := PageID = wpSelectDir;
  WizardForm.NextButton.BringToFront;
  WizardForm.CancelButton.BringToFront;
end;

{ ==================================================================
  Логика установки (перенесена без изменений)
  ================================================================== }

function LegacyName: String;
begin
  Result := 'TG' + 'Player';
end;

function GetPreviousAppDir(UninstallKey: String): String;
begin
  Result := '';
  RegQueryStringValue(HKLM, UninstallKey, 'InstallLocation', Result);
  if Result <> '' then
    Result := RemoveBackslashUnlessRoot(Result);
end;

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

procedure CopyPreviousData(FromDir, ToDir: String);
var
  Names: TArrayOfString;
  I: Integer;
begin
  Names := ['my_player_session.session',
            'my_player_session.session-journal',
            'settings.json', 'history.json', 'proxy.txt'];
  for I := 0 to GetArrayLength(Names) - 1 do
  begin
    if FileExists(FromDir + '\' + Names[I]) and
       (not FileExists(ToDir + '\' + Names[I])) then
      CopyFile(FromDir + '\' + Names[I], ToDir + '\' + Names[I], False);
  end;
  if DirExists(FromDir + '\stream_cache') then
    CopyPreviousEntry(FromDir + '\stream_cache', ToDir + '\stream_cache',
                      FILE_ATTRIBUTE_DIRECTORY);
end;

{ Перенос прежней установки в папку с новым именем. Вызывается из
  InitializeSetup: при установке с правами администратора Inno не
  вызывает PrepareToInstall. }
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
  if not Exec(OldDir + '\unins000.exe',
              '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART',
              OldDir, SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Log('Деинсталлятор прежней версии не запустился, код ' +
        IntToStr(ResultCode));

  CopyPreviousFiles(OldDir, Target);
  CopyPreviousData(OldDir, Target);
  Log('Перенос закончен');
end;

function InitializeSetup(): Boolean;
var
  UninstallKey: String;
  Waited: Integer;
begin
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

  { Ждём, пока закрывается плеер при обновлении из него. Мьютекс вместо
    AppMutex: AppMutex в тихом режиме отвечал «Отмена» и установка
    обрывалась до копирования. }
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

procedure DeleteStaleShortcuts;
var
  Dir, Name: String;
begin
  Dir := ExpandConstant('{autoprograms}\' + LegacyName());
  if not DirExists(Dir) then
    Exit;
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

{ Последний шаг установки: чистим прежние ярлыки. Запуск из плеера
  (тихое обновление) делаем здесь же — ждать финальной страницы нельзя,
  при тихой установке её нет. При обычной установке плеер запускает
  кнопка «Запустить Tegr» на финальной странице. }
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssDone then
    Exit;
  Log('ssDone: RunAfterUpdate=' + IntToStr(Ord(RunAfterUpdate)));
  DeleteStaleShortcuts();
  if RunAfterUpdate then
  begin
    Log('Обновление из плеера: запускаю после установки');
    LaunchPlayer;
  end;
end;

{ ==================================================================
  Хуки мастера
  ================================================================== }

{ Включает задачу установки по её имени. Обращение по номеру роняло
  установку: состав задач в разных сборках разный. }
procedure SetTask(TaskName: String; Checked: Boolean);
var
  I: Integer;
begin
  for I := 0 to WizardForm.TasksList.Items.Count - 1 do
  begin
    if Pos(Lowercase(TaskName),
           Lowercase(WizardForm.TasksList.Items[I])) > 0 then
    begin
      WizardForm.TasksList.Checked[I] := Checked;
      Exit;
    end;
  end;
end;

{ Запускает плеер от имени пользователя: от администратора он получил бы
  чужой профиль. Не вышло — пробуем напрямую. }
procedure LaunchPlayer;
var
  ErrCode: Integer;
  ExePath: String;
  Dir: String;
  R1, R2, R3: Boolean;
begin
  { Запуск ровно один: «Запустить» и хук NextButtonClick на финальной
    странице могут сработать оба, второй старт не нужен. }
  if PlayerLaunched then
  begin
    Log('LaunchPlayer: уже запускали, повтор пропущен');
    Exit;
  end;
  PlayerLaunched := True;

  Dir := ExpandConstant('{app}');
  ExePath := Dir + '\{#AppExe}';
  R1 := ExecAsOriginalUser(ExePath, '', Dir, SW_SHOWNORMAL, ewNoWait, ErrCode);
  if R1 then
    Log('Плеер запущен от пользователя')
  else
  begin
    Log('ExecAsOriginalUser не удался, код ' + IntToStr(ErrCode));
    R2 := ShellExecAsOriginalUser('open', ExePath, '', Dir,
                                  SW_SHOWNORMAL, ewNoWait, ErrCode);
    if R2 then
      Log('Плеер запущен через ShellExecAsOriginalUser')
    else
    begin
      Log('ShellExecAsOriginalUser не удался, код ' + IntToStr(ErrCode));
      R3 := Exec(ExePath, '', Dir, SW_SHOWNORMAL, ewNoWait, ErrCode);
      if R3 then
        Log('Плеер запущен напрямую')
      else
        Log('Запуск не удался, код ' + IntToStr(ErrCode));
    end;
  end;
end;

{ Единственная функция закрытия мастера. Зовём штатный обработчик
  NextButton — тот, который закрывает окно по нажатию «Запустить Tegr».
  Своего «WizardForm.Close» на финальной странице оказалось мало: окно
  оставалось на экране. Если обработчик снять не удалось, остаётся Close. }
procedure CloseWizard;
begin
  if NextOrig <> nil then
  begin
    Log('Закрываю мастер штатным обработчиком NextButton');
    NextOrig(WizardForm.NextButton);
  end
  else
  begin
    Log('Штатный обработчик NextButton недоступен, WizardForm.Close');
    WizardForm.Close;
  end;
end;

{ «Запустить Tegr»: сначала плеер, потом закрытие мастера — тем же
  способом, что и раньше закрывала эта кнопка. }
procedure RunClick(Sender: TObject);
begin
  Log('RunClick на финальной странице');
  if not SkipRun then
    LaunchPlayer;
  CloseWizard;
end;

{ «Выход»: то же закрытие, что у «Запустить», но без запуска плеера. }
procedure ExitClick(Sender: TObject);
begin
  Log('ExitClick на финальной странице');
  SkipRun := True;
  CloseWizard;
end;

{ Хук вызывается ДО перехода. Здесь переносим выбор значка на рабочий
  стол в штатный список задач — сам значок создаёт Inno. }
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then
    SetTask('desktopicon', DeskChk.Checked)
  else if CurPageID = wpFinished then
  begin
    { На последней странице Inno хук всё же зовёт (page=14) —
      поэтому обычный запуск делаем здесь; окно мастер закроет сам.
      SkipRun ставит кнопка «Выход»: тогда только закрываемся. }
    if not (RunAfterUpdate or SkipRun) then
      LaunchPlayer;
  end;
end;

{ Штатная «Отмена» мастера при показе спрашивала подтверждение. Убираем
  вопрос: Confirm := False. }
procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if CurPageID = wpFinished then
  begin
    SkipRun := True;
    Confirm := False;
  end;
end;

{ Пропускаем все страницы, которые нам не нужны: приветствие, задачи,
  готовность, компоненты. После выбора папки сразу идёт копирование. }
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = wpWelcome) or (PageID = wpSelectComponents) or
            (PageID = wpSelectProgramGroup) or (PageID = wpSelectTasks) or
            (PageID = wpReady) or (PageID = wpPreparing) or
            (PageID = wpInfoBefore) or (PageID = wpInfoAfter);
end;

procedure InitializeWizard;
begin
  WasSilent := WizardSilent();
  LoadPalette(PlayerPrefersDark);

  WizardForm.Color := UiBg;
  WizardForm.ClientWidth := WIN_W;
  WizardForm.ClientHeight := WIN_H;

  { Своё оформление поверх страниц: страницы остаются рабочими, их
    содержимое прячем. }
  HeaderPanel := TPanel.Create(WizardForm);
  with HeaderPanel do
  begin
    Parent := WizardForm;
    SetBounds(0, 0, WIN_W, HEAD_H);
    BevelOuter := bvNone;
    { Без ParentBackground=False панель заливается фоном родителя
      и собственный Color не виден — из-за этого пропадала и полоса. }
    ParentBackground := False;
    { Шапка того же цвета, что и тело: окно должно быть одного фона,
      без светлой полосы сверху. }
    Color := UiBg;
  end;

  VerLab := TLabel.Create(WizardForm);
  with VerLab do
  begin
    Parent := HeaderPanel;
    AutoSize := False;
    SetBounds(PAD, 9, 300, 16);
    Caption := '{#AppName} {#AppVersion}';
    Font.Size := 9;
    Font.Style := [fsBold];
    Transparent := True;
  end;

  HeadLab := TLabel.Create(WizardForm);
  with HeadLab do
  begin
    Parent := WizardForm;
    AutoSize := False;
    SetBounds(PAD, HEAD_H + 8, WIN_W - PAD * 2, 26);
    Font.Size := 14;
    Font.Style := [fsBold];
    Transparent := True;
  end;

  SubLab := TLabel.Create(WizardForm);
  with SubLab do
  begin
    Parent := WizardForm;
    AutoSize := False;
    SetBounds(PAD, HEAD_H + 36, WIN_W - PAD * 2, 26);
    WordWrap := True;
    Transparent := True;
  end;

  StepLab := TLabel.Create(WizardForm);
  with StepLab do
  begin
    Parent := WizardForm;
    AutoSize := False;
    SetBounds(PAD, HEAD_H + 64, WIN_W - PAD * 2, 14);
    Transparent := True;
  end;

  ErrLab := TLabel.Create(WizardForm);
  with ErrLab do
  begin
    Parent := WizardForm;
    AutoSize := False;
    SetBounds(PAD, HEAD_H + 150, WIN_W - PAD * 2, 14);
    Caption := '';
    Transparent := True;
  end;

  { Полоса хода: тонкая оранжевая линия над кнопками. Дорожка заметно
    темнее фона, заливка — акцентный оранжевый. }
  TrackPanel := TPanel.Create(WizardForm);
  with TrackPanel do
  begin
    Parent := WizardForm;
    SetBounds(PAD, WIN_H - PAD - BTN_H - 8, WIN_W - PAD * 2, 2);
    BevelOuter := bvNone;
    ParentBackground := False;
  end;

  FillPanel := TPanel.Create(WizardForm);
  with FillPanel do
  begin
    Parent := WizardForm;
    SetBounds(PAD, WIN_H - PAD - BTN_H - 8, 0, 2);
    BevelOuter := bvNone;
    ParentBackground := False;
  end;

  DeskChk := TCheckBox.Create(WizardForm);
  with DeskChk do
  begin
    Parent := WizardForm;
    SetBounds(PAD, HEAD_H + 112, WIN_W - PAD * 2, 20);
    Caption := 'Создать значок на рабочем столе';
    Checked := True;
  end;

  { Кнопки мастера переносим себе: клик идёт прямо в них, без имитации. }
  WizardForm.NextButton.Parent := WizardForm;
  WizardForm.CancelButton.Parent := WizardForm;

  { Поле пути и «Обзор» — тоже штатные, только на нашем окне. Так путь
    проверяет сам Inno, и «Обзор» работает своим диалогом. }
  WizardForm.DirEdit.Parent := WizardForm;
  WizardForm.DirBrowseButton.Parent := WizardForm;

  StyleHeader;
  HideWizardChrome;

  TrackPanel.Color := MixColor(UiBg, UiText, 120);
  FillPanel.Color := UiAccent;

  LayoutFor(wpSelectDir);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  HideWizardChrome;
  StyleHeader;
  TrackPanel.Color := MixColor(UiBg, UiText, 120);
  FillPanel.Color := UiAccent;

  if CurPageID = wpInstalling then
  begin
    AnimStop;
    AnimPos := 0;
    AnimTarget := 0;
    FillPanel.Width := 0;
  end
  else if CurPageID = wpFinished then
  begin
    AnimStop;
    AnimPos := 256;
    AnimTarget := 256;
    FillPanel.Width := TrackPanel.Width;
    if RunAfterUpdate then
      WantRun := True;
  end;

  { Финальная страница: снимаем штатные обработчики и ставим свои на
    ОБЕ кнопки. Обёртки тонкие: всё закрытие делает одна CloseWizard,
    поэтому «Выход» и «Запустить» закрывают окно одинаково. }
  if CurPageID = wpFinished then
  begin
    if not NextCaptured then
    begin
      NextOrig := WizardForm.NextButton.OnClick;
      NextCaptured := True;
      if NextOrig <> nil then
        Log('Штатный обработчик NextButton снят')
      else
        Log('Штатный обработчик NextButton пуст');
    end;
    WizardForm.NextButton.OnClick := @RunClick;
    WizardForm.CancelButton.OnClick := @ExitClick;
  end;

  LayoutFor(CurPageID);
end;
