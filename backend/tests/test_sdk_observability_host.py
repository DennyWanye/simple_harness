from __future__ import annotations

import json
import importlib.util
import logging
import re
from pathlib import Path

import pytest

from observability.sdk import (
    HostCorrelationPolicy,
    HostSdkObservability,
    SDK_EVENTS_FILENAME,
    SDK_RING_FILENAME,
    SDK_SNAPSHOT_FILENAME,
)


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("simple_harness.observability") is None,
    reason="run against Harness bc6ae8d source until Host revendors the final wheel",
)


def _sdk():
    from simple_harness.observability import ObservabilityRuntime, Outcome

    return ObservabilityRuntime, Outcome


def _all_bytes(root: Path) -> bytes:
    return b"\n".join(path.read_bytes() for path in root.rglob("*") if path.is_file())


def test_shared_sink_correlation_and_bounded_exports_are_safe(tmp_path, caplog):
    runtime_type, outcome = _sdk()
    caplog.set_level(logging.INFO, logger="test.sdk.observability")
    host = HostSdkObservability(tmp_path, logger=logging.getLogger("test.sdk.observability"))
    assert host.available

    token = host.bind_ingress(
        run_id="run-authority-1",
        session_id="session-authority-1",
        request_id="CANARY_EXTERNAL_CORRELATION_readable",
    )
    try:
        correlation = host.correlation("memory-entity-1", "session-authority-1")
        assert re.fullmatch(r"[0-9a-f]{32,64}", correlation.trace_id)
        runtime = runtime_type(host.sink)
        canaries = {
            "CANARY_MEMORY_BODY_secret",
            "sk-CANARY_API_KEY_secret",
            "Bearer CANARY_AUTH_secret",
            "CANARY_TOKEN_secret",
            "CANARY_EXCEPTION_secret",
        }
        for index, canary in enumerate(canaries):
            assert not runtime.emit_transition(
                "host.canary.rejected",
                component="host",
                operation="ingress",
                outcome=outcome.FAILED,
                correlation=correlation.child(),
                attributes={
                    "unknown_sensitive_value": canary,
                    "attempt": index + 1,
                    "duration_ms": 1,
                    "error_code": "host_canary_rejected",
                },
            )
        assert runtime.emit_transition(
            "host.ingress.accepted",
            component="host",
            operation="ingress",
            outcome=outcome.ACCEPTED,
            correlation=correlation,
            attributes={"attempt": 1, "duration_ms": 0},
        )
        assert runtime.flush(1.0)
        host.register_snapshot_source(
            "memory", lambda: {"health": "ok", "queue_count": 0}
        )
        assert set(host.export().values()) == {"ok"}
    finally:
        host.reset_ingress(token)

    exported = _all_bytes(tmp_path)
    for canary in canaries | {"CANARY_EXTERNAL_CORRELATION_readable"}:
        assert canary.encode() not in exported
        assert canary not in "\n".join(record.getMessage() for record in caplog.records)
    assert (tmp_path / SDK_EVENTS_FILENAME).stat().st_size <= 1_048_576
    ring = json.loads((tmp_path / SDK_RING_FILENAME).read_text())
    assert len(ring["events"]) <= ring["capacity"] == 256
    snapshot = json.loads((tmp_path / SDK_SNAPSHOT_FILENAME).read_text())
    assert snapshot["sources"]["memory"]["health"] == "ok"


def test_snapshot_fault_and_export_path_fault_degrade_without_raising(tmp_path):
    host = HostSdkObservability(tmp_path)
    host.register_snapshot_source("memory", lambda: (_ for _ in ()).throw(RuntimeError("secret")))
    assert host.export()[SDK_SNAPSHOT_FILENAME] == "ok"
    snapshot = json.loads((tmp_path / SDK_SNAPSHOT_FILENAME).read_text())
    assert snapshot["health"] == "degraded"
    assert snapshot["sources"]["memory"]["error_code"] == "snapshot_unavailable"

    (tmp_path / SDK_RING_FILENAME).unlink()
    (tmp_path / SDK_RING_FILENAME).mkdir()
    assert host.export()[SDK_RING_FILENAME] == "degraded:unsafe_export_path"


