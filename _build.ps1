param(
    [string]$DistDir = 'dist',
    [string]$BuildDir = 'build',
    [switch]$AllowRunning
)

$ErrorActionPreference = 'Stop'

# Build Tegr.exe from this directory.
# Name and version come from tgplayer/core/version.py.

$root = $PSScriptRoot
$py = 'C:\Users\yolorik\AppData\Local\Programs\Python\Python311\python.exe'
if ($env:PYTHON) { $py = $env:PYTHON }
if (-not (Test-Path $py)) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found) { $py = $found.Source }
}
if (-not (Test-Path $py)) {
    $found = Get-Command py -ErrorAction SilentlyContinue
    if ($found) { $py = $found.Source }
}
if (-not (Test-Path $py)) {
    Write-Output 'Python 3.11 not found. Set PYTHON or install it.'
    exit 1
}

$meta = & $py -c "import sys; sys.path.insert(0, r'$root'); from tgplayer.core import version as v; print(v.APP_NAME); print(v.VERSION)"
if (-not $meta -or $meta.Count -lt 2) {
    Write-Output 'Cannot read app name/version'
    exit 1
}
$nm = $meta[0].Trim()
$ver = $meta[1].Trim()
Write-Output "App: $nm $ver"
Write-Output "Root: $root"

$running = Get-Process -Name $nm -ErrorAction SilentlyContinue
if ($running -and -not $AllowRunning) {
    Write-Output "Close $nm before building."
    exit 1
}

$dist = Join-Path $root $DistDir
$dst = Join-Path $dist $nm
$build = Join-Path $root $BuildDir
$entry = Join-Path $root 'player_tegr.py'
$icon = Join-Path $root 'tgplayer.ico'
$mpvDll = Join-Path $root 'libmpv-2.dll'
$vulkanDll = Join-Path $root 'vulkan-1.dll'

if (-not (Test-Path $entry)) { Write-Output "Missing $entry"; exit 1 }
if (-not (Test-Path $mpvDll)) { Write-Output "Missing $mpvDll"; exit 1 }
if (-not (Test-Path $vulkanDll)) { Write-Output "Missing $vulkanDll"; exit 1 }

# Windows file properties: version, product name, description.
New-Item -ItemType Directory -Force -Path $build | Out-Null
$viSrc = Join-Path $root 'version_info.txt'
$viOut = Join-Path $build 'version_info_gen.txt'
$vi = Get-Content $viSrc -Raw -Encoding UTF8
$parts = ($ver -replace '[^0-9.]', '').Split('.')
while ($parts.Count -lt 3) { $parts += '0' }
$vtuple = "$($parts[0]), $($parts[1]), $($parts[2]), 0"
$vi = $vi -replace 'filevers=\([^)]*\)', "filevers=($vtuple)"
$vi = $vi -replace 'prodvers=\([^)]*\)', "prodvers=($vtuple)"
$vi = $vi -replace "(?<=StringStruct\(u'FileVersion', u')[^']*", $ver
$vi = $vi -replace "(?<=StringStruct\(u'ProductVersion', u')[^']*", $ver
$vi = $vi -replace "(?<=StringStruct\(u'ProductName', u')[^']*", $nm
$vi = $vi -replace "(?<=StringStruct\(u'CompanyName', u')[^']*", $nm
$vi = $vi -replace "(?<=StringStruct\(u'InternalName', u')[^']*", $nm
$vi = $vi -replace "(?<=StringStruct\(u'OriginalFilename', u')[^']*", "$nm.exe"
[System.IO.File]::WriteAllText($viOut, $vi,
    (New-Object System.Text.UTF8Encoding $false))

& $py -m PyInstaller --noconfirm --clean --windowed --name $nm `
    --icon $icon `
    --version-file $viOut `
    --add-binary "$mpvDll;." `
    --add-binary "$vulkanDll;." `
    --hidden-import mpv `
    --hidden-import cryptg `
    --hidden-import python_socks --collect-submodules python_socks `
    --hidden-import socks `
    --hidden-import segno --collect-submodules segno `
    --hidden-import PyQt6.QtMultimedia `
    --hidden-import PyQt6.QtMultimediaWidgets `
    --collect-submodules telethon `
    --collect-submodules tgplayer `
    --log-level WARN `
    --distpath $dist --workpath $build --specpath $build `
    $entry
$code = $LASTEXITCODE
Write-Output "PyInstaller exit=$code"
if ($code -ne 0) { exit $code }

# Resources the program looks for next to the executable.
$assets = @('tgplayer.ico', 'tgplayer_plane.png',
            'tgplayer_plane_key.png', 'proxy.txt')
foreach ($a in $assets) {
    $src = Join-Path $root $a
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $dst $a) -Force
    }
}

$exe = Join-Path $dst ($nm + '.exe')
if (-not (Test-Path $exe)) {
    Write-Output "Build failed: $exe not found"
    exit 1
}
Get-Item $exe | Select-Object Name, Length, LastWriteTime
Write-Output '--- bundled libraries ---'
Get-ChildItem (Join-Path $dst '_internal') -Filter '*.dll' |
    Where-Object { $_.Name -in @('libmpv-2.dll', 'vulkan-1.dll') } |
    Select-Object Name, Length
