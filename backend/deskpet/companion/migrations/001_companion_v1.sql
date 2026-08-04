PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS companion_schema (
    component TEXT PRIMARY KEY CHECK(component = 'companion'),
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    installed_at TEXT NOT NULL
) WITHOUT ROWID;
INSERT OR IGNORE INTO companion_schema(component, schema_version, installed_at)
VALUES ('companion', 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

CREATE TABLE IF NOT EXISTS profiles (
    profile_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation >= 1),
    identity_namespace_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','deleted')),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    PRIMARY KEY(profile_id, generation)
) WITHOUT ROWID;
CREATE UNIQUE INDEX IF NOT EXISTS uq_profiles_active_identity
ON profiles(identity_namespace_hash) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS profile_bindings (
    device_scope TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    binding_epoch INTEGER NOT NULL CHECK(binding_epoch >= 1),
    status TEXT NOT NULL CHECK(status IN ('unready','ready')),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    updated_at TEXT NOT NULL,
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS profile_control_leases (
    device_scope TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    requested_window_label TEXT NOT NULL,
    requested_scope TEXT NOT NULL,
    window_label TEXT,
    scope TEXT,
    control_epoch INTEGER NOT NULL CHECK(control_epoch >= 1),
    challenge_hash TEXT NOT NULL,
    last_seq INTEGER NOT NULL DEFAULT 0 CHECK(last_seq >= 0),
    backend_process_instance_id TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('challenged','active','revoked','expired')),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    CHECK (
      (status = 'challenged' AND window_label IS NULL AND scope IS NULL) OR
      (status <> 'challenged' AND
       ((window_label = 'main' AND scope = 'identity_bind') OR
        (window_label = 'message-panel' AND scope = 'companion_action')))
    ),
    PRIMARY KEY(device_scope, connection_id)
) WITHOUT ROWID;
CREATE UNIQUE INDEX IF NOT EXISTS uq_profile_control_active
ON profile_control_leases(device_scope, window_label, scope)
WHERE status = 'active';

