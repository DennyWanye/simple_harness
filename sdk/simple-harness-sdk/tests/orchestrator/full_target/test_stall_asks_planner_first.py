# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""判停之前先问规划器一次（HTN 精简 片 D 第 1 项）。

真机里按"没有可派发的工作"失败的有 21 局，是第一大失败类；判停之前从没问过规划器。
"计划卡住了要不要改"是判断，归规划器；程序这边只有秩序：

* 停滞照旧先记录、再多看一轮确认（世界动了就不算）；
* 确认之后不直接判失败，而是记一条通用修复请求（触发源 ``NO_DISPATCHABLE_WORK``），请求里
  只有事实——哪些步骤被哪道闸挡住、哪些要求还欠着——由规划器决定改计划、问用户还是别的；
* 每个计划版本只问一次：同一版计划问过之后又停在原地，才按"没有可派发的工作"停，并在停机
  报告里写明问过、规划器那一轮有没有开出来；
* 计划换了版本，旧版本的这条请求由系统了结，不让它指着已经不存在的局面。
"""
from __future__ import annotations

import asyncio

from agent_orchestrator.contracts import MissionStatus
from agent_orchestrator.orchestrator import planning_repair_requests as requests
from agent_orchestrator.planning.htn.repair_adapter import RepairEventAdapter
from agent_orchestrator.planning.htn.repair_decision import RepairTriggerSource
from agent_orchestrator.runtime.role_templates import PLANNER_HIERARCHICAL
from test_htn_end_to_end import _install, _stalled


def _stall_requests(events) -> list:
    return [event for event in events if event.type == requests.REQUESTED
            and event.payload["request"]["trigger_source"] == "NO_DISPATCHABLE_WORK"]


def test_no_dispatchable_work_is_a_trigger_source_of_its_own() -> None:
    assert RepairTriggerSource("NO_DISPATCHABLE_WORK") is RepairTriggerSource.NO_DISPATCHABLE_WORK
    request = RepairEventAdapter.request_from_event(
        {"type": "NoDispatchableWork", "trigger_refs": ("task-1",), "payload": {"context": {}}},
        mission_id="mission-1", plan_revision=3)
    assert request.trigger_source is RepairTriggerSource.NO_DISPATCHABLE_WORK


def test_a_confirmed_stall_asks_the_planner_instead_of_failing(tmp_path) -> None:
    world, orchestrator, _ = _stalled(tmp_path)

    async def case():
        async with orchestrator as loop:
            _install(loop, world, planner_asked=False)
            await loop._record_hierarchical_stall()
            carried = await loop._confirm_and_stop_stalled()
            admissions = loop.hierarchical.admissions(world.mission.id)
            tasks = {str(spec.task_id) for spec in loop.hierarchical.network(world.mission.id).occurrences}
            return (carried, loop.store.get_mission(world.mission.id),
                    list(loop.store.list_events(world.mission.id)), admissions, tasks)

    carried, mission, events, admissions, tasks = asyncio.run(case())
    assert carried is True, "the loop comes round again so the Planner's turn is opened"
    assert mission.status is MissionStatus.ACTIVE
    [asked] = _stall_requests(events)
    revision = int(admissions.plan_revision)
    assert asked.payload["source_key"] == requests.stalled_key(world.mission.id, revision)
    context = asked.payload["request"]["context"]
    # facts only: what every gate withheld and which duties are still owed
    assert context["reason"] == "no_dispatchable_work"
    assert context["plan_revision"] == revision
    assert len(context["withheld"]) == len(admissions.refusals)
    assert all(item["reason"] for item in context["withheld"])
    assert context["outstanding_obligations"]
    assert "admitted_not_dispatched" in context
    # the request is about the whole plan: a change on any step answers it
    assert tasks <= set(asked.payload["trigger_scope"])
    assert "MissionFailed" not in [event.type for event in events]


def test_the_planner_is_asked_once_per_plan_revision_and_then_the_stall_stops(tmp_path) -> None:
    world, orchestrator, _ = _stalled(tmp_path)

    async def case():
        async with orchestrator as loop:
            _install(loop, world, planner_asked=False)
            await loop._record_hierarchical_stall()
            assert await loop._confirm_and_stop_stalled() is True
            # the same plan, the same place: the Planner's turn changed nothing
            await loop._record_hierarchical_stall()
            carried = await loop._confirm_and_stop_stalled()
            return (carried, loop.store.get_mission(world.mission.id),
                    list(loop.store.list_events(world.mission.id)))

    carried, mission, events = asyncio.run(case())
    assert carried is False
    assert len(_stall_requests(events)) == 1, "one request per plan revision"
    assert mission.status is MissionStatus.FAILED
    assert mission.final_report["stop_reason"] == "no_dispatchable_work"
    asked = mission.final_report["detail"]["planner_asked"]
    assert asked["request_id"] == _stall_requests(events)[0].payload["request_id"]
    # nobody opened the Planner's turn in this direct drive, and the report says so
    assert asked["planner_turn_opened"] is False


def test_the_stop_report_says_the_planner_turn_was_opened(tmp_path) -> None:
    world, orchestrator, _ = _stalled(tmp_path)

    async def case():
        async with orchestrator as loop:
            _install(loop, world)  # the Planner was asked on this revision and changed nothing
            await loop.run()
            return loop.store.get_mission(world.mission.id), list(loop.store.list_events(world.mission.id))

    mission, events = asyncio.run(case())
    assert mission.final_report["stop_reason"] == "no_dispatchable_work"
    assert mission.final_report["detail"]["planner_asked"]["planner_turn_opened"] is True
    assert len(_stall_requests(events)) == 1


def test_the_run_opens_a_planner_turn_for_the_stall(tmp_path) -> None:
    """整条路：停滞 → 请求 → 主循环回头再转一轮 → 规划器那一轮被开出来（建了规划意图）。
    任务没有被不问就判失败。（这个夹具没有给规划授权，意图停在等授权，模型不会真被调用；
    请求开出规划轮之后规划器如何作答，由"目标还没有做法"那一组测试覆盖，走的是同一条路。）"""
    world, orchestrator, _ = _stalled(tmp_path)

    async def case():
        async with orchestrator as loop:
            _install(loop, world, planner_asked=False)
            await loop.run()
            return (loop.store.get_mission(world.mission.id),
                    list(loop.store.list_events(world.mission.id)),
                    [intent for intent in loop.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED")
                     if intent.mission_id == world.mission.id and intent.kind == "plan"])

    mission, events, planner_intents = asyncio.run(case())
    [asked] = _stall_requests(events)
    resumed = [event for event in events if event.type == "PlanningServiceResumed"
               and event.payload["source_type"] == requests.REQUESTED]
    assert [event.payload["service_id"] for event in resumed] == [
        f"{requests.REQUESTED}:{asked.payload['request_id']}"]
    assert [intent.intent_id for intent in planner_intents] == [resumed[0].payload["intent_id"]]
    assert mission.status is MissionStatus.ACTIVE
    assert "MissionFailed" not in [event.type for event in events]


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


def test_the_request_says_when_the_final_review_ended_without_a_verdict(tmp_path) -> None:
    """片 C 真机第 1 局（2026-10-02）：三步都验收了，最终审查两次回复都因引用了没展示给它的证据
    被拒收，任务停在原地、按"没有可派发的工作"结束，停机报告里一个字没提审查。这个事实要写进
    交给规划器的请求里。"""
    world, orchestrator, _ = _stalled(tmp_path)
    review = {"final_review": {"reason": "UNEXPOSED_EVIDENCE", "review_key": "assurance-mission-final:abc",
                               "interrupted": False}}

    async def case():
        async with orchestrator as loop:
            _install(loop, world, planner_asked=False)
            loop._root_review_stop_detail = lambda mission, new_mode: dict(review)  # type: ignore[method-assign]
            await loop._record_hierarchical_stall()
            await loop._confirm_and_stop_stalled()
            return list(loop.store.list_events(world.mission.id))

    [asked] = _stall_requests(asyncio.run(case()))
    assert asked.payload["request"]["context"]["final_review"] == review["final_review"]


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
    assert Orchestrator._exhausted_reviews(fake, "m1", "assurance-mission-final:") == [
        {"reason": "UNEXPOSED_EVIDENCE", "review_key": "assurance-mission-final:k1", "interrupted": False}]
    assert Orchestrator._final_review_unreadable_detail(fake, "m1") == {"final_review": {
        "reason": "UNEXPOSED_EVIDENCE", "review_key": "assurance-mission-final:k1", "interrupted": False}}
    # 片 C 真机第 2 局：中间目标的组合审查两次回复都按格式错误拒收，同样没有结论，同样要说。
    connection.execute("INSERT INTO events VALUES ('m2', 9, 'AssuranceReviewFormatExhausted', ?)",
                       (json.dumps({"review_key": "assurance-composition:k3", "reason": "OBJECT_FIELDS_UNKNOWN"}),))
    assert Orchestrator._final_review_unreadable_detail(fake, "m2") == {"composition_review": {
        "reason": "OBJECT_FIELDS_UNKNOWN", "review_key": "assurance-composition:k3", "interrupted": False}}


def test_a_request_that_cannot_be_recorded_does_not_escape_the_loop(tmp_path, monkeypatch) -> None:
    """独立核验提醒：记请求时要算影响范围，操作台账读不了会抛 ``SourceUnavailable``（它不是契约
    错误）。接不住的话异常冲出主循环，任务既没问成也不判停。问不成就照旧判停，报告里如实写
    没问过。"""
    from agent_orchestrator.runtime.planning_operations import SourceUnavailable

    world, orchestrator, _ = _stalled(tmp_path)

    def unreadable(*args, **kwargs):
        raise SourceUnavailable("OPERATION_LEDGER_INCONSISTENT")

    monkeypatch.setattr(requests, "request_planner_for_stall", unreadable)

    async def case():
        async with orchestrator as loop:
            _install(loop, world, planner_asked=False)
            await loop._record_hierarchical_stall()
            carried = await loop._confirm_and_stop_stalled()
            return carried, loop.store.get_mission(world.mission.id)

    carried, mission = asyncio.run(case())
    assert carried is False
    assert mission.status is MissionStatus.FAILED
    assert mission.final_report["stop_reason"] == "no_dispatchable_work"
    assert mission.final_report["detail"]["planner_asked"] is None
