# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 AC: EVIDENCE 清单的 `event_count` 是**视图水位上**的 canonical 事件数。

视图按读取时物化, 物化之后追加的事件不属于这条修订; 所以 `event_count` 只能和
「同一 task_scope_id 且 event_sequence <= 该视图 event_watermark」的行数比,
不能和事件表的当前行数(更不能和跨 scope 的整表行数)比。清单里现在把
`event_watermark` 一并写出来, 让这条口径能被视图自身证明。

另一半是 16384 边界: README 超限走 bounded 截断, 但截断只动 README 的字节,
EVIDENCE 的计数始终来自 archive, 不来自被截断的切片。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from deskpet.memory.schema import initialize_human_memory_program_state_db
from deskpet.task_scope.projections import VIEW_LIMITS, TaskScopeProjectionStore
from deskpet.task_scope.store import CanonicalTaskScopeStore

_BOUNDED_TAIL = "…[bounded; details are content-addressed in EVIDENCE]"


async def _v40_db(tmp_path: Path) -> tuple[Path, CanonicalTaskScopeStore]:
    db_path = tmp_path / "state.db"
    await initialize_human_memory_program_state_db(db_path)
    with sqlite3.connect(db_path) as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        migrations = Path(__file__).parents[2] / "deskpet" / "memory" / "migrations"
        if "task_scope_projection_sources" not in tables:
            db.executescript((migrations / "031_task_scope_projections_v39.sql").read_text())
        if "task_scope_search_documents" not in tables:
            db.executescript((migrations / "032_task_scope_search_v40.sql").read_text())
    return db_path, CanonicalTaskScopeStore(db_path)


def _stored_evidence_manifests(db_path: Path, task_scope_id: str) -> list[dict]:
    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            "SELECT content FROM task_scope_read_view_revisions"
            " WHERE task_scope_id=? AND view_kind='EVIDENCE' ORDER BY created_at",
            (task_scope_id,),
        ).fetchall()
    return [json.loads(bytes(row[0]).decode()) for row in rows]


def _events_at(db_path: Path, task_scope_id: str, watermark: int) -> int:
    with sqlite3.connect(db_path) as db:
        return int(
            db.execute(
                "SELECT COUNT(*) FROM task_scope_events"
                " WHERE task_scope_id=? AND event_sequence<=?",
                (task_scope_id, watermark),
            ).fetchone()[0]
        )


@pytest.mark.asyncio
async def test_evidence_count_is_pinned_to_the_view_watermark(tmp_path: Path) -> None:
    """第 11 次 3a5016d0 的形状: 视图停在水位 20, 事件表已经有 25 行。"""

    db_path, store = await _v40_db(tmp_path)
    await store.create_task_scope(
        task_scope_id="scope-watermark", subject="actor-1", title="Watermark"
    )
    await store.append_deterministic_events(
        task_scope_id="scope-watermark", subject="actor-1", count=20,
        canary="scope-watermark-canary", batch_size=20,
    )
    projections = TaskScopeProjectionStore(db_path)
    first = json.loads((await projections.materialize(task_scope_id="scope-watermark"))["EVIDENCE"].content)
    assert first["event_watermark"] == 20
    assert first["event_count"] == 20

    # 物化之后再追加 5 条(现实里就是「触发这次读取的那几条事件」), 不重新物化。
    await store.append_deterministic_events(
        task_scope_id="scope-watermark", subject="actor-1", count=5,
        canary="scope-watermark-canary-2", batch_size=5,
    )
    with sqlite3.connect(db_path) as db:
        total = int(
            db.execute(
                "SELECT COUNT(*) FROM task_scope_events WHERE task_scope_id=?",
                ("scope-watermark",),
            ).fetchone()[0]
        )
    assert total == 25
    stored = _stored_evidence_manifests(db_path, "scope-watermark")
    assert [m["event_count"] for m in stored] == [20]
    # 已落库的那条修订仍然只说 20 —— 它在自己的水位上一条不丢, 但不等于事件表当前行数。
    assert stored[-1]["event_count"] == _events_at(db_path, "scope-watermark", stored[-1]["event_watermark"])
    assert stored[-1]["event_count"] != total

    # 再物化一次: 水位推进到 25, 计数跟着推进, 从不倒退。
    second = json.loads((await projections.materialize(task_scope_id="scope-watermark"))["EVIDENCE"].content)
    assert (second["event_watermark"], second["event_count"]) == (25, 25)
    assert second["event_count"] == _events_at(db_path, "scope-watermark", 25) == total
    stored = _stored_evidence_manifests(db_path, "scope-watermark")
    assert [m["event_watermark"] for m in stored] == [20, 25]
    assert [m["event_count"] for m in stored] == [20, 25]


