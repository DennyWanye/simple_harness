# SPDX-License-Identifier: Apache-2.0
"""全业务事件重放 v3（HTN 补齐阶段 G）：在产品同形世界里跑完的任务上核"能由事件重建"。"""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
from dataclasses import replace
from typing import Any

import pytest

from agent_orchestrator.observability import business_replay
from agent_orchestrator.observability.business_replay import (
    CONSISTENT,
    INCONSISTENT,
    OUT_OF_SCOPE,
    verify_library,
    verify_mission,
)
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.source_records import row_identity
from agent_orchestrator.storage.store import Store
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider
from replay_v3_audit import audit_database


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _mission(world: Any, key: str, criteria: list[str]) -> str:
    return world.create({"goal": "写笔记", "idempotency_key": key, "success_criteria": criteria})["mission_id"]


def _sources(report: dict[str, Any]) -> dict[str, str]:
    return {name: item["status"] for name, item in report["tables"].items()
            if item["rebuild"] == "immutable_source"}


def test_a_completed_mission_rebuilds_its_source_tables(tmp_path):
    """跑完的任务：每张只增业务表 ``CONSISTENT``；在副本里删掉一行源记录 → ``INCONSISTENT``。

    **改坏检验**（G-01）：比对点名时不核哈希 → 副本里改过内容的行不报 → 变红。"""

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            mission_id = _mission(world, "replay-sources", ["file:a.md", "file:b.md"])
            mission = await world.run_until_settled(mission_id, rounds=30)
            assert str(mission.status.value) == "COMPLETED"
            report = verify_mission(world.store, mission_id)
            sources = _sources(report)
            assert sources and set(sources.values()) == {CONSISTENT}, report
            assert report["tables"]["acceptances"]["rows"] >= 1
        shutil.copy(tmp_path / "root" / "orchestrator.db", tmp_path / "copy.db")  # closed: WAL folded in
        copy = Store.open(tmp_path / "copy.db")
        try:
            copy.connection.execute("PRAGMA foreign_keys = OFF")  # 副本里故意造坏，不经业务路径
            copy.connection.execute("DROP TRIGGER review_records_immutable_delete")
            copy.connection.execute("DROP TRIGGER acceptances_immutable_update")
            [record] = copy.connection.execute(
                "SELECT rowid FROM review_records WHERE mission_id=? LIMIT 1", (mission_id,)).fetchall()
            copy.connection.execute("DELETE FROM review_records WHERE rowid=?", (record[0],))
            copy.connection.execute(
                "UPDATE acceptances SET acceptance_json = acceptance_json || ' ' WHERE rowid ="
                " (SELECT min(rowid) FROM acceptances WHERE mission_id=?)", (mission_id,))
            tampered = verify_mission(copy, mission_id)
        finally:
            copy.close()
        assert tampered["tables"]["review_records"]["status"] == INCONSISTENT
        assert any("missing" in item for item in tampered["tables"]["review_records"]["problems"])
        assert tampered["tables"]["acceptances"]["status"] == INCONSISTENT
        assert tampered["status"] == INCONSISTENT
        # 审计插件读关好的库：原库全库一致、没有不一致的表；改过的副本判不一致
        original = audit_database(tmp_path / "root" / "orchestrator.db")
        assert original["library"] == CONSISTENT and not any(
            item.endswith(INCONSISTENT) for item in original["tables_not_consistent"]), original
        assert audit_database(tmp_path / "copy.db")["status"] == INCONSISTENT

    asyncio.run(case())


def test_a_mission_born_under_another_inventory_is_out_of_scope(tmp_path, monkeypatch):
    """按别的口径建的任务如实报"范围外"，不算一致也不算不一致（裁决 G-6）。

    **改坏检验**（G-03）：建任务时不写口径摘要 → 当前口径下的任务也报范围外 → 变红。"""

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            mission_id = _mission(world, "replay-scope", ["file:a.md"])
            assert verify_mission(world.store, mission_id)["status"] != OUT_OF_SCOPE
            monkeypatch.setattr(business_replay, "replay_scope_digest", lambda: "0" * 64)
            report = verify_mission(world.store, mission_id)
            assert report["status"] == OUT_OF_SCOPE and report["tables"] == {}

    asyncio.run(case())


