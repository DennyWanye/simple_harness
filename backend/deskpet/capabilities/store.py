# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""SQLite repository for product-owned capability and policy state.

New SDK composition passes a ``ProductStateDatabase`` owner.  Legacy path/UoW
construction remains only for the still-live pre-cutover runtime and is not
used by SDK adapters.  Product-owned stores fence :meth:`bind` to connections
opened by this repository, preventing an SDK execution transaction from being
mistaken for product policy authority.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aiosqlite

from deskpet.capabilities.skill_install_verification_store import (
    CapabilitySkillInstallVerificationAttempt,
    CapabilitySkillInstallVerificationAttestation,
    _allocate_skill_install_verification_attempt,
    _attach_skill_install_verification_attestation,
    _cas_skill_install_verification_attempt,
    _get_current_skill_install_verification_attempt,
    _get_skill_install_verification_attempt,
    _get_skill_install_verification_attestation,
    _list_pending_skill_install_verification_attempts,
    _list_succeeded_global_skill_install_intents,
    _supersede_skill_install_verification_attempt,
)

from .contracts import (
    EMPTY_RECEIPT_SET_HASH,
    LEGACY_LOCAL_OWNER_KEY,
    JsonValue,
    OwnerScopeKey,
    canonical_json,
    fingerprint_json,
)
from .refresh_contracts import (
    CapabilityOperationReceipt,
    CapabilityRefreshCommit,
    CapabilityRefreshIntent,
)
from .store_domains.core import (
    CAPABILITY_SCHEMA_VERSION,
    CapabilityOperationEvidence,
    CapabilityOperationMember,
    CapabilityPublishIntentMember,
    CapabilitySkillInstallHandoff,
    CapabilitySkillInstallIntent,
    CapabilitySkillInstallMember,
    CapabilityStoreState,
    CapabilityStoreTx,
    _owner_binding_stamp_tx,
)
from .store_domains.operations import (
    ALL_CAPABILITY_OPERATION_PHASES,
    BATCH_CAPABILITY_OPERATION_PHASES,
    CAPABILITY_OPERATION_PHASES,
    OperationMixin,
)
from .store_domains.receipts_policy import ReceiptPolicyMixin
from .store_domains.runtime_leases import RuntimeLeaseMixin
from .store_domains.runtime_sets import CapabilityRuntimeInstanceSpec, RuntimeSetMixin
from .store_domains.snapshot_leases import SnapshotLeaseMixin
from .store_domains.snapshots import RunSnapshotMixin, SnapshotProjectionState
from .store_domains.support import (
    CapabilityOperationRecord,
    CapabilityPublishIntent,
    CapabilityRuntimeCallLease,
    CapabilityRuntimeLease,
    CapabilityRuntimePrepareIntent,
    CapabilityRuntimeProjectionReceipt,
    CapabilityRuntimeSetRecord,
    CapabilitySchemaMissing,
    CapabilitySnapshotLeaseIntent,
    CapabilitySnapshotLeaseReleaseReceipt,
    CapabilityStoreConflict,
    CapabilityStoreError,
    CapabilityVersionRecord,
    CapabilityVersionStorage,
    LegacyAuthorizationImportOutcome,
    LegacyAuthorizationImportRecord,
    PreparedRunCatalogProjection,
    SnapshotLeaseAdoptionReceipt,
    _fetch_mappings,
)
from .store_domains.versions import VersionBindingMixin

