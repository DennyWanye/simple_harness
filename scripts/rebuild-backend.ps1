# Rebuild frozen backend (CPU venv, thin bundle) into F:\deskpet-build\dist.
# dist-portable junction -> this dist, so the NSIS bundle picks it up.
# kill any lingering frozen backend holding the dist exe (swallow stderr at
# cmd level so "process not found" doesn't trip ErrorActionPreference=Stop)
cmd /c "taskkill /F /IM deskpet-backend.exe 2>nul 1>nul"
Start-Sleep 1
$ErrorActionPreference = "Stop"
$env:DESKPET_BUNDLE_MODELS = "0"
Set-Location "G:\projects\deskpet\backend"
& "F:\deskpet-build\venv\Scripts\python.exe" -m PyInstaller deskpet-backend.spec `
    --noconfirm --clean `
    --distpath "F:\deskpet-build\dist" `
    --workpath "F:\deskpet-build\build"
Write-Host "REBUILD_EXIT=$LASTEXITCODE"
