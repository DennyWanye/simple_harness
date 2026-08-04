from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import psutil
import pytest

from deskpet.capabilities.local_runtime import LocalRuntimeRequest, LocalToolRuntime

WORKER = Path(__file__).parent / "fixtures" / "json_tool_worker.py"


def _request() -> LocalRuntimeRequest:
    return LocalRuntimeRequest(
        tool="fixture.echo",
        args={"hello": "世界"},
        root_run_id="root-runtime-test",
        run_id="run-runtime-test",
        effect_id="effect-runtime-test",
    )


@pytest.mark.asyncio
async def test_success_uses_argv_and_single_json_protocol() -> None:
    runtime = LocalToolRuntime()
    result = await runtime.execute([sys.executable, str(WORKER), "success"], _request())
    assert result.status == "success"
    assert result.response is not None
    assert result.response["value"] == {"echo": {"hello": "世界"}}
    assert result.stderr == "bounded fixture log"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("malformed", "not JSON"),
        ("multiple", "more than one"),
        ("wrong-request", "request_id"),
        ("host-spoof", "unsupported keys"),
    ],
)
async def test_protocol_rejects_malformed_and_spoofed_results(
    mode: str, message: str
) -> None:
    runtime = LocalToolRuntime()
    result = await runtime.execute([sys.executable, str(WORKER), mode], _request())
    assert result.status == "malformed"
    assert message in (result.error_message or "")


@pytest.mark.asyncio
async def test_stdout_limit_is_classified_as_malformed() -> None:
    runtime = LocalToolRuntime(stdout_limit_bytes=1024)
    result = await runtime.execute([sys.executable, str(WORKER), "oversized"], _request())
    assert result.status == "malformed"
    assert "exceeded 1024" in (result.error_message or "")


@pytest.mark.asyncio
async def test_worker_declared_failure_is_not_malformed() -> None:
    runtime = LocalToolRuntime()
    result = await runtime.execute([sys.executable, str(WORKER), "failure"], _request())
    assert result.status == "failure"
    assert result.error_code == "fixture_failure"


@pytest.mark.asyncio
async def test_nonzero_exit_is_classified_as_crash() -> None:
    runtime = LocalToolRuntime()
    result = await runtime.execute([sys.executable, str(WORKER), "crash"], _request())
    assert result.status == "crash"
    assert result.exit_code == 7
    assert result.error_code == "local_tool_process_failed"


@pytest.mark.asyncio
async def test_timeout_cleans_only_observed_lease_tree() -> None:
    unrelated = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)"
    )
    try:
        runtime = LocalToolRuntime(
            default_timeout_seconds=0.5,
            monitor_interval_seconds=0.01,
            cleanup_grace_seconds=0.5,
        )
        result = await runtime.execute(
            [sys.executable, str(WORKER), "hang-child"], _request()
        )
        assert result.status == "timeout"
        assert result.cleanup is not None
        assert len(result.cleanup.matched_pids) >= 2
        assert result.cleanup.remaining_pids == ()
        assert psutil.pid_exists(unrelated.pid)
    finally:
        if unrelated.returncode is None:
            unrelated.kill()
        await unrelated.wait()


@pytest.mark.asyncio
async def test_cancel_releases_runtime_lease() -> None:
    runtime = LocalToolRuntime(
        default_timeout_seconds=30,
        monitor_interval_seconds=0.01,
        cleanup_grace_seconds=0.5,
    )
    task = asyncio.create_task(
        runtime.execute([sys.executable, str(WORKER), "hang-child"], _request())
    )
    await asyncio.sleep(0.25)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime._leases == {}
