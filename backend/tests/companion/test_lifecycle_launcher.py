from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import json
import shutil
import time
import uuid

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = REPO_ROOT / "scripts" / "e2e" / "launch_companion_growth.ps1"
JOB_HOST = REPO_ROOT / "scripts" / "e2e" / "companion_growth_job_host.ps1"
RUNTIME_ROOT = (
    REPO_ROOT
    / "plans"
    / "2026-07-24-human-anchored-companion-growth"
    / "evidence"
    / "manual-runtime"
)


def _powershell() -> str:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    executable = (
        system_root
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    if not executable.is_file():
        pytest.skip("Windows PowerShell is unavailable")
    return str(executable)


def _run_launcher(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(LAUNCHER),
            *arguments,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def _two_free_ports() -> tuple[int, int]:
    sockets: list[socket.socket] = []
    try:
        for _ in range(2):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
            sockets.append(listener)
        return tuple(listener.getsockname()[1] for listener in sockets)  # type: ignore[return-value]
    finally:
        for listener in sockets:
            listener.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows lifecycle contract")
def test_lifecycle_scripts_parse_without_powershell_errors() -> None:
    quoted_paths = [str(path).replace("'", "''") for path in (LAUNCHER, JOB_HOST)]
    paths = ",".join(f"'{path}'" for path in quoted_paths)
    command = (
        "$failed=$false;"
        f"foreach($path in @({paths})){{"
        "$tokens=$null;$errors=$null;"
        "[void][Management.Automation.Language.Parser]::ParseFile("
        "(Resolve-Path -LiteralPath $path),[ref]$tokens,[ref]$errors);"
        "if($errors.Count -gt 0){$errors|ForEach-Object{Write-Error $_};$failed=$true}"
        "};"
        "if($failed){exit 1}"
    )
    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_job_host_sums_private_bytes_without_measure_object_property_binding() -> None:
    helper = JOB_HOST.read_text(encoding="utf-8")
    assert "Measure-Object -Property private_bytes" not in helper
    assert "$privateBytes += [int64]$record.private_bytes" in helper


@pytest.mark.skipif(os.name != "nt", reason="Windows lifecycle contract")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ScenarioId", ".."),
        ("ScenarioId", r"case\escape"),
        ("ScenarioId", "case%2fescape"),
        ("LaunchId", "launch.with.dot"),
        ("LaunchId", "launch:ads"),
        ("LaunchId", "launch with space"),
    ],
)
def test_invalid_ids_fail_before_creating_runtime_paths(field: str, value: str) -> None:
    safe_scenario = f"contract-{uuid.uuid4().hex}"
    safe_launch = f"contract-{uuid.uuid4().hex}"
    arguments = {
        "ScenarioId": safe_scenario,
        "LaunchId": safe_launch,
    }
    arguments[field] = value
    result = _run_launcher(
        "-Action",
        "Status",
        "-ScenarioId",
        arguments["ScenarioId"],
        "-LaunchId",
        arguments["LaunchId"],
    )
    assert result.returncode != 0
    assert not (RUNTIME_ROOT / safe_scenario).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows lifecycle contract")
def test_non_utc_clock_fails_before_launch_artifacts_are_created() -> None:
    scenario = f"contract-{uuid.uuid4().hex}"
    launch = f"contract-{uuid.uuid4().hex}"
    backend_port, vite_port = _two_free_ports()
    result = _run_launcher(
        "-Action",
        "Start",
        "-ScenarioId",
        scenario,
        "-LaunchId",
        launch,
        "-BackendPort",
        str(backend_port),
        "-VitePort",
        str(vite_port),
        "-ClockUtc",
        "2026-07-25T12:00:00+08:00",
    )
    assert result.returncode != 0
    assert "absolute UTC" in (result.stdout + result.stderr)
    assert not (RUNTIME_ROOT / scenario).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows lifecycle contract")
