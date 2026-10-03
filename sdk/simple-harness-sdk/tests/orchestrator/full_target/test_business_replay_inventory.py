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

    for change, message in ((no_rebuild, "missions: a business table lists writers, events and rebuild"),
                            (unknown_rebuild, "missions: rebuild must be one of"),
                            (silent_exclusion, "workspaces: a table outside business says why")):
        with pytest.raises(InventoryError, match=message):
            edited(change)


def test_a_folded_table_with_gaps_is_not_covered(tmp_path, monkeypatch):
    """有 ``gaps`` 的折叠表即使有折叠函数也报"未覆盖"（裁决 G-1：gaps 必须为空才算覆盖）。"""

    entry = {"class": "business", "rebuild": "fold", "rebuilder": "probe", "gaps": ["x.py:writer"]}
    monkeypatch.setitem(business_replay.FOLDERS, "probe", lambda store, mission_id, events: [])
    store = Store.open(tmp_path / "o.db")
    try:
        result = business_replay._folded_table(store, "m", "missions", entry)
    finally:
        store.close()
    assert result["status"] == business_replay.NOT_COVERED