CAPABILITY_SCHEMA_V1_SQL = r"""
CREATE TABLE IF NOT EXISTS capability_schema_state (
    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
    schema_version INTEGER NOT NULL CHECK(schema_version>=1),
    catalog_generation INTEGER NOT NULL DEFAULT 0 CHECK(catalog_generation>=0),
    binding_generation INTEGER NOT NULL DEFAULT 0 CHECK(binding_generation>=0),
    updated_at REAL NOT NULL
);
INSERT OR IGNORE INTO capability_schema_state(
    singleton_id,schema_version,catalog_generation,binding_generation,updated_at
) VALUES(1,1,0,0,CAST(strftime('%s','now') AS REAL));

CREATE TABLE IF NOT EXISTS capability_versions (
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    descriptor_json TEXT NOT NULL,
    source_json TEXT NOT NULL,
    install_path TEXT NOT NULL,
    validation_status TEXT NOT NULL CHECK(validation_status IN (
        'pending','healthy','degraded','failed'
    )),
    expected_tool_fingerprints_json TEXT NOT NULL DEFAULT '[]',
    parent_version TEXT,
    parent_manifest_hash TEXT,
    derived_from_receipt_ref TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY(pack_id,version,manifest_hash),
    UNIQUE(pack_id,version),
    CHECK(
        (parent_version IS NULL AND parent_manifest_hash IS NULL)
        OR
        (parent_version IS NOT NULL AND parent_manifest_hash IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_capability_versions_pack_created
    ON capability_versions(pack_id,created_at DESC);

CREATE TABLE IF NOT EXISTS capability_bindings (
    binding_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK(scope IN ('builtin','run','project','user')),
    scope_key TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    active_version TEXT NOT NULL,
    active_manifest_hash TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation>0),
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    updated_at REAL NOT NULL,
    UNIQUE(scope,scope_key,pack_id),
    FOREIGN KEY(pack_id,active_version,active_manifest_hash)
        REFERENCES capability_versions(pack_id,version,manifest_hash)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_capability_bindings_visible
    ON capability_bindings(scope,scope_key,enabled,pack_id);

CREATE TABLE IF NOT EXISTS capability_operations (
    operation_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    root_run_id TEXT,
    kind TEXT NOT NULL CHECK(kind IN (
        'activate','install','update','build','repair','rollback','uninstall'
    )),
    pack_id TEXT,
    requested_scope TEXT CHECK(requested_scope IS NULL OR requested_scope IN (
        'builtin','run','project','user'
    )),
    requested_scope_key TEXT,
    phase TEXT NOT NULL CHECK(phase IN (
        'planned','staged','verified','environment_ready','candidate_ready',
        'publish_intent','catalog_swapped','bound','published'
    )),
    status TEXT NOT NULL CHECK(status IN (
        'running','succeeded','failed','cancelled','unknown'
    )),
    request_json TEXT NOT NULL,
    error_json TEXT,
    started_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    ended_at REAL
);
CREATE INDEX IF NOT EXISTS idx_capability_operations_recovery
    ON capability_operations(status,phase,updated_at);

CREATE TABLE IF NOT EXISTS capability_operation_phase_evidence (
    operation_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('intent','committed','failed','unknown')),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(operation_id,phase),
    FOREIGN KEY(operation_id) REFERENCES capability_operations(operation_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS capability_validation_results (
    operation_id TEXT NOT NULL,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('passed','failed','skipped','unknown')),
    evidence_ref TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    PRIMARY KEY(operation_id,check_name),
    FOREIGN KEY(operation_id) REFERENCES capability_operations(operation_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS capability_runtime_leases (
    lease_id TEXT PRIMARY KEY,
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    server_id TEXT NOT NULL DEFAULT '',
    pid INTEGER,
    run_id TEXT,
    session_generation INTEGER NOT NULL DEFAULT 1 CHECK(session_generation>0),
    state TEXT NOT NULL CHECK(state IN ('starting','ready','draining','stopped','unknown')),
    heartbeat_at REAL NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(pack_id,version,manifest_hash)
        REFERENCES capability_versions(pack_id,version,manifest_hash)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS capability_runtime_call_leases (
    call_lease_id TEXT PRIMARY KEY,
    runtime_lease_id TEXT NOT NULL,
    effect_id TEXT NOT NULL UNIQUE,
    session_generation INTEGER NOT NULL CHECK(session_generation>0),
    state TEXT NOT NULL CHECK(state IN ('claimed','running','settled','unknown')),
    started_at REAL NOT NULL,
    ended_at REAL,
    FOREIGN KEY(runtime_lease_id) REFERENCES capability_runtime_leases(lease_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS capability_snapshot_leases (
    snapshot_ref TEXT NOT NULL,
    run_id TEXT NOT NULL,
    root_run_id TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    tool_spec_fingerprints_json TEXT NOT NULL,
    acquired_at REAL NOT NULL,
    released_at REAL,
    PRIMARY KEY(snapshot_ref,run_id,pack_id,version,manifest_hash),
    FOREIGN KEY(pack_id,version,manifest_hash)
        REFERENCES capability_versions(pack_id,version,manifest_hash)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_capability_snapshot_leases_active
    ON capability_snapshot_leases(pack_id,version,manifest_hash,released_at);

CREATE TABLE IF NOT EXISTS capability_publish_intents (
    intent_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE,
    expected_registry_revision INTEGER NOT NULL CHECK(expected_registry_revision>=0),
    old_specs_json TEXT NOT NULL,
    new_specs_json TEXT NOT NULL,
    old_binding_json TEXT,
    new_binding_json TEXT NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN (
        'publish_intent','catalog_swapped','bound'
    )),
    status TEXT NOT NULL CHECK(status IN (
        'pending','committed','rolled_back','unknown'
    )),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(operation_id) REFERENCES capability_operations(operation_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_capability_publish_intents_pending
    ON capability_publish_intents(status,updated_at);

CREATE TABLE IF NOT EXISTS capability_operation_receipts (
    operation_id TEXT PRIMARY KEY,
    receipt_hash TEXT NOT NULL UNIQUE,
    receipt_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(operation_id) REFERENCES capability_operations(operation_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS capability_refresh_intents (
    intent_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE,
    refresh_nonce TEXT NOT NULL UNIQUE,
    root_run_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN (
        'tool_effect','child_terminal'
    )),
    status TEXT NOT NULL CHECK(status IN ('pending','committed','failed')),
    intent_json TEXT NOT NULL,
    commit_hash TEXT,
    commit_json TEXT,
    error_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(operation_id) REFERENCES capability_operation_receipts(operation_id)
        ON DELETE RESTRICT,
    CHECK(
        (status='committed' AND commit_hash IS NOT NULL AND commit_json IS NOT NULL)
        OR
        (status!='committed' AND commit_hash IS NULL AND commit_json IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_capability_refresh_intents_pending
    ON capability_refresh_intents(root_run_id,run_id,status,created_at);

CREATE TABLE IF NOT EXISTS capability_refresh_snapshots (
    snapshot_ref TEXT NOT NULL,
    snapshot_kind TEXT NOT NULL CHECK(snapshot_kind IN (
        'context_os','exposure_intent'
    )),
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(snapshot_ref,snapshot_kind)
);

CREATE TABLE IF NOT EXISTS capability_search_receipts (
    receipt_id TEXT PRIMARY KEY,
    root_run_id TEXT NOT NULL,
    catalog_stamp_fingerprint TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(root_run_id,catalog_stamp_fingerprint,query_hash)
);

CREATE TABLE IF NOT EXISTS capability_failure_receipts (
    receipt_ref TEXT PRIMARY KEY,
    root_run_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    failure_report_ref TEXT NOT NULL,
    provider_call_id TEXT NOT NULL,
    effect_id TEXT NOT NULL UNIQUE,
    capability_id TEXT NOT NULL,
    pack_version TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_spec_fingerprint TEXT NOT NULL,
    binding_scope TEXT NOT NULL CHECK(binding_scope IN (
        'builtin','run','project','user'
    )),
    scope_key TEXT NOT NULL,
    canonical_args_json TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    error_code TEXT NOT NULL,
    error_fingerprint TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    UNIQUE(root_run_id,failure_report_ref),
    FOREIGN KEY(capability_id,pack_version,manifest_hash)
        REFERENCES capability_versions(pack_id,version,manifest_hash)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_capability_failure_receipts_root_error
    ON capability_failure_receipts(root_run_id,error_fingerprint,created_at);

CREATE TABLE IF NOT EXISTS capability_repair_attempts (
    root_run_id TEXT NOT NULL,
    error_fingerprint TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK(attempt_no BETWEEN 1 AND 3),
    failure_receipt_ref TEXT NOT NULL,
    control_call_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'admitted','running','succeeded','failed','cancelled'
    )),
    child_run_id TEXT,
    operation_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(root_run_id,error_fingerprint,attempt_no),
    UNIQUE(root_run_id,control_call_id),
    FOREIGN KEY(failure_receipt_ref)
        REFERENCES capability_failure_receipts(receipt_ref)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_capability_repair_attempts_receipt
    ON capability_repair_attempts(failure_receipt_ref,attempt_no);

CREATE TABLE IF NOT EXISTS authorization_policy_state (
    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
    mode TEXT NOT NULL CHECK(mode IN ('manual','auto')),
    generation INTEGER NOT NULL CHECK(generation>=0),
    updated_at REAL NOT NULL,
    provenance TEXT NOT NULL DEFAULT 'needs_user_choice' CHECK(provenance IN (
        'factory_default','factory_default_migrated','legacy_import',
        'user_explicit','needs_user_choice'
    )),
    schema_generation INTEGER NOT NULL DEFAULT 2 CHECK(schema_generation=2),
    user_set_receipt_ref TEXT
);
INSERT OR IGNORE INTO authorization_policy_state(
    singleton_id,mode,generation,updated_at,provenance,schema_generation
) VALUES(1,'auto',0,CAST(strftime('%s','now') AS REAL),'factory_default',2);

CREATE TABLE IF NOT EXISTS authorization_policy_legacy_imports (
    source_key TEXT PRIMARY KEY,
    outcome TEXT NOT NULL CHECK(outcome IN ('imported','missing','invalid')),
    source_fingerprint TEXT CHECK(
        source_fingerprint IS NULL OR (
            length(source_fingerprint)=64
            AND source_fingerprint NOT GLOB '*[^0-9a-f]*'
        )
    ),
    imported_mode TEXT CHECK(
        imported_mode IS NULL OR imported_mode IN ('manual','auto')
    ),
    error_code TEXT,
    imported_at REAL NOT NULL,
    CHECK(
        (
            outcome='imported'
            AND source_fingerprint IS NOT NULL
            AND imported_mode IS NOT NULL
            AND error_code IS NULL
        )
        OR
        (
            outcome='missing'
            AND source_fingerprint IS NULL
            AND imported_mode IS NULL
            AND error_code IS NULL
        )
        OR
        (
            outcome='invalid'
            AND imported_mode IS NULL
            AND error_code IS NOT NULL
        )
    )
);

CREATE TABLE IF NOT EXISTS task_grants (
    task_grant_id TEXT PRIMARY KEY,
    root_run_id TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('user','policy:auto')),
    policy_generation INTEGER NOT NULL CHECK(policy_generation>=0),
    version INTEGER NOT NULL CHECK(version>0),
    grant_fingerprint TEXT NOT NULL,
    grant_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','revoked')),
    created_at REAL NOT NULL,
    revoked_at REAL
);
CREATE INDEX IF NOT EXISTS idx_task_grants_root_active
    ON task_grants(root_run_id,status,version DESC);
"""

