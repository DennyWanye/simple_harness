"""Versioned schema for the standalone ``workflow.db`` database."""

from __future__ import annotations

import time
import asyncio
from pathlib import Path
from typing import Callable

import aiosqlite

WORKFLOW_SCHEMA_VERSION = 30
_INITIALIZE_LOCKS: dict[str, asyncio.Lock] = {}

_SCHEMA_V2 = r"""
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS workflow_schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_start_requests (
    request_key TEXT PRIMARY KEY, session_id TEXT NOT NULL, request_id TEXT NOT NULL,
    turn_id TEXT NOT NULL, workflow_name TEXT NOT NULL, capability_hash TEXT NOT NULL,
    run_id TEXT NOT NULL, created_at REAL NOT NULL,
    UNIQUE(session_id, request_id, turn_id, workflow_name)
);
CREATE TABLE IF NOT EXISTS workflow_fork_requests (
    fork_key TEXT PRIMARY KEY, source_run_id TEXT NOT NULL, source_checkpoint_ns TEXT NOT NULL,
    source_checkpoint_id TEXT NOT NULL, child_run_id TEXT NOT NULL, status TEXT NOT NULL,
    state_patch_json TEXT, error_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '', head_checkpoint_ns TEXT NOT NULL DEFAULT '', head_checkpoint_id TEXT,
    parent_run_id TEXT, source_checkpoint_id TEXT, session_id TEXT NOT NULL,
    request_id TEXT, turn_id TEXT, workflow_name TEXT NOT NULL, workflow_version TEXT NOT NULL,
    manifest_hash TEXT NOT NULL, implementation_hash TEXT NOT NULL, capability_hash TEXT NOT NULL,
    state_schema_version INTEGER NOT NULL, status TEXT NOT NULL, active_nodes_json TEXT NOT NULL DEFAULT '[]',
    lease_owner TEXT, lease_epoch INTEGER NOT NULL DEFAULT 0, lease_expires_at REAL,
    heartbeat_at REAL, run_version INTEGER NOT NULL DEFAULT 0, event_seq INTEGER NOT NULL DEFAULT 0,
    cancel_reason TEXT, error_json TEXT, recovery_action TEXT,
    created_at REAL NOT NULL, started_at REAL, updated_at REAL NOT NULL, ended_at REAL
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status_lease ON workflow_runs(status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_session_created ON workflow_runs(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_thread ON workflow_runs(thread_id, checkpoint_ns);
CREATE TABLE IF NOT EXISTS workflow_session_refs (
    run_id TEXT NOT NULL, session_kind TEXT NOT NULL, session_id TEXT NOT NULL,
    session_epoch INTEGER NOT NULL DEFAULT 0, deleted_at REAL,
    PRIMARY KEY(run_id, session_kind), FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS workflow_capabilities (
    capability_hash TEXT PRIMARY KEY, snapshot_json TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_nodes (
    node_execution_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, node_id TEXT NOT NULL,
    base_checkpoint_id TEXT NOT NULL, invocation_key TEXT NOT NULL, task_id TEXT, task_path TEXT,
    latest_attempt INTEGER NOT NULL, latest_status TEXT NOT NULL, updated_at REAL NOT NULL,
    UNIQUE(run_id, base_checkpoint_id, invocation_key),
    FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_workflow_nodes_run_status ON workflow_nodes(run_id, latest_status);
CREATE TABLE IF NOT EXISTS workflow_node_attempts (
    node_execution_id TEXT NOT NULL, retry_attempt INTEGER NOT NULL, task_id TEXT, task_path TEXT,
    status TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL, error_ref TEXT,
    next_attempt_at REAL,
    PRIMARY KEY(node_execution_id, retry_attempt),
    FOREIGN KEY(node_execution_id) REFERENCES workflow_nodes(node_execution_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS workflow_decisions (
    decision_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, node_execution_id TEXT,
    interrupt_id TEXT, checkpoint_ns TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
    kind TEXT NOT NULL, status TEXT NOT NULL, prompt_json TEXT NOT NULL, response_json TEXT,
    nonce TEXT NOT NULL, decision_version INTEGER NOT NULL DEFAULT 0, expires_at REAL,
    created_at REAL NOT NULL, resolved_at REAL, consumed_at REAL,
    consumed_checkpoint_id TEXT,
    UNIQUE(run_id, interrupt_id), FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_workflow_decisions_open ON workflow_decisions(run_id, status, expires_at);
CREATE TABLE IF NOT EXISTS workflow_grants (
    grant_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL, effect_id TEXT, scope_json TEXT NOT NULL,
    status TEXT NOT NULL, created_at REAL NOT NULL, consumed_at REAL,
    FOREIGN KEY(decision_id) REFERENCES workflow_decisions(decision_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS workflow_effects (
    effect_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, node_execution_id TEXT NOT NULL,
    effect_fingerprint TEXT NOT NULL, effect_type TEXT NOT NULL, policy_json TEXT NOT NULL,
    args_hash TEXT NOT NULL, status TEXT NOT NULL, prepared_json TEXT NOT NULL,
    outcome_json TEXT, receipt_ref TEXT, artifact_refs_json TEXT NOT NULL DEFAULT '[]',
    lease_epoch INTEGER NOT NULL, started_at REAL NOT NULL, updated_at REAL NOT NULL, ended_at REAL,
    UNIQUE(run_id, effect_fingerprint), FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_workflow_effects_status ON workflow_effects(run_id, status);
CREATE TABLE IF NOT EXISTS workflow_effect_targets (
    effect_id TEXT NOT NULL, reservation_key TEXT NOT NULL, target_json TEXT NOT NULL,
    PRIMARY KEY(effect_id, reservation_key), FOREIGN KEY(effect_id) REFERENCES workflow_effects(effect_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS workflow_node_effects (
    node_execution_id TEXT NOT NULL, effect_id TEXT NOT NULL,
    PRIMARY KEY(node_execution_id, effect_id)
);
CREATE TABLE IF NOT EXISTS workflow_checkpoint_effects (
    thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
    effect_id TEXT NOT NULL, node_execution_id TEXT NOT NULL,
    PRIMARY KEY(thread_id, checkpoint_ns, checkpoint_id, effect_id)
);
CREATE TABLE IF NOT EXISTS workflow_target_reservations (
    reservation_key TEXT PRIMARY KEY, display_path TEXT NOT NULL, run_id TEXT NOT NULL,
    call_id TEXT NOT NULL, status TEXT NOT NULL, lease_expires_at REAL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_checkpoints (
    thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT, run_id TEXT NOT NULL, checkpoint_type TEXT NOT NULL,
    checkpoint_blob BLOB NOT NULL, metadata_blob BLOB NOT NULL,
    engine_kind TEXT NOT NULL DEFAULT 'langgraph-legacy', snapshot_version INTEGER,
    created_at REAL NOT NULL,
    PRIMARY KEY(thread_id, checkpoint_ns, checkpoint_id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_checkpoints_run_created ON workflow_checkpoints(run_id, created_at DESC);
CREATE TABLE IF NOT EXISTS workflow_pending_writes (
    thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL, base_checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL, write_index INTEGER NOT NULL, channel TEXT NOT NULL,
    value_type TEXT NOT NULL, value_blob BLOB, task_path TEXT NOT NULL DEFAULT '',
    write_kind TEXT, payload_json TEXT, node_execution_id TEXT,
    PRIMARY KEY(thread_id, checkpoint_ns, base_checkpoint_id, task_id, write_index)
);
CREATE TABLE IF NOT EXISTS workflow_checkpoint_owners (
    run_id TEXT NOT NULL, thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL, parent_checkpoint_id TEXT, created_at REAL NOT NULL,
    PRIMARY KEY(run_id, checkpoint_ns, checkpoint_id)
);
CREATE TABLE IF NOT EXISTS workflow_operations (
    operation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, operation_kind TEXT NOT NULL,
    request_hash TEXT NOT NULL, result_json TEXT NOT NULL, created_at REAL NOT NULL,
    FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_workflow_operations_run_created
    ON workflow_operations(run_id, created_at);
CREATE TABLE IF NOT EXISTS workflow_blobs (
    sha256 TEXT PRIMARY KEY, size_bytes INTEGER NOT NULL, media_type TEXT NOT NULL,
    relative_path TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_blob_refs (
    sha256 TEXT NOT NULL, owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL,
    created_at REAL NOT NULL, PRIMARY KEY(sha256, owner_kind, owner_id),
    FOREIGN KEY(sha256) REFERENCES workflow_blobs(sha256) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS workflow_events (
    event_id TEXT PRIMARY KEY, event_key TEXT NOT NULL, run_id TEXT NOT NULL, seq INTEGER NOT NULL, event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(run_id, seq), UNIQUE(run_id, event_key),
    FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS workflow_deliveries (
    delivery_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, run_id TEXT NOT NULL, channel TEXT NOT NULL,
    target_id TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    delivery_version INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL, last_error TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL, delivered_at REAL,
    UNIQUE(event_id, channel, target_id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_deliveries_retry ON workflow_deliveries(status, next_attempt_at);
CREATE TABLE IF NOT EXISTS workflow_receipt_ledger (
    receipt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, effect_id TEXT,
    signature_version INTEGER NOT NULL, canonical_json TEXT NOT NULL, signature TEXT NOT NULL,
    accepted_at REAL NOT NULL, delivered_at REAL
);
CREATE TABLE IF NOT EXISTS trace_runs (
    trace_id TEXT PRIMARY KEY, run_id TEXT, session_id TEXT NOT NULL, request_id TEXT, turn_id TEXT,
    kind TEXT NOT NULL, workflow_name TEXT, workflow_version TEXT, status TEXT NOT NULL,
    started_at REAL NOT NULL, ended_at REAL, duration_ms REAL, error_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_trace_runs_session_started ON trace_runs(session_id, started_at DESC);
CREATE TABLE IF NOT EXISTS trace_spans (
    span_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, parent_span_id TEXT, run_id TEXT,
    workflow_name TEXT, workflow_version TEXT, node_id TEXT, lifecycle_stage TEXT,
    kind TEXT NOT NULL, status TEXT NOT NULL, name TEXT NOT NULL, attributes_json TEXT NOT NULL DEFAULT '{}',
    input_ref TEXT, output_ref TEXT, error_json TEXT, privacy_class TEXT NOT NULL DEFAULT 'internal',
    started_at REAL NOT NULL, ended_at REAL, duration_ms REAL,
    FOREIGN KEY(trace_id) REFERENCES trace_runs(trace_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_trace_spans_trace_started ON trace_spans(trace_id, started_at);
CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, run_id TEXT, span_id TEXT,
    evaluator_name TEXT NOT NULL, evaluator_version TEXT NOT NULL, evaluator_type TEXT NOT NULL,
    score REAL, verdict TEXT NOT NULL, labels_json TEXT NOT NULL DEFAULT '[]', explanation TEXT,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]', degraded INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS eval_datasets (
    dataset_id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL, UNIQUE(name, version)
);
CREATE TABLE IF NOT EXISTS eval_examples (
    example_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, input_json TEXT NOT NULL,
    expected_json TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
    FOREIGN KEY(dataset_id) REFERENCES eval_datasets(dataset_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS eval_experiments (
    experiment_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, version_key TEXT NOT NULL,
    config_json TEXT NOT NULL, status TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL,
    FOREIGN KEY(dataset_id) REFERENCES eval_datasets(dataset_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS eval_results (
    experiment_id TEXT NOT NULL, example_id TEXT NOT NULL, run_id TEXT, evaluation_id TEXT,
    status TEXT NOT NULL, score REAL, latency_ms REAL, error_taxonomy TEXT, result_json TEXT NOT NULL,
    PRIMARY KEY(experiment_id, example_id)
);
INSERT OR IGNORE INTO workflow_schema_migrations(version, applied_at)
VALUES(2, CAST(strftime('%s','now') AS REAL));
PRAGMA user_version=2;
COMMIT;
"""


async def _configure(db: aiosqlite.Connection) -> None:
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=FULL")
    await db.execute("PRAGMA busy_timeout=5000")


async def initialize_workflow_db(path: str | Path) -> Path:
    """Serialize the full migration chain for one normalized DB path."""

    key = str(Path(path).resolve(strict=False)).casefold()
    lock = _INITIALIZE_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        return await _initialize_workflow_db_unlocked(path)


async def _initialize_workflow_db_unlocked(path: str | Path) -> Path:
    """Create or migrate ``workflow.db`` atomically and return its path."""

    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await _configure(db)
        row = await (await db.execute("PRAGMA user_version")).fetchone()
        current = int(row[0]) if row else 0
        if current > WORKFLOW_SCHEMA_VERSION:
            raise RuntimeError(
                f"workflow.db schema {current} is newer than supported {WORKFLOW_SCHEMA_VERSION}"
            )
        try:
            if current == 0:
                # Fresh databases intentionally use the exact v2 base and then
                # traverse the same migration path as existing installations.
                await db.executescript(_SCHEMA_V2)
                current = 2
            while current < WORKFLOW_SCHEMA_VERSION:
                if current == 1:
                    await _migrate_v1_to_v2(db)
                elif current == 2:
                    await _migrate_v2_to_v3(db)
                elif current == 3:
                    await _migrate_v3_to_v4(db)
                elif current == 4:
                    await _migrate_v4_to_v5(db)
                elif current == 5:
                    await _migrate_v5_to_v6(db)
                elif current == 6:
                    await _migrate_v6_to_v7(db)
                elif current == 7:
                    await _migrate_v7_to_v8(db)
                elif current == 8:
                    await _migrate_v8_to_v9(db)
                elif current == 9:
                    await _migrate_v9_to_v10(db)
                elif current == 10:
                    await _migrate_v10_to_v11(db)
                elif current == 11:
                    await _migrate_v11_to_v12(db)
                elif current == 12:
                    await _migrate_v12_to_v13(db)
                elif current == 13:
                    await _migrate_v13_to_v14(db)
                elif current == 14:
                    await _migrate_v14_to_v15(db)
                elif current == 15:
                    await _migrate_v15_to_v16(db)
                elif current == 16:
                    await _migrate_v16_to_v17_execution_run_snapshots(db)
                elif current == 17:
                    await _migrate_v17_to_v18_capability_owner_scope(db)
                elif current == 18:
                    await _migrate_v18_to_v19_candidate_draft_receipts(db)
                elif current == 19:
                    await _migrate_v19_to_v20_candidate_draft_materials(db)
                elif current == 20:
                    await _migrate_v20_to_v21_personal_workflow_tickets(db)
                elif current == 21:
                    await _migrate_v21_to_v22_run_context_owner_identity(db)
                elif current == 22:
                    await _migrate_v22_to_v23_provider_invocation_audits(db)
                elif current == 23:
                    await _migrate_v23_to_v24_provider_invocation_inputs(db)
                elif current == 24:
                    await _migrate_v24_to_v25_provider_batch_pending_count(db)
                elif current == 25:
                    await _migrate_v25_to_v26_project_workspace_rebind(db)
                elif current == 26:
                    await _migrate_v26_to_v27_active_execution_budget(db)
                elif current == 27:
                    await _migrate_v27_to_v28_existing_workspace_rebind(db)
                elif current == 28:
                    await _migrate_v28_to_v29_public_run_projection(db)
                elif current == 29:
                    await _migrate_v29_to_v30_capability_skill_verification(db)
                else:  # pragma: no cover - guarded by the version constant
                    raise RuntimeError(f"no workflow.db migration from schema {current}")
                row = await (await db.execute("PRAGMA user_version")).fetchone()
                migrated = int(row[0]) if row else current
                if migrated <= current:
                    raise RuntimeError(
                        f"workflow.db migration from schema {current} did not advance"
                    )
                current = migrated
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
    return db_path


async def _columns(db: aiosqlite.Connection, table: str) -> set[str]:
    rows = await (await db.execute(f"PRAGMA table_info({table})")).fetchall()
    return {str(row[1]) for row in rows}


async def _tables(db: aiosqlite.Connection) -> set[str]:
    rows = await (
        await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    ).fetchall()
    return {str(row[0]) for row in rows}


