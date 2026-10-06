# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3l / defect N7: a non-root compound in composition_review must form a resolution.

Grok H-L4-M3-r0: a nested compound's leaves were all accepted, the compound entered
``composition_review`` and nothing formed a GoalResolution for it; its successor stayed
``WAITING_ORDER`` and the Mission died ``no_dispatchable_work``.  The composition path
that fixed it reads the children's CURRENT acceptances and asks the (assurance)
composition reviewer.

2026-10-03 A′：世界换成产品同形部署，计划（根做法里的子目标、子目标自己的做法）都由规划器提出、
经独立审阅、采用后提交，执行与验收是主循环真跑。

删除（记偏离）：
* ``linked_assess_by_reading_still_forms_a_resolution``：``product_world/test_sub_goal.py`` 覆盖——子目标的
  组合审阅通过形成目标结论，否则根终审到不了、任务完成不了。
* ``the_fixture_really_parks_the_successor_on_waiting_order``：夹具自检，随夹具消失。
* ``composition_does_not_fill_pass_when_the_linked_leaf_criterion_is_missing``：它测的
  ``CompositionAcceptanceAssembly._child_covers``（程序判子步骤的哪条准则算覆盖了上级准则）已随
  旧审阅路径删除；保证通道下这件事由组合审阅员按准则判，不再有程序兜底（AER I05 的那个
  "任何 PASS 都算覆盖"的回落也就不存在了）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from h1i_seed import CONFIG, run_until

from agent_orchestrator.orchestrator.completion_support import current_child_supports
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.fixtures import package_of, role_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, decision, planner_reply


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _method(context: dict[str, Any], steps: list[tuple[str, str, dict[str, Any]]],
            links: list[tuple[str, int]], finalizer: str, ordering=()) -> dict[str, Any]:
    request = context["request"]
    kinds = {str(item["task_type_ref"]["id"]): item for item in request["operators"]}
    kinds.update({str(item["task_type_ref"]["id"]): item for item in request.get("subgoal_types", ())})
    criteria = [item["id"] for item in request["criterion_evidence"]]
    identity = request["new_method_identity"]
    return {
        "schema_version": 1, "method_id": identity["method_id"], "method_version": identity["method_version"],
        "goal_type_ref": request["goal_type_ref"],
        "parameter_schema_ref": request["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [],
        "steps": [{"local_id": local, "task_type_ref": kinds[kind]["task_type_ref"],
                   "form": "compound" if kind.startswith("sub-goal") else "primitive", "arguments": arguments,
                   "required_capabilities": [] if kind.startswith("sub-goal") else list(kinds[kind]["required_capabilities"]),
                   "obligation_relation": "refines_parent"} for local, kind, arguments in steps],
        "ordering": [{"before": before, "after": after} for before, after in ordering],
        "required_capabilities": [], "expected_effects": [],
        "composition": {
            "criterion_links": [{"parent_criterion_id": criteria[index], "child_step": local,
                                 "child_criterion_id": criteria[index],
                                 "evidence_requirement": f"{local} 完成 {criteria[index]}"} for local, index in links],
            "outputs": {}, "finalizer_step": finalizer, "independent_review_required": True,
        },
        "basis_refs": [],
    }


SUB = {"goal": {"op": "constant", "value": "写出 facts.md 和 reproduce.md"}}


def test_accepted_children_do_not_mix_sibling_acceptances_on_a_shared_duty(tmp_path) -> None:
    """P1-2: CURRENT acceptances are keyed by the child occurrence's own task.

    根做法只有一个子目标（承接两条要求）；子目标的做法是两步（facts、reproduce，各承接一条）。
    facts 做完并验收、reproduce 的执行者调用被扣住时，组合审阅读到的已验收子步骤只有 facts——
    兄弟步骤在同一份义务下的验收不能顶替 reproduce。"""

    def planner(request: Any) -> Any:
        package = package_of(request)
        contexts = package.get("method_proposal_contexts") or []
        if contexts and not (package.get("method_selection") or [{}])[0].get("applicable"):
            goal = str((contexts[0]["request"].get("goal_type_ref") or {}).get("id"))
            if goal == "user-goal":
                method = _method(contexts[0], [("assess", "sub-goal-1", SUB)], [("assess", 0), ("assess", 1)], "assess")
            else:
                method = _method(contexts[0], [("facts", "prepare-delivery", {}), ("reproduce", "prepare-delivery", {})],
                                 [("facts", 0), ("reproduce", 1)], "reproduce")
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                            {"method_proposal": {"method": method, "rationale": goal}}, goal)
        return planner_reply(request)

    class Provider(LayeredScriptedProvider):
        def __init__(self) -> None:
            super().__init__(planner=planner)
            self.reproducing = asyncio.Event()
            self.release_reproduce = asyncio.Event()

        async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
            if role_of(request) == "worker" and "reproduce.md" in json.dumps(
                    package_of(request).get("task_contract", {}).get("outputs") or [], ensure_ascii=False):
                self.reproducing.set()
                await self.release_reproduce.wait()
            return await super().invoke(request, cancel=cancel)

    provider = Provider()

    async def case() -> Any:
        try:
            async with product_world(tmp_path / "root", provider, **{**CONFIG, "max_concurrency": 2,
                                                                       "max_concurrent_model_calls": 2}) as world:
                mission_id = world.create({"goal": "整理两份文件", "idempotency_key": "p23l-n7-siblings",
                                           "success_criteria": ["file:facts.md", "file:reproduce.md"]})["mission_id"]
                store = world.store
                dispatch = world.loop._dispatch_for(mission_id)

                def facts_accepted() -> bool:
                    network = dispatch.network(mission_id)
                    return provider.reproducing.is_set() and any(
                        task.status.value == "COMPLETED" and "facts.md" in " ".join(task.success_criteria)
                        for task in store.list_tasks(mission_id)) and network is not None

                await run_until(world, facts_accepted, timeout=60)
                network = dispatch.network(mission_id)
                assess = next(spec for spec in network.occurrences
                              if str(network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id)
                              == "sub-goal-1")
                children = list(network.adopted_children(assess.occurrence_id))
                by_slot = {child.slot_key: str(child.occurrence_id) for child in children}
                return by_slot, current_child_supports(store, mission_id, children), \
                    HtnStore(store).list_goal_resolutions(mission_id)
        finally:
            provider.release_reproduce.set()

    by_slot, accepted, resolutions = asyncio.run(case())
    assert set(by_slot) == {"facts", "reproduce"}
    assert by_slot["facts"] in accepted
    assert by_slot["reproduce"] not in accepted, (
        f"a sibling's CURRENT acceptance on the shared duty must not stand in for reproduce: {accepted}")
    assert resolutions == (), "no composition resolution while a gating child is unaccepted"


