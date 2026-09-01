from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from deskpet.execution.recovery_fence import (
    HumanMemoryIngressFenced,
    HumanMemoryRecoveryCoordinator,
    HumanMemoryRecoveryError,
)
from deskpet.memory.migrator import ensure_v9
from deskpet.memory.recovery_fence import build_recovery_lifecycle_port
from deskpet.memory.schema import (
    InitializeError,
    _write_bootstrap_marker,
    initialize_human_memory_program_state_db,
)
from deskpet.task_scope.projections import TaskScopeProjectionStore
from deskpet.task_scope.store import CanonicalTaskScopeStore


@pytest.mark.asyncio
async def test_recovery_fence_manifest_export_and_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store = CanonicalTaskScopeStore(db_path)
    await store.create_task_scope(
        task_scope_id="scope-1", subject="actor-1", title="Long memory"
    )
    await store.append_host_event(
        task_scope_id="scope-1",
        event_kind="host.turn",
        source_event_id="turn-1",
        payload={"event_index": 1, "text": "safe"},
    )
    coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
        db_path, export_root=tmp_path / "exports"
    )
    closing = await coordinator.begin_close()
    assert closing.state == "CLOSING"
    with pytest.raises(HumanMemoryIngressFenced, match="human_memory_ingress_fenced"):
        await store.append_host_event(
            task_scope_id="scope-1",
            event_kind="host.turn",
            source_event_id="turn-fenced",
            payload={"event_index": 2},
        )
    with (
        sqlite3.connect(db_path) as db,
        pytest.raises(sqlite3.IntegrityError, match="human_memory_ingress_fenced"),
    ):
        db.execute(
            "INSERT INTO task_scopes(task_scope_id,subject,title,created_at) "
            "VALUES ('unregistered','actor-1','bad',1)"
        )
    receipts = await coordinator.drain_or_park(action="park")
    assert len(receipts) == 5
    assert (await coordinator.quiesce()).state == "QUIESCED"
    assert len(await coordinator.checkpoint_wal()) == 64
    manifest = await coordinator.seal()
    assert (await coordinator.snapshot()).state == "SEALED"
    await coordinator.verify_manifest(manifest.manifest_id)
    exported = await coordinator.emergency_export(export_id="export-1")
    content = exported.artifact_path.read_bytes()
    assert hashlib.sha256(content).hexdigest() == exported.artifact_sha256
    assert all(len(line) <= 32 * 1024 for line in content.splitlines())
    assert b"password" not in content.lower()
    assert await coordinator.emergency_export(export_id="export-1") == exported
    assert (await coordinator.reopen()).state == "OPEN"
    appended = await store.append_host_event(
        task_scope_id="scope-1",
        event_kind="host.turn",
        source_event_id="turn-after-reopen",
        payload={"event_index": 2},
    )
    assert appended.event_sequence == 2


@pytest.mark.asyncio
async def test_registry_unknown_column_fails_closed_before_manifest(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    await CanonicalTaskScopeStore(db_path).create_task_scope(
        task_scope_id="scope-1", subject="actor-1", title="Memory"
    )
    coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
        db_path, export_root=tmp_path / "exports"
    )
    with sqlite3.connect(db_path) as db:
        db.execute("ALTER TABLE task_scopes ADD COLUMN surprise TEXT")
        db.commit()
    with pytest.raises(
        HumanMemoryRecoveryError,
        match="human_memory_recovery_unknown_protected_column",
    ):
        await coordinator.begin_close()
    assert (await coordinator.snapshot()).state == "FAILED_CLOSED"


