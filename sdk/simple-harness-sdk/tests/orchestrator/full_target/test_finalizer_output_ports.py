# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3d / defect D3: the finalizer step's output port is declared, told and enforced.

The Grok acceptance run (H arm, 2026-09-17) lost 10 of 40 episodes here, and every
one of them looked like a success until the last step: the plan committed, all four
leaves ran, ``code_test`` passed, each leaf was accepted — and then the root review
rejected a criterion with "no artifact was delivered on a declared output port".

Three readers shared one rule — "a port is declared when a ``DataRequirement``
consumes it" — and the finalizer step's port, which nothing downstream consumes but
the root's criterion reads, was declared by none of them.  This file is the invariant
that keeps the readers together: a criterion link *is* a consumer, and under the
completion protocol (the only world production runs) every step owes its own required
ports.

2026-10-03 A′：计划由规划器在产品同形部署上提出、经独立审阅、采用后提交（执行者被扣住，这些
断言只需要已提交的计划），几种做法形状参数化。规划世界在通用"用户目标"世界上多登记两个步骤类型
（``probe-step`` 带一个可选端口、``review-step`` 读它）——规划世界本来就是测试替身。

删除（覆盖在别处）：
* ``finalizer_step_is_a_criterion_linked_occurrence`` / ``criterion_linked_finalizer_declares_the_port``
  / ``finalizer_leaf_is_told_about_its_declared_output_port`` / ``finalizer_that_claims_its_port_is_accepted_and_indexed``：
  ``product_world/test_full_circle.py``——脚本化执行者按请求里声明的端口认领，收尾步不声明或认领
  不上就验收不了、到不了完成。
* ``finalizer_that_claims_no_port_is_refused``：``test_unclaimed_port_is_a_rejected_result.py``。
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import replace
from typing import Any

import pytest
from h1i_seed import CONFIG, run_until

from agent_orchestrator.contracts.htn import OccurrenceId
from agent_orchestrator.orchestrator.accepted_outputs import (
    coverage_in_revision,
    criterion_linked_occurrences,
    declared_output_ports,
    output_ports_in_revision,
)
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world, user_goal_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, decision, planner_reply


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


# ======================================================================================
# The planning world: the user-goal world plus a probe step with an optional port
# ======================================================================================


def probe_world(loop: Any, mission: Any) -> Any:
    """``probe-step`` produces ``delivery`` (required), ``note`` (required) and ``aside``
    (optional); ``review-step`` consumes a ``delivery`` and produces ``verdict``."""
    from agent_orchestrator.contracts.htn import GoalSignature, PortSpec, SideEffectKind, TaskForm
    from agent_orchestrator.contracts.semantic_base import VersionedRef, content_hash_of
    from agent_orchestrator.planning.htn.registry import TaskTypeSpec
    from agent_orchestrator.planning.htn.world import capability_records

    world = user_goal_world(loop, mission)
    params = VersionedRef("user.goal-parameters", 1, content_hash_of(
        {"fields": [{"name": "goal", "type": "string", "required": True}]}))
    outputs = VersionedRef("user.workspace-outputs", 1, content_hash_of({"fields": []}))
    content = tuple(f"c-user-{i + 1}" for i in range(len(mission.success_criteria)))
    operator = VersionedRef("user.workspace-worker", 1, content_hash_of({"tools": list(mission.allowed_tools)}))
    shapes = {
        "probe-step": ((), (PortSpec("delivery", outputs), PortSpec("note", outputs),
                            PortSpec("aside", outputs, required=False))),
        "review-step": ((PortSpec("delivery", outputs),), (PortSpec("verdict", outputs),)),
    }
    for name, (inputs, ports) in shapes.items():
        signature = GoalSignature(name, 1, params, outputs, mission.goal, content)
        body = {"name": name, "form": str(TaskForm.PRIMITIVE), "signature": signature.to_json(),
                "ports": [p.to_json() for p in ports], "input_ports": [p.to_json() for p in inputs]}
        world.catalog.register(TaskTypeSpec(
            task_type_ref=VersionedRef(name, 1, content_hash_of(body)), form=TaskForm.PRIMITIVE,
            goal_signature=signature, input_ports=inputs, output_ports=ports, parameter_schema_ref=params,
            output_schema_ref=outputs, operator_ref=operator, required_capabilities=("workspace.prepare",),
            side_effect_kind=SideEffectKind.LOCAL_WRITE, reversible=True, domain="user", refinement_level=None,
        ))
    world.records = capability_records(world.catalog, capability_layers={"workspace.prepare": None}, unauthorized=())
    return world