# ProductStateDatabase owns the physical v1 -> v2 table rebuild.  Keeping the
# exact legacy text above immutable lets that owner validate and copy old rows;
# this definition is the fresh-v2 contract used after the rebuild.
CAPABILITY_SCHEMA_SQL = (
    CAPABILITY_SCHEMA_V1_SQL.replace(
        "'activate','install','update','build','repair','rollback','uninstall'",
        "'activate','install','update','build','repair','rollback','uninstall',"
        "'skill_install_batch'",
        1,
    )
    .replace(
        "'publish_intent','catalog_swapped','bound','published'\n    )),",
        "'publish_intent','catalog_swapped','bound','published',"
        "'batch_staged','batch_prepared','batch_publish_intent',"
        "'batch_files_materialized','batch_catalog_swapped','batch_committed'\n    )),",
        1,
    )
    .replace(
        "'publish_intent','catalog_swapped','bound'\n    )),",
        "'publish_intent','catalog_swapped','bound','batch_publish_intent',"
        "'batch_files_materialized','batch_catalog_swapped','batch_committed'\n    )),",
        1,
    )
    .replace(
        "    ended_at REAL\n);\nCREATE INDEX IF NOT EXISTS idx_capability_operations_recovery",
        "    ended_at REAL,\n"
        "    CHECK((kind='skill_install_batch' AND phase LIKE 'batch_%') OR "
        "(kind!='skill_install_batch' AND phase NOT LIKE 'batch_%'))\n"
        ");\nCREATE INDEX IF NOT EXISTS idx_capability_operations_recovery",
        1,
    )
    + r"""

CREATE TABLE IF NOT EXISTS capability_skill_install_intents (
    intent_id TEXT PRIMARY KEY,
    effect_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    root_run_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    project_scope_key TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    source_json TEXT NOT NULL,
    exact_commit TEXT NOT NULL,
    archive_hash TEXT NOT NULL,
    raw_tree_hash TEXT NOT NULL,
    member_set_stamp TEXT NOT NULL,
    permission_set_hash TEXT NOT NULL,
    confirmation_nonce TEXT NOT NULL UNIQUE,
    confirmation_version INTEGER NOT NULL CHECK(confirmation_version>0),
    expires_at REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'staging','awaiting_confirmation','publishing',
        'published_pending_runtime_verification','succeeded',
        'stage_failed_cleanup_pending','denied_cleanup_pending',
        'expired_cleanup_pending','stage_failed','denied','expired','unknown'
    )),
    state_version INTEGER NOT NULL CHECK(state_version>0),
    settlement_ref TEXT,
    cleanup_ref TEXT,
    verification_ref TEXT,
    error_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(effect_id,call_id)
);
CREATE INDEX IF NOT EXISTS idx_capability_skill_install_intents_status_expiry
    ON capability_skill_install_intents(status,expires_at,intent_id);

CREATE TABLE IF NOT EXISTS capability_skill_install_members (
    intent_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal>=0),
    normalized_name TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    member_json TEXT NOT NULL,
    PRIMARY KEY(intent_id,ordinal),
    UNIQUE(intent_id,normalized_name),
    FOREIGN KEY(intent_id) REFERENCES capability_skill_install_intents(intent_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS capability_skill_install_handoffs (
    intent_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE,
    confirmation_receipt_hash TEXT NOT NULL UNIQUE,
    member_set_stamp TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(intent_id) REFERENCES capability_skill_install_intents(intent_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(operation_id) REFERENCES capability_operations(operation_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS capability_operation_members (
    operation_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal>=0),
    normalized_name TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    committed_version TEXT,
    committed_manifest_hash TEXT,
    committed_set_stamp TEXT,
    PRIMARY KEY(operation_id,ordinal),
    UNIQUE(operation_id,normalized_name),
    FOREIGN KEY(operation_id) REFERENCES capability_operations(operation_id)
        ON DELETE CASCADE,
    FOREIGN KEY(pack_id,committed_version,committed_manifest_hash)
        REFERENCES capability_versions(pack_id,version,manifest_hash)
        ON DELETE RESTRICT,
    CHECK((committed_version IS NULL AND committed_manifest_hash IS NULL
           AND committed_set_stamp IS NULL) OR
          (committed_version IS NOT NULL AND committed_manifest_hash IS NOT NULL
           AND committed_set_stamp IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS capability_publish_intent_members (
    publish_intent_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    old_binding_json TEXT,
    new_binding_json TEXT NOT NULL,
    PRIMARY KEY(publish_intent_id,ordinal),
    FOREIGN KEY(publish_intent_id) REFERENCES capability_publish_intents(intent_id)
        ON DELETE CASCADE,
    FOREIGN KEY(operation_id,ordinal)
        REFERENCES capability_operation_members(operation_id,ordinal)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS capability_operation_evidence (
    operation_id TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    evidence_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(operation_id,evidence_kind,idempotency_key),
    FOREIGN KEY(operation_id) REFERENCES capability_operations(operation_id)
        ON DELETE CASCADE
);
"""
)

