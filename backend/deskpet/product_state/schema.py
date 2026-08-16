"""Schema v1 for the product-owned state database."""

from __future__ import annotations

from deskpet.capabilities.store import (
    CAPABILITY_SCHEMA_SQL as _CAPABILITY_SCHEMA_SQL,
    CAPABILITY_SCHEMA_V2_STATEMENTS,
)

SCHEMA_VERSION = 1

# Capability v2 remains product-owned.  During detachment its final DDL is
# reused verbatim, except TaskGrant receives the new durable lifecycle states.
CAPABILITY_SCHEMA_SQL = _CAPABILITY_SCHEMA_SQL.replace(
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
    CAPABILITY_SCHEMA_SQL,
    *CAPABILITY_SCHEMA_V2_STATEMENTS,
    """
    UPDATE capability_schema_state
    SET schema_version=2, updated_at=CAST(strftime('%s','now') AS REAL)
    WHERE singleton_id=1 AND schema_version=1;
    """,
    PRODUCT_SCHEMA_SQL,
)

__all__ = ("SCHEMA_V1_PARTS", "SCHEMA_VERSION")
