-- Immutable per-Run context/route authority ledger (S5a).
-- Rows record Host authority facts (route decisions, no-recall decisions,
-- per-turn Context snapshot receipts, route tool invocation lineage). They
-- never grant Provider, Tool, SDK start, signal, or effect authority.
-- occurrence_presented is created with its full S5b column set but stays
-- empty in S5a: the reconcile gate only reads membership and never advances.

CREATE TABLE context_route_marker (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    format_epoch TEXT NOT NULL CHECK(format_epoch='human-memory-v1'),
    schema_version INTEGER NOT NULL CHECK(schema_version=1),
    migration_id TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL CHECK(length(migration_sha256)=64),
    initialized_at REAL NOT NULL
);

CREATE TABLE context_route_decisions (
    decision_id TEXT PRIMARY KEY,
    sdk_run_id TEXT NOT NULL,
    provider_turn_ordinal INTEGER NOT NULL CHECK(provider_turn_ordinal > 0),
    route TEXT NOT NULL CHECK(route IN (
        'direct_standalone','memory_standalone','continue_active','resume_existing','create_new'
    )),
    origin TEXT NOT NULL CHECK(origin IN ('context_tool','host_initial','no_recall')),
    task_scope_id TEXT,
    binding_set_revision INTEGER CHECK(binding_set_revision IS NULL OR binding_set_revision > 0),
    binding_set_receipt_id TEXT,
    binding_set_receipt_hash TEXT CHECK(binding_set_receipt_hash IS NULL OR length(binding_set_receipt_hash)=64),
    recall_refs_json TEXT NOT NULL,
    receipt_id TEXT NOT NULL UNIQUE,
    receipt_hash TEXT NOT NULL CHECK(length(receipt_hash)=64),
    receipt_json TEXT NOT NULL,
    raw_call_id TEXT,
    effect_id TEXT,
    request_fingerprint TEXT CHECK(request_fingerprint IS NULL OR length(request_fingerprint)=64),
    idempotency_key TEXT NOT NULL,
    decision_hash TEXT NOT NULL UNIQUE CHECK(length(decision_hash)=64),
    recorded_at REAL NOT NULL,
    UNIQUE(sdk_run_id,idempotency_key),
    CHECK((binding_set_receipt_id IS NULL) = (binding_set_receipt_hash IS NULL))
);

CREATE TABLE run_context_snapshot_receipts (
    snapshot_id TEXT PRIMARY KEY,
    sdk_run_id TEXT NOT NULL,
    provider_turn_ordinal INTEGER NOT NULL CHECK(provider_turn_ordinal > 0),
    prior_context_revision INTEGER NOT NULL CHECK(prior_context_revision >= 0),
    snapshot_revision INTEGER NOT NULL CHECK(snapshot_revision > 0),
    source_revisions_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
    expected_request_fingerprint TEXT NOT NULL CHECK(length(expected_request_fingerprint)=64),
    receipt_hash TEXT NOT NULL CHECK(length(receipt_hash)=64),
    recorded_at REAL NOT NULL,
    UNIQUE(sdk_run_id,snapshot_revision)
);

CREATE TABLE context_route_tool_invocations (
    invocation_id TEXT PRIMARY KEY,
    sdk_run_id TEXT NOT NULL,
    raw_call_id TEXT NOT NULL,
    effect_id TEXT NOT NULL,
    proposal_hash TEXT NOT NULL CHECK(length(proposal_hash)=64),
    verdict TEXT NOT NULL CHECK(verdict IN ('accepted','rejected','clarification')),
    decision_id TEXT,
    detail_json TEXT NOT NULL,
    invocation_hash TEXT NOT NULL UNIQUE CHECK(length(invocation_hash)=64),
    recorded_at REAL NOT NULL,
    UNIQUE(sdk_run_id,effect_id),
    FOREIGN KEY(decision_id) REFERENCES context_route_decisions(decision_id)
);

CREATE TABLE occurrence_presented (
    occurrence_key TEXT PRIMARY KEY CHECK(length(occurrence_key)=64),
    memory_id TEXT NOT NULL,
    prospective_revision INTEGER NOT NULL CHECK(prospective_revision > 0),
    presented_at REAL,
    presented_run_id TEXT,
    settled_at REAL,
    settled_reason TEXT,
    CHECK((presented_at IS NULL) = (presented_run_id IS NULL)),
    CHECK(settled_at IS NULL OR presented_at IS NOT NULL),
    CHECK((settled_at IS NULL) = (settled_reason IS NULL))
);

CREATE TRIGGER context_route_marker_no_update BEFORE UPDATE ON context_route_marker BEGIN SELECT RAISE(ABORT,'context_route_append_only'); END;
CREATE TRIGGER context_route_marker_no_delete BEFORE DELETE ON context_route_marker BEGIN SELECT RAISE(ABORT,'context_route_append_only'); END;
CREATE TRIGGER context_route_decisions_no_update BEFORE UPDATE ON context_route_decisions BEGIN SELECT RAISE(ABORT,'context_route_append_only'); END;
CREATE TRIGGER context_route_decisions_no_delete BEFORE DELETE ON context_route_decisions BEGIN SELECT RAISE(ABORT,'context_route_append_only'); END;
CREATE TRIGGER run_context_snapshot_receipts_no_update BEFORE UPDATE ON run_context_snapshot_receipts BEGIN SELECT RAISE(ABORT,'context_route_append_only'); END;
CREATE TRIGGER run_context_snapshot_receipts_no_delete BEFORE DELETE ON run_context_snapshot_receipts BEGIN SELECT RAISE(ABORT,'context_route_append_only'); END;
CREATE TRIGGER context_route_tool_invocations_no_update BEFORE UPDATE ON context_route_tool_invocations BEGIN SELECT RAISE(ABORT,'context_route_append_only'); END;
CREATE TRIGGER context_route_tool_invocations_no_delete BEFORE DELETE ON context_route_tool_invocations BEGIN SELECT RAISE(ABORT,'context_route_append_only'); END;
CREATE TRIGGER occurrence_presented_no_delete BEFORE DELETE ON occurrence_presented BEGIN SELECT RAISE(ABORT,'context_route_append_only'); END;
CREATE TRIGGER occurrence_presented_guard BEFORE UPDATE ON occurrence_presented
BEGIN
    SELECT CASE
        WHEN NEW.occurrence_key <> OLD.occurrence_key
             OR NEW.memory_id <> OLD.memory_id
             OR NEW.prospective_revision <> OLD.prospective_revision
        THEN RAISE(ABORT,'occurrence_presented_identity_immutable')
        WHEN OLD.presented_at IS NOT NULL AND (
             NEW.presented_at IS NOT OLD.presented_at
             OR NEW.presented_run_id IS NOT OLD.presented_run_id)
        THEN RAISE(ABORT,'occurrence_presented_monotonic')
        WHEN OLD.settled_at IS NOT NULL AND (
             NEW.settled_at IS NOT OLD.settled_at
             OR NEW.settled_reason IS NOT OLD.settled_reason)
        THEN RAISE(ABORT,'occurrence_presented_monotonic')
    END;
END;
