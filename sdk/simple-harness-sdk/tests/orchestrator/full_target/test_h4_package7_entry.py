"""Package 7 enters the real collector without upgrading frozen older Missions.

All on the product's deployment and its main loop (``h1i_seed``): the planner proposes a
two-step chain (``write`` delivers, ``continue`` consumes that delivery), the method is
independently reviewed and adopted; ``write``'s result is sent back by its reviewer, and
the planner's repair round replaces that unaccepted step with a successor that keeps its
duty.  Both decisions go through the production collector.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from h1i_seed import CONFIG, events, run_until
from taskgraph_exec.production_fixture import CHAIN_CRITERIA, chain_planner

from agent_orchestrator.runtime.role_templates import (
    PLANNER_HIERARCHICAL,
    PLANNING_DECISION_PACKAGE_VERSION,
)
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    review_input,
    review_reply,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


@pytest.mark.parametrize("drop_version", [False, True])
def test_package7_initial_refinement_reaches_atomic_commit(tmp_path, drop_version):
    seen: dict[str, Any] = {"rework": False, "requests": []}

    def reviewer(request: Any) -> str:
        package = review_input(request)
        assert package is not None
        if (package.get("package") or {}).get("purpose") == "TASK_CONTENT" and not seen["rework"]:
            seen["rework"] = True  # the first step's first result is sent back
            return review_reply(package, verdict="REWORK", grade="FAIL", reason="要点太笼统，请写具体。")
        return review_reply(package)

    def planner(request: Any) -> Any:
        package = package_of(request)
        seen["requests"].append(request)
        if not package.get("repair_requests"):
            return chain_planner(request)
        # The repair round: replace the step that was sent back, keeping its duty.
        world = seen["world"]
        dispatch = world.loop._dispatch_for(seen["mission_id"])
        network = dispatch.network(seen["mission_id"])
        seen["before"] = network
        old = next(binding for binding in network.task_bindings
                   if str(binding.form) == "primitive" and not binding.input_ports)
        seen["old"] = old
        task_type = next(spec for spec in dispatch.planning.catalog.task_types()
                         if spec.goal_signature == old.goal_signature)
        subject = next(row for row in package["planning_subjects"] if row["task_id"] == str(old.task_id))

        def visible(kind: str, identity: str) -> dict[str, Any]:
            return next(row for row in package["visible_refs"] if row["kind"] == kind and row["id"] == identity)

        body = {"schema_version": 1, "decision_type": "REPAIR", "subject_key": subject["subject_key"],
            "rationale": "Replace this unaccepted Task while retaining its duty.", "reason_refs": [], "assumptions": [],
            "uncertainties": [], "alternatives": [], "replan_triggers": [],
            "payload": {"repair_kind": "PROPOSE_SUCCESSOR", "old_task_ref": visible("task", str(old.task_id)),
                "obligation_ref": visible("obligation", str(old.obligation_id)),
                "goal_type_ref": task_type.task_type_ref.to_json(),
                # The step type's parameter schema requires its goal; the plan's own step
                # carries none (reported: the materialised step's bindings are empty).
                "bindings": {**dict(old.typed_parameters), "goal": "写出 facts.md，三条具体的要点"}}}
        if drop_version:
            # 2026-09-30 无损补齐：只缺 version、id + content_hash 在 successor_types 里唯一对上。
            del body["payload"]["goal_type_ref"]["version"]
        seen["successor_request"] = request
        return "<planning_decision>" + json.dumps(body) + "</planning_decision>"

    async def case():
        provider = LayeredScriptedProvider(planner=planner, reviewer=reviewer)
        try:
            async with product_world(tmp_path / "root", provider, **CONFIG) as world:
                created = world.create({"goal": "先写 facts.md，再接着写 NOTES.md",
                                        "idempotency_key": f"h4-package7-{drop_version}",
                                        "success_criteria": list(CHAIN_CRITERIA)})
                loop = world.loop
                mission = loop.store.get_mission(created["mission_id"])
                seen.update(world=world, mission_id=mission.id)
                dispatch = loop._dispatch_for(mission.id)

                def successor_settled() -> bool:
                    rows = [e for e in events(loop, mission.id, "PlanningDecisionEvaluated")
                            if e.payload.get("decision_type") == "REPAIR"]
                    if rows and rows[-1].payload["status"] == "COMMITTED":
                        provider.held.update({"worker", "planner"})  # nothing else needs to run
                        return True
                    return False

                await run_until(world, successor_settled, timeout=60)

                adoption = [e.payload for e in events(loop, mission.id, "PlanningDecisionEvaluated")
                            if e.payload.get("decision_type") == "REFINE"]
                assert [row["status"] for row in adoption] == ["COMMITTED"]
                first_package = package_of(seen["requests"][0])
                assert first_package["package_version"] == PLANNING_DECISION_PACKAGE_VERSION
                assert {"REPAIR"}.issubset(
                    first_package["planning_protocol"]["enabled_decision_types"])
                assert {"REBIND_INPUT", "CANCEL_BRANCH", "PROPOSE_SUCCESSOR"}.issubset(
                    first_package["planning_protocol"]["enabled_repair_kinds"])
                intent_id = next(e.payload["request_id"] for e in events(loop, mission.id, "PlanningDecisionEvaluated")
                                 if e.payload.get("decision_type") == "REPAIR")
                assert loop.store.get_intent(intent_id).config["prompt_version"] == PLANNER_HIERARCHICAL.prompt_version
                stored = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(intent_id, 0)
                assert stored is not None and stored["status"] == "COMMITTED", stored
                assert int(dispatch.network(mission.id).plan_revision) == 2
                old = seen["old"]
                _assert_successor_convergence(seen["before"], dispatch.network(mission.id), old)
                evaluated = [event.payload for event in events(loop, mission.id, "PlanningDecisionEvaluated")
                             if event.payload.get("request_id") == intent_id]
                assert [row.get("autofilled", []) for row in evaluated] == (
                    [["/payload/goal_type_ref/version"]] if drop_version else [[]])
                assert old.task_id not in {spec.task_id for spec in dispatch.network(mission.id).occurrences}
                from agent_orchestrator.orchestrator.planning_repair_requests import (
                    collect_triggers,
                    pending_requests,
                )
                collect_triggers(loop, loop.store.get_mission(mission.id))
                assert not pending_requests(loop.store, mission.id)
        finally:
            provider.release.set()

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
