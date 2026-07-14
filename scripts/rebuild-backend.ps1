# Rebuild frozen backend (CPU venv, thin bundle) into F:\deskpet-build\dist.
# dist-portable junction -> this dist, so the NSIS bundle picks it up.
# kill any lingering frozen backend holding the dist exe (swallow stderr at
# cmd level so "process not found" doesn't trip ErrorActionPreference=Stop)
cmd /c "taskkill /F /IM deskpet-backend.exe 2>nul 1>nul"
Start-Sleep 1
$ErrorActionPreference = "Stop"
$env:DESKPET_BUNDLE_MODELS = "0"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $repoRoot "backend")
$buildPython = "F:\deskpet-build\venv\Scripts\python.exe"
if (-not (Test-Path $buildPython)) {
    $buildPython = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
}
& $buildPython -m PyInstaller deskpet-backend.spec `
    --noconfirm --clean `
    --distpath "F:\deskpet-build\dist" `
    --workpath "F:\deskpet-build\build"
Write-Host "REBUILD_EXIT=$LASTEXITCODE"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
