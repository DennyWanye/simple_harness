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
    verify_execution_ledgers,
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
            # 会被改的表由整行变化折叠出来：整个任务一致、没有静默改动（偏差裁决 1）
            assert report["status"] == CONSISTENT and report["silent_changes"] == [], {
                name: item["problems"][:3] for name, item in report["tables"].items() if item["problems"]}
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
            # 两库对照（裁决 G-7）：原库每条用量回执在执行库里恰好一条、用量一致；副本里把一条
            # 用量多算 1 → 报"不一致"。**改坏检验**（G-24）：已知用量不比数 → 变红。
            executions = sorted((tmp_path / "root").glob("execution*.db"))
            original_ledgers = verify_execution_ledgers(copy, executions)
            [ref] = copy.connection.execute(
                "SELECT usage_ref FROM imported_usage WHERE mission_id=? AND unknown=0 LIMIT 1",
                (mission_id,)).fetchone()
            copy.connection.execute("UPDATE imported_usage SET input_tokens=input_tokens+1 WHERE usage_ref=?", (ref,))
            ledgers = verify_execution_ledgers(copy, executions)
        finally:
            copy.close()
        assert tampered["tables"]["review_records"]["status"] == INCONSISTENT
        assert any("missing" in item for item in tampered["tables"]["review_records"]["problems"])
        assert tampered["tables"]["acceptances"]["status"] == INCONSISTENT
        assert tampered["status"] == INCONSISTENT
        assert original_ledgers["status"] == CONSISTENT and original_ledgers["calls"] >= 1, original_ledgers
        assert ledgers["status"] == INCONSISTENT and ledgers["mismatched"] == [ref], ledgers
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


@pytest.mark.replay_audit_exempt("用例绕过产品路径直接登记一条做法定义，造一次全局变动")
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
            names: dict[tuple[str, str, str], int] = {}
            for (payload,) in world.store.connection.execute(
                    "SELECT payload_json FROM events WHERE type='RowsWritten'"):
                for item in json.loads(payload)["named"]:
                    marker = (item["table"], json.dumps(item["key"], sort_keys=True), item["content_hash"])
                    names[marker] = names.get(marker, 0) + 1
            receipts = world.store.connection.execute("SELECT rowid, * FROM commit_receipts").fetchall()
            assert receipts
            for row in receipts:
                key, digest = row_identity(world.store.connection, "commit_receipts", row)
                assert names.get(("commit_receipts", json.dumps(key, sort_keys=True), digest)) == 1, key

    asyncio.run(case())


# ----------------------------------------------------------------- 整行变化机制（偏差裁决 1）

async def _finished(world: Any, key: str) -> str:
    mission_id = _mission(world, key, ["file:a.md"])
    assert str((await world.run_until_settled(mission_id, rounds=30)).status.value) == "COMPLETED"
    return mission_id


def _touch(world: Any, mission_id: str, sql: str, args: tuple[Any, ...], note: str) -> None:
    """One ordinary transaction: the change plus a domain event of this Mission."""

    from agent_orchestrator.contracts.models import Event

    with world.store.transaction() as connection:
        connection.execute(sql, args)
        identity = f"test-touch:{mission_id}:{note}"
        world.store.append_event(Event(
            id=identity, type="TestTouched", trace_id=identity, mission_id=mission_id, task_id=None,
            attempt_id=None, actor_type="system", actor_id="test", payload={"note": note},
            idempotency_key=identity, created_at=world.store.now))


def _changes(world: Any, mission_id: str, table: str, after_seq: int = 0) -> list[dict[str, Any]]:
    return [item for (payload,) in world.store.connection.execute(
        "SELECT payload_json FROM events WHERE type='RowsWritten' AND mission_id=? AND seq>?",
        (mission_id, after_seq)) for item in json.loads(payload)["changed"] if item["table"] == table]


def _last_seq(world: Any) -> int:
    return int(world.store.connection.execute("SELECT max(seq) FROM events").fetchone()[0])


def test_a_row_changed_back_and_forth_is_logged_every_time(tmp_path):
    """同一行 A→B→A→B→A：每次都有一条改动、链接得上（幂等键含追加前序号，偏差裁决 1 第 4 条）。

    **改坏检验**（G-06）：幂等键去掉追加前序号 → 第二次 A→B 与第一次撞键 → 变红。"""

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            mission_id = await _finished(world, "rows-back-and-forth")
            mark = _last_seq(world)
            [original] = world.store.connection.execute(
                "SELECT updated_at FROM missions WHERE mission_id=?", (mission_id,)).fetchone()
            try:
                for step, value in enumerate((original + 1, original, original + 1, original)):
                    _touch(world, mission_id, "UPDATE missions SET updated_at=? WHERE mission_id=?",
                           (value, mission_id), f"flip-{step}")
            except sqlite3.IntegrityError as error:
                raise AssertionError(f"a repeated change was refused as a duplicate: {error}") from error
            changes = _changes(world, mission_id, "missions", mark)
            assert len(changes) == 4
            assert [item["before"] for item in changes[1:]] == [item["after"] for item in changes[:-1]]
            report = verify_mission(world.store, mission_id)
            assert report["tables"]["missions"]["status"] == CONSISTENT, report["tables"]["missions"]

    asyncio.run(case())


