-- S5b v46: effect closure / Harness evidence reservations / Host↔Memory async
-- face (design-freeze §5, all seven tables created once in Task 2; later Tasks
-- only write them).  Every table is append-only; the three mutable-state
-- tables (reservations, ingestion outbox, invocation attempts) carry a
-- monotonic state guard instead of free UPDATE.  Rows record Host facts and
-- never grant Provider, Tool, SDK start, signal, or effect authority.

CREATE TABLE effect_closure_marker (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    format_epoch TEXT NOT NULL CHECK(format_epoch='human-memory-v1'),
    schema_version INTEGER NOT NULL CHECK(schema_version=1),
    migration_id TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL CHECK(length(migration_sha256)=64),
    initialized_at REAL NOT NULL
);

-- Semantic closure receipts: one per (Run, watermark, outcome).  `pending`
-- never clears dirt; a later receipt for the same scope may follow it.
CREATE TABLE task_scope_closure_receipts (
    receipt_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    sdk_run_id TEXT NOT NULL,
    host_run_id TEXT NOT NULL,
    closure_watermark INTEGER NOT NULL CHECK(closure_watermark >= 0),
    outcome TEXT NOT NULL CHECK(outcome IN ('mutate','no_mutation','pending')),
    plan_id TEXT,
    reason_code TEXT NOT NULL,
    attempt_id TEXT,
    created_at REAL NOT NULL,
    UNIQUE(sdk_run_id, closure_watermark, outcome),
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id)
);
CREATE INDEX idx_task_scope_closure_receipts_scope
ON task_scope_closure_receipts(task_scope_id, closure_watermark, created_at);

-- Harness evidence reservations (§3): a source_sequence is allocated before
-- the physical action; the terminal observer is the only drainer.
CREATE TABLE harness_evidence_reservations (
    reservation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_scope_id TEXT NOT NULL,
    source_sequence INTEGER NOT NULL CHECK(source_sequence > 0),
    source_event_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN (
        'provider_invocation','tool_invocation','context_snapshot','route_decision','run_terminal'
    )),
    status TEXT NOT NULL CHECK(status IN ('reserved','ingested','abandoned')),
    reserved_at REAL NOT NULL,
    resolved_at REAL,
    -- Task 2 review F-2: the Tool name is recorded at reservation time so an
    -- abandoned PROJECT_EFFECT (SDK ledger non-terminal at Run terminal) can be
    -- tombstoned with its effect class and still count as material dirt.
    tool_name TEXT,
    UNIQUE(run_id, source_sequence),
    CHECK((status='reserved') = (resolved_at IS NULL)),
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id)
);
CREATE INDEX idx_harness_evidence_reservations_run
ON harness_evidence_reservations(run_id, status, source_sequence);

-- Terminal-commit Memory ingestion outbox (Task 4 writes it).
CREATE TABLE memory_ingestion_outbox (
    outbox_id TEXT PRIMARY KEY,
    host_run_id TEXT NOT NULL,
    sdk_run_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    envelope_hash TEXT NOT NULL CHECK(length(envelope_hash)=64),
    model_config_hash TEXT NOT NULL CHECK(length(model_config_hash)=64),
    analysis_lineage_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','claimed','delivered','dead_letter')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    lease_owner TEXT,
    lease_expires_at REAL,
    receipt_json TEXT,
    last_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(sdk_run_id, turn_id),
    CHECK((lease_owner IS NULL) = (lease_expires_at IS NULL))
);
CREATE INDEX idx_memory_ingestion_outbox_state
ON memory_ingestion_outbox(state, lease_expires_at, created_at);

CREATE TABLE memory_ingestion_evidence_links (
    outbox_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    PRIMARY KEY(outbox_id, evidence_id),
    FOREIGN KEY(outbox_id) REFERENCES memory_ingestion_outbox(outbox_id)
);

