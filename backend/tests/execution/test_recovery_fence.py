from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from deskpet.execution import (
    ContextLineage,
    ForegroundQueueError,
    ForegroundQueueStore,
)
from deskpet.execution.recovery_fence import (
    HumanMemoryIngressFenced,
    HumanMemoryRecoveryCoordinator,
    HumanMemoryRecoveryError,
)
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.migrator import HUMAN_MEMORY_TARGET_SCHEMA_VERSION, ensure_v9
from deskpet.memory.recovery_fence import build_recovery_lifecycle_port
from deskpet.memory.recovery_work_items import is_human_memory_work_item_parked_tx
from deskpet.memory.schema import (
    InitializeError,
    _write_bootstrap_marker,
    initialize_human_memory_program_state_db,
)
from deskpet.task_scope.projections import (
    ProjectionIntegrityError,
    TaskScopeProjectionStore,
)
from deskpet.task_scope.search import TaskScopeSearchStore
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
    with (
        sqlite3.connect(db_path) as db,
        pytest.raises(sqlite3.IntegrityError, match="human_memory_ingress_fenced"),
    ):
        db.execute(
            "INSERT INTO foreground_preparation_drafts(draft_id) "
            "VALUES ('must-be-fenced-before-column-validation')"
        )
    receipts = await coordinator.drain_or_park(action="park")
    assert len(receipts) == 5
    assert (await coordinator.quiesce()).state == "QUIESCED"
    assert len(await coordinator.checkpoint_wal()) == 64
    manifest = await coordinator.seal()
    assert (await coordinator.snapshot()).state == "SEALED"
    await coordinator.verify_manifest(manifest.manifest_id)
    execution_tables = {
        "foreground_execution_marker",
        "foreground_preparation_drafts",
        "foreground_run_preparation_bindings",
        "foreground_execution_preparations",
        "foreground_execution_start_intents",
        "foreground_execution_start_observations",
        "foreground_execution_reconciliations",
    }
    with sqlite3.connect(db_path) as db:
        manifest_tables = {
            row[0]
            for row in db.execute(
                "SELECT table_name FROM human_memory_recovery_manifest_tables "
                "WHERE manifest_id=?",
                (manifest.manifest_id,),
            ).fetchall()
        }
        assert execution_tables <= manifest_tables
    exported = await coordinator.emergency_export(export_id="export-1")
    assert exported.overall_root == manifest.overall_root
    content = exported.artifact_path.read_bytes()
    assert hashlib.sha256(content).hexdigest() == exported.artifact_sha256
    assert all(len(line) <= 32 * 1024 for line in content.splitlines())
    assert b"password" not in content.lower()
    assert b"foreground_execution_marker" in content
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
async def test_public_explicit_close_seals_export_to_same_manifest_root(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    await CanonicalTaskScopeStore(db_path).create_task_scope(
        task_scope_id="scope-1", subject="actor-1", title="Memory"
    )
    port = build_recovery_lifecycle_port(
        db_path=db_path, artifact_dir=tmp_path / "exports"
    )
    closing = await port.begin_close(subject="actor-1")
    assert closing["state"] == "CLOSING"
    sealed = await port.drain_checkpoint_seal(subject="actor-1")
    assert sealed["state"] == "SEALED"
    assert sealed["wal_busy"] == 0
    assert sealed["drained_or_parked"] is True
    manifest = await port.sealed_manifest(subject="actor-1")
    assert manifest["overall_root"] == sealed["overall_root"]
    exported = await port.emergency_export(subject="actor-1")
    assert exported["manifest_ref"] == manifest["manifest_ref"]
    assert exported["overall_root"] == manifest["overall_root"]


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
        assert (
            db.execute("PRAGMA user_version").fetchone()[0]
            == HUMAN_MEMORY_TARGET_SCHEMA_VERSION
        )
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
@pytest.mark.parametrize(
    "stage",
    (
        "before_035_human_memory_quiescence_v43_commit",
        "after_035_human_memory_quiescence_v43_commit",
    ),
)
async def test_v43_fault_restart_has_one_exact_marker(
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
        assert (
            db.execute("PRAGMA user_version").fetchone()[0]
            == HUMAN_MEMORY_TARGET_SCHEMA_VERSION
        )
        assert db.execute(
            "SELECT COUNT(*) FROM human_memory_quiescence_marker"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM human_memory_migration_chain "
            "WHERE schema_version=43"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM human_memory_recovery_table_registry "
            "WHERE table_name IN ('human_memory_quiescence_marker',"
            "'human_memory_recovery_work_items')"
        ).fetchone()[0] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    (
        "before_036_foreground_execution_v44_commit",
        "after_036_foreground_execution_v44_commit",
    ),
)
async def test_v44_fault_restart_registers_execution_ledger_exactly_once(
    tmp_path: Path, stage: str
) -> None:
    db_path = tmp_path / "state.db"

    def crash(actual: str) -> None:
        if actual == stage:
            raise RuntimeError(f"crash:{stage}")

    with pytest.raises(InitializeError, match=stage):
        await initialize_human_memory_program_state_db(db_path, fault_inject=crash)
    await initialize_human_memory_program_state_db(db_path)
    execution_tables = (
        "foreground_execution_marker",
        "foreground_preparation_drafts",
        "foreground_run_preparation_bindings",
        "foreground_execution_preparations",
        "foreground_execution_start_intents",
        "foreground_execution_start_observations",
        "foreground_execution_reconciliations",
    )
    placeholders = ",".join("?" for _ in execution_tables)
    with sqlite3.connect(db_path) as db:
        assert (
            db.execute("PRAGMA user_version").fetchone()[0]
            == HUMAN_MEMORY_TARGET_SCHEMA_VERSION
        )
        assert db.execute(
            "SELECT COUNT(*) FROM foreground_execution_marker"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM human_memory_migration_chain "
            "WHERE schema_version=44"
        ).fetchone()[0] == 1
        registry = db.execute(
            "SELECT table_name,taxonomy FROM human_memory_recovery_table_registry "
            f"WHERE table_name IN ({placeholders}) ORDER BY table_name",
            execution_tables,
        ).fetchall()
        assert len(registry) == len(execution_tables)
        assert {taxonomy for _, taxonomy in registry} == {"A"}
        for table in execution_tables:
            trigger_count = db.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE ?",
                (f"hm_recovery_fence_{table}_%",),
            ).fetchone()[0]
            assert trigger_count == 3


