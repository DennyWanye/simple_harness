-- Session provider binding lifecycle v23.
-- A cleared binding remains as a tombstone row so every mutation advances a
-- durable epoch. Provider incarnation/revision prevent remove+re-add ABA.

ALTER TABLE code_session_provider
    ADD COLUMN provider_incarnation_id TEXT;

ALTER TABLE code_session_provider
    ADD COLUMN provider_config_revision INTEGER;

ALTER TABLE code_session_provider
    ADD COLUMN binding_epoch INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS provider_binding_reconcile_marker (
    singleton       INTEGER PRIMARY KEY CHECK (singleton = 1),
    registry_digest TEXT NOT NULL,
    completed_at    REAL NOT NULL
);
