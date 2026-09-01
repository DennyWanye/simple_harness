-- Durable recovery fence, logical manifests and emergency export lineage.

CREATE TABLE human_memory_recovery_marker (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    format_epoch TEXT NOT NULL CHECK(format_epoch='human-memory-v1'),
    schema_version INTEGER NOT NULL CHECK(schema_version=1),
    migration_id TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL CHECK(length(migration_sha256)=64),
    initialized_at REAL NOT NULL
);

CREATE TABLE human_memory_recovery_transitions (
    transition_id TEXT PRIMARY KEY,
    generation INTEGER NOT NULL CHECK(generation > 0),
    from_state TEXT,
    to_state TEXT NOT NULL CHECK(to_state IN ('OPEN','CLOSING','QUIESCED','SEALED','FAILED_CLOSED')),
    cutoff TEXT,
    reason_code TEXT,
    transition_hash TEXT NOT NULL UNIQUE CHECK(length(transition_hash)=64),
    transition_json TEXT NOT NULL,
    recorded_at REAL NOT NULL
);

CREATE TABLE human_memory_recovery_fence (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    state TEXT NOT NULL CHECK(state IN ('OPEN','CLOSING','QUIESCED','SEALED','FAILED_CLOSED')),
    generation INTEGER NOT NULL CHECK(generation > 0),
    cutoff TEXT,
    last_transition_id TEXT NOT NULL UNIQUE,
    last_transition_hash TEXT NOT NULL CHECK(length(last_transition_hash)=64),
    failure_code TEXT,
    updated_at REAL NOT NULL,
    FOREIGN KEY(last_transition_id) REFERENCES human_memory_recovery_transitions(transition_id)
);

CREATE TABLE human_memory_recovery_worker_receipts (
    receipt_id TEXT PRIMARY KEY,
    generation INTEGER NOT NULL CHECK(generation > 0),
    worker_kind TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('drain','park')),
    cutoff TEXT NOT NULL,
    item_count INTEGER NOT NULL CHECK(item_count >= 0),
    item_root TEXT NOT NULL CHECK(length(item_root)=64),
    gap_count INTEGER NOT NULL CHECK(gap_count >= 0),
    receipt_hash TEXT NOT NULL UNIQUE CHECK(length(receipt_hash)=64),
    receipt_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    UNIQUE(generation,worker_kind)
);

CREATE TABLE human_memory_recovery_wal_receipts (
    receipt_id TEXT PRIMARY KEY,
    generation INTEGER NOT NULL UNIQUE CHECK(generation > 0),
    busy INTEGER NOT NULL,
    log_frames INTEGER NOT NULL,
    checkpointed_frames INTEGER NOT NULL,
    receipt_hash TEXT NOT NULL UNIQUE CHECK(length(receipt_hash)=64),
    receipt_json TEXT NOT NULL,
    recorded_at REAL NOT NULL
);

CREATE TABLE human_memory_recovery_manifests (
    manifest_id TEXT PRIMARY KEY,
    generation INTEGER NOT NULL UNIQUE CHECK(generation > 0),
    cutoff TEXT NOT NULL,
    db_instance_id TEXT NOT NULL,
    migration_chain_hash TEXT NOT NULL CHECK(length(migration_chain_hash)=64),
    table_set_hash TEXT NOT NULL CHECK(length(table_set_hash)=64),
    overall_root TEXT NOT NULL CHECK(length(overall_root)=64),
    manifest_hash TEXT NOT NULL UNIQUE CHECK(length(manifest_hash)=64),
    manifest_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE human_memory_recovery_manifest_tables (
    table_manifest_id TEXT PRIMARY KEY,
    manifest_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    taxonomy TEXT NOT NULL CHECK(taxonomy IN ('A','B','C')),
    schema_hash TEXT NOT NULL CHECK(length(schema_hash)=64),
    primary_key_json TEXT NOT NULL,
    row_count INTEGER NOT NULL CHECK(row_count >= 0),
    row_root TEXT NOT NULL CHECK(length(row_root)=64),
    manifest_hash TEXT NOT NULL CHECK(length(manifest_hash)=64),
    manifest_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(manifest_id,table_name),
    FOREIGN KEY(manifest_id) REFERENCES human_memory_recovery_manifests(manifest_id)
);

CREATE TABLE human_memory_emergency_exports (
    receipt_id TEXT PRIMARY KEY,
    export_id TEXT NOT NULL UNIQUE,
    manifest_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation > 0),
    artifact_name TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL CHECK(length(artifact_sha256)=64),
    artifact_size INTEGER NOT NULL CHECK(artifact_size >= 0),
    overall_root TEXT NOT NULL CHECK(length(overall_root)=64),
    receipt_hash TEXT NOT NULL UNIQUE CHECK(length(receipt_hash)=64),
    receipt_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(manifest_id) REFERENCES human_memory_recovery_manifests(manifest_id)
);

