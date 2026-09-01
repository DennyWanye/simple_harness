-- Durable foreground admission, FIFO, control and lease authority.
-- The Host owns only these coordination facts. Actual Agent execution remains
-- exclusively owned by the Harness SDK Runtime and is referenced by sdk_run_id.

CREATE TABLE foreground_queue_marker (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    format_epoch TEXT NOT NULL CHECK (format_epoch = 'human-memory-v1'),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    migration_id TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL CHECK (length(migration_sha256) = 64),
    initialized_at REAL NOT NULL
);

CREATE TABLE foreground_turns (
    turn_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    primary_conversation_id TEXT NOT NULL,
    task_scope_id TEXT,
    evidence_id TEXT NOT NULL,
    evidence_hash TEXT NOT NULL CHECK (length(evidence_hash) = 64),
    idempotency_key TEXT NOT NULL,
    enqueue_sequence INTEGER NOT NULL CHECK (enqueue_sequence > 0),
    turn_hash TEXT NOT NULL UNIQUE CHECK (length(turn_hash) = 64),
    turn_json TEXT NOT NULL,
    enqueued_at REAL NOT NULL,
    UNIQUE(subject, idempotency_key),
    UNIQUE(subject, enqueue_sequence),
    FOREIGN KEY(primary_conversation_id)
        REFERENCES human_memory_primary_conversations(primary_conversation_id),
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id),
    FOREIGN KEY(evidence_id) REFERENCES human_memory_evidence(evidence_id)
);

CREATE INDEX idx_foreground_turns_fifo
ON foreground_turns(subject, enqueue_sequence, turn_id);

CREATE TABLE foreground_runs (
    host_run_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    primary_conversation_id TEXT NOT NULL,
    turn_id TEXT NOT NULL UNIQUE,
    enqueue_sequence INTEGER NOT NULL CHECK (enqueue_sequence > 0),
    task_scope_id TEXT,
    binding_set_revision INTEGER NOT NULL CHECK (binding_set_revision >= 0),
    binding_set_receipt_id TEXT,
    binding_set_receipt_hash TEXT CHECK (
        binding_set_receipt_hash IS NULL OR length(binding_set_receipt_hash) = 64
    ),
    context_snapshot_id TEXT NOT NULL,
    context_snapshot_revision INTEGER NOT NULL CHECK (context_snapshot_revision > 0),
    context_snapshot_hash TEXT NOT NULL CHECK (length(context_snapshot_hash) = 64),
    lineage_hash TEXT NOT NULL CHECK (length(lineage_hash) = 64),
    claim_idempotency_key TEXT NOT NULL,
    admission_receipt_id TEXT NOT NULL UNIQUE,
    admission_receipt_hash TEXT NOT NULL UNIQUE CHECK (length(admission_receipt_hash) = 64),
    admission_json TEXT NOT NULL,
    admitted_at REAL NOT NULL,
    UNIQUE(subject, claim_idempotency_key),
    CHECK (
        (binding_set_revision = 0 AND binding_set_receipt_id IS NULL AND binding_set_receipt_hash IS NULL)
        OR
        (binding_set_revision > 0 AND binding_set_receipt_id IS NOT NULL AND binding_set_receipt_hash IS NOT NULL)
    ),
    FOREIGN KEY(primary_conversation_id)
        REFERENCES human_memory_primary_conversations(primary_conversation_id),
    FOREIGN KEY(turn_id) REFERENCES foreground_turns(turn_id),
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id)
);

CREATE TABLE foreground_turn_transitions (
    transition_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    from_state TEXT NOT NULL CHECK (from_state IN ('ABSENT', 'QUEUED', 'CLAIMED')),
    to_state TEXT NOT NULL CHECK (to_state IN ('QUEUED', 'CLAIMED', 'SETTLED')),
    host_run_id TEXT,
    transition_hash TEXT NOT NULL UNIQUE CHECK (length(transition_hash) = 64),
    transition_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    UNIQUE(turn_id, to_state),
    CHECK (
        (from_state = 'ABSENT' AND to_state = 'QUEUED' AND host_run_id IS NULL)
        OR (from_state = 'QUEUED' AND to_state = 'CLAIMED' AND host_run_id IS NOT NULL)
        OR (from_state = 'CLAIMED' AND to_state = 'SETTLED' AND host_run_id IS NOT NULL)
    ),
    FOREIGN KEY(turn_id) REFERENCES foreground_turns(turn_id),
    FOREIGN KEY(host_run_id) REFERENCES foreground_runs(host_run_id)
);

