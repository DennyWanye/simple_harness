CREATE TABLE IF NOT EXISTS sdk_context_public_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    root_run_id TEXT NOT NULL,
    snapshot_version INTEGER NOT NULL,
    snapshot_fingerprint TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    public_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(session_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_sdk_context_snapshot_session
ON sdk_context_public_snapshots(session_id, created_at, snapshot_id);

CREATE TABLE IF NOT EXISTS sdk_provider_attempt_audit (
    invocation_id TEXT PRIMARY KEY,
    settlement_version INTEGER NOT NULL,
    payload_hash TEXT NOT NULL,
    session_id TEXT NOT NULL,
    root_run_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    state TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    binding_epoch INTEGER NOT NULL,
    context_window INTEGER NOT NULL,
    effective_ceiling INTEGER NOT NULL,
    usage_available INTEGER NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    cache_tokens INTEGER,
    reasoning_tokens INTEGER,
    settled_at REAL NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sdk_provider_attempt_session
ON sdk_provider_attempt_audit(session_id, settled_at, invocation_id);

CREATE TABLE IF NOT EXISTS sdk_provider_projection_cursors (
    consumer_id TEXT PRIMARY KEY,
    settled_at REAL NOT NULL,
    invocation_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    updated_at REAL NOT NULL
);
