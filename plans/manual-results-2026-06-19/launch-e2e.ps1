# E2E launcher (absolute paths; no $PSScriptRoot dependency).
# 跑仓库 backend(非 frozen) + 自管唯一 vite, userdata 指向含 CATL 的 backend/userdata。
$ErrorActionPreference = "Stop"
$repo     = "G:\projects\deskpet"
$backend  = "$repo\backend"
$tauri    = "$repo\tauri-app"
$userdata = "$backend\userdata"

$env:DESKPET_USER_DATA_DIR = $userdata
$env:DESKPET_DEV_MODE      = "1"
$env:DESKPET_PYTHON        = "$backend\.venv\Scripts\python.exe"
$env:DESKPET_BACKEND_DIR   = $backend

Write-Host "==> launch-e2e: backend_dir=$backend python=$($env:DESKPET_PYTHON)"
Write-Host "==> userdata=$userdata"
Set-Location $tauri
npm run tauri dev
