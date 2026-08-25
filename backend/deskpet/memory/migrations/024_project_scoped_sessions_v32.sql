-- Project-scoped immutable sessions (user_version=32).
-- Transaction ownership belongs to migrator.py.

CREATE TABLE projects (
    project_id          TEXT PRIMARY KEY,
    display_name        TEXT NOT NULL CHECK(length(trim(display_name)) > 0),
    canonical_root      TEXT NOT NULL,
    root_kind           TEXT NOT NULL CHECK(root_kind IN ('git','folder')),
    filesystem_identity TEXT NOT NULL UNIQUE,
    project_revision    INTEGER NOT NULL DEFAULT 1 CHECK(project_revision >= 1),
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    last_opened_at      REAL NOT NULL
);

CREATE INDEX idx_projects_catalog
    ON projects(last_opened_at DESC, project_id ASC);

CREATE TABLE session_project_bindings (
    session_id         TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE RESTRICT,
    project_id         TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    execution_kind     TEXT NOT NULL CHECK(execution_kind IN ('project_root','explicit')),
    execution_root     TEXT,
    execution_identity TEXT,
    source_session_id  TEXT REFERENCES sessions(id) ON DELETE RESTRICT,
    handoff_json       TEXT,
    binding_version    INTEGER NOT NULL DEFAULT 1 CHECK(binding_version = 1),
    created_at         REAL NOT NULL,
    CHECK(
      (execution_kind='project_root' AND execution_root IS NULL AND execution_identity IS NULL)
      OR
      (execution_kind='explicit' AND execution_root IS NOT NULL AND execution_identity IS NOT NULL)
    )
);

CREATE INDEX idx_session_project_bindings_project
    ON session_project_bindings(project_id, created_at DESC, session_id ASC);

CREATE TABLE session_handoff_consumptions (
    session_id   TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE RESTRICT,
    first_run_id TEXT NOT NULL UNIQUE,
    consumed_at  REAL NOT NULL
);

CREATE TABLE project_run_admissions (
    run_id           TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
    project_id       TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    project_revision INTEGER NOT NULL,
    state            TEXT NOT NULL CHECK(state IN ('active','released')),
    admitted_at      REAL NOT NULL,
    released_at      REAL
);
CREATE INDEX idx_project_run_admissions_active
    ON project_run_admissions(project_id,state);

CREATE TRIGGER session_project_binding_no_update
BEFORE UPDATE ON session_project_bindings
BEGIN
  SELECT RAISE(ABORT,'session_project_binding_immutable');
END;

CREATE TRIGGER session_project_binding_no_delete
BEFORE DELETE ON session_project_bindings
BEGIN
  SELECT RAISE(ABORT,'session_project_binding_immutable');
END;

CREATE TABLE session_creation_receipts (
    request_id   TEXT PRIMARY KEY,
    intent_hash  TEXT NOT NULL,
    session_id   TEXT NOT NULL UNIQUE REFERENCES sessions(id) ON DELETE RESTRICT,
    result_json  TEXT NOT NULL,
    lifecycle    TEXT NOT NULL DEFAULT 'active' CHECK(lifecycle IN ('active','deleted')),
    created_at   REAL NOT NULL,
    deleted_at   REAL
);

CREATE TABLE session_catalog_entries (
    session_id   TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE RESTRICT,
    product_kind TEXT NOT NULL CHECK(product_kind IN ('conversation','internal')),
    created_at   REAL NOT NULL
);

CREATE TABLE project_session_catalog_state (
    singleton       INTEGER PRIMARY KEY CHECK(singleton=1),
    catalog_revision INTEGER NOT NULL CHECK(catalog_revision >= 1)
);
INSERT INTO project_session_catalog_state(singleton,catalog_revision) VALUES(1,1);

CREATE TABLE project_session_backfill_state (
    singleton            INTEGER PRIMARY KEY CHECK(singleton=1),
    phase                TEXT NOT NULL CHECK(phase IN ('pending','scanning','applying','verifying','completed','failed')),
    source_high_water    TEXT,
    last_base_session_id TEXT,
    source_count         INTEGER NOT NULL DEFAULT 0,
    migrated_count       INTEGER NOT NULL DEFAULT 0,
    skipped_count        INTEGER NOT NULL DEFAULT 0,
    conflict_count       INTEGER NOT NULL DEFAULT 0,
    outcome_digest       TEXT,
    backup_path          TEXT,
    backup_sha256        TEXT,
    started_at           REAL,
    updated_at           REAL NOT NULL,
    completed_at         REAL,
    error_code           TEXT
);
INSERT INTO project_session_backfill_state(singleton,phase,updated_at)
VALUES(1,'pending',strftime('%s','now'));

CREATE TABLE project_session_backfill_outcomes (
    base_session_id TEXT PRIMARY KEY,
    source_digest   TEXT NOT NULL,
    outcome         TEXT NOT NULL CHECK(outcome IN ('migrated','invalid','missing','conflict','prebound')),
    project_id      TEXT REFERENCES projects(project_id) ON DELETE RESTRICT,
    detail_code     TEXT,
    recorded_at     REAL NOT NULL
);

-- Revision is deliberately broad: any membership or ordering-relevant write
-- invalidates outstanding cursors in the same transaction as that write.
CREATE TRIGGER catalog_revision_session_insert AFTER INSERT ON sessions BEGIN
  UPDATE project_session_catalog_state SET catalog_revision=catalog_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER catalog_revision_message_insert AFTER INSERT ON messages BEGIN
  UPDATE project_session_catalog_state SET catalog_revision=catalog_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER catalog_revision_message_delete AFTER DELETE ON messages BEGIN
  UPDATE project_session_catalog_state SET catalog_revision=catalog_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER catalog_revision_message_activity_update
AFTER UPDATE OF created_at,session_id ON messages BEGIN
  UPDATE project_session_catalog_state SET catalog_revision=catalog_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER catalog_revision_project_insert AFTER INSERT ON projects BEGIN
  UPDATE project_session_catalog_state SET catalog_revision=catalog_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER catalog_revision_project_update
AFTER UPDATE OF display_name,canonical_root,project_revision,last_opened_at ON projects BEGIN
  UPDATE project_session_catalog_state SET catalog_revision=catalog_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER catalog_revision_binding_insert AFTER INSERT ON session_project_bindings BEGIN
  UPDATE project_session_catalog_state SET catalog_revision=catalog_revision+1 WHERE singleton=1;
END;
CREATE TRIGGER catalog_revision_catalog_entry_insert AFTER INSERT ON session_catalog_entries BEGIN
  UPDATE project_session_catalog_state SET catalog_revision=catalog_revision+1 WHERE singleton=1;
END;

CREATE INDEX idx_session_catalog_product
    ON session_catalog_entries(product_kind, created_at DESC, session_id ASC);