@pytest.mark.asyncio
async def test_concurrent_close_has_one_generation_and_stable_fence(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    await CanonicalTaskScopeStore(db_path).create_task_scope(
        task_scope_id="scope-1", subject="actor-1", title="Memory"
    )
    first = await HumanMemoryRecoveryCoordinator.bind_for_host(
        db_path, export_root=tmp_path / "exports"
    )
    second = await HumanMemoryRecoveryCoordinator.bind_for_host(
        db_path, export_root=tmp_path / "exports"
    )
    snapshots = await asyncio.gather(first.begin_close(), second.begin_close())
    assert {item.state for item in snapshots} == {"CLOSING"}
    assert {item.generation for item in snapshots} == {2}
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM human_memory_recovery_transitions "
            "WHERE generation=2 AND to_state='CLOSING'"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_public_lifecycle_builder_is_subject_bound_and_restart_safe(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    await CanonicalTaskScopeStore(db_path).create_task_scope(
        task_scope_id="scope-1", subject="actor-1", title="Memory"
    )
    port = build_recovery_lifecycle_port(
        db_path=db_path, artifact_dir=tmp_path / "exports"
    )
    first = await port.manifest(subject="actor-1")
    assert first["receipt_ref"] == first["manifest_ref"]
    coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
        db_path, export_root=tmp_path / "exports"
    )
    assert (await coordinator.snapshot()).state == "OPEN"
    second = await port.manifest(subject="actor-1")
    assert int(second["generation"]) == int(first["generation"]) + 1
    assert (await coordinator.snapshot()).state == "OPEN"
    store = CanonicalTaskScopeStore(db_path)
    assert (
        await store.append_host_event(
            task_scope_id="scope-1",
            event_kind="host.turn",
            source_event_id="after-public-manifest",
            payload={"event_index": 1},
        )
    ).event_sequence == 1
    exported = await port.emergency_export(subject="actor-1")
    assert Path(str(exported["artifact_path"])).is_file()
    assert (await coordinator.snapshot()).state == "OPEN"
    assert (
        await store.append_host_event(
            task_scope_id="scope-1",
            event_kind="host.turn",
            source_event_id="after-public-export",
            payload={"event_index": 2},
        )
    ).event_sequence == 2
    with pytest.raises(
        HumanMemoryRecoveryError, match="human_memory_recovery_subject_mismatch"
    ):
        await port.manifest(subject="actor-2")


@pytest.mark.asyncio
async def test_public_manifest_resumes_existing_sealed_generation_and_reopens(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    await CanonicalTaskScopeStore(db_path).create_task_scope(
        task_scope_id="scope-1", subject="actor-1", title="Memory"
    )
    coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
        db_path, export_root=tmp_path / "exports"
    )
    await coordinator.begin_close()
    await coordinator.drain_or_park(action="park")
    await coordinator.quiesce()
    await coordinator.checkpoint_wal()
    sealed = await coordinator.seal()
    port = build_recovery_lifecycle_port(
        db_path=db_path, artifact_dir=tmp_path / "exports"
    )
    resumed = await port.manifest(subject="actor-1")
    assert resumed["manifest_ref"] == sealed.manifest_id
    assert resumed["receipt_ref"] == sealed.manifest_id
    assert (await coordinator.snapshot()).state == "OPEN"


@pytest.mark.asyncio
async def test_concurrent_and_repeated_public_manifest_calls_leave_ingress_open(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    await CanonicalTaskScopeStore(db_path).create_task_scope(
        task_scope_id="scope-1", subject="actor-1", title="Memory"
    )
    port = build_recovery_lifecycle_port(
        db_path=db_path, artifact_dir=tmp_path / "exports"
    )
    concurrent = await asyncio.gather(
        *(port.manifest(subject="actor-1") for _ in range(3))
    )
    assert sorted(int(item["generation"]) for item in concurrent) == [2, 3, 4]
    assert len({str(item["receipt_ref"]) for item in concurrent}) == 3
    repeated = await port.manifest(subject="actor-1")
    assert repeated["generation"] == 5
    coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
        db_path, export_root=tmp_path / "exports"
    )
    assert (await coordinator.snapshot()).state == "OPEN"


def test_public_port_survives_adapter_style_event_loop_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    asyncio.run(
        CanonicalTaskScopeStore(db_path).create_task_scope(
            task_scope_id="scope-1", subject="actor-1", title="Memory"
        )
    )
    port = build_recovery_lifecycle_port(
        db_path=db_path, artifact_dir=tmp_path / "exports"
    )
    manifest = asyncio.run(port.manifest(subject="actor-1"))
    exported = asyncio.run(port.emergency_export(subject="actor-1"))
    assert manifest["receipt_ref"] == manifest["manifest_ref"]
    assert Path(str(exported["artifact_path"])).is_file()
    coordinator = asyncio.run(
        HumanMemoryRecoveryCoordinator.bind_for_host(
            db_path, export_root=tmp_path / "exports"
        )
    )
    assert asyncio.run(coordinator.snapshot()).state == "OPEN"


@pytest.mark.asyncio
async def test_public_reopen_failure_remains_failed_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "state.db"
    await CanonicalTaskScopeStore(db_path).create_task_scope(
        task_scope_id="scope-1", subject="actor-1", title="Memory"
    )
    port = build_recovery_lifecycle_port(
        db_path=db_path, artifact_dir=tmp_path / "exports"
    )
    coordinator = await port._bound_coordinator()

    async def fail_reopen():
        raise HumanMemoryRecoveryError("human_memory_recovery_reopen_fault")

    monkeypatch.setattr(coordinator, "_reopen", fail_reopen)
    with pytest.raises(
        HumanMemoryRecoveryError, match="human_memory_recovery_reopen_fault"
    ):
        await port.manifest(subject="actor-1")
    assert (await coordinator.snapshot()).state == "FAILED_CLOSED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    (
        "before_034_human_memory_recovery_v42_commit",
        "after_034_human_memory_recovery_v42_commit",
    ),
)
async def test_v42_fault_restart_has_one_exact_marker(
    tmp_path: Path, stage: str
) -> None:
    db_path = tmp_path / "state.db"

    def crash(actual: str) -> None:
        if actual == stage:
            raise RuntimeError(f"crash:{stage}")

    with pytest.raises(InitializeError, match=stage):
        await initialize_human_memory_program_state_db(db_path, fault_inject=crash)
    await initialize_human_memory_program_state_db(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 42
        assert db.execute(
            "SELECT COUNT(*) FROM human_memory_recovery_marker"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM human_memory_migration_chain "
            "WHERE schema_version=42"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM human_memory_recovery_transitions "
            "WHERE transition_id='human-memory-recovery-genesis'"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_export_corruption_is_detected_and_fails_closed(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    await CanonicalTaskScopeStore(db_path).create_task_scope(
        task_scope_id="scope-1", subject="actor-1", title="Memory"
    )
    coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
        db_path, export_root=tmp_path / "exports"
    )
    await coordinator.begin_close()
    await coordinator.drain_or_park(action="park")
    await coordinator.quiesce()
    await coordinator.checkpoint_wal()
    await coordinator.seal()
    exported = await coordinator.emergency_export(export_id="corrupt-export")
    exported.artifact_path.write_bytes(b"corrupt")
    with pytest.raises(
        HumanMemoryRecoveryError, match="human_memory_export_artifact_mismatch"
    ):
        await coordinator.emergency_export(export_id="corrupt-export")
    assert (await coordinator.snapshot()).state == "FAILED_CLOSED"


@pytest.mark.asyncio
async def test_busy_wal_checkpoint_fails_closed_without_manifest(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    await CanonicalTaskScopeStore(db_path).create_task_scope(
        task_scope_id="scope-1", subject="actor-1", title="Memory"
    )
    coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
        db_path, export_root=tmp_path / "exports"
    )
    reader = sqlite3.connect(db_path)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM task_scopes").fetchall()
        await coordinator.begin_close()
        await coordinator.drain_or_park(action="park")
        await coordinator.quiesce()
        with pytest.raises(
            HumanMemoryRecoveryError,
            match="human_memory_recovery_checkpoint_busy",
        ):
            await coordinator.checkpoint_wal()
    finally:
        reader.rollback()
        reader.close()
    assert (await coordinator.snapshot()).state == "FAILED_CLOSED"
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM human_memory_recovery_manifests"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_v38_resume_backfills_exact_projection_source_in_migration_tx(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    _write_bootstrap_marker(db_path)

    def stop_after_v38(stage: str) -> None:
        if stage == "after_task_workspace_binding_commit":
            raise RuntimeError("stop-after-v38")

    with pytest.raises(RuntimeError, match="stop-after-v38"):
        await ensure_v9(
            db_path,
            include_human_memory_program=True,
            fault_inject=stop_after_v38,
        )
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 38
        db.execute(
            "INSERT INTO human_memory_primary_conversations("
            "primary_conversation_id,subject,writable,created_at) "
            "VALUES ('primary-1','actor-1',1,1)"
        )
        db.execute(
            "INSERT INTO task_scopes(task_scope_id,subject,title,created_at) "
            "VALUES ('scope-old','actor-1','Existing v38 task',1)"
        )
        state = {
            "schema_version": 1,
            "task_scope_id": "scope-old",
            "subject": "actor-1",
            "title": "Existing v38 task",
            "status": "active",
            "goal": None,
            "resume": None,
            "operations": [],
        }
        state_json = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        state_hash = hashlib.sha256(state_json.encode()).hexdigest()
        db.execute(
            "INSERT INTO task_scope_canonical_revisions("
            "task_scope_id,revision,prior_revision,decision_id,state_hash,state_json,event_watermark,created_at) "
            "VALUES ('scope-old',1,NULL,NULL,?,?,0,1)",
            (state_hash, state_json),
        )
        db.execute(
            "INSERT INTO task_scope_heads(task_scope_id,current_revision,event_watermark,state_hash,updated_at) "
            "VALUES ('scope-old',1,0,?,1)",
            (state_hash,),
        )
        db.commit()
    await initialize_human_memory_program_state_db(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 42
        assert db.execute(
            "SELECT COUNT(*) FROM task_scope_projection_sources WHERE task_scope_id='scope-old'"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM task_scope_projection_source_outbox WHERE task_scope_id='scope-old'"
        ).fetchone()[0] == 1
    readme = await TaskScopeProjectionStore(db_path).read_view(
        "README", task_scope_id="scope-old"
    )
    assert "Existing v38 task" in readme.content
