# SPDX-License-Identifier: Apache-2.0
"""取消以持久屏障为准，不把交接成功写成外部效果撤回。"""
import asyncio

import pytest
from test_session import SPEC, FakeHost
from test_session_async import FakeChat

from agent_orchestrator.host import HostSettings, Session, SessionError


def make(tmp_path):
    host = FakeHost(tmp_path)
    host.settings = HostSettings(continuation_merge_window_seconds=0)
    parent = Session(host, session_id="main")
    parent._chat = FakeChat(parent)
    return host, parent


def test_cancel_tree_is_idempotent_persistent_and_cannot_cross_tree(tmp_path):
    async def case():
        host, parent = make(tmp_path)
        await parent.create_child("a", command_id="child")
        task = parent._delegations.delegate("t", "c", {**SPEC, "child_key": "a"})
        receipt = parent.cancel("session_tree", "main", command_id="stop", reason="结束")
        assert receipt["status"] == "cancel_requested"
        assert parent.cancel("session_tree", "main", command_id="stop", reason="结束") == receipt
        with pytest.raises(SessionError, match="idempotency_conflict"):
            parent.cancel("session_tree", "main", command_id="stop", reason="不同")
        with pytest.raises(SessionError, match="not_found_or_forbidden"):
            parent.cancel("session_tree", "other", command_id="other")
        with pytest.raises(SessionError, match="cancelled"):
            parent._delegations.delegate("new", "call", SPEC)
        assert len(host.made) == 1 and host.cancelled == [task["mission_id"]]
        reopened = Session(host, session_id="main")
        assert reopened._is_cancelled()
        kinds = [e["kind"] for e in parent._store.events(0)]
        assert "cancel_requested" in kinds and "cancelled" in kinds
        await parent.close(force=True)
    asyncio.run(case())


def test_barrier_is_checked_after_send_lock_before_queue_claim(tmp_path):
    async def case():
        _, parent = make(tmp_path)
        await parent.create_child("a", command_id="child")
        child = parent.child("a")
        child._chat = FakeChat(child)
        await child._send_lock.acquire()
        parent.child_send("a", "追加", command_id="directive")
        child._wake_inputs()
        await asyncio.sleep(0)
        parent.cancel("session_tree", "main", command_id="stop")
        child._send_lock.release()
        await asyncio.sleep(0.02)
        assert child.queued_inputs()[0]["state"] == "cancelled"
        assert child._chat.calls == 0
        assert not child._store.rows("continuation_usage")
        await parent.close(force=True)
    asyncio.run(case())


def test_late_goal_result_is_auditable_and_does_not_start_continuation(tmp_path):
    async def case():
        host, parent = make(tmp_path)
        parent.put_goal({"goal_id": "g", "objective": "整理", "completion_criteria": ["交付"],
                         "budget": {"turns": 10}}, command_id="goal", expected_version=0)
        await parent.create_child("a", command_id="child")
        task = parent._delegations.delegate("t", "c", {**SPEC, "child_key": "a"})
        parent.cancel("goal", "g", command_id="stop", expected_version=1)
        host.finish(task["mission_id"])
        parent._inbox.catch_up()
        await parent._continuation.tick()
        assert not parent.queued_inputs() and parent._chat.calls == 0
        events = [e for e in parent._store.events(0) if e["kind"] == "result_received"]
        assert events and events[-1]["after_cancel"] is True
        assert parent.inbox()[0]["goal_ref"] == {"goal_id": "g", "goal_version": 1}
        assert parent.children()[0]["goal_ref"] == {"goal_id": "g", "goal_version": 1}
        await parent.close(force=True)
    asyncio.run(case())


def test_interrupt_only_stops_current_reply(tmp_path):
    async def case():
        host, parent = make(tmp_path)
        task = parent._delegations.delegate("t", "c", SPEC)
        await parent.interrupt()
        assert host.cancelled == []
        assert parent.task(task["delegation_id"])["status"] == "ACTIVE"
        await parent.close(force=True)
    asyncio.run(case())


def test_cancel_before_mission_creation_cannot_recover_into_new_effect(tmp_path):
    _, parent = make(tmp_path)
    class Crash(BaseException):
        pass
    def stop(point):
        if point == "delegation_recorded":
            raise Crash()
    parent._checkpoint = stop
    with pytest.raises(Crash):
        parent._delegations.delegate("t", "c", SPEC)
    parent.cancel("session_tree", "main", command_id="stop")
    parent._delegations.recover()
    assert not parent._host.made
    assert parent._store.rows("delegations")[0]["terminal_status"] == "CANCELLED"


def test_cancel_command_is_the_only_added_schema_and_runtime_identity(tmp_path):
    from agent_orchestrator.host.session_chat import COMMAND_SCHEMAS
    assert COMMAND_SCHEMAS["cancel"]["properties"]["target_kind"]["enum"] == [
        "goal", "delegation", "session_tree"]
    assert "principal_id" not in COMMAND_SCHEMAS["cancel"]["properties"]
