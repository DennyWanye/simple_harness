# SPDX-License-Identifier: Apache-2.0
"""用量晚到、任务已终态（崩溃切点 K11；HTN 补齐阶段 G 第 4 批）。

真场景：执行者的模型调用还在半路时用户取消任务 → 任务终态 → 调用这才返回，执行库记下用量。
恢复要求：用量导入原账户、按原预留结账；不新建尝试、不派发、任务状态不变；之后只多记账类
事件与对"任务已终态"的观察；全业务重放与两库对照都一致。

用量是在收回那次被取消的调用时导入的（意图那时还没结清），走的是产品本身的收回路径。"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_orchestrator.observability.business_replay import (
    CONSISTENT,
    verify_execution_ledgers,
    verify_mission,
)
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _count(store, sql, *args):
    return store.connection.execute(sql, args).fetchone()[0]


def test_usage_arriving_after_the_mission_ended_settles_the_original_account(tmp_path):
    async def case():
        provider = LayeredScriptedProvider()
        provider.held.add("worker")
        root = tmp_path / "root"
        async with product_world(root, provider) as world:
            store = world.loop.store
            mission_id = world.create({"goal": "写笔记", "idempotency_key": "late-usage",
                                       "success_criteria": ["file:a.md"]})["mission_id"]
            stop = asyncio.Event()

            async def drive() -> None:  # the held call keeps run() from going idle
                while not stop.is_set():
                    await world.loop.run()
                    await world.deployment.between_cycles(auto=True)
                    await asyncio.sleep(0.05)

            runner = asyncio.create_task(drive())
            try:
                await asyncio.wait_for(provider.entered.wait(), 60)
                world.control.cancel(mission_id)
                for _ in range(300):
                    if str(store.get_mission(mission_id).status.value) == "CANCELLED":
                        break
                    await asyncio.sleep(0.05)
                assert str(store.get_mission(mission_id).status.value) == "CANCELLED"
                [(subject_id,)] = store.connection.execute(
                    "SELECT subject_id FROM dispatch_intents WHERE mission_id=? AND kind='attempt'",
                    (mission_id,)).fetchall()
                held = world.loop.commit.ledger.reservation(subject_id)
                assert held is not None and held["state"] == "RESERVED"
                attempts = _count(store, "SELECT count(*) FROM attempts WHERE mission_id=?", mission_id)
                intents = _count(store, "SELECT count(*) FROM dispatch_intents WHERE mission_id=?", mission_id)
                last_seq = _count(store, "SELECT max(seq) FROM events WHERE mission_id=?", mission_id)
                asked = len(provider.asked)

                provider.release.set()  # the call returns only now; the execution side records its usage
                for _ in range(400):
                    reservation = world.loop.commit.ledger.reservation(subject_id)
                    if reservation["state"] == "SETTLED":
                        break
                    await asyncio.sleep(0.05)
            finally:
                stop.set()
                provider.release.set()
                await asyncio.wait_for(runner, 30)

            reservation = world.loop.commit.ledger.reservation(subject_id)
            assert reservation["state"] == "SETTLED", reservation
            assert reservation["account_id"] == held["account_id"]
            usage = store.connection.execute(
                "SELECT usage_ref,input_tokens,output_tokens,unknown FROM imported_usage WHERE subject_id=?",
                (subject_id,)).fetchall()
            assert usage and all(row["unknown"] == 0 and row["input_tokens"] > 0 for row in usage), usage
            assert str(store.get_mission(mission_id).status.value) == "CANCELLED"
            assert _count(store, "SELECT count(*) FROM attempts WHERE mission_id=?", mission_id) == attempts
            assert _count(store, "SELECT count(*) FROM dispatch_intents WHERE mission_id=?", mission_id) == intents
            assert len(provider.asked) == asked  # nothing re-ran
            # 之后只多：收回那次被取消的调用（结果按"被取代"归档、意图结清）、按原预留结账，以及
            # 收尾与跟进对"任务已终态"的观察；没有新工作、没有内容被接受。
            later = [(row[0], json.loads(row[1])) for row in store.connection.execute(
                "SELECT type,payload_json FROM events WHERE mission_id=? AND seq>? AND type!='RowsWritten'",
                (mission_id, last_seq))]
            assert {kind for kind, _ in later} <= {
                "IntentSettled", "ResultRejected", "BudgetReleased", "BudgetSettled",
                "AssuranceCloseoutEvaluated", "AssuranceStatusNotified", "TaskGraphFollowupConsumed"}, later
            assert [p["reason"] for kind, p in later if kind == "ResultRejected"] == ["superseded"]
            assert all(p["result"]["status"] == "MISSION_TERMINAL"
                       for kind, p in later if kind == "TaskGraphFollowupConsumed")
            [released] = [p for kind, p in later if kind == "BudgetReleased"]
            assert released["subject_id"] == subject_id
            assert released["settled_tokens"] == sum(row["input_tokens"] + row["output_tokens"] for row in usage)
            report = verify_mission(store, mission_id)
            assert report["status"] == CONSISTENT, [
                (name, item["problems"][:2]) for name, item in report["tables"].items() if item["problems"]]
            ledgers = verify_execution_ledgers(store, sorted(root.glob("execution*.db")))
            assert ledgers["status"] == CONSISTENT, ledgers

    asyncio.run(case())