@pytest.mark.asyncio
async def test_exact_park_is_restart_verifiable_and_preserves_raw_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    store = CanonicalTaskScopeStore(db_path)
    await store.create_task_scope(
        task_scope_id="scope-1", subject="actor-1", title="Memory"
    )
    await store.append_host_event(
        task_scope_id="scope-1",
        event_kind="host.turn",
        source_event_id="event-1",
        payload={"event_index": 1},
    )
    with sqlite3.connect(db_path) as db:
        before = {
            table: tuple(db.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall())
            for table in (
                "task_scope_projection_source_outbox",
                "task_scope_projection_outbox",
                "task_scope_search_outbox",
            )
        }
    coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
        db_path, export_root=tmp_path / "exports"
    )
    await coordinator.begin_close()
    await coordinator.drain_or_park(action="park")
    with pytest.raises(
        ProjectionIntegrityError, match="human_memory_projection_work_item_parked"
    ):
        await TaskScopeProjectionStore(db_path).read_view(
            "README", task_scope_id="scope-1"
        )
    restarted = await HumanMemoryRecoveryCoordinator.bind_for_host(
        db_path, export_root=tmp_path / "exports"
    )
    assert (await restarted.quiesce()).state == "QUIESCED"
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM human_memory_recovery_work_items "
            "WHERE generation=2"
        ).fetchone()[0] == sum(len(rows) for rows in before.values())
        after = {
            table: tuple(db.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall())
            for table in before
        }
    assert after == before


