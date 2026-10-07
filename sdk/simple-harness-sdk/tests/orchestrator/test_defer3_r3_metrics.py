# SPDX-License-Identifier: Apache-2.0
"""推后第 3 批车道 R3：U05 监控补八项指标（原文 §23.2 表）。一律按事件或表行计数，不读任何正文。

新思路数 / 剪枝率：计划修订里采用 / 退掉的做法实例；重复率：H06 的结果内容哈希计数；知识污染率：
已验证知识被顶出争议的条目 ÷ 入库数；误报率：根终审打回 ÷ 根终审送审；审阅积压：待审数、峰值、
升起次数、应对变化次数；任务成功率与每成功任务成本：单个任务给结局与 token，全库给比例与均摊。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

_FULL_TARGET = Path(__file__).resolve().parent / "full_target"
for _path in (_FULL_TARGET, _FULL_TARGET / "taskgraph_exec"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from agent_orchestrator.observability.metrics import (  # noqa: E402
    METRICS_VERSION,
    deployment_outcomes,
    metrics,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_an_empty_library_has_no_rates_and_no_errors(tmp_path: Path) -> None:
    from agent_orchestrator.storage.store import Store

    store = Store.open(tmp_path / "o.db")
    report = metrics(store, "nothing")
    assert METRICS_VERSION == "metrics-v2"
    assert report["search"] == {"new_approaches": 0, "retired_approaches": 0, "prune_rate": None,
                                "duplicate_results": 0, "results": 0, "duplicate_rate": None}
    assert report["knowledge"]["pollution_rate"] is None
    assert report["verification"]["false_positive_rate"] is None
    assert report["outcome"] == {"status": None, "succeeded": None, "tokens": 0, "cost_per_success": None}
    assert deployment_outcomes(store) == {"missions_ended": 0, "completed": 0, "failed": 0, "cancelled": 0,
                                          "success_rate": None, "tokens_of_ended": 0, "cost_per_success": None}


def test_the_eight_indicators_are_event_counts_on_a_real_mission(tmp_path: Path) -> None:
    from production_fixture import CHAIN_CRITERIA, chain_planner, enabled_world

    async def case() -> dict[str, Any]:
        async with enabled_world(tmp_path, key="r3-metrics", planner=chain_planner,
                                 criteria=CHAIN_CRITERIA) as world:
            await world.until(lambda: str(world.store.get_mission(world.mission.id).status) in
                              {"COMPLETED", "FAILED", "CANCELLED"}, timeout=60)
            store, commit, mission_id = world.store, world.loop.commit, world.mission.id
            before = metrics(store, mission_id)
            tokens = int(store.connection.execute(
                "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM imported_usage WHERE mission_id=?",
                (mission_id,)).fetchone()[0])
            # 补一些事件，逐项核对计数口径（只数事件，不读内容）
            with store.transaction():
                commit._emit("PlanRevisionCommitted", mission_id, key="r3:plan-x",
                             payload={"adopted_method_instances": ["i-a", "i-b"], "retired_method_instances": ["i-0"]})
                commit._emit("KnowledgeCommitted", mission_id, key="r3:k1", payload={})
                commit._emit("KnowledgeCommitted", mission_id, key="r3:k2", payload={})
                commit._emit("ClaimDisputed", mission_id, key="r3:d1",
                             payload={"claim_id": "c9", "contradicts": "k1", "other_status": "VERIFIED"})
                commit._emit("ClaimDisputed", mission_id, key="r3:d2",
                             payload={"claim_id": "c8", "contradicts": "k1", "other_status": "VERIFIED"})
                commit._emit("ClaimDisputed", mission_id, key="r3:d3",
                             payload={"claim_id": "c7", "contradicts": "c6", "other_status": "PROPOSED"})
                commit._emit("HierarchicalRootReviewCut", mission_id, key="r3:cut", payload={})
                commit._emit("HierarchicalRootReviewRejected", mission_id, key="r3:rej", payload={})
                commit._emit("BackpressureRaised", mission_id, key="r3:bp",
                             payload={"dimension": "pending_verifications"})
                commit._emit("BackpressureRaised", mission_id, key="r3:bp2", payload={"dimension": "running_attempts"})
                commit._emit("BacklogResponseChanged", mission_id, key="r3:br", payload={})
            after = metrics(store, mission_id)
            return {"before": before, "after": after, "tokens": tokens, "deployment": deployment_outcomes(store),
                    "status": str(store.get_mission(mission_id).status)}

    seen = asyncio.run(case())
    assert seen["status"] == "COMPLETED"
    before, after = seen["before"], seen["after"]
    adopted, retired = before["search"]["new_approaches"], before["search"]["retired_approaches"]
    assert adopted >= 1 and retired == 0 and before["search"]["prune_rate"] == 0.0
    assert after["search"]["new_approaches"] == adopted + 2
    assert after["search"]["retired_approaches"] == 1
    assert after["search"]["prune_rate"] == round(1 / (adopted + 2), 4)
    assert before["search"]["results"] == 2 and before["search"]["duplicate_results"] == 0
    assert before["search"]["duplicate_rate"] == 0.0
    committed = before["knowledge"]["committed"]
    assert after["knowledge"]["committed"] == committed + 2
    assert after["knowledge"]["polluted"] == before["knowledge"]["polluted"] + 1  # 同一条只算一次
    assert after["knowledge"]["pollution_rate"] == round(after["knowledge"]["polluted"] / (committed + 2), 4)
    cuts = before["verification"]["root_reviews"]
    assert cuts == 1 and before["verification"]["root_review_rejections"] == 0
    assert before["verification"]["false_positive_rate"] == 0.0
    assert after["verification"]["false_positive_rate"] == round(1 / 2, 4)
    backlog = after["verification"]["backlog"]
    assert backlog["pending_now"] == 0
    assert backlog["raised"] == before["verification"]["backlog"]["raised"] + 1
    assert backlog["responses"] == before["verification"]["backlog"]["responses"] + 1
    assert after["outcome"] == {"status": "COMPLETED", "succeeded": True, "tokens": seen["tokens"],
                                "cost_per_success": seen["tokens"]}
    assert seen["deployment"] == {"missions_ended": 1, "completed": 1, "failed": 0, "cancelled": 0,
                                  "success_rate": 1.0, "tokens_of_ended": seen["tokens"],
                                  "cost_per_success": float(seen["tokens"])}
