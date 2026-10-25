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
