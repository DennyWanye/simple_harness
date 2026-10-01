# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Loop-level fixtures for a hierarchical Mission on the current planning protocol.

A real ``Orchestrator`` loop needs three things a scripted test has to supply:

* the Mission keeps its protocol binding and holds a confirmed completion mapping —
  ``build_world(...)`` in ``test_htn_end_to_end`` does that;
* every Planner request is granted its planning authorization, which is the Host's job
  in a deployment — :func:`auto_grant` stands in for the Host;
* the Planner answers with a ``<planning_decision>`` built from the package it was
  shown — the ``*_step`` callables here are such scripted Planners.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi
from agent_orchestrator.contracts.planning_decisions import PlanningDecisionEnvelopeV1
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.hierarchical_dispatch import is_hierarchical
from agent_orchestrator.planning.decision_codec import serialize_planning_decision
from agent_orchestrator.storage.planning_admission_store import PlanningAdmissionStore
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from agent_orchestrator.testing.fixtures import package_of


def auto_grant(loop: Any) -> Any:
    """Grant every Planner request this loop opens, as an auto-permission Host does.

    The grant is issued right after the request is bound, inside the same transaction
    the intent is created in, so the request never waits for an authority nobody in
    the test is going to give.
    """

    original = loop._create_planner_intent_now

    def create(mission_id: str, *, ordinal: int) -> Any:
        intent = original(mission_id, ordinal=ordinal)
        mission = loop.store.get_mission(mission_id)
        if mission is None or not is_hierarchical(mission):
            return intent
        request = PlanningDecisionStore(loop.store).get_planning_request_for_intent(
            intent.intent_id
        )
        if request is not None and (
            PlanningAdmissionStore(loop.store).get_request_binding(request.request_id) is None
        ):
            PlanningAuthorizationApi(
                loop.store, tenant_id=mission.tenant_id, principal=Principal(loop._owner)
            ).issue(
                mission_id,
                command_id="grant-" + request.request_id,
                request_id=request.request_id,
                approval_source="HOST_AUTO_PERMISSION",
            )
        return intent

    loop._create_planner_intent_now = create
    return loop


def decision_text(body: Mapping[str, Any]) -> str:
    """One ``<planning_decision>`` block for a decision body (validated by the codec)."""

    return serialize_planning_decision(PlanningDecisionEnvelopeV1.from_json(dict(body)))


def _envelope(subject_key: str, decision_type: str, payload: Mapping[str, Any], *,
              rationale: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "decision_type": decision_type,
        "subject_key": subject_key,
        "rationale": rationale,
        "reason_refs": [],
        "assumptions": [],
        "payload": dict(payload),
        "uncertainties": [],
        "alternatives": [],
        "replan_triggers": [],
    }


def refine_reply(package: Mapping[str, Any], *, method_id: str | None = None) -> str:
    """REFINE the first open goal with ``method_id``, or with the first applicable method."""

    goal = next(row for row in package["views"]["goals"] if row["open"])

    def applies(row: Mapping[str, Any]) -> bool:
        return any(report["verdict"] == "APPLICABLE"
                   and report["goal_occurrence_id"] == goal["occurrence_id"]
                   for report in row["applicability"])

    chosen = next(
        row for row in package["views"]["methods"]
        if (row["method_ref"]["id"] == method_id if method_id is not None else applies(row))
    )
    return decision_text(
        _envelope(
            goal["subject_key"],
            "REFINE",
            {"method_ref": dict(chosen["method_ref"]), "bindings": dict(goal["params"])},
            rationale="选择当前可适用的已注册方法。",
        )
    )


def refine_step(*, method_id: str | None = None) -> Callable[[Any], str]:
    """A scripted Planner: REFINE the open goal out of the package it was shown."""

    def step(request: Any) -> str:
        return refine_reply(package_of(request), method_id=method_id)

    return step


def content_critic_step(*, verdict: str = "PASS", blocker: str | None = None) -> Callable[[Any], str]:
    """A scripted content reviewer for the completion protocol.

    The local criteria it must answer are ``task_content_scope.criteria`` — copied in
    order, by ``criterion_id`` — not the Mission's own success criteria; a package with
    no scope (the Mission-level judgement) falls back to those.
    """

    def step(request: Any) -> str:
        package = package_of(request)
        scope = package.get("task_content_scope")
        if isinstance(scope, Mapping):
            criteria = [str(item["criterion_id"]) for item in scope.get("criteria", ())]
        else:
            criteria = list(package.get("mission_success_criteria", []))
        body = {
            "verdict": verdict,
            "findings": [] if blocker is None else [{"severity": "blocker", "detail": blocker}],
            "mission_criteria": [
                {"criterion": item, "met": verdict == "PASS", "reason": "scripted"}
                for item in criteria
            ],
        }
        return "<critic_verdict>" + json.dumps(body, ensure_ascii=False) + "</critic_verdict>"

    return step


def planning_package_of(intent: Any) -> dict[str, Any]:
    """The sealed package a Planner intent carries (exactly what admission re-reads)."""

    package = intent.config.get("planning_package")
    if not isinstance(package, Mapping):
        raise AssertionError(f"intent {intent.intent_id} carries no planning package")
    return json.loads(json.dumps(package))


__all__ = (
    "auto_grant",
    "content_critic_step",
    "decision_text",
    "planning_package_of",
    "refine_reply",
    "refine_step",
)
