# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3d / defect D5-B: a nested compound is refined, not left hanging.

``_cycle_inner`` asked the Planner exactly once, while the Mission was CREATED:

    for mission in self._active_missions():
        if mission.status is MissionStatus.CREATED:
            await self._start_planning(mission)

After the first ``PlanRevisionCommitted`` the Mission is ACTIVE and ``begin_planning``
— whose only caller is ``_start_planning`` — was never reached again.  §6.3's
decomposition is recursive, so a Planner that proposed a *compound* step produced a
plan the system accepted, recorded as ``CompoundPhaseChanged{planning_ready,
NEEDS_REFINEMENT}``, and then could never run: the compound's primitives stayed in
``WAITING_ORDER`` and the Mission stopped with ``hierarchical_no_dispatchable_work``.
That is episode H-L4-M3-r2 of the Grok acceptance run, and it is also the reason no
plan in the whole run was deeper than one level.

Why refinement rather than refusing the proposal at ``plan_commits``: the two-level
plan is *correct*, and the machinery that compiles it — ``coverage_from_slots`` re-deriving
round one's claims so "a second refinement round" can compile at all — was built for
exactly this.  Refusing it would make the honest failure permanent instead of making
the plan run.  The bound needs no counter: one round per plan revision, so a
refinement that succeeds moves the revision on and one that does not leaves the
Mission to its ordinary stall.

HTN 精简片 B（2026-10-01）：主循环里的专用入口 ``_refine_open_compounds`` 已删除。
"计划里有目标还没有做法"现在由 ``planning_repair_requests.open_goal_triggers``（经
``collect_triggers``）记一条通用 ``PlanningRepairRequested``（触发源 ``GOAL_UNREFINED``，
幂等键 ``open-goals:<任务号>:<计划版本号>``），再由 ``_resume_planning_services`` 开一轮规划器。
本文件测的几件事不变，只是改走这条路：同一计划版本只问一次、重启不重问、预算不够时
任务可见地停下（阶段名 ``planning_service_resume``）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

_HTN_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "htn"
if str(_HTN_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_HTN_FIXTURES))

from htn_world import method, param, step  # noqa: E402
from test_htn_end_to_end import (  # noqa: E402
    HIERARCHICAL_SEMANTICS,
    ROOT_DUTY,
    ROOT_TASK,
    World,
    build_world,
)

from agent_orchestrator.contracts import MissionStatus, MissionStopReason  # noqa: E402
from agent_orchestrator.contracts.htn import TaskForm  # noqa: E402
from agent_orchestrator.graph.eligibility import ReadinessReason  # noqa: E402
from agent_orchestrator.orchestrator.event_handler import Orchestrator  # noqa: E402
from agent_orchestrator.orchestrator.planning_repair_requests import (  # noqa: E402
    REQUESTED,
    collect_triggers,
)
from agent_orchestrator.runtime.assembly import OrchestratorConfig  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.testing.fixtures import (  # noqa: E402
    RoleScriptedProvider,
)
from scripted_plans import apply_scripted_plan, plan_revision_proposal_step  # noqa: E402


def _nested_outer():
    """``plan.goal`` → one **compound** step, which is what nothing ever refined."""

    return method(
        "plan.nested",
        "plan.goal",
        parameter_schema="plan.goal.params",
        steps=(
            step(
                "inner",
                "plan.subgoal",
                TaskForm.COMPOUND,
                {"subject": param("subject")},
            ),
        ),
        links=(("c-root", "inner", "c-sub"),),
        finalizer="inner",
    )


def _inner_method():
    return method(
        "plan.inner",
        "plan.subgoal",
        parameter_schema="plan.subgoal.params",
        steps=(
            step(
                "leaf",
                "plan.leaf",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("plan.read",),
            ),
        ),
        links=(("c-sub", "leaf", "c-done"),),
        finalizer="leaf",
    )


def _proposal(contract, *, goal_id: str, obligation_id: str, revision: int, proposal_id: str):
    reference = contract.method_ref()
    return plan_revision_proposal_step(
        proposal_id=proposal_id,
        expected_plan_revision=revision,
        read_set=[
            {
                "kind": "method",
                "id": reference.method_id,
                "semantic_revision": reference.version,
                "content_hash": reference.content_hash,
            }
        ],
        operations=[
            {
                "op": "refine",
                "goal_id": goal_id,
                "obligation_id": obligation_id,
                "method_ref": {
                    "id": reference.method_id,
                    "version": reference.version,
                    "content_hash": reference.content_hash,
                },
                "bindings": {},
            }
        ],
    )


