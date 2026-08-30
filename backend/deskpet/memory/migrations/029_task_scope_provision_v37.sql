-- Recoverable TaskScope task_home provisioning.  This migration deliberately
-- does not create workspace binding authority; proposed_workspace_root is a
-- non-authoritative candidate for the append-only Task 4 binding workflow.

CREATE TABLE task_scope_provision_marker (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    format_epoch TEXT NOT NULL CHECK (format_epoch = 'human-memory-v1'),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    migration_id TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL CHECK (length(migration_sha256) = 64),
    initialized_at REAL NOT NULL
);

CREATE TABLE task_scope_provisions (
    provision_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
    provision_mode TEXT NOT NULL CHECK (provision_mode IN ('managed', 'explicit')),
    trusted_provenance TEXT NOT NULL CHECK (trusted_provenance IN (
        'host_managed_policy', 'trusted_user_selection', 'trusted_project_picker'
    )),
    managed_workspace_root TEXT,
    proposed_workspace_root TEXT,
    proposed_workspace_identity TEXT,
    task_home TEXT NOT NULL UNIQUE,
    staging_path TEXT NOT NULL UNIQUE,
    metadata_location TEXT NOT NULL CHECK (metadata_location IN ('managed', 'project', 'app_data')),
    state TEXT NOT NULL CHECK (state IN (
        'reserved', 'filesystem_ready', 'committed', 'failed_retryable'
    )),
    task_home_identity TEXT,
    failure_code TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id)
);

CREATE TABLE task_scope_provision_events (
    event_id TEXT PRIMARY KEY,
    provision_id TEXT NOT NULL,
    event_sequence INTEGER NOT NULL CHECK (event_sequence > 0),
    from_state TEXT,
    to_state TEXT NOT NULL CHECK (to_state IN (
        'reserved', 'filesystem_ready', 'committed', 'failed_retryable'
    )),
    reason_code TEXT NOT NULL,
    event_hash TEXT NOT NULL CHECK (length(event_hash) = 64),
    event_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(provision_id, event_sequence),
    FOREIGN KEY(provision_id) REFERENCES task_scope_provisions(provision_id)
);

CREATE TABLE task_scope_provision_receipts (
    receipt_id TEXT PRIMARY KEY,
    provision_id TEXT NOT NULL UNIQUE,
    task_scope_id TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
    task_home TEXT NOT NULL UNIQUE,
    task_home_identity TEXT NOT NULL CHECK (length(task_home_identity) = 64),
    proposed_workspace_root TEXT,
    proposed_workspace_identity TEXT,
    metadata_location TEXT NOT NULL CHECK (metadata_location IN ('managed', 'project', 'app_data')),
    receipt_hash TEXT NOT NULL CHECK (length(receipt_hash) = 64),
    receipt_json TEXT NOT NULL,
    committed_at REAL NOT NULL,
    FOREIGN KEY(provision_id) REFERENCES task_scope_provisions(provision_id),
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id)
);

CREATE TRIGGER task_scope_provision_marker_no_update BEFORE UPDATE ON task_scope_provision_marker BEGIN SELECT RAISE(ABORT, 'task_scope_provision_append_only'); END;
CREATE TRIGGER task_scope_provision_marker_no_delete BEFORE DELETE ON task_scope_provision_marker BEGIN SELECT RAISE(ABORT, 'task_scope_provision_append_only'); END;
CREATE TRIGGER task_scope_provisions_no_delete BEFORE DELETE ON task_scope_provisions BEGIN SELECT RAISE(ABORT, 'task_scope_provision_append_only'); END;
CREATE TRIGGER task_scope_provisions_identity_immutable
BEFORE UPDATE ON task_scope_provisions
WHEN NEW.provision_id <> OLD.provision_id
  OR NEW.task_scope_id <> OLD.task_scope_id
  OR NEW.idempotency_key <> OLD.idempotency_key
  OR NEW.request_hash <> OLD.request_hash
  OR NEW.provision_mode <> OLD.provision_mode
  OR NEW.trusted_provenance <> OLD.trusted_provenance
  OR NEW.managed_workspace_root IS NOT OLD.managed_workspace_root
  OR NEW.proposed_workspace_root IS NOT OLD.proposed_workspace_root
  OR NEW.task_home <> OLD.task_home
  OR NEW.staging_path <> OLD.staging_path
  OR NEW.metadata_location <> OLD.metadata_location
  OR (OLD.task_home_identity IS NOT NULL AND NEW.task_home_identity IS NOT OLD.task_home_identity)
  OR (OLD.proposed_workspace_identity IS NOT NULL AND NEW.proposed_workspace_identity IS NOT OLD.proposed_workspace_identity)
BEGIN SELECT RAISE(ABORT, 'task_scope_provision_identity_immutable'); END;
CREATE TRIGGER task_scope_provision_events_no_update BEFORE UPDATE ON task_scope_provision_events BEGIN SELECT RAISE(ABORT, 'task_scope_provision_append_only'); END;
CREATE TRIGGER task_scope_provision_events_no_delete BEFORE DELETE ON task_scope_provision_events BEGIN SELECT RAISE(ABORT, 'task_scope_provision_append_only'); END;
CREATE TRIGGER task_scope_provision_receipts_no_update BEFORE UPDATE ON task_scope_provision_receipts BEGIN SELECT RAISE(ABORT, 'task_scope_provision_append_only'); END;
CREATE TRIGGER task_scope_provision_receipts_no_delete BEFORE DELETE ON task_scope_provision_receipts BEGIN SELECT RAISE(ABORT, 'task_scope_provision_append_only'); END;