def test_rows_inserted_and_removed_in_one_transaction_leave_no_trace(tmp_path):
    """同一事务先插后删不留记录；保存点回滚掉的改动不记；跨事务插入再删除如实记成删除。

    **改坏检验**（G-10）：不装删除后的触发器 → 删掉的行没被记下 → 重建出的行库里没有 → 变红。"""

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            mission_id = await _finished(world, "rows-insert-remove")
            mark = _last_seq(world)
            row = ("probe-approval", "probe", mission_id, "probe-subject", "pending", 1, "{}", 1.0, 1.0)
            insert = "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?)"
            with world.store.transaction() as connection:
                connection.execute(insert, row)
                connection.execute("DELETE FROM approvals WHERE request_id='probe-approval'")
                connection.execute("SAVEPOINT probe")
                connection.execute("UPDATE missions SET updated_at=updated_at+1 WHERE mission_id=?", (mission_id,))
                connection.execute("ROLLBACK TO probe")
                connection.execute("RELEASE probe")
            assert _changes(world, mission_id, "approvals", mark) == []
            assert _changes(world, mission_id, "missions", mark) == []
            _touch(world, mission_id, insert, row, "insert")
            _touch(world, mission_id, "DELETE FROM approvals WHERE request_id='probe-approval'", (), "delete")
            changes = _changes(world, mission_id, "approvals", mark)
            assert [(item["before"] is None, item["after"] is None) for item in changes] == [
                (True, False), (False, True)]
            report = verify_mission(world.store, mission_id)
            assert report["tables"]["approvals"]["status"] == CONSISTENT, report["tables"]["approvals"]

    asyncio.run(case())


def test_an_idempotent_replay_adds_no_row_events(tmp_path):
    """命令重放、没改出不同内容的写入，都不多记整行变化（空改动不记，偏差裁决 1 第 2 条）。

    **改坏检验**（G-09）：提交前不跳过前后哈希相同的行 → 空改动也记一条 → 变红。"""

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            mission_id = await _finished(world, "rows-idempotent")
            mark = _last_seq(world)
            again = world.create({"goal": "写笔记", "idempotency_key": "rows-idempotent",
                                  "success_criteria": ["file:a.md"]})
            assert again["mission_id"] == mission_id
            _touch(world, mission_id, "UPDATE missions SET updated_at=updated_at WHERE mission_id=?",
                   (mission_id,), "no-op")
            assert _changes(world, mission_id, "missions", mark) == []
            assert verify_mission(world.store, mission_id)["status"] == CONSISTENT

    asyncio.run(case())


@pytest.mark.replay_audit_exempt("用例故意另开连接改坏一行")
def test_an_out_of_band_write_breaks_the_chain(tmp_path):
    """绕过存储层（另开连接）改一行，之后再正常改一次：v3 报链断（偏差裁决 1 第 7 条）。

    **改坏检验**（G-19）：v3 不核改前哈希 → 最后一版与库里一致就放过 → 变红。"""

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            mission_id = await _finished(world, "rows-out-of-band")
            outside = sqlite3.connect(tmp_path / "root" / "orchestrator.db")
            outside.execute("UPDATE missions SET updated_at=updated_at+7 WHERE mission_id=?", (mission_id,))
            outside.commit()
            outside.close()
            _touch(world, mission_id, "UPDATE missions SET updated_at=updated_at+1 WHERE mission_id=?",
                   (mission_id,), "after-outside")
            table = verify_mission(world.store, mission_id)["tables"]["missions"]
            assert table["status"] == INCONSISTENT and any("does not chain" in p for p in table["problems"]), table

    asyncio.run(case())