def _nested_world(tmp_path, *, key: str, tokens: int | None = None) -> World:
    """A Mission whose committed plan holds one unrefined compound step."""

    evidence = Path(tmp_path) / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    world = build_world(
        evidence,
        key=key,
        mode=HIERARCHICAL_SEMANTICS,
        # the refinement entry opens a real Planner request, so the world stays bound
        bound=True,
        **({} if tokens is None else {"tokens": tokens}),
    )
    env = world.env
    env.register_type(
        "plan.subgoal",
        form=TaskForm.COMPOUND,
        parameters=(("subject", "string"),),
        criteria=("c-sub",),
        domain="plan",
    )
    outer, inner = _nested_outer(), _inner_method()
    for contract in (outer, inner):
        receipt = env.admit(contract)
        assert receipt.admitted, receipt.problems
        HtnStore(world.store).register_method(
            contract, env.registry.registration(contract.method_ref())
        )
    outcome = apply_scripted_plan(world.dispatch,
        world.mission.id,
        _proposal(outer, goal_id=ROOT_TASK, obligation_id=ROOT_DUTY, revision=0,
                  proposal_id="prop-outer"),
        principal=world.principal,
        command_id="cmd-outer",
    )
    assert outcome.committed, outcome.last_reason
    world.dispatch.advance_compound_phases(world.mission.id)
    return world


def _ask_round(loop: Orchestrator, mission_id: str) -> bool:
    """The new path's one step: record the generic request, then open a Planner round.

    Returns whether a Planner round was opened (what ``_refine_open_compounds`` used to
    answer).  Recording the request alone is not a round.
    """

    mission = loop.store.get_mission(mission_id)
    assert mission is not None
    collect_triggers(loop, mission)
    return loop._resume_planning_services(loop.store.get_mission(mission_id))


def _open_goal_requests(loop: Orchestrator, mission_id: str) -> list[str]:
    """The requests' keys with the Mission id taken out (``open-goals:<mission>:<revision>``),
    so the assertions below read as "the request for revision N"."""
    return [
        str(event.payload.get("source_key")).replace(f"{mission_id}:", "")
        for event in loop.store.list_events(mission_id)
        if event.type == REQUESTED
        and str(event.payload.get("source_key", "")).startswith("open-goals:")
    ]


def _cycle(
    world: World,
    evidence: Path,
    *,
    rounds: int = 1,
    settle_between: bool = False,
    whole_cycle: bool = False,
) -> dict[str, Any]:
    """``whole_cycle`` runs ``loop._cycle()`` instead of the two calls, for the cases
    whose ending (a budget stop) is decided by ``_cycle_inner``'s own ``except``."""

    config = OrchestratorConfig(
        evidence_root=evidence, max_concurrency=1, test_timeout_seconds=5,
    )

    async def case() -> dict[str, Any]:
        async with Orchestrator(config, RoleScriptedProvider({"planner": []})) as loop:
            world.env.semantics = HtnStore(loop.store)
            loop.install_hierarchical(planning=world.env)
            moved: list[bool] = []
            for _ in range(rounds):
                if whole_cycle:
                    moved.append(await loop._cycle())
                else:
                    moved.append(_ask_round(loop, world.mission.id))
                if settle_between:
                    # Review P1-3: the guard under test is the *per-revision* mark, and
                    # while the first round's intent is still PENDING the separate
                    # "one question at a time" check hides it.  Settling the intent is
                    # what the real loop does the moment the Planner replies.
                    for item in loop.store.list_intents(
                        "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED"
                    ):
                        if item.mission_id == world.mission.id and item.kind == "plan":
                            loop._settle_intent(item, "FAILED")
            final = loop.store.get_mission(world.mission.id)
            return {
                "moved": moved,
                "status": final.status,
                "stop_reason": final.stop_reason,
                "report": dict(final.final_report or {}),
                "next_ordinal": loop._next_planning_ordinal(world.mission.id),
                "events": [item.type for item in loop.store.list_events(world.mission.id)],
                "open_goal_requests": _open_goal_requests(loop, world.mission.id),
                "intents": [
                    item
                    for item in loop.store.list_intents(
                        "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED"
                    )
                    if item.mission_id == world.mission.id and item.kind == "plan"
                ],
            }

    return asyncio.run(case())


