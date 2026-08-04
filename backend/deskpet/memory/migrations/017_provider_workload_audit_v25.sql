CREATE TABLE IF NOT EXISTS provider_workload_audit (
    stable_call_id TEXT PRIMARY KEY,
    workload_class TEXT NOT NULL,
    callsite_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    provider_incarnation_id TEXT NOT NULL,
    model TEXT NOT NULL,
    config_revision INTEGER NOT NULL CHECK(config_revision >= 1),
    session_id TEXT,
    root_run_id TEXT,
    detached INTEGER NOT NULL CHECK(detached IN (0, 1)),
    owner_policy TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
    error_class TEXT,
    breaker_transition TEXT,
    injection_ref TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_provider_workload_audit_root
    ON provider_workload_audit(session_id, root_run_id, created_at, stable_call_id);

CREATE INDEX IF NOT EXISTS idx_provider_workload_audit_retention
    ON provider_workload_audit(created_at, stable_call_id);
