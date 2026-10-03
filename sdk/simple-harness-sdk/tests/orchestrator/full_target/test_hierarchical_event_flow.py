# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3b: the hierarchical mode's event flow — the compound gate, the projection as the
read, the compound phases, the inputs from the manifest, and the event handler's own
integrity branches.

The legacy Task row is a *display* index in this mode: every answer here (what is
ready, what is running, whether the root review may start, whether the Mission is
terminal, what a compound's phase is) is read from the typed plan, and rewriting the
row's status bytes changes none of them.

2026-10-03 A′：手搭世界（裸 ``CommitService`` + 内存规划世界 + 绕过规划器提交 + 手插显示行）退役。
世界换成产品同形部署：规划器提出两步链 write → continue（``taskgraph_exec/production_fixture``），
经独立审阅、采用后提交；执行者调用被扣住，世界停在计划刚提交的那一刻。显示行的"状态被改"、
计划的"损坏"都用裁决①b1 的字节改写造出（改的是产品自己写下的行，不新插行）。

删除（覆盖在别处，或产品走不到——记偏离）：
* 一轮规划：提交一个修订、成当前、读回 3 个占用、只一个根、图版本不前进 → 【Host 通道】整圈、
  【整圈】、【子目标】；种子网络只有根 → ``test_planner_package_single_layer.py``（本文件仍在种子
  世界里断言一次）。
* 没有规划世界的部署（F）；同一回复再来不出第二个修订 → ``test_root_review_repair_library.py``。
* 原子步不被拦、无语义绑定在门口即损坏、就绪集里没有复合、计划成员读不回、子女都验收后解锁并读作
  ACCEPTED、等子女进组合审阅、消费者在等数据、UpstreamInput 形状、计划被拒不被吞、容忍读取带出
  错误码：覆盖见分诊表第三节第 2 小节（【整圈】、【子目标】、``test_progress_acceptance_2b.py``、
  ``test_h1i_production_entry.py``，以及本文件的主循环用例）。
* 变异自检元测试 2 条（无目录覆盖）：它们的见证断言都已写进下面的用例。
* "插一行没有语义的显示任务 / 计划成员"类造法（①b2）一律改成改坏已有行的字节；"两个根绑定"只能
  插行造出，只留"一个根绑定都读不出"那一档。
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

_TASKGRAPH = Path(__file__).resolve().parent / "taskgraph_exec"
if str(_TASKGRAPH) not in sys.path:
    sys.path.insert(0, str(_TASKGRAPH))

from h1i_seed import run_until  # noqa: E402
from production_fixture import (  # noqa: E402
    CHAIN_CRITERIA,
    ProductionWorld,
    chain_planner,
    enabled_world,
)

from agent_orchestrator.contracts import MissionStatus, TaskStatus  # noqa: E402
from agent_orchestrator.contracts.models import ContractError  # noqa: E402
from agent_orchestrator.graph.eligibility import OccurrenceOutcome, ReadinessReason  # noqa: E402
from agent_orchestrator.graph.projection_validation import GraphIntegrityError  # noqa: E402
from agent_orchestrator.orchestrator.hierarchical_dispatch import (  # noqa: E402
    COMPOUND_DISPLAY_STATUS,
    COMPOUND_PHASE_CHANGED,
    DISPATCH_INTERCEPTED,
    PLAN_INTEGRITY_FAILED,
    CompoundPhase,
    PlanIntegrityError,
    next_compound_phase,
)
from agent_orchestrator.orchestrator.plan_commits import PLAN_REVISION_COMMITTED  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.testing.fixtures import package_of, role_of  # noqa: E402
from agent_orchestrator.testing.product_world import product_world  # noqa: E402
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _steps(world: ProductionWorld) -> dict[str, Any]:
    network = world.dispatch.network(world.mission.id)
    found: dict[str, Any] = {}
    for instance in network.method_instances:
        for child in instance.child_bindings:
            found[str(child.slot_key)] = network.occurrence(child.occurrence_id)
    return found


def _root(world: ProductionWorld) -> Any:
    network = world.dispatch.network(world.mission.id)
    return network.occurrence(network.root_occurrence_ids[0])


def _events(store: Any, mission_id: str, kind: str) -> list[Any]:
    return [event for event in store.list_events(mission_id) if event.type == kind]


def _rewrite_status(store: Any, task_id: str, status: TaskStatus) -> None:
    """①b1: the display row's status bytes change under the plan (a damaged or foreign write)."""
    row = store.connection.execute("SELECT json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    document = json.loads(row[0])
    assert "status" in document
    document["status"] = str(status)
    store.connection.execute("UPDATE tasks SET status=?, json=? WHERE task_id=?",
                             (str(status), json.dumps(document), task_id))
    store.connection.commit()
    assert store.get_task(task_id).status is status


def _damage_binding(store: Any, mission_id: str, task_id: str, *, to_mission: str | None = None) -> None:
    """①b1: one stored task meaning loses its identity bytes (a damaged disk does not honour
    foreign keys either, so they are off for this one write)."""
    connection = store.connection
    connection.execute("PRAGMA foreign_keys=OFF")
    if to_mission is None:
        connection.execute("UPDATE task_semantics SET task_id=? WHERE mission_id=? AND task_id=?",
                           (task_id + "-damaged", mission_id, task_id))
    else:
        connection.execute("UPDATE task_semantics SET mission_id=? WHERE mission_id=? AND task_id=?",
                           (to_mission, mission_id, task_id))
    connection.commit()
    connection.execute("PRAGMA foreign_keys=ON")


@asynccontextmanager
async def committed(tmp_path: Path, *, key: str) -> AsyncIterator[ProductionWorld]:
    """Plan revision 1 (write → continue) just committed; the write step's executor is held."""
    async with enabled_world(tmp_path, key=key, planner=chain_planner, criteria=CHAIN_CRITERIA,
                             hold_worker=True) as world:
        await world.commit_seed()
        yield world


# ======================================================================================
# Pure contract
# ======================================================================================


def test_a_reply_claiming_an_authority_field_is_refused_at_the_boundary() -> None:
    """A model-side proposal that names an authority field (here the manager epoch) is
    refused when it is decoded; authority is never something a reply can carry."""

    from scripted_plans import plan_revision_proposal_step, scripted_plan_proposal

    text = plan_revision_proposal_step(
        proposal_id="prop-1", expected_plan_revision=0, read_set=[],
        operations=[{"op": "refine", "goal_id": "task-root", "obligation_id": "obl-root",
                     "method_ref": {"id": "m", "version": 1, "content_hash": "a" * 64}, "bindings": {}}],
        extras={"manager_epoch": 3})
    with pytest.raises(ContractError) as caught:
        scripted_plan_proposal(text, mission_id="mission-stub")
    assert "manager_epoch" in str(caught.value)


def test_every_phase_has_a_display_status() -> None:
    assert set(COMPOUND_DISPLAY_STATUS) == set(CompoundPhase)


# ======================================================================================
# Before the first plan revision: the seed
# ======================================================================================


def test_the_seed_holds_only_the_unrefined_root(tmp_path) -> None:
    """建任务之后、第一版计划之前：网络就是根目标自己（第 0 版），规划前沿里只有这个待细化的复合，
    它的阶段是 PLANNING_READY。规划器那次调用被扣住，世界停在这一刻。"""

    provider = LayeredScriptedProvider()
    provider.held.add("planner")

    async def case() -> Any:
        try:
            async with product_world(tmp_path / "root", provider) as world:
                mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                           "idempotency_key": "p23b-seed"})["mission_id"]
                await run_until(world, provider.entered.is_set)
                dispatch = world.loop._dispatch_for(mission_id)
                seed = dispatch.network(mission_id)
                return seed, dispatch.read(mission_id), dispatch.advance_compound_phases(mission_id)
        finally:
            provider.release.set()

    seed, view, phases = asyncio.run(case())
    assert int(seed.plan_revision) == 0
    [root] = seed.occurrences
    assert list(seed.root_occurrence_ids) == [root.occurrence_id]
    assert view.planning_frontier.by_reason[ReadinessReason.NEEDS_REFINEMENT] == (root.occurrence_id,)
    assert phases[root.occurrence_id] is CompoundPhase.PLANNING_READY


