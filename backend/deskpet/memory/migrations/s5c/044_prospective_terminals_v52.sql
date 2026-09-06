-- Preserve the frozen v50 cursor and its immutable recovery-registry identity.
-- v52 copies its existing chain unchanged and adds a distinct terminal target.
CREATE TABLE prospective_invalidation_terminals (
    record_id TEXT PRIMARY KEY,
    owner_key TEXT NOT NULL,
    outbox_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind='not_required'),
    source_json TEXT NOT NULL,
    source_hash TEXT NOT NULL CHECK(length(source_hash)=64),
    receipt_json TEXT NOT NULL,
    receipt_hash TEXT NOT NULL CHECK(length(receipt_hash)=64),
    record_hash TEXT NOT NULL CHECK(length(record_hash)=64),
    UNIQUE(owner_key,outbox_id)
);

CREATE TABLE prospective_outbox_cursor_v52 (
    owner_key TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence>0),
    after_time REAL NOT NULL CHECK(after_time>=0),
    after_id TEXT NOT NULL,
    registration_record_id TEXT UNIQUE,
    prior_hash TEXT NOT NULL CHECK(length(prior_hash)=64),
    cursor_hash TEXT NOT NULL CHECK(length(cursor_hash)=64),
    terminal_record_id TEXT UNIQUE,
    PRIMARY KEY(owner_key,sequence),
    UNIQUE(owner_key,after_time,after_id),
    CHECK((registration_record_id IS NULL) != (terminal_record_id IS NULL)),
    FOREIGN KEY(registration_record_id) REFERENCES prospective_scheduler_registrations(record_id),
    FOREIGN KEY(terminal_record_id) REFERENCES prospective_invalidation_terminals(record_id)
);

INSERT INTO prospective_outbox_cursor_v52
SELECT owner_key,sequence,after_time,after_id,registration_record_id,prior_hash,cursor_hash,NULL
FROM prospective_outbox_cursor;

CREATE TRIGGER s5c_cursor_v50_sealed BEFORE INSERT ON prospective_outbox_cursor
BEGIN SELECT RAISE(ABORT,'s5c_cursor_successor_required'); END;
CREATE TRIGGER s5c_terminal_no_update BEFORE UPDATE ON prospective_invalidation_terminals
BEGIN SELECT RAISE(ABORT,'s5c_append_only'); END;
CREATE TRIGGER s5c_terminal_no_delete BEFORE DELETE ON prospective_invalidation_terminals
BEGIN SELECT RAISE(ABORT,'s5c_append_only'); END;
CREATE TRIGGER s5c_cursor_v52_no_update BEFORE UPDATE ON prospective_outbox_cursor_v52
BEGIN SELECT RAISE(ABORT,'s5c_append_only'); END;
CREATE TRIGGER s5c_cursor_v52_no_delete BEFORE DELETE ON prospective_outbox_cursor_v52
BEGIN SELECT RAISE(ABORT,'s5c_append_only'); END;
