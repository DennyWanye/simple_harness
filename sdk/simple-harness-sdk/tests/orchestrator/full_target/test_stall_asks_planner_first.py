# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""停滞：先记录、再多看一轮确认、判停之前先问规划器一次（HTN 精简 片 D 第 1 项；P2.3c 2c/2d）。

真机里按"没有可派发的工作"失败的有 21 局，是第一大失败类；判停之前从没问过规划器。
"计划卡住了要不要改"是判断，归规划器；程序这边只有秩序：

* 停滞照旧先记录、再多看一轮确认（世界动了就不算）；
* 确认之后不直接判失败，而是记一条通用修复请求（触发源 ``NO_DISPATCHABLE_WORK``），请求里
  只有事实——哪些步骤被哪道闸挡住、哪些要求还欠着——由规划器决定改计划、问用户还是别的；
* 每个计划版本只问一次：同一版计划问过之后又停在原地，才按"没有可派发的工作"停，并在停机
  报告里写明问过、规划器那一轮有没有开出来；
* 计划换了版本，旧版本的这条请求由系统了结，不让它指着已经不存在的局面。

2026-10-03 A′：停滞由产品自己造出（原来靠手搭世界里"需求没准入"造 NOT_SELECTED，产品会自动
准入需求，造不出来）。产品同形部署上的一个任务：唯一一步做完、被验收，最终审查的审阅员两次回复
的调用一直没回来（重切次数用完）。2026-10-03 阶段 C：回复回来了但不能用，改为记成判不下来并问人，
不再是停滞。这时没有一步可派发、也没有在等
任何东西。规划器被问到时答 NO_CHANGE（"不改"）。原 ``test_htn_end_to_end.py`` §18 停滞组并入本文件。

删除（偏离分诊表，记录在案）：
* ``an_occurrence_that_was_admitted_and_never_ran_is_named_in_the_record``：原用例手工准入需求造
  "放行了却没派发"；产品自动准入需求，放行而没派发只在并发/预算占满时出现，那时有在途工作、
  不会判停滞，产品同形世界造不出。
* ``a_confirmation_that_moves_the_world_lets_the_run_carry_on``（手工准入需求让世界在确认中动）
  并入 ``a_stall_that_the_confirm_cycle_clears_does_not_fail_the_mission``：两条守同一个比较。
* §18 两种许可并存 / 同主体冲突 / 同键第二意见 / 无规划世界不发许可（前提与观测通道，产品
  ``observers=()``）：按分诊裁决⑥，等阶段 D 带观察器的 world_factory，本轮删。
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, NamedTuple

import pytest
from h1i_seed import CONFIG

from agent_orchestrator.contracts import MissionStatus
from agent_orchestrator.contracts.htn import ObligationId
from agent_orchestrator.orchestrator import planning_repair_requests as requests
from agent_orchestrator.orchestrator.hierarchical_dispatch import MISSION_STALLED
from agent_orchestrator.planning.htn.repair_adapter import RepairEventAdapter
from agent_orchestrator.planning.htn.repair_decision import RepairTriggerSource
from agent_orchestrator.runtime.role_templates import PLANNER_HIERARCHICAL
from agent_orchestrator.storage.obligation_store import ObligationStore
from agent_orchestrator.orchestrator.root_review import ROOT_REVIEW_CUT_BUDGET_SPENT
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import ProductWorld, product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    planner_reply,
    review_input,
    review_reply,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


# ======================================================================================
# The stalled world
# ======================================================================================


def final_review_never_answers(request: Any) -> Any:
    """The reviewer: every review passes, except the Mission's final review, whose every call
    comes back too late (see ``_FinalReviewLate``) — so it ends without a verdict, and re-cutting it runs out too.
    (A reply that comes back unusable is no longer a stall: it is on record as inconclusive
    and the person rules — 阶段 C 第 3 条, tests/orchestrator/product_world/test_review_no_verdict.py.)"""
    data = review_input(request)
    if data is None:
        return None
    return review_reply(data)