@pytest.mark.replay_audit_exempt("用例故意造一次没有领域事件的改动")
def test_a_silent_change_is_reported(tmp_path):
    """改了业务行、本事务却没有本任务的领域事件：报"静默改动"；写了 ``silent_ok`` 的记账表放行。

    **改坏检验**（G-21）：v3 不核 ``with_events`` → 静默改动不报 → 变红。"""

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            mission_id = await _finished(world, "rows-silent")
            with world.store.transaction() as connection:  # 记账表：放行
                connection.execute("UPDATE budget_accounts SET updated_at=updated_at+1 WHERE mission_id=?",
                                   (mission_id,))
            assert verify_mission(world.store, mission_id)["silent_changes"] == []
            with world.store.transaction() as connection:  # 任务表：不放行
                connection.execute("UPDATE missions SET updated_at=updated_at+1 WHERE mission_id=?", (mission_id,))
            report = verify_mission(world.store, mission_id)
            assert report["silent_changes"] and report["tables"]["missions"]["status"] == INCONSISTENT, report

    asyncio.run(case())


def test_bookkeeping_alone_is_not_progress(tmp_path):
    """只有存储层记账事件的事务不算进展（空转水位不动，偏差裁决 1 R13）。

    **改坏检验**（G-20）：水位把 ``RowsWritten`` 当进展 → 只续一次额度水位就动了 → 变红。"""

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            mission_id = await _finished(world, "rows-watermark")
            before = world.loop._durable_watermark()
            with world.store.transaction() as connection:
                connection.execute("UPDATE budget_accounts SET updated_at=updated_at+1 WHERE mission_id=?",
                                   (mission_id,))
            assert _changes(world, mission_id, "budget_accounts")  # it was logged
            assert world.loop._durable_watermark() == before

    asyncio.run(case())


def test_rows_are_owned_by_their_declared_mission_while_other_missions_are_live(tmp_path):
    """两个没结束的任务；一个规划提交里登记新做法（全局触发器给两个任务都写唤醒信号）：那次的
    变动回执归提出做法的任务，不落到部署时间线（偏差裁决 1 第五节附带发现 1）。

    **改坏检验**（G-22）：归属判断把触发器写的 ``AssuranceEvidenceChanged`` 也算进"本事务的任务"
    → 本事务成了两个任务 → 回执落到部署时间线 → 变红。"""

    async def case():
        provider = LayeredScriptedProvider()
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            first = _mission(world, "rows-owner-a", ["file:a.md"])
            for _ in range(6):
                await world.drain(timeout=20)
            second = _mission(world, "rows-owner-b", ["file:b.md"])
            for _ in range(6):
                await world.drain(timeout=20)
            assert {str(world.store.get_mission(m).status.value) for m in (first, second)} == {"ACTIVE"}
            owners: dict[str, str] = {}
            for mission_id, payload in world.store.connection.execute(
                    "SELECT mission_id, payload_json FROM events WHERE type='RowsWritten'"):
                for item in json.loads(payload)["named"]:
                    if item["table"] == "commit_receipts":
                        owners[item["key"]["commit_id"]] = mission_id
            mutations = [commit_id for (commit_id,) in world.store.connection.execute(
                "SELECT commit_id FROM commit_receipts WHERE kind='AssuranceSourceMutation'")]
            assert mutations
            assert all(owners[commit_id] in {first, second} for commit_id in mutations), (
                {commit_id: owners.get(commit_id) for commit_id in mutations})
            for mission_id in (first, second):
                assert verify_mission(world.store, mission_id)["status"] == CONSISTENT

    asyncio.run(case())


def test_a_root_review_cut_that_fails_halfway_leaves_no_unclaimed_package(tmp_path, monkeypatch):
    """根终审切包时"已切包"事件写一半出错：包、输入清单与事件同一事务一起回滚，不留没人认领的包；
    下一轮重切，任务照常完成（阶段 G 第 3 批）。

    **改坏检验**（G-07）：切包不包在一个事务里 → 包已写、事件没写 → 变红。"""

    import agent_orchestrator.orchestrator.root_review as root_review

    real = root_review.append_hierarchical_event
    broke: list[str] = []

    def flaky(store: Any, kind: str, *args: Any, **kwargs: Any) -> Any:
        if kind == root_review.ROOT_REVIEW_CUT and not broke:
            broke.append(kind)
            raise sqlite3.OperationalError("disk I/O error")
        return real(store, kind, *args, **kwargs)

    monkeypatch.setattr(root_review, "append_hierarchical_event", flaky)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            mission_id = _mission(world, "rows-root-cut", ["file:a.md"])
            mission = await world.run_until_settled(mission_id, rounds=30)
            assert broke and str(mission.status.value) == "COMPLETED", mission.final_report
            finals = {str(package.package_id) for package in HtnStore(world.store).list_review_packages(mission_id)
                      if str(package.purpose) == "MISSION_FINAL"}
            cuts = {event.payload["package_id"] for event in world.store.list_events(mission_id)
                    if event.type == root_review.ROOT_REVIEW_CUT}
            assert finals and finals <= cuts, (finals, cuts)
            assert verify_mission(world.store, mission_id)["status"] == CONSISTENT

    asyncio.run(case())
