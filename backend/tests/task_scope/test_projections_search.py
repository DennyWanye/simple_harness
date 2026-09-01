from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from deskpet.memory.schema import initialize_human_memory_program_state_db
from deskpet.task_scope.projections import (
    ProjectionIntegrityError,
    TaskScopeProjectionStore,
)
from deskpet.task_scope.search import TaskScopeSearchError, TaskScopeSearchStore
from deskpet.task_scope.store import CanonicalTaskScopeStore


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


@pytest.mark.asyncio
async def test_source_views_groups_rebuild_and_checkpoint_drift(tmp_path: Path) -> None:
    db_path, store = await _v40_db(tmp_path)
    await store.create_task_scope(
        task_scope_id="scope-1", subject="actor-1", title="Memory Project"
    )
    receipt = await store.append_deterministic_events(
        task_scope_id="scope-1", subject="actor-1", count=1001,
        canary="scope-1-canary", batch_size=128
    )
    assert [receipt.first_event_sequence, receipt.last_event_sequence] == [1, 1001]
    await store.create_checkpoint(
        checkpoint_id="checkpoint-1",
        task_scope_id="scope-1",
        metadata={
            "repo": "/repo",
            "branch": "main",
            "head": "abc",
            "dirty": False,
            "files": ["a.py"],
            "tests": ["pytest"],
            "artifacts": [],
            "next_action": "continue",
        },
    )

    projections = TaskScopeProjectionStore(db_path)
    views = await projections.materialize(task_scope_id="scope-1")
    assert set(views) == {"README", "PLAN", "STATUS", "DECISIONS", "RESUME", "EVIDENCE"}
    assert len(views["README"].content.encode()) <= 16 * 1024
    assert len(views["STATUS"].content.encode()) <= 12 * 1024
    assert len(views["RESUME"].content.encode()) <= 24 * 1024
    evidence = json.loads(views["EVIDENCE"].content)
    groups = await projections.list_evidence_groups(task_scope_id="scope-1")
    assert [group["event_count"] for group in groups] == [500, 500, 1]
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM task_scope_projection_sources").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM task_scope_projection_source_outbox").fetchone()[0] == 3
        assert db.execute("SELECT MAX(length(content)) FROM task_scope_read_blocks").fetchone()[0] <= 32768
        before = db.execute(
            "SELECT block_id,content_sha256 FROM task_scope_read_blocks ORDER BY block_id"
        ).fetchall()
        db.execute("DELETE FROM task_scope_read_blocks")
        db.commit()
    rebuilt = await projections.materialize(task_scope_id="scope-1")
    assert rebuilt == views
    with sqlite3.connect(db_path) as db:
        after = db.execute(
            "SELECT block_id,content_sha256 FROM task_scope_read_blocks ORDER BY block_id"
        ).fetchall()
    assert after == before
    block = await projections.read_block(views["EVIDENCE"].root_block_id or "")
    assert hashlib.sha256(block).hexdigest()
    clean = await projections.verify_checkpoint(
        task_scope_id="scope-1",
        live_probe={
            "repo": "/repo",
            "branch": "main",
            "head": "abc",
            "dirty": False,
            "files": ["a.py"],
            "tests": ["pytest"],
            "artifacts": [],
            "next_action": "continue",
        },
    )
    assert clean.drifted is False
    drift = await projections.verify_checkpoint(
        task_scope_id="scope-1",
        live_probe={"repo": "/repo", "branch": "main", "head": "def"},
    )
    assert drift.drifted is True
    assert "head" in drift.changed_fields


