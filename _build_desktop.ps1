"""Сборка .exe плеера в отдельную папку — текущий код, для проверки.

Сборка как в _build.ps1, но результат кладётся в папку на рабочем столе
и НЕ трогает dist (там лежит рабочая 3.2.2d). Профиль (сессия, настройки,
журнал) берётся из рабочей папки dist, чтобы не входить в аккаунт заново.
"""
$ErrorActionPreference = 'Continue'

$py = 'C:\Users\yolorik\AppData\Local\Programs\Python\Python311\python.exe'
$root = $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$out = Join-Path $desktop 'tegr'
$build = Join-Path $root 'build\tegr'
$work = Join-Path $root '_build_data\tegr'
$profile = Join-Path $root 'dist\Tegr'

$nm = (& $py -c "import sys; sys.path.insert(0, r'$root'); from tgplayer.core import version as v; print(v.APP_NAME)" | Select-Object -First 1).Trim()
if (-not $nm) { Write-Output 'не удалось прочитать имя программы'; exit 1 }
Write-Output "имя приложения: $nm"
Write-Output "куда: $out"

$p = Get-Process -Name $nm -ErrorAction SilentlyContinue
if ($p) { Write-Output 'ВНИМАНИЕ: плеер запущен, закройте его'; exit 1 }

New-Item -ItemType Directory -Force -Path $build, $work, $out | Out-Null

# Версия и сведения о сборке — как в _build.ps1
$verLine = & $py -c "import sys; sys.path.insert(0, r'$root'); from tgplayer.core import version as v; print(v.VERSION); print(v.LOADER_TITLES.get(v.LOADER, ''))"
$ver = $verLine[0].Trim()
$verNote = ($verLine | Select-Object -Skip 1) -join ' '.Trim()
Write-Output "версия: $ver"

$entry = Join-Path $root 'player_deepseek.py'
$entryBuild = Join-Path $build 'player_deepseek_build.py'
Copy-Item $entry $entryBuild -Force

$icon = Join-Path $root 'tgplayer.ico'
$mpvDll = Join-Path $root 'libmpv-2.dll'
$vulkanDll = Join-Path $root 'vulkan-1.dll'

& $py -m PyInstaller --noconfirm --clean --windowed --name $nm `
    --icon $icon `
    --add-binary "$mpvDll;." `
    --add-binary "$vulkanDll;." `
    --hidden-import mpv --hidden-import cryptg `
    --hidden-import python_socks --collect-submodules python_socks --hidden-import socks `
    --hidden-import segno --collect-submodules segno `
    --collect-submodules telethon --log-level WARN `
    --distpath $build --workpath $work --specpath $work `
    $entryBuild 2>&1 | Select-Object -Last 3
Write-Output "build exit=$LASTEXITCODE"

$src = Join-Path $build $nm
if (-not (Test-Path (Join-Path $src "$nm.exe"))) {
    Write-Output 'СБОРКА НЕ ПОЛУЧИЛАСЬ'
    exit 1
}

# Переносим собранное в папку на рабочем столе
Copy-Item (Join-Path $src '*') $out -Recurse -Force

# Профиль из рабочей папки: сессия, настройки, прокси, история
foreach ($f in @('my_player_session.session', 'settings.json', 'proxy.txt', 'history.json')) {
    $from = Join-Path $profile $f
    if (Test-Path $from) { Copy-Item $from (Join-Path $out $f) -Force }
}
# Картинки значка — рядом с .exe (оттуда их ищет paths)
foreach ($a in @('tgplayer.ico', 'tgplayer_plane.png', 'tgplayer_plane_key.png')) {
    $from = Join-Path $root $a
    if (Test-Path $from) { Copy-Item $from (Join-Path $out $a) -Force }
}

Write-Output '--- готово ---'
Get-ChildItem $out | Select-Object Name, Length, LastWriteTime
Get-ChildItem (Join-Path $out '_internal') -Filter '*.dll' |
    Where-Object { $_.Name -in @('libmpv-2.dll', 'vulkan-1.dll') } |
    Select-Object Name, Length
