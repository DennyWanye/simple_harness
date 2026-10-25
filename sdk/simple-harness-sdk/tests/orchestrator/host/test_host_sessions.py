# SPDX-License-Identifier: Apache-2.0
"""Borrowed sessions share one running Host without taking its root lock again."""

from __future__ import annotations

import asyncio
import importlib

import pytest
from test_chat import _chat_script
from test_session import SPEC

from agent_orchestrator.host import OrchestrationHost, Principal, SessionPolicy
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider
from agent_orchestrator.host.session_ledger import SessionError


async def running_host(root, replies):
    return await OrchestrationHost.open(
        root,
        principal=Principal("host-user", "User"),
        provider=ScriptedProvider(chat=_chat_script(replies)),
        counter=FixtureWordCounter(),
        require_acceptance=False,
    )


def test_open_session_reuses_root_runtime_and_closing_it_keeps_host_usable(tmp_path):
    async def run():
        host = await running_host(tmp_path, ["Session answer", "Host answer", "Restored answer"])
        try:
            runtime, store = host.orchestrator, host.store
            session = await host.open_session("desktop")
            driver = host._driver
            first, second = await asyncio.gather(
                host.open_session("desktop"), host.open_session("desktop")
            )
            assert first is second is session
            assert session._host is host and host.orchestrator is runtime and host.store is store
            assert host._driver is driver
            assert (await session.send("Hello", key="one"))["text"] == "Session answer"
            await session.close()
            assert host.state == "available" and host.store is store
            assert (
                await host.chat(
                    {"session_id": "legacy", "text": "Hello", "idempotency_key": "legacy-1"}
                )
            )["text"] == "Host answer"
            restored = await host.open_session("desktop")
            assert restored is not session and restored.history()[-1]["input"] == "Hello"
            assert (await restored.send("Again", key="two"))["text"] == "Restored answer"
        finally:
            await host.close()

    asyncio.run(run())


def test_two_host_sessions_isolate_history_tasks_and_scope(tmp_path):
    async def run():
        host = await running_host(tmp_path, ["Alpha answer", "Beta answer"])
        try:
            alpha, beta = await host.open_session("alpha"), await host.open_session("beta")
            await alpha.send("Alpha input", key="same-key")
            await beta.send("Beta input", key="same-key")
            assert [r["input"] for r in alpha.history()] == ["Alpha input"]
            assert [r["input"] for r in beta.history()] == ["Beta input"]
            for session in (alpha, beta):
                run_id = next(iter(session._chat._runs))
                assert host._resolve_output_scope(run_id)["session_id"] == session.session_id
            turn = alpha._commit_input("Write a report", "task")
            receipt = alpha._delegations.delegate(turn["turn_id"], "delegate", SPEC)
            assert len(alpha.tasks()) == 1 and beta.tasks() == []
            with pytest.raises(Exception, match="permission_denied"):
                beta.cancel_task(receipt["mission_id"])
            host.cancel(receipt["mission_id"])
            alpha._inbox.catch_up()
            assert all(e["session_id"] == "alpha" for e in alpha._store.events(0))
            assert all(e["session_id"] == "beta" for e in beta._store.events(0))
            assert not any(e["kind"] == "delegated" for e in beta._store.events(0))
        finally:
            await host.close()

    asyncio.run(run())


