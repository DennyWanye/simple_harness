# Focused check: confirm tomlkit is now bundled (no "No module named tomlkit").
$ErrorActionPreference = "SilentlyContinue"
$exe = "F:\deskpet-build\dist\deskpet-backend\deskpet-backend.exe"
$tmp = Join-Path $env:TEMP ("dp-tk-" + [guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$errLog = Join-Path $tmp "stderr.log"
$env:DESKPET_USER_DATA_DIR = $tmp
$env:DESKPET_DEV_MODE = "1"
$env:DESKPET_BACKEND_PORT = "8124"
$p = Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe) `
    -RedirectStandardError $errLog -RedirectStandardOutput (Join-Path $tmp "out.log") `
    -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 16
taskkill /F /T /PID $p.Id 2>$null | Out-Null
Start-Sleep 1
$L = Get-Content $errLog
Write-Host "===== lines mentioning tomlkit / feature_flag / endpoints_recover / No module named ====="
$hits = $L | Select-String "tomlkit|feature_flag|endpoints_recover|No module named"
if($hits){ $hits | ForEach-Object { Write-Host $_ } }
else { Write-Host "(none -- no missing-module warnings; tomlkit OK)" -ForegroundColor Green }
Write-Host "`n===== empty-key guard / Bearer presence (config seeded path) ====="
$L | Select-String "seeded user config|_UNUSABLE|empty_api_key" | Select-Object -First 3 | ForEach-Object { Write-Host $_ }
Remove-Item $tmp -Recurse -Force -EA 0
