# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 8 · slice A (plan D8-1' / D8-2' / D8-3'; S8-02, S8-05): Replay rebuilds facts that
already happened — a pure fold of events compared with the library — never executing,
never writing, and reporting what the record cannot decide instead of guessing."""

from __future__ import annotations


from agent_orchestrator.observability.replay import (
    Projection,
)


# ------------------------------------------------------------------ S8-05


# ------------------------------------------------------------------ step-7 states on record
# ------------------------------------------------------------------ code re-review (round 2)


def test_re_review_a_created_attempt_makes_its_ready_task_active():
    events = [
        {"id": "e1", "seq": 1, "type": "MissionCreated", "mission_id": "m", "payload": {}},
        {
            "id": "e2",
            "seq": 2,
            "type": "TaskCommitted",
            "mission_id": "m",
            "task_id": "t",
            "payload": {"dependencies": []},
        },
        {
            "id": "e3",
            "seq": 3,
            "type": "AttemptCreated",
            "mission_id": "m",
            "task_id": "t",
            "attempt_id": "a",
            "payload": {},
        },
    ]
    projection = Projection().feed(events)
    assert (
        projection.objects["task"]["t"]["status"] == "ACTIVE"
    )  # the library's accept of AttemptCreated
    assert projection.objects["attempt"]["a"]["status"] == "PENDING"
