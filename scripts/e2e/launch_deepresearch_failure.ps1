[CmdletBinding()]
param(
    [string]$ResultRoot = "",
    [string]$NodePath = "",
    [int]$BackendPort = 8116,
    [int]$VitePort = 5186,
    [ValidateSet("failure", "success")]
    [string]$Profile = "failure"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$TauriApp = Join-Path $RepoRoot "tauri-app"
$BackendDir = Join-Path $RepoRoot "backend"
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
$Node = $NodePath
if (-not $Node) { $Node = $env:DESKPET_NODE }
if (-not $Node) {
    $nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($nodeCommand) { $Node = $nodeCommand.Source }
}
if (-not $Node) {
    $runtimeRoot = Join-Path $env:LOCALAPPDATA "OpenAI\Codex\runtimes\cua_node"
    $Node = Get-ChildItem -LiteralPath $runtimeRoot -Filter node.exe -Recurse -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $Node) { throw "Node.js not found. Pass -NodePath or set DESKPET_NODE." }
$Node = [IO.Path]::GetFullPath($Node)
$TauriCli = Join-Path $TauriApp "node_modules\@tauri-apps\cli\tauri.js"
$ExpectedApp = [IO.Path]::GetFullPath((Join-Path $TauriApp "src-tauri\target\debug\deskpet.exe"))

if (-not $ResultRoot) {
    $ResultRoot = Join-Path $RepoRoot "plans\2026-07-15-deepresearch-wide-topic-reliability\evidence"
}
$ResultRoot = [IO.Path]::GetFullPath($ResultRoot)
if ($Profile -eq "success") {
    if ($BackendPort -eq 8116) { $BackendPort = 8100 }
    if ($VitePort -eq 5186) { $VitePort = 5173 }
    $UserDataDir = Join-Path $RepoRoot "plans\manual-results-2026-07-14-search-gateway-deepresearch\userdata"
    $ConfigPath = Join-Path $RepoRoot "config.toml"
} else {
    $UserDataDir = Join-Path $ResultRoot "failure-userdata"
    $ConfigPath = Join-Path $ResultRoot "failure-config.toml"
}
$DevConfigPath = Join-Path $ResultRoot "$Profile-tauri-dev-config.json"
$StdoutLog = Join-Path $ResultRoot "$Profile-tauri.stdout.log"
$StderrLog = Join-Path $ResultRoot "$Profile-tauri.stderr.log"
$StatePath = Join-Path $ResultRoot "$Profile-processes.json"

foreach ($required in @($Python, $Node, $TauriCli, (Join-Path $RepoRoot "config.toml"))) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required path not found: $required" }
}
foreach ($port in @($BackendPort, $VitePort)) {
    $owner = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($owner) { throw "Port $port is already listening (PID $($owner[0].OwningProcess)); refusing to kill an unrelated process." }
}
New-Item -ItemType Directory -Force -Path $ResultRoot, $UserDataDir | Out-Null

function Set-TomlSectionValue {
    param([string]$Text, [string]$Section, [string]$Key, [string]$Value)
    $sectionPattern = "(?ms)(^\[" + [regex]::Escape($Section) + "\]\s*\r?\n)(.*?)(?=^\[|\z)"
    $match = [regex]::Match($Text, $sectionPattern)
    if (-not $match.Success) { throw "TOML section [$Section] not found" }
    $body = $match.Groups[2].Value
    $keyPattern = "(?m)^\s*" + [regex]::Escape($Key) + "\s*=.*$"
    if ([regex]::IsMatch($body, $keyPattern)) {
        $body = [regex]::Replace($body, $keyPattern, "$Key = $Value", 1)
    } else {
        $body = "$body$Key = $Value`r`n"
    }
    return $Text.Substring(0, $match.Groups[2].Index) + $body + $Text.Substring($match.Groups[2].Index + $match.Groups[2].Length)
}

if ($Profile -eq "failure") {
    $configText = [IO.File]::ReadAllText((Join-Path $RepoRoot "config.toml"))
    $configText = Set-TomlSectionValue $configText "search_gateway" "providers" '["searxng"]'
    $configText = Set-TomlSectionValue $configText "search_gateway" "searxng_url" '"http://127.0.0.1:9/search"'
    $configText = Set-TomlSectionValue $configText "search_gateway" "per_provider_timeout_s" '0.5'
    $configText = Set-TomlSectionValue $configText "search_gateway" "research_total_timeout_s" '8.0'
    $configText = Set-TomlSectionValue $configText "research" "direct_sources" 'false'
    $configText = Set-TomlSectionValue $configText "research" "source_packs" 'false'
    [IO.File]::WriteAllText($ConfigPath, $configText, (New-Object Text.UTF8Encoding($false)))
}

