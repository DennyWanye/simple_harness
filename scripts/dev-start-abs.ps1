# Absolute-path dev launcher (avoids $PSScriptRoot null under nested invocation).
# Mirrors dev-start.ps1: single-owner backend via .venv, userdata pinned to G.
$ErrorActionPreference = "Stop"

$repo     = "G:\projects\deskpet"
$backend  = Join-Path $repo "backend"
$tauri    = Join-Path $repo "tauri-app"
$userdata = Join-Path $backend "userdata"
$venvPy   = Join-Path $backend ".venv\Scripts\python.exe"

New-Item -ItemType Directory -Force -Path $userdata | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $userdata "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $userdata "workspace") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $userdata "logs") | Out-Null

# Free port 8100: kill any prior dev backend/Tauri.
Get-Process | Where-Object { $_.ProcessName -in @("python","deskpet","deskpet-backend") } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

if (-not (Test-Path $venvPy)) { Write-Host "ERROR: venv missing $venvPy" -ForegroundColor Red; exit 1 }

Write-Host "==> Single-owner backend env (Tauri spawns it via .venv)" -ForegroundColor Cyan
$env:DESKPET_USER_DATA_DIR = $userdata
$env:DESKPET_DEV_MODE      = "1"
$env:DESKPET_PYTHON        = $venvPy
$env:DESKPET_BACKEND_DIR   = $backend

Write-Host "==> Starting Tauri dev (single backend via .venv; frontend hot-reloads)" -ForegroundColor Cyan
Set-Location $tauri
npm run tauri dev