CREATE TABLE human_memory_recovery_table_registry (
    table_name TEXT PRIMARY KEY,
    taxonomy TEXT NOT NULL CHECK(taxonomy IN ('A','B','C','D')),
    columns_json TEXT NOT NULL
);

INSERT INTO human_memory_recovery_table_registry(table_name,taxonomy,columns_json) VALUES
('human_memory_program_bootstrap','A','[]'),
('human_memory_program_marker','A','[]'),
('human_memory_migration_chain','A','[]'),
('human_memory_primary_conversations','A','[]'),
('human_memory_init_receipts','A','[]'),
('human_memory_sanitization_receipts','A','[]'),
('human_memory_evidence','A','[]'),
('task_scope_archive_marker','A','[]'),
('task_scope_provision_marker','A','[]'),
('task_workspace_binding_marker','A','[]'),
('foreground_queue_marker','A','[]'),
('task_scopes','A','[]'),
('task_scope_events','A','[]'),
('task_scope_steps','A','[]'),
('task_scope_evidence_links','A','[]'),
('task_scope_mutation_decisions','A','[]'),
('task_scope_mutation_attempts','A','[]'),
('task_scope_canonical_revisions','A','[]'),
('task_scope_checkpoints','A','[]'),
('task_scope_projection_outbox','A','[]'),
('task_scope_search_outbox','A','[]'),
('task_scope_execution_ingest_receipts','A','[]'),
('task_scope_terminal_gate_receipts','A','[]'),
('task_scope_provisions','A','[]'),
('task_scope_provision_events','A','[]'),
('task_scope_provision_receipts','A','[]'),
('task_workspace_binding_proposals','A','[]'),
('task_workspace_manual_challenges','A','[]'),
('task_workspace_manual_decisions','A','[]'),
('task_workspace_run_mode_snapshots','A','[]'),
('task_workspace_binding_grants','A','[]'),
('task_workspace_binding_revisions','A','[]'),
('task_workspace_binding_roots','A','[]'),
('task_scope_projection_sources','A','[]'),
('task_scope_projection_source_outbox','A','[]'),
('foreground_turns','A','[]'),
('foreground_runs','A','[]'),
('foreground_turn_transitions','A','[]'),
('foreground_run_sdk_bindings','A','[]'),
('foreground_run_transitions','A','[]'),
('foreground_lease_receipts','A','[]'),
('foreground_control_intents','A','[]'),
('foreground_signal_outbox','A','[]'),
('foreground_signal_acks','A','[]'),
('foreground_terminal_receipts','A','[]'),
('task_scope_heads','B','[]'),
('task_scope_run_watermarks','B','[]'),
('task_workspace_binding_heads','B','[]'),
('task_scope_projection_source_heads','B','[]'),
('foreground_turn_heads','B','[]'),
('foreground_run_heads','B','[]'),
('task_scope_projection_revisions','C','[]'),
('task_scope_read_view_revisions','C','[]'),
('task_scope_projection_materialization_receipts','C','[]'),
('task_scope_search_documents','C','[]'),
('task_scope_search_rebuild_receipts','C','[]'),
('task_scope_search_access_receipts','C','[]'),
('human_memory_recovery_marker','C','[]'),
('human_memory_recovery_transitions','C','[]'),
('human_memory_recovery_fence','C','[]'),
('human_memory_recovery_worker_receipts','C','[]'),
('human_memory_recovery_wal_receipts','C','[]'),
('human_memory_recovery_manifests','C','[]'),
('human_memory_recovery_manifest_tables','C','[]'),
('human_memory_emergency_exports','C','[]'),
('human_memory_recovery_table_registry','C','[]'),
('task_scope_projection_cache','D','[]'),
('task_scope_read_blocks','D','[]'),
('task_scope_search_heads','D','[]'),
('task_scope_search_fts','D','[]'),
('task_scope_search_fts_data','D','[]'),
('task_scope_search_fts_idx','D','[]'),
('task_scope_search_fts_content','D','[]'),
('task_scope_search_fts_docsize','D','[]'),
('task_scope_search_fts_config','D','[]');

