"""Frozen v1/v2 and current v3 schemas for product-owned state."""

from __future__ import annotations

from deskpet.capabilities.store import (
    CAPABILITY_SCHEMA_SQL as _CAPABILITY_SCHEMA_SQL,
    CAPABILITY_SCHEMA_V1_SQL as _CAPABILITY_SCHEMA_V1_SQL,
    CAPABILITY_SCHEMA_V2_STATEMENTS,
)

SCHEMA_VERSION = 3

# Capability v2 remains product-owned.  During detachment its final DDL is
# reused verbatim, except TaskGrant receives the new durable lifecycle states.
def _product_capability_schema(value: str) -> str:
    return value.replace(
        "status TEXT NOT NULL CHECK(status IN ('active','revoked'))",
        "status TEXT NOT NULL CHECK(status IN ('prepared','active','expired','revoked'))",
    ).replace(
        "    created_at REAL NOT NULL,\n    revoked_at REAL\n);\nCREATE INDEX IF NOT EXISTS idx_task_grants_root_active",
        "    creator_principal_id TEXT NOT NULL DEFAULT 'legacy',\n"
        "    prepared_at REAL,\n"
        "    activated_at REAL,\n"
        "    expires_at REAL,\n"
        "    created_at REAL NOT NULL,\n"
        "    revoked_at REAL\n);\nCREATE INDEX IF NOT EXISTS idx_task_grants_root_active",
    )


CAPABILITY_SCHEMA_V1_SQL = _product_capability_schema(_CAPABILITY_SCHEMA_V1_SQL)
CAPABILITY_SCHEMA_SQL = _product_capability_schema(_CAPABILITY_SCHEMA_SQL)

VERIFICATION_V3_COLUMNS_SQL = """
ALTER TABLE capability_skill_install_intents
ADD COLUMN verification_attempt_generation INTEGER NOT NULL DEFAULT 0
    CHECK(verification_attempt_generation>=0);
ALTER TABLE capability_skill_install_intents
ADD COLUMN current_verification_attempt_id TEXT;
ALTER TABLE capability_skill_install_intents
ADD COLUMN migrated_verification_provenance TEXT
    CHECK(migrated_verification_provenance IS NULL OR
          migrated_verification_provenance='legacy_v2');
"""

VERIFICATION_V3_SCHEMA_SQL = """
CREATE TABLE capability_skill_install_verification_attempts (
    attempt_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    attempt_generation INTEGER NOT NULL CHECK(attempt_generation>0),
    state_version INTEGER NOT NULL CHECK(state_version>0),
    status TEXT NOT NULL CHECK(status IN (
        'prepared','launching','running','terminal_succeeded','terminal_failed',
        'unknown','superseded'
    )),
    verifier_session_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    expected_run_id TEXT NOT NULL UNIQUE,
    actual_run_id TEXT UNIQUE,
    manager_operation_id TEXT NOT NULL,
    manager_receipt_hash TEXT NOT NULL,
    committed_set_stamp TEXT NOT NULL,
    project_scope_key TEXT NOT NULL,
    expected_member_set_stamp TEXT NOT NULL,
    run_catalog_content_stamp TEXT,
    terminal_event_id TEXT,
    terminal_event_hash TEXT,
    evidence_hash TEXT,
    superseded_by_attempt_id TEXT,
    error_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    terminal_at REAL,
    UNIQUE(intent_id,attempt_generation),
    FOREIGN KEY(intent_id) REFERENCES capability_skill_install_intents(intent_id)
        ON DELETE CASCADE,
    FOREIGN KEY(superseded_by_attempt_id)
        REFERENCES capability_skill_install_verification_attempts(attempt_id)
        ON DELETE RESTRICT
);
CREATE UNIQUE INDEX capability_skill_install_one_unresolved_attempt
ON capability_skill_install_verification_attempts(intent_id)
WHERE status IN ('prepared','launching','running','unknown');
CREATE TRIGGER capability_skill_install_current_attempt_update
BEFORE UPDATE OF current_verification_attempt_id
ON capability_skill_install_intents
WHEN NEW.current_verification_attempt_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM capability_skill_install_verification_attempts attempt
    WHERE attempt.attempt_id=NEW.current_verification_attempt_id
      AND attempt.intent_id=NEW.intent_id
)
BEGIN
    SELECT RAISE(ABORT,'current verification attempt differs');
END;
CREATE TRIGGER capability_skill_install_current_attempt_delete
BEFORE DELETE ON capability_skill_install_verification_attempts
WHEN EXISTS (
    SELECT 1 FROM capability_skill_install_intents intent
    WHERE intent.current_verification_attempt_id=OLD.attempt_id
)
BEGIN
    SELECT RAISE(ABORT,'current verification attempt is referenced');
END;

CREATE TABLE capability_skill_install_verification_attestations (
    attestation_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    attempt_id TEXT UNIQUE,
    provenance TEXT NOT NULL CHECK(provenance IN ('runtime_v3','legacy_v2')),
    runtime_proof_valid INTEGER NOT NULL CHECK(runtime_proof_valid IN (0,1)),
    verification_ref TEXT NOT NULL,
    evidence_hash TEXT,
    attestation_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(intent_id) REFERENCES capability_skill_install_intents(intent_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(attempt_id)
        REFERENCES capability_skill_install_verification_attempts(attempt_id)
        ON DELETE RESTRICT,
    CHECK(
        (provenance='runtime_v3' AND runtime_proof_valid=1 AND attempt_id IS NOT NULL
         AND evidence_hash IS NOT NULL)
        OR
        (provenance='legacy_v2' AND runtime_proof_valid=0 AND attempt_id IS NULL
         AND evidence_hash IS NULL)
    )
);
CREATE UNIQUE INDEX capability_skill_install_one_legacy_attestation
ON capability_skill_install_verification_attestations(intent_id)
WHERE provenance='legacy_v2';
"""