@pytest.mark.asyncio
async def test_permission_first_search_exact_open_and_unavailable(tmp_path: Path) -> None:
    db_path, store = await _v40_db(tmp_path)
    await store.create_task_scope(
        task_scope_id="allowed", subject="actor-1", title="Alpha launch"
    )
    await store.create_task_scope(
        task_scope_id="foreign", subject="actor-2", title="Alpha secret"
    )
    search = TaskScopeSearchStore(db_path)
    await search.rebuild_index()
    before: tuple[object, ...]
    with sqlite3.connect(db_path) as db:
        before = (
            db.execute("SELECT COUNT(*) FROM task_scope_heads").fetchone()[0],
            db.execute("SELECT COUNT(*) FROM task_workspace_binding_heads").fetchone()[0],
        )
    result = await search.search(
        subject="actor-1",
        allowed_scope_ids=["allowed", "foreign"],
        query="Alpha",
        limit=10,
    )
    assert [candidate.task_scope_id for candidate in result.candidates] == ["allowed"]
    with sqlite3.connect(db_path) as db:
        after = (
            db.execute("SELECT COUNT(*) FROM task_scope_heads").fetchone()[0],
            db.execute("SELECT COUNT(*) FROM task_workspace_binding_heads").fetchone()[0],
        )
    assert after == before
    opened = await search.open_exact(
        subject="actor-1",
        allowed_scope_ids=["allowed"],
        task_scope_id="allowed",
        expected_source_hash=result.candidates[0].source_hash,
        live_probe={"head": "changed"},
    )
    assert len(json.dumps(opened.resume_package, ensure_ascii=False).encode()) <= 24 * 1024
    assert "Alpha" not in canonical_query_fields(opened.resume_package)
    assert opened.drift_report is not None
    assert opened.drift_report.drifted is True
    with pytest.raises(TaskScopeSearchError, match="human_memory_permission_denied"):
        await search.open_exact(
            subject="actor-1",
            allowed_scope_ids=["allowed"],
            task_scope_id="foreign",
        )
    with sqlite3.connect(db_path) as db:
        db.execute("DROP TABLE task_scope_search_fts")
        db.commit()
    with pytest.raises(TaskScopeSearchError, match="human_memory_search_unavailable"):
        await search.search(
            subject="actor-1",
            allowed_scope_ids=["allowed"],
            query="Alpha",
        )


def canonical_query_fields(package: dict[str, object]) -> str:
    # Search queries/snippets are never part of the exact-open authority package.
    return " ".join(str(key).lower() for key in package)


@pytest.mark.asyncio
async def test_public_bulk_seam_materializes_100k_in_500_event_groups(
    tmp_path: Path,
) -> None:
    db_path, store = await _v40_db(tmp_path)
    await store.create_task_scope(
        task_scope_id="scope-large", subject="actor-1", title="Large history"
    )
    receipt = await store.append_deterministic_events(
        task_scope_id="scope-large", subject="actor-1", count=100_000,
        canary="scope-large-canary", batch_size=2000
    )
    assert receipt.count == 100_000
    assert receipt.last_event_sequence == 100_000
    evidence = await TaskScopeProjectionStore(db_path).read_view(
        "EVIDENCE", task_scope_id="scope-large"
    )
    manifest = json.loads(evidence.content)
    assert manifest["event_count"] == 100_000
    assert manifest["logical_group_count"] == 200
    groups = await TaskScopeProjectionStore(db_path).list_evidence_groups(
        task_scope_id="scope-large"
    )
    assert len(groups) == 200
    assert all(group["event_count"] == 500 for group in groups)
    recovered: list[int] = []
    projection_store = TaskScopeProjectionStore(db_path)
    for group in groups:
        events = await projection_store.read_evidence_group(str(group["block_id"]))
        recovered.extend(int(event["payload"]["event_index"]) for event in events)
    assert recovered == list(range(1, 100_001))
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT MAX(length(content)) FROM task_scope_read_blocks").fetchone()[0] <= 32768
        assert db.execute(
            "SELECT COUNT(*) FROM task_scope_projection_source_outbox WHERE task_scope_id='scope-large'"
        ).fetchone()[0] == 2


@pytest.mark.asyncio
async def test_evidence_group_reassembles_oversized_event_and_fails_on_missing_ref(
    tmp_path: Path,
) -> None:
    db_path, store = await _v40_db(tmp_path)
    await store.create_task_scope(
        task_scope_id="scope-chunked", subject="actor-1", title="Chunked history"
    )
    await store.append_host_event(
        task_scope_id="scope-chunked",
        event_kind="host.turn",
        source_event_id="oversized-event",
        payload={"event_index": 1, "body": "记" * 70_000},
    )
    projections = TaskScopeProjectionStore(db_path)
    await projections.materialize(task_scope_id="scope-chunked")
    groups = await projections.list_evidence_groups(task_scope_id="scope-chunked")
    events = await projections.read_evidence_group(str(groups[0]["block_id"]))
    assert events[0]["payload"]["body"] == "记" * 70_000

    group = json.loads(await projections.read_block(str(groups[0]["block_id"])))
    root_ref = group["leaves_root"]
    if root_ref["block_kind"] == "leaf":
        missing_id = root_ref["block_id"]
    else:
        index = json.loads(await projections.read_block(root_ref["block_id"]))
        missing_id = index["children"][0]["block_id"]
    with sqlite3.connect(db_path) as db:
        db.execute("DELETE FROM task_scope_read_blocks WHERE block_id=?", (missing_id,))
        db.commit()
    with pytest.raises(ProjectionIntegrityError, match="task_scope_projection_block_missing"):
        await projections.read_evidence_group(str(groups[0]["block_id"]))
