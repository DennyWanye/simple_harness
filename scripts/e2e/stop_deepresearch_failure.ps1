[CmdletBinding()]
param([string]$StatePath = "")

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $StatePath) {
    $StatePath = Join-Path $RepoRoot "plans\2026-07-15-deepresearch-wide-topic-reliability\evidence\failure-processes.json"
}
$StatePath = [IO.Path]::GetFullPath($StatePath)
if (-not (Test-Path -LiteralPath $StatePath)) { throw "Launch state not found: $StatePath" }
$state = Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json

function Stop-RecordedProcess {
    param([object]$Record, [string]$Label)
    $process = Get-Process -Id ([int]$Record.pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) { return }
    $actualPath = [IO.Path]::GetFullPath($process.Path)
    $expectedPath = [IO.Path]::GetFullPath([string]$Record.path)
    $actualTicks = $process.StartTime.ToUniversalTime().Ticks
    if ($actualPath -ne $expectedPath -or $actualTicks -ne [int64]$Record.start_time_utc_ticks) {
        throw "$Label PID identity mismatch; refusing to stop PID $($Record.pid)"
    }
    Stop-Process -Id $process.Id -Force
}

Stop-RecordedProcess -Record $state.app -Label "DeskPet test app"
Stop-RecordedProcess -Record $state.cli -Label "Tauri CLI"

foreach ($port in @([int]$state.backend_port, [int]$state.vite_port)) {
    $owners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    foreach ($owner in $owners) {
        $process = Get-Process -Id ([int]$owner.OwningProcess) -ErrorAction SilentlyContinue
        if ($null -eq $process) { continue }
        $path = [IO.Path]::GetFullPath($process.Path)
        $started = $process.StartTime.ToUniversalTime().Ticks
        $backendPrefix = [IO.Path]::GetFullPath((Join-Path $RepoRoot "backend")) + [IO.Path]::DirectorySeparatorChar
        $buildPythonPrefix = [IO.Path]::GetFullPath((Join-Path $RepoRoot ".build\python311")) + [IO.Path]::DirectorySeparatorChar
        $recordedCliPath = [IO.Path]::GetFullPath([string]$state.cli.path)
        $allowedPath = $path -eq $recordedCliPath -or
            $path.StartsWith($backendPrefix, [StringComparison]::OrdinalIgnoreCase) -or
            $path.StartsWith($buildPythonPrefix, [StringComparison]::OrdinalIgnoreCase)
        if (-not $allowedPath -or $started -lt [int64]$state.launch_time_utc_ticks) {
            throw "Port $port owner identity mismatch; refusing to stop PID $($process.Id) at $path"
        }
        Stop-Process -Id $process.Id -Force
    }
}

for ($attempt = 0; $attempt -lt 40; $attempt++) {
    $appAlive = Get-Process -Id ([int]$state.app.pid) -ErrorAction SilentlyContinue
    $cliAlive = Get-Process -Id ([int]$state.cli.pid) -ErrorAction SilentlyContinue
    $listeners = @(
        Get-NetTCPConnection -LocalPort ([int]$state.backend_port), ([int]$state.vite_port) -State Listen -ErrorAction SilentlyContinue
    )
    if ($null -eq $appAlive -and $null -eq $cliAlive -and $listeners.Count -eq 0) { break }
    Start-Sleep -Milliseconds 250
}
if (Get-Process -Id ([int]$state.app.pid) -ErrorAction SilentlyContinue) { throw "Recorded DeskPet test app is still running" }
if (Get-Process -Id ([int]$state.cli.pid) -ErrorAction SilentlyContinue) { throw "Recorded Tauri CLI is still running" }
foreach ($port in @([int]$state.backend_port, [int]$state.vite_port)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "Port $port is still listening" }
}

$state | Add-Member -NotePropertyName stopped_at -NotePropertyValue ((Get-Date).ToString("o")) -Force
$state | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 -LiteralPath $StatePath
Write-Output "DEEPRESEARCH_FAILURE_STOPPED=1"
