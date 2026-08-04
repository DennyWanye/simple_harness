$ErrorActionPreference = "Stop"
$repoRoot = "F:\projects\deskpet"
$python = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
$env:DESKPET_NODE = "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$logPath = Join-Path $repoRoot "plans\2026-07-23-universal-action-and-capability-packs\manual-results-2026-07-24\last-mile-strict.log"

Set-Location $repoRoot
& $python scripts\acceptance\last_mile_smoke.py --strict *>&1 |
    Tee-Object -FilePath $logPath
exit $LASTEXITCODE
