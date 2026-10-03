# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3d / defect D5-B: a nested compound is refined, not left hanging.

``_cycle_inner`` asked the Planner exactly once, while the Mission was CREATED; after
the first ``PlanRevisionCommitted`` a Planner that proposed a *compound* step produced
a plan the system accepted and then could never run (episode H-L4-M3-r2).

HTN 精简片 B（2026-10-01）："计划里有目标还没有做法"由 ``planning_repair_requests.open_goal_triggers``
（经 ``collect_triggers``）记一条通用 ``PlanningRepairRequested``（触发源 ``GOAL_UNREFINED``，
幂等键 ``open-goals:<任务号>:<计划版本号>``），再由 ``_resume_planning_services`` 开一轮规划器。
本文件测的几件事：同一计划版本只问一次、问过的规划轮了结之后不再重开、重启不重问、预算不够时
任务可见地停下（阶段名 ``planning_service_resume``）。

2026-10-03 A′：世界换成产品同形部署。规划器为根目标提一个做法——只有一个复合子目标
（``sub-goal-1``）——经独立审阅、采用并提交（计划第 1 版）；系统随后为这个没有做法的子目标问规划器。
规划器的回答由用例定：扣住（在途）、答"不改"（NO_CHANGE，这一轮了结）。不再手工提交计划、不再
手工把规划意图改成 FAILED。删除：``the_committed_plan_really_holds_an_unrefined_compound``（夹具
自检；它的前提在本文件各用例的开头断言）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, NamedTuple

import pytest
from h1i_seed import CONFIG, run_until

