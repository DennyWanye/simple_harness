-- Immutable foreground execution preparation and lifecycle audit ledger.
-- Pre-claim drafts contain only source identities and hashes; they never grant
-- Provider, Tool, SDK start, signal, or effect authority.

CREATE TABLE foreground_execution_marker (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    format_epoch TEXT NOT NULL CHECK(format_epoch='human-memory-v1'),
    schema_version INTEGER NOT NULL CHECK(schema_version=1),
    migration_id TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL CHECK(length(migration_sha256)=64),
    initialized_at REAL NOT NULL
);

CREATE TABLE foreground_preparation_drafts (
    draft_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    turn_hash TEXT NOT NULL CHECK(length(turn_hash)=64),
    candidate_hash TEXT NOT NULL CHECK(length(candidate_hash)=64),
    context_snapshot_id TEXT NOT NULL,
    context_snapshot_revision INTEGER NOT NULL CHECK(context_snapshot_revision > 0),
    context_snapshot_hash TEXT NOT NULL CHECK(length(context_snapshot_hash)=64),
    idempotency_key TEXT NOT NULL,
    draft_hash TEXT NOT NULL UNIQUE CHECK(length(draft_hash)=64),
    candidate_json TEXT NOT NULL,
    draft_json TEXT NOT NULL,
    prepared_at REAL NOT NULL,
    UNIQUE(subject,idempotency_key),
    FOREIGN KEY(turn_id) REFERENCES foreground_turns(turn_id)
);

CREATE TABLE foreground_run_preparation_bindings (
    binding_id TEXT PRIMARY KEY,
    host_run_id TEXT NOT NULL UNIQUE,
    draft_id TEXT NOT NULL UNIQUE,
    draft_hash TEXT NOT NULL CHECK(length(draft_hash)=64),
    candidate_hash TEXT NOT NULL CHECK(length(candidate_hash)=64),
    binding_hash TEXT NOT NULL UNIQUE CHECK(length(binding_hash)=64),
    binding_json TEXT NOT NULL,
    bound_at REAL NOT NULL,
    FOREIGN KEY(host_run_id) REFERENCES foreground_runs(host_run_id),
    FOREIGN KEY(draft_id) REFERENCES foreground_preparation_drafts(draft_id)
);

CREATE TABLE foreground_execution_preparations (
    preparation_id TEXT PRIMARY KEY,
    host_run_id TEXT NOT NULL UNIQUE,
    owner_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation > 0),
    draft_id TEXT NOT NULL,
    draft_hash TEXT NOT NULL CHECK(length(draft_hash)=64),
    context_ref TEXT NOT NULL,
    context_hash TEXT NOT NULL CHECK(length(context_hash)=64),
    provider_ref TEXT NOT NULL,
    provider_hash TEXT NOT NULL CHECK(length(provider_hash)=64),
    tool_ref TEXT NOT NULL,
    tool_hash TEXT NOT NULL CHECK(length(tool_hash)=64),
    execution_request_hash TEXT NOT NULL CHECK(length(execution_request_hash)=64),
    idempotency_key TEXT NOT NULL,
    preparation_hash TEXT NOT NULL UNIQUE CHECK(length(preparation_hash)=64),
    preparation_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    UNIQUE(host_run_id,idempotency_key),
    FOREIGN KEY(host_run_id) REFERENCES foreground_runs(host_run_id),
    FOREIGN KEY(draft_id) REFERENCES foreground_preparation_drafts(draft_id)
);

CREATE TABLE foreground_execution_start_intents (
    intent_id TEXT PRIMARY KEY,
    host_run_id TEXT NOT NULL UNIQUE,
    sdk_run_id TEXT NOT NULL UNIQUE,
    owner_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation > 0),
    preparation_id TEXT NOT NULL,
    preparation_hash TEXT NOT NULL CHECK(length(preparation_hash)=64),
    start_request_hash TEXT NOT NULL CHECK(length(start_request_hash)=64),
    idempotency_key TEXT NOT NULL,
    intent_hash TEXT NOT NULL UNIQUE CHECK(length(intent_hash)=64),
    intent_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    UNIQUE(host_run_id,idempotency_key),
    FOREIGN KEY(host_run_id) REFERENCES foreground_runs(host_run_id),
    FOREIGN KEY(preparation_id) REFERENCES foreground_execution_preparations(preparation_id)
);

