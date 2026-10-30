# SPDX-License-Identifier: Apache-2.0
"""可信宿主 Goal 的结构收口，以及不请求模型的恢复事实。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_session import SPEC, FakeHost

from agent_orchestrator.host import Session
from agent_orchestrator.host.session_ledger import SessionError


def make(tmp_path):
    host = FakeHost(tmp_path)
    return host, Session(host, session_id="main")


def spec(goal_id="g", **changes):
    return {
        "goal_id": goal_id,
        "objective": "交付报告",
        "completion_criteria": ["报告包含可核验来源"],
        "stop_conditions": ["宿主要求停止"],
        "budget": {"max_model_calls": 20},
        "grant_ref": "grant-report",
        "policy_revision": "policy-1",
        **changes,
    }


def put(session, goal_id="g", *, command="put", version=0, **changes):
    return session.put_goal(spec(goal_id, **changes), command_id=command, expected_version=version)


def test_goal_version_history_idempotency_terminal_and_single_active(tmp_path):
    _, session = make(tmp_path)
    first = put(session)
    assert first["goal_ref"] == {"goal_id": "g", "goal_version": 1}
    assert put(session) == first
    with pytest.raises(SessionError, match="idempotency_conflict"):
        put(session, objective="改参数")
    with pytest.raises(SessionError, match="active_work"):
        put(session, "other", command="other")
    second = put(session, command="update", version=1, objective="扩写报告")
    assert second["goal_ref"]["goal_version"] == 2
    assert session._store.get("goal_history", "g:1")["objective"] == "交付报告"
    assert session._store.get("goal_history", "g:2")["objective"] == "扩写报告"
    with pytest.raises(SessionError, match="stale_version"):
        put(session, command="stale", version=1)
    done = session.finish_goal("g", "succeeded", command_id="finish", expected_version=2, basis="审阅员已核验")
    assert done["state"] == "succeeded"
    assert session.finish_goal("g", "succeeded", command_id="finish", expected_version=2, basis="审阅员已核验") == done
    with pytest.raises(SessionError, match="stale_version"):
        put(session, command="after-done", version=done["version"])
    assert put(session, "next", command="next")["goal_ref"]["goal_version"] == 1


@pytest.mark.parametrize("changes", [
    {"objective": ""}, {"completion_criteria": []}, {"completion_criteria": [""]},
    {"budget": {}}, {"budget": {"max_model_calls": 0}},
    {"budget": {"max_model_calls": True}}, {"budget": {"max_model_calls": 1.5}},
    {"stop_conditions": "text"}, {"principal_id": "forged"},
])
def test_goal_spec_rejects_unbounded_or_forged_inputs_without_rows(tmp_path, changes):
    _, session = make(tmp_path)
    with pytest.raises(SessionError, match="invalid_argument"):
        put(session, **changes)
    assert session._store.rows("goals") == []
    assert session._store.rows("commands") == []


def test_generated_goal_id_is_stable_and_root_only(tmp_path):
    _, parent = make(tmp_path)
    body = spec()
    del body["goal_id"]
    first = parent.put_goal(body, command_id="generated", expected_version=0)
    assert first["goal_ref"]["goal_id"]
    assert parent.put_goal(body, command_id="generated", expected_version=0) == first
    asyncio.run(parent.create_child("a", command_id="child"))
    with pytest.raises(SessionError, match="not_found_or_forbidden"):
        parent.child("a").put_goal(spec("child-goal"), command_id="no", expected_version=0)


def test_goal_success_requires_basis_and_structural_facts_in_children(tmp_path):
    host, parent = make(tmp_path)
    put(parent)
    with pytest.raises(SessionError, match="invalid_argument"):
        parent.finish_goal("g", "succeeded", command_id="blank", expected_version=1)
    asyncio.run(parent.create_child("a", command_id="child"))
    assert parent.children()[0]["goal_ref"] == {"goal_id": "g", "goal_version": 1}
    task = parent._delegations.delegate("turn", "call", {**SPEC, "child_key": "a"})
    assert task["goal_ref"] == {"goal_id": "g", "goal_version": 1}
    with pytest.raises(SessionError, match="active_work"):
        parent.finish_goal("g", "succeeded", command_id="busy", expected_version=1, basis="看过了")
    host.finish(task["mission_id"])
    parent._inbox.catch_up()
    with pytest.raises(SessionError, match="active_work"):
        parent.finish_goal("g", "succeeded", command_id="unapplied", expected_version=1, basis="看过了")
    notice = parent.inbox()[0]
    assert notice["goal_ref"] == {"goal_id": "g", "goal_version": 1}
    with pytest.raises(SessionError, match="invalid_argument"):
        parent.exclude_result(notice["event_id"], "", command_id="blank-exclude")
    excluded = parent.exclude_result(notice["event_id"], "审阅员判定该结果不作为完成依据", command_id="exclude")
    assert parent.exclude_result(notice["event_id"], "审阅员判定该结果不作为完成依据", command_id="exclude") == excluded
    assert parent.inbox()[0]["applied_turn_id"] is None
    assert parent.finish_goal("g", "succeeded", command_id="finish", expected_version=1, basis="验收通过")["state"] == "succeeded"


def test_goal_old_result_keeps_frozen_version_and_root_scope(tmp_path):
    host, parent = make(tmp_path)
    put(parent)
    asyncio.run(parent.create_child("a", command_id="child"))
    task = parent._delegations.delegate("turn", "call", {**SPEC, "child_key": "a"})
    frozen = deepcopy(host.made)
    put(parent, command="update", version=1, objective="新版目标")
    assert host.made == frozen
    host.finish(task["mission_id"])
    parent._inbox.catch_up()
    assert parent.inbox()[0]["goal_ref"]["goal_version"] == 1
    assert parent.child("a").inbox()[0]["goal_ref"]["goal_version"] == 1
    other = Session(host, session_id="other")
    with pytest.raises(SessionError, match="not_found_or_forbidden"):
        other.exclude_result(parent.inbox()[0]["event_id"], "越树", command_id="forbidden")
    with pytest.raises(SessionError, match="not_found_or_forbidden"):
        other.finish_goal("g", "failed", command_id="forbidden", expected_version=2)


@pytest.mark.parametrize("namespace,row", [
    ("requests", {"request_id": "approval", "request_kind": "authorization", "version": 1, "state": "pending", "goal_ref": {"goal_id": "g", "goal_version": 1}}),
    ("effects", {"operation_key": "op", "params_hash": "h", "state": "unknown", "goal_ref": {"goal_id": "g", "goal_version": 1}, "receipt_ref": None}),
])
def test_goal_pending_request_and_unknown_effect_block_success(tmp_path, namespace, row):
    _, session = make(tmp_path)
    put(session)
    with session._store.transaction():
        session._store.put(namespace, "r", row)
    with pytest.raises(SessionError, match="active_work"):
        session.finish_goal("g", "succeeded", command_id="finish", expected_version=1, basis="审阅通过")
    if namespace == "effects":
        goal = session._store.get("goals", "g")
        assert goal["state"] == "waiting"
        assert "unknown" in str(goal["waiting_reasons"])


def test_goal_unknown_and_pending_requests_in_task_store_block_finish(tmp_path):
    host, session = make(tmp_path)
    put(session)
    task = session._delegations.delegate("turn", "call", SPEC)
    host.finish(task["mission_id"])
    session._inbox.catch_up()
    session.exclude_result(session.inbox()[0]["event_id"], "不采用该结果", command_id="exclude")
    host.approvals = lambda mid: [{"request_id": "pending-approval", "status": "PENDING"}]
    with pytest.raises(SessionError, match="active_work"):
        session.finish_goal("g", "succeeded", command_id="finish", expected_version=1, basis="看过")


def test_recovery_report_has_durable_facts_without_model_calls(tmp_path):
    from agent_orchestrator.host.recovery_report import build_recovery_report

    _, session = make(tmp_path)
    with session._store.transaction():
        session._store.put("queued_inputs", "input", {"input_id": "input", "kind": "user_message", "state": "running"})
        session._store.put("continuation_results", "held", {"event_id": "held", "state": "hold", "reason": "policy"})
        session._store.put("requests", "approval", {"request_id": "approval", "request_kind": "authorization", "state": "pending"})
        session._store.put("effects", "effect", {"operation_key": "effect", "state": "unknown"})
        session._store.put("voice_deliveries", "voice", {"input_id": "voice", "state": "submitted", "binding_id": "gone"})
        session._store.put("delegations", "rejected", {"delegation_id": "rejected", "error": "authorization_revoked"})
    session._host.owner_generation = 7
    session._chat = SimpleNamespace(send=lambda *a, **k: pytest.fail("must not call model"))
    report = build_recovery_report(session)
    assert set(report) == {"store_version", "owner_generation", "resumed_refs", "waiting_approval_refs", "held_refs", "unknown_effect_refs", "rejected_refs", "reasons"}
    assert report["store_version"] == session.capabilities()["session_config_version"]
    assert report["owner_generation"] == 7
    assert "input" in report["resumed_refs"]
    assert "held" in report["held_refs"] and report["reasons"]["held"] == "policy"
    assert "approval" in report["waiting_approval_refs"]
    assert {"effect", "voice"} <= set(report["unknown_effect_refs"])
    assert "rejected" in report["rejected_refs"]
    assert report == session.recovery_report()
