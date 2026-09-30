"""Package 7 enters the real collector without upgrading frozen older Missions."""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.role_templates import PLANNER_HIERARCHICAL_V13, PLANNING_DECISION_PACKAGE_LABEL
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from agent_orchestrator.testing.fixtures import RoleScriptedProvider
from test_h1i_production_entry import _config, _seed_new_protocol, _refine_reply


@pytest.mark.parametrize("drop_version", [False, True])
def test_package7_initial_refinement_reaches_atomic_commit(tmp_path, drop_version):
    async def case():
        async with Orchestrator(_config(tmp_path), RoleScriptedProvider({"planner": []})) as loop:
            mission, _world, _binding, dispatch = _seed_new_protocol(loop, tmp_path, key="h4-package7")
            intent = await loop._create_planner_intent(mission.id, ordinal=1)
            assert intent.config["prompt_version"] == PLANNER_HIERARCHICAL_V13.prompt_version
            package = intent.config["planning_package"]
            assert package["package_version"] == PLANNING_DECISION_PACKAGE_LABEL
            assert {"REPAIR", "BIND_EXISTING_GOAL"}.issubset(package["planning_protocol"]["enabled_decision_types"])
            assert {"REBIND_INPUT", "CANCEL_BRANCH", "PROPOSE_SUCCESSOR"}.issubset(
                package["planning_protocol"]["enabled_repair_kinds"])
            PlanningAuthorizationApi(loop.store, tenant_id=mission.tenant_id, principal=Principal(loop._owner)).issue(
                mission.id, command_id="grant-h4-entry", request_id=intent.intent_id)
            await loop._collect_plan_decision(intent, object(), mission, _refine_reply(package), dispatch)
            decision = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(intent.intent_id, 0)
            assert decision is not None and decision["status"] == "COMMITTED", None if decision is None else (decision["status"], decision["detail_json"])
            assert int(dispatch.network(mission.id).plan_revision) == 1
            network = dispatch.network(mission.id)
            old = next(binding for binding in network.task_bindings
                       if str(binding.form) == "primitive" and not binding.input_ports)
            task_type = next(spec for spec in _world.catalog.task_types() if spec.goal_signature == old.goal_signature)
            next_intent = await loop._create_planner_intent(mission.id, ordinal=2)
            next_package = next_intent.config["planning_package"]
            subject = next(row for row in next_package["planning_subjects"] if row["task_id"] == str(old.task_id))
            def visible(kind, identity):
                return next(row for row in next_package["visible_refs"] if row["kind"] == kind and row["id"] == identity)
            body = {"schema_version": 1, "decision_type": "REPAIR", "subject_key": subject["subject_key"],
                "rationale": "Replace this unaccepted Task while retaining its duty.", "reason_refs": [], "assumptions": [],
                "uncertainties": [], "alternatives": [], "replan_triggers": [],
                "payload": {"repair_kind": "PROPOSE_SUCCESSOR", "old_task_ref": visible("task", str(old.task_id)),
                    "obligation_ref": visible("obligation", str(old.obligation_id)),
                    "goal_type_ref": task_type.task_type_ref.to_json(), "bindings": dict(old.typed_parameters)}}
            if drop_version:
                # 2026-09-30 无损补齐：只缺 version、id + content_hash 在 successor_types 里唯一对上。
                del body["payload"]["goal_type_ref"]["version"]
            PlanningAuthorizationApi(loop.store, tenant_id=mission.tenant_id, principal=Principal(loop._owner)).issue(
                mission.id, command_id="grant-h4-successor", request_id=next_intent.intent_id)
            await loop._collect_plan_decision(next_intent, object(), loop.store.get_mission(mission.id),
                "<planning_decision>" + json.dumps(body) + "</planning_decision>", dispatch)
            stored = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(next_intent.intent_id, 0)
            assert stored is not None and stored["status"] == "COMMITTED", None if stored is None else (stored["status"], stored["detail_json"])
            assert int(dispatch.network(mission.id).plan_revision) == 2
            _assert_successor_convergence(network, dispatch.network(mission.id), old)
            evaluated = [event.payload for event in loop.store.list_events(mission.id)
                         if event.type == "PlanningDecisionEvaluated"
                         and event.payload.get("request_id") == next_intent.intent_id]
            assert [row.get("autofilled", []) for row in evaluated] == (
                [["/payload/goal_type_ref/version"]] if drop_version else [[]])
            assert old.task_id not in {spec.task_id for spec in dispatch.network(mission.id).occurrences}
            from agent_orchestrator.orchestrator.planning_repair_requests import collect_triggers, pending_requests
            collect_triggers(loop, loop.store.get_mission(mission.id))
            assert not pending_requests(loop.store, mission.id)
    asyncio.run(case())


def _assert_successor_convergence(before_network, after_network, old):
    """2026-09-30 结构修复真机第 3 局：后继步骤换掉第一步后，第二步（只被同一父目标需要）的
    输入改指新第一步，TaskGraph 收敛检查却把它当成"共享产出被悄悄改义"整轮拒绝。
    只被这一个父目标（换了新方法实例）需要的步骤不是共享的：它应列为"输入已替换"去重做。
    真正共享（另一个仍在的使用方也要它）时照旧拒绝。"""
    from dataclasses import replace as _replace
    from agent_orchestrator.contracts.semantic_base import TypedRef, TypedRefKind
    from agent_orchestrator.graph.convergence import compute_convergence_impact
    from agent_orchestrator.graph.network_codec import encode

    requirements = TypedRef(TypedRefKind.REQUIREMENTS, "req", 1, "a" * 64)
    before = encode(before_network, requirements)
    candidate = encode(after_network, requirements)
    impact = compute_convergence_impact(before, candidate)
    kinds = {target.task_id: target.target_kind for target in impact.targets}
    assert kinds[str(old.task_id)] == "RETIRING"
    consumers = {str(edge.consumer_occurrence) for edge in before_network.data_requirements
                 if str(edge.producer_occurrence) in {str(spec.occurrence_id) for spec in before_network.occurrences
                                                      if spec.task_id == old.task_id}}
    assert consumers and all(
        kinds.get(str(spec.task_id)) == "INPUT_REPLACED"
        for spec in before_network.occurrences if str(spec.occurrence_id) in consumers)
    # The same step demanded through a sharing policy is shared: changing its input is refused.
    from agent_orchestrator.contracts.htn import ReusePolicy

    def shared(network):
        return _replace(network, method_instances=tuple(
            _replace(item, child_bindings=tuple(
                _replace(child, reuse_policy=ReusePolicy.SHARE_ACTIVE)
                if str(child.goal_occurrence_id or child.occurrence_id) in consumers else child
                for child in item.child_bindings))
            for item in network.method_instances))
    with pytest.raises(Exception, match="TASKGRAPH_SHARED_PRODUCER_BINDING_CHANGED"):
        compute_convergence_impact(encode(shared(before_network), requirements),
                                   encode(shared(after_network), requirements))
