$ErrorActionPreference = 'Continue'
$nm = 'TG' + 'Player' + 'V2'
$mod = 'player' + '_deepseek'
$dst = 'C:\tgplayer\dist_v2\TGPlayerV2'
$bak = 'C:\tgplayer\_v2_data'
$data = @('my_player_session.session', 'proxy.txt', 'history.json', 'settings.json')

$p = Get-Process -Name $nm -ErrorAction SilentlyContinue
if ($p) { Write-Output 'ВНИМАНИЕ: плеер запущен, закройте его'; exit 1 }
Write-Output 'плеер не запущен'

New-Item -ItemType Directory -Force -Path $bak | Out-Null
foreach ($f in $data) {
    $src = Join-Path $dst $f
    if (Get-Item $src -ErrorAction SilentlyContinue) {
        Copy-Item $src (Join-Path $bak $f) -Force
    }
}

Copy-Item "C:\tgplayer\${mod}.py" "C:\tgplayer\${mod}_v2.py" -Force

# --- версия: берём из единственного источника, tgplayer/core/version.py ---
$py = 'C:\Users\yolorik\AppData\Local\Programs\Python\Python311\python.exe'
$verLine = & $py -c "import sys; sys.path.insert(0, r'C:\tgplayer'); from tgplayer.core import version as v; print(v.VERSION); print(v.LOADER_TITLES.get(v.LOADER, ''))"
$ver = $verLine[0].Trim()
$verNote = ($verLine | Select-Object -Skip 1) -join ' '.Trim()
$nums = ($ver -replace '[^0-9.]', '').Split('.')
while ($nums.Count -lt 3) { $nums += '0' }
$vtuple = "$($nums[0]), $($nums[1]), $($nums[2]), 0"
Write-Output "версия сборки: $ver  ($verNote)"

$vi = Get-Content 'C:\tgplayer\version_info.txt' -Raw -Encoding UTF8
$vi = $vi -replace 'filevers=\([^)]*\)', "filevers=($vtuple)"
$vi = $vi -replace 'prodvers=\([^)]*\)', "prodvers=($vtuple)"
$vi = $vi -replace "(?<=StringStruct\(u'FileVersion', u')[^']*", $ver
$vi = $vi -replace "(?<=StringStruct\(u'ProductVersion', u')[^']*", $ver
if ($verNote) {
    $vi = $vi -replace "(?<=StringStruct\(u'Comments', u')[^']*", $verNote
}
# Пишем в UTF-8 без BOM: PyInstaller читает файл как обычный текст, и BOM
# в начале ломает разбор первой строки (VSVersionInfo не распознаётся).
[System.IO.File]::WriteAllText('C:\tgplayer\version_info_gen.txt', $vi,
    (New-Object System.Text.UTF8Encoding $false))

& $py -m PyInstaller --noconfirm --clean --windowed --name $nm `
    --icon C:\tgplayer\tgplayer.ico `
    --version-file C:\tgplayer\version_info_gen.txt `
    --add-binary "C:\tgplayer\libmpv-2.dll;." `
    --add-binary "C:\tgplayer\vulkan-1.dll;." `
    --hidden-import mpv --hidden-import cryptg `
    --hidden-import python_socks --collect-submodules python_socks --hidden-import socks `
    --collect-submodules telethon --log-level WARN `
    --distpath C:\tgplayer\dist_v2 --workpath C:\tgplayer\build_v2 --specpath C:\tgplayer\build_v2 `
    "C:/tgplayer/${mod}_v2.py" 2>&1 | Select-Object -Last 1
Write-Output "build exit=$LASTEXITCODE"

foreach ($f in $data) {
    $src = Join-Path $bak $f
    if (Get-Item $src -ErrorAction SilentlyContinue) {
        Copy-Item $src (Join-Path $dst $f) -Force
    }
}
Copy-Item 'C:\tgplayer\tgplayer.ico' (Join-Path $dst 'tgplayer.ico') -Force

$exe = Join-Path $dst ($nm + '.exe')
Get-Item $exe | Select-Object Name, Length, LastWriteTime
Write-Output '--- библиотеки в поставке ---'
Get-ChildItem (Join-Path $dst '_internal') -Filter '*.dll' |
    Where-Object { $_.Name -in @('libmpv-2.dll', 'vulkan-1.dll') } |
    Select-Object Name, Length