def test_global_change_wakes_only_unfinished_missions(tmp_path):
    """一个已结束、一个进行中的任务，登记一个新做法：只有进行中的多一条证据变更事件，全局纪元
    加 1，变动回执照样记（裁决 G-3）。

    **改坏检验**（G-02）：迁移 41 的全局触发器去掉"没结束"条件 → 已结束的也被唤醒 → 变红。"""

    async def case():
        provider = LayeredScriptedProvider()
        async with product_world(tmp_path / "root", provider) as world:
            ended = _mission(world, "replay-ended", ["file:a.md"])
            assert str((await world.run_until_settled(ended, rounds=30)).status.value) == "COMPLETED"
            provider.held.add("worker")
            running = _mission(world, "replay-running", ["file:b.md"])
            for _ in range(6):
                await world.drain(timeout=20)
            assert str(world.store.get_mission(running).status.value) == "ACTIVE"

            def changes(mission_id: str) -> int:
                return len([e for e in world.store.list_events(mission_id)
                            if e.type == "AssuranceEvidenceChanged" and e.payload.get("scope") == "GLOBAL"])

            def epoch() -> int:
                return int(world.store.connection.execute(
                    "SELECT epoch FROM assurance_environment_state WHERE singleton=1").fetchone()[0])

            before = {ended: changes(ended), running: changes(running)}, epoch()
            htn = HtnStore(world.store)
            stored = htn.list_methods()[0]
            contract = replace(stored.contract, method_id="replay-probe")
            from agent_orchestrator.storage.assurance_changes import original_source_mutation

            with world.store.transaction(), original_source_mutation(world.store, writer="test"):
                htn.register_method(contract, replace(stored.registration, method_ref=contract.method_ref()))
            assert changes(ended) == before[0][ended]
            assert changes(running) == before[0][running] + 1
            assert epoch() == before[1] + 1

    asyncio.run(case())


@pytest.mark.parametrize("table", ["acceptances", "task_semantics", "commit_receipts"])
def test_immutable_sources_refuse_update_and_delete(tmp_path, table):
    """只增业务表与回执账，库层不许改、不许删（迁移 41）。"""

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            mission_id = _mission(world, "replay-guard", ["file:a.md"])
            await world.run_until_settled(mission_id, rounds=30)
            connection = world.store.connection
            rowid = connection.execute(f"SELECT min(rowid) FROM {table}").fetchone()[0]
            assert rowid is not None
            column = connection.execute(f"PRAGMA table_info({table})").fetchall()[-1][1]
            for statement in (f"UPDATE {table} SET {column}={column} WHERE rowid=?",
                              f"DELETE FROM {table} WHERE rowid=?"):
                with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                    connection.execute(statement, (rowid,))

    asyncio.run(case())


def test_every_immutable_row_and_receipt_is_named_in_its_own_transaction(tmp_path):
    """跑完一个任务：只增表与回执账的每一行都恰好被一条源记录点名事件点名、哈希对得上（裁决 G-1）。

    **改坏检验**（G-17）：自动点名漏掉回执账 → 回执行没人点名 → 变红。"""

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            mission_id = _mission(world, "replay-named", ["file:a.md", "file:b.md"])
            await world.run_until_settled(mission_id, rounds=30)
            library = verify_library(world.store)
            assert library["status"] == CONSISTENT, library
            assert library["unnamed_count"] == 0 and library["named_twice"] == []
            # 回执账单独核（不靠全库检查用的那份表单）：每条回执恰好被点名一次、哈希对得上
            names = business_replay._names(world.store, None)
            receipts = world.store.connection.execute("SELECT rowid, * FROM commit_receipts").fetchall()
            assert receipts
            for row in receipts:
                key, digest = row_identity(world.store.connection, "commit_receipts", row)
                assert names[("commit_receipts", json.dumps(key, sort_keys=True), digest)] == 1, key

    asyncio.run(case())
