-- Append-only TaskScope workspace binding authority. Public SDK DTO hashes are
-- audit lineage only; exact Host durable records in these tables are authority.

CREATE TABLE task_workspace_binding_marker (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    format_epoch TEXT NOT NULL CHECK (format_epoch = 'human-memory-v1'),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    migration_id TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL CHECK (length(migration_sha256) = 64),
    initialized_at REAL NOT NULL
);

CREATE TABLE task_workspace_binding_proposals (
    proposal_id TEXT PRIMARY KEY,
    proposal_hash TEXT NOT NULL UNIQUE CHECK (length(proposal_hash) = 64),
    run_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    task_scope_id TEXT NOT NULL,
    root_identity_hash TEXT NOT NULL CHECK (length(root_identity_hash) = 64),
    base_revision INTEGER NOT NULL CHECK (base_revision >= 0),
    idempotency_key TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    UNIQUE(task_scope_id, idempotency_key),
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id)
);

CREATE TABLE task_workspace_manual_challenges (
    challenge_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE,
    challenge_hash TEXT NOT NULL CHECK (length(challenge_hash) = 64),
    sdk_challenge_hash TEXT NOT NULL CHECK (length(sdk_challenge_hash) = 64),
    authorization_nonce TEXT NOT NULL UNIQUE,
    challenge_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    FOREIGN KEY(proposal_id) REFERENCES task_workspace_binding_proposals(proposal_id)
);

CREATE TABLE task_workspace_manual_decisions (
    receipt_id TEXT PRIMARY KEY,
    challenge_id TEXT NOT NULL UNIQUE,
    receipt_hash TEXT NOT NULL CHECK (length(receipt_hash) = 64),
    decision TEXT NOT NULL CHECK (decision IN ('allow', 'deny')),
    host_receipt_id TEXT NOT NULL UNIQUE,
    decision_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    FOREIGN KEY(challenge_id) REFERENCES task_workspace_manual_challenges(challenge_id)
);

CREATE TABLE task_workspace_run_mode_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL UNIQUE CHECK (length(request_hash) = 64),
    run_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    task_scope_id TEXT NOT NULL,
    binding_set_revision INTEGER NOT NULL CHECK (binding_set_revision >= 0),
    context_snapshot_revision INTEGER NOT NULL CHECK (context_snapshot_revision > 0),
    configuration_revision INTEGER NOT NULL CHECK (configuration_revision > 0),
    mode TEXT NOT NULL CHECK (mode = 'auto'),
    snapshot_hash TEXT NOT NULL CHECK (length(snapshot_hash) = 64),
    request_json TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id)
);

CREATE TABLE task_workspace_binding_grants (
    grant_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE,
    proposal_hash TEXT NOT NULL CHECK (length(proposal_hash) = 64),
    source TEXT NOT NULL CHECK (source IN ('manual', 'auto')),
    source_authority_ref TEXT NOT NULL,
    source_authority_hash TEXT NOT NULL CHECK (length(source_authority_hash) = 64),
    host_grant_ref TEXT NOT NULL UNIQUE,
    host_grant_hash TEXT NOT NULL CHECK (length(host_grant_hash) = 64),
    grant_hash TEXT NOT NULL UNIQUE CHECK (length(grant_hash) = 64),
    grant_json TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    FOREIGN KEY(proposal_id) REFERENCES task_workspace_binding_proposals(proposal_id)
);

CREATE TABLE task_workspace_binding_revisions (
    receipt_id TEXT PRIMARY KEY,
    binding_id TEXT NOT NULL,
    task_scope_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    base_revision INTEGER NOT NULL CHECK (base_revision >= 0),
    binding_set_revision INTEGER NOT NULL CHECK (binding_set_revision = base_revision + 1),
    parent_receipt_id TEXT,
    parent_receipt_hash TEXT CHECK (parent_receipt_hash IS NULL OR length(parent_receipt_hash) = 64),
    previous_root_set_digest TEXT NOT NULL CHECK (length(previous_root_set_digest) = 64),
    root_set_digest TEXT NOT NULL CHECK (length(root_set_digest) = 64),
    root_identity_hashes_json TEXT NOT NULL,
    appended_root_id TEXT NOT NULL,
    appended_root_identity_hash TEXT NOT NULL CHECK (length(appended_root_identity_hash) = 64),
    grant_id TEXT NOT NULL UNIQUE,
    grant_hash TEXT NOT NULL CHECK (length(grant_hash) = 64),
    host_receipt_ref TEXT NOT NULL UNIQUE,
    host_receipt_hash TEXT NOT NULL CHECK (length(host_receipt_hash) = 64),
    receipt_hash TEXT NOT NULL UNIQUE CHECK (length(receipt_hash) = 64),
    receipt_json TEXT NOT NULL,
    committed_at REAL NOT NULL,
    UNIQUE(task_scope_id, binding_set_revision),
    CHECK (
        (base_revision = 0 AND parent_receipt_id IS NULL AND parent_receipt_hash IS NULL)
        OR
        (base_revision > 0 AND parent_receipt_id IS NOT NULL AND parent_receipt_hash IS NOT NULL)
    ),
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id),
    FOREIGN KEY(parent_receipt_id) REFERENCES task_workspace_binding_revisions(receipt_id),
    FOREIGN KEY(grant_id) REFERENCES task_workspace_binding_grants(grant_id)
);

