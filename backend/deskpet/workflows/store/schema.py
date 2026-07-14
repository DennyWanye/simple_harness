"""Versioned schema for the standalone ``workflow.db`` database."""

from __future__ import annotations

import time
from pathlib import Path

import aiosqlite

WORKFLOW_SCHEMA_VERSION = 2

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
                await db.executescript(_SCHEMA_V2)
            elif current == 1:
                await _migrate_v1_to_v2(db)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
    return db_path


async def _columns(db: aiosqlite.Connection, table: str) -> set[str]:
    rows = await (await db.execute(f"PRAGMA table_info({table})")).fetchall()
    return {str(row[1]) for row in rows}


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
