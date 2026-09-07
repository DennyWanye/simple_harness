CREATE TABLE procedure_observation_attempts (
    authority_id TEXT PRIMARY KEY,
    use_id TEXT NOT NULL REFERENCES procedure_uses(use_id),
    attempt_ordinal INTEGER NOT NULL CHECK(attempt_ordinal>=2),
    previous_authority_id TEXT NOT NULL UNIQUE,
    previous_ref_hash TEXT NOT NULL CHECK(length(previous_ref_hash)=64),
    body_json TEXT NOT NULL,
    body_hash TEXT NOT NULL CHECK(length(body_hash)=64),
    UNIQUE(use_id,attempt_ordinal)
);
CREATE TRIGGER procedure_observation_attempts_no_update BEFORE UPDATE ON procedure_observation_attempts
BEGIN SELECT RAISE(ABORT,'procedure_observation_attempt_immutable'); END;
CREATE TRIGGER procedure_observation_attempts_no_delete BEFORE DELETE ON procedure_observation_attempts
BEGIN SELECT RAISE(ABORT,'procedure_observation_attempt_immutable'); END;
