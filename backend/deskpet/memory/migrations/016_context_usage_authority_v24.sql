ALTER TABLE session_context_usage_history ADD COLUMN source_event_id TEXT;
ALTER TABLE session_context_usage_history ADD COLUMN payload_hash TEXT;
ALTER TABLE session_context_usage_history ADD COLUMN based_on_sample_id TEXT;
ALTER TABLE session_context_usage_history ADD COLUMN binding_epoch INTEGER NOT NULL DEFAULT 0;
ALTER TABLE session_context_usage_history ADD COLUMN provider_id TEXT;
ALTER TABLE session_context_usage_history ADD COLUMN model_id TEXT;
ALTER TABLE session_context_usage_history ADD COLUMN completion_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE session_context_usage_history ADD COLUMN cached_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE session_context_usage_history ADD COLUMN completed_at REAL;

UPDATE session_context_usage_history
SET source_event_id = 'legacy:' || sample_id,
    payload_hash = 'legacy:' || sample_id,
    completed_at = created_at
WHERE source_event_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_context_usage_history_source
ON session_context_usage_history(session_id, source_event_id);

CREATE TABLE IF NOT EXISTS session_context_usage_state_v2 (
    session_id          TEXT PRIMARY KEY,
    source              TEXT NOT NULL CHECK(source IN ('measured','compacted','binding_only')),
    sample_id           TEXT,
    state_version       INTEGER NOT NULL,
    binding_epoch       INTEGER NOT NULL,
    availability        TEXT NOT NULL CHECK(availability IN ('available','unavailable','unknown')),
    provider_id         TEXT,
    model_id            TEXT,
    context_window      INTEGER NOT NULL,
    tokens              INTEGER NOT NULL,
    effective_ceiling   INTEGER NOT NULL,
    completion_tokens   INTEGER NOT NULL,
    cached_tokens       INTEGER NOT NULL,
    based_on_sample_id  TEXT,
    source_event_id     TEXT,
    source_completed_at REAL NOT NULL,
    has_measurement     INTEGER NOT NULL CHECK(has_measurement IN (0,1)),
    legacy_incomplete   INTEGER NOT NULL CHECK(legacy_incomplete IN (0,1)),
    updated_at          REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_context_usage_state_session_version
ON session_context_usage_state_v2(session_id, state_version);