@pytest.mark.asyncio
async def test_two_scopes_do_not_share_one_event_count(tmp_path: Path) -> None:
    """第 11 次 FAIL 的另一半: 两个 scope 的事件在同一张表里, 计数不许跨 scope 相加。"""

    db_path, store = await _v40_db(tmp_path)
    for scope, count in (("scope-long", 151), ("scope-short", 25)):
        await store.create_task_scope(
            task_scope_id=scope, subject="actor-1", title=f"Scope {scope}"
        )
        await store.append_deterministic_events(
            task_scope_id=scope, subject="actor-1", count=count,
            canary=f"{scope}-canary", batch_size=64,
        )
    projections = TaskScopeProjectionStore(db_path)
    long_view = json.loads((await projections.materialize(task_scope_id="scope-long"))["EVIDENCE"].content)
    short_view = json.loads((await projections.materialize(task_scope_id="scope-short"))["EVIDENCE"].content)
    with sqlite3.connect(db_path) as db:
        whole_table = int(db.execute("SELECT COUNT(*) FROM task_scope_events").fetchone()[0])
    assert whole_table == 176
    assert (long_view["event_watermark"], long_view["event_count"]) == (151, 151)
    assert (short_view["event_watermark"], short_view["event_count"]) == (25, 25)
    assert long_view["event_count"] == _events_at(db_path, "scope-long", 151)
    assert short_view["event_count"] == _events_at(db_path, "scope-short", 25)


async def _scope_with_goal(
    store: CanonicalTaskScopeStore, scope_id: str, goal: str
) -> None:
    await store.create_task_scope(
        task_scope_id=scope_id, subject="actor-1", title="Boundary", goal=goal
    )
    await store.append_deterministic_events(
        task_scope_id=scope_id, subject="actor-1", count=7,
        canary=f"{scope_id}-canary", batch_size=7,
    )


@pytest.mark.asyncio
async def test_readme_16384_boundary_never_moves_the_evidence_count(tmp_path: Path) -> None:
    """正好 16384 字节不截断, 16385 字节截断; 两边 EVIDENCE 计数都来自 archive。"""

    db_path, store = await _v40_db(tmp_path)
    projections = TaskScopeProjectionStore(db_path)
    cap = VIEW_LIMITS["README"]
    assert cap == 16384

    # 先量一次 README 的固定开销(标题/状态/id/source_hash 都是定长), 再反推 goal 长度。
    await _scope_with_goal(store, "scope-probe-a", "g" * 1024)
    probe = (await projections.materialize(task_scope_id="scope-probe-a"))["README"]
    overhead = len(probe.content.encode()) - 1024

    await _scope_with_goal(store, "scope-exact-b", "g" * (cap - overhead))
    exact = (await projections.materialize(task_scope_id="scope-exact-b"))["README"]
    assert len(exact.content.encode()) == cap
    assert _BOUNDED_TAIL not in exact.content

    await _scope_with_goal(store, "scope-over--c", "g" * (cap - overhead + 1))
    over = (await projections.materialize(task_scope_id="scope-over--c"))["README"]
    assert len(over.content.encode()) <= cap
    assert over.content.endswith(_BOUNDED_TAIL + "\n")

    for scope in ("scope-exact-b", "scope-over--c"):
        manifest = json.loads(
            (await projections.read_view("EVIDENCE", task_scope_id=scope)).content
        )
        assert manifest["event_watermark"] == 7
        assert manifest["event_count"] == 7 == _events_at(db_path, scope, 7)
        assert len(manifest["canonical_archive_block_id"]) > 0
