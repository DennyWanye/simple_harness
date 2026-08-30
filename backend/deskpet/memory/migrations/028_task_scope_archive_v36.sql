-- Canonical TaskScope archive for the fresh human-memory-v1 epoch.
-- Raw events, imported evidence, decisions, canonical revisions and
-- checkpoints are permanent.  Heads, watermarks and projection_cache are
-- explicitly derived coordination state and may be advanced/rebuilt.

CREATE TABLE task_scope_archive_marker (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    format_epoch TEXT NOT NULL CHECK (format_epoch = 'human-memory-v1'),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    migration_id TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL CHECK (length(migration_sha256) = 64),
    initialized_at REAL NOT NULL
);

CREATE TABLE task_scopes (
    task_scope_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX idx_task_scopes_subject_created
ON task_scopes(subject, created_at, task_scope_id);

CREATE TABLE task_scope_events (
    event_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    event_sequence INTEGER NOT NULL CHECK (event_sequence > 0),
    event_kind TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('host', 'harness', 'mutation')),
    source_event_id TEXT NOT NULL UNIQUE,
    payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
    payload_json TEXT NOT NULL,
    reason_code TEXT,
    occurred_at REAL NOT NULL,
    committed_at REAL NOT NULL,
    UNIQUE(task_scope_id, event_sequence),
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id)
);
CREATE INDEX idx_task_scope_events_order
ON task_scope_events(task_scope_id, event_sequence);

CREATE TABLE task_scope_steps (
    step_record_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    operation_kind TEXT NOT NULL CHECK (operation_kind LIKE 'plan.%'),
    value TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id),
    FOREIGN KEY(event_id) REFERENCES task_scope_events(event_id)
);

CREATE TABLE task_scope_evidence_links (
    link_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    created_at REAL NOT NULL,
    UNIQUE(event_id, ordinal),
    UNIQUE(event_id, evidence_id),
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id),
    FOREIGN KEY(event_id) REFERENCES task_scope_events(event_id),
    FOREIGN KEY(evidence_id) REFERENCES human_memory_evidence(evidence_id)
);

CREATE TABLE task_scope_mutation_decisions (
    decision_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    plan_id TEXT NOT NULL UNIQUE,
    plan_hash TEXT NOT NULL CHECK (length(plan_hash) = 64),
    base_revision INTEGER NOT NULL CHECK (base_revision > 0),
    committed_revision INTEGER NOT NULL CHECK (committed_revision > 0),
    outcome TEXT NOT NULL CHECK (outcome IN ('mutate', 'no_mutation')),
    plan_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id)
);

CREATE TABLE task_scope_mutation_attempts (
    attempt_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    plan_id TEXT NOT NULL UNIQUE,
    plan_hash TEXT NOT NULL CHECK (length(plan_hash) = 64),
    requested_base_revision INTEGER NOT NULL CHECK (requested_base_revision > 0),
    observed_revision INTEGER NOT NULL CHECK (observed_revision > 0),
    result TEXT NOT NULL CHECK (result IN ('applied', 'cas_conflict')),
    plan_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id)
);

CREATE TABLE task_scope_canonical_revisions (
    task_scope_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    prior_revision INTEGER,
    decision_id TEXT,
    state_hash TEXT NOT NULL CHECK (length(state_hash) = 64),
    state_json TEXT NOT NULL,
    event_watermark INTEGER NOT NULL CHECK (event_watermark >= 0),
    created_at REAL NOT NULL,
    PRIMARY KEY(task_scope_id, revision),
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id),
    FOREIGN KEY(decision_id) REFERENCES task_scope_mutation_decisions(decision_id)
);

CREATE TABLE task_scope_heads (
    task_scope_id TEXT PRIMARY KEY,
    current_revision INTEGER NOT NULL CHECK (current_revision > 0),
    event_watermark INTEGER NOT NULL CHECK (event_watermark >= 0),
    state_hash TEXT NOT NULL CHECK (length(state_hash) = 64),
    updated_at REAL NOT NULL,
    FOREIGN KEY(task_scope_id, current_revision)
        REFERENCES task_scope_canonical_revisions(task_scope_id, revision)
);

CREATE TABLE task_scope_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    checkpoint_hash TEXT NOT NULL CHECK (length(checkpoint_hash) = 64),
    checkpoint_json TEXT NOT NULL,
    event_watermark INTEGER NOT NULL CHECK (event_watermark >= 0),
    created_at REAL NOT NULL,
    FOREIGN KEY(task_scope_id, revision)
        REFERENCES task_scope_canonical_revisions(task_scope_id, revision)
);

CREATE TABLE task_scope_projection_revisions (
    projection_revision_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    canonical_revision INTEGER NOT NULL,
    projection_hash TEXT NOT NULL CHECK (length(projection_hash) = 64),
    created_at REAL NOT NULL,
    UNIQUE(task_scope_id, canonical_revision),
    FOREIGN KEY(task_scope_id, canonical_revision)
        REFERENCES task_scope_canonical_revisions(task_scope_id, revision)
);