from agent_orchestrator.contracts import MissionStatus, MissionStopReason
from agent_orchestrator.contracts.htn import TaskForm
from agent_orchestrator.orchestrator.planning_repair_requests import REQUESTED, collect_triggers
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import ProductWorld, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, decision, planner_reply


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _root_method(context: dict[str, Any], *, with_leaf: bool) -> dict[str, Any]:
    """根做法：一个复合子目标（``inner``，承接最后一条要求）；``with_leaf`` 时前面再加一个原子步骤
    （承接第一条要求），计划提交后就有可派发的工作（M3-r2 的形状，任务是 ACTIVE）。"""
    request = context["request"]
    part = next(item for item in request["subgoal_types"] if item["task_type_ref"]["id"] == "sub-goal-1")
    criteria = [item["id"] for item in request["criterion_evidence"]]
    identity = request["new_method_identity"]
    steps = [{"local_id": "inner", "task_type_ref": part["task_type_ref"], "form": "compound",
              "arguments": {"goal": {"op": "constant", "value": "写出 NOTES.md，列三条要点"}},
              "required_capabilities": [], "obligation_relation": "refines_parent"}]
    links = [("inner", criteria[-1])]
    if with_leaf:
        leaf = next(item for item in request["operators"] if str(item["task_type_ref"]["id"]).endswith("prepare-delivery"))
        steps.insert(0, {"local_id": "leaf", "task_type_ref": leaf["task_type_ref"], "form": "primitive",
                         "arguments": {}, "required_capabilities": list(leaf["required_capabilities"]),
                         "obligation_relation": "refines_parent"})
        links.insert(0, ("leaf", criteria[0]))
    return {
        "schema_version": 1, "method_id": identity["method_id"], "method_version": identity["method_version"],
        "goal_type_ref": request["goal_type_ref"],
        "parameter_schema_ref": request["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [], "steps": steps, "ordering": [],
        "required_capabilities": [], "expected_effects": [],
        "composition": {
            "criterion_links": [{"parent_criterion_id": criterion, "child_step": local, "child_criterion_id": criterion,
                                 "evidence_requirement": f"{local} 完成 {criterion}"} for local, criterion in links],
            "outputs": {}, "finalizer_step": "inner", "independent_review_required": True,
        },
        "basis_refs": [],
    }


def _no_change(request: Any) -> str:
    """规划器看了这个没有做法的子目标，决定先不改（这一轮了结、计划不动）。"""
    package = package_of(request)
    contexts = package.get("method_proposal_contexts") or []
    subject = contexts[0]["subject_key"] if contexts else package["planning_subjects"][0]["subject_key"]
    return decision(subject, "NO_CHANGE", {"reason": "先不处理这个子目标。"}, "不改。")


class Nested(NamedTuple):
    world: ProductWorld
    mission_id: str
    provider: LayeredScriptedProvider

    @property
    def loop(self) -> Any:
        return self.world.loop

    def open_goal_requests(self) -> list[str]:
        """The requests' keys with the Mission id taken out (``open-goals:<revision>``)."""
        return [str(event.payload.get("source_key")).replace(f"{self.mission_id}:", "")
                for event in self.loop.store.list_events(self.mission_id)
                if event.type == REQUESTED and str(event.payload.get("source_key", "")).startswith("open-goals:")]

    def events(self) -> list[str]:
        return [event.type for event in self.loop.store.list_events(self.mission_id)]

    def request_rounds(self) -> list[str]:
        """Planner rounds opened for a repair request (not the method-review service)."""
        return [str(event.payload["service_id"]) for event in self.loop.store.list_events(self.mission_id)
                if event.type == "PlanningServiceResumed" and event.payload.get("source_type") == REQUESTED]

    def planner_intents(self) -> list[Any]:
        return [item for item in self.loop.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED")
                if item.mission_id == self.mission_id and item.kind == "plan" and ":planner:" in item.intent_id]

    def ask_round(self) -> bool:
        """Record the generic request (if not yet on file), then open a Planner round."""
        collect_triggers(self.loop, self.loop.store.get_mission(self.mission_id))
        return self.loop._resume_planning_services(self.loop.store.get_mission(self.mission_id))


@asynccontextmanager
async def nested(tmp_path: Any, *, key: str, with_leaf: bool = False,
                 sub_goal: Callable[[Any], Any] | None = None, budget: dict[str, Any] | None = None,
                 root: str = "root", **config: Any) -> AsyncIterator[Nested]:
    """计划第 1 版刚提交（根做法里有一个没有做法的复合子目标）。``sub_goal`` 是规划器对子目标那一轮
    的回答；``None`` 表示扣住那次调用（在途）。执行者一律扣住。"""

    calls = {"n": 0}
    holder: dict[str, Any] = {}

    def planner(request: Any) -> Any:
        calls["n"] += 1
        package = package_of(request)
        contexts = package.get("method_proposal_contexts") or []
        if calls["n"] == 1:
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                            {"method_proposal": {"method": _root_method(contexts[0], with_leaf=with_leaf),
                                                 "rationale": "交给一个子目标。"}}, "交给一个子目标。")
        if calls["n"] == 2:
            if sub_goal is None:
                holder["provider"].held.add("planner")
            return planner_reply(request)  # adopt the reviewed root method
        assert sub_goal is not None
        return sub_goal(request)

    provider = LayeredScriptedProvider(planner=planner)
    holder["provider"] = provider
    provider.held.add("worker")
    criteria = ["file:a.md", "file:NOTES.md"] if with_leaf else ["file:NOTES.md"]
    request = {"goal": "写出要求的文件", "idempotency_key": key, "success_criteria": criteria}
    if budget is not None:
        request["budget"] = budget
    try:
        async with product_world(tmp_path / root, provider, **{**CONFIG, **config}) as world:
            mission_id = world.create(request)["mission_id"]
            semantics = HtnStore(world.store)
            await run_until(world, lambda: semantics.active_plan_revision(mission_id) is not None)
            network = world.loop._dispatch_for(mission_id).network(mission_id)
            unrefined = [str(spec.task_id) for spec in network.occurrences
                         if spec.form is TaskForm.COMPOUND and network.adopted_instance_for(spec.occurrence_id) is None]
            assert len(unrefined) == 1, unrefined  # the shape of the defect, asserted rather than assumed
            yield Nested(world, mission_id, provider)
    finally:
        provider.release.set()