def test_c_composition_without_coverage_does_not_form_accept(tmp_path) -> None:
    """AER I05/I07: a nested compound that declares no criterion and that no parent link names
    proves no contribution to completion.  The plan is refused at commit — it never reaches a
    composition review, let alone an ACCEPT.  The Planner's proposal is reviewed and adopted;
    the commit is what refuses it, with nothing written.

    2026-10-03 A′（偏离，按产品实际）：拒绝现在更早，出在把计划步骤落成任务行时——这个子目标
    没有任何完成条件（§6.3"完成条件不清"），还没到冻结完成范围（原来的
    ``OP_COMPLETION_SCOPE_UNRESOLVED``）。"""

    def planner(request: Any) -> Any:
        package = package_of(request)
        contexts = package.get("method_proposal_contexts") or []
        if (contexts and str((contexts[0]["request"].get("goal_type_ref") or {}).get("id")) == "user-goal"
                and not (package.get("method_selection") or [{}])[0].get("applicable")):
            method = _method(contexts[0], [("assess", "sub-goal-1", SUB), ("revert", "prepare-delivery", {})],
                             [("revert", 0)], "revert", ordering=[("assess", "revert")])
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                            {"method_proposal": {"method": method, "rationale": "子目标不承接要求"}}, "x")
        return planner_reply(request)

    provider = LayeredScriptedProvider(planner=planner)
    provider.held.add("worker")

    async def case() -> Any:
        try:
            async with product_world(tmp_path / "root", provider, **CONFIG) as world:
                mission_id = world.create({"goal": "写出 NOTES.md", "idempotency_key": "p23m-i07-uncovered",
                                           "success_criteria": ["file:NOTES.md"]})["mission_id"]
                store = world.store

                def refused() -> list[Any]:
                    return [event for event in store.list_events(mission_id) if event.type == "PlanningRejected"]

                await run_until(world, lambda: bool(refused()), timeout=60)
                semantics = HtnStore(store)
                packages = [item for item in semantics.list_review_packages(mission_id)
                            if str(item.package_id).startswith("pkg-compose-")]
                return refused(), semantics.active_plan_revision(mission_id), \
                    semantics.list_goal_resolutions(mission_id), packages
        finally:
            provider.release.set()

    refusals, active, resolutions, packages = asyncio.run(case())
    detail = json.dumps(refusals[0].payload, ensure_ascii=False)
    assert refusals[0].payload["reason"] == "proposal_not_grounded"
    # 被拒的位置随产品演进前后移过（10-03 A′ 在落任务行时、之后又回到冻结完成范围时）；要守的是
    # "这个子目标证明不了对完成有贡献，整份计划被拒、什么都不写"，所以认这两种具名原因之一。
    assert ("OP_COMPLETION_SCOPE_UNRESOLVED" in detail and "no provable completion contribution" in detail) \
        or "success_criteria must not be empty" in detail, detail
    assert active is None, "the whole plan is refused"
    assert resolutions == ()
    assert packages == []
