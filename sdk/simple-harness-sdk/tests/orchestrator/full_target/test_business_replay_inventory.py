# SPDX-License-Identifier: Apache-2.0
"""全业务重放 v3 覆盖清单的守护测试（HTN 补齐阶段 A 第 5 条立、阶段 G 第 1 批改成第 2 版）。

新表或新字段没有归类就失败；业务表必须标重建方式（只增源记录 / 由事件折叠）；只增源记录必须
有库层不许改删守卫；非业务表必须写为什么不重建；未覆盖的折叠表不算覆盖。"""
from __future__ import annotations

import copy
import json

import pytest

from agent_orchestrator.observability import business_replay
from agent_orchestrator.observability.business_replay import (
    CLASSES,
    InventoryError,
    check_inventory,
    inventory,
)
from agent_orchestrator.storage.store import Store


def test_every_table_and_field_of_the_current_schema_is_classified(tmp_path):
    store = Store.open(tmp_path / "o.db")
    try:
        check_inventory(store)
    finally:
        store.close()
    tables = inventory()["tables"]
    assert {entry["class"] for entry in tables.values()} == set(CLASSES)
    business = [entry for entry in tables.values() if entry["class"] == "business"]
    assert business and all(entry["rebuild"] in {"immutable_source", "fold"} for entry in business)


def test_an_unclassified_table_or_field_fails_the_guard(tmp_path):
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


def test_an_immutable_source_without_its_guard_fails(tmp_path):
    store = Store.open(tmp_path / "o.db")
    try:
        store.connection.execute("DROP TRIGGER review_records_immutable_update")
        with pytest.raises(InventoryError, match="review_records is an immutable source without a UPDATE"):
            check_inventory(store)
    finally:
        store.close()


@pytest.fixture
def edited(monkeypatch):
    """Validate an edited copy of the inventory through the real loader."""

    original = json.loads(business_replay.Path(business_replay.__file__)
                          .with_name("business_replay_inventory.json").read_text("utf-8"))

    def load(change):
        raw = copy.deepcopy(original)
        change(raw["tables"])
        monkeypatch.setattr(business_replay.Path, "read_text", lambda self, *a, **k: json.dumps(raw))
        business_replay.inventory.cache_clear()
        try:
            return business_replay.inventory()
        finally:
            monkeypatch.undo()
            business_replay.inventory.cache_clear()

    return load


def test_inventory_v2_rules(edited):
    """**改坏检验**（G-04）：不查 ``rebuild`` → 第一种情形不报错 → 变红。"""

    def no_rebuild(tables):
        del tables["missions"]["rebuild"]

    def unknown_rebuild(tables):
        tables["missions"]["rebuild"] = "copy_whole_row"

    def silent_exclusion(tables):
        tables["workspaces"]["note"] = " "

    for change, message in ((no_rebuild, "missions: a business table says how it is rebuilt"),
                            (unknown_rebuild, "missions: rebuild must be one of"),
                            (silent_exclusion, "workspaces: a table outside business says why")):
        with pytest.raises(InventoryError, match=message):
            edited(change)


def test_a_business_table_without_mission_id_must_name_its_owner(edited):
    """没有 ``mission_id`` 的业务表必须写明经哪张表找到任务（偏差裁决 1 第 3 条：不兜底）。

    **改坏检验**（G-04）：清单不查 ``owner`` → 删掉 ``planning_decisions`` 的 ``owner`` 不报错 → 变红。"""

    def no_owner(tables):
        del tables["planning_decisions"]["owner"]

    with pytest.raises(InventoryError, match="planning_decisions: a business table without mission_id names its owner"):
        edited(no_owner)


def test_deployment_identity_never_takes_an_unproven_replay(tmp_path):
    """部署身份里的重放结论只认 CONSISTENT，并且得带 v3 报告的哈希（裁决 G-9；F2 联测后上游局已跑过 v3）。

    **改坏检验**（G-15）：读取方接受任意取值 → 说"没跑 / 部分一致"的清单被接受 → 变红。"""

    from agent_orchestrator.orchestrator.taskgraph_deployment import InstalledHtnWiringAcceptance
    from agent_orchestrator.runtime.planning_operations import SourceUnavailable

    reader = InstalledHtnWiringAcceptance()
    original = json.loads(reader.manifest.read_text(encoding="utf-8"))
    assert original["upstream"]["business_replay"] == "CONSISTENT"
    reader._read()  # the installed manifest itself is accepted

    proven = original["upstream"]
    unproven = {k: v for k, v in proven.items() if k != "business_replay_receipt_sha256"}
    for value in (unproven, {**proven, "business_replay": "NOT_RUN"}, {**proven, "business_replay": "PARTIAL"},
                  {**proven, "business_replay_receipt_sha256": "not-a-digest"}):
        edited = tmp_path / "manifest.json"
        edited.write_text(json.dumps({**original, "upstream": value}), encoding="utf-8")
        reader.manifest = edited
        with pytest.raises(SourceUnavailable) as refused:
            reader._read()
        assert str(refused.value.__cause__) == "actual HTN wiring evidence is missing"


#: 允许打开可写连接的文件与理由（偏差裁决 2）。清单外的 ``sqlite3.connect`` 必须是只读打开。
WRITABLE_CONNECTIONS = {
    "storage/store.py": "存储层本身（备份目标是副本）",
    "observability/taskgraph_replay.py": "执行图历史重建到另一个新库",
    "domains/drone_sim.py": "无人机模拟自己的库",
    "evaluation/appworld_operations.py": "AppWorld 评测自己的库",
}


def test_only_the_store_opens_a_writable_connection():
    """编排库的可写连接只来自存储层：经它写的每一行都被整行事件记下，绕过它的写会让链断
    （HTN 补齐阶段 G 偏差裁决 2；运行时那一半是 ``test_an_out_of_band_write_breaks_the_chain``）。

    **改坏检验**（G-25）：组装处的只读打开去掉 ``mode=ro`` → 清单外多一个可写连接 → 变红。"""
    import ast
    from pathlib import Path

    import agent_orchestrator

    package = Path(agent_orchestrator.__file__).parent
    writable: dict[str, int] = {}
    for path in sorted(package.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "connect" and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "sqlite3"
                    and "mode=ro" not in (ast.get_source_segment(source, node) or "")):
                name = path.relative_to(package).as_posix()
                writable[name] = writable.get(name, 0) + 1
    assert set(writable) <= set(WRITABLE_CONNECTIONS), sorted(set(writable) - set(WRITABLE_CONNECTIONS))
    assert set(WRITABLE_CONNECTIONS) <= set(writable), (  # 清单过时也失败
        sorted(set(WRITABLE_CONNECTIONS) - set(writable)))
