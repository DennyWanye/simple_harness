# SPDX-License-Identifier: Apache-2.0
"""审阅这一侧的唤醒事件只唤醒该唤醒的那一项（推后第 1 批车道 P1a，A25 第 2、3 行；按 P1a 偏差裁决第 3 件调整）。

原计划事件消费表：

* ACTUAL_CHECK_AVAILABLE → REVIEW：工作键是"已有的待办审阅槽"。实际信号是检查绑定写入同一事务里屏障
  触发器写的资料变更事件（``source_table=assurance_check_bindings``）；不另有检查事件，同一次绑定只叫醒
  读方一次。等检查的导入被它唤醒，做完的导入不被唤醒；
* BUSINESS_OR_RUNTIME_SETTLED → REVIEW："新事实可解开原等待的工作"——调用与预算结清唤醒卡在预算上
  的导入，不等它按退避到点。

用产品同形世界走一个内容审阅必检的任务，留下真实的审阅、检查绑定与工作箱；需要"还在等"的那一刻时，
把工作箱里的行按允许的转移摆成在等（外界时机）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _review_world import ReviewScript, quick_waits, reviewed_mission  # noqa: E402

import agent_orchestrator.testing.scripted_replies as scripted  # noqa: E402
from agent_orchestrator.orchestrator.assurance_consumers import (  # noqa: E402
    CLOSEOUT_SOURCE_EVENTS,
    VALIDITY_SOURCE_EVENTS,
)
from agent_orchestrator.orchestrator.assurance_review_consumer import (  # noqa: E402
    AssuranceReviewConsumer,
    WAKE_EVENTS,
)
from agent_orchestrator.orchestrator.assurance_tick import AssuranceTick  # noqa: E402
from agent_orchestrator.orchestrator.commit_service import CommitService  # noqa: E402

FAR = 10**15


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    quick_waits(monkeypatch)
    one_step = scripted.one_step_method

    def own_criterion_step(context):  # type: ignore[no-untyped-def]
        method = one_step(context)
        for link in method["composition"]["criterion_links"]:
            link["child_criterion_id"] = "c-notes-written"
        return method

    monkeypatch.setattr(scripted, "one_step_method", own_criterion_step)


def _imports(store, mission_id):  # type: ignore[no-untyped-def]
    """审阅导入工作：用途 → 工作键。"""
    rows = store.connection.execute(
        "SELECT w.work_key, json_extract(b.binding_json,'$.subject.purpose') FROM assurance_pending_work w "
        "JOIN assurance_review_bindings b ON w.work_key LIKE 'review-import:'||b.review_key||':%' "
        "WHERE w.mission_id=? AND w.consumer='REVIEW'", (mission_id,)).fetchall()
    return {purpose: key for key, purpose in rows}


def _hold(store, mission_id, key, reason):  # type: ignore[no-untyped-def]
    """按工作箱允许的转移把一项做完的导入摆回"在等"：重开（目标升到最新事件）→ 领取 → 等待。"""
    head = store.connection.execute(
        "SELECT event_id, seq FROM events WHERE mission_id=? ORDER BY seq DESC LIMIT 1", (mission_id,)).fetchone()
    where = " WHERE mission_id=? AND consumer='REVIEW' AND work_key=?"
    with store.transaction():
        for sql, args in (
            ("UPDATE assurance_pending_work SET state='PENDING', trigger_event_id=?, target_epoch=?, "
             "row_version=row_version+1", (head[0], head[1])),
            ("UPDATE assurance_pending_work SET state='RUNNING', owner='p1a', lease_until_ms=?, "
             "row_version=row_version+1", (FAR,)),
            ("UPDATE assurance_pending_work SET state='WAITING', owner=NULL, lease_until_ms=NULL, "
             "wait_reason=?, not_before_ms=?, row_version=row_version+1", (reason, FAR)),
        ):
            store.connection.execute(sql + where, (*args, mission_id, key))


def _emit_again(case, event):  # type: ignore[no-untyped-def]
    """同一个写方再写一条同类型、同内容的事件（新序号）：唤醒只看事件类型与它指向的事实。"""
    commit = case.world.loop.commit
    with case.store.transaction():
        return commit._emit(event.type, case.mission_id, key="p1a-again:" + event.id,
                            task_id=event.task_id, attempt_id=event.attempt_id, payload=event.payload)


def test_a_check_binding_wakes_only_the_import_waiting_for_it_through_one_barrier_event(tmp_path, monkeypatch):
    """检查绑定晚到：同一事务里只有一条有效性 / 审阅会读的事件（屏障的资料变更事件），它叫醒在等检查的
    内容审阅导入；已做完的方法审阅导入不被叫醒。"""
    state: dict = {"defer": True, "held": [], "woken": [], "relevant": []}
    bind, prepare, tick = (CommitService.import_assurance_check_locked,
                           AssuranceReviewConsumer.prepare, AssuranceTick.tick)

    def held_bind(self, **command):  # type: ignore[no-untyped-def]
        if state["defer"]:
            state["held"].append((self, command))
            return None
        return bind(self, **command)

    async def watched_prepare(self, claim):  # type: ignore[no-untyped-def]
        result = await prepare(self, claim)
        state.setdefault("consumer", self)
        if getattr(result, "reason", None) == "CHECK_PENDING":
            state["waiting"] = claim.work_key
        return result

    async def timed_tick(self):  # type: ignore[no-untyped-def]
        progressed = await tick(self)
        if state.get("waiting") and state["defer"]:
            state["defer"] = False
            consumer, store = state["consumer"], self.store
            for commit, command in state["held"]:
                head = max(e.seq for e in store.list_events(command["mission_id"]))
                with store.transaction():
                    bind(commit, **command)
                written = [e for e in store.list_events(command["mission_id"]) if e.seq > head]
                # 这一次绑定的事务里，有效性 / 审阅会读的事件恰好一条，就是屏障的资料变更事件
                relevant = [e for e in written if e.type in VALIDITY_SOURCE_EVENTS | WAKE_EVENTS]
                state["relevant"].append([(e.type, e.payload.get("source_table")) for e in relevant])
                for event in relevant:
                    state["woken"].append({t.work_key for t in consumer.classify(event)})
        return progressed

    monkeypatch.setattr(CommitService, "import_assurance_check_locked", held_bind)
    monkeypatch.setattr(AssuranceReviewConsumer, "prepare", watched_prepare)
    monkeypatch.setattr(AssuranceTick, "tick", timed_tick)

    async def run() -> None:
        async with reviewed_mission(tmp_path, ReviewScript()) as case:
            mission = await case.settle()
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            imports = _imports(case.store, case.mission_id)
            assert state["held"] and state["waiting"] == imports["TASK_CONTENT"]
            assert state["relevant"] == [[("AssuranceEvidenceChanged", "assurance_check_bindings")]] * len(state["held"])
            for woken in state["woken"]:
                assert imports["TASK_CONTENT"] in woken
                assert imports["METHOD_PLAN"] not in woken  # 已做完的导入不因晚到的检查重解

    asyncio.run(run())


def test_every_registered_wake_name_has_a_writer_and_no_receipt_kind_is_registered():
    """有效性与审阅登记的每个事件名在源码里都有写方（写事件的调用或屏障触发器）；检查导入的两个回执
    种类名不是事件，不得登记（P1a 偏差裁决第 3 件）。"""
    import ast
    import re

    import agent_orchestrator

    root = Path(agent_orchestrator.__file__).parent
    written: set[str] = set()
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None) or ""
                if "emit" in name.lower() and node.args and isinstance(node.args[0], ast.Constant):
                    written.add(str(node.args[0].value))
    sql = "\n".join(path.read_text(encoding="utf-8") for path in [*root.rglob("*.sql"), *root.rglob("*.py")])
    written.update(m.group(1) for m in re.finditer(r"INSERT INTO events.{0,600}?'([A-Z][A-Za-z]+)'", sql, re.S))
    assert VALIDITY_SOURCE_EVENTS | WAKE_EVENTS <= written, sorted((VALIDITY_SOURCE_EVENTS | WAKE_EVENTS) - written)
    receipt_kinds = {"AssuranceLocalCheckImported", "AssuranceExecutorCheckImported"}
    assert not receipt_kinds & (VALIDITY_SOURCE_EVENTS | CLOSEOUT_SOURCE_EVENTS | WAKE_EVENTS)
    assert "AssuranceCheckBound" not in written  # 检查到了只有屏障这一条信号


def test_a_settlement_wakes_an_import_waiting_on_budget_before_its_due_time(tmp_path):
    async def run() -> None:
        async with reviewed_mission(tmp_path, ReviewScript()) as case:
            mission = await case.settle()
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            store, tick = case.store, case.world.loop._assurance_tick
            consumer = tick.consumers["REVIEW"]
            imports = _imports(store, case.mission_id)
            released = case.events("BudgetReleased")[-1]
            _hold(store, case.mission_id, imports["TASK_CONTENT"], "BUDGET_WAIT")
            _hold(store, case.mission_id, imports["METHOD_PLAN"], "SOURCE_UNAVAILABLE")
            again = _emit_again(case, released)
            # 只唤醒卡在预算上的那一项；别的原因的等待不归结清事件管
            assert [target.work_key for target in consumer.classify(again)] == [imports["TASK_CONTENT"]]
            await tick.tick()
            rows = dict(store.connection.execute(
                "SELECT work_key, state FROM assurance_pending_work WHERE mission_id=? AND consumer='REVIEW'",
                (case.mission_id,)).fetchall())
            # 到点还远，被结清事件唤醒后当轮就领走做完（导入早已正式入库：按原回执确认，不重算）
            assert rows[imports["TASK_CONTENT"]] == "DONE"
            assert rows[imports["METHOD_PLAN"]] == "WAITING"

    asyncio.run(run())