CAPABILITY_SCHEMA_V2_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS capability_legacy_global_convergence (
        global_owner_key TEXT NOT NULL,
        pack_id TEXT NOT NULL,
        source_binding_set_stamp TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK(outcome IN (
            'promoted','legacy_global_conflict'
        )),
        selected_identity_json TEXT,
        conflict_json TEXT,
        created_at REAL NOT NULL,
        PRIMARY KEY(global_owner_key,pack_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_owner_detail_versions (
        owner_key TEXT NOT NULL,
        scope TEXT NOT NULL CHECK(scope IN ('builtin','run','project','user')),
        scope_key TEXT NOT NULL,
        row_state TEXT NOT NULL CHECK(row_state IN ('existing','deleted')),
        version INTEGER NOT NULL CHECK(version>=0),
        owner_catalog_generation INTEGER NOT NULL CHECK(owner_catalog_generation>=0),
        committed_owner_binding_set_stamp TEXT NOT NULL CHECK(
            length(committed_owner_binding_set_stamp)=64
            AND committed_owner_binding_set_stamp NOT GLOB '*[^0-9a-f]*'
        ),
        manager_receipt_set_hash TEXT NOT NULL CHECK(
            length(manager_receipt_set_hash)=64
            AND manager_receipt_set_hash NOT GLOB '*[^0-9a-f]*'
        ),
        updated_at REAL NOT NULL,
        PRIMARY KEY(owner_key,scope,scope_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_version_storage (
        pack_id TEXT NOT NULL,
        version TEXT NOT NULL,
        manifest_hash TEXT NOT NULL,
        storage_key TEXT NOT NULL UNIQUE,
        pack_storage_schema TEXT NOT NULL CHECK(
            pack_storage_schema IN ('legacy_v1','content_key_v2_pack')
        ),
        pack_root_hash TEXT NOT NULL,
        environment_storage_schema TEXT NOT NULL CHECK(
            environment_storage_schema IN (
                'legacy_v1','content_key_v2_environment'
            )
        ),
        environment_root_hash TEXT NOT NULL,
        archive_hash TEXT NOT NULL,
        created_at REAL NOT NULL,
        PRIMARY KEY(pack_id,version,manifest_hash),
        FOREIGN KEY(pack_id,version,manifest_hash)
            REFERENCES capability_versions(pack_id,version,manifest_hash)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_run_catalog_snapshots (
        run_catalog_content_stamp TEXT PRIMARY KEY,
        request_owner_key TEXT NOT NULL,
        request_scope_canonical_json TEXT NOT NULL,
        request_scope_hash TEXT NOT NULL,
        catalog_generation_vector_json TEXT NOT NULL,
        catalog_generation_vector_hash TEXT NOT NULL,
        entry_set_hash TEXT NOT NULL,
        expected_entry_count INTEGER NOT NULL CHECK(expected_entry_count>=0),
        created_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_run_catalog_snapshot_entries (
        run_catalog_content_stamp TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK(ordinal>=0),
        entry_kind TEXT NOT NULL CHECK(
            entry_kind IN ('pack','host_tool','host_instruction')
        ),
        selected_binding_id TEXT,
        selected_owner_key TEXT,
        selected_scope TEXT,
        selected_scope_key TEXT,
        selected_binding_generation INTEGER,
        stable_host_binding_id TEXT,
        visible_bindings_json TEXT NOT NULL,
        visible_binding_set_hash TEXT NOT NULL,
        descriptor_envelope_json TEXT NOT NULL,
        descriptor_fingerprint TEXT NOT NULL,
        pack_id TEXT,
        version TEXT,
        manifest_hash TEXT,
        host_provider_name TEXT,
        host_source TEXT,
        host_spec_version TEXT,
        host_schema_hash TEXT,
        host_content_hash TEXT,
        host_build_identity TEXT,
        tool_spec_fingerprints_json TEXT NOT NULL DEFAULT '[]',
        instruction_refs_hash TEXT NOT NULL,
        workflow_refs_hash TEXT NOT NULL,
        runtime_descriptor_hash TEXT NOT NULL,
        PRIMARY KEY(run_catalog_content_stamp,ordinal),
        UNIQUE(run_catalog_content_stamp,descriptor_fingerprint),
        FOREIGN KEY(run_catalog_content_stamp)
            REFERENCES capability_run_catalog_snapshots(run_catalog_content_stamp)
            ON DELETE CASCADE,
        FOREIGN KEY(pack_id,version,manifest_hash)
            REFERENCES capability_versions(pack_id,version,manifest_hash)
            ON DELETE RESTRICT,
        CHECK(
            (
                entry_kind='pack'
                AND selected_binding_id IS NOT NULL
                AND selected_owner_key IS NOT NULL
                AND selected_scope IS NOT NULL
                AND selected_scope_key IS NOT NULL
                AND selected_binding_generation IS NOT NULL
                AND stable_host_binding_id IS NULL
                AND pack_id IS NOT NULL
                AND version IS NOT NULL
                AND manifest_hash IS NOT NULL
                AND host_provider_name IS NULL
                AND host_source IS NULL
                AND host_spec_version IS NULL
                AND host_schema_hash IS NULL
                AND host_content_hash IS NULL
                AND host_build_identity IS NULL
            )
            OR
            (
                entry_kind IN ('host_tool','host_instruction')
                AND selected_binding_id IS NULL
                AND selected_owner_key IS NULL
                AND selected_scope IS NULL
                AND selected_scope_key IS NULL
                AND selected_binding_generation IS NULL
                AND stable_host_binding_id IS NOT NULL
                AND pack_id IS NULL
                AND version IS NULL
                AND manifest_hash IS NULL
                AND host_provider_name IS NOT NULL
                AND host_source IS NOT NULL
                AND host_spec_version IS NOT NULL
                AND host_schema_hash IS NOT NULL
                AND host_content_hash IS NOT NULL
                AND host_build_identity IS NOT NULL
            )
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_snapshot_lease_intents (
        lease_intent_id TEXT PRIMARY KEY,
        lease_intent_hash TEXT NOT NULL,
        snapshot_ref TEXT NOT NULL,
        snapshot_ref_schema TEXT NOT NULL CHECK(
            snapshot_ref_schema IN ('legacy_v1','run_catalog_v2')
        ),
        run_id TEXT NOT NULL,
        root_run_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        lease_generation INTEGER NOT NULL CHECK(lease_generation>0),
        owner_operation_id TEXT NOT NULL,
        lease_owner_kind TEXT NOT NULL CHECK(
            lease_owner_kind IN (
                'run_start','refresh_commit','legacy_boundary',
                'queued_child_legacy'
            )
        ),
        owner_record_ref TEXT,
        owner_record_hash TEXT,
        start_fingerprint TEXT,
        run_catalog_content_stamp TEXT NOT NULL,
        entry_set_hash TEXT NOT NULL,
        expected_entry_count INTEGER NOT NULL CHECK(expected_entry_count>=0),
        status TEXT NOT NULL CHECK(
            status IN ('prepared','bound','released','conflict')
        ),
        prepared_at REAL NOT NULL,
        bound_at REAL,
        released_at REAL,
        last_error TEXT,
        UNIQUE(run_id,owner_operation_id),
        FOREIGN KEY(run_catalog_content_stamp)
            REFERENCES capability_run_catalog_snapshots(run_catalog_content_stamp)
            ON DELETE RESTRICT,
        CHECK(
            (
                owner_record_ref IS NULL
                AND owner_record_hash IS NULL
                AND start_fingerprint IS NULL
                AND status='prepared'
            )
            OR
            (
                owner_record_ref IS NOT NULL
                AND owner_record_hash IS NOT NULL
                AND start_fingerprint IS NOT NULL
                AND status IN ('bound','released','conflict')
            )
        )
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
        idx_capability_lease_intent_snapshot_active
    ON capability_snapshot_lease_intents(snapshot_ref,run_id)
    WHERE status IN ('prepared','bound')
    """,
    """
    DROP INDEX IF EXISTS idx_capability_lease_intent_run_bound
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
        idx_capability_lease_intent_run_bound
    ON capability_snapshot_lease_intents(run_id)
    WHERE status='bound' AND snapshot_ref_schema='run_catalog_v2'
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_snapshot_lease_release_receipts (
        release_receipt_id TEXT PRIMARY KEY,
        release_receipt_hash TEXT NOT NULL UNIQUE,
        lease_intent_id TEXT NOT NULL UNIQUE,
        run_id TEXT NOT NULL,
        snapshot_ref TEXT NOT NULL,
        release_reason TEXT NOT NULL CHECK(
            release_reason IN (
                'terminal','cancel','refresh_replaced','prepared_orphan',
                'revoked','legacy_terminal'
            )
        ),
        owner_terminal_or_transition_ref TEXT NOT NULL,
        owner_terminal_or_transition_hash TEXT NOT NULL,
        last_active_member_for_snapshot INTEGER NOT NULL
            CHECK(last_active_member_for_snapshot IN (0,1)),
        cleanup_ticket_ref TEXT NOT NULL,
        cleanup_ticket_hash TEXT NOT NULL,
        cleanup_status TEXT NOT NULL CHECK(
            cleanup_status IN (
                'pending','cleaned','cleanup_required'
            )
        ),
        created_at REAL NOT NULL,
        cleaned_at REAL,
        last_error TEXT,
        FOREIGN KEY(lease_intent_id)
            REFERENCES capability_snapshot_lease_intents(lease_intent_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_owner_runtime_activations (
        owner_key TEXT NOT NULL,
        scope TEXT NOT NULL CHECK(scope IN ('builtin','run','project','user')),
        scope_key TEXT NOT NULL,
        owner_runtime_activation_generation INTEGER NOT NULL
            CHECK(owner_runtime_activation_generation>0),
        owner_activation_id TEXT NOT NULL UNIQUE,
        owner_binding_set_stamp TEXT NOT NULL,
        startup_or_profile_epoch INTEGER NOT NULL
            CHECK(startup_or_profile_epoch>=0),
        status TEXT NOT NULL CHECK(status IN (
            'prepared','launching','health_passed','published',
            'aborting','aborted','cleanup_required'
        )),
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        last_error TEXT,
        PRIMARY KEY(
            owner_key,scope,scope_key,
            owner_runtime_activation_generation
        )
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
        idx_capability_owner_runtime_activation_current
    ON capability_owner_runtime_activations(owner_key,scope,scope_key)
    WHERE status IN ('prepared','launching','health_passed','aborting')
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_lease_runtime_activations (
        lease_activation_id TEXT PRIMARY KEY,
        snapshot_ref TEXT NOT NULL,
        run_catalog_content_stamp TEXT NOT NULL,
        process_instance_id TEXT NOT NULL,
        process_catalog_stamp TEXT NOT NULL,
        entry_set_hash TEXT NOT NULL,
        expected_member_count INTEGER NOT NULL CHECK(expected_member_count>=0),
        expected_pack_count INTEGER NOT NULL CHECK(expected_pack_count>=0),
        status TEXT NOT NULL CHECK(status IN (
            'prepared','launching','health_passed','hidden_published',
            'aborting','aborted','cleanup_required','retired'
        )),
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        last_error TEXT,
        UNIQUE(process_instance_id,snapshot_ref),
        FOREIGN KEY(run_catalog_content_stamp)
            REFERENCES capability_run_catalog_snapshots(run_catalog_content_stamp)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_lease_runtime_members (
        lease_activation_id TEXT NOT NULL,
        lease_intent_id TEXT NOT NULL,
        snapshot_ref TEXT NOT NULL,
        run_id TEXT NOT NULL,
        run_start_snapshot_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('bound','released','conflict')),
        PRIMARY KEY(lease_activation_id,lease_intent_id),
        FOREIGN KEY(lease_activation_id)
            REFERENCES capability_lease_runtime_activations(lease_activation_id)
            ON DELETE RESTRICT,
        FOREIGN KEY(lease_intent_id)
            REFERENCES capability_snapshot_lease_intents(lease_intent_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_runtime_projection_receipts (
        projection_receipt_id TEXT PRIMARY KEY,
        projection_receipt_hash TEXT NOT NULL UNIQUE,
        purpose TEXT NOT NULL CHECK(purpose IN (
            'mutation','owner_rehydrate','lease_rehydrate','snapshot_pin'
        )),
        operation_id TEXT,
        owner_activation_id TEXT,
        lease_activation_id TEXT,
        lease_intent_id TEXT,
        owner_binding_set_stamp TEXT,
        run_catalog_content_stamp TEXT,
        process_instance_id TEXT NOT NULL,
        process_catalog_stamp TEXT,
        process_projection_fingerprint TEXT,
        runtime_set_set_hash TEXT,
        prepared_tool_set_envelope_json TEXT,
        prepared_tool_set_hash TEXT,
        pin_token_hash TEXT,
        status TEXT NOT NULL CHECK(status IN (
            'prepared','ready','retired','conflict'
        )),
        created_at REAL NOT NULL,
        ready_at REAL,
        retired_at REAL,
        FOREIGN KEY(owner_activation_id)
            REFERENCES capability_owner_runtime_activations(owner_activation_id)
            ON DELETE RESTRICT,
        FOREIGN KEY(lease_activation_id)
            REFERENCES capability_lease_runtime_activations(lease_activation_id)
            ON DELETE RESTRICT,
        FOREIGN KEY(lease_intent_id)
            REFERENCES capability_snapshot_lease_intents(lease_intent_id)
            ON DELETE RESTRICT,
        CHECK(
            (
                purpose='snapshot_pin'
                AND operation_id IS NULL
                AND owner_activation_id IS NULL
                AND lease_activation_id IS NULL
                AND lease_intent_id IS NOT NULL
                AND owner_binding_set_stamp IS NULL
                AND run_catalog_content_stamp IS NOT NULL
                AND process_catalog_stamp IS NOT NULL
                AND process_projection_fingerprint IS NULL
                AND runtime_set_set_hash IS NULL
            )
            OR
            (
                purpose='lease_rehydrate'
                AND operation_id IS NULL
                AND owner_activation_id IS NULL
                AND lease_activation_id IS NOT NULL
                AND lease_intent_id IS NULL
                AND owner_binding_set_stamp IS NULL
                AND run_catalog_content_stamp IS NOT NULL
                AND process_catalog_stamp IS NOT NULL
                AND process_projection_fingerprint IS NULL
            )
            OR
            (
                purpose='owner_rehydrate'
                AND operation_id IS NULL
                AND owner_activation_id IS NOT NULL
                AND lease_activation_id IS NULL
                AND lease_intent_id IS NULL
                AND owner_binding_set_stamp IS NOT NULL
                AND run_catalog_content_stamp IS NULL
                AND process_catalog_stamp IS NULL
                AND process_projection_fingerprint IS NOT NULL
            )
            OR
            (
                purpose='mutation'
                AND operation_id IS NOT NULL
                AND owner_activation_id IS NULL
                AND lease_activation_id IS NULL
                AND lease_intent_id IS NULL
                AND owner_binding_set_stamp IS NOT NULL
                AND run_catalog_content_stamp IS NULL
                AND process_catalog_stamp IS NULL
                AND process_projection_fingerprint IS NOT NULL
            )
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_runtime_sets (
        operation_id TEXT PRIMARY KEY,
        purpose TEXT NOT NULL CHECK(
            purpose IN ('mutation','owner_rehydrate','lease_rehydrate')
        ),
        lifecycle_action TEXT,
        owner_activation_id TEXT,
        lease_activation_id TEXT,
        target_pack_id TEXT,
        target_version TEXT,
        owner_binding_set_stamp TEXT,
        run_catalog_content_stamp TEXT,
        target_package_hash TEXT,
        target_manifest_hash TEXT,
        runtime_set_hash TEXT NOT NULL,
        expected_instance_count INTEGER NOT NULL
            CHECK(expected_instance_count>=0),
        authorization_hash TEXT,
        launch_revocation_epoch INTEGER NOT NULL
            CHECK(launch_revocation_epoch>=0),
        status TEXT NOT NULL CHECK(status IN (
            'prepared','launching','started','health_passed','activated',
            'not_started','unknown','aborting','aborted','cleanup_required'
        )),
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        last_error TEXT,
        FOREIGN KEY(owner_activation_id)
            REFERENCES capability_owner_runtime_activations(owner_activation_id)
            ON DELETE RESTRICT,
        FOREIGN KEY(lease_activation_id)
            REFERENCES capability_lease_runtime_activations(lease_activation_id)
            ON DELETE RESTRICT,
        CHECK(
            (
                purpose='mutation'
                AND lifecycle_action IS NOT NULL
                AND owner_activation_id IS NULL
                AND lease_activation_id IS NULL
                AND owner_binding_set_stamp IS NOT NULL
                AND run_catalog_content_stamp IS NULL
            )
            OR
            (
                purpose='owner_rehydrate'
                AND lifecycle_action IS NULL
                AND owner_activation_id IS NOT NULL
                AND lease_activation_id IS NULL
                AND owner_binding_set_stamp IS NOT NULL
                AND run_catalog_content_stamp IS NULL
            )
            OR
            (
                purpose='lease_rehydrate'
                AND lifecycle_action IS NULL
                AND owner_activation_id IS NULL
                AND lease_activation_id IS NOT NULL
                AND owner_binding_set_stamp IS NULL
                AND run_catalog_content_stamp IS NOT NULL
            )
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS capability_runtime_prepare_intents (
        operation_id TEXT NOT NULL,
        runtime_instance_id TEXT NOT NULL,
        entry_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK(ordinal>=0),
        runtime_kind TEXT NOT NULL CHECK(runtime_kind IN (
            'dependency_probe','tool_health','mcp','local_runtime'
        )),
        status TEXT NOT NULL CHECK(status IN (
            'prepared','launch_claimed','started','health_passed','activated',
            'not_started','unknown','aborting','aborted','cleanup_required'
        )),
        runtime_set_hash TEXT NOT NULL,
        authorization_hash TEXT,
        launch_revocation_epoch INTEGER NOT NULL
            CHECK(launch_revocation_epoch>=0),
        adapter_fingerprint TEXT NOT NULL,
        argv_hash TEXT NOT NULL,
        env_scope_hash TEXT NOT NULL,
        workdir TEXT NOT NULL,
        job_identity TEXT,
        pid INTEGER,
        process_create_time REAL,
        session_identity TEXT,
        start_outcome TEXT,
        start_ack_at REAL,
        health_outcome_hash TEXT,
        started_at REAL,
        updated_at REAL NOT NULL,
        last_error TEXT,
        PRIMARY KEY(operation_id,runtime_instance_id),
        UNIQUE(operation_id,entry_id),
        FOREIGN KEY(operation_id)
            REFERENCES capability_runtime_sets(operation_id)
            ON DELETE RESTRICT
    )
    """,
)

CAPABILITY_SNAPSHOT_LEASE_V2_SQL = """
CREATE TABLE capability_snapshot_leases_v2 (
    lease_entry_id TEXT PRIMARY KEY,
    lease_intent_id TEXT NOT NULL,
    snapshot_ref TEXT NOT NULL,
    run_id TEXT NOT NULL,
    root_run_id TEXT NOT NULL,
    run_catalog_content_stamp TEXT NOT NULL,
    entry_ordinal INTEGER NOT NULL CHECK(entry_ordinal>=0),
    entry_kind TEXT NOT NULL CHECK(
        entry_kind IN ('pack','host_tool','host_instruction')
    ),
    selected_binding_id TEXT,
    selected_owner_key TEXT,
    selected_scope TEXT,
    selected_scope_key TEXT,
    selected_binding_generation INTEGER,
    stable_host_binding_id TEXT,
    visible_binding_set_hash TEXT NOT NULL,
    descriptor_fingerprint TEXT NOT NULL,
    runtime_descriptor_hash TEXT NOT NULL,
    pack_id TEXT,
    version TEXT,
    manifest_hash TEXT,
    host_provider_name TEXT,
    host_source TEXT,
    host_spec_version TEXT,
    host_schema_hash TEXT,
    host_content_hash TEXT,
    host_build_identity TEXT,
    tool_spec_fingerprints_json TEXT NOT NULL,
    acquired_at REAL NOT NULL,
    released_at REAL,
    UNIQUE(lease_intent_id,entry_ordinal),
    FOREIGN KEY(lease_intent_id)
        REFERENCES capability_snapshot_lease_intents(lease_intent_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(run_catalog_content_stamp,entry_ordinal)
        REFERENCES capability_run_catalog_snapshot_entries(
            run_catalog_content_stamp,ordinal
        ) ON DELETE RESTRICT,
    FOREIGN KEY(pack_id,version,manifest_hash)
        REFERENCES capability_versions(pack_id,version,manifest_hash)
        ON DELETE RESTRICT,
    CHECK(
        (
            entry_kind='pack'
            AND selected_binding_id IS NOT NULL
            AND selected_owner_key IS NOT NULL
            AND selected_scope IS NOT NULL
            AND selected_scope_key IS NOT NULL
            AND selected_binding_generation IS NOT NULL
            AND stable_host_binding_id IS NULL
            AND pack_id IS NOT NULL
            AND version IS NOT NULL
            AND manifest_hash IS NOT NULL
            AND host_provider_name IS NULL
            AND host_source IS NULL
            AND host_spec_version IS NULL
            AND host_schema_hash IS NULL
            AND host_content_hash IS NULL
            AND host_build_identity IS NULL
        )
        OR
        (
            entry_kind IN ('host_tool','host_instruction')
            AND selected_binding_id IS NULL
            AND selected_owner_key IS NULL
            AND selected_scope IS NULL
            AND selected_scope_key IS NULL
            AND selected_binding_generation IS NULL
            AND stable_host_binding_id IS NOT NULL
            AND pack_id IS NULL
            AND version IS NULL
            AND manifest_hash IS NULL
            AND host_provider_name IS NOT NULL
            AND host_source IS NOT NULL
            AND host_spec_version IS NOT NULL
            AND host_schema_hash IS NOT NULL
            AND host_content_hash IS NOT NULL
            AND host_build_identity IS NOT NULL
        )
    )
)
"""


async def _capability_table_columns(db: aiosqlite.Connection, table: str) -> set[str]:
    rows = await (await db.execute(f"PRAGMA table_info({table})")).fetchall()
    return {str(row[1]) for row in rows}


async def _install_capability_v2_tables(db: aiosqlite.Connection) -> None:
    for statement in CAPABILITY_SCHEMA_V2_STATEMENTS:
        await db.execute(statement)


async def _migrate_snapshot_leases_v1_to_v2(
    db: aiosqlite.Connection,
) -> None:
    columns = await _capability_table_columns(db, "capability_snapshot_leases")
    if "lease_entry_id" in columns:
        return
    legacy_rows = await _fetch_mappings(
        await db.execute(
            """SELECT lease.*,version.descriptor_json
               FROM capability_snapshot_leases AS lease
               JOIN capability_versions AS version
                 ON version.pack_id=lease.pack_id
                AND version.version=lease.version
                AND version.manifest_hash=lease.manifest_hash
               ORDER BY lease.snapshot_ref,lease.run_id,
                        lease.pack_id,lease.version,lease.manifest_hash"""
        )
    )
    await db.execute(CAPABILITY_SNAPSHOT_LEASE_V2_SQL)
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in legacy_rows:
        grouped.setdefault((str(row["snapshot_ref"]), str(row["run_id"])), []).append(row)
    now = time.time()
    for (snapshot_ref, run_id), rows in grouped.items():
        root_run_id = str(rows[0]["root_run_id"])
        catalog_entries: list[dict[str, JsonValue]] = []
        for ordinal, row in enumerate(rows):
            descriptor = json.loads(str(row["descriptor_json"]))
            descriptor_fingerprint = fingerprint_json(descriptor)
            catalog_entries.append(
                {
                    "ordinal": ordinal,
                    "entry_kind": "pack",
                    "descriptor_fingerprint": descriptor_fingerprint,
                    "pack_id": str(row["pack_id"]),
                    "version": str(row["version"]),
                    "manifest_hash": str(row["manifest_hash"]),
                    "tool_spec_fingerprints": json.loads(str(row["tool_spec_fingerprints_json"])),
                }
            )
        run_catalog_content_stamp = fingerprint_json(
            {
                "domain": "legacy-run-catalog-migration-v1",
                "snapshot_ref": snapshot_ref,
                "run_id": run_id,
                "entries": catalog_entries,
            }
        )
        entry_set_hash = fingerprint_json(catalog_entries)
        request_scope = canonical_json(
            {
                "run_key": run_id,
                "project_key": None,
                "user_key": "default",
                "builtin_key": "builtin",
            }
        )
        await db.execute(
            """INSERT INTO capability_run_catalog_snapshots(
                run_catalog_content_stamp,request_owner_key,
                request_scope_canonical_json,request_scope_hash,
                catalog_generation_vector_json,
                catalog_generation_vector_hash,entry_set_hash,
                expected_entry_count,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                run_catalog_content_stamp,
                LEGACY_LOCAL_OWNER_KEY,
                request_scope,
                fingerprint_json(json.loads(request_scope)),
                "{}",
                fingerprint_json({}),
                entry_set_hash,
                len(rows),
                now,
            ),
        )
        owner_operation_id = f"legacy-boundary:{run_id}:1"
        intent_identity = {
            "domain": "capability-lease-intent-v1",
            "snapshot_ref": snapshot_ref,
            "run_id": run_id,
            "root_run_id": root_run_id,
            "owner_operation_id": owner_operation_id,
            "run_catalog_content_stamp": run_catalog_content_stamp,
            "entry_set_hash": entry_set_hash,
            "expected_entry_count": len(rows),
            "lease_generation": 1,
        }
        lease_intent_hash = fingerprint_json(intent_identity)
        all_released = all(row["released_at"] is not None for row in rows)
        status = "released" if all_released else "conflict"
        owner_record_ref = f"migration:legacy-unproven:{run_id}"
        owner_record_hash = fingerprint_json({"ref": owner_record_ref})
        start_fingerprint = fingerprint_json(
            {"legacy_snapshot_ref": snapshot_ref, "run_id": run_id}
        )
        released_at = max(float(row["released_at"]) for row in rows) if all_released else None
        await db.execute(
            """INSERT INTO capability_snapshot_lease_intents(
                lease_intent_id,lease_intent_hash,snapshot_ref,
                snapshot_ref_schema,run_id,root_run_id,request_id,turn_id,
                lease_generation,owner_operation_id,lease_owner_kind,
                owner_record_ref,owner_record_hash,start_fingerprint,
                run_catalog_content_stamp,entry_set_hash,
                expected_entry_count,status,prepared_at,bound_at,released_at,
                last_error
            ) VALUES(?,?,?,'legacy_v1',?,?,?,?,? ,?,'legacy_boundary',
                     ?,?,?,?,?,?,?,?, ?,?,?)""",
            (
                lease_intent_hash,
                lease_intent_hash,
                snapshot_ref,
                run_id,
                root_run_id,
                "migration:legacy",
                "migration:legacy",
                1,
                owner_operation_id,
                owner_record_ref,
                owner_record_hash,
                start_fingerprint,
                run_catalog_content_stamp,
                entry_set_hash,
                len(rows),
                status,
                min(float(row["acquired_at"]) for row in rows),
                min(float(row["acquired_at"]) for row in rows),
                released_at,
                None if all_released else "legacy_owner_record_unproven",
            ),
        )
        for ordinal, row in enumerate(rows):
            descriptor = json.loads(str(row["descriptor_json"]))
            descriptor_fingerprint = fingerprint_json(descriptor)
            visible_hash = fingerprint_json([])
            runtime_hash = fingerprint_json([])
            selected_binding_id = fingerprint_json(
                {
                    "domain": "legacy-migrated-binding-v1",
                    "snapshot_ref": snapshot_ref,
                    "run_id": run_id,
                    "pack_id": str(row["pack_id"]),
                }
            )
            await db.execute(
                """INSERT INTO capability_run_catalog_snapshot_entries(
                    run_catalog_content_stamp,ordinal,entry_kind,
                    selected_binding_id,selected_owner_key,selected_scope,
                    selected_scope_key,selected_binding_generation,
                    stable_host_binding_id,visible_bindings_json,
                    visible_binding_set_hash,descriptor_envelope_json,
                    descriptor_fingerprint,pack_id,version,manifest_hash,
                    host_provider_name,host_source,host_spec_version,
                    host_schema_hash,host_content_hash,host_build_identity,
                    tool_spec_fingerprints_json,instruction_refs_hash,
                    workflow_refs_hash,runtime_descriptor_hash
                ) VALUES(?,?,'pack',?,?,?,?,?,NULL,'[]',?,?,?,?,?,?,
                         NULL,NULL,NULL,NULL,NULL,NULL,?,?,?,?)""",
                (
                    run_catalog_content_stamp,
                    ordinal,
                    selected_binding_id,
                    LEGACY_LOCAL_OWNER_KEY,
                    "run",
                    run_id,
                    0,
                    visible_hash,
                    canonical_json(descriptor),
                    descriptor_fingerprint,
                    str(row["pack_id"]),
                    str(row["version"]),
                    str(row["manifest_hash"]),
                    str(row["tool_spec_fingerprints_json"]),
                    fingerprint_json([]),
                    fingerprint_json([]),
                    runtime_hash,
                ),
            )
            lease_entry_id = fingerprint_json(
                {
                    "domain": "capability-lease-entry-v2",
                    "lease_intent_id": lease_intent_hash,
                    "entry_ordinal": ordinal,
                }
            )
            await db.execute(
                """INSERT INTO capability_snapshot_leases_v2(
                    lease_entry_id,lease_intent_id,snapshot_ref,run_id,
                    root_run_id,run_catalog_content_stamp,entry_ordinal,
                    entry_kind,selected_binding_id,selected_owner_key,
                    selected_scope,selected_scope_key,
                    selected_binding_generation,stable_host_binding_id,
                    visible_binding_set_hash,descriptor_fingerprint,
                    runtime_descriptor_hash,pack_id,version,manifest_hash,
                    host_provider_name,host_source,host_spec_version,
                    host_schema_hash,host_content_hash,host_build_identity,
                    tool_spec_fingerprints_json,acquired_at,released_at
                ) VALUES(?,?,?,?,?,?,?,'pack',?,?,?,?,?,NULL,?,?,?,?,?,?,
                         NULL,NULL,NULL,NULL,NULL,NULL,?,?,?)""",
                (
                    lease_entry_id,
                    lease_intent_hash,
                    snapshot_ref,
                    run_id,
                    root_run_id,
                    run_catalog_content_stamp,
                    ordinal,
                    selected_binding_id,
                    LEGACY_LOCAL_OWNER_KEY,
                    "run",
                    run_id,
                    0,
                    visible_hash,
                    descriptor_fingerprint,
                    runtime_hash,
                    str(row["pack_id"]),
                    str(row["version"]),
                    str(row["manifest_hash"]),
                    str(row["tool_spec_fingerprints_json"]),
                    float(row["acquired_at"]),
                    (None if row["released_at"] is None else float(row["released_at"])),
                ),
            )
    await db.execute("DROP TABLE capability_snapshot_leases")
    await db.execute(
        "ALTER TABLE capability_snapshot_leases_v2 RENAME TO capability_snapshot_leases"
    )
    await db.execute(
        """CREATE INDEX idx_capability_snapshot_leases_active
           ON capability_snapshot_leases(
               pack_id,version,manifest_hash,released_at
           )"""
    )


async def migrate_capability_schema_v1_to_v2(
    db: aiosqlite.Connection,
) -> None:
    """Upgrade the execution-owned Capability sub-schema in-place.

    The caller owns the surrounding SQLite transaction.  Re-entry after a
    successful migration validates the v2 shape and performs no writes.
    """

    state = await (
        await db.execute(
            """SELECT schema_version FROM capability_schema_state
               WHERE singleton_id=1"""
        )
    ).fetchone()
    if state is None:
        raise CapabilitySchemaMissing(
            "capability_schema_missing", "capability schema state is absent"
        )
    version = int(state[0])
    if version > CAPABILITY_SCHEMA_VERSION:
        raise CapabilitySchemaMissing(
            "unsupported_capability_schema",
            f"capability schema is {version}, expected {CAPABILITY_SCHEMA_VERSION}",
        )
    if version in {2, 3, 4}:
        columns = await _capability_table_columns(db, "capability_bindings")
        required = {
            "owner_key",
            "management_policy",
            "management_generation",
        }
        if not required.issubset(columns):
            raise CapabilitySchemaMissing(
                "capability_schema_v2_incomplete",
                "capability binding owner columns are missing",
            )
        await _install_capability_v2_tables(db)
        await _migrate_snapshot_leases_v1_to_v2(db)
        return
    if version != 1:
        raise CapabilitySchemaMissing(
            "unsupported_capability_schema",
            f"cannot migrate capability schema {version}",
        )

    old_count = int(
        (await (await db.execute("SELECT COUNT(*) FROM capability_bindings")).fetchone())[0]
    )
    await db.execute(
        """
        CREATE TABLE capability_bindings_v2 (
            binding_id TEXT PRIMARY KEY,
            owner_key TEXT NOT NULL,
            scope TEXT NOT NULL CHECK(scope IN ('builtin','run','project','user')),
            scope_key TEXT NOT NULL,
            pack_id TEXT NOT NULL,
            active_version TEXT NOT NULL,
            active_manifest_hash TEXT NOT NULL,
            generation INTEGER NOT NULL CHECK(generation>0),
            enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
            management_policy TEXT NOT NULL CHECK(
                management_policy IN (
                    'host_managed','user_managed','legacy_import'
                )
            ),
            management_generation INTEGER NOT NULL
                CHECK(management_generation>=0),
            updated_at REAL NOT NULL,
            UNIQUE(owner_key,scope,scope_key,pack_id),
            FOREIGN KEY(pack_id,active_version,active_manifest_hash)
                REFERENCES capability_versions(pack_id,version,manifest_hash)
                ON DELETE RESTRICT
        )
        """
    )
    await db.execute(
        """
        INSERT INTO capability_bindings_v2(
            binding_id,owner_key,scope,scope_key,pack_id,active_version,
            active_manifest_hash,generation,enabled,management_policy,
            management_generation,updated_at
        )
        SELECT binding_id,
               CASE WHEN scope='builtin'
                    THEN 'builtin'
                    ELSE ? END,
               scope,scope_key,pack_id,active_version,active_manifest_hash,
               generation,enabled,
               CASE WHEN scope='builtin'
                    THEN 'host_managed'
                    ELSE 'legacy_import' END,
               0,updated_at
        FROM capability_bindings
        """,
        (LEGACY_LOCAL_OWNER_KEY,),
    )
    new_count = int(
        (await (await db.execute("SELECT COUNT(*) FROM capability_bindings_v2")).fetchone())[0]
    )
    if new_count != old_count:
        raise CapabilityStoreError(
            "capability_binding_copy_failed",
            "capability binding migration row count changed",
        )
    await db.execute("DROP TABLE capability_bindings")
    await db.execute("ALTER TABLE capability_bindings_v2 RENAME TO capability_bindings")
    await db.execute(
        """CREATE INDEX idx_capability_bindings_visible
           ON capability_bindings(
               owner_key,scope,scope_key,enabled,pack_id
           )"""
    )
    await _install_capability_v2_tables(db)
    await _migrate_snapshot_leases_v1_to_v2(db)

    owner_rows = await (
        await db.execute(
            """SELECT DISTINCT owner_key,scope,scope_key
               FROM capability_bindings
               ORDER BY owner_key,scope,scope_key"""
        )
    ).fetchall()
    now = time.time()
    for owner_key, scope, scope_key in owner_rows:
        key = OwnerScopeKey(str(owner_key), str(scope), str(scope_key))
        stamp = await _owner_binding_stamp_tx(db, key)
        await db.execute(
            """INSERT INTO capability_owner_detail_versions(
                owner_key,scope,scope_key,row_state,version,
                owner_catalog_generation,committed_owner_binding_set_stamp,
                manager_receipt_set_hash,updated_at
            ) VALUES(?,?,?,'existing',1,1,?,?,?)""",
            (
                key.owner_key,
                key.scope,
                key.scope_key,
                stamp.fingerprint,
                EMPTY_RECEIPT_SET_HASH,
                now,
            ),
        )
    await db.execute(
        """UPDATE capability_schema_state SET schema_version=?,updated_at=?
           WHERE singleton_id=1 AND schema_version=1""",
        (2, now),
    )


async def _execute_capability_schema_script(
    db: aiosqlite.Connection,
    script: str,
) -> None:
    """Execute complete SQL statements without committing the caller's transaction."""

    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            pending = ""
            if statement:
                await db.execute(statement)
    if pending.strip():
        raise CapabilitySchemaMissing(
            "capability_schema_script_incomplete",
            "capability verification schema contains an incomplete statement",
        )


async def migrate_capability_schema_v2_to_v3(
    db: aiosqlite.Connection,
) -> None:
    """Make the execution database the sole Skill-install verification owner.

    ProductState v4 retains its historical physical tables for compatibility,
    but production installation, Manager publication, SDK Run evidence, and
    verification settlement all use this execution-owned Capability schema.
    The caller owns the surrounding transaction.
    """

    state = await (
        await db.execute("SELECT schema_version FROM capability_schema_state WHERE singleton_id=1")
    ).fetchone()
    if state is None:
        raise CapabilitySchemaMissing(
            "capability_schema_missing", "capability schema state is absent"
        )
    version = int(state[0])
    if version in {3, 4}:
        columns = await _capability_table_columns(db, "capability_skill_install_intents")
        required_columns = {
            "verification_attempt_generation",
            "current_verification_attempt_id",
            "migrated_verification_provenance",
        }
        tables = {
            str(row[0])
            for row in await (
                await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
        required_tables = {
            "capability_skill_install_verification_attempts",
            "capability_skill_install_verification_attestations",
            "capability_skill_install_verification_migration_quarantine",
        }
        if not required_columns.issubset(columns) or not required_tables.issubset(tables):
            raise CapabilitySchemaMissing(
                "capability_schema_v3_incomplete",
                "execution Skill-install verification schema is incomplete",
            )
        return
    if version != 2:
        raise CapabilitySchemaMissing(
            "unsupported_capability_schema",
            f"cannot migrate capability schema {version} to 3",
        )

    # Import lazily to avoid the ProductState schema's historical dependency
    # on the frozen Capability v1/v2 DDL during module initialization.
    from deskpet.product_state.schema import (
        VERIFICATION_V3_COLUMNS_SQL,
        VERIFICATION_V4_QUARANTINE_SQL,
        VERIFICATION_V4_SCHEMA_SQL,
    )

    for script in (
        VERIFICATION_V3_COLUMNS_SQL,
        VERIFICATION_V4_SCHEMA_SQL,
        VERIFICATION_V4_QUARANTINE_SQL,
    ):
        await _execute_capability_schema_script(db, script)
    await db.execute(
        "UPDATE capability_schema_state SET schema_version=3,updated_at=? "
        "WHERE singleton_id=1 AND schema_version=2",
        (time.time(),),
    )
    await migrate_capability_schema_v2_to_v3(db)


async def migrate_capability_schema_v3_to_v4(
    db: aiosqlite.Connection,
) -> None:
    """Accept the SDK's zero-based authorization decision version.

    The v3 table accidentally required ``confirmation_version > 0`` even
    though the SDK's first durable decision is version zero.  Rebuild only the
    intent table so the persisted value remains the exact SDK identity rather
    than an offset or translated surrogate.

    Callers migrating a database that can contain child rows must temporarily
    disable SQLite foreign-key enforcement around the surrounding transaction,
    then run ``foreign_key_check`` before commit.
    """

    state = await (
        await db.execute("SELECT schema_version FROM capability_schema_state WHERE singleton_id=1")
    ).fetchone()
    if state is None:
        raise CapabilitySchemaMissing(
            "capability_schema_missing", "capability schema state is absent"
        )
    version = int(state[0])
    if version == 4:
        row = await (
            await db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='capability_skill_install_intents'"
            )
        ).fetchone()
        normalized = "" if row is None or row[0] is None else str(row[0]).replace(" ", "")
        if "CHECK(confirmation_version>=0)" not in normalized:
            raise CapabilitySchemaMissing(
                "capability_schema_v4_incomplete",
                "Skill-install confirmation version still rejects SDK version zero",
            )
        return
    if version != 3:
        raise CapabilitySchemaMissing(
            "unsupported_capability_schema",
            f"cannot migrate capability schema {version} to 4",
        )

    table_row = await (
        await db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='capability_skill_install_intents'"
        )
    ).fetchone()
    if table_row is None or table_row[0] is None:
        raise CapabilitySchemaMissing(
            "capability_schema_v3_incomplete",
            "Skill-install intent table is absent",
        )
    original_sql = str(table_row[0])
    if "CHECK(confirmation_version>0)" not in original_sql.replace(" ", ""):
        raise CapabilitySchemaMissing(
            "capability_schema_v3_incomplete",
            "Skill-install confirmation version constraint is unexpected",
        )
    dependent_sql = tuple(
        (str(row[0]), str(row[1]), str(row[2]))
        for row in await (
            await db.execute(
                "SELECT type,name,sql FROM sqlite_master "
                "WHERE type IN ('index','trigger') AND sql IS NOT NULL "
                "AND sql LIKE '%capability_skill_install_intents%' "
                "ORDER BY type,name"
            )
        ).fetchall()
    )
    columns = tuple(
        str(row[1])
        for row in await (
            await db.execute("PRAGMA table_info(capability_skill_install_intents)")
        ).fetchall()
    )
    column_list = ",".join(columns)
    replacement_sql = original_sql.replace(
        "CREATE TABLE capability_skill_install_intents",
        "CREATE TABLE capability_skill_install_intents_v4",
        1,
    ).replace(
        "CHECK(confirmation_version>0)",
        "CHECK(confirmation_version>=0)",
        1,
    )
    await db.execute(replacement_sql)
    await db.execute(
        f"INSERT INTO capability_skill_install_intents_v4({column_list}) "
        f"SELECT {column_list} FROM capability_skill_install_intents"
    )
    for object_type, name, _sql in dependent_sql:
        await db.execute(f"DROP {object_type.upper()} {name}")
    await db.execute("DROP TABLE capability_skill_install_intents")
    await db.execute(
        "ALTER TABLE capability_skill_install_intents_v4 RENAME TO capability_skill_install_intents"
    )
    for _object_type, _name, sql in dependent_sql:
        await db.execute(sql)
    await db.execute(
        "UPDATE capability_schema_state SET schema_version=4,updated_at=? "
        "WHERE singleton_id=1 AND schema_version=3",
        (time.time(),),
    )
    await migrate_capability_schema_v3_to_v4(db)


async def install_capability_schema(db: aiosqlite.Connection) -> None:
    """Install idempotent DDL on a caller-owned execution DB connection.

    Production must call this from the next workflow schema migration.  It is
    separate from ``CapabilityStore.initialize`` so repository construction
    cannot silently become a competing schema owner.
    """

    await db.executescript(CAPABILITY_SCHEMA_SQL)


async def initialize_capability_database(path: str | Path) -> Path:
    """Test/dev helper for a database whose execution schema owner is absent."""

    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=FULL")
        await db.execute("PRAGMA busy_timeout=5000")
        await install_capability_schema(db)
        await migrate_capability_schema_v1_to_v2(db)
        await migrate_capability_schema_v2_to_v3(db)
        await migrate_capability_schema_v3_to_v4(db)
        await db.commit()
    return db_path


class CapabilityStore(
    VersionBindingMixin,
    RunSnapshotMixin,
    RuntimeSetMixin,
    OperationMixin,
    RuntimeLeaseMixin,
    SnapshotLeaseMixin,
    ReceiptPolicyMixin,
):
    """Repository over product state (or explicit pre-cutover compatibility)."""


# The install aggregate implementation is shared by the transaction facade and
# repository.  Bind it explicitly here so callers get both caller-owned and
# self-owned transaction APIs without duplicating the CAS implementation.
for _skill_install_method in (
    "_create_skill_install_intent_tx",
    "create_skill_install_intent",
    "get_skill_install_intent",
    "get_skill_install_intent_for_effect",
    "_skill_install_members_tx",
    "skill_install_members",
    "pending_skill_install_intents",
    "_cas_skill_install_intent_tx",
    "cas_skill_install_intent",
    "claim_skill_install_retry",
    "settle_skill_install_retry",
    "_handoff_skill_install_intent_tx",
    "handoff_skill_install_intent",
    "operation_members",
    "put_operation_evidence",
    "put_publish_intent_members",
    "publish_intent_members",
    "operation_evidence",
    "bind_skill_install_confirmation",
):
    setattr(
        CapabilityStore, _skill_install_method, getattr(CapabilityStoreTx, _skill_install_method)
    )

for _name, _method in {
    "get_skill_install_verification_attempt": _get_skill_install_verification_attempt,
    "get_current_skill_install_verification_attempt": _get_current_skill_install_verification_attempt,
    "list_pending_skill_install_verification_attempts": _list_pending_skill_install_verification_attempts,
    "list_succeeded_global_skill_install_intents": _list_succeeded_global_skill_install_intents,
    "get_skill_install_verification_attestation": _get_skill_install_verification_attestation,
    "allocate_skill_install_verification_attempt": _allocate_skill_install_verification_attempt,
    "cas_skill_install_verification_attempt": _cas_skill_install_verification_attempt,
    "supersede_skill_install_verification_attempt": _supersede_skill_install_verification_attempt,
    "attach_skill_install_verification_attestation": _attach_skill_install_verification_attestation,
}.items():
    setattr(CapabilityStore, _name, _method)


__all__ = [
    "ALL_CAPABILITY_OPERATION_PHASES",
    "BATCH_CAPABILITY_OPERATION_PHASES",
    "CAPABILITY_OPERATION_PHASES",
    "CAPABILITY_SCHEMA_SQL",
    "CAPABILITY_SCHEMA_V1_SQL",
    "CAPABILITY_SCHEMA_VERSION",
    "CapabilityOperationRecord",
    "CapabilityOperationEvidence",
    "CapabilityOperationMember",
    "CapabilityOperationReceipt",
    "CapabilityPublishIntent",
    "CapabilityPublishIntentMember",
    "CapabilitySkillInstallHandoff",
    "CapabilitySkillInstallIntent",
    "CapabilitySkillInstallMember",
    "CapabilitySkillInstallVerificationAttempt",
    "CapabilitySkillInstallVerificationAttestation",
    "CapabilityRefreshCommit",
    "CapabilityRefreshIntent",
    "CapabilityRuntimeCallLease",
    "CapabilityRuntimeInstanceSpec",
    "CapabilityRuntimeLease",
    "CapabilityRuntimePrepareIntent",
    "CapabilityRuntimeProjectionReceipt",
    "CapabilityRuntimeSetRecord",
    "CapabilitySchemaMissing",
    "CapabilitySnapshotLeaseReleaseReceipt",
    "CapabilitySnapshotLeaseIntent",
    "CapabilityStore",
    "CapabilityStoreConflict",
    "CapabilityStoreError",
    "CapabilityStoreState",
    "CapabilityStoreTx",
    "CapabilityVersionRecord",
    "CapabilityVersionStorage",
    "PreparedRunCatalogProjection",
    "SnapshotLeaseAdoptionReceipt",
    "SnapshotProjectionState",
    "LegacyAuthorizationImportOutcome",
    "LegacyAuthorizationImportRecord",
    "initialize_capability_database",
    "install_capability_schema",
    "migrate_capability_schema_v1_to_v2",
    "migrate_capability_schema_v2_to_v3",
    "migrate_capability_schema_v3_to_v4",
]