async def _add_column_if_missing(
    db: aiosqlite.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    if column not in await _columns(db, table):
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


async def _migrate_v1_to_v2(db: aiosqlite.Connection) -> None:
    """Upgrade the workflow database without decoding legacy checkpoints."""

    await db.execute("BEGIN IMMEDIATE")
    await _add_column_if_missing(db, "workflow_checkpoints", "engine_kind", "TEXT NOT NULL DEFAULT 'langgraph-legacy'")
    await _add_column_if_missing(db, "workflow_checkpoints", "snapshot_version", "INTEGER")
    await _add_column_if_missing(db, "workflow_pending_writes", "write_kind", "TEXT")
    await _add_column_if_missing(db, "workflow_pending_writes", "payload_json", "TEXT")
    await _add_column_if_missing(db, "workflow_pending_writes", "node_execution_id", "TEXT")
    await _add_column_if_missing(db, "workflow_node_attempts", "next_attempt_at", "REAL")
    await _add_column_if_missing(db, "workflow_decisions", "consumed_at", "REAL")
    await _add_column_if_missing(db, "workflow_decisions", "consumed_checkpoint_id", "TEXT")
    await db.execute(
        """CREATE TABLE IF NOT EXISTS workflow_operations (
        operation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, operation_kind TEXT NOT NULL,
        request_hash TEXT NOT NULL DEFAULT '', result_json TEXT NOT NULL, created_at REAL NOT NULL,
        FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
        )"""
    )
    await _add_column_if_missing(
        db, "workflow_operations", "request_hash", "TEXT NOT NULL DEFAULT ''"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflow_operations_run_created "
        "ON workflow_operations(run_id, created_at)"
    )
    await db.execute(
        "INSERT OR IGNORE INTO workflow_schema_migrations(version, applied_at) VALUES(2, ?)",
        (time.time(),),
    )
    await db.execute("PRAGMA user_version=2")
    await db.commit()


async def _migrate_v2_to_v3(db: aiosqlite.Connection) -> None:
    """Create the complete v3 control, budget, snapshot and lineage skeleton."""

    await db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS workflow_effect_budget_reservations (
            effect_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            ledger_kind TEXT NOT NULL,
            input_reserved INTEGER NOT NULL CHECK(input_reserved >= 0),
            output_reserved INTEGER NOT NULL CHECK(output_reserved >= 0),
            cost_reserved_micros INTEGER NOT NULL CHECK(cost_reserved_micros >= 0),
            input_actual INTEGER CHECK(input_actual IS NULL OR input_actual >= 0),
            output_actual INTEGER CHECK(output_actual IS NULL OR output_actual >= 0),
            cost_actual_micros INTEGER CHECK(cost_actual_micros IS NULL OR cost_actual_micros >= 0),
            status TEXT NOT NULL CHECK(status IN ('reserved','committed','released','held_uncertain')),
            dispatch_state TEXT NOT NULL CHECK(dispatch_state IN ('not_started','started')),
            upstream_started_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(effect_id) REFERENCES workflow_effects(effect_id) ON DELETE CASCADE,
            FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
            CHECK(
                (dispatch_state='not_started' AND upstream_started_at IS NULL) OR
                (dispatch_state='started' AND upstream_started_at IS NOT NULL)
            )
        );
        CREATE INDEX IF NOT EXISTS idx_workflow_effect_budget_run_status
            ON workflow_effect_budget_reservations(run_id, ledger_kind, status);
        CREATE INDEX IF NOT EXISTS idx_workflow_effect_budget_dispatch
            ON workflow_effect_budget_reservations(run_id, dispatch_state, status);

        CREATE TABLE IF NOT EXISTS workflow_run_control_commands (
            command_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'open','accepted','observed','settled','consumed','rejected','expired'
            )),
            head_checkpoint_ns TEXT NOT NULL DEFAULT '',
            head_checkpoint_id TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            accepted_at REAL,
            observed_at REAL,
            settled_at REAL,
            consumed_at REAL,
            settle_deadline REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(run_id, idempotency_key),
            FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_workflow_run_control_status
            ON workflow_run_control_commands(run_id, status, created_at);
        CREATE INDEX IF NOT EXISTS idx_workflow_run_control_deadline
            ON workflow_run_control_commands(status, settle_deadline);

        CREATE TABLE IF NOT EXISTS workflow_research_snapshots (
            snapshot_hash TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            schema_version INTEGER NOT NULL CHECK(schema_version > 0),
            manifest_ref TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL,
            FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_workflow_research_snapshots_run
            ON workflow_research_snapshots(run_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_workflow_research_snapshots_expiry
            ON workflow_research_snapshots(expires_at);

        CREATE TABLE IF NOT EXISTS workflow_research_snapshot_pins (
            pin_id TEXT PRIMARY KEY,
            snapshot_hash TEXT NOT NULL,
            run_id TEXT NOT NULL,
            pin_kind TEXT NOT NULL,
            expires_at REAL,
            created_at REAL NOT NULL,
            UNIQUE(snapshot_hash, run_id, pin_kind),
            FOREIGN KEY(snapshot_hash) REFERENCES workflow_research_snapshots(snapshot_hash) ON DELETE CASCADE,
            FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_workflow_research_snapshot_pins_run
            ON workflow_research_snapshot_pins(run_id, expires_at);
        CREATE INDEX IF NOT EXISTS idx_workflow_research_snapshot_pins_snapshot
            ON workflow_research_snapshot_pins(snapshot_hash, expires_at);

        CREATE TABLE IF NOT EXISTS workflow_research_lineage (
            operation_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE,
            parent_run_id TEXT,
            parent_operation_id TEXT,
            snapshot_hash TEXT,
            parent_report_ref TEXT,
            budget_lease_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(parent_run_id) REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
            FOREIGN KEY(parent_operation_id) REFERENCES workflow_research_lineage(operation_id) ON DELETE RESTRICT,
            FOREIGN KEY(snapshot_hash) REFERENCES workflow_research_snapshots(snapshot_hash) ON DELETE RESTRICT,
            CHECK(parent_run_id IS NULL OR parent_run_id <> run_id),
            CHECK(
                (parent_run_id IS NULL AND parent_operation_id IS NULL AND snapshot_hash IS NULL) OR
                (parent_run_id IS NOT NULL AND parent_operation_id IS NOT NULL AND snapshot_hash IS NOT NULL)
            )
        );
        CREATE INDEX IF NOT EXISTS idx_workflow_research_lineage_parent
            ON workflow_research_lineage(parent_run_id, parent_operation_id);
        CREATE INDEX IF NOT EXISTS idx_workflow_research_lineage_snapshot
            ON workflow_research_lineage(snapshot_hash);
        """
    )
    await db.execute(
        "INSERT OR IGNORE INTO workflow_schema_migrations(version, applied_at) VALUES(3, ?)",
        (time.time(),),
    )
    await db.execute("PRAGMA user_version=3")
    await db.commit()


async def _migrate_v3_to_v4(db: aiosqlite.Connection) -> None:
    """Install v6 durable attempt, deadline, delivery, and lineage owners."""

    await db.execute("BEGIN IMMEDIATE")
    tables = await _tables(db)
    run_columns = await _columns(db, "workflow_runs")
    pre_v4_v6_continuation = None
    if {
        "workflow_name",
        "workflow_version",
    } <= run_columns and "workflow_research_lineage" in tables:
        pre_v4_v6_continuation = await (
            await db.execute(
                """SELECT 1
                FROM workflow_research_lineage AS lineage
                JOIN workflow_runs AS run ON run.run_id=lineage.run_id
                WHERE lineage.parent_run_id IS NOT NULL
                  AND run.workflow_name='deep_research'
                  AND run.workflow_version='v6'
                LIMIT 1"""
            )
        ).fetchone()
    if pre_v4_v6_continuation is not None:
        raise RuntimeError(
            "workflow.db contains a pre-v4 deep_research/v6 continuation"
        )

    if "workflow_effects" in tables:
        await _add_column_if_missing(db, "workflow_effects", "logical_effect_id", "TEXT")
        await _add_column_if_missing(
            db,
            "workflow_effects",
            "attempt_no",
            "INTEGER CHECK(attempt_no IS NULL OR attempt_no >= 1)",
        )
        await _add_column_if_missing(
            db,
            "workflow_effects",
            "supersedes_effect_id",
            "TEXT REFERENCES workflow_effects(effect_id) ON DELETE RESTRICT",
        )
    if "workflow_deliveries" in tables:
        await _add_column_if_missing(db, "workflow_deliveries", "intent_id", "TEXT")
        await _add_column_if_missing(db, "workflow_deliveries", "manifest_ref", "TEXT")
        await _add_column_if_missing(
            db,
            "workflow_deliveries",
            "required_durable",
            "INTEGER NOT NULL DEFAULT 0 CHECK(required_durable IN (0,1))",
        )
        await _add_column_if_missing(
            db,
            "workflow_deliveries",
            "claim_expires_at",
            "REAL CHECK(claim_expires_at IS NULL OR claim_expires_at >= 0)",
        )

    statements = [
        """CREATE TABLE workflow_research_deadlines (
            deadline_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            run_id TEXT NOT NULL,
            parent_deadline_id TEXT,
            logical_scope TEXT NOT NULL,
            policy_hash TEXT NOT NULL,
            budget_ms INTEGER NOT NULL CHECK(budget_ms > 0),
            remaining_ms INTEGER NOT NULL CHECK(remaining_ms >= 0),
            created_at REAL NOT NULL,
            last_observed_at REAL NOT NULL,
            wall_not_after REAL NOT NULL,
            offline_policy TEXT NOT NULL CHECK(offline_policy='count'),
            rollback_tolerance_ms INTEGER NOT NULL CHECK(rollback_tolerance_ms >= 0),
            revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
            status TEXT NOT NULL CHECK(status IN ('open','expired','completed')),
            terminal_reason TEXT,
            terminal_at REAL,
            FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(parent_deadline_id)
                REFERENCES workflow_research_deadlines(deadline_id) ON DELETE CASCADE
        )""",
        """CREATE INDEX idx_workflow_research_deadlines_run_status
            ON workflow_research_deadlines(run_id,status)""",
        """CREATE TABLE workflow_research_resource_budgets (
            budget_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            policy_hash TEXT NOT NULL,
            resource_kind TEXT NOT NULL
                CHECK(resource_kind IN ('query','fetch','browser','llm','lane')),
            hard_limit INTEGER NOT NULL CHECK(hard_limit >= 0),
            reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved >= 0),
            consumed INTEGER NOT NULL DEFAULT 0 CHECK(consumed >= 0),
            revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
            UNIQUE(run_id,resource_kind),
            FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
        )""",
        """CREATE TABLE workflow_research_resource_reservations (
            reservation_id TEXT PRIMARY KEY,
            budget_id TEXT NOT NULL,
            deadline_id TEXT NOT NULL,
            effect_id TEXT NOT NULL,
            amount_reserved INTEGER NOT NULL CHECK(amount_reserved > 0),
            amount_actual INTEGER CHECK(
                amount_actual IS NULL OR
                (amount_actual >= 0 AND amount_actual <= amount_reserved)
            ),
            status TEXT NOT NULL CHECK(status IN ('reserved','committed','released')),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(effect_id,budget_id),
            FOREIGN KEY(budget_id)
                REFERENCES workflow_research_resource_budgets(budget_id) ON DELETE CASCADE,
            FOREIGN KEY(deadline_id)
                REFERENCES workflow_research_deadlines(deadline_id) ON DELETE CASCADE,
            FOREIGN KEY(effect_id) REFERENCES workflow_effects(effect_id) ON DELETE CASCADE
        )""",
        """CREATE TABLE workflow_effect_attempt_heads (
            logical_effect_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            latest_attempt_no INTEGER NOT NULL CHECK(latest_attempt_no >= 1),
            canonical_effect_id TEXT,
            policy_id TEXT NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(canonical_effect_id) REFERENCES workflow_effects(effect_id)
        )""",
        """CREATE TABLE workflow_research_continuation_heads (
            parent_run_id TEXT PRIMARY KEY,
            child_run_id TEXT NOT NULL UNIQUE,
            parent_operation_id TEXT NOT NULL,
            child_operation_id TEXT NOT NULL UNIQUE,
            source_snapshot_hash TEXT NOT NULL,
            spec_hash TEXT NOT NULL,
            spec_blob_digest TEXT NOT NULL,
            evidence_head_hash TEXT NOT NULL,
            policy_version INTEGER NOT NULL CHECK(policy_version=1),
            claimed_at REAL NOT NULL,
            CHECK(parent_run_id <> child_run_id),
            CHECK(length(spec_hash)=64 AND spec_hash NOT GLOB '*[^0-9a-f]*'),
            CHECK(
                length(spec_blob_digest)=64 AND
                spec_blob_digest NOT GLOB '*[^0-9a-f]*'
            ),
            FOREIGN KEY(parent_run_id) REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
            FOREIGN KEY(child_run_id) REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
            FOREIGN KEY(parent_operation_id)
                REFERENCES workflow_research_lineage(operation_id) ON DELETE RESTRICT,
            FOREIGN KEY(child_operation_id)
                REFERENCES workflow_research_lineage(operation_id) ON DELETE RESTRICT,
            FOREIGN KEY(source_snapshot_hash)
                REFERENCES workflow_research_snapshots(snapshot_hash) ON DELETE RESTRICT,
            FOREIGN KEY(spec_blob_digest) REFERENCES workflow_blobs(sha256) ON DELETE RESTRICT
        )""",
        """CREATE INDEX idx_workflow_research_continuation_heads_snapshot
            ON workflow_research_continuation_heads(source_snapshot_hash,parent_run_id)""",
    ]
    if "workflow_effects" in tables:
        statements.extend(
            (
                """CREATE UNIQUE INDEX uq_workflow_effect_logical_attempt
                    ON workflow_effects(run_id,logical_effect_id,attempt_no)
                    WHERE logical_effect_id IS NOT NULL""",
                """CREATE INDEX idx_workflow_effect_attempt_heads_run
                    ON workflow_effect_attempt_heads(run_id,updated_at)""",
            )
        )
    if "workflow_deliveries" in tables:
        statements.extend(
            (
                """CREATE UNIQUE INDEX uq_workflow_deliveries_manifest_spec
                    ON workflow_deliveries(run_id,manifest_ref,intent_id,channel,target_id)
                    WHERE manifest_ref IS NOT NULL""",
                """CREATE INDEX idx_workflow_deliveries_manifest_required_status
                    ON workflow_deliveries(
                        run_id,manifest_ref,required_durable,status,next_attempt_at
                    )""",
            )
        )
    for statement in statements:
        await db.execute(statement)
    await db.execute(
        "INSERT INTO workflow_schema_migrations(version, applied_at) VALUES(4, ?)",
        (time.time(),),
    )
    await db.execute("PRAGMA user_version=4")
    await db.commit()


async def _migrate_v4_to_v5(db: aiosqlite.Connection) -> None:
    """Install the generic execution ledger without rewriting legacy rows."""

    await db.executescript(
        """
        BEGIN IMMEDIATE;

        CREATE TABLE execution_runs (
            run_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            idempotency_key TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL,
            root_run_id TEXT NOT NULL,
            parent_run_id TEXT,
            request_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            venue TEXT NOT NULL,
            workspace_json TEXT NOT NULL,
            capability_hash TEXT NOT NULL,
            provider_plan_json TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            auth_epoch INTEGER NOT NULL DEFAULT 0 CHECK(auth_epoch>=0),
            payload_fingerprint TEXT NOT NULL,
            capability_fingerprint TEXT NOT NULL,
            driver_kind TEXT NOT NULL,
            profile_key TEXT NOT NULL,
            persistence_level TEXT NOT NULL
                CHECK(persistence_level IN ('ephemeral','durable')),
            status TEXT NOT NULL CHECK(status IN (
                'created','queued','running','waiting','cancel_requested',
                'completed','failed','cancelled'
            )),
            version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0),
            durable_seq INTEGER NOT NULL DEFAULT 0 CHECK(durable_seq>=0),
            terminal_event_id TEXT,
            cancel_reason TEXT,
            created_at REAL NOT NULL,
            started_at REAL,
            updated_at REAL NOT NULL,
            ended_at REAL,
            CHECK(parent_run_id IS NULL OR parent_run_id<>run_id),
            CHECK(length(payload_fingerprint)=64
                AND payload_fingerprint NOT GLOB '*[^0-9a-f]*'),
            CHECK(length(capability_fingerprint)=64
                AND capability_fingerprint NOT GLOB '*[^0-9a-f]*'),
            CHECK(length(capability_hash)=64
                AND capability_hash NOT GLOB '*[^0-9a-f]*'),
            CHECK(capability_hash=capability_fingerprint),
            CHECK(
                (status IN ('completed','failed','cancelled')
                    AND ended_at IS NOT NULL AND terminal_event_id IS NOT NULL)
                OR
                (status NOT IN ('completed','failed','cancelled')
                    AND ended_at IS NULL AND terminal_event_id IS NULL)
            ),
            FOREIGN KEY(root_run_id) REFERENCES execution_runs(run_id) ON DELETE RESTRICT,
            FOREIGN KEY(parent_run_id) REFERENCES execution_runs(run_id) ON DELETE RESTRICT
        );
        CREATE INDEX idx_execution_runs_session_created
            ON execution_runs(session_id,created_at DESC,run_id);
        CREATE INDEX idx_execution_runs_root_status
            ON execution_runs(root_run_id,status,created_at,run_id);
        CREATE INDEX idx_execution_runs_parent_status
            ON execution_runs(parent_run_id,status,created_at,run_id);

        CREATE TABLE execution_run_links (
            link_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            root_run_id TEXT NOT NULL,
            parent_run_id TEXT NOT NULL,
            child_run_id TEXT NOT NULL,
            attachment_policy TEXT NOT NULL CHECK(attachment_policy IN (
                'attached','detached','root_terminal_child'
            )),
            link_kind TEXT NOT NULL CHECK(link_kind IN ('structural','domain')),
            domain_kind TEXT NOT NULL DEFAULT '',
            domain_id TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            CHECK(parent_run_id<>child_run_id),
            CHECK(
                (link_kind='structural' AND domain_kind='' AND domain_id='')
                OR
                (link_kind='domain' AND domain_kind<>'' AND domain_id<>'')
            ),
            UNIQUE(parent_run_id,child_run_id,link_kind,domain_kind,domain_id),
            FOREIGN KEY(root_run_id) REFERENCES execution_runs(run_id) ON DELETE RESTRICT,
            FOREIGN KEY(parent_run_id) REFERENCES execution_runs(run_id) ON DELETE RESTRICT,
            FOREIGN KEY(child_run_id) REFERENCES execution_runs(run_id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX uq_execution_run_links_structural_child
            ON execution_run_links(child_run_id) WHERE link_kind='structural';
        CREATE INDEX idx_execution_run_links_root_parent
            ON execution_run_links(root_run_id,parent_run_id,child_run_id);
        CREATE INDEX idx_execution_run_links_domain
            ON execution_run_links(domain_kind,domain_id,root_run_id);

        CREATE TABLE execution_decisions (
            decision_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            run_id TEXT NOT NULL,
            nonce TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN (
                'permission','plan','clarification','ppt_outline',
                'skill_candidate','workflow_hitl'
            )),
            status TEXT NOT NULL CHECK(status IN ('open','allowed','denied','expired')),
            prompt_schema_version INTEGER NOT NULL CHECK(prompt_schema_version>0),
            prompt_json TEXT NOT NULL,
            response_schema_version INTEGER CHECK(response_schema_version IS NULL OR response_schema_version>0),
            response_json TEXT,
            domain_kind TEXT,
            domain_id TEXT,
            call_id TEXT,
            effect_id TEXT,
            tool_name TEXT,
            args_hash TEXT,
            capability_hash TEXT,
            scope_hash TEXT,
            decision_version INTEGER NOT NULL DEFAULT 0 CHECK(decision_version>=0),
            expires_at REAL,
            created_at REAL NOT NULL,
            resolved_at REAL,
            consumed_at REAL,
            consumed_checkpoint_id TEXT,
            UNIQUE(run_id,nonce),
            CHECK(
                kind<>'permission' OR (
                    call_id IS NOT NULL AND effect_id IS NOT NULL AND tool_name IS NOT NULL
                    AND args_hash IS NOT NULL AND capability_hash IS NOT NULL
                    AND scope_hash IS NOT NULL
                )
            ),
            CHECK(
                (status='open' AND response_json IS NULL AND resolved_at IS NULL)
                OR
                (status IN ('allowed','denied') AND response_json IS NOT NULL
                    AND response_schema_version IS NOT NULL AND resolved_at IS NOT NULL)
                OR
                (status='expired' AND resolved_at IS NOT NULL)
            ),
            CHECK(
                (consumed_at IS NULL AND consumed_checkpoint_id IS NULL)
                OR (consumed_at IS NOT NULL AND consumed_checkpoint_id IS NOT NULL)
            ),
            FOREIGN KEY(run_id) REFERENCES execution_runs(run_id) ON DELETE CASCADE
        );
        CREATE INDEX idx_execution_decisions_run_status_expiry
            ON execution_decisions(run_id,status,expires_at,created_at);

        CREATE TABLE execution_grants (
            grant_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            decision_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            call_id TEXT NOT NULL,
            effect_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            args_hash TEXT NOT NULL,
            capability_hash TEXT NOT NULL,
            scope_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('issued','consumed','expired','revoked')),
            grant_version INTEGER NOT NULL DEFAULT 0 CHECK(grant_version>=0),
            expires_at REAL NOT NULL,
            created_at REAL NOT NULL,
            consumed_at REAL,
            CHECK(
                (status='consumed' AND consumed_at IS NOT NULL)
                OR (status<>'consumed' AND consumed_at IS NULL)
            ),
            FOREIGN KEY(decision_id) REFERENCES execution_decisions(decision_id) ON DELETE CASCADE,
            FOREIGN KEY(run_id) REFERENCES execution_runs(run_id) ON DELETE CASCADE
        );
        CREATE INDEX idx_execution_grants_run_status_expiry
            ON execution_grants(run_id,status,expires_at);
        CREATE TRIGGER execution_grant_permission_guard
        BEFORE INSERT ON execution_grants
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM execution_decisions AS decision
                WHERE decision.decision_id=NEW.decision_id
                  AND decision.run_id=NEW.run_id
                  AND decision.kind='permission'
                  AND decision.status='allowed'
                  AND decision.call_id=NEW.call_id
                  AND decision.effect_id=NEW.effect_id
                  AND decision.tool_name=NEW.tool_name
                  AND decision.args_hash=NEW.args_hash
                  AND decision.capability_hash=NEW.capability_hash
                  AND decision.scope_hash=NEW.scope_hash
            ) THEN RAISE(ABORT,'invalid_execution_grant_decision') END;
        END;

        CREATE TABLE execution_effects (
            effect_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            run_id TEXT NOT NULL,
            effect_fingerprint TEXT NOT NULL,
            call_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            args_hash TEXT NOT NULL,
            capability_hash TEXT NOT NULL,
            scope_hash TEXT,
            effect_type TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'prepared','running','succeeded','failed','accepted','unknown','cancelled','late_reconciled'
            )),
            policy_json TEXT NOT NULL,
            prepared_json TEXT NOT NULL,
            outcome_json TEXT,
            receipt_ref TEXT,
            artifact_refs_json TEXT NOT NULL DEFAULT '[]',
            effect_version INTEGER NOT NULL DEFAULT 0 CHECK(effect_version>=0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            ended_at REAL,
            UNIQUE(run_id,effect_fingerprint),
            FOREIGN KEY(run_id) REFERENCES execution_runs(run_id) ON DELETE CASCADE
        );
        CREATE INDEX idx_execution_effects_run_status
            ON execution_effects(run_id,status,updated_at);

        CREATE TABLE execution_effect_attempts (
            effect_id TEXT NOT NULL,
            attempt_no INTEGER NOT NULL CHECK(attempt_no>=1),
            status TEXT NOT NULL CHECK(status IN (
                'running','succeeded','failed','accepted','unknown','cancelled','late_reconciled'
            )),
            worker_owner TEXT,
            worker_epoch INTEGER NOT NULL DEFAULT 0 CHECK(worker_epoch>=0),
            started_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            ended_at REAL,
            error_json TEXT,
            outcome_json TEXT,
            PRIMARY KEY(effect_id,attempt_no),
            FOREIGN KEY(effect_id) REFERENCES execution_effects(effect_id) ON DELETE CASCADE
        );
        CREATE INDEX idx_execution_effect_attempts_status
            ON execution_effect_attempts(status,updated_at);

        CREATE TABLE execution_effect_links (
            run_id TEXT NOT NULL,
            node_execution_id TEXT NOT NULL DEFAULT '',
            effect_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            PRIMARY KEY(run_id,node_execution_id,effect_id,checkpoint_ns,checkpoint_id),
            FOREIGN KEY(run_id) REFERENCES execution_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(effect_id) REFERENCES execution_effects(effect_id) ON DELETE CASCADE
        );
        CREATE INDEX idx_execution_effect_links_checkpoint
            ON execution_effect_links(run_id,checkpoint_ns,checkpoint_id,node_execution_id);

        CREATE TABLE execution_events (
            event_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            event_key TEXT NOT NULL,
            run_id TEXT NOT NULL,
            durable_seq INTEGER NOT NULL CHECK(durable_seq>=1),
            kind TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'succeeded','failed','accepted','waiting',
                'cancel_requested','cancelled','unknown'
            )),
            driver_kind TEXT NOT NULL,
            correlation_json TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            error_json TEXT,
            artifact_refs_json TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL,
            UNIQUE(run_id,durable_seq),
            UNIQUE(run_id,event_key),
            FOREIGN KEY(run_id) REFERENCES execution_runs(run_id) ON DELETE CASCADE
        );
        CREATE INDEX idx_execution_events_run_created
            ON execution_events(run_id,durable_seq,event_id);

        CREATE TABLE execution_deliveries (
            delivery_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            event_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            sink_kind TEXT NOT NULL,
            sink_instance TEXT NOT NULL,
            target_id TEXT NOT NULL,
            policy TEXT NOT NULL CHECK(policy IN (
                'durable_required','retry_while_bound','best_effort'
            )),
            status TEXT NOT NULL CHECK(status IN (
                'pending','delivering','delivered','failed','discarded'
            )),
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
            delivery_version INTEGER NOT NULL DEFAULT 0 CHECK(delivery_version>=0),
            next_attempt_at REAL,
            last_error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            delivered_at REAL,
            UNIQUE(event_id,sink_kind,sink_instance,target_id),
            FOREIGN KEY(event_id) REFERENCES execution_events(event_id) ON DELETE CASCADE,
            FOREIGN KEY(run_id) REFERENCES execution_runs(run_id) ON DELETE CASCADE
        );
        CREATE INDEX idx_execution_deliveries_retry
            ON execution_deliveries(status,next_attempt_at,created_at);
        CREATE INDEX idx_execution_deliveries_run
            ON execution_deliveries(run_id,status,created_at);

        CREATE TABLE execution_continuations (
            run_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            command_schema_version INTEGER NOT NULL CHECK(command_schema_version>0),
            canonical_messages_json TEXT NOT NULL,
            session_projection_cursor INTEGER NOT NULL DEFAULT 0
                CHECK(session_projection_cursor>=0),
            prepared_context_ref TEXT,
            tool_set_snapshot_ref TEXT,
            pending_prepared_call_json TEXT,
            pending_decision_id TEXT,
            iteration INTEGER NOT NULL DEFAULT 0 CHECK(iteration>=0),
            provider_state_json TEXT NOT NULL DEFAULT '{}',
            continuation_version INTEGER NOT NULL DEFAULT 0 CHECK(continuation_version>=0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(run_id) REFERENCES execution_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(pending_decision_id)
                REFERENCES execution_decisions(decision_id) ON DELETE RESTRICT
        );

        CREATE TABLE execution_child_commands (
            operation_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            parent_run_id TEXT NOT NULL,
            child_run_id TEXT NOT NULL UNIQUE,
            profile_key TEXT NOT NULL,
            join_policy TEXT NOT NULL CHECK(join_policy IN (
                'attached','detached','root_terminal_child'
            )),
            capability_snapshot_ref TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'pending','leased','scheduled','acked','failed','cancelled'
            )),
            schedule_lease_owner TEXT,
            schedule_lease_epoch INTEGER NOT NULL DEFAULT 0 CHECK(schedule_lease_epoch>=0),
            schedule_lease_expires_at REAL,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
            next_attempt_at REAL,
            last_error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            ack_at REAL,
            CHECK(parent_run_id<>child_run_id),
            CHECK(
                status NOT IN ('leased','scheduled')
                OR (
                    schedule_lease_owner IS NOT NULL
                    AND schedule_lease_epoch>0
                    AND schedule_lease_expires_at IS NOT NULL
                )
            ),
            CHECK((status='acked' AND ack_at IS NOT NULL) OR status<>'acked'),
            FOREIGN KEY(parent_run_id) REFERENCES execution_runs(run_id) ON DELETE RESTRICT,
            FOREIGN KEY(child_run_id) REFERENCES execution_runs(run_id) ON DELETE CASCADE
        );
        CREATE INDEX idx_execution_child_commands_schedule
            ON execution_child_commands(status,next_attempt_at,schedule_lease_expires_at);
        CREATE INDEX idx_execution_child_commands_parent
            ON execution_child_commands(parent_run_id,status,created_at);

        INSERT INTO workflow_schema_migrations(version,applied_at)
        VALUES(5,CAST(strftime('%s','now') AS REAL));
        PRAGMA user_version=5;
        COMMIT;
        """
    )


async def _migrate_v5_to_v6(db: aiosqlite.Connection) -> None:
    """Make delegated-child commands durable before child creation.

    The v5 child_run_id foreign key required the child row to exist before the
    command could commit, which inverted the durable scheduler boundary.  The
    v6 command reserves a deterministic child id and freezes its complete
    launch request first.  The structural link becomes the child-existence
    authority once scheduling succeeds.
    """

    existing = await (
        await db.execute("SELECT COUNT(*) FROM execution_child_commands")
    ).fetchone()
    if existing is not None and int(existing[0]) != 0:
        raise RuntimeError(
            "workflow.db v5 contains test-only child commands without a frozen "
            "launch request; clear or explicitly migrate them before schema v6"
        )

    await db.executescript(
        """
        BEGIN IMMEDIATE;

        ALTER TABLE execution_child_commands
            RENAME TO execution_child_commands_v5;

        CREATE TABLE execution_child_commands (
            operation_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            parent_run_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            child_run_id TEXT NOT NULL UNIQUE,
            profile_key TEXT NOT NULL,
            join_policy TEXT NOT NULL CHECK(join_policy IN (
                'attached','detached','root_terminal_child'
            )),
            capability_snapshot_ref TEXT NOT NULL,
            capability_subset_json TEXT NOT NULL,
            child_request_json TEXT NOT NULL,
            child_spec_json TEXT NOT NULL,
            intent_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'pending','leased','scheduled','acked','failed','cancelled'
            )),
            schedule_lease_owner TEXT,
            schedule_lease_epoch INTEGER NOT NULL DEFAULT 0 CHECK(schedule_lease_epoch>=0),
            schedule_lease_expires_at REAL,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
            next_attempt_at REAL,
            last_error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            ack_at REAL,
            CHECK(parent_run_id<>child_run_id),
            CHECK(length(intent_fingerprint)=64
                AND intent_fingerprint NOT GLOB '*[^0-9a-f]*'),
            CHECK(
                status NOT IN ('leased','scheduled')
                OR (
                    schedule_lease_owner IS NOT NULL
                    AND schedule_lease_epoch>0
                    AND schedule_lease_expires_at IS NOT NULL
                )
            ),
            CHECK((status='acked' AND ack_at IS NOT NULL) OR status<>'acked'),
            UNIQUE(parent_run_id,command_id),
            FOREIGN KEY(parent_run_id) REFERENCES execution_runs(run_id) ON DELETE RESTRICT
        );

        DROP TABLE execution_child_commands_v5;

        CREATE INDEX idx_execution_child_commands_schedule
            ON execution_child_commands(status,next_attempt_at,schedule_lease_expires_at);
        CREATE INDEX idx_execution_child_commands_parent
            ON execution_child_commands(parent_run_id,status,created_at);

        CREATE TABLE execution_child_signal_inbox (
            signal_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            operation_id TEXT NOT NULL,
            parent_run_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            child_run_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('accepted','terminal')),
            payload_json TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            delivered_at REAL,
            UNIQUE(operation_id,kind),
            FOREIGN KEY(operation_id)
                REFERENCES execution_child_commands(operation_id) ON DELETE CASCADE,
            FOREIGN KEY(parent_run_id) REFERENCES execution_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(child_run_id) REFERENCES execution_runs(run_id) ON DELETE CASCADE
        );
        CREATE INDEX idx_execution_child_signal_pending
            ON execution_child_signal_inbox(parent_run_id,delivered_at,created_at,signal_id);

        INSERT INTO workflow_schema_migrations(version,applied_at)
        VALUES(6,CAST(strftime('%s','now') AS REAL));
        PRAGMA user_version=6;
        COMMIT;
        """
    )


async def _migrate_v6_to_v7(db: aiosqlite.Connection) -> None:
    """Add the dormant runtime-owner and durable legacy-drain control plane.

    Version 7 is deliberately additive.  Existing execution rows remain owned
    by the legacy runtime at generation zero, and the singleton runtime state
    starts in the same mode.  Activation is a later deployment operation; this
    migration only installs the durable facts it will need.
    """

    await db.executescript(
        """
        BEGIN IMMEDIATE;

        ALTER TABLE execution_runs ADD COLUMN owner_kind TEXT NOT NULL
            DEFAULT 'legacy' CHECK(owner_kind IN ('legacy','kernel'));
        ALTER TABLE execution_runs ADD COLUMN owner_generation INTEGER NOT NULL
            DEFAULT 0 CHECK(
                (owner_kind='legacy' AND owner_generation=0)
                OR (owner_kind='kernel' AND owner_generation>0)
            );

        CREATE TABLE execution_runtime_state (
            singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
            generation INTEGER NOT NULL CHECK(generation>=0),
            phase TEXT NOT NULL CHECK(phase IN (
                'legacy','draining','activated','open'
            )),
            drain_manifest_hash TEXT,
            drain_count INTEGER NOT NULL DEFAULT 0 CHECK(drain_count>=0),
            created_at REAL NOT NULL,
            activated_at REAL,
            updated_at REAL NOT NULL,
            CHECK(
                drain_manifest_hash IS NULL
                OR (
                    length(drain_manifest_hash)=64
                    AND drain_manifest_hash NOT GLOB '*[^0-9a-f]*'
                )
            ),
            CHECK(
                (phase='legacy' AND generation=0
                    AND drain_manifest_hash IS NULL AND drain_count=0
                    AND activated_at IS NULL)
                OR
                (phase='draining' AND generation=0
                    AND drain_manifest_hash IS NOT NULL
                    AND activated_at IS NULL)
                OR
                (phase IN ('activated','open') AND generation>0
                    AND drain_manifest_hash IS NOT NULL
                    AND activated_at IS NOT NULL)
            )
        );

        INSERT INTO execution_runtime_state(
            singleton_id,generation,phase,drain_manifest_hash,drain_count,
            created_at,activated_at,updated_at
        ) VALUES(
            1,0,'legacy',NULL,0,
            CAST(strftime('%s','now') AS REAL),NULL,
            CAST(strftime('%s','now') AS REAL)
        );

        CREATE TABLE execution_legacy_drain_items (
            drain_item_id TEXT PRIMARY KEY,
            manifest_generation INTEGER NOT NULL CHECK(manifest_generation>0),
            source_kind TEXT NOT NULL CHECK(source_kind IN (
                'workflow_run','execution_run'
            )),
            source_run_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'pending','leased','drained','failed'
            )),
            lease_owner TEXT,
            lease_epoch INTEGER NOT NULL DEFAULT 0 CHECK(lease_epoch>=0),
            lease_expires_at REAL,
            last_error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            drained_at REAL,
            UNIQUE(manifest_generation,source_kind,source_run_id),
            CHECK(
                (status='leased' AND lease_owner IS NOT NULL
                    AND lease_epoch>0 AND lease_expires_at IS NOT NULL)
                OR
                (status<>'leased' AND lease_owner IS NULL
                    AND lease_expires_at IS NULL)
            ),
            CHECK(
                (status='drained' AND drained_at IS NOT NULL)
                OR (status<>'drained' AND drained_at IS NULL)
            )
        );
        CREATE INDEX idx_execution_legacy_drain_items_status
            ON execution_legacy_drain_items(
                manifest_generation,status,lease_expires_at,updated_at
            );

        CREATE TRIGGER execution_runs_owner_insert_guard
        BEFORE INSERT ON execution_runs
        BEGIN
            SELECT CASE WHEN NOT EXISTS(
                SELECT 1 FROM execution_runtime_state
                WHERE singleton_id=1 AND (
                    (phase='legacy' AND generation=0
                        AND NEW.owner_kind='legacy'
                        AND NEW.owner_generation=0)
                    OR
                    (phase='open' AND generation>0
                        AND NEW.owner_kind='kernel'
                        AND NEW.owner_generation=generation)
                )
            ) THEN RAISE(ABORT,'execution_owner_not_active') END;
        END;

        CREATE TRIGGER execution_runs_owner_immutable
        BEFORE UPDATE OF owner_kind,owner_generation ON execution_runs
        WHEN NEW.owner_kind<>OLD.owner_kind
            OR NEW.owner_generation<>OLD.owner_generation
        BEGIN
            SELECT RAISE(ABORT,'execution_owner_immutable');
        END;

        INSERT INTO workflow_schema_migrations(version,applied_at)
        VALUES(7,CAST(strftime('%s','now') AS REAL));
        PRAGMA user_version=7;
        COMMIT;
        """
    )


async def _migrate_v7_to_v8(db: aiosqlite.Connection) -> None:
    """Add a per-run recovery lease, independent of deployment ownership."""

    await db.executescript(
        """
        BEGIN IMMEDIATE;
        ALTER TABLE execution_runs ADD COLUMN recovery_owner TEXT;
        ALTER TABLE execution_runs ADD COLUMN recovery_epoch INTEGER NOT NULL
            DEFAULT 0 CHECK(recovery_epoch>=0);
        ALTER TABLE execution_runs ADD COLUMN recovery_expires_at REAL;
        ALTER TABLE execution_runs ADD COLUMN recovery_heartbeat_at REAL;
        CREATE INDEX idx_execution_runs_recovery_lease
            ON execution_runs(status,recovery_expires_at,run_id);
        CREATE TRIGGER execution_runs_recovery_lease_guard_insert
        BEFORE INSERT ON execution_runs
        WHEN NOT (
            (NEW.recovery_owner IS NULL AND NEW.recovery_expires_at IS NULL)
            OR
            (NEW.recovery_owner IS NOT NULL AND NEW.recovery_epoch>0
                AND NEW.recovery_expires_at IS NOT NULL)
        )
        BEGIN SELECT RAISE(ABORT,'invalid_recovery_lease'); END;
        CREATE TRIGGER execution_runs_recovery_lease_guard_update
        BEFORE UPDATE OF recovery_owner,recovery_epoch,recovery_expires_at
            ON execution_runs
        WHEN NOT (
            (NEW.recovery_owner IS NULL AND NEW.recovery_expires_at IS NULL)
            OR
            (NEW.recovery_owner IS NOT NULL AND NEW.recovery_epoch>0
                AND NEW.recovery_expires_at IS NOT NULL)
        )
        BEGIN SELECT RAISE(ABORT,'invalid_recovery_lease'); END;
        INSERT INTO workflow_schema_migrations(version,applied_at)
        VALUES(8,CAST(strftime('%s','now') AS REAL));
        PRAGMA user_version=8;
        COMMIT;
        """
    )


async def _migrate_v8_to_v9(db: aiosqlite.Connection) -> None:
    """Permit only fenced, precommitted child scheduling during activation."""

    await db.executescript(
        """
        BEGIN IMMEDIATE;
        DROP TRIGGER execution_runs_owner_insert_guard;
        CREATE TRIGGER execution_runs_owner_insert_guard
        BEFORE INSERT ON execution_runs
        BEGIN
            SELECT CASE WHEN NOT EXISTS(
                SELECT 1 FROM execution_runtime_state AS runtime
                WHERE runtime.singleton_id=1 AND (
                    (runtime.phase='legacy' AND runtime.generation=0
                        AND NEW.owner_kind='legacy'
                        AND NEW.owner_generation=0)
                    OR
                    (runtime.phase='open' AND runtime.generation>0
                        AND NEW.owner_kind='kernel'
                        AND NEW.owner_generation=runtime.generation)
                    OR
                    (runtime.phase='activated' AND runtime.generation>0
                        AND NEW.owner_kind='kernel'
                        AND NEW.owner_generation=runtime.generation
                        AND EXISTS(
                            SELECT 1
                            FROM execution_child_commands AS command
                            JOIN execution_runs AS parent
                              ON parent.run_id=command.parent_run_id
                            WHERE command.child_run_id=NEW.run_id
                              AND command.parent_run_id=NEW.parent_run_id
                              AND command.status IN ('leased','scheduled')
                              AND command.schedule_lease_owner IS NOT NULL
                              AND command.schedule_lease_epoch>0
                              AND command.schedule_lease_expires_at IS NOT NULL
                              AND command.schedule_lease_expires_at>NEW.created_at
                              AND json_extract(command.child_spec_json,'$.run_id')=NEW.run_id
                              AND json_extract(command.child_spec_json,'$.idempotency_key')
                                  =NEW.idempotency_key
                              AND json_extract(
                                  command.child_spec_json,'$.context.parent_run_id'
                              )=NEW.parent_run_id
                              AND parent.owner_kind='kernel'
                              AND parent.owner_generation=runtime.generation
                              AND parent.root_run_id=NEW.root_run_id
                              AND parent.session_id=NEW.session_id
                              AND parent.principal_id=NEW.principal_id
                              AND parent.auth_epoch=NEW.auth_epoch
                        ))
                )
            ) THEN RAISE(ABORT,'execution_owner_not_active') END;
        END;
        INSERT INTO workflow_schema_migrations(version,applied_at)
        VALUES(9,CAST(strftime('%s','now') AS REAL));
        PRAGMA user_version=9;
        COMMIT;
        """
    )


async def _migrate_v9_to_v10(db: aiosqlite.Connection) -> None:
    """Install durable task attempts, failure facts and profile launch tickets."""

    await db.executescript(
        """
        BEGIN IMMEDIATE;

        CREATE TABLE IF NOT EXISTS execution_task_goals (
            goal_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            root_run_id TEXT NOT NULL UNIQUE,
            task_scope_id TEXT NOT NULL UNIQUE,
            objective_ref TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'active','waiting_external','completed','blocked','cancelled'
            )),
            goal_version INTEGER NOT NULL DEFAULT 0 CHECK(goal_version>=0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            ended_at REAL,
            CHECK(
                (status IN ('completed','blocked','cancelled') AND ended_at IS NOT NULL)
                OR
                (status IN ('active','waiting_external') AND ended_at IS NULL)
            ),
            FOREIGN KEY(root_run_id)
                REFERENCES execution_runs(run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS execution_plan_versions (
            root_run_id TEXT NOT NULL,
            plan_version INTEGER NOT NULL CHECK(plan_version>=1),
            trigger_failure_set_id TEXT,
            created_at REAL NOT NULL,
            PRIMARY KEY(root_run_id,plan_version),
            FOREIGN KEY(root_run_id)
                REFERENCES execution_task_goals(root_run_id) ON DELETE CASCADE
        );

        CREATE TRIGGER IF NOT EXISTS execution_plan_versions_monotonic
        BEFORE INSERT ON execution_plan_versions
        WHEN NEW.plan_version<>COALESCE(
            (SELECT MAX(plan_version)+1 FROM execution_plan_versions
             WHERE root_run_id=NEW.root_run_id),
            1
        )
        BEGIN SELECT RAISE(ABORT,'execution_plan_version_not_monotonic'); END;

        CREATE TABLE IF NOT EXISTS execution_provider_turn_fences (
            provider_turn_id TEXT PRIMARY KEY,
            root_run_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL CHECK(
                length(request_hash)=64
                AND request_hash NOT GLOB '*[^0-9a-f]*'
            ),
            state TEXT NOT NULL CHECK(state IN ('pending','accepted','cancelled')),
            accepted_batch_id TEXT,
            fence_version INTEGER NOT NULL DEFAULT 0 CHECK(fence_version>=0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            accepted_at REAL,
            UNIQUE(root_run_id,provider_turn_id),
            UNIQUE(root_run_id,idempotency_key),
            CHECK(
                (state='accepted' AND accepted_batch_id IS NOT NULL
                    AND accepted_at IS NOT NULL)
                OR
                (state<>'accepted' AND accepted_batch_id IS NULL
                    AND accepted_at IS NULL)
            ),
            FOREIGN KEY(root_run_id)
                REFERENCES execution_task_goals(root_run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS execution_provider_action_batches (
            provider_batch_id TEXT PRIMARY KEY,
            root_run_id TEXT NOT NULL,
            provider_turn_id TEXT NOT NULL,
            canonical_assistant_batch_ref TEXT NOT NULL,
            batch_fingerprint TEXT NOT NULL CHECK(
                length(batch_fingerprint)=64
                AND batch_fingerprint NOT GLOB '*[^0-9a-f]*'
            ),
            pending_call_count INTEGER NOT NULL CHECK(pending_call_count>=1),
            failure_set_id TEXT,
            status TEXT NOT NULL CHECK(status IN (
                'admitted','running','waiting_external','ready_backfill','settled'
            )),
            batch_version INTEGER NOT NULL DEFAULT 0 CHECK(batch_version>=0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            settled_at REAL,
            UNIQUE(root_run_id,provider_turn_id),
            CHECK(
                (status='settled' AND settled_at IS NOT NULL)
                OR (status<>'settled' AND settled_at IS NULL)
            ),
            FOREIGN KEY(root_run_id)
                REFERENCES execution_task_goals(root_run_id) ON DELETE CASCADE,
            FOREIGN KEY(provider_turn_id)
                REFERENCES execution_provider_turn_fences(provider_turn_id)
                ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS execution_attempt_records (
            attempt_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            root_run_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            provider_turn_id TEXT NOT NULL,
            provider_batch_id TEXT NOT NULL,
            plan_version INTEGER NOT NULL CHECK(plan_version>=1),
            trigger_failure_set_id TEXT,
            supersedes_attempt_id TEXT,
            strategy_fingerprint TEXT NOT NULL CHECK(
                length(strategy_fingerprint)=64
                AND strategy_fingerprint NOT GLOB '*[^0-9a-f]*'
            ),
            planned_call_refs_json TEXT NOT NULL
                CHECK(json_valid(planned_call_refs_json)),
            checkpoint_ref TEXT,
            status TEXT NOT NULL CHECK(status IN (
                'running','failed','rejected','succeeded','waiting_external',
                'blocked','cancelled'
            )),
            budget_eligible INTEGER NOT NULL CHECK(budget_eligible IN (0,1)),
            attempt_version INTEGER NOT NULL DEFAULT 0 CHECK(attempt_version>=0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            ended_at REAL,
            UNIQUE(root_run_id,provider_batch_id),
            UNIQUE(root_run_id,provider_turn_id),
            CHECK(
                (status IN ('failed','rejected','succeeded','blocked','cancelled')
                    AND ended_at IS NOT NULL)
                OR
                (status IN ('running','waiting_external') AND ended_at IS NULL)
            ),
            FOREIGN KEY(root_run_id)
                REFERENCES execution_task_goals(root_run_id) ON DELETE CASCADE,
            FOREIGN KEY(run_id)
                REFERENCES execution_runs(run_id) ON DELETE RESTRICT,
            FOREIGN KEY(provider_turn_id)
                REFERENCES execution_provider_turn_fences(provider_turn_id)
                ON DELETE RESTRICT,
            FOREIGN KEY(provider_batch_id)
                REFERENCES execution_provider_action_batches(provider_batch_id)
                ON DELETE CASCADE,
            FOREIGN KEY(root_run_id,plan_version)
                REFERENCES execution_plan_versions(root_run_id,plan_version)
                ON DELETE RESTRICT,
            FOREIGN KEY(supersedes_attempt_id)
                REFERENCES execution_attempt_records(attempt_id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS execution_provider_action_calls (
            call_record_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            root_run_id TEXT NOT NULL,
            provider_batch_id TEXT NOT NULL,
            call_order INTEGER NOT NULL CHECK(call_order>=0),
            provider_call_id TEXT NOT NULL,
            raw_tool_name TEXT NOT NULL,
            raw_arguments_ref TEXT NOT NULL,
            raw_arguments_hash TEXT NOT NULL CHECK(
                length(raw_arguments_hash)=64
                AND raw_arguments_hash NOT GLOB '*[^0-9a-f]*'
            ),
            parsed_arguments_hash TEXT CHECK(
                parsed_arguments_hash IS NULL OR (
                    length(parsed_arguments_hash)=64
                    AND parsed_arguments_hash NOT GLOB '*[^0-9a-f]*'
                )
            ),
            admission_state TEXT NOT NULL CHECK(admission_state IN (
                'admitted','prepared','rejected','waiting_external','settled'
            )),
            prepared_call_ref TEXT,
            command_boundary_ref TEXT,
            terminal_outcome_ref TEXT,
            call_version INTEGER NOT NULL DEFAULT 0 CHECK(call_version>=0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(provider_batch_id,call_order),
            UNIQUE(root_run_id,provider_call_id),
            CHECK(
                (admission_state='prepared'
                    AND parsed_arguments_hash IS NOT NULL
                    AND prepared_call_ref IS NOT NULL)
                OR admission_state<>'prepared'
            ),
            CHECK(
                (admission_state IN ('rejected','settled')
                    AND terminal_outcome_ref IS NOT NULL)
                OR
                (admission_state NOT IN ('rejected','settled')
                    AND terminal_outcome_ref IS NULL)
            ),
            FOREIGN KEY(root_run_id)
                REFERENCES execution_task_goals(root_run_id) ON DELETE CASCADE,
            FOREIGN KEY(provider_batch_id)
                REFERENCES execution_provider_action_batches(provider_batch_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_execution_provider_calls_batch_state
            ON execution_provider_action_calls(
                provider_batch_id,admission_state,call_order
            );

        CREATE TABLE IF NOT EXISTS execution_task_failure_reports (
            report_ref TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            root_run_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            task_scope_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            plan_version INTEGER NOT NULL CHECK(plan_version>=1),
            call_record_id TEXT NOT NULL,
            source_kind TEXT NOT NULL CHECK(source_kind IN (
                'tool_parse','tool_unknown','tool_preflight','tool_prepare',
                'tool_authorization','tool_executor','child_launch',
                'child_terminal','strategy_rejected'
            )),
            source_identity TEXT NOT NULL,
            provider_call_id TEXT,
            child_run_id TEXT,
            inner_failure_ref TEXT,
            failed_call_id TEXT,
            failed_effect_id TEXT,
            failed_step TEXT NOT NULL,
            error_class TEXT NOT NULL CHECK(error_class IN (
                'transient','task_project','capability','environment'
            )),
            error_code TEXT NOT NULL,
            error_fingerprint TEXT NOT NULL CHECK(
                length(error_fingerprint)=64
                AND error_fingerprint NOT GLOB '*[^0-9a-f]*'
            ),
            action_fingerprint TEXT NOT NULL CHECK(
                length(action_fingerprint)=64
                AND action_fingerprint NOT GLOB '*[^0-9a-f]*'
            ),
            exit_code INTEGER,
            evidence_refs_json TEXT NOT NULL CHECK(json_valid(evidence_refs_json)),
            completed_step_refs_json TEXT NOT NULL
                CHECK(json_valid(completed_step_refs_json)),
            artifact_refs_json TEXT NOT NULL CHECK(json_valid(artifact_refs_json)),
            checkpoint_ref TEXT,
            prior_strategy_fingerprints_json TEXT NOT NULL
                CHECK(json_valid(prior_strategy_fingerprints_json)),
            created_at REAL NOT NULL,
            UNIQUE(attempt_id,call_record_id),
            FOREIGN KEY(root_run_id)
                REFERENCES execution_task_goals(root_run_id) ON DELETE CASCADE,
            FOREIGN KEY(run_id)
                REFERENCES execution_runs(run_id) ON DELETE RESTRICT,
            FOREIGN KEY(attempt_id)
                REFERENCES execution_attempt_records(attempt_id) ON DELETE CASCADE,
            FOREIGN KEY(call_record_id)
                REFERENCES execution_provider_action_calls(call_record_id)
                ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS execution_attempt_failure_sets (
            failure_set_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            root_run_id TEXT NOT NULL,
            failed_attempt_id TEXT NOT NULL,
            primary_report_ref TEXT NOT NULL,
            backfill_state TEXT NOT NULL CHECK(backfill_state IN (
                'pending','ready','committed'
            )),
            provider_resume_state TEXT NOT NULL CHECK(provider_resume_state IN (
                'pending','requested','accepted'
            )),
            set_version INTEGER NOT NULL DEFAULT 0 CHECK(set_version>=0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(root_run_id,failed_attempt_id),
            FOREIGN KEY(root_run_id)
                REFERENCES execution_task_goals(root_run_id) ON DELETE CASCADE,
            FOREIGN KEY(failed_attempt_id)
                REFERENCES execution_attempt_records(attempt_id) ON DELETE CASCADE,
            FOREIGN KEY(primary_report_ref)
                REFERENCES execution_task_failure_reports(report_ref)
                ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS execution_attempt_failure_set_members (
            failure_set_id TEXT NOT NULL,
            report_ref TEXT NOT NULL,
            provider_call_order INTEGER NOT NULL CHECK(provider_call_order>=0),
            created_at REAL NOT NULL,
            PRIMARY KEY(failure_set_id,report_ref),
            UNIQUE(failure_set_id,provider_call_order),
            FOREIGN KEY(failure_set_id)
                REFERENCES execution_attempt_failure_sets(failure_set_id)
                ON DELETE CASCADE,
            FOREIGN KEY(report_ref)
                REFERENCES execution_task_failure_reports(report_ref)
                ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS execution_task_external_waits (
            wait_ref TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            root_run_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            call_record_id TEXT NOT NULL,
            provider_call_id TEXT NOT NULL,
            command_boundary_ref TEXT,
            effect_id TEXT,
            wait_kind TEXT NOT NULL CHECK(wait_kind IN (
                'credential','user_content','uac','third_party'
            )),
            required_action_ref TEXT NOT NULL,
            checkpoint_ref TEXT,
            resume_admission_state TEXT NOT NULL CHECK(
                resume_admission_state IN ('admitted','prepared')
            ),
            evidence_refs_json TEXT NOT NULL CHECK(json_valid(evidence_refs_json)),
            state TEXT NOT NULL CHECK(state IN ('open','satisfied','cancelled')),
            response_ref TEXT,
            wait_version INTEGER NOT NULL DEFAULT 0 CHECK(wait_version>=0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            resolved_at REAL,
            UNIQUE(root_run_id,call_record_id),
            CHECK(
                (state='open' AND response_ref IS NULL AND resolved_at IS NULL)
                OR
                (state='satisfied' AND response_ref IS NOT NULL
                    AND resolved_at IS NOT NULL)
                OR
                (state='cancelled' AND resolved_at IS NOT NULL)
            ),
            FOREIGN KEY(root_run_id)
                REFERENCES execution_task_goals(root_run_id) ON DELETE CASCADE,
            FOREIGN KEY(attempt_id)
                REFERENCES execution_attempt_records(attempt_id) ON DELETE CASCADE,
            FOREIGN KEY(call_record_id)
                REFERENCES execution_provider_action_calls(call_record_id)
                ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS execution_profile_launch_tickets (
            ticket_ref TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            parent_run_id TEXT NOT NULL,
            root_run_id TEXT NOT NULL,
            task_scope_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            provider_turn_id TEXT NOT NULL,
            profile_key TEXT NOT NULL,
            driver_kind TEXT NOT NULL,
            profile_catalog_generation INTEGER NOT NULL
                CHECK(profile_catalog_generation>0),
            capability_snapshot_ref TEXT NOT NULL,
            task_grant_ref TEXT NOT NULL,
            spawn_call_id TEXT NOT NULL,
            trigger_failure_set_id TEXT,
            request_fingerprint TEXT NOT NULL CHECK(
                length(request_fingerprint)=64
                AND request_fingerprint NOT GLOB '*[^0-9a-f]*'
            ),
            state TEXT NOT NULL CHECK(state IN ('issued','consumed','cancelled')),
            child_command_id TEXT UNIQUE,
            child_run_id TEXT UNIQUE,
            ticket_version INTEGER NOT NULL DEFAULT 0 CHECK(ticket_version>=0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            consumed_at REAL,
            cancelled_at REAL,
            UNIQUE(parent_run_id,spawn_call_id),
            CHECK(
                (state='issued' AND child_command_id IS NULL
                    AND child_run_id IS NULL AND consumed_at IS NULL
                    AND cancelled_at IS NULL)
                OR
                (state='consumed' AND child_command_id IS NOT NULL
                    AND child_run_id IS NOT NULL AND consumed_at IS NOT NULL
                    AND cancelled_at IS NULL)
                OR
                (state='cancelled' AND child_command_id IS NULL
                    AND child_run_id IS NULL AND consumed_at IS NULL
                    AND cancelled_at IS NOT NULL)
            ),
            FOREIGN KEY(parent_run_id)
                REFERENCES execution_runs(run_id) ON DELETE RESTRICT,
            FOREIGN KEY(root_run_id)
                REFERENCES execution_task_goals(root_run_id) ON DELETE CASCADE,
            FOREIGN KEY(attempt_id)
                REFERENCES execution_attempt_records(attempt_id) ON DELETE CASCADE,
            FOREIGN KEY(provider_turn_id)
                REFERENCES execution_provider_turn_fences(provider_turn_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_execution_profile_tickets_recovery
            ON execution_profile_launch_tickets(
                state,parent_run_id,created_at,ticket_ref
            );

        CREATE TRIGGER IF NOT EXISTS execution_task_goal_transition
        BEFORE UPDATE OF status ON execution_task_goals
        WHEN NOT (
            (OLD.status='active' AND NEW.status IN (
                'active','waiting_external','completed','blocked','cancelled'
            ))
            OR
            (OLD.status='waiting_external' AND NEW.status IN (
                'waiting_external','active','completed','blocked','cancelled'
            ))
            OR
            (OLD.status IN ('completed','blocked','cancelled')
                AND NEW.status=OLD.status)
        )
        BEGIN SELECT RAISE(ABORT,'invalid_execution_task_goal_transition'); END;

        CREATE TRIGGER IF NOT EXISTS execution_attempt_transition
        BEFORE UPDATE OF status ON execution_attempt_records
        WHEN NOT (
            (OLD.status='running' AND NEW.status IN (
                'running','failed','rejected','succeeded','waiting_external',
                'blocked','cancelled'
            ))
            OR
            (OLD.status='waiting_external' AND NEW.status IN (
                'waiting_external','running','failed','succeeded','blocked','cancelled'
            ))
            OR
            (OLD.status IN ('failed','rejected','succeeded','blocked','cancelled')
                AND NEW.status=OLD.status)
        )
        BEGIN SELECT RAISE(ABORT,'invalid_execution_attempt_transition'); END;

        CREATE TRIGGER IF NOT EXISTS execution_provider_call_transition
        BEFORE UPDATE OF admission_state ON execution_provider_action_calls
        WHEN NOT (
            (OLD.admission_state='admitted' AND NEW.admission_state IN (
                'admitted','prepared','rejected','waiting_external','settled'
            ))
            OR
            (OLD.admission_state='prepared' AND NEW.admission_state IN (
                'prepared','waiting_external','settled'
            ))
            OR
            (OLD.admission_state='waiting_external' AND NEW.admission_state IN (
                'waiting_external','admitted','prepared','settled'
            ))
            OR
            (OLD.admission_state IN ('rejected','settled')
                AND NEW.admission_state=OLD.admission_state)
        )
        BEGIN SELECT RAISE(ABORT,'invalid_execution_provider_call_transition'); END;

        CREATE TRIGGER IF NOT EXISTS execution_external_wait_transition
        BEFORE UPDATE OF state ON execution_task_external_waits
        WHEN NOT (
            (OLD.state='open' AND NEW.state IN ('open','satisfied','cancelled'))
            OR
            (OLD.state IN ('satisfied','cancelled') AND NEW.state=OLD.state)
        )
        BEGIN SELECT RAISE(ABORT,'invalid_execution_external_wait_transition'); END;

        CREATE TRIGGER IF NOT EXISTS execution_profile_ticket_transition
        BEFORE UPDATE OF state ON execution_profile_launch_tickets
        WHEN NOT (
            (OLD.state='issued' AND NEW.state IN ('issued','consumed','cancelled'))
            OR
            (OLD.state IN ('consumed','cancelled') AND NEW.state=OLD.state)
        )
        BEGIN SELECT RAISE(ABORT,'invalid_execution_profile_ticket_transition'); END;

        CREATE TRIGGER IF NOT EXISTS execution_profile_ticket_identity_immutable
        BEFORE UPDATE OF
            parent_run_id,root_run_id,task_scope_id,attempt_id,provider_turn_id,
            profile_key,driver_kind,profile_catalog_generation,
            capability_snapshot_ref,task_grant_ref,spawn_call_id,
            trigger_failure_set_id,request_fingerprint
        ON execution_profile_launch_tickets
        BEGIN SELECT RAISE(ABORT,'execution_profile_ticket_identity_immutable'); END;

        INSERT OR IGNORE INTO workflow_schema_migrations(version,applied_at)
        VALUES(10,CAST(strftime('%s','now') AS REAL));
        PRAGMA user_version=10;
        COMMIT;
        """
    )


async def _migrate_v10_to_v11(db: aiosqlite.Connection) -> None:
    """Install the capability repositories in the execution-owned database."""

    # Keep this import at migration time.  Importing the capabilities package
    # while ``workflows.effects`` is still initializing creates a package-level
    # cycle through ``capabilities.tool_proxy``.
    from deskpet.capabilities.store import CAPABILITY_SCHEMA_SQL

    await db.executescript(
        f"""
        BEGIN IMMEDIATE;
        {CAPABILITY_SCHEMA_SQL}
        INSERT OR IGNORE INTO workflow_schema_migrations(version,applied_at)
        VALUES(11,CAST(strftime('%s','now') AS REAL));
        PRAGMA user_version=11;
        COMMIT;
        """
    )


async def _migrate_v11_to_v12(db: aiosqlite.Connection) -> None:
    """Add content-addressed payloads used by same-run catalog refresh."""

    # Replaying the idempotent capability DDL keeps the capability repository
    # subordinate to the workflow database's migration owner.
    from deskpet.capabilities.store import CAPABILITY_SCHEMA_SQL

    await db.executescript(
        f"""
        BEGIN IMMEDIATE;
        {CAPABILITY_SCHEMA_SQL}
        INSERT OR IGNORE INTO workflow_schema_migrations(version,applied_at)
        VALUES(12,CAST(strftime('%s','now') AS REAL));
        PRAGMA user_version=12;
        COMMIT;
        """
    )


async def _migrate_v12_to_v13(db: aiosqlite.Connection) -> None:
    """Add host-signed capability failure receipts and repair budgets."""

    from deskpet.capabilities.store import CAPABILITY_SCHEMA_SQL

    await db.executescript(
        f"""
        BEGIN IMMEDIATE;
        {CAPABILITY_SCHEMA_SQL}
        INSERT OR IGNORE INTO workflow_schema_migrations(version,applied_at)
        VALUES(13,CAST(strftime('%s','now') AS REAL));
        PRAGMA user_version=13;
        COMMIT;
        """
    )


async def _migrate_v13_to_v14(db: aiosqlite.Connection) -> None:
    """Allow the same failure source to recur in later model Attempts."""

    await db.executescript(
        """
        PRAGMA foreign_keys=OFF;
        BEGIN IMMEDIATE;
        CREATE TABLE execution_task_failure_reports_v14 (
            report_ref TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            root_run_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            task_scope_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            plan_version INTEGER NOT NULL CHECK(plan_version>=1),
            call_record_id TEXT NOT NULL,
            source_kind TEXT NOT NULL CHECK(source_kind IN (
                'tool_parse','tool_unknown','tool_preflight','tool_prepare',
                'tool_authorization','tool_executor','child_launch',
                'child_terminal','strategy_rejected'
            )),
            source_identity TEXT NOT NULL,
            provider_call_id TEXT,
            child_run_id TEXT,
            inner_failure_ref TEXT,
            failed_call_id TEXT,
            failed_effect_id TEXT,
            failed_step TEXT NOT NULL,
            error_class TEXT NOT NULL CHECK(error_class IN (
                'transient','task_project','capability','environment'
            )),
            error_code TEXT NOT NULL,
            error_fingerprint TEXT NOT NULL CHECK(
                length(error_fingerprint)=64
                AND error_fingerprint NOT GLOB '*[^0-9a-f]*'
            ),
            action_fingerprint TEXT NOT NULL CHECK(
                length(action_fingerprint)=64
                AND action_fingerprint NOT GLOB '*[^0-9a-f]*'
            ),
            exit_code INTEGER,
            evidence_refs_json TEXT NOT NULL CHECK(json_valid(evidence_refs_json)),
            completed_step_refs_json TEXT NOT NULL
                CHECK(json_valid(completed_step_refs_json)),
            artifact_refs_json TEXT NOT NULL CHECK(json_valid(artifact_refs_json)),
            checkpoint_ref TEXT,
            prior_strategy_fingerprints_json TEXT NOT NULL
                CHECK(json_valid(prior_strategy_fingerprints_json)),
            created_at REAL NOT NULL,
            UNIQUE(attempt_id,call_record_id),
            FOREIGN KEY(root_run_id)
                REFERENCES execution_task_goals(root_run_id) ON DELETE CASCADE,
            FOREIGN KEY(run_id)
                REFERENCES execution_runs(run_id) ON DELETE RESTRICT,
            FOREIGN KEY(attempt_id)
                REFERENCES execution_attempt_records(attempt_id) ON DELETE CASCADE,
            FOREIGN KEY(call_record_id)
                REFERENCES execution_provider_action_calls(call_record_id)
                ON DELETE RESTRICT
        );
        INSERT INTO execution_task_failure_reports_v14(
            report_ref,schema_version,root_run_id,run_id,task_scope_id,
            attempt_id,plan_version,call_record_id,source_kind,source_identity,
            provider_call_id,child_run_id,inner_failure_ref,failed_call_id,
            failed_effect_id,failed_step,error_class,error_code,error_fingerprint,
            action_fingerprint,exit_code,evidence_refs_json,
            completed_step_refs_json,artifact_refs_json,checkpoint_ref,
            prior_strategy_fingerprints_json,created_at
        )
        SELECT
            report_ref,schema_version,root_run_id,run_id,task_scope_id,
            attempt_id,plan_version,call_record_id,source_kind,source_identity,
            provider_call_id,child_run_id,inner_failure_ref,failed_call_id,
            failed_effect_id,failed_step,error_class,error_code,error_fingerprint,
            action_fingerprint,exit_code,evidence_refs_json,
            completed_step_refs_json,artifact_refs_json,checkpoint_ref,
            prior_strategy_fingerprints_json,created_at
        FROM execution_task_failure_reports;
        DROP TABLE execution_task_failure_reports;
        ALTER TABLE execution_task_failure_reports_v14
            RENAME TO execution_task_failure_reports;
        INSERT OR IGNORE INTO workflow_schema_migrations(version,applied_at)
        VALUES(14,CAST(strftime('%s','now') AS REAL));
        PRAGMA user_version=14;
        COMMIT;
        PRAGMA foreign_keys=ON;
        """
    )


async def _migrate_v14_to_v15(db: aiosqlite.Connection) -> None:
    """Persist root-local work, conversation, and UI projection identity."""

    await db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS execution_task_work_contexts (
            root_run_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            session_id TEXT NOT NULL,
            task_scope_id TEXT NOT NULL UNIQUE,
            workspace_root TEXT,
            workspace_source TEXT NOT NULL CHECK(workspace_source IN (
                'existing','user_path','task_default','none'
            )),
            binding_version INTEGER NOT NULL CHECK(binding_version>0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(root_run_id)
                REFERENCES execution_runs(run_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_execution_task_work_session
            ON execution_task_work_contexts(session_id,created_at,root_run_id);

        CREATE TABLE IF NOT EXISTS execution_conversation_boundaries (
            boundary_ref TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            session_id TEXT NOT NULL,
            root_run_id TEXT NOT NULL UNIQUE,
            task_scope_id TEXT NOT NULL UNIQUE,
            seed_message_refs_json TEXT NOT NULL
                CHECK(json_valid(seed_message_refs_json)),
            continuation_message_refs_json TEXT NOT NULL
                CHECK(json_valid(continuation_message_refs_json)),
            boundary_version INTEGER NOT NULL CHECK(boundary_version>0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(root_run_id)
                REFERENCES execution_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(task_scope_id)
                REFERENCES execution_task_work_contexts(task_scope_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_execution_conversation_session
            ON execution_conversation_boundaries(
                session_id,updated_at,root_run_id
            );

        CREATE TABLE IF NOT EXISTS execution_task_run_projections (
            projection_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            session_id TEXT NOT NULL,
            root_run_id TEXT NOT NULL UNIQUE,
            task_scope_id TEXT NOT NULL UNIQUE,
            ui_state TEXT NOT NULL CHECK(ui_state IN (
                'open','background','closed'
            )),
            projection_version INTEGER NOT NULL CHECK(projection_version>=0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(root_run_id)
                REFERENCES execution_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(task_scope_id)
                REFERENCES execution_task_work_contexts(task_scope_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_execution_task_projection_session
            ON execution_task_run_projections(
                session_id,ui_state,updated_at,root_run_id
            );

        CREATE TRIGGER IF NOT EXISTS execution_task_work_identity_immutable
        BEFORE UPDATE OF
            root_run_id,session_id,task_scope_id,workspace_source
        ON execution_task_work_contexts
        BEGIN SELECT RAISE(
            ABORT,'execution_task_work_identity_immutable'
        ); END;

        CREATE TRIGGER IF NOT EXISTS execution_conversation_identity_immutable
        BEFORE UPDATE OF
            boundary_ref,session_id,root_run_id,task_scope_id,
            seed_message_refs_json
        ON execution_conversation_boundaries
        BEGIN SELECT RAISE(
            ABORT,'execution_conversation_identity_immutable'
        ); END;

        CREATE TRIGGER IF NOT EXISTS execution_task_projection_identity_immutable
        BEFORE UPDATE OF
            projection_id,session_id,root_run_id,task_scope_id
        ON execution_task_run_projections
        BEGIN SELECT RAISE(
            ABORT,'execution_task_projection_identity_immutable'
        ); END;

        INSERT OR IGNORE INTO workflow_schema_migrations(version,applied_at)
        VALUES(15,CAST(strftime('%s','now') AS REAL));
        PRAGMA user_version=15;
        COMMIT;
        """
    )


async def _migrate_v15_to_v16(db: aiosqlite.Connection) -> None:
    """Queue user steering for a running root without creating another Run."""

    await db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS execution_user_continuations (
            root_run_id TEXT NOT NULL,
            message_ref TEXT NOT NULL,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            task_scope_id TEXT NOT NULL,
            content TEXT NOT NULL CHECK(length(trim(content))>0),
            reserved_boundary_version INTEGER NOT NULL
                CHECK(reserved_boundary_version>1),
            status TEXT NOT NULL CHECK(status IN ('pending','bound','failed')),
            created_at REAL NOT NULL,
            settled_at REAL,
            error TEXT,
            PRIMARY KEY(root_run_id,message_ref),
            UNIQUE(root_run_id,reserved_boundary_version),
            CHECK(
                (status='pending' AND settled_at IS NULL AND error IS NULL)
                OR (status='bound' AND settled_at IS NOT NULL AND error IS NULL)
                OR (status='failed' AND settled_at IS NOT NULL AND error IS NOT NULL)
            ),
            FOREIGN KEY(root_run_id)
                REFERENCES execution_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(task_scope_id)
                REFERENCES execution_task_work_contexts(task_scope_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_execution_user_continuation_pending
            ON execution_user_continuations(
                root_run_id,status,reserved_boundary_version
            );

        CREATE TRIGGER IF NOT EXISTS execution_user_continuation_identity_immutable
        BEFORE UPDATE OF
            root_run_id,message_ref,task_scope_id,content,
            reserved_boundary_version,created_at
        ON execution_user_continuations
        BEGIN SELECT RAISE(
            ABORT,'execution_user_continuation_identity_immutable'
        ); END;

        INSERT OR IGNORE INTO workflow_schema_migrations(version,applied_at)
        VALUES(16,CAST(strftime('%s','now') AS REAL));
        PRAGMA user_version=16;
        COMMIT;
        """
    )


async def _migrate_v16_to_v17_execution_run_snapshots(
    db: aiosqlite.Connection,
) -> None:
    """Add physical provider dispatch facts and conservative effect handoff v2."""

    effect_count = int(
        (
            await (
                await db.execute("SELECT COUNT(*) FROM execution_effects")
            ).fetchone()
        )[0]
    )
    attempt_count = int(
        (
            await (
                await db.execute("SELECT COUNT(*) FROM execution_effect_attempts")
            ).fetchone()
        )[0]
    )
    mismatch = await (
        await db.execute(
            """SELECT e.effect_id,e.status,a.status
            FROM execution_effects AS e
            JOIN execution_effect_attempts AS a ON a.effect_id=e.effect_id
            WHERE e.status<>a.status
            LIMIT 1"""
        )
    ).fetchone()
    if mismatch is not None:
        raise RuntimeError(
            "workflow.db v16 effect/attempt status mismatch: "
            f"{mismatch[0]} effect={mismatch[1]} attempt={mismatch[2]}"
        )
    prepared_with_attempt = await (
        await db.execute(
            """SELECT e.effect_id FROM execution_effects AS e
            JOIN execution_effect_attempts AS a ON a.effect_id=e.effect_id
            WHERE e.status='prepared' LIMIT 1"""
        )
    ).fetchone()
    if prepared_with_attempt is not None:
        raise RuntimeError(
            "workflow.db v16 prepared effect unexpectedly has an attempt: "
            f"{prepared_with_attempt[0]}"
        )

    await db.commit()
    await db.execute("PRAGMA foreign_keys=OFF")
    await db.execute("PRAGMA legacy_alter_table=ON")
    try:
        await db.create_function(
            "deskpet_sha256",
            1,
            lambda value: __import__("hashlib").sha256(
                str(value).encode("utf-8")
            ).hexdigest(),
            deterministic=True,
        )
        await db.executescript(
            """
            BEGIN IMMEDIATE;

            CREATE TABLE execution_run_start_snapshots (
                run_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL CHECK(schema_version=1),
                snapshot_schema_version INTEGER NOT NULL
                    CHECK(snapshot_schema_version>=1),
                start_fingerprint TEXT NOT NULL CHECK(
                    length(start_fingerprint)=64
                    AND start_fingerprint NOT GLOB '*[^0-9a-f]*'
                ),
                canonical_messages_json TEXT NOT NULL,
                session_cursor_json TEXT NOT NULL,
                prepared_refs_json TEXT NOT NULL,
                sanitized_request_json TEXT NOT NULL,
                run_context_json TEXT NOT NULL,
                run_spec_json TEXT NOT NULL,
                capability_snapshot_json TEXT NOT NULL,
                capability_snapshot_hash TEXT NOT NULL CHECK(
                    length(capability_snapshot_hash)=64
                    AND capability_snapshot_hash NOT GLOB '*[^0-9a-f]*'
                ),
                provider_launch_policy_json TEXT NOT NULL,
                terminal_deliveries_json TEXT NOT NULL,
                terminal_deliveries_hash TEXT NOT NULL CHECK(
                    length(terminal_deliveries_hash)=64
                    AND terminal_deliveries_hash NOT GLOB '*[^0-9a-f]*'
                ),
                capability_lease_intent_ref TEXT,
                capability_lease_intent_hash TEXT CHECK(
                    capability_lease_intent_hash IS NULL OR (
                        length(capability_lease_intent_hash)=64
                        AND capability_lease_intent_hash
                            NOT GLOB '*[^0-9a-f]*'
                    )
                ),
                start_extension_receipts_json TEXT NOT NULL DEFAULT '[]',
                start_extension_receipts_hash TEXT NOT NULL DEFAULT
                    '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'
                    CHECK(
                        length(start_extension_receipts_hash)=64
                        AND start_extension_receipts_hash
                            NOT GLOB '*[^0-9a-f]*'
                    ),
                created_at REAL NOT NULL,
                CHECK(
                    (capability_lease_intent_ref IS NULL
                        AND capability_lease_intent_hash IS NULL)
                    OR
                    (capability_lease_intent_ref IS NOT NULL
                        AND capability_lease_intent_hash IS NOT NULL)
                ),
                FOREIGN KEY(run_id)
                    REFERENCES execution_runs(run_id) ON DELETE CASCADE
            );
            CREATE TRIGGER execution_run_start_snapshot_immutable_update
            BEFORE UPDATE ON execution_run_start_snapshots
            BEGIN SELECT RAISE(
                ABORT,'execution_run_start_snapshot_immutable'
            ); END;
            CREATE TRIGGER execution_run_start_snapshot_immutable_delete
            BEFORE DELETE ON execution_run_start_snapshots
            BEGIN SELECT RAISE(
                ABORT,'execution_run_start_snapshot_immutable'
            ); END;

            CREATE TABLE execution_provider_invocations (
                run_id TEXT NOT NULL,
                invocation_id TEXT NOT NULL UNIQUE,
                schema_version INTEGER NOT NULL CHECK(schema_version=1),
                provider_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                adapter_id TEXT NOT NULL,
                policy_snapshot_json TEXT NOT NULL,
                request_hash TEXT NOT NULL CHECK(
                    length(request_hash)=64
                    AND request_hash NOT GLOB '*[^0-9a-f]*'
                ),
                idempotency_group_id TEXT NOT NULL,
                attempt_ordinal INTEGER NOT NULL CHECK(attempt_ordinal>=0),
                status TEXT NOT NULL CHECK(status IN (
                    'claimed','completed','failed','unknown'
                )),
                claim_owner TEXT NOT NULL,
                claim_epoch INTEGER NOT NULL CHECK(claim_epoch>=1),
                claimed_at REAL NOT NULL,
                revocation_epoch INTEGER NOT NULL CHECK(revocation_epoch>=0),
                dispatch_started_ack_ref TEXT,
                dispatch_started_ack_hash TEXT CHECK(
                    dispatch_started_ack_hash IS NULL OR (
                        length(dispatch_started_ack_hash)=64
                        AND dispatch_started_ack_hash
                            NOT GLOB '*[^0-9a-f]*'
                    )
                ),
                dispatch_started_at REAL,
                stream_epoch INTEGER NOT NULL CHECK(stream_epoch>=0),
                outcome_ref TEXT,
                outcome_hash TEXT CHECK(
                    outcome_hash IS NULL OR (
                        length(outcome_hash)=64
                        AND outcome_hash NOT GLOB '*[^0-9a-f]*'
                    )
                ),
                updated_at REAL NOT NULL,
                PRIMARY KEY(run_id,invocation_id),
                CHECK(
                    (dispatch_started_ack_ref IS NULL
                        AND dispatch_started_ack_hash IS NULL
                        AND dispatch_started_at IS NULL)
                    OR
                    (dispatch_started_ack_ref IS NOT NULL
                        AND dispatch_started_ack_hash IS NOT NULL
                        AND dispatch_started_at IS NOT NULL)
                ),
                CHECK(
                    (status='completed'
                        AND outcome_ref IS NOT NULL
                        AND outcome_hash IS NOT NULL)
                    OR
                    (status<>'completed')
                ),
                CHECK(
                    (outcome_ref IS NULL AND outcome_hash IS NULL)
                    OR
                    (outcome_ref IS NOT NULL AND outcome_hash IS NOT NULL)
                ),
                FOREIGN KEY(run_id)
                    REFERENCES execution_runs(run_id) ON DELETE CASCADE
            );
            CREATE INDEX idx_execution_provider_invocations_status
                ON execution_provider_invocations(
                    run_id,status,attempt_ordinal,invocation_id
                );
            CREATE TRIGGER execution_provider_invocation_identity_immutable
            BEFORE UPDATE OF
                run_id,invocation_id,schema_version,provider_id,model_id,
                adapter_id,policy_snapshot_json,request_hash,
                idempotency_group_id,attempt_ordinal,claim_owner,claim_epoch,
                claimed_at,revocation_epoch,stream_epoch
            ON execution_provider_invocations
            BEGIN SELECT RAISE(
                ABORT,'execution_provider_invocation_identity_immutable'
            ); END;
            CREATE TRIGGER execution_provider_invocation_monotonic
            BEFORE UPDATE OF status ON execution_provider_invocations
            WHEN NOT (
                OLD.status='claimed'
                AND NEW.status IN ('completed','failed','unknown')
            )
            BEGIN SELECT RAISE(
                ABORT,'execution_provider_invocation_not_monotonic'
            ); END;

            CREATE TABLE execution_provider_invocation_outcomes (
                run_id TEXT NOT NULL,
                invocation_id TEXT NOT NULL,
                schema_version INTEGER NOT NULL CHECK(schema_version=1),
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL CHECK(
                    length(payload_hash)=64
                    AND payload_hash NOT GLOB '*[^0-9a-f]*'
                ),
                created_at REAL NOT NULL,
                PRIMARY KEY(run_id,invocation_id),
                FOREIGN KEY(run_id,invocation_id)
                    REFERENCES execution_provider_invocations(
                        run_id,invocation_id
                    ) ON DELETE CASCADE
            );
            CREATE TRIGGER execution_provider_invocation_outcome_immutable_update
            BEFORE UPDATE ON execution_provider_invocation_outcomes
            BEGIN SELECT RAISE(
                ABORT,'execution_provider_invocation_outcome_immutable'
            ); END;
            CREATE TRIGGER execution_provider_invocation_outcome_immutable_delete
            BEFORE DELETE ON execution_provider_invocation_outcomes
            BEGIN SELECT RAISE(
                ABORT,'execution_provider_invocation_outcome_immutable'
            ); END;

            CREATE TABLE execution_run_fences (
                run_id TEXT NOT NULL,
                fence_kind TEXT NOT NULL,
                fence_version INTEGER NOT NULL CHECK(fence_version>=0),
                schema_version INTEGER NOT NULL CHECK(schema_version=1),
                status TEXT NOT NULL CHECK(status IN (
                    'active','revoked','cancelled'
                )),
                reason TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(run_id,fence_kind,fence_version),
                FOREIGN KEY(run_id)
                    REFERENCES execution_runs(run_id) ON DELETE CASCADE
            );
            CREATE INDEX idx_execution_run_fences_latest
                ON execution_run_fences(
                    run_id,fence_kind,fence_version DESC
                );
            CREATE TRIGGER execution_run_fence_identity_immutable
            BEFORE UPDATE OF
                run_id,fence_kind,fence_version,schema_version,reason,
                source_ref,created_at
            ON execution_run_fences
            BEGIN SELECT RAISE(
                ABORT,'execution_run_fence_identity_immutable'
            ); END;
            CREATE TRIGGER execution_run_fence_monotonic
            BEFORE UPDATE OF status ON execution_run_fences
            WHEN NOT (
                OLD.status='active'
                AND NEW.status IN ('revoked','cancelled')
            )
            BEGIN SELECT RAISE(
                ABORT,'execution_run_fence_not_monotonic'
            ); END;

            ALTER TABLE execution_deliveries
                ADD COLUMN delivery_fence_epoch INTEGER;
            ALTER TABLE execution_deliveries
                ADD COLUMN delivery_owner_id TEXT;
            ALTER TABLE execution_deliveries
                ADD COLUMN delivery_owner_generation INTEGER;
            ALTER TABLE execution_deliveries
                ADD COLUMN delivery_dependency_hash TEXT;
            ALTER TABLE execution_deliveries
                ADD COLUMN delivery_snapshot_ref TEXT;
            ALTER TABLE execution_deliveries
                ADD COLUMN delivery_snapshot_hash TEXT;
            ALTER TABLE execution_deliveries
                ADD COLUMN release_receipt_ref TEXT;
            ALTER TABLE execution_deliveries
                ADD COLUMN release_receipt_hash TEXT;

            CREATE TABLE execution_terminal_extension_receipts (
                run_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                receipt_order INTEGER NOT NULL CHECK(receipt_order>=0),
                kind TEXT NOT NULL,
                ref TEXT NOT NULL,
                content_hash TEXT NOT NULL CHECK(
                    length(content_hash)=64
                    AND content_hash NOT GLOB '*[^0-9a-f]*'
                ),
                created_at REAL NOT NULL,
                PRIMARY KEY(run_id,event_id,receipt_order),
                UNIQUE(run_id,event_id,kind,ref),
                FOREIGN KEY(run_id)
                    REFERENCES execution_runs(run_id) ON DELETE CASCADE,
                FOREIGN KEY(event_id)
                    REFERENCES execution_events(event_id) ON DELETE CASCADE
            );
            CREATE TRIGGER execution_terminal_receipt_immutable_update
            BEFORE UPDATE ON execution_terminal_extension_receipts
            BEGIN SELECT RAISE(
                ABORT,'execution_terminal_receipt_immutable'
            ); END;
            CREATE TRIGGER execution_terminal_receipt_immutable_delete
            BEFORE DELETE ON execution_terminal_extension_receipts
            BEGIN SELECT RAISE(
                ABORT,'execution_terminal_receipt_immutable'
            ); END;

            CREATE TABLE execution_effects_v17 (
                effect_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL CHECK(schema_version=1),
                run_id TEXT NOT NULL,
                effect_fingerprint TEXT NOT NULL,
                call_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                args_hash TEXT NOT NULL,
                capability_hash TEXT NOT NULL,
                scope_hash TEXT,
                effect_type TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'prepared','running','succeeded','failed','accepted',
                    'unknown','cancelled','late_reconciled'
                )),
                handoff_state TEXT NOT NULL CHECK(handoff_state IN (
                    'unresolved','not_started','started',
                    'started_may_complete','reconciled'
                )),
                completion_disposition TEXT NOT NULL CHECK(
                    completion_disposition IN (
                        'normal','confirmed_not_started',
                        'inflight_effect_may_complete',
                        'reconciled_not_completed',
                        'reconciled_completed_suppressed'
                    )
                ),
                handoff_ack_ref TEXT,
                handoff_ack_hash TEXT,
                handoff_ack_at REAL,
                handoff_unknown_receipt_ref TEXT,
                handoff_unknown_receipt_hash TEXT,
                handoff_unknown_at REAL,
                cancel_receipt_ref TEXT,
                cancel_receipt_hash TEXT,
                reconcile_receipt_ref TEXT,
                reconcile_receipt_hash TEXT,
                late_outcome_hash TEXT,
                policy_json TEXT NOT NULL,
                prepared_json TEXT NOT NULL,
                outcome_json TEXT,
                receipt_ref TEXT,
                artifact_refs_json TEXT NOT NULL DEFAULT '[]',
                effect_version INTEGER NOT NULL DEFAULT 0
                    CHECK(effect_version>=0),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                ended_at REAL,
                UNIQUE(run_id,effect_fingerprint),
                FOREIGN KEY(run_id)
                    REFERENCES execution_runs(run_id) ON DELETE CASCADE,
                CHECK(
                    (status='prepared' AND handoff_state='unresolved'
                        AND completion_disposition='normal')
                    OR
                    (status='running'
                        AND handoff_state IN ('unresolved','started')
                        AND completion_disposition='normal')
                    OR
                    (status='cancelled' AND (
                        (handoff_state='not_started'
                            AND completion_disposition='confirmed_not_started')
                        OR
                        (handoff_state='reconciled'
                            AND completion_disposition='normal')
                    ))
                    OR
                    (status='unknown'
                        AND handoff_state='started_may_complete'
                        AND completion_disposition=
                            'inflight_effect_may_complete')
                    OR
                    (status IN ('succeeded','failed','accepted')
                        AND handoff_state='reconciled'
                        AND completion_disposition='normal')
                    OR
                    (status='late_reconciled'
                        AND handoff_state='reconciled'
                        AND completion_disposition IN (
                            'reconciled_not_completed',
                            'reconciled_completed_suppressed'
                        ))
                ),
                CHECK(
                    (handoff_ack_ref IS NULL AND handoff_ack_hash IS NULL
                        AND handoff_ack_at IS NULL)
                    OR
                    (handoff_ack_ref IS NOT NULL
                        AND length(handoff_ack_hash)=64
                        AND handoff_ack_at IS NOT NULL)
                ),
                CHECK(
                    (handoff_unknown_receipt_ref IS NULL
                        AND handoff_unknown_receipt_hash IS NULL
                        AND handoff_unknown_at IS NULL)
                    OR
                    (handoff_unknown_receipt_ref IS NOT NULL
                        AND length(handoff_unknown_receipt_hash)=64
                        AND handoff_unknown_at IS NOT NULL)
                ),
                CHECK(
                    (cancel_receipt_ref IS NULL
                        AND cancel_receipt_hash IS NULL)
                    OR
                    (cancel_receipt_ref IS NOT NULL
                        AND length(cancel_receipt_hash)=64)
                ),
                CHECK(
                    (reconcile_receipt_ref IS NULL
                        AND reconcile_receipt_hash IS NULL)
                    OR
                    (reconcile_receipt_ref IS NOT NULL
                        AND length(reconcile_receipt_hash)=64)
                )
            );

            CREATE TABLE execution_effect_attempts_v17 (
                effect_id TEXT NOT NULL,
                attempt_no INTEGER NOT NULL CHECK(attempt_no>=1),
                status TEXT NOT NULL CHECK(status IN (
                    'running','succeeded','failed','accepted','unknown',
                    'cancelled','late_reconciled'
                )),
                handoff_state TEXT NOT NULL CHECK(handoff_state IN (
                    'unresolved','not_started','started',
                    'started_may_complete','reconciled'
                )),
                completion_disposition TEXT NOT NULL CHECK(
                    completion_disposition IN (
                        'normal','confirmed_not_started',
                        'inflight_effect_may_complete',
                        'reconciled_not_completed',
                        'reconciled_completed_suppressed'
                    )
                ),
                handoff_ack_ref TEXT,
                handoff_ack_hash TEXT,
                handoff_ack_at REAL,
                handoff_unknown_receipt_ref TEXT,
                handoff_unknown_receipt_hash TEXT,
                handoff_unknown_at REAL,
                cancel_receipt_ref TEXT,
                cancel_receipt_hash TEXT,
                reconcile_receipt_ref TEXT,
                reconcile_receipt_hash TEXT,
                late_outcome_hash TEXT,
                worker_owner TEXT,
                worker_epoch INTEGER NOT NULL DEFAULT 0 CHECK(worker_epoch>=0),
                started_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                ended_at REAL,
                error_json TEXT,
                outcome_json TEXT,
                PRIMARY KEY(effect_id,attempt_no),
                FOREIGN KEY(effect_id)
                    REFERENCES execution_effects(effect_id) ON DELETE CASCADE,
                CHECK(
                    (status='running'
                        AND handoff_state IN ('unresolved','started')
                        AND completion_disposition='normal')
                    OR
                    (status='cancelled' AND (
                        (handoff_state='not_started'
                            AND completion_disposition='confirmed_not_started')
                        OR
                        (handoff_state='reconciled'
                            AND completion_disposition='normal')
                    ))
                    OR
                    (status='unknown'
                        AND handoff_state='started_may_complete'
                        AND completion_disposition=
                            'inflight_effect_may_complete')
                    OR
                    (status IN ('succeeded','failed','accepted')
                        AND handoff_state='reconciled'
                        AND completion_disposition='normal')
                    OR
                    (status='late_reconciled'
                        AND handoff_state='reconciled'
                        AND completion_disposition IN (
                            'reconciled_not_completed',
                            'reconciled_completed_suppressed'
                        ))
                )
            );

            INSERT INTO execution_effects_v17(
                effect_id,schema_version,run_id,effect_fingerprint,call_id,
                tool_name,args_hash,capability_hash,scope_hash,effect_type,
                status,handoff_state,completion_disposition,
                handoff_ack_ref,handoff_ack_hash,handoff_ack_at,
                handoff_unknown_receipt_ref,
                handoff_unknown_receipt_hash,handoff_unknown_at,
                policy_json,prepared_json,outcome_json,receipt_ref,
                artifact_refs_json,effect_version,created_at,updated_at,ended_at
            )
            SELECT
                effect_id,schema_version,run_id,effect_fingerprint,call_id,
                tool_name,args_hash,capability_hash,scope_hash,effect_type,
                CASE
                    WHEN status='prepared' THEN 'cancelled'
                    WHEN status IN ('running','unknown') THEN 'unknown'
                    ELSE status
                END,
                CASE
                    WHEN status='prepared' THEN 'not_started'
                    WHEN status IN ('running','unknown')
                        THEN 'started_may_complete'
                    ELSE 'reconciled'
                END,
                CASE
                    WHEN status='prepared' THEN 'confirmed_not_started'
                    WHEN status IN ('running','unknown')
                        THEN 'inflight_effect_may_complete'
                    WHEN status='late_reconciled'
                        THEN 'reconciled_not_completed'
                    ELSE 'normal'
                END,
                CASE WHEN status='prepared'
                    THEN 'migration:v17:confirmed-not-started:'||effect_id END,
                CASE WHEN status='prepared'
                    THEN deskpet_sha256(
                        'migration:v17:confirmed-not-started:'||effect_id
                    ) END,
                CASE WHEN status='prepared' THEN updated_at END,
                CASE WHEN status IN ('running','unknown')
                    THEN 'migration:v17:started-may-complete:'||effect_id END,
                CASE WHEN status IN ('running','unknown')
                    THEN deskpet_sha256(
                        'migration:v17:started-may-complete:'||effect_id
                    ) END,
                CASE WHEN status IN ('running','unknown') THEN updated_at END,
                policy_json,prepared_json,outcome_json,receipt_ref,
                artifact_refs_json,effect_version,created_at,updated_at,
                CASE
                    WHEN status IN ('prepared','running','unknown')
                        THEN COALESCE(ended_at,updated_at)
                    ELSE ended_at
                END
            FROM execution_effects;

            INSERT INTO execution_effect_attempts_v17(
                effect_id,attempt_no,status,handoff_state,
                completion_disposition,handoff_ack_ref,handoff_ack_hash,
                handoff_ack_at,handoff_unknown_receipt_ref,
                handoff_unknown_receipt_hash,handoff_unknown_at,
                worker_owner,worker_epoch,started_at,updated_at,ended_at,
                error_json,outcome_json
            )
            SELECT
                a.effect_id,a.attempt_no,
                CASE WHEN e.status IN ('running','unknown')
                    THEN 'unknown' ELSE e.status END,
                CASE WHEN e.status IN ('running','unknown')
                    THEN 'started_may_complete' ELSE 'reconciled' END,
                CASE
                    WHEN e.status IN ('running','unknown')
                        THEN 'inflight_effect_may_complete'
                    WHEN e.status='late_reconciled'
                        THEN 'reconciled_not_completed'
                    ELSE 'normal'
                END,
                NULL,NULL,NULL,
                CASE WHEN e.status IN ('running','unknown')
                    THEN 'migration:v17:started-may-complete:'||e.effect_id END,
                CASE WHEN e.status IN ('running','unknown')
                    THEN deskpet_sha256(
                        'migration:v17:started-may-complete:'||e.effect_id
                    ) END,
                CASE WHEN e.status IN ('running','unknown')
                    THEN a.updated_at END,
                a.worker_owner,a.worker_epoch,a.started_at,a.updated_at,
                CASE WHEN e.status IN ('running','unknown')
                    THEN COALESCE(a.ended_at,a.updated_at)
                    ELSE a.ended_at
                END,
                a.error_json,a.outcome_json
            FROM execution_effect_attempts AS a
            JOIN execution_effects AS e ON e.effect_id=a.effect_id;

            INSERT INTO execution_effect_attempts_v17(
                effect_id,attempt_no,status,handoff_state,
                completion_disposition,handoff_ack_ref,handoff_ack_hash,
                handoff_ack_at,worker_owner,worker_epoch,started_at,updated_at,
                ended_at,error_json,outcome_json
            )
            SELECT
                effect_id,1,'cancelled','not_started',
                'confirmed_not_started',
                'migration:v17:confirmed-not-started:'||effect_id,
                deskpet_sha256(
                    'migration:v17:confirmed-not-started:'||effect_id
                ),
                updated_at,'migration:v17',0,created_at,updated_at,
                updated_at,NULL,NULL
            FROM execution_effects
            WHERE status='prepared';

            ALTER TABLE execution_effects RENAME TO execution_effects_v16;
            ALTER TABLE execution_effect_attempts
                RENAME TO execution_effect_attempts_v16;
            ALTER TABLE execution_effects_v17 RENAME TO execution_effects;
            ALTER TABLE execution_effect_attempts_v17
                RENAME TO execution_effect_attempts;
            DROP TABLE execution_effect_attempts_v16;
            DROP TABLE execution_effects_v16;

            CREATE INDEX idx_execution_effects_run_status
                ON execution_effects(run_id,status,updated_at);
            CREATE INDEX idx_execution_effect_attempts_status
                ON execution_effect_attempts(status,updated_at);
            CREATE TRIGGER execution_effect_attempt_consistency_insert
            BEFORE INSERT ON execution_effect_attempts
            WHEN NOT EXISTS (
                SELECT 1 FROM execution_effects AS e
                WHERE e.effect_id=NEW.effect_id
                  AND e.status=NEW.status
                  AND e.handoff_state=NEW.handoff_state
                  AND e.completion_disposition=NEW.completion_disposition
            )
            BEGIN SELECT RAISE(
                ABORT,'execution_effect_attempt_inconsistent'
            ); END;

            ALTER TABLE execution_provider_turn_fences
                ADD COLUMN source_invocation_id TEXT
                REFERENCES execution_provider_invocations(invocation_id);
            CREATE TRIGGER execution_provider_turn_source_immutable
            BEFORE UPDATE OF source_invocation_id
            ON execution_provider_turn_fences
            BEGIN SELECT RAISE(
                ABORT,'execution_provider_turn_source_immutable'
            ); END;
            CREATE TRIGGER execution_provider_turn_completed_source_insert
            BEFORE INSERT ON execution_provider_turn_fences
            WHEN NEW.source_invocation_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM execution_provider_invocations AS invocation
                  WHERE invocation.invocation_id=NEW.source_invocation_id
                    AND invocation.status='completed'
              )
            BEGIN SELECT RAISE(
                ABORT,'execution_provider_turn_source_not_completed'
            ); END;

            INSERT OR IGNORE INTO workflow_schema_migrations(version,applied_at)
            VALUES(17,CAST(strftime('%s','now') AS REAL));
            PRAGMA user_version=17;
            COMMIT;
            """
        )
        new_effect_count = int(
            (
                await (
                    await db.execute("SELECT COUNT(*) FROM execution_effects")
                ).fetchone()
            )[0]
        )
        new_attempt_count = int(
            (
                await (
                    await db.execute(
                        "SELECT COUNT(*) FROM execution_effect_attempts"
                    )
                ).fetchone()
            )[0]
        )
        prepared_count = int(
            (
                await (
                    await db.execute(
                        """SELECT COUNT(*) FROM execution_effects
                        WHERE handoff_ack_ref LIKE
                            'migration:v17:confirmed-not-started:%'"""
                    )
                ).fetchone()
            )[0]
        )
        if new_effect_count != effect_count:
            raise RuntimeError("workflow.db v17 effect copy verification failed")
        if new_attempt_count != attempt_count + prepared_count:
            raise RuntimeError("workflow.db v17 attempt copy verification failed")
    finally:
        await db.execute("PRAGMA legacy_alter_table=OFF")
        await db.execute("PRAGMA foreign_keys=ON")
    foreign_key_errors = await (
        await db.execute("PRAGMA foreign_key_check")
    ).fetchall()
    if foreign_key_errors:
        raise RuntimeError(
            f"workflow.db v17 foreign key verification failed: {foreign_key_errors[0]}"
        )


async def _migrate_v17_to_v18_capability_owner_scope(
    db: aiosqlite.Connection,
) -> None:
    """Install capability owner authority and run-catalog durability."""

    # Import at migration time to preserve the package initialization boundary
    # documented by the original capability repository migration.
    from deskpet.capabilities.store import migrate_capability_schema_v1_to_v2

    await db.execute("BEGIN IMMEDIATE")
    try:
        await migrate_capability_schema_v1_to_v2(db)
        foreign_key_errors = await (
            await db.execute("PRAGMA foreign_key_check")
        ).fetchall()
        if foreign_key_errors:
            raise RuntimeError(
                "workflow.db v18 capability foreign key verification failed: "
                f"{foreign_key_errors[0]}"
            )
        await db.execute(
            """INSERT OR IGNORE INTO workflow_schema_migrations(
                   version,applied_at
               ) VALUES(18,?)""",
            (time.time(),),
        )
        await db.execute("PRAGMA user_version=18")
        await db.commit()
    except BaseException:
        if db.in_transaction:
            await db.rollback()
        raise


async def _migrate_v18_to_v19_candidate_draft_receipts(
    db: aiosqlite.Connection,
) -> None:
    """Add immutable host-issued receipts for governed candidate drafts."""

    await db.execute("BEGIN IMMEDIATE")
    try:
        await db.execute(
            """
            CREATE TABLE execution_candidate_draft_receipts (
                receipt_id TEXT PRIMARY KEY,
                builder_launch_id TEXT NOT NULL UNIQUE,
                child_run_id TEXT NOT NULL,
                child_start_hash TEXT NOT NULL CHECK(
                    length(child_start_hash)=64
                    AND child_start_hash NOT GLOB '*[^0-9a-f]*'
                ),
                proposal_ref TEXT NOT NULL,
                proposal_hash TEXT NOT NULL CHECK(
                    length(proposal_hash)=64
                    AND proposal_hash NOT GLOB '*[^0-9a-f]*'
                ),
                evidence_set_hash TEXT NOT NULL CHECK(
                    length(evidence_set_hash)=64
                    AND evidence_set_hash NOT GLOB '*[^0-9a-f]*'
                ),
                target_fence_hash TEXT NOT NULL CHECK(
                    length(target_fence_hash)=64
                    AND target_fence_hash NOT GLOB '*[^0-9a-f]*'
                ),
                validated_draft_hash TEXT NOT NULL CHECK(
                    length(validated_draft_hash)=64
                    AND validated_draft_hash NOT GLOB '*[^0-9a-f]*'
                ),
                manifest_hash TEXT NOT NULL CHECK(
                    length(manifest_hash)=64
                    AND manifest_hash NOT GLOB '*[^0-9a-f]*'
                ),
                archive_hash TEXT NOT NULL CHECK(
                    length(archive_hash)=64
                    AND archive_hash NOT GLOB '*[^0-9a-f]*'
                ),
                file_set_hash TEXT NOT NULL CHECK(
                    length(file_set_hash)=64
                    AND file_set_hash NOT GLOB '*[^0-9a-f]*'
                ),
                effect_topology_hash TEXT NOT NULL CHECK(
                    length(effect_topology_hash)=64
                    AND effect_topology_hash NOT GLOB '*[^0-9a-f]*'
                ),
                receipt_hash TEXT NOT NULL CHECK(
                    length(receipt_hash)=64
                    AND receipt_hash NOT GLOB '*[^0-9a-f]*'
                ),
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TRIGGER
                execution_candidate_draft_receipts_immutable_update
            BEFORE UPDATE ON execution_candidate_draft_receipts
            BEGIN
                SELECT RAISE(
                    ABORT,'execution_candidate_draft_receipt_immutable'
                );
            END
            """
        )
        await db.execute(
            """
            CREATE TRIGGER
                execution_candidate_draft_receipts_immutable_delete
            BEFORE DELETE ON execution_candidate_draft_receipts
            BEGIN
                SELECT RAISE(
                    ABORT,'execution_candidate_draft_receipt_immutable'
                );
            END
            """
        )
        await db.execute(
            """INSERT INTO workflow_schema_migrations(version,applied_at)
               VALUES(19,?)""",
            (time.time(),),
        )
        await db.execute("PRAGMA user_version=19")
        await db.commit()
    except BaseException:
        if db.in_transaction:
            await db.rollback()
        raise


async def _migrate_v19_to_v20_candidate_draft_materials(
    db: aiosqlite.Connection,
) -> None:
    """Freeze exact candidate archive bytes next to the immutable receipt."""

    await db.execute("BEGIN IMMEDIATE")
    try:
        await db.execute(
            """
            CREATE TABLE execution_candidate_draft_materials (
                receipt_id TEXT PRIMARY KEY,
                archive_blob BLOB NOT NULL,
                validated_draft_hash TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,
                archive_hash TEXT NOT NULL,
                file_set_hash TEXT NOT NULL,
                effect_topology_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(receipt_id)
                    REFERENCES execution_candidate_draft_receipts(receipt_id)
                    ON DELETE RESTRICT
            )
            """
        )
        await db.execute(
            """
            CREATE TRIGGER
                execution_candidate_draft_materials_immutable_update
            BEFORE UPDATE ON execution_candidate_draft_materials
            BEGIN
                SELECT RAISE(
                    ABORT,'execution_candidate_draft_material_immutable'
                );
            END
            """
        )
        await db.execute(
            """
            CREATE TRIGGER
                execution_candidate_draft_materials_immutable_delete
            BEFORE DELETE ON execution_candidate_draft_materials
            BEGIN
                SELECT RAISE(
                    ABORT,'execution_candidate_draft_material_immutable'
                );
            END
            """
        )
        await db.execute(
            """INSERT INTO workflow_schema_migrations(version,applied_at)
               VALUES(20,?)""",
            (time.time(),),
        )
        await db.execute("PRAGMA user_version=20")
        await db.commit()
    except BaseException:
        if db.in_transaction:
            await db.rollback()
        raise


async def _migrate_v20_to_v21_personal_workflow_tickets(
    db: aiosqlite.Connection,
) -> None:
    """Bind one Personal Workflow selection to at most one child launch."""

    await db.execute("BEGIN IMMEDIATE")
    try:
        await _add_column_if_missing(
            db,
            "execution_profile_launch_tickets",
            "personal_selection_id",
            "TEXT",
        )
        await _add_column_if_missing(
            db,
            "execution_profile_launch_tickets",
            "personal_selection_fingerprint",
            "TEXT",
        )
        await db.execute(
            """
            CREATE UNIQUE INDEX
                idx_execution_profile_ticket_personal_selection_once
            ON execution_profile_launch_tickets(
                parent_run_id,personal_selection_id
            )
            WHERE profile_key='workflow.personal_v1'
              AND personal_selection_id IS NOT NULL
            """
        )
        await db.execute(
            """
            CREATE TRIGGER
                execution_profile_ticket_personal_identity_immutable
            BEFORE UPDATE OF
                personal_selection_id,personal_selection_fingerprint
            ON execution_profile_launch_tickets
            BEGIN
                SELECT RAISE(
                    ABORT,'execution_profile_ticket_identity_immutable'
                );
            END
            """
        )
        await db.execute(
            """
            CREATE TRIGGER
                execution_profile_ticket_personal_fields_consistent_insert
            BEFORE INSERT ON execution_profile_launch_tickets
            WHEN NOT (
                (
                    NEW.profile_key='workflow.personal_v1'
                    AND NEW.personal_selection_id IS NOT NULL
                    AND length(NEW.personal_selection_id)>0
                    AND NEW.personal_selection_fingerprint IS NOT NULL
                    AND length(NEW.personal_selection_fingerprint)=64
                    AND NEW.personal_selection_fingerprint
                        NOT GLOB '*[^0-9a-f]*'
                )
                OR
                (
                    NEW.profile_key<>'workflow.personal_v1'
                    AND NEW.personal_selection_id IS NULL
                    AND NEW.personal_selection_fingerprint IS NULL
                )
            )
            BEGIN
                SELECT RAISE(
                    ABORT,'execution_profile_ticket_personal_fields_invalid'
                );
            END
            """
        )
        await db.execute(
            """
            CREATE TRIGGER
                execution_profile_ticket_personal_fields_consistent_update
            BEFORE UPDATE OF profile_key ON execution_profile_launch_tickets
            WHEN NOT (
                (
                    NEW.profile_key='workflow.personal_v1'
                    AND NEW.personal_selection_id IS NOT NULL
                    AND length(NEW.personal_selection_id)>0
                    AND NEW.personal_selection_fingerprint IS NOT NULL
                    AND length(NEW.personal_selection_fingerprint)=64
                    AND NEW.personal_selection_fingerprint
                        NOT GLOB '*[^0-9a-f]*'
                )
                OR
                (
                    NEW.profile_key<>'workflow.personal_v1'
                    AND NEW.personal_selection_id IS NULL
                    AND NEW.personal_selection_fingerprint IS NULL
                )
            )
            BEGIN
                SELECT RAISE(
                    ABORT,'execution_profile_ticket_personal_fields_invalid'
                );
            END
            """
        )
        await db.execute(
            """
            CREATE TABLE execution_skill_scope_activations (
                activation_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL CHECK(schema_version=1),
                run_id TEXT NOT NULL,
                root_run_id TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                scope_hash TEXT NOT NULL,
                capability_snapshot_ref TEXT NOT NULL,
                run_catalog_content_stamp TEXT NOT NULL,
                allowed_tool_names_json TEXT NOT NULL
                    CHECK(json_valid(allowed_tool_names_json)),
                allowed_tool_refs_json TEXT NOT NULL
                    CHECK(json_valid(allowed_tool_refs_json)),
                effective_tool_ref_hashes_json TEXT NOT NULL
                    CHECK(json_valid(effective_tool_ref_hashes_json)),
                effective_tool_refs_hash TEXT NOT NULL,
                instruction_content_hash TEXT NOT NULL,
                continuation_version INTEGER NOT NULL
                    CHECK(continuation_version>=1),
                boundary_ref TEXT NOT NULL,
                boundary_hash TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status='committed'),
                receipt_ref TEXT NOT NULL UNIQUE,
                receipt_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(run_id,scope_id),
                FOREIGN KEY(run_id)
                    REFERENCES execution_runs(run_id) ON DELETE RESTRICT
            )
            """
        )
        await db.execute(
            """
            CREATE TRIGGER execution_skill_scope_activations_immutable_update
            BEFORE UPDATE ON execution_skill_scope_activations
            BEGIN
                SELECT RAISE(
                    ABORT,'execution_skill_scope_activation_immutable'
                );
            END
            """
        )
        await db.execute(
            """
            CREATE TRIGGER execution_skill_scope_activations_immutable_delete
            BEFORE DELETE ON execution_skill_scope_activations
            BEGIN
                SELECT RAISE(
                    ABORT,'execution_skill_scope_activation_immutable'
                );
            END
            """
        )
        await db.execute(
            """INSERT INTO workflow_schema_migrations(version,applied_at)
               VALUES(21,?)""",
            (time.time(),),
        )
        await db.execute("PRAGMA user_version=21")
        await db.commit()
    except BaseException:
        if db.in_transaction:
            await db.rollback()
        raise


async def _migrate_v21_to_v22_run_context_owner_identity(
    db: aiosqlite.Connection,
) -> None:
    """Persist the immutable Companion owner identity carried by RunContext."""

    await db.execute("BEGIN IMMEDIATE")
    try:
        await _add_column_if_missing(
            db,
            "execution_runs",
            "context_owner_key",
            "TEXT",
        )
        await _add_column_if_missing(
            db,
            "execution_runs",
            "context_profile_generation",
            "INTEGER NOT NULL DEFAULT 0 CHECK(context_profile_generation>=0)",
        )
        await _add_column_if_missing(
            db,
            "execution_runs",
            "context_binding_epoch",
            "INTEGER NOT NULL DEFAULT 0 CHECK(context_binding_epoch>=0)",
        )
        await db.execute(
            """
            CREATE TRIGGER execution_run_context_owner_identity_insert
            BEFORE INSERT ON execution_runs
            WHEN NOT (
                (
                    NEW.context_owner_key IS NULL
                    AND NEW.context_profile_generation=0
                    AND NEW.context_binding_epoch=0
                )
                OR
                (
                    NEW.context_owner_key IS NOT NULL
                    AND length(NEW.context_owner_key)>0
                    AND NEW.context_profile_generation>0
                    AND NEW.context_binding_epoch>0
                )
            )
            BEGIN
                SELECT RAISE(
                    ABORT,'execution_run_context_owner_identity_invalid'
                );
            END
            """
        )
        await db.execute(
            """
            CREATE TRIGGER execution_run_context_owner_identity_immutable
            BEFORE UPDATE OF
                context_owner_key,context_profile_generation,context_binding_epoch
            ON execution_runs
            WHEN NEW.context_owner_key IS NOT OLD.context_owner_key
                OR NEW.context_profile_generation<>OLD.context_profile_generation
                OR NEW.context_binding_epoch<>OLD.context_binding_epoch
            BEGIN
                SELECT RAISE(
                    ABORT,'execution_run_context_owner_identity_immutable'
                );
            END
            """
        )
        await db.execute(
            """INSERT INTO workflow_schema_migrations(version,applied_at)
               VALUES(22,?)""",
            (time.time(),),
        )
        await db.execute("PRAGMA user_version=22")
        await db.commit()
    except BaseException:
        if db.in_transaction:
            await db.rollback()
        raise


async def _migrate_v22_to_v23_provider_invocation_audits(
    db: aiosqlite.Connection,
) -> None:
    """Persist the terminal reason behind failed/unknown provider handoffs.

    ``execution_provider_invocations.outcome_ref`` proves that an audit
    receipt existed, but older rows retained only a hash.  That made a real
    post-handoff timeout indistinguishable from cancellation or a transport
    reset when inspecting a completed Run.  This immutable companion row is
    intentionally small and contains no request/response payload.
    """

    await db.execute("BEGIN IMMEDIATE")
    try:
        await db.executescript(
            """
            CREATE TABLE execution_provider_invocation_audits (
                run_id TEXT NOT NULL,
                invocation_id TEXT NOT NULL,
                schema_version INTEGER NOT NULL CHECK(schema_version=1),
                terminal_status TEXT NOT NULL CHECK(
                    terminal_status IN ('failed','unknown')
                ),
                reason TEXT NOT NULL,
                error_type TEXT,
                error_message TEXT,
                created_at REAL NOT NULL,
                PRIMARY KEY(run_id,invocation_id),
                FOREIGN KEY(run_id,invocation_id)
                    REFERENCES execution_provider_invocations(
                        run_id,invocation_id
                    ) ON DELETE CASCADE
            );
            CREATE INDEX idx_execution_provider_invocation_audits_status
                ON execution_provider_invocation_audits(
                    run_id,terminal_status,created_at
                );
            CREATE TRIGGER execution_provider_invocation_audit_immutable_update
            BEFORE UPDATE ON execution_provider_invocation_audits
            BEGIN SELECT RAISE(
                ABORT,'execution_provider_invocation_audit_immutable'
            ); END;
            CREATE TRIGGER execution_provider_invocation_audit_immutable_delete
            BEFORE DELETE ON execution_provider_invocation_audits
            BEGIN SELECT RAISE(
                ABORT,'execution_provider_invocation_audit_immutable'
            ); END;
            """
        )
        await db.execute(
            """INSERT INTO workflow_schema_migrations(version,applied_at)
               VALUES(23,?)""",
            (time.time(),),
        )
        await db.execute("PRAGMA user_version=23")
        await db.commit()
    except BaseException:
        if db.in_transaction:
            await db.rollback()
        raise


async def _migrate_v23_to_v24_provider_invocation_inputs(
    db: aiosqlite.Connection,
) -> None:
    """Add bounded, redacted Provider inputs for Harness observability."""

    await db.execute("BEGIN IMMEDIATE")
    try:
        await db.executescript(
            """
            CREATE TABLE execution_provider_invocation_inputs (
                run_id TEXT NOT NULL,
                invocation_id TEXT NOT NULL,
                schema_version INTEGER NOT NULL CHECK(schema_version=1),
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL CHECK(
                    length(payload_hash)=64
                    AND payload_hash NOT GLOB '*[^0-9a-f]*'
                ),
                created_at REAL NOT NULL,
                PRIMARY KEY(run_id,invocation_id),
                FOREIGN KEY(run_id,invocation_id)
                    REFERENCES execution_provider_invocations(
                        run_id,invocation_id
                    ) ON DELETE CASCADE
            );
            CREATE TRIGGER execution_provider_invocation_input_immutable_update
            BEFORE UPDATE ON execution_provider_invocation_inputs
            BEGIN SELECT RAISE(
                ABORT,'execution_provider_invocation_input_immutable'
            ); END;
            CREATE TRIGGER execution_provider_invocation_input_immutable_delete
            BEFORE DELETE ON execution_provider_invocation_inputs
            BEGIN SELECT RAISE(
                ABORT,'execution_provider_invocation_input_immutable'
            ); END;
            """
        )
        await db.execute(
            """INSERT INTO workflow_schema_migrations(version,applied_at)
               VALUES(24,?)""",
            (time.time(),),
        )
        await db.execute("PRAGMA user_version=24")
        await db.commit()
    except BaseException:
        if db.in_transaction:
            await db.rollback()
        raise


async def _migrate_v24_to_v25_provider_batch_pending_count(
    db: aiosqlite.Connection,
) -> None:
    """Allow a settled provider batch to record zero pending calls."""

    await db.commit()
    await db.execute("PRAGMA foreign_keys=OFF")
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.executescript(
            """
            CREATE TABLE execution_provider_action_batches_v25 (
                provider_batch_id TEXT PRIMARY KEY,
                root_run_id TEXT NOT NULL,
                provider_turn_id TEXT NOT NULL,
                canonical_assistant_batch_ref TEXT NOT NULL,
                batch_fingerprint TEXT NOT NULL CHECK(
                    length(batch_fingerprint)=64
                    AND batch_fingerprint NOT GLOB '*[^0-9a-f]*'
                ),
                pending_call_count INTEGER NOT NULL
                    CHECK(pending_call_count>=0),
                failure_set_id TEXT,
                status TEXT NOT NULL CHECK(status IN (
                    'admitted','running','waiting_external',
                    'ready_backfill','settled'
                )),
                batch_version INTEGER NOT NULL DEFAULT 0
                    CHECK(batch_version>=0),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                settled_at REAL,
                UNIQUE(root_run_id,provider_turn_id),
                CHECK(
                    (status='settled' AND settled_at IS NOT NULL
                        AND pending_call_count=0)
                    OR
                    (status<>'settled' AND settled_at IS NULL
                        AND pending_call_count>=1)
                ),
                FOREIGN KEY(root_run_id)
                    REFERENCES execution_task_goals(root_run_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(provider_turn_id)
                    REFERENCES execution_provider_turn_fences(provider_turn_id)
                    ON DELETE RESTRICT
            );
            INSERT INTO execution_provider_action_batches_v25
            SELECT
                provider_batch_id,root_run_id,provider_turn_id,
                canonical_assistant_batch_ref,batch_fingerprint,
                CASE WHEN status='settled' THEN 0 ELSE pending_call_count END,
                failure_set_id,status,batch_version,created_at,updated_at,
                settled_at
            FROM execution_provider_action_batches;
            DROP TABLE execution_provider_action_batches;
            ALTER TABLE execution_provider_action_batches_v25
                RENAME TO execution_provider_action_batches;
            """
        )
        await db.execute(
            """INSERT INTO workflow_schema_migrations(version,applied_at)
               VALUES(25,?)""",
            (time.time(),),
        )
        await db.execute("PRAGMA user_version=25")
        await db.commit()
    except BaseException:
        if db.in_transaction:
            await db.rollback()
        raise
    finally:
        await db.execute("PRAGMA foreign_keys=ON")


async def _migrate_v25_to_v26_project_workspace_rebind(
    db: aiosqlite.Connection,
) -> None:
    """Allow one fenced task-default -> user-selected workspace transition."""

    await db.executescript(
        """
        BEGIN IMMEDIATE;
        DROP TRIGGER IF EXISTS execution_task_work_identity_immutable;
        CREATE TRIGGER execution_task_work_identity_immutable
        BEFORE UPDATE OF root_run_id,session_id,task_scope_id
        ON execution_task_work_contexts
        BEGIN SELECT RAISE(
            ABORT,'execution_task_work_identity_immutable'
        ); END;
        CREATE TRIGGER execution_task_workspace_rebind_guard
        BEFORE UPDATE OF workspace_root,workspace_source,binding_version
        ON execution_task_work_contexts
        WHEN NOT (
            NEW.workspace_root IS OLD.workspace_root
            AND NEW.workspace_source=OLD.workspace_source
            AND NEW.binding_version=OLD.binding_version
        ) AND NOT (
            OLD.workspace_source='task_default'
            AND NEW.workspace_source='user_path'
            AND NEW.workspace_root IS NOT NULL
            AND NEW.binding_version=OLD.binding_version+1
        )
        BEGIN SELECT RAISE(
            ABORT,'execution_task_workspace_rebind_invalid'
        ); END;
        INSERT INTO workflow_schema_migrations(version,applied_at)
        VALUES(26,CAST(strftime('%s','now') AS REAL));
        PRAGMA user_version=26;
        COMMIT;
        """
    )


async def _migrate_v26_to_v27_active_execution_budget(
    db: aiosqlite.Connection,
) -> None:
    """Persist root Run active time separately from user/external waiting."""

    await db.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE execution_run_active_budgets (
            run_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK(schema_version=1),
            limit_seconds REAL NOT NULL CHECK(limit_seconds>0),
            consumed_seconds REAL NOT NULL DEFAULT 0 CHECK(consumed_seconds>=0),
            active_since REAL,
            budget_state TEXT NOT NULL CHECK(budget_state IN (
                'active','paused','expired','terminal'
            )),
            configured INTEGER NOT NULL DEFAULT 0 CHECK(configured IN (0,1)),
            budget_version INTEGER NOT NULL DEFAULT 0 CHECK(budget_version>=0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(run_id) REFERENCES execution_runs(run_id) ON DELETE CASCADE,
            CHECK(
                (budget_state='active' AND active_since IS NOT NULL)
                OR
                (budget_state<>'active' AND active_since IS NULL)
            )
        );
        CREATE INDEX idx_execution_run_active_budgets_due
            ON execution_run_active_budgets(
                budget_state,limit_seconds,consumed_seconds,active_since
            );

        INSERT INTO execution_run_active_budgets(
            run_id,schema_version,limit_seconds,consumed_seconds,active_since,
            budget_state,configured,budget_version,created_at,updated_at
        )
        SELECT
            run_id,1,900.0,0.0,
            CASE WHEN status IN ('created','queued','running')
                THEN updated_at ELSE NULL END,
            CASE
                WHEN status IN ('completed','failed','cancelled') THEN 'terminal'
                WHEN status IN ('created','queued','running') THEN 'active'
                ELSE 'paused'
            END,
            0,0,created_at,updated_at
        FROM execution_runs
        WHERE parent_run_id IS NULL;

        CREATE TRIGGER execution_root_active_budget_insert
        AFTER INSERT ON execution_runs
        WHEN NEW.parent_run_id IS NULL
        BEGIN
            INSERT INTO execution_run_active_budgets(
                run_id,schema_version,limit_seconds,consumed_seconds,active_since,
                budget_state,configured,budget_version,created_at,updated_at
            ) VALUES(
                NEW.run_id,1,900.0,0.0,
                CASE WHEN NEW.status IN ('created','queued','running')
                    THEN NEW.updated_at ELSE NULL END,
                CASE
                    WHEN NEW.status IN ('completed','failed','cancelled') THEN 'terminal'
                    WHEN NEW.status IN ('created','queued','running') THEN 'active'
                    ELSE 'paused'
                END,
                0,0,NEW.created_at,NEW.updated_at
            );
        END;

        CREATE TRIGGER execution_root_active_budget_status
        AFTER UPDATE OF status ON execution_runs
        WHEN OLD.status<>NEW.status AND NEW.parent_run_id IS NULL
        BEGIN
            UPDATE execution_run_active_budgets
            SET consumed_seconds = consumed_seconds + CASE
                    WHEN budget_state='active' AND active_since IS NOT NULL
                    THEN MAX(0.0,NEW.updated_at-active_since)
                    ELSE 0.0
                END,
                active_since = CASE
                    WHEN budget_state='expired' THEN NULL
                    WHEN NEW.status IN ('created','queued','running')
                    THEN NEW.updated_at
                    ELSE NULL
                END,
                budget_state = CASE
                    WHEN budget_state='expired'
                        AND NEW.status NOT IN ('completed','failed','cancelled')
                    THEN 'expired'
                    WHEN NEW.status IN ('completed','failed','cancelled')
                    THEN 'terminal'
                    WHEN NEW.status IN ('created','queued','running')
                    THEN 'active'
                    ELSE 'paused'
                END,
                budget_version=budget_version+1,
                updated_at=NEW.updated_at
            WHERE run_id=NEW.run_id;
        END;

        INSERT INTO workflow_schema_migrations(version,applied_at)
        VALUES(27,CAST(strftime('%s','now') AS REAL));
        PRAGMA user_version=27;
        COMMIT;
        """
    )


async def _migrate_v27_to_v28_existing_workspace_rebind(
    db: aiosqlite.Connection,
) -> None:
    """Let a fresh task replace an inherited project after native confirmation."""

    await db.executescript(
        """
        BEGIN IMMEDIATE;
        DROP TRIGGER IF EXISTS execution_task_workspace_rebind_guard;
        CREATE TRIGGER execution_task_workspace_rebind_guard
        BEFORE UPDATE OF workspace_root,workspace_source,binding_version
        ON execution_task_work_contexts
        WHEN NOT (
            NEW.workspace_root IS OLD.workspace_root
            AND NEW.workspace_source=OLD.workspace_source
            AND NEW.binding_version=OLD.binding_version
        ) AND NOT (
            OLD.workspace_source IN ('task_default','existing')
            AND NEW.workspace_source='user_path'
            AND NEW.workspace_root IS NOT NULL
            AND NEW.binding_version=OLD.binding_version+1
        )
        BEGIN SELECT RAISE(
            ABORT,'execution_task_workspace_rebind_invalid'
        ); END;
        INSERT INTO workflow_schema_migrations(version,applied_at)
        VALUES(28,CAST(strftime('%s','now') AS REAL));
        PRAGMA user_version=28;
        COMMIT;
        """
    )


async def _migrate_v28_to_v29_public_run_projection(
    db: aiosqlite.Connection,
    *,
    fault_injector: Callable[[str], None] | None = None,
) -> None:
    """Install immutable presentation sidecars in one atomic transaction.

    The optional hook exists only for deterministic crash-window tests.  A
    raised exception rolls back DDL, the durable marker, and ``user_version``
    together, so retrying the migration is safe.
    """

    def fault(point: str) -> None:
        if fault_injector is not None:
            fault_injector(point)

    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """
            CREATE TABLE execution_run_tool_presentation_specs (
                root_run_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                schema_version INTEGER NOT NULL CHECK(schema_version=1),
                policy_json TEXT NOT NULL,
                policy_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(root_run_id,tool_name),
                FOREIGN KEY(root_run_id) REFERENCES execution_runs(run_id)
                    ON DELETE CASCADE
            )
            """
        )
        await db.execute(
            "CREATE INDEX idx_execution_tool_specs_root "
            "ON execution_run_tool_presentation_specs(root_run_id,tool_name)"
        )
        await db.execute(
            """
            CREATE TABLE execution_tool_public_projections (
                effect_id TEXT PRIMARY KEY,
                root_run_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                schema_version INTEGER NOT NULL CHECK(schema_version=1),
                projection_json TEXT NOT NULL,
                projection_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY(root_run_id) REFERENCES execution_runs(run_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(effect_id) REFERENCES execution_effects(effect_id)
                    ON DELETE CASCADE
            )
            """
        )
        await db.execute(
            "CREATE INDEX idx_execution_tool_public_root "
            "ON execution_tool_public_projections(root_run_id,created_at,effect_id)"
        )
        await db.execute(
            """
            CREATE TABLE execution_run_block_signals (
                signal_id TEXT PRIMARY KEY,
                root_run_id TEXT NOT NULL UNIQUE,
                schema_version INTEGER NOT NULL CHECK(schema_version=1),
                reason_code TEXT NOT NULL CHECK(reason_code IN (
                    'provider_binding_unavailable',
                    'capability_unavailable',
                    'external_dependency_unavailable',
                    'workspace_unavailable'
                )),
                evidence_refs_json TEXT NOT NULL,
                producer TEXT NOT NULL,
                created_event_id TEXT NOT NULL UNIQUE,
                payload_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY(root_run_id) REFERENCES execution_runs(run_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(created_event_id) REFERENCES execution_events(event_id)
                    ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
            )
            """
        )
        fault("after_ddl")
        await db.execute(
            "INSERT INTO workflow_schema_migrations(version,applied_at) "
            "VALUES(29,?)",
            (time.time(),),
        )
        fault("after_marker")
        fault("before_user_version")
        await db.execute("PRAGMA user_version=29")
        fault("after_user_version")
        await db.commit()
    except BaseException:
        if db.in_transaction:
            await db.rollback()
        raise


async def _migrate_v29_to_v30_capability_skill_verification(
    db: aiosqlite.Connection,
    *,
    fault_injector: Callable[[str], None] | None = None,
) -> None:
    """Move the complete Skill-install verification aggregate to execution.db."""

    from deskpet.capabilities.store import migrate_capability_schema_v2_to_v3

    def fault(point: str) -> None:
        if fault_injector is not None:
            fault_injector(point)

    try:
        await db.execute("BEGIN IMMEDIATE")
        await migrate_capability_schema_v2_to_v3(db)
        fault("after_capability_schema")
        foreign_key_errors = await (
            await db.execute("PRAGMA foreign_key_check")
        ).fetchall()
        if foreign_key_errors:
            raise RuntimeError(
                "workflow.db v30 capability foreign key verification failed: "
                f"{foreign_key_errors[0]}"
            )
        await db.execute(
            "INSERT INTO workflow_schema_migrations(version,applied_at) VALUES(30,?)",
            (time.time(),),
        )
        fault("after_marker")
        await db.execute("PRAGMA user_version=30")
        fault("after_user_version")
        await db.commit()
    except BaseException:
        if db.in_transaction:
            await db.rollback()
        raise
