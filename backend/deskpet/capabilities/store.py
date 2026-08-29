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

import asyncio
import inspect
import hashlib
import json
import math
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Literal, Mapping, Protocol, Sequence

import aiosqlite

from deskpet.permissions.policy import (
    AuthorizationMode,
    AuthorizationPolicyProvenance,
    AuthorizationPolicyState,
)
from deskpet.types.task_grants import TaskGrant
from deskpet.execution.contracts import root_idempotency_key

from .contracts import (
    CapabilityBinding,
    CapabilityCatalogEntry,
    CapabilityScope,
    CapabilityVersionDescriptor,
    EMPTY_OWNER_BINDING_SET_STAMP,
    EMPTY_RECEIPT_SET_HASH,
    JsonValue,
    LEGACY_LOCAL_OWNER_KEY,
    OwnerBindingSetStamp,
    OwnerScopeKey,
    PlatformDetailSnapshot,
    PlatformDetailToken,
    PlatformDetailTokenVector,
    RunCatalogEntryIdentity,
    RunCatalogContentStamp,
    canonical_json,
    fingerprint_json,
)
from .failure_receipts import (
    CapabilityFailureReceipt,
    CapabilityFailureReceiptIssuer,
    CapabilityRepairAttempt,
)
from .refresh_contracts import (
    CapabilityOperationReceipt,
    CapabilityRefreshCommit,
    CapabilityRefreshIntent,
    refresh_intent_from_dict,
)

