# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Host projection of SDK liveness blockers."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings
from ._word_counter import FixtureWordCounter


def _heartbeat(attempt_id: str, liveness: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        type="HeartbeatReceived", attempt_id=attempt_id, payload={"liveness": liveness}
    )


def _service(*, heartbeats: list[SimpleNamespace], statuses: dict[str, str]) -> OrchestrationService:
    attempts = {
        attempt_id: SimpleNamespace(id=attempt_id, task_id=f"task-{attempt_id}", status=status)
        for attempt_id, status in statuses.items()
    }
    store = SimpleNamespace(
        list_events=lambda mission_id: heartbeats,
        get_attempt=attempts.get,
        read_view=nullcontext,
        list_tasks=lambda mission_id: [SimpleNamespace(id=a.task_id) for a in attempts.values()],
        list_attempts=lambda task_id: [a for a in attempts.values() if a.task_id == task_id],
        waiting_on=lambda mission_id: [],
        has_table=lambda name: False,  # no strict-TaskGraph requirement table in this fake
    )
    control = SimpleNamespace(
        missions=lambda *, limit: [
            {
                "mission_id": "mission-1",
                "status": "ACTIVE",
                "pending_approvals": 0,
            }
        ],
        snapshot=lambda mission_id: {
            "snapshot": {
                "mission": {"id": mission_id, "status": "ACTIVE"},
                "attempts": [
                    {"id": attempt.id, "task_id": attempt.task_id, "status": str(attempt.status)}
                    for attempt in attempts.values()
                ],
            }
        },
    )
    service = OrchestrationService(".", OrchestrationSettings(), principal=object(), native_test_counter=FixtureWordCounter())
    service._state = "available"
    service._control = control
    service._orchestrator = SimpleNamespace(store=store)
    return service


def test_blocked_uses_latest_heartbeat_and_keeps_unknown_blockers_cautious() -> None:
    service = _service(
        heartbeats=[
            _heartbeat(
                "transitioned",
                {
                    "blocked": True,
                    "blocker": {
                        "kind": "provider",
                        "blocker_id": "provider-call-1",
                        "ledger_identity": "invocation-1",
                        "handoff_attempt": 0,
                    },
                },
            ),
            _heartbeat(
                "transitioned",
                {"blocked": True, "blocker": {"kind": "provider_slot_wait", "billable": False}},
            ),
            _heartbeat(
                "actual-unknown",
                {
                    "blocked": True,
                    "blocker": {
                        "kind": "provider",
                        "blocker_id": "provider-call-2",
                        "ledger_identity": "invocation-2",
                        "handoff_attempt": 0,
                    },
                },
            ),
            _heartbeat("legacy-unknown", {"blocked": True}),
            _heartbeat(
                "terminal",
                {
                    "blocked": True,
                    "blocker": {
                        "kind": "provider",
                        "blocker_id": "provider-call-3",
                        "ledger_identity": "invocation-3",
                        "handoff_attempt": 0,
                    },
                },
            ),
        ],
        statuses={
            "transitioned": "RUNNING",
            "actual-unknown": "RUNNING",
            "legacy-unknown": "RUNNING",
            "terminal": "COMPLETED",
        },
    )

    assert service._blocked("mission-1") == [
        {
            "task_id": "task-actual-unknown",
            "attempt_id": "actual-unknown",
            "reason": "turn_outcome_unknown",
        },
        {
            "task_id": "task-legacy-unknown",
            "attempt_id": "legacy-unknown",
            "reason": "turn_outcome_unknown",
        },
    ]


def test_nonbillable_slot_wait_projects_as_running_in_list_and_detail() -> None:
    service = _service(
        heartbeats=[
            _heartbeat(
                "queued",
                {"blocked": True, "blocker": {"kind": "provider_slot_wait", "billable": False}},
            )
        ],
        statuses={"queued": "RUNNING"},
    )

    [row] = service.list_missions()
    detail = service.mission_detail("mission-1")

    assert (row["blocked"], row["ui_state"]) == (False, "running")
    assert detail["blocked"] == []
    assert detail["mission"]["ui_state"] == "running"


def test_bounded_billable_response_wait_is_running_but_unknown_remains_visible() -> None:
    for blocker, expected in [
        ({"kind": "provider_response_wait", "billable": True, "bounded": True}, "running"),
        ({"kind": "provider_response_wait", "billable": True}, "unknown"),
        ({"kind": "provider_response_wait", "billable": True, "bounded": False}, "unknown"),
        ({"kind": "provider", "billable": True, "bounded": True}, "unknown"),
    ]:
        service = _service(
            heartbeats=[_heartbeat("response", {"blocked": True, "blocker": blocker})],
            statuses={"response": "RUNNING"},
        )
        [row] = service.list_missions()
        detail = service.mission_detail("mission-1")
        assert row["ui_state"] == detail["mission"]["ui_state"] == expected
        assert bool(detail["blocked"]) == (expected == "unknown")


def test_completed_tasks_await_mission_judgment_in_list_and_detail() -> None:
    from deskpet.orchestration.projection import project_detail, ui_state

    service = _service(heartbeats=[], statuses={"done": "COMPLETED"})
    service._orchestrator.store.list_tasks = lambda mid: [
        SimpleNamespace(id="task-done", status="COMPLETED")
    ]
    [row] = service.list_missions()
    detail = project_detail({"snapshot": {
        "mission": {"id": "mission-1", "status": "ACTIVE"},
        "tasks": [{"id": "task-done", "status": "COMPLETED"}],
        "attempts": [{"id": "done", "task_id": "task-done", "status": "COMPLETED"}],
    }})
    assert row["ui_state"] == detail["mission"]["ui_state"] == "verifying"
    assert ui_state("ACTIVE", task_statuses=["COMPLETED", "READY"]) == "queued"
    assert ui_state("ACTIVE", task_statuses=[]) == "queued"
    assert ui_state("ACTIVE", task_statuses=["COMPLETED"], waiting=True) == "waiting_person"
    assert ui_state("ACTIVE", task_statuses=["COMPLETED"], blocked=True) == "unknown"
    assert ui_state("COMPLETED", task_statuses=["COMPLETED"]) == "delivered"


def test_list_rows_count_completed_tasks_for_the_progress_bar() -> None:
    service = _service(heartbeats=[], statuses={"done": "COMPLETED"})
    service._orchestrator.store.list_tasks = lambda mid: [
        SimpleNamespace(id="a", status="COMPLETED"), SimpleNamespace(id="b", status="ACTIVE"),
        SimpleNamespace(id="c", status="TaskStatus.COMPLETED"),
    ]
    [row] = service.list_missions()
    assert row["task_counts"] == {"completed": 2, "total": 3}


def test_a_finished_mission_row_reads_no_event_log() -> None:
    """NEXT-TG-1.0 §9: every push while anything runs re-sends mission_list; a finished
    Mission's row must not re-read its whole event log for blocked/waiting."""
    service = _service(heartbeats=[], statuses={"a1": "COMPLETED"})

    def forbidden(_mission_id):
        raise AssertionError("a finished Mission's event log was read for the list")

    service._orchestrator.store.list_events = forbidden
    service._control.missions = lambda *, limit: [
        {"mission_id": "mission-1", "status": "COMPLETED", "pending_approvals": 0}]
    [row] = service.list_missions()
    assert row["ui_state"] == "delivered" and row["blocked"] is False