@pytest.mark.asyncio
async def test_quiesce_rejects_omitted_exact_item_and_fails_closed(
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
    with sqlite3.connect(db_path) as db:
        db.execute("DROP TRIGGER human_memory_recovery_work_item_no_delete")
        db.execute(
            "DELETE FROM human_memory_recovery_work_items WHERE item_receipt_id=("
            "SELECT item_receipt_id FROM human_memory_recovery_work_items LIMIT 1)"
        )
        db.commit()
    with pytest.raises(HumanMemoryRecoveryError, match="human_memory_recovery_outbox_gap"):
        await coordinator.quiesce()
    assert (await coordinator.snapshot()).state == "FAILED_CLOSED"


@pytest.mark.asyncio
async def test_quiesce_rejects_stale_generation_item_and_fails_closed(
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
    with sqlite3.connect(db_path) as db:
        db.execute("DROP TRIGGER human_memory_recovery_work_item_no_update")
        db.execute(
            "UPDATE human_memory_recovery_work_items SET generation=999 "
            "WHERE item_receipt_id=(SELECT item_receipt_id FROM "
            "human_memory_recovery_work_items LIMIT 1)"
        )
        db.commit()
    with pytest.raises(HumanMemoryRecoveryError, match="human_memory_recovery_outbox_gap"):
        await coordinator.quiesce()
    assert (await coordinator.snapshot()).state == "FAILED_CLOSED"


@pytest.mark.asyncio
async def test_forged_worker_summary_cannot_authorize_quiescence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    await CanonicalTaskScopeStore(db_path).create_task_scope(
        task_scope_id="scope-1", subject="actor-1", title="Memory"
    )
    coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
        db_path, export_root=tmp_path / "exports"
    )
    closing = await coordinator.begin_close()
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO human_memory_recovery_worker_receipts("
            "receipt_id,generation,worker_kind,action,cutoff,item_count,item_root,"
            "gap_count,receipt_hash,receipt_json,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "forged-summary",
                closing.generation,
                "projection-source",
                "park",
                closing.cutoff,
                0,
                "a" * 64,
                0,
                "b" * 64,
                "{}",
                1.0,
            ),
        )
        db.commit()
    with pytest.raises(
        HumanMemoryRecoveryError,
        match="human_memory_recovery_worker_receipt_conflict",
    ):
        await coordinator.drain_or_park(action="park")
    assert (await coordinator.snapshot()).state == "FAILED_CLOSED"


@pytest.mark.asyncio
async def test_writer_worker_close_race_is_transaction_linearized(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    store = CanonicalTaskScopeStore(db_path)
    await store.create_task_scope(
        task_scope_id="scope-1", subject="actor-1", title="Memory"
    )
    coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
        db_path, export_root=tmp_path / "exports"
    )

    async def append_racer() -> str:
        try:
            await store.append_host_event(
                task_scope_id="scope-1",
                event_kind="host.turn",
                source_event_id="racing-event",
                payload={"event_index": 1},
            )
            return "committed"
        except HumanMemoryIngressFenced:
            return "fenced"

    close_result, append_result, worker_result = await asyncio.gather(
        coordinator.begin_close(),
        append_racer(),
        TaskScopeProjectionStore(db_path).read_view(
            "README", task_scope_id="scope-1"
        ),
        return_exceptions=True,
    )
    assert close_result.state == "CLOSING"
    assert append_result in {"committed", "fenced"}
    assert not isinstance(worker_result, BaseException)
    await coordinator.drain_or_park(action="park")
    assert (await coordinator.quiesce()).state == "QUIESCED"


