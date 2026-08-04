ALTER TABLE run_growth_dependency_evidence
  RENAME TO run_growth_dependency_evidence_v1;
ALTER TABLE run_growth_dependency_items
  RENAME TO run_growth_dependency_items_v1;

CREATE TABLE run_growth_dependency_items (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    snapshot_id TEXT NOT NULL,
    dependency_kind TEXT NOT NULL CHECK(dependency_kind IN (
      'preference','capability_pack','personal_workflow','memory_scope'
    )),
    dependency_id TEXT NOT NULL,
    pack_id TEXT,
    version TEXT,
    manifest_hash TEXT,
    binding_generation INTEGER,
    catalog_content_stamp TEXT,
    content_hash TEXT NOT NULL,
    effect_hash TEXT,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, snapshot_id, dependency_kind, dependency_id),
    FOREIGN KEY(profile_id, profile_generation, snapshot_id)
      REFERENCES run_growth_snapshots(profile_id, profile_generation, snapshot_id)
) WITHOUT ROWID;

INSERT INTO run_growth_dependency_items
SELECT * FROM run_growth_dependency_items_v1;

CREATE TABLE run_growth_dependency_evidence (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    snapshot_id TEXT NOT NULL,
    dependency_kind TEXT NOT NULL,
    dependency_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, snapshot_id, dependency_kind, dependency_id, event_id),
    FOREIGN KEY(profile_id, profile_generation, snapshot_id, dependency_kind, dependency_id)
      REFERENCES run_growth_dependency_items(
        profile_id, profile_generation, snapshot_id, dependency_kind, dependency_id),
    FOREIGN KEY(profile_id, profile_generation, event_id)
      REFERENCES growth_events(profile_id, profile_generation, event_id)
) WITHOUT ROWID;

INSERT INTO run_growth_dependency_evidence
SELECT * FROM run_growth_dependency_evidence_v1;

DROP TABLE run_growth_dependency_evidence_v1;
DROP TABLE run_growth_dependency_items_v1;

CREATE TABLE owner_memory_read_scopes (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    scope_ref TEXT NOT NULL,
    scope_hash TEXT NOT NULL,
    binding_epoch INTEGER NOT NULL CHECK(binding_epoch >= 1),
    session_set_version INTEGER NOT NULL CHECK(session_set_version >= 1),
    session_set_hash TEXT NOT NULL,
    as_of_message_id INTEGER NOT NULL CHECK(as_of_message_id >= 0),
    session_ids_json TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, scope_ref),
    UNIQUE(profile_id, profile_generation, scope_hash),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;