#: shape → (criteria, steps ``(local, type, arguments, linked criterion index or None)``, finalizer)
SHAPES = {
    # write → continue (DATA on delivery); each step owns one requirement; continue is the finalizer
    "chain": (("file:a.md", "file:b.md"),
              [("write", "prepare-delivery", {}, 0),
               ("continue", "continue-delivery", {"delivery": {"op": "output", "step": "write", "port": "delivery"}}, 1)],
              "continue"),
    # the same, plus an audit step that reads write's delivery and that nothing reads or links
    "unlinked": (("file:a.md", "file:b.md"),
                 [("write", "prepare-delivery", {}, 0),
                  ("continue", "continue-delivery", {"delivery": {"op": "output", "step": "write", "port": "delivery"}}, 1),
                  ("audit", "continue-delivery", {"delivery": {"op": "output", "step": "write", "port": "delivery"}}, None)],
                 "continue"),
    # the criterion link points at probe, which is not the finalizer; review reads probe
    "linked-nonfinal": (("file:a.md",),
                        [("probe", "probe-step", {}, 0),
                         ("review", "review-step", {"delivery": {"op": "output", "step": "probe", "port": "delivery"}}, None)],
                        "review"),
}


def _method(context: dict[str, Any], shape: str) -> dict[str, Any]:
    request = context["request"]
    kinds = {str(item["task_type_ref"]["id"]): item for item in request["operators"]}
    criteria = [item["id"] for item in request["criterion_evidence"]]
    identity = request["new_method_identity"]
    _, steps, finalizer = SHAPES[shape]
    return {
        "schema_version": 1, "method_id": identity["method_id"], "method_version": identity["method_version"],
        "goal_type_ref": request["goal_type_ref"],
        "parameter_schema_ref": request["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [],
        "steps": [{"local_id": local, "task_type_ref": kinds[kind]["task_type_ref"], "form": "primitive",
                   "arguments": arguments, "required_capabilities": list(kinds[kind]["required_capabilities"]),
                   "obligation_relation": "refines_parent"} for local, kind, arguments, _ in steps],
        "ordering": [], "required_capabilities": [], "expected_effects": [],
        "composition": {
            "criterion_links": [
                {"parent_criterion_id": criteria[link], "child_step": local, "child_criterion_id": criteria[link],
                 "evidence_requirement": f"{local} 这一步完成 {criteria[link]}"}
                for local, _, _, link in steps if link is not None],
            "outputs": {}, "finalizer_step": finalizer, "independent_review_required": True,
        },
        "basis_refs": [],
    }


def _committed_plan(tmp_path, shape: str) -> dict[str, Any]:
    """The plan the Planner proposed in ``shape`` is reviewed, adopted and committed by the
    main loop; returns what the three readers say about every step."""

    def planner(request: Any) -> Any:
        package = package_of(request)
        contexts = package.get("method_proposal_contexts") or []
        if contexts and not (package.get("method_selection") or [{}])[0].get("applicable"):
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                            {"method_proposal": {"method": _method(contexts[0], shape), "rationale": shape}}, shape)
        return planner_reply(request)

    provider = LayeredScriptedProvider(planner=planner)
    provider.held.add("worker")
    criteria = SHAPES[shape][0]

    async def case() -> dict[str, Any]:
        try:
            async with product_world(tmp_path / "root", provider, world_factory=probe_world, **CONFIG) as world:
                mission_id = world.create({"goal": "按步骤写出文件", "idempotency_key": f"d3-{shape}",
                                           "success_criteria": list(criteria)})["mission_id"]
                semantics = HtnStore(world.store)
                await run_until(world, lambda: semantics.active_plan_revision(mission_id) is not None)
                dispatch = world.loop._dispatch_for(mission_id)
                network = dispatch.network(mission_id)
                revision = int(semantics.active_plan_revision(mission_id).revision)
                steps: dict[str, dict[str, Any]] = {}
                for instance in network.method_instances:
                    for child in instance.child_bindings:
                        occurrence = OccurrenceId(str(child.occurrence_id))
                        task = str(network.occurrence(occurrence).task_id)
                        steps[str(child.slot_key)] = {
                            "occurrence": occurrence,
                            "bare": dict(declared_output_ports(network, occurrence)),
                            "own": dict(declared_output_ports(network, occurrence, own_ports=True)),
                            "rows": dict(output_ports_in_revision(semantics, mission_id, revision, occurrence, task)),
                            "told": [(item["port"], item["required"])
                                     for item in dispatch.declared_output_ports_for(mission_id, task)],
                            "shuffled": dict(declared_output_ports(
                                replace(network, task_bindings=tuple(reversed(network.task_bindings))), occurrence)),
                            "inputs": {port.port_key: port for port in network.binding_for_occurrence(occurrence).input_ports},
                        }
                linked = criterion_linked_occurrences(coverage_in_revision(semantics, mission_id, revision))
                edges = list(network.data_requirements)
                return {"steps": steps, "linked": linked, "edges": edges}
        finally:
            provider.release.set()

    return asyncio.run(case())


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_production_readers_give_the_same_answer(tmp_path, shape: str) -> None:
    """The network reader (with the step's own ports, as the completion protocol reads it),
    the rows reader and what the leaf is told are one answer, for every step of every shape;
    and the network reader matches bindings by occurrence, not by position (review P2-7).

    **Mutation**: teach any one of them the old "consumed only" rule, or zip bindings with
    occurrences by position, and this goes red.
    """

    plan = _committed_plan(tmp_path, shape)
    for local, step in plan["steps"].items():
        assert step["rows"] == step["own"], (shape, local)
        assert {port for port, _ in step["told"]} == set(step["rows"]), (shape, local)
        assert all(required for _, required in step["told"]), "a told port is an owed port"
        assert step["shuffled"] == step["bare"], (shape, local)
    if shape == "chain":
        write, final = plan["steps"]["write"], plan["steps"]["continue"]
        # a criterion link adds ports; it never relabels one a live edge already declares
        edge = next(item for item in plan["edges"] if item.producer_occurrence == write["occurrence"])
        assert write["bare"]["delivery"].to_json() == edge.schema_ref.to_json()
        # defect D3's core: nothing consumes the finalizer's port; the root criterion reads it
        assert final["occurrence"] in plan["linked"]
        assert set(final["bare"]) == {"delivery"}


