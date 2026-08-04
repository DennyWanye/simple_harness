[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')]
    [string]$ScenarioId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')]
    [string]$LaunchId,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1024, 65535)]
    [int]$BackendPort,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1024, 65535)]
    [int]$VitePort,

    [Parameter(Mandatory = $true)]
    [string]$ClockUtc
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "The companion growth lifecycle helper requires Windows Job Objects."
}
Add-Type -AssemblyName System.Security

$script:RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$script:RuntimeRoot = [IO.Path]::GetFullPath((Join-Path $script:RepoRoot "plans\2026-07-24-human-anchored-companion-growth\evidence\manual-runtime"))
$script:ScenarioRoot = [IO.Path]::GetFullPath((Join-Path $script:RuntimeRoot $ScenarioId))
$script:LaunchRoot = [IO.Path]::GetFullPath((Join-Path $script:ScenarioRoot "launches\$LaunchId"))
$script:ManifestPath = Join-Path $script:LaunchRoot "process-manifest.json"
$script:TokenPath = Join-Path $script:LaunchRoot "control-token.dpapi"
$script:ConfigPath = Join-Path $script:LaunchRoot "tauri-dev-config.json"
$script:NpmShimPath = Join-Path $script:LaunchRoot "npm.cmd"
$script:LogPath = Join-Path $script:LaunchRoot "logs\tauri.log"
$script:HelperLogPath = Join-Path $script:LaunchRoot "logs\job-host.log"
$script:UserDataPath = Join-Path $script:ScenarioRoot "user-data"
$script:BackendPath = Join-Path $script:RepoRoot "backend"
$script:PythonPath = Join-Path $script:BackendPath ".venv\Scripts\python.exe"
$script:TauriPath = Join-Path $script:RepoRoot "tauri-app"
$script:JobHandle = [IntPtr]::Zero
$script:RootProcessHandle = [IntPtr]::Zero
$script:RootPid = 0
$script:PipeName = ""
$script:ControlToken = ""
$script:StopRequested = $false

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
        [Parameter(Mandatory = $true)][string]$TrustedRoot
    )
    if (-not (Test-ContainedPath -Path $Path -Root $TrustedRoot) -and
        [IO.Path]::GetFullPath($Path) -ne [IO.Path]::GetFullPath($TrustedRoot)) {
        throw "Path escaped the trusted runtime root: $Path"
    }
    $cursor = [IO.Path]::GetFullPath($TrustedRoot)
    if (-not (Test-Path -LiteralPath $cursor -PathType Container)) {
        throw "Trusted runtime root does not exist: $cursor"
    }
    if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Trusted runtime root is a reparse point: $cursor"
    }
    $resolvedRoot = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $cursor).Path)
    if ($resolvedRoot -ne [IO.Path]::GetFullPath($cursor)) {
        throw "Trusted runtime root resolved to an unexpected location: $resolvedRoot"
    }
    $relative = [IO.Path]::GetFullPath($Path).Substring($cursor.TrimEnd('\').Length).TrimStart('\')
    foreach ($component in ($relative -split '\\')) {
        if ([string]::IsNullOrEmpty($component)) { continue }
        $cursor = Join-Path $cursor $component
        if (-not (Test-Path -LiteralPath $cursor)) { break }
        if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Reparse points are forbidden in lifecycle paths: $cursor"
        }
        $resolved = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $cursor).Path)
        if ($resolved -ne [IO.Path]::GetFullPath($cursor)) {
            throw "Lifecycle path resolved to an unexpected location: $resolved"
        }
    }
}

function Write-JsonAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value,
        [int]$Depth = 16
    )
    $directory = Split-Path -Parent $Path
    $temporary = Join-Path $directory (".{0}.{1}.tmp" -f ([IO.Path]::GetFileName($Path)), [Guid]::NewGuid().ToString("N"))
    $json = $Value | ConvertTo-Json -Depth $Depth
    [IO.File]::WriteAllText($temporary, $json, (New-Object Text.UTF8Encoding($false)))
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