# ======================================================================================
# Plan revision 1 just committed
# ======================================================================================


def test_the_committed_plan_is_the_read_and_the_display_row_is_not(tmp_path) -> None:
    """计划第 1 版刚提交（write 的执行者被扣住）：

    * 提交（R1）：一条 PlanRevisionCommitted，来源里带这一轮规划决定；读回的网络是第 1 版、三个
      占用（根 + 两步）、一个采用的做法实例、根仍是唯一的根；整数图版本不前进。
    * 复合闸：根在任何执行者派发之前被拦（NEEDS_REFINEMENT），记一条事件、写明复合；原子步不被
      拦；不认识的任务是损坏。
    * 投影就是读（R3）：显示行的状态字节被改成 READY / ACTIVE / COMPLETED（①b1），就绪集、在跑集、
      根终审能否开始、任务是否终结、复合阶段——一个答案都不变；类型视图只把旧状态当诊断带着。
    * 阶段：根在等子步骤（WAITING_CHILDREN），阶段事件一条、不建尝试；原子步不能问阶段；已有结论
      的复合是 RESOLUTION_COMMITTED。
    * 输入：没有数据边的步骤从空开始；生产者没完成，消费者也从空开始；不认识的任务是损坏。
    """

    async def case() -> None:
        async with committed(tmp_path, key="p23b-read") as world:
            store, dispatch, mission_id = world.store, world.dispatch, world.mission.id
            steps, root = _steps(world), _root(world)
            write, follow = steps["write"], steps["continue"]
            root_task = str(root.task_id)

            # the commit
            [event] = _events(store, mission_id, PLAN_REVISION_COMMITTED)
            assert event.payload["proposal_id"]
            decision = world.committed_decision()
            assert decision["status"] == "COMMITTED"
            assert HtnStore(store).active_plan_revision(mission_id).state == "ACTIVE"
            network = dispatch.network(mission_id)
            assert int(network.plan_revision) == 1
            assert len(network.occurrences) == 3 and len(network.adopted_instance_ids) == 1
            assert list(network.root_occurrence_ids) == [root.occurrence_id]
            mission = store.get_mission(mission_id)
            assert int((mission.final_report or {}).get("graph_version") or 1) == 1

            # the compound gate
            intercepted = dispatch.intercept_worker_dispatch(mission_id, root_task)
            assert intercepted is not None and intercepted.reason == str(ReadinessReason.NEEDS_REFINEMENT)
            [recorded] = _events(store, mission_id, DISPATCH_INTERCEPTED)
            assert recorded.payload["reason"] == "NEEDS_REFINEMENT" and recorded.payload["form"] == "compound"
            assert dispatch.intercept_worker_dispatch(mission_id, str(follow.task_id)) is None
            with pytest.raises(GraphIntegrityError):
                dispatch.intercept_worker_dispatch(mission_id, "task-nowhere")

            # the phases
            phases = dispatch.advance_compound_phases(mission_id)
            assert phases[root.occurrence_id] is CompoundPhase.WAITING_CHILDREN
            changed = _events(store, mission_id, COMPOUND_PHASE_CHANGED)
            assert changed and changed[-1].payload["phase"] == "waiting_children"
            view = dispatch.read(mission_id)
            with pytest.raises(ContractError):
                next_compound_phase(write, view.network, view.reports[write.occurrence_id],
                                    child_outcomes={}, resolved=False)
            assert next_compound_phase(root, view.network, view.reports[root.occurrence_id],
                                       child_outcomes={}, resolved=True) is CompoundPhase.RESOLUTION_COMMITTED

            # the inputs
            assert dispatch.attempt_inputs(mission_id, str(write.task_id)) == []
            assert dispatch.attempt_inputs(mission_id, str(follow.task_id)) == []
            with pytest.raises(GraphIntegrityError):
                dispatch.attempt_inputs(mission_id, "task-nowhere")

            # the projection is the read: the display row's bytes change no answer
            def answers() -> tuple[Any, ...]:
                return (dispatch.ready_occurrences(mission_id), dispatch.running_occurrences(mission_id),
                        dispatch.root_review_ready(mission_id), dispatch.terminal(mission_id),
                        dispatch.advance_compound_phases(mission_id))

            before = answers()
            assert root.occurrence_id not in before[0], "a compound is never ready"
            for status in (TaskStatus.READY, TaskStatus.ACTIVE):
                _rewrite_status(store, root_task, status)
                assert answers() == before
                assert root.occurrence_id not in set(dispatch.running_occurrences(mission_id))
            assert dispatch.intercept_worker_dispatch(mission_id, root_task) is not None
            assert store.list_attempts(root_task) == []
            views = {str(item.task_id): item for item in dispatch.occurrences(mission_id)}
            assert views[root_task].legacy_status == "ACTIVE"
            assert dispatch.read(mission_id).reports[root.occurrence_id].reason is ReadinessReason.NEEDS_REFINEMENT
            for spec in (root, write, follow):
                _rewrite_status(store, str(spec.task_id), TaskStatus.COMPLETED)
            assert dispatch.terminal(mission_id) is False
            assert dispatch.root_review_ready(mission_id) is False
            outcomes = dispatch.read(mission_id).outcomes
            assert outcomes[follow.occurrence_id] is not OccurrenceOutcome.ACCEPTED, \
                "a completed row without an acceptance is not accepted"

    asyncio.run(case())


