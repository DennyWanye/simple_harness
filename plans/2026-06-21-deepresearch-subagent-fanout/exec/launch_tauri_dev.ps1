# Phase 1 windows-mcp 真机测试：启动 Tauri dev（跑本树 backend）
$ErrorActionPreference = "SilentlyContinue"
$ROOT = "G:\projects\deskpet"
$RESULT = "$ROOT\plans\manual-results-2026-06-21-wi8-deepresearch"
New-Item -ItemType Directory -Force "$RESULT\screenshots" | Out-Null

# 1. 清理旧进程（防端口/孤儿）
taskkill /F /IM deskpet.exe 2>$null | Out-Null
taskkill /F /IM deskpet-backend.exe 2>$null | Out-Null
Get-Process node -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*deskpet*' } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# 2. 注入 env：让 Tauri spawn 本树 backend（非 frozen exe）+ dev 模式
$env:DESKPET_BACKEND_DIR = "$ROOT\backend"
$env:DESKPET_PYTHON      = "$ROOT\backend\.venv\Scripts\python.exe"
$env:DESKPET_DEV_MODE    = "1"
Remove-Item Env:\DESKPET_DEEPRESEARCH_DIR -ErrorAction SilentlyContinue

# 3. 启动 Tauri dev（自管 vite，勿手动起 backend/vite）
Set-Location "$ROOT\tauri-app"
npx tauri dev *>&1 | Tee-Object -FilePath "$RESULT\tauri-dev.log"
