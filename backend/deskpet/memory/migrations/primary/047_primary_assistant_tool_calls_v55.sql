-- 2026-09-08 HM-TO-A6 F-K1: durable side record of the assistant's own tool
-- calls (name + arguments) per settled primary Run, keyed by the transcript
-- ordinal of the assistant item that issued them.
--
-- Host state only. The S1 terminal observation (`primary_run_terminal`) keeps
-- its exact archived shape and envelope hash; these rows are joined at
-- projection time and are bound to one observation by (evidence_id, envelope
-- hash). A row grants nothing: it is a content-addressed copy of public SDK
-- provider records (`provider_invocations.response_json`) that already hold
-- the same arguments. Old observations without rows render exactly as before.
CREATE TABLE primary_assistant_tool_calls (
    record_id TEXT PRIMARY KEY,
    sdk_run_id TEXT NOT NULL,
    host_run_id TEXT NOT NULL,
    message_ordinal INTEGER NOT NULL CHECK(message_ordinal > 1),
    observation_evidence_id TEXT NOT NULL,
    observation_envelope_hash TEXT NOT NULL CHECK(length(observation_envelope_hash)=64),
    body_json TEXT NOT NULL,
    body_hash TEXT NOT NULL CHECK(length(body_hash)=64),
    recorded_at REAL NOT NULL,
    UNIQUE(sdk_run_id, message_ordinal)
);
CREATE INDEX primary_assistant_tool_calls_by_observation
    ON primary_assistant_tool_calls(observation_evidence_id, observation_envelope_hash);
CREATE TRIGGER primary_assistant_tool_calls_no_update BEFORE UPDATE ON primary_assistant_tool_calls
BEGIN SELECT RAISE(ABORT,'primary_assistant_tool_calls_immutable'); END;
CREATE TRIGGER primary_assistant_tool_calls_no_delete BEFORE DELETE ON primary_assistant_tool_calls
BEGIN SELECT RAISE(ABORT,'primary_assistant_tool_calls_immutable'); END;
