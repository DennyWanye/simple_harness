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
        parent.put_goal(
            {
                "goal_id": "g",
                "objective": "整理",
                "completion_criteria": ["交付"],
                "budget": {"turns": 10},
            },
            command_id="goal",
            expected_version=0,
        )
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
        "goal",
        "delegation",
        "session_tree",
    ]
    assert "principal_id" not in COMMAND_SCHEMAS["cancel"]["properties"]


def test_cancel_scopes_do_not_mix_and_late_cancel_keeps_succeeded_goal(tmp_path):
    async def case():
        host, parent = make(tmp_path)
        parent.put_goal(
            {
                "goal_id": "g",
                "objective": "报告",
                "completion_criteria": ["交付"],
                "budget": {"turns": 2},
            },
            command_id="goal",
            expected_version=0,
        )
        parent.finish_goal("g", "succeeded", command_id="done", expected_version=1, basis="核验")
        parent.cancel("goal", "g", command_id="late")
        assert parent._goals.find("g")["state"] == "succeeded"
        await parent.create_child("a", command_id="a")
        await parent.create_child("b", command_id="b")
        a = parent._delegations.delegate("t", "a", {**SPEC, "child_key": "a"})
        b = parent._delegations.delegate("t", "b", {**SPEC, "child_key": "b"})
        parent.cancel("delegation", a["mission_id"], command_id="cancel-a")
        assert host.cancelled == [a["mission_id"]]
        assert parent.task(b["delegation_id"])["status"] == "ACTIVE"
        await parent.close(force=True)

    asyncio.run(case())


def test_submitted_voice_is_marked_handed_off_until_played(tmp_path):
    _, parent = make(tmp_path)
    parent._chat = None
    with parent._store.transaction():
        parent._store.put(
            "queued_inputs",
            "q",
            {
                "input_id": "q",
                "idempotency_key": "q",
                "turn_id": "t",
                "kind": "result_event",
                "state": "running",
                "payload": [],
            },
        )
        parent._store.put("voice_deliveries", "q", {"input_id": "q", "state": "submitted"})
    parent.cancel("session_tree", "main", command_id="stop")
    assert parent._store.get("voice_deliveries", "q")["cancel_requested"]
    assert parent._store.get("cancellations", "session_tree:main")["state"] == "cancel_requested"
    with parent._store.transaction():
        parent._store.put("voice_deliveries", "q", {"input_id": "q", "state": "played"})
    parent._converge_cancellations()
    assert parent._store.get("voice_deliveries", "q")["state"] == "played"


def test_real_model_cancel_command_reaches_scope_barrier(tmp_path):
    from test_host_sessions import running_host

    async def case():
        host = await running_host(
            tmp_path,
            [
                (
                    "cancel",
                    {"target_kind": "session_tree", "target_id": "main", "reason": "用户停止"},
                ),
                "已请求停止",
            ],
        )
        try:
            parent = await host.open_session("main")
            result = await parent.send("停止本树", key="stop")
            assert result["state"] == "committed"
            assert parent._store.get("cancellations", "session_tree:main") is not None
            with pytest.raises(SessionError, match="cancelled"):
                await parent.send("新任务", key="later", wait=False)
        finally:
            await host.close()

    asyncio.run(case())


def test_cancel_child_tree_blocks_only_its_late_result_at_root(tmp_path):
    async def case():
        host, parent = make(tmp_path)
        await parent.create_child("a", command_id="a")
        await parent.create_child("b", command_id="b")
        a = parent._delegations.delegate("t", "a", {**SPEC, "child_key": "a"})
        parent._delegations.delegate("t", "b", {**SPEC, "child_key": "b"})
        parent.cancel("session_tree", parent.child("a").session_id, command_id="stop-a")
        host.finish(a["mission_id"])
        parent._inbox.catch_up()
        await parent._continuation.tick()
        assert not parent.queued_inputs()
        received = [e for e in parent._store.events(0) if e["kind"] == "result_received"]
        assert received[-1]["after_cancel"] is True
        assert parent._chat.calls == 0
        assert len(host.cancelled) == 1
        await parent.close(force=True)

    asyncio.run(case())


def test_old_input_without_goal_does_not_borrow_new_goal_when_routed(tmp_path):
    async def case():
        _, parent = make(tmp_path)
        await parent.create_child("a", command_id="a")
        parent._current_input = {"turn_id": "old", "goal_ref": None}
        parent.put_goal(
            {
                "goal_id": "new",
                "objective": "新报告",
                "completion_criteria": ["交付"],
                "budget": {"turns": 2},
                "grant_ref": "new-grant",
            },
            command_id="goal",
            expected_version=0,
        )
        task = parent._delegations.delegate("old", "a", {**SPEC, "child_key": "a"})
        assert task["goal_ref"] is None
        row = parent.child("a")._store.rows("delegations")[0]
        assert row["authorization"]["goal_ref"] is None
        assert row["authorization"]["grant_ref"] is None
        await parent.close(force=True)

    asyncio.run(case())


def test_archived_child_does_not_break_parent_recovery(tmp_path):
    async def case():
        host, parent = make(tmp_path)
        await parent.create_child("a", command_id="a")
        child = parent.child("a")
        child.archive(command_id="archive")
        await parent.close(force=True)
        reopened = Session(host, session_id="main")
        await reopened.recover()
        assert reopened.children()[0]["archived"] is True
        assert not reopened.children()[0]["open"]
        with pytest.raises(SessionError, match="not_found_or_forbidden"):
            reopened.child("a")
        await reopened.close(force=True)

    asyncio.run(case())