def test_host_session_principal_policy_and_host_close_own_lifecycle(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from test_realtime_bridge import Audio, Client

    from agent_orchestrator.host import VoiceProfile
    from agent_orchestrator.host.realtime_bridge import VoiceBinding

    monkeypatch.setattr(VoiceBinding, "_request", lambda self: SimpleNamespace(tools=[]))

    async def run():
        host = await running_host(tmp_path, [])
        try:
            with pytest.raises(Exception, match="permission_denied"):
                await host.open_session("desktop", principal=Principal("other", "Other"))
            session = await host.open_session("desktop", principal=host.principal)
            with pytest.raises(Exception, match="session_config_frozen"):
                await host.open_session("desktop", policy=SessionPolicy(max_model_calls=2))
            audio, client = Audio(), Client()
            await session.attach_voice(audio, VoiceProfile(client_factory=client))
            await host.close()
            assert session._closed and session._voice is None
            assert client.wires[0].closed and audio.written[-1] is None
            assert host.state == "closed"
            with pytest.raises(Exception):
                await host.open_session("closed")
        finally:
            if host.state != "closed":
                await host.close()

    asyncio.run(run())


def test_borrowed_session_factory_is_declared_public():
    from agent_orchestrator.public_api import PUBLIC_MODULES

    assert "agent_orchestrator.host.sessions" in PUBLIC_MODULES
    module = importlib.import_module("agent_orchestrator.host.sessions")
    assert "open_session" in module.__all__ and callable(module.open_session)


def test_opening_another_session_does_not_settle_a_live_chat_reservation(tmp_path):
    async def run():
        host = await running_host(tmp_path, ["Held answer"])
        provider = host._given_provider
        provider.held.add("chat")
        job = None
        try:
            alpha = await host.open_session("alpha")
            job = asyncio.create_task(alpha.send("Hello", key="held"))
            await asyncio.wait_for(provider.entered.wait(), 10)
            before = host.global_budget_status()
            assert before["reserved_tokens"] > 0
            await host.open_session("beta")
            after = host.global_budget_status()
            assert after["reserved_tokens"] == before["reserved_tokens"]
            assert after["used_tokens"] == before["used_tokens"]
            provider.release.set()
            assert (await job)["state"] == "committed"
        finally:
            provider.release.set()
            if job is not None:
                await job
            await host.close()

    asyncio.run(run())


def test_host_close_releases_all_sessions_when_one_cancel_fails(tmp_path, monkeypatch):
    from agent_orchestrator.host.instance_lock import InstanceLock

    async def run():
        host = await running_host(tmp_path, [])
        alpha = await host.open_session("alpha")
        beta = await host.open_session("beta")

        async def fail(body):
            raise RuntimeError("cancel unavailable")

        monkeypatch.setattr(alpha._chat, "cancel", fail)
        try:
            await host.close()
            assert host.state == "closed" and alpha._closed and beta._closed
            assert not alpha._chat.available and not beta._chat.available
            assert alpha._pump_task.done() and beta._pump_task.done()
            lock = InstanceLock(tmp_path)
            assert lock.acquire()
            lock.release()
        finally:
            await alpha._chat.close()
            await beta.close()
            await host.close()

    asyncio.run(run())


async def lifecycle_host(root, replies, monkeypatch):
    host = await running_host(root, replies)

    async def idle_driver():
        while not host._closing:
            await host._wake.wait()
            host._wake.clear()

    # Keep OS sandbox recovery outside these lifecycle interleavings.
    monkeypatch.setattr(host, "run_forever", idle_driver)
    return host


def test_review3_reopen_cannot_settle_a_live_predecessor(tmp_path, monkeypatch):
    async def run():
        host = await lifecycle_host(tmp_path, ["Held answer", "New answer"], monkeypatch)
        session = await host.open_session("desktop")
        provider = host._given_provider
        provider.held.add("chat")
        entered, release = asyncio.Event(), asyncio.Event()
        original = session._chat.cancel

        async def delayed_cancel(body):
            entered.set()
            await release.wait()
            return await original(body)

        monkeypatch.setattr(session._chat, "cancel", delayed_cancel)
        sending = asyncio.create_task(session.send("Hello", key="held"))
        closing = None
        try:
            await asyncio.wait_for(provider.entered.wait(), 10)
            before = host.global_budget_status()
            assert before["reserved_tokens"] > 0
            closing = asyncio.create_task(session.close())
            await asyncio.wait_for(entered.wait(), 10)
            with pytest.raises(SessionError, match="session_closing"):
                await host.open_session("desktop")
            assert not closing.done() and not sending.done() and session._chat.available
            assert host.global_budget_status() == before
            release.set()
            provider.release.set()
            await asyncio.wait_for(asyncio.gather(sending, closing), 10)
            assert not session._chat.available
            replacement = await host.open_session("desktop")
            assert replacement is not session
            assert (await replacement.send("Again", key="new"))["state"] == "committed"
        finally:
            release.set()
            provider.release.set()
            await asyncio.gather(sending, *([closing] if closing else []), return_exceptions=True)
            await host.close()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["voice", "cancel", "chat_close"])
def test_close_waiters_survive_caller_cancellation_until_resources_release(
    tmp_path, monkeypatch, phase
):
    from types import SimpleNamespace

    from test_realtime_bridge import Audio, Client

    from agent_orchestrator.host import VoiceProfile
    from agent_orchestrator.host.realtime_bridge import VoiceBinding

    monkeypatch.setattr(VoiceBinding, "_request", lambda self: SimpleNamespace(tools=[]))

    async def run():
        host = await lifecycle_host(tmp_path, [], monkeypatch)
        session = await host.open_session("desktop")
        audio, client = Audio(), Client()
        binding = await session.attach_voice(audio, VoiceProfile(client_factory=client))
        owner, method = {
            "voice": (binding, "detach"),
            "cancel": (session._chat, "cancel"),
            "chat_close": (session._chat, "close"),
        }[phase]
        original = getattr(owner, method)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def gated(*args):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return await original(*args)

        monkeypatch.setattr(owner, method, gated)
        first = asyncio.create_task(session.close())
        second = None
        try:
            await asyncio.wait_for(entered.wait(), 10)
            second = asyncio.create_task(session.close())
            await asyncio.wait({second}, timeout=0.05)
            assert not second.done(), "every close waiter must observe actual teardown"
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            with pytest.raises(SessionError, match="session_closing"):
                await host.open_session("desktop")
            assert session._chat.available and calls == 1
            release.set()
            await asyncio.wait_for(second, 10)
            assert calls == 1 and not session._chat.available and session._pump_task.done()
            assert session._voice is None and client.wires[0].closed
            assert audio.written[-1] is None
            assert await host.open_session("desktop") is not session
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
            await session.close()
            await host.close()

    asyncio.run(run())