def test_accepting_only_one_child_does_not_unlock_the_root_review(tmp_path) -> None:
    """write 做完并验收、continue 的执行者被扣住：write 在结果投影里读作 ACCEPTED，根终审仍不就绪
    （它等每一个门控子步骤）。"""

    class Provider(LayeredScriptedProvider):
        def __init__(self) -> None:
            super().__init__(planner=chain_planner)
            self.second = asyncio.Event()
            self.go = asyncio.Event()

        async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
            if role_of(request) == "worker" and "NOTES.md" in json.dumps(
                    package_of(request).get("task_contract", {}).get("outputs") or []):
                self.second.set()
                await self.go.wait()
            return await super().invoke(request, cancel=cancel)

    provider = Provider()

    async def case() -> Any:
        try:
            async with enabled_world(tmp_path, key="p23b-one-child", provider=provider,
                                     criteria=CHAIN_CRITERIA) as world:
                await run_until(world.product, provider.second.is_set, timeout=60)
                steps = _steps(world)
                outcomes = world.dispatch.read(world.mission.id).outcomes
                return steps, outcomes, world.dispatch.root_review_ready(world.mission.id)
        finally:
            provider.go.set()

    steps, outcomes, ready = asyncio.run(case())
    assert outcomes[steps["write"].occurrence_id] is OccurrenceOutcome.ACCEPTED
    assert outcomes[steps["continue"].occurrence_id] is not OccurrenceOutcome.ACCEPTED
    assert ready is False