CAPABILITY_SCHEMA_VERSION = 2
CAPABILITY_OPERATION_PHASES: tuple[str, ...] = (
    "planned",
    "staged",
    "verified",
    "environment_ready",
    "candidate_ready",
    "publish_intent",
    "catalog_swapped",
    "bound",
    "published",
)
BATCH_CAPABILITY_OPERATION_PHASES: tuple[str, ...] = (
    "batch_staged",
    "batch_prepared",
    "batch_publish_intent",
    "batch_files_materialized",
    "batch_catalog_swapped",
    "batch_committed",
)
ALL_CAPABILITY_OPERATION_PHASES = (
    CAPABILITY_OPERATION_PHASES + BATCH_CAPABILITY_OPERATION_PHASES
)
LegacyAuthorizationImportOutcome = Literal[
    "imported",
    "missing",
    "invalid",
]

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
    CAPABILITY_SCHEMA_V1_SQL
    .replace(
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


class CapabilityStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CapabilityStoreConflict(CapabilityStoreError):
    pass


class CapabilitySchemaMissing(CapabilityStoreError):
    pass


class ExecutionDatabaseOwner(Protocol):
    path: Path

    async def initialize(self) -> None:
        ...


@dataclass(frozen=True, slots=True)
class CapabilityStoreState:
    schema_version: int
    catalog_generation: int
    binding_generation: int
    pending_publish_count: int


@dataclass(frozen=True, slots=True)
class CapabilitySnapshotLeaseIntent:
    lease_intent_id: str
    lease_intent_hash: str
    snapshot_ref: str
    snapshot_ref_schema: str
    run_id: str
    root_run_id: str
    request_id: str
    turn_id: str
    lease_generation: int
    owner_operation_id: str
    lease_owner_kind: str
    owner_record_ref: str | None
    owner_record_hash: str | None
    start_fingerprint: str | None
    run_catalog_content_stamp: str
    entry_set_hash: str
    expected_entry_count: int
    status: str
    prepared_at: float
    bound_at: float | None
    released_at: float | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class CapabilityRuntimeProjectionReceipt:
    projection_receipt_id: str
    projection_receipt_hash: str
    purpose: str
    lease_intent_id: str | None
    run_catalog_content_stamp: str | None
    process_instance_id: str
    process_catalog_stamp: str | None
    prepared_tool_set_hash: str | None
    pin_token_hash: str | None
    status: str
    created_at: float
    ready_at: float | None
    retired_at: float | None


@dataclass(frozen=True, slots=True)
class PreparedRunCatalogProjection:
    intent: CapabilitySnapshotLeaseIntent
    projection_receipt: CapabilityRuntimeProjectionReceipt
    process_catalog_stamp: str
    status: str = "prepared"


@dataclass(frozen=True, slots=True)
class SnapshotLeaseAdoptionReceipt:
    intent_id: str
    intent_hash: str
    status: str
    owner_record_ref: str
    owner_record_hash: str
    start_fingerprint: str
    projection_receipt_id: str
    projection_receipt_hash: str


@dataclass(frozen=True, slots=True)
class CapabilitySnapshotLeaseReleaseReceipt:
    release_receipt_id: str
    release_receipt_hash: str
    lease_intent_id: str
    run_id: str
    snapshot_ref: str
    release_reason: str
    owner_terminal_or_transition_ref: str
    owner_terminal_or_transition_hash: str
    last_active_member_for_snapshot: bool
    cleanup_ticket_ref: str
    cleanup_ticket_hash: str
    cleanup_status: str
    status: str
    created_at: float
    cleaned_at: float | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class SnapshotProjectionState:
    lease_intent_id: str
    lease_intent_hash: str
    intent_status: str
    start_fingerprint: str | None
    run_catalog_content_stamp: str
    process_instance_id: str
    process_catalog_stamp: str
    projection_status: str
    projection_receipt_id: str
    projection_receipt_hash: str
    pin_token_hash: str | None


def _validated_digest(value: str, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class CapabilityVersionStorage:
    pack_id: str
    version: str
    manifest_hash: str
    storage_key: str
    pack_storage_schema: str
    pack_root_hash: str
    environment_storage_schema: str
    environment_root_hash: str
    archive_hash: str
    created_at: float

    def __post_init__(self) -> None:
        if not self.pack_id or not self.version:
            raise ValueError("version storage identity is required")
        object.__setattr__(
            self, "manifest_hash", _validated_digest(self.manifest_hash, "manifest_hash")
        )
        if (
            len(self.storage_key) != 32
            or any(character not in "0123456789abcdef" for character in self.storage_key)
        ):
            raise ValueError("storage_key must be 32 lowercase hex characters")
        if self.pack_storage_schema not in {"legacy_v1", "content_key_v2_pack"}:
            raise ValueError("invalid pack storage schema")
        if self.environment_storage_schema not in {
            "legacy_v1",
            "content_key_v2_environment",
        }:
            raise ValueError("invalid environment storage schema")
        for name in ("pack_root_hash", "environment_root_hash", "archive_hash"):
            object.__setattr__(
                self, name, _validated_digest(getattr(self, name), name)
            )
        if not math.isfinite(float(self.created_at)):
            raise ValueError("created_at must be finite")

    @staticmethod
    def derive_storage_key(
        pack_id: str, version: str, manifest_hash: str
    ) -> str:
        _validated_digest(manifest_hash, "manifest_hash")
        return hashlib.sha256(
            canonical_json(
                ["capability-storage-v2", pack_id, version, manifest_hash]
            ).encode("utf-8")
        ).hexdigest()[:32]

    @property
    def pack_root(self) -> str:
        return f"cv2/{self.storage_key}/p"

    @property
    def environment_root(self) -> str:
        return f"cv2/{self.storage_key}/e"

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "version": self.version,
            "manifest_hash": self.manifest_hash,
            "storage_key": self.storage_key,
            "pack_storage_schema": self.pack_storage_schema,
            "pack_root_hash": self.pack_root_hash,
            "environment_storage_schema": self.environment_storage_schema,
            "environment_root_hash": self.environment_root_hash,
            "archive_hash": self.archive_hash,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class CapabilityRuntimeInstanceSpec:
    runtime_instance_id: str
    entry_id: str
    ordinal: int
    runtime_kind: str
    adapter_fingerprint: str
    argv_hash: str
    env_scope_hash: str
    workdir: str

    def __post_init__(self) -> None:
        if not self.runtime_instance_id or not self.entry_id or not self.workdir:
            raise ValueError("runtime instance identity and workdir are required")
        if not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise ValueError("runtime instance ordinal must be non-negative")
        if self.runtime_kind not in {
            "dependency_probe",
            "tool_health",
            "mcp",
            "local_runtime",
        }:
            raise ValueError("invalid runtime kind")
        for name in ("adapter_fingerprint", "argv_hash", "env_scope_hash"):
            object.__setattr__(
                self, name, _validated_digest(getattr(self, name), name)
            )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "runtime_instance_id": self.runtime_instance_id,
            "entry_id": self.entry_id,
            "ordinal": self.ordinal,
            "runtime_kind": self.runtime_kind,
            "adapter_fingerprint": self.adapter_fingerprint,
            "argv_hash": self.argv_hash,
            "env_scope_hash": self.env_scope_hash,
            "workdir": self.workdir,
        }


@dataclass(frozen=True, slots=True)
class CapabilityRuntimePrepareIntent:
    operation_id: str
    runtime_instance_id: str
    entry_id: str
    ordinal: int
    runtime_kind: str
    status: str
    runtime_set_hash: str
    authorization_hash: str | None
    launch_revocation_epoch: int
    adapter_fingerprint: str
    argv_hash: str
    env_scope_hash: str
    workdir: str
    job_identity: str | None
    pid: int | None
    process_create_time: float | None
    session_identity: str | None
    start_outcome: str | None
    start_ack_at: float | None
    health_outcome_hash: str | None
    started_at: float | None
    updated_at: float
    last_error: str | None


@dataclass(frozen=True, slots=True)
class CapabilityRuntimeSetRecord:
    operation_id: str
    purpose: str
    lifecycle_action: str | None
    owner_activation_id: str | None
    lease_activation_id: str | None
    target_pack_id: str | None
    target_version: str | None
    owner_binding_set_stamp: str | None
    run_catalog_content_stamp: str | None
    target_package_hash: str | None
    target_manifest_hash: str | None
    runtime_set_hash: str
    expected_instance_count: int
    authorization_hash: str | None
    launch_revocation_epoch: int
    status: str
    created_at: float
    updated_at: float
    last_error: str | None
    instances: tuple[CapabilityRuntimePrepareIntent, ...]


@dataclass(frozen=True, slots=True)
class CapabilityVersionRecord:
    descriptor: CapabilityVersionDescriptor
    install_path: Path
    validation_status: str
    expected_tool_fingerprints: tuple[str, ...]
    parent_version: str | None
    parent_manifest_hash: str | None
    derived_from_receipt_ref: str | None
    created_at: float


@dataclass(frozen=True, slots=True)
class CapabilityOperationRecord:
    operation_id: str
    idempotency_key: str
    root_run_id: str | None
    kind: str
    pack_id: str | None
    requested_scope: str | None
    requested_scope_key: str | None
    phase: str
    status: str
    request: Mapping[str, JsonValue]
    error: Mapping[str, JsonValue] | None
    started_at: float
    updated_at: float
    ended_at: float | None


@dataclass(frozen=True, slots=True)
class CapabilityPublishIntent:
    intent_id: str
    operation_id: str
    expected_registry_revision: int
    old_specs: tuple[Mapping[str, JsonValue], ...]
    new_specs: tuple[Mapping[str, JsonValue], ...]
    old_binding: Mapping[str, JsonValue] | None
    new_binding: Mapping[str, JsonValue]
    phase: str
    status: str
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class CapabilitySkillInstallIntent:
    intent_id: str
    effect_id: str
    call_id: str
    root_run_id: str
    run_id: str
    channel: str
    project_scope_key: str
    principal_id: str
    source: Mapping[str, JsonValue]
    exact_commit: str
    archive_hash: str
    raw_tree_hash: str
    member_set_stamp: str
    permission_set_hash: str
    confirmation_nonce: str
    confirmation_version: int
    expires_at: float
    status: str
    state_version: int
    settlement_ref: str | None
    cleanup_ref: str | None
    verification_ref: str | None
    error: Mapping[str, JsonValue] | None
    created_at: float
    updated_at: float
    verification_attempt_generation: int = 0
    current_verification_attempt_id: str | None = None
    migrated_verification_provenance: str | None = None

    @property
    def schema_version(self) -> int:
        return 2 if self.source.get("schema") == "global-skill-install-source-v2" else 1

    @property
    def install_scope(self) -> str:
        return "user" if self.schema_version == 2 else "project"

    @property
    def install_scope_key(self) -> str:
        return self.project_scope_key


@dataclass(frozen=True, slots=True)
class CapabilitySkillInstallVerificationAttempt:
    attempt_id: str
    intent_id: str
    attempt_generation: int
    state_version: int
    status: str
    verifier_session_id: str
    request_id: str
    turn_id: str
    expected_run_id: str
    actual_run_id: str | None
    manager_operation_id: str
    manager_receipt_hash: str
    committed_set_stamp: str
    project_scope_key: str
    expected_member_set_stamp: str
    run_catalog_content_stamp: str | None
    terminal_event_id: str | None
    terminal_event_hash: str | None
    evidence_hash: str | None
    superseded_by_attempt_id: str | None
    error: Mapping[str, JsonValue] | None
    created_at: float
    updated_at: float
    terminal_at: float | None


@dataclass(frozen=True, slots=True)
class CapabilitySkillInstallVerificationAttestation:
    attestation_id: str
    intent_id: str
    attempt_id: str | None
    provenance: str
    runtime_proof_valid: bool
    verification_ref: str
    evidence_hash: str | None
    attestation: Mapping[str, JsonValue]
    created_at: float


@dataclass(frozen=True, slots=True)
class CapabilitySkillInstallMember:
    intent_id: str
    ordinal: int
    normalized_name: str
    pack_id: str
    version: str
    manifest_hash: str
    content_hash: str
    source_digest: str
    member: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class CapabilityOperationMember:
    operation_id: str
    ordinal: int
    normalized_name: str
    pack_id: str
    version: str
    manifest_hash: str
    content_hash: str
    source_digest: str
    committed_version: str | None = None
    committed_manifest_hash: str | None = None
    committed_set_stamp: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityPublishIntentMember:
    publish_intent_id: str
    operation_id: str
    ordinal: int
    old_binding: Mapping[str, JsonValue] | None
    new_binding: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class CapabilitySkillInstallHandoff:
    intent_id: str
    operation_id: str
    confirmation_receipt_hash: str
    member_set_stamp: str
    created_at: float


@dataclass(frozen=True, slots=True)
class CapabilityOperationEvidence:
    operation_id: str
    evidence_kind: str
    idempotency_key: str
    evidence: Mapping[str, JsonValue]
    created_at: float


@dataclass(frozen=True, slots=True)
class CapabilityRuntimeLease:
    runtime_lease_id: str
    pack_id: str
    version: str
    manifest_hash: str
    server_id: str
    pid: int | None
    run_id: str | None
    session_generation: int
    state: str
    heartbeat_at: float
    created_at: float

    @property
    def provenance_ref(self) -> str:
        """Stable logical provenance; physical reconnects do not change it."""

        return fingerprint_json(
            {
                "runtime_lease_id": self.runtime_lease_id,
                "pack_id": self.pack_id,
                "version": self.version,
                "manifest_hash": self.manifest_hash,
                "server_id": self.server_id,
            }
        )


@dataclass(frozen=True, slots=True)
class CapabilityRuntimeCallLease:
    call_lease_id: str
    runtime_lease_id: str
    effect_id: str
    session_generation: int
    state: str
    started_at: float
    ended_at: float | None


@dataclass(frozen=True, slots=True)
class LegacyAuthorizationImportRecord:
    """Durable proof that one legacy authorization source was consumed."""

    source_key: str
    outcome: LegacyAuthorizationImportOutcome
    source_fingerprint: str | None
    imported_mode: AuthorizationMode | None
    error_code: str | None
    imported_at: float

    def __post_init__(self) -> None:
        if not self.source_key.strip():
            raise ValueError("legacy import source_key is required")
        if self.outcome not in {"imported", "missing", "invalid"}:
            raise ValueError(f"unknown legacy import outcome: {self.outcome}")
        if self.source_fingerprint is not None and (
            len(self.source_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.source_fingerprint
            )
        ):
            raise ValueError(
                "legacy import source_fingerprint must be a lowercase SHA-256 digest"
            )
        if self.imported_mode is not None and self.imported_mode not in {
            "manual",
            "auto",
        }:
            raise ValueError(
                f"unknown imported authorization mode: {self.imported_mode}"
            )
        if not math.isfinite(float(self.imported_at)):
            raise ValueError("legacy import timestamp must be finite")
        if self.outcome == "imported":
            if (
                self.source_fingerprint is None
                or self.imported_mode is None
                or self.error_code is not None
            ):
                raise ValueError("imported legacy state requires a source and mode")
        elif self.outcome == "missing":
            if (
                self.source_fingerprint is not None
                or self.imported_mode is not None
                or self.error_code is not None
            ):
                raise ValueError("missing legacy state cannot carry source data")
        elif self.imported_mode is not None or not self.error_code:
            raise ValueError(
                "invalid legacy state requires no mode and an error code"
            )


def _json_object(raw: str | None) -> Mapping[str, JsonValue] | None:
    if raw is None:
        return None
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise CapabilityStoreError("corrupt_json", "stored value is not an object")
    return value


def _json_array(raw: str) -> tuple[Any, ...]:
    value = json.loads(raw)
    if not isinstance(value, list):
        raise CapabilityStoreError("corrupt_json", "stored value is not an array")
    return tuple(value)


def _descriptor_from_json(raw: str) -> CapabilityVersionDescriptor:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise CapabilityStoreError("corrupt_descriptor", "descriptor is not an object")
    try:
        return CapabilityVersionDescriptor(
            capability_id=str(value["capability_id"]),
            display_name=str(value["display_name"]),
            version=str(value["version"]),
            kind=str(value["kind"]),  # type: ignore[arg-type]
            source=str(value["source"]),
            description=str(value["description"]),
            aliases=tuple(str(item) for item in value["aliases"]),
            logical_tool_ids=tuple(str(item) for item in value["logical_tool_ids"]),
            provider_tool_names=tuple(
                str(item) for item in value["provider_tool_names"]
            ),
            permission_categories=tuple(
                str(item) for item in value["permission_categories"]
            ),
            effect_kinds=tuple(str(item) for item in value["effect_kinds"]),
            schema_hash=str(value["schema_hash"]),
            manifest_hash=str(value["manifest_hash"]),
            health=str(value["health"]),  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CapabilityStoreError(
            "corrupt_descriptor", f"cannot decode capability descriptor: {exc}"
        ) from exc


def _binding_from_row(row: Mapping[str, Any]) -> CapabilityBinding:
    keys = set(row.keys())
    scope = str(row["scope"])
    return CapabilityBinding(
        binding_id=str(row["binding_id"]),
        capability_id=str(row["pack_id"]),
        version=str(row["active_version"]),
        manifest_hash=str(row["active_manifest_hash"]),
        scope=scope,  # type: ignore[arg-type]
        scope_key=str(row["scope_key"]),
        active=bool(row["enabled"]),
        generation=int(row["generation"]),
        owner_key=(
            str(row["owner_key"])
            if "owner_key" in keys
            else _default_owner_key(scope)
        ),
        management_policy=(
            str(row["management_policy"])
            if "management_policy" in keys
            else ("host_managed" if scope == "builtin" else "legacy_import")
        ),  # type: ignore[arg-type]
        management_generation=(
            int(row["management_generation"])
            if "management_generation" in keys
            else 0
        ),
    )


def _detail_token_from_row(row: Mapping[str, Any]) -> PlatformDetailToken:
    return PlatformDetailToken(
        key=OwnerScopeKey(
            str(row["owner_key"]),
            str(row["scope"]),  # type: ignore[arg-type]
            str(row["scope_key"]),
        ),
        exists=str(row["row_state"]) == "existing",
        version=int(row["version"]),
        owner_catalog_generation=int(row["owner_catalog_generation"]),
        committed_owner_binding_set_stamp=str(
            row["committed_owner_binding_set_stamp"]
        ),
        manager_receipt_set_hash=str(row["manager_receipt_set_hash"]),
    )


def _snapshot_lease_intent_from_row(
    row: Mapping[str, Any],
) -> CapabilitySnapshotLeaseIntent:
    return CapabilitySnapshotLeaseIntent(
        lease_intent_id=str(row["lease_intent_id"]),
        lease_intent_hash=str(row["lease_intent_hash"]),
        snapshot_ref=str(row["snapshot_ref"]),
        snapshot_ref_schema=str(row["snapshot_ref_schema"]),
        run_id=str(row["run_id"]),
        root_run_id=str(row["root_run_id"]),
        request_id=str(row["request_id"]),
        turn_id=str(row["turn_id"]),
        lease_generation=int(row["lease_generation"]),
        owner_operation_id=str(row["owner_operation_id"]),
        lease_owner_kind=str(row["lease_owner_kind"]),
        owner_record_ref=(
            None
            if row["owner_record_ref"] is None
            else str(row["owner_record_ref"])
        ),
        owner_record_hash=(
            None
            if row["owner_record_hash"] is None
            else str(row["owner_record_hash"])
        ),
        start_fingerprint=(
            None
            if row["start_fingerprint"] is None
            else str(row["start_fingerprint"])
        ),
        run_catalog_content_stamp=str(row["run_catalog_content_stamp"]),
        entry_set_hash=str(row["entry_set_hash"]),
        expected_entry_count=int(row["expected_entry_count"]),
        status=str(row["status"]),
        prepared_at=float(row["prepared_at"]),
        bound_at=None if row["bound_at"] is None else float(row["bound_at"]),
        released_at=(
            None if row["released_at"] is None else float(row["released_at"])
        ),
        last_error=None if row["last_error"] is None else str(row["last_error"]),
    )


def _runtime_projection_receipt_from_row(
    row: Mapping[str, Any],
) -> CapabilityRuntimeProjectionReceipt:
    return CapabilityRuntimeProjectionReceipt(
        projection_receipt_id=str(row["projection_receipt_id"]),
        projection_receipt_hash=str(row["projection_receipt_hash"]),
        purpose=str(row["purpose"]),
        lease_intent_id=(
            None
            if row["lease_intent_id"] is None
            else str(row["lease_intent_id"])
        ),
        run_catalog_content_stamp=(
            None
            if row["run_catalog_content_stamp"] is None
            else str(row["run_catalog_content_stamp"])
        ),
        process_instance_id=str(row["process_instance_id"]),
        process_catalog_stamp=(
            None
            if row["process_catalog_stamp"] is None
            else str(row["process_catalog_stamp"])
        ),
        prepared_tool_set_hash=(
            None
            if row["prepared_tool_set_hash"] is None
            else str(row["prepared_tool_set_hash"])
        ),
        pin_token_hash=(
            None
            if row["pin_token_hash"] is None
            else str(row["pin_token_hash"])
        ),
        status=str(row["status"]),
        created_at=float(row["created_at"]),
        ready_at=(
            None if row["ready_at"] is None else float(row["ready_at"])
        ),
        retired_at=(
            None if row["retired_at"] is None else float(row["retired_at"])
        ),
    )


def _snapshot_release_receipt_from_row(
    row: Mapping[str, Any],
) -> CapabilitySnapshotLeaseReleaseReceipt:
    return CapabilitySnapshotLeaseReleaseReceipt(
        release_receipt_id=str(row["release_receipt_id"]),
        release_receipt_hash=str(row["release_receipt_hash"]),
        lease_intent_id=str(row["lease_intent_id"]),
        run_id=str(row["run_id"]),
        snapshot_ref=str(row["snapshot_ref"]),
        release_reason=str(row["release_reason"]),
        owner_terminal_or_transition_ref=str(
            row["owner_terminal_or_transition_ref"]
        ),
        owner_terminal_or_transition_hash=str(
            row["owner_terminal_or_transition_hash"]
        ),
        last_active_member_for_snapshot=bool(
            row["last_active_member_for_snapshot"]
        ),
        cleanup_ticket_ref=str(row["cleanup_ticket_ref"]),
        cleanup_ticket_hash=str(row["cleanup_ticket_hash"]),
        cleanup_status=str(row["cleanup_status"]),
        status="released",
        created_at=float(row["created_at"]),
        cleaned_at=(
            None if row["cleaned_at"] is None else float(row["cleaned_at"])
        ),
        last_error=(
            None if row["last_error"] is None else str(row["last_error"])
        ),
    )


def _version_storage_from_row(
    row: Mapping[str, Any],
) -> CapabilityVersionStorage:
    return CapabilityVersionStorage(
        pack_id=str(row["pack_id"]),
        version=str(row["version"]),
        manifest_hash=str(row["manifest_hash"]),
        storage_key=str(row["storage_key"]),
        pack_storage_schema=str(row["pack_storage_schema"]),
        pack_root_hash=str(row["pack_root_hash"]),
        environment_storage_schema=str(row["environment_storage_schema"]),
        environment_root_hash=str(row["environment_root_hash"]),
        archive_hash=str(row["archive_hash"]),
        created_at=float(row["created_at"]),
    )


def _runtime_prepare_intent_from_row(
    row: Mapping[str, Any],
) -> CapabilityRuntimePrepareIntent:
    return CapabilityRuntimePrepareIntent(
        operation_id=str(row["operation_id"]),
        runtime_instance_id=str(row["runtime_instance_id"]),
        entry_id=str(row["entry_id"]),
        ordinal=int(row["ordinal"]),
        runtime_kind=str(row["runtime_kind"]),
        status=str(row["status"]),
        runtime_set_hash=str(row["runtime_set_hash"]),
        authorization_hash=(
            None
            if row["authorization_hash"] is None
            else str(row["authorization_hash"])
        ),
        launch_revocation_epoch=int(row["launch_revocation_epoch"]),
        adapter_fingerprint=str(row["adapter_fingerprint"]),
        argv_hash=str(row["argv_hash"]),
        env_scope_hash=str(row["env_scope_hash"]),
        workdir=str(row["workdir"]),
        job_identity=(
            None if row["job_identity"] is None else str(row["job_identity"])
        ),
        pid=None if row["pid"] is None else int(row["pid"]),
        process_create_time=(
            None
            if row["process_create_time"] is None
            else float(row["process_create_time"])
        ),
        session_identity=(
            None
            if row["session_identity"] is None
            else str(row["session_identity"])
        ),
        start_outcome=(
            None if row["start_outcome"] is None else str(row["start_outcome"])
        ),
        start_ack_at=(
            None if row["start_ack_at"] is None else float(row["start_ack_at"])
        ),
        health_outcome_hash=(
            None
            if row["health_outcome_hash"] is None
            else str(row["health_outcome_hash"])
        ),
        started_at=(
            None if row["started_at"] is None else float(row["started_at"])
        ),
        updated_at=float(row["updated_at"]),
        last_error=None if row["last_error"] is None else str(row["last_error"]),
    )


def _runtime_set_from_rows(
    row: Mapping[str, Any],
    instances: Sequence[Mapping[str, Any]],
) -> CapabilityRuntimeSetRecord:
    return CapabilityRuntimeSetRecord(
        operation_id=str(row["operation_id"]),
        purpose=str(row["purpose"]),
        lifecycle_action=(
            None
            if row["lifecycle_action"] is None
            else str(row["lifecycle_action"])
        ),
        owner_activation_id=(
            None
            if row["owner_activation_id"] is None
            else str(row["owner_activation_id"])
        ),
        lease_activation_id=(
            None
            if row["lease_activation_id"] is None
            else str(row["lease_activation_id"])
        ),
        target_pack_id=(
            None if row["target_pack_id"] is None else str(row["target_pack_id"])
        ),
        target_version=(
            None if row["target_version"] is None else str(row["target_version"])
        ),
        owner_binding_set_stamp=(
            None
            if row["owner_binding_set_stamp"] is None
            else str(row["owner_binding_set_stamp"])
        ),
        run_catalog_content_stamp=(
            None
            if row["run_catalog_content_stamp"] is None
            else str(row["run_catalog_content_stamp"])
        ),
        target_package_hash=(
            None
            if row["target_package_hash"] is None
            else str(row["target_package_hash"])
        ),
        target_manifest_hash=(
            None
            if row["target_manifest_hash"] is None
            else str(row["target_manifest_hash"])
        ),
        runtime_set_hash=str(row["runtime_set_hash"]),
        expected_instance_count=int(row["expected_instance_count"]),
        authorization_hash=(
            None
            if row["authorization_hash"] is None
            else str(row["authorization_hash"])
        ),
        launch_revocation_epoch=int(row["launch_revocation_epoch"]),
        status=str(row["status"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        last_error=None if row["last_error"] is None else str(row["last_error"]),
        instances=tuple(
            _runtime_prepare_intent_from_row(item) for item in instances
        ),
    )


def _version_from_row(row: Mapping[str, Any]) -> CapabilityVersionRecord:
    fingerprints = _json_array(str(row["expected_tool_fingerprints_json"]))
    return CapabilityVersionRecord(
        descriptor=_descriptor_from_json(str(row["descriptor_json"])),
        install_path=Path(str(row["install_path"])),
        validation_status=str(row["validation_status"]),
        expected_tool_fingerprints=tuple(str(item) for item in fingerprints),
        parent_version=(
            None if row["parent_version"] is None else str(row["parent_version"])
        ),
        parent_manifest_hash=(
            None
            if row["parent_manifest_hash"] is None
            else str(row["parent_manifest_hash"])
        ),
        derived_from_receipt_ref=(
            None
            if row["derived_from_receipt_ref"] is None
            else str(row["derived_from_receipt_ref"])
        ),
        created_at=float(row["created_at"]),
    )


def _operation_identity_request_json(request: Mapping[str, JsonValue]) -> str:
    """Canonical request JSON as used for operation-identity comparison.

    WBUI-DEF-S08-02: builtin (first-party) packs ship inside the app install
    directory, so their `source.uri` is an absolute path that changes whenever
    the app runs from a different location (dev worktree vs installed bundle,
    moved .app, user-data dir carried to another machine).  The idempotency
    key is a content hash (id+version+manifest hash) and deliberately ignores
    the path — but the stored request used to participate verbatim, so the
    same logical install replayed from a new install path raised
    operation_idempotency_conflict inside lifespan and the backend never
    bound its port.  Align the two: for builtin sources the uri is a runtime
    resolution detail, not identity.  Non-builtin sources (git, archives…)
    keep the uri — there it IS the identity of what was installed.
    """
    normalized = dict(request)
    source = normalized.get("source")
    if isinstance(source, Mapping) and source.get("type") == "builtin":
        normalized["source"] = {**source, "uri": None}
    return canonical_json(normalized)


def _operation_from_row(row: Mapping[str, Any]) -> CapabilityOperationRecord:
    request = _json_object(str(row["request_json"]))
    if request is None:  # pragma: no cover - NOT NULL in schema
        raise CapabilityStoreError("corrupt_operation", "request cannot be null")
    return CapabilityOperationRecord(
        operation_id=str(row["operation_id"]),
        idempotency_key=str(row["idempotency_key"]),
        root_run_id=None if row["root_run_id"] is None else str(row["root_run_id"]),
        kind=str(row["kind"]),
        pack_id=None if row["pack_id"] is None else str(row["pack_id"]),
        requested_scope=(
            None if row["requested_scope"] is None else str(row["requested_scope"])
        ),
        requested_scope_key=(
            None
            if row["requested_scope_key"] is None
            else str(row["requested_scope_key"])
        ),
        phase=str(row["phase"]),
        status=str(row["status"]),
        request=request,
        error=_json_object(
            None if row["error_json"] is None else str(row["error_json"])
        ),
        started_at=float(row["started_at"]),
        updated_at=float(row["updated_at"]),
        ended_at=None if row["ended_at"] is None else float(row["ended_at"]),
    )


def _failure_receipt_from_row(
    row: Mapping[str, Any],
) -> CapabilityFailureReceipt:
    canonical_args = _json_object(str(row["canonical_args_json"]))
    if canonical_args is None:  # pragma: no cover - NOT NULL in schema
        raise CapabilityStoreError(
            "corrupt_failure_receipt", "canonical args cannot be null"
        )
    return CapabilityFailureReceipt(
        receipt_ref=str(row["receipt_ref"]),
        root_run_id=str(row["root_run_id"]),
        run_id=str(row["run_id"]),
        attempt_id=str(row["attempt_id"]),
        failure_report_ref=str(row["failure_report_ref"]),
        provider_call_id=str(row["provider_call_id"]),
        effect_id=str(row["effect_id"]),
        capability_id=str(row["capability_id"]),
        pack_version=str(row["pack_version"]),
        manifest_hash=str(row["manifest_hash"]),
        tool_name=str(row["tool_name"]),
        tool_spec_fingerprint=str(row["tool_spec_fingerprint"]),
        binding_scope=str(row["binding_scope"]),
        scope_key=str(row["scope_key"]),
        canonical_args=canonical_args,
        args_hash=str(row["args_hash"]),
        error_code=str(row["error_code"]),
        error_fingerprint=str(row["error_fingerprint"]),
        evidence_refs=tuple(
            str(item)
            for item in _json_array(str(row["evidence_refs_json"]))
        ),
    )


def _repair_attempt_from_row(
    row: Mapping[str, Any],
) -> CapabilityRepairAttempt:
    return CapabilityRepairAttempt(
        root_run_id=str(row["root_run_id"]),
        error_fingerprint=str(row["error_fingerprint"]),
        attempt_no=int(row["attempt_no"]),
        failure_receipt_ref=str(row["failure_receipt_ref"]),
        control_call_id=str(row["control_call_id"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        child_run_id=(
            None if row["child_run_id"] is None else str(row["child_run_id"])
        ),
        operation_id=(
            None if row["operation_id"] is None else str(row["operation_id"])
        ),
    )


def _publish_intent_from_row(row: Mapping[str, Any]) -> CapabilityPublishIntent:
    old_specs = _json_array(str(row["old_specs_json"]))
    new_specs = _json_array(str(row["new_specs_json"]))
    old_binding = _json_object(
        None if row["old_binding_json"] is None else str(row["old_binding_json"])
    )
    new_binding = _json_object(str(row["new_binding_json"]))
    if new_binding is None:  # pragma: no cover - NOT NULL
        raise CapabilityStoreError("corrupt_publish_intent", "new binding is missing")
    return CapabilityPublishIntent(
        intent_id=str(row["intent_id"]),
        operation_id=str(row["operation_id"]),
        expected_registry_revision=int(row["expected_registry_revision"]),
        old_specs=tuple(dict(item) for item in old_specs),
        new_specs=tuple(dict(item) for item in new_specs),
        old_binding=old_binding,
        new_binding=new_binding,
        phase=str(row["phase"]),
        status=str(row["status"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _skill_install_intent_from_row(
    row: Mapping[str, Any],
) -> CapabilitySkillInstallIntent:
    source = _json_object(str(row["source_json"]))
    if source is None:
        raise CapabilityStoreError("corrupt_skill_install_intent", "source is null")
    return CapabilitySkillInstallIntent(
        intent_id=str(row["intent_id"]), effect_id=str(row["effect_id"]),
        call_id=str(row["call_id"]), root_run_id=str(row["root_run_id"]),
        run_id=str(row["run_id"]), channel=str(row["channel"]),
        project_scope_key=str(row["project_scope_key"]),
        principal_id=str(row["principal_id"]), source=source,
        exact_commit=str(row["exact_commit"]), archive_hash=str(row["archive_hash"]),
        raw_tree_hash=str(row["raw_tree_hash"]),
        member_set_stamp=str(row["member_set_stamp"]),
        permission_set_hash=str(row["permission_set_hash"]),
        confirmation_nonce=str(row["confirmation_nonce"]),
        confirmation_version=int(row["confirmation_version"]),
        expires_at=float(row["expires_at"]), status=str(row["status"]),
        state_version=int(row["state_version"]),
        settlement_ref=None if row["settlement_ref"] is None else str(row["settlement_ref"]),
        cleanup_ref=None if row["cleanup_ref"] is None else str(row["cleanup_ref"]),
        verification_ref=None if row["verification_ref"] is None else str(row["verification_ref"]),
        error=_json_object(None if row["error_json"] is None else str(row["error_json"])),
        created_at=float(row["created_at"]), updated_at=float(row["updated_at"]),
        verification_attempt_generation=int(row["verification_attempt_generation"])
            if "verification_attempt_generation" in row.keys() else 0,
        current_verification_attempt_id=(
            None if "current_verification_attempt_id" not in row.keys()
            or row["current_verification_attempt_id"] is None
            else str(row["current_verification_attempt_id"])
        ),
        migrated_verification_provenance=(
            None if "migrated_verification_provenance" not in row.keys()
            or row["migrated_verification_provenance"] is None
            else str(row["migrated_verification_provenance"])
        ),
    )


def _skill_install_member_from_row(row: Mapping[str, Any]) -> CapabilitySkillInstallMember:
    member = _json_object(str(row["member_json"]))
    if member is None:
        raise CapabilityStoreError("corrupt_skill_install_member", "member is null")
    return CapabilitySkillInstallMember(
        intent_id=str(row["intent_id"]), ordinal=int(row["ordinal"]),
        normalized_name=str(row["normalized_name"]), pack_id=str(row["pack_id"]),
        version=str(row["version"]), manifest_hash=str(row["manifest_hash"]),
        content_hash=str(row["content_hash"]), source_digest=str(row["source_digest"]),
        member=member,
    )


def _operation_member_from_row(row: Mapping[str, Any]) -> CapabilityOperationMember:
    return CapabilityOperationMember(
        operation_id=str(row["operation_id"]), ordinal=int(row["ordinal"]),
        normalized_name=str(row["normalized_name"]), pack_id=str(row["pack_id"]),
        version=str(row["version"]), manifest_hash=str(row["manifest_hash"]),
        content_hash=str(row["content_hash"]), source_digest=str(row["source_digest"]),
        committed_version=None if row["committed_version"] is None else str(row["committed_version"]),
        committed_manifest_hash=None if row["committed_manifest_hash"] is None else str(row["committed_manifest_hash"]),
        committed_set_stamp=None if row["committed_set_stamp"] is None else str(row["committed_set_stamp"]),
    )


def _runtime_lease_from_row(row: Mapping[str, Any]) -> CapabilityRuntimeLease:
    return CapabilityRuntimeLease(
        runtime_lease_id=str(row["lease_id"]),
        pack_id=str(row["pack_id"]),
        version=str(row["version"]),
        manifest_hash=str(row["manifest_hash"]),
        server_id=str(row["server_id"]),
        pid=None if row["pid"] is None else int(row["pid"]),
        run_id=None if row["run_id"] is None else str(row["run_id"]),
        session_generation=int(row["session_generation"]),
        state=str(row["state"]),
        heartbeat_at=float(row["heartbeat_at"]),
        created_at=float(row["created_at"]),
    )


def _runtime_call_lease_from_row(
    row: Mapping[str, Any],
) -> CapabilityRuntimeCallLease:
    return CapabilityRuntimeCallLease(
        call_lease_id=str(row["call_lease_id"]),
        runtime_lease_id=str(row["runtime_lease_id"]),
        effect_id=str(row["effect_id"]),
        session_generation=int(row["session_generation"]),
        state=str(row["state"]),
        started_at=float(row["started_at"]),
        ended_at=None if row["ended_at"] is None else float(row["ended_at"]),
    )


def _legacy_authorization_import_from_row(
    row: Mapping[str, Any],
) -> LegacyAuthorizationImportRecord:
    return LegacyAuthorizationImportRecord(
        source_key=str(row["source_key"]),
        outcome=str(row["outcome"]),  # type: ignore[arg-type]
        source_fingerprint=(
            None
            if row["source_fingerprint"] is None
            else str(row["source_fingerprint"])
        ),
        imported_mode=(
            None if row["imported_mode"] is None else str(row["imported_mode"])
        ),  # type: ignore[arg-type]
        error_code=None if row["error_code"] is None else str(row["error_code"]),
        imported_at=float(row["imported_at"]),
    )


def _operation_receipt_from_row(
    row: Mapping[str, Any],
) -> CapabilityOperationReceipt:
    value = json.loads(str(row["receipt_json"]))
    if not isinstance(value, dict):
        raise CapabilityStoreError(
            "corrupt_operation_receipt", "operation receipt is not an object"
        )
    return CapabilityOperationReceipt.from_dict(value)


def _refresh_intent_from_row(
    row: Mapping[str, Any],
) -> CapabilityRefreshIntent:
    value = json.loads(str(row["intent_json"]))
    if not isinstance(value, dict):
        raise CapabilityStoreError(
            "corrupt_refresh_intent", "refresh intent is not an object"
        )
    value["status"] = str(row["status"])
    value["commit_hash"] = (
        None if row["commit_hash"] is None else str(row["commit_hash"])
    )
    if row["commit_json"] is None:
        value["commit"] = None
    else:
        commit = json.loads(str(row["commit_json"]))
        if not isinstance(commit, dict):
            raise CapabilityStoreError(
                "corrupt_refresh_intent", "refresh commit is not an object"
            )
        value["commit"] = commit
    return refresh_intent_from_dict(value)


def _default_owner_key(scope: str) -> str:
    return "builtin" if scope == "builtin" else LEGACY_LOCAL_OWNER_KEY


async def _capability_table_columns(
    db: aiosqlite.Connection, table: str
) -> set[str]:
    rows = await (await db.execute(f"PRAGMA table_info({table})")).fetchall()
    return {str(row[1]) for row in rows}


async def _install_capability_v2_tables(db: aiosqlite.Connection) -> None:
    for statement in CAPABILITY_SCHEMA_V2_STATEMENTS:
        await db.execute(statement)


async def _fetch_mappings(
    cursor: aiosqlite.Cursor,
) -> list[Mapping[str, Any]]:
    rows = await cursor.fetchall()
    if not rows or hasattr(rows[0], "keys"):
        return list(rows)
    columns = tuple(str(item[0]) for item in cursor.description or ())
    return [dict(zip(columns, row, strict=True)) for row in rows]


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
        grouped.setdefault(
            (str(row["snapshot_ref"]), str(row["run_id"])), []
        ).append(row)
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
                    "tool_spec_fingerprints": json.loads(
                        str(row["tool_spec_fingerprints_json"])
                    ),
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
        released_at = (
            max(float(row["released_at"]) for row in rows)
            if all_released
            else None
        )
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
                    (
                        None
                        if row["released_at"] is None
                        else float(row["released_at"])
                    ),
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


async def _owner_bindings_tx(
    db: aiosqlite.Connection, key: OwnerScopeKey
) -> tuple[CapabilityBinding, ...]:
    cursor = await db.execute(
        """SELECT * FROM capability_bindings
           WHERE owner_key=? AND scope=? AND scope_key=?
           ORDER BY pack_id,binding_id""",
        (key.owner_key, key.scope, key.scope_key),
    )
    rows = await cursor.fetchall()
    if rows and not hasattr(rows[0], "keys"):
        columns = tuple(str(item[0]) for item in cursor.description or ())
        rows = [dict(zip(columns, row, strict=True)) for row in rows]
    return tuple(_binding_from_row(row) for row in rows)


async def _owner_binding_stamp_tx(
    db: aiosqlite.Connection, key: OwnerScopeKey
) -> OwnerBindingSetStamp:
    return OwnerBindingSetStamp(key, await _owner_bindings_tx(db, key))


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
    if version == CAPABILITY_SCHEMA_VERSION:
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
        (await (await db.execute("SELECT COUNT(*) FROM capability_bindings")).fetchone())[
            0
        ]
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
        (
            await (
                await db.execute("SELECT COUNT(*) FROM capability_bindings_v2")
            ).fetchone()
        )[0]
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
        (CAPABILITY_SCHEMA_VERSION, now),
    )


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
        await db.commit()
    return db_path


class CapabilityStoreTx:
    """Capability DML bound to one caller-owned SQLite transaction."""

    def __init__(self, store: "CapabilityStore", db: aiosqlite.Connection) -> None:
        self._store = store
        self._db = db

    async def put_publish_intent_members_in_transaction(
        self,
        publish_intent_id: str,
        members: Sequence[CapabilityPublishIntentMember],
    ) -> tuple[CapabilityPublishIntentMember, ...]:
        """Freeze batch members in the caller-owned publish-intent transaction."""

        if tuple(member.ordinal for member in members) != tuple(range(len(members))):
            raise CapabilityStoreError(
                "invalid_publish_intent_members",
                "member ordinals must be dense and ordered",
            )
        existing = await (await self._db.execute(
            "SELECT * FROM capability_publish_intent_members "
            "WHERE publish_intent_id=? ORDER BY ordinal",
            (publish_intent_id,),
        )).fetchall()
        if existing:
            current = tuple(CapabilityPublishIntentMember(
                publish_intent_id=str(row["publish_intent_id"]),
                operation_id=str(row["operation_id"]), ordinal=int(row["ordinal"]),
                old_binding=_json_object(
                    None if row["old_binding_json"] is None else str(row["old_binding_json"])
                ),
                new_binding=_json_object(str(row["new_binding_json"])) or {},
            ) for row in existing)
            if current != tuple(members):
                raise CapabilityStoreConflict(
                    "publish_intent_members_conflict", "publish members changed"
                )
            return current
        for member in members:
            if member.publish_intent_id != publish_intent_id:
                raise CapabilityStoreError(
                    "invalid_publish_intent_members", "publish intent differs"
                )
            await self._db.execute(
                "INSERT INTO capability_publish_intent_members VALUES(?,?,?,?,?)",
                (member.publish_intent_id, member.operation_id, member.ordinal,
                 None if member.old_binding is None else canonical_json(dict(member.old_binding)),
                 canonical_json(dict(member.new_binding))),
            )
        return tuple(members)

    async def state(self) -> CapabilityStoreState:
        return await self._store._state_tx(self._db)

    async def record_version(self, record: CapabilityVersionRecord) -> CapabilityVersionRecord:
        return await self._store._record_version_tx(self._db, record)

    async def put_version_storage(
        self, record: CapabilityVersionStorage
    ) -> CapabilityVersionStorage:
        return await self._store._put_version_storage_tx(self._db, record)

    async def prepare_runtime_set(
        self, **kwargs: Any
    ) -> CapabilityRuntimeSetRecord:
        return await self._store._prepare_runtime_set_tx(self._db, **kwargs)

    async def mark_runtime_instance_ready(
        self, operation_id: str, runtime_instance_id: str, **kwargs: Any
    ) -> CapabilityRuntimePrepareIntent:
        return await self._store._mark_runtime_instance_ready_tx(
            self._db, operation_id, runtime_instance_id, **kwargs
        )

    async def mark_runtime_set_ready(
        self, operation_id: str
    ) -> CapabilityRuntimeSetRecord:
        return await self._store._mark_runtime_set_ready_tx(
            self._db, operation_id
        )

    async def retire_runtime_set(
        self, operation_id: str
    ) -> CapabilityRuntimeSetRecord:
        return await self._store._retire_runtime_set_tx(
            self._db, operation_id
        )

    async def set_binding(
        self,
        *,
        scope: str,
        scope_key: str,
        pack_id: str,
        version: str,
        manifest_hash: str,
        expected_generation: int,
        enabled: bool = True,
        owner_key: str | None = None,
        management_policy: str | None = None,
    ) -> CapabilityBinding:
        return await self._store._set_binding_tx(
            self._db,
            scope=scope,
            scope_key=scope_key,
            pack_id=pack_id,
            version=version,
            manifest_hash=manifest_hash,
            expected_generation=expected_generation,
            enabled=enabled,
            owner_key=owner_key,
            management_policy=management_policy,
        )

    async def create_operation(self, **kwargs: Any) -> CapabilityOperationRecord:
        return await self._store._create_operation_tx(self._db, **kwargs)

    async def create_skill_install_intent(
        self, intent: CapabilitySkillInstallIntent,
        members: Sequence[CapabilitySkillInstallMember],
    ) -> CapabilitySkillInstallIntent:
        return await self._store._create_skill_install_intent_tx(
            self._db, intent, members
        )

    async def cas_skill_install_intent(self, intent_id: str, **kwargs: Any) -> CapabilitySkillInstallIntent:
        return await self._store._cas_skill_install_intent_tx(
            self._db, intent_id, **kwargs
        )

    async def handoff_skill_install_intent(self, intent_id: str, **kwargs: Any) -> CapabilitySkillInstallHandoff:
        return await self._store._handoff_skill_install_intent_tx(
            self._db, intent_id, **kwargs
        )

    async def bind_operation_pack_id(
        self, operation_id: str, pack_id: str
    ) -> CapabilityOperationRecord:
        return await self._store._bind_operation_pack_id_tx(
            self._db, operation_id, pack_id
        )

    async def record_phase_intent(
        self, operation_id: str, phase: str
    ) -> CapabilityOperationRecord:
        return await self._store._record_phase_intent_tx(
            self._db, operation_id, phase
        )

    async def commit_phase(
        self,
        operation_id: str,
        phase: str,
        *,
        evidence: Mapping[str, JsonValue] | None = None,
    ) -> CapabilityOperationRecord:
        return await self._store._commit_phase_tx(
            self._db, operation_id, phase, evidence=evidence
        )

    async def create_publish_intent(self, **kwargs: Any) -> CapabilityPublishIntent:
        return await self._store._create_publish_intent_tx(self._db, **kwargs)

    async def advance_publish_intent(
        self, intent_id: str, *, phase: str, status: str = "pending"
    ) -> CapabilityPublishIntent:
        return await self._store._advance_publish_intent_tx(
            self._db, intent_id, phase=phase, status=status
        )

    async def acquire_snapshot_lease(self, **kwargs: Any) -> None:
        await self._store._acquire_snapshot_lease_tx(self._db, **kwargs)

    async def release_snapshot_lease(
        self, snapshot_ref: str, run_id: str
    ) -> int:
        return await self._store._release_snapshot_lease_tx(
            self._db, snapshot_ref, run_id
        )

    async def clone_snapshot_lease(
        self,
        *,
        snapshot_ref: str,
        source_run_id: str,
        target_run_id: str,
        root_run_id: str,
    ) -> tuple[str, ...]:
        return await self._store._clone_snapshot_lease_tx(
            self._db,
            snapshot_ref=snapshot_ref,
            source_run_id=source_run_id,
            target_run_id=target_run_id,
            root_run_id=root_run_id,
        )

    async def prepare_run_catalog_projection_in_tx(
        self, **kwargs: Any
    ) -> PreparedRunCatalogProjection:
        return await self._store.prepare_run_catalog_projection_in_tx(
            self._db, **kwargs
        )

    async def adopt_snapshot_lease_intent_in_tx(
        self, lease_intent_id: str, **kwargs: Any
    ) -> SnapshotLeaseAdoptionReceipt:
        return await self._store.adopt_snapshot_lease_intent_in_tx(
            self._db, lease_intent_id, **kwargs
        )

    async def release_snapshot_lease_intent_in_tx(
        self, lease_intent_id: str, **kwargs: Any
    ) -> CapabilitySnapshotLeaseReleaseReceipt:
        return await self._store.release_snapshot_lease_intent_in_tx(
            self._db, lease_intent_id, **kwargs
        )

    async def put_operation_receipt(
        self, receipt: CapabilityOperationReceipt
    ) -> CapabilityOperationReceipt:
        return await self._store._put_operation_receipt_tx(self._db, receipt)

    async def stage_refresh_intent(
        self, intent: CapabilityRefreshIntent
    ) -> CapabilityRefreshIntent:
        return await self._store._stage_refresh_intent_tx(self._db, intent)

    async def commit_refresh_intent(
        self, commit: CapabilityRefreshCommit
    ) -> CapabilityRefreshIntent:
        return await self._store._commit_refresh_intent_tx(self._db, commit)

    async def fail_refresh_intent(
        self,
        intent_id: str,
        *,
        error: Mapping[str, JsonValue],
    ) -> CapabilityRefreshIntent:
        return await self._store._fail_refresh_intent_tx(
            self._db, intent_id, error=error
        )

    async def create_runtime_lease(
        self, **kwargs: Any
    ) -> CapabilityRuntimeLease:
        return await self._store._create_runtime_lease_tx(self._db, **kwargs)

    async def transition_runtime_lease(
        self, runtime_lease_id: str, **kwargs: Any
    ) -> CapabilityRuntimeLease:
        return await self._store._transition_runtime_lease_tx(
            self._db, runtime_lease_id, **kwargs
        )

    async def reconnect_runtime_lease(
        self, runtime_lease_id: str, **kwargs: Any
    ) -> CapabilityRuntimeLease:
        return await self._store._reconnect_runtime_lease_tx(
            self._db, runtime_lease_id, **kwargs
        )

    async def claim_runtime_call(
        self, **kwargs: Any
    ) -> CapabilityRuntimeCallLease:
        return await self._store._claim_runtime_call_tx(self._db, **kwargs)

    async def mark_runtime_call_running(
        self, call_lease_id: str
    ) -> CapabilityRuntimeCallLease:
        return await self._store._mark_runtime_call_running_tx(
            self._db, call_lease_id
        )

    async def settle_runtime_call(
        self, call_lease_id: str
    ) -> CapabilityRuntimeCallLease:
        return await self._store._settle_runtime_call_tx(
            self._db, call_lease_id
        )

    async def put_failure_receipt(
        self, receipt: CapabilityFailureReceipt
    ) -> CapabilityFailureReceipt:
        return await self._store._put_failure_receipt_tx(self._db, receipt)

    async def claim_repair_attempt(
        self,
        *,
        root_run_id: str,
        failure_receipt_ref: str,
        control_call_id: str,
    ) -> CapabilityRepairAttempt:
        return await self._store._claim_repair_attempt_tx(
            self._db,
            root_run_id=root_run_id,
            failure_receipt_ref=failure_receipt_ref,
            control_call_id=control_call_id,
        )

    async def _create_skill_install_intent_tx(
        self, db: aiosqlite.Connection, intent: CapabilitySkillInstallIntent,
        members: Sequence[CapabilitySkillInstallMember],
    ) -> CapabilitySkillInstallIntent:
        if intent.state_version != 1 or intent.status != "staging":
            raise CapabilityStoreError("invalid_skill_install_intent", "new intent must be staging at version 1")
        if tuple(member.ordinal for member in members) != tuple(range(len(members))):
            raise CapabilityStoreError("invalid_skill_install_members", "member ordinals must be dense and ordered")
        if any(member.intent_id != intent.intent_id for member in members):
            raise CapabilityStoreError("invalid_skill_install_members", "member intent differs")
        existing = await (await db.execute(
            "SELECT * FROM capability_skill_install_intents WHERE intent_id=? OR (effect_id=? AND call_id=?)",
            (intent.intent_id, intent.effect_id, intent.call_id),
        )).fetchone()
        if existing is not None:
            current = _skill_install_intent_from_row(existing)
            current_members = await self._skill_install_members_tx(db, current.intent_id)
            if current != intent or current_members != tuple(members):
                raise CapabilityStoreConflict("skill_install_intent_conflict", "install intent identity changed")
            return current
        await db.execute(
            """INSERT INTO capability_skill_install_intents(
                intent_id,effect_id,call_id,root_run_id,run_id,channel,
                project_scope_key,principal_id,source_json,exact_commit,archive_hash,
                raw_tree_hash,member_set_stamp,permission_set_hash,confirmation_nonce,
                confirmation_version,expires_at,status,state_version,settlement_ref,
                cleanup_ref,verification_ref,error_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (intent.intent_id,intent.effect_id,intent.call_id,intent.root_run_id,
             intent.run_id,intent.channel,intent.project_scope_key,intent.principal_id,
             canonical_json(dict(intent.source)),intent.exact_commit,intent.archive_hash,
             intent.raw_tree_hash,intent.member_set_stamp,intent.permission_set_hash,
             intent.confirmation_nonce,intent.confirmation_version,intent.expires_at,
             intent.status,intent.state_version,intent.settlement_ref,intent.cleanup_ref,
             intent.verification_ref,None if intent.error is None else canonical_json(dict(intent.error)),
             intent.created_at,intent.updated_at),
        )
        for member in members:
            await db.execute(
                """INSERT INTO capability_skill_install_members(
                    intent_id,ordinal,normalized_name,pack_id,version,manifest_hash,
                    content_hash,source_digest,member_json) VALUES(?,?,?,?,?,?,?,?,?)""",
                (member.intent_id,member.ordinal,member.normalized_name,member.pack_id,
                 member.version,member.manifest_hash,member.content_hash,
                 member.source_digest,canonical_json(dict(member.member))),
            )
        return intent

    async def create_skill_install_intent(
        self, intent: CapabilitySkillInstallIntent,
        members: Sequence[CapabilitySkillInstallMember],
    ) -> CapabilitySkillInstallIntent:
        async with self.write_transaction() as db:
            return await self._create_skill_install_intent_tx(db, intent, members)

    async def get_skill_install_intent(self, intent_id: str) -> CapabilitySkillInstallIntent | None:
        async with self.read_connection() as db:
            row = await (await db.execute(
                "SELECT * FROM capability_skill_install_intents WHERE intent_id=?", (intent_id,)
            )).fetchone()
            return None if row is None else _skill_install_intent_from_row(row)

    async def get_skill_install_intent_for_effect(
        self, effect_id: str, call_id: str
    ) -> CapabilitySkillInstallIntent | None:
        async with self.read_connection() as db:
            row = await (await db.execute(
                "SELECT * FROM capability_skill_install_intents "
                "WHERE effect_id=? AND call_id=?",
                (effect_id, call_id),
            )).fetchone()
            return None if row is None else _skill_install_intent_from_row(row)

    async def _skill_install_members_tx(self, db: aiosqlite.Connection, intent_id: str) -> tuple[CapabilitySkillInstallMember, ...]:
        rows = await (await db.execute(
            "SELECT * FROM capability_skill_install_members WHERE intent_id=? ORDER BY ordinal", (intent_id,)
        )).fetchall()
        return tuple(_skill_install_member_from_row(row) for row in rows)

    async def skill_install_members(self, intent_id: str) -> tuple[CapabilitySkillInstallMember, ...]:
        async with self.read_connection() as db:
            return await self._skill_install_members_tx(db, intent_id)

    async def pending_skill_install_intents(self, *, now: float | None = None) -> tuple[CapabilitySkillInstallIntent, ...]:
        async with self.read_connection() as db:
            params: tuple[Any, ...] = () if now is None else (now,)
            expiry = "" if now is None else " AND expires_at<=?"
            rows = await (await db.execute(
                "SELECT * FROM capability_skill_install_intents WHERE status NOT IN "
                "('succeeded','stage_failed','denied','expired')" + expiry +
                " ORDER BY expires_at,intent_id", params,
            )).fetchall()
            return tuple(_skill_install_intent_from_row(row) for row in rows)

    async def _cas_skill_install_intent_tx(
        self, db: aiosqlite.Connection, intent_id: str, *, expected_state_version: int,
        status: str, settlement_ref: str | None = None, cleanup_ref: str | None = None,
        verification_ref: str | None = None, error: Mapping[str, JsonValue] | None = None,
    ) -> CapabilitySkillInstallIntent:
        row = await (await db.execute(
            "SELECT * FROM capability_skill_install_intents WHERE intent_id=?",
            (intent_id,),
        )).fetchone()
        if row is None:
            raise CapabilityStoreError("skill_install_intent_not_found", intent_id)
        current = _skill_install_intent_from_row(row)
        transitions = {
            "staging": {"awaiting_confirmation", "stage_failed_cleanup_pending"},
            "awaiting_confirmation": {
                "publishing", "denied_cleanup_pending", "expired_cleanup_pending"
            },
            "stage_failed_cleanup_pending": {"stage_failed"},
            "denied_cleanup_pending": {"denied"},
            "expired_cleanup_pending": {"expired"},
            "publishing": {"published_pending_runtime_verification", "unknown"},
            "published_pending_runtime_verification": {"succeeded", "unknown"},
        }
        if status not in transitions.get(current.status, set()):
            raise CapabilityStoreConflict(
                "skill_install_intent_transition_conflict",
                f"cannot transition install intent {current.status} to {status}",
            )
        cursor = await db.execute(
            """UPDATE capability_skill_install_intents SET status=?,state_version=state_version+1,
               settlement_ref=COALESCE(?,settlement_ref),cleanup_ref=COALESCE(?,cleanup_ref),
               verification_ref=COALESCE(?,verification_ref),error_json=?,updated_at=?
               WHERE intent_id=? AND state_version=?""",
            (status,settlement_ref,cleanup_ref,verification_ref,
             None if error is None else canonical_json(dict(error)),self._clock(),intent_id,expected_state_version),
        )
        if cursor.rowcount != 1:
            raise CapabilityStoreConflict("skill_install_intent_cas_conflict", "install intent state changed")
        row = await (await db.execute("SELECT * FROM capability_skill_install_intents WHERE intent_id=?", (intent_id,))).fetchone()
        return _skill_install_intent_from_row(row)

    async def cas_skill_install_intent(self, intent_id: str, **kwargs: Any) -> CapabilitySkillInstallIntent:
        async with self.write_transaction() as db:
            return await self._cas_skill_install_intent_tx(db, intent_id, **kwargs)

    async def bind_skill_install_confirmation(
        self,
        intent_id: str,
        *,
        expected_state_version: int,
        confirmation_nonce: str,
        confirmation_version: int,
    ) -> CapabilitySkillInstallIntent:
        """CAS-bind the SDK-owned final confirmation identity before handoff."""

        if not confirmation_nonce or confirmation_version < 0:
            raise CapabilityStoreError(
                "invalid_skill_install_confirmation", "confirmation identity is invalid"
            )
        async with self.write_transaction() as db:
            cursor = await db.execute(
                """UPDATE capability_skill_install_intents
                   SET confirmation_nonce=?,confirmation_version=?,
                       state_version=state_version+1,updated_at=?
                   WHERE intent_id=? AND state_version=?
                     AND status='awaiting_confirmation'""",
                (confirmation_nonce, confirmation_version, self._clock(),
                 intent_id, expected_state_version),
            )
            if cursor.rowcount != 1:
                current = await (await db.execute(
                    "SELECT * FROM capability_skill_install_intents WHERE intent_id=?",
                    (intent_id,),
                )).fetchone()
                if current is not None:
                    value = _skill_install_intent_from_row(current)
                    if (
                        value.confirmation_nonce == confirmation_nonce
                        and value.confirmation_version == confirmation_version
                    ):
                        return value
                raise CapabilityStoreConflict(
                    "skill_install_confirmation_cas_conflict",
                    "install confirmation identity changed",
                )
            row = await (await db.execute(
                "SELECT * FROM capability_skill_install_intents WHERE intent_id=?",
                (intent_id,),
            )).fetchone()
            return _skill_install_intent_from_row(row)

    async def _handoff_skill_install_intent_tx(
        self, db: aiosqlite.Connection, intent_id: str, *, expected_state_version: int,
        operation_id: str, idempotency_key: str, confirmation_receipt_hash: str,
    ) -> CapabilitySkillInstallHandoff:
        existing = await (await db.execute(
            "SELECT * FROM capability_skill_install_handoffs WHERE intent_id=? OR operation_id=?",
            (intent_id, operation_id),
        )).fetchone()
        if existing is not None:
            handoff = CapabilitySkillInstallHandoff(
                intent_id=str(existing["intent_id"]),operation_id=str(existing["operation_id"]),
                confirmation_receipt_hash=str(existing["confirmation_receipt_hash"]),
                member_set_stamp=str(existing["member_set_stamp"]),created_at=float(existing["created_at"]),
            )
            if handoff.intent_id != intent_id or handoff.operation_id != operation_id or handoff.confirmation_receipt_hash != confirmation_receipt_hash:
                raise CapabilityStoreConflict("skill_install_handoff_conflict", "handoff identity changed")
            return handoff
        intent_row = await (await db.execute(
            "SELECT * FROM capability_skill_install_intents WHERE intent_id=?", (intent_id,)
        )).fetchone()
        if intent_row is None:
            raise CapabilityStoreError("skill_install_intent_not_found", intent_id)
        intent = _skill_install_intent_from_row(intent_row)
        if intent.status != "awaiting_confirmation" or intent.state_version != expected_state_version:
            raise CapabilityStoreConflict("skill_install_handoff_cas_conflict", "confirmation is not consumable")
        members = await self._skill_install_members_tx(db, intent_id)
        await self._create_operation_tx(
            db, operation_id=operation_id, idempotency_key=idempotency_key,
            kind="skill_install_batch", request={"install_intent_id": intent_id,
            "member_set_stamp": intent.member_set_stamp}, root_run_id=intent.root_run_id,
            requested_scope=intent.install_scope,
            requested_scope_key=intent.install_scope_key,
        )
        for member in members:
            await db.execute(
                """INSERT INTO capability_operation_members(operation_id,ordinal,normalized_name,
                   pack_id,version,manifest_hash,content_hash,source_digest) VALUES(?,?,?,?,?,?,?,?)""",
                (operation_id,member.ordinal,member.normalized_name,member.pack_id,member.version,
                 member.manifest_hash,member.content_hash,member.source_digest),
            )
        now = self._clock()
        await db.execute(
            "INSERT INTO capability_skill_install_handoffs VALUES(?,?,?,?,?)",
            (intent_id,operation_id,confirmation_receipt_hash,intent.member_set_stamp,now),
        )
        await self._cas_skill_install_intent_tx(db,intent_id,expected_state_version=expected_state_version,
                                                status="publishing",settlement_ref=confirmation_receipt_hash)
        return CapabilitySkillInstallHandoff(intent_id,operation_id,confirmation_receipt_hash,intent.member_set_stamp,now)

    async def handoff_skill_install_intent(self, intent_id: str, **kwargs: Any) -> CapabilitySkillInstallHandoff:
        async with self.write_transaction() as db:
            return await self._handoff_skill_install_intent_tx(db, intent_id, **kwargs)

    async def operation_members(self, operation_id: str) -> tuple[CapabilityOperationMember, ...]:
        async with self.read_connection() as db:
            rows = await (await db.execute(
                "SELECT * FROM capability_operation_members WHERE operation_id=? ORDER BY ordinal", (operation_id,)
            )).fetchall()
            return tuple(_operation_member_from_row(row) for row in rows)

    async def put_publish_intent_members(
        self, publish_intent_id: str,
        members: Sequence[CapabilityPublishIntentMember],
    ) -> tuple[CapabilityPublishIntentMember, ...]:
        if tuple(member.ordinal for member in members) != tuple(range(len(members))):
            raise CapabilityStoreError("invalid_publish_intent_members", "member ordinals must be dense and ordered")
        async with self.write_transaction() as db:
            existing = await (await db.execute(
                "SELECT * FROM capability_publish_intent_members WHERE publish_intent_id=? ORDER BY ordinal",
                (publish_intent_id,),
            )).fetchall()
            if existing:
                current = tuple(CapabilityPublishIntentMember(
                    publish_intent_id=str(row["publish_intent_id"]), operation_id=str(row["operation_id"]),
                    ordinal=int(row["ordinal"]),
                    old_binding=_json_object(None if row["old_binding_json"] is None else str(row["old_binding_json"])),
                    new_binding=_json_object(str(row["new_binding_json"])) or {},
                ) for row in existing)
                if current != tuple(members):
                    raise CapabilityStoreConflict("publish_intent_members_conflict", "publish members changed")
                return current
            for member in members:
                if member.publish_intent_id != publish_intent_id:
                    raise CapabilityStoreError("invalid_publish_intent_members", "publish intent differs")
                await db.execute(
                    "INSERT INTO capability_publish_intent_members VALUES(?,?,?,?,?)",
                    (member.publish_intent_id,member.operation_id,member.ordinal,
                     None if member.old_binding is None else canonical_json(dict(member.old_binding)),
                     canonical_json(dict(member.new_binding))),
                )
            return tuple(members)

    async def publish_intent_members(self, publish_intent_id: str) -> tuple[CapabilityPublishIntentMember, ...]:
        async with self.read_connection() as db:
            rows = await (await db.execute(
                "SELECT * FROM capability_publish_intent_members WHERE publish_intent_id=? ORDER BY ordinal",
                (publish_intent_id,),
            )).fetchall()
            return tuple(CapabilityPublishIntentMember(
                publish_intent_id=str(row["publish_intent_id"]),operation_id=str(row["operation_id"]),
                ordinal=int(row["ordinal"]),
                old_binding=_json_object(None if row["old_binding_json"] is None else str(row["old_binding_json"])),
                new_binding=_json_object(str(row["new_binding_json"])) or {},
            ) for row in rows)

    async def put_operation_evidence(self, evidence: CapabilityOperationEvidence) -> CapabilityOperationEvidence:
        payload = canonical_json(dict(evidence.evidence))
        async with self.write_transaction() as db:
            row = await (await db.execute(
                "SELECT * FROM capability_operation_evidence WHERE idempotency_key=?", (evidence.idempotency_key,)
            )).fetchone()
            if row is not None:
                current = CapabilityOperationEvidence(str(row["operation_id"]),str(row["evidence_kind"]),
                    str(row["idempotency_key"]),_json_object(str(row["evidence_json"])) or {},float(row["created_at"]))
                if current != evidence:
                    raise CapabilityStoreConflict("operation_evidence_conflict", "evidence identity changed")
                return current
            await db.execute("INSERT INTO capability_operation_evidence VALUES(?,?,?,?,?)",
                             (evidence.operation_id,evidence.evidence_kind,evidence.idempotency_key,payload,evidence.created_at))
            return evidence

    async def operation_evidence(self, operation_id: str) -> tuple[CapabilityOperationEvidence, ...]:
        async with self.read_connection() as db:
            rows = await (await db.execute(
                "SELECT * FROM capability_operation_evidence WHERE operation_id=? ORDER BY created_at,evidence_kind,idempotency_key",
                (operation_id,),
            )).fetchall()
            return tuple(CapabilityOperationEvidence(
                operation_id=str(row["operation_id"]), evidence_kind=str(row["evidence_kind"]),
                idempotency_key=str(row["idempotency_key"]),
                evidence=_json_object(str(row["evidence_json"])) or {}, created_at=float(row["created_at"]),
            ) for row in rows)

    async def put_task_grant(self, grant: TaskGrant) -> TaskGrant:
        return await self._store._put_task_grant_tx(self._db, grant)

    async def get_policy_state(self) -> AuthorizationPolicyState:
        return await self._store._get_policy_state_tx(self._db)

    async def compare_and_set_policy_mode(
        self,
        mode: AuthorizationMode,
        *,
        expected_generation: int,
        provenance: AuthorizationPolicyProvenance = "user_explicit",
        user_set_receipt_ref: str | None = None,
    ) -> AuthorizationPolicyState:
        return await self._store._compare_and_set_policy_mode_tx(
            self._db, mode, expected_generation=expected_generation,
            provenance=provenance,
            user_set_receipt_ref=user_set_receipt_ref,
        )

    async def get_legacy_authorization_import(
        self, source_key: str
    ) -> LegacyAuthorizationImportRecord | None:
        return await self._store._get_legacy_authorization_import_tx(
            self._db, source_key
        )

    async def put_legacy_authorization_import(
        self, record: LegacyAuthorizationImportRecord
    ) -> LegacyAuthorizationImportRecord:
        return await self._store._put_legacy_authorization_import_tx(
            self._db, record
        )


class CapabilityStore:
    """Repository over product state (or explicit pre-cutover compatibility)."""

    def __init__(
        self,
        execution_database: str | Path | ExecutionDatabaseOwner,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if isinstance(execution_database, (str, Path)):
            self.path = Path(execution_database)
            self._owner: ExecutionDatabaseOwner | None = None
        else:
            self.path = Path(execution_database.path)
            self._owner = execution_database
        self.product_owned = bool(
            getattr(execution_database, "is_product_state_owner", False)
        )
        self._owned_connection_ids: set[int] = set()
        self._clock = clock
        self._initialize_lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            if self._owner is not None:
                initialized = self._owner.initialize()
                if inspect.isawaitable(initialized):
                    await initialized
            if not self.path.exists():
                raise CapabilitySchemaMissing(
                    "execution_database_missing",
                    f"execution database does not exist: {self.path}",
                )
            async with aiosqlite.connect(self.path) as db:
                row = await (
                    await db.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name='capability_schema_state'"
                    )
                ).fetchone()
                if row is None:
                    raise CapabilitySchemaMissing(
                        "capability_schema_missing",
                        "execution database has no capability schema; run the UoW migration",
                    )
                state = await (
                    await db.execute(
                        "SELECT schema_version FROM capability_schema_state "
                        "WHERE singleton_id=1"
                    )
                ).fetchone()
                version = int(state[0]) if state else 0
                if version != CAPABILITY_SCHEMA_VERSION:
                    raise CapabilitySchemaMissing(
                        "unsupported_capability_schema",
                        f"capability schema is {version}, expected {CAPABILITY_SCHEMA_VERSION}",
                    )
                await self._migrate_authorization_policy_provenance(db)
                await db.commit()
            self._initialized = True

    async def _migrate_authorization_policy_provenance(
        self, db: aiosqlite.Connection
    ) -> None:
        """Add the v2 provenance columns and classify legacy rows once."""

        columns = {
            str(row[1])
            for row in await (await db.execute(
                "PRAGMA table_info(authorization_policy_state)"
            )).fetchall()
        }
        if "provenance" not in columns:
            await db.execute(
                "ALTER TABLE authorization_policy_state ADD COLUMN provenance "
                "TEXT NOT NULL DEFAULT 'needs_user_choice'"
            )
        if "schema_generation" not in columns:
            await db.execute(
                "ALTER TABLE authorization_policy_state ADD COLUMN schema_generation "
                "INTEGER NOT NULL DEFAULT 2"
            )
        if "user_set_receipt_ref" not in columns:
            await db.execute(
                "ALTER TABLE authorization_policy_state ADD COLUMN user_set_receipt_ref TEXT"
            )
        await db.execute(
            """UPDATE authorization_policy_state
               SET provenance='user_explicit',
                   user_set_receipt_ref=COALESCE(
                       user_set_receipt_ref,'policy-user-set:migrated:' || generation
                   )
               WHERE generation>0 AND provenance='needs_user_choice'"""
        )
        await db.execute(
            """UPDATE authorization_policy_state
               SET provenance='legacy_import',user_set_receipt_ref=NULL
               WHERE provenance='needs_user_choice' AND EXISTS (
                   SELECT 1 FROM authorization_policy_legacy_imports
                   WHERE outcome='imported'
               )"""
        )
        await db.execute(
            """UPDATE authorization_policy_state
               SET mode='auto',
                   provenance='factory_default_migrated',user_set_receipt_ref=NULL,
                   updated_at=?
               WHERE mode='manual' AND generation=0
                 AND provenance='needs_user_choice'
                 AND user_set_receipt_ref IS NULL
                 AND EXISTS (
                     SELECT 1 FROM authorization_policy_legacy_imports
                     WHERE outcome='missing'
                 )""",
            (self._clock(),),
        )

    def now(self) -> float:
        return self._clock()

    def bind(self, db: aiosqlite.Connection) -> CapabilityStoreTx:
        if self.product_owned and id(db) not in self._owned_connection_ids:
            raise CapabilityStoreConflict(
                "foreign_product_transaction",
                "product CapabilityStore can bind only its own product-state transaction",
            )
        return CapabilityStoreTx(self, db)

    async def _connect(self) -> aiosqlite.Connection:
        await self.initialize()
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=FULL")
        await db.execute("PRAGMA busy_timeout=5000")
        self._owned_connection_ids.add(id(db))
        return db

    @asynccontextmanager
    async def read_connection(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await self._connect()
        try:
            yield db
        finally:
            self._owned_connection_ids.discard(id(db))
            await db.close()

    @asynccontextmanager
    async def write_transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self.read_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            else:
                if db.in_transaction:
                    await db.commit()

    async def _state_tx(self, db: aiosqlite.Connection) -> CapabilityStoreState:
        row = await (
            await db.execute(
                """SELECT schema_version,catalog_generation,binding_generation,
                (SELECT COUNT(*) FROM capability_publish_intents
                  WHERE status IN ('pending','unknown')) AS pending_publish_count
                FROM capability_schema_state WHERE singleton_id=1"""
            )
        ).fetchone()
        if row is None:
            raise CapabilitySchemaMissing(
                "capability_schema_missing", "capability schema state is absent"
            )
        return CapabilityStoreState(
            schema_version=int(row["schema_version"]),
            catalog_generation=int(row["catalog_generation"]),
            binding_generation=int(row["binding_generation"]),
            pending_publish_count=int(row["pending_publish_count"]),
        )

    async def state(self) -> CapabilityStoreState:
        async with self.read_connection() as db:
            return await self._state_tx(db)

    async def _bump_generation_tx(
        self,
        db: aiosqlite.Connection,
        *,
        catalog: bool,
        binding: bool,
    ) -> None:
        await db.execute(
            """UPDATE capability_schema_state
               SET catalog_generation=catalog_generation+?,
                   binding_generation=binding_generation+?,
                   updated_at=?
               WHERE singleton_id=1""",
            (int(catalog), int(binding), self._clock()),
        )

    async def _ensure_owner_detail_key_tx(
        self, db: aiosqlite.Connection, key: OwnerScopeKey
    ) -> PlatformDetailToken:
        now = self._clock()
        await db.execute(
            """INSERT OR IGNORE INTO capability_owner_detail_versions(
                owner_key,scope,scope_key,row_state,version,
                owner_catalog_generation,committed_owner_binding_set_stamp,
                manager_receipt_set_hash,updated_at
            ) VALUES(?,?,?,'existing',0,0,?,?,?)""",
            (
                key.owner_key,
                key.scope,
                key.scope_key,
                EMPTY_OWNER_BINDING_SET_STAMP,
                EMPTY_RECEIPT_SET_HASH,
                now,
            ),
        )
        row = await (
            await db.execute(
                """SELECT * FROM capability_owner_detail_versions
                   WHERE owner_key=? AND scope=? AND scope_key=?""",
                (key.owner_key, key.scope, key.scope_key),
            )
        ).fetchone()
        if row is None:  # pragma: no cover - INSERT OR IGNORE guarantees it
            raise CapabilityStoreError(
                "owner_detail_missing", "owner detail row was not created"
            )
        return _detail_token_from_row(row)

    async def _touch_owner_detail_tx(
        self, db: aiosqlite.Connection, key: OwnerScopeKey
    ) -> PlatformDetailToken:
        await self._ensure_owner_detail_key_tx(db, key)
        stamp = await _owner_binding_stamp_tx(db, key)
        exists = bool(stamp.bindings)
        cursor = await db.execute(
            """UPDATE capability_owner_detail_versions
               SET row_state=?,version=version+1,
                   owner_catalog_generation=owner_catalog_generation+1,
                   committed_owner_binding_set_stamp=?,updated_at=?
               WHERE owner_key=? AND scope=? AND scope_key=?""",
            (
                "existing" if exists else "deleted",
                stamp.fingerprint if exists else EMPTY_OWNER_BINDING_SET_STAMP,
                self._clock(),
                key.owner_key,
                key.scope,
                key.scope_key,
            ),
        )
        if cursor.rowcount != 1:
            raise CapabilityStoreConflict(
                "owner_detail_generation_conflict",
                "owner detail token changed during mutation",
            )
        row = await (
            await db.execute(
                """SELECT * FROM capability_owner_detail_versions
                   WHERE owner_key=? AND scope=? AND scope_key=?""",
                (key.owner_key, key.scope, key.scope_key),
            )
        ).fetchone()
        if row is None:  # pragma: no cover
            raise CapabilityStoreError(
                "owner_detail_missing", "owner detail row disappeared"
            )
        return _detail_token_from_row(row)

    async def _record_version_tx(
        self, db: aiosqlite.Connection, record: CapabilityVersionRecord
    ) -> CapabilityVersionRecord:
        descriptor = record.descriptor
        install_path = Path(record.install_path).resolve(strict=False)
        if record.validation_status not in {"pending", "healthy", "degraded", "failed"}:
            raise CapabilityStoreError(
                "invalid_validation_status",
                f"unknown validation status: {record.validation_status}",
            )
        source_json = canonical_json({"source": descriptor.source})
        payload = (
            descriptor.capability_id,
            descriptor.version,
            descriptor.manifest_hash,
            canonical_json(descriptor.to_dict()),
            source_json,
            str(install_path),
            record.validation_status,
            canonical_json(list(record.expected_tool_fingerprints)),
            record.parent_version,
            record.parent_manifest_hash,
            record.derived_from_receipt_ref,
            record.created_at,
        )
        existing = await (
            await db.execute(
                "SELECT * FROM capability_versions WHERE pack_id=? AND version=?",
                (descriptor.capability_id, descriptor.version),
            )
        ).fetchone()
        if existing is not None:
            current = _version_from_row(existing)
            if (
                current.descriptor.manifest_hash != descriptor.manifest_hash
                or current.descriptor.fingerprint != descriptor.fingerprint
                or current.install_path != install_path
                or current.expected_tool_fingerprints
                != record.expected_tool_fingerprints
                or current.parent_version != record.parent_version
                or current.parent_manifest_hash != record.parent_manifest_hash
                or current.derived_from_receipt_ref
                != record.derived_from_receipt_ref
            ):
                raise CapabilityStoreConflict(
                    "capability_version_conflict",
                    "same capability version already exists with different immutable data",
                )
            return current
        await db.execute(
            """INSERT INTO capability_versions(
                pack_id,version,manifest_hash,descriptor_json,source_json,
                install_path,validation_status,expected_tool_fingerprints_json,
                parent_version,parent_manifest_hash,derived_from_receipt_ref,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            payload,
        )
        await self._bump_generation_tx(db, catalog=True, binding=False)
        return record

    async def record_version(
        self, record: CapabilityVersionRecord
    ) -> CapabilityVersionRecord:
        async with self.write_transaction() as db:
            return await self._record_version_tx(db, record)

    async def migrate_version_tool_fingerprint_schema(
        self,
        *,
        pack_id: str,
        version: str,
        manifest_hash: str,
        expected_fingerprints: Sequence[str],
        replacement_fingerprints: Sequence[str],
    ) -> bool:
        """CAS one validated ToolSpec fingerprint-schema upgrade.

        Capability pack identity and files remain immutable.  This narrowly
        migrates only the host-computed fingerprint projection after the
        caller has reproduced every legacy value from the validated pack.
        """

        expected = tuple(str(value) for value in expected_fingerprints)
        replacement = tuple(
            str(value) for value in replacement_fingerprints
        )
        if not expected or len(expected) != len(replacement):
            raise ValueError("fingerprint migration requires equal non-empty sets")
        if any(
            len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in expected + replacement
        ):
            raise ValueError("fingerprint migration values must be sha256 hex")
        async with self.write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT expected_tool_fingerprints_json
                       FROM capability_versions
                       WHERE pack_id=? AND version=? AND manifest_hash=?""",
                    (pack_id, version, manifest_hash),
                )
            ).fetchone()
            if row is None:
                raise CapabilityStoreError(
                    "capability_version_missing",
                    "fingerprint migration target is absent",
                )
            current = tuple(_json_array(str(row[0])))
            if current == replacement:
                return False
            if current != expected:
                raise CapabilityStoreConflict(
                    "capability_fingerprint_migration_conflict",
                    "capability ToolSpec fingerprints changed concurrently",
                )
            cursor = await db.execute(
                """UPDATE capability_versions
                   SET expected_tool_fingerprints_json=?
                   WHERE pack_id=? AND version=? AND manifest_hash=?
                     AND expected_tool_fingerprints_json=?""",
                (
                    json.dumps(list(replacement), separators=(",", ":")),
                    pack_id,
                    version,
                    manifest_hash,
                    str(row[0]),
                ),
            )
            if cursor.rowcount != 1:
                raise CapabilityStoreConflict(
                    "capability_fingerprint_migration_conflict",
                    "capability ToolSpec fingerprints changed concurrently",
                )
            await self._bump_generation_tx(
                db,
                catalog=True,
                binding=False,
            )
            return True

    async def _put_version_storage_tx(
        self,
        db: aiosqlite.Connection,
        record: CapabilityVersionStorage,
    ) -> CapabilityVersionStorage:
        identity = (record.pack_id, record.version, record.manifest_hash)
        version_row = await (
            await db.execute(
                """SELECT 1 FROM capability_versions
                   WHERE pack_id=? AND version=? AND manifest_hash=?""",
                identity,
            )
        ).fetchone()
        if version_row is None:
            raise CapabilityStoreError(
                "capability_version_missing",
                "version storage cannot reference an absent capability version",
            )
        identity_rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_version_storage
                   WHERE pack_id=? AND version=? AND manifest_hash=?""",
                identity,
            )
        )
        if identity_rows:
            current = _version_storage_from_row(identity_rows[0])
            if current != record:
                raise CapabilityStoreConflict(
                    "version_storage_conflict",
                    "immutable version storage mapping changed",
                )
            return current
        key_rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_version_storage
                   WHERE storage_key=?""",
                (record.storage_key,),
            )
        )
        if key_rows:
            occupied = _version_storage_from_row(key_rows[0])
            if (
                occupied.pack_id,
                occupied.version,
                occupied.manifest_hash,
            ) != identity:
                raise CapabilityStoreConflict(
                    "storage_key_collision",
                    "storage key belongs to another full capability identity",
                )
        await db.execute(
            """INSERT INTO capability_version_storage(
                pack_id,version,manifest_hash,storage_key,
                pack_storage_schema,pack_root_hash,
                environment_storage_schema,environment_root_hash,
                archive_hash,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                record.pack_id,
                record.version,
                record.manifest_hash,
                record.storage_key,
                record.pack_storage_schema,
                record.pack_root_hash,
                record.environment_storage_schema,
                record.environment_root_hash,
                record.archive_hash,
                record.created_at,
            ),
        )
        return record

    async def put_version_storage(
        self, record: CapabilityVersionStorage
    ) -> CapabilityVersionStorage:
        async with self.write_transaction() as db:
            return await self._put_version_storage_tx(db, record)

    async def get_version_storage(
        self, pack_id: str, version: str, manifest_hash: str
    ) -> CapabilityVersionStorage | None:
        async with self.read_connection() as db:
            rows = await _fetch_mappings(
                await db.execute(
                    """SELECT * FROM capability_version_storage
                       WHERE pack_id=? AND version=? AND manifest_hash=?""",
                    (pack_id, version, manifest_hash),
                )
            )
            return None if not rows else _version_storage_from_row(rows[0])

    async def get_version(
        self, pack_id: str, version: str, manifest_hash: str | None = None
    ) -> CapabilityVersionRecord | None:
        async with self.read_connection() as db:
            if manifest_hash is None:
                row = await (
                    await db.execute(
                        "SELECT * FROM capability_versions WHERE pack_id=? AND version=?",
                        (pack_id, version),
                    )
                ).fetchone()
            else:
                row = await (
                    await db.execute(
                        """SELECT * FROM capability_versions
                           WHERE pack_id=? AND version=? AND manifest_hash=?""",
                        (pack_id, version, manifest_hash),
                    )
                ).fetchone()
            return None if row is None else _version_from_row(row)

    async def list_versions(
        self, pack_id: str | None = None
    ) -> tuple[CapabilityVersionRecord, ...]:
        async with self.read_connection() as db:
            if pack_id is None:
                rows = await (
                    await db.execute(
                        "SELECT * FROM capability_versions ORDER BY pack_id,created_at"
                    )
                ).fetchall()
            else:
                rows = await (
                    await db.execute(
                        """SELECT * FROM capability_versions WHERE pack_id=?
                           ORDER BY created_at""",
                        (pack_id,),
                    )
                ).fetchall()
            return tuple(_version_from_row(row) for row in rows)

    @staticmethod
    def _binding_id(
        owner_key: str, scope: str, scope_key: str, pack_id: str
    ) -> str:
        return hashlib.sha256(
            f"{owner_key}|{scope}|{scope_key}|{pack_id}".encode("utf-8")
        ).hexdigest()

    async def _set_binding_tx(
        self,
        db: aiosqlite.Connection,
        *,
        scope: str,
        scope_key: str,
        pack_id: str,
        version: str,
        manifest_hash: str,
        expected_generation: int,
        enabled: bool = True,
        owner_key: str | None = None,
        management_policy: str | None = None,
    ) -> CapabilityBinding:
        if scope not in {"builtin", "run", "project", "user"}:
            raise CapabilityStoreError("invalid_scope", f"unknown scope: {scope}")
        resolved_owner_key = str(owner_key or _default_owner_key(scope)).strip()
        if not resolved_owner_key:
            raise CapabilityStoreError("invalid_owner_key", "owner key is required")
        resolved_management_policy = str(
            management_policy
            or ("host_managed" if scope == "builtin" else "legacy_import")
        )
        if resolved_management_policy not in {
            "host_managed",
            "user_managed",
            "legacy_import",
        }:
            raise CapabilityStoreError(
                "invalid_management_policy", "unknown management policy"
            )
        version_row = await (
            await db.execute(
                """SELECT 1 FROM capability_versions
                   WHERE pack_id=? AND version=? AND manifest_hash=?""",
                (pack_id, version, manifest_hash),
            )
        ).fetchone()
        if version_row is None:
            raise CapabilityStoreError(
                "capability_version_not_found",
                f"unknown capability version {pack_id}@{version}",
            )
        row = await (
            await db.execute(
                """SELECT * FROM capability_bindings
                   WHERE owner_key=? AND scope=? AND scope_key=? AND pack_id=?""",
                (resolved_owner_key, scope, scope_key, pack_id),
            )
        ).fetchone()
        now = self._clock()
        binding_id = self._binding_id(
            resolved_owner_key, scope, scope_key, pack_id
        )
        previous_identity: tuple[str, str, str] | None = None
        if row is None:
            if expected_generation != 0:
                raise CapabilityStoreConflict(
                    "binding_generation_conflict", "binding was not created"
                )
            generation = 1
            await db.execute(
                """INSERT INTO capability_bindings(
                    binding_id,owner_key,scope,scope_key,pack_id,active_version,
                    active_manifest_hash,generation,enabled,management_policy,
                    management_generation,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    binding_id,
                    resolved_owner_key,
                    scope,
                    scope_key,
                    pack_id,
                    version,
                    manifest_hash,
                    generation,
                    int(enabled),
                    resolved_management_policy,
                    1,
                    now,
                ),
            )
        else:
            current = _binding_from_row(row)
            if (
                current.version == version
                and current.manifest_hash == manifest_hash
                and current.active == enabled
                and current.management_policy == resolved_management_policy
            ):
                if expected_generation not in {
                    current.generation,
                    current.generation - 1,
                }:
                    raise CapabilityStoreConflict(
                        "binding_generation_conflict",
                        "binding generation changed",
                    )
                return current
            if current.generation != expected_generation:
                raise CapabilityStoreConflict(
                    "binding_generation_conflict", "binding generation changed"
                )
            if current.active:
                previous_identity = (
                    current.capability_id,
                    current.version,
                    current.manifest_hash,
                )
            generation = current.generation + 1
            management_generation = current.management_generation + 1
            cursor = await db.execute(
                """UPDATE capability_bindings SET
                    active_version=?,active_manifest_hash=?,generation=?,
                    enabled=?,management_policy=?,management_generation=?,
                    updated_at=?
                   WHERE binding_id=? AND generation=? AND owner_key=?""",
                (
                    version,
                    manifest_hash,
                    generation,
                    int(enabled),
                    resolved_management_policy,
                    management_generation,
                    now,
                    current.binding_id,
                    expected_generation,
                    resolved_owner_key,
                ),
            )
            if cursor.rowcount != 1:
                raise CapabilityStoreConflict(
                    "binding_generation_conflict", "binding generation changed"
                )
        await self._bump_generation_tx(db, catalog=True, binding=True)
        key = OwnerScopeKey(
            resolved_owner_key,
            scope,  # type: ignore[arg-type]
            scope_key,
        )
        await self._touch_owner_detail_tx(db, key)
        if previous_identity is not None and (
            previous_identity != (pack_id, version, manifest_hash)
            or not enabled
        ):
            await self._drain_unreferenced_runtimes_tx(
                db,
                pack_id=previous_identity[0],
                version=previous_identity[1],
                manifest_hash=previous_identity[2],
            )
        return CapabilityBinding(
            binding_id=binding_id,
            capability_id=pack_id,
            version=version,
            manifest_hash=manifest_hash,
            scope=scope,  # type: ignore[arg-type]
            scope_key=scope_key,
            active=enabled,
            generation=generation,
            owner_key=resolved_owner_key,
            management_policy=resolved_management_policy,  # type: ignore[arg-type]
            management_generation=(
                1 if row is None else current.management_generation + 1
            ),
        )

    async def set_binding(self, **kwargs: Any) -> CapabilityBinding:
        async with self.write_transaction() as db:
            return await self._set_binding_tx(db, **kwargs)

    async def converge_legacy_project_skill_bindings(
        self, *, global_owner_key: str
    ) -> tuple[Mapping[str, JsonValue], ...]:
        """Deterministically promote exact legacy Project Skill bindings.

        Every Project source for a pack participates in one frozen stamp.  A
        promotion is legal only when version, manifest, member content and
        durable install receipt all agree.  Promotion and retirement happen
        in the same SQLite transaction; ambiguous state is recorded durably
        and left untouched for an explicit reinstall/choice.
        """

        owner_key = str(global_owner_key).strip()
        if not owner_key.startswith("user:v2:"):
            raise CapabilityStoreError(
                "invalid_global_owner_key", "validated user-global owner is required"
            )
        active_statuses = (
            "staging", "awaiting_confirmation", "publishing",
            "published_pending_runtime_verification", "succeeded", "unknown",
        )
        results: list[Mapping[str, JsonValue]] = []
        async with self.write_transaction() as db:
            rows = await (await db.execute(
                f"""SELECT b.binding_id,b.owner_key,b.scope_key,b.pack_id,
                           b.active_version AS version,
                           b.active_manifest_hash AS manifest_hash,b.generation,
                           i.intent_id,i.status,i.settlement_ref,i.verification_ref,
                           i.member_set_stamp,m.ordinal,m.content_hash,
                           h.operation_id,o.kind AS operation_kind,
                           o.phase AS operation_phase,o.status AS operation_status,
                           o.request_json,h.member_set_stamp AS handoff_member_set_stamp,
                           pe.status AS evidence_status,pe.evidence_json,
                           om.committed_version,om.committed_manifest_hash,
                           om.committed_set_stamp
                    FROM capability_bindings b
                    JOIN capability_skill_install_members m
                      ON m.pack_id=b.pack_id AND m.version=b.active_version
                     AND m.manifest_hash=b.active_manifest_hash
                    JOIN capability_skill_install_intents i
                      ON i.intent_id=m.intent_id AND i.project_scope_key=b.scope_key
                    LEFT JOIN capability_skill_install_handoffs h
                      ON h.intent_id=i.intent_id
                    LEFT JOIN capability_operations o
                      ON o.operation_id=h.operation_id
                    LEFT JOIN capability_operation_phase_evidence pe
                      ON pe.operation_id=h.operation_id AND pe.phase='batch_committed'
                    LEFT JOIN capability_operation_members om
                      ON om.operation_id=h.operation_id AND om.ordinal=m.ordinal
                    WHERE b.scope='project' AND b.enabled=1
                      AND i.source_json NOT LIKE '%global-skill-install-source-v2%'
                      AND i.status IN ({','.join('?' for _ in active_statuses)})
                    ORDER BY b.pack_id,b.owner_key,b.scope_key,b.binding_id,i.intent_id""",
                active_statuses,
            )).fetchall()
            by_pack: dict[str, list[Mapping[str, Any]]] = {}
            for row in rows:
                by_pack.setdefault(str(row["pack_id"]), []).append(row)
            for pack_id, candidates in sorted(by_pack.items()):
                frozen = []
                for row in candidates:
                    evidence = _json_object(
                        None if row["evidence_json"] is None else str(row["evidence_json"])
                    ) or {}
                    evidence_members = tuple(
                        item for item in evidence.get("members", ())
                        if isinstance(item, Mapping)
                    )
                    operation_member_rows = await (await db.execute(
                        """SELECT ordinal,normalized_name,pack_id,version,
                                  manifest_hash,content_hash,source_digest,
                                  committed_version,committed_manifest_hash,
                                  committed_set_stamp
                           FROM capability_operation_members
                           WHERE operation_id=? ORDER BY ordinal""",
                        (str(row["operation_id"] or ""),),
                    )).fetchall()
                    intent_member_rows = await (await db.execute(
                        """SELECT ordinal,normalized_name,pack_id,version,
                                  manifest_hash,content_hash,source_digest
                           FROM capability_skill_install_members
                           WHERE intent_id=? ORDER BY ordinal""",
                        (str(row["intent_id"]),),
                    )).fetchall()
                    rebuilt_members = [
                        {
                            "pack_id": str(item["pack_id"]),
                            "version": str(item["version"]),
                            "manifest_hash": str(item["manifest_hash"]),
                            "content_hash": str(item["content_hash"]),
                        }
                        for item in operation_member_rows
                    ]
                    intended_members = [
                        {
                            "pack_id": str(item["pack_id"]),
                            "version": str(item["version"]),
                            "manifest_hash": str(item["manifest_hash"]),
                            "content_hash": str(item["content_hash"]),
                        }
                        for item in intent_member_rows
                    ]
                    receipt_member_identities = [
                        {
                            "pack_id": str(item.get("pack_id") or ""),
                            "version": str(item.get("version") or ""),
                            "manifest_hash": str(item.get("manifest_hash") or ""),
                            "content_hash": str(item.get("content_hash") or ""),
                        }
                        for item in evidence_members
                    ]
                    full_member_set_valid = (
                        rebuilt_members == intended_members == receipt_member_identities
                        and tuple(int(item["ordinal"]) for item in operation_member_rows)
                        == tuple(range(len(operation_member_rows)))
                        and all(
                            str(item["committed_version"] or "") == str(item["version"])
                            and str(item["committed_manifest_hash"] or "") == str(item["manifest_hash"])
                            and str(item["committed_set_stamp"] or "") == str(row["member_set_stamp"])
                            for item in operation_member_rows
                        )
                    )
                    expected_member = {
                        "pack_id": str(row["pack_id"]),
                        "version": str(row["version"]),
                        "manifest_hash": str(row["manifest_hash"]),
                        "content_hash": str(row["content_hash"]),
                    }
                    matching_members = [
                        item for item in evidence_members
                        if all(str(item.get(key) or "") == value for key, value in expected_member.items())
                    ]
                    receipt_payload = {
                        key: value for key, value in evidence.items()
                        if key not in {"manager_receipt_hash", "install_root"}
                    }
                    receipt_hash = str(evidence.get("manager_receipt_hash") or "")
                    operation_request = _json_object(
                        None if row["request_json"] is None else str(row["request_json"])
                    ) or {}
                    evidence_valid = all((
                        str(row["status"]) == "succeeded",
                        str(row["operation_kind"] or "") == "skill_install_batch",
                        str(row["operation_phase"] or "") == "batch_committed",
                        str(row["operation_status"] or "") == "succeeded",
                        str(row["evidence_status"] or "") == "committed",
                        str(evidence.get("schema") or "") == "capability-batch-manager-receipt-v1",
                        str(evidence.get("operation_id") or "") == str(row["operation_id"] or ""),
                        str(evidence.get("scope") or "") == "project",
                        str(evidence.get("scope_key") or "") == str(row["scope_key"]),
                        str(evidence.get("project_scope_key") or "") == str(row["scope_key"]),
                        str(evidence.get("owner_key") or "") == str(row["owner_key"]),
                        str(evidence.get("committed_set_stamp") or "") == str(row["member_set_stamp"]),
                        str(row["handoff_member_set_stamp"] or "") == str(row["member_set_stamp"]),
                        str(operation_request.get("member_set_stamp") or "") == str(row["member_set_stamp"]),
                        str(evidence.get("publication_state") or "") == "active",
                        len(matching_members) == 1,
                        full_member_set_valid,
                        str(row["committed_version"] or "") == str(row["version"]),
                        str(row["committed_manifest_hash"] or "") == str(row["manifest_hash"]),
                        str(row["committed_set_stamp"] or "") == str(row["member_set_stamp"]),
                        len(receipt_hash) == 64,
                        receipt_hash == fingerprint_json(receipt_payload),
                        str(row["settlement_ref"] or "") == receipt_hash,
                        bool(str(row["verification_ref"] or "")),
                    ))
                    frozen.append({
                        "binding_id": str(row["binding_id"]),
                        "owner_key": str(row["owner_key"]),
                        "scope_key": str(row["scope_key"]),
                        "generation": int(row["generation"]),
                        "intent_id": str(row["intent_id"]),
                        "intent_status": str(row["status"]),
                        "version": str(row["version"]),
                        "manifest_hash": str(row["manifest_hash"]),
                        "content_hash": str(row["content_hash"]),
                        "manager_receipt_hash": receipt_hash,
                        "runtime_verification_ref": str(row["verification_ref"] or ""),
                        "manager_evidence_valid": evidence_valid,
                        "ordered_member_set_hash": fingerprint_json(rebuilt_members),
                    })
                source_stamp = fingerprint_json(frozen)
                prior = await (await db.execute(
                    """SELECT * FROM capability_legacy_global_convergence
                       WHERE global_owner_key=? AND pack_id=?""",
                    (owner_key, pack_id),
                )).fetchone()
                if prior is not None:
                    if str(prior["source_binding_set_stamp"]) != source_stamp:
                        raise CapabilityStoreConflict(
                            "legacy_global_convergence_source_changed",
                            f"legacy source set changed after decision for {pack_id}",
                        )
                    results.append({
                        "pack_id": pack_id, "outcome": str(prior["outcome"]),
                        "source_binding_set_stamp": source_stamp,
                    })
                    continue
                identities = {
                    (item["version"], item["manifest_hash"], item["content_hash"])
                    for item in frozen
                }
                selected = next(iter(identities)) if len(identities) == 1 else None
                existing = await (await db.execute(
                    """SELECT b.*,v.derived_from_receipt_ref
                       FROM capability_bindings b
                       JOIN capability_versions v
                         ON v.pack_id=b.pack_id AND v.version=b.active_version
                        AND v.manifest_hash=b.active_manifest_hash
                       WHERE b.owner_key=? AND b.scope='user' AND b.scope_key=?
                         AND b.pack_id=?""",
                    (owner_key, owner_key, pack_id),
                )).fetchone()
                conflict_reasons: list[str] = []
                if selected is None:
                    conflict_reasons.append("legacy_sources_differ")
                if any(not item["manager_evidence_valid"] for item in frozen):
                    conflict_reasons.append("legacy_manager_receipt_invalid")
                if existing is not None and selected is not None and (
                    str(existing["active_version"]), str(existing["active_manifest_hash"])
                ) != selected[:2]:
                    conflict_reasons.append("existing_global_binding_differs")
                if existing is not None and selected is not None:
                    receipt_hashes = {
                        str(item["manager_receipt_hash"]) for item in frozen
                    }
                    existing_receipt = str(existing["derived_from_receipt_ref"] or "")
                    # Version/manifest alone do not identify installed bytes.  The
                    # immutable version row must point at one of the Manager
                    # receipts just validated above; that receipt commits the
                    # exact ordered content hashes.  No receipt means content is
                    # unprovable, not "probably the same".
                    if existing_receipt not in receipt_hashes:
                        conflict_reasons.append("existing_global_content_or_receipt_differs")
                if conflict_reasons:
                    conflict = {"reasons": sorted(set(conflict_reasons)), "sources": frozen}
                    await db.execute(
                        """INSERT INTO capability_legacy_global_convergence(
                           global_owner_key,pack_id,source_binding_set_stamp,outcome,
                           selected_identity_json,conflict_json,created_at
                           ) VALUES(?,?,?,'legacy_global_conflict',NULL,?,?)""",
                        (owner_key, pack_id, source_stamp, canonical_json(conflict), self._clock()),
                    )
                    results.append({"pack_id": pack_id, "outcome": "legacy_global_conflict",
                                    "source_binding_set_stamp": source_stamp})
                    continue
                assert selected is not None
                await self._set_binding_tx(
                    db, scope="user", scope_key=owner_key, pack_id=pack_id,
                    version=selected[0], manifest_hash=selected[1],
                    expected_generation=(0 if existing is None else int(existing["generation"])),
                    owner_key=owner_key, management_policy="user_managed",
                )
                retired_binding_ids: set[str] = set()
                for item in frozen:
                    if item["binding_id"] in retired_binding_ids:
                        continue
                    cursor = await db.execute(
                        """DELETE FROM capability_bindings
                           WHERE binding_id=? AND generation=?""",
                        (item["binding_id"], item["generation"]),
                    )
                    if cursor.rowcount != 1:
                        raise CapabilityStoreConflict(
                            "legacy_project_retirement_cas_conflict",
                            f"legacy Project binding changed for {pack_id}",
                        )
                    retired_binding_ids.add(item["binding_id"])
                    await self._touch_owner_detail_tx(
                        db, OwnerScopeKey(item["owner_key"], "project", item["scope_key"])
                    )
                await self._bump_generation_tx(db, catalog=True, binding=True)
                selected_json = {
                    "version": selected[0], "manifest_hash": selected[1],
                    "content_hash": selected[2],
                    "manager_receipt_set_hash": fingerprint_json(sorted(
                        str(item["manager_receipt_hash"]) for item in frozen
                    )),
                }
                await db.execute(
                    """INSERT INTO capability_legacy_global_convergence(
                       global_owner_key,pack_id,source_binding_set_stamp,outcome,
                       selected_identity_json,conflict_json,created_at
                       ) VALUES(?,?,?,'promoted',?,NULL,?)""",
                    (owner_key, pack_id, source_stamp, canonical_json(selected_json), self._clock()),
                )
                results.append({"pack_id": pack_id, "outcome": "promoted",
                                "source_binding_set_stamp": source_stamp})
        return tuple(results)

    async def get_binding(
        self,
        scope: str,
        scope_key: str,
        pack_id: str,
        *,
        owner_key: str | None = None,
    ) -> CapabilityBinding | None:
        resolved_owner_key = str(owner_key or _default_owner_key(scope))
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM capability_bindings
                       WHERE owner_key=? AND scope=? AND scope_key=? AND pack_id=?""",
                    (resolved_owner_key, scope, scope_key, pack_id),
                )
            ).fetchone()
            return None if row is None else _binding_from_row(row)

    async def ensure_owner_detail_key(
        self, key: OwnerScopeKey
    ) -> PlatformDetailToken:
        async with self.write_transaction() as db:
            return await self._ensure_owner_detail_key_tx(db, key)

    async def _read_detail_tokens_tx(
        self,
        db: aiosqlite.Connection,
        keys: Sequence[OwnerScopeKey],
    ) -> PlatformDetailTokenVector:
        ordered = tuple(sorted(set(keys)))
        if not ordered:
            return PlatformDetailTokenVector(())
        clauses = " OR ".join(
            "(owner_key=? AND scope=? AND scope_key=?)" for _ in ordered
        )
        params = tuple(
            value
            for key in ordered
            for value in (key.owner_key, key.scope, key.scope_key)
        )
        rows = await (
            await db.execute(
                f"""SELECT * FROM capability_owner_detail_versions
                    WHERE {clauses}""",
                params,
            )
        ).fetchall()
        existing = {
            OwnerScopeKey(
                str(row["owner_key"]),
                str(row["scope"]),  # type: ignore[arg-type]
                str(row["scope_key"]),
            ): _detail_token_from_row(row)
            for row in rows
        }
        return PlatformDetailTokenVector(
            tuple(
                existing.get(key)
                or PlatformDetailToken(
                    key=key,
                    exists=False,
                    version=0,
                    owner_catalog_generation=0,
                    committed_owner_binding_set_stamp=(
                        EMPTY_OWNER_BINDING_SET_STAMP
                    ),
                    manager_receipt_set_hash=EMPTY_RECEIPT_SET_HASH,
                )
                for key in ordered
            )
        )

    async def read_detail_token_vector(
        self, keys: Sequence[OwnerScopeKey]
    ) -> PlatformDetailTokenVector:
        async with self.read_connection() as db:
            return await self._read_detail_tokens_tx(db, keys)

    async def read_detail_snapshot(
        self, keys: Sequence[OwnerScopeKey]
    ) -> PlatformDetailSnapshot:
        """Read exact owner keys in one Store transaction.

        Platform-level publish locks, CatalogGate checks, Manager receipts, and
        fallback/source reconciliation are intentionally outside this Store
        foundation.
        """

        ordered = tuple(sorted(set(keys)))
        async with self.read_connection() as db:
            await db.execute("BEGIN")
            try:
                tokens = await self._read_detail_tokens_tx(db, ordered)
                if not ordered:
                    bindings: tuple[CapabilityBinding, ...] = ()
                else:
                    clauses = " OR ".join(
                        "(owner_key=? AND scope=? AND scope_key=?)"
                        for _ in ordered
                    )
                    params = tuple(
                        value
                        for key in ordered
                        for value in (key.owner_key, key.scope, key.scope_key)
                    )
                    rows = await (
                        await db.execute(
                            f"""SELECT * FROM capability_bindings
                                WHERE {clauses}
                                ORDER BY owner_key,scope,scope_key,pack_id""",
                            params,
                        )
                    ).fetchall()
                    bindings = tuple(_binding_from_row(row) for row in rows)
            finally:
                if db.in_transaction:
                    await db.rollback()
        return PlatformDetailSnapshot(tokens=tokens, bindings=bindings)

    async def delete_binding(
        self,
        *,
        owner_key: str,
        scope: str,
        scope_key: str,
        pack_id: str,
        expected_generation: int,
    ) -> bool:
        key = OwnerScopeKey(
            owner_key, scope, scope_key  # type: ignore[arg-type]
        )
        async with self.write_transaction() as db:
            cursor = await db.execute(
                """DELETE FROM capability_bindings
                   WHERE owner_key=? AND scope=? AND scope_key=? AND pack_id=?
                     AND generation=?""",
                (
                    key.owner_key,
                    key.scope,
                    key.scope_key,
                    pack_id,
                    expected_generation,
                ),
            )
            if cursor.rowcount != 1:
                row = await (
                    await db.execute(
                        """SELECT generation FROM capability_bindings
                           WHERE owner_key=? AND scope=? AND scope_key=?
                             AND pack_id=?""",
                        (key.owner_key, key.scope, key.scope_key, pack_id),
                    )
                ).fetchone()
                if row is None:
                    return False
                raise CapabilityStoreConflict(
                    "binding_generation_conflict", "binding generation changed"
                )
            await self._bump_generation_tx(db, catalog=True, binding=True)
            await self._touch_owner_detail_tx(db, key)
            return True

    async def _put_run_catalog_snapshot_tx(
        self,
        db: aiosqlite.Connection,
        stamp: RunCatalogContentStamp,
        *,
        request_owner_key: str,
        catalog_generation_vector: Mapping[str, int],
        created_at: float | None = None,
        require_complete: bool = False,
    ) -> str:
        vector = {
            str(key): int(value)
            for key, value in sorted(catalog_generation_vector.items())
        }
        if any(value < 0 for value in vector.values()):
            raise CapabilityStoreError(
                "invalid_catalog_generation_vector",
                "catalog generations must be non-negative",
            )
        vector_json = canonical_json(vector)
        vector_hash = fingerprint_json(vector)
        now = self._clock() if created_at is None else float(created_at)
        existing = await (
            await db.execute(
                """SELECT * FROM capability_run_catalog_snapshots
                   WHERE run_catalog_content_stamp=?""",
                (stamp.fingerprint,),
            )
        ).fetchone()
        header = (
            request_owner_key,
            stamp.request_scope.canonical,
            stamp.request_scope_hash,
            vector_json,
            vector_hash,
            stamp.entry_set_hash,
            len(stamp.entries),
        )
        expected_rows: list[tuple[Any, ...]] = []
        empty_hash = fingerprint_json([])
        for ordinal, entry in enumerate(stamp.entries):
            envelope = dict(entry.canonical_envelope)
            visible = envelope.get("visible_bindings", [])
            if not isinstance(visible, list):
                raise CapabilityStoreError(
                    "invalid_visible_bindings",
                    "visible bindings must be a list",
                )
            selected = envelope.get("selected_binding")
            if selected is not None and not isinstance(selected, dict):
                raise CapabilityStoreError(
                    "invalid_selected_binding",
                    "selected binding must be an object",
                )
            selected = selected or {}
            tool_fingerprints = envelope.get("tool_spec_fingerprints", [])
            if not isinstance(tool_fingerprints, list):
                raise CapabilityStoreError(
                    "invalid_tool_fingerprints",
                    "tool fingerprints must be a list",
                )
            required_hashes = {}
            for field_name in (
                "instruction_refs_hash",
                "workflow_refs_hash",
                "runtime_descriptor_hash",
            ):
                value = envelope.get(field_name)
                if (
                    require_complete
                    and (not isinstance(value, str) or len(value) != 64)
                ):
                    raise CapabilityStoreError(
                        "invalid_catalog_entry",
                        f"{field_name} is required for durable catalog entries",
                    )
                required_hashes[field_name] = (
                    value
                    if isinstance(value, str) and len(value) == 64
                    else empty_hash
                )
            host_build = envelope.get("host_build_identity")
            expected_rows.append(
                (
                    stamp.fingerprint,
                    ordinal,
                    entry.entry_kind,
                    selected.get("binding_id"),
                    selected.get("owner_key"),
                    selected.get("scope"),
                    selected.get("scope_key"),
                    selected.get("generation"),
                    envelope.get("stable_host_binding_id"),
                    canonical_json(visible),
                    fingerprint_json(visible),
                    canonical_json(envelope),
                    entry.descriptor_fingerprint,
                    envelope.get("pack_id"),
                    envelope.get("version"),
                    envelope.get("manifest_hash"),
                    envelope.get("host_provider_name"),
                    envelope.get("host_source"),
                    envelope.get("host_spec_version"),
                    envelope.get("host_schema_hash"),
                    envelope.get("host_content_hash"),
                    (
                        None
                        if host_build is None
                        else canonical_json(host_build)
                    ),
                    canonical_json(tool_fingerprints),
                    required_hashes["instruction_refs_hash"],
                    required_hashes["workflow_refs_hash"],
                    required_hashes["runtime_descriptor_hash"],
                )
            )
        if existing is not None:
            actual = (
                str(existing["request_owner_key"]),
                str(existing["request_scope_canonical_json"]),
                str(existing["request_scope_hash"]),
                str(existing["catalog_generation_vector_json"]),
                str(existing["catalog_generation_vector_hash"]),
                str(existing["entry_set_hash"]),
                int(existing["expected_entry_count"]),
            )
            cursor = await db.execute(
                """SELECT run_catalog_content_stamp,ordinal,entry_kind,
                    selected_binding_id,selected_owner_key,selected_scope,
                    selected_scope_key,selected_binding_generation,
                    stable_host_binding_id,visible_bindings_json,
                    visible_binding_set_hash,descriptor_envelope_json,
                    descriptor_fingerprint,pack_id,version,manifest_hash,
                    host_provider_name,host_source,host_spec_version,
                    host_schema_hash,host_content_hash,host_build_identity,
                    tool_spec_fingerprints_json,instruction_refs_hash,
                    workflow_refs_hash,runtime_descriptor_hash
                   FROM capability_run_catalog_snapshot_entries
                   WHERE run_catalog_content_stamp=?
                   ORDER BY ordinal""",
                (stamp.fingerprint,),
            )
            stored_rows = [tuple(row) for row in await cursor.fetchall()]
            if actual != header or stored_rows != expected_rows:
                raise CapabilityStoreConflict(
                    "run_catalog_snapshot_conflict",
                    "stored catalog snapshot differs from immutable content",
                )
            return stamp.fingerprint
        await db.execute(
            """INSERT INTO capability_run_catalog_snapshots(
                run_catalog_content_stamp,request_owner_key,
                request_scope_canonical_json,request_scope_hash,
                catalog_generation_vector_json,
                catalog_generation_vector_hash,entry_set_hash,
                expected_entry_count,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (stamp.fingerprint, *header, now),
        )
        for row in expected_rows:
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
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    row,
                )
        return stamp.fingerprint

    async def put_run_catalog_snapshot(
        self,
        stamp: RunCatalogContentStamp,
        *,
        request_owner_key: str,
        catalog_generation_vector: Mapping[str, int],
        created_at: float | None = None,
    ) -> str:
        async with self.write_transaction() as db:
            return await self._put_run_catalog_snapshot_tx(
                db,
                stamp,
                request_owner_key=request_owner_key,
                catalog_generation_vector=catalog_generation_vector,
                created_at=created_at,
            )

    async def _prepare_snapshot_lease_intent_tx(
        self,
        db: aiosqlite.Connection,
        *,
        snapshot_ref: str,
        snapshot_ref_schema: str,
        run_id: str,
        root_run_id: str,
        request_id: str,
        turn_id: str,
        owner_operation_id: str,
        lease_owner_kind: str,
        run_catalog_content_stamp: str,
        entry_set_hash: str,
        expected_entry_count: int,
    ) -> CapabilitySnapshotLeaseIntent:
        if snapshot_ref_schema not in {"legacy_v1", "run_catalog_v2"}:
            raise CapabilityStoreError(
                "invalid_snapshot_ref_schema", "unknown snapshot ref schema"
            )
        if lease_owner_kind not in {
            "run_start",
            "refresh_commit",
            "legacy_boundary",
            "queued_child_legacy",
        }:
            raise CapabilityStoreError(
                "invalid_lease_owner_kind", "unknown lease owner kind"
            )
        if snapshot_ref_schema == "run_catalog_v2" and lease_owner_kind not in {
            "run_start",
            "refresh_commit",
        }:
            raise CapabilityStoreError(
                "invalid_lease_owner_kind",
                "run_catalog_v2 supports only run start or refresh commit",
            )
        identity = {
            "domain": "capability-lease-intent-v1",
            "snapshot_ref": snapshot_ref,
            "run_id": run_id,
            "root_run_id": root_run_id,
            "owner_operation_id": owner_operation_id,
            "run_catalog_content_stamp": run_catalog_content_stamp,
            "entry_set_hash": entry_set_hash,
            "expected_entry_count": int(expected_entry_count),
        }
        existing_rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_snapshot_lease_intents
                   WHERE run_id=? AND owner_operation_id=?""",
                (run_id, owner_operation_id),
            )
        )
        if existing_rows:
            existing = existing_rows[0]
            record = _snapshot_lease_intent_from_row(existing)
            candidate = {
                **identity,
                "lease_generation": record.lease_generation,
            }
            immutable = (
                record.snapshot_ref_schema,
                record.request_id,
                record.turn_id,
                record.lease_owner_kind,
            )
            requested = (
                snapshot_ref_schema,
                request_id,
                turn_id,
                lease_owner_kind,
            )
            if (
                record.lease_intent_hash != fingerprint_json(candidate)
                or immutable != requested
            ):
                raise CapabilityStoreConflict(
                    "snapshot_lease_intent_conflict",
                    "owner operation already has another lease intent",
                )
            return record
        previous_rows = await _fetch_mappings(
            await db.execute(
                """SELECT status,lease_generation
                   FROM capability_snapshot_lease_intents
                   WHERE run_id=?
                   ORDER BY lease_generation DESC LIMIT 1""",
                (run_id,),
            )
        )
        previous = previous_rows[0] if previous_rows else None
        if (
            previous is not None
            and str(previous["status"]) != "released"
            and snapshot_ref_schema != "legacy_v1"
        ):
            raise CapabilityStoreConflict(
                "snapshot_lease_intent_active",
                "previous run lease intent is not released",
            )
        lease_generation = (
            1 if previous is None else int(previous["lease_generation"]) + 1
        )
        identity["lease_generation"] = lease_generation
        lease_intent_hash = fingerprint_json(identity)
        now = self._clock()
        await db.execute(
            """INSERT INTO capability_snapshot_lease_intents(
                lease_intent_id,lease_intent_hash,snapshot_ref,
                snapshot_ref_schema,run_id,root_run_id,request_id,turn_id,
                lease_generation,owner_operation_id,lease_owner_kind,
                owner_record_ref,owner_record_hash,start_fingerprint,
                run_catalog_content_stamp,entry_set_hash,
                expected_entry_count,status,prepared_at,bound_at,
                released_at,last_error
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'prepared',?,
                     NULL,NULL,NULL)""",
            (
                lease_intent_hash,
                lease_intent_hash,
                snapshot_ref,
                snapshot_ref_schema,
                run_id,
                root_run_id,
                request_id,
                turn_id,
                lease_generation,
                owner_operation_id,
                lease_owner_kind,
                None,
                None,
                None,
                run_catalog_content_stamp,
                entry_set_hash,
                int(expected_entry_count),
                now,
            ),
        )
        rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_snapshot_lease_intents
                   WHERE lease_intent_id=?""",
                (lease_intent_hash,),
            )
        )
        if not rows:  # pragma: no cover
            raise CapabilityStoreError(
                "snapshot_lease_intent_missing",
                "prepared lease intent disappeared",
            )
        return _snapshot_lease_intent_from_row(rows[0])

    async def prepare_snapshot_lease_intent(
        self,
        **kwargs: Any,
    ) -> CapabilitySnapshotLeaseIntent:
        async with self.write_transaction() as db:
            intent = await self._prepare_snapshot_lease_intent_tx(db, **kwargs)
            await self._put_snapshot_lease_rows_tx(db, intent)
            return intent

    async def _put_snapshot_lease_rows_tx(
        self,
        db: aiosqlite.Connection,
        intent: CapabilitySnapshotLeaseIntent,
    ) -> None:
        catalog_rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_run_catalog_snapshot_entries
                   WHERE run_catalog_content_stamp=?
                   ORDER BY ordinal""",
                (intent.run_catalog_content_stamp,),
            )
        )
        if len(catalog_rows) != intent.expected_entry_count:
            raise CapabilityStoreConflict(
                "snapshot_lease_entry_count_mismatch",
                "catalog entry count differs from the lease intent",
            )
        expected: list[tuple[Any, ...]] = []
        for row in catalog_rows:
            ordinal = int(row["ordinal"])
            lease_entry_id = fingerprint_json(
                {
                    "domain": "capability-lease-entry-v2",
                    "lease_intent_id": intent.lease_intent_id,
                    "entry_ordinal": ordinal,
                }
            )
            expected.append(
                (
                    lease_entry_id,
                    intent.lease_intent_id,
                    intent.snapshot_ref,
                    intent.run_id,
                    intent.root_run_id,
                    intent.run_catalog_content_stamp,
                    ordinal,
                    str(row["entry_kind"]),
                    row["selected_binding_id"],
                    row["selected_owner_key"],
                    row["selected_scope"],
                    row["selected_scope_key"],
                    row["selected_binding_generation"],
                    row["stable_host_binding_id"],
                    str(row["visible_binding_set_hash"]),
                    str(row["descriptor_fingerprint"]),
                    str(row["runtime_descriptor_hash"]),
                    row["pack_id"],
                    row["version"],
                    row["manifest_hash"],
                    row["host_provider_name"],
                    row["host_source"],
                    row["host_spec_version"],
                    row["host_schema_hash"],
                    row["host_content_hash"],
                    row["host_build_identity"],
                    str(row["tool_spec_fingerprints_json"]),
                )
            )
        current = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_snapshot_leases
                   WHERE lease_intent_id=? ORDER BY entry_ordinal""",
                (intent.lease_intent_id,),
            )
        )
        if current:
            actual = [
                (
                    str(row["lease_entry_id"]),
                    str(row["lease_intent_id"]),
                    str(row["snapshot_ref"]),
                    str(row["run_id"]),
                    str(row["root_run_id"]),
                    str(row["run_catalog_content_stamp"]),
                    int(row["entry_ordinal"]),
                    str(row["entry_kind"]),
                    row["selected_binding_id"],
                    row["selected_owner_key"],
                    row["selected_scope"],
                    row["selected_scope_key"],
                    row["selected_binding_generation"],
                    row["stable_host_binding_id"],
                    str(row["visible_binding_set_hash"]),
                    str(row["descriptor_fingerprint"]),
                    str(row["runtime_descriptor_hash"]),
                    row["pack_id"],
                    row["version"],
                    row["manifest_hash"],
                    row["host_provider_name"],
                    row["host_source"],
                    row["host_spec_version"],
                    row["host_schema_hash"],
                    row["host_content_hash"],
                    row["host_build_identity"],
                    str(row["tool_spec_fingerprints_json"]),
                )
                for row in current
            ]
            if actual != expected:
                raise CapabilityStoreConflict(
                    "snapshot_lease_rows_conflict",
                    "stored lease entries differ from immutable catalog entries",
                )
            return
        now = self._clock()
        for row in expected:
            await db.execute(
                """INSERT INTO capability_snapshot_leases(
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
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                         NULL)""",
                (*row, now),
            )

    async def prepare_run_catalog_projection_in_tx(
        self,
        db: aiosqlite.Connection,
        *,
        content: RunCatalogContentStamp,
        process_stamp: ProcessCatalogStamp,
        snapshot_ref: str,
        request_owner_key: str,
        catalog_generation_vector: Mapping[str, int],
        run_id: str,
        root_run_id: str,
        request_id: str,
        turn_id: str,
        owner_operation_id: str,
        lease_owner_kind: str,
        prepared_tool_set_envelope: Mapping[str, JsonValue],
        prepared_tool_set_hash: str,
    ) -> PreparedRunCatalogProjection:
        if process_stamp.run_catalog_content_stamp != content.fingerprint:
            raise CapabilityStoreConflict(
                "process_catalog_stamp_conflict",
                "process stamp belongs to another run catalog",
            )
        if snapshot_ref != content.snapshot_ref:
            raise CapabilityStoreConflict(
                "snapshot_ref_conflict",
                "snapshot ref does not match the run catalog",
            )
        tool_set_json = canonical_json(dict(prepared_tool_set_envelope))
        if fingerprint_json(json.loads(tool_set_json)) != prepared_tool_set_hash:
            raise CapabilityStoreConflict(
                "prepared_tool_set_conflict",
                "prepared tool set hash does not match its envelope",
            )
        await self._put_run_catalog_snapshot_tx(
            db,
            content,
            request_owner_key=request_owner_key,
            catalog_generation_vector=catalog_generation_vector,
            require_complete=True,
        )
        intent = await self._prepare_snapshot_lease_intent_tx(
            db,
            snapshot_ref=snapshot_ref,
            snapshot_ref_schema="run_catalog_v2",
            run_id=run_id,
            root_run_id=root_run_id,
            request_id=request_id,
            turn_id=turn_id,
            owner_operation_id=owner_operation_id,
            lease_owner_kind=lease_owner_kind,
            run_catalog_content_stamp=content.fingerprint,
            entry_set_hash=content.entry_set_hash,
            expected_entry_count=len(content.entries),
        )
        await self._put_snapshot_lease_rows_tx(db, intent)
        receipt_payload = {
            "domain": "runtime-projection-receipt-v1",
            "purpose": "snapshot_pin",
            "lease_intent_id": intent.lease_intent_id,
            "run_catalog_content_stamp": content.fingerprint,
            "process_instance_id": process_stamp.process_instance_id,
            "process_catalog_stamp": process_stamp.fingerprint,
            "prepared_tool_set_hash": prepared_tool_set_hash,
        }
        projection_receipt_id = fingerprint_json(receipt_payload)
        projection_receipt_hash = projection_receipt_id
        existing_rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_runtime_projection_receipts
                   WHERE projection_receipt_id=? OR lease_intent_id=?""",
                (projection_receipt_id, intent.lease_intent_id),
            )
        )
        if existing_rows:
            if len(existing_rows) != 1:
                raise CapabilityStoreConflict(
                    "runtime_projection_receipt_conflict",
                    "lease intent resolves to multiple projection receipts",
                )
            current = _runtime_projection_receipt_from_row(existing_rows[0])
            immutable = (
                current.projection_receipt_id,
                current.projection_receipt_hash,
                current.purpose,
                current.lease_intent_id,
                current.run_catalog_content_stamp,
                current.process_instance_id,
                current.process_catalog_stamp,
                current.prepared_tool_set_hash,
            )
            expected = (
                projection_receipt_id,
                projection_receipt_hash,
                "snapshot_pin",
                intent.lease_intent_id,
                content.fingerprint,
                process_stamp.process_instance_id,
                process_stamp.fingerprint,
                prepared_tool_set_hash,
            )
            if immutable != expected:
                raise CapabilityStoreConflict(
                    "runtime_projection_receipt_conflict",
                    "stored projection receipt differs from immutable request",
                )
            receipt = current
        else:
            now = self._clock()
            await db.execute(
                """INSERT INTO capability_runtime_projection_receipts(
                    projection_receipt_id,projection_receipt_hash,purpose,
                    operation_id,owner_activation_id,lease_activation_id,
                    lease_intent_id,owner_binding_set_stamp,
                    run_catalog_content_stamp,process_instance_id,
                    process_catalog_stamp,process_projection_fingerprint,
                    runtime_set_set_hash,prepared_tool_set_envelope_json,
                    prepared_tool_set_hash,pin_token_hash,status,created_at,
                    ready_at,retired_at
                ) VALUES(?,?,'snapshot_pin',NULL,NULL,NULL,?,NULL,?,?,?,NULL,
                         NULL,?,?,NULL,'prepared',?,NULL,NULL)""",
                (
                    projection_receipt_id,
                    projection_receipt_hash,
                    intent.lease_intent_id,
                    content.fingerprint,
                    process_stamp.process_instance_id,
                    process_stamp.fingerprint,
                    tool_set_json,
                    prepared_tool_set_hash,
                    now,
                ),
            )
            receipt_row = (
                await _fetch_mappings(
                    await db.execute(
                        """SELECT *
                           FROM capability_runtime_projection_receipts
                           WHERE projection_receipt_id=?""",
                        (projection_receipt_id,),
                    )
                )
            )[0]
            receipt = _runtime_projection_receipt_from_row(receipt_row)
        return PreparedRunCatalogProjection(
            intent=intent,
            projection_receipt=receipt,
            process_catalog_stamp=process_stamp.fingerprint,
        )

    async def _bind_snapshot_lease_intent_tx(
        self,
        db: aiosqlite.Connection,
        lease_intent_id: str,
        *,
        owner_record_ref: str,
        owner_record_hash: str,
        start_fingerprint: str,
    ) -> CapabilitySnapshotLeaseIntent:
        rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_snapshot_lease_intents
                   WHERE lease_intent_id=?""",
                (lease_intent_id,),
            )
        )
        if not rows:
            raise CapabilityStoreError(
                "snapshot_lease_intent_missing", "lease intent is absent"
            )
        current = _snapshot_lease_intent_from_row(rows[0])
        expected = (
            owner_record_ref,
            owner_record_hash,
            start_fingerprint,
        )
        actual = (
            current.owner_record_ref,
            current.owner_record_hash,
            current.start_fingerprint,
        )
        if current.status == "bound":
            if actual != expected:
                raise CapabilityStoreConflict(
                    "snapshot_lease_intent_conflict",
                    "bound lease intent owner record differs",
                )
            return current
        if current.status != "prepared":
            raise CapabilityStoreConflict(
                "snapshot_lease_intent_terminal",
                "lease intent is no longer preparable",
            )
        lease_count = int(
            (
                await (
                    await db.execute(
                        """SELECT COUNT(*) FROM capability_snapshot_leases
                           WHERE lease_intent_id=?""",
                        (lease_intent_id,),
                    )
                ).fetchone()
            )[0]
        )
        if lease_count != current.expected_entry_count:
            raise CapabilityStoreConflict(
                "snapshot_lease_entry_count_mismatch",
                "cannot adopt an incomplete snapshot lease",
            )
        cursor = await db.execute(
            """UPDATE capability_snapshot_lease_intents
               SET status='bound',owner_record_ref=?,owner_record_hash=?,
                   start_fingerprint=?,bound_at=?
               WHERE lease_intent_id=? AND status='prepared'""",
            (
                owner_record_ref,
                owner_record_hash,
                start_fingerprint,
                self._clock(),
                lease_intent_id,
            ),
        )
        if cursor.rowcount != 1:
            raise CapabilityStoreConflict(
                "snapshot_lease_intent_conflict",
                "lease intent changed before bind",
            )
        rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_snapshot_lease_intents
                   WHERE lease_intent_id=?""",
                (lease_intent_id,),
            )
        )
        return _snapshot_lease_intent_from_row(rows[0])

    async def bind_snapshot_lease_intent(
        self,
        lease_intent_id: str,
        *,
        owner_record_ref: str,
        owner_record_hash: str,
        start_fingerprint: str,
    ) -> CapabilitySnapshotLeaseIntent:
        async with self.write_transaction() as db:
            return await self._bind_snapshot_lease_intent_tx(
                db,
                lease_intent_id,
                owner_record_ref=owner_record_ref,
                owner_record_hash=owner_record_hash,
                start_fingerprint=start_fingerprint,
            )

    async def adopt_snapshot_lease_intent_in_tx(
        self,
        db: aiosqlite.Connection,
        lease_intent_id: str,
        *,
        intent_hash: str,
        owner_record_ref: str,
        owner_record_hash: str,
        start_fingerprint: str,
    ) -> SnapshotLeaseAdoptionReceipt:
        rows = await _fetch_mappings(
            await db.execute(
                """SELECT lease_intent_hash
                   FROM capability_snapshot_lease_intents
                   WHERE lease_intent_id=?""",
                (lease_intent_id,),
            )
        )
        if not rows or str(rows[0]["lease_intent_hash"]) != intent_hash:
            raise CapabilityStoreConflict(
                "snapshot_lease_intent_conflict",
                "intent hash differs from the prepared lease",
            )
        intent = await self._bind_snapshot_lease_intent_tx(
            db,
            lease_intent_id,
            owner_record_ref=owner_record_ref,
            owner_record_hash=owner_record_hash,
            start_fingerprint=start_fingerprint,
        )
        projection_rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_runtime_projection_receipts
                   WHERE lease_intent_id=? AND purpose='snapshot_pin'""",
                (lease_intent_id,),
            )
        )
        if len(projection_rows) != 1:
            raise CapabilityStoreConflict(
                "runtime_projection_receipt_missing",
                "snapshot pin projection receipt is absent or ambiguous",
            )
        projection = _runtime_projection_receipt_from_row(
            projection_rows[0]
        )
        return SnapshotLeaseAdoptionReceipt(
            intent_id=intent.lease_intent_id,
            intent_hash=intent.lease_intent_hash,
            status=intent.status,
            owner_record_ref=owner_record_ref,
            owner_record_hash=owner_record_hash,
            start_fingerprint=start_fingerprint,
            projection_receipt_id=projection.projection_receipt_id,
            projection_receipt_hash=projection.projection_receipt_hash,
        )

    async def release_snapshot_lease_intent_in_tx(
        self,
        db: aiosqlite.Connection,
        lease_intent_id: str,
        *,
        intent_hash: str,
        release_reason: str,
        owner_terminal_or_transition_ref: str,
        owner_terminal_or_transition_hash: str,
    ) -> CapabilitySnapshotLeaseReleaseReceipt:
        if release_reason not in {
            "terminal",
            "cancel",
            "refresh_replaced",
            "prepared_orphan",
            "revoked",
            "legacy_terminal",
        }:
            raise CapabilityStoreError(
                "invalid_snapshot_release_reason",
                "unknown snapshot lease release reason",
            )
        intent_rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_snapshot_lease_intents
                   WHERE lease_intent_id=?""",
                (lease_intent_id,),
            )
        )
        if not intent_rows:
            raise CapabilityStoreError(
                "snapshot_lease_intent_missing", "lease intent is absent"
            )
        intent = _snapshot_lease_intent_from_row(intent_rows[0])
        if intent.lease_intent_hash != intent_hash:
            raise CapabilityStoreConflict(
                "snapshot_lease_intent_conflict",
                "intent hash differs from the prepared lease",
            )
        receipt_payload = {
            "domain": "snapshot-lease-release-receipt-v1",
            "lease_intent_id": lease_intent_id,
            "intent_hash": intent_hash,
            "run_id": intent.run_id,
            "snapshot_ref": intent.snapshot_ref,
            "release_reason": release_reason,
            "owner_terminal_or_transition_ref": (
                owner_terminal_or_transition_ref
            ),
            "owner_terminal_or_transition_hash": (
                owner_terminal_or_transition_hash
            ),
        }
        release_receipt_id = fingerprint_json(receipt_payload)
        release_receipt_hash = release_receipt_id
        existing_rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_snapshot_lease_release_receipts
                   WHERE lease_intent_id=? OR release_receipt_id=?""",
                (lease_intent_id, release_receipt_id),
            )
        )
        if existing_rows:
            if len(existing_rows) != 1:
                raise CapabilityStoreConflict(
                    "snapshot_release_receipt_conflict",
                    "lease intent resolves to multiple release receipts",
                )
            existing = _snapshot_release_receipt_from_row(existing_rows[0])
            immutable = (
                existing.release_receipt_id,
                existing.release_receipt_hash,
                existing.release_reason,
                existing.owner_terminal_or_transition_ref,
                existing.owner_terminal_or_transition_hash,
            )
            expected = (
                release_receipt_id,
                release_receipt_hash,
                release_reason,
                owner_terminal_or_transition_ref,
                owner_terminal_or_transition_hash,
            )
            if immutable != expected:
                raise CapabilityStoreConflict(
                    "snapshot_release_receipt_conflict",
                    "lease intent was released by another owner transition",
                )
            return existing
        if intent.status not in {"prepared", "bound"}:
            raise CapabilityStoreConflict(
                "snapshot_lease_intent_terminal",
                "lease intent cannot be released from its current status",
            )
        now = self._clock()
        if intent.status == "prepared":
            prepared_release_fingerprint = fingerprint_json(
                {
                    "domain": "prepared-lease-release-v1",
                    "lease_intent_id": lease_intent_id,
                    "owner_transition_hash": (
                        owner_terminal_or_transition_hash
                    ),
                }
            )
            owner_record_ref = owner_terminal_or_transition_ref
            owner_record_hash = owner_terminal_or_transition_hash
            start_fingerprint = prepared_release_fingerprint
        else:
            owner_record_ref = intent.owner_record_ref
            owner_record_hash = intent.owner_record_hash
            start_fingerprint = intent.start_fingerprint
        cursor = await db.execute(
            """UPDATE capability_snapshot_lease_intents
               SET status='released',owner_record_ref=?,owner_record_hash=?,
                   start_fingerprint=?,released_at=?
               WHERE lease_intent_id=? AND status IN ('prepared','bound')""",
            (
                owner_record_ref,
                owner_record_hash,
                start_fingerprint,
                now,
                lease_intent_id,
            ),
        )
        if cursor.rowcount != 1:
            raise CapabilityStoreConflict(
                "snapshot_lease_intent_conflict",
                "lease intent changed before release",
            )
        await db.execute(
            """UPDATE capability_snapshot_leases SET released_at=?
               WHERE lease_intent_id=? AND released_at IS NULL""",
            (now, lease_intent_id),
        )
        active_count = int(
            (
                await (
                    await db.execute(
                        """SELECT COUNT(*)
                           FROM capability_snapshot_lease_intents
                           WHERE snapshot_ref=? AND lease_intent_id<>?
                             AND status IN ('prepared','bound')""",
                        (intent.snapshot_ref, lease_intent_id),
                    )
                ).fetchone()
            )[0]
        )
        last_active = active_count == 0
        cleanup_ticket_ref = (
            f"capability-cleanup:{intent.snapshot_ref}:"
            f"{'last' if last_active else lease_intent_id}"
        )
        cleanup_ticket_hash = fingerprint_json(
            {
                "cleanup_ticket_ref": cleanup_ticket_ref,
                "lease_intent_id": lease_intent_id,
                "last_active_member_for_snapshot": last_active,
            }
        )
        await db.execute(
            """INSERT INTO capability_snapshot_lease_release_receipts(
                release_receipt_id,release_receipt_hash,lease_intent_id,
                run_id,snapshot_ref,release_reason,
                owner_terminal_or_transition_ref,
                owner_terminal_or_transition_hash,
                last_active_member_for_snapshot,cleanup_ticket_ref,
                cleanup_ticket_hash,cleanup_status,created_at,cleaned_at,
                last_error
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending',?,NULL,NULL)""",
            (
                release_receipt_id,
                release_receipt_hash,
                lease_intent_id,
                intent.run_id,
                intent.snapshot_ref,
                release_reason,
                owner_terminal_or_transition_ref,
                owner_terminal_or_transition_hash,
                int(last_active),
                cleanup_ticket_ref,
                cleanup_ticket_hash,
                now,
            ),
        )
        await db.execute(
            """UPDATE capability_runtime_projection_receipts
               SET status='retired',retired_at=?
               WHERE lease_intent_id=? AND status IN ('prepared','ready')""",
            (now, lease_intent_id),
        )
        rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_snapshot_lease_release_receipts
                   WHERE release_receipt_id=?""",
                (release_receipt_id,),
            )
        )
        return _snapshot_release_receipt_from_row(rows[0])

    async def release_snapshot_lease_intent(
        self, lease_intent_id: str
    ) -> CapabilitySnapshotLeaseIntent:
        async with self.write_transaction() as db:
            rows = await _fetch_mappings(
                await db.execute(
                    """SELECT * FROM capability_snapshot_lease_intents
                       WHERE lease_intent_id=?""",
                    (lease_intent_id,),
                )
            )
            if not rows:
                raise CapabilityStoreError(
                    "snapshot_lease_intent_missing", "lease intent is absent"
                )
            current = _snapshot_lease_intent_from_row(rows[0])
            await self.release_snapshot_lease_intent_in_tx(
                db,
                lease_intent_id,
                intent_hash=current.lease_intent_hash,
                release_reason="cancel",
                owner_terminal_or_transition_ref=(
                    f"legacy-release:{lease_intent_id}"
                ),
                owner_terminal_or_transition_hash=fingerprint_json(
                    {"legacy_release": lease_intent_id}
                ),
            )
            rows = await _fetch_mappings(
                await db.execute(
                    """SELECT * FROM capability_snapshot_lease_intents
                       WHERE lease_intent_id=?""",
                    (lease_intent_id,),
                )
            )
            return _snapshot_lease_intent_from_row(rows[0])

    async def activate_snapshot_projection_ready(
        self,
        lease_intent_id: str,
        *,
        intent_hash: str,
        start_fingerprint: str,
        process_instance_id: str,
        process_catalog_stamp: str,
        pin_token_hash: str,
    ) -> CapabilityRuntimeProjectionReceipt:
        async with self.write_transaction() as db:
            intent_rows = await _fetch_mappings(
                await db.execute(
                    """SELECT * FROM capability_snapshot_lease_intents
                       WHERE lease_intent_id=?""",
                    (lease_intent_id,),
                )
            )
            if not intent_rows:
                raise CapabilityStoreError(
                    "snapshot_lease_intent_missing", "lease intent is absent"
                )
            intent = _snapshot_lease_intent_from_row(intent_rows[0])
            if (
                intent.lease_intent_hash != intent_hash
                or intent.status != "bound"
                or intent.start_fingerprint != start_fingerprint
            ):
                raise CapabilityStoreConflict(
                    "snapshot_projection_not_bound",
                    "snapshot intent is not the expected bound owner record",
                )
            projection_rows = await _fetch_mappings(
                await db.execute(
                    """SELECT * FROM capability_runtime_projection_receipts
                       WHERE lease_intent_id=? AND purpose='snapshot_pin'""",
                    (lease_intent_id,),
                )
            )
            if len(projection_rows) != 1:
                raise CapabilityStoreConflict(
                    "runtime_projection_receipt_missing",
                    "snapshot pin projection receipt is absent or ambiguous",
                )
            current = _runtime_projection_receipt_from_row(
                projection_rows[0]
            )
            expected_process = (
                process_instance_id,
                process_catalog_stamp,
            )
            actual_process = (
                current.process_instance_id,
                current.process_catalog_stamp,
            )
            if actual_process != expected_process:
                raise CapabilityStoreConflict(
                    "runtime_projection_receipt_conflict",
                    "projection receipt belongs to another process catalog",
                )
            if current.status == "ready":
                if current.pin_token_hash != pin_token_hash:
                    raise CapabilityStoreConflict(
                        "runtime_projection_receipt_conflict",
                        "ready projection pin token differs",
                    )
                return current
            if current.status != "prepared":
                raise CapabilityStoreConflict(
                    "runtime_projection_receipt_terminal",
                    "projection receipt cannot become ready",
                )
            await db.execute(
                """UPDATE capability_runtime_projection_receipts
                   SET status='ready',pin_token_hash=?,ready_at=?
                   WHERE projection_receipt_id=? AND status='prepared'""",
                (
                    pin_token_hash,
                    self._clock(),
                    current.projection_receipt_id,
                ),
            )
            projection_rows = await _fetch_mappings(
                await db.execute(
                    """SELECT * FROM capability_runtime_projection_receipts
                       WHERE projection_receipt_id=?""",
                    (current.projection_receipt_id,),
                )
            )
            return _runtime_projection_receipt_from_row(projection_rows[0])

    async def read_snapshot_projection_state(
        self, lease_intent_id: str
    ) -> SnapshotProjectionState | None:
        async with self.read_connection() as db:
            rows = await _fetch_mappings(
                await db.execute(
                    """SELECT intent.lease_intent_id,intent.lease_intent_hash,
                              intent.status AS intent_status,
                              intent.start_fingerprint,
                              intent.run_catalog_content_stamp,
                              projection.process_instance_id,
                              projection.process_catalog_stamp,
                              projection.status AS projection_status,
                              projection.projection_receipt_id,
                              projection.projection_receipt_hash,
                              projection.pin_token_hash
                       FROM capability_snapshot_lease_intents AS intent
                       JOIN capability_runtime_projection_receipts AS projection
                         ON projection.lease_intent_id=intent.lease_intent_id
                        AND projection.purpose='snapshot_pin'
                       WHERE intent.lease_intent_id=?""",
                    (lease_intent_id,),
                )
            )
            if not rows:
                return None
            if len(rows) != 1:
                raise CapabilityStoreConflict(
                    "runtime_projection_receipt_conflict",
                    "snapshot intent has multiple projection receipts",
                )
            row = rows[0]
            return SnapshotProjectionState(
                lease_intent_id=str(row["lease_intent_id"]),
                lease_intent_hash=str(row["lease_intent_hash"]),
                intent_status=str(row["intent_status"]),
                start_fingerprint=(
                    None
                    if row["start_fingerprint"] is None
                    else str(row["start_fingerprint"])
                ),
                run_catalog_content_stamp=str(
                    row["run_catalog_content_stamp"]
                ),
                process_instance_id=str(row["process_instance_id"]),
                process_catalog_stamp=str(row["process_catalog_stamp"]),
                projection_status=str(row["projection_status"]),
                projection_receipt_id=str(row["projection_receipt_id"]),
                projection_receipt_hash=str(
                    row["projection_receipt_hash"]
                ),
                pin_token_hash=(
                    None
                    if row["pin_token_hash"] is None
                    else str(row["pin_token_hash"])
                ),
            )

    @staticmethod
    def _runtime_set_fingerprint(
        *,
        operation_id: str,
        purpose: str,
        lifecycle_action: str | None,
        owner_activation_id: str | None,
        lease_activation_id: str | None,
        target_pack_id: str | None,
        target_version: str | None,
        owner_binding_set_stamp: str | None,
        run_catalog_content_stamp: str | None,
        target_package_hash: str | None,
        target_manifest_hash: str | None,
        authorization_hash: str | None,
        launch_revocation_epoch: int,
        instances: Sequence[CapabilityRuntimeInstanceSpec],
    ) -> str:
        return fingerprint_json(
            {
                "domain": "capability-runtime-set-v1",
                "operation_id": operation_id,
                "purpose": purpose,
                "lifecycle_action": lifecycle_action,
                "owner_activation_id": owner_activation_id,
                "lease_activation_id": lease_activation_id,
                "target_pack_id": target_pack_id,
                "target_version": target_version,
                "owner_binding_set_stamp": owner_binding_set_stamp,
                "run_catalog_content_stamp": run_catalog_content_stamp,
                "target_package_hash": target_package_hash,
                "target_manifest_hash": target_manifest_hash,
                "authorization_hash": authorization_hash,
                "launch_revocation_epoch": launch_revocation_epoch,
                "instances": [
                    item.to_dict()
                    for item in sorted(
                        instances, key=lambda item: (item.ordinal, item.entry_id)
                    )
                ],
            }
        )

    def _validate_runtime_set_integrity(
        self, record: CapabilityRuntimeSetRecord
    ) -> tuple[CapabilityRuntimeInstanceSpec, ...]:
        specs = tuple(
            CapabilityRuntimeInstanceSpec(
                runtime_instance_id=item.runtime_instance_id,
                entry_id=item.entry_id,
                ordinal=item.ordinal,
                runtime_kind=item.runtime_kind,
                adapter_fingerprint=item.adapter_fingerprint,
                argv_hash=item.argv_hash,
                env_scope_hash=item.env_scope_hash,
                workdir=item.workdir,
            )
            for item in record.instances
        )
        recomputed = self._runtime_set_fingerprint(
            operation_id=record.operation_id,
            purpose=record.purpose,
            lifecycle_action=record.lifecycle_action,
            owner_activation_id=record.owner_activation_id,
            lease_activation_id=record.lease_activation_id,
            target_pack_id=record.target_pack_id,
            target_version=record.target_version,
            owner_binding_set_stamp=record.owner_binding_set_stamp,
            run_catalog_content_stamp=record.run_catalog_content_stamp,
            target_package_hash=record.target_package_hash,
            target_manifest_hash=record.target_manifest_hash,
            authorization_hash=record.authorization_hash,
            launch_revocation_epoch=record.launch_revocation_epoch,
            instances=specs,
        )
        ordered_ordinals = tuple(item.ordinal for item in specs)
        if (
            len(specs) != record.expected_instance_count
            or ordered_ordinals != tuple(range(len(specs)))
            or len({item.entry_id for item in specs}) != len(specs)
            or len({item.runtime_instance_id for item in specs}) != len(specs)
            or recomputed != record.runtime_set_hash
            or any(
                item.runtime_set_hash != record.runtime_set_hash
                for item in record.instances
            )
        ):
            raise CapabilityStoreConflict(
                "runtime_set_incomplete",
                "runtime set header and members fail count/hash validation",
            )
        return specs

    async def _get_runtime_set_tx(
        self, db: aiosqlite.Connection, operation_id: str
    ) -> CapabilityRuntimeSetRecord | None:
        headers = await _fetch_mappings(
            await db.execute(
                "SELECT * FROM capability_runtime_sets WHERE operation_id=?",
                (operation_id,),
            )
        )
        if not headers:
            return None
        members = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_runtime_prepare_intents
                   WHERE operation_id=?
                   ORDER BY ordinal,entry_id,runtime_instance_id""",
                (operation_id,),
            )
        )
        return _runtime_set_from_rows(headers[0], members)

    async def _prepare_runtime_set_tx(
        self,
        db: aiosqlite.Connection,
        *,
        operation_id: str,
        purpose: str,
        lifecycle_action: str | None,
        owner_activation_id: str | None,
        lease_activation_id: str | None,
        target_pack_id: str | None,
        target_version: str | None,
        owner_binding_set_stamp: str | None,
        run_catalog_content_stamp: str | None,
        target_package_hash: str | None,
        target_manifest_hash: str | None,
        authorization_hash: str | None,
        launch_revocation_epoch: int,
        instances: Sequence[CapabilityRuntimeInstanceSpec],
        runtime_set_hash: str | None = None,
    ) -> CapabilityRuntimeSetRecord:
        if not operation_id:
            raise CapabilityStoreError(
                "invalid_runtime_set", "operation id is required"
            )
        if not isinstance(launch_revocation_epoch, int) or launch_revocation_epoch < 0:
            raise CapabilityStoreError(
                "invalid_runtime_set", "launch revocation epoch is invalid"
            )
        if purpose == "mutation":
            valid_shape = (
                lifecycle_action is not None
                and owner_activation_id is None
                and lease_activation_id is None
                and owner_binding_set_stamp is not None
                and run_catalog_content_stamp is None
            )
        elif purpose == "owner_rehydrate":
            valid_shape = (
                lifecycle_action is None
                and owner_activation_id is not None
                and lease_activation_id is None
                and owner_binding_set_stamp is not None
                and run_catalog_content_stamp is None
            )
        elif purpose == "lease_rehydrate":
            valid_shape = (
                lifecycle_action is None
                and owner_activation_id is None
                and lease_activation_id is not None
                and owner_binding_set_stamp is None
                and run_catalog_content_stamp is not None
            )
        else:
            valid_shape = False
        if not valid_shape:
            raise CapabilityStoreError(
                "invalid_runtime_set",
                "runtime set purpose and authority references disagree",
            )
        for name, value in (
            ("owner_binding_set_stamp", owner_binding_set_stamp),
            ("run_catalog_content_stamp", run_catalog_content_stamp),
            ("target_package_hash", target_package_hash),
            ("target_manifest_hash", target_manifest_hash),
            ("authorization_hash", authorization_hash),
        ):
            if value is not None:
                try:
                    _validated_digest(value, name)
                except ValueError as exc:
                    raise CapabilityStoreError(
                        "invalid_runtime_set", str(exc)
                    ) from exc
        ordered = tuple(
            sorted(tuple(instances), key=lambda item: (item.ordinal, item.entry_id))
        )
        if (
            len({item.runtime_instance_id for item in ordered}) != len(ordered)
            or len({item.entry_id for item in ordered}) != len(ordered)
            or len({item.ordinal for item in ordered}) != len(ordered)
            or tuple(item.ordinal for item in ordered) != tuple(range(len(ordered)))
        ):
            raise CapabilityStoreError(
                "invalid_runtime_set",
                "runtime instances require unique contiguous ordinals and identities",
            )
        expected_hash = self._runtime_set_fingerprint(
            operation_id=operation_id,
            purpose=purpose,
            lifecycle_action=lifecycle_action,
            owner_activation_id=owner_activation_id,
            lease_activation_id=lease_activation_id,
            target_pack_id=target_pack_id,
            target_version=target_version,
            owner_binding_set_stamp=owner_binding_set_stamp,
            run_catalog_content_stamp=run_catalog_content_stamp,
            target_package_hash=target_package_hash,
            target_manifest_hash=target_manifest_hash,
            authorization_hash=authorization_hash,
            launch_revocation_epoch=launch_revocation_epoch,
            instances=ordered,
        )
        if runtime_set_hash is not None and runtime_set_hash != expected_hash:
            raise CapabilityStoreConflict(
                "runtime_set_hash_mismatch",
                "runtime set hash does not match its immutable members",
            )
        current = await self._get_runtime_set_tx(db, operation_id)
        if current is not None:
            current_specs = tuple(
                CapabilityRuntimeInstanceSpec(
                    runtime_instance_id=item.runtime_instance_id,
                    entry_id=item.entry_id,
                    ordinal=item.ordinal,
                    runtime_kind=item.runtime_kind,
                    adapter_fingerprint=item.adapter_fingerprint,
                    argv_hash=item.argv_hash,
                    env_scope_hash=item.env_scope_hash,
                    workdir=item.workdir,
                )
                for item in current.instances
            )
            immutable = (
                current.purpose,
                current.lifecycle_action,
                current.owner_activation_id,
                current.lease_activation_id,
                current.target_pack_id,
                current.target_version,
                current.owner_binding_set_stamp,
                current.run_catalog_content_stamp,
                current.target_package_hash,
                current.target_manifest_hash,
                current.runtime_set_hash,
                current.expected_instance_count,
                current.authorization_hash,
                current.launch_revocation_epoch,
                current_specs,
            )
            expected = (
                purpose,
                lifecycle_action,
                owner_activation_id,
                lease_activation_id,
                target_pack_id,
                target_version,
                owner_binding_set_stamp,
                run_catalog_content_stamp,
                target_package_hash,
                target_manifest_hash,
                expected_hash,
                len(ordered),
                authorization_hash,
                launch_revocation_epoch,
                ordered,
            )
            if immutable != expected:
                raise CapabilityStoreConflict(
                    "runtime_set_conflict",
                    "stored runtime set differs from immutable request",
                )
            return current
        now = self._clock()
        await db.execute(
            """INSERT INTO capability_runtime_sets(
                operation_id,purpose,lifecycle_action,owner_activation_id,
                lease_activation_id,target_pack_id,target_version,
                owner_binding_set_stamp,run_catalog_content_stamp,
                target_package_hash,target_manifest_hash,runtime_set_hash,
                expected_instance_count,authorization_hash,
                launch_revocation_epoch,status,created_at,updated_at,last_error
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'prepared',?,?,NULL)""",
            (
                operation_id,
                purpose,
                lifecycle_action,
                owner_activation_id,
                lease_activation_id,
                target_pack_id,
                target_version,
                owner_binding_set_stamp,
                run_catalog_content_stamp,
                target_package_hash,
                target_manifest_hash,
                expected_hash,
                len(ordered),
                authorization_hash,
                launch_revocation_epoch,
                now,
                now,
            ),
        )
        for item in ordered:
            await db.execute(
                """INSERT INTO capability_runtime_prepare_intents(
                    operation_id,runtime_instance_id,entry_id,ordinal,
                    runtime_kind,status,runtime_set_hash,authorization_hash,
                    launch_revocation_epoch,adapter_fingerprint,argv_hash,
                    env_scope_hash,workdir,job_identity,pid,
                    process_create_time,session_identity,start_outcome,
                    start_ack_at,health_outcome_hash,started_at,updated_at,
                    last_error
                ) VALUES(?,?,?,?,?,'prepared',?,?,?,?,?,?,?,NULL,NULL,NULL,
                         NULL,NULL,NULL,NULL,NULL,?,NULL)""",
                (
                    operation_id,
                    item.runtime_instance_id,
                    item.entry_id,
                    item.ordinal,
                    item.runtime_kind,
                    expected_hash,
                    authorization_hash,
                    launch_revocation_epoch,
                    item.adapter_fingerprint,
                    item.argv_hash,
                    item.env_scope_hash,
                    item.workdir,
                    now,
                ),
            )
        prepared = await self._get_runtime_set_tx(db, operation_id)
        if prepared is None:  # pragma: no cover
            raise CapabilityStoreError(
                "runtime_set_missing", "prepared runtime set disappeared"
            )
        return prepared

    async def prepare_runtime_set(
        self, **kwargs: Any
    ) -> CapabilityRuntimeSetRecord:
        async with self.write_transaction() as db:
            return await self._prepare_runtime_set_tx(db, **kwargs)

    async def get_runtime_set(
        self, operation_id: str
    ) -> CapabilityRuntimeSetRecord | None:
        async with self.read_connection() as db:
            return await self._get_runtime_set_tx(db, operation_id)

    async def _mark_runtime_instance_ready_tx(
        self,
        db: aiosqlite.Connection,
        operation_id: str,
        runtime_instance_id: str,
        *,
        job_identity: str,
        pid: int,
        process_create_time: float,
        session_identity: str,
        start_outcome: str,
        start_ack_at: float,
        health_outcome_hash: str,
        started_at: float,
    ) -> CapabilityRuntimePrepareIntent:
        _validated_digest(health_outcome_hash, "health_outcome_hash")
        rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_runtime_prepare_intents
                   WHERE operation_id=? AND runtime_instance_id=?""",
                (operation_id, runtime_instance_id),
            )
        )
        if not rows:
            raise CapabilityStoreError(
                "runtime_instance_missing", "runtime instance is absent"
            )
        current = _runtime_prepare_intent_from_row(rows[0])
        expected_runtime = (
            job_identity,
            pid,
            float(process_create_time),
            session_identity,
            start_outcome,
            float(start_ack_at),
            health_outcome_hash,
            float(started_at),
        )
        actual_runtime = (
            current.job_identity,
            current.pid,
            current.process_create_time,
            current.session_identity,
            current.start_outcome,
            current.start_ack_at,
            current.health_outcome_hash,
            current.started_at,
        )
        if current.status in {"health_passed", "activated"}:
            if actual_runtime != expected_runtime:
                raise CapabilityStoreConflict(
                    "runtime_instance_conflict",
                    "ready runtime identity differs from immutable receipt",
                )
            return current
        if current.status not in {"prepared", "launch_claimed", "started"}:
            raise CapabilityStoreConflict(
                "runtime_instance_terminal",
                "runtime instance cannot become ready from its current state",
            )
        await db.execute(
            """UPDATE capability_runtime_prepare_intents
               SET status='health_passed',job_identity=?,pid=?,
                   process_create_time=?,session_identity=?,start_outcome=?,
                   start_ack_at=?,health_outcome_hash=?,started_at=?,
                   updated_at=?,last_error=NULL
               WHERE operation_id=? AND runtime_instance_id=?""",
            (
                *expected_runtime,
                self._clock(),
                operation_id,
                runtime_instance_id,
            ),
        )
        rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_runtime_prepare_intents
                   WHERE operation_id=? AND runtime_instance_id=?""",
                (operation_id, runtime_instance_id),
            )
        )
        return _runtime_prepare_intent_from_row(rows[0])

    async def mark_runtime_instance_ready(
        self, operation_id: str, runtime_instance_id: str, **kwargs: Any
    ) -> CapabilityRuntimePrepareIntent:
        async with self.write_transaction() as db:
            return await self._mark_runtime_instance_ready_tx(
                db, operation_id, runtime_instance_id, **kwargs
            )

    async def _mark_runtime_set_ready_tx(
        self, db: aiosqlite.Connection, operation_id: str
    ) -> CapabilityRuntimeSetRecord:
        current = await self._get_runtime_set_tx(db, operation_id)
        if current is None:
            raise CapabilityStoreError(
                "runtime_set_missing", "runtime set is absent"
            )
        self._validate_runtime_set_integrity(current)
        if current.status == "activated":
            return current
        if current.status not in {"prepared", "launching", "started", "health_passed"}:
            raise CapabilityStoreConflict(
                "runtime_set_terminal",
                "runtime set cannot become ready from its current state",
            )
        if any(item.status != "health_passed" for item in current.instances):
            raise CapabilityStoreConflict(
                "runtime_set_not_ready",
                "all runtime instances must pass health before activation",
            )
        now = self._clock()
        await db.execute(
            """UPDATE capability_runtime_prepare_intents
               SET status='activated',updated_at=?
               WHERE operation_id=? AND status='health_passed'""",
            (now, operation_id),
        )
        await db.execute(
            """UPDATE capability_runtime_sets
               SET status='activated',updated_at=?,last_error=NULL
               WHERE operation_id=?""",
            (now, operation_id),
        )
        ready = await self._get_runtime_set_tx(db, operation_id)
        assert ready is not None
        return ready

    async def mark_runtime_set_ready(
        self, operation_id: str
    ) -> CapabilityRuntimeSetRecord:
        async with self.write_transaction() as db:
            return await self._mark_runtime_set_ready_tx(db, operation_id)

    async def _retire_runtime_set_tx(
        self, db: aiosqlite.Connection, operation_id: str
    ) -> CapabilityRuntimeSetRecord:
        current = await self._get_runtime_set_tx(db, operation_id)
        if current is None:
            raise CapabilityStoreError(
                "runtime_set_missing", "runtime set is absent"
            )
        if current.status == "aborted":
            return current
        if current.status == "cleanup_required":
            raise CapabilityStoreConflict(
                "runtime_set_cleanup_required",
                "runtime set requires explicit cleanup reconciliation",
            )
        now = self._clock()
        await db.execute(
            """UPDATE capability_runtime_prepare_intents
               SET status='aborted',updated_at=?
               WHERE operation_id=? AND status!='cleanup_required'""",
            (now, operation_id),
        )
        await db.execute(
            """UPDATE capability_runtime_sets
               SET status='aborted',updated_at=?,last_error=NULL
               WHERE operation_id=?""",
            (now, operation_id),
        )
        retired = await self._get_runtime_set_tx(db, operation_id)
        assert retired is not None
        return retired

    async def retire_runtime_set(
        self, operation_id: str
    ) -> CapabilityRuntimeSetRecord:
        async with self.write_transaction() as db:
            return await self._retire_runtime_set_tx(db, operation_id)

    async def list_runtime_sets_for_recovery(
        self,
    ) -> tuple[CapabilityRuntimeSetRecord, ...]:
        async with self.read_connection() as db:
            rows = await _fetch_mappings(
                await db.execute(
                    """SELECT operation_id FROM capability_runtime_sets
                       WHERE status IN (
                           'prepared','launching','started','health_passed',
                           'not_started','unknown','aborting','cleanup_required'
                       )
                       ORDER BY created_at,operation_id"""
                )
            )
            records: list[CapabilityRuntimeSetRecord] = []
            for row in rows:
                record = await self._get_runtime_set_tx(
                    db, str(row["operation_id"])
                )
                if record is not None:
                    self._validate_runtime_set_integrity(record)
                    records.append(record)
            return tuple(records)

    async def list_active_versions(
        self, *, owner_key: str | None = None
    ) -> tuple[CapabilityVersionRecord, ...]:
        """Return each immutable version selected by any active binding once."""

        async with self.read_connection() as db:
            owner_clause = "" if owner_key is None else "AND b.owner_key=?"
            parameters: tuple[Any, ...] = (
                () if owner_key is None else (owner_key,)
            )
            rows = await (
                await db.execute(
                    f"""SELECT DISTINCT v.*
                       FROM capability_versions AS v
                       JOIN capability_bindings AS b
                         ON b.pack_id=v.pack_id
                        AND b.active_version=v.version
                        AND b.active_manifest_hash=v.manifest_hash
                       WHERE b.enabled=1 {owner_clause}
                       ORDER BY v.pack_id,v.version,v.manifest_hash""",
                    parameters,
                )
            ).fetchall()
        return tuple(_version_from_row(row) for row in rows)

    async def visible_entries(
        self, scope: CapabilityScope, *, owner_key: str | None = None
    ) -> tuple[CapabilityCatalogEntry, ...]:
        keys = scope.binding_keys()
        owner_keys = tuple(
            (
                "builtin"
                if scope_name == "builtin"
                else str(owner_key or LEGACY_LOCAL_OWNER_KEY)
            )
            for scope_name, _scope_key in keys
        )
        clauses = " OR ".join(
            "(b.owner_key=? AND b.scope=? AND b.scope_key=?)" for _ in keys
        )
        params = tuple(
            value
            for owner, item in zip(owner_keys, keys, strict=True)
            for value in (owner, *item)
        )
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    f"""SELECT b.*,v.descriptor_json,
                        v.expected_tool_fingerprints_json
                        FROM capability_bindings b
                        JOIN capability_versions v
                          ON v.pack_id=b.pack_id
                         AND v.version=b.active_version
                         AND v.manifest_hash=b.active_manifest_hash
                        WHERE b.enabled=1 AND ({clauses})
                        ORDER BY b.pack_id,b.scope,b.generation""",
                    params,
                )
            ).fetchall()
        grouped: dict[
            tuple[str, str, str],
            tuple[CapabilityVersionDescriptor, list[CapabilityBinding], tuple[str, ...]],
        ] = {}
        for row in rows:
            descriptor = _descriptor_from_json(str(row["descriptor_json"]))
            key = (
                descriptor.capability_id,
                descriptor.version,
                descriptor.manifest_hash,
            )
            fingerprints = tuple(
                str(item)
                for item in _json_array(
                    str(row["expected_tool_fingerprints_json"])
                )
            )
            if key not in grouped:
                grouped[key] = (descriptor, [], fingerprints)
            grouped[key][1].append(_binding_from_row(row))
        return tuple(
            CapabilityCatalogEntry(
                version=descriptor,
                bindings=tuple(bindings),
                expected_tool_fingerprints=fingerprints,
            )
            for descriptor, bindings, fingerprints in grouped.values()
        )

    async def _create_operation_tx(
        self,
        db: aiosqlite.Connection,
        *,
        operation_id: str,
        idempotency_key: str,
        kind: str,
        request: Mapping[str, JsonValue],
        root_run_id: str | None = None,
        pack_id: str | None = None,
        requested_scope: str | None = None,
        requested_scope_key: str | None = None,
    ) -> CapabilityOperationRecord:
        if kind not in {
            "activate",
            "install",
            "update",
            "build",
            "repair",
            "rollback",
            "uninstall",
            "skill_install_batch",
        }:
            raise CapabilityStoreError(
                "invalid_operation_kind", f"unknown operation kind: {kind}"
            )
        request_json = canonical_json(dict(request))
        existing = await (
            await db.execute(
                """SELECT * FROM capability_operations
                   WHERE operation_id=? OR idempotency_key=?""",
                (operation_id, idempotency_key),
            )
        ).fetchone()
        if existing is not None:
            record = _operation_from_row(existing)
            # Install requests may discover their pack_id only after the
            # staged manifest validates.  Binding that discovered identity to
            # the durable operation must not make the original request cease
            # to be idempotent when it intentionally supplied pack_id=None.
            pack_identity_matches = (
                record.pack_id == pack_id
                or (
                    pack_id is None
                    and record.request.get("expected_pack_id") is None
                )
            )
            if (
                record.operation_id != operation_id
                or record.idempotency_key != idempotency_key
                or record.kind != kind
                or _operation_identity_request_json(record.request)
                != _operation_identity_request_json(request)
                or record.root_run_id != root_run_id
                or not pack_identity_matches
                or record.requested_scope != requested_scope
                or record.requested_scope_key != requested_scope_key
            ):
                raise CapabilityStoreConflict(
                    "operation_idempotency_conflict",
                    "operation identity already belongs to another request",
                )
            return record
        now = self._clock()
        initial_phase = "batch_staged" if kind == "skill_install_batch" else "planned"
        await db.execute(
            """INSERT INTO capability_operations(
                operation_id,idempotency_key,root_run_id,kind,pack_id,
                requested_scope,requested_scope_key,phase,status,request_json,
                error_json,started_at,updated_at,ended_at
            ) VALUES(?,?,?,?,?,?,?,?, 'running',?,NULL,?,?,NULL)""",
            (
                operation_id,
                idempotency_key,
                root_run_id,
                kind,
                pack_id,
                requested_scope,
                requested_scope_key,
                initial_phase,
                request_json,
                now,
                now,
            ),
        )
        row = await (
            await db.execute(
                "SELECT * FROM capability_operations WHERE operation_id=?",
                (operation_id,),
            )
        ).fetchone()
        return _operation_from_row(row)

    async def create_operation(self, **kwargs: Any) -> CapabilityOperationRecord:
        async with self.write_transaction() as db:
            return await self._create_operation_tx(db, **kwargs)

    async def get_operation(
        self, operation_id: str
    ) -> CapabilityOperationRecord | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM capability_operations WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            return None if row is None else _operation_from_row(row)

    async def _bind_operation_pack_id_tx(
        self, db: aiosqlite.Connection, operation_id: str, pack_id: str
    ) -> CapabilityOperationRecord:
        row = await (
            await db.execute(
                "SELECT * FROM capability_operations WHERE operation_id=?",
                (operation_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError("operation_not_found", operation_id)
        operation = _operation_from_row(row)
        if operation.pack_id is not None and operation.pack_id != pack_id:
            raise CapabilityStoreConflict(
                "operation_pack_conflict",
                "operation is already bound to another capability",
            )
        if operation.pack_id is None:
            await db.execute(
                """UPDATE capability_operations SET pack_id=?,updated_at=?
                   WHERE operation_id=? AND pack_id IS NULL""",
                (pack_id, self._clock(), operation_id),
            )
        current = await self.get_operation_from_db(db, operation_id)
        if current is None:  # pragma: no cover
            raise CapabilityStoreError("operation_not_found", operation_id)
        return current

    async def bind_operation_pack_id(
        self, operation_id: str, pack_id: str
    ) -> CapabilityOperationRecord:
        async with self.write_transaction() as db:
            return await self._bind_operation_pack_id_tx(db, operation_id, pack_id)

    async def get_phase_evidence(
        self, operation_id: str, phase: str
    ) -> Mapping[str, JsonValue] | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT status,evidence_json
                       FROM capability_operation_phase_evidence
                       WHERE operation_id=? AND phase=?""",
                    (operation_id, phase),
                )
            ).fetchone()
            if row is None or str(row["status"]) != "committed":
                return None
            value = _json_object(str(row["evidence_json"]))
            return value

    async def _record_phase_intent_tx(
        self, db: aiosqlite.Connection, operation_id: str, phase: str
    ) -> CapabilityOperationRecord:
        if phase not in ALL_CAPABILITY_OPERATION_PHASES:
            raise CapabilityStoreError(
                "invalid_operation_phase", f"unknown operation phase: {phase}"
            )
        row = await (
            await db.execute(
                "SELECT * FROM capability_operations WHERE operation_id=?",
                (operation_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError("operation_not_found", operation_id)
        operation = _operation_from_row(row)
        phase_order = (
            BATCH_CAPABILITY_OPERATION_PHASES
            if operation.kind == "skill_install_batch"
            else CAPABILITY_OPERATION_PHASES
        )
        if operation.phase not in phase_order or phase not in phase_order:
            raise CapabilityStoreConflict("invalid_phase_transition", "operation phase graph differs")
        current_index = phase_order.index(operation.phase)
        target_index = phase_order.index(phase)
        if target_index not in {current_index, current_index + 1}:
            raise CapabilityStoreConflict(
                "invalid_phase_transition",
                f"cannot advance {operation.phase} to {phase}",
            )
        now = self._clock()
        stable_key = f"{operation_id}:{phase}"
        evidence_row = await (
            await db.execute(
                """SELECT * FROM capability_operation_phase_evidence
                   WHERE operation_id=? AND phase=?""",
                (operation_id, phase),
            )
        ).fetchone()
        if evidence_row is None:
            await db.execute(
                """INSERT INTO capability_operation_phase_evidence(
                    operation_id,phase,idempotency_key,status,evidence_json,
                    created_at,updated_at
                ) VALUES(?,?,?,'intent','{}',?,?)""",
                (operation_id, phase, stable_key, now, now),
            )
        elif str(evidence_row["idempotency_key"]) != stable_key:
            raise CapabilityStoreConflict(
                "phase_idempotency_conflict", "phase intent identity changed"
            )
        return operation

    async def record_phase_intent(
        self, operation_id: str, phase: str
    ) -> CapabilityOperationRecord:
        async with self.write_transaction() as db:
            return await self._record_phase_intent_tx(db, operation_id, phase)

    async def _commit_phase_tx(
        self,
        db: aiosqlite.Connection,
        operation_id: str,
        phase: str,
        *,
        evidence: Mapping[str, JsonValue] | None,
    ) -> CapabilityOperationRecord:
        operation = await self._record_phase_intent_tx(db, operation_id, phase)
        now = self._clock()
        evidence_json = canonical_json(dict(evidence or {}))
        evidence_row = await (
            await db.execute(
                """SELECT * FROM capability_operation_phase_evidence
                   WHERE operation_id=? AND phase=?""",
                (operation_id, phase),
            )
        ).fetchone()
        if str(evidence_row["status"]) == "committed":
            if str(evidence_row["evidence_json"]) != evidence_json:
                raise CapabilityStoreConflict(
                    "phase_evidence_conflict",
                    "committed phase evidence cannot change",
                )
            current = await self.get_operation_from_db(db, operation_id)
            if current is None:  # pragma: no cover
                raise CapabilityStoreError("operation_not_found", operation_id)
            return current
        await db.execute(
            """UPDATE capability_operation_phase_evidence
               SET status='committed',evidence_json=?,updated_at=?
               WHERE operation_id=? AND phase=?""",
            (evidence_json, now, operation_id, phase),
        )
        terminal = phase in {"published", "batch_committed"}
        cursor = await db.execute(
            """UPDATE capability_operations
                SET phase=?,status=?,error_json=NULL,updated_at=?,ended_at=?
                WHERE operation_id=? AND phase=?
                  AND status IN ('running','unknown')""",
            (
                phase,
                "succeeded" if terminal else "running",
                now,
                now if terminal else None,
                operation_id,
                operation.phase,
            ),
        )
        if cursor.rowcount != 1 and operation.phase != phase:
            raise CapabilityStoreConflict(
                "operation_phase_conflict", "operation phase changed"
            )
        current = await self.get_operation_from_db(db, operation_id)
        if current is None:  # pragma: no cover
            raise CapabilityStoreError("operation_not_found", operation_id)
        return current

    async def commit_phase(
        self,
        operation_id: str,
        phase: str,
        *,
        evidence: Mapping[str, JsonValue] | None = None,
    ) -> CapabilityOperationRecord:
        async with self.write_transaction() as db:
            return await self._commit_phase_tx(
                db, operation_id, phase, evidence=evidence
            )

    async def get_operation_from_db(
        self, db: aiosqlite.Connection, operation_id: str
    ) -> CapabilityOperationRecord | None:
        row = await (
            await db.execute(
                "SELECT * FROM capability_operations WHERE operation_id=?",
                (operation_id,),
            )
        ).fetchone()
        return None if row is None else _operation_from_row(row)

    async def fail_operation(
        self,
        operation_id: str,
        *,
        status: str = "failed",
        error: Mapping[str, JsonValue],
    ) -> CapabilityOperationRecord:
        if status not in {"failed", "cancelled", "unknown"}:
            raise CapabilityStoreError(
                "invalid_operation_status", f"invalid failure status: {status}"
            )
        async with self.write_transaction() as db:
            now = self._clock()
            cursor = await db.execute(
                """UPDATE capability_operations SET
                    status=?,error_json=?,updated_at=?,ended_at=?
                   WHERE operation_id=? AND status IN ('running','unknown')""",
                (
                    status,
                    canonical_json(dict(error)),
                    now,
                    None if status == "unknown" else now,
                    operation_id,
                ),
            )
            row = await (
                await db.execute(
                    "SELECT * FROM capability_operations WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            if row is None:
                raise CapabilityStoreError("operation_not_found", operation_id)
            record = _operation_from_row(row)
            if cursor.rowcount != 1 and (
                record.status != status
                or canonical_json(dict(record.error or {}))
                != canonical_json(dict(error))
            ):
                raise CapabilityStoreConflict(
                    "operation_terminal_conflict",
                    "operation already ended differently",
                )
            return record

    async def list_recoverable_operations(
        self,
    ) -> tuple[CapabilityOperationRecord, ...]:
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT * FROM capability_operations
                       WHERE status IN ('running','unknown')
                       ORDER BY started_at,operation_id"""
                )
            ).fetchall()
            return tuple(_operation_from_row(row) for row in rows)

    async def list_operations(
        self, *, limit: int = 100
    ) -> tuple[CapabilityOperationRecord, ...]:
        bounded = max(1, min(int(limit), 500))
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT * FROM capability_operations
                       ORDER BY updated_at DESC,operation_id DESC LIMIT ?""",
                    (bounded,),
                )
            ).fetchall()
            return tuple(_operation_from_row(row) for row in rows)

    async def list_validation_results(
        self, operation_id: str
    ) -> tuple[Mapping[str, JsonValue], ...]:
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT check_name,status,evidence_ref,created_at
                       FROM capability_validation_results
                       WHERE operation_id=?
                       ORDER BY created_at,check_name""",
                    (operation_id,),
                )
            ).fetchall()
            return tuple(
                {
                    "check_name": str(row["check_name"]),
                    "status": str(row["status"]),
                    "evidence_ref": (
                        None
                        if row["evidence_ref"] is None
                        else str(row["evidence_ref"])
                    ),
                    "created_at": float(row["created_at"]),
                }
                for row in rows
            )

    async def record_validation_result(
        self,
        operation_id: str,
        check_name: str,
        *,
        status: str,
        detail: Mapping[str, JsonValue] | None = None,
        evidence_ref: str | None = None,
    ) -> None:
        if status not in {"passed", "failed", "skipped", "unknown"}:
            raise CapabilityStoreError(
                "invalid_validation_result", f"unknown result status: {status}"
            )
        detail_json = canonical_json(dict(detail or {}))
        async with self.write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM capability_validation_results
                       WHERE operation_id=? AND check_name=?""",
                    (operation_id, check_name),
                )
            ).fetchone()
            if row is not None:
                if (
                    str(row["status"]) != status
                    or str(row["detail_json"]) != detail_json
                    or (
                        None
                        if row["evidence_ref"] is None
                        else str(row["evidence_ref"])
                    )
                    != evidence_ref
                ):
                    raise CapabilityStoreConflict(
                        "validation_result_conflict",
                        "validation result cannot change",
                    )
                return
            await db.execute(
                """INSERT INTO capability_validation_results(
                    operation_id,check_name,status,evidence_ref,detail_json,created_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    operation_id,
                    check_name,
                    status,
                    evidence_ref,
                    detail_json,
                    self._clock(),
                ),
            )

    async def _create_publish_intent_tx(
        self,
        db: aiosqlite.Connection,
        *,
        intent_id: str,
        operation_id: str,
        expected_registry_revision: int,
        old_specs: Sequence[Mapping[str, JsonValue]],
        new_specs: Sequence[Mapping[str, JsonValue]],
        old_binding: Mapping[str, JsonValue] | None,
        new_binding: Mapping[str, JsonValue],
    ) -> CapabilityPublishIntent:
        old_specs_json = canonical_json([dict(item) for item in old_specs])
        new_specs_json = canonical_json([dict(item) for item in new_specs])
        old_binding_json = (
            None if old_binding is None else canonical_json(dict(old_binding))
        )
        new_binding_json = canonical_json(dict(new_binding))
        existing = await (
            await db.execute(
                """SELECT * FROM capability_publish_intents
                   WHERE intent_id=? OR operation_id=?""",
                (intent_id, operation_id),
            )
        ).fetchone()
        if existing is not None:
            current = _publish_intent_from_row(existing)
            if (
                current.intent_id != intent_id
                or current.operation_id != operation_id
                or current.expected_registry_revision != expected_registry_revision
                or canonical_json(list(current.old_specs)) != old_specs_json
                or canonical_json(list(current.new_specs)) != new_specs_json
                or (
                    None
                    if current.old_binding is None
                    else canonical_json(dict(current.old_binding))
                )
                != old_binding_json
                or canonical_json(dict(current.new_binding)) != new_binding_json
            ):
                raise CapabilityStoreConflict(
                    "publish_intent_conflict",
                    "publish intent identity already belongs to another swap",
                )
            return current
        now = self._clock()
        operation_row = await (await db.execute(
            "SELECT kind FROM capability_operations WHERE operation_id=?", (operation_id,)
        )).fetchone()
        publish_phase = (
            "batch_publish_intent"
            if operation_row is not None and str(operation_row["kind"]) == "skill_install_batch"
            else "publish_intent"
        )
        await db.execute(
            """INSERT INTO capability_publish_intents(
                intent_id,operation_id,expected_registry_revision,old_specs_json,
                new_specs_json,old_binding_json,new_binding_json,phase,status,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?, 'pending',?,?)""",
            (
                intent_id,
                operation_id,
                expected_registry_revision,
                old_specs_json,
                new_specs_json,
                old_binding_json,
                new_binding_json,
                publish_phase,
                now,
                now,
            ),
        )
        await self._bump_generation_tx(db, catalog=True, binding=False)
        row = await (
            await db.execute(
                "SELECT * FROM capability_publish_intents WHERE intent_id=?",
                (intent_id,),
            )
        ).fetchone()
        return _publish_intent_from_row(row)

    async def create_publish_intent(self, **kwargs: Any) -> CapabilityPublishIntent:
        async with self.write_transaction() as db:
            return await self._create_publish_intent_tx(db, **kwargs)

    async def _advance_publish_intent_tx(
        self,
        db: aiosqlite.Connection,
        intent_id: str,
        *,
        phase: str,
        status: str,
    ) -> CapabilityPublishIntent:
        if phase not in {
            "publish_intent", "catalog_swapped", "bound", "batch_publish_intent",
            "batch_files_materialized", "batch_catalog_swapped", "batch_committed",
        }:
            raise CapabilityStoreError(
                "invalid_publish_phase", f"unknown publish phase: {phase}"
            )
        if status not in {"pending", "committed", "rolled_back", "unknown"}:
            raise CapabilityStoreError(
                "invalid_publish_status", f"unknown publish status: {status}"
            )
        row = await (
            await db.execute(
                "SELECT * FROM capability_publish_intents WHERE intent_id=?",
                (intent_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError("publish_intent_not_found", intent_id)
        current = _publish_intent_from_row(row)
        order = (
            {"batch_publish_intent": 0, "batch_files_materialized": 1,
             "batch_catalog_swapped": 2, "batch_committed": 3}
            if current.phase.startswith("batch_")
            else {"publish_intent": 0, "catalog_swapped": 1, "bound": 2}
        )
        if phase not in order:
            raise CapabilityStoreConflict("publish_phase_conflict", "publish phase graph differs")
        if order[phase] < order[current.phase] or order[phase] > order[current.phase] + 1:
            raise CapabilityStoreConflict(
                "publish_phase_conflict",
                f"cannot advance publish intent {current.phase} to {phase}",
            )
        if current.status in {"committed", "rolled_back"}:
            if current.status != status or current.phase != phase:
                raise CapabilityStoreConflict(
                    "publish_terminal_conflict", "publish intent is already terminal"
                )
            return current
        now = self._clock()
        await db.execute(
            """UPDATE capability_publish_intents
               SET phase=?,status=?,updated_at=? WHERE intent_id=?""",
            (phase, status, now, intent_id),
        )
        await self._bump_generation_tx(db, catalog=True, binding=False)
        row = await (
            await db.execute(
                "SELECT * FROM capability_publish_intents WHERE intent_id=?",
                (intent_id,),
            )
        ).fetchone()
        return _publish_intent_from_row(row)

    async def advance_publish_intent(
        self, intent_id: str, *, phase: str, status: str = "pending"
    ) -> CapabilityPublishIntent:
        async with self.write_transaction() as db:
            return await self._advance_publish_intent_tx(
                db, intent_id, phase=phase, status=status
            )

    async def pending_publish_intents(
        self,
    ) -> tuple[CapabilityPublishIntent, ...]:
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT * FROM capability_publish_intents
                       WHERE status IN ('pending','unknown')
                       ORDER BY created_at,intent_id"""
                )
            ).fetchall()
            return tuple(_publish_intent_from_row(row) for row in rows)

    async def get_publish_intent_for_operation(
        self, operation_id: str
    ) -> CapabilityPublishIntent | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM capability_publish_intents
                       WHERE operation_id=?""",
                    (operation_id,),
                )
            ).fetchone()
            return None if row is None else _publish_intent_from_row(row)

    async def _put_operation_receipt_tx(
        self,
        db: aiosqlite.Connection,
        receipt: CapabilityOperationReceipt,
    ) -> CapabilityOperationReceipt:
        if not isinstance(receipt, CapabilityOperationReceipt):
            raise TypeError("receipt must be CapabilityOperationReceipt")
        operation_row = await (
            await db.execute(
                "SELECT * FROM capability_operations WHERE operation_id=?",
                (receipt.operation_id,),
            )
        ).fetchone()
        if operation_row is None:
            raise CapabilityStoreError(
                "operation_not_found", receipt.operation_id
            )
        operation = _operation_from_row(operation_row)
        if operation.status != "succeeded" or operation.phase != "published":
            raise CapabilityStoreConflict(
                "operation_not_published",
                "only a completed published operation can issue a receipt",
            )
        if operation.kind != receipt.action:
            raise CapabilityStoreConflict(
                "operation_receipt_action_mismatch",
                "operation receipt action differs from the operation",
            )
        if operation.root_run_id != receipt.root_run_id:
            raise CapabilityStoreConflict(
                "operation_receipt_root_mismatch",
                "operation receipt root run differs from the operation",
            )
        existing = await (
            await db.execute(
                """SELECT * FROM capability_operation_receipts
                   WHERE operation_id=? OR receipt_hash=?""",
                (receipt.operation_id, receipt.operation_receipt_hash),
            )
        ).fetchall()
        if existing:
            if len(existing) != 1:
                raise CapabilityStoreConflict(
                    "operation_receipt_conflict",
                    "operation and receipt hash refer to different receipts",
                )
            current = _operation_receipt_from_row(existing[0])
            if current != receipt:
                raise CapabilityStoreConflict(
                    "operation_receipt_conflict",
                    "operation already has a different immutable receipt",
                )
            return current
        await db.execute(
            """INSERT INTO capability_operation_receipts(
                operation_id,receipt_hash,receipt_json,created_at
            ) VALUES(?,?,?,?)""",
            (
                receipt.operation_id,
                receipt.operation_receipt_hash,
                canonical_json(receipt.to_dict()),
                self._clock(),
            ),
        )
        return receipt

    async def put_operation_receipt(
        self, receipt: CapabilityOperationReceipt
    ) -> CapabilityOperationReceipt:
        async with self.write_transaction() as db:
            return await self._put_operation_receipt_tx(db, receipt)

    async def get_operation_receipt(
        self, operation_id: str
    ) -> CapabilityOperationReceipt | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM capability_operation_receipts
                       WHERE operation_id=?""",
                    (operation_id,),
                )
            ).fetchone()
            return None if row is None else _operation_receipt_from_row(row)

    async def _stage_refresh_intent_tx(
        self,
        db: aiosqlite.Connection,
        intent: CapabilityRefreshIntent,
    ) -> CapabilityRefreshIntent:
        if not isinstance(intent, CapabilityRefreshIntent):
            raise TypeError("intent must be CapabilityRefreshIntent")
        if intent.status != "pending":
            raise CapabilityStoreError(
                "invalid_refresh_status", "new refresh intent must be pending"
            )
        receipt_row = await (
            await db.execute(
                """SELECT * FROM capability_operation_receipts
                   WHERE operation_id=?""",
                (intent.operation_id,),
            )
        ).fetchone()
        if receipt_row is None:
            raise CapabilityStoreError(
                "operation_receipt_not_found", intent.operation_id
            )
        receipt = _operation_receipt_from_row(receipt_row)
        if (
            receipt.root_run_id != intent.root_run_id
            or receipt.refresh_nonce != intent.refresh_nonce
            or receipt.old_stamp.fingerprint != intent.old_stamp.fingerprint
            or receipt.parent_command_id != intent.source_command_id
            or receipt.parent_effect_id != intent.source_effect_id
        ):
            raise CapabilityStoreConflict(
                "refresh_receipt_mismatch",
                "refresh intent is not bound to the published operation receipt",
            )
        existing_rows = await (
            await db.execute(
                """SELECT * FROM capability_refresh_intents
                   WHERE intent_id=? OR operation_id=? OR refresh_nonce=?""",
                (intent.intent_id, intent.operation_id, intent.refresh_nonce),
            )
        ).fetchall()
        if existing_rows:
            if len(existing_rows) != 1:
                raise CapabilityStoreConflict(
                    "refresh_intent_conflict",
                    "refresh identities refer to different intents",
                )
            current = _refresh_intent_from_row(existing_rows[0])
            if current != intent:
                raise CapabilityStoreConflict(
                    "refresh_intent_conflict",
                    "refresh intent identity already belongs to another payload",
                )
            return current
        now = self._clock()
        await db.execute(
            """INSERT INTO capability_refresh_intents(
                intent_id,operation_id,refresh_nonce,root_run_id,run_id,
                source_kind,status,intent_json,commit_hash,commit_json,
                error_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,'pending',?,NULL,NULL,NULL,?,?)""",
            (
                intent.intent_id,
                intent.operation_id,
                intent.refresh_nonce,
                intent.root_run_id,
                intent.run_id,
                intent.source_kind,
                canonical_json(intent.to_dict()),
                now,
                now,
            ),
        )
        return intent

    async def get_refresh_intent(
        self, intent_id: str
    ) -> CapabilityRefreshIntent | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM capability_refresh_intents
                       WHERE intent_id=?""",
                    (intent_id,),
                )
            ).fetchone()
            return None if row is None else _refresh_intent_from_row(row)

    async def pending_refresh_intents(
        self,
        *,
        root_run_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[CapabilityRefreshIntent, ...]:
        clauses = ["status='pending'"]
        params: list[str] = []
        if root_run_id is not None:
            clauses.append("root_run_id=?")
            params.append(root_run_id)
        if run_id is not None:
            clauses.append("run_id=?")
            params.append(run_id)
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    f"""SELECT * FROM capability_refresh_intents
                        WHERE {' AND '.join(clauses)}
                        ORDER BY created_at,intent_id""",
                    tuple(params),
                )
            ).fetchall()
            return tuple(_refresh_intent_from_row(row) for row in rows)

    async def _commit_refresh_intent_tx(
        self,
        db: aiosqlite.Connection,
        commit: CapabilityRefreshCommit,
    ) -> CapabilityRefreshIntent:
        if not isinstance(commit, CapabilityRefreshCommit):
            raise TypeError("commit must be CapabilityRefreshCommit")
        row = await (
            await db.execute(
                """SELECT * FROM capability_refresh_intents
                   WHERE intent_id=?""",
                (commit.intent_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError(
                "refresh_intent_not_found", commit.intent_id
            )
        current = _refresh_intent_from_row(row)
        if current.status == "committed":
            if current.commit_hash != commit.fingerprint:
                raise CapabilityStoreConflict(
                    "refresh_commit_conflict",
                    "refresh nonce was committed with another payload",
                )
            return current
        if current.status != "pending":
            raise CapabilityStoreConflict(
                "refresh_intent_terminal", "refresh intent is already terminal"
            )
        identity = (
            current.intent_id,
            current.root_run_id,
            current.run_id,
            current.operation_id,
            current.refresh_nonce,
            current.expected_continuation_version,
            current.old_stamp.fingerprint,
            current.old_catalog_snapshot_ref,
            current.old_tool_set_snapshot_ref,
        )
        committed_identity = (
            commit.intent_id,
            commit.root_run_id,
            commit.run_id,
            commit.operation_id,
            commit.refresh_nonce,
            commit.expected_continuation_version,
            commit.old_stamp.fingerprint,
            commit.old_catalog_snapshot_ref,
            commit.old_tool_set_snapshot_ref,
        )
        if identity != committed_identity:
            raise CapabilityStoreConflict(
                "refresh_commit_conflict",
                "refresh commit does not match its pending intent",
            )
        receipt_row = await (
            await db.execute(
                """SELECT * FROM capability_operation_receipts
                   WHERE operation_id=?""",
                (commit.operation_id,),
            )
        ).fetchone()
        if receipt_row is None:
            raise CapabilityStoreError(
                "operation_receipt_not_found", commit.operation_id
            )
        receipt = _operation_receipt_from_row(receipt_row)
        if (
            receipt.affected_tool_spec_fingerprints
            != commit.affected_tool_spec_fingerprints
        ):
            raise CapabilityStoreConflict(
                "refresh_commit_fingerprint_mismatch",
                "refresh commit differs from published ToolSpec evidence",
            )
        cursor = await db.execute(
            """UPDATE capability_refresh_intents
               SET status='committed',commit_hash=?,commit_json=?,updated_at=?
               WHERE intent_id=? AND status='pending'""",
            (
                commit.fingerprint,
                canonical_json(commit.to_dict()),
                self._clock(),
                commit.intent_id,
            ),
        )
        if cursor.rowcount != 1:
            raise CapabilityStoreConflict(
                "refresh_commit_conflict", "refresh intent changed concurrently"
            )
        row = await (
            await db.execute(
                """SELECT * FROM capability_refresh_intents
                   WHERE intent_id=?""",
                (commit.intent_id,),
            )
        ).fetchone()
        return _refresh_intent_from_row(row)

    async def _fail_refresh_intent_tx(
        self,
        db: aiosqlite.Connection,
        intent_id: str,
        *,
        error: Mapping[str, JsonValue],
    ) -> CapabilityRefreshIntent:
        error_json = canonical_json(dict(error))
        row = await (
            await db.execute(
                """SELECT * FROM capability_refresh_intents
                   WHERE intent_id=?""",
                (intent_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError("refresh_intent_not_found", intent_id)
        current = _refresh_intent_from_row(row)
        if current.status == "committed":
            raise CapabilityStoreConflict(
                "refresh_intent_terminal",
                "committed refresh intent cannot be failed",
            )
        if current.status == "failed":
            if str(row["error_json"]) != error_json:
                raise CapabilityStoreConflict(
                    "refresh_failure_conflict",
                    "refresh intent already failed differently",
                )
            return current
        await db.execute(
            """UPDATE capability_refresh_intents
               SET status='failed',error_json=?,updated_at=?
               WHERE intent_id=? AND status='pending'""",
            (error_json, self._clock(), intent_id),
        )
        row = await (
            await db.execute(
                """SELECT * FROM capability_refresh_intents
                   WHERE intent_id=?""",
                (intent_id,),
            )
        ).fetchone()
        return _refresh_intent_from_row(row)

    @staticmethod
    def _validate_runtime_identity(
        *,
        runtime_lease_id: str,
        pack_id: str,
        version: str,
        manifest_hash: str,
        pid: int | None,
    ) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (runtime_lease_id, pack_id, version)
        ):
            raise CapabilityStoreError(
                "invalid_runtime_identity",
                "runtime lease, pack and version identities are required",
            )
        if (
            len(manifest_hash) != 64
            or any(
                character not in "0123456789abcdef"
                for character in manifest_hash
            )
        ):
            raise CapabilityStoreError(
                "invalid_runtime_identity",
                "runtime manifest_hash must be a lowercase SHA-256 digest",
            )
        if pid is not None and (not isinstance(pid, int) or pid <= 0):
            raise CapabilityStoreError(
                "invalid_runtime_pid", "runtime pid must be a positive integer"
            )

    async def _create_runtime_lease_tx(
        self,
        db: aiosqlite.Connection,
        *,
        runtime_lease_id: str,
        pack_id: str,
        version: str,
        manifest_hash: str,
        server_id: str = "",
        pid: int | None = None,
        run_id: str | None = None,
    ) -> CapabilityRuntimeLease:
        self._validate_runtime_identity(
            runtime_lease_id=runtime_lease_id,
            pack_id=pack_id,
            version=version,
            manifest_hash=manifest_hash,
            pid=pid,
        )
        if run_id is not None and not run_id:
            raise CapabilityStoreError(
                "invalid_runtime_identity", "run_id cannot be empty"
            )
        existing = await (
            await db.execute(
                "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                (runtime_lease_id,),
            )
        ).fetchone()
        if existing is not None:
            current = _runtime_lease_from_row(existing)
            if (
                current.pack_id,
                current.version,
                current.manifest_hash,
                current.server_id,
                current.run_id,
            ) != (
                pack_id,
                version,
                manifest_hash,
                server_id,
                run_id,
            ):
                raise CapabilityStoreConflict(
                    "runtime_lease_conflict",
                    "runtime lease identity already belongs to another runtime",
                )
            return current
        now = self._clock()
        await db.execute(
            """INSERT INTO capability_runtime_leases(
                lease_id,pack_id,version,manifest_hash,server_id,pid,run_id,
                session_generation,state,heartbeat_at,created_at
            ) VALUES(?,?,?,?,?,?,?,1,'starting',?,?)""",
            (
                runtime_lease_id,
                pack_id,
                version,
                manifest_hash,
                server_id,
                pid,
                run_id,
                now,
                now,
            ),
        )
        row = await (
            await db.execute(
                "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                (runtime_lease_id,),
            )
        ).fetchone()
        return _runtime_lease_from_row(row)

    async def create_runtime_lease(self, **kwargs: Any) -> CapabilityRuntimeLease:
        async with self.write_transaction() as db:
            return await self._create_runtime_lease_tx(db, **kwargs)

    async def get_runtime_lease(
        self, runtime_lease_id: str
    ) -> CapabilityRuntimeLease | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                    (runtime_lease_id,),
                )
            ).fetchone()
            return None if row is None else _runtime_lease_from_row(row)

    async def _active_runtime_call_count_tx(
        self, db: aiosqlite.Connection, runtime_lease_id: str
    ) -> int:
        row = await (
            await db.execute(
                """SELECT COUNT(*) FROM capability_runtime_call_leases
                   WHERE runtime_lease_id=? AND state!='settled'""",
                (runtime_lease_id,),
            )
        ).fetchone()
        return 0 if row is None else int(row[0])

    async def _transition_runtime_lease_tx(
        self,
        db: aiosqlite.Connection,
        runtime_lease_id: str,
        *,
        expected_state: str,
        target_state: str,
        expected_generation: int | None = None,
        pid: int | None = None,
    ) -> CapabilityRuntimeLease:
        states = {"starting", "ready", "draining", "stopped", "unknown"}
        if expected_state not in states or target_state not in states:
            raise CapabilityStoreError(
                "invalid_runtime_state", "unknown runtime lease state"
            )
        if pid is not None and (not isinstance(pid, int) or pid <= 0):
            raise CapabilityStoreError(
                "invalid_runtime_pid", "runtime pid must be a positive integer"
            )
        row = await (
            await db.execute(
                "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                (runtime_lease_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError(
                "runtime_lease_not_found", runtime_lease_id
            )
        current = _runtime_lease_from_row(row)
        if expected_generation is not None and (
            current.session_generation != expected_generation
        ):
            raise CapabilityStoreConflict(
                "runtime_generation_conflict", "runtime session changed"
            )
        if current.state == target_state:
            return current
        if current.state != expected_state:
            raise CapabilityStoreConflict(
                "runtime_state_conflict",
                f"runtime is {current.state}, expected {expected_state}",
            )
        allowed = {
            "starting": {"ready", "draining", "unknown", "stopped"},
            "ready": {"draining", "unknown"},
            "unknown": {"ready", "draining", "stopped"},
            "draining": {"unknown", "stopped"},
            "stopped": set(),
        }
        if target_state not in allowed[current.state]:
            raise CapabilityStoreConflict(
                "runtime_state_conflict",
                f"runtime cannot transition {current.state} to {target_state}",
            )
        if target_state == "stopped" and (
            await self._active_runtime_call_count_tx(db, runtime_lease_id)
        ):
            raise CapabilityStoreConflict(
                "runtime_calls_in_flight",
                "runtime cannot stop while call leases are active",
            )
        cursor = await db.execute(
            """UPDATE capability_runtime_leases
               SET state=?,pid=COALESCE(?,pid),heartbeat_at=?
               WHERE lease_id=? AND state=? AND session_generation=?""",
            (
                target_state,
                pid,
                self._clock(),
                runtime_lease_id,
                current.state,
                current.session_generation,
            ),
        )
        if cursor.rowcount != 1:
            raise CapabilityStoreConflict(
                "runtime_state_conflict", "runtime lease changed concurrently"
            )
        row = await (
            await db.execute(
                "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                (runtime_lease_id,),
            )
        ).fetchone()
        return _runtime_lease_from_row(row)

    async def transition_runtime_lease(
        self, runtime_lease_id: str, **kwargs: Any
    ) -> CapabilityRuntimeLease:
        async with self.write_transaction() as db:
            return await self._transition_runtime_lease_tx(
                db, runtime_lease_id, **kwargs
            )

    async def _reconnect_runtime_lease_tx(
        self,
        db: aiosqlite.Connection,
        runtime_lease_id: str,
        *,
        expected_generation: int,
        pid: int | None = None,
    ) -> CapabilityRuntimeLease:
        if pid is not None and (not isinstance(pid, int) or pid <= 0):
            raise CapabilityStoreError(
                "invalid_runtime_pid", "runtime pid must be a positive integer"
            )
        cursor = await db.execute(
            """UPDATE capability_runtime_leases
               SET session_generation=session_generation+1,state='ready',
                   pid=COALESCE(?,pid),heartbeat_at=?
               WHERE lease_id=? AND session_generation=?
                 AND state IN ('ready','unknown')""",
            (pid, self._clock(), runtime_lease_id, expected_generation),
        )
        if cursor.rowcount != 1:
            row = await (
                await db.execute(
                    "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                    (runtime_lease_id,),
                )
            ).fetchone()
            if row is None:
                raise CapabilityStoreError(
                    "runtime_lease_not_found", runtime_lease_id
                )
            raise CapabilityStoreConflict(
                "runtime_generation_conflict",
                "runtime cannot reconnect from its current state or generation",
            )
        row = await (
            await db.execute(
                "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                (runtime_lease_id,),
            )
        ).fetchone()
        return _runtime_lease_from_row(row)

    async def reconnect_runtime_lease(
        self, runtime_lease_id: str, **kwargs: Any
    ) -> CapabilityRuntimeLease:
        async with self.write_transaction() as db:
            return await self._reconnect_runtime_lease_tx(
                db, runtime_lease_id, **kwargs
            )

    async def heartbeat_runtime_lease(
        self, runtime_lease_id: str, *, session_generation: int
    ) -> CapabilityRuntimeLease:
        async with self.write_transaction() as db:
            cursor = await db.execute(
                """UPDATE capability_runtime_leases SET heartbeat_at=?
                   WHERE lease_id=? AND session_generation=? AND state!='stopped'""",
                (self._clock(), runtime_lease_id, session_generation),
            )
            if cursor.rowcount != 1:
                raise CapabilityStoreConflict(
                    "runtime_generation_conflict",
                    "runtime heartbeat does not match a live session",
                )
            row = await (
                await db.execute(
                    "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                    (runtime_lease_id,),
                )
            ).fetchone()
            return _runtime_lease_from_row(row)

    async def _claim_runtime_call_tx(
        self,
        db: aiosqlite.Connection,
        *,
        call_lease_id: str,
        runtime_lease_id: str,
        effect_id: str,
    ) -> CapabilityRuntimeCallLease:
        if not all(
            isinstance(value, str) and value
            for value in (call_lease_id, runtime_lease_id, effect_id)
        ):
            raise CapabilityStoreError(
                "invalid_runtime_call_identity",
                "call lease, runtime lease and effect identities are required",
            )
        existing_rows = await (
            await db.execute(
                """SELECT * FROM capability_runtime_call_leases
                   WHERE call_lease_id=? OR effect_id=?""",
                (call_lease_id, effect_id),
            )
        ).fetchall()
        if existing_rows:
            if len(existing_rows) != 1:
                raise CapabilityStoreConflict(
                    "runtime_call_conflict",
                    "call lease and effect identities refer to different calls",
                )
            current = _runtime_call_lease_from_row(existing_rows[0])
            if (
                current.call_lease_id != call_lease_id
                or current.runtime_lease_id != runtime_lease_id
                or current.effect_id != effect_id
            ):
                raise CapabilityStoreConflict(
                    "runtime_call_conflict",
                    "runtime call identity already belongs to another call",
                )
            return current
        runtime_row = await (
            await db.execute(
                "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                (runtime_lease_id,),
            )
        ).fetchone()
        if runtime_row is None:
            raise CapabilityStoreError(
                "runtime_lease_not_found", runtime_lease_id
            )
        runtime = _runtime_lease_from_row(runtime_row)
        if runtime.state != "ready":
            raise CapabilityStoreConflict(
                "runtime_not_accepting_calls",
                f"runtime is {runtime.state}; new calls require ready",
            )
        now = self._clock()
        await db.execute(
            """INSERT INTO capability_runtime_call_leases(
                call_lease_id,runtime_lease_id,effect_id,session_generation,
                state,started_at,ended_at
            ) VALUES(?,?,?,?,'claimed',?,NULL)""",
            (
                call_lease_id,
                runtime_lease_id,
                effect_id,
                runtime.session_generation,
                now,
            ),
        )
        row = await (
            await db.execute(
                """SELECT * FROM capability_runtime_call_leases
                   WHERE call_lease_id=?""",
                (call_lease_id,),
            )
        ).fetchone()
        return _runtime_call_lease_from_row(row)

    async def claim_runtime_call(
        self, **kwargs: Any
    ) -> CapabilityRuntimeCallLease:
        async with self.write_transaction() as db:
            return await self._claim_runtime_call_tx(db, **kwargs)

    async def _mark_runtime_call_running_tx(
        self, db: aiosqlite.Connection, call_lease_id: str
    ) -> CapabilityRuntimeCallLease:
        row = await (
            await db.execute(
                """SELECT * FROM capability_runtime_call_leases
                   WHERE call_lease_id=?""",
                (call_lease_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError(
                "runtime_call_not_found", call_lease_id
            )
        current = _runtime_call_lease_from_row(row)
        if current.state == "running":
            return current
        if current.state != "claimed":
            raise CapabilityStoreConflict(
                "runtime_call_state_conflict",
                f"runtime call is {current.state}, expected claimed",
            )
        await db.execute(
            """UPDATE capability_runtime_call_leases SET state='running'
               WHERE call_lease_id=? AND state='claimed'""",
            (call_lease_id,),
        )
        row = await (
            await db.execute(
                """SELECT * FROM capability_runtime_call_leases
                   WHERE call_lease_id=?""",
                (call_lease_id,),
            )
        ).fetchone()
        return _runtime_call_lease_from_row(row)

    async def mark_runtime_call_running(
        self, call_lease_id: str
    ) -> CapabilityRuntimeCallLease:
        async with self.write_transaction() as db:
            return await self._mark_runtime_call_running_tx(db, call_lease_id)

    async def _settle_runtime_call_tx(
        self, db: aiosqlite.Connection, call_lease_id: str
    ) -> CapabilityRuntimeCallLease:
        row = await (
            await db.execute(
                """SELECT * FROM capability_runtime_call_leases
                   WHERE call_lease_id=?""",
                (call_lease_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError(
                "runtime_call_not_found", call_lease_id
            )
        current = _runtime_call_lease_from_row(row)
        if current.state == "settled":
            return current
        await db.execute(
            """UPDATE capability_runtime_call_leases
               SET state='settled',ended_at=?
               WHERE call_lease_id=? AND state!='settled'""",
            (self._clock(), call_lease_id),
        )
        row = await (
            await db.execute(
                """SELECT * FROM capability_runtime_call_leases
                   WHERE call_lease_id=?""",
                (call_lease_id,),
            )
        ).fetchone()
        return _runtime_call_lease_from_row(row)

    async def settle_runtime_call(
        self, call_lease_id: str
    ) -> CapabilityRuntimeCallLease:
        async with self.write_transaction() as db:
            return await self._settle_runtime_call_tx(db, call_lease_id)

    async def runtime_call_leases(
        self, runtime_lease_id: str
    ) -> tuple[CapabilityRuntimeCallLease, ...]:
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT * FROM capability_runtime_call_leases
                       WHERE runtime_lease_id=? ORDER BY started_at,call_lease_id""",
                    (runtime_lease_id,),
                )
            ).fetchall()
            return tuple(_runtime_call_lease_from_row(row) for row in rows)

    async def runtime_can_stop(self, runtime_lease_id: str) -> bool:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT state FROM capability_runtime_leases WHERE lease_id=?",
                    (runtime_lease_id,),
                )
            ).fetchone()
            if row is None:
                raise CapabilityStoreError(
                    "runtime_lease_not_found", runtime_lease_id
                )
            return (
                str(row["state"]) == "draining"
                and await self._active_runtime_call_count_tx(
                    db, runtime_lease_id
                )
                == 0
            )

    async def _drain_unreferenced_runtimes_tx(
        self,
        db: aiosqlite.Connection,
        *,
        pack_id: str,
        version: str,
        manifest_hash: str,
    ) -> tuple[CapabilityRuntimeLease, ...]:
        binding = await (
            await db.execute(
                """SELECT 1 FROM capability_bindings
                   WHERE pack_id=? AND active_version=? AND active_manifest_hash=?
                     AND enabled=1 LIMIT 1""",
                (pack_id, version, manifest_hash),
            )
        ).fetchone()
        snapshot = await (
            await db.execute(
                """SELECT 1 FROM capability_snapshot_leases
                   WHERE pack_id=? AND version=? AND manifest_hash=?
                     AND released_at IS NULL LIMIT 1""",
                (pack_id, version, manifest_hash),
            )
        ).fetchone()
        if binding is None and snapshot is None:
            await db.execute(
                """UPDATE capability_runtime_leases
                   SET state='draining',heartbeat_at=?
                   WHERE pack_id=? AND version=? AND manifest_hash=?
                     AND state IN ('starting','ready','unknown')""",
                (self._clock(), pack_id, version, manifest_hash),
            )
        rows = await (
            await db.execute(
                """SELECT * FROM capability_runtime_leases
                   WHERE pack_id=? AND version=? AND manifest_hash=?
                   ORDER BY created_at,lease_id""",
                (pack_id, version, manifest_hash),
            )
        ).fetchall()
        return tuple(_runtime_lease_from_row(row) for row in rows)

    async def drain_unreferenced_runtimes(
        self, *, pack_id: str, version: str, manifest_hash: str
    ) -> tuple[CapabilityRuntimeLease, ...]:
        async with self.write_transaction() as db:
            return await self._drain_unreferenced_runtimes_tx(
                db,
                pack_id=pack_id,
                version=version,
                manifest_hash=manifest_hash,
            )

    async def _acquire_snapshot_lease_tx(
        self,
        db: aiosqlite.Connection,
        *,
        snapshot_ref: str,
        run_id: str,
        root_run_id: str,
        entries: Sequence[CapabilityCatalogEntry],
    ) -> None:
        pending = await (
            await db.execute(
                """SELECT COUNT(*) FROM capability_publish_intents
                   WHERE status IN ('pending','unknown')"""
            )
        ).fetchone()
        if pending and int(pending[0]) > 0:
            raise CapabilityStoreConflict(
                "publish_reconciliation_required",
                "cannot lease a catalog while publish state is unresolved",
            )
        catalog_entries: list[RunCatalogEntryIdentity] = []
        empty_hash = fingerprint_json([])
        for entry in entries:
            if not entry.bindings:
                raise CapabilityStoreConflict(
                    "snapshot_lease_binding_missing",
                    "legacy catalog entries require a selected binding",
                )
            selected = entry.bindings[0]
            envelope: dict[str, JsonValue] = {
                "selected_binding": selected.to_dict(),
                "visible_bindings": [
                    binding.to_dict() for binding in entry.bindings
                ],
                "pack_id": entry.version.capability_id,
                "version": entry.version.version,
                "manifest_hash": entry.version.manifest_hash,
                "tool_spec_fingerprints": list(
                    entry.expected_tool_fingerprints
                ),
                "instruction_refs_hash": empty_hash,
                "workflow_refs_hash": empty_hash,
                "runtime_descriptor_hash": fingerprint_json(
                    {
                        "domain": "legacy-runtime-descriptor-v1",
                        "descriptor_fingerprint": entry.version.fingerprint,
                    }
                ),
            }
            catalog_entries.append(
                RunCatalogEntryIdentity(
                    entry_kind="pack",
                    descriptor_fingerprint=entry.version.fingerprint,
                    canonical_envelope=envelope,
                )
            )
        content = RunCatalogContentStamp(
            request_scope=CapabilityScope(run_key=root_run_id),
            entries=tuple(catalog_entries),
        )
        request_owner_key = (
            entries[0].bindings[0].owner_key
            if entries and entries[0].bindings
            else LEGACY_LOCAL_OWNER_KEY
        )
        await self._put_run_catalog_snapshot_tx(
            db,
            content,
            request_owner_key=request_owner_key,
            catalog_generation_vector={},
            require_complete=True,
        )
        owner_operation_id = fingerprint_json(
            {
                "domain": "legacy-snapshot-acquire-v2",
                "snapshot_ref": snapshot_ref,
                "run_id": run_id,
            }
        )
        intent = await self._prepare_snapshot_lease_intent_tx(
            db,
            snapshot_ref=snapshot_ref,
            snapshot_ref_schema="legacy_v1",
            run_id=run_id,
            root_run_id=root_run_id,
            request_id=f"legacy:{run_id}",
            turn_id=f"legacy:{run_id}",
            owner_operation_id=owner_operation_id,
            lease_owner_kind="legacy_boundary",
            run_catalog_content_stamp=content.fingerprint,
            entry_set_hash=content.entry_set_hash,
            expected_entry_count=len(content.entries),
        )
        await self._put_snapshot_lease_rows_tx(db, intent)
        owner_record_ref = f"legacy-boundary:{run_id}:{snapshot_ref}"
        owner_record_hash = fingerprint_json(
            {"domain": "legacy-owner-record-v1", "ref": owner_record_ref}
        )
        await self._bind_snapshot_lease_intent_tx(
            db,
            intent.lease_intent_id,
            owner_record_ref=owner_record_ref,
            owner_record_hash=owner_record_hash,
            start_fingerprint=fingerprint_json(
                {
                    "domain": "legacy-start-v1",
                    "snapshot_ref": snapshot_ref,
                    "run_id": run_id,
                    "run_catalog_content_stamp": content.fingerprint,
                }
            ),
        )

    async def acquire_snapshot_lease(self, **kwargs: Any) -> None:
        async with self.write_transaction() as db:
            await self._acquire_snapshot_lease_tx(db, **kwargs)

    async def _release_snapshot_lease_tx(
        self,
        db: aiosqlite.Connection,
        snapshot_ref: str,
        run_id: str,
    ) -> int:
        affected = await _fetch_mappings(
            await db.execute(
                """SELECT DISTINCT pack_id,version,manifest_hash
                   FROM capability_snapshot_leases
                   WHERE snapshot_ref=? AND run_id=? AND released_at IS NULL""",
                (snapshot_ref, run_id),
            )
        )
        intent_rows = await _fetch_mappings(
            await db.execute(
                """SELECT lease_intent_id,lease_intent_hash
                   FROM capability_snapshot_lease_intents
                   WHERE snapshot_ref=? AND run_id=?
                     AND status IN ('prepared','bound')
                   ORDER BY lease_generation""",
                (snapshot_ref, run_id),
            )
        )
        released_count = 0
        for intent_row in intent_rows:
            count_row = await (
                await db.execute(
                    """SELECT COUNT(*) FROM capability_snapshot_leases
                       WHERE lease_intent_id=? AND released_at IS NULL""",
                    (str(intent_row["lease_intent_id"]),),
                )
            ).fetchone()
            released_count += int(count_row[0])
            terminal_ref = f"legacy-release:{run_id}:{snapshot_ref}"
            await self.release_snapshot_lease_intent_in_tx(
                db,
                str(intent_row["lease_intent_id"]),
                intent_hash=str(intent_row["lease_intent_hash"]),
                release_reason="legacy_terminal",
                owner_terminal_or_transition_ref=terminal_ref,
                owner_terminal_or_transition_hash=fingerprint_json(
                    {"domain": "legacy-terminal-v1", "ref": terminal_ref}
                ),
            )
        for row in affected:
            await self._drain_unreferenced_runtimes_tx(
                db,
                pack_id=str(row["pack_id"]),
                version=str(row["version"]),
                manifest_hash=str(row["manifest_hash"]),
            )
        return released_count

    async def release_snapshot_lease(self, snapshot_ref: str, run_id: str) -> int:
        async with self.write_transaction() as db:
            return await self._release_snapshot_lease_tx(
                db, snapshot_ref, run_id
            )

    async def _snapshot_lease_fingerprints_tx(
        self,
        db: aiosqlite.Connection,
        *,
        snapshot_ref: str,
        run_id: str,
    ) -> tuple[str, ...]:
        rows = await (
            await db.execute(
                """SELECT tool_spec_fingerprints_json
                   FROM capability_snapshot_leases
                   WHERE snapshot_ref=? AND run_id=? AND released_at IS NULL
                   ORDER BY pack_id,version,manifest_hash""",
                (snapshot_ref, run_id),
            )
        ).fetchall()
        fingerprints: set[str] = set()
        for row in rows:
            payload = json.loads(str(row["tool_spec_fingerprints_json"]))
            if not isinstance(payload, list) or not all(
                isinstance(item, str) for item in payload
            ):
                raise CapabilityStoreError(
                    "corrupt_snapshot_lease",
                    "snapshot lease tool fingerprints are invalid",
                )
            fingerprints.update(payload)
        return tuple(sorted(fingerprints))

    async def snapshot_lease_fingerprints(
        self, *, snapshot_ref: str, run_id: str
    ) -> tuple[str, ...]:
        async with self.read_connection() as db:
            return await self._snapshot_lease_fingerprints_tx(
                db, snapshot_ref=snapshot_ref, run_id=run_id
            )

    async def _clone_snapshot_lease_tx(
        self,
        db: aiosqlite.Connection,
        *,
        snapshot_ref: str,
        source_run_id: str,
        target_run_id: str,
        root_run_id: str,
    ) -> tuple[str, ...]:
        if not all(
            isinstance(value, str) and value
            for value in (
                snapshot_ref,
                source_run_id,
                target_run_id,
                root_run_id,
            )
        ):
            raise ValueError("snapshot and run identities are required")
        rows = await _fetch_mappings(
            await db.execute(
                """SELECT l.tool_spec_fingerprints_json,
                          i.run_catalog_content_stamp,i.entry_set_hash,
                          i.expected_entry_count
                   FROM capability_snapshot_leases AS l
                   JOIN capability_snapshot_lease_intents AS i
                     ON i.lease_intent_id=l.lease_intent_id
                   WHERE l.snapshot_ref=? AND l.run_id=?
                     AND l.released_at IS NULL AND i.status='bound'
                   ORDER BY l.entry_ordinal""",
                (snapshot_ref, source_run_id),
            )
        )
        fingerprints: set[str] = set()
        for row in rows:
            payload = str(row["tool_spec_fingerprints_json"])
            parsed = json.loads(payload)
            if not isinstance(parsed, list) or not all(
                isinstance(item, str) for item in parsed
            ):
                raise CapabilityStoreError(
                    "corrupt_snapshot_lease",
                    "snapshot lease tool fingerprints are invalid",
                )
            fingerprints.update(parsed)
        if not rows:
            return ()
        first = rows[0]
        owner_operation_id = fingerprint_json(
            {
                "domain": "legacy-snapshot-clone-v2",
                "snapshot_ref": snapshot_ref,
                "source_run_id": source_run_id,
                "target_run_id": target_run_id,
            }
        )
        intent = await self._prepare_snapshot_lease_intent_tx(
            db,
            snapshot_ref=snapshot_ref,
            snapshot_ref_schema="legacy_v1",
            run_id=target_run_id,
            root_run_id=root_run_id,
            request_id=f"legacy:{target_run_id}",
            turn_id=f"legacy:{target_run_id}",
            owner_operation_id=owner_operation_id,
            lease_owner_kind="queued_child_legacy",
            run_catalog_content_stamp=str(first["run_catalog_content_stamp"]),
            entry_set_hash=str(first["entry_set_hash"]),
            expected_entry_count=int(first["expected_entry_count"]),
        )
        await self._put_snapshot_lease_rows_tx(db, intent)
        owner_record_ref = (
            f"legacy-child-boundary:{source_run_id}:{target_run_id}:{snapshot_ref}"
        )
        await self._bind_snapshot_lease_intent_tx(
            db,
            intent.lease_intent_id,
            owner_record_ref=owner_record_ref,
            owner_record_hash=fingerprint_json(
                {"domain": "legacy-child-owner-v1", "ref": owner_record_ref}
            ),
            start_fingerprint=fingerprint_json(
                {
                    "domain": "legacy-child-start-v1",
                    "snapshot_ref": snapshot_ref,
                    "source_run_id": source_run_id,
                    "target_run_id": target_run_id,
                }
            ),
        )
        return tuple(sorted(fingerprints))

    async def clone_snapshot_lease(
        self,
        *,
        snapshot_ref: str,
        source_run_id: str,
        target_run_id: str,
        root_run_id: str,
    ) -> tuple[str, ...]:
        async with self.write_transaction() as db:
            return await self._clone_snapshot_lease_tx(
                db,
                snapshot_ref=snapshot_ref,
                source_run_id=source_run_id,
                target_run_id=target_run_id,
                root_run_id=root_run_id,
            )

    async def active_snapshot_refs_for_run(
        self, run_id: str
    ) -> tuple[str, ...]:
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT DISTINCT snapshot_ref
                       FROM capability_snapshot_leases
                       WHERE run_id=? AND released_at IS NULL
                       ORDER BY snapshot_ref""",
                    (run_id,),
                )
            ).fetchall()
            return tuple(str(row["snapshot_ref"]) for row in rows)

    async def terminal_snapshot_lease_owners(
        self,
    ) -> tuple[tuple[str, str], ...]:
        """Find exact terminal run leases left by a post-commit crash."""

        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT DISTINCT lease.snapshot_ref,lease.run_id
                       FROM capability_snapshot_leases AS lease
                       JOIN execution_runs AS run ON run.run_id=lease.run_id
                       WHERE lease.released_at IS NULL
                         AND run.status IN ('completed','failed','cancelled')
                       ORDER BY lease.run_id,lease.snapshot_ref"""
                )
            ).fetchall()
            return tuple(
                (str(row["snapshot_ref"]), str(row["run_id"]))
                for row in rows
            )

    async def version_has_active_lease(
        self, pack_id: str, version: str, manifest_hash: str
    ) -> bool:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT 1 FROM capability_snapshot_leases
                       WHERE pack_id=? AND version=? AND manifest_hash=?
                         AND released_at IS NULL LIMIT 1""",
                    (pack_id, version, manifest_hash),
                )
            ).fetchone()
            return row is not None

    async def record_search_receipt(
        self,
        *,
        receipt_id: str,
        root_run_id: str,
        catalog_stamp_fingerprint: str,
        query_hash: str,
        result: Mapping[str, JsonValue],
        created_at: float,
    ) -> None:
        result_json = canonical_json(dict(result))
        async with self.write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM capability_search_receipts
                       WHERE receipt_id=? OR (
                           root_run_id=? AND catalog_stamp_fingerprint=?
                           AND query_hash=?
                       )""",
                    (
                        receipt_id,
                        root_run_id,
                        catalog_stamp_fingerprint,
                        query_hash,
                    ),
                )
            ).fetchone()
            if row is not None:
                if (
                    str(row["receipt_id"]) != receipt_id
                    or str(row["result_json"]) != result_json
                ):
                    raise CapabilityStoreConflict(
                        "search_receipt_conflict",
                        "search receipt identity changed",
                    )
                return
            await db.execute(
                """INSERT INTO capability_search_receipts(
                    receipt_id,root_run_id,catalog_stamp_fingerprint,query_hash,
                    result_json,created_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    receipt_id,
                    root_run_id,
                    catalog_stamp_fingerprint,
                    query_hash,
                    result_json,
                    created_at,
                ),
            )

    async def has_search_evidence(
        self, *, root_run_id: str, catalog_stamp_fingerprint: str
    ) -> bool:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT 1 FROM capability_search_receipts
                       WHERE root_run_id=? AND catalog_stamp_fingerprint=?
                       LIMIT 1""",
                    (root_run_id, catalog_stamp_fingerprint),
                )
            ).fetchone()
            return row is not None

    async def _put_failure_receipt_tx(
        self,
        db: aiosqlite.Connection,
        receipt: CapabilityFailureReceipt,
    ) -> CapabilityFailureReceipt:
        payload = (
            receipt.receipt_ref,
            receipt.root_run_id,
            receipt.run_id,
            receipt.attempt_id,
            receipt.failure_report_ref,
            receipt.provider_call_id,
            receipt.effect_id,
            receipt.capability_id,
            receipt.pack_version,
            receipt.manifest_hash,
            receipt.tool_name,
            receipt.tool_spec_fingerprint,
            receipt.binding_scope,
            receipt.scope_key,
            canonical_json(dict(receipt.canonical_args)),
            receipt.args_hash,
            receipt.error_code,
            receipt.error_fingerprint,
            canonical_json(list(receipt.evidence_refs)),
        )
        existing = await (
            await db.execute(
                """SELECT * FROM capability_failure_receipts
                   WHERE receipt_ref=? OR effect_id=? OR (
                       root_run_id=? AND failure_report_ref=?
                   )""",
                (
                    receipt.receipt_ref,
                    receipt.effect_id,
                    receipt.root_run_id,
                    receipt.failure_report_ref,
                ),
            )
        ).fetchone()
        if existing is not None:
            current = _failure_receipt_from_row(existing)
            if current != receipt:
                raise CapabilityStoreConflict(
                    "failure_receipt_conflict",
                    "capability failure receipt replay differs",
                )
            return current
        await db.execute(
            """INSERT INTO capability_failure_receipts(
                receipt_ref,root_run_id,run_id,attempt_id,failure_report_ref,
                provider_call_id,effect_id,capability_id,pack_version,
                manifest_hash,tool_name,tool_spec_fingerprint,binding_scope,
                scope_key,canonical_args_json,args_hash,error_code,
                error_fingerprint,evidence_refs_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (*payload, self._clock()),
        )
        return receipt

    async def put_failure_receipt(
        self, receipt: CapabilityFailureReceipt
    ) -> CapabilityFailureReceipt:
        async with self.write_transaction() as db:
            return await self._put_failure_receipt_tx(db, receipt)

    async def issue_failure_receipt(
        self,
        *,
        root_run_id: str,
        run_id: str,
        attempt_id: str,
        failure_report_ref: str,
        provider_call_id: str,
        effect_id: str,
        capability_id: str,
        pack_version: str,
        manifest_hash: str,
        tool_name: str,
        tool_spec_fingerprint: str,
        canonical_args: Mapping[str, JsonValue],
        error_code: str,
        error_fingerprint: str,
        evidence_refs: Sequence[str] = (),
    ) -> CapabilityFailureReceipt:
        """Validate immutable pack provenance and persist a host receipt."""

        async with self.write_transaction() as db:
            version_row = await (
                await db.execute(
                    """SELECT expected_tool_fingerprints_json,descriptor_json
                       FROM capability_versions
                       WHERE pack_id=? AND version=? AND manifest_hash=?""",
                    (capability_id, pack_version, manifest_hash),
                )
            ).fetchone()
            if version_row is None:
                raise CapabilityStoreError(
                    "failure_capability_version_missing",
                    "failed prepared call does not resolve to an installed version",
                )
            expected = {
                str(item)
                for item in _json_array(
                    str(version_row["expected_tool_fingerprints_json"])
                )
            }
            descriptor = _descriptor_from_json(str(version_row["descriptor_json"]))
            if (
                tool_spec_fingerprint not in expected
                or tool_name not in descriptor.provider_tool_names
            ):
                raise CapabilityStoreError(
                    "failure_capability_provenance_mismatch",
                    "failed prepared call does not belong to the immutable pack",
                )
            binding_row = await (
                await db.execute(
                    """SELECT * FROM capability_bindings
                       WHERE pack_id=? AND active_version=?
                         AND active_manifest_hash=? AND enabled=1
                       ORDER BY CASE
                           WHEN scope='run' AND scope_key=? THEN 0
                           WHEN scope='project' THEN 1
                           WHEN scope='user' THEN 2
                           ELSE 3
                       END, generation DESC
                       LIMIT 1""",
                    (
                        capability_id,
                        pack_version,
                        manifest_hash,
                        root_run_id,
                    ),
                )
            ).fetchone()
            if binding_row is None:
                raise CapabilityStoreError(
                    "failure_capability_binding_missing",
                    "failed pack version has no active trusted binding",
                )
            binding = _binding_from_row(binding_row)
            receipt = CapabilityFailureReceiptIssuer.issue(
                root_run_id=root_run_id,
                run_id=run_id,
                attempt_id=attempt_id,
                failure_report_ref=failure_report_ref,
                provider_call_id=provider_call_id,
                effect_id=effect_id,
                capability_id=capability_id,
                pack_version=pack_version,
                manifest_hash=manifest_hash,
                tool_name=tool_name,
                tool_spec_fingerprint=tool_spec_fingerprint,
                binding_scope=binding.scope,
                scope_key=binding.scope_key,
                canonical_args=canonical_args,
                error_code=error_code,
                error_fingerprint=error_fingerprint,
                evidence_refs=evidence_refs,
            )
            return await self._put_failure_receipt_tx(db, receipt)

    async def get_failure_receipt(
        self, receipt_ref: str
    ) -> CapabilityFailureReceipt | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM capability_failure_receipts
                       WHERE receipt_ref=?""",
                    (receipt_ref,),
                )
            ).fetchone()
            return None if row is None else _failure_receipt_from_row(row)

    async def _claim_repair_attempt_tx(
        self,
        db: aiosqlite.Connection,
        *,
        root_run_id: str,
        failure_receipt_ref: str,
        control_call_id: str,
    ) -> CapabilityRepairAttempt:
        receipt_row = await (
            await db.execute(
                """SELECT * FROM capability_failure_receipts
                   WHERE receipt_ref=?""",
                (failure_receipt_ref,),
            )
        ).fetchone()
        if receipt_row is None:
            raise CapabilityStoreError(
                "failure_receipt_missing",
                "capability repair requires a durable host-signed receipt",
            )
        receipt = _failure_receipt_from_row(receipt_row)
        if receipt.root_run_id != root_run_id:
            raise CapabilityStoreError(
                "failure_receipt_root_mismatch",
                "capability failure receipt belongs to another root run",
            )
        replay = await (
            await db.execute(
                """SELECT * FROM capability_repair_attempts
                   WHERE root_run_id=? AND control_call_id=?""",
                (root_run_id, control_call_id),
            )
        ).fetchone()
        if replay is not None:
            attempt = _repair_attempt_from_row(replay)
            if attempt.failure_receipt_ref != failure_receipt_ref:
                raise CapabilityStoreConflict(
                    "repair_attempt_conflict",
                    "control call already belongs to another failure receipt",
                )
            return attempt
        row = await (
            await db.execute(
                """SELECT COALESCE(MAX(attempt_no),0) AS max_attempt
                   FROM capability_repair_attempts
                   WHERE root_run_id=? AND error_fingerprint=?""",
                (root_run_id, receipt.error_fingerprint),
            )
        ).fetchone()
        attempt_no = int(row["max_attempt"] or 0) + 1
        if attempt_no > 3:
            raise CapabilityStoreConflict(
                "capability_repair_budget_exhausted",
                "the same capability failure was already repaired three times",
            )
        now = self._clock()
        await db.execute(
            """INSERT INTO capability_repair_attempts(
                root_run_id,error_fingerprint,attempt_no,failure_receipt_ref,
                control_call_id,status,child_run_id,operation_id,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,'admitted',NULL,NULL,?,?)""",
            (
                root_run_id,
                receipt.error_fingerprint,
                attempt_no,
                failure_receipt_ref,
                control_call_id,
                now,
                now,
            ),
        )
        return CapabilityRepairAttempt(
            root_run_id=root_run_id,
            error_fingerprint=receipt.error_fingerprint,
            attempt_no=attempt_no,
            failure_receipt_ref=failure_receipt_ref,
            control_call_id=control_call_id,
            status="admitted",
        )

    async def claim_repair_attempt(
        self,
        *,
        root_run_id: str,
        failure_receipt_ref: str,
        control_call_id: str,
    ) -> CapabilityRepairAttempt:
        async with self.write_transaction() as db:
            return await self._claim_repair_attempt_tx(
                db,
                root_run_id=root_run_id,
                failure_receipt_ref=failure_receipt_ref,
                control_call_id=control_call_id,
            )

    async def list_repair_attempts(
        self,
        *,
        root_run_id: str,
        error_fingerprint: str | None = None,
    ) -> tuple[CapabilityRepairAttempt, ...]:
        async with self.read_connection() as db:
            if error_fingerprint is None:
                rows = await (
                    await db.execute(
                        """SELECT * FROM capability_repair_attempts
                           WHERE root_run_id=?
                           ORDER BY error_fingerprint,attempt_no""",
                        (root_run_id,),
                    )
                ).fetchall()
            else:
                rows = await (
                    await db.execute(
                        """SELECT * FROM capability_repair_attempts
                           WHERE root_run_id=? AND error_fingerprint=?
                           ORDER BY attempt_no""",
                        (root_run_id, error_fingerprint),
                    )
                ).fetchall()
            return tuple(_repair_attempt_from_row(row) for row in rows)

    async def _get_policy_state_tx(
        self, db: aiosqlite.Connection
    ) -> AuthorizationPolicyState:
        row = await (
            await db.execute(
                """SELECT mode,generation,updated_at,provenance,
                          schema_generation,user_set_receipt_ref
                   FROM authorization_policy_state WHERE singleton_id=1"""
            )
        ).fetchone()
        if row is None:
            raise CapabilitySchemaMissing(
                "authorization_policy_missing", "policy singleton is absent"
            )
        return AuthorizationPolicyState(
            mode=str(row["mode"]),  # type: ignore[arg-type]
            generation=int(row["generation"]),
            updated_at=float(row["updated_at"]),
            provenance=str(row["provenance"]),  # type: ignore[arg-type]
            schema_generation=int(row["schema_generation"]),
            user_set_receipt_ref=(
                None if row["user_set_receipt_ref"] is None
                else str(row["user_set_receipt_ref"])
            ),
        )

    async def get_policy_state(self) -> AuthorizationPolicyState:
        async with self.read_connection() as db:
            return await self._get_policy_state_tx(db)

    async def _compare_and_set_policy_mode_tx(
        self,
        db: aiosqlite.Connection,
        mode: AuthorizationMode,
        *,
        expected_generation: int,
        provenance: AuthorizationPolicyProvenance = "user_explicit",
        user_set_receipt_ref: str | None = None,
    ) -> AuthorizationPolicyState:
        current = await self._get_policy_state_tx(db)
        successor = current.transition(
            mode, expected_generation=expected_generation, now=self._clock(),
            provenance=provenance,
            user_set_receipt_ref=user_set_receipt_ref,
        )
        cursor = await db.execute(
            """UPDATE authorization_policy_state
               SET mode=?,generation=?,updated_at=?,provenance=?,
                   schema_generation=?,user_set_receipt_ref=?
               WHERE singleton_id=1 AND generation=?""",
            (
                successor.mode,
                successor.generation,
                successor.updated_at,
                successor.provenance,
                successor.schema_generation,
                successor.user_set_receipt_ref,
                expected_generation,
            ),
        )
        if cursor.rowcount != 1:
            raise CapabilityStoreConflict(
                "policy_generation_conflict", "authorization policy changed"
            )
        return successor

    async def compare_and_set_policy_mode(
        self,
        mode: AuthorizationMode,
        *,
        expected_generation: int,
        provenance: AuthorizationPolicyProvenance = "user_explicit",
        user_set_receipt_ref: str | None = None,
    ) -> AuthorizationPolicyState:
        async with self.write_transaction() as db:
            return await self._compare_and_set_policy_mode_tx(
                db, mode, expected_generation=expected_generation,
                provenance=provenance,
                user_set_receipt_ref=user_set_receipt_ref,
            )

    async def _get_legacy_authorization_import_tx(
        self,
        db: aiosqlite.Connection,
        source_key: str,
    ) -> LegacyAuthorizationImportRecord | None:
        row = await (
            await db.execute(
                """SELECT source_key,outcome,source_fingerprint,imported_mode,
                          error_code,imported_at
                   FROM authorization_policy_legacy_imports
                   WHERE source_key=?""",
                (source_key,),
            )
        ).fetchone()
        if row is None:
            return None
        return _legacy_authorization_import_from_row(row)

    async def get_legacy_authorization_import(
        self, source_key: str
    ) -> LegacyAuthorizationImportRecord | None:
        async with self.read_connection() as db:
            return await self._get_legacy_authorization_import_tx(db, source_key)

    async def _put_legacy_authorization_import_tx(
        self,
        db: aiosqlite.Connection,
        record: LegacyAuthorizationImportRecord,
    ) -> LegacyAuthorizationImportRecord:
        existing = await self._get_legacy_authorization_import_tx(
            db, record.source_key
        )
        if existing is not None:
            if existing != record:
                raise CapabilityStoreConflict(
                    "legacy_authorization_import_conflict",
                    "legacy authorization source was already consumed",
                )
            return existing
        await db.execute(
            """INSERT INTO authorization_policy_legacy_imports(
                   source_key,outcome,source_fingerprint,imported_mode,
                   error_code,imported_at
               ) VALUES(?,?,?,?,?,?)""",
            (
                record.source_key,
                record.outcome,
                record.source_fingerprint,
                record.imported_mode,
                record.error_code,
                record.imported_at,
            ),
        )
        return record

    async def put_legacy_authorization_import(
        self, record: LegacyAuthorizationImportRecord
    ) -> LegacyAuthorizationImportRecord:
        async with self.write_transaction() as db:
            return await self._put_legacy_authorization_import_tx(db, record)

    async def _put_task_grant_tx(
        self, db: aiosqlite.Connection, grant: TaskGrant
    ) -> TaskGrant:
        grant_json = canonical_json(grant.to_dict())
        existing = await (
            await db.execute(
                "SELECT * FROM task_grants WHERE task_grant_id=?",
                (grant.task_grant_id,),
            )
        ).fetchone()
        if existing is not None:
            if (
                str(existing["grant_fingerprint"]) != grant.fingerprint
                or str(existing["grant_json"]) != grant_json
            ):
                raise CapabilityStoreConflict(
                    "task_grant_conflict",
                    "TaskGrant identity already belongs to another grant",
                )
            return grant
        await db.execute(
            """INSERT INTO task_grants(
                task_grant_id,root_run_id,source,policy_generation,version,
                grant_fingerprint,grant_json,status,created_at,revoked_at
            ) VALUES(?,?,?,?,?,?,?,'active',?,NULL)""",
            (
                grant.task_grant_id,
                grant.root_run_id,
                grant.source,
                grant.policy_generation,
                grant.version,
                grant.fingerprint,
                grant_json,
                self._clock(),
            ),
        )
        return grant

    async def put_task_grant(self, grant: TaskGrant) -> TaskGrant:
        async with self.write_transaction() as db:
            return await self._put_task_grant_tx(db, grant)

    async def get_task_grant(self, task_grant_id: str) -> TaskGrant | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT grant_json FROM task_grants
                       WHERE task_grant_id=? AND status='active'""",
                    (task_grant_id,),
                )
            ).fetchone()
            if row is None:
                return None
            return TaskGrant.from_dict(json.loads(str(row["grant_json"])))

    async def revoke_task_grant(self, task_grant_id: str) -> bool:
        async with self.write_transaction() as db:
            cursor = await db.execute(
                """UPDATE task_grants SET status='revoked',revoked_at=?
                   WHERE task_grant_id=? AND status='active'""",
                (self._clock(), task_grant_id),
            )
            return cursor.rowcount == 1


def _verification_attempt_from_row(row: Mapping[str, Any]) -> CapabilitySkillInstallVerificationAttempt:
    return CapabilitySkillInstallVerificationAttempt(
        attempt_id=str(row["attempt_id"]), intent_id=str(row["intent_id"]),
        attempt_generation=int(row["attempt_generation"]), state_version=int(row["state_version"]),
        status=str(row["status"]), verifier_session_id=str(row["verifier_session_id"]),
        request_id=str(row["request_id"]), turn_id=str(row["turn_id"]),
        expected_run_id=str(row["expected_run_id"]),
        actual_run_id=None if row["actual_run_id"] is None else str(row["actual_run_id"]),
        manager_operation_id=str(row["manager_operation_id"]),
        manager_receipt_hash=str(row["manager_receipt_hash"]),
        committed_set_stamp=str(row["committed_set_stamp"]),
        project_scope_key=str(row["project_scope_key"]),
        expected_member_set_stamp=str(row["expected_member_set_stamp"]),
        run_catalog_content_stamp=None if row["run_catalog_content_stamp"] is None else str(row["run_catalog_content_stamp"]),
        terminal_event_id=None if row["terminal_event_id"] is None else str(row["terminal_event_id"]),
        terminal_event_hash=None if row["terminal_event_hash"] is None else str(row["terminal_event_hash"]),
        evidence_hash=None if row["evidence_hash"] is None else str(row["evidence_hash"]),
        superseded_by_attempt_id=None if row["superseded_by_attempt_id"] is None else str(row["superseded_by_attempt_id"]),
        error=_json_object(None if row["error_json"] is None else str(row["error_json"])),
        created_at=float(row["created_at"]), updated_at=float(row["updated_at"]),
        terminal_at=None if row["terminal_at"] is None else float(row["terminal_at"]),
    )


def _verification_attestation_from_row(
    row: Mapping[str, Any],
) -> CapabilitySkillInstallVerificationAttestation:
    payload = _json_object(str(row["attestation_json"]))
    if payload is None:
        raise CapabilityStoreError("corrupt_json", "attestation is absent")
    return CapabilitySkillInstallVerificationAttestation(
        attestation_id=str(row["attestation_id"]),
        intent_id=str(row["intent_id"]),
        attempt_id=None if row["attempt_id"] is None else str(row["attempt_id"]),
        provenance=str(row["provenance"]),
        runtime_proof_valid=bool(row["runtime_proof_valid"]),
        verification_ref=str(row["verification_ref"]),
        evidence_hash=None if row["evidence_hash"] is None else str(row["evidence_hash"]),
        attestation=payload,
        created_at=float(row["created_at"]),
    )


async def _get_skill_install_verification_attempt(self, attempt_id: str):
    async with self.read_connection() as db:
        row = await (await db.execute(
            "SELECT * FROM capability_skill_install_verification_attempts WHERE attempt_id=?",
            (attempt_id,),
        )).fetchone()
        return None if row is None else _verification_attempt_from_row(row)


async def _get_current_skill_install_verification_attempt(self, intent_id: str):
    async with self.read_connection() as db:
        row = await (await db.execute(
            "SELECT a.* FROM capability_skill_install_intents i JOIN "
            "capability_skill_install_verification_attempts a "
            "ON a.attempt_id=i.current_verification_attempt_id "
            "WHERE i.intent_id=?", (intent_id,),
        )).fetchone()
        return None if row is None else _verification_attempt_from_row(row)


async def _get_skill_install_verification_attestation(self, intent_id: str):
    async with self.read_connection() as db:
        rows = await (await db.execute(
            "SELECT * FROM capability_skill_install_verification_attestations "
            "WHERE intent_id=? ORDER BY created_at DESC,attestation_id DESC",
            (intent_id,),
        )).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise CapabilityStoreConflict(
                "skill_install_verification_attestation_ambiguous",
                "intent has multiple verification attestations",
            )
        return _verification_attestation_from_row(rows[0])


async def _allocate_skill_install_verification_attempt(
    self, intent_id: str, *, expected_state_version: int,
    manager_operation_id: str, manager_receipt_hash: str,
    committed_set_stamp: str, project_scope_key: str,
    expected_member_set_stamp: str, verifier_session_id: str,
):
    async with self.write_transaction() as db:
        row = await (await db.execute(
            "SELECT * FROM capability_skill_install_intents WHERE intent_id=?", (intent_id,)
        )).fetchone()
        if row is None:
            raise CapabilityStoreError("skill_install_intent_not_found", intent_id)
        intent = _skill_install_intent_from_row(row)
        if intent.current_verification_attempt_id:
            current = await (await db.execute(
                "SELECT * FROM capability_skill_install_verification_attempts WHERE attempt_id=?",
                (intent.current_verification_attempt_id,),
            )).fetchone()
            if current is None:
                raise CapabilityStoreConflict("skill_install_verification_attempt_missing", "current attempt is missing")
            existing = _verification_attempt_from_row(current)
            if (
                existing.manager_operation_id != manager_operation_id
                or existing.manager_receipt_hash != manager_receipt_hash
                or existing.committed_set_stamp != committed_set_stamp
                or existing.project_scope_key != project_scope_key
                or existing.expected_member_set_stamp != expected_member_set_stamp
                or existing.verifier_session_id != verifier_session_id
            ):
                raise CapabilityStoreConflict(
                    "skill_install_verification_attempt_conflict",
                    "current attempt differs from immutable allocation",
                )
            return existing
        if intent.state_version != expected_state_version or intent.status != "published_pending_runtime_verification":
            raise CapabilityStoreConflict("skill_install_verification_attempt_cas_conflict", "intent cannot allocate verification")
        generation = intent.verification_attempt_generation + 1
        identity = {
            "domain":"skill-install-verification-attempt-v1","intent_id":intent_id,
            "attempt_generation":generation,"manager_operation_id":manager_operation_id,
            "manager_receipt_hash":manager_receipt_hash,"committed_set_stamp":committed_set_stamp,
            "project_scope_key":project_scope_key,"expected_member_set_stamp":expected_member_set_stamp,
            "verifier_session_id":verifier_session_id,
        }
        attempt_id = fingerprint_json(identity)
        request_id = f"skill-install-verify-request:{attempt_id}"
        turn_id = f"skill-install-verify-turn:{attempt_id}"
        root_key = root_idempotency_key(verifier_session_id, request_id, turn_id)
        expected_run_id = uuid.uuid5(uuid.NAMESPACE_URL, f"deskpet:{root_key}").hex
        now = self._clock()
        await db.execute(
            "INSERT INTO capability_skill_install_verification_attempts("
            "attempt_id,intent_id,attempt_generation,state_version,status,verifier_session_id,"
            "request_id,turn_id,expected_run_id,manager_operation_id,manager_receipt_hash,"
            "committed_set_stamp,project_scope_key,expected_member_set_stamp,created_at,updated_at) "
            "VALUES(?,?,?,1,'prepared',?,?,?,?,?,?,?,?,?,?,?)",
            (attempt_id,intent_id,generation,verifier_session_id,request_id,turn_id,expected_run_id,
             manager_operation_id,manager_receipt_hash,committed_set_stamp,project_scope_key,
             expected_member_set_stamp,now,now),
        )
        changed = await db.execute(
            "UPDATE capability_skill_install_intents SET verification_attempt_generation=?,"
            "current_verification_attempt_id=?,state_version=state_version+1,updated_at=? "
            "WHERE intent_id=? AND state_version=? AND current_verification_attempt_id IS NULL",
            (generation,attempt_id,now,intent_id,expected_state_version),
        )
        if changed.rowcount != 1:
            raise CapabilityStoreConflict("skill_install_verification_attempt_cas_conflict", "attempt allocation lost CAS")
        row = await (await db.execute(
            "SELECT * FROM capability_skill_install_verification_attempts WHERE attempt_id=?", (attempt_id,)
        )).fetchone()
        return _verification_attempt_from_row(row)


async def _cas_skill_install_verification_attempt(
    self, attempt_id: str, *, expected_state_version: int, status: str, **fields: Any,
):
    transitions = {"prepared":{"launching","unknown","terminal_failed"},
                   "launching":{"running","unknown","terminal_failed"},
                   "running":{"terminal_succeeded","terminal_failed","unknown"},
                   "unknown":{"launching","running","terminal_failed","superseded"},
                   "terminal_failed":{"superseded"}}
    async with self.write_transaction() as db:
        row = await (await db.execute(
            "SELECT * FROM capability_skill_install_verification_attempts WHERE attempt_id=?", (attempt_id,)
        )).fetchone()
        if row is None:
            raise CapabilityStoreError("skill_install_verification_attempt_not_found", attempt_id)
        current = _verification_attempt_from_row(row)
        if current.state_version != expected_state_version or status not in transitions.get(current.status,set()):
            raise CapabilityStoreConflict("skill_install_verification_attempt_cas_conflict", "attempt transition conflict")
        allowed = ("actual_run_id","run_catalog_content_stamp","terminal_event_id","terminal_event_hash","evidence_hash","superseded_by_attempt_id")
        values = [fields.get(name) for name in allowed]
        now = self._clock()
        changed = await db.execute(
            "UPDATE capability_skill_install_verification_attempts SET status=?,state_version=state_version+1,"
            + ",".join(f"{name}=COALESCE(?,{name})" for name in allowed)
            + ",error_json=?,updated_at=?,terminal_at=? WHERE attempt_id=? AND state_version=?",
            (status,*values,None if fields.get("error") is None else canonical_json(dict(fields["error"])),
             now, now if status.startswith("terminal_") else None,attempt_id,expected_state_version),
        )
        if changed.rowcount != 1:
            raise CapabilityStoreConflict("skill_install_verification_attempt_cas_conflict", "attempt CAS lost")
        row = await (await db.execute("SELECT * FROM capability_skill_install_verification_attempts WHERE attempt_id=?",(attempt_id,))).fetchone()
        return _verification_attempt_from_row(row)


async def _attach_skill_install_verification_attestation(
    self, intent_id: str, *, expected_intent_state_version: int, attempt_id: str,
    expected_attempt_state_version: int, attestation: Mapping[str, JsonValue],
):
    evidence_hash = str(attestation.get("evidence_hash") or "")
    verification_ref = fingerprint_json(dict(attestation))
    attestation_id = fingerprint_json({"intent_id":intent_id,"attempt_id":attempt_id,"verification_ref":verification_ref})
    async with self.write_transaction() as db:
        attempt = await (await db.execute("SELECT * FROM capability_skill_install_verification_attempts WHERE attempt_id=? AND intent_id=?",(attempt_id,intent_id))).fetchone()
        intent = await (await db.execute("SELECT * FROM capability_skill_install_intents WHERE intent_id=?",(intent_id,))).fetchone()
        if attempt is None or intent is None or str(attempt["status"]) != "terminal_succeeded" or int(attempt["state_version"]) != expected_attempt_state_version or int(intent["state_version"]) != expected_intent_state_version or str(intent["current_verification_attempt_id"] or "") != attempt_id or not evidence_hash:
            raise CapabilityStoreConflict("skill_install_verification_attestation_cas_conflict", "attestation authority differs")
        now = self._clock()
        await db.execute("INSERT INTO capability_skill_install_verification_attestations(attestation_id,intent_id,attempt_id,provenance,runtime_proof_valid,verification_ref,evidence_hash,attestation_json,created_at) VALUES(?,?,?,'runtime_v3',1,?,?,?,?)",(attestation_id,intent_id,attempt_id,verification_ref,evidence_hash,canonical_json(dict(attestation)),now))
        changed = await db.execute("UPDATE capability_skill_install_intents SET status='succeeded',verification_ref=?,state_version=state_version+1,updated_at=? WHERE intent_id=? AND state_version=? AND current_verification_attempt_id=? AND status='published_pending_runtime_verification'",(verification_ref,now,intent_id,expected_intent_state_version,attempt_id))
        if changed.rowcount != 1:
            raise CapabilityStoreConflict("skill_install_verification_attestation_cas_conflict", "intent attestation CAS lost")
        return CapabilitySkillInstallVerificationAttestation(
            attestation_id=attestation_id,
            intent_id=intent_id,
            attempt_id=attempt_id,
            provenance="runtime_v3",
            runtime_proof_valid=True,
            verification_ref=verification_ref,
            evidence_hash=evidence_hash,
            attestation=dict(attestation),
            created_at=now,
        )


async def _supersede_skill_install_verification_attempt(
    self, intent_id: str, *, expected_intent_state_version: int,
    attempt_id: str, expected_attempt_state_version: int,
    verifier_session_id: str,
):
    """Atomically replace one unresolved/failed attempt with its next generation."""
    async with self.write_transaction() as db:
        intent_row = await (await db.execute(
            "SELECT * FROM capability_skill_install_intents WHERE intent_id=?", (intent_id,)
        )).fetchone()
        attempt_row = await (await db.execute(
            "SELECT * FROM capability_skill_install_verification_attempts "
            "WHERE attempt_id=? AND intent_id=?", (attempt_id, intent_id)
        )).fetchone()
        if intent_row is None or attempt_row is None:
            raise CapabilityStoreError("skill_install_verification_attempt_not_found", attempt_id)
        old = _verification_attempt_from_row(attempt_row)
        if (
            int(intent_row["state_version"]) != expected_intent_state_version
            or str(intent_row["current_verification_attempt_id"] or "") != attempt_id
            or old.state_version != expected_attempt_state_version
            or old.status not in {"unknown", "terminal_failed"}
        ):
            raise CapabilityStoreConflict(
                "skill_install_verification_attempt_cas_conflict",
                "attempt supersession authority differs",
            )
        generation = old.attempt_generation + 1
        identity = {
            "domain": "skill-install-verification-attempt-v1",
            "intent_id": intent_id,
            "attempt_generation": generation,
            "manager_operation_id": old.manager_operation_id,
            "manager_receipt_hash": old.manager_receipt_hash,
            "committed_set_stamp": old.committed_set_stamp,
            "project_scope_key": old.project_scope_key,
            "expected_member_set_stamp": old.expected_member_set_stamp,
            "verifier_session_id": verifier_session_id,
        }
        successor_id = fingerprint_json(identity)
        request_id = f"skill-install-verify-request:{successor_id}"
        turn_id = f"skill-install-verify-turn:{successor_id}"
        root_key = root_idempotency_key(verifier_session_id, request_id, turn_id)
        expected_run_id = uuid.uuid5(uuid.NAMESPACE_URL, f"deskpet:{root_key}").hex
        now = self._clock()
        changed = await db.execute(
            "UPDATE capability_skill_install_verification_attempts SET "
            "status='superseded',state_version=state_version+1,updated_at=? "
            "WHERE attempt_id=? AND state_version=? AND status IN ('unknown','terminal_failed')",
            (now, attempt_id, expected_attempt_state_version),
        )
        if changed.rowcount != 1:
            raise CapabilityStoreConflict(
                "skill_install_verification_attempt_cas_conflict", "attempt supersession lost CAS"
            )
        await db.execute(
            "INSERT INTO capability_skill_install_verification_attempts("
            "attempt_id,intent_id,attempt_generation,state_version,status,verifier_session_id,"
            "request_id,turn_id,expected_run_id,manager_operation_id,manager_receipt_hash,"
            "committed_set_stamp,project_scope_key,expected_member_set_stamp,created_at,updated_at) "
            "VALUES(?,?,?,1,'prepared',?,?,?,?,?,?,?,?,?,?,?)",
            (successor_id, intent_id, generation, verifier_session_id, request_id, turn_id,
             expected_run_id, old.manager_operation_id, old.manager_receipt_hash,
             old.committed_set_stamp, old.project_scope_key, old.expected_member_set_stamp,
             now, now),
        )
        await db.execute(
            "UPDATE capability_skill_install_verification_attempts "
            "SET superseded_by_attempt_id=? WHERE attempt_id=?",
            (successor_id, attempt_id),
        )
        changed = await db.execute(
            "UPDATE capability_skill_install_intents SET verification_attempt_generation=?,"
            "current_verification_attempt_id=?,state_version=state_version+1,updated_at=? "
            "WHERE intent_id=? AND state_version=? AND current_verification_attempt_id=?",
            (generation, successor_id, now, intent_id, expected_intent_state_version, attempt_id),
        )
        if changed.rowcount != 1:
            raise CapabilityStoreConflict(
                "skill_install_verification_attempt_cas_conflict", "intent supersession lost CAS"
            )
        row = await (await db.execute(
            "SELECT * FROM capability_skill_install_verification_attempts WHERE attempt_id=?",
            (successor_id,),
        )).fetchone()
        return _verification_attempt_from_row(row)


# The install aggregate implementation is shared by the transaction facade and
# repository.  Bind it explicitly here so callers get both caller-owned and
# self-owned transaction APIs without duplicating the CAS implementation.
for _skill_install_method in (
    "_create_skill_install_intent_tx", "create_skill_install_intent",
    "get_skill_install_intent", "get_skill_install_intent_for_effect",
    "_skill_install_members_tx",
    "skill_install_members", "pending_skill_install_intents",
    "_cas_skill_install_intent_tx", "cas_skill_install_intent",
    "_handoff_skill_install_intent_tx", "handoff_skill_install_intent",
    "operation_members", "put_operation_evidence",
    "put_publish_intent_members", "publish_intent_members", "operation_evidence",
    "bind_skill_install_confirmation",
):
    setattr(CapabilityStore, _skill_install_method, getattr(CapabilityStoreTx, _skill_install_method))

for _name, _method in {
    "get_skill_install_verification_attempt": _get_skill_install_verification_attempt,
    "get_current_skill_install_verification_attempt": _get_current_skill_install_verification_attempt,
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
]
