-- One-time v33 reset of all pre-project-scoped conversation data.
--
-- This migration deliberately does not preserve or translate legacy Session,
-- message, Project, Run, context, projection, or conversation-derived Memory
-- rows.  The runner owns the surrounding transaction.  External databases
-- and the optional sqlite-vec table are cleared by project_session_reset.py
-- before product ingress opens.

PRAGMA secure_delete=ON;

CREATE TABLE legacy_session_reset_state (
    singleton      INTEGER PRIMARY KEY CHECK(singleton=1),
    policy_version INTEGER NOT NULL CHECK(policy_version=1),
    phase          TEXT NOT NULL CHECK(
        phase IN ('state_cleared','external_stores_cleared','completed','failed')
    ),
    backup_path    TEXT,
    updated_at     REAL NOT NULL,
    completed_at   REAL,
    error_code     TEXT
);

-- The v32 immutable provenance trigger is correct for normal product writes,
-- but the explicitly approved upgrade reset must remove every old binding.
DROP TRIGGER session_project_binding_no_delete;

DELETE FROM companion_ingress_outbox;
DELETE FROM companion_projection_route_outbox;
DELETE FROM companion_projection_routes;
DELETE FROM companion_session_owners;
DELETE FROM companion_projection_redaction_receipts;
DELETE FROM companion_owner_scope_versions;

DELETE FROM project_run_admissions;
DELETE FROM session_handoff_consumptions;
DELETE FROM session_creation_receipts;
DELETE FROM session_catalog_entries;
DELETE FROM project_session_backfill_outcomes;
DELETE FROM session_project_bindings;
DELETE FROM projects;

DELETE FROM product_memory_outbox;
DELETE FROM memory_session_identities;
DELETE FROM memory_user_bindings;
DELETE FROM memory_users;
DELETE FROM memory_identity_bindings;
DELETE FROM state_db_identity;

DELETE FROM sdk_context_source_bindings;
DELETE FROM sdk_context_sources;
DELETE FROM sdk_context_public_snapshots;
DELETE FROM sdk_provider_attempt_audit;
DELETE FROM sdk_provider_projection_cursors;
DELETE FROM provider_workload_audit;
DELETE FROM provider_binding_reconcile_marker;

DELETE FROM messages_chunks;
DELETE FROM memory_user_feedback;
DELETE FROM memory_qa_set;
DELETE FROM memory_eval_run;
DELETE FROM messages_archive;
DELETE FROM messages;

DELETE FROM session_context_segments;
DELETE FROM session_context_snapshots;
DELETE FROM session_context_usage_history;
DELETE FROM session_context_usage_state_v2;
DELETE FROM session_titles;
DELETE FROM session_delivery_state;
DELETE FROM session_plans;
DELETE FROM goal_tasks;
DELETE FROM session_goals;
DELETE FROM workspace_state;
DELETE FROM ppt_outline_history;
DELETE FROM supervisor_hints;
DELETE FROM code_todos;
DELETE FROM code_session_provider;
DELETE FROM code_sessions;

DELETE FROM sessions;
DELETE FROM sqlite_sequence;

UPDATE project_session_catalog_state
SET catalog_revision=catalog_revision+1
WHERE singleton=1;

DROP TABLE project_session_backfill_outcomes;
DROP TABLE project_session_backfill_state;

CREATE TRIGGER session_project_binding_no_delete
BEFORE DELETE ON session_project_bindings
BEGIN
  SELECT RAISE(ABORT,'session_project_binding_immutable');
END;

INSERT INTO legacy_session_reset_state(
    singleton,policy_version,phase,updated_at
) VALUES(1,1,'state_cleared',strftime('%s','now'));