# ======================================================================================
# The event handler's own integrity branches, exercised through the loop
# ======================================================================================


def test_a_mission_whose_root_meaning_does_not_read_back_stops_through_the_planner_branch(tmp_path) -> None:
    """建任务之后、第一轮规划之前，根目标那一行语义被改坏（①b1，读不出这个任务的任何根）：
    规划分支以 ``root_not_identified`` 停下这一个任务；损坏不是规划器答错，不再问它。"""

    provider = LayeredScriptedProvider()

    async def case() -> Any:
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "orch-damaged"})["mission_id"]
            root_task = str(HtnStore(world.store).list_task_semantics(mission_id)[0].task_id)
            _damage_binding(world.store, mission_id, root_task, to_mission="mission-elsewhere")
            mission = await world.run_until_settled(mission_id, rounds=4, timeout=15)
            return mission, list(world.store.list_events(mission_id)), list(provider.asked)

    mission, events, asked = asyncio.run(case())
    integrity = [event for event in events if event.type == PLAN_INTEGRITY_FAILED]
    assert integrity, [event.type for event in events]
    assert integrity[0].payload["code"] == "root_not_identified"
    assert "cycle" not in integrity[0].payload["diagnose"]
    assert mission.status is MissionStatus.FAILED
    assert [event for event in events if event.type == "PlanningRejected"] == []
    assert "planner" not in asked