PRODUCT_SCHEMA_SQL = """
CREATE TABLE product_schema_meta (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL
);

CREATE TABLE product_schema_manifest (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_hash TEXT NOT NULL,
    tables_json TEXT NOT NULL
);

CREATE TABLE capability_host_operation_receipts (
    operation_key TEXT PRIMARY KEY,
    request_fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','settled')),
    result_json TEXT,
    result_hash TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    CHECK(
        (state='pending' AND result_json IS NULL AND result_hash IS NULL)
        OR
        (state='settled' AND result_json IS NOT NULL AND result_hash IS NOT NULL)
    )
);

CREATE TABLE authorization_sagas (
    authorization_id TEXT PRIMARY KEY,
    request_fingerprint TEXT NOT NULL,
    request_json TEXT NOT NULL,
    effect_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'prepared','decision_bound','effect_bound','handoff_committed',
        'settled','aborted','expired','revoked','quarantined','dispatch_unknown'
    )),
    version INTEGER NOT NULL,
    decision_sdk_receipt_hash TEXT,
    decision_host_receipt_hash TEXT,
    bound_decision_nonce TEXT,
    bound_decision_version INTEGER,
    effect_sdk_receipt_hash TEXT,
    effect_host_receipt_hash TEXT,
    handoff_sdk_receipt_hash TEXT,
    handoff_host_receipt_hash TEXT,
    outcome_hash TEXT,
    terminal_reason_hash TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE UNIQUE INDEX authorization_sagas_effect_identity
ON authorization_sagas(effect_id, call_id);
"""

SCHEMA_V1_PARTS = (
    CAPABILITY_SCHEMA_V1_SQL,
    *CAPABILITY_SCHEMA_V2_STATEMENTS,
    """
    UPDATE capability_schema_state
    SET schema_version=2, updated_at=CAST(strftime('%s','now') AS REAL)
    WHERE singleton_id=1 AND schema_version=1;
    """,
    PRODUCT_SCHEMA_SQL,
)

SCHEMA_V2_PARTS = (
    CAPABILITY_SCHEMA_SQL,
    *CAPABILITY_SCHEMA_V2_STATEMENTS,
    """
    UPDATE capability_schema_state
    SET schema_version=2, updated_at=CAST(strftime('%s','now') AS REAL)
    WHERE singleton_id=1 AND schema_version=1;
    """,
    PRODUCT_SCHEMA_SQL,
)

SCHEMA_V3_PARTS = (*SCHEMA_V2_PARTS, VERIFICATION_V3_COLUMNS_SQL, VERIFICATION_V3_SCHEMA_SQL)

__all__ = (
    "CAPABILITY_SCHEMA_SQL",
    "CAPABILITY_SCHEMA_V1_SQL",
    "PRODUCT_SCHEMA_SQL",
    "SCHEMA_V1_PARTS",
    "SCHEMA_V2_PARTS",
    "SCHEMA_V3_PARTS",
    "SCHEMA_VERSION",
)