def test_close_waits_for_inflight_send_even_if_cancel_returns_early(tmp_path, monkeypatch):
    async def run():
        host = await lifecycle_host(tmp_path, ["Held answer"], monkeypatch)
        session = await host.open_session("desktop")
        provider = host._given_provider
        provider.held.add("chat")
        cancelled = asyncio.Event()

        async def no_cancel(body):
            cancelled.set()
            return {"cancelled": False}

        monkeypatch.setattr(session._chat, "cancel", no_cancel)
        sending = asyncio.create_task(session.send("Hello", key="held"))
        closing = None
        try:
            await asyncio.wait_for(provider.entered.wait(), 10)
            before = host.global_budget_status()
            closing = asyncio.create_task(session.close())
            await asyncio.wait_for(cancelled.wait(), 10)
            await asyncio.wait({closing}, timeout=0.05)
            assert not closing.done() and session._chat.available and not sending.done()
            assert host.global_budget_status() == before
            with pytest.raises(SessionError, match="session_closing"):
                await host.open_session("desktop")
            provider.release.set()
            await asyncio.wait_for(asyncio.gather(sending, closing), 10)
            assert await host.open_session("desktop") is not session
        finally:
            provider.release.set()
            await asyncio.gather(sending, *([closing] if closing else []), return_exceptions=True)
            await host.close()

    asyncio.run(run())


@pytest.mark.parametrize("method", ["cancel", "close"])
def test_close_failure_preserves_ownership_until_executor_is_released(
    tmp_path, monkeypatch, method
):
    async def run():
        host = await lifecycle_host(tmp_path, [], monkeypatch)
        session = await host.open_session("desktop")
        original = getattr(session._chat, method)

        async def fail(*args):
            raise RuntimeError("teardown unavailable")

        monkeypatch.setattr(session._chat, method, fail)
        try:
            with pytest.raises(RuntimeError, match="teardown unavailable"):
                await session.close()
            if method == "close":
                assert session._chat.available and host._sessions["desktop"] is session
                with pytest.raises(SessionError, match="session_closing"):
                    await host.open_session("desktop")
                monkeypatch.setattr(session._chat, method, original)
                await session.close()
            else:
                assert not session._chat.available and "desktop" not in host._sessions
            replacement = await host.open_session("desktop")
            assert replacement is not session and replacement._chat.available
        finally:
            monkeypatch.setattr(session._chat, method, original)
            await host.close()

    asyncio.run(run())


def test_host_and_session_close_share_teardown_despite_cancelled_host_waiter(
    tmp_path, monkeypatch
):
    from agent_orchestrator.host.instance_lock import InstanceLock

    async def run():
        host = await lifecycle_host(tmp_path, [], monkeypatch)
        session = await host.open_session("desktop")
        entered, release = asyncio.Event(), asyncio.Event()
        original = session._chat.close
        calls = 0

        async def gated():
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            await original()

        monkeypatch.setattr(session._chat, "close", gated)
        closing = asyncio.create_task(session.close())
        first = second = None
        probe = InstanceLock(tmp_path)
        try:
            await asyncio.wait_for(entered.wait(), 10)
            first = asyncio.create_task(host.close())
            await asyncio.wait({first}, timeout=0.05)
            assert not first.done(), "Host must not release a still closing session"
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(host.close())
            await asyncio.wait({second}, timeout=0.05)
            assert not second.done() and not probe.acquire()
            release.set()
            await asyncio.wait_for(asyncio.gather(closing, second), 10)
            assert calls == 1 and host.state == "closed" and not session._chat.available
            assert probe.acquire()
        finally:
            release.set()
            tasks = [task for task in (closing, first, second) if task is not None]
            await asyncio.gather(*tasks, return_exceptions=True)
            probe.release()
            await host.close()

    asyncio.run(run())


def test_host_keeps_root_lock_when_a_session_executor_cannot_close(tmp_path, monkeypatch):
    from agent_orchestrator.host.instance_lock import InstanceLock

    async def run():
        host = await lifecycle_host(tmp_path, [], monkeypatch)
        session = await host.open_session("desktop")
        original = session._chat.close
        probe = InstanceLock(tmp_path)

        async def fail():
            raise RuntimeError("executor still live")

        monkeypatch.setattr(session._chat, "close", fail)
        try:
            with pytest.raises(RuntimeError, match="executor still live"):
                await host.close()
            assert host.state != "closed" and session._chat.available
            assert not probe.acquire()
            monkeypatch.setattr(session._chat, "close", original)
            await host.close()
            assert host.state == "closed" and not session._chat.available
            assert probe.acquire()
        finally:
            probe.release()
            monkeypatch.setattr(session._chat, "close", original)
            await host.close()

    asyncio.run(run())