def test_missing_sdk_is_a_bounded_degraded_noop(tmp_path, monkeypatch):
    import observability.sdk as module

    real_import = __import__

    def denied(name, *args, **kwargs):
        if name == "simple_harness.observability":
            raise ImportError("candidate wheel not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", denied)
    host = module.HostSdkObservability(tmp_path)
    assert not host.available
    assert host.export()[SDK_RING_FILENAME] == "ok"
    snapshot = json.loads((tmp_path / SDK_SNAPSHOT_FILENAME).read_text())
    assert snapshot["health"] == "degraded"
    assert snapshot["degraded_codes"] == ["sdk_observability_unavailable"]


def test_correlation_policy_does_not_accept_or_return_principal():
    policy = HostCorrelationPolicy()
    token = policy.bind_ingress(
        run_id="authority-run", session_id="authority-session", request_id="readable-request"
    )
    try:
        first = policy("entity-a", "authority-session")
        second = policy("entity-b", "different-session")
    finally:
        policy.reset(token)
    assert first.trace_id != "readable-request"
    assert second.trace_id != first.trace_id
    assert not hasattr(first, "principal")


def test_main_wires_one_host_sink_into_memory_and_harness() -> None:
    main_source = (Path(__file__).parents[1] / "main.py").read_text()
    assert "observability_sink=_sdk_observability.sink" in main_source
    assert "correlation=_sdk_observability.correlation" in main_source
    assert 'runtime_config_kwargs["observability_sink"] = _sdk_observability.sink' in main_source
    assert "_sdk_observability.bind_ingress(" in main_source
    assert "_sdk_observability.reset_ingress(observability_token)" in main_source


@pytest.mark.asyncio
async def test_installed_memory_async_snapshot_reaches_export_without_coroutine_leak(tmp_path):
    import warnings
    from simple_harness_memory import MemoryManager

    host = HostSdkObservability(tmp_path / "logs")
    manager = await MemoryManager.build_development(tmp_path / "memory.db", embedder="hash")
    try:
        host.register_snapshot_source("memory", manager.diagnostics_snapshot)
        host.register_snapshot_source("sync", lambda: {"health": "ok"})
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", RuntimeWarning)
            host.export()  # Compatibility path explicitly reports async-required.
            before = json.loads((host.log_dir / SDK_SNAPSHOT_FILENAME).read_text())
            assert before["sources"]["memory"]["error_code"] == "snapshot_requires_async_export"
            assert set((await host.export_async()).values()) == {"ok"}
        assert not [item for item in caught if "never awaited" in str(item.message)]
        actual = json.loads((host.log_dir / SDK_SNAPSHOT_FILENAME).read_text())
        expected = await manager.diagnostics_snapshot()
        assert actual["sources"]["memory"] == expected
        assert actual["sources"]["memory"]["lifecycle"] == "open"
        assert "storage" in actual["sources"]["memory"]
        assert actual["sources"]["sync"] == {"health": "ok"}
        assert actual["degraded_codes"] == []
    finally:
        await manager.close()
        host.sink.close()


@pytest.mark.asyncio
async def test_async_snapshot_timeout_and_cancellation_leave_no_pending_collector(tmp_path):
    import asyncio

    host = HostSdkObservability(tmp_path)
    started, stopped = asyncio.Event(), asyncio.Event()
    async def blocked():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    host.register_snapshot_source("blocked", blocked)
    host.register_snapshot_source("sync", lambda: {"health": "ok"})
    try:
        await host.export_async()
        assert started.is_set() and stopped.is_set()
        result = json.loads((tmp_path / SDK_SNAPSHOT_FILENAME).read_text())
        assert result["sources"]["blocked"] == {"health": "degraded", "error_code": "snapshot_unavailable"}
        assert result["sources"]["sync"] == {"health": "ok"}
        started.clear()
        stopped.clear()
        pending = asyncio.create_task(host.export_async())
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert stopped.is_set()
    finally:
        host.sink.close()