class _FinalReviewLate(LayeredScriptedProvider):
    """Every call of the Mission's final review comes back after its turn deadline (a late
    reply is never used).  Late rather than held: a held call would keep the scripted
    model's slot and the Planner's stall turn could not be sent."""

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        data = review_input(request)
        if data is not None and str((data.get("package") or {}).get("purpose")) == "MISSION_FINAL":
            await asyncio.sleep(TURN_DEADLINE * 2)
        return await super().invoke(request, cancel=cancel)


def stall_request(request: Any) -> dict[str, Any] | None:
    package = package_of(request)
    return next((entry for entry in package.get("repair_requests") or ()
                 if entry["request"]["trigger_source"] == "NO_DISPATCHABLE_WORK"), None)


def planner_answering(on_stall: Callable[[Any, dict[str, Any]], Any]) -> Callable[[Any], Any]:
    def planner(request: Any) -> Any:
        asked = stall_request(request)
        return planner_reply(request) if asked is None else on_stall(request, asked)

    return planner


def no_change(request: Any, asked: dict[str, Any]) -> str:
    """The Planner looks at the stall and decides to change nothing."""
    package = package_of(request)
    root = package["planning_subjects"][0]["subject_key"]
    return decision(root, "NO_CHANGE", {"reason": "这一版计划没有可改的地方。"}, "不改计划。")


TURN_DEADLINE = 1.0


def _cut_budget_spent(loop: Any, mission_id: str) -> bool:
    return any(event.type == ROOT_REVIEW_CUT_BUDGET_SPENT for event in loop.store.list_events(mission_id))


class Stalled(NamedTuple):
    world: ProductWorld
    loop: Any
    mission_id: str
    provider: LayeredScriptedProvider

    def events(self, kind: str | None = None) -> list[Any]:
        return [event for event in self.loop.store.list_events(self.mission_id)
                if kind is None or event.type == kind]

    def mission(self) -> Any:
        return self.loop.store.get_mission(self.mission_id)


