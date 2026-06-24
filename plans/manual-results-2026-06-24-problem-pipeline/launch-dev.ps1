# 七步流水线真机 E2E 启动脚本 — 只给 Tauri 进程注入 env，不手动起 backend/vite（项目坑 #7/#8/#9）
$ErrorActionPreference = 'Continue'
$env:DESKPET_BACKEND_DIR   = 'G:\projects\deskpet\backend'
$env:DESKPET_PYTHON        = 'G:\projects\deskpet\backend\.venv\Scripts\python.exe'
$env:DESKPET_DEV_MODE      = '1'
$env:DESKPET_USER_DATA_DIR = 'G:\projects\deskpet\backend\userdata'
$env:DESKPET_BACKEND_PORT  = '8100'
$env:DESKPET_VITE_PORT     = '5173'

$logDir = 'G:\projects\deskpet\plans\manual-results-2026-06-24-problem-pipeline'
New-Item -ItemType Directory -Force -Path "$logDir\screenshots" | Out-Null

Set-Location 'G:\projects\deskpet\tauri-app'
# npx tauri dev 自管唯一 vite(beforeDevCommand) + spawn backend 到 8100
& npx tauri dev *> "$logDir\tauri-dev.log"