@pytest.fixture
def nested(tmp_path):
    world = _nested_world(tmp_path, key="p23d-nested")
    path = Path(tmp_path) / "evidence"
    world.store.close()
    return world, path


def test_the_committed_plan_really_holds_an_unrefined_compound(tmp_path) -> None:
    """The fixture is the shape of the defect, asserted rather than assumed.

    ``ReadinessReason.NEEDS_REFINEMENT`` is **not** the test: §18.5 constraint 4 makes
    every compound answer that whether or not it has been refined, so a refined root
    reports it too.  "Nobody refined this" is ``adopted_instance_for(...) is None``,
    which is the same test ``goals_needing_method`` applies.
    """

    world = _nested_world(tmp_path, key="p23d-nested-shape")
    network = world.dispatch.network(world.mission.id)
    unrefined = [
        str(spec.task_id)
        for spec in network.occurrences
        if spec.form is TaskForm.COMPOUND
        and network.adopted_instance_for(spec.occurrence_id) is None
    ]
    assert len(unrefined) == 1 and unrefined[0] != ROOT_TASK, unrefined
    assert ReadinessReason.NEEDS_REFINEMENT in (
        world.dispatch.read(world.mission.id).planning_frontier.by_reason
    )
    # A plan whose only step is an unrefined compound commits no dispatchable work, so
    # the Mission has not been activated — which is why the branch may not be gated on
    # ACTIVE.  The M3-r2 episode had other dispatchable leaves and was ACTIVE; both
    # shapes hang for the same reason and both have to be reopened.
    assert world.store.get_mission(world.mission.id).status is MissionStatus.PLANNING


def test_an_unrefined_nested_compound_reopens_the_planner(nested) -> None:
    """**Mutation**: drop ``open_goal_triggers`` from ``collect_triggers`` and M3-r2 comes back.

    片 B：问规划器的是一条通用请求（``open-goals:1``），不再是专用入口。
    """

    world, evidence = nested
    outcome = _cycle(world, evidence)
    assert outcome["moved"] == [True]
    assert outcome["open_goal_requests"] == ["open-goals:1"]
    assert "PlanningServiceResumed" in outcome["events"]
    assert "HierarchicalRefinementRequested" not in outcome["events"]
    assert [item.config["ordinal"] for item in outcome["intents"]] == [1]


def test_the_same_revision_is_not_put_to_the_planner_twice(nested) -> None:
    """One round per plan revision, so a Planner that cannot refine is asked once."""

    world, evidence = nested
    outcome = _cycle(world, evidence, rounds=3)
    assert outcome["moved"] == [True, False, False]
    assert outcome["open_goal_requests"] == ["open-goals:1"]
    assert outcome["events"].count("PlanningServiceResumed") == 1
    assert len(outcome["intents"]) == 1


def test_a_fully_refined_plan_asks_for_nothing(tmp_path) -> None:
    """The other half: an ordinary flat plan opens no extra Planner round at all."""

    from test_htn_end_to_end import committed

    evidence = Path(tmp_path) / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    world = committed(evidence, key="p23d-nested-flat", demand=True)
    world.store.close()
    outcome = _cycle(world, evidence, rounds=2)
    assert outcome["moved"] == [False, False]
    assert outcome["open_goal_requests"] == []
    assert outcome["intents"] == []


# ======================================================================================
# review P1-3 / P0-1: the guard and the failure mode the first round of tests missed
# ======================================================================================


def test_the_revision_is_not_reopened_once_the_first_round_has_settled(tmp_path) -> None:
    """The per-revision mark, with the "one question at a time" check taken away.

    **Mutation M11** (survived the first round of tests): in the old entry, delete the
    per-revision mark.  ``test_the_same_revision_is_not_put_to_the_planner_twice``
    stayed green because its three calls all happen while the first round's intent is
    still PENDING, so the *other* guard answered.  In the real loop the intent settles
    the moment the Planner replies — and without the mark the same revision is then
    asked again every cycle, an unbounded Planner loop held back only by the budget.

    片 B：这个"标记"现在是两件落库的事实——幂等键 ``open-goals:<版本>`` 的请求只记
    一条，``PlanningServiceResumed`` 只为它开一轮。
    """

    world = _nested_world(tmp_path, key="p23d-nested-settled")
    evidence = Path(tmp_path) / "evidence"
    world.store.close()
    outcome = _cycle(world, evidence, rounds=3, settle_between=True)
    assert outcome["moved"] == [True, False, False]
    assert outcome["open_goal_requests"] == ["open-goals:1"]
    assert outcome["events"].count("PlanningServiceResumed") == 1
    # One *created* intent even though every round found the compound still unrefined.
    assert outcome["next_ordinal"] == 2