CREATE TABLE task_workspace_binding_roots (
    binding_root_id TEXT PRIMARY KEY,
    task_scope_id TEXT NOT NULL,
    root_id TEXT NOT NULL,
    canonical_path TEXT NOT NULL,
    path_hash TEXT NOT NULL CHECK (length(path_hash) = 64),
    filesystem_identity_kind TEXT NOT NULL CHECK (filesystem_identity_kind IN ('posix_inode', 'windows_file_id')),
    filesystem_volume_id TEXT NOT NULL,
    filesystem_object_id TEXT NOT NULL,
    filesystem_identity_hash TEXT NOT NULL CHECK (length(filesystem_identity_hash) = 64),
    root_identity_hash TEXT NOT NULL CHECK (length(root_identity_hash) = 64),
    first_binding_set_revision INTEGER NOT NULL CHECK (first_binding_set_revision > 0),
    receipt_id TEXT NOT NULL,
    root_json TEXT NOT NULL,
    committed_at REAL NOT NULL,
    UNIQUE(task_scope_id, root_id),
    UNIQUE(task_scope_id, canonical_path),
    UNIQUE(task_scope_id, root_identity_hash),
    FOREIGN KEY(task_scope_id, first_binding_set_revision)
        REFERENCES task_workspace_binding_revisions(task_scope_id, binding_set_revision),
    FOREIGN KEY(receipt_id) REFERENCES task_workspace_binding_revisions(receipt_id)
);

CREATE TABLE task_workspace_binding_heads (
    task_scope_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    binding_id TEXT NOT NULL UNIQUE,
    current_revision INTEGER NOT NULL CHECK (current_revision > 0),
    current_receipt_id TEXT NOT NULL UNIQUE,
    current_receipt_hash TEXT NOT NULL CHECK (length(current_receipt_hash) = 64),
    root_set_digest TEXT NOT NULL CHECK (length(root_set_digest) = 64),
    updated_at REAL NOT NULL,
    FOREIGN KEY(task_scope_id, current_revision)
        REFERENCES task_workspace_binding_revisions(task_scope_id, binding_set_revision),
    FOREIGN KEY(current_receipt_id) REFERENCES task_workspace_binding_revisions(receipt_id)
);

CREATE TRIGGER task_workspace_binding_marker_no_update BEFORE UPDATE ON task_workspace_binding_marker BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_binding_marker_no_delete BEFORE DELETE ON task_workspace_binding_marker BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_binding_proposals_no_update BEFORE UPDATE ON task_workspace_binding_proposals BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_binding_proposals_no_delete BEFORE DELETE ON task_workspace_binding_proposals BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_manual_challenges_no_update BEFORE UPDATE ON task_workspace_manual_challenges BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_manual_challenges_no_delete BEFORE DELETE ON task_workspace_manual_challenges BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_manual_decisions_no_update BEFORE UPDATE ON task_workspace_manual_decisions BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_manual_decisions_no_delete BEFORE DELETE ON task_workspace_manual_decisions BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_run_mode_snapshots_no_update BEFORE UPDATE ON task_workspace_run_mode_snapshots BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_run_mode_snapshots_no_delete BEFORE DELETE ON task_workspace_run_mode_snapshots BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_binding_grants_no_update BEFORE UPDATE ON task_workspace_binding_grants BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_binding_grants_no_delete BEFORE DELETE ON task_workspace_binding_grants BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_binding_revisions_no_update BEFORE UPDATE ON task_workspace_binding_revisions BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_binding_revisions_no_delete BEFORE DELETE ON task_workspace_binding_revisions BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_binding_roots_no_update BEFORE UPDATE ON task_workspace_binding_roots BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_binding_roots_no_delete BEFORE DELETE ON task_workspace_binding_roots BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_binding_heads_no_delete BEFORE DELETE ON task_workspace_binding_heads BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_append_only'); END;
CREATE TRIGGER task_workspace_binding_heads_guard
BEFORE UPDATE ON task_workspace_binding_heads
WHEN NEW.task_scope_id <> OLD.task_scope_id
  OR NEW.subject <> OLD.subject
  OR NEW.binding_id <> OLD.binding_id
  OR NEW.current_revision <> OLD.current_revision + 1
  OR NEW.current_receipt_id = OLD.current_receipt_id
  OR NEW.current_receipt_hash = OLD.current_receipt_hash
  OR NEW.root_set_digest = OLD.root_set_digest
BEGIN SELECT RAISE(ABORT, 'task_workspace_binding_head_invalid'); END;
