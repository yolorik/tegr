; Установщик TGPlayerV2.
;
; Куда ставится: PrivilegesRequired=lowest, поэтому {autopf} раскрывается
; в %LocalAppData%\Programs. Это принципиально: программа пишет рядом с
; собой настройки, историю, файл входа и кеш роликов, а в Program Files
; без прав администратора писать некуда. Заодно установка не требует
; прав администратора вообще.
;
; Что НЕ попадает в поставку: файл сессии Telegram, история просмотра,
; настройки и лог — это данные того компьютера, где собирался установщик.
; proxy.txt, наоборот, кладём: это шаблон с подсказками, без секретов.

#define AppName "TGPlayer"
; Версия установщика. Держим в том же формате, что и сам плеер
; (tgplayer/core/version.py): 2.4.0d, где буква — схема загрузчика.
; Сверяйте при сборке: установщик обёртывает уже собранный .exe, и если
; .exe пересобрали с новой версией, здесь её надо поднять руками.
#define AppVersion "2.4.0d"
#define AppExe "TGPlayerV2.exe"
#define SrcDir "C:\tgplayer\dist_v2\TGPlayerV2"

[Setup]
AppId={{8F3C1A64-2B7E-4D91-9C55-6E0A7B4D2F18}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=TGPlayer
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=C:\tgplayer\installer
OutputBaseFilename=TGPlayerV2_Setup
SetupIconFile=C:\tgplayer\tgplayer.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
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