def test_a_refinement_round_that_cannot_be_funded_stops_the_mission_visibly(tmp_path) -> None:
    """Review P0-1: ``BudgetExhausted`` used to escape ``run()`` with a traceback.

    ``_create_planner_intent`` reserves ``planner_reserve_tokens`` against the Mission
    account, and a Mission whose account cannot cover it raises ``BudgetExhausted`` —
    a ``StoreError`` that ``_cycle`` does not forgive and ``run()`` does not catch.
    ``_start_planning`` has caught it since step 6; the two rounds P2.3d added had not,
    so the runner's episode died with a traceback, no ``MissionFailed`` and an
    unreadable ``mission_status``.

    片 B：规划轮由 ``_resume_planning_services`` 开，预算不够由 ``_cycle_inner`` 的
    ``except BudgetExhausted`` 接住，所以这里跑整轮 ``_cycle``。停下任务本身算这一轮有进展，
    "没开出规划轮"改由"没有 ``PlanningServiceResumed``、没有规划意图"来断言。
    """

    world = _nested_world(tmp_path, key="p23d-nested-broke", tokens=100)
    evidence = Path(tmp_path) / "evidence"
    world.store.close()
    outcome = _cycle(world, evidence, whole_cycle=True)
    assert outcome["open_goal_requests"] == ["open-goals:1"]
    assert "PlanningServiceResumed" not in outcome["events"], "an unfundable round is not opened"
    assert outcome["status"] is MissionStatus.FAILED
    assert outcome["stop_reason"] == str(MissionStopReason.BUDGET_EXHAUSTED)
    assert not outcome["intents"], "nothing was dispatched"


def test_a_restarted_process_does_not_ask_the_same_revision_again(tmp_path) -> None:
    """Review P2-5: the bound must survive the process that set it.

    片 B：边界现在全在库里（``open-goals:<版本>`` 请求 + ``PlanningServiceResumed``），
    新进程读同一个库既不再记第二条请求，也不再开第二轮。

    (Old wording) ``self._refinement_rounds`` was a dict on the Orchestrator instance.  The runner
    gives each episode its own process, but a crash-and-resume — or a second instance
    over the same library — would find the mark gone and spend another Planner round on
    a question that was already asked.  The bound is a fact about the *plan revision*,
    so it belongs where the plan revision does.
    """

    world = _nested_world(tmp_path, key="p23d-nested-restart")
    evidence = Path(tmp_path) / "evidence"
    world.store.close()
    config = OrchestratorConfig(
        evidence_root=evidence, max_concurrency=1, test_timeout_seconds=5,
    )

    async def case() -> dict[str, Any]:
        async with Orchestrator(config, RoleScriptedProvider({"planner": []})) as first:
            world.env.semantics = HtnStore(first.store)
            first.install_hierarchical(planning=world.env)
            asked = _ask_round(first, world.mission.id)
            for item in first.store.list_intents(
                "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED"
            ):
                if item.mission_id == world.mission.id and item.kind == "plan":
                    first._settle_intent(item, "FAILED")
        # A brand new Orchestrator over the same library: nothing in memory carries over.
        async with Orchestrator(config, RoleScriptedProvider({"planner": []})) as second:
            world.env.semantics = HtnStore(second.store)
            second.install_hierarchical(planning=world.env)
            mission = second.store.get_mission(world.mission.id)
            recorded_again = collect_triggers(second, mission)
            again = second._resume_planning_services(second.store.get_mission(world.mission.id))
            events = [item.type for item in second.store.list_events(world.mission.id)]
            return {
                "asked": asked,
                "recorded_again": recorded_again,
                "again": again,
                "open_goal_requests": _open_goal_requests(second, world.mission.id),
                "resumed": events.count("PlanningServiceResumed"),
                "next_ordinal": second._next_planning_ordinal(world.mission.id),
            }

    outcome = asyncio.run(case())
    assert outcome["asked"] is True
    assert outcome["recorded_again"] is False, "the request for this revision is already on file"
    assert outcome["again"] is False, "the revision was already put to the Planner"
    assert outcome["open_goal_requests"] == ["open-goals:1"]
    assert outcome["resumed"] == 1
    assert outcome["next_ordinal"] == 2


