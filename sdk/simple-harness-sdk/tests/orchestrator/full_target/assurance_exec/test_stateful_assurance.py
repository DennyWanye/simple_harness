# SPDX-License-Identifier: Apache-2.0
"""随机序列下保证通道审阅 / 验收的不变式（小号版；2026-10-03 迁到产品同形世界）。

这个环境没有 hypothesis（计划不许为验收装运行时依赖），序列取自 ``random.Random(seed)``。每个种子
随机给出外界的事：内容审阅与终审每次调用的结论（通过 / 判不下 / 空答复），以及主循环跑一小段
之后人做什么（看一眼快照、在审阅卡上判通过 / 不通过、回答终审裁决问题）。每一小段之后从库里
重读不变式：

* 每份审阅（同一个审阅键）至多两次调用（初次 + 一次重发或复审），序号只有 1、2；
* 每份审阅至多一条正式记录；没有正式通过的审阅（或人的通过裁决），就没有验收；
* 每个任务至多一份有效验收、一张验收许可证；
* 读快照不写任何东西；保证通道的有效性纪元只增不减；
* 终审没通过（也没有人判通过）就没有根结论，任务不会完成。

六个种子，每个种子至多 8 小段。
"""

from __future__ import annotations

import asyncio
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _review_world import ReviewScript, quick_waits, reviewed_mission  # noqa: E402

from agent_orchestrator.storage.planning_human_store import PlanningHumanStore  # noqa: E402

VERDICTS = ("ACCEPT", "INCONCLUSIVE", "EMPTY")


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    quick_waits(monkeypatch)


def _count(store, sql, *params):
    return store.connection.execute(sql, params).fetchone()[0]


def _epoch(store, mission_id):
    row = store.connection.execute(
        "SELECT epoch FROM validity_epochs WHERE mission_id=? AND scope_id=?",
        (mission_id, "assurance:mission")).fetchone()
    return 0 if row is None else int(row[0])


def _invariants(case, provider, trace):
    store, mission_id = case.store, case.mission_id
    per_key = store.connection.execute(
        "SELECT review_key, count(*), max(ordinal), min(ordinal) FROM assurance_review_invocations "
        "WHERE mission_id=? GROUP BY review_key", (mission_id,)).fetchall()
    for key, count, high, low in per_key:
        assert count <= 2 and low == 1 and high <= 2, (key, count, trace)
    official = store.connection.execute(
        "SELECT package_id, count(*) FROM review_records WHERE mission_id=? AND official=1 GROUP BY package_id",
        (mission_id,)).fetchall()
    assert all(n == 1 for _, n in official), trace
    accepted = store.connection.execute(
        "SELECT task_id, count(*) FROM acceptances WHERE mission_id=? AND validity='CURRENT' GROUP BY task_id",
        (mission_id,)).fetchall()
    assert all(n == 1 for _, n in accepted), trace
    certificates = _count(store, "SELECT count(*) FROM assurance_use_certificates WHERE mission_id=? "
                                 "AND consumer_kind='ACCEPTANCE'", mission_id)
    assert certificates <= len(accepted), trace
    if accepted:
        # 每份验收都有审阅（模型的正式通过，或者人的通过裁决）在先
        assert official or case.events("AssuranceReviewAdjudicated"), trace
    final_passed = any(e.type == "GoalResolutionCommitted" for e in case.events())
    if case.status() == "COMPLETED":
        assert final_passed, trace
    assert sum(provider.review_calls.values()) <= 2 * max(1, len(per_key)), trace


async def _sequence(tmp_path, seed):
    rng = random.Random(seed)
    provider = ReviewScript(verdicts={
        "TASK_CONTENT": [rng.choice(VERDICTS) for _ in range(2)],
        "MISSION_FINAL": [rng.choice(("ACCEPT", "INCONCLUSIVE")) for _ in range(2)],
    })
    trace: list[str] = [f"script={provider.verdicts}"]
    async with reviewed_mission(tmp_path, provider, key=f"stateful-{seed}") as case:
        store, mission_id = case.store, case.mission_id
        last_epoch = _epoch(store, mission_id)
        for _ in range(8):
            try:
                await case.run_until(lambda: provider.repair_asked.is_set() or case.status() in {
                    "COMPLETED", "FAILED"}, timeout=rng.choice((0.5, 1.0, 2.0)))
            except TimeoutError:
                pass
            action = rng.choice(("snapshot", "decide", "answer", "wait"))
            trace.append(action)
            if action == "snapshot":
                before = store.connection.total_changes
                case.world.control.snapshot(mission_id)
                assert store.connection.total_changes == before, trace
            elif action == "decide":
                for card in case.pending_approvals():
                    if card["kind"] == "review":
                        verdict = rng.choice(("review_pass", "review_fail"))
                        trace.append(verdict)
                        case.world.control.decide(card["request_id"], verdict, note="随机序列里的人")
            elif action == "answer":
                for question in PlanningHumanStore(store).list(mission_id):
                    if question["state"] == "PENDING":
                        ruling = rng.choice(("pass", "fail"))
                        trace.append("final-" + ruling)
                        case.world.control.answer_planning_question({
                            "decision_id": question["decision_id"], "answer": ruling,
                            "expected_version": question["version"], "nonce": f"n-{seed}-{len(trace)}"})
            _invariants(case, provider, trace)
            epoch = _epoch(store, mission_id)
            assert epoch >= last_epoch, trace
            last_epoch = epoch
            if provider.repair_asked.is_set() or case.status() in {"COMPLETED", "FAILED"}:
                break
        return trace


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_random_sequences_keep_invariants(tmp_path, seed):
    trace = asyncio.run(_sequence(tmp_path, seed))
    assert trace
