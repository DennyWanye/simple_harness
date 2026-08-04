[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Start", "Status", "Stop")]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')]
    [string]$ScenarioId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')]
    [string]$LaunchId,

    [ValidateRange(1024, 65535)]
    [int]$BackendPort = 18100,

    [ValidateRange(1024, 65535)]
    [int]$VitePort = 15173,

    [string]$ClockUtc = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "The companion growth lifecycle launcher requires Windows."
}
Add-Type -AssemblyName System.Security
if ($BackendPort -eq $VitePort) {
    throw "BackendPort and VitePort must be different."
}

$fileIdentitySource = @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class CompanionGrowthFileIdentity
{
    [StructLayout(LayoutKind.Sequential)]
    private struct BY_HANDLE_FILE_INFORMATION
    {
        public uint FileAttributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle handle,
        out BY_HANDLE_FILE_INFORMATION information);

    public static string Get(string path)
    {
        using (FileStream stream = new FileStream(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.ReadWrite | FileShare.Delete))
        {
            BY_HANDLE_FILE_INFORMATION information;
            if (!GetFileInformationByHandle(stream.SafeFileHandle, out information))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            return String.Format(
                "{0:x8}:{1:x8}:{2:x8}",
                information.VolumeSerialNumber,
                information.FileIndexHigh,
                information.FileIndexLow);
        }
    }
}
'@
Add-Type -TypeDefinition $fileIdentitySource -Language CSharp

$script:RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$script:RuntimeRoot = [IO.Path]::GetFullPath((Join-Path $script:RepoRoot "plans\2026-07-24-human-anchored-companion-growth\evidence\manual-runtime"))
$script:ScenarioRoot = [IO.Path]::GetFullPath((Join-Path $script:RuntimeRoot $ScenarioId))
$script:LaunchRoot = [IO.Path]::GetFullPath((Join-Path $script:ScenarioRoot "launches\$LaunchId"))
$script:UserDataPath = Join-Path $script:ScenarioRoot "user-data"
$script:ManifestPath = Join-Path $script:LaunchRoot "process-manifest.json"
$script:CleanupPath = Join-Path $script:LaunchRoot "cleanup-result.json"
$script:TokenPath = Join-Path $script:LaunchRoot "control-token.dpapi"
$script:ConfigPath = Join-Path $script:LaunchRoot "tauri-dev-config.json"
$script:NpmShimPath = Join-Path $script:LaunchRoot "npm.cmd"
$script:LogPath = Join-Path $script:LaunchRoot "logs\tauri.log"
$script:HelperLogPath = Join-Path $script:LaunchRoot "logs\job-host.log"
$script:HelperErrorPath = Join-Path $script:LaunchRoot "logs\job-host.error.log"
$script:BackendPath = Join-Path $script:RepoRoot "backend"
$script:PythonPath = Join-Path $script:BackendPath ".venv\Scripts\python.exe"
$script:HelperPath = Join-Path $PSScriptRoot "companion_growth_job_host.ps1"

function Test-ContainedPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )
    $canonicalPath = [IO.Path]::GetFullPath($Path)
    $canonicalRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    return $canonicalPath.StartsWith($canonicalRoot, [StringComparison]::OrdinalIgnoreCase)
}

function Assert-NoReparsePoint {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$TrustedRoot,
        [switch]$AllowMissing
    )
    $canonical = [IO.Path]::GetFullPath($Path)
    if (-not (Test-ContainedPath -Path $canonical -Root $TrustedRoot) -and
        $canonical -ne [IO.Path]::GetFullPath($TrustedRoot)) {
        throw "Path escaped the trusted runtime root: $canonical"
    }
    $root = [IO.Path]::GetFullPath($TrustedRoot)
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        if ($AllowMissing) { return }
        throw "Trusted runtime root does not exist: $root"
    }
    $cursor = $root
    if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Trusted runtime root is a reparse point: $cursor"
    }
    $resolvedRoot = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $cursor).Path)
    if ($resolvedRoot -ne [IO.Path]::GetFullPath($cursor)) {
        throw "Trusted runtime root resolved to an unexpected location: $resolvedRoot"
    }
    $relative = $canonical.Substring($root.TrimEnd('\').Length).TrimStart('\')
    foreach ($component in ($relative -split '\\')) {
        if ([string]::IsNullOrEmpty($component)) { continue }
        $cursor = Join-Path $cursor $component
        $item = $null
        $resolved = $null
        for ($attempt = 0; $attempt -lt 8; $attempt++) {
            try {
                $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
                $resolved = [IO.Path]::GetFullPath(
                    (Resolve-Path -LiteralPath $cursor -ErrorAction Stop).Path
                )
                break
            } catch [System.Management.Automation.ItemNotFoundException] {
                if ($AllowMissing) { break }
                if ($attempt -ge 7) { throw }
                # The helper updates the manifest with File.Replace.  A reader
                # can observe the replacement boundary between Test-Path and
                # Resolve-Path, so retry this exact path identity briefly.
                Start-Sleep -Milliseconds 25
            }
        }
        if ($null -eq $item -or $null -eq $resolved) {
            if ($AllowMissing) { break }
            throw "Lifecycle path is missing: $cursor"
        }
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Reparse points are forbidden in lifecycle paths: $cursor"
        }
        if ($resolved -ne [IO.Path]::GetFullPath($cursor)) {
            throw "Lifecycle path resolved to an unexpected location: $resolved"
        }
    }
}

