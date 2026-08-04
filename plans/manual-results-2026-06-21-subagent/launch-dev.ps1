# Launch wrapper for subagent-driver E2E — runs the dev stack (backend via
# Tauri-spawned .venv python + vite + webview) with ALL output captured to a
# log file so we can grep backend structlog (subagent_scheduled, etc.).
$ErrorActionPreference = "Stop"

$repo     = "G:\projects\deskpet"
$backend  = Join-Path $repo "backend"
$tauri    = Join-Path $repo "tauri-app"
$userdata = Join-Path $backend "userdata"
$logDir   = Join-Path $repo "plans\manual-results-2026-06-21-subagent"
$log      = Join-Path $logDir "tauri-dev.log"

New-Item -ItemType Directory -Force -Path $userdata | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $userdata "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $userdata "workspace") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $userdata "logs") | Out-Null

$venvPy = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Write-Host "ERROR: venv missing $venvPy"; exit 1 }

# Single-owner backend: Tauri spawns it via .venv (CLAUDE.md trap #7/#8).
$env:DESKPET_USER_DATA_DIR = $userdata
$env:DESKPET_DEV_MODE      = "1"
$env:DESKPET_PYTHON        = $venvPy
$env:DESKPET_BACKEND_DIR   = $backend

Set-Location $tauri
# *> captures all streams (stdout+stderr+info+warn) to the log.
npm run tauri dev *> $log