def _mixed_outer():
    """One primitive step **and** one unrefined compound: the M3-r2 shape.

    The plan has dispatchable work, so the Mission is ACTIVE — which is the state the
    ``_nested_outer`` fixture cannot reach (a plan whose only step is an unrefined
    compound commits nothing to dispatch and leaves the Mission in PLANNING) and which
    is the one production is normally in.  Verification P2-C: without it, the
    ``fail_mission`` half of ``_stop_planning_round`` is exercised only from D5-A.
    """

    return method(
        "plan.mixed",
        "plan.goal",
        parameter_schema="plan.goal.params",
        steps=(
            step(
                "leaf",
                "plan.leaf",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("plan.read",),
            ),
            step(
                "inner",
                "plan.subgoal",
                TaskForm.COMPOUND,
                {"subject": param("subject")},
            ),
        ),
        links=(("c-root", "inner", "c-sub"),),
        finalizer="inner",
    )


def _mixed_world(tmp_path, *, key: str, tokens: int | None = None) -> World:
    evidence = Path(tmp_path) / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    world = build_world(
        evidence,
        key=key,
        mode=HIERARCHICAL_SEMANTICS,
        # the refinement entry opens a real Planner request, so the world stays bound
        bound=True,
        **({} if tokens is None else {"tokens": tokens}),
    )
    env = world.env
    env.register_type(
        "plan.subgoal",
        form=TaskForm.COMPOUND,
        parameters=(("subject", "string"),),
        criteria=("c-sub",),
        domain="plan",
    )
    outer, inner = _mixed_outer(), _inner_method()
    for contract in (outer, inner):
        receipt = env.admit(contract)
        assert receipt.admitted, receipt.problems
        HtnStore(world.store).register_method(
            contract, env.registry.registration(contract.method_ref())
        )
    outcome = apply_scripted_plan(world.dispatch,
        world.mission.id,
        _proposal(
            outer, goal_id=ROOT_TASK, obligation_id=ROOT_DUTY, revision=0,
            proposal_id="prop-mixed",
        ),
        principal=world.principal,
        command_id="cmd-mixed",
    )
    assert outcome.committed, outcome.last_reason
    world.dispatch.advance_compound_phases(world.mission.id)
    return world


def test_an_active_mission_that_cannot_fund_a_refinement_round_fails_as_a_mission(
    tmp_path,
) -> None:
    """Verification P2-C: the ``fail_mission`` half, from D5-B's own side.

    The ``tokens=100`` test above uses a plan whose only step is the unrefined compound,
    so its Mission never left PLANNING and it went out through ``fail_planning`` — the
    right ending for that shape, but not the shape production is usually in.  With a
    dispatchable leaf beside the compound the Mission is ACTIVE, and then a refinement
    round it cannot fund must end the *Mission*, not file a planning failure against a
    Mission that was planned, and must stop the work it has open.
    """

    world = _mixed_world(tmp_path, key="p23d-mixed-broke", tokens=100)
    evidence = Path(tmp_path) / "evidence"
    assert world.store.get_mission(world.mission.id).status is MissionStatus.ACTIVE
    world.store.close()
    outcome = _cycle(world, evidence, whole_cycle=True)
    assert outcome["open_goal_requests"] == ["open-goals:1"]
    assert "PlanningServiceResumed" not in outcome["events"], "an unfundable round is not opened"
    assert not outcome["intents"], "nothing was dispatched"
    assert outcome["status"] is MissionStatus.FAILED
    assert outcome["stop_reason"] == str(MissionStopReason.BUDGET_EXHAUSTED)
    assert "planning_failure" not in outcome["report"], "it did not fail at planning"
    # 片 B：规划轮由通用的 ``_resume_planning_services`` 开，阶段名随之改为它的。
    assert outcome["report"]["detail"]["phase"] == "planning_service_resume"
    assert "MissionFailed" in outcome["events"]