CREATE TABLE IF NOT EXISTS profile_control_commands (
    device_scope TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    request_seq INTEGER NOT NULL CHECK(request_seq >= 1),
    backend_process_instance_id TEXT NOT NULL,
    credential_nonce_hash TEXT NOT NULL,
    command_kind TEXT NOT NULL,
    canonical_schema TEXT NOT NULL CHECK(canonical_schema = 'control-command-canonical-v1'),
    request_hash TEXT NOT NULL,
    binding_epoch INTEGER NOT NULL CHECK(binding_epoch >= 1),
    status TEXT NOT NULL CHECK(status IN ('claimed','completed','failed','unknown')),
    result_ref TEXT,
    result_hash TEXT,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(device_scope, connection_id, request_seq),
    UNIQUE(backend_process_instance_id, connection_id, credential_nonce_hash),
    FOREIGN KEY(device_scope, connection_id)
      REFERENCES profile_control_leases(device_scope, connection_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS companion_detail_versions (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    detail_version INTEGER NOT NULL CHECK(detail_version >= 1),
    detail_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS growth_events (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    event_id TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    context_key TEXT NOT NULL,
    root_run_id TEXT NOT NULL,
    retry_of TEXT,
    payload_json TEXT,
    content_state TEXT NOT NULL CHECK(content_state IN ('live','tombstoned')),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, event_id),
    UNIQUE(profile_id, profile_generation, source_kind, source_ref),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS preferences (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    preference_key TEXT NOT NULL,
    recent_value_json TEXT,
    long_term_value_json TEXT,
    explicit_value_json TEXT,
    inferred_value_json TEXT,
    authority TEXT NOT NULL CHECK(authority IN ('explicit','inferred','mixed','none')),
    state_version INTEGER NOT NULL CHECK(state_version >= 1),
    valid_until TEXT,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, preference_key),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS preference_evidence (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    preference_key TEXT NOT NULL,
    event_id TEXT NOT NULL,
    context_key TEXT NOT NULL,
    weight REAL NOT NULL,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, preference_key, event_id),
    FOREIGN KEY(profile_id, profile_generation, preference_key)
      REFERENCES preferences(profile_id, profile_generation, preference_key),
    FOREIGN KEY(profile_id, profile_generation, event_id)
      REFERENCES growth_events(profile_id, profile_generation, event_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS growth_targets (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    target_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('skill','workflow')),
    stable_name TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    governance_domain TEXT NOT NULL CHECK(governance_domain = 'companion_growth'),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, target_id),
    UNIQUE(profile_id, profile_generation, kind, stable_name),
    UNIQUE(profile_id, profile_generation, pack_id),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS growth_target_reservations (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    kind TEXT NOT NULL CHECK(kind IN ('skill','workflow')),
    stable_name TEXT NOT NULL,
    target_id TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    candidate_id TEXT,
    reservation_version INTEGER NOT NULL CHECK(reservation_version >= 1),
    status TEXT NOT NULL CHECK(status IN ('held','released','consumed')),
    expires_at TEXT,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, kind, stable_name),
    UNIQUE(profile_id, profile_generation, target_id),
    FOREIGN KEY(profile_id, profile_generation, target_id)
      REFERENCES growth_targets(profile_id, profile_generation, target_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS candidate_packages (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    package_id TEXT NOT NULL,
    candidate_mode TEXT NOT NULL CHECK(candidate_mode IN ('genesis','update','builtin_override')),
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    candidate_content_hash TEXT NOT NULL,
    candidate_manifest_hash TEXT NOT NULL,
    candidate_package_hash TEXT NOT NULL,
    archive_hash TEXT NOT NULL,
    source_facts_json TEXT NOT NULL,
    target_facts_json TEXT NOT NULL,
    effect_topology_hash TEXT NOT NULL,
    governance_domain TEXT NOT NULL CHECK(governance_domain = 'companion_growth'),
    content_state TEXT NOT NULL CHECK(content_state IN ('live','redacted')),
    blob_cleanup_state TEXT NOT NULL CHECK(blob_cleanup_state IN ('live','pending','deleted','cleanup_required')),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, package_id),
    UNIQUE(profile_id, profile_generation, candidate_content_hash),
    UNIQUE(profile_id, profile_generation, candidate_package_hash),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS candidate_package_blobs (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    package_id TEXT NOT NULL,
    blob_kind TEXT NOT NULL,
    blob_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    payload BLOB,
    cleanup_state TEXT NOT NULL CHECK(cleanup_state IN ('live','pending','deleted','cleanup_required')),
    deletion_receipt_hash TEXT,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK((cleanup_state = 'live' AND payload IS NOT NULL) OR cleanup_state <> 'live'),
    PRIMARY KEY(profile_id, profile_generation, package_id, blob_kind, blob_id),
    FOREIGN KEY(profile_id, profile_generation, package_id)
      REFERENCES candidate_packages(profile_id, profile_generation, package_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS candidate_package_files (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    package_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    file_kind TEXT NOT NULL,
    file_mode INTEGER NOT NULL CHECK(file_mode >= 0),
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    blob_kind TEXT NOT NULL DEFAULT 'file' CHECK(blob_kind = 'file'),
    blob_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    CHECK(relative_path <> '' AND substr(relative_path,1,1) <> '/' AND instr(relative_path,'\\') = 0),
    CHECK(relative_path <> '..' AND relative_path NOT LIKE '../%' AND relative_path NOT LIKE '%/../%'),
    CHECK(file_kind <> 'symlink'),
    PRIMARY KEY(profile_id, profile_generation, package_id, relative_path),
    FOREIGN KEY(profile_id, profile_generation, package_id)
      REFERENCES candidate_packages(profile_id, profile_generation, package_id),
    FOREIGN KEY(profile_id, profile_generation, package_id, blob_kind, blob_id)
      REFERENCES candidate_package_blobs(profile_id, profile_generation, package_id, blob_kind, blob_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS candidate_artifacts (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    candidate_id TEXT NOT NULL,
    candidate_attempt_key TEXT NOT NULL,
    package_id TEXT NOT NULL,
    proposal_source_kind TEXT NOT NULL CHECK(proposal_source_kind IN ('reflection','explicit_user_build')),
    proposal_source_ref TEXT NOT NULL,
    proposal_source_hash TEXT NOT NULL,
    reflection_job_id TEXT,
    candidate_mode TEXT NOT NULL CHECK(candidate_mode IN ('genesis','update','builtin_override')),
    target_id TEXT NOT NULL,
    source_owner_key TEXT,
    source_scope TEXT,
    source_scope_key TEXT,
    source_version TEXT,
    source_manifest_hash TEXT,
    source_binding_generation INTEGER,
    target_owner_key TEXT NOT NULL,
    target_scope TEXT NOT NULL,
    target_scope_key TEXT NOT NULL,
    target_expected_absent INTEGER NOT NULL CHECK(target_expected_absent IN (0,1)),
    target_expected_binding_generation INTEGER NOT NULL CHECK(target_expected_binding_generation >= 0),
    evidence_set_hash TEXT NOT NULL,
    reservation_version INTEGER,
    attempt_generation INTEGER NOT NULL CHECK(attempt_generation >= 1),
    status TEXT NOT NULL CHECK(status IN (
      'proposed','preflight_failed','awaiting_eval_authorization','evaluating','eligible',
      'awaiting_activation_confirmation','activation_pending','activating','active',
      'activation_failed','rejected','stale','invalidated','expired','quarantined',
      'rollback_pending','rolled_back','disabled')),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
      (candidate_mode='genesis' AND source_owner_key IS NULL AND source_scope IS NULL
       AND source_scope_key IS NULL AND source_binding_generation IS NULL
       AND source_version IS NULL AND source_manifest_hash IS NULL
       AND target_expected_absent=1 AND reservation_version IS NOT NULL
       AND target_expected_binding_generation=0)
      OR
      (candidate_mode='update' AND source_owner_key=target_owner_key
       AND source_scope=target_scope AND source_scope_key=target_scope_key
       AND source_version IS NOT NULL AND source_manifest_hash IS NOT NULL
       AND source_binding_generation=target_expected_binding_generation
       AND target_expected_absent=0 AND reservation_version IS NULL
       AND target_expected_binding_generation>=1)
      OR
      (candidate_mode='builtin_override' AND source_owner_key='builtin'
       AND source_scope='builtin' AND source_version IS NOT NULL
       AND source_manifest_hash IS NOT NULL AND source_binding_generation>=1
       AND target_owner_key LIKE 'companion:%' AND target_scope='user'
       AND target_expected_absent=1 AND target_expected_binding_generation=0
       AND reservation_version IS NULL)
    ),
    PRIMARY KEY(profile_id, profile_generation, candidate_id),
    UNIQUE(profile_id, profile_generation, candidate_attempt_key),
    UNIQUE(profile_id, profile_generation, proposal_source_kind, proposal_source_ref),
    FOREIGN KEY(profile_id, profile_generation, package_id)
      REFERENCES candidate_packages(profile_id, profile_generation, package_id),
    FOREIGN KEY(profile_id, profile_generation, target_id)
      REFERENCES growth_targets(profile_id, profile_generation, target_id)
) WITHOUT ROWID;

CREATE UNIQUE INDEX IF NOT EXISTS uq_candidate_reflection_job
ON candidate_artifacts(profile_id, profile_generation, reflection_job_id)
WHERE reflection_job_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS candidate_evidence (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    candidate_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, candidate_id, event_id),
    FOREIGN KEY(profile_id, profile_generation, candidate_id)
      REFERENCES candidate_artifacts(profile_id, profile_generation, candidate_id),
    FOREIGN KEY(profile_id, profile_generation, event_id)
      REFERENCES growth_events(profile_id, profile_generation, event_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS candidate_package_sources (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    package_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    build_id TEXT,
    evidence_set_json TEXT NOT NULL,
    evidence_set_hash TEXT NOT NULL,
    builder_launch_id TEXT,
    builder_child_run_id TEXT,
    builder_receipt_ref TEXT NOT NULL,
    builder_receipt_hash TEXT NOT NULL,
    provenance_state TEXT NOT NULL CHECK(provenance_state IN ('live','forgotten')),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, package_id, candidate_id),
    FOREIGN KEY(profile_id, profile_generation, package_id)
      REFERENCES candidate_packages(profile_id, profile_generation, package_id),
    FOREIGN KEY(profile_id, profile_generation, candidate_id)
      REFERENCES candidate_artifacts(profile_id, profile_generation, candidate_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS reflection_decisions (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    job_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('candidate','abstain','insufficient','stale','noop')),
    reason_code TEXT NOT NULL,
    evidence_set_hash TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, job_id, decision_id),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS candidate_builds (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    build_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('reflection','explicit_user_build')),
    source_ref TEXT NOT NULL,
    reflection_job_id TEXT,
    proposal_ref TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    evidence_set_json TEXT NOT NULL,
    evidence_set_hash TEXT NOT NULL,
    candidate_mode TEXT NOT NULL CHECK(candidate_mode IN ('genesis','update','builtin_override')),
    source_fence_json TEXT,
    target_fence_json TEXT NOT NULL,
    build_permit_ref TEXT NOT NULL,
    build_permit_hash TEXT NOT NULL,
    builder_launch_id TEXT NOT NULL,
    builder_child_run_id TEXT NOT NULL,
    expected_child_start_hash TEXT NOT NULL,
    draft_receipt_ref TEXT,
    draft_receipt_hash TEXT,
    status TEXT NOT NULL CHECK(status IN (
      'proposed','launch_pending','child_precreated','running','handoff_pending',
      'built','failed','inconclusive','cancelled','stale')),
    claim_owner TEXT,
    claim_epoch INTEGER NOT NULL DEFAULT 0 CHECK(claim_epoch >= 0),
    lease_expires_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
    last_error TEXT,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, build_id),
    UNIQUE(profile_id, profile_generation, source_kind, source_ref),
    UNIQUE(profile_id, profile_generation, builder_launch_id),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS evaluation_runs (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    evaluation_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    candidate_mode TEXT NOT NULL CHECK(candidate_mode IN ('genesis','update','builtin_override')),
    suite_hash TEXT NOT NULL,
    attempt_key TEXT NOT NULL,
    baseline_kind TEXT NOT NULL CHECK(baseline_kind IN ('source_pack','capability_absent_v1')),
    old_snapshot_hash TEXT NOT NULL,
    candidate_snapshot_hash TEXT NOT NULL,
    source_owner_key TEXT,
    source_scope TEXT,
    source_scope_key TEXT,
    source_pack_id TEXT,
    source_version TEXT,
    source_manifest_hash TEXT,
    source_binding_generation INTEGER,
    absent_baseline_ref TEXT,
    absent_baseline_hash TEXT,
    runner_id TEXT NOT NULL,
    runner_policy_hash TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    execution_permit_ref TEXT,
    execution_permit_hash TEXT,
    status TEXT NOT NULL CHECK(status IN ('queued','running','passed','failed','inconclusive')),
    claim_owner TEXT,
    claim_epoch INTEGER NOT NULL DEFAULT 0 CHECK(claim_epoch >= 0),
    lease_expires_at TEXT,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
      (candidate_mode='genesis' AND baseline_kind='capability_absent_v1'
       AND source_pack_id IS NULL AND absent_baseline_ref IS NOT NULL AND absent_baseline_hash IS NOT NULL)
      OR
      (candidate_mode IN ('update','builtin_override') AND baseline_kind='source_pack'
       AND source_pack_id IS NOT NULL AND source_version IS NOT NULL
       AND source_manifest_hash IS NOT NULL AND source_binding_generation>=1
       AND absent_baseline_ref IS NULL AND absent_baseline_hash IS NULL)
    ),
    PRIMARY KEY(profile_id, profile_generation, evaluation_id),
    UNIQUE(profile_id, profile_generation, candidate_id, suite_hash, attempt_key),
    FOREIGN KEY(profile_id, profile_generation, candidate_id)
      REFERENCES candidate_artifacts(profile_id, profile_generation, candidate_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS evaluation_case_inputs (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    evaluation_id TEXT NOT NULL,
    input_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('packaged_suite','historical_replay')),
    resource_ref TEXT,
    resource_hash TEXT,
    source_event_refs_json TEXT,
    input_envelope_blob BLOB NOT NULL,
    input_hash TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    adapter_build_fingerprint TEXT NOT NULL,
    assertion_ref TEXT NOT NULL,
    assertion_hash TEXT NOT NULL,
    read_tool_fixture_blob BLOB NOT NULL,
    read_tool_fixture_root_hash TEXT NOT NULL,
    evaluation_tool_adapter_map_json TEXT NOT NULL,
    content_state TEXT NOT NULL CHECK(content_state IN ('live','redacted')),
    cleanup_receipt_hash TEXT,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, evaluation_id, input_id),
    UNIQUE(profile_id, profile_generation, evaluation_id, case_id),
    FOREIGN KEY(profile_id, profile_generation, evaluation_id)
      REFERENCES evaluation_runs(profile_id, profile_generation, evaluation_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS evaluation_cases (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    evaluation_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    variant TEXT NOT NULL CHECK(variant IN ('old','candidate')),
    input_id TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    manifest_case_version TEXT NOT NULL,
    blind_label TEXT NOT NULL,
    expected_kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','leased','cleanup_required','committed','failed','inconclusive')),
    claim_owner TEXT,
    claim_epoch INTEGER NOT NULL DEFAULT 0 CHECK(claim_epoch >= 0),
    lease_expires_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
    next_retry_at TEXT,
    recovery_only INTEGER NOT NULL DEFAULT 0 CHECK(recovery_only IN (0,1)),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, evaluation_id, case_id, variant),
    FOREIGN KEY(profile_id, profile_generation, evaluation_id, input_id)
      REFERENCES evaluation_case_inputs(profile_id, profile_generation, evaluation_id, input_id)
) WITHOUT ROWID;

CREATE TRIGGER IF NOT EXISTS trg_evaluation_case_variant_input_match_insert
BEFORE INSERT ON evaluation_cases
WHEN EXISTS (
  SELECT 1 FROM evaluation_cases c
  WHERE c.profile_id=NEW.profile_id AND c.profile_generation=NEW.profile_generation
    AND c.evaluation_id=NEW.evaluation_id AND c.case_id=NEW.case_id
    AND (c.input_id<>NEW.input_id OR c.input_hash<>NEW.input_hash)
)
BEGIN SELECT RAISE(ABORT, 'evaluation_variant_input_mismatch'); END;

CREATE TABLE IF NOT EXISTS evaluation_authorizations (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    authorization_id TEXT NOT NULL,
    nonce TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    package_hash TEXT NOT NULL,
    suite_hash TEXT NOT NULL,
    runner_policy_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    actor TEXT NOT NULL,
    risk_ack TEXT NOT NULL CHECK(risk_ack='no_os_sandbox'),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, authorization_id),
    UNIQUE(profile_id, profile_generation, nonce),
    FOREIGN KEY(profile_id, profile_generation, candidate_id)
      REFERENCES candidate_artifacts(profile_id, profile_generation, candidate_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS evaluation_execution_permits (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    permit_id TEXT NOT NULL,
    evaluation_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('safe_auto','user_authorized')),
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
    CHECK((mode='safe_auto' AND authorization_id IS NULL) OR
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

CREATE TABLE IF NOT EXISTS evaluation_case_launches (
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
    permit_mode TEXT NOT NULL CHECK(permit_mode IN ('safe_auto','user_authorized')),
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

CREATE TRIGGER IF NOT EXISTS trg_evaluation_launch_no_unsafe_retry
BEFORE INSERT ON evaluation_case_launches
WHEN EXISTS (
  SELECT 1 FROM evaluation_case_launches l
  WHERE l.profile_id=NEW.profile_id AND l.profile_generation=NEW.profile_generation
    AND l.evaluation_id=NEW.evaluation_id AND l.case_id=NEW.case_id AND l.variant=NEW.variant
    AND l.status IN ('claimed','started','unknown','aborting','cleanup_required')
)
BEGIN SELECT RAISE(ABORT, 'evaluation_launch_recovery_required'); END;

CREATE TRIGGER IF NOT EXISTS trg_evaluation_launch_requires_live_case
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

CREATE TABLE IF NOT EXISTS evaluation_results (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    evaluation_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    variant TEXT NOT NULL CHECK(variant IN ('old','candidate')),
    assertions_json TEXT NOT NULL,
    judge_result_json TEXT NOT NULL,
    usage_json TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, evaluation_id, case_id, variant),
    FOREIGN KEY(profile_id, profile_generation, evaluation_id, case_id, variant)
      REFERENCES evaluation_cases(profile_id, profile_generation, evaluation_id, case_id, variant)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS evaluation_reports (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    report_id TEXT NOT NULL,
    evaluation_id TEXT NOT NULL,
    candidate_package_hash TEXT NOT NULL,
    dataset_hash TEXT NOT NULL,
    suite_hash TEXT NOT NULL,
    results_root_hash TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK(verdict IN ('passed','failed','inconclusive')),
    reason_code TEXT NOT NULL,
    required_case_count INTEGER NOT NULL CHECK(required_case_count >= 1),
    committed_result_count INTEGER NOT NULL CHECK(committed_result_count >= 0),
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, report_id),
    UNIQUE(profile_id, profile_generation, evaluation_id),
    FOREIGN KEY(profile_id, profile_generation, evaluation_id)
      REFERENCES evaluation_runs(profile_id, profile_generation, evaluation_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS risk_assessments (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    risk_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    candidate_package_hash TEXT NOT NULL,
    static_preflight_json TEXT NOT NULL,
    effect_topology_diff_json TEXT NOT NULL,
    risk TEXT NOT NULL CHECK(risk IN ('low','medium','high','unknown')),
    risk_hash TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, risk_id),
    UNIQUE(profile_id, profile_generation, candidate_id, candidate_package_hash),
    FOREIGN KEY(profile_id, profile_generation, candidate_id)
      REFERENCES candidate_artifacts(profile_id, profile_generation, candidate_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS growth_decisions (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    decision_id TEXT NOT NULL,
    nonce TEXT NOT NULL,
    candidate_id TEXT,
    report_id TEXT,
    risk_id TEXT,
    candidate_mode TEXT CHECK(candidate_mode IN ('genesis','update','builtin_override')),
    source_fence_json TEXT,
    target_owner_key TEXT NOT NULL,
    target_scope TEXT NOT NULL,
    target_scope_key TEXT NOT NULL,
    target_expected_absent INTEGER NOT NULL CHECK(target_expected_absent IN (0,1)),
    target_expected_binding_generation INTEGER NOT NULL CHECK(target_expected_binding_generation >= 0),
    decision TEXT NOT NULL CHECK(decision IN ('activate','reject','rollback','stale')),
    actor TEXT NOT NULL CHECK(actor IN ('user','system')),
    reason_code TEXT NOT NULL,
    activation_package_hash TEXT,
    activation_code_digest TEXT,
    activation_risk_ack TEXT NOT NULL CHECK(activation_risk_ack IN ('none','persistent_local_code_no_os_sandbox')),
    decision_hash TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, decision_id),
    UNIQUE(profile_id, profile_generation, nonce),
    FOREIGN KEY(profile_id, profile_generation, candidate_id)
      REFERENCES candidate_artifacts(profile_id, profile_generation, candidate_id),
    FOREIGN KEY(profile_id, profile_generation, report_id)
      REFERENCES evaluation_reports(profile_id, profile_generation, report_id),
    FOREIGN KEY(profile_id, profile_generation, risk_id)
      REFERENCES risk_assessments(profile_id, profile_generation, risk_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS capability_activation_requests (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    activation_request_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('install','update','rollback','uninstall','disable')),
    candidate_id TEXT,
    candidate_mode TEXT CHECK(candidate_mode IN ('genesis','update','builtin_override')),
    rollback_kind TEXT CHECK(rollback_kind IS NULL OR rollback_kind IN ('same_owner_version','remove_override')),
    activation_mode TEXT NOT NULL CHECK(activation_mode IN ('normal','quarantine_release')),
    target_owner_key TEXT NOT NULL,
    target_scope TEXT NOT NULL,
    target_scope_key TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    target_version TEXT,
    target_manifest_hash TEXT,
    candidate_package_hash TEXT,
    archive_hash TEXT,
    code_digest TEXT,
    source_fence_json TEXT,
    target_expected_absent INTEGER NOT NULL CHECK(target_expected_absent IN (0,1)),
    target_expected_binding_generation INTEGER NOT NULL CHECK(target_expected_binding_generation >= 0),
    report_id TEXT,
    risk_id TEXT,
    decision_id TEXT,
    risk_ack TEXT,
    cause_ref TEXT,
    manager_idempotency_key TEXT NOT NULL,
    manager_operation_id TEXT,
    runtime_set_ref TEXT,
    runtime_set_hash TEXT,
    expected_quarantine_generation INTEGER,
    expected_support_set_hash TEXT,
    status TEXT NOT NULL CHECK(status IN (
      'pending','claimed','staging','staged','publishing','succeeded',
      'failed','cancelled','stale','unknown','cleanup_required')),
    claim_owner TEXT,
    claim_epoch INTEGER NOT NULL DEFAULT 0 CHECK(claim_epoch >= 0),
    lease_expires_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
      (action IN ('install','update') AND candidate_id IS NOT NULL
       AND target_version IS NOT NULL AND target_manifest_hash IS NOT NULL
       AND candidate_package_hash IS NOT NULL AND archive_hash IS NOT NULL
       AND report_id IS NOT NULL AND risk_id IS NOT NULL AND decision_id IS NOT NULL
       AND rollback_kind IS NULL)
      OR
      (action='rollback' AND candidate_id IS NULL AND cause_ref IS NOT NULL
       AND rollback_kind IS NOT NULL AND target_expected_binding_generation>=1)
      OR
      (action IN ('uninstall','disable') AND candidate_id IS NULL
       AND cause_ref IS NOT NULL AND rollback_kind IS NULL
       AND candidate_package_hash IS NULL AND archive_hash IS NULL)
    ),
    PRIMARY KEY(profile_id, profile_generation, activation_request_id),
    UNIQUE(profile_id, profile_generation, request_fingerprint),
    FOREIGN KEY(profile_id, profile_generation, candidate_id)
      REFERENCES candidate_artifacts(profile_id, profile_generation, candidate_id),
    FOREIGN KEY(profile_id, profile_generation, report_id)
      REFERENCES evaluation_reports(profile_id, profile_generation, report_id),
    FOREIGN KEY(profile_id, profile_generation, risk_id)
      REFERENCES risk_assessments(profile_id, profile_generation, risk_id),
    FOREIGN KEY(profile_id, profile_generation, decision_id)
      REFERENCES growth_decisions(profile_id, profile_generation, decision_id)
) WITHOUT ROWID;
CREATE UNIQUE INDEX IF NOT EXISTS uq_capability_mutation_target
ON capability_activation_requests(
  profile_id, profile_generation, target_owner_key, target_scope, target_scope_key, pack_id
) WHERE status IN ('pending','claimed','staging','staged','publishing','unknown','cleanup_required');

CREATE TABLE IF NOT EXISTS capability_activation_receipts (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    activation_request_id TEXT NOT NULL,
    manager_operation_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('install','update','rollback','uninstall','disable')),
    candidate_mode TEXT CHECK(candidate_mode IN ('genesis','update','builtin_override')),
    pack_id TEXT NOT NULL,
    version TEXT,
    manifest_hash TEXT,
    source_fence_hash TEXT,
    runtime_set_ref TEXT,
    runtime_set_hash TEXT,
    target_owner_key TEXT,
    target_scope TEXT,
    binding_generation INTEGER,
    owner_binding_set_stamp TEXT,
    process_projection_fingerprint TEXT,
    target_user_owner_binding_set_stamp TEXT,
    fallback_owner_key TEXT,
    fallback_scope TEXT,
    fallback_scope_key TEXT,
    fallback_binding_id TEXT,
    fallback_binding_generation INTEGER,
    fallback_pack_id TEXT,
    fallback_version TEXT,
    fallback_manifest_hash TEXT,
    fallback_owner_binding_set_stamp TEXT,
    fallback_process_projection_fingerprint TEXT,
    fallback_runtime_set_ref TEXT,
    fallback_runtime_set_hash TEXT,
    absence_proof_hash TEXT,
    result_hash TEXT NOT NULL,
    settled_at TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    CHECK (
      (action IN ('install','update') AND binding_generation>=1 AND absence_proof_hash IS NULL)
      OR
      (action='rollback')
      OR
      (action IN ('uninstall','disable') AND absence_proof_hash IS NOT NULL
       AND fallback_owner_key IS NULL AND fallback_runtime_set_hash IS NULL)
    ),
    PRIMARY KEY(profile_id, profile_generation, activation_request_id),
    UNIQUE(profile_id, profile_generation, manager_operation_id),
    FOREIGN KEY(profile_id, profile_generation, activation_request_id)
      REFERENCES capability_activation_requests(profile_id, profile_generation, activation_request_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS capability_activation_guards (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    guard_id TEXT NOT NULL,
    activation_request_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    receipt_hash TEXT NOT NULL,
    target_owner_key TEXT NOT NULL,
    target_scope TEXT NOT NULL,
    target_scope_key TEXT NOT NULL,
    binding_generation INTEGER NOT NULL CHECK(binding_generation>=1),
    owner_stamp TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    policy_id TEXT NOT NULL CHECK(policy_id='companion_guard_v1'),
    policy_hash TEXT NOT NULL,
    window_seconds INTEGER NOT NULL CHECK(window_seconds=86400),
    threshold INTEGER NOT NULL CHECK(threshold=1),
    rollback_plan_json TEXT NOT NULL,
    rollback_plan_hash TEXT NOT NULL,
    trigger_incident_id TEXT,
    rollback_request_id TEXT,
    rollback_receipt_hash TEXT,
    status TEXT NOT NULL CHECK(status IN ('open','rollback_pending','rolled_back','expired','superseded')),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, guard_id),
    UNIQUE(profile_id, profile_generation, activation_request_id),
    FOREIGN KEY(profile_id, profile_generation, activation_request_id)
      REFERENCES capability_activation_requests(profile_id, profile_generation, activation_request_id)
) WITHOUT ROWID;
CREATE UNIQUE INDEX IF NOT EXISTS uq_activation_guard_target
ON capability_activation_guards(
  profile_id, profile_generation, target_owner_key, target_scope, target_scope_key, pack_id
) WHERE status IN ('open','rollback_pending');

CREATE TABLE IF NOT EXISTS capability_guard_incidents (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    guard_id TEXT NOT NULL,
    incident_id TEXT NOT NULL,
    source_authority TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    binding_generation INTEGER NOT NULL CHECK(binding_generation>=1),
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    runtime_generation INTEGER NOT NULL CHECK(runtime_generation>=0),
    failure_class TEXT NOT NULL CHECK(failure_class IN (
      'package_integrity','runtime_contract','schema_fingerprint','effect_policy_fingerprint')),
    severity TEXT NOT NULL CHECK(severity='critical'),
    failure_fingerprint TEXT NOT NULL,
    source_receipt_ref TEXT NOT NULL,
    source_receipt_hash TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    dedupe_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('accepted','rejected','triggered')),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, guard_id, incident_id),
    UNIQUE(profile_id, profile_generation, source_authority, source_event_id),
    FOREIGN KEY(profile_id, profile_generation, guard_id)
      REFERENCES capability_activation_guards(profile_id, profile_generation, guard_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS capability_version_supports (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    evidence_set_json TEXT NOT NULL,
    evidence_set_hash TEXT NOT NULL,
    build_receipt_ref TEXT NOT NULL,
    build_receipt_hash TEXT NOT NULL,
    report_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    activation_receipt_hash TEXT,
    support_state TEXT NOT NULL CHECK(support_state IN ('eligible','active','forgotten','invalidated')),
    support_hash TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, pack_id, version, manifest_hash, candidate_id),
    FOREIGN KEY(profile_id, profile_generation, candidate_id)
      REFERENCES candidate_artifacts(profile_id, profile_generation, candidate_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS capability_quarantines (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    fence_generation INTEGER NOT NULL CHECK(fence_generation>=1),
    support_set_hash TEXT NOT NULL,
    release_request_id TEXT,
    release_receipt_hash TEXT,
    status TEXT NOT NULL CHECK(status IN ('active','rollback_pending','release_pending','released','disabled')),
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, pack_id, version, manifest_hash),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS jobs (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    job_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','leased','succeeded','failed','cancelled','expired')),
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

CREATE TABLE IF NOT EXISTS delegated_task_grants (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    grant_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('read','draft','reversible_local')),
    target TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    version INTEGER NOT NULL CHECK(version>=1),
    status TEXT NOT NULL CHECK(status IN ('active','revoked','expired')),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, grant_id),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS companion_run_bindings (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    run_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    root_run_id TEXT NOT NULL,
    job_id TEXT,
    snapshot_generation INTEGER NOT NULL CHECK(snapshot_generation>=0),
    snapshot_id TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    all_generations_root_hash TEXT NOT NULL,
    start_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('prepared','active','revoked','terminal')),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, run_id),
    FOREIGN KEY(profile_id, profile_generation, snapshot_id)
      REFERENCES run_growth_snapshots(profile_id, profile_generation, snapshot_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS companion_settings (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    setting_key TEXT NOT NULL,
    value_type TEXT NOT NULL CHECK(value_type IN ('bool','integer','string','json')),
    value_json TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version>=1),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, setting_key),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS companion_budget_windows (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    window_kind TEXT NOT NULL,
    window_start TEXT NOT NULL,
    reserved_tokens INTEGER NOT NULL CHECK(reserved_tokens>=0),
    actual_tokens INTEGER NOT NULL CHECK(actual_tokens>=0),
    reserved_ms INTEGER NOT NULL CHECK(reserved_ms>=0),
    actual_ms INTEGER NOT NULL CHECK(actual_ms>=0),
    reserved_jobs INTEGER NOT NULL CHECK(reserved_jobs>=0),
    actual_jobs INTEGER NOT NULL CHECK(actual_jobs>=0),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, window_kind, window_start),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS reminders (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    reminder_id TEXT NOT NULL,
    schedule_json TEXT NOT NULL,
    timezone TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','cancelled','completed')),
    schedule_version INTEGER NOT NULL CHECK(schedule_version>=1),
    quiet_policy_json TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, reminder_id),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS reminder_occurrences (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    occurrence_id TEXT NOT NULL,
    reminder_id TEXT NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','leased','delivered','cancelled','expired')),
    claim_owner TEXT,
    claim_epoch INTEGER NOT NULL DEFAULT 0 CHECK(claim_epoch>=0),
    lease_expires_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt>=0),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, occurrence_id),
    UNIQUE(profile_id, profile_generation, reminder_id, due_at),
    FOREIGN KEY(profile_id, profile_generation, reminder_id)
      REFERENCES reminders(profile_id, profile_generation, reminder_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS reminder_mutation_receipts (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    effect_id TEXT NOT NULL,
    operation_kind TEXT NOT NULL CHECK(operation_kind IN ('create','cancel')),
    args_json TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    reminder_id TEXT NOT NULL,
    before_schedule_version INTEGER,
    after_schedule_version INTEGER,
    result_payload_ref TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    CHECK (
      (operation_kind='create' AND before_schedule_version IS NULL AND after_schedule_version=1)
      OR
      (operation_kind='cancel' AND before_schedule_version>=1
       AND after_schedule_version=before_schedule_version+1)
    ),
    PRIMARY KEY(profile_id, profile_generation, effect_id),
    UNIQUE(profile_id, profile_generation, operation_kind, request_hash, effect_id),
    FOREIGN KEY(profile_id, profile_generation, reminder_id)
      REFERENCES reminders(profile_id, profile_generation, reminder_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS notifications (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    notification_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence>=1),
    summary_json TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    actions_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    redaction_version INTEGER NOT NULL DEFAULT 0 CHECK(redaction_version>=0),
    status TEXT NOT NULL CHECK(status IN ('pending','projected','superseded','redacted')),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, notification_id),
    UNIQUE(profile_id, profile_generation, sequence),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS outbox (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    outbox_id TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    event_id TEXT NOT NULL,
    sink_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','claimed','delivered','dead_letter')),
    claim_owner TEXT,
    claim_epoch INTEGER NOT NULL DEFAULT 0 CHECK(claim_epoch>=0),
    lease_expires_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt>=0),
    next_retry_at TEXT,
    delivered_at TEXT,
    result_hash TEXT,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, outbox_id),
    UNIQUE(profile_id, profile_generation, event_kind, event_id, sink_kind),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS audit_events (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    audit_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    before_hash TEXT,
    after_hash TEXT,
    lineage_ref TEXT,
    audit_hash TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, audit_id),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS lineage_edges (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    from_kind TEXT NOT NULL,
    from_id TEXT NOT NULL,
    to_kind TEXT NOT NULL,
    to_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, from_kind, from_id, to_kind, to_id, relation),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS run_growth_snapshots (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    snapshot_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    snapshot_generation INTEGER NOT NULL CHECK(snapshot_generation>=0),
    snapshot_hash TEXT NOT NULL,
    prior_snapshot_id TEXT,
    prior_snapshot_hash TEXT,
    status TEXT NOT NULL CHECK(status IN ('prepared','bound','revoked','terminal')),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, profile_generation, snapshot_id),
    UNIQUE(profile_id, profile_generation, run_id, snapshot_generation),
    FOREIGN KEY(profile_id, profile_generation, prior_snapshot_id)
      REFERENCES run_growth_snapshots(profile_id, profile_generation, snapshot_id),
    FOREIGN KEY(profile_id, profile_generation)
      REFERENCES profiles(profile_id, generation)
) WITHOUT ROWID;
CREATE UNIQUE INDEX IF NOT EXISTS uq_run_growth_initial_request
ON run_growth_snapshots(profile_id, profile_generation, request_id)
WHERE snapshot_generation=0;

CREATE TABLE IF NOT EXISTS run_growth_dependency_items (
    profile_id TEXT NOT NULL,
    profile_generation INTEGER NOT NULL CHECK(profile_generation >= 1),
    snapshot_id TEXT NOT NULL,
    dependency_kind TEXT NOT NULL CHECK(dependency_kind IN ('preference','capability_pack','personal_workflow')),
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

CREATE TABLE IF NOT EXISTS run_growth_dependency_evidence (
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

CREATE TABLE IF NOT EXISTS growth_authority_state (
    authority_key TEXT PRIMARY KEY CHECK(authority_key='growth'),
    phase TEXT NOT NULL CHECK(phase IN ('legacy','preparing','companion','paused')),
    generation INTEGER NOT NULL CHECK(generation>=1),
    migration_version INTEGER,
    migration_hash TEXT,
    cutover_operation_id TEXT,
    roll_forward_required INTEGER NOT NULL DEFAULT 0 CHECK(roll_forward_required IN (0,1)),
    drain_started_at TEXT,
    drain_completed_at TEXT,
    prepared_at TEXT,
    switched_at TEXT,
    last_error TEXT,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    updated_at TEXT NOT NULL
) WITHOUT ROWID;
INSERT OR IGNORE INTO growth_authority_state(
  authority_key, phase, generation, roll_forward_required,
  reason_code, schema_version, updated_at
) VALUES (
  'growth', 'legacy', 1, 0, 'initial_legacy',
  1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
);

CREATE TABLE IF NOT EXISTS growth_authority_journal (
    authority_key TEXT NOT NULL CHECK(authority_key='growth'),
    generation INTEGER NOT NULL CHECK(generation>=1),
    event_seq INTEGER NOT NULL CHECK(event_seq>=1),
    from_phase TEXT NOT NULL CHECK(from_phase IN ('legacy','preparing','companion','paused')),
    to_phase TEXT NOT NULL CHECK(to_phase IN ('legacy','preparing','companion','paused')),
    cutover_operation_id TEXT,
    marker_committed INTEGER NOT NULL CHECK(marker_committed IN (0,1)),
    marker_hash TEXT,
    old_binding_generation INTEGER,
    new_binding_generation INTEGER,
    old_owner_binding_set_stamp TEXT,
    new_owner_binding_set_stamp TEXT,
    substep_phase TEXT,
    substep_hash TEXT,
    drain_state TEXT,
    journal_payload_json TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(authority_key, generation, event_seq),
    FOREIGN KEY(authority_key)
      REFERENCES growth_authority_state(authority_key)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS ix_jobs_claim
ON jobs(status, next_retry_at, lease_expires_at, created_at);
CREATE INDEX IF NOT EXISTS ix_outbox_claim
ON outbox(status, next_retry_at, lease_expires_at, created_at);
CREATE INDEX IF NOT EXISTS ix_candidate_evidence_event
ON candidate_evidence(profile_id, profile_generation, event_id);
CREATE INDEX IF NOT EXISTS ix_dependency_evidence_event
ON run_growth_dependency_evidence(profile_id, profile_generation, event_id);
CREATE INDEX IF NOT EXISTS ix_lineage_from
ON lineage_edges(profile_id, profile_generation, from_kind, from_id);
