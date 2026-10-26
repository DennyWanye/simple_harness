# SPDX-License-Identifier: Apache-2.0
"""Planning regressions from the 2026-10-26 desktop run."""

from __future__ import annotations

import asyncio
import json

import pytest
from h1i_seed import plan_reply, reviewed
from test_planning_bound_per_round import _count

from agent_orchestrator.contracts.error_table import refusal_charges_planner
from agent_orchestrator.orchestrator.failure_classes import INFRA, classify_failure
from agent_orchestrator.planning.decision_feedback import feedback_from_decision
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    one_step_method,
    planner_reply,
    review_input,
    review_reply,
)


@pytest.mark.parametrize(
    "error",
    [
        {"error_code": "provider_server_error", "status_code": 524},
        {
            "error_code": "provider_empty_response",
            "message": "empty response",
        },
        {
            "error_code": "provider_protocol_error",
            "message": "invalid chat stream: event carries an error object",
            "source_kind": "tool_parse",
        },
    ],
)
def test_provider_turn_failures_share_six_free_rounds(error):
    failure = {
        "reason": "turn_failed",
        "error_kind": "other" if error.get("source_kind") == "tool_parse" else "provider_error",
        "error": error,
    }
    assert classify_failure(failure) == INFRA
    rejected = (
        "PlanningRejected",
        {"reason": "proposal_unreadable", "detail": {"turn_failed": True, "error": error}},
    )
    assert _count(*([rejected] * 6)) == 0
    assert _count(*([rejected] * 7)) == 1


def test_internal_contract_errors_do_not_charge_but_invalid_parameters_do():
    assert refusal_charges_planner(["INTERNAL_CONTRACT_ERROR"]) is False
    assert refusal_charges_planner(["PARAMETER_INVALID"]) is True
    assert refusal_charges_planner(["INTERNAL_CONTRACT_ERROR", "PARAMETER_INVALID"]) is True
    assert (
        _count(("PlanningRejected", {"detail": {"rejection_codes": ["INTERNAL_CONTRACT_ERROR"]}}))
        == 0
    )


def test_sqlite_commit_conflict_does_not_charge_planner(tmp_path):
    async def case():
        async with reviewed(tmp_path, key="sqlite-commit-conflict") as (seed, opener, _provider):
            loop, mission, _world, _root, dispatch, _product = seed
            loop.store.connection.execute(
                "CREATE TRIGGER test_plan_conflict BEFORE INSERT ON plan_revisions "
                "BEGIN SELECT RAISE(ABORT, 'UNIQUE constraint failed: "
                "plan_revisions.mission_id, plan_revisions.revision'); END"
            )
            try:
                await loop._collect_plan_decision(
                    opener,
                    object(),
                    mission,
                    plan_reply(opener.config["planning_package"]),
                    dispatch,
                )
            finally:
                loop.store.connection.execute("DROP TRIGGER test_plan_conflict")
            row = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(
                opener.intent_id, 0
            )
            assert row["status"] == "COMMIT_REJECTED"
            assert row["rejection_codes"] == ["INTERNAL_CONTRACT_ERROR"]
            assert loop._planning_attempts(mission.id) == 0
            rejected = [
                e for e in loop.store.list_events(mission.id) if e.type == "PlanningRejected"
            ]
            assert rejected[-1].payload["reason"] == "planning_source_unavailable"
            assert rejected[-1].payload["detail"]["rejection_codes"] == ["INTERNAL_CONTRACT_ERROR"]
            assert not HtnStore(loop.store).list_plan_revisions(mission.id)

    asyncio.run(case())


