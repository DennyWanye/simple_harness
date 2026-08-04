CREATE TABLE IF NOT EXISTS session_context_usage_history (
    sample_id          TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL,
    run_id             TEXT,
    request_id         TEXT,
    attempt_id         TEXT,
    event_type         TEXT NOT NULL,
    tokens_before      INTEGER,
    tokens_after       INTEGER NOT NULL,
    prompt_tokens      INTEGER NOT NULL,
    context_window     INTEGER NOT NULL,
    effective_ceiling  INTEGER NOT NULL,
    estimate_method    TEXT NOT NULL,
    metadata_json      TEXT NOT NULL,
    created_at         REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_context_usage_history_session_time
ON session_context_usage_history(session_id, created_at, sample_id);

CREATE INDEX IF NOT EXISTS idx_context_usage_history_run
ON session_context_usage_history(run_id, created_at)
WHERE run_id IS NOT NULL;

PRAGMA user_version=22;
