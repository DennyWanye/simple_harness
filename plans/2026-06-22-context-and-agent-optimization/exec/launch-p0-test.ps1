# P0 真测启动脚本 — 注入 DESKPET_BACKEND_DIR 让 Tauri 跑当前 checkout 的后端
$env:DESKPET_BACKEND_DIR = "G:\projects\deskpet\backend"
$env:DESKPET_PYTHON      = "G:\projects\deskpet\backend\.venv\Scripts\python.exe"
$env:DESKPET_BACKEND_PORT = "8100"
$env:DESKPET_DEV_MODE    = "1"
$log = "G:\projects\deskpet\plans\2026-06-22-context-and-agent-optimization\exec\tauri-dev-P0.log"
Set-Location "G:\projects\deskpet\tauri-app"
# 合并 stdout+stderr 到日志 (backend structlog 走 stderr→inherit→落这)
& npx tauri dev *>&1 | Tee-Object -FilePath $log
