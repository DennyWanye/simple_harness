-- Actual primary tool identity index; no output bytes, scope grant or watermark.
CREATE TABLE primary_effect_identities (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    host_run_id TEXT NOT NULL REFERENCES foreground_runs(host_run_id),
    sdk_run_id TEXT NOT NULL REFERENCES foreground_run_sdk_bindings(sdk_run_id),
    effect_id TEXT NOT NULL UNIQUE,
    tool_name TEXT NOT NULL,
    identity_hash TEXT NOT NULL CHECK(length(identity_hash)=64),
    identity_json TEXT NOT NULL
);
CREATE INDEX primary_effect_identities_run ON primary_effect_identities(sdk_run_id,sequence);
CREATE TRIGGER primary_effect_identities_no_update BEFORE UPDATE ON primary_effect_identities
BEGIN SELECT RAISE(ABORT,'primary_effect_identity_append_only'); END;
CREATE TRIGGER primary_effect_identities_no_delete BEFORE DELETE ON primary_effect_identities
BEGIN SELECT RAISE(ABORT,'primary_effect_identity_append_only'); END;
