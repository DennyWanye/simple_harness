# TC01 dev 真测启动：注入我的 backend 源码 + 隔离空 key userdata，让 Tauri 自管 backend+vite。
$ErrorActionPreference = "Continue"
$env:DESKPET_BACKEND_DIR  = "G:\projects\deskpet\backend"
$env:DESKPET_PYTHON       = "G:\projects\deskpet\backend\.venv\Scripts\python.exe"
$env:DESKPET_USER_DATA_DIR= "G:\projects\deskpet\backend\.dev-userdata-emptykey"
$env:DESKPET_DEV_MODE     = "1"
$env:DESKPET_BACKEND_PORT = "8100"
$env:DESKPET_VITE_PORT    = "5173"
# 不 source .env（避免预置 DESKPET_CLOUD_API_KEY）；chain 路径用假 endpoint 触发护栏，
# 即便 spawn_once 从 keychain 注入 legacy key 也不影响本测试。
Remove-Item Env:DESKPET_CLOUD_API_KEY -ErrorAction SilentlyContinue

$log = "G:\projects\deskpet\plans\manual-results-2026-06-28-userdata-path\tauri-dev4.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
New-Item -ItemType Directory -Force -Path "G:\projects\deskpet\plans\manual-results-2026-06-28-userdata-path\screenshots" | Out-Null

Set-Location "G:\projects\deskpet\tauri-app"
# tauri dev 会自跑 beforeDevCommand 起 vite（不手动起第二个 vite）。stderr 合并进 log。
& npm run tauri dev *>&1 | Tee-Object -FilePath $log