-- Derived and deliberately rebuildable from canonical revisions.
CREATE TABLE task_scope_projection_cache (
    task_scope_id TEXT NOT NULL,
    canonical_revision INTEGER NOT NULL,
    projection_hash TEXT NOT NULL CHECK (length(projection_hash) = 64),
    projection_json TEXT NOT NULL,
    rebuilt_at REAL NOT NULL,
    PRIMARY KEY(task_scope_id, canonical_revision),
    FOREIGN KEY(task_scope_id, canonical_revision)
        REFERENCES task_scope_canonical_revisions(task_scope_id, revision)
);

CREATE TABLE task_scope_projection_outbox (
    outbox_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    canonical_revision INTEGER NOT NULL,
    projection_hash TEXT NOT NULL CHECK (length(projection_hash) = 64),
    created_at REAL NOT NULL,
    UNIQUE(task_scope_id, canonical_revision),
    FOREIGN KEY(task_scope_id, canonical_revision)
        REFERENCES task_scope_canonical_revisions(task_scope_id, revision)
);

CREATE TABLE task_scope_search_outbox (
    outbox_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    canonical_revision INTEGER NOT NULL,
    state_hash TEXT NOT NULL CHECK (length(state_hash) = 64),
    created_at REAL NOT NULL,
    UNIQUE(task_scope_id, canonical_revision),
    FOREIGN KEY(task_scope_id, canonical_revision)
        REFERENCES task_scope_canonical_revisions(task_scope_id, revision)
);

CREATE TABLE task_scope_execution_ingest_receipts (
    receipt_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source_sequence INTEGER NOT NULL CHECK (source_sequence > 0),
    source_event_id TEXT NOT NULL UNIQUE,
    evidence_hash TEXT NOT NULL CHECK (length(evidence_hash) = 64),
    event_id TEXT NOT NULL UNIQUE,
    evidence_kind TEXT NOT NULL,
    committed_at REAL NOT NULL,
    UNIQUE(run_id, source_sequence),
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id),
    FOREIGN KEY(event_id) REFERENCES task_scope_events(event_id)
);

CREATE TABLE task_scope_run_watermarks (
    run_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    durable_source_sequence INTEGER NOT NULL DEFAULT 0 CHECK (durable_source_sequence >= 0),
    terminal_source_sequence INTEGER CHECK (terminal_source_sequence > 0),
    updated_at REAL NOT NULL,
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id)
);

CREATE TABLE task_scope_terminal_gate_receipts (
    gate_receipt_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    run_id TEXT NOT NULL UNIQUE,
    terminal_source_sequence INTEGER NOT NULL CHECK (terminal_source_sequence > 0),
    durable_source_sequence INTEGER NOT NULL CHECK (durable_source_sequence >= terminal_source_sequence),
    created_at REAL NOT NULL,
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id)
);

-- All authority/history tables are append-only.  Only heads, watermarks and
-- projection_cache are mutable derived coordination state.
CREATE TRIGGER task_scope_archive_marker_no_update BEFORE UPDATE ON task_scope_archive_marker BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_archive_marker_no_delete BEFORE DELETE ON task_scope_archive_marker BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scopes_no_update BEFORE UPDATE ON task_scopes BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scopes_no_delete BEFORE DELETE ON task_scopes BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_events_no_update BEFORE UPDATE ON task_scope_events BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_events_no_delete BEFORE DELETE ON task_scope_events BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_steps_no_update BEFORE UPDATE ON task_scope_steps BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_steps_no_delete BEFORE DELETE ON task_scope_steps BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_evidence_links_no_update BEFORE UPDATE ON task_scope_evidence_links BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_evidence_links_no_delete BEFORE DELETE ON task_scope_evidence_links BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_decisions_no_update BEFORE UPDATE ON task_scope_mutation_decisions BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_decisions_no_delete BEFORE DELETE ON task_scope_mutation_decisions BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_attempts_no_update BEFORE UPDATE ON task_scope_mutation_attempts BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_attempts_no_delete BEFORE DELETE ON task_scope_mutation_attempts BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_canonical_no_update BEFORE UPDATE ON task_scope_canonical_revisions BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_canonical_no_delete BEFORE DELETE ON task_scope_canonical_revisions BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_checkpoints_no_update BEFORE UPDATE ON task_scope_checkpoints BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_checkpoints_no_delete BEFORE DELETE ON task_scope_checkpoints BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_projection_revisions_no_update BEFORE UPDATE ON task_scope_projection_revisions BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_projection_revisions_no_delete BEFORE DELETE ON task_scope_projection_revisions BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_projection_outbox_no_update BEFORE UPDATE ON task_scope_projection_outbox BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_projection_outbox_no_delete BEFORE DELETE ON task_scope_projection_outbox BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_search_outbox_no_update BEFORE UPDATE ON task_scope_search_outbox BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_search_outbox_no_delete BEFORE DELETE ON task_scope_search_outbox BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_ingest_receipts_no_update BEFORE UPDATE ON task_scope_execution_ingest_receipts BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_ingest_receipts_no_delete BEFORE DELETE ON task_scope_execution_ingest_receipts BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_terminal_gate_no_update BEFORE UPDATE ON task_scope_terminal_gate_receipts BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
CREATE TRIGGER task_scope_terminal_gate_no_delete BEFORE DELETE ON task_scope_terminal_gate_receipts BEGIN SELECT RAISE(ABORT, 'task_scope_append_only'); END;