CREATE TABLE foreground_execution_start_observations (
    observation_id TEXT PRIMARY KEY,
    host_run_id TEXT NOT NULL,
    sdk_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation > 0),
    outcome TEXT NOT NULL CHECK(outcome IN ('RETURNED','RAISED','QUERY_FOUND','QUERY_MISSING')),
    result_ref TEXT,
    result_hash TEXT CHECK(result_hash IS NULL OR length(result_hash)=64),
    error_code TEXT,
    idempotency_key TEXT NOT NULL,
    observation_hash TEXT NOT NULL UNIQUE CHECK(length(observation_hash)=64),
    observation_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    UNIQUE(host_run_id,idempotency_key),
    CHECK((result_ref IS NULL) = (result_hash IS NULL)),
    FOREIGN KEY(host_run_id) REFERENCES foreground_runs(host_run_id),
    FOREIGN KEY(sdk_run_id) REFERENCES foreground_execution_start_intents(sdk_run_id)
);

CREATE TABLE foreground_execution_reconciliations (
    reconciliation_id TEXT PRIMARY KEY,
    host_run_id TEXT NOT NULL,
    sdk_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation > 0),
    observed_state TEXT NOT NULL CHECK(observed_state IN (
        'BOUND_RUNNING','BOUND_WAITING','BOUND_TERMINAL','UNBOUND_RETRY','FAILED_CLOSED'
    )),
    evidence_ref TEXT,
    evidence_hash TEXT CHECK(evidence_hash IS NULL OR length(evidence_hash)=64),
    idempotency_key TEXT NOT NULL,
    reconciliation_hash TEXT NOT NULL UNIQUE CHECK(length(reconciliation_hash)=64),
    reconciliation_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    UNIQUE(host_run_id,idempotency_key),
    CHECK((evidence_ref IS NULL) = (evidence_hash IS NULL)),
    FOREIGN KEY(host_run_id) REFERENCES foreground_runs(host_run_id),
    FOREIGN KEY(sdk_run_id) REFERENCES foreground_execution_start_intents(sdk_run_id)
);

CREATE TRIGGER foreground_execution_marker_no_update BEFORE UPDATE ON foreground_execution_marker BEGIN SELECT RAISE(ABORT,'foreground_execution_append_only'); END;
CREATE TRIGGER foreground_execution_marker_no_delete BEFORE DELETE ON foreground_execution_marker BEGIN SELECT RAISE(ABORT,'foreground_execution_append_only'); END;
CREATE TRIGGER foreground_preparation_drafts_no_update BEFORE UPDATE ON foreground_preparation_drafts BEGIN SELECT RAISE(ABORT,'foreground_execution_append_only'); END;
CREATE TRIGGER foreground_preparation_drafts_no_delete BEFORE DELETE ON foreground_preparation_drafts BEGIN SELECT RAISE(ABORT,'foreground_execution_append_only'); END;
CREATE TRIGGER foreground_run_preparation_bindings_no_update BEFORE UPDATE ON foreground_run_preparation_bindings BEGIN SELECT RAISE(ABORT,'foreground_execution_append_only'); END;
CREATE TRIGGER foreground_run_preparation_bindings_no_delete BEFORE DELETE ON foreground_run_preparation_bindings BEGIN SELECT RAISE(ABORT,'foreground_execution_append_only'); END;
CREATE TRIGGER foreground_execution_preparations_no_update BEFORE UPDATE ON foreground_execution_preparations BEGIN SELECT RAISE(ABORT,'foreground_execution_append_only'); END;
CREATE TRIGGER foreground_execution_preparations_no_delete BEFORE DELETE ON foreground_execution_preparations BEGIN SELECT RAISE(ABORT,'foreground_execution_append_only'); END;
CREATE TRIGGER foreground_execution_start_intents_no_update BEFORE UPDATE ON foreground_execution_start_intents BEGIN SELECT RAISE(ABORT,'foreground_execution_append_only'); END;
CREATE TRIGGER foreground_execution_start_intents_no_delete BEFORE DELETE ON foreground_execution_start_intents BEGIN SELECT RAISE(ABORT,'foreground_execution_append_only'); END;
CREATE TRIGGER foreground_execution_start_observations_no_update BEFORE UPDATE ON foreground_execution_start_observations BEGIN SELECT RAISE(ABORT,'foreground_execution_append_only'); END;
CREATE TRIGGER foreground_execution_start_observations_no_delete BEFORE DELETE ON foreground_execution_start_observations BEGIN SELECT RAISE(ABORT,'foreground_execution_append_only'); END;
CREATE TRIGGER foreground_execution_reconciliations_no_update BEFORE UPDATE ON foreground_execution_reconciliations BEGIN SELECT RAISE(ABORT,'foreground_execution_append_only'); END;
CREATE TRIGGER foreground_execution_reconciliations_no_delete BEFORE DELETE ON foreground_execution_reconciliations BEGIN SELECT RAISE(ABORT,'foreground_execution_append_only'); END;
