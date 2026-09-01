-- Exact, immutable worker/outbox quiescence evidence for recovery v43.

CREATE TABLE human_memory_quiescence_marker (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    format_epoch TEXT NOT NULL CHECK(format_epoch='human-memory-v1'),
    schema_version INTEGER NOT NULL CHECK(schema_version=1),
    migration_id TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL CHECK(length(migration_sha256)=64),
    initialized_at REAL NOT NULL
);

CREATE TABLE human_memory_recovery_work_items (
    item_receipt_id TEXT PRIMARY KEY,
    generation INTEGER NOT NULL CHECK(generation > 0),
    worker_kind TEXT NOT NULL CHECK(worker_kind IN (
        'projection-source','projection-legacy','search',
        'foreground-signal','foreground-lease'
    )),
    source_table TEXT NOT NULL CHECK(source_table IN (
        'task_scope_projection_source_outbox','task_scope_projection_outbox',
        'task_scope_search_outbox','foreground_signal_outbox','foreground_run_heads'
    )),
    item_pk TEXT NOT NULL,
    item_content_hash TEXT NOT NULL CHECK(length(item_content_hash)=64),
    disposition TEXT NOT NULL CHECK(disposition IN (
        'delivered','acked','parked','lease_parked'
    )),
    proof_ref TEXT,
    proof_hash TEXT CHECK(proof_hash IS NULL OR length(proof_hash)=64),
    durable_watermark INTEGER,
    lease_owner_id TEXT,
    lease_generation INTEGER CHECK(lease_generation IS NULL OR lease_generation > 0),
    cutoff TEXT NOT NULL CHECK(length(cutoff)=64),
    receipt_hash TEXT NOT NULL UNIQUE CHECK(length(receipt_hash)=64),
    receipt_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    UNIQUE(generation,worker_kind,source_table,item_pk),
    CHECK (
        (disposition='lease_parked' AND lease_owner_id IS NOT NULL AND lease_generation IS NOT NULL)
        OR (disposition<>'lease_parked' AND lease_owner_id IS NULL AND lease_generation IS NULL)
    )
);

CREATE TRIGGER human_memory_quiescence_marker_no_update
BEFORE UPDATE ON human_memory_quiescence_marker BEGIN
    SELECT RAISE(ABORT,'human_memory_recovery_append_only');
END;
CREATE TRIGGER human_memory_quiescence_marker_no_delete
BEFORE DELETE ON human_memory_quiescence_marker BEGIN
    SELECT RAISE(ABORT,'human_memory_recovery_append_only');
END;
CREATE TRIGGER human_memory_recovery_work_item_no_update
BEFORE UPDATE ON human_memory_recovery_work_items BEGIN
    SELECT RAISE(ABORT,'human_memory_recovery_append_only');
END;
CREATE TRIGGER human_memory_recovery_work_item_no_delete
BEFORE DELETE ON human_memory_recovery_work_items BEGIN
    SELECT RAISE(ABORT,'human_memory_recovery_append_only');
END;