@pytest.mark.asyncio
async def test_active_foreground_lease_is_exactly_parked_without_row_loss(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    await CanonicalTaskScopeStore(db_path).create_task_scope(
        task_scope_id="scope-1", subject="actor-1", title="Memory"
    )
    primary = await HumanMemoryProgramStore(db_path).initialize_subject("actor-1")
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO human_memory_sanitization_receipts("
            "receipt_id,evidence_id,subject,run_id,envelope_sha256,source_sha256,"
            "sanitized_sha256,filter_policy_version,receipt_sha256,receipt_json,"
            "admitted_at,committed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "receipt-1",
                "evidence-1",
                "actor-1",
                "source-run-1",
                "1" * 64,
                "a" * 64,
                "b" * 64,
                "test/v1",
                "c" * 64,
                "{}",
                1.0,
                1.0,
            ),
        )
        db.execute(
            "INSERT INTO human_memory_evidence("
            "evidence_id,primary_conversation_id,subject,run_id,source_kind,source_ref,"
            "source_sha256,sanitized_sha256,envelope_sha256,receipt_id,payload_json,"
            "envelope_json,occurred_at,committed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "evidence-1",
                primary.primary_conversation_id,
                "actor-1",
                "source-run-1",
                "typed_observation",
                "test/evidence-1",
                "a" * 64,
                "b" * 64,
                "1" * 64,
                "receipt-1",
                "{}",
                "{}",
                1.0,
                1.0,
            ),
        )
        db.commit()
    queue = ForegroundQueueStore(db_path, clock=lambda: 100.0)
    await queue.initialize()
    await queue.enqueue_turn(
        subject="actor-1",
        primary_conversation_id=primary.primary_conversation_id,
        task_scope_id="scope-1",
        evidence_id="evidence-1",
        evidence_hash="1" * 64,
        idempotency_key="enqueue-1",
        turn_payload={"text": "run"},
    )
    candidate = await queue.read_next_preparation_candidate("actor-1")
    assert candidate is not None
    draft = await queue.prepare_candidate(
        subject="actor-1",
        expected_candidate_hash=candidate.candidate_hash,
        context=ContextLineage("context-1", 1, "d" * 64),
        idempotency_key="prepare-1",
    )
    admission = await queue.claim_next(
        subject="actor-1",
        owner_id="owner-1",
        claim_idempotency_key="claim-1",
        preparation_draft_id=draft.draft_id,
        preparation_draft_hash=draft.draft_hash,
        lease_seconds=10,
    )
    assert admission is not None
    with sqlite3.connect(db_path) as db:
        head_before = db.execute(
            "SELECT * FROM foreground_run_heads WHERE host_run_id=?",
            (admission.host_run_id,),
        ).fetchone()
    coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
        db_path, export_root=tmp_path / "exports"
    )
    await coordinator.begin_close()
    await coordinator.drain_or_park(action="park")
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        assert await is_human_memory_work_item_parked_tx(
            db,
            worker_kind="foreground-lease",
            source_table="foreground_run_heads",
            primary_key="host_run_id",
            item_pk=admission.host_run_id,
        )
    with sqlite3.connect(db_path) as db:
        lease_receipt = db.execute(
            "SELECT disposition,lease_owner_id,lease_generation "
            "FROM human_memory_recovery_work_items WHERE worker_kind='foreground-lease'"
        ).fetchone()
        head_after = db.execute(
            "SELECT * FROM foreground_run_heads WHERE host_run_id=?",
            (admission.host_run_id,),
        ).fetchone()
    assert lease_receipt == ("lease_parked", "owner-1", admission.generation)
    assert head_after == head_before
    assert (await coordinator.quiesce()).state == "QUIESCED"
    await coordinator.checkpoint_wal()
    await coordinator.seal()
    await coordinator.reopen()
    with pytest.raises(ForegroundQueueError, match="foreground_lease_recovery_parked"):
        await queue.heartbeat(
            host_run_id=admission.host_run_id,
            owner_id="owner-1",
            generation=admission.generation,
            lease_seconds=10,
            idempotency_key="old-lease-after-reopen",
        )


@pytest.mark.asyncio
async def test_drain_uses_durable_projection_and_search_proofs(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    await CanonicalTaskScopeStore(db_path).create_task_scope(
        task_scope_id="scope-1", subject="actor-1", title="Memory"
    )
    await TaskScopeProjectionStore(db_path).read_view(
        "README", task_scope_id="scope-1"
    )
    await TaskScopeSearchStore(db_path).rebuild_scope("scope-1")
    coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
        db_path, export_root=tmp_path / "exports"
    )
    await coordinator.begin_close()
    await coordinator.drain_or_park(action="drain")
    assert (await coordinator.quiesce()).state == "QUIESCED"
    with sqlite3.connect(db_path) as db:
        dispositions = {
            str(row[0]): str(row[1])
            for row in db.execute(
                "SELECT worker_kind,disposition FROM human_memory_recovery_work_items"
            )
        }
    assert dispositions["projection-source"] == "delivered"
    assert dispositions["projection-legacy"] == "delivered"
    assert dispositions["search"] == "delivered"


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
        assert (
            db.execute("PRAGMA user_version").fetchone()[0]
            == HUMAN_MEMORY_TARGET_SCHEMA_VERSION
        )
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
