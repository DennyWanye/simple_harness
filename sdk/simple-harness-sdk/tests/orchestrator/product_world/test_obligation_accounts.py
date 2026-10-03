# SPDX-License-Identifier: Apache-2.0
"""按"要完成的那件事"汇总失败与花费（HTN 补齐阶段 D）。

义务行里不存这些数；读的时候由尝试与结算记录推出，下级义务的合计进上级。规划包、对外快照
读的是同一处。只记账，不改任何上限。

**改坏检验**：规划包的义务视图改回读零 → 变红。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_orchestrator.orchestrator.obligation_accounts import obligation_accounts
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    planner_reply,
    retry_same_method,
    review_input,
    review_reply,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_obligation_account_counts_failures_and_spend(tmp_path):
    repair_views: list[list[dict[str, Any]]] = []
    seen = {"content": 0}

    def planner(request: Any) -> Any:
        package = package_of(request)
        if package.get("repair_requests"):
            repair_views.append(package["views"]["obligations"])
            return retry_same_method(request)
        return planner_reply(request)

    def reviewer(request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        if str((data.get("package") or {}).get("purpose")) == "TASK_CONTENT":
            seen["content"] += 1
            if seen["content"] == 1:
                return review_reply(data, verdict="REWORK", grade="FAIL", reason="要点太笼统，请写具体。")
        return review_reply(data)

    async def case():
        provider = LayeredScriptedProvider(planner=planner, reviewer=reviewer)
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "duty-account"})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=30)
            assert mission.status.value == "COMPLETED", mission.final_report
            store = world.store
            accounts = obligation_accounts(store, mission_id)
            leaf = max(accounts.values(), key=lambda row: row["attempts"])
            assert leaf["attempts"] == 2 and leaf["failed_attempts"] == 1
            settled = sum(int(row[0] or 0) for row in store.connection.execute(
                "SELECT r.settled_tokens FROM budget_reservations r JOIN attempts a ON a.attempt_id=r.subject_id"
                " WHERE r.mission_id=?", (mission_id,)))
            assert leaf["settled_tokens"] == settled > 0
            assert all(row["attempts"] <= leaf["attempts"] for row in accounts.values())
            # 规划器在修复轮里看到的是真数，不是零
            assert repair_views, "the planner was never asked to repair"
            seen_failures = [duty["failures"][0]["count"] for duty in repair_views[0]]
            assert max(seen_failures) == 1, repair_views[0]
            # 对外快照读同一处
            assert world.control.snapshot(mission_id)["snapshot"]["obligation_accounts"] == accounts
            # 义务表里没有那三列
            columns = {row[1] for row in store.connection.execute("PRAGMA table_info(obligations)")}
            assert not {"failure_count", "spent_tokens", "spent_attempts"} & columns

    asyncio.run(case())
