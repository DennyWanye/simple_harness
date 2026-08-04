[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, ParameterSetName = "Start")]
    [ValidatePattern('^E2E-CTX-(0[1-9]|1[01])$')]
    [string]$CaseId,
    [int]$BackendPort = 8300,
    [int]$VitePort = 5373,
    [string]$UserDataDir = "",
    [string]$Python = "",
    [string]$BackendDir = "",
    [string]$Config = "",
    [ValidateSet("Default", "On", "Off")]
    [string]$ContextOSMode = "Default",
    [string]$Log = "",
    [string]$ResultRoot = "",
    [string]$StatePath = "",
    [string[]]$SessionId = @("default"),
    [ValidatePattern('^E2E-CTX-(0[1-9]|1[01])$')]
    [string]$ReuseUserDataFrom = "",
    [ValidateRange(8192, 2000000)]
    [int]$ModelWindow = 8192,
    [Parameter(Mandatory = $true, ParameterSetName = "Stop")]
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LatestState = Join-Path $RepoRoot "plans\2026-07-13-context-os-v1\test-results\context-os-launch-latest.json"

function Stop-OwnedTree {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return }
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue)
    foreach ($child in $children) { Stop-OwnedTree -ProcessId ([int]$child.ProcessId) }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Stop-RecordedLaunch {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
        throw "Context OS launch state not found: $Path"
    }
    $state = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    $finalControl = [ordered]@{}
    try {
        $finalControl.daemon = Invoke-RestMethod -Uri "http://127.0.0.1:18991/health" -TimeoutSec 2
    } catch { $finalControl.daemon = @{ unavailable = $_.Exception.Message } }
    try {
        $finalControl.provider = Invoke-RestMethod -Uri "http://127.0.0.1:18992/__control/state" -TimeoutSec 2
    } catch { $finalControl.provider = @{ unavailable = $_.Exception.Message } }
    $state | Add-Member -NotePropertyName final_control_state -NotePropertyValue $finalControl -Force
    foreach ($name in @("tauri_pid", "provider_pid", "daemon_pid")) {
        $pidValue = [int]($state.$name)
        if ($pidValue -gt 0) { Stop-OwnedTree -ProcessId $pidValue }
    }
    $state | Add-Member -NotePropertyName stopped_at -NotePropertyValue ((Get-Date).ToString("o")) -Force
    $state | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -LiteralPath $Path
    if ($state.evidence_manifest) {
        $manifest = Get-Content -Raw -LiteralPath $state.evidence_manifest | ConvertFrom-Json
        $manifest | Add-Member -NotePropertyName final_control_state -NotePropertyValue $finalControl -Force
        $manifest | Add-Member -NotePropertyName stopped_at -NotePropertyValue $state.stopped_at -Force
        $manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $state.evidence_manifest
    }
    Write-Output ("CONTEXT_OS_RESULT_ROOT=" + $state.result_root)
    Write-Output "CONTEXT_OS_STOPPED=1"
}

if ($Stop) {
    $stopState = $StatePath
    if (-not $stopState -and $ResultRoot) { $stopState = Join-Path $ResultRoot "launch-state.json" }
    if (-not $stopState) { $stopState = $LatestState }
    Stop-RecordedLaunch -Path $stopState
    exit 0
}

