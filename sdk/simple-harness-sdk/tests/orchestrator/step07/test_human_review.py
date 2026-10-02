# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 7 · slice D (D7-9', S7-07): takeover and comments — a person's word is kept as
HumanOverride with its basis, and it never widens what was authorised.

删旧平面模式 第三刀：两条"任务级审阅员与全局裁判意见相左 → 交给人"的平面整圈测试随平面删；
其余四条只需要任务行，换成分层底座的 ``ledger_service``。"""

from __future__ import annotations

import pytest
from helpers_step07 import ALICE, BOB, complete, drive_to_running, ledger_service

from agent_orchestrator.contracts import AttemptStatus, MissionStatus, TaskStatus
from agent_orchestrator.orchestrator.action_commits import ActionCommitError


# ------------------------------------------------------------------ takeover and comments
def test_s7_07_a_takeover_stop_ends_the_task_with_its_basis_on_the_books(tmp_path):
    service, mission, t, _config, _connectors, _ = ledger_service(tmp_path)
    attempt = drive_to_running(service, t["A"])
    record = service.takeover(
        t["A"].id, principal=ALICE, action="stop", basis="卡住 40 分钟没有进展"
    )
    assert record["action"] == "takeover_stop" and "no approval, tool, budget" in record["scope"]
    final = service.store.get_mission(mission.id)
    assert final.status is MissionStatus.FAILED and final.stop_reason == "human_override"
    assert final.final_report["detail"]["basis"] == "卡住 40 分钟没有进展"
    assert service.store.get_attempt(attempt.id).status is AttemptStatus.CANCELLED
    [event] = [e for e in service.store.list_events(mission.id) if e.type == "HumanOverride"]
    assert (event.actor_type, event.actor_id) == ("user", "alice")


def test_s7_07_retry_with_note_stays_within_the_task_and_carries_the_note(tmp_path):
    service, mission, t, _config, _connectors, _ = ledger_service(tmp_path)
    attempt = drive_to_running(service, t["A"])
    service.takeover(
        t["A"].id,
        principal=BOB,
        action="retry_with_note",
        basis="方向不对",
        note="先读 docs/SPEC.md",
    )
    closed = service.store.get_attempt(attempt.id)
    assert (
        closed.status is AttemptStatus.CANCELLED
        and closed.failure["reason"] == "human_retry_with_note"
    )
    [comment] = [e for e in service.store.list_events(mission.id) if e.type == "HumanCommentAdded"]
    assert (
        comment.payload["target_id"] == t["A"].id and comment.payload["text"] == "先读 docs/SPEC.md"
    )
    assert service.store.get_mission(mission.id).status is MissionStatus.ACTIVE
    task = service.store.get_task(t["A"].id)
    assert (
        task.status is TaskStatus.ACTIVE and task.budget.max_attempts == t["A"].budget.max_attempts
    )
    with pytest.raises(ActionCommitError):  # only stop / retry_with_note exist
        service.takeover(t["A"].id, principal=BOB, action="approve", basis="x")
    with pytest.raises(ActionCommitError):
        service.takeover(
            t["A"].id, principal=BOB, action="stop", basis="sk-" + "a" * 40
        )  # looks like a secret


def test_a_takeover_never_revives_an_ended_task_or_mission(tmp_path):
    service, mission, t, _config, _connectors, _ = ledger_service(tmp_path)
    complete(service, t["A"])
    with pytest.raises(ActionCommitError):
        service.takeover(t["A"].id, principal=ALICE, action="retry_with_note", basis="再来一次")
    service.cancel_mission(mission.id)
    with pytest.raises(ActionCommitError):
        service.takeover(t["B"].id, principal=ALICE, action="stop", basis="停")


def test_comments_are_kept_as_data_on_a_task_or_a_request(tmp_path):
    service, mission, t, _config, connectors, deployment = ledger_service(tmp_path)
    from helpers_step07 import candidate

    action = service.propose_action(
        candidate(),
        mission_id=mission.id,
        task_id=t["A"].id,
        result_id="result-1",
        attempt_id=f"{t['A'].id}:attempt-1",
        artifact_id="artifact-1",
        artifact_hash="a" * 64,
        connectors=connectors,
        deployment=deployment,
    )
    service.add_comment(action["approval_request_id"], principal=ALICE, text="上线窗口在周四")
    service.add_comment(t["A"].id, principal=BOB, text="注意兼容旧客户端")
    request = service.store.get_approval(action["approval_request_id"])
    assert [c["text"] for c in request["comments"]] == ["上线窗口在周四"]
    assert request["state"] == "PENDING"  # a comment decides nothing
    texts = [
        e.payload["text"]
        for e in service.store.list_events(mission.id)
        if e.type == "HumanCommentAdded"
    ]
    assert texts == ["上线窗口在周四", "注意兼容旧客户端"]


# ------------------------------------------------------------------ code review round 1
