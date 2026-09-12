# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-4: what the UI sees — a whitelisted projection, gap-free event paging, change pushes.

Draft written before the implementation (plan 2026-09-11 H3).
"""

from __future__ import annotations

import asyncio
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
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import notes_provider, notes_request


async def _completed_service(root, principal):  # type: ignore[no-untyped-def]
    service = OrchestrationService(
        root, OrchestrationSettings(), provider=notes_provider(), principal=principal, drive=False
    )
    await service.start()
    created = service.create_mission(notes_request("k-proj"))
    await service.drain()
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
        # NOT_REQUIRED stays NOT_REQUIRED (original §14.1) — never shown as a pass
        assert all(
            layer["status"] != "PASS" for layer in layers if layer["layer"] == "code_test"
        )
        assert detail["usage"]["amount_micros"] is None  # unpriced, not zero
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
    assert usage["amount_micros"] is None


def test_old_snapshot_does_not_invent_zero_budget_usage():
    usage = project_detail({"snapshot": {}})["usage"]
    assert usage["reserved_tokens"] is None and usage["settled_tokens"] is None


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
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(tick_active_seconds=0.05, tick_idle_seconds=0.2),
        provider=notes_provider(),
        principal=principal,
    )
    await service.start()
    pushed: list[tuple[float, dict]] = []

    async def broadcast(message: dict) -> None:
        pushed.append((time.monotonic(), message))

    pump = MissionChangePump(service, broadcast, interval=0.1)
    pump.start()
    try:
        created = service.create_mission(notes_request("k-pump"))
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
        assert all("sk-" not in str(message) for _, message in pushed)
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
