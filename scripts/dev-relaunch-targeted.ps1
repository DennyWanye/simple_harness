# Targeted dev relaunch: kill ONLY deskpet.exe (its backend python dies with
# it via the KILL_ON_JOB_CLOSE job object) + the deskpet vite node — never the
# user's unrelated python (pip installs, blender-mcp, uv).
$repo    = "G:\projects\deskpet"
$backend = Join-Path $repo "backend"
$tauri   = Join-Path $repo "tauri-app"
$venvPy  = Join-Path $backend ".venv\Scripts\python.exe"

cmd /c "taskkill /F /IM deskpet.exe 2>nul 1>nul"
# belt-and-suspenders: also kill any python still running THIS repo's main.py
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -EA 0 |
  Where-Object { $_.CommandLine -match 'deskpet\\backend\\main\.py' } |
  ForEach-Object { cmd /c "taskkill /F /PID $($_.ProcessId) 2>nul 1>nul" }
# deskpet vite (node running vite/tauri under this repo)
Get-CimInstance Win32_Process -Filter "Name='node.exe'" -EA 0 |
  Where-Object { $_.CommandLine -match 'deskpet' -and $_.CommandLine -match 'vite|tauri' } |
  ForEach-Object { cmd /c "taskkill /F /PID $($_.ProcessId) 2>nul 1>nul" }
Start-Sleep -Seconds 2

$env:DESKPET_USER_DATA_DIR = Join-Path $backend "userdata"
$env:DESKPET_DEV_MODE      = "1"
$env:DESKPET_PYTHON        = $venvPy
$env:DESKPET_BACKEND_DIR   = $backend

Write-Host "==> Relaunching Tauri dev (targeted; backend reloaded with rename handler)"
Set-Location $tauri
npm run tauri dev
