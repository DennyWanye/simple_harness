DROP TRIGGER IF EXISTS trg_evaluation_launch_no_unsafe_retry;
DROP TRIGGER IF EXISTS trg_evaluation_launch_requires_live_case;

ALTER TABLE evaluation_case_launches RENAME TO evaluation_case_launches_v5;
ALTER TABLE evaluation_execution_permits RENAME TO evaluation_execution_permits_v5;

CREATE TABLE evaluation_execution_permits (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    permit_id TEXT NOT NULL,
    evaluation_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('safe_auto','safe_static','user_authorized')),
    candidate_id TEXT NOT NULL,
    package_hash TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    archive_hash TEXT NOT NULL,
    suite_hash TEXT NOT NULL,
    runner_policy_hash TEXT NOT NULL,
    issued_revocation_epoch INTEGER NOT NULL CHECK(issued_revocation_epoch >= 0),
    preflight_ref TEXT NOT NULL,
    preflight_hash TEXT NOT NULL,
    risk_ref TEXT NOT NULL,
    risk_hash TEXT NOT NULL,
    authorization_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('issued','claimed','revoked','expired')),
    permit_hash TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK((mode IN ('safe_auto','safe_static') AND authorization_id IS NULL) OR
          (mode='user_authorized' AND authorization_id IS NOT NULL)),
    PRIMARY KEY(profile_id, profile_generation, permit_id),
    UNIQUE(profile_id, profile_generation, evaluation_id),
    FOREIGN KEY(profile_id, profile_generation, evaluation_id)
      REFERENCES evaluation_runs(profile_id, profile_generation, evaluation_id),
    FOREIGN KEY(profile_id, profile_generation, candidate_id)
      REFERENCES candidate_artifacts(profile_id, profile_generation, candidate_id),
    FOREIGN KEY(profile_id, profile_generation, authorization_id)
      REFERENCES evaluation_authorizations(profile_id, profile_generation, authorization_id)
) WITHOUT ROWID;

INSERT INTO evaluation_execution_permits
SELECT * FROM evaluation_execution_permits_v5;

CREATE TABLE evaluation_case_launches (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    evaluation_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    variant TEXT NOT NULL CHECK(variant IN ('old','candidate')),
    attempt_ordinal INTEGER NOT NULL CHECK(attempt_ordinal >= 1),
    launch_id TEXT NOT NULL,
    candidate_package_hash TEXT NOT NULL,
    candidate_manifest_hash TEXT NOT NULL,
    candidate_archive_hash TEXT NOT NULL,
    suite_hash TEXT NOT NULL,
    permit_mode TEXT NOT NULL CHECK(permit_mode IN ('safe_auto','safe_static','user_authorized')),
    permit_id TEXT NOT NULL,
    permit_hash TEXT NOT NULL,
    case_lease_epoch INTEGER NOT NULL CHECK(case_lease_epoch >= 1),
    revocation_epoch INTEGER NOT NULL CHECK(revocation_epoch >= 0),
    adapter_id TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    adapter_fingerprint TEXT NOT NULL,
    launch_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
      'claimed','started','completed','failed','not_started','unknown',
      'aborting','aborted','cleanup_required')),
    start_outcome TEXT,
    started_ack_at TEXT,
    job_identity TEXT,
    runtime_identity TEXT,
    session_identity TEXT,
    outcome_ref TEXT,
    outcome_hash TEXT,
    cleanup_receipt_hash TEXT,
    survivor_count INTEGER CHECK(survivor_count IS NULL OR survivor_count >= 0),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, evaluation_id, case_id, variant, attempt_ordinal),
    UNIQUE(profile_id, profile_generation, launch_id),
    FOREIGN KEY(profile_id, profile_generation, evaluation_id, case_id, variant)
      REFERENCES evaluation_cases(profile_id, profile_generation, evaluation_id, case_id, variant),
    FOREIGN KEY(profile_id, profile_generation, permit_id)
      REFERENCES evaluation_execution_permits(profile_id, profile_generation, permit_id)
) WITHOUT ROWID;

INSERT INTO evaluation_case_launches
SELECT * FROM evaluation_case_launches_v5;

DROP TABLE evaluation_case_launches_v5;
DROP TABLE evaluation_execution_permits_v5;

CREATE TRIGGER trg_evaluation_launch_no_unsafe_retry
BEFORE INSERT ON evaluation_case_launches
WHEN EXISTS (
  SELECT 1 FROM evaluation_case_launches l
  WHERE l.profile_id=NEW.profile_id AND l.profile_generation=NEW.profile_generation
    AND l.evaluation_id=NEW.evaluation_id AND l.case_id=NEW.case_id AND l.variant=NEW.variant
    AND l.status IN ('claimed','started','unknown','aborting','cleanup_required')
)
BEGIN SELECT RAISE(ABORT, 'evaluation_launch_recovery_required'); END;

CREATE TRIGGER trg_evaluation_launch_requires_live_case
BEFORE INSERT ON evaluation_case_launches
WHEN NOT EXISTS (
  SELECT 1 FROM evaluation_cases c
  JOIN evaluation_runs e
    ON e.profile_id=c.profile_id AND e.profile_generation=c.profile_generation
   AND e.evaluation_id=c.evaluation_id
  JOIN candidate_artifacts a
    ON a.profile_id=e.profile_id AND a.profile_generation=e.profile_generation
   AND a.candidate_id=e.candidate_id
  JOIN evaluation_execution_permits p
    ON p.profile_id=e.profile_id AND p.profile_generation=e.profile_generation
   AND p.evaluation_id=e.evaluation_id
  WHERE c.profile_id=NEW.profile_id AND c.profile_generation=NEW.profile_generation
    AND c.evaluation_id=NEW.evaluation_id AND c.case_id=NEW.case_id AND c.variant=NEW.variant
    AND c.status='leased' AND c.claim_epoch=NEW.case_lease_epoch
    AND e.status IN ('queued','running') AND a.status NOT IN ('invalidated','expired','stale')
    AND p.permit_id=NEW.permit_id AND p.permit_hash=NEW.permit_hash
    AND p.status IN ('issued','claimed')
)
BEGIN SELECT RAISE(ABORT, 'evaluation_launch_not_authorized'); END;
