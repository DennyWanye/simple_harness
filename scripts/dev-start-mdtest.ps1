# Ad-hoc dev launcher for markdown-render testing. Mirrors
# dev-start.ps1: single-owner backend spawned by Tauri via .venv,
# frontend hot-reloads.
$ErrorActionPreference = "Stop"

$repo     = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backend  = Join-Path $repo "backend"
$tauri    = Join-Path $repo "tauri-app"
$userdata = Join-Path $backend "userdata"
$venvPy   = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPy)) { Write-Host "ERROR: venv missing $venvPy" -ForegroundColor Red; exit 1 }

# Kill prior dev backend/Tauri so port 8100 is free.
Get-Process | Where-Object { $_.ProcessName -in @("deskpet","deskpet-backend") } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

$env:DESKPET_USER_DATA_DIR = $userdata
$env:DESKPET_DEV_MODE      = "1"
$env:DESKPET_PYTHON        = $venvPy
$env:DESKPET_BACKEND_DIR   = $backend

Write-Host "==> Starting Tauri dev (single backend via .venv; frontend hot-reloads)" -ForegroundColor Cyan
Set-Location $tauri
npm run tauri dev