def test_long_scenario_fails_user_data_path_budget_before_artifacts() -> None:
    scenario = "s" * 64
    launch = f"contract-{uuid.uuid4().hex}"
    backend_port, vite_port = _two_free_ports()
    result = _run_launcher(
        "-Action",
        "Start",
        "-ScenarioId",
        scenario,
        "-LaunchId",
        launch,
        "-BackendPort",
        str(backend_port),
        "-VitePort",
        str(vite_port),
        "-ClockUtc",
        "2026-07-25T12:00:00Z",
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "Windows user-data path budget" in combined
    assert "projected first-party pack path length" in combined
    assert not (RUNTIME_ROOT / scenario).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows lifecycle contract")
def test_stop_falls_back_to_frozen_root_ancestry_when_helper_is_dead() -> None:
    scenario = f"c{uuid.uuid4().hex[:8]}"
    launch = f"l{uuid.uuid4().hex[:8]}"
    launch_root = RUNTIME_ROOT / scenario / "launches" / launch
    launch_root.mkdir(parents=True)
    (launch_root / "logs").mkdir()
    (RUNTIME_ROOT / scenario / "user-data").mkdir()
    backend_port, vite_port = _two_free_ports()
    marker = launch_root / "child.pid"
    escaped_marker = str(marker).replace("'", "''")
    root_command = (
        "$child=Start-Process cmd.exe -ArgumentList "
        "'/d','/c','ping -n 300 127.0.0.1 >nul' "
        "-WindowStyle Hidden -PassThru;"
        f"[IO.File]::WriteAllText('{escaped_marker}',[string]$child.Id);"
        "Wait-Process -Id $child.Id"
    )
    root = subprocess.Popen(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            root_command,
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists()
        child_pid = int(marker.read_text())
        child_parent = subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={child_pid}').ParentProcessId",
            ],
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        assert int(child_parent) == root.pid
        create_time = subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-Process -Id {root.pid}).StartTime.ToUniversalTime().ToString('o')",
            ],
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        manifest = {
            "schema_version": 1,
            "scenario_id": scenario,
            "launch_id": launch,
            "state": "booting",
            "started_at_utc": "2026-07-25T12:00:00Z",
            "clock_utc": "2026-07-25T12:00:00Z",
            "ports": {"backend": backend_port, "vite": vite_port},
            "paths": {
                "repo_root": str(REPO_ROOT),
                "scenario_root": str(RUNTIME_ROOT / scenario),
                "launch_root": str(launch_root),
                "user_data": str(RUNTIME_ROOT / scenario / "user-data"),
                "source_backend": str(REPO_ROOT / "backend"),
                "python": str(REPO_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"),
                "temp_config": str(launch_root / "tauri-dev-config.json"),
                "npm_shim": str(launch_root / "npm.cmd"),
                "tauri_log": str(launch_root / "logs" / "tauri.log"),
            },
            "helper": None,
            "root": {
                "pid": root.pid,
                "create_time_utc": create_time,
                "parent_pid": os.getpid(),
                "command_line": root_command,
                "executable_path": _powershell(),
                "private_bytes": 1,
                "scope_basis": "fixture_root",
            },
            "job": None,
            "control_pipe": None,
            "observed_processes": [
                {
                    "pid": os.getpid(),
                    "create_time_utc": "2000-01-01T00:00:00.0000000Z",
                    "parent_pid": 0,
                    "command_line": "reused fixture identity",
                    "executable_path": "",
                    "private_bytes": 1,
                    "scope_basis": "stale_manifest_identity",
                }
            ],
            "late_descendants": [],
            "listener_owners": [],
            "scope_unknown": [],
            "status_history": [],
            "updated_at_utc": None,
            "ready_at_utc": None,
            "stop_requested_at_utc": None,
            "stopped_at_utc": None,
            "failure": None,
            "failed_at_utc": None,
            "cleanup_result": None,
            "temp_config_sha256": "",
            "temp_config_file_identity": "",
            "npm_shim_sha256": "",
            "npm_shim_file_identity": "",
        }
        (launch_root / "process-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        result = _run_launcher(
            "-Action",
            "Stop",
            "-ScenarioId",
            scenario,
            "-LaunchId",
            launch,
            "-BackendPort",
            str(backend_port),
            "-VitePort",
            str(vite_port),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        cleanup = json.loads((launch_root / "cleanup-result.json").read_text())
        assert cleanup["reason"] == "stop_offline_fallback"
        assert cleanup["survivor_count"] == 0
        assert cleanup["released_private_bytes"] > 0
        assert any(note["kind"] == "pid_reused" for note in cleanup["audit_notes"])
        assert any(item["pid"] == child_pid for item in cleanup["process_results"])
        assert root.poll() is not None
        child_check = subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"if(Get-Process -Id {child_pid} -ErrorAction SilentlyContinue){{exit 1}}",
            ],
            check=False,
        )
        assert child_check.returncode == 0
        repeated = _run_launcher(
            "-Action",
            "Stop",
            "-ScenarioId",
            scenario,
            "-LaunchId",
            launch,
            "-BackendPort",
            str(backend_port),
            "-VitePort",
            str(vite_port),
        )
        assert repeated.returncode == 0, repeated.stdout + repeated.stderr
        repeated_cleanup = json.loads(
            (launch_root / "cleanup-result.json").read_text()
        )
        assert repeated_cleanup["survivor_count"] == 0
    finally:
        if root.poll() is None:
            root.kill()
            root.wait(timeout=10)
        if launch_root.parents[1].exists():
            shutil.rmtree(launch_root.parents[1])


