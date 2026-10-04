# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Read what a Planner request shows, then assemble it — once.

``planning/htn/planner_package`` is pure: it shapes rows and bounds the package.  This
module does the reading — the store, the network, the registry, the evidence snapshot,
the obligation ledger — and hands the rows over.  Every row is read here exactly once
and is the row the model sees; there is no intermediate mapping to convert.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .obligation_accounts import account_of, obligation_accounts
from ..contracts.htn import TaskForm
from ..contracts.semantic_base import content_hash_of
from ..planning.htn.planner_package import (
    PlannerPackageError,
    _network_authorities,
    assemble_planner_package,
    fact_rows,
    failure_index,
    failure_outline,
    goal_rows,
    method_rows,
    method_signatures,
    plan_row,
    task_type_row,
)
from ..storage.obligation_store import ObligationStore
from .completion_support import read_completion_support

#: The Planner's own rejected replies, as the store records them.
_PLANNING_REJECTIONS = frozenset({"PlanningRejected"})


def read_planner_package(
    *,
    store: Any,
    mission: Any,
    network: Any,
    world: Any,
    dispatch: Any,
    reports: Sequence[Any],
    budget: Mapping[str, int],
    package_version: int,
    previous_feedback: Any = None,
) -> dict[str, Any]:
    """Everything one hierarchical Planner request carries, read from the live records.

    ``reports`` is the applicability assessment the caller already recorded for this
    request (one read, rendered and recorded); ``budget`` is the planning budget view,
    whose numbers only the event handler can compute.
    """

    from ._read_set import SemanticReadSetChecker
    from .method_plan_reviews import reviews_by_method
    from .planning_graph_repairs import graph_repair_sources
    from .planning_repair_requests import pending_requests, repair_goal_occurrences
    from .planning_selection import candidate_context
    from ..planning.htn.world import catalog_digest
    from .method_library import directory, library_reads

    htn = dispatch.semantics()
    duties = ObligationStore(store)
    under_repair = repair_goal_occurrences(store, network)
    adopted = set(network.adopted_instance_ids)
    # A decision names a method instance by its full reference quadruple; the digest is
    # the instance's own, stated here because no view row carries it.
    instance_refs = [
        {"kind": "method_instance", "id": str(instance.instance_id),
         "semantic_revision": max(1, int(instance.plan_revision)),
         "content_hash": instance.parameters_digest()}
        for instance in network.method_instances if instance.instance_id in adopted
    ]

    # goals / plans ------------------------------------------------------------------
    task_states: dict[str, dict[str, Any]] = {}
    outcomes = dispatch.occurrence_outcomes(mission.id, network)
    for occurrence in network.occurrences:
        task = store.get_task(str(occurrence.task_id))
        if task is not None and task.mission_id == mission.id:
            task_states[str(occurrence.occurrence_id)] = {
                "task_status": str(task.status), "task_version": task.version,
                "occurrence_outcome": str(outcomes[occurrence.occurrence_id])}
    goals = goal_rows(network, task_states=task_states, under_repair=under_repair)

    # methods ------------------------------------------------------------------------
    choices = candidate_context(dispatch, mission.id, reports)
    methods, omitted_methods = method_rows(
        world.registry, method_signatures(network, under_repair), mission_id=mission.id,
        retired=dispatch.retired_methods(mission.id), reviews=reviews_by_method(store, mission.id),
        reports=reports, schemas=world.schemas,
        first=[candidate["method_id"] for choice in choices for candidate in choice["applicable"]])

    # facts --------------------------------------------------------------------------
    snapshot = world.snapshot()
    now_ms = int(store.now * 1000)

    def state_of(proposition_key: str) -> tuple[str, str]:
        entry = snapshot.lookup(proposition_key)
        if entry is None:
            raise PlannerPackageError("observation is missing from the evidence snapshot")
        return str(entry.availability), str(entry.truth(now_ms=now_ms))

    facts, omitted_facts = fact_rows(
        htn.list_observations(mission.id), state_of=state_of,
        read_item=SemanticReadSetChecker(store, htn, mission_id=mission.id).read_item)

    # obligations --------------------------------------------------------------------
    obligations = []
    duty_refs = []
    spent = obligation_accounts(store, mission.id)  # read-time totals, the duty and all under it
    for duty in duties.list_obligations(mission.id):
        account = duties.account(mission.id, duty.obligation_id)
        totals = account_of(spent, duty.obligation_id)
        obligations.append({
            "parent": None if duty.parent_obligation_id is None else str(duty.parent_obligation_id),
            "demand": {"obligation_id": str(duty.obligation_id), "admitted": account.has_admitted_demand,
                       "lifecycle": str(account.lifecycle), "requiredness": str(duty.requiredness)},
            "fuel": {"limit": account.fuel_limit, "used": account.fuel_used,
                     "remaining": account.remaining_fuel},
            "failures": [{"count": totals["failed_attempts"]}],
            "attempts": totals["attempts"],
            "budget": {"lineage_ref": duty.budget_lineage_ref, "spent_tokens": totals["settled_tokens"]},
        })
        duty_refs.append({"kind": "obligation", "id": str(duty.obligation_id), "semantic_revision": 1,
                          "content_hash": content_hash_of(duty.to_json())})

    # accepted results ---------------------------------------------------------------
    outputs = htn.list_acceptance_outputs(mission.id)
    accepted = []
    counted: dict[str, frozenset[str]] = {}

    def _counted(occurrence_id: str) -> frozenset[str]:
        """The acceptances this step's completion rests on right now (completion reading)."""
        from .completion_status import read_occurrence_completion

        if occurrence_id not in counted:
            try:
                counted[occurrence_id] = frozenset(
                    read_occurrence_completion(store, mission.id, occurrence_id).preparation_acceptance_ids)
            except Exception:  # noqa: BLE001 - unreadable completion counts nothing
                counted[occurrence_id] = frozenset()
        return counted[occurrence_id]
    for acceptance in htn.list_acceptances(mission.id):
        if str(acceptance.validity) != "CURRENT":
            continue
        if read_completion_support(store, mission.id, str(acceptance.acceptance_id)) is None:
            continue
        ports = [row for row in outputs if row.get("acceptance_id") == str(acceptance.acceptance_id)]
        for occurrence in network.occurrences:
            if occurrence.task_id != acceptance.task_id:
                continue
            accepted.append({
                "acceptance_ref": {"kind": "acceptance", "id": str(acceptance.acceptance_id),
                                   "semantic_revision": 1,
                                   "content_hash": content_hash_of(acceptance.to_json())},
                "producer_occurrence": str(occurrence.occurrence_id),
                "ports": ports,
                "artifacts": [item.to_json() for item in acceptance.artifact_refs],
                "currentness": str(acceptance.validity),
                # 阶段 E：这次验收是按第几版要求通过的；在现行计划与现行要求下还算不算数
                #（完成度读取的答案，不另写规则）
                "requirements_revision": int(acceptance.requirements_revision),
                "counts_under_current": str(acceptance.acceptance_id) in _counted(
                    str(occurrence.occurrence_id)),
                "support_revision": acceptance.contract_revision,
                "permitted_uses": ["DATA"] if ports else [],
            })

    # failures: an index.  The record of a step failure is in the pending repair request
    # about it (``repair_requests``), and only there.
    refused: list[tuple[float, dict[str, Any]]] = []
    failed: list[tuple[float, dict[str, Any]]] = []
    for event in store.list_events(mission.id, types=sorted(_PLANNING_REJECTIONS)):
        if event.type in _PLANNING_REJECTIONS:
            refused.append((float(event.created_at), {
                "source": "planning", "reason": str(event.payload.get("reason") or ""),
                "attempt_review_ref": None, "repeat_count": 1, "last_seen": str(event.created_at),
                "findings": [event.payload.get("detail")]}))
    for task in store.list_tasks(mission.id):
        for attempt in store.list_attempts(task.id):
            if attempt.failure is None and not attempt.feedback:
                continue
            outline = failure_outline(attempt.failure)
            failed.append((float(attempt.created_at), {
                "source": "attempt", "reason": str(attempt.status),
                "attempt_review_ref": {"kind": "attempt", "id": attempt.id, "version": attempt.version,
                                       "content_hash": content_hash_of(attempt.to_json())},
                "repeat_count": 1, "last_seen": str(attempt.created_at),
                "findings": [*attempt.feedback, *([outline] if outline else [])]}))

    views = {
        "goals": goals,
        "obligations": obligations,
        "plans": [plan_row(network, method_instance_refs=instance_refs)],
        "methods": methods,
        "facts": facts,
        "accepted_results": accepted,
        "failures": failure_index(attempts=failed, planning=refused),
        "capabilities": [
            {"capability": row.capability_id, "registered": row.registered,
             "configured": row.configured, "reachable": row.reachable, "healthy": row.healthy,
             "authorized": row.authorized, "compatible": row.compatible}
            for row in world.capabilities().records],
        "planning_budgets": [dict(budget)],
        # 全库做法（阶段 C3）：目录只有编号与一句用途；读过的条目带原文，只当先例
        "method_library": directory(store, mission, catalog_digest(world),
                                    method_signatures(network, under_repair)),
        "library_reads": library_reads(store, mission.id),
    }

    # what the Planner is asked about, and what its decisions may name ----------------
    sharing = graph_repair_sources(store, network)
    open_goals = {choice["occurrence_id"] for choice in choices}
    repaired = {str(item) for item in under_repair}
    # A successor replaces a step the plan already holds.  Before the first plan there
    # is none, and the type catalogue says nothing the Planner can use; the types it
    # may build a *method* from are in ``method_proposal_contexts``.
    has_steps = any(spec.form is TaskForm.PRIMITIVE for spec in network.occurrences)
    observers = getattr(world, "observers", None)
    sections = {
        "repair_requests": pending_requests(store, mission.id),
        "human_answers": answered_questions_for_planner(store, mission.id),
        "abandoned_plan_changes": abandoned_plan_changes_for_planner(store, mission.id),
        "method_selection": choices,
        # Written for every goal that has no method yet, however many candidates it
        # has: the Planner may judge at any time that none of them fits and propose one.
        # And for every refined goal a repair request is about (2026-10-03): whether to
        # retry or to replace the method is the Planner's call, and replacing it needs a
        # method to replace it with — the desktop has no seed library to pick from.
        "method_proposal_contexts": [
            {"subject_key": goal["subject_key"],
             "request": dispatch.method_proposal_context(mission.id, goal["task_id"])}
            for goal in goals
            if goal["occurrence_id"] in open_goals or str(goal["occurrence_id"]) in repaired],
        "sharing_candidates": list(sharing),
        "successor_types": ([task_type_row(spec, world.schemas) for spec in world.catalog.task_types()]
                            if has_steps else []),
        "evidence_predicates": [
            signature.to_json() for signature in world.predicates.signatures()
            if observers is not None and observers.observer_for(signature.predicate_ref.id) is not None],
    }
    latest = htn.latest_requirements_revision(mission.id)
    return assemble_planner_package(
        requirements=None if latest is None else {
            "revision": int(latest.revision),
            "criteria": [{"id": str(item.criterion_id), "revision": int(item.revision),
                          "statement": str(item.statement)} for item in latest.criteria]},
        package_version=package_version, mission=mission, network=network, views=views,
        sections=sections, previous_feedback=previous_feedback,
        authorities=[*_network_authorities(network), *duty_refs],
        extra_refs=[*instance_refs, *(ref for row in sharing
                                      for ref in (row["task_ref"], row["resolution_ref"])
                                      if ref is not None)],
        omitted={"methods": omitted_methods, "facts": omitted_facts})


