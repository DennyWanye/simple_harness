# SPDX-License-Identifier: Apache-2.0
"""第 1 批 T08：清单绑定按输入版本号各记一行，尝试身份守卫恢复为"绑定版本 = 本次输入版本"。

迁移 30 曾把 ``tg_attempt_identity_guard`` 的相等放宽成 ``<=``，原因是 ``input_manifest_bindings``
主键只有 ``(mission_id, task_id, manifest_hash)``：换代后同一份内容只留首次绑定那一行。迁移 44
把 ``input_binding_revision`` 加进主键，守卫回到原计划（``taskgraph_schema.py`` V25 文本）的相等。

**改坏检验**：``insert_input_manifest`` 的 ``ON CONFLICT`` 改回三列 → 第一条变红；守卫改回 ``<=`` →
第二条变红；``taskgraph_attempt_inputs`` 两处读去掉 ``input_binding_revision=?`` → 第三条变红。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_orchestrator.contracts import Budget, Mission, MissionStatus
from agent_orchestrator.storage import schema
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.store import Store

MISSION = "mission-1"
DOCUMENT = {"inputs": [{"port": "source", "artifact_id": "artifact-1"}]}


def _mission(mission_id: str = MISSION) -> Mission:
    return Mission(id=mission_id, goal="g", success_criteria=("ok",), stop_conditions=(), allowed_tools=(),
                   risk_level="sandbox", budget=Budget(max_tokens=1000, max_attempts=2), tenant_id="t",
                   status=MissionStatus.CREATED, created_at=1.0, version=1, idempotency_key=mission_id)


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store.open(tmp_path / "orchestrator.db")
    opened.insert_mission(_mission(), spec_hash="h")
    return opened


def _bindings(store: Store) -> list[tuple[int, str | None]]:
    rows = store.connection.execute(
        "SELECT input_binding_revision, request_id FROM input_manifest_bindings WHERE mission_id=? AND task_id=? "
        "ORDER BY input_binding_revision", (MISSION, "task-1")).fetchall()
    return [(int(row[0]), row[1]) for row in rows]


def test_the_same_manifest_is_bound_once_per_input_revision(store: Store) -> None:
    htn = HtnStore(store)
    digest = htn.insert_input_manifest(MISSION, "task-1", DOCUMENT, input_binding_revision=0, request_id="r-0")
    assert htn.insert_input_manifest(MISSION, "task-1", DOCUMENT, input_binding_revision=1, request_id="r-1") == digest
    # 同一版本再绑一次：原行不动（不是第二行，也不报错）
    assert htn.insert_input_manifest(MISSION, "task-1", DOCUMENT, input_binding_revision=1) == digest
    assert _bindings(store) == [(0, "r-0"), (1, "r-1")]
    assert store.connection.execute("SELECT count(*) FROM input_manifests").fetchone()[0] == 1
    assert len(htn.list_manifest_bindings(digest)) == 2
    columns = store.connection.execute("PRAGMA table_info(input_manifest_bindings)").fetchall()
    primary_key = [row[1] for row in sorted((r for r in columns if r[5]), key=lambda r: r[5])]
    assert primary_key == ["mission_id", "task_id", "manifest_hash", "input_binding_revision"]


def test_the_attempt_identity_guard_requires_the_exact_binding_revision(store: Store) -> None:
    rows = {row[0]: row[1] for row in store.connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND ("
        "name='tg_attempt_identity_guard' OR name LIKE '%input_manifest_bindings%')")}
    guard = rows["tg_attempt_identity_guard"]
    assert "b.input_binding_revision=NEW.input_binding_revision" in guard
    assert "<=NEW.input_binding_revision" not in guard
    # 表重建后触发器与索引齐全
    assert {"input_manifest_bindings_immutable_update", "input_manifest_bindings_immutable_delete",
            "assurance_source_input_manifest_bindings_insert", "assurance_source_input_manifest_bindings_update",
            "assurance_source_input_manifest_bindings_delete"} <= set(rows)
    indexes = {row[0] for row in store.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='input_manifest_bindings'")}
    assert {"input_manifest_bindings_task_idx", "input_manifest_bindings_hash_idx",
            "input_manifest_bindings_request_idx"} <= indexes
    with pytest.raises(Exception, match="immutable source record"):
        store.connection.execute("DELETE FROM input_manifest_bindings")
    assert schema.SCHEMA_VERSION == 44 and schema.MIGRATIONS[-1].name == "orchestrator-manifest-binding-per-input-revision"
    assert "input_binding_revision=NEW.input_binding_revision" in schema.DDL_V44


def test_frozen_input_reads_require_the_exact_binding_revision() -> None:
    import agent_orchestrator.storage.taskgraph_attempt_inputs as module
    import agent_orchestrator.storage.assurance_source_inventory as inventory

    text = Path(module.__file__).read_text(encoding="utf-8")
    queries = re.findall(r"SELECT input_binding_revision FROM input_manifest_bindings WHERE mission_id=\? \"\s*\"AND task_id=\? AND manifest_hash=\?([^\"]*)\"", text)
    assert len(queries) == 2 and all("AND input_binding_revision=?" in q for q in queries), queries
    assert "or earlier" not in text and "schema v30" not in text
    assert inventory.SOURCE_PRIMARY_KEYS["input_manifest_bindings"] == (
        "mission_id", "task_id", "manifest_hash", "input_binding_revision")