def test_goal_mismatch_feedback_keeps_exact_expected_and_observed(tmp_path):
    async def case():
        async with reviewed(tmp_path, key="goal-mismatch-feedback") as (seed, opener, _provider):
            loop, mission, _world, root, dispatch, _product = seed
            raw = plan_reply(opener.config["planning_package"])
            body = json.loads(
                raw.removeprefix("<planning_decision>").removesuffix("</planning_decision>")
            )
            expected = root.typed_parameters["goal"]
            body["payload"]["bindings"]["goal"] = "A rewritten goal"
            text = "<planning_decision>" + json.dumps(body) + "</planning_decision>"
            await loop._collect_plan_decision(opener, object(), mission, text, dispatch)
            row = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(
                opener.intent_id, 0
            )
            assert row["status"] == "REJECTED"
            assert row["rejection_codes"] == ["PARAMETER_INVALID"]
            feedback = feedback_from_decision(
                row,
                budgets={
                    "same_request_format_retries_remaining": 0,
                    "planning_rounds_remaining": 2,
                    "root_review_repairs_remaining": 1,
                    "repeated_failure_before_escalation_remaining": None,
                },
            )
            [problem] = feedback.problems
            assert problem.field_path == "/payload/bindings/goal"
            assert problem.expected == json.dumps(
                expected, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
            assert problem.observed == '"A rewritten goal"'
            assert loop._planning_attempts(mission.id) == 1
            assert not HtnStore(loop.store).list_plan_revisions(mission.id)

    asyncio.run(case())


def test_refused_repair_can_commit_a_different_method_at_same_revision(tmp_path):
    state = {"review_failed": False, "same_refused": False, "proposed": False}

    def reviewer(request):
        data = review_input(request)
        if data is None:
            return None
        if (
            str((data.get("package") or {}).get("purpose")) == "TASK_CONTENT"
            and not state["review_failed"]
        ):
            state["review_failed"] = True
            return review_reply(data, verdict="REJECTED", grade="FAIL", reason="Missing details")
        return review_reply(data)

    def planner(request):
        package = package_of(request)
        if not package.get("repair_requests"):
            return planner_reply(request)
        goal = next(
            item
            for item in package["views"]["goals"]
            if item.get("under_repair") and item.get("adopted_method")
        )
        instance = next(
            item
            for item in package["visible_refs"]
            if item["kind"] == "method_instance"
            and item["id"] == goal["adopted_method"]["method_instance_id"]
        )
        if not state["same_refused"]:
            state["same_refused"] = True
            replacement = goal["adopted_method"]["method_ref"]
        elif not state["proposed"]:
            state["proposed"] = True
            context = next(
                item
                for item in package["method_proposal_contexts"]
                if item["subject_key"] == goal["subject_key"]
            )
            method = one_step_method(context)
            method["steps"][0]["local_id"] = "rewrite"
            method["composition"]["finalizer_step"] = "rewrite"
            for link in method["composition"]["criterion_links"]:
                link["child_step"] = "rewrite"
            return decision(
                context["subject_key"],
                "PROPOSE_METHOD",
                {"method_proposal": {"method": method, "rationale": "Use a fresh writing step"}},
                "Propose replacement",
            )
        else:
            replacement = next(
                item
                for item in package["visible_refs"]
                if item["kind"] == "method"
                and item["content_hash"] != goal["adopted_method"]["method_ref"]["content_hash"]
            )
        return decision(
            goal["subject_key"],
            "REPAIR",
            {
                "repair_kind": "REPLACE_METHOD",
                "rejected_method_instance": instance,
                "replacement_method_ref": replacement,
                "bindings": goal["params"],
            },
            "Replace method",
        )

    async def case():
        provider = LayeredScriptedProvider(planner=planner, reviewer=reviewer)
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create(
                {
                    "goal": "Write NOTES.md with three details",
                    "idempotency_key": "repair-revision",
                    "success_criteria": ["file:NOTES.md"],
                }
            )["mission_id"]
            async with asyncio.timeout(30):
                mission = await world.run_until_settled(mission_id, rounds=40)
            assert mission.status.value == "COMPLETED", (mission.status, mission.final_report)
            events = [
                e.payload
                for e in world.store.list_events(mission_id)
                if e.type == "PlanningDecisionEvaluated"
            ]
            refused = [e for e in events if e.get("status") == "COMMIT_REJECTED"]
            assert len(refused) == 1 and refused[0]["rejection_codes"] == ["REPAIR_NOT_ALLOWED"]
            repairs = [
                e
                for e in events
                if e.get("decision_type") == "REPAIR" and e.get("status") == "COMMITTED"
            ]
            assert len(repairs) == 1 and repairs[0]["plan_revision"] == 2
            assert [
                (r.revision, r.state) for r in HtnStore(world.store).list_plan_revisions(mission_id)
            ] == [(1, "RETIRED"), (2, "ACTIVE")]

    asyncio.run(case())
