# Rebuild the production onedir into the established portable target. Browser
# acquisition and all fail-closed assertions stay centralized in
# build_backend.ps1. This script deliberately does not kill running processes.
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$buildPython = "F:\deskpet-build\venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $buildPython -PathType Leaf)) {
    $buildPython = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
}
$archive = Join-Path $env:LOCALAPPDATA "DeskPetBuildCache\playwright-1.61.0-r1228\downloads\chrome-headless-shell-win64-149.0.7827.55.zip"
& (Join-Path $PSScriptRoot "build_backend.ps1") `
    -BuildPython $buildPython `
    -DistPath "F:\deskpet-build\dist" `
    -WorkPath "F:\deskpet-build\build" `
    -BrowserCacheRoot "F:\deskpet-build\pw-161-1228" `
    -BrowserArchive $archive `
    -Offline
exit $LASTEXITCODE
