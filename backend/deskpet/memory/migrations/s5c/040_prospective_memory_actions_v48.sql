-- S5c T2 explicit-only migration. The default production initializer stays v47.
-- Four domain journals; no timer, grant issuer, Tool, or disclosure bypass.
CREATE TABLE prospective_scheduler_registrations (
    record_id TEXT PRIMARY KEY,
    owner_key TEXT NOT NULL,
    outbox_id TEXT NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('prepared','applied')),
    source_json TEXT NOT NULL,
    source_hash TEXT NOT NULL CHECK(length(source_hash)=64),
    authority_id TEXT NOT NULL,
    authority_json TEXT NOT NULL,
    authority_hash TEXT NOT NULL CHECK(length(authority_hash)=64),
    record_hash TEXT NOT NULL CHECK(length(record_hash)=64),
    UNIQUE(owner_key,outbox_id,phase)
);
CREATE UNIQUE INDEX s5c_registration_authority
ON prospective_scheduler_registrations(owner_key,authority_id) WHERE phase='prepared';

CREATE TABLE prospective_outbox_cursor (
    owner_key TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence>0),
    after_time REAL NOT NULL CHECK(after_time>=0),
    after_id TEXT NOT NULL,
    registration_record_id TEXT NOT NULL UNIQUE,
    prior_hash TEXT NOT NULL CHECK(length(prior_hash)=64),
    cursor_hash TEXT NOT NULL CHECK(length(cursor_hash)=64),
    PRIMARY KEY(owner_key,sequence),
    UNIQUE(owner_key,after_time,after_id),
    FOREIGN KEY(registration_record_id) REFERENCES prospective_scheduler_registrations(record_id)
);

CREATE TABLE prospective_occurrences (
    record_id TEXT PRIMARY KEY,
    owner_key TEXT NOT NULL,
    occurrence_key TEXT NOT NULL CHECK(length(occurrence_key)=64 AND occurrence_key NOT GLOB '*[^0-9a-f]*'),
    phase TEXT NOT NULL CHECK(phase IN ('claimed','presented','acknowledged','settled','overdue')),
    sdk_run_id TEXT,
    snapshot_id TEXT,
    reason TEXT,
    inbox_json TEXT NOT NULL,
    record_hash TEXT NOT NULL CHECK(length(record_hash)=64),
    CHECK(phase NOT IN ('presented','acknowledged') OR sdk_run_id IS NOT NULL),
    CHECK(phase!='presented' OR snapshot_id IS NOT NULL),
    CHECK((phase='settled') = (reason IS NOT NULL)),
    CHECK(reason IS NULL OR reason IN ('acknowledged','suppressed','superseded','expired')),
    FOREIGN KEY(snapshot_id) REFERENCES run_context_snapshot_receipts(snapshot_id)
);
CREATE UNIQUE INDEX s5c_occurrence_once
ON prospective_occurrences(owner_key,occurrence_key,phase)
WHERE phase IN ('claimed','overdue','settled');
CREATE UNIQUE INDEX s5c_occurrence_run_once
ON prospective_occurrences(owner_key,occurrence_key,sdk_run_id,phase)
WHERE phase IN ('presented','acknowledged');

CREATE TABLE memory_action_events (
    action_id TEXT NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('requested','authorized','suppressed','analysis_queued','applied')),
    owner_key TEXT NOT NULL,
    request_json TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
    authority_id TEXT,
    authority_json TEXT,
    authority_hash TEXT CHECK(authority_hash IS NULL OR length(authority_hash)=64),
    record_hash TEXT NOT NULL CHECK(length(record_hash)=64),
    PRIMARY KEY(owner_key,action_id,phase),
    CHECK((authority_id IS NULL) = (authority_json IS NULL)),
    CHECK((authority_id IS NULL) = (authority_hash IS NULL)),
    CHECK((phase='authorized') = (authority_id IS NOT NULL))
);
CREATE UNIQUE INDEX s5c_action_authority
ON memory_action_events(owner_key,authority_id) WHERE phase='authorized';

CREATE TRIGGER s5c_registration_no_update BEFORE UPDATE ON prospective_scheduler_registrations
BEGIN SELECT RAISE(ABORT,'s5c_append_only'); END;
CREATE TRIGGER s5c_registration_no_delete BEFORE DELETE ON prospective_scheduler_registrations
BEGIN SELECT RAISE(ABORT,'s5c_append_only'); END;
CREATE TRIGGER s5c_cursor_no_update BEFORE UPDATE ON prospective_outbox_cursor
BEGIN SELECT RAISE(ABORT,'s5c_append_only'); END;
CREATE TRIGGER s5c_cursor_no_delete BEFORE DELETE ON prospective_outbox_cursor
BEGIN SELECT RAISE(ABORT,'s5c_append_only'); END;
CREATE TRIGGER s5c_occurrence_no_update BEFORE UPDATE ON prospective_occurrences
BEGIN SELECT RAISE(ABORT,'s5c_append_only'); END;
CREATE TRIGGER s5c_occurrence_no_delete BEFORE DELETE ON prospective_occurrences
BEGIN SELECT RAISE(ABORT,'s5c_append_only'); END;
CREATE TRIGGER s5c_action_no_update BEFORE UPDATE ON memory_action_events
BEGIN SELECT RAISE(ABORT,'s5c_append_only'); END;
CREATE TRIGGER s5c_action_no_delete BEFORE DELETE ON memory_action_events
BEGIN SELECT RAISE(ABORT,'s5c_append_only'); END;