CREATE TABLE foreground_turn_heads (
    turn_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    current_state TEXT NOT NULL CHECK (current_state IN ('QUEUED', 'CLAIMED', 'SETTLED')),
    host_run_id TEXT,
    last_transition_id TEXT NOT NULL UNIQUE,
    last_transition_hash TEXT NOT NULL CHECK (length(last_transition_hash) = 64),
    updated_at REAL NOT NULL,
    CHECK (
        (current_state = 'QUEUED' AND host_run_id IS NULL)
        OR (current_state IN ('CLAIMED', 'SETTLED') AND host_run_id IS NOT NULL)
    ),
    FOREIGN KEY(turn_id) REFERENCES foreground_turns(turn_id),
    FOREIGN KEY(host_run_id) REFERENCES foreground_runs(host_run_id),
    FOREIGN KEY(last_transition_id) REFERENCES foreground_turn_transitions(transition_id)
);

CREATE INDEX idx_foreground_turn_heads_pending
ON foreground_turn_heads(subject, current_state, turn_id);

CREATE TABLE foreground_run_sdk_bindings (
    binding_id TEXT PRIMARY KEY,
    host_run_id TEXT NOT NULL UNIQUE,
    sdk_run_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL,
    binding_hash TEXT NOT NULL UNIQUE CHECK (length(binding_hash) = 64),
    binding_json TEXT NOT NULL,
    bound_at REAL NOT NULL,
    UNIQUE(host_run_id, idempotency_key),
    FOREIGN KEY(host_run_id) REFERENCES foreground_runs(host_run_id)
);

CREATE TABLE foreground_run_transitions (
    transition_id TEXT PRIMARY KEY,
    host_run_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    from_state TEXT NOT NULL CHECK (from_state IN (
        'ABSENT', 'CLAIMED', 'RUNNING', 'PAUSE_REQUESTED', 'PAUSED',
        'STOP_REQUESTED', 'CANCEL_REQUESTED'
    )),
    to_state TEXT NOT NULL CHECK (to_state IN (
        'CLAIMED', 'RUNNING', 'PAUSE_REQUESTED', 'PAUSED',
        'STOP_REQUESTED', 'CANCEL_REQUESTED',
        'COMPLETED', 'FAILED', 'STOPPED', 'CANCELLED'
    )),
    generation INTEGER NOT NULL CHECK (generation > 0),
    owner_id TEXT,
    sdk_event_id TEXT,
    idempotency_key TEXT NOT NULL,
    causal_evidence_ref TEXT,
    causal_evidence_hash TEXT CHECK (
        causal_evidence_hash IS NULL OR length(causal_evidence_hash) = 64
    ),
    transition_hash TEXT NOT NULL UNIQUE CHECK (length(transition_hash) = 64),
    transition_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    UNIQUE(host_run_id, idempotency_key),
    FOREIGN KEY(host_run_id) REFERENCES foreground_runs(host_run_id)
);

CREATE TABLE foreground_run_heads (
    host_run_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    primary_conversation_id TEXT NOT NULL,
    turn_id TEXT NOT NULL UNIQUE,
    current_state TEXT NOT NULL CHECK (current_state IN (
        'CLAIMED', 'RUNNING', 'PAUSE_REQUESTED', 'PAUSED',
        'STOP_REQUESTED', 'CANCEL_REQUESTED',
        'COMPLETED', 'FAILED', 'STOPPED', 'CANCELLED'
    )),
    desired_control TEXT CHECK (desired_control IN ('pause', 'stop', 'cancel')),
    sdk_run_id TEXT UNIQUE,
    owner_id TEXT,
    generation INTEGER NOT NULL CHECK (generation > 0),
    lease_expires_at REAL,
    last_transition_id TEXT NOT NULL UNIQUE,
    last_transition_hash TEXT NOT NULL CHECK (length(last_transition_hash) = 64),
    updated_at REAL NOT NULL,
    CHECK (
        (current_state IN ('COMPLETED', 'FAILED', 'STOPPED', 'CANCELLED')
         AND owner_id IS NULL AND lease_expires_at IS NULL)
        OR
        (current_state NOT IN ('COMPLETED', 'FAILED', 'STOPPED', 'CANCELLED')
         AND owner_id IS NOT NULL AND lease_expires_at IS NOT NULL)
    ),
    FOREIGN KEY(host_run_id) REFERENCES foreground_runs(host_run_id),
    FOREIGN KEY(primary_conversation_id)
        REFERENCES human_memory_primary_conversations(primary_conversation_id),
    FOREIGN KEY(turn_id) REFERENCES foreground_turns(turn_id),
    FOREIGN KEY(last_transition_id) REFERENCES foreground_run_transitions(transition_id)
);

CREATE UNIQUE INDEX uq_foreground_one_nonterminal_per_subject
ON foreground_run_heads(subject)
WHERE current_state NOT IN ('COMPLETED', 'FAILED', 'STOPPED', 'CANCELLED');