function Read-Manifest {
    if (-not (Test-Path -LiteralPath $script:ManifestPath -PathType Leaf)) {
        throw "Lifecycle manifest is missing: $script:ManifestPath"
    }
    $manifest = Read-SharedText -Path $script:ManifestPath | ConvertFrom-Json
    if ($manifest.scenario_id -ne $ScenarioId -or $manifest.launch_id -ne $LaunchId) {
        throw "Lifecycle manifest identity does not match the requested launch."
    }
    if ([IO.Path]::GetFullPath([string]$manifest.paths.launch_root) -ne $script:LaunchRoot) {
        throw "Lifecycle manifest launch root does not match the canonical launch root."
    }
    if ([int]$manifest.ports.backend -ne $BackendPort -or [int]$manifest.ports.vite -ne $VitePort) {
        throw "Lifecycle manifest ports do not match the helper invocation."
    }
    if ([string]$manifest.clock_utc -ne $ClockUtc) {
        throw "Lifecycle manifest clock does not match the helper invocation."
    }
    $expectedPaths = @{
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
    foreach ($property in $expectedPaths.Keys) {
        if ([IO.Path]::GetFullPath([string]$manifest.paths.$property) -ne
            [IO.Path]::GetFullPath([string]$expectedPaths[$property])) {
            throw "Lifecycle manifest path '$property' does not match the helper-derived scope."
        }
    }
    return $manifest
}

function Get-ProcessRecord {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [string]$ScopeBasis = "job"
    )
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $cim) { return $null }
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    return [ordered]@{
        pid = $ProcessId
        create_time_utc = $process.StartTime.ToUniversalTime().ToString("o")
        parent_pid = [int]$cim.ParentProcessId
        command_line = [string]$cim.CommandLine
        executable_path = [string]$cim.ExecutablePath
        private_bytes = [int64]$process.PrivateMemorySize64
        scope_basis = $ScopeBasis
    }
}

function Test-ProcessIdentity {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$CreateTimeUtc
    )
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $false }
    return $process.StartTime.ToUniversalTime().ToString("o") -eq $CreateTimeUtc
}

