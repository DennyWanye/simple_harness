CREATE TABLE preference_turn_decision_receipts (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    source_message_ref TEXT NOT NULL,
    source_message_hash TEXT NOT NULL,
    assessment_input_hash TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    decision_hash TEXT NOT NULL,
    interpreter_id TEXT NOT NULL,
    interpreter_version TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, source_message_ref),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;
