# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""启动／重建失败的协议（原桌面端 ``test_startup_failure_recovery``）：失败的候选不发布、关掉、
可以重试；驱动循环重试完整进入而不跑失败的候选；首次启动失败释放锁、不复用关掉的客户端。
没有模型调用，没有子进程。"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_orchestrator.api import facade, policies
from agent_orchestrator.artifacts.workspace import WorkspaceCleanupIncomplete
from agent_orchestrator.host import HostRequestError, HostSettings, OrchestrationHost, Principal
from agent_orchestrator.host import host as host_module
from agent_orchestrator.host.instance_lock import InstanceLock


def _host(tmp_path, **kwargs) -> OrchestrationHost:
    host = OrchestrationHost(
        tmp_path,
        principal=Principal("local-user:test", "本机用户"),
        settings=HostSettings(backoff_max_seconds=0.001),
        provider=object(),
        counter=object(),
        require_acceptance=False,
        auto_mode=False,
        **kwargs,
    )
    host.config = object()
    host.policy = SimpleNamespace(to_json=dict)
    # 这些用例钉的是候选的重试/关闭协议；执行池的拼装用桩代替
    host._assemble_runtime = lambda: None  # type: ignore[method-assign]
    return host


def _candidate():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE events(seq INTEGER, type TEXT, payload_json TEXT, created_at REAL)")
    bound: dict = {}
    return SimpleNamespace(
        __aenter__=AsyncMock(),
        __aexit__=AsyncMock(),
        run=AsyncMock(),
        commit=object(),
        store=SimpleNamespace(connection=db),
        bound=bound,
        set_between_cycles=lambda duty, *, every_seconds: bound.update(
            duty=duty, every=every_seconds
        ),
        recovery_status=lambda: {"state": "NATIVE"},
        durable_watermark=lambda: ("w",),
    )


class _Control(tuple):
    def assurance_root_diagnostic(self):
        return {
            "state": "NATIVE",
            "execution_allowed": True,
            "current_authentication_required": False,
        }


def _wire_candidates(monkeypatch, candidates):
    pending = iter(candidates)
    monkeypatch.setattr(
        host_module.event_handler, "Orchestrator", lambda *args, **kwargs: next(pending)
    )
    monkeypatch.setattr(
        facade, "MissionControlV1", lambda candidate, **kwargs: _Control(("control", candidate))
    )
    monkeypatch.setattr(policies, "PolicyApi", lambda commit, *args, **kwargs: ("policy", commit))


@pytest.mark.parametrize("cleanup_raises", [False, True])
def test_rebuild_failure_is_unpublished_closed_and_can_retry(tmp_path, monkeypatch, cleanup_raises):
    async def case():
        host = _host(tmp_path)
        old, failed, healthy = _candidate(), _candidate(), _candidate()
        host.orchestrator = old
        host.state = "available"
        host.control = object()
        host.policy_api = object()
        error = WorkspaceCleanupIncomplete([{"status": "residual"}])

        async def fail_enter():
            assert host.orchestrator is None
            assert host.control is None and host.policy_api is None
            raise error

        failed.__aenter__.side_effect = fail_enter
        if cleanup_raises:
            failed.__aexit__.side_effect = RuntimeError("secondary close failure")

        async def healthy_enter():
            assert host.orchestrator is None  # not published while entering

        healthy.__aenter__.side_effect = healthy_enter
        _wire_candidates(monkeypatch, [failed, healthy])
        rebuilt = []
        host.on_rebuilt = lambda: rebuilt.append(host.orchestrator)
        try:
            with pytest.raises(WorkspaceCleanupIncomplete) as raised:
                await asyncio.wait_for(host._rebuild(), 5)
            assert raised.value is error
            assert host.orchestrator is None
            assert host.control is None and host.policy_api is None
            with pytest.raises(HostRequestError):
                host._require()
            old.__aexit__.assert_awaited_once()
            failed.__aexit__.assert_awaited_once()
            failed.run.assert_not_awaited()
            await asyncio.wait_for(host._rebuild(), 5)
            assert host.orchestrator is healthy
            assert host.control == ("control", healthy)
            assert host.policy_api == ("policy", healthy.commit)
            assert rebuilt == [healthy]
            # 宿主职责绑在循环的两轮之间（一个任务的长回合不再压住别的任务的规划授权）
            assert healthy.bound == {
                "duty": host._host_duties,
                "every": host.settings.tick_active_seconds,
            }
        finally:
            await asyncio.wait_for(host.close(), 5)
            await asyncio.wait_for(host.close(), 5)
        healthy.__aexit__.assert_awaited_once()
        failed.__aexit__.assert_awaited_once()

    asyncio.run(case())


def test_driver_retries_full_enter_without_running_failed_candidate(tmp_path, monkeypatch):
    async def case():
        host = _host(tmp_path)
        host.state = "degraded"
        failed, healthy = _candidate(), _candidate()
        failed.__aenter__.side_effect = WorkspaceCleanupIncomplete([{"status": "unknown"}])

        async def run_healthy():
            healthy.__aenter__.assert_awaited_once()
            host._closing = True
            host._wake.set()

        healthy.run.side_effect = run_healthy
        _wire_candidates(monkeypatch, [failed, healthy])
        try:
            await asyncio.wait_for(host.run_forever(), 5)
            failed.run.assert_not_awaited()
            failed.__aexit__.assert_awaited_once()
            healthy.run.assert_awaited_once()
            assert host.state == "available"
        finally:
            await asyncio.wait_for(host.close(), 5)

    asyncio.run(case())


def test_initial_failure_releases_lock_and_never_reopens_a_closed_client(tmp_path, monkeypatch):
    async def case():
        client = SimpleNamespace(aclose=AsyncMock())
        host = _host(tmp_path, http_client=client)
        host._own_client = True  # 让宿主负责关它（给了 http_client= 时默认是调用方关）
        failed = _candidate()
        error = WorkspaceCleanupIncomplete([{"status": "unknown"}])

        async def fail_open():
            host.orchestrator = failed
            raise error

        open_failed = AsyncMock(side_effect=fail_open)
        monkeypatch.setattr(host, "_open", open_failed)
        await asyncio.wait_for(host.start(), 5)
        assert host.state == "unavailable" and "WorkspaceCleanupIncomplete" in str(host.reason)
        assert host._driver is None and host.orchestrator is None
        failed.__aexit__.assert_awaited_once()
        client.aclose.assert_awaited_once()
        probe = InstanceLock(tmp_path)
        assert probe.acquire()
        probe.release()
        await asyncio.wait_for(host.start(), 5)
        open_failed.assert_awaited_once()  # never reopen with the closed provider client
        await asyncio.wait_for(host.close(), 5)
        await asyncio.wait_for(host.close(), 5)
        client.aclose.assert_awaited_once()

        # a fresh host with a fresh client starts normally
        fresh_client = SimpleNamespace(aclose=AsyncMock())
        fresh = _host(tmp_path, http_client=fresh_client)
        healthy = _candidate()

        async def open_fresh():
            fresh_client.aclose.assert_not_awaited()
            fresh.orchestrator = healthy
            fresh.control = _Control(("control", healthy))

        monkeypatch.setattr(fresh, "_open", AsyncMock(side_effect=open_fresh))
        try:
            await asyncio.wait_for(fresh.start(), 5)
            assert fresh.state == "available"
        finally:
            await asyncio.wait_for(fresh.close(), 5)

    asyncio.run(case())