$interopSource = @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class CompanionGrowthNative
{
    public const uint CREATE_SUSPENDED = 0x00000004;
    public const uint CREATE_NEW_PROCESS_GROUP = 0x00000200;
    public const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
    public const int JobObjectBasicProcessIdList = 3;
    public const int JobObjectExtendedLimitInformation = 9;

    [StructLayout(LayoutKind.Sequential)]
    public struct SECURITY_ATTRIBUTES
    {
        public int nLength;
        public IntPtr lpSecurityDescriptor;
        public int bInheritHandle;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct STARTUPINFO
    {
        public int cb;
        public string lpReserved;
        public string lpDesktop;
        public string lpTitle;
        public int dwX;
        public int dwY;
        public int dwXSize;
        public int dwYSize;
        public int dwXCountChars;
        public int dwYCountChars;
        public int dwFillAttribute;
        public int dwFlags;
        public short wShowWindow;
        public short cbReserved2;
        public IntPtr lpReserved2;
        public IntPtr hStdInput;
        public IntPtr hStdOutput;
        public IntPtr hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct PROCESS_INFORMATION
    {
        public IntPtr hProcess;
        public IntPtr hThread;
        public int dwProcessId;
        public int dwThreadId;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct IO_COUNTERS
    {
        public UInt64 ReadOperationCount;
        public UInt64 WriteOperationCount;
        public UInt64 OtherOperationCount;
        public UInt64 ReadTransferCount;
        public UInt64 WriteTransferCount;
        public UInt64 OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public Int64 PerProcessUserTimeLimit;
        public Int64 PerJobUserTimeLimit;
        public UInt32 LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public UInt32 ActiveProcessLimit;
        public UIntPtr Affinity;
        public UInt32 PriorityClass;
        public UInt32 SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr CreateJobObject(IntPtr attributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool SetInformationJobObject(
        IntPtr job,
        int infoClass,
        ref JOBOBJECT_EXTENDED_LIMIT_INFORMATION info,
        uint length);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool QueryInformationJobObject(
        IntPtr job,
        int infoClass,
        IntPtr info,
        uint length,
        out uint returnLength);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool CreateProcess(
        string applicationName,
        StringBuilder commandLine,
        IntPtr processAttributes,
        IntPtr threadAttributes,
        bool inheritHandles,
        uint creationFlags,
        IntPtr environment,
        string currentDirectory,
        ref STARTUPINFO startupInfo,
        out PROCESS_INFORMATION processInformation);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern uint ResumeThread(IntPtr thread);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr handle);
}
'@

Add-Type -TypeDefinition $interopSource -Language CSharp

function New-KillOnCloseJob {
    param([Parameter(Mandatory = $true)][string]$Name)
    $handle = [CompanionGrowthNative]::CreateJobObject([IntPtr]::Zero, $Name)
    if ($handle -eq [IntPtr]::Zero) {
        throw "CreateJobObject failed with Win32 error $([Runtime.InteropServices.Marshal]::GetLastWin32Error())."
    }
    $limits = New-Object CompanionGrowthNative+JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    $limits.BasicLimitInformation.LimitFlags = [CompanionGrowthNative]::JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    $size = [Runtime.InteropServices.Marshal]::SizeOf($limits)
    if (-not [CompanionGrowthNative]::SetInformationJobObject(
        $handle,
        [CompanionGrowthNative]::JobObjectExtendedLimitInformation,
        [ref]$limits,
        [uint32]$size
    )) {
        $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        [void][CompanionGrowthNative]::CloseHandle($handle)
        throw "SetInformationJobObject failed with Win32 error $errorCode."
    }
    return $handle
}

function Get-JobProcessIds {
    $capacity = 65536
    $buffer = [Runtime.InteropServices.Marshal]::AllocHGlobal($capacity)
    try {
        [uint32]$returned = 0
        if (-not [CompanionGrowthNative]::QueryInformationJobObject(
            $script:JobHandle,
            [CompanionGrowthNative]::JobObjectBasicProcessIdList,
            $buffer,
            [uint32]$capacity,
            [ref]$returned
        )) {
            throw "QueryInformationJobObject failed with Win32 error $([Runtime.InteropServices.Marshal]::GetLastWin32Error())."
        }
        $listed = [Runtime.InteropServices.Marshal]::ReadInt32($buffer, 4)
        $offset = 8
        $ids = New-Object Collections.Generic.List[int]
        for ($index = 0; $index -lt $listed; $index++) {
            $pidValue = if ([IntPtr]::Size -eq 8) {
                [Runtime.InteropServices.Marshal]::ReadInt64($buffer, $offset + ($index * 8))
            } else {
                [Runtime.InteropServices.Marshal]::ReadInt32($buffer, $offset + ($index * 4))
            }
            if ($pidValue -gt 0 -and $pidValue -le [int]::MaxValue) {
                $ids.Add([int]$pidValue)
            }
        }
        return @($ids)
    } finally {
        [Runtime.InteropServices.Marshal]::FreeHGlobal($buffer)
    }
}

function Get-RecursiveDescendantIds {
    param([int]$RootProcessId)
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $byParent = @{}
    $byPid = @{}
    foreach ($process in $all) {
        $byPid[[int]$process.ProcessId] = $process
        $parent = [int]$process.ParentProcessId
        if (-not $byParent.ContainsKey($parent)) {
            $byParent[$parent] = New-Object Collections.Generic.List[int]
        }
        $byParent[$parent].Add([int]$process.ProcessId)
    }
    $result = New-Object Collections.Generic.List[int]
    $queue = New-Object Collections.Generic.Queue[int]
    $queue.Enqueue($RootProcessId)
    while ($queue.Count -gt 0) {
        $current = $queue.Dequeue()
        if (-not $byParent.ContainsKey($current)) { continue }
        if (-not $byPid.ContainsKey($current)) { continue }
        $parentCreatedUtc = $byPid[$current].CreationDate.ToUniversalTime()
        foreach ($child in $byParent[$current]) {
            if (-not $byPid.ContainsKey($child)) { continue }
            $childCreatedUtc = $byPid[$child].CreationDate.ToUniversalTime()
            # Win32_Process.ParentProcessId is only the creator PID; Windows
            # does not retain the creator identity after it exits.  Once that
            # PID is reused, an old unrelated process can otherwise look like
            # a descendant of this launch.  A real child cannot predate its
            # parent identity, so reject that stale-PID edge.
            if ($childCreatedUtc -lt $parentCreatedUtc) { continue }
            if (-not $result.Contains($child)) {
                $result.Add($child)
                $queue.Enqueue($child)
            }
        }
    }
    return @($result)
}

function Get-DynamicStatus {
    $manifest = Read-Manifest
    $unknown = New-Object Collections.Generic.List[object]

    $helperIdentityValid = Test-ProcessIdentity `
        -ProcessId ([int]$manifest.helper.pid) `
        -CreateTimeUtc ([string]$manifest.helper.create_time_utc)
    if (-not $helperIdentityValid) {
        $unknown.Add([ordered]@{ kind = "helper_identity"; pid = [int]$manifest.helper.pid; reason = "helper PID/create-time mismatch" })
    }

    $jobIds = @(Get-JobProcessIds | Sort-Object -Unique)
    $jobSet = @{}
    foreach ($jobPid in $jobIds) { $jobSet[[int]$jobPid] = $true }
    $records = New-Object Collections.Generic.List[object]
    foreach ($jobPid in $jobIds) {
        $record = Get-ProcessRecord -ProcessId $jobPid -ScopeBasis "job:$($manifest.job.identity)"
        if ($null -ne $record) { $records.Add($record) }
    }

    if ($script:RootPid -gt 0) {
        $rootProcess = Get-Process -Id $script:RootPid -ErrorAction SilentlyContinue
        if ($null -ne $rootProcess -and
            $rootProcess.StartTime.ToUniversalTime().ToString("o") -ne [string]$manifest.root.create_time_utc) {
            $unknown.Add([ordered]@{
                kind = "root_pid_reused"
                pid = $script:RootPid
                expected_create_time_utc = [string]$manifest.root.create_time_utc
                actual_create_time_utc = $rootProcess.StartTime.ToUniversalTime().ToString("o")
            })
        }
        foreach ($descendant in @(Get-RecursiveDescendantIds -RootProcessId $script:RootPid)) {
            if (-not $jobSet.ContainsKey([int]$descendant)) {
                $record = Get-ProcessRecord -ProcessId ([int]$descendant) -ScopeBasis "root_descendant_outside_job"
                if ($null -ne $record) {
                    $unknown.Add([ordered]@{
                        kind = "descendant_outside_job"
                        pid = [int]$descendant
                        create_time_utc = $record.create_time_utc
                        command_line = $record.command_line
                    })
                }
            }
        }
    }

    $listenerOwners = New-Object Collections.Generic.List[object]
    foreach ($port in @($BackendPort, $VitePort)) {
        foreach ($listener in @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
            $owner = [int]$listener.OwningProcess
            $inJob = $jobSet.ContainsKey($owner)
            $ownerRecord = Get-ProcessRecord -ProcessId $owner -ScopeBasis $(if ($inJob) { "job_listener" } else { "listener_outside_job" })
            $listenerRecord = [ordered]@{
                port = $port
                pid = $owner
                in_job = $inJob
                process = $ownerRecord
            }
            $listenerOwners.Add($listenerRecord)
            if (-not $inJob) {
                $unknown.Add([ordered]@{ kind = "listener_outside_job"; port = $port; pid = $owner })
            }
        }
    }

    $previousPids = @{}
    foreach ($previous in @($manifest.observed_processes)) {
        $previousPids[[int]$previous.pid] = $true
    }
    $late = @($records | Where-Object { -not $previousPids.ContainsKey([int]$_.pid) })
    $now = [DateTimeOffset]::UtcNow.ToString("o")
    $history = @($manifest.status_history)
    $history += [ordered]@{
        observed_at_utc = $now
        job_process_count = $records.Count
        listener_count = $listenerOwners.Count
        scope_unknown_count = $unknown.Count
    }
    # PowerShell 5.1 keeps the deserialized manifest property as Object[].
    # Assign concrete arrays rather than Generic.List instances, otherwise
    # the property binder raises "argument type mismatch" on real Status.
    $recordValues = @($records | ForEach-Object { $_ })
    $listenerValues = @($listenerOwners | ForEach-Object { $_ })
    $unknownValues = @($unknown | ForEach-Object { $_ })
    $manifest.observed_processes = $recordValues
    $knownLate = @{}
    foreach ($record in @($manifest.late_descendants)) {
        $knownLate["$($record.pid)|$($record.create_time_utc)"] = $true
    }
    $newLate = @(
        foreach ($record in $late) {
            $key = "$($record.pid)|$($record.create_time_utc)"
            if (-not $knownLate.ContainsKey($key)) {
                $knownLate[$key] = $true
                $record
            }
        }
    )
    $manifest.late_descendants = @($manifest.late_descendants) + @($newLate)
    $manifest.listener_owners = $listenerValues
    $manifest.scope_unknown = $unknownValues
    $manifest.status_history = $history
    $manifest.updated_at_utc = $now
    Write-JsonAtomic -Path $script:ManifestPath -Value $manifest

    return [ordered]@{
        ok = ($unknown.Count -eq 0)
        action = "status"
        scenario_id = $ScenarioId
        launch_id = $LaunchId
        state = [string]$manifest.state
        job_processes = $recordValues
        late_descendants = @($newLate)
        listener_owners = $listenerValues
        scope_unknown = $unknownValues
        observed_at_utc = $now
    }
}

function Invoke-GracefulStop {
    $status = Get-DynamicStatus
    if (-not $status.ok) {
        return [ordered]@{
            ok = $false
            action = "stop"
            error = "scope_unknown"
            status = $status
        }
    }

    $records = @($status.job_processes)
    $recordByPid = @{}
    foreach ($record in $records) { $recordByPid[[int]$record.pid] = $record }
    $withDepth = @(
        foreach ($record in $records) {
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
    foreach ($item in @($withDepth | Sort-Object depth -Descending)) {
        $record = $item.record
        $recordIdentityValid = Test-ProcessIdentity `
            -ProcessId ([int]$record.pid) `
            -CreateTimeUtc ([string]$record.create_time_utc)
        if (-not $recordIdentityValid) {
            continue
        }
        $process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
        if ($null -ne $process) {
            try { [void]$process.CloseMainWindow() } catch { }
        }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    while ([DateTime]::UtcNow -lt $deadline -and @(Get-JobProcessIds).Count -gt 0) {
        Start-Sleep -Milliseconds 100
    }

    [int64]$privateBytes = 0
    foreach ($record in $records) {
        $privateBytes += [int64]$record.private_bytes
    }
    if ($script:JobHandle -ne [IntPtr]::Zero) {
        [void][CompanionGrowthNative]::CloseHandle($script:JobHandle)
        $script:JobHandle = [IntPtr]::Zero
    }
    $script:StopRequested = $true
    $manifest = Read-Manifest
    $manifest.state = "stopping"
    $manifest.stop_requested_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    Write-JsonAtomic -Path $script:ManifestPath -Value $manifest
    return [ordered]@{
        ok = $true
        action = "stop"
        released_private_bytes_candidate = $privateBytes
        stopped_processes = @($records)
        requested_at_utc = $manifest.stop_requested_at_utc
    }
}

function Test-ControlToken {
    param([string]$Candidate)
    if ([string]::IsNullOrEmpty($Candidate)) { return $false }
    $left = [Text.Encoding]::UTF8.GetBytes($script:ControlToken)
    $right = [Text.Encoding]::UTF8.GetBytes($Candidate)
    if ($left.Length -ne $right.Length) { return $false }
    $difference = 0
    for ($index = 0; $index -lt $left.Length; $index++) {
        $difference = $difference -bor ($left[$index] -bxor $right[$index])
    }
    return $difference -eq 0
}

function Serve-ControlPipe {
    while (-not $script:StopRequested) {
        $pipe = New-Object IO.Pipes.NamedPipeServerStream(
            $script:PipeName,
            [IO.Pipes.PipeDirection]::InOut,
            1,
            [IO.Pipes.PipeTransmissionMode]::Byte,
            [IO.Pipes.PipeOptions]::None
        )
        try {
            $pipe.WaitForConnection()
            $reader = New-Object IO.StreamReader($pipe, (New-Object Text.UTF8Encoding($false)), $false, 4096, $true)
            $writer = New-Object IO.StreamWriter($pipe, (New-Object Text.UTF8Encoding($false)), 4096, $true)
            $writer.AutoFlush = $true
            try {
                $line = $reader.ReadLine()
                $request = $line | ConvertFrom-Json
                if (-not (Test-ControlToken -Candidate ([string]$request.token))) {
                    $response = [ordered]@{ ok = $false; error = "authentication_failed" }
                } elseif ($request.action -eq "status") {
                    $response = Get-DynamicStatus
                } elseif ($request.action -eq "stop") {
                    $response = Invoke-GracefulStop
                } else {
                    $response = [ordered]@{ ok = $false; error = "unsupported_action" }
                }
                $writer.WriteLine(($response | ConvertTo-Json -Depth 16 -Compress))
            } catch {
                $writer.WriteLine(([ordered]@{
                    ok = $false
                    error = $_.Exception.Message
                    trace = $_.ScriptStackTrace
                } | ConvertTo-Json -Compress))
            } finally {
                $reader.Dispose()
                $writer.Dispose()
            }
        } finally {
            $pipe.Dispose()
        }
    }
}

function Start-SuspendedTauriRoot {
    $pnpm = (Get-Command pnpm.cmd -ErrorAction Stop).Source
    $pnpmDirectory = Split-Path -Parent $pnpm
    $bundledNodeBin = [IO.Path]::GetFullPath(
        (Join-Path $pnpmDirectory "..\..\node\bin")
    )
    if (Test-Path -LiteralPath (Join-Path $bundledNodeBin "node.exe")) {
        $env:PATH = "$script:LaunchRoot;$bundledNodeBin;$env:PATH"
    } elseif (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) {
        throw "pnpm is available but node.exe is not resolvable for project shims."
    }
    $cmd = Join-Path $env:SystemRoot "System32\cmd.exe"
    $escapedPnpm = $pnpm.Replace('"', '""')
    $escapedConfig = $script:ConfigPath.Replace('"', '""')
    $escapedLog = $script:LogPath.Replace('"', '""')
    $commandLine = '/d /s /c ""{0}" exec tauri dev --config "{1}" >> "{2}" 2>&1"' -f $escapedPnpm, $escapedConfig, $escapedLog

    $env:DESKPET_BACKEND_DIR = $script:BackendPath
    $env:DESKPET_PYTHON = $script:PythonPath
    $env:DESKPET_BACKEND_PORT = [string]$BackendPort
    $env:DESKPET_VITE_PORT = [string]$VitePort
    $env:DESKPET_USER_DATA_DIR = $script:UserDataPath
    $env:DESKPET_DEV_MODE = "1"
    $env:DESKPET_E2E_CLOCK_UTC = $ClockUtc
    # The Codex-managed pnpm wrapper performs a supply-chain status install
    # before exec. Dependencies are already locked and present; suppressing
    # lifecycle scripts avoids the wrapper's ignored-builds hard failure.
    $env:pnpm_config_ignore_scripts = "true"

    $startup = New-Object CompanionGrowthNative+STARTUPINFO
    $startup.cb = [Runtime.InteropServices.Marshal]::SizeOf($startup)
    $processInfo = New-Object CompanionGrowthNative+PROCESS_INFORMATION
    $created = [CompanionGrowthNative]::CreateProcess(
        $cmd,
        (New-Object Text.StringBuilder($commandLine)),
        [IntPtr]::Zero,
        [IntPtr]::Zero,
        $false,
        ([CompanionGrowthNative]::CREATE_SUSPENDED -bor [CompanionGrowthNative]::CREATE_NEW_PROCESS_GROUP),
        [IntPtr]::Zero,
        $script:TauriPath,
        [ref]$startup,
        [ref]$processInfo
    )
    if (-not $created) {
        throw "CreateProcess(CREATE_SUSPENDED) failed with Win32 error $([Runtime.InteropServices.Marshal]::GetLastWin32Error())."
    }
    $script:RootProcessHandle = $processInfo.hProcess
    $script:RootPid = $processInfo.dwProcessId
    try {
        if (-not [CompanionGrowthNative]::AssignProcessToJobObject($script:JobHandle, $processInfo.hProcess)) {
            throw "AssignProcessToJobObject failed with Win32 error $([Runtime.InteropServices.Marshal]::GetLastWin32Error())."
        }
        $resumeResult = [CompanionGrowthNative]::ResumeThread($processInfo.hThread)
        if ($resumeResult -eq [uint32]::MaxValue) {
            throw "ResumeThread failed with Win32 error $([Runtime.InteropServices.Marshal]::GetLastWin32Error())."
        }
    } finally {
        [void][CompanionGrowthNative]::CloseHandle($processInfo.hThread)
    }
}

function Wait-SourceStackReady {
    $deadline = [DateTime]::UtcNow.AddMinutes(5)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (-not (Get-Process -Id $script:RootPid -ErrorAction SilentlyContinue)) {
            throw "The suspended Tauri root exited before the source stack became ready."
        }
        $logText = if (Test-Path -LiteralPath $script:LogPath) {
            Read-SharedText -Path $script:LogPath
        } else {
            ""
        }
        if ($logText -match '\[backend_launch\]\s+Bundled exe=') {
            throw "Tauri selected a bundled backend; source-backend E2E is invalid."
        }
        $sourceMarker = $logText -match '\[backend_launch\]\s+Dev python=' -and
            $logText.IndexOf($script:BackendPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
        $backendReady = $false
        $viteReady = $false
        try {
            $backendResponse = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$BackendPort/health" -TimeoutSec 2
            $backendReady = $backendResponse.StatusCode -eq 200
        } catch { }
        try {
            $viteResponse = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:$VitePort/" -TimeoutSec 2
            $viteReady = $viteResponse.StatusCode -eq 200
        } catch { }
        if ($sourceMarker -and $backendReady -and $viteReady) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "Timed out waiting for source backend, Vite, and source-path log evidence."
}

try {
    foreach ($path in @($script:ScenarioRoot, $script:LaunchRoot, $script:ManifestPath, $script:TokenPath, $script:ConfigPath, $script:NpmShimPath, $script:LogPath)) {
        Assert-NoReparsePoint -Path $path -TrustedRoot $script:RuntimeRoot
    }
    foreach ($required in @($script:ManifestPath, $script:TokenPath, $script:ConfigPath, $script:NpmShimPath, $script:BackendPath, $script:PythonPath, $script:TauriPath)) {
        if (-not (Test-Path -LiteralPath $required)) {
            throw "Required lifecycle path is missing: $required"
        }
    }

    $protectedToken = [IO.File]::ReadAllBytes($script:TokenPath)
    $tokenBytes = [Security.Cryptography.ProtectedData]::Unprotect(
        $protectedToken,
        [Text.Encoding]::UTF8.GetBytes("$ScenarioId/$LaunchId"),
        [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    $script:ControlToken = [Text.Encoding]::UTF8.GetString($tokenBytes)
    $script:PipeName = "deskpet-companion-growth-$LaunchId-$([Guid]::NewGuid().ToString('N'))"
    $jobIdentity = "Local\DeskPetCompanionGrowth-$LaunchId-$([Guid]::NewGuid().ToString('N'))"
    $script:JobHandle = New-KillOnCloseJob -Name $jobIdentity

    Start-SuspendedTauriRoot
    $helperRecord = Get-ProcessRecord -ProcessId $PID -ScopeBasis "lifecycle_helper"
    $rootRecord = Get-ProcessRecord -ProcessId $script:RootPid -ScopeBasis "job:$jobIdentity"
    $manifest = Read-Manifest
    $manifest.helper = $helperRecord
    $manifest.root = $rootRecord
    $manifest.job = [ordered]@{
        identity = $jobIdentity
        kill_on_job_close = $true
        silent_breakaway_allowed = $false
        assigned_before_resume = $true
    }
    $manifest.control_pipe = $script:PipeName
    $manifest.state = "booting"
    $manifest.updated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    Write-JsonAtomic -Path $script:ManifestPath -Value $manifest

    Wait-SourceStackReady
    $manifest = Read-Manifest
    $manifest.state = "ready"
    $manifest.ready_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    $manifest.observed_processes = @(
        foreach ($jobPid in @(Get-JobProcessIds | Sort-Object -Unique)) {
            Get-ProcessRecord -ProcessId $jobPid -ScopeBasis "job:$jobIdentity"
        }
    )
    $manifest.late_descendants = @()
    $manifest.listener_owners = @()
    $manifest.scope_unknown = @()
    $manifest.status_history = @()
    Write-JsonAtomic -Path $script:ManifestPath -Value $manifest
    Serve-ControlPipe
} catch {
    try {
        if (Test-Path -LiteralPath $script:ManifestPath) {
            $failed = Read-Manifest
            $failed.state = "failed"
            $failed.failure = $_.Exception.Message
            $failed.failed_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
            Write-JsonAtomic -Path $script:ManifestPath -Value $failed
        }
    } catch { }
    throw
} finally {
    if ($script:RootProcessHandle -ne [IntPtr]::Zero) {
        [void][CompanionGrowthNative]::CloseHandle($script:RootProcessHandle)
        $script:RootProcessHandle = [IntPtr]::Zero
    }
    if ($script:JobHandle -ne [IntPtr]::Zero) {
        [void][CompanionGrowthNative]::CloseHandle($script:JobHandle)
        $script:JobHandle = [IntPtr]::Zero
    }
}
