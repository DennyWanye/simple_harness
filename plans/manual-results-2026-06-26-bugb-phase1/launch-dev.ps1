# BUG-B Phase 1 windows-mcp E2E launcher.
# Only inject env into the Tauri process; do NOT manually start backend/vite (pitfalls #7/#8/#9).
$ErrorActionPreference = 'Continue'
$env:DESKPET_BACKEND_DIR   = 'G:\projects\deskpet\backend'
$env:DESKPET_PYTHON        = 'G:\projects\deskpet\backend\.venv\Scripts\python.exe'
$env:DESKPET_DEV_MODE      = '1'
$env:DESKPET_USER_DATA_DIR = 'G:\projects\deskpet\backend\userdata'
$env:DESKPET_BACKEND_PORT  = '8100'
$env:DESKPET_VITE_PORT     = '5173'
Remove-Item Env:\HTTP_PROXY  -ErrorAction SilentlyContinue
Remove-Item Env:\HTTPS_PROXY -ErrorAction SilentlyContinue
Remove-Item Env:\http_proxy  -ErrorAction SilentlyContinue
Remove-Item Env:\https_proxy -ErrorAction SilentlyContinue
Remove-Item Env:\ALL_PROXY   -ErrorAction SilentlyContinue
Remove-Item Env:\all_proxy   -ErrorAction SilentlyContinue
$env:NO_PROXY  = '*'
$env:no_proxy  = '*'
$envFile = 'G:\projects\deskpet\.env'
if (Test-Path $envFile) {
    $raw = Get-Content $envFile -Raw -Encoding UTF8
    if ($raw -match 'DESKPET_CLOUD_API_KEY\s*=\s*(tsk_[A-Za-z0-9]+)') {
        $env:DESKPET_CLOUD_API_KEY = $matches[1]
    }
}
Write-Host ("[launch] DESKPET_CLOUD_API_KEY len=" + ($env:DESKPET_CLOUD_API_KEY).Length)

$logDir = 'G:\projects\deskpet\plans\manual-results-2026-06-26-bugb-phase1'
New-Item -ItemType Directory -Force -Path "$logDir\screenshots" | Out-Null
$logName = if ($env:DESKPET_LOGNAME) { $env:DESKPET_LOGNAME } else { 'tauri-dev.log' }

Set-Location 'G:\projects\deskpet\tauri-app'
& npx tauri dev *> "$logDir\$logName"
