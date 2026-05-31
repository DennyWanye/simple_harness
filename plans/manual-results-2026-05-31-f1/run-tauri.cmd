@echo off
REM F1 真机 windows-mcp 测试 — 启动主 checkout 的 tauri dev, 连 worktree 8500 F1 backend
set DESKPET_BACKEND_PORT=8500
set DESKPET_VITE_PORT=5573
set DESKPET_USER_DATA=G:\projects\deskpet-stage2-f1f2\.dev-userdata
set DESKPET_DEV_MODE=1
cd /d G:\projects\deskpet\tauri-app
echo [run-tauri] starting tauri dev (backend=8500 vite=5573) at %DATE% %TIME%
npx tauri dev --config "{\"build\":{\"devUrl\":\"http://localhost:5573\"}}" > "G:\projects\deskpet-stage2-f1f2\plans\manual-results-2026-05-31-f1\tauridev.log" 2>&1
echo [run-tauri] tauri dev EXITED code %ERRORLEVEL% at %DATE% %TIME% >> "G:\projects\deskpet-stage2-f1f2\plans\manual-results-2026-05-31-f1\tauridev.log"
