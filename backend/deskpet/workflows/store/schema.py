"""Versioned schema for the standalone ``workflow.db`` database."""

from __future__ import annotations

import time
from pathlib import Path

import aiosqlite

WORKFLOW_SCHEMA_VERSION = 5

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
                'prepared','running','succeeded','failed','unknown','cancelled','late_reconciled'
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
                'running','succeeded','failed','unknown','cancelled','late_reconciled'
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
