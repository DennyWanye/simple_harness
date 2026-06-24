# Harden-E2E dev launcher — explicit env, data → G:, run G: source (not frozen).
$ErrorActionPreference = "Stop"
$repo    = "G:\projects\deskpet"
$backend = "$repo\backend"
$venvPy  = "$backend\.venv\Scripts\python.exe"
$userdata = "$backend\userdata"

if (-not (Test-Path $venvPy)) { Write-Host "ERROR venv missing: $venvPy"; exit 1 }

# Single-owner backend: only inject env, let Tauri spawn the backend via .venv.
$env:DESKPET_USER_DATA_DIR = $userdata
$env:DESKPET_DEV_MODE      = "1"
$env:DESKPET_PYTHON        = $venvPy
$env:DESKPET_BACKEND_DIR   = $backend

Write-Host "==> userdata=$userdata python=$venvPy backend=$backend"
Set-Location "$repo\tauri-app"
npm run tauri dev