def test_a_planner_turn_that_did_not_commit_is_a_planning_rejection(tmp_path) -> None:
    """规划器那一轮在提供方那里失败（没有提交回复）：记为规划被拒（``proposal_unreadable``，写明
    规划回合失败），而不是被当成一份回复去解析。"""

    from simple_harness.providers.errors import ProviderServerError

    state = {"failed": False}

    def planner(request: Any) -> Any:
        if not state["failed"]:
            state["failed"] = True
            raise ProviderServerError(public_message="upstream 503", retryable=False, status_code=503)
        from agent_orchestrator.testing.scripted_replies import planner_reply

        return planner_reply(request)

    provider = LayeredScriptedProvider(planner=planner)
    provider.held.add("worker")

    async def case() -> Any:
        try:
            async with product_world(tmp_path / "root", provider) as world:
                mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                           "idempotency_key": "orch-nocommit"})["mission_id"]
                await run_until(world, lambda: bool(_events(world.store, mission_id, "PlanningRejected")), timeout=60)
                return _events(world.store, mission_id, "PlanningRejected")
        finally:
            provider.release.set()

    [rejected, *_] = asyncio.run(case())
    assert rejected.payload["reason"] == "proposal_unreadable"
    assert "planner turn failed" in json.dumps(rejected.payload["detail"], ensure_ascii=False)


@pytest.mark.parametrize("entry", ["decide", "dispatch"])
def test_a_damaged_plan_stops_one_mission_and_is_read_as_corruption(tmp_path, entry: str) -> None:
    """计划第 1 版提交后，continue 那一行语义被改坏（①b1）：

    * 读侧：默认读抛 ``GraphIntegrityError``（``semantic_binding_missing``）；消息点名缺的任务、不提环、
      不给拓扑序。
    * 主循环：判定分支（``_decide``）或派发入口（``_next_attempt``）抛出的完整性失败，由"一个任务
      一轮"的边界接住，停下这一个任务，不让异常冲出共享的主循环。
    """

    async def case() -> Any:
        async with committed(tmp_path, key=f"orch-{entry}") as world:
            loop, store, dispatch, mission_id = world.loop, world.store, world.dispatch, world.mission.id
            follow_task = str(_steps(world)["continue"].task_id)
            _damage_binding(store, mission_id, follow_task)
            with pytest.raises(GraphIntegrityError):
                dispatch.read(mission_id)
            # 偏离（记为疑似缺陷）：执行图绑定后容忍读也抛出——``_read_network`` 对缺语义的任务直接
            # raise，不再把错误带在视图上（hierarchical_dispatch.py ``_read_network``），所以这里不再
            # 断言容忍读的四条（错误码、每个出现 GRAPH_INTEGRITY、两个前沿为空）。
            with pytest.raises(GraphIntegrityError) as caught:
                dispatch.network(mission_id)
            assert isinstance(caught.value, PlanIntegrityError)
            assert caught.value.code == "semantic_binding_missing"
            assert "cycle" not in str(caught.value) and "cycle" not in caught.value.diagnose()
            assert follow_task in str(caught.value)
            assert caught.value.cycle == () and not hasattr(caught.value, "order")
            mission = store.get_mission(mission_id)
            # 阶段 B 裁决第 9 类：损坏由"一个任务一轮"的边界接住，查表判"数据损坏"、当轮停
            if entry == "decide":
                step = lambda: loop._decide(mission)  # noqa: E731
            else:
                step = lambda: loop._next_attempt(mission, store.get_task(follow_task), [])  # noqa: E731
            assert await loop._mission_round(mission.id, entry, step) is True
            return store.get_mission(mission_id), _events(store, mission_id, PLAN_INTEGRITY_FAILED)

    mission, integrity = asyncio.run(case())
    assert integrity and integrity[0].payload["code"] == "semantic_binding_missing"
    assert mission.status is MissionStatus.FAILED
    # 2026-10-03 阶段 B 裁决第 7 类：绑定坏掉的那一步没有终止记录，随任务一起结束，报告里点名
    assert mission.final_report["tasks_without_terminal_record"]