def test_cancel_lost_mission_create_response_reconciles_without_replay(tmp_path):
    from types import SimpleNamespace

    from test_session import Crash

    host, parent = make(tmp_path)
    parent._chat = None

    def crash(point):
        if point == "mission_created":
            raise Crash()

    parent._checkpoint = crash
    with pytest.raises(Crash):
        parent._delegations.delegate("t", "c", SPEC)
    assert parent._store.rows("delegations")[0]["mission_id"] is None
    assert len(host.made) == 1
    host.orchestrator = SimpleNamespace(
        store=SimpleNamespace(
            find_mission=lambda tenant, key: (
                SimpleNamespace(id=host.made[key]["mission_id"]),
                "hash",
            )
        )
    )
    parent.cancel("session_tree", "main", command_id="stop")
    assert host.cancelled == [next(iter(host.made.values()))["mission_id"]]
    assert parent._store.rows("delegations")[0]["cancel_converged"]
    assert len(host.made) == 1


def test_cancel_unreconcilable_mission_handoff_stays_unknown(tmp_path):
    from test_session import Crash

    host, parent = make(tmp_path)
    parent._chat = None

    def crash(point):
        if point == "mission_created":
            raise Crash()

    parent._checkpoint = crash
    with pytest.raises(Crash):
        parent._delegations.delegate("t", "c", SPEC)
    parent.cancel("session_tree", "main", command_id="stop")
    barrier = parent._store.get("cancellations", "session_tree:main")
    assert barrier["effect_state"] == "unknown" and barrier["state"] == "cancel_requested"
    row = parent._store.rows("delegations")[0]
    assert not row.get("cancel_converged")
    report = parent.recovery_report()
    assert row["delegation_id"] in report["unknown_effect_refs"]
    assert report["reasons"][row["delegation_id"]] == "mission_handoff_unknown"
    assert len(host.made) == 1


def test_cancel_goal_cancels_its_pending_authorization_request(tmp_path):
    from agent_orchestrator.host import AuthorizationDecision

    class Port:
        async def decide(self, request):
            return AuthorizationDecision.needs_approval("card")

    async def case():
        _, parent = make(tmp_path)
        parent.put_goal(
            {
                "goal_id": "g",
                "objective": "目标",
                "completion_criteria": ["交付"],
                "budget": {"turns": 2},
            },
            command_id="g",
            expected_version=0,
        )
        parent._authorization.port = Port()
        with pytest.raises(SessionError, match="authorization_required"):
            await parent.create_child("a", command_id="a")
        [request] = parent._store.rows("authorization_requests")
        parent.cancel("goal", "g", command_id="stop")
        assert (
            parent._store.get("authorization_requests", request["request_id"])["state"]
            == "cancelled"
        )
        assert parent._goals.find("g")["state"] == "cancelled"
        with pytest.raises(SessionError, match="cancelled"):
            parent.respond(request["request_id"], "approve", request["version"])
        await parent.close(force=True)

    asyncio.run(case())


def test_unrelated_submitted_voice_does_not_hold_another_cancel_scope(tmp_path):
    async def case():
        _, parent = make(tmp_path)
        await parent.create_child("a", command_id="a")
        await parent.create_child("b", command_id="b")
        parent._delegations.delegate("t", "b", {**SPEC, "child_key": "b"})
        child = parent.child("a")
        with child._store.transaction():
            child._store.put(
                "queued_inputs",
                "q",
                {
                    "input_id": "q",
                    "idempotency_key": "q",
                    "turn_id": "t",
                    "kind": "result_event",
                    "state": "running",
                    "payload": [],
                },
            )
            child._store.put("voice_deliveries", "q", {"input_id": "q", "state": "submitted"})
        parent.cancel("session_tree", child.session_id, command_id="stop-a")
        parent.cancel("session_tree", parent.child("b").session_id, command_id="stop-b")
        assert (
            parent._store.get("cancellations", f"session_tree:{child.session_id}")["state"]
            == "cancel_requested"
        )
        b = parent._store.get("cancellations", f"session_tree:{parent.child('b').session_id}")
        assert b["state"] == "cancelled" and b["target_statuses"] == ["CANCELLED"]
        await parent.close(force=True)

    asyncio.run(case())


@pytest.mark.parametrize("policy_action", ["enqueue", "hold", "deny"])
def test_user_consumption_settles_continuation_responsibility(tmp_path, policy_action):
    from test_continuation import parent_with_result

    from agent_orchestrator.host import ContinuationDecision

    class Policy:
        async def decide(self, context):
            return ContinuationDecision(policy_action)

    async def case():
        _, parent = await parent_with_result(
            tmp_path, settings=HostSettings(continuation_merge_window_seconds=100), policy=Policy()
        )
        await parent._continuation.tick()
        assert parent.continuations()[0]["state"] in {"pending", "hold", "deny"}
        receipt = await parent.send("读结果", key="read")
        assert receipt["state"] == "committed"
        state = parent.continuations()[0]
        assert state["state"] == "applied" and state["applied_turn_id"] == receipt["turn_id"]
        assert not parent.recovery_report()["held_refs"]
        assert not parent.recovery_report()["rejected_refs"]
        await parent.close()

    asyncio.run(case())


def test_directive_reuses_terminal_facts_without_polling_task_history(tmp_path):
    from test_continuation import parent_with_result

    async def case():
        host, parent = await parent_with_result(tmp_path)

        def forbid_snapshot(*args, **kwargs):
            raise AssertionError("terminal result is already durable")

        host.snapshot = forbid_snapshot
        parent.child_send("a", "追加", command_id="directive")
        assert parent.child("a")._next_input() is not None
        await parent.close(force=True)

    asyncio.run(case())
