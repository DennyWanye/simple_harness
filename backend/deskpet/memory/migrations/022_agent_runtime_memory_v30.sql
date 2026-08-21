-- DeskPet Agent Runtime memory authority (user_version=30).
--
-- Product messages and their Memory projection intent are committed by the
-- same state.db transaction.  The immutable session binding is the only
-- authority used by the asynchronous dispatcher; it never consults ambient
-- profile state.

CREATE TABLE IF NOT EXISTS state_db_identity (
    singleton     INTEGER PRIMARY KEY CHECK (singleton = 1),
    instance_id   TEXT NOT NULL UNIQUE,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_users (
    user_id       TEXT PRIMARY KEY,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_user_bindings (
    session_id    TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    user_id       TEXT NOT NULL REFERENCES memory_users(user_id) ON DELETE RESTRICT,
    created_at    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_user_bindings_user
    ON memory_user_bindings(user_id, session_id);

CREATE TABLE IF NOT EXISTS product_memory_outbox (
    source_event_id       TEXT PRIMARY KEY,
    state_db_instance_id  TEXT NOT NULL,
    message_id            INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id               TEXT NOT NULL REFERENCES memory_users(user_id) ON DELETE RESTRICT,
    session_id            TEXT NOT NULL,
    role                  TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    memory_text           TEXT NOT NULL,
    payload_hash          TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'claimed', 'applied', 'dead_letter')),
    attempt               INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    claim_token           TEXT,
    lease_expires_at      REAL,
    next_attempt_at       REAL NOT NULL,
    last_error_code       TEXT,
    created_at            REAL NOT NULL,
    updated_at            REAL NOT NULL,
    applied_at            REAL,
    UNIQUE (message_id),
    FOREIGN KEY (session_id) REFERENCES memory_user_bindings(session_id) ON DELETE CASCADE,
    FOREIGN KEY (state_db_instance_id) REFERENCES state_db_identity(instance_id)
);

CREATE INDEX IF NOT EXISTS idx_product_memory_outbox_dispatch
    ON product_memory_outbox(status, next_attempt_at, lease_expires_at, created_at);