function New-SafeDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-ContainedPath -Path $Path -Root $script:RuntimeRoot) -and
        [IO.Path]::GetFullPath($Path) -ne $script:RuntimeRoot) {
        throw "Refusing to create a directory outside the fixed runtime root: $Path"
    }
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-SafeDirectory -Path $parent
    }
    if (-not (Test-Path -LiteralPath $Path)) {
        [void][IO.Directory]::CreateDirectory($Path)
    }
    Assert-NoReparsePoint -Path $Path -TrustedRoot $script:RuntimeRoot
}

function Write-JsonAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value,
        [int]$Depth = 16
    )
    $directory = Split-Path -Parent $Path
    $temporary = Join-Path $directory (".{0}.{1}.tmp" -f ([IO.Path]::GetFileName($Path)), [Guid]::NewGuid().ToString("N"))
    [IO.File]::WriteAllText(
        $temporary,
        ($Value | ConvertTo-Json -Depth $Depth),
        (New-Object Text.UTF8Encoding($false))
    )
    if (Test-Path -LiteralPath $Path) {
        $backup = Join-Path $directory (".{0}.{1}.bak" -f ([IO.Path]::GetFileName($Path)), [Guid]::NewGuid().ToString("N"))
        try {
            [IO.File]::Replace($temporary, $Path, $backup, $true)
        } finally {
            if (Test-Path -LiteralPath $backup) {
                [IO.File]::Delete($backup)
            }
        }
    } else {
        [IO.File]::Move($temporary, $Path)
    }
}

