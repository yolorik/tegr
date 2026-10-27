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

$py = 'C:\Users\yolorik\AppData\Local\Programs\Python\Python311\python.exe'
& $py -m PyInstaller --noconfirm --clean --windowed --name $nm `
    --icon C:\tgplayer\tgplayer.ico `
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
