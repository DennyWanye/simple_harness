# Phase 1 windows-mcp：干净重启 DeskPet（全硬编码路径，避开变量插值 bug）
# 1. 杀旧实例（含上一会话遗留的 deskpet + backend + tauri dev 子进程）
try { taskkill /F /IM deskpet.exe 2>$null | Out-Null } catch {}
try { taskkill /F /IM deskpet-backend.exe 2>$null | Out-Null } catch {}
Get-Process node  -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*deskpet*' } | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process cargo -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

# 2. env（硬编码）：Tauri spawn 本树 backend（含 WI-8）+ dev 模式 + 默认落盘目录
$env:DESKPET_BACKEND_DIR = "G:\projects\deskpet\backend"
$env:DESKPET_PYTHON      = "G:\projects\deskpet\backend\.venv\Scripts\python.exe"
$env:DESKPET_DEV_MODE    = "1"
Remove-Item Env:\DESKPET_DEEPRESEARCH_DIR -ErrorAction SilentlyContinue

# 3. 启动（cmd /c 硬编码 cwd，确保 npm 在 tauri-app 下跑）
Write-Host "[relaunch] killing done; starting tauri:dev (hardcoded cwd)"
cmd /c "cd /d G:\projects\deskpet\tauri-app && npm run tauri:dev" *>&1 | Tee-Object -FilePath "G:\projects\deskpet\plans\manual-results-2026-06-21-wi8-deepresearch\tauri-dev.log"
