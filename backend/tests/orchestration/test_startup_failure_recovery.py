"""Controlled startup/rebuild failures; no provider calls or OS child processes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_orchestrator.artifacts.workspace import WorkspaceCleanupIncomplete
from agent_orchestrator.orchestrator import event_handler
from deskpet.orchestration import service as service_module
from deskpet.orchestration.lock import InstanceLock
from deskpet.orchestration.service import (
    OrchestrationRequestError, OrchestrationService, OrchestrationSettings,
)


def _service(tmp_path, principal, **kwargs):
    service = OrchestrationService(
        tmp_path, OrchestrationSettings(backoff_max_seconds=0.001),
        principal=principal, **kwargs,
    )
    service._config = object()
    service._deployment = SimpleNamespace(to_json=lambda: {})
    return service


def _candidate():
    return SimpleNamespace(
        __aenter__=AsyncMock(), __aexit__=AsyncMock(), run=AsyncMock(), commit=object()
    )


def _wire_candidates(monkeypatch, candidates):
    from agent_orchestrator.api import facade, policies

    pending = iter(candidates)
    monkeypatch.setattr(event_handler, "Orchestrator", lambda *args, **kwargs: next(pending))
    monkeypatch.setattr(service_module, "source_runtime_options", lambda *args: {})
    monkeypatch.setattr(facade, "MissionControlV1", lambda candidate, **kwargs: ("control", candidate))
    monkeypatch.setattr(policies, "PolicyApi", lambda commit, *args, **kwargs: ("policy", commit))


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_raises", [False, True])
async def test_rebuild_failure_is_unpublished_closed_and_can_retry(
    tmp_path, principal, monkeypatch, cleanup_raises
):
    client = SimpleNamespace(aclose=AsyncMock())
    service = _service(tmp_path, principal, http_client=client)
    old, failed, healthy = _candidate(), _candidate(), _candidate()
    service._orchestrator = old
    service._state = "available"
    service._control = object()
    service._policy = object()
    error = WorkspaceCleanupIncomplete([{"status": "residual"}])

    async def fail_enter():
        assert service._orchestrator is None
        assert service._control is None and service._policy is None
        raise error

    failed.__aenter__.side_effect = fail_enter
    if cleanup_raises:
        failed.__aexit__.side_effect = RuntimeError("secondary close failure")

    async def healthy_enter():
        assert service._orchestrator is None  # not published while entering

    healthy.__aenter__.side_effect = healthy_enter
    _wire_candidates(monkeypatch, [failed, healthy])
    try:
        with pytest.raises(WorkspaceCleanupIncomplete) as raised:
            await asyncio.wait_for(service._rebuild(), 5)
        assert raised.value is error
        assert service._orchestrator is None
        assert service._control is None and service._policy is None
        with pytest.raises(OrchestrationRequestError):
            service._require()
        old.__aexit__.assert_awaited_once()
        failed.__aexit__.assert_awaited_once()
        failed.run.assert_not_awaited()
        client.aclose.assert_not_awaited()  # this client remains usable for rebuild
        await asyncio.wait_for(service._rebuild(), 5)
        assert service._orchestrator is healthy
        assert service._control == ("control", healthy)
        assert service._policy == ("policy", healthy.commit)
    finally:
        await asyncio.wait_for(service.close(), 5)
        await asyncio.wait_for(service.close(), 5)
    healthy.__aexit__.assert_awaited_once()
    failed.__aexit__.assert_awaited_once()
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_driver_retries_full_enter_without_running_failed_candidate(
    tmp_path, principal, monkeypatch
):
    service = _service(tmp_path, principal)
    service._state = "degraded"
    failed, healthy = _candidate(), _candidate()
    failed.__aenter__.side_effect = WorkspaceCleanupIncomplete([{"status": "unknown"}])

    async def run_healthy():
        healthy.__aenter__.assert_awaited_once()
        service._closing = True
        service._wake.set()

    healthy.run.side_effect = run_healthy
    _wire_candidates(monkeypatch, [failed, healthy])
    try:
        await asyncio.wait_for(service._drive(), 5)
        failed.run.assert_not_awaited()
        failed.__aexit__.assert_awaited_once()
        healthy.run.assert_awaited_once()
        assert service._state == "available"
    finally:
        await asyncio.wait_for(service.close(), 5)


@pytest.mark.asyncio
async def test_initial_failure_releases_lock_and_requires_fresh_service_client(
    tmp_path, principal, monkeypatch
):
    client = SimpleNamespace(aclose=AsyncMock())
    service = _service(tmp_path, principal, http_client=client)
    failed = _candidate()
    error = WorkspaceCleanupIncomplete([{"status": "unknown"}])

    async def fail_open():
        service._orchestrator = failed
        raise error

    open_failed = AsyncMock(side_effect=fail_open)
    monkeypatch.setattr(service, "_open", open_failed)
    monkeypatch.setattr(service_module, "distributions", lambda: {"consistent": True})
    await asyncio.wait_for(service.start(), 5)
    assert service._state == "unavailable" and "WorkspaceCleanupIncomplete" in service._reason
    assert service._driver is None and service._orchestrator is None
    failed.__aexit__.assert_awaited_once()
    client.aclose.assert_awaited_once()
    probe = InstanceLock(tmp_path)
    assert probe.acquire()
    probe.release()
    await asyncio.wait_for(service.start(), 5)
    open_failed.assert_awaited_once()  # never reopen with the closed provider client
    await asyncio.wait_for(service.close(), 5)
    await asyncio.wait_for(service.close(), 5)
    client.aclose.assert_awaited_once()

    # The existing deactivate/activate boundary constructs a new service/client.
    fresh_client = SimpleNamespace(aclose=AsyncMock())
    fresh = _service(tmp_path, principal, http_client=fresh_client, drive=False)
    healthy = _candidate()

    async def open_fresh():
        assert fresh._http_client is fresh_client
        fresh_client.aclose.assert_not_awaited()
        fresh._orchestrator = healthy

    monkeypatch.setattr(fresh, "_open", AsyncMock(side_effect=open_fresh))
    monkeypatch.setattr(service_module, "build_manifest", lambda **kwargs: {})
    monkeypatch.setattr(service_module, "write_manifest", lambda *args: None)
    try:
        await asyncio.wait_for(fresh.start(), 5)
        assert fresh._state == "available"
    finally:
        await asyncio.wait_for(fresh.close(), 5)
    fresh_client.aclose.assert_awaited_once()
