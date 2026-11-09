param([string]$SrcDir = '')
$ErrorActionPreference = 'Stop'

# Build the Inno Setup installer from dist\<AppName>.
# Result: releases\<version>\<AppName>_Setup.exe

$root = $PSScriptRoot
$iscc = 'C:\Users\yolorik\AppData\Local\Programs\Inno Setup 6\ISCC.exe'
if ($env:ISCC) { $iscc = $env:ISCC }
if (-not (Test-Path $iscc)) {
    foreach ($candidate in @(
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
    )) {
        if (Test-Path $candidate) { $iscc = $candidate; break }
    }
}
if (-not (Test-Path $iscc)) {
    $found = Get-Command ISCC -ErrorAction SilentlyContinue
    if ($found) { $iscc = $found.Source }
}
if (-not (Test-Path $iscc)) {
    Write-Output 'Inno Setup 6 (ISCC.exe) not found. Set ISCC or install it.'
    exit 1
}
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
$dir = Join-Path (Join-Path $root 'releases') $ver
New-Item -ItemType Directory -Force -Path $dir | Out-Null

Write-Output "App:     $nm"
Write-Output "Version: $ver"
Write-Output "Output:  $dir"

if ($SrcDir) {
    $src = [System.IO.Path]::GetFullPath($SrcDir)
} else {
    $src = Join-Path $root "dist\$nm"
}
if (-not (Test-Path (Join-Path $src ($nm + '.exe')))) {
    Write-Output "Build dist first: $src"
    exit 1
}

$defs = @(
    "/DAppName=$nm",
    "/DAppVersion=$ver",
    "/DAppExe=$nm.exe",
    "/DSrcDir=$src",
    "/DSourceRoot=$root"
)

$done = $false
for ($i = 1; $i -le 6; $i++) {
    Write-Output "Inno attempt $i"
    & $iscc "/O$dir" @defs (Join-Path $root 'installer\Tegr.iss')
    if ($LASTEXITCODE -eq 0) { $done = $true; break }
    Start-Sleep -Seconds 6
}
if (-not $done) {
    Write-Output 'Installer build failed'
    exit 1
}

$setup = Join-Path $dir ($nm + '_Setup.exe')
if (-not (Test-Path $setup)) {
    Write-Output "Missing $setup"
    exit 1
}

$hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
[System.IO.File]::WriteAllText(
    (Join-Path $dir 'SHA256.txt'), $hash,
    (New-Object System.Text.UTF8Encoding $false))

Get-ChildItem $dir | Select-Object Name, Length, LastWriteTime
Write-Output "SHA256: $hash"
