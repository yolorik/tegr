$ErrorActionPreference = 'Continue'
$iscc = 'C:\Users\yolorik\AppData\Local\Programs\Inno Setup 6\ISCC.exe'
$out = 'C:\tgplayer\installer\TGPlayerV2_Setup.exe'

# ISCC regularly trips over a locked output file right after creating it
# (antivirus holds it open), so retry a few times.
$done = $false
for ($i = 1; $i -le 8; $i++) {
    Write-Output "attempt $i"
    & $iscc 'C:\tgplayer\installer\tgplayer.iss' 2>&1 | Select-Object -Last 4
    if ($LASTEXITCODE -eq 0) { $done = $true; break }
    Start-Sleep -Seconds 8
}

if ($done) {
    Get-Item $out | Select-Object Name, Length, LastWriteTime
} else {
    Write-Output 'installer build failed'
}
