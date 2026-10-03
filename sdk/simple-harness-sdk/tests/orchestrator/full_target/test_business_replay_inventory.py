# SPDX-License-Identifier: Apache-2.0
"""全业务重放 v3 覆盖清单的守护测试（HTN 补齐阶段 A 第 5 条）。

新表或新字段没有归类就失败：各阶段新增的业务事实必须当批登记进清单。"""
from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.observability.business_replay import (  # noqa: E402
    CLASSES,
    REPLAY_VERSION,
    InventoryError,
    check_inventory,
    coverage_report,
    inventory,
)
from agent_orchestrator.storage.store import Store  # noqa: E402
from agent_orchestrator.testing.product_world import product_world  # noqa: E402
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider  # noqa: E402


def test_every_table_and_field_of_the_current_schema_is_classified(tmp_path):
    store = Store.open(tmp_path / "o.db")
    try:
        check_inventory(store)
    finally:
        store.close()
    classes = {entry["class"] for entry in inventory()["tables"].values()}
    assert classes <= set(CLASSES) and {"business", "runtime", "global", "derived", "log"} == classes


def test_an_unclassified_table_or_field_fails_the_guard(tmp_path):
    """**Mutation**: skip the field comparison in ``check_inventory`` → red."""
    store = Store.open(tmp_path / "o.db")
    try:
        store.connection.execute("CREATE TABLE brand_new_fact (mission_id TEXT, value TEXT)")
        with pytest.raises(InventoryError, match="brand_new_fact is not classified"):
            check_inventory(store)
        store.connection.execute("DROP TABLE brand_new_fact")
        store.connection.execute("ALTER TABLE knowledge ADD COLUMN brand_new_field TEXT")
        with pytest.raises(InventoryError, match="knowledge fields differ"):
            check_inventory(store)
    finally:
        store.close()


def test_the_skeleton_reports_coverage_honestly_for_one_mission(tmp_path, monkeypatch):
    """在产品同形部署上整圈跑完的一个任务（规划、执行、验收、终审都有真实数据）上出覆盖报告。"""

    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)

    async def case() -> None:
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            created = world.create({"goal": "写一份 NOTES.md，列出三条要点。", "success_criteria": ["file:NOTES.md"],
                                    "idempotency_key": "replay-v3"})
            mission = await world.run_until_settled(created["mission_id"])
            assert str(mission.status.value) == "COMPLETED"
            report = coverage_report(world.store, mission.id)
            assert report["version"] == REPLAY_VERSION and report["status"] == "SKELETON"
            assert report["mission_rows"]["missions"] == 1
            assert report["mission_rows"]["tasks"] >= 2  # the root and its step
            business = {name for name, entry in inventory()["tables"].items() if entry["class"] == "business"}
            parts = (set(report["covered"]), set(report["partial"]), set(report["not_covered"]))
            assert parts[0] | parts[1] | parts[2] == business
            assert sum(len(part) for part in parts) == len(business)

    asyncio.run(case())
