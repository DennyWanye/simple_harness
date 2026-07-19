param(
    [string]$DistPath,
    [string]$WorkPath,
    [string]$BrowserCacheRoot,
    [string]$BrowserArchive,
    [string]$BuildPython,
    [switch]$Offline,
    [switch]$BundleModels
)

# Production backend build. The Playwright browser is acquired into a private,
# short cache and validated before PyInstaller reads the spec.
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "backend"
if (-not $BuildPython) {
    $BuildPython = if ($env:DESKPET_BUILD_PYEXE) { $env:DESKPET_BUILD_PYEXE } else { Join-Path $backendDir ".venv\Scripts\python.exe" }
}
if (-not $DistPath) { $DistPath = Join-Path $backendDir "dist" }
if (-not $WorkPath) { $WorkPath = Join-Path $backendDir "build" }
if (-not $BrowserCacheRoot) { $BrowserCacheRoot = Join-Path $env:LOCALAPPDATA "DPW\pw-161-1228" }

if (-not (Test-Path -LiteralPath $BuildPython -PathType Leaf)) {
    throw "Build Python not found: $BuildPython"
}
$DistPath = [IO.Path]::GetFullPath($DistPath)
$WorkPath = [IO.Path]::GetFullPath($WorkPath)
$BrowserCacheRoot = [IO.Path]::GetFullPath($BrowserCacheRoot)

# Fail before deletion/build if the predicted deepest packaged browser path is
# unsafe for Windows tooling. This check uses the same locked runtime contract.
& $BuildPython -c "import sys; sys.path.insert(0, r'$backendDir'); from deskpet.playwright_bundle import assert_short_build_paths; from pathlib import Path; assert_short_build_paths(Path(r'$DistPath'), Path(r'$WorkPath'), Path(r'$BrowserCacheRoot'))"
if ($LASTEXITCODE -ne 0) { throw "Playwright short-path assertion failed" }

function Remove-BuildTree([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $root = [IO.Path]::GetPathRoot($full).TrimEnd('\')
    if ($full.Length -lt 8 -or $full -eq $root -or $full -eq $repoRoot.TrimEnd('\')) {
        throw "Refusing unsafe recursive delete: $full"
    }
    Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction SilentlyContinue
}

$acquireArgs = @(
    (Join-Path $repoRoot "scripts\acquire_playwright_browser.py"),
    "--cache-root", $BrowserCacheRoot,
    "--json"
)
if ($BrowserArchive) { $acquireArgs += @("--archive", [IO.Path]::GetFullPath($BrowserArchive)) }
if ($Offline) { $acquireArgs += "--offline" }
& $BuildPython @acquireArgs
if ($LASTEXITCODE -ne 0) { throw "Pinned Playwright browser acquisition failed" }

$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $BrowserCacheRoot "playwright-browsers"
$env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1"
# The production NSIS artifact must stay below the 32-bit makensis mmap
# ceiling.  Models are provisioned by ModelProvisioner on first run, while the
# pinned Playwright browser remains part of the offline-capable installer.
# MSI/fat-bundle jobs can opt back in explicitly with -BundleModels.
$env:DESKPET_BUNDLE_MODELS = if ($BundleModels) { "1" } else { "0" }
Write-Host ("[build_backend] bundle models: {0}" -f $env:DESKPET_BUNDLE_MODELS)
Remove-BuildTree $DistPath
Remove-BuildTree $WorkPath

$torchVer = & $BuildPython -c "import torch; print(torch.__version__)" 2>$null
if ($torchVer -and $torchVer -notmatch "\+cpu$") {
    throw "Detected torch '$torchVer'; production backend bundling requires the CPU-only wheel."
}

Push-Location $backendDir
try {
    $t0 = Get-Date
    & $BuildPython -m PyInstaller deskpet-backend.spec --noconfirm --clean --distpath $DistPath --workpath $WorkPath
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
    Write-Host ("[build_backend] build time: {0:N1}s" -f ((Get-Date) - $t0).TotalSeconds)
}
finally {
    Pop-Location
}

$out = Join-Path $DistPath "deskpet-backend"
$exe = Join-Path $out "deskpet-backend.exe"
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Expected frozen backend not produced: $exe" }
& $BuildPython (Join-Path $repoRoot "scripts\assert_playwright_packaging.py") $out --json
if ($LASTEXITCODE -ne 0) { throw "Frozen Playwright packaging assertion failed" }

$bytes = (Get-ChildItem -LiteralPath $out -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("[build_backend] frozen backend: {0}; total size: {1:N1} MB" -f $exe, ($bytes / 1MB))