if (-not $ResultRoot) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $ResultRoot = Join-Path $RepoRoot "plans\2026-07-13-context-os-v1\test-results\manual-$stamp"
}
$ResultRoot = [IO.Path]::GetFullPath($ResultRoot)
$CaseRoot = Join-Path $ResultRoot ("cases\" + $CaseId)
if ($ReuseUserDataFrom) {
    if ($CaseId -ne "E2E-CTX-08" -or $ReuseUserDataFrom -ne "E2E-CTX-07") {
        throw "Only E2E-CTX-08 may explicitly reuse E2E-CTX-07 userdata for restart recovery."
    }
    if ($UserDataDir) { throw "-UserDataDir and -ReuseUserDataFrom are mutually exclusive." }
    $UserDataDir = Join-Path $ResultRoot ("cases\" + $ReuseUserDataFrom + "\userdata")
}
if (-not $UserDataDir) { $UserDataDir = Join-Path $CaseRoot "userdata" }
if (-not $BackendDir) { $BackendDir = Join-Path $RepoRoot "backend" }
if (-not $Python) { $Python = Join-Path $BackendDir ".venv\Scripts\python.exe" }
if (-not $Config) { $Config = Join-Path $RepoRoot "config.toml" }
if (-not $Log) { $Log = Join-Path $CaseRoot "logs\tauri-dev.log" }
$UserDataDir = [IO.Path]::GetFullPath($UserDataDir)
$BackendDir = [IO.Path]::GetFullPath($BackendDir)
$Python = [IO.Path]::GetFullPath($Python)
$Config = [IO.Path]::GetFullPath($Config)
$Log = [IO.Path]::GetFullPath($Log)
$StatePath = Join-Path $CaseRoot "launch-state.json"
$LogsDir = Join-Path $CaseRoot "logs"
$EvidenceManifest = Join-Path $CaseRoot "evidence-manifest.json"

foreach ($required in @($Python, $BackendDir, $Config)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required path not found: $required" }
}
foreach ($dir in @($ResultRoot, $CaseRoot, $UserDataDir, $LogsDir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}
if ($ContextOSMode -ne "Default") {
    $configText = [IO.File]::ReadAllText($Config)
    $matches = [regex]::Matches(
        $configText,
        '(?m)^\s*context_os_v1\s*=\s*(?:true|false)\s*$'
    )
    if ($matches.Count -ne 1) {
        throw "Context OS mode override requires exactly one context_os_v1 assignment in $Config"
    }
    $enabledText = if ($ContextOSMode -eq "On") { "true" } else { "false" }
    $configText = [regex]::Replace(
        $configText,
        '(?m)^(\s*context_os_v1\s*=\s*)(?:true|false)(\s*)$',
        ('$1' + $enabledText + '$2')
    )
    $isolatedConfig = Join-Path $ResultRoot ("context-os-" + $ContextOSMode.ToLowerInvariant() + ".toml")
    [IO.File]::WriteAllText($isolatedConfig, $configText, (New-Object Text.UTF8Encoding($false)))
    $Config = $isolatedConfig
}
# Force the isolated fixture model to the deterministic 8K window used by
# compact/coverage cases. This file lives only under the disposable E2E
# userdata root and never changes the user's global model overrides.
$modelOverrideText = @'
[models."ctx-primary"]
context_window = 8192
effective_pct = 0.95
'@
$modelOverrideText = $modelOverrideText.Replace("context_window = 8192", "context_window = $ModelWindow")
[IO.File]::WriteAllText(
    (Join-Path $UserDataDir "model_overrides.toml"),
    $modelOverrideText,
    (New-Object Text.UTF8Encoding($false))
)

foreach ($port in @($BackendPort, $VitePort, 18991, 18992)) {
    $owner = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($owner) { throw "Required port $port is already listening (PID $($owner[0].OwningProcess)); launcher will not kill unrelated processes." }
}

$env:DESKPET_BACKEND_PORT = [string]$BackendPort
$env:DESKPET_VITE_PORT = [string]$VitePort
$env:DESKPET_USER_DATA_DIR = $UserDataDir
$env:DESKPET_DEV_MODE = "1"
$env:DESKPET_PYTHON = $Python
$env:DESKPET_BACKEND_DIR = $BackendDir
$env:DESKPET_CONFIG = $Config
$env:DESKPET_CONTEXT_OS_E2E_CASE_ID = $CaseId
if ($SessionId.Count -ne 1 -and $CaseId -in @("E2E-CTX-02", "E2E-CTX-10")) {
    throw "$CaseId requires exactly one session for request-local fixture grants."
}
$env:DESKPET_CONTEXT_OS_E2E_SESSION_ID = [string]$SessionId[0]
$env:DESKPET_CONTEXT_OS_E2E_PROVIDER_URL = "http://127.0.0.1:18992/v1"
$env:DESKPET_CONTEXT_OS_E2E_HOOK_TIMEOUT_MS = "500"
$fixtureCatalogEnabled = $CaseId -ne "E2E-CTX-11"
if ($fixtureCatalogEnabled) {
    $env:DESKPET_CONTEXT_OS_E2E_FIXTURE_CATALOG = "1"
    $env:DESKPET_CONTEXT_OS_E2E_DAEMON_URL = "http://127.0.0.1:18991"
    $env:DESKPET_CONTEXT_OS_E2E_FAULT_HOOKS = "1"
    $env:DESKPET_CONTEXT_OS_E2E_MCP_CONFIG = Join-Path $RepoRoot "scripts\e2e\fixtures\context-os-mcp.json"
} else {
    # The OFF rollback golden is calibrated against the product's built-in
    # legacy registry. Do not pollute it with the 500 E2E-only MCP specs, but
    # keep the loopback daemon/provider trust gate so UI requests still use
    # the auditable provider seam rather than the real relay.
    $env:DESKPET_CONTEXT_OS_E2E_DAEMON_URL = "http://127.0.0.1:18991"
    $env:DESKPET_CONTEXT_OS_E2E_FIXTURE_CATALOG = "0"
    Remove-Item Env:DESKPET_CONTEXT_OS_E2E_MCP_CONFIG -ErrorAction SilentlyContinue
    $env:DESKPET_CONTEXT_OS_E2E_FAULT_HOOKS = "1"
}
$env:DESKPET_CONTEXT_OS_E2E_CONFIG = Join-Path $RepoRoot "scripts\e2e\fixtures\context-os-8k.toml"
$env:DESKPET_E2E_PROVIDER_DIRECT = "1"
$env:DESKPET_CLOUD_API_KEY = "ctx-e2e-local-key"
if ($CaseId -eq "E2E-CTX-10") {
    # Three real tool rounds plus one reserved tools=None final attempt.
    $env:DESKPET_CONTEXT_OS_E2E_MAX_ITERATIONS = "4"
} else {
    Remove-Item Env:DESKPET_CONTEXT_OS_E2E_MAX_ITERATIONS -ErrorAction SilentlyContinue
}
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if (-not (Test-Path -LiteralPath (Join-Path $cargoBin "cargo.exe"))) {
    throw "Cargo not found after Rust installation: $cargoBin"
}
$env:PATH = "$cargoBin;$env:PATH"

$daemonLog = Join-Path $LogsDir "fixture-daemon.stdout.log"
$daemonErr = Join-Path $LogsDir "fixture-daemon.stderr.log"
$providerLog = Join-Path $LogsDir "provider-fixture.stdout.log"
$providerErr = Join-Path $LogsDir "provider-fixture.stderr.log"
$fixtureEvents = Join-Path $LogsDir "fixture.jsonl"
$providerEvents = Join-Path $LogsDir "provider-seam.jsonl"
$daemon = $null
$provider = $null
$tauri = $null

function Wait-Health {
    param([string]$Uri, [string]$Label)
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        try {
            $null = Invoke-RestMethod -Uri $Uri -TimeoutSec 2
            return
        } catch { Start-Sleep -Milliseconds 100 }
    }
    throw "$Label health check failed: $Uri"
}

try {
    $daemon = Start-Process -FilePath $Python -ArgumentList @(
        (Join-Path $RepoRoot "scripts\e2e\context_os_mcp_daemon.py"), "--log", $fixtureEvents
    ) -WorkingDirectory $RepoRoot -RedirectStandardOutput $daemonLog -RedirectStandardError $daemonErr -WindowStyle Hidden -PassThru
    Wait-Health -Uri "http://127.0.0.1:18991/health" -Label "daemon"
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18991/reset" -ContentType "application/json" -Body "{}" -TimeoutSec 2 | Out-Null
    foreach ($sid in $SessionId) {
        if ([string]::IsNullOrWhiteSpace($sid)) { throw "Session ids must be non-empty." }
        $allowSessionBody = @{ session_id = $sid } | ConvertTo-Json -Compress
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18991/allow-session" -ContentType "application/json" -Body $allowSessionBody -TimeoutSec 2 | Out-Null
    }
    $daemonInitial = Invoke-RestMethod -Uri "http://127.0.0.1:18991/health" -TimeoutSec 2

    $provider = Start-Process -FilePath $Python -ArgumentList @(
        (Join-Path $RepoRoot "scripts\e2e\context_os_provider_seam.py"), "--log", $providerEvents
    ) -WorkingDirectory $RepoRoot -RedirectStandardOutput $providerLog -RedirectStandardError $providerErr -WindowStyle Hidden -PassThru
    Wait-Health -Uri "http://127.0.0.1:18992/v1/models" -Label "provider"
    $providerInitial = Invoke-RestMethod -Uri "http://127.0.0.1:18992/__control/state" -TimeoutSec 2
    $providerFaultCount = @($providerInitial.faults.PSObject.Properties).Count
    $providerPausedCount = @($providerInitial.paused).Count
    if ($providerFaultCount -ne 0 -or $providerInitial.fallback -or $providerInitial.force_finish -or $providerPausedCount -ne 0 -or $providerInitial.scenario_count -ne 0) {
        throw "Provider fixture did not start with a clean per-case control state."
    }
    $catalogBody = @{ model = "ctx-primary"; window = $ModelWindow } | ConvertTo-Json -Compress
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18992/__control/catalog" -ContentType "application/json" -Body $catalogBody -TimeoutSec 2 | Out-Null
    # Evidence must describe the configured case, not the provider's boot
    # defaults captured before the per-case model window override.
    $providerConfigured = Invoke-RestMethod -Uri "http://127.0.0.1:18992/__control/state" -TimeoutSec 2

    $tauriApp = Join-Path $RepoRoot "tauri-app"
    $tauriCli = Join-Path $tauriApp "node_modules\@tauri-apps\cli\tauri.js"
    $node = "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    if (-not (Test-Path -LiteralPath $node)) { $node = (Get-Command node.exe -ErrorAction Stop).Source }
    if (-not (Test-Path -LiteralPath $tauriCli)) { throw "Tauri CLI not found: $tauriCli" }
    $devConfig = Join-Path $ResultRoot "tauri-dev-config.json"
    $viteCommand = "$node node_modules/vite/bin/vite.js --mode relay"
    @{
        build = @{
            devUrl = "http://localhost:$VitePort"
            beforeDevCommand = $viteCommand
        }
    } | ConvertTo-Json -Depth 4 -Compress | Set-Content -Encoding ASCII -LiteralPath $devConfig

    # A single PowerShell owner captures both streams into the required log;
    # Tauri alone starts its configured Vite and backend children.
    $quotedNode = $node.Replace("'", "''")
    $quotedCli = $tauriCli.Replace("'", "''")
    $quotedCfg = $devConfig.Replace("'", "''")
    $quotedLog = $Log.Replace("'", "''")
    $command = "& '$quotedNode' '$quotedCli' dev --config '$quotedCfg' *>> '$quotedLog'"
    $tauri = Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command
    ) -WorkingDirectory $tauriApp -WindowStyle Hidden -PassThru

    $state = [ordered]@{
        schema_version = 2; case_id = $CaseId; result_root = $ResultRoot; case_root = $CaseRoot; userdata = $UserDataDir
        session_ids = @($SessionId); reused_userdata_from = $ReuseUserDataFrom
        context_os_mode = $ContextOSMode; config = $Config
        log = $Log; daemon_pid = $daemon.Id; provider_pid = $provider.Id
        tauri_pid = $tauri.Id; backend_port = $BackendPort; vite_port = $VitePort
        evidence_manifest = $EvidenceManifest
        started_at = (Get-Date).ToString("o")
    }
    $manifest = [ordered]@{
        schema_version = 1; case_id = $CaseId
        isolation = @{ userdata = $UserDataDir; session_ids = @($SessionId); reused_userdata_from = $ReuseUserDataFrom }
        control_reset = @{ daemon = $daemonInitial; provider = $providerConfigured }
        fixture_catalog_enabled = $fixtureCatalogEnabled
        context_os_mode = $ContextOSMode
        config = $Config
        evidence_files = @{
            backend_log = $Log; fixture_log = $fixtureEvents; provider_log = $providerEvents
            launch_state = $StatePath
        }
        started_at = $state.started_at
    }
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $EvidenceManifest
    $state | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -LiteralPath $StatePath
    $state | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -LiteralPath $LatestState
    Write-Output "CONTEXT_OS_RESULT_ROOT=$ResultRoot"
    Write-Output "CONTEXT_OS_CASE_ROOT=$CaseRoot"
    Write-Output "CONTEXT_OS_EVIDENCE_MANIFEST=$EvidenceManifest"
    Write-Output "CONTEXT_OS_STATE=$StatePath"
    Write-Output "CONTEXT_OS_DAEMON_PID=$($daemon.Id)"
    Write-Output "CONTEXT_OS_PROVIDER_PID=$($provider.Id)"
    Write-Output "CONTEXT_OS_TAURI_PID=$($tauri.Id)"
} catch {
    if ($tauri) { Stop-OwnedTree -ProcessId $tauri.Id }
    if ($provider) { Stop-OwnedTree -ProcessId $provider.Id }
    if ($daemon) { Stop-OwnedTree -ProcessId $daemon.Id }
    throw
}
