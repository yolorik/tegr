# Бэкап исходников Tegr перед внесением изменений.
#   .\ _backup.ps1            -> создать бэкап
#   .\ _backup.ps1 -List      -> показать бэкапы
#   .\ _backup.ps1 -Restore <папка>  -> восстановить из бэкапа
param(
    [switch]$List,
    [string]$Restore
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backups = Join-Path $root "_backups"

function Make-Backup {
    $stamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
    $dest  = Join-Path $backups "src_$stamp"

    $dirty = git -C $root status --porcelain
    if ($dirty) {
        Write-Host "ВНИМАНИЕ: есть незакоммиченные изменения — они попадут в бэкап как есть." -ForegroundColor Yellow
    }

    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    robocopy $root $dest /E /XD .git dist build __pycache__ _backups _build _build_data stream_cache `
        /XF *.pyc *.dll *.exe *.ico *.log *.bin version_info_gen.txt /NFL /NDL /NJH /NJS /NP | Out-Null

    $head = git -C $root rev-parse --short HEAD
    $subject = git -C $root log -1 --pretty=%s
    @(
        "Дата:    $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        "Коммит:  $head  ($subject)"
        "Ветка:   $(git -C $root rev-parse --abbrev-ref HEAD)"
    ) | Set-Content -Path (Join-Path $dest "BACKUP_INFO.txt") -Encoding utf8

    $size = [math]::Round((Get-ChildItem $dest -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 2)
    $n    = (Get-ChildItem $dest -Recurse -File).Count
    Write-Host "Бэкап готов: $dest" -ForegroundColor Green
    Write-Host "  файлов: $n, размер: $size MB, коммит: $head"
}

function List-Backups {
    if (-not (Test-Path $backups)) { Write-Host "Бэкапов нет."; return }
    Get-ChildItem $backups -Directory | Sort-Object Name -Descending | ForEach-Object {
        $n   = (Get-ChildItem $_.FullName -Recurse -File).Count
        $sum = [math]::Round((Get-ChildItem $_.FullName -Recurse -File | Measure-Object Length -Sum).Sum / 1KB, 0)
        "{0}   {1,3} файлов, {2,6} KB" -f $_.Name, $n, $sum
    }
}

function Restore-Backup([string]$name) {
    $src = Join-Path $backups $name
    if (-not (Test-Path $src)) {
        $src = $name
    }
    if (-not (Test-Path $src)) { throw "Бэкап не найден: $name" }

    Write-Host "Сначала сделаю бэкап текущего состояния..." -ForegroundColor Yellow
    Make-Backup

    Get-ChildItem $src -Recurse -File |
        Where-Object { $_.Name -ne "BACKUP_INFO.txt" } |
        ForEach-Object {
            $rel = $_.FullName.Substring((Resolve-Path $src).Path.Length + 1)
            $dst = Join-Path $root $rel
            New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
            Copy-Item $_.FullName $dst -Force
            Write-Host "  восстановлен: $rel"
        }
    Write-Host "Готово." -ForegroundColor Green
}

if ($List)                { List-Backups }
elseif ($Restore)         { Restore-Backup $Restore }
else                      { Make-Backup }