CREATE TABLE foreground_lease_receipts (
    lease_receipt_id TEXT PRIMARY KEY,
    host_run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    prior_generation INTEGER CHECK (prior_generation IS NULL OR prior_generation > 0),
    action TEXT NOT NULL CHECK (action IN ('acquire', 'heartbeat', 'reclaim', 'resume', 'close')),
    expires_at REAL,
    idempotency_key TEXT NOT NULL,
    lease_hash TEXT NOT NULL UNIQUE CHECK (length(lease_hash) = 64),
    lease_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    UNIQUE(host_run_id, idempotency_key),
    CHECK (
        (action = 'close' AND expires_at IS NULL)
        OR (action <> 'close' AND expires_at IS NOT NULL)
    ),
    FOREIGN KEY(host_run_id) REFERENCES foreground_runs(host_run_id)
);

CREATE TABLE foreground_control_intents (
    control_id TEXT PRIMARY KEY,
    host_run_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    control_kind TEXT NOT NULL CHECK (control_kind IN ('pause', 'stop', 'cancel')),
    reason TEXT NOT NULL,
    requested_generation INTEGER NOT NULL CHECK (requested_generation > 0),
    outcome TEXT NOT NULL CHECK (outcome IN (
        'signalled', 'superseded', 'already_requested', 'already_terminal'
    )),
    reduced_state TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    control_hash TEXT NOT NULL UNIQUE CHECK (length(control_hash) = 64),
    control_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    UNIQUE(host_run_id, idempotency_key),
    FOREIGN KEY(host_run_id) REFERENCES foreground_runs(host_run_id)
);

CREATE TABLE foreground_signal_outbox (
    signal_id TEXT PRIMARY KEY,
    control_id TEXT NOT NULL,
    host_run_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    control_kind TEXT NOT NULL CHECK (control_kind IN ('pause', 'stop', 'cancel')),
    signal_hash TEXT NOT NULL UNIQUE CHECK (length(signal_hash) = 64),
    signal_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(control_id, generation),
    FOREIGN KEY(control_id) REFERENCES foreground_control_intents(control_id),
    FOREIGN KEY(host_run_id) REFERENCES foreground_runs(host_run_id)
);

CREATE TABLE foreground_signal_acks (
    ack_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL UNIQUE,
    host_run_id TEXT NOT NULL,
    sdk_run_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    sdk_signal_id TEXT NOT NULL UNIQUE,
    ack_hash TEXT NOT NULL UNIQUE CHECK (length(ack_hash) = 64),
    ack_json TEXT NOT NULL,
    acknowledged_at REAL NOT NULL,
    FOREIGN KEY(signal_id) REFERENCES foreground_signal_outbox(signal_id),
    FOREIGN KEY(host_run_id) REFERENCES foreground_runs(host_run_id),
    FOREIGN KEY(sdk_run_id) REFERENCES foreground_run_sdk_bindings(sdk_run_id)
);

CREATE TABLE foreground_terminal_receipts (
    terminal_receipt_id TEXT PRIMARY KEY,
    host_run_id TEXT NOT NULL UNIQUE,
    sdk_run_id TEXT NOT NULL,
    terminal_state TEXT NOT NULL CHECK (terminal_state IN (
        'COMPLETED', 'FAILED', 'STOPPED', 'CANCELLED'
    )),
    generation INTEGER NOT NULL CHECK (generation > 0),
    sdk_event_id TEXT NOT NULL UNIQUE,
    sdk_event_hash TEXT NOT NULL CHECK (length(sdk_event_hash) = 64),
    receipt_hash TEXT NOT NULL UNIQUE CHECK (length(receipt_hash) = 64),
    receipt_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    FOREIGN KEY(host_run_id) REFERENCES foreground_runs(host_run_id),
    FOREIGN KEY(sdk_run_id) REFERENCES foreground_run_sdk_bindings(sdk_run_id)
);

