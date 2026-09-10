# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""A6-7 判据修正: head 前进 + 血缘, 而不是「旧 revision 退出 active」。

`cognitive_memory_revisions` 挂着 `cognitive_memory_revisions_immutable_update`
/ `_immutable_delete` 两个无条件 `RAISE(ABORT)` 触发器
（记忆 SDK 的 schema_v5 后端，该 SDK 已于 2026-09-10 从 Host 移除）, SDK 在物理上无法回头
改写旧 revision 的 lifecycle_state。原判据因此恒为 FAIL —— 那是验证器缺陷,
不是 SDK 缺陷。契约 `slices/S3-cognitive-systems-recall.md:152` 「所有长期普通
候选先要求 exact principal、exact current head」: 生效的是 head 指针。
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_VERIFY = (
    Path(__file__).resolve().parents[3] / "scripts" / "native" / "a6_verify.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("a6_verify_under_test", _VERIFY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


a6 = _load()


def _hm_db(tmp_path: Path, heads, revisions, relations) -> "a6.RoDb":
    path = tmp_path / "human_memory_v7.db"
    conn = sqlite3.connect(path)
    conn.execute("create table cognitive_memory_heads(memory_id text primary key,"
                 " current_revision integer)")
    conn.execute("create table cognitive_memory_revisions(memory_id text, revision integer,"
                 " lifecycle_state text, conflict_status text)")
    conn.execute("create table cognitive_relations(relation_domain text, relation_kind text,"
                 " source_memory_id text, source_revision integer,"
                 " target_memory_id text, target_revision integer)")
    conn.executemany("insert into cognitive_memory_heads values (?,?)", heads)
    conn.executemany("insert into cognitive_memory_revisions values (?,?,?,?)", revisions)
    conn.executemany("insert into cognitive_relations values (?,?,?,?,?,?)", relations)
    conn.commit()
    conn.close()
    return a6.RoDb(str(path), "human_memory_v7.db")


def _run(tmp_path: Path, heads, revisions, relations):
    db = _hm_db(tmp_path, heads, revisions, relations)
    try:
        return a6.item_a6_7(SimpleNamespace(hm=db))
    finally:
        db.close()


# run4 证据的真实形状: 3 个 revision 全是 active, head=3, 两条 evolution 边。
_RUN4_REVISIONS = [
    ("m1", 1, "active", "uncontested"),
    ("m1", 2, "active", "uncontested"),
    ("m1", 3, "active", "contested"),
]
_RUN4_RELATIONS = [
    ("evolution", "amends", "m1", 2, "m1", 1),
    ("evolution", "contests", "m1", 3, "m1", 2),
]


def test_append_only_revisions_all_active_is_not_a_failure(tmp_path: Path) -> None:
    item = _run(tmp_path, [("m1", 3)], _RUN4_REVISIONS, _RUN4_RELATIONS)
    assert item.verdict == a6.PASS
    assert item.numbers["lifecycle_states_observed"] == ["active"]
    assert item.numbers["memories_with_multiple_revisions"] == 1


def test_head_that_did_not_advance_to_the_latest_revision_fails(tmp_path: Path) -> None:
    item = _run(tmp_path, [("m1", 2)], _RUN4_REVISIONS, _RUN4_RELATIONS)
    assert item.verdict == a6.FAIL
    assert item.numbers["head_not_latest_revision"] == [("m1", 2, 3)]


def test_new_revision_without_evolution_lineage_fails(tmp_path: Path) -> None:
    item = _run(tmp_path, [("m1", 3)], _RUN4_REVISIONS, _RUN4_RELATIONS[:1])
    assert item.verdict == a6.FAIL
    assert item.numbers["missing_evolution_lineage"] == [("m1", 3)]


def test_terminal_supersede_head_state_is_accepted(tmp_path: Path) -> None:
    # SUPERSEDE writes `superseded` onto the NEW head revision
    # (`core/mutations.py:423-425`); that is a legal head state, not a defect.
    revisions = [("m1", 1, "active", "uncontested"), ("m1", 2, "superseded", "uncontested")]
    item = _run(tmp_path, [("m1", 2)], revisions,
                [("evolution", "supersedes", "m1", 2, "m1", 1)])
    assert item.verdict == a6.PASS


def test_illegal_head_lifecycle_still_fails(tmp_path: Path) -> None:
    revisions = [("m1", 1, "active", "uncontested"), ("m1", 2, "forgotten", "uncontested")]
    item = _run(tmp_path, [("m1", 2)], revisions,
                [("evolution", "supersedes", "m1", 2, "m1", 1)])
    assert item.verdict == a6.FAIL
    assert item.numbers["head_lifecycle_invalid"] == [("m1", 2, "forgotten")]


def test_no_second_revision_stays_inconclusive(tmp_path: Path) -> None:
    item = _run(tmp_path, [("m1", 1)], [("m1", 1, "active", "uncontested")], [])
    assert item.verdict == a6.INCONCLUSIVE


def test_selftest_still_runs_every_item() -> None:
    assert a6.selftest() == 0
