; Установщик TGPlayer.
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

#define AppName "TGPlayer"
; Версия установщика. Держим в том же формате, что и сам плеер
; (tgplayer/core/version.py): 2.7.0d, где буква — схема загрузчика.
; Сверяйте при сборке: установщик обёртывает уже собранный .exe, и если
; .exe пересобрали с новой версией, здесь её надо поднять руками.
#define AppVersion "3.0.0d"
#define AppExe "TGPlayer.exe"
#define SrcDir "H:\deepseek projects\tgplayer\dist\TGPlayer"

[Setup]
AppId={{8F3C1A64-2B7E-4D91-9C55-6E0A7B4D2F18}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=TGPlayer
; Путь по умолчанию: Program Files для всех пользователей, когда установка
; идёт с правами администратора. {autopf} при таком режиме раскрывается
; именно туда — и теперь это работает, потому что права уже есть.
DefaultDirName={autopf}\{#AppName}
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
OutputBaseFilename=TGPlayer_Setup
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
Filename: "{app}\{#AppExe}"; Description: "Запустить {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Кеш роликов может занимать гигабайты — при удалении его убираем.
; Настройки, историю и вход НЕ трогаем: после переустановки человек
; останется в своём аккаунте и с привычными настройками.
; Журнал тоже не удаляем: по нему разбирают сбои, и он нужен уже после
; того, как программу сняли.
Type: filesandordirs; Name: "{app}\stream_cache"
