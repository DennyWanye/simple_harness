ALTER TABLE jobs RENAME TO jobs_v3;

CREATE TABLE jobs (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    job_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
      'queued','leased','waiting_decision',
      'succeeded','failed','cancelled','expired'
    )),
    claim_owner TEXT,
    claim_epoch INTEGER NOT NULL DEFAULT 0 CHECK(claim_epoch>=0),
    lease_expires_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt>=0),
    next_retry_at TEXT,
    budget_reserved_tokens INTEGER NOT NULL DEFAULT 0 CHECK(budget_reserved_tokens>=0),
    budget_actual_tokens INTEGER NOT NULL DEFAULT 0 CHECK(budget_actual_tokens>=0),
    budget_reserved_ms INTEGER NOT NULL DEFAULT 0 CHECK(budget_reserved_ms>=0),
    budget_actual_ms INTEGER NOT NULL DEFAULT 0 CHECK(budget_actual_ms>=0),
    result_ref TEXT,
    result_hash TEXT,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, job_id),
    UNIQUE(profile_id, profile_generation, kind, dedupe_key),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

INSERT INTO jobs
SELECT * FROM jobs_v3;

DROP TABLE jobs_v3;

CREATE INDEX ix_jobs_claim
ON jobs(status, next_retry_at, lease_expires_at, created_at);

CREATE TABLE job_execution_waits (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    job_id TEXT NOT NULL,
    execution_run_id TEXT NOT NULL,
    execution_session_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    decision_nonce TEXT NOT NULL,
    decision_version INTEGER NOT NULL CHECK(decision_version>=0),
    decision_kind TEXT NOT NULL CHECK(decision_kind='permission'),
    call_id TEXT NOT NULL,
    effect_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    capability_hash TEXT NOT NULL,
    scope_hash TEXT NOT NULL,
    wait_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('waiting','settled','cancelled')),
    result_ref TEXT,
    result_hash TEXT,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, job_id),
    UNIQUE(profile_id, profile_generation, decision_id),
    FOREIGN KEY(profile_id, profile_generation, job_id)
      REFERENCES jobs(profile_id, profile_generation, job_id)
) WITHOUT ROWID;

CREATE INDEX ix_job_execution_waits_decision
ON job_execution_waits(
  profile_id, profile_generation, execution_run_id, decision_id, status
);