-- Run-bound post-turn invocation attempts (closure / analysis), five-state
-- monotonic ledger (§6; Task 3 / Task 4 write it).
CREATE TABLE post_turn_invocation_attempts (
    attempt_id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL CHECK(purpose IN ('closure','analysis')),
    host_run_id TEXT NOT NULL,
    sdk_run_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation > 0),
    task_scope_id TEXT,
    closure_watermark INTEGER CHECK(closure_watermark IS NULL OR closure_watermark >= 0),
    request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
    attempt_ordinal INTEGER NOT NULL CHECK(attempt_ordinal > 0),
    evidence_set_key TEXT NOT NULL CHECK(length(evidence_set_key)=64),
    status TEXT NOT NULL CHECK(status IN ('reserved','handed_off','succeeded','failed','unknown')),
    unknown_class TEXT CHECK(unknown_class IS NULL OR unknown_class IN ('not_sent','sent_unknown','sent_confirmed')),
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_config_hash TEXT NOT NULL CHECK(length(model_config_hash)=64),
    provider_request_id TEXT,
    result_hash TEXT CHECK(result_hash IS NULL OR length(result_hash)=64),
    plan_id TEXT,
    reserved_at REAL NOT NULL,
    handed_off_at REAL,
    settled_at REAL,
    reason_code TEXT,
    -- Task 4: the durable analysis result envelope of a succeeded attempt, so a
    -- Memory reclaim after a Host crash replays the same delivery (zero calls).
    result_envelope_json TEXT,
    UNIQUE(request_hash, attempt_ordinal)
);
CREATE INDEX idx_post_turn_invocation_attempts_run
ON post_turn_invocation_attempts(sdk_run_id, purpose, status);

CREATE TABLE post_turn_invocation_members (
    attempt_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    run_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    PRIMARY KEY(attempt_id, evidence_id),
    FOREIGN KEY(attempt_id) REFERENCES post_turn_invocation_attempts(attempt_id)
);
CREATE INDEX idx_post_turn_invocation_members_lookup
ON post_turn_invocation_members(subject, run_id, evidence_id);

-- EffectGate sticky rejection memo per (Run, route receipt) (§4 step 2; Task 6).
CREATE TABLE effect_gate_rejections (
    rejection_id TEXT PRIMARY KEY,
    sdk_run_id TEXT NOT NULL,
    route_receipt_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(sdk_run_id, route_receipt_id)
);

-- Host pre-admission rejections of model payloads (Task 5).
CREATE TABLE host_pre_admission_audit (
    audit_id TEXT PRIMARY KEY,
    sdk_run_id TEXT,
    payload_kind TEXT NOT NULL CHECK(payload_kind IN ('context_route','task_scope_update','analysis_result')),
    reason_code TEXT NOT NULL,
    payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
    created_at REAL NOT NULL
);

-- Append-only triggers.
CREATE TRIGGER effect_closure_marker_no_update BEFORE UPDATE ON effect_closure_marker BEGIN SELECT RAISE(ABORT,'effect_closure_append_only'); END;
CREATE TRIGGER effect_closure_marker_no_delete BEFORE DELETE ON effect_closure_marker BEGIN SELECT RAISE(ABORT,'effect_closure_append_only'); END;
CREATE TRIGGER task_scope_closure_receipts_no_update BEFORE UPDATE ON task_scope_closure_receipts BEGIN SELECT RAISE(ABORT,'effect_closure_append_only'); END;
CREATE TRIGGER task_scope_closure_receipts_no_delete BEFORE DELETE ON task_scope_closure_receipts BEGIN SELECT RAISE(ABORT,'effect_closure_append_only'); END;
CREATE TRIGGER harness_evidence_reservations_no_delete BEFORE DELETE ON harness_evidence_reservations BEGIN SELECT RAISE(ABORT,'effect_closure_append_only'); END;
CREATE TRIGGER memory_ingestion_outbox_no_delete BEFORE DELETE ON memory_ingestion_outbox BEGIN SELECT RAISE(ABORT,'effect_closure_append_only'); END;
CREATE TRIGGER memory_ingestion_evidence_links_no_update BEFORE UPDATE ON memory_ingestion_evidence_links BEGIN SELECT RAISE(ABORT,'effect_closure_append_only'); END;
CREATE TRIGGER memory_ingestion_evidence_links_no_delete BEFORE DELETE ON memory_ingestion_evidence_links BEGIN SELECT RAISE(ABORT,'effect_closure_append_only'); END;
CREATE TRIGGER post_turn_invocation_attempts_no_delete BEFORE DELETE ON post_turn_invocation_attempts BEGIN SELECT RAISE(ABORT,'effect_closure_append_only'); END;
CREATE TRIGGER post_turn_invocation_members_no_update BEFORE UPDATE ON post_turn_invocation_members BEGIN SELECT RAISE(ABORT,'effect_closure_append_only'); END;
CREATE TRIGGER post_turn_invocation_members_no_delete BEFORE DELETE ON post_turn_invocation_members BEGIN SELECT RAISE(ABORT,'effect_closure_append_only'); END;
CREATE TRIGGER effect_gate_rejections_no_update BEFORE UPDATE ON effect_gate_rejections BEGIN SELECT RAISE(ABORT,'effect_closure_append_only'); END;
CREATE TRIGGER effect_gate_rejections_no_delete BEFORE DELETE ON effect_gate_rejections BEGIN SELECT RAISE(ABORT,'effect_closure_append_only'); END;
CREATE TRIGGER host_pre_admission_audit_no_update BEFORE UPDATE ON host_pre_admission_audit BEGIN SELECT RAISE(ABORT,'effect_closure_append_only'); END;
CREATE TRIGGER host_pre_admission_audit_no_delete BEFORE DELETE ON host_pre_admission_audit BEGIN SELECT RAISE(ABORT,'effect_closure_append_only'); END;