$viteCommand = "$Node node_modules/vite/bin/vite.js --mode relay"
$tauriConfigPath = Join-Path $TauriApp "src-tauri\tauri.conf.json"
$devConfig = Get-Content -Raw -Encoding UTF8 -LiteralPath $tauriConfigPath | ConvertFrom-Json
$devConfig.build.devUrl = "http://localhost:$VitePort"
$devConfig.build.beforeDevCommand = $viteCommand
$messagePanel = @($devConfig.app.windows | Where-Object { $_.label -eq "message-panel" })
if ($messagePanel.Count -ne 1) {
    throw "Expected exactly one message-panel window in $tauriConfigPath"
}
# The production window remains hidden-by-default.  This generated E2E-only
# override exposes the real message window at startup so coordinate-driven
# testing is not blocked by the non-focusable transparent pet window.
$messagePanel[0].visible = $true
$messagePanel[0].alwaysOnTop = $true
$messagePanel[0].skipTaskbar = $false
$messagePanel[0] | Add-Member -NotePropertyName focus -NotePropertyValue $true -Force
$messagePanel[0] | Add-Member -NotePropertyName focusable -NotePropertyValue $true -Force
$devConfig | ConvertTo-Json -Depth 32 | Set-Content -Encoding UTF8 -LiteralPath $DevConfigPath

$beforeDeskpet = @(
    Get-Process -Name "deskpet" -ErrorAction SilentlyContinue | ForEach-Object { [int]$_.Id }
)
$launchAt = Get-Date
$env:DESKPET_CONFIG = $ConfigPath
$env:DESKPET_USER_DATA_DIR = $UserDataDir
$env:DESKPET_BACKEND_DIR = $BackendDir
$env:DESKPET_PYTHON = $Python
$env:DESKPET_BACKEND_PORT = [string]$BackendPort
$env:DESKPET_VITE_PORT = [string]$VitePort
$env:DESKPET_DEV_MODE = "1"
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if (Test-Path -LiteralPath (Join-Path $cargoBin "cargo.exe")) { $env:PATH = "$cargoBin;$env:PATH" }

$cli = Start-Process -FilePath $Node -ArgumentList @(
    $TauriCli, "dev", "--config", $DevConfigPath
) -WorkingDirectory $TauriApp -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog

$app = $null
for ($attempt = 0; $attempt -lt 180 -and $null -eq $app; $attempt++) {
    Start-Sleep -Milliseconds 500
    $candidates = @(Get-Process -Name "deskpet" -ErrorAction SilentlyContinue | Where-Object {
        $_.Id -notin $beforeDeskpet -and $_.StartTime -ge $launchAt
    })
    foreach ($candidate in $candidates) {
        try { $candidatePath = [IO.Path]::GetFullPath($candidate.Path) } catch { continue }
        if ($candidatePath -eq $ExpectedApp) { $app = $candidate; break }
    }
    if ($cli.HasExited) { throw "Tauri CLI exited before deskpet.exe started. See $StderrLog" }
}
if ($null -eq $app) { throw "Timed out waiting for the isolated DeskPet GUI: $ExpectedApp" }

$state = [ordered]@{
    schema_version = 1
    launch_time_utc_ticks = $launchAt.ToUniversalTime().Ticks
    working_directory = $TauriApp
    config = $ConfigPath
    userdata = $UserDataDir
    backend_port = $BackendPort
    vite_port = $VitePort
    stdout_log = $StdoutLog
    stderr_log = $StderrLog
    cli = @{
        pid = [int]$cli.Id
        path = [IO.Path]::GetFullPath($cli.Path)
        start_time_utc_ticks = $cli.StartTime.ToUniversalTime().Ticks
    }
    app = @{
        pid = [int]$app.Id
        path = [IO.Path]::GetFullPath($app.Path)
        start_time_utc_ticks = $app.StartTime.ToUniversalTime().Ticks
    }
}
$state | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 -LiteralPath $StatePath
Write-Output "DEEPRESEARCH_E2E_PROFILE=$Profile"
Write-Output "DEEPRESEARCH_E2E_STATE=$StatePath"
Write-Output "DEEPRESEARCH_E2E_APP_PID=$($app.Id)"
Write-Output "DEEPRESEARCH_E2E_BACKEND_PORT=$BackendPort"
Write-Output "DEEPRESEARCH_E2E_VITE_PORT=$VitePort"