CREATE TRIGGER human_memory_recovery_marker_no_update BEFORE UPDATE ON human_memory_recovery_marker BEGIN SELECT RAISE(ABORT,'human_memory_recovery_append_only'); END;
CREATE TRIGGER human_memory_recovery_marker_no_delete BEFORE DELETE ON human_memory_recovery_marker BEGIN SELECT RAISE(ABORT,'human_memory_recovery_append_only'); END;
CREATE TRIGGER human_memory_recovery_transition_no_update BEFORE UPDATE ON human_memory_recovery_transitions BEGIN SELECT RAISE(ABORT,'human_memory_recovery_append_only'); END;
CREATE TRIGGER human_memory_recovery_transition_no_delete BEFORE DELETE ON human_memory_recovery_transitions BEGIN SELECT RAISE(ABORT,'human_memory_recovery_append_only'); END;
CREATE TRIGGER human_memory_recovery_worker_no_update BEFORE UPDATE ON human_memory_recovery_worker_receipts BEGIN SELECT RAISE(ABORT,'human_memory_recovery_append_only'); END;
CREATE TRIGGER human_memory_recovery_worker_no_delete BEFORE DELETE ON human_memory_recovery_worker_receipts BEGIN SELECT RAISE(ABORT,'human_memory_recovery_append_only'); END;
CREATE TRIGGER human_memory_recovery_wal_no_update BEFORE UPDATE ON human_memory_recovery_wal_receipts BEGIN SELECT RAISE(ABORT,'human_memory_recovery_append_only'); END;
CREATE TRIGGER human_memory_recovery_wal_no_delete BEFORE DELETE ON human_memory_recovery_wal_receipts BEGIN SELECT RAISE(ABORT,'human_memory_recovery_append_only'); END;
CREATE TRIGGER human_memory_recovery_manifest_no_update BEFORE UPDATE ON human_memory_recovery_manifests BEGIN SELECT RAISE(ABORT,'human_memory_recovery_append_only'); END;
CREATE TRIGGER human_memory_recovery_manifest_no_delete BEFORE DELETE ON human_memory_recovery_manifests BEGIN SELECT RAISE(ABORT,'human_memory_recovery_append_only'); END;
CREATE TRIGGER human_memory_recovery_manifest_table_no_update BEFORE UPDATE ON human_memory_recovery_manifest_tables BEGIN SELECT RAISE(ABORT,'human_memory_recovery_append_only'); END;
CREATE TRIGGER human_memory_recovery_manifest_table_no_delete BEFORE DELETE ON human_memory_recovery_manifest_tables BEGIN SELECT RAISE(ABORT,'human_memory_recovery_append_only'); END;
CREATE TRIGGER human_memory_emergency_export_no_update BEFORE UPDATE ON human_memory_emergency_exports BEGIN SELECT RAISE(ABORT,'human_memory_recovery_append_only'); END;
CREATE TRIGGER human_memory_emergency_export_no_delete BEFORE DELETE ON human_memory_emergency_exports BEGIN SELECT RAISE(ABORT,'human_memory_recovery_append_only'); END;
CREATE TRIGGER human_memory_recovery_fence_no_delete BEFORE DELETE ON human_memory_recovery_fence BEGIN SELECT RAISE(ABORT,'human_memory_recovery_fence_invalid'); END;
CREATE TRIGGER human_memory_recovery_fence_guard BEFORE UPDATE ON human_memory_recovery_fence
WHEN NEW.singleton<>OLD.singleton
 OR NOT (
   (OLD.state='OPEN' AND NEW.state='CLOSING' AND NEW.generation=OLD.generation+1 AND NEW.cutoff IS NOT NULL)
   OR (OLD.state='CLOSING' AND NEW.state='QUIESCED' AND NEW.generation=OLD.generation AND NEW.cutoff=OLD.cutoff)
   OR (OLD.state='QUIESCED' AND NEW.state='SEALED' AND NEW.generation=OLD.generation AND NEW.cutoff=OLD.cutoff)
   OR (OLD.state='SEALED' AND NEW.state='OPEN' AND NEW.generation=OLD.generation AND NEW.cutoff IS NULL)
   OR (OLD.state<>'FAILED_CLOSED' AND NEW.state='FAILED_CLOSED' AND NEW.generation=OLD.generation)
 )
BEGIN SELECT RAISE(ABORT,'human_memory_recovery_fence_invalid'); END;
