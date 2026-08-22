-- Official Harness/Memory SDK product bindings (user_version=31).

CREATE TABLE IF NOT EXISTS memory_identity_bindings (
    actor_id       TEXT PRIMARY KEY,
    household_id   TEXT NOT NULL UNIQUE,
    created_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_session_identities (
    session_id      TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    deployment_id   TEXT NOT NULL,
    household_id    TEXT NOT NULL,
    actor_id         TEXT NOT NULL REFERENCES memory_identity_bindings(actor_id) ON DELETE RESTRICT,
    created_at       REAL NOT NULL,
    UNIQUE (deployment_id, household_id, actor_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_session_actor
    ON memory_session_identities(actor_id, session_id);

CREATE TABLE IF NOT EXISTS sdk_context_sources (
    source_snapshot_ref TEXT PRIMARY KEY,
    payload_json        TEXT NOT NULL,
    payload_hash        TEXT NOT NULL,
    byte_count          INTEGER NOT NULL CHECK (byte_count > 0),
    item_count          INTEGER NOT NULL CHECK (item_count > 0),
    state               TEXT NOT NULL CHECK (state IN ('pending','staged','consumed')),
    ref_count           INTEGER NOT NULL DEFAULT 0 CHECK (ref_count >= 0),
    created_at          REAL NOT NULL,
    expires_at          REAL NOT NULL,
    updated_at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sdk_context_source_bindings (
    binding_id          TEXT PRIMARY KEY,
    source_snapshot_ref TEXT NOT NULL REFERENCES sdk_context_sources(source_snapshot_ref) ON DELETE RESTRICT,
    root_run_id         TEXT NOT NULL,
    continuation_id     TEXT,
    status              TEXT NOT NULL CHECK (status IN ('pending','claimed','staged','consumed')),
    claim_token         TEXT,
    lease_expires_at    REAL,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    UNIQUE(root_run_id, continuation_id)
);

CREATE INDEX IF NOT EXISTS idx_sdk_context_source_gc
    ON sdk_context_sources(state, expires_at, ref_count);
CREATE INDEX IF NOT EXISTS idx_sdk_context_binding_claim
    ON sdk_context_source_bindings(status, lease_expires_at, updated_at);