-- Monotonic state guards (identity immutable; status only moves forward).
CREATE TRIGGER harness_evidence_reservations_guard BEFORE UPDATE ON harness_evidence_reservations
BEGIN
    SELECT CASE
        WHEN NEW.reservation_id <> OLD.reservation_id
             OR NEW.run_id <> OLD.run_id
             OR NEW.task_scope_id <> OLD.task_scope_id
             OR NEW.source_sequence <> OLD.source_sequence
             OR NEW.source_event_id <> OLD.source_event_id
             OR NEW.kind <> OLD.kind
             OR NEW.reserved_at <> OLD.reserved_at
             OR NEW.tool_name IS NOT OLD.tool_name
        THEN RAISE(ABORT,'harness_evidence_reservation_identity_immutable')
        WHEN OLD.status <> 'reserved' AND (
             NEW.status <> OLD.status OR NEW.resolved_at IS NOT OLD.resolved_at)
        THEN RAISE(ABORT,'harness_evidence_reservation_monotonic')
        WHEN OLD.status = 'reserved' AND NEW.status NOT IN ('ingested','abandoned')
        THEN RAISE(ABORT,'harness_evidence_reservation_monotonic')
    END;
END;

CREATE TRIGGER memory_ingestion_outbox_guard BEFORE UPDATE ON memory_ingestion_outbox
BEGIN
    SELECT CASE
        WHEN NEW.outbox_id <> OLD.outbox_id
             OR NEW.host_run_id <> OLD.host_run_id
             OR NEW.sdk_run_id <> OLD.sdk_run_id
             OR NEW.turn_id <> OLD.turn_id
             OR NEW.subject <> OLD.subject
             OR NEW.evidence_ids_json <> OLD.evidence_ids_json
             OR NEW.envelope_hash <> OLD.envelope_hash
             OR NEW.model_config_hash <> OLD.model_config_hash
             OR NEW.analysis_lineage_json <> OLD.analysis_lineage_json
             OR NEW.created_at <> OLD.created_at
        THEN RAISE(ABORT,'memory_ingestion_outbox_identity_immutable')
        WHEN NEW.state <> OLD.state AND NOT (
             (OLD.state = 'pending' AND NEW.state IN ('claimed','dead_letter'))
             OR (OLD.state = 'claimed' AND NEW.state IN ('pending','delivered','dead_letter')))
        THEN RAISE(ABORT,'memory_ingestion_outbox_monotonic')
        -- Task 6 (Task 2 review F-5): a terminal row is frozen in every mutable
        -- column, not only attempts / receipt_json.
        WHEN OLD.state IN ('delivered','dead_letter') AND (
             NEW.attempts <> OLD.attempts
             OR NEW.receipt_json IS NOT OLD.receipt_json
             OR NEW.last_error IS NOT OLD.last_error
             OR NEW.lease_owner IS NOT OLD.lease_owner
             OR NEW.lease_expires_at IS NOT OLD.lease_expires_at
             OR NEW.state <> OLD.state)
        THEN RAISE(ABORT,'memory_ingestion_outbox_monotonic')
        WHEN NEW.attempts < OLD.attempts
        THEN RAISE(ABORT,'memory_ingestion_outbox_monotonic')
    END;
