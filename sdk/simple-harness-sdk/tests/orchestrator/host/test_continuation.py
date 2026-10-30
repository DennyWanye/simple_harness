# SPDX-License-Identifier: Apache-2.0
"""结果是资料；稳定续办输入、策略、预算和通话所有权。"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from test_session import SPEC, Crash, FakeHost
from test_session_async import eventually
from test_session_replay import ReplayChat

from agent_orchestrator.host import HostSettings, Session


async def parent_with_result(root, *, settings=None, policy=None):
    host = FakeHost(root)
    host.settings = settings or HostSettings(continuation_merge_window_seconds=0)
    parent = Session(host, session_id="main", continuation_policy=policy)
    parent._chat = ReplayChat(parent)
    await parent.create_child("a", command_id="create")
    mission = parent._delegations.delegate("original", "call", {**SPEC, "child_key": "a"})
    host.finish(mission["mission_id"])
    parent._inbox.catch_up()
    return host, parent


async def tick(session):
    await session._continuation.tick()
    await session.recover()


def test_default_merge_freezes_event_set_applies_once_and_ack_is_separate(tmp_path):
    async def case():
        settings = HostSettings(continuation_merge_window_seconds=0.08)
        host, parent = await parent_with_result(tmp_path, settings=settings)
        await tick(parent)
        assert not parent.queued_inputs()
        await parent.create_child("b", command_id="b")
        second = parent._delegations.delegate("original", "call-2", {**SPEC, "child_key": "b"})
        host.finish(second["mission_id"])
        parent._inbox.catch_up()
        await tick(parent)
        await asyncio.sleep(0.09)
        await tick(parent)
        await eventually(lambda: len(parent.history()) == 1)
        [row] = parent.queued_inputs()
        assert row["kind"] == "result_event" and row["source"] == "continuation"
        assert set(row["payload"]) == {n["event_id"] for n in parent.inbox()}
        assert all(n["applied_turn_id"] == row["turn_id"] and not n["acked"] for n in parent.inbox())
        for _ in range(3):
            parent._inbox.catch_up()
            await tick(parent)
        assert len(parent.queued_inputs()) == 1 and parent._chat.calls == 1
        assert parent.ack(parent.inbox()[0]["event_id"])["acked"]
        kinds = {e["kind"] for e in parent._store.events(0)}
        assert {"result_received", "continuation_queued", "result_applied", "continuation_completed"} <= kinds
        await parent.close()

    asyncio.run(case())


@pytest.mark.parametrize("decision", ["hold", "deny", "error", "timeout"])
def test_policy_context_and_fail_closed_decisions(tmp_path, decision):
    from agent_orchestrator.host import ContinuationDecision

    contexts = []

    class Policy:
        async def decide(self, context):
            contexts.append(context)
            if decision == "error":
                raise RuntimeError("private failure")
            if decision == "timeout":
                raise TimeoutError()
            return getattr(ContinuationDecision, decision)("host_reason")

    async def case():
        _, parent = await parent_with_result(tmp_path, policy=Policy())
        await tick(parent)
        await tick(parent)
        assert parent._chat.calls == 0 and not parent.queued_inputs()
        [context] = contexts
        assert context.session_id == "main" and context.child_key == "a"
        assert context.event_id == parent.inbox()[0]["event_id"]
        assert context.mission_id and context.delegation_id and context.pending_event_ids
        kind = "continuation_denied" if decision == "deny" else "continuation_held"
        [event] = [e for e in parent._store.events(0) if e["kind"] == kind]
        assert event["reason"] == ("host_reason" if decision in {"deny", "hold"} else "policy_unavailable")
        assert parent.inbox()[0]["applied_turn_id"] is None
        await parent.close()

    asyncio.run(case())


@pytest.mark.parametrize("point", ["inbox_written", "continuation_queued", "continuation_applied"])
def test_reopen_at_three_durable_boundaries_never_drops_or_duplicates(tmp_path, point):
    async def case():
        host, parent = await parent_with_result(tmp_path)
        fired = []

        def crash(name):
            if name == point and not fired:
                fired.append(name)
                raise Crash(name)

        parent._checkpoint = crash
        if point == "inbox_written":
            # 重新镜像，从该持久边界真实注入。
            with parent._store.transaction():
                parent._store.connection.execute("DELETE FROM records WHERE namespace='inbox'")
            with pytest.raises(Crash):
                parent._inbox.catch_up()
        else:
            try:
                await tick(parent)
                await eventually(lambda: bool(fired))
            except Crash:
                pass
        assert fired
        if parent._input_task is not None:
            await asyncio.gather(parent._input_task, return_exceptions=True)
        before = parent._chat.calls
        await parent.close()
        reopened = Session(host, session_id="main")
        reopened._chat = ReplayChat(reopened)
        await reopened.recover()
        reopened._inbox.catch_up()
        await tick(reopened)
        await eventually(lambda: bool(reopened.inbox()[0]["applied_turn_id"]))
        assert len(reopened.queued_inputs()) == 1
        assert before + reopened._chat.calls == 1
        assert len(reopened.history()) == 1
        await reopened.close()

    asyncio.run(case())


def test_budget_persists_and_idle_never_invokes_model(tmp_path):
    async def case():
        settings = HostSettings(continuation_merge_window_seconds=0, continuation_max_turns_per_session=1)
        host, parent = await parent_with_result(tmp_path, settings=settings)
        await tick(parent)
        await eventually(lambda: len(parent.history()) == 1)
        await parent.close()
        parent = Session(host, session_id="main")
        parent._chat = ReplayChat(parent)
        await parent.recover()
        second = parent._delegations.delegate("second", "call", {**SPEC, "child_key": "a"})
        host.finish(second["mission_id"])
        parent._inbox.catch_up()
        for _ in range(4):
            await tick(parent)
        assert parent._chat.calls == 0 and len(parent.history()) == 1
        assert any(e["kind"] == "continuation_budget_exhausted" for e in parent._store.events(0))
        assert len(parent._store.rows("continuation_usage")) == 1
        assert parent.inbox()[-1]["applied_turn_id"] is None
        await parent.close()

    asyncio.run(case())


def test_settings_and_capabilities(tmp_path):
    settings = HostSettings()
    assert settings.continuation_enabled is True
    assert settings.continuation_merge_window_seconds == 5
    assert settings.continuation_max_turns_per_session == 50
    assert settings.continuation_max_model_calls_per_turn is None
    assert settings.continuation_voice_max_wait_seconds == 60
    for changes in ({"continuation_merge_window_seconds": -1}, {"continuation_max_turns_per_session": -1}, {"continuation_max_model_calls_per_turn": 0}, {"continuation_voice_max_wait_seconds": float("nan")}):
        with pytest.raises(ValueError):
            replace(settings, **changes)
    caps = Session(FakeHost(tmp_path)).capabilities()
    assert {"async_accept", "queued_input_execution", "continuation"} <= set(caps["capabilities"])
    assert set(caps["unsupported"]) == {"goals", "authorization_port", "steer_running_mission"}
