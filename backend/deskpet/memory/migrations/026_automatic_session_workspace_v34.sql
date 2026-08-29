-- Ordinary Session automatic workspace authority (user_version=34).
-- Transaction ownership belongs to migrator.py.

CREATE TABLE project_origins (
    project_id      TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE RESTRICT,
    project_origin  TEXT NOT NULL CHECK(project_origin IN ('user_selected','automatic_session_workspace'))
);

CREATE TABLE workspace_allocations (
    allocation_id       TEXT PRIMARY KEY,
    request_id          TEXT NOT NULL UNIQUE,
    intent_hash         TEXT NOT NULL,
    session_id          TEXT NOT NULL UNIQUE,
    project_id          TEXT,
    directory_path      TEXT NOT NULL UNIQUE,
    directory_identity  TEXT,
    owned_marker        TEXT NOT NULL,
    state               TEXT NOT NULL CHECK(state IN (
        'reserved','directory_created','completed','compensation_required','failed'
    )),
    error_code          TEXT,
    reconciliation_attempts INTEGER NOT NULL DEFAULT 0,
    last_reconciled_at  REAL,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    completed_at        REAL
);

CREATE INDEX idx_workspace_allocations_state
    ON workspace_allocations(state, updated_at, allocation_id);
