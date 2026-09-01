-- Deterministic TaskScope projection source identity and bounded read artifacts.

CREATE TABLE task_scope_projection_source_heads (
    task_scope_id TEXT PRIMARY KEY,
    source_sequence INTEGER NOT NULL CHECK(source_sequence > 0),
    source_id TEXT NOT NULL UNIQUE,
    source_hash TEXT NOT NULL CHECK(length(source_hash) = 64),
    updated_at REAL NOT NULL,
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id)
);

CREATE TABLE task_scope_projection_sources (
    source_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    source_sequence INTEGER NOT NULL CHECK(source_sequence > 0),
    canonical_revision INTEGER NOT NULL CHECK(canonical_revision > 0),
    state_hash TEXT NOT NULL CHECK(length(state_hash) = 64),
    event_watermark INTEGER NOT NULL CHECK(event_watermark >= 0),
    event_prefix_root TEXT NOT NULL CHECK(length(event_prefix_root) = 64),
    checkpoint_sequence INTEGER NOT NULL CHECK(checkpoint_sequence >= 0),
    checkpoint_set_root TEXT NOT NULL CHECK(length(checkpoint_set_root) = 64),
    binding_set_revision INTEGER NOT NULL CHECK(binding_set_revision >= 0),
    binding_receipt_hash TEXT CHECK(binding_receipt_hash IS NULL OR length(binding_receipt_hash) = 64),
    renderer_contract_version TEXT NOT NULL,
    source_hash TEXT NOT NULL UNIQUE CHECK(length(source_hash) = 64),
    source_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(task_scope_id, source_sequence),
    FOREIGN KEY(task_scope_id, canonical_revision)
        REFERENCES task_scope_canonical_revisions(task_scope_id, revision)
);

CREATE TABLE task_scope_projection_source_outbox (
    outbox_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    source_id TEXT NOT NULL UNIQUE,
    covered_from_sequence INTEGER NOT NULL CHECK(covered_from_sequence > 0),
    covered_through_sequence INTEGER NOT NULL CHECK(covered_through_sequence >= covered_from_sequence),
    source_hash TEXT NOT NULL CHECK(length(source_hash) = 64),
    created_at REAL NOT NULL,
    FOREIGN KEY(source_id) REFERENCES task_scope_projection_sources(source_id)
);

CREATE TABLE task_scope_read_blocks (
    block_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    view_kind TEXT NOT NULL CHECK(view_kind IN ('README','PLAN','STATUS','DECISIONS','RESUME','EVIDENCE')),
    block_kind TEXT NOT NULL CHECK(block_kind IN ('chunk','leaf','group','index')),
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
    content BLOB NOT NULL CHECK(length(content) <= 32768),
    created_at REAL NOT NULL,
    UNIQUE(source_id, view_kind, content_sha256),
    FOREIGN KEY(source_id) REFERENCES task_scope_projection_sources(source_id)
);

CREATE TABLE task_scope_read_view_revisions (
    view_revision_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    task_scope_id TEXT NOT NULL,
    view_kind TEXT NOT NULL CHECK(view_kind IN ('README','PLAN','STATUS','DECISIONS','RESUME','EVIDENCE')),
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
    content BLOB NOT NULL,
    root_block_id TEXT,
    block_count INTEGER NOT NULL CHECK(block_count >= 0),
    receipt_hash TEXT NOT NULL UNIQUE CHECK(length(receipt_hash) = 64),
    receipt_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(source_id, view_kind),
    FOREIGN KEY(source_id) REFERENCES task_scope_projection_sources(source_id)
);

CREATE TABLE task_scope_projection_materialization_receipts (
    receipt_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL UNIQUE,
    source_hash TEXT NOT NULL CHECK(length(source_hash) = 64),
    renderer_contract_version TEXT NOT NULL,
    projection_root_hash TEXT NOT NULL CHECK(length(projection_root_hash) = 64),
    receipt_hash TEXT NOT NULL UNIQUE CHECK(length(receipt_hash) = 64),
    receipt_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(source_id) REFERENCES task_scope_projection_sources(source_id)
);

CREATE TRIGGER task_scope_projection_sources_no_update BEFORE UPDATE ON task_scope_projection_sources BEGIN SELECT RAISE(ABORT,'task_scope_projection_append_only'); END;
CREATE TRIGGER task_scope_projection_sources_no_delete BEFORE DELETE ON task_scope_projection_sources BEGIN SELECT RAISE(ABORT,'task_scope_projection_append_only'); END;
CREATE TRIGGER task_scope_projection_source_outbox_no_update BEFORE UPDATE ON task_scope_projection_source_outbox BEGIN SELECT RAISE(ABORT,'task_scope_projection_append_only'); END;
CREATE TRIGGER task_scope_projection_source_outbox_no_delete BEFORE DELETE ON task_scope_projection_source_outbox BEGIN SELECT RAISE(ABORT,'task_scope_projection_append_only'); END;
CREATE TRIGGER task_scope_read_blocks_no_update BEFORE UPDATE ON task_scope_read_blocks BEGIN SELECT RAISE(ABORT,'task_scope_projection_content_addressed'); END;
CREATE TRIGGER task_scope_read_views_no_update BEFORE UPDATE ON task_scope_read_view_revisions BEGIN SELECT RAISE(ABORT,'task_scope_projection_append_only'); END;
CREATE TRIGGER task_scope_read_views_no_delete BEFORE DELETE ON task_scope_read_view_revisions BEGIN SELECT RAISE(ABORT,'task_scope_projection_append_only'); END;
CREATE TRIGGER task_scope_projection_receipts_no_update BEFORE UPDATE ON task_scope_projection_materialization_receipts BEGIN SELECT RAISE(ABORT,'task_scope_projection_append_only'); END;
CREATE TRIGGER task_scope_projection_receipts_no_delete BEFORE DELETE ON task_scope_projection_materialization_receipts BEGIN SELECT RAISE(ABORT,'task_scope_projection_append_only'); END;
CREATE TRIGGER task_scope_projection_source_heads_no_delete BEFORE DELETE ON task_scope_projection_source_heads BEGIN SELECT RAISE(ABORT,'task_scope_projection_head_required'); END;

