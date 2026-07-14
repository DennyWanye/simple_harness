-- 010_context_os_v18.sql -- Context OS durable derived state
--
-- These tables are intentionally created only by the formal v18 migration.
-- Runtime stores must never lazily create or repair them.

CREATE TABLE session_context_snapshots (
    session_id TEXT NOT NULL,
    task_scope_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    source_revisions_json TEXT NOT NULL DEFAULT '{}',
    objective TEXT NOT NULL DEFAULT '',
    decisions_json TEXT NOT NULL DEFAULT '[]',
    completed_json TEXT NOT NULL DEFAULT '[]',
    pending_json TEXT NOT NULL DEFAULT '[]',
    artifacts_json TEXT NOT NULL DEFAULT '[]',
    blockers_json TEXT NOT NULL DEFAULT '[]',
    narrative_summary TEXT NOT NULL DEFAULT '',
    last_prepared_toolset_json TEXT NOT NULL DEFAULT '{}',
    tool_policy_fingerprint TEXT,
    tool_registry_revision INTEGER,
    last_compaction_cycle_id TEXT,
    snapshot_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, task_scope_id),
    CHECK (revision >= 1)
);

CREATE TABLE session_context_segments (
    segment_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    level INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('raw_index', 'summary')),
    first_message_id INTEGER NOT NULL,
    last_message_id INTEGER NOT NULL,
    message_count INTEGER NOT NULL,
    source_hash TEXT NOT NULL,
    child_segment_ids_json TEXT NOT NULL DEFAULT '[]',
    summary_text TEXT NOT NULL DEFAULT '',
    token_estimates_json TEXT NOT NULL DEFAULT '{}',
    provider_id TEXT,
    model_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'valid',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(session_id, level, first_message_id, last_message_id, source_hash),
    CHECK (level >= 0),
    CHECK (first_message_id <= last_message_id),
    CHECK (message_count > 0),
    CHECK (revision >= 1)
);

CREATE INDEX idx_context_segments_cover
    ON session_context_segments(
        session_id, status, first_message_id, last_message_id, level
    );