@asynccontextmanager
async def stalled(tmp_path: Any, *, key: str,
                  on_stall: Callable[[Any, dict[str, Any]], Any] = no_change) -> AsyncIterator[Stalled]:
    """The Mission's one step is accepted and its final review ended without a verdict; the
    loop is stepped (deployment duties + one cycle, as the product runs a round) up to that
    point and handed over idle: nothing in flight, the stall not yet recorded."""

    provider = _FinalReviewLate(planner=planner_answering(on_stall), reviewer=final_review_never_answers)
    try:
        async with product_world(tmp_path / "root", provider, turn_deadline_seconds=TURN_DEADLINE, **CONFIG) as world:
            loop = world.loop
            mission_id = world.create({"goal": "写一份 NOTES.md，列出三条要点。", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": key})["mission_id"]
            await loop.recover()
            async with asyncio.timeout(60):
                while not (_cut_budget_spent(loop, mission_id) and not loop._has_inflight()):
                    await world.deployment.between_cycles(auto=True)
                    await loop._cycle()
                    await asyncio.sleep(0.01)
            assert loop.store.get_mission(mission_id).status is MissionStatus.ACTIVE
            assert not [event for event in loop.store.list_events(mission_id) if event.type == MISSION_STALLED]
            yield Stalled(world, loop, mission_id, provider)
    finally:
        provider.release.set()


def _stall_requests(events) -> list:
    return [event for event in events if event.type == requests.REQUESTED
            and event.payload["request"]["trigger_source"] == "NO_DISPATCHABLE_WORK"]


# ======================================================================================
# Pure parts
# ======================================================================================


def test_no_dispatchable_work_is_a_trigger_source_of_its_own() -> None:
    assert RepairTriggerSource("NO_DISPATCHABLE_WORK") is RepairTriggerSource.NO_DISPATCHABLE_WORK
    request = RepairEventAdapter.request_from_event(
        {"type": "NoDispatchableWork", "trigger_refs": ("task-1",), "payload": {"context": {}}},
        mission_id="mission-1", plan_revision=3)
    assert request.trigger_source is RepairTriggerSource.NO_DISPATCHABLE_WORK


def test_the_request_key_names_the_mission_and_the_plan_revision() -> None:
    """事件的幂等键全库唯一：键里没有任务号，库里第一个任务占了"第 3 版"之后，别的任务的第 3 版
    请求就写不进去；没有版本号，换了计划之后就不会再问。"""
    assert requests.stalled_key("m1", 3) == "stalled:m1:3"
    assert requests.stalled_key("m2", 3) != requests.stalled_key("m1", 3) != requests.stalled_key("m1", 4)


def test_a_stall_request_of_an_older_plan_revision_is_superseded() -> None:
    pending = [
        {"request_id": "r1", "source_key": requests.stalled_key("m1", 3)},
        {"request_id": "r2", "source_key": requests.stalled_key("m1", 4)},
        {"request_id": "r3", "source_key": requests.open_goals_key("m1", 3)},
        {"request_id": "r4", "source_key": "event:whatever"},
    ]
    assert requests.superseded_revision_requests(pending, "m1", 4) == ["r1", "r3"]


def test_the_prompt_explains_the_request_as_facts() -> None:
    prompt = PLANNER_HIERARCHICAL.instructions
    assert "NO_DISPATCHABLE_WORK" in prompt
    assert "context.withheld" in prompt
    assert "context.final_review" in prompt and "context.composition_review" in prompt


def test_a_review_whose_second_reply_was_refused_counts_as_ended_without_a_verdict() -> None:
    """"审阅用完了"不只是格式重试用完：第二次回复能解码却不能采用（引用了没展示的证据等）
    同样是终局，同样要进停机报告。"""
    import json
    import sqlite3
    from types import SimpleNamespace

    from agent_orchestrator.orchestrator.event_handler import Orchestrator

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE events(mission_id, seq, type, payload_json)")
    connection.execute("CREATE TABLE commit_receipts(commit_id, kind, receipt_json)")
    rows = [
        (1, "AssuranceReviewInterpretationRejected", {"review_key": "assurance-mission-final:k1", "error_code": "UNEXPOSED_EVIDENCE"}),
        (2, "AssuranceReviewImportRejected", {"review_key": "assurance-mission-final:k1", "reason": "UNEXPOSED_EVIDENCE"}),
        (3, "AssuranceReviewImportRejected", {"review_key": "assurance-content:k2", "reason": "FINDING_SCOPE"}),
    ]
    connection.executemany("INSERT INTO events VALUES ('m1', ?, ?, ?)",
                           [(seq, kind, json.dumps(payload)) for seq, kind, payload in rows])
    fake = SimpleNamespace(store=SimpleNamespace(connection=connection, get_receipt=lambda receipt_id: None))
    fake._exhausted_reviews = lambda mission_id, prefix: Orchestrator._exhausted_reviews(fake, mission_id, prefix)
    fake._inconclusive_reviews = lambda mission_id: []
    assert Orchestrator._exhausted_reviews(fake, "m1", "assurance-mission-final:") == [
        {"reason": "UNEXPOSED_EVIDENCE", "review_key": "assurance-mission-final:k1", "interrupted": False}]
    assert Orchestrator._reviews_without_verdict_detail(fake, "m1") == {"final_review": {
        "reason": "UNEXPOSED_EVIDENCE", "review_key": "assurance-mission-final:k1", "interrupted": False}}
    # 片 C 真机第 2 局：中间目标的组合审查两次回复都按格式错误拒收，同样没有结论，同样要说。
    connection.execute("INSERT INTO events VALUES ('m2', 9, 'AssuranceReviewFormatExhausted', ?)",
                       (json.dumps({"review_key": "assurance-composition:k3", "reason": "OBJECT_FIELDS_UNKNOWN"}),))
    assert Orchestrator._reviews_without_verdict_detail(fake, "m2") == {"composition_review": {
        "reason": "OBJECT_FIELDS_UNKNOWN", "review_key": "assurance-composition:k3", "interrupted": False}}


# ======================================================================================
# The record, the confirmation and the stop (P2.3c part 2c / 2d)
# ======================================================================================


def test_a_stall_is_recorded_confirmed_and_stops_the_mission_once_the_planner_changed_nothing(tmp_path) -> None:
    """整条路经 ``run()``：记录停滞（原因随记录走、自带指纹）→ 问规划器一次（它答"不改"）→ 同一版
    计划又停在原地 → 多看一轮确认 → 按"没有可派发的工作"停。停机报告列出每一条被挡的步骤与还欠着的
    义务，写明问过、规划器那一轮开出来了；停机不是完成：没有根结论、没有任务完成、义务没有透支。"""

    async def case():
        async with stalled(tmp_path, key="stall-run") as world:
            await world.loop.run()
            admissions = world.loop._dispatch_for(world.mission_id).admissions(world.mission_id)
            duties = ObligationStore(world.loop.store)
            fuel = [duties.account(world.mission_id, ObligationId(str(duty))).remaining_fuel
                    for duty in duties.obligation_ids(world.mission_id)]
            return world.mission(), world.events(), admissions, fuel, list(world.provider.asked)

    mission, events, admissions, fuel, asked = asyncio.run(case())
    kinds = [item.type for item in events]
    # the record: written down, with its reasons and its own identity, before the verdict
    stalls = [item for item in events if item.type == MISSION_STALLED]
    assert stalls, "the stall is recorded"
    payload = stalls[0].payload
    assert payload["code"] == "hierarchical_no_dispatchable_work"
    assert payload["withheld"] and all(item["reason"] for item in payload["withheld"])
    assert payload["fingerprint"], "the stall carries its own identity (§9.1)"
    assert kinds.index(MISSION_STALLED) < kinds.index("MissionFailed")
    # one stall record per (revision, reasons): asking the Planner did not move the plan
    assert len({item.idempotency_key for item in stalls}) == len(stalls) == 1
    # the Planner was asked once for this plan revision, and its turn was opened
    assert len(_stall_requests(events)) == 1
    assert asked.count("planner") >= 3  # propose, adopt, and the stall question
    # the stop
    assert mission.status is MissionStatus.FAILED
    report = mission.final_report
    assert report["stop_reason"] == "no_dispatchable_work"
    detail = report["detail"]
    assert detail["confirmed_after_one_more_cycle"] is True
    assert detail["planner_asked"]["request_id"] == _stall_requests(events)[0].payload["request_id"]
    assert detail["planner_asked"]["planner_turn_opened"] is True
    assert len(detail["withheld"]) == len(admissions.refusals), "every refusal, not a sample"
    assert all(item["reason"] and item["detail_codes"] for item in detail["withheld"])
    assert detail["outstanding_obligations"], "the duties still owed are named"
    assert all(set(item) >= {"obligation_id", "has_admitted_demand", "remaining_fuel"}
               for item in detail["outstanding_obligations"])
    assert detail["plan_revision"] == int(admissions.plan_revision)
    # §7.4: a bound, not a verdict about the goal
    assert report["stop_reason"] not in {"insufficient_evidence", "mission_criteria_unmet", "planning_failed", "no_progress"}
    # §21.5: a stop is never a completion
    assert "MissionCompleted" not in kinds
    assert "GoalResolutionCommitted" not in kinds, "no root Resolution was formed"
    assert fuel and all(item >= 0 for item in fuel), "no duty is over-drawn at the end"


def test_a_confirmed_stall_asks_the_planner_instead_of_failing(tmp_path) -> None:
    async def case():
        async with stalled(tmp_path, key="stall-ask") as world:
            loop = world.loop
            await loop._record_hierarchical_stall()
            carried = await loop._confirm_and_stop_stalled()
            dispatch = loop._dispatch_for(world.mission_id)
            admissions = dispatch.admissions(world.mission_id)
            tasks = {str(spec.task_id) for spec in dispatch.network(world.mission_id).occurrences}
            return carried, world.mission(), world.events(), admissions, tasks

    carried, mission, events, admissions, tasks = asyncio.run(case())
    assert carried is True, "the loop comes round again so the Planner's turn is opened"
    assert mission.status is MissionStatus.ACTIVE
    [asked] = _stall_requests(events)
    revision = int(admissions.plan_revision)
    assert asked.payload["source_key"] == requests.stalled_key(mission.id, revision)
    context = asked.payload["request"]["context"]
    # facts only: what every gate withheld and which duties are still owed
    assert context["reason"] == "no_dispatchable_work"
    assert context["plan_revision"] == revision
    assert len(context["withheld"]) == len(admissions.refusals)
    assert all(item["reason"] for item in context["withheld"])
    assert context["outstanding_obligations"]
    assert "admitted_not_dispatched" in context
    # 最终审查没给出结论这件事（调用一直没回来、重切次数用完），写进交给规划器的请求里
    assert context["root_review"]["reason"] == "root_review_cut_budget_spent"
    # the request is about the whole plan: a change on any step answers it
    assert tasks <= set(asked.payload["trigger_scope"])
    assert "MissionFailed" not in [event.type for event in events]


def test_the_planner_is_asked_once_per_plan_revision_and_then_the_stall_stops(tmp_path) -> None:
    async def case():
        async with stalled(tmp_path, key="stall-once") as world:
            loop = world.loop
            await loop._record_hierarchical_stall()
            assert await loop._confirm_and_stop_stalled() is True
            # the same plan, the same place: nobody changed anything
            await loop._record_hierarchical_stall()
            carried = await loop._confirm_and_stop_stalled()
            return carried, world.mission(), world.events()

    carried, mission, events = asyncio.run(case())
    assert carried is False
    assert len(_stall_requests(events)) == 1, "one request per plan revision"
    assert mission.status is MissionStatus.FAILED
    assert mission.final_report["stop_reason"] == "no_dispatchable_work"
    asked = mission.final_report["detail"]["planner_asked"]
    assert asked["request_id"] == _stall_requests(events)[0].payload["request_id"]
    # nobody opened the Planner's turn in this direct drive, and the report says so
    assert asked["planner_turn_opened"] is False


def test_the_run_opens_a_planner_turn_for_the_stall(tmp_path) -> None:
    """整条路：停滞 → 请求 → 主循环回头再转一轮 → 规划器那一轮被开出来（建了规划意图、部署自动
    模式签了授权、调用到了规划器——这里扣住那次调用）。任务没有被不问就判失败。"""

    def hold(request: Any, asked: dict[str, Any]) -> Any:
        raise AssertionError("held calls are never answered in this case")

    async def case():
        async with stalled(tmp_path, key="stall-turn", on_stall=hold) as world:
            world.provider.held.add("planner")
            world.provider.entered.clear()
            task = asyncio.create_task(world.loop.run())
            try:
                async with asyncio.timeout(30):
                    await world.provider.entered.wait()
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            intents = [intent for intent in world.loop.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED")
                       if intent.mission_id == world.mission_id and intent.kind == "plan"]
            return world.mission(), world.events(), intents

    mission, events, planner_intents = asyncio.run(case())
    [asked] = _stall_requests(events)
    resumed = [event for event in events if event.type == "PlanningServiceResumed"
               and event.payload["source_type"] == requests.REQUESTED]
    assert [event.payload["service_id"] for event in resumed] == [
        f"{requests.REQUESTED}:{asked.payload['request_id']}"]
    assert [intent.intent_id for intent in planner_intents] == [resumed[0].payload["intent_id"]]
    assert mission.status is MissionStatus.ACTIVE
    assert "MissionFailed" not in [event.type for event in events]


def test_a_stall_that_the_confirm_cycle_clears_does_not_fail_the_mission(tmp_path) -> None:
    """Memo test 1, and decision 2's first mutation self-check (merged with third-round
    review P1-A's "a confirmation that moves the world lets the run carry on").

    §9.1 only licenses a stop on a *repeated* lack of progress, so the confirmation
    **re-reads the world** and compares; it does not act on the verdict it was handed.
    A stall recorded under a world that no longer holds (its recorded fingerprint is not
    the current one) is carried on, not stopped.

    **Mutation**: make ``_confirm_and_stop_stalled`` treat the fingerprint it was
    handed as the current one (skip the re-read) and this goes red.
    """

    async def case():
        async with stalled(tmp_path, key="stall-moved") as world:
            loop = world.loop
            loop._stalled_at[world.mission_id] = "a fingerprint from a world that moved"
            carried = await loop._confirm_and_stop_stalled()
            return carried, world.mission()

    carried, mission = asyncio.run(case())
    assert carried is True, "a moved world asks the loop for another cycle"
    assert mission.status is MissionStatus.ACTIVE, "a changed world is not a confirmed stall"


def test_the_stall_path_stops_after_exactly_one_confirm_cycle(tmp_path) -> None:
    """Memo test 7 — decision 2's second mutation self-check.

    The confirmation is **one** cycle, hard-coded.  A ``while`` here would turn an idle
    loop into a busy one, which is the failure this whole path exists to end.

    **Mutation**: wrap the body of ``_confirm_and_stop_stalled`` in a retry loop and
    the count below stops being 1.
    """

    calls: list[str] = []

    async def case():
        async with stalled(tmp_path, key="stall-one-cycle") as world:
            loop = world.loop
            dispatch = loop._dispatch_for(world.mission_id)
            real = dispatch.admissions

            def counted(mission_id, *args, **kwargs):
                calls.append(str(mission_id))
                return real(mission_id, *args, **kwargs)

            dispatch.admissions = counted  # type: ignore[method-assign]
            loop._stalled_at[world.mission_id] = "not the current fingerprint"
            await loop._confirm_and_stop_stalled()
            return world.mission_id

    mission_id = asyncio.run(case())
    assert calls == [mission_id], "the confirmation reads the admissions once"


def test_run_itself_comes_back_round_after_a_carry_on(tmp_path) -> None:
    """Review round 4, P1-1: P1-A's fix lives in ``run()``, so the test has to too.

    After a confirmation that carried on there must be another ``_cycle``, because that
    cycle is where unblocked work would be dispatched.  The first confirmation is handed
    a recorded fingerprint the world no longer matches (the world moved); the second,
    which moved nothing, ends the run.
    """

    async def case():
        async with stalled(tmp_path, key="stall-carry-on") as world:
            loop = world.loop
            timeline: list[str] = []
            real_cycle = loop._cycle
            real_confirm = loop._confirm_and_stop_stalled

            async def counted_cycle():
                timeline.append("cycle")
                return await real_cycle()

            async def watched_confirm():
                if "carry-on" not in timeline:
                    loop._stalled_at[world.mission_id] = "a world that has moved since"
                carried = await real_confirm()
                timeline.append("carry-on" if carried else "stop")
                return carried

            loop._cycle = counted_cycle  # type: ignore[method-assign]
            loop._confirm_and_stop_stalled = watched_confirm  # type: ignore[method-assign]
            await loop.run(max_cycles=40)
            return timeline

    timeline = asyncio.run(case())
    assert "carry-on" in timeline, "the confirmation asked the loop for another cycle"
    after = timeline[timeline.index("carry-on") + 1:]
    assert "cycle" in after, (
        "run() returned on a carry-on instead of coming back round; the work the "
        "confirmation unblocked would never be dispatched"
    )
    assert timeline[-1] == "stop", "and a later confirmation, which moved nothing, ends it"


def test_the_carry_on_is_bounded_so_a_moving_world_ends_the_run(tmp_path) -> None:
    """The other half of P1-A: carrying on is not a licence to spin.

    Past ``MAX_STALL_CARRY_ONS`` the run ends with the Mission still ACTIVE and its stall
    recorded — an answer to the caller, not a verdict about the Mission.
    """

    from agent_orchestrator.orchestrator.event_handler import MAX_STALL_CARRY_ONS

    async def case():
        async with stalled(tmp_path, key="stall-bounded") as world:
            loop = world.loop
            answers: list[bool] = []
            for _ in range(MAX_STALL_CARRY_ONS + 2):
                loop._stalled_at[world.mission_id] = f"never matches {len(answers)}"
                answers.append(await loop._confirm_and_stop_stalled())
            return answers, world.mission()

    answers, mission = asyncio.run(case())
    assert answers[:MAX_STALL_CARRY_ONS] == [True] * MAX_STALL_CARRY_ONS
    assert not any(answers[MAX_STALL_CARRY_ONS:]), "the carry-on budget runs out"
    assert mission is not None and mission.status is MissionStatus.ACTIVE, (
        "running out of carry-ons ends the run, it does not stop the Mission"
    )


def test_a_request_that_cannot_be_recorded_does_not_escape_the_loop(tmp_path, monkeypatch) -> None:
    """独立核验提醒：记请求时要算影响范围，操作台账读不了会抛 ``SourceUnavailable``（它不是契约
    错误）。接不住的话异常冲出主循环，任务既没问成也不判停。问不成就照旧判停，报告里如实写
    没问过。

    这个世界里没有对外操作、台账没有可改坏的字节，所以"台账读不了"由注入的异常代替（只注入
    读失败，不绕过任何闸门）。"""
    from agent_orchestrator.runtime.planning_operations import SourceUnavailable

    def unreadable(*args, **kwargs):
        raise SourceUnavailable("OPERATION_LEDGER_INCONSISTENT")

    async def case():
        async with stalled(tmp_path, key="stall-unreadable") as world:
            monkeypatch.setattr(requests, "request_planner_for_stall", unreadable)
            loop = world.loop
            await loop._record_hierarchical_stall()
            carried = await loop._confirm_and_stop_stalled()
            return carried, world.mission()

    carried, mission = asyncio.run(case())
    assert carried is False
    assert mission.status is MissionStatus.FAILED
    assert mission.final_report["stop_reason"] == "no_dispatchable_work"
    assert mission.final_report["detail"]["planner_asked"] is None


# ======================================================================================
# 表二 20（2026-10-03 收尾裁决）：改了计划却不派新尝试、又停在原地——问规划器有上限
# ======================================================================================
def test_stall_asks_are_counted_since_the_last_new_attempt() -> None:
    from types import SimpleNamespace as E

    def asked(revision: int) -> Any:
        return E(type=requests.REQUESTED, payload={"source_key": requests.stalled_key("m", revision)})

    other = E(type=requests.REQUESTED, payload={"source_key": "event:x"})
    events = [asked(1), E(type="AttemptCreated", payload={}), asked(2), other, asked(3)]
    store = E(iter_events=lambda mission_id: iter(events))
    assert requests.stall_asks_since_new_work(store, "m") == 2  # the new attempt reset the count


def test_a_stall_asked_about_too_often_without_new_work_stops_without_asking_again(tmp_path, monkeypatch) -> None:
    """计数到上限时：不再记请求、不再问规划器，按"没有可派发的工作"停，详情写明次数与上限。
    （整条"规划器反复提交不派新步骤的改动"的循环在产品同形世界里造价高，这里只证明接线；
    计数本身由上一条证明。）"""
    monkeypatch.setattr(requests, "stall_asks_since_new_work", lambda store, mission_id: 3)

    async def case():
        async with stalled(tmp_path, key="stall-cap") as world:
            loop = world.loop
            await loop._record_hierarchical_stall()
            carried = await loop._confirm_and_stop_stalled()
            return carried, world.mission(), world.events()

    carried, mission, events = asyncio.run(case())
    assert carried is False and not _stall_requests(events)
    assert mission.final_report["stop_reason"] == "no_dispatchable_work"
    detail = mission.final_report["detail"]
    assert (detail["stall_asks_without_new_work"], detail["stall_asks_cap"]) == (3, 3)