def test_an_unrefined_nested_compound_reopens_the_planner(tmp_path) -> None:
    """**Mutation**: drop ``open_goal_triggers`` from ``collect_triggers`` and M3-r2 comes back.

    片 B：问规划器的是一条通用请求（``open-goals:1``），不再是专用入口；那一轮的规划器调用被扣住。
    """

    async def case():
        async with nested(tmp_path, key="p23d-nested") as world:
            # a plan whose only step is an unrefined compound commits nothing to dispatch
            assert world.loop.store.get_mission(world.mission_id).status is MissionStatus.PLANNING
            await run_until(world.world, lambda: world.provider.asked.count("planner") >= 3)
            return world.open_goal_requests(), world.events(), world.planner_intents(), world.request_rounds()

    requests, events, intents, rounds = asyncio.run(case())
    assert requests == ["open-goals:1"]
    assert len(rounds) == 1
    assert "HierarchicalRefinementRequested" not in events
    assert [item.config["ordinal"] for item in intents] == [3]  # propose, adopt, then this round


def test_the_same_revision_is_not_put_to_the_planner_twice(tmp_path) -> None:
    """One round per plan revision, so a Planner that cannot refine is asked once — while the
    round is in flight, and (review P1-3 / mutation M11) after it has settled too: the
    Planner answers NO_CHANGE, the round settles, and asking again opens nothing."""

    async def case():
        async with nested(tmp_path, key="p23d-nested-once") as world:
            await run_until(world.world, lambda: world.provider.asked.count("planner") >= 3)
            in_flight = [world.ask_round(), world.ask_round()]
            return in_flight, world.open_goal_requests(), len(world.request_rounds())

    in_flight, requests, resumed = asyncio.run(case())
    assert in_flight == [False, False]
    assert requests == ["open-goals:1"]
    assert resumed == 1

    async def settled():
        async with nested(tmp_path, key="p23d-nested-settled", sub_goal=_no_change, root="settled") as world:
            loop = world.loop
            await run_until(world.world, lambda: any(
                event.type == "PlanningDecisionEvaluated" and event.payload.get("decision_type") == "NO_CHANGE"
                for event in loop.store.list_events(world.mission_id)))
            again = [world.ask_round(), world.ask_round()]
            return again, world.open_goal_requests(), world.request_rounds(), world.planner_intents()

    again, requests, services, intents = asyncio.run(settled())
    assert again == [False, False]
    assert requests == ["open-goals:1"]
    assert len(services) == 1, "the settled round is not reopened for the same revision"
    assert intents == []


def test_a_fully_refined_plan_asks_for_nothing(tmp_path) -> None:
    """The other half: an ordinary one-level plan opens no extra Planner round at all."""

    from h1i_seed import committed

    async def case():
        async with committed(tmp_path, key="p23d-nested-flat") as seed:
            mission = seed.loop.store.get_mission(seed.mission.id)
            recorded = collect_triggers(seed.loop, mission)
            opened = [seed.loop._resume_planning_services(seed.loop.store.get_mission(seed.mission.id))
                      for _ in range(2)]
            requests = [event for event in seed.loop.store.list_events(seed.mission.id)
                        if event.type == REQUESTED and str(event.payload.get("source_key", "")).startswith("open-goals:")]
            return recorded, opened, requests

    recorded, opened, requests = asyncio.run(case())
    assert recorded is False
    assert opened == [False, False]
    assert requests == []


