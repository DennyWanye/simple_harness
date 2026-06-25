# Production-acceptance + idempotency E2E launcher.
# Only inject env into the Tauri process; do NOT manually start backend/vite (project pitfalls #7/#8/#9).
# Log file name comes from $env:DESKPET_LOGNAME (default tauri-dev.log) so each run/restart uses a fresh log.
$ErrorActionPreference = 'Continue'
$env:DESKPET_BACKEND_DIR   = 'G:\projects\deskpet\backend'
$env:DESKPET_PYTHON        = 'G:\projects\deskpet\backend\.venv\Scripts\python.exe'
$env:DESKPET_DEV_MODE      = '1'
$env:DESKPET_USER_DATA_DIR = 'G:\projects\deskpet\backend\userdata'
$env:DESKPET_BACKEND_PORT  = '8100'
$env:DESKPET_VITE_PORT     = '5173'
# Clash proxy (7897) kills idle ~60s connections -> non-streaming pre-analysis call to the relay
# hangs during gpt-5.5 thinking. Fully remove proxy env so backend httpx connects directly to the relay.
Remove-Item Env:\HTTP_PROXY  -ErrorAction SilentlyContinue
Remove-Item Env:\HTTPS_PROXY -ErrorAction SilentlyContinue
Remove-Item Env:\http_proxy  -ErrorAction SilentlyContinue
Remove-Item Env:\https_proxy -ErrorAction SilentlyContinue
Remove-Item Env:\ALL_PROXY   -ErrorAction SilentlyContinue
Remove-Item Env:\all_proxy   -ErrorAction SilentlyContinue
$env:NO_PROXY  = '*'
$env:no_proxy  = '*'
# Cloud LLM key: the keychain cloud-llm slot got tangled across Windows credential persistence
# realms (keyring-rs ENTERPRISE vs win32cred LOCAL_MACHINE) and the relay login flow never syncs
# it (followup 2026-06-25-relay-cloud-key-sync). We deleted the keychain slot, so process_manager.rs
# sees no key and does NOT override DESKPET_CLOUD_API_KEY -> the backend inherits this launcher value.
# NOTE: .env is UTF-8 with Chinese comments; read -Raw -Encoding UTF8 and match the tsk_ token
# anywhere (a comment line above can merge into the key line under the wrong codepage, which broke
# a line-anchored regex).
$envFile = 'G:\projects\deskpet\.env'
if (Test-Path $envFile) {
    $raw = Get-Content $envFile -Raw -Encoding UTF8
    if ($raw -match 'DESKPET_CLOUD_API_KEY\s*=\s*(tsk_[A-Za-z0-9]+)') {
        $env:DESKPET_CLOUD_API_KEY = $matches[1]
    }
}
Write-Host ("[launch] DESKPET_CLOUD_API_KEY len=" + ($env:DESKPET_CLOUD_API_KEY).Length)

$logDir = 'G:\projects\deskpet\plans\manual-results-2026-06-25-problem-pipeline-prod'
New-Item -ItemType Directory -Force -Path "$logDir\screenshots" | Out-Null
$logName = if ($env:DESKPET_LOGNAME) { $env:DESKPET_LOGNAME } else { 'tauri-dev.log' }

Set-Location 'G:\projects\deskpet\tauri-app'
# npx tauri dev self-manages the single vite (beforeDevCommand) + spawns backend on 8100.
& npx tauri dev *> "$logDir\$logName"