END;

CREATE TRIGGER post_turn_invocation_attempts_guard BEFORE UPDATE ON post_turn_invocation_attempts
BEGIN
    SELECT CASE
        WHEN NEW.attempt_id <> OLD.attempt_id
             OR NEW.purpose <> OLD.purpose
             OR NEW.host_run_id <> OLD.host_run_id
             OR NEW.sdk_run_id <> OLD.sdk_run_id
             OR NEW.generation <> OLD.generation
             OR NEW.task_scope_id IS NOT OLD.task_scope_id
             OR NEW.closure_watermark IS NOT OLD.closure_watermark
             OR NEW.request_hash <> OLD.request_hash
             OR NEW.attempt_ordinal <> OLD.attempt_ordinal
             OR NEW.evidence_set_key <> OLD.evidence_set_key
             OR NEW.provider_id <> OLD.provider_id
             OR NEW.model_id <> OLD.model_id
             OR NEW.model_config_hash <> OLD.model_config_hash
             OR NEW.reserved_at <> OLD.reserved_at
        THEN RAISE(ABORT,'post_turn_invocation_attempt_identity_immutable')
        WHEN NEW.status <> OLD.status AND NOT (
             (OLD.status = 'reserved' AND NEW.status IN ('handed_off','failed'))
             OR (OLD.status = 'handed_off' AND NEW.status IN ('succeeded','failed','unknown')))
        THEN RAISE(ABORT,'post_turn_invocation_attempt_monotonic')
        -- Task 6 (Task 2 review F-5): a settled row freezes every audit column;
        -- the durable result may only grow from "response only" to
        -- "response + envelope" exactly once (Task 4 review F-1: the Provider
        -- response is settled before derivation, the envelope attached after).
        WHEN OLD.status IN ('succeeded','failed','unknown') AND (
             NEW.status <> OLD.status
             OR NEW.settled_at IS NOT OLD.settled_at
             OR NEW.unknown_class IS NOT OLD.unknown_class
             OR NEW.result_hash IS NOT OLD.result_hash
             OR NEW.plan_id IS NOT OLD.plan_id
             OR NEW.reason_code IS NOT OLD.reason_code
             OR NEW.provider_request_id IS NOT OLD.provider_request_id
             OR (OLD.result_envelope_json IS NOT NULL
                 AND NEW.result_envelope_json IS NOT OLD.result_envelope_json
                 AND (NEW.result_envelope_json IS NULL
                      OR json_extract(OLD.result_envelope_json, '$.envelope') IS NOT NULL
                      OR json_extract(NEW.result_envelope_json, '$.envelope') IS NULL
                      OR json_extract(NEW.result_envelope_json, '$.response')
                         IS NOT json_extract(OLD.result_envelope_json, '$.response'))))
        THEN RAISE(ABORT,'post_turn_invocation_attempt_monotonic')
        -- provider_request_id: write-once (never rewritten once known).
        WHEN OLD.provider_request_id IS NOT NULL AND NEW.provider_request_id IS NOT OLD.provider_request_id
        THEN RAISE(ABORT,'post_turn_invocation_attempt_monotonic')
        -- unknown_class only travels with a terminal status.
        WHEN NEW.unknown_class IS NOT OLD.unknown_class AND NEW.status NOT IN ('failed','unknown')
        THEN RAISE(ABORT,'post_turn_invocation_attempt_monotonic')
        -- handed_off_at: write-once, and only together with status → handed_off.
        WHEN OLD.handed_off_at IS NOT NULL AND NEW.handed_off_at IS NOT OLD.handed_off_at
        THEN RAISE(ABORT,'post_turn_invocation_attempt_monotonic')
        WHEN OLD.handed_off_at IS NULL AND NEW.handed_off_at IS NOT NULL AND NEW.status <> 'handed_off'
        THEN RAISE(ABORT,'post_turn_invocation_attempt_monotonic')
    END;
END;
