# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Host projection of SDK liveness blockers."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings


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
    service = OrchestrationService(".", OrchestrationSettings(), principal=object())
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