def test_a_restarted_process_does_not_ask_the_same_revision_again(tmp_path) -> None:
    """Review P2-5: the bound must survive the process that set it.  The round for revision 1
    was opened (its call held); a brand new Orchestrator over the same library neither records
    a second request nor opens a second round."""

    async def case():
        async with nested(tmp_path, key="p23d-nested-restart") as world:
            await run_until(world.world, lambda: world.provider.asked.count("planner") >= 3)
            before = world.planner_intents()
        provider = LayeredScriptedProvider()
        provider.held.update({"planner", "worker"})
        try:
            async with product_world(tmp_path / "root", provider, **CONFIG) as second:
                mission = second.store.get_mission(world.mission_id)
                recorded_again = collect_triggers(second.loop, mission)
                again = second.loop._resume_planning_services(second.store.get_mission(world.mission_id))
                resumed = sum(1 for e in second.store.list_events(world.mission_id)
                              if e.type == "PlanningServiceResumed" and e.payload.get("source_type") == REQUESTED)
                requests = [str(e.payload.get("source_key")) for e in second.store.list_events(world.mission_id)
                            if e.type == REQUESTED and str(e.payload.get("source_key", "")).startswith("open-goals:")]
                return before, recorded_again, again, requests, resumed, \
                    second.loop._next_planning_ordinal(world.mission_id)
        finally:
            provider.release.set()

    before, recorded_again, again, requests, resumed, next_ordinal = asyncio.run(case())
    assert len(before) == 1
    assert recorded_again is False, "the request for this revision is already on file"
    assert again is False, "the revision was already put to the Planner"
    assert len(requests) == 1
    assert resumed == 1
    assert next_ordinal == 4


@pytest.mark.parametrize(("with_leaf", "max_tokens", "reserve"), [(False, 20_400, 20_000), (True, 700_000, 500_000)],
                         ids=["planning", "active"])
def test_a_refinement_round_that_cannot_be_funded_stops_the_mission_visibly(
        tmp_path, with_leaf: bool, max_tokens: int, reserve: int) -> None:
    """Review P0-1 / verification P2-C: ``BudgetExhausted`` used to escape ``run()`` with a
    traceback.  The round for the unrefined sub-goal is reserved against the Mission account
    when it is opened; a Mission that cannot cover it is stopped visibly (``budget_exhausted``,
    phase ``planning_service_resume``) and no Planner round is opened.

    预算（测试计数器一词一个，脚本化的每次回复实际花 150）：
    * ``planning``：根做法只有那个复合子目标，提交后没有可派发的工作，任务还在 PLANNING。规划轮的
      预留设成 20000、审阅起始预留 100，任务额度 20400 够前两轮规划与做法审阅，第三轮开的时候只剩
      19950——经规划失败那条路停下（任务从没进入 ACTIVE）。
    * ``active``：根做法里多一个原子步骤，提交后它先被派发（执行者被扣住，占着它那份额度），任务是
      ACTIVE（M3-r2 的形状）；规划轮预留 500000 付不起——停的是任务、不是规划，在途的工作被停掉。
    """

    async def case():
        async with nested(tmp_path, key=f"p23d-nested-broke-{with_leaf}", with_leaf=with_leaf,
                          sub_goal=_no_change, budget={"max_tokens": max_tokens, "max_attempts": 12},
                          planner_reserve_tokens=reserve, critic_reserve_tokens=100) as world:
            loop = world.loop
            await run_until(world.world, lambda: str(loop.store.get_mission(world.mission_id).status.value)
                            in {"FAILED", "COMPLETED", "CANCELLED"})
            mission = loop.store.get_mission(world.mission_id)
            attempts = [attempt for task in loop.store.list_tasks(world.mission_id)
                        for attempt in loop.store.list_attempts(task.id)]
            return (mission, world.open_goal_requests(), world.events(), world.request_rounds(),
                    world.planner_intents(), attempts)

    mission, requests, events, rounds, intents, attempts = asyncio.run(case())
    assert requests == ["open-goals:1"]
    assert rounds == [], "an unfundable round is not opened"
    assert not intents, "no Planner round was dispatched"
    assert mission.status is MissionStatus.FAILED
    assert mission.stop_reason == str(MissionStopReason.BUDGET_EXHAUSTED), mission.final_report
    assert mission.final_report["detail"]["phase"] == "planning_service_resume"
    assert "MissionFailed" in events
    if with_leaf:
        assert "planning_failure" not in mission.final_report, "it did not fail at planning"
        assert attempts and all(str(attempt.status.value) == "CANCELLED" for attempt in attempts), \
            "the work it had open is stopped"
    else:
        assert mission.final_report["planning_failure"]["reason"] == "budget_exhausted"
        assert attempts == []
