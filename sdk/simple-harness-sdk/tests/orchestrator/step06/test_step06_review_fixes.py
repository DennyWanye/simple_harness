# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 6 · code review round 1 dispositions (reports/code-review-round1.md): one
decisive test per P0/P1 fix and for the P2 fixes that changed behaviour."""

from __future__ import annotations

import asyncio

import pytest
from helpers_step06 import config, spec

from agent_orchestrator.contracts import Budget, MissionStatus
from agent_orchestrator.governance.budgets import UsageFact
from agent_orchestrator.orchestrator.commit_service import mission_account
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.model_router import (
    ModelRouter,
    RoutingRules,
    RoutingUnavailable,
    RuntimeProfile,
    classify_turn_error,
)
from agent_orchestrator.testing.fixtures import (
    _recorder_task,
    _write_files_then,
    demo_dynamic_dag_provider,
)


def _doc(key, *, policy=("format_check", "rule_check")):
    return _recorder_task(
        key,
        f"写出文档 {key}.md（独立任务 {key}）",
        [],
        [f"file:{key}.md"],
        1.0,
        [f"{key}.md"],
        policy=list(policy),
    )


def _doc_script(key, content=None):
    return _write_files_then(
        {f"{key}.md": content or f"# {key}\n"},
        test_path=None,
        summary=f"{key} 写出",
        claim=f"{key}.md 写出",
    )


# ------------------------------------------------------------------ P1-2 tool calls in parallel
def test_p1_2_parallel_tasks_share_the_tool_call_pool_without_a_false_exhaustion(tmp_path):
    tasks = [_doc("P1"), _doc("P2")]
    provider = demo_dynamic_dag_provider(
        tasks=tasks, per_attempt={"P1": [_doc_script("P1")], "P2": [_doc_script("P2")]}
    )

    async def case():
        async with Orchestrator(config(tmp_path, max_concurrency=2), provider) as orchestrator:
            mission = await orchestrator.submit_mission(
                spec(
                    "p1-2",
                    success_criteria=("file:P1.md", "file:P2.md"),
                    budget=Budget(max_tool_calls=10, max_attempts=8),
                )
            )
            await asyncio.wait_for(orchestrator.run(), 60)
            store = orchestrator.store
            assert store.get_mission(mission.id).status is MissionStatus.COMPLETED, (
                orchestrator.progress_log
            )
            for task in store.list_tasks(mission.id):
                assert task.budget.max_tool_calls == 5  # shared out, not copied to every Task
                attempt = store.list_attempts(task.id)[0]
                assert store.get_intent_for_subject(attempt.id).config["max_tool_calls"] <= 5
            with store.transaction():
                account = orchestrator.commit.ledger.account(mission_account(mission.id))
            assert account.settled_tool_calls == 4 and account.reserved_tool_calls == 0

    asyncio.run(case())


# ------------------------------------------------------------------ P1-6 held reservations
def test_p1_6_a_reservation_held_by_an_unknown_charge_is_listed_and_on_the_timeline(tmp_path):
    from graph_helpers6 import drive_to_running, graph_service

    service, mission, t = graph_service(tmp_path)
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