def test_lifecycle_contract_is_job_scoped_and_never_broad_kills() -> None:
    launcher = LAUNCHER.read_text(encoding="utf-8")
    helper = JOB_HOST.read_text(encoding="utf-8")
    combined = f"{launcher}\n{helper}"

    for required in (
        "CREATE_SUSPENDED",
        "AssignProcessToJobObject",
        "ResumeThread",
        "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
        "QueryInformationJobObject",
        "NamedPipeServerStream",
        "NamedPipeClientStream",
        "DataProtectionScope]::CurrentUser",
        "DESKPET_BACKEND_DIR",
        "DESKPET_PYTHON",
        "DESKPET_BACKEND_PORT",
        "DESKPET_VITE_PORT",
        "DESKPET_USER_DATA_DIR",
        "DESKPET_DEV_MODE",
        "DESKPET_E2E_CLOCK_UTC",
        "exec tauri dev --config",
        "npm_shim_sha256",
        "npm_shim_file_identity",
        "$recordValues = @($records | ForEach-Object { $_ })",
        "$listenerValues = @($listenerOwners | ForEach-Object { $_ })",
        "$unknownValues = @($unknown | ForEach-Object { $_ })",
    ):
        assert required in combined

    lowered = combined.lower()
    assert "taskkill" not in lowered
    assert "stop-process" not in lowered
    assert "/im " not in lowered
    assert "start-process -filepath $script:pythonpath" not in lowered
    assert "New-Item -ItemType Directory -LiteralPath" not in combined
    assert "[IO.File]::Replace($temporary, $Path, $null" not in combined
    assert combined.count("[IO.File]::Replace($temporary, $Path, $backup") == 2
    assert "Complete-ScopedCleanup" in launcher
    assert 'Reason "start_failed"' in launcher
    assert "exact_start_process_identity" in launcher
    assert "verified_process_ancestry" in launcher
    assert "immutable_launch_marker" in launcher
    assert "Stop-VerifiedProcessRecords" in launcher
    assert launcher.count("[AllowEmptyCollection()][object[]]$ExtraRecords") == 2


def test_launcher_derives_manifest_and_enforces_global_launch_identity() -> None:
    launcher = LAUNCHER.read_text(encoding="utf-8")
    helper = JOB_HOST.read_text(encoding="utf-8")
    assert "ManifestPath" not in launcher.split("param(", 1)[1].split(")", 1)[0]
    assert 'Join-Path $script:LaunchRoot "process-manifest.json"' in launcher
    assert "function Read-SharedText" in launcher
    assert "Read-SharedText -Path $script:ManifestPath | ConvertFrom-Json" in launcher
    assert "Read-SharedText -Path $script:ManifestPath | ConvertFrom-Json" in helper
    assert "[IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete" in launcher
    assert "[IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete" in helper
    assert "Find-ExistingLaunchId" in launcher
    assert "Every Start requires a globally unique LaunchId" in launcher
    assert "temp_config_sha256" in launcher
    assert "temp_config_file_identity" in launcher
    assert "Refusing to delete $($item.label) whose frozen content identity changed" in launcher
    assert "Refusing to delete a replaced $($item.label) file identity" in launcher
