param(
    [ValidateSet("S1-S5", "S6")]
    [string]$ScenarioBatch = "S1-S5"
)

$ErrorActionPreference = "Stop"
$repoRoot = "F:\projects\deskpet"
$evidenceRoot = Join-Path $repoRoot "plans\2026-07-23-universal-action-and-capability-packs\manual-results-2026-07-24"
$fixturePath = Join-Path $evidenceRoot "fixture-manifest.json"
$fixture = Get-Content -Raw -Encoding utf8 -LiteralPath $fixturePath |
    ConvertFrom-Json

$env:Path = "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;$env:Path"
$env:DESKPET_BACKEND_DIR = Join-Path $repoRoot "backend"
$env:DESKPET_PYTHON = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
$env:DESKPET_BACKEND_PORT = "18120"
$env:DESKPET_VITE_PORT = "15193"
$env:DESKPET_DEV_MODE = "1"
$env:DESKPET_USER_DATA_DIR = Join-Path $repoRoot ".e2e-universal-action\userdata"
$env:DESKPET_WORKSPACE_DIR = Join-Path $repoRoot ".e2e-universal-action\workspaces"
$env:CARGO_TARGET_DIR = Join-Path $repoRoot ".build\cargo-universal-action-validation"
$env:DESKPET_CAPABILITY_SOURCES_JSON = $fixture.fixtures.ultraforge_badhash.environment.DESKPET_CAPABILITY_SOURCES_JSON
$env:DESKPET_ULTRAFORGE_CANARY = $fixture.fixtures.ultraforge_badhash.environment.DESKPET_ULTRAFORGE_CANARY

if ($ScenarioBatch -eq "S6") {
    $env:DESKPET_CAPABILITY_E2E_CASE_ID = $fixture.fixtures.godot_fail_once.environment.DESKPET_CAPABILITY_E2E_CASE_ID
    $logName = "tauri-s6.log"
}
else {
    Remove-Item Env:DESKPET_CAPABILITY_E2E_CASE_ID -ErrorAction SilentlyContinue
    $logName = "tauri-s1-s5.log"
}

$logPath = Join-Path $evidenceRoot $logName
$node = "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$tauriCli = Join-Path $repoRoot "tauri-app\node_modules\@tauri-apps\cli\tauri.js"
$devConfig = Join-Path $evidenceRoot "tauri-dev-config.json"
Set-Location (Join-Path $repoRoot "tauri-app")
$ErrorActionPreference = "Continue"
& $node $tauriCli dev --config $devConfig *>&1 |
    Tee-Object -FilePath $logPath
exit $LASTEXITCODE
