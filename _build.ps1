$ErrorActionPreference = 'Continue'
# Сборка .exe плеера.
#
# $root считается от расположения ЭТОГО скрипта, а не задаётся путём.
# Раньше здесь стоял жёсткий «C:\tgplayer», и после переноса проекта в
# другую папку сборка искала исходники там, где их уже нет. Все пути ниже
# строятся от $root через Join-Path — тогда проект можно переносить куда
# угодно, и сборка продолжит работать.

$root = $PSScriptRoot
$nm = 'TGPlayer'
$mod = 'player_deepseek'
$dst = Join-Path $root 'dist\TGPlayer'
$bak = Join-Path $root '_build_data'
$build = Join-Path $root 'build'
$data = @('my_player_session.session', 'proxy.txt', 'history.json', 'settings.json')

$p = Get-Process -Name $nm -ErrorAction SilentlyContinue
if ($p) { Write-Output 'ВНИМАНИЕ: плеер запущен, закройте его'; exit 1 }
Write-Output 'плеер не запущен'
Write-Output "папка проекта: $root"

New-Item -ItemType Directory -Force -Path $bak | Out-Null
foreach ($f in $data) {
    $src = Join-Path $dst $f
    if (Get-Item $src -ErrorAction SilentlyContinue) {
        Copy-Item $src (Join-Path $bak $f) -Force
    }
}

$entry = Join-Path $root "$mod.py"
$entryBuild = Join-Path $root "${mod}_build.py"
Copy-Item $entry $entryBuild -Force

# --- версия: берём из единственного источника, tgplayer/core/version.py ---
$py = 'C:\Users\yolorik\AppData\Local\Programs\Python\Python311\python.exe'
$verLine = & $py -c "import sys; sys.path.insert(0, r'$root'); from tgplayer.core import version as v; print(v.VERSION); print(v.LOADER_TITLES.get(v.LOADER, ''))"
$ver = $verLine[0].Trim()
$verNote = ($verLine | Select-Object -Skip 1) -join ' '.Trim()
$nums = ($ver -replace '[^0-9.]', '').Split('.')
while ($nums.Count -lt 3) { $nums += '0' }
$vtuple = "$($nums[0]), $($nums[1]), $($nums[2]), 0"
Write-Output "версия сборки: $ver"

$viPath = Join-Path $root 'version_info.txt'
$viOut = Join-Path $root 'version_info_gen.txt'
$vi = Get-Content $viPath -Raw -Encoding UTF8
$vi = $vi -replace 'filevers=\([^)]*\)', "filevers=($vtuple)"
$vi = $vi -replace 'prodvers=\([^)]*\)', "prodvers=($vtuple)"
$vi = $vi -replace "(?<=StringStruct\(u'FileVersion', u')[^']*", $ver
$vi = $vi -replace "(?<=StringStruct\(u'ProductVersion', u')[^']*", $ver
if ($verNote) {
    $vi = $vi -replace "(?<=StringStruct\(u'Comments', u')[^']*", $verNote
}
# Пишем в UTF-8 без BOM: PyInstaller читает файл как обычный текст, и BOM
# в начале ломает разбор первой строки (VSVersionInfo не распознаётся).
[System.IO.File]::WriteAllText($viOut, $vi,
    (New-Object System.Text.UTF8Encoding $false))

$icon = Join-Path $root 'tgplayer.ico'
$mpvDll = Join-Path $root 'libmpv-2.dll'
$vulkanDll = Join-Path $root 'vulkan-1.dll'

& $py -m PyInstaller --noconfirm --clean --windowed --name $nm `
    --icon $icon `
    --version-file $viOut `
    --add-binary "$mpvDll;." `
    --add-binary "$vulkanDll;." `
    --hidden-import mpv --hidden-import cryptg `
    --hidden-import python_socks --collect-submodules python_socks --hidden-import socks `
    --hidden-import segno --collect-submodules segno `
    --collect-submodules telethon --log-level WARN `
    --distpath (Join-Path $root 'dist') --workpath $build --specpath $build `
    $entryBuild 2>&1 | Select-Object -Last 1
Write-Output "build exit=$LASTEXITCODE"

foreach ($f in $data) {
    $src = Join-Path $bak $f
    if (Get-Item $src -ErrorAction SilentlyContinue) {
        Copy-Item $src (Join-Path $dst $f) -Force
    }
}
Copy-Item $icon (Join-Path $dst 'tgplayer.ico') -Force

# Картинки значка: самолётик для окна входа и версия с ключевым цветом
# для трея. Лежат рядом с .exe — оттуда их и находит paths.
$assets = @('tgplayer_plane.png', 'tgplayer_plane_key.png')
foreach ($a in $assets) {
    $src = Join-Path $root $a
    if (Test-Path $src) { Copy-Item $src (Join-Path $dst $a) -Force }
}

$exe = Join-Path $dst ($nm + '.exe')
Get-Item $exe | Select-Object Name, Length, LastWriteTime
Write-Output '--- библиотеки в поставке ---'
Get-ChildItem (Join-Path $dst '_internal') -Filter '*.dll' |
    Where-Object { $_.Name -in @('libmpv-2.dll', 'vulkan-1.dll') } |
    Select-Object Name, Length
