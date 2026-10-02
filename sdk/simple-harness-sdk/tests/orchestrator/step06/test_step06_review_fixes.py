# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 6 · code review round 1 dispositions (reports/code-review-round1.md): one
decisive test per P0/P1 fix and for the P2 fixes that changed behaviour."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "full_target"))

from leaf_world import drive_to_running, leaf_world  # noqa: E402

from agent_orchestrator.governance.budgets import UsageFact
from agent_orchestrator.runtime.model_router import (
    ModelRouter,
    RoutingRules,
    RoutingUnavailable,
    RuntimeProfile,
    classify_turn_error,
)


# ------------------------------------------------------------------ P1-6 held reservations
def test_p1_6_a_reservation_held_by_an_unknown_charge_is_listed_and_on_the_timeline(tmp_path):
    world = leaf_world(tmp_path, key="p1-6")
    service, mission, t = world.service, world.mission, {"A": world.tasks["a"]}
    attempt = drive_to_running(service, t["A"])
    with service.store.transaction():
        service.ledger.import_usage(
            subject_id=attempt.id,
            mission_id=mission.id,
            facts=[UsageFact("u-1", 10, 5, None, unknown=True)],
        )
        report = service.ledger.costs_report(mission.id)
    assert [r["subject_id"] for r in report["held_reservations"]] == [attempt.id]
    first = service.record_reservation_held(
        attempt.id, mission.id, task_id=t["A"].id, reason="unknown_usage"
    )
    again = service.record_reservation_held(
        attempt.id, mission.id, task_id=t["A"].id, reason="unknown_usage"
    )
    assert first.id == again.id and service.store.count_events(mission.id, "ReservationHeld") == 1
    assert first.payload["reserved_tokens"] == 4_000


# ------------------------------------------------------------------ P2-1 / P2-3 routing details
def test_p2_1_turn_errors_are_classified_by_exact_codes_only():
    assert (
        classify_turn_error({"raw_failures": [{"error_code": "provider_server_error"}]})
        == "provider_unavailable"
    )
    assert classify_turn_error({"error_code": "provider_protocol_error"}) == "provider_error"
    assert (
        classify_turn_error({"error_code": "provider_empty_response"}) == "other"
    )  # already retried in the turn
    assert (
        classify_turn_error({"error_code": "provider_cancelled"}) == "other"
    )  # our own cancel is not ill health
    assert (
        classify_turn_error({"message": "see provider_server_error in the docs"}) == "other"
    )  # free text is not a code


def test_p2_3_a_fallback_that_is_cooling_down_too_is_not_used():
    class _Stub:
        async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
            raise AssertionError

    profiles = {n: RuntimeProfile(n, _Stub(), f"m-{n}") for n in ("small", "large")}
    router = ModelRouter(profiles, RoutingRules(default="small", fallback={"small": "large"}))
    with pytest.raises(RoutingUnavailable) as unavailable:
        router.route(role="worker", unavailable_until={"small": 100.0, "large": 90.0}, now=50.0)
    assert unavailable.value.profile_id == "large"