def test_a_step_neither_consumed_nor_linked_still_declares_no_port_under_the_bare_rule(tmp_path) -> None:
    """边界：既没被消费也没被链接的一步。"消费或链接才算声明"这条规则本身（不带 ``own_ports``
    的网络读者）照旧不给它端口；但完成协议下（产品唯一的世界）每一步都欠它**自己**声明的必需端口
    （2026-09-29 真机第十二局），所以生产读者告诉这一步的端口恰好是它自己契约里的那个，不多不少。
    验收侧 ``read_review_origin`` 用的也是带自己端口的那一份（两边对不上会连拒四次）。"""

    from agent_orchestrator.orchestrator import taskgraph_review

    audit = _committed_plan(tmp_path, "unlinked")["steps"]["audit"]
    assert audit["bare"] == {}
    assert audit["told"] == [("delivery", True)]
    assert set(audit["rows"]) == {"delivery"} == set(audit["own"])
    assert "own_ports=True" in inspect.getsource(taskgraph_review.read_review_origin)


def test_a_criterion_link_to_a_non_finalizer_step_declares_its_required_ports_only(tmp_path) -> None:
    """The rule is "criterion-linked", not "is the finalizer" (review P2-10); and criterion
    linkage contributes the producer's **required** ports only (review P2-6, mutation M05):
    an optional port is neither told nor enforced."""

    plan = _committed_plan(tmp_path, "linked-nonfinal")
    probe, review = plan["steps"]["probe"], plan["steps"]["review"]
    assert probe["occurrence"] in plan["linked"] and review["occurrence"] not in plan["linked"]
    # linked: the unconsumed required port is owed under the bare rule; the optional one is not
    assert {"delivery", "note"} <= set(probe["bare"]) and "aside" not in probe["bare"]
    assert set(probe["rows"]) == {"delivery", "note"}, "required and linked: owed; optional: not asked for"
    # the finalizer is not criterion-linked here, so under the bare rule its own port is nobody's;
    # under the completion protocol it still owes its own required port
    assert "verdict" not in review["bare"]
    assert set(review["rows"]) == {"verdict"}
