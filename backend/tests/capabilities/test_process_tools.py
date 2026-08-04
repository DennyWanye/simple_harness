from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import psutil
import pytest

from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.os_tools.process_tools import (
    ManagedProcessLease,
    ProcessIdentity,
    ProcessToolService,
    process_start,
    process_stop,
    process_wait,
    set_process_tool_service,
)


def _context(root_run_id: str) -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id="scope",
        session_id="session",
        request_id="request",
        root_run_id=root_run_id,
        run_id=f"run:{root_run_id}",
        call_id="call",
        effect_id="effect",
    )


@pytest.mark.asyncio
async def test_process_start_is_argv_only_and_stop_is_same_run_scoped(
    tmp_path: Path,
) -> None:
    service = ProcessToolService(log_root=tmp_path / "logs")
    previous = set_process_tool_service(service)
    try:
        rejected = json.loads(
            await process_start(
                {"command": f"{sys.executable} -c pass"},
                execution_context=_context("root-a"),
            )
        )
        assert rejected["error"]["code"] == "shell_string_rejected"

        started = json.loads(
            await process_start(
                {
                    "executable": sys.executable,
                    "argv": ["-c", "import time; time.sleep(30)"],
                },
                execution_context=_context("root-a"),
            )
        )
        process = started["process"]
        assert process["command_line"]
        assert process["creation_time"] > 0
        assert Path(process["stdout_log_ref"]).parent.is_relative_to(tmp_path / "logs")

        forbidden = json.loads(
            await process_stop(
                {"lease_id": process["lease_id"]},
                execution_context=_context("root-b"),
            )
        )
        assert forbidden["error"]["code"] == "process_identity_mismatch"
        assert psutil.pid_exists(process["pid"])

        stopped = json.loads(
            await process_stop(
                {"lease_id": process["lease_id"]},
                execution_context=_context("root-a"),
            )
        )
        assert stopped["ok"] is True
        assert stopped["cleanup"]["remaining_pids"] == []
        assert process["pid"] in stopped["cleanup"]["stopped_pids"]
    finally:
        await service.reset_for_tests()
        set_process_tool_service(previous)


@pytest.mark.asyncio
async def test_explicit_identity_mismatch_does_not_stop_unrelated_process(
    tmp_path: Path,
) -> None:
    service = ProcessToolService(log_root=tmp_path / "logs")
    previous = set_process_tool_service(service)
    unrelated = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)"
    )
    try:
        identity = psutil.Process(unrelated.pid)
        result = json.loads(
            await process_stop(
                {
                    "pid": unrelated.pid,
                    "creation_time": identity.create_time() + 100,
                    "command_line": identity.cmdline(),
                },
                execution_context=_context("root-a"),
            )
        )
        assert result["error"]["code"] == "process_identity_mismatch"
        assert psutil.pid_exists(unrelated.pid)
    finally:
        if unrelated.returncode is None:
            unrelated.kill()
        await unrelated.wait()
        await service.reset_for_tests()
        set_process_tool_service(previous)


@pytest.mark.asyncio
async def test_process_wait_times_out_without_killing_then_observes_exit(
    tmp_path: Path,
) -> None:
    service = ProcessToolService(log_root=tmp_path / "logs")
    previous = set_process_tool_service(service)
    try:
        started = json.loads(
            await process_start(
                {
                    "executable": sys.executable,
                    "argv": ["-c", "import time; time.sleep(0.3)"],
                },
                execution_context=_context("root-a"),
            )
        )["process"]
        pending = json.loads(
            await process_wait(
                {"lease_id": started["lease_id"], "timeout_seconds": 0.01},
                execution_context=_context("root-a"),
            )
        )
        assert pending["process"]["running"] is True
        completed = json.loads(
            await process_wait(
                {"lease_id": started["lease_id"], "timeout_seconds": 2},
                execution_context=_context("root-a"),
            )
        )
        assert completed["process"]["running"] is False
        assert completed["process"]["exit_code"] == 0
    finally:
        await service.reset_for_tests()
        set_process_tool_service(previous)


@pytest.mark.asyncio
async def test_lease_tracks_child_after_launcher_exits(tmp_path: Path) -> None:
    service = ProcessToolService(log_root=tmp_path / "logs")
    previous = set_process_tool_service(service)
    try:
        code = (
            "import subprocess,sys,time;"
            "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
            "time.sleep(0.3)"
        )
        started = json.loads(
            await process_start(
                {"executable": sys.executable, "argv": ["-c", code]},
                execution_context=_context("root-child"),
            )
        )["process"]
        await asyncio.sleep(0.5)
        waited = json.loads(
            await process_wait(
                {"lease_id": started["lease_id"], "timeout_seconds": 1},
                execution_context=_context("root-child"),
            )
        )["process"]
        assert waited["exit_code"] == 0
        assert waited["running"] is True
        assert waited["descendant_pids"]
        stopped = json.loads(
            await process_stop(
                {"lease_id": started["lease_id"]},
                execution_context=_context("root-child"),
            )
        )
        assert stopped["cleanup"]["remaining_pids"] == []
        assert set(waited["descendant_pids"]).issubset(
            set(stopped["cleanup"]["stopped_pids"])
        )
    finally:
        await service.reset_for_tests()
        set_process_tool_service(previous)


@pytest.mark.asyncio
async def test_process_monitor_tolerates_root_disappearing_during_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = ProcessIdentity(
        pid=424242,
        creation_time=10.0,
        command_line=("vanishing.exe",),
        executable="vanishing.exe",
    )

    class VanishingProcess:
        def create_time(self) -> float:
            return identity.creation_time

        def cmdline(self) -> list[str]:
            return list(identity.command_line)

        def children(self, *, recursive: bool) -> list[psutil.Process]:
            assert recursive is True
            raise psutil.NoSuchProcess(identity.pid)

    monkeypatch.setattr(
        "deskpet.tools.os_tools.process_tools.psutil.Process",
        lambda _pid: VanishingProcess(),
    )
    service = ProcessToolService(log_root=tmp_path / "logs")
    lease = ManagedProcessLease(
        lease_id="process:root-race:test",
        root_run_id="root-race",
        process=object(),  # type: ignore[arg-type]
        identity=identity,
        stdout_log=tmp_path / "stdout.log",
        stderr_log=tmp_path / "stderr.log",
        stdout_handle=None,
        stderr_handle=None,
        started_at=0.0,
        observed={identity.pid: identity},
    )
    service._observe_lease(lease)

    async def failed_monitor() -> None:
        raise psutil.NoSuchProcess(identity.pid)

    lease.monitor_task = asyncio.create_task(failed_monitor())
    await asyncio.sleep(0)
    service._leases[lease.lease_id] = lease
    await service._finalize_lease(lease)
    assert lease.lease_id not in service._leases
