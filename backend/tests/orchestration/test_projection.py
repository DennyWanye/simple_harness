# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-4: what the UI sees — a whitelisted projection, gap-free event paging, change pushes.

Draft written before the implementation (plan 2026-09-11 H3).
"""

from __future__ import annotations

import asyncio
import re
import time

import pytest
from deskpet.orchestration.projection import (
    MISSION_FIELDS,
    project_approval,
    project_detail,
    project_event,
    ui_state,
)
from deskpet.orchestration.pump import MissionChangePump

from ._layered_lane import layered_service, notes_mission, quick_runtime, run_until_settled


@pytest.fixture(autouse=True)
def _quick_runtime(monkeypatch):
    quick_runtime(monkeypatch)


async def _completed_service(root, principal):  # type: ignore[no-untyped-def]
    service = layered_service(root, principal)
    await service.start()
    created = service.create_mission(notes_mission("k-proj"))
    mission = await run_until_settled(service, created["mission_id"])
    assert mission.status.value == "COMPLETED", mission.final_report
    return service, created["mission_id"]


@pytest.mark.asyncio
async def test_detail_is_whitelisted_and_marks_model_text(orchestration_root, principal):
    service, mission_id = await _completed_service(orchestration_root, principal)
    try:
        detail = service.mission_detail(mission_id)
        assert set(detail["mission"]) <= set(MISSION_FIELDS)
        summaries = [r["summary"] for r in detail["results"]]
        assert summaries and all(s["source"] == "model" for s in summaries)
        layers = [layer for r in detail["results"] for layer in r["verification_layers"]]
        assert {"format_check", "rule_check"} <= {layer["layer"] for layer in layers}
        # no test was run: the code_test layer never claims a real pass — it says the
        # check was not applicable (original §14.1)
        assert all(
            layer["status"] != "PASS" or "not applicable" in layer["summary"]["text"]
            for layer in layers if layer["layer"] == "code_test"
        )
        assert "amount_micros" not in detail["usage"]  # tokens only, no money
        assert detail["usage"]["reserved_tokens"] == 0
        assert detail["usage"]["settled_tokens"] is not None
        assert detail["usage"]["ledger_version"] is not None
        # review P2-4: a Planner wrote the Task goals, a Critic the critic_review summary
        assert detail["tasks"] and all(t["goal"]["source"] == "model" for t in detail["tasks"])
        sources = {layer["layer"]: layer["summary"]["source"] for layer in layers}
        assert sources.get("rule_check") == "system"
        assert sources.get("critic_review", "model") == "model"
        assert detail["mission"]["ui_state"] == "delivered"
    finally:
        await service.close()


@pytest.mark.parametrize("status", ["ACTIVE", "FAILED", "CANCELLED"])
def test_budget_projection_keeps_actual_held_reservation_even_after_terminal(status):
    view = {"snapshot": {
        "mission": {"status": status},
        "budget_usage": {"reserved_tokens": 700, "settled_tokens": 123, "version": 8},
    }}
    usage = project_detail(view)["usage"]
    assert usage["reserved_tokens"] == 700  # UNKNOWN can outlive a cancelled Mission
    assert usage["settled_tokens"] == 123
    assert "amount_micros" not in usage


def test_old_snapshot_does_not_invent_zero_budget_usage():
    usage = project_detail({"snapshot": {}})["usage"]
    assert usage["reserved_tokens"] is None and usage["settled_tokens"] is None


def _final_rejection_view():
    return {"snapshot": {
        "mission": {"id": "m", "status": "FAILED", "stop_reason": "max_attempts_reached"},
        "attempts": [{"id": "a1", "task_id": "t", "mission_id": "m", "failure": {
            "reason": "verification_failed", "failures": [{
                "layer": "rule_check", "status": "FAIL", "summary": "private summary",
                "detail": {"reason": "stale_source", "internal": "hidden", "source_current_issues": [{
                    "code": "stale_source", "reason": "revoked", "path": "sources/A.md",
                    "version": "a" * 64, "storage_uri": "private-path",
                }]},
            }],
        }}],
        "results": [{"envelope": {"id": "r1", "attempt_id": "a1", "task_id": "t", "mission_id": "m"},
                     "verification_state": "DONE", "verdict": "FAIL", "verifications": [
                         {"layer": "rule_check", "status": "PASS"},
                         {"layer": "human_review", "status": "PASS"},
                     ]}],
        "approvals": [{"request_id": "review-r1", "kind": "review", "state": "GRANTED"}],
    }}


def test_final_rejection_uses_same_attempt_without_rewriting_frozen_layers():
    detail = project_detail(_final_rejection_view())
    result = detail["results"][0]
    assert result["verdict"] == "FAIL" and result["verification_state"] == "DONE"
    assert result["final_rejection"] == {
        "reason": "stale_source", "source_issues": [{
            "code": "stale_source", "reason": "revoked", "path": "sources/A.md", "version": "a" * 64,
        }],
    }
    assert [row["status"] for row in result["verification_layers"]] == ["PASS", "PASS"]
    assert detail["mission"]["stop_reason"] == "max_attempts_reached"
    assert detail["approvals"][0]["state"] == "GRANTED"


def test_final_rejection_does_not_borrow_another_attempt_failure_on_same_task():
    view = _final_rejection_view()
    snapshot = view["snapshot"]
    snapshot["attempts"].append({"id": "a2", "task_id": "t", "mission_id": "m"})
    snapshot["results"].append({
        "envelope": {"id": "r2", "attempt_id": "a2", "task_id": "t", "mission_id": "m"},
        "verification_state": "DONE", "verdict": "FAIL",
    })
    first, other = project_detail(view)["results"]
    assert first["final_rejection"]["reason"] == "stale_source"
    assert other["verdict"] == "FAIL" and other["final_rejection"] is None


@pytest.mark.parametrize("case", ["pending", "pass", "missing_attempt", "foreign_task", "foreign_mission",
                                 "unknown_reason", "not_verification_failure", "passed_layer"])
def test_final_rejection_requires_final_failure_and_whitelisted_attempt_reason(case):
    view = _final_rejection_view()
    result = view["snapshot"]["results"][0]
    attempt = view["snapshot"]["attempts"][0]
    if case == "pending":
        result["verification_state"] = "PENDING"
    elif case == "pass":
        result["verdict"] = "PASS"
    elif case == "missing_attempt":
        result["envelope"]["attempt_id"] = "missing"
    elif case == "foreign_task":
        attempt["task_id"] = "foreign"
    elif case == "foreign_mission":
        attempt["mission_id"] = "foreign"
    elif case == "unknown_reason":
        attempt["failure"]["failures"][0]["detail"]["reason"] = "untrusted_model_reason"
    elif case == "not_verification_failure":
        attempt["failure"]["reason"] = "outcome_failure"
    else:
        attempt["failure"]["failures"][0]["status"] = "PASS"
    assert project_detail(view)["results"][0]["final_rejection"] is None


@pytest.mark.asyncio
async def test_event_paging_is_gap_free(orchestration_root, principal):
    service, mission_id = await _completed_service(orchestration_root, principal)
    try:
        seen: list[int] = []
        after = 0
        while True:
            page = service.events(mission_id, after_seq=after, limit=3)
            seen += [e["seq"] for e in page["events"]]
            assert all("payload" not in e and "trace_id" not in e for e in page["events"])  # P2-3
            after = page["through_seq"]  # the SDK facade's page cursor
            if not page["has_more"]:
                break
        assert seen == sorted(set(seen))
        assert len(seen) == service.mission_detail(mission_id)["event_count"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_pump_announces_a_status_change_within_two_seconds(orchestration_root, principal):
    service = layered_service(
        orchestration_root, principal, drive=True, tick_active_seconds=0.05, tick_idle_seconds=0.2
    )
    await service.start()
    pushed: list[tuple[float, dict]] = []

    async def broadcast(message: dict) -> None:
        pushed.append((time.monotonic(), message))

    pump = MissionChangePump(service, broadcast, interval=0.1)
    pump.start()
    try:
        created = service.create_mission(notes_mission("k-pump"))
        deadline = time.monotonic() + 20
        completed_at = None
        while time.monotonic() < deadline and completed_at is None:
            if service.mission_detail(created["mission_id"])["mission"]["status"] == "COMPLETED":
                completed_at = time.monotonic()
            await asyncio.sleep(0.02)
        assert completed_at is not None
        await asyncio.sleep(2.0)
        done = [
            at
            for at, message in pushed
            if message["type"] == "mission_changed"
            and message["payload"]["mission_id"] == created["mission_id"]
            and message["payload"]["status"] == "COMPLETED"
        ]
        assert done and done[0] - completed_at <= 2.0
        # 密钥形如 sk-xxxx；按词首匹配，Task id（"...:task-1"）里的 "sk-" 不是密钥。
        assert not any(re.search(r"\bsk-", str(message)) for _, message in pushed)
    finally:
        await pump.stop()
        await service.close()


@pytest.mark.parametrize(
    ("status", "kwargs", "expected"),
    [
        ("COMPLETED", {}, "delivered"),
        ("FAILED", {}, "failed"),
        ("CANCELLED", {}, "cancelled"),
        ("COMPLETED", {"blocked": True, "waiting": True}, "delivered"),  # the recorded status wins
        ("ACTIVE", {"blocked": True, "waiting": True}, "unknown"),
        ("ACTIVE", {"waiting": True, "attempt_statuses": ["RUNNING"]}, "waiting_person"),
        ("ACTIVE", {"attempt_statuses": ["COMPLETED", "RUNNING"]}, "running"),
        ("ACTIVE", {"attempt_statuses": ["COMPLETED", "VERIFYING"]}, "verifying"),
        ("ACTIVE", {"attempt_statuses": ["SUBMITTED"]}, "verifying"),
        ("CREATED", {}, "received"),
        ("PLANNING", {}, "received"),
        ("ACTIVE", {"attempt_statuses": ["COMPLETED"]}, "queued"),
        ("ACTIVE", {}, "queued"),
    ],
)
def test_ui_state_is_the_p31_vocabulary(status, kwargs, expected):  # type: ignore[no-untyped-def]
    assert ui_state(status, **kwargs) == expected


def test_an_event_row_never_carries_its_payload():
    raw = {
        "seq": 9,
        "type": "WorkerTurnCompleted",
        "created_at": 1.0,
        "task_id": "t-1",
        "attempt_id": "a-1",
        "actor_type": "agent",
        "actor_id": "worker",
        "trace_id": "trace",
        "idempotency_key": "key",
        "payload": {"path": "/Users/someone/private/file.txt", "text": "内部"},
    }
    row = project_event(raw)
    assert set(row) == {"seq", "type", "created_at", "task_id", "attempt_id", "actor_type"}
    assert "/Users/" not in str(row)
    comment = project_event({**raw, "type": "HumanCommentAdded", "payload": {"text": "看过了"}})
    assert comment["summary"] == "看过了"


def test_approval_text_is_marked_and_action_params_are_bounded():
    raw = {
        "request_id": "req-1",
        "kind": "action",
        "summary": {"layers": [{"layer": "critic_review", "status": "PASS", "summary": "模型说好"}]},
        "action": {"params": {"blob": "x" * 5000}, "reason": {"text": "因为要改配置"}},
    }
    item = project_approval(raw)
    assert item["summary"]["source"] == "model"
    assert "critic_review: PASS" in item["summary"]["text"]
    assert item["action"]["params"]["truncated"] is True
    assert len(item["action"]["params"]["preview"]) <= 600
    assert item["action"]["reason"] == {"text": "因为要改配置", "source": "model"}
    small = project_approval({**raw, "action": {"params": {"key": "feature_flags.new_ui"}}})
    assert small["action"]["params"] == {"key": "feature_flags.new_ui"}


def test_an_action_approval_keeps_its_fields_and_the_reason_source():
    """2026-09-29 真机：动作审批摘要整个转成文字后卡片只能显示原始字典；系统写的理由被标成模型写的。"""
    from deskpet.orchestration.projection import project_approval

    summary = {"connector": "file_publish", "operation": "publish", "target": "README.md",
               "params": {"artifact_path": "README.md", "content_hash": "c9" * 32, "storage_uri": "/secret/path"},
               "reason": "用户在确认页批准的操作：发布 README.md 到 README.md", "reason_source": "system"}
    shown = project_approval({"request_id": "a-1", "kind": "action", "summary": summary})["summary"]
    assert shown == {"connector": "file_publish", "operation": "publish", "target": "README.md",
                     "params": {"artifact_path": "README.md"},
                     "reason": "用户在确认页批准的操作：发布 README.md 到 README.md", "reason_source": "system"}
    forged = project_approval({"request_id": "a-2", "kind": "action",
                               "summary": {**summary, "reason_source": "model (untrusted)"}})["summary"]
    assert forged["reason_source"] == "model"


def test_a_resubmitted_action_card_says_why_the_last_one_did_not_take_effect():
    """2026-10-03 真机点击发现：系统按原内容重交的卡片，SDK 写了"上次为何没生效"，Host 摘要只放行
    白名单字段把它丢了，卡片上看不到。"""
    from deskpet.orchestration.projection import project_approval

    previous = {"action_key": "action-1:v1", "attempt": 1, "outcome": "human_ruled_not_applied",
                "reason": "human_ruled_failed"}
    summary = {"connector": "file_publish", "operation": "publish", "target": "minutes.md",
               "params": {"artifact_path": "minutes.md"}, "reason": "发布", "reason_source": "system",
               "previous_attempt": previous}
    shown = project_approval({"request_id": "a-1", "kind": "action", "summary": summary})["summary"]
    assert shown["previous_attempt"] == {"attempt": 1, "outcome": "human_ruled_not_applied",
                                         "reason": "human_ruled_failed"}
    first = project_approval({"request_id": "a-2", "kind": "action",
                              "summary": {k: v for k, v in summary.items() if k != "previous_attempt"}})["summary"]
    assert "previous_attempt" not in first