function Read-SharedText {
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = [IO.File]::Open(
        $Path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
    )
    try {
        $reader = New-Object IO.StreamReader($stream, [Text.Encoding]::UTF8, $true)
        try {
            return $reader.ReadToEnd()
        } finally {
            $reader.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Get-NormalizedClock {
    if ([string]::IsNullOrWhiteSpace($ClockUtc)) {
        return [DateTimeOffset]::UtcNow.ToString("o")
    }
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
        $ClockUtc,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind,
        [ref]$parsed
    ) -or $parsed.Offset -ne [TimeSpan]::Zero) {
        throw "ClockUtc must be an absolute UTC timestamp with a zero offset."
    }
    return $parsed.ToUniversalTime().ToString("o")
}

function Assert-UserDataPathBudget {
    # The current longest first-party install target is the Godot
    # project_check schema below a content-addressed (64 hex) pack root.
    # Keep an additional MAX_PATH margin because pathlib/Win32 APIs used by
    # capability publication are not uniformly long-path aware.
    $longestKnownSuffix = "capabilities\packs\godot\1.0.2\$(('f' * 64))\tools\godot\project_check.schema.json"
    $projected = Join-Path $script:UserDataPath $longestKnownSuffix
    $safeBudget = 240
    if ($projected.Length -gt $safeBudget) {
        throw "ScenarioId '$ScenarioId' exceeds the Windows user-data path budget: projected first-party pack path length=$($projected.Length), safe budget=$safeBudget. Use a shorter ScenarioId (for example 's1')."
    }
}

function Read-TrustedManifest {
    Assert-NoReparsePoint -Path $script:ManifestPath -TrustedRoot $script:RuntimeRoot
    $manifest = Read-SharedText -Path $script:ManifestPath | ConvertFrom-Json
    if ($manifest.schema_version -ne 1 -or
        $manifest.scenario_id -ne $ScenarioId -or
        $manifest.launch_id -ne $LaunchId) {
        throw "Manifest identity does not match the requested ScenarioId/LaunchId."
    }
    $expected = @{
        repo_root = $script:RepoRoot
        launch_root = $script:LaunchRoot
        scenario_root = $script:ScenarioRoot
        user_data = $script:UserDataPath
        source_backend = $script:BackendPath
        python = $script:PythonPath
        temp_config = $script:ConfigPath
        npm_shim = $script:NpmShimPath
        tauri_log = $script:LogPath
    }
    foreach ($property in $expected.Keys) {
        if ([IO.Path]::GetFullPath([string]$manifest.paths.$property) -ne [IO.Path]::GetFullPath([string]$expected[$property])) {
            throw "Manifest path '$property' is outside the derived lifecycle scope."
        }
    }
    if ([int]$manifest.ports.backend -ne $BackendPort -or [int]$manifest.ports.vite -ne $VitePort) {
        throw "Manifest ports do not match the requested launcher ports."
    }
    return $manifest
}

function Get-ControlToken {
    Assert-NoReparsePoint -Path $script:TokenPath -TrustedRoot $script:RuntimeRoot
    $protected = [IO.File]::ReadAllBytes($script:TokenPath)
    $bytes = [Security.Cryptography.ProtectedData]::Unprotect(
        $protected,
        [Text.Encoding]::UTF8.GetBytes("$ScenarioId/$LaunchId"),
        [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    return [Text.Encoding]::UTF8.GetString($bytes)
}

function Read-SharedText {
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = [IO.File]::Open(
        $Path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
    )
    try {
        $reader = New-Object IO.StreamReader($stream, [Text.Encoding]::UTF8, $true)
        try {
            return $reader.ReadToEnd()
        } finally {
            $reader.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Invoke-Control {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("status", "stop")][string]$ControlAction,
        [int]$TimeoutMilliseconds = 10000
    )
    $manifest = Read-TrustedManifest
    if ([string]::IsNullOrWhiteSpace([string]$manifest.control_pipe)) {
        throw "The lifecycle helper has not published its authenticated control pipe."
    }
    $helperIdentityValid = Test-ProcessIdentity `
        -ProcessId ([int]$manifest.helper.pid) `
        -CreateTimeUtc ([string]$manifest.helper.create_time_utc)
    if (-not $helperIdentityValid) {
        throw "Lifecycle helper PID/create-time identity no longer matches the manifest."
    }
    $pipe = New-Object IO.Pipes.NamedPipeClientStream(
        ".",
        [string]$manifest.control_pipe,
        [IO.Pipes.PipeDirection]::InOut,
        [IO.Pipes.PipeOptions]::None
    )
    try {
        $pipe.Connect($TimeoutMilliseconds)
        $reader = New-Object IO.StreamReader($pipe, (New-Object Text.UTF8Encoding($false)), $false, 4096, $true)
        $writer = New-Object IO.StreamWriter($pipe, (New-Object Text.UTF8Encoding($false)), 4096, $true)
        $writer.AutoFlush = $true
        try {
            $request = [ordered]@{ token = Get-ControlToken; action = $ControlAction }
            $writer.WriteLine(($request | ConvertTo-Json -Compress))
            $line = $reader.ReadLine()
            if ([string]::IsNullOrWhiteSpace($line)) {
                throw "Lifecycle helper closed the control pipe without a response."
            }
            return $line | ConvertFrom-Json
        } finally {
            $reader.Dispose()
            $writer.Dispose()
        }
    } finally {
        $pipe.Dispose()
    }
}

function Test-ProcessIdentity {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$CreateTimeUtc
    )
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $false }
    try {
        $startTime = $process.StartTime
        if ($null -eq $startTime) { return $false }
        return $startTime.ToUniversalTime().ToString("o") -eq $CreateTimeUtc
    } catch {
        # The process may exit between Get-Process and reading StartTime.
        # That is already the desired cleanup state, not an identity error.
        return $false
    }
}

function Get-ProcessRecord {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [string]$ScopeBasis = "offline_verified"
    )
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $cim -or $null -eq $process) { return $null }
    try {
        $startTimeUtc = $process.StartTime.ToUniversalTime().ToString("o")
        $privateBytes = [int64]$process.PrivateMemorySize64
    } catch {
        return $null
    }
    return [ordered]@{
        pid = $ProcessId
        create_time_utc = $startTimeUtc
        parent_pid = [int]$cim.ParentProcessId
        command_line = [string]$cim.CommandLine
        executable_path = [string]$cim.ExecutablePath
        private_bytes = $privateBytes
        scope_basis = $ScopeBasis
    }
}

