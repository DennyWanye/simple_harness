CREATE TABLE procedure_uses (
    use_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    task_scope_id TEXT NOT NULL REFERENCES task_scopes(task_scope_id),
    sdk_run_id TEXT NOT NULL UNIQUE,
    memory_id TEXT NOT NULL,
    target_revision INTEGER NOT NULL CHECK(target_revision >= 1),
    body_json TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    created_at REAL NOT NULL CHECK(created_at >= 0)
);
CREATE TABLE procedure_use_reservations (
    use_id TEXT NOT NULL REFERENCES procedure_uses(use_id),
    step_ordinal INTEGER NOT NULL CHECK(step_ordinal >= 1 AND step_ordinal <= 16),
    call_id TEXT NOT NULL UNIQUE,
    body_json TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    PRIMARY KEY(use_id,step_ordinal)
);
CREATE TABLE procedure_use_effects (
    use_id TEXT NOT NULL REFERENCES procedure_uses(use_id),
    step_ordinal INTEGER NOT NULL CHECK(step_ordinal >= 1 AND step_ordinal <= 16),
    effect_id TEXT NOT NULL UNIQUE,
    body_json TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    PRIMARY KEY(use_id,step_ordinal),
    FOREIGN KEY(use_id,step_ordinal) REFERENCES procedure_use_reservations(use_id,step_ordinal)
);
CREATE TABLE procedure_observation_journal (
    use_id TEXT NOT NULL REFERENCES procedure_uses(use_id),
    phase TEXT NOT NULL CHECK(phase IN ('prepared','applied','rejected')),
    body_json TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    PRIMARY KEY(use_id,phase)
);
CREATE TRIGGER procedure_uses_no_update BEFORE UPDATE ON procedure_uses
BEGIN SELECT RAISE(ABORT,'procedure_use_immutable'); END;
CREATE TRIGGER procedure_uses_no_delete BEFORE DELETE ON procedure_uses
BEGIN SELECT RAISE(ABORT,'procedure_use_immutable'); END;
CREATE TRIGGER procedure_use_effects_no_update BEFORE UPDATE ON procedure_use_effects
BEGIN SELECT RAISE(ABORT,'procedure_use_effect_immutable'); END;
CREATE TRIGGER procedure_use_reservations_no_update BEFORE UPDATE ON procedure_use_reservations
BEGIN SELECT RAISE(ABORT,'procedure_use_reservation_immutable'); END;
CREATE TRIGGER procedure_use_reservations_no_delete BEFORE DELETE ON procedure_use_reservations
BEGIN SELECT RAISE(ABORT,'procedure_use_reservation_immutable'); END;
CREATE TRIGGER procedure_use_effects_no_delete BEFORE DELETE ON procedure_use_effects
BEGIN SELECT RAISE(ABORT,'procedure_use_effect_immutable'); END;
CREATE TRIGGER procedure_observation_journal_no_update BEFORE UPDATE ON procedure_observation_journal
BEGIN SELECT RAISE(ABORT,'procedure_observation_immutable'); END;
CREATE TRIGGER procedure_observation_journal_no_delete BEFORE DELETE ON procedure_observation_journal
BEGIN SELECT RAISE(ABORT,'procedure_observation_immutable'); END;
