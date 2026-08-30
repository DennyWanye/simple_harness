-- Human Memory Program v1 is deliberately a fresh-data epoch.
-- The normal state.db migrator does not apply this file.  It is admitted only
-- by initialize_human_memory_program_state_db() after proving that the data
-- directory started empty.

CREATE TABLE human_memory_program_marker (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    format_epoch TEXT NOT NULL CHECK (format_epoch = 'human-memory-v1'),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    migration_id TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL CHECK (length(migration_sha256) = 64),
    initialized_at REAL NOT NULL
);

CREATE TABLE human_memory_primary_conversations (
    primary_conversation_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    writable INTEGER NOT NULL DEFAULT 1 CHECK (writable = 1),
    created_at REAL NOT NULL
);

CREATE UNIQUE INDEX uq_human_memory_writable_primary_per_subject
ON human_memory_primary_conversations(subject)
WHERE writable = 1;

CREATE TABLE human_memory_init_receipts (
    receipt_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL UNIQUE,
    primary_conversation_id TEXT NOT NULL UNIQUE,
    format_epoch TEXT NOT NULL CHECK (format_epoch = 'human-memory-v1'),
    marker_sha256 TEXT NOT NULL CHECK (length(marker_sha256) = 64),
    receipt_sha256 TEXT NOT NULL CHECK (length(receipt_sha256) = 64),
    created_at REAL NOT NULL,
    FOREIGN KEY (primary_conversation_id)
        REFERENCES human_memory_primary_conversations(primary_conversation_id)
);

CREATE TABLE human_memory_sanitization_receipts (
    receipt_id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL UNIQUE,
    subject TEXT NOT NULL,
    run_id TEXT NOT NULL,
    envelope_sha256 TEXT NOT NULL CHECK (length(envelope_sha256) = 64),
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    sanitized_sha256 TEXT NOT NULL CHECK (length(sanitized_sha256) = 64),
    filter_policy_version TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL CHECK (length(receipt_sha256) = 64),
    receipt_json TEXT NOT NULL,
    admitted_at REAL NOT NULL,
    committed_at REAL NOT NULL
);

CREATE TABLE human_memory_evidence (
    evidence_id TEXT PRIMARY KEY,
    primary_conversation_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN (
        'user_message', 'assistant_message', 'tool_result',
        'provider_record', 'runtime_event', 'typed_observation'
    )),
    source_ref TEXT NOT NULL,
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    sanitized_sha256 TEXT NOT NULL CHECK (length(sanitized_sha256) = 64),
    envelope_sha256 TEXT NOT NULL CHECK (length(envelope_sha256) = 64),
    receipt_id TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    occurred_at REAL NOT NULL,
    committed_at REAL NOT NULL,
    FOREIGN KEY (primary_conversation_id)
        REFERENCES human_memory_primary_conversations(primary_conversation_id),
    FOREIGN KEY (receipt_id)
        REFERENCES human_memory_sanitization_receipts(receipt_id)
);

CREATE INDEX idx_human_memory_evidence_conversation_order
ON human_memory_evidence(primary_conversation_id, committed_at, evidence_id);

-- The Human Memory raw epoch has no physical mutation path.  Corrections and
-- forgetting are represented by later evidence/relationship rows in later
-- slices, never by changing these records.
CREATE TRIGGER human_memory_program_marker_no_update
BEFORE UPDATE ON human_memory_program_marker BEGIN
    SELECT RAISE(ABORT, 'human_memory_append_only');
END;
CREATE TRIGGER human_memory_program_marker_no_delete
BEFORE DELETE ON human_memory_program_marker BEGIN
    SELECT RAISE(ABORT, 'human_memory_append_only');
END;
CREATE TRIGGER human_memory_primary_no_update
BEFORE UPDATE ON human_memory_primary_conversations BEGIN
    SELECT RAISE(ABORT, 'human_memory_append_only');
END;
CREATE TRIGGER human_memory_primary_no_delete
BEFORE DELETE ON human_memory_primary_conversations BEGIN
    SELECT RAISE(ABORT, 'human_memory_append_only');
END;
CREATE TRIGGER human_memory_init_receipts_no_update
BEFORE UPDATE ON human_memory_init_receipts BEGIN
    SELECT RAISE(ABORT, 'human_memory_append_only');
END;
CREATE TRIGGER human_memory_init_receipts_no_delete
BEFORE DELETE ON human_memory_init_receipts BEGIN
    SELECT RAISE(ABORT, 'human_memory_append_only');
END;
CREATE TRIGGER human_memory_sanitization_receipts_no_update
BEFORE UPDATE ON human_memory_sanitization_receipts BEGIN
    SELECT RAISE(ABORT, 'human_memory_append_only');
END;
CREATE TRIGGER human_memory_sanitization_receipts_no_delete
BEFORE DELETE ON human_memory_sanitization_receipts BEGIN
    SELECT RAISE(ABORT, 'human_memory_append_only');
END;
CREATE TRIGGER human_memory_evidence_no_update
BEFORE UPDATE ON human_memory_evidence BEGIN
    SELECT RAISE(ABORT, 'human_memory_append_only');
END;
CREATE TRIGGER human_memory_evidence_no_delete
BEFORE DELETE ON human_memory_evidence BEGIN
    SELECT RAISE(ABORT, 'human_memory_append_only');
END;