def answered_questions_for_planner(store: Any, mission_id: str) -> list[dict[str, Any]]:
    """规划器能看到的全部已答问题（最近 16 条）。

    2026-10-01（审阅升级具名后续）：此前只列"绑定还是最新"的回答——任务里每写一次库时钟就变，
    答过的问题就从规划器眼前消失，它于是把同一个问题连问 13 次。回答是给这个任务的，一律给看，
    只标明 ``binding_current``。
    """
    from ..storage.planning_human_store import PlanningHumanStore

    questions = PlanningHumanStore(store)
    return [
        {"subject_key": row["subject_key"], "question": row["request"]["payload"]["question"],
         "answer": row["answer"], "binding_current": questions.binding_current(row)}
        for row in questions.list(mission_id) if row["state"] == "ANSWERED"
    ][-16:]


def abandoned_plan_changes_for_planner(store: Any, mission_id: str) -> list[dict[str, Any]]:
    """用户亲手放弃过的改计划（最近 8 条）：哪一个决定、改的是什么、用户的理由原文。

    阶段 B 第 2 条（2026-10-03）：一次改计划卡在半路、用户点"放弃这次改计划"后，旧计划恢复执行；
    不告诉规划器，它会再提同样的改法，来回循环。这里只摆事实，要不要换个改法由它判断。"""
    import json

    from ..storage.planning_decision_store import PlanningDecisionStore

    decisions = PlanningDecisionStore(store)
    rows = []
    for event in store.list_events(mission_id, types=["TaskGraphConvergenceAbandoned"]):
        result = event.payload.get("result") or {}
        row = decisions.get_planning_decision(str(result["decision_id"])) if result.get("decision_id") else None
        decision = {} if row is None else json.loads(row["canonical_json"])
        rows.append({"decision_id": result.get("decision_id"),
                     "decision_type": decision.get("decision_type"), "subject_key": decision.get("subject_key"),
                     "rationale": decision.get("rationale"), "plan_revision": result.get("source_revision"),
                     "reason": event.payload.get("reason") or ""})
    return rows[-8:]


__all__ = ("abandoned_plan_changes_for_planner", "answered_questions_for_planner", "read_planner_package")
