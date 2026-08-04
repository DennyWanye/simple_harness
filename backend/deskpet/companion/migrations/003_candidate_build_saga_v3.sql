ALTER TABLE reflection_decisions ADD COLUMN source_kind TEXT;
ALTER TABLE reflection_decisions ADD COLUMN source_ref TEXT;
ALTER TABLE reflection_decisions ADD COLUMN proposal_ref TEXT;
ALTER TABLE reflection_decisions ADD COLUMN proposal_json TEXT;
ALTER TABLE reflection_decisions ADD COLUMN proposal_hash TEXT;

ALTER TABLE candidate_builds RENAME TO candidate_builds_v2;

CREATE TABLE candidate_builds (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    build_id TEXT NOT NULL,
    source_kind TEXT NOT NULL
      CHECK(source_kind IN ('reflection','explicit_user_build')),
    source_ref TEXT NOT NULL,
    reflection_job_id TEXT,
    proposal_ref TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    evidence_set_json TEXT NOT NULL,
    evidence_set_hash TEXT NOT NULL,
    candidate_mode TEXT NOT NULL
      CHECK(candidate_mode IN ('genesis','update','builtin_override')),
    source_fence_json TEXT,
    source_fence_hash TEXT NOT NULL,
    target_fence_json TEXT NOT NULL,
    target_fence_hash TEXT NOT NULL,
    build_permit_ref TEXT,
    build_permit_hash TEXT,
    builder_launch_id TEXT NOT NULL,
    builder_child_run_id TEXT NOT NULL,
    expected_child_start_hash TEXT NOT NULL,
    task_workspace TEXT,
    draft_receipt_ref TEXT,
    draft_receipt_hash TEXT,
    draft_receipt_expectation_json TEXT,
    candidate_ref TEXT,
    candidate_hash TEXT,
    status TEXT NOT NULL CHECK(status IN (
      'proposed','launch_pending','child_precreated','running',
      'handoff_pending','built','failed','inconclusive','cancelled','stale'
    )),
    claim_owner TEXT,
    claim_epoch INTEGER NOT NULL DEFAULT 0 CHECK(claim_epoch >= 0),
    lease_expires_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
    last_error TEXT,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
      (build_permit_ref IS NULL AND build_permit_hash IS NULL)
      OR
      (build_permit_ref IS NOT NULL AND build_permit_hash IS NOT NULL)
    ),
    CHECK (
      status NOT IN ('handoff_pending','built')
      OR
      (draft_receipt_ref IS NOT NULL
       AND draft_receipt_hash IS NOT NULL
       AND draft_receipt_expectation_json IS NOT NULL)
    ),
    CHECK (
      status!='built'
      OR (candidate_ref IS NOT NULL AND candidate_hash IS NOT NULL)
    ),
    PRIMARY KEY(profile_id, profile_generation, build_id),
    UNIQUE(profile_id, profile_generation, source_kind, source_ref),
    UNIQUE(profile_id, profile_generation, builder_launch_id),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

INSERT INTO candidate_builds(
    profile_id,profile_generation,build_id,source_kind,source_ref,
    reflection_job_id,proposal_ref,proposal_json,proposal_hash,evidence_set_json,
    evidence_set_hash,candidate_mode,source_fence_json,source_fence_hash,
    target_fence_json,target_fence_hash,build_permit_ref,build_permit_hash,
    builder_launch_id,builder_child_run_id,expected_child_start_hash,
    draft_receipt_ref,draft_receipt_hash,status,claim_owner,claim_epoch,
    lease_expires_at,attempt,last_error,reason_code,schema_version,
    created_at,updated_at
)
SELECT
    profile_id,profile_generation,build_id,source_kind,source_ref,
    reflection_job_id,proposal_ref,'{}',proposal_hash,evidence_set_json,
    evidence_set_hash,candidate_mode,source_fence_json,
    deskpet_sha256(COALESCE(source_fence_json,'null')),
    target_fence_json,deskpet_sha256(target_fence_json),
    build_permit_ref,build_permit_hash,builder_launch_id,
    builder_child_run_id,expected_child_start_hash,draft_receipt_ref,
    draft_receipt_hash,status,claim_owner,claim_epoch,lease_expires_at,
    attempt,last_error,reason_code,schema_version,created_at,updated_at
FROM candidate_builds_v2;

DROP TABLE candidate_builds_v2;