-- Immutable authority and receipt ledgers.
CREATE TRIGGER foreground_queue_marker_no_update BEFORE UPDATE ON foreground_queue_marker BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_queue_marker_no_delete BEFORE DELETE ON foreground_queue_marker BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_turns_no_update BEFORE UPDATE ON foreground_turns BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_turns_no_delete BEFORE DELETE ON foreground_turns BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_runs_no_update BEFORE UPDATE ON foreground_runs BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_runs_no_delete BEFORE DELETE ON foreground_runs BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_turn_transitions_no_update BEFORE UPDATE ON foreground_turn_transitions BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_turn_transitions_no_delete BEFORE DELETE ON foreground_turn_transitions BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_run_bindings_no_update BEFORE UPDATE ON foreground_run_sdk_bindings BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_run_bindings_no_delete BEFORE DELETE ON foreground_run_sdk_bindings BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_run_transitions_no_update BEFORE UPDATE ON foreground_run_transitions BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_run_transitions_no_delete BEFORE DELETE ON foreground_run_transitions BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_lease_receipts_no_update BEFORE UPDATE ON foreground_lease_receipts BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_lease_receipts_no_delete BEFORE DELETE ON foreground_lease_receipts BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_control_intents_no_update BEFORE UPDATE ON foreground_control_intents BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_control_intents_no_delete BEFORE DELETE ON foreground_control_intents BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_signal_outbox_no_update BEFORE UPDATE ON foreground_signal_outbox BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_signal_outbox_no_delete BEFORE DELETE ON foreground_signal_outbox BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_signal_acks_no_update BEFORE UPDATE ON foreground_signal_acks BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_signal_acks_no_delete BEFORE DELETE ON foreground_signal_acks BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_terminal_receipts_no_update BEFORE UPDATE ON foreground_terminal_receipts BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;
CREATE TRIGGER foreground_terminal_receipts_no_delete BEFORE DELETE ON foreground_terminal_receipts BEGIN SELECT RAISE(ABORT, 'foreground_queue_append_only'); END;

CREATE TRIGGER foreground_turn_heads_no_delete
BEFORE DELETE ON foreground_turn_heads BEGIN
    SELECT RAISE(ABORT, 'foreground_queue_append_only');
END;

CREATE TRIGGER foreground_turn_heads_guard
BEFORE UPDATE ON foreground_turn_heads
WHEN NEW.turn_id <> OLD.turn_id
  OR NEW.subject <> OLD.subject
  OR NOT (
      (OLD.current_state = 'QUEUED' AND NEW.current_state = 'CLAIMED')
      OR (OLD.current_state = 'CLAIMED' AND NEW.current_state = 'SETTLED')
  )
  OR NEW.last_transition_id = OLD.last_transition_id
  OR NEW.last_transition_hash = OLD.last_transition_hash
BEGIN
    SELECT RAISE(ABORT, 'foreground_turn_transition_invalid');
END;

CREATE TRIGGER foreground_run_heads_no_delete
BEFORE DELETE ON foreground_run_heads BEGIN
    SELECT RAISE(ABORT, 'foreground_queue_append_only');
END;

CREATE TRIGGER foreground_run_heads_guard
BEFORE UPDATE ON foreground_run_heads
WHEN NEW.host_run_id <> OLD.host_run_id
  OR NEW.subject <> OLD.subject
  OR NEW.primary_conversation_id <> OLD.primary_conversation_id
  OR NEW.turn_id <> OLD.turn_id
      OR (OLD.sdk_run_id IS NOT NULL AND NEW.sdk_run_id IS NOT OLD.sdk_run_id)
  OR NEW.generation < OLD.generation
  OR NEW.generation > OLD.generation + 1
  OR OLD.current_state IN ('COMPLETED', 'FAILED', 'STOPPED', 'CANCELLED')
  OR NOT (
      NEW.current_state = OLD.current_state
      OR (OLD.current_state = 'CLAIMED' AND NEW.current_state IN (
          'RUNNING', 'PAUSE_REQUESTED', 'STOP_REQUESTED', 'CANCEL_REQUESTED',
          'COMPLETED', 'FAILED', 'STOPPED', 'CANCELLED'
      ))
      OR (OLD.current_state = 'RUNNING' AND NEW.current_state IN (
          'PAUSE_REQUESTED', 'STOP_REQUESTED', 'CANCEL_REQUESTED',
          'COMPLETED', 'FAILED', 'STOPPED', 'CANCELLED'
      ))
      OR (OLD.current_state = 'PAUSE_REQUESTED' AND NEW.current_state IN (
          'PAUSED', 'RUNNING', 'STOP_REQUESTED', 'CANCEL_REQUESTED',
          'COMPLETED', 'FAILED', 'STOPPED', 'CANCELLED'
      ))
      OR (OLD.current_state = 'PAUSED' AND NEW.current_state IN (
          'RUNNING', 'STOP_REQUESTED', 'CANCEL_REQUESTED',
          'COMPLETED', 'FAILED', 'STOPPED', 'CANCELLED'
      ))
      OR (OLD.current_state = 'STOP_REQUESTED' AND NEW.current_state IN (
          'CANCEL_REQUESTED', 'COMPLETED', 'FAILED', 'STOPPED', 'CANCELLED'
      ))
      OR (OLD.current_state = 'CANCEL_REQUESTED' AND NEW.current_state IN (
          'COMPLETED', 'FAILED', 'STOPPED', 'CANCELLED'
      ))
  )
BEGIN
    SELECT RAISE(ABORT, 'foreground_run_transition_invalid');
END;