function Get-OfflineScopeSnapshot {
    param(
        [Parameter(Mandatory = $true)]$Manifest,
        [AllowEmptyCollection()][object[]]$ExtraRecords = @()
    )
    $records = @{}
    $unknown = New-Object Collections.Generic.List[object]
    $auditNotes = New-Object Collections.Generic.List[object]
    $allProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $byParent = @{}
    foreach ($process in $allProcesses) {
        $parent = [int]$process.ParentProcessId
        if (-not $byParent.ContainsKey($parent)) {
            $byParent[$parent] = New-Object Collections.Generic.List[int]
        }
        $byParent[$parent].Add([int]$process.ProcessId)
    }

    function Add-FrozenRecord {
        param($Frozen, [string]$Basis)
        if ($null -eq $Frozen -or [int]$Frozen.pid -le 0) { return }
        $pidValue = [int]$Frozen.pid
        $current = Get-ProcessRecord -ProcessId $pidValue -ScopeBasis $Basis
        if ($null -eq $current) { return }
        if ([string]$current.create_time_utc -ne [string]$Frozen.create_time_utc) {
            $auditNotes.Add([ordered]@{
                kind = "pid_reused"
                pid = $pidValue
                expected_create_time_utc = [string]$Frozen.create_time_utc
                actual_create_time_utc = [string]$current.create_time_utc
                disposition = "not_launch_identity_no_kill"
            })
            return
        }
        $records["$pidValue|$($current.create_time_utc)"] = $current
    }

    Add-FrozenRecord -Frozen $Manifest.helper -Basis "manifest_helper_identity"
    Add-FrozenRecord -Frozen $Manifest.root -Basis "manifest_root_identity"
    foreach ($record in @($Manifest.observed_processes) + @($Manifest.late_descendants) + @($ExtraRecords)) {
        Add-FrozenRecord -Frozen $record -Basis "manifest_process_identity"
    }

    # A launch-owned config, shim, and log are globally unique immutable paths.
    # A Scenario user-data path is deliberately not used alone because it is
    # shared across restart LaunchIds.
    foreach ($process in $allProcesses) {
        $command = [string]$process.CommandLine
        if ([string]::IsNullOrEmpty($command)) { continue }
        $hasUniqueMarker = $command.IndexOf($script:ConfigPath, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            $command.IndexOf($script:NpmShimPath, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            $command.IndexOf($script:LogPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
        if ($hasUniqueMarker) {
            $current = Get-ProcessRecord -ProcessId ([int]$process.ProcessId) -ScopeBasis "immutable_launch_marker"
            if ($null -ne $current) {
                $records["$($current.pid)|$($current.create_time_utc)"] = $current
            }
        }
    }

    # Recursively close over current parentage from every frozen/marker seed.
    $queue = New-Object Collections.Generic.Queue[int]
    $queued = @{}
    $recordValues = @($records.GetEnumerator() | ForEach-Object { $_.Value })
    foreach ($record in $recordValues) {
        $pidValue = [int]$record.pid
        $queue.Enqueue($pidValue)
        $queued[$pidValue] = $true
    }
    while ($queue.Count -gt 0) {
        $parent = $queue.Dequeue()
        if (-not $byParent.ContainsKey($parent)) { continue }
        foreach ($childPid in $byParent[$parent]) {
            $child = Get-ProcessRecord -ProcessId $childPid -ScopeBasis "verified_process_ancestry"
            if ($null -eq $child) { continue }
            $records["$($child.pid)|$($child.create_time_utc)"] = $child
            if (-not $queued.ContainsKey([int]$child.pid)) {
                $queued[[int]$child.pid] = $true
                $queue.Enqueue([int]$child.pid)
            }
        }
    }

    $recordValues = @($records.GetEnumerator() | ForEach-Object { $_.Value })
    $listenerOwners = New-Object Collections.Generic.List[object]
    $verifiedPids = @{}
    foreach ($record in $recordValues) { $verifiedPids[[int]$record.pid] = $true }
    foreach ($port in @($BackendPort, $VitePort)) {
        foreach ($owner in @(Get-PortOwners -Port $port)) {
            $isVerified = $verifiedPids.ContainsKey([int]$owner)
            $listenerOwners.Add([ordered]@{ port = $port; pid = [int]$owner; verified = $isVerified })
            if (-not $isVerified) {
                $unknown.Add([ordered]@{ kind = "listener_outside_verified_scope"; port = $port; pid = [int]$owner })
            }
        }
    }
    $listenerValues = @($listenerOwners | ForEach-Object { $_ })
    $unknownValues = @($unknown | ForEach-Object { $_ })
    $auditNoteValues = @($auditNotes | ForEach-Object { $_ })
    return [pscustomobject]@{
        records = @($recordValues)
        listener_owners = $listenerValues
        scope_unknown = $unknownValues
        audit_notes = $auditNoteValues
    }
}

function Stop-VerifiedProcessRecords {
    param([Parameter(Mandatory = $true)][object[]]$Records)
    $recordByPid = @{}
    foreach ($record in $Records) { $recordByPid[[int]$record.pid] = $record }
    $ordered = @(
        foreach ($record in $Records) {
            $depth = 0
            $seen = @{}
            $parent = [int]$record.parent_pid
            while ($recordByPid.ContainsKey($parent) -and -not $seen.ContainsKey($parent)) {
                $seen[$parent] = $true
                $depth += 1
                $parent = [int]$recordByPid[$parent].parent_pid
            }
            [pscustomobject]@{ record = $record; depth = $depth }
        }
    )
    foreach ($item in @($ordered | Sort-Object depth -Descending)) {
        $record = $item.record
        if ([int]$record.pid -eq $PID) {
            throw "Refusing to terminate the current lifecycle launcher process."
        }
        if (-not (Test-ProcessIdentity -ProcessId ([int]$record.pid) -CreateTimeUtc ([string]$record.create_time_utc))) {
            continue
        }
        $process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
        if ($null -ne $process) {
            try {
                $process.Kill()
                [void]$process.WaitForExit(5000)
            } catch {
                # Keep cleanup best-effort within the frozen identity set.  A
                # transient access race (notably a conhost exiting with its
                # parent) must not abort the second pass; any real survivor is
                # detected and reported by Complete-ScopedCleanup below.
            }
        }
    }
}

function Remove-TrustedLaunchArtifacts {
    param([Parameter(Mandatory = $true)]$Manifest)
    $results = New-Object Collections.Generic.List[object]
    foreach ($item in @(
        [pscustomobject]@{
            label = "temp_config"
            path = [string]$Manifest.paths.temp_config
            expected_path = $script:ConfigPath
            hash = [string]$Manifest.temp_config_sha256
            identity = [string]$Manifest.temp_config_file_identity
        },
        [pscustomobject]@{
            label = "npm_shim"
            path = [string]$Manifest.paths.npm_shim
            expected_path = $script:NpmShimPath
            hash = [string]$Manifest.npm_shim_sha256
            identity = [string]$Manifest.npm_shim_file_identity
        }
    )) {
        $recorded = [IO.Path]::GetFullPath($item.path)
        Assert-NoReparsePoint -Path $recorded -TrustedRoot $script:LaunchRoot -AllowMissing
        if ($recorded -ne [IO.Path]::GetFullPath($item.expected_path)) {
            throw "Refusing to delete an untrusted $($item.label) path."
        }
        if (-not (Test-Path -LiteralPath $recorded -PathType Leaf)) {
            $results.Add([ordered]@{ artifact = $item.label; path = $recorded; result = "already_absent" })
            continue
        }
        Assert-NoReparsePoint -Path $recorded -TrustedRoot $script:LaunchRoot
        $actualHash = (Get-FileHash -LiteralPath $recorded -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $item.hash) {
            throw "Refusing to delete $($item.label) whose frozen content identity changed."
        }
        $actualIdentity = [CompanionGrowthFileIdentity]::Get($recorded)
        if ($actualIdentity -ne $item.identity) {
            throw "Refusing to delete a replaced $($item.label) file identity."
        }
        [IO.File]::Delete($recorded)
        $results.Add([ordered]@{ artifact = $item.label; path = $recorded; result = "deleted" })
    }
    return @($results | ForEach-Object { $_ })
}

function Complete-ScopedCleanup {
    param(
        [Parameter(Mandatory = $true)]$Manifest,
        [Parameter(Mandatory = $true)][string]$Reason,
        [string]$Failure = "",
        $HelperResponse = $null,
        [AllowEmptyCollection()][object[]]$ExtraRecords = @()
    )
    $initial = Get-OfflineScopeSnapshot -Manifest $Manifest -ExtraRecords $ExtraRecords
    $allRecords = @{}
    foreach ($record in $initial.records) {
        $allRecords["$($record.pid)|$($record.create_time_utc)"] = $record
    }
    if (
        @($initial.scope_unknown).Count -eq 0 -and
        @($initial.records).Count -gt 0
    ) {
        Stop-VerifiedProcessRecords -Records @($initial.records)
        Start-Sleep -Milliseconds 500
        $allRecordValues = @($allRecords.GetEnumerator() | ForEach-Object { $_.Value })
        $retry = Get-OfflineScopeSnapshot -Manifest $Manifest -ExtraRecords $allRecordValues
        foreach ($record in $retry.records) {
            $allRecords["$($record.pid)|$($record.create_time_utc)"] = $record
        }
        if (@($retry.scope_unknown).Count -eq 0 -and @($retry.records).Count -gt 0) {
            Stop-VerifiedProcessRecords -Records @($retry.records)
            Start-Sleep -Milliseconds 500
        }
    }

    $processResults = New-Object Collections.Generic.List[object]
    [int64]$released = 0
    $unknown = New-Object Collections.Generic.List[object]
    $auditNotes = New-Object Collections.Generic.List[object]
    foreach ($entry in @($initial.scope_unknown)) { $unknown.Add($entry) }
    foreach ($entry in @($initial.audit_notes)) { $auditNotes.Add($entry) }
    $allRecordValues = @($allRecords.GetEnumerator() | ForEach-Object { $_.Value })
    foreach ($record in $allRecordValues) {
        $survivor = Test-ProcessIdentity -ProcessId ([int]$record.pid) -CreateTimeUtc ([string]$record.create_time_utc)
        $processResults.Add([ordered]@{
            pid = [int]$record.pid
            create_time_utc = [string]$record.create_time_utc
            parent_pid = [int]$record.parent_pid
            command_line = [string]$record.command_line
            scope_basis = [string]$record.scope_basis
            private_bytes = [int64]$record.private_bytes
            survivor = $survivor
        })
        if ($survivor) {
            $unknown.Add([ordered]@{ kind = "verified_identity_survivor"; pid = [int]$record.pid; create_time_utc = [string]$record.create_time_utc })
        } else {
            $released += [int64]$record.private_bytes
        }
    }
    $final = Get-OfflineScopeSnapshot -Manifest $Manifest -ExtraRecords $allRecordValues
    foreach ($entry in @($final.scope_unknown)) { $unknown.Add($entry) }
    foreach ($entry in @($final.audit_notes)) { $auditNotes.Add($entry) }
    foreach ($record in @($final.records)) {
        if (Test-ProcessIdentity -ProcessId ([int]$record.pid) -CreateTimeUtc ([string]$record.create_time_utc)) {
            $key = "verified_identity_survivor|$($record.pid)|$($record.create_time_utc)"
            if (-not (@($unknown | ForEach-Object { $_ }) | Where-Object { "$($_.kind)|$($_.pid)|$($_.create_time_utc)" -eq $key })) {
                $unknown.Add([ordered]@{ kind = "verified_identity_survivor"; pid = [int]$record.pid; create_time_utc = [string]$record.create_time_utc })
            }
        }
    }

    $artifactResults = @()
    $artifactError = $null
    try {
        $artifactResults = @(Remove-TrustedLaunchArtifacts -Manifest $Manifest)
    } catch {
        $artifactError = $_.Exception.Message
        $unknown.Add([ordered]@{ kind = "artifact_cleanup_failed"; error = $artifactError })
    }
    $processResultValues = @($processResults | ForEach-Object { $_ })
    $unknownValues = @($unknown | ForEach-Object { $_ })
    $auditNoteValues = @($auditNotes | ForEach-Object { $_ })
    $cleanup = [ordered]@{
        schema_version = 1
        scenario_id = $ScenarioId
        launch_id = $LaunchId
        reason = $Reason
        failure = $Failure
        stopped_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        process_results = $processResultValues
        listener_owners_before_stop = @($initial.listener_owners)
        scope_unknown = $unknownValues
        audit_notes = $auditNoteValues
        survivor_count = $unknownValues.Count
        released_private_bytes = $released
        helper_stop_response = $HelperResponse
        artifact_cleanup = @($artifactResults)
        artifact_cleanup_error = $artifactError
    }
    Write-JsonAtomic -Path $script:CleanupPath -Value $cleanup
    $Manifest.state = if ($unknownValues.Count -eq 0) { "stopped" } else { "cleanup_blocked" }
    $Manifest.stopped_at_utc = $cleanup.stopped_at_utc
    $Manifest.cleanup_result = $script:CleanupPath
    if (-not [string]::IsNullOrEmpty($Failure)) {
        $Manifest.failure = $Failure
        $Manifest.failed_at_utc = $cleanup.stopped_at_utc
    }
    Write-JsonAtomic -Path $script:ManifestPath -Value $Manifest
    return [pscustomobject]$cleanup
}

function Get-PortOwners {
    param([int]$Port)
    return @(
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
}

function Assert-PortsFree {
    foreach ($port in @($BackendPort, $VitePort)) {
        $owners = @(Get-PortOwners -Port $port)
        if ($owners.Count -gt 0) {
            throw "Required port $port is owned by unknown live PID(s) $($owners -join ','); no process was killed."
        }
    }
}

function Find-ExistingLaunchId {
    if (-not (Test-Path -LiteralPath $script:RuntimeRoot -PathType Container)) { return @() }
    $matches = New-Object Collections.Generic.List[string]
    foreach ($scenario in @(Get-ChildItem -LiteralPath $script:RuntimeRoot -Directory -Force)) {
        if ($scenario.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Runtime Scenario root is a reparse point: $($scenario.FullName)"
        }
        $launches = Join-Path $scenario.FullName "launches"
        if (-not (Test-Path -LiteralPath $launches -PathType Container)) { continue }
        Assert-NoReparsePoint -Path $launches -TrustedRoot $script:RuntimeRoot
        $candidate = Join-Path $launches $LaunchId
        if (Test-Path -LiteralPath $candidate) {
            Assert-NoReparsePoint -Path $candidate -TrustedRoot $script:RuntimeRoot
            $matches.Add([IO.Path]::GetFullPath($candidate))
        }
    }
    return @($matches)
}

function Start-Launch {
    $existingLaunches = @(Find-ExistingLaunchId)
    if ($existingLaunches.Count -gt 0) {
        throw "LaunchId '$LaunchId' already exists at $($existingLaunches -join ','). Every Start requires a globally unique LaunchId."
    }
    if (-not (Test-Path -LiteralPath $script:PythonPath -PathType Leaf)) {
        throw "Source backend Python is missing: $script:PythonPath"
    }
    if (-not (Test-Path -LiteralPath $script:HelperPath -PathType Leaf)) {
        throw "Lifecycle helper is missing: $script:HelperPath"
    }
    Assert-PortsFree
    $normalizedClock = Get-NormalizedClock
    Assert-UserDataPathBudget

    if (-not (Test-Path -LiteralPath $script:RuntimeRoot)) {
        $runtimeParent = Split-Path -Parent $script:RuntimeRoot
        if (-not (Test-Path -LiteralPath $runtimeParent -PathType Container)) {
            throw "Plan evidence root is missing: $runtimeParent"
        }
        [void][IO.Directory]::CreateDirectory($script:RuntimeRoot)
    }
    Assert-NoReparsePoint -Path $script:RuntimeRoot -TrustedRoot $script:RuntimeRoot
    New-SafeDirectory -Path $script:ScenarioRoot
    New-SafeDirectory -Path $script:UserDataPath
    New-SafeDirectory -Path (Join-Path $script:ScenarioRoot "launches")
    New-SafeDirectory -Path $script:LaunchRoot
    New-SafeDirectory -Path (Join-Path $script:LaunchRoot "logs")

    $config = [ordered]@{
        build = [ordered]@{
            devUrl = "http://localhost:$VitePort"
        }
    }
    Write-JsonAtomic -Path $script:ConfigPath -Value $config -Depth 4
    $pnpmPath = (Get-Command pnpm.cmd -ErrorAction Stop).Source
    $shim = "@echo off`r`ncall `"$pnpmPath`" %*`r`nexit /b %ERRORLEVEL%`r`n"
    [IO.File]::WriteAllText(
        $script:NpmShimPath,
        $shim,
        (New-Object Text.ASCIIEncoding)
    )

    $tokenBytes = New-Object byte[] 32
    $random = New-Object Security.Cryptography.RNGCryptoServiceProvider
    try {
        $random.GetBytes($tokenBytes)
    } finally {
        $random.Dispose()
    }
    $token = [Convert]::ToBase64String($tokenBytes)
    $protected = [Security.Cryptography.ProtectedData]::Protect(
        [Text.Encoding]::UTF8.GetBytes($token),
        [Text.Encoding]::UTF8.GetBytes("$ScenarioId/$LaunchId"),
        [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    $temporaryToken = Join-Path $script:LaunchRoot (".control-token.$([Guid]::NewGuid().ToString('N')).tmp")
    [IO.File]::WriteAllBytes($temporaryToken, $protected)
    [IO.File]::Move($temporaryToken, $script:TokenPath)
    (Get-Item -LiteralPath $script:TokenPath).Attributes = [IO.FileAttributes]::Hidden

    $manifest = [ordered]@{
        schema_version = 1
        scenario_id = $ScenarioId
        launch_id = $LaunchId
        state = "starting"
        started_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        clock_utc = $normalizedClock
        ports = [ordered]@{ backend = $BackendPort; vite = $VitePort }
        paths = [ordered]@{
            repo_root = $script:RepoRoot
            scenario_root = $script:ScenarioRoot
            launch_root = $script:LaunchRoot
            user_data = $script:UserDataPath
            source_backend = $script:BackendPath
            python = $script:PythonPath
            temp_config = $script:ConfigPath
            npm_shim = $script:NpmShimPath
            tauri_log = $script:LogPath
        }
        helper = $null
        root = $null
        job = $null
        control_pipe = $null
        observed_processes = @()
        late_descendants = @()
        listener_owners = @()
        scope_unknown = @()
        status_history = @()
        updated_at_utc = $null
        ready_at_utc = $null
        stop_requested_at_utc = $null
        stopped_at_utc = $null
        failure = $null
        failed_at_utc = $null
        cleanup_result = $null
        temp_config_sha256 = (Get-FileHash -LiteralPath $script:ConfigPath -Algorithm SHA256).Hash.ToLowerInvariant()
        temp_config_file_identity = [CompanionGrowthFileIdentity]::Get($script:ConfigPath)
        npm_shim_sha256 = (Get-FileHash -LiteralPath $script:NpmShimPath -Algorithm SHA256).Hash.ToLowerInvariant()
        npm_shim_file_identity = [CompanionGrowthFileIdentity]::Get($script:NpmShimPath)
    }
    Write-JsonAtomic -Path $script:ManifestPath -Value $manifest

    $powerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
    $escapedHelper = $script:HelperPath.Replace('"', '\"')
    $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -ScenarioId {1} -LaunchId {2} -BackendPort {3} -VitePort {4} -ClockUtc "{5}"' -f `
        $escapedHelper, $ScenarioId, $LaunchId, $BackendPort, $VitePort, $normalizedClock
    $hostProcess = $null
    try {
        $hostProcess = Start-Process -FilePath $powerShell `
            -ArgumentList $arguments `
            -WorkingDirectory $script:RepoRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $script:HelperLogPath `
            -RedirectStandardError $script:HelperErrorPath `
            -PassThru
        $deadline = [DateTime]::UtcNow.AddMinutes(6)
        while ([DateTime]::UtcNow -lt $deadline) {
            if ($hostProcess.HasExited) {
                $failure = ""
                if (Test-Path -LiteralPath $script:HelperErrorPath) {
                    $failure = Read-SharedText -Path $script:HelperErrorPath
                }
                throw "Lifecycle helper exited before ready (exit=$($hostProcess.ExitCode)): $failure"
            }
            $current = Read-TrustedManifest
            if ($current.state -eq "failed") {
                throw "Lifecycle helper failed: $($current.failure)"
            }
            if ($current.state -eq "ready") {
                $status = Invoke-Control -ControlAction status
                if (-not $status.ok) {
                    throw "Lifecycle helper reached ready but Status failed: $($status | ConvertTo-Json -Depth 16 -Compress)"
                }
                Write-Output "COMPANION_GROWTH_STATE=ready"
                Write-Output "COMPANION_GROWTH_SCENARIO_ROOT=$script:ScenarioRoot"
                Write-Output "COMPANION_GROWTH_LAUNCH_ROOT=$script:LaunchRoot"
                Write-Output "COMPANION_GROWTH_MANIFEST=$script:ManifestPath"
                Write-Output ($status | ConvertTo-Json -Depth 16 -Compress)
                return
            }
            Start-Sleep -Milliseconds 250
        }
        throw "Timed out waiting for the lifecycle helper to publish ready state."
    } catch {
        $startFailure = $_
        $manifestForCleanup = Read-TrustedManifest
        $extra = @()
        if ($null -ne $hostProcess -and -not $hostProcess.HasExited) {
            $helperRecord = Get-ProcessRecord -ProcessId $hostProcess.Id -ScopeBasis "exact_start_process_identity"
            if ($null -ne $helperRecord) { $extra += $helperRecord }
        }
        $cleanup = Complete-ScopedCleanup `
            -Manifest $manifestForCleanup `
            -Reason "start_failed" `
            -Failure $startFailure.Exception.Message `
            -ExtraRecords $extra
        Write-Output "COMPANION_GROWTH_CLEANUP=$script:CleanupPath"
        Write-Output ($cleanup | ConvertTo-Json -Depth 16 -Compress)
        throw $startFailure
    }
}

function Show-Status {
    try {
        $response = Invoke-Control -ControlAction status
    } catch {
        $manifest = Read-TrustedManifest
        $offline = Get-OfflineScopeSnapshot -Manifest $manifest
        $response = [pscustomobject]@{
            ok = (@($offline.scope_unknown).Count -eq 0)
            action = "status"
            mode = "offline_manifest_scope"
            state = [string]$manifest.state
            job_processes = @($offline.records)
            listener_owners = @($offline.listener_owners)
            scope_unknown = @($offline.scope_unknown)
            audit_notes = @($offline.audit_notes)
            control_error = $_.Exception.Message
        }
    }
    if (-not $response.ok) {
        Write-Output ($response | ConvertTo-Json -Depth 16 -Compress)
        throw "Lifecycle scope is unknown; Status failed closed without killing any process."
    }
    Write-Output ($response | ConvertTo-Json -Depth 16 -Compress)
}

function Stop-Launch {
    $manifest = Read-TrustedManifest
    $before = $null
    $response = $null
    $controlError = ""
    try {
        $before = Invoke-Control -ControlAction status
        if (-not $before.ok) {
            throw "Lifecycle helper Status reported unknown scope."
        }
        $response = Invoke-Control -ControlAction stop -TimeoutMilliseconds 20000
        if (-not $response.ok) {
            throw "Lifecycle helper refused Stop."
        }
    } catch {
        $controlError = $_.Exception.Message
    }
    $extra = @()
    if (
        $null -ne $before -and
        $before.PSObject.Properties.Name -contains "job_processes"
    ) {
        $extra += @($before.job_processes)
    }
    $cleanup = Complete-ScopedCleanup `
        -Manifest $manifest `
        -Reason $(if ([string]::IsNullOrEmpty($controlError)) { "stop" } else { "stop_offline_fallback" }) `
        -Failure $controlError `
        -HelperResponse $response `
        -ExtraRecords $extra

    Write-Output "COMPANION_GROWTH_CLEANUP=$script:CleanupPath"
    Write-Output ($cleanup | ConvertTo-Json -Depth 16 -Compress)
    if ([int]$cleanup.survivor_count -gt 0) {
        throw "Stop retained evidence for $($cleanup.survivor_count) unknown survivor(s); no out-of-scope process was killed."
    }
}

foreach ($path in @($script:ScenarioRoot, $script:LaunchRoot, $script:ManifestPath)) {
    Assert-NoReparsePoint -Path $path -TrustedRoot $script:RuntimeRoot -AllowMissing:($Action -eq "Start")
}
foreach ($optionalLaunchArtifact in @($script:TokenPath, $script:ConfigPath, $script:NpmShimPath, $script:LogPath)) {
    Assert-NoReparsePoint -Path $optionalLaunchArtifact -TrustedRoot $script:RuntimeRoot -AllowMissing
}

switch ($Action) {
    "Start" { Start-Launch }
    "Status" { Show-Status }
    "Stop" { Stop-Launch }
}
