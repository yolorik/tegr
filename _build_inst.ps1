$ErrorActionPreference = 'Continue'
# Папка проекта — от расположения этого скрипта, а не жёстким путём:
# после переноса проекта сборка искала исходники в старом месте.
$root = $PSScriptRoot
# Сборка установщика. Результат кладём в releases\<версия>\ — рядом с
# ним же описание и контрольная сумма, готовая к выкладке на GitHub.
#
# В корне проекта установщики не держим: раньше они копились там, и
# разобраться, какой из них актуальный, было нельзя.

$iscc = 'C:\Users\yolorik\AppData\Local\Programs\Inno Setup 6\ISCC.exe'
$py   = 'C:\Users\yolorik\AppData\Local\Programs\Python\Python311\python.exe'

# Версию и имя берём из единственного источника, чтобы имя папки и файлов
# не разъехалось с тем, что подписано внутри.
$meta = & $py -c "import sys; sys.path.insert(0, r'$root'); from tgplayer.core import version as v; print(v.VERSION); print(v.APP_NAME)"
$ver = ($meta | Select-Object -First 1).Trim()
$nm = ($meta | Select-Object -Skip 1 -First 1).Trim()
if (-not $ver -or -not $nm) { Write-Output 'не удалось прочитать версию'; exit 1 }

$dir = Join-Path (Join-Path $root 'releases') $ver
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Write-Output "версия: $ver"
Write-Output "имя:    $nm"
Write-Output "папка:  $dir"

# Имена и версию передаём в .iss параметрами: там они только значения по
# умолчанию. Так при переименовании программы не надо править два файла —
# достаточно version.py.
$defs = @(
    "/DAppName=$nm",
    "/DAppVersion=$ver",
    "/DAppExe=$nm.exe",
    "/DSrcDir=$(Join-Path $root "dist\$nm")"
)

# OutputDir передаём параметром: в .iss оставлено место по умолчанию,
# а конкретную папку задаёт этот скрипт.
$done = $false
for ($i = 1; $i -le 8; $i++) {
    Write-Output "попытка $i"
    & $iscc "/O$dir" @defs (Join-Path $root 'installer\tgplayer.iss') 2>&1 | Select-Object -Last 4
    if ($LASTEXITCODE -eq 0) { $done = $true; break }
    Start-Sleep -Seconds 8
}

if (-not $done) { Write-Output 'сборка установщика не удалась'; exit 1 }

$setup = Join-Path $dir ($nm + '_Setup.exe')
if (-not (Test-Path $setup)) { Write-Output "нет файла: $setup"; exit 1 }

# Контрольная сумма рядом — её прикладывают к релизу.
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
[System.IO.File]::WriteAllText((Join-Path $dir 'SHA256.txt'), $hash,
    (New-Object System.Text.UTF8Encoding $false))
Write-Output "SHA256: $hash"

# Шаблон описания релиза: если его ещё нет, создаём заготовку.
$notes = Join-Path $dir 'RELEASE_NOTES.md'
if (-not (Test-Path $notes)) {
    $tpl = @"
## $nm $ver

### Что нового

- заполнить

### Как установить

Скачать ``$($nm)_Setup.exe`` из раздела Assets ниже и запустить.

Папку установки можно выбрать. По умолчанию — в профиль пользователя,
права администратора не требуются. Установка в Program Files работает:
установщик сам запросит повышение.

Контрольная сумма — в ``SHA256.txt``.

STABLE
"@
    [System.IO.File]::WriteAllText($notes, $tpl,
        (New-Object System.Text.UTF8Encoding $false))
    Write-Output "создан шаблон описания: $notes"
}

Write-Output ''
Write-Output '--- готово к выкладке ---'
Get-ChildItem $dir | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
