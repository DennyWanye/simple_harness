# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Connection, transactions, generations and owner detail keys."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import aiosqlite

from deskpet.permissions.policy import (
    AuthorizationMode,
    AuthorizationPolicyProvenance,
    AuthorizationPolicyState,
)
from deskpet.types.task_grants import TaskGrant

from ..contracts import (
    EMPTY_OWNER_BINDING_SET_STAMP,
    EMPTY_RECEIPT_SET_HASH,
    CapabilityBinding,
    JsonValue,
    OwnerBindingSetStamp,
    OwnerScopeKey,
    PlatformDetailToken,
    RunCatalogContentStamp,
    canonical_json,
)
from ..failure_receipts import CapabilityFailureReceipt, CapabilityRepairAttempt
from ..refresh_contracts import (
    CapabilityOperationReceipt,
    CapabilityRefreshCommit,
    CapabilityRefreshIntent,
)
from .support import (
    CapabilityOperationRecord,
    CapabilityPublishIntent,
    CapabilityRuntimeCallLease,
    CapabilityRuntimeLease,
    CapabilityRuntimePrepareIntent,
    CapabilityRuntimeSetRecord,
    CapabilitySchemaMissing,
    CapabilitySnapshotLeaseIntent,
    CapabilitySnapshotLeaseReleaseReceipt,
    CapabilityStoreConflict,
    CapabilityStoreError,
    CapabilityVersionRecord,
    CapabilityVersionStorage,
    LegacyAuthorizationImportRecord,
    PreparedRunCatalogProjection,
    SnapshotLeaseAdoptionReceipt,
    _binding_from_row,
    _detail_token_from_row,
    _json_object,
)

if TYPE_CHECKING:
    from ..store import CapabilityStore


CAPABILITY_SCHEMA_VERSION = 4


class ExecutionDatabaseOwner(Protocol):
    path: Path

    async def initialize(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CapabilityStoreState:
    schema_version: int
    catalog_generation: int
    binding_generation: int
    pending_publish_count: int


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


def _skill_install_intent_from_row(
    row: Mapping[str, Any],
) -> CapabilitySkillInstallIntent:
    source = _json_object(str(row["source_json"]))
    if source is None:
        raise CapabilityStoreError("corrupt_skill_install_intent", "source is null")
    return CapabilitySkillInstallIntent(
        intent_id=str(row["intent_id"]),
        effect_id=str(row["effect_id"]),
        call_id=str(row["call_id"]),
        root_run_id=str(row["root_run_id"]),
        run_id=str(row["run_id"]),
        channel=str(row["channel"]),
        project_scope_key=str(row["project_scope_key"]),
        principal_id=str(row["principal_id"]),
        source=source,
        exact_commit=str(row["exact_commit"]),
        archive_hash=str(row["archive_hash"]),
        raw_tree_hash=str(row["raw_tree_hash"]),
        member_set_stamp=str(row["member_set_stamp"]),
        permission_set_hash=str(row["permission_set_hash"]),
        confirmation_nonce=str(row["confirmation_nonce"]),
        confirmation_version=int(row["confirmation_version"]),
        expires_at=float(row["expires_at"]),
        status=str(row["status"]),
        state_version=int(row["state_version"]),
        settlement_ref=None if row["settlement_ref"] is None else str(row["settlement_ref"]),
        cleanup_ref=None if row["cleanup_ref"] is None else str(row["cleanup_ref"]),
        verification_ref=None if row["verification_ref"] is None else str(row["verification_ref"]),
        error=_json_object(None if row["error_json"] is None else str(row["error_json"])),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        verification_attempt_generation=int(row["verification_attempt_generation"])
        if "verification_attempt_generation" in row.keys()
        else 0,
        current_verification_attempt_id=(
            None
            if "current_verification_attempt_id" not in row.keys()
            or row["current_verification_attempt_id"] is None
            else str(row["current_verification_attempt_id"])
        ),
        migrated_verification_provenance=(
            None
            if "migrated_verification_provenance" not in row.keys()
            or row["migrated_verification_provenance"] is None
            else str(row["migrated_verification_provenance"])
        ),
    )


def _skill_install_member_from_row(row: Mapping[str, Any]) -> CapabilitySkillInstallMember:
    member = _json_object(str(row["member_json"]))
    if member is None:
        raise CapabilityStoreError("corrupt_skill_install_member", "member is null")
    return CapabilitySkillInstallMember(
        intent_id=str(row["intent_id"]),
        ordinal=int(row["ordinal"]),
        normalized_name=str(row["normalized_name"]),
        pack_id=str(row["pack_id"]),
        version=str(row["version"]),
        manifest_hash=str(row["manifest_hash"]),
        content_hash=str(row["content_hash"]),
        source_digest=str(row["source_digest"]),
        member=member,
    )


def _operation_member_from_row(row: Mapping[str, Any]) -> CapabilityOperationMember:
    return CapabilityOperationMember(
        operation_id=str(row["operation_id"]),
        ordinal=int(row["ordinal"]),
        normalized_name=str(row["normalized_name"]),
        pack_id=str(row["pack_id"]),
        version=str(row["version"]),
        manifest_hash=str(row["manifest_hash"]),
        content_hash=str(row["content_hash"]),
        source_digest=str(row["source_digest"]),
        committed_version=None
        if row["committed_version"] is None
        else str(row["committed_version"]),
        committed_manifest_hash=None
        if row["committed_manifest_hash"] is None
        else str(row["committed_manifest_hash"]),
        committed_set_stamp=None
        if row["committed_set_stamp"] is None
        else str(row["committed_set_stamp"]),
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


class CapabilityStoreTx:
    """Capability DML bound to one caller-owned SQLite transaction."""

    def __init__(self, store: CapabilityStore, db: aiosqlite.Connection) -> None:
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
        existing = await (
            await self._db.execute(
                "SELECT * FROM capability_publish_intent_members "
                "WHERE publish_intent_id=? ORDER BY ordinal",
                (publish_intent_id,),
            )
        ).fetchall()
        if existing:
            current = tuple(
                CapabilityPublishIntentMember(
                    publish_intent_id=str(row["publish_intent_id"]),
                    operation_id=str(row["operation_id"]),
                    ordinal=int(row["ordinal"]),
                    old_binding=_json_object(
                        None if row["old_binding_json"] is None else str(row["old_binding_json"])
                    ),
                    new_binding=_json_object(str(row["new_binding_json"])) or {},
                )
                for row in existing
            )
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
                (
                    member.publish_intent_id,
                    member.operation_id,
                    member.ordinal,
                    None
                    if member.old_binding is None
                    else canonical_json(dict(member.old_binding)),
                    canonical_json(dict(member.new_binding)),
                ),
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

    async def prepare_runtime_set(self, **kwargs: Any) -> CapabilityRuntimeSetRecord:
        return await self._store._prepare_runtime_set_tx(self._db, **kwargs)

    async def mark_runtime_instance_ready(
        self, operation_id: str, runtime_instance_id: str, **kwargs: Any
    ) -> CapabilityRuntimePrepareIntent:
        return await self._store._mark_runtime_instance_ready_tx(
            self._db, operation_id, runtime_instance_id, **kwargs
        )

    async def mark_runtime_set_ready(self, operation_id: str) -> CapabilityRuntimeSetRecord:
        return await self._store._mark_runtime_set_ready_tx(self._db, operation_id)

    async def retire_runtime_set(self, operation_id: str) -> CapabilityRuntimeSetRecord:
        return await self._store._retire_runtime_set_tx(self._db, operation_id)

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
        self,
        intent: CapabilitySkillInstallIntent,
        members: Sequence[CapabilitySkillInstallMember],
    ) -> CapabilitySkillInstallIntent:
        return await self._store._create_skill_install_intent_tx(self._db, intent, members)

    async def cas_skill_install_intent(
        self, intent_id: str, **kwargs: Any
    ) -> CapabilitySkillInstallIntent:
        return await self._store._cas_skill_install_intent_tx(self._db, intent_id, **kwargs)

    async def handoff_skill_install_intent(
        self, intent_id: str, **kwargs: Any
    ) -> CapabilitySkillInstallHandoff:
        return await self._store._handoff_skill_install_intent_tx(self._db, intent_id, **kwargs)

    async def bind_operation_pack_id(
        self, operation_id: str, pack_id: str
    ) -> CapabilityOperationRecord:
        return await self._store._bind_operation_pack_id_tx(self._db, operation_id, pack_id)

    async def record_phase_intent(self, operation_id: str, phase: str) -> CapabilityOperationRecord:
        return await self._store._record_phase_intent_tx(self._db, operation_id, phase)

    async def commit_phase(
        self,
        operation_id: str,
        phase: str,
        *,
        evidence: Mapping[str, JsonValue] | None = None,
    ) -> CapabilityOperationRecord:
        return await self._store._commit_phase_tx(self._db, operation_id, phase, evidence=evidence)

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

    async def release_snapshot_lease(self, snapshot_ref: str, run_id: str) -> int:
        return await self._store._release_snapshot_lease_tx(self._db, snapshot_ref, run_id)

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
        return await self._store.prepare_run_catalog_projection_in_tx(self._db, **kwargs)

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
        return await self._store._fail_refresh_intent_tx(self._db, intent_id, error=error)

    async def create_runtime_lease(self, **kwargs: Any) -> CapabilityRuntimeLease:
        return await self._store._create_runtime_lease_tx(self._db, **kwargs)

    async def transition_runtime_lease(
        self, runtime_lease_id: str, **kwargs: Any
    ) -> CapabilityRuntimeLease:
        return await self._store._transition_runtime_lease_tx(self._db, runtime_lease_id, **kwargs)

    async def reconnect_runtime_lease(
        self, runtime_lease_id: str, **kwargs: Any
    ) -> CapabilityRuntimeLease:
        return await self._store._reconnect_runtime_lease_tx(self._db, runtime_lease_id, **kwargs)

    async def claim_runtime_call(self, **kwargs: Any) -> CapabilityRuntimeCallLease:
        return await self._store._claim_runtime_call_tx(self._db, **kwargs)

    async def mark_runtime_call_running(self, call_lease_id: str) -> CapabilityRuntimeCallLease:
        return await self._store._mark_runtime_call_running_tx(self._db, call_lease_id)

    async def settle_runtime_call(self, call_lease_id: str) -> CapabilityRuntimeCallLease:
        return await self._store._settle_runtime_call_tx(self._db, call_lease_id)

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
        self,
        db: aiosqlite.Connection,
        intent: CapabilitySkillInstallIntent,
        members: Sequence[CapabilitySkillInstallMember],
    ) -> CapabilitySkillInstallIntent:
        if intent.state_version != 1 or intent.status != "staging":
            raise CapabilityStoreError(
                "invalid_skill_install_intent", "new intent must be staging at version 1"
            )
        if tuple(member.ordinal for member in members) != tuple(range(len(members))):
            raise CapabilityStoreError(
                "invalid_skill_install_members", "member ordinals must be dense and ordered"
            )
        if any(member.intent_id != intent.intent_id for member in members):
            raise CapabilityStoreError("invalid_skill_install_members", "member intent differs")
        existing = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_intents WHERE intent_id=? OR (effect_id=? AND call_id=?)",
                (intent.intent_id, intent.effect_id, intent.call_id),
            )
        ).fetchone()
        if existing is not None:
            current = _skill_install_intent_from_row(existing)
            current_members = await self._skill_install_members_tx(db, current.intent_id)
            if current != intent or current_members != tuple(members):
                raise CapabilityStoreConflict(
                    "skill_install_intent_conflict", "install intent identity changed"
                )
            return current
        await db.execute(
            """INSERT INTO capability_skill_install_intents(
                intent_id,effect_id,call_id,root_run_id,run_id,channel,
                project_scope_key,principal_id,source_json,exact_commit,archive_hash,
                raw_tree_hash,member_set_stamp,permission_set_hash,confirmation_nonce,
                confirmation_version,expires_at,status,state_version,settlement_ref,
                cleanup_ref,verification_ref,error_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                intent.intent_id,
                intent.effect_id,
                intent.call_id,
                intent.root_run_id,
                intent.run_id,
                intent.channel,
                intent.project_scope_key,
                intent.principal_id,
                canonical_json(dict(intent.source)),
                intent.exact_commit,
                intent.archive_hash,
                intent.raw_tree_hash,
                intent.member_set_stamp,
                intent.permission_set_hash,
                intent.confirmation_nonce,
                intent.confirmation_version,
                intent.expires_at,
                intent.status,
                intent.state_version,
                intent.settlement_ref,
                intent.cleanup_ref,
                intent.verification_ref,
                None if intent.error is None else canonical_json(dict(intent.error)),
                intent.created_at,
                intent.updated_at,
            ),
        )
        for member in members:
            await db.execute(
                """INSERT INTO capability_skill_install_members(
                    intent_id,ordinal,normalized_name,pack_id,version,manifest_hash,
                    content_hash,source_digest,member_json) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    member.intent_id,
                    member.ordinal,
                    member.normalized_name,
                    member.pack_id,
                    member.version,
                    member.manifest_hash,
                    member.content_hash,
                    member.source_digest,
                    canonical_json(dict(member.member)),
                ),
            )
        return intent

    async def create_skill_install_intent(
        self,
        intent: CapabilitySkillInstallIntent,
        members: Sequence[CapabilitySkillInstallMember],
    ) -> CapabilitySkillInstallIntent:
        async with self.write_transaction() as db:
            return await self._create_skill_install_intent_tx(db, intent, members)

    async def get_skill_install_intent(self, intent_id: str) -> CapabilitySkillInstallIntent | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM capability_skill_install_intents WHERE intent_id=?", (intent_id,)
                )
            ).fetchone()
            return None if row is None else _skill_install_intent_from_row(row)

    async def get_skill_install_intent_for_effect(
        self, effect_id: str, call_id: str
    ) -> CapabilitySkillInstallIntent | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM capability_skill_install_intents "
                    "WHERE effect_id=? AND call_id=?",
                    (effect_id, call_id),
                )
            ).fetchone()
            return None if row is None else _skill_install_intent_from_row(row)

    async def _skill_install_members_tx(
        self, db: aiosqlite.Connection, intent_id: str
    ) -> tuple[CapabilitySkillInstallMember, ...]:
        rows = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_members WHERE intent_id=? ORDER BY ordinal",
                (intent_id,),
            )
        ).fetchall()
        return tuple(_skill_install_member_from_row(row) for row in rows)

    async def skill_install_members(
        self, intent_id: str
    ) -> tuple[CapabilitySkillInstallMember, ...]:
        async with self.read_connection() as db:
            return await self._skill_install_members_tx(db, intent_id)

    async def pending_skill_install_intents(
        self, *, now: float | None = None
    ) -> tuple[CapabilitySkillInstallIntent, ...]:
        async with self.read_connection() as db:
            params: tuple[Any, ...] = () if now is None else (now,)
            expiry = "" if now is None else " AND expires_at<=?"
            rows = await (
                await db.execute(
                    "SELECT * FROM capability_skill_install_intents WHERE status NOT IN "
                    "('succeeded','stage_failed','denied','expired')"
                    + expiry
                    + " ORDER BY expires_at,intent_id",
                    params,
                )
            ).fetchall()
            return tuple(_skill_install_intent_from_row(row) for row in rows)

    async def _cas_skill_install_intent_tx(
        self,
        db: aiosqlite.Connection,
        intent_id: str,
        *,
        expected_state_version: int,
        status: str,
        settlement_ref: str | None = None,
        cleanup_ref: str | None = None,
        verification_ref: str | None = None,
        error: Mapping[str, JsonValue] | None = None,
    ) -> CapabilitySkillInstallIntent:
        row = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_intents WHERE intent_id=?",
                (intent_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError("skill_install_intent_not_found", intent_id)
        current = _skill_install_intent_from_row(row)
        transitions = {
            "staging": {"awaiting_confirmation", "stage_failed_cleanup_pending"},
            "awaiting_confirmation": {
                "publishing",
                "denied_cleanup_pending",
                "expired_cleanup_pending",
            },
            "stage_failed_cleanup_pending": {"stage_failed"},
            "denied_cleanup_pending": {"denied"},
            "expired_cleanup_pending": {"expired"},
            "publishing": {
                "published_pending_runtime_verification",
                "stage_failed_cleanup_pending",
                "unknown",
            },
            "published_pending_runtime_verification": {"succeeded", "unknown"},
            "unknown": {"stage_failed_cleanup_pending"},
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
            (
                status,
                settlement_ref,
                cleanup_ref,
                verification_ref,
                None if error is None else canonical_json(dict(error)),
                self._clock(),
                intent_id,
                expected_state_version,
            ),
        )
        if cursor.rowcount != 1:
            raise CapabilityStoreConflict(
                "skill_install_intent_cas_conflict", "install intent state changed"
            )
        row = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_intents WHERE intent_id=?", (intent_id,)
            )
        ).fetchone()
        return _skill_install_intent_from_row(row)

    async def cas_skill_install_intent(
        self, intent_id: str, **kwargs: Any
    ) -> CapabilitySkillInstallIntent:
        async with self.write_transaction() as db:
            return await self._cas_skill_install_intent_tx(db, intent_id, **kwargs)

    async def claim_skill_install_retry(
        self,
        intent_id: str,
        *,
        failure_receipt_ref: str,
        expected_attempt_generation: int,
        retry_command_id: str,
    ) -> tuple[CapabilitySkillInstallIntent, bool]:
        """CAS-claim one explicit retry generation for a staged source failure."""

        command_id = str(retry_command_id).strip()
        receipt_ref = str(failure_receipt_ref).strip()
        if not command_id or not receipt_ref or expected_attempt_generation < 1:
            raise CapabilityStoreError(
                "skill_install_retry_identity_invalid",
                "Skill install retry identity is incomplete",
            )
        async with self.write_transaction() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM capability_skill_install_intents WHERE intent_id=?",
                    (intent_id,),
                )
            ).fetchone()
            if row is None:
                raise CapabilityStoreError("skill_install_intent_not_found", intent_id)
            current = _skill_install_intent_from_row(row)
            error = dict(current.error or {})
            current_generation = int(error.get("attempt_generation") or 1)
            if (
                error.get("retry_command_id") == command_id
                and current_generation == expected_attempt_generation + 1
            ):
                return current, False
            if (
                current.status != "stage_failed"
                or not bool(error.get("retryable", False))
                or current.settlement_ref != receipt_ref
                or current_generation != expected_attempt_generation
            ):
                raise CapabilityStoreConflict(
                    "skill_install_retry_cas_conflict",
                    "Skill install retry no longer owns the expected failure generation",
                )
            next_error = {
                **error,
                "attempt_generation": current_generation + 1,
                "retry_command_id": command_id,
                "retry_state": "claimed",
                "previous_failure_receipt_ref": receipt_ref,
            }
            cursor = await db.execute(
                "UPDATE capability_skill_install_intents SET "
                "state_version=state_version+1,error_json=?,updated_at=? "
                "WHERE intent_id=? AND state_version=? AND status='stage_failed'",
                (
                    canonical_json(next_error),
                    self._clock(),
                    intent_id,
                    current.state_version,
                ),
            )
            if cursor.rowcount != 1:
                raise CapabilityStoreConflict(
                    "skill_install_retry_cas_conflict",
                    "Skill install retry was claimed concurrently",
                )
            updated = await (
                await db.execute(
                    "SELECT * FROM capability_skill_install_intents WHERE intent_id=?",
                    (intent_id,),
                )
            ).fetchone()
            return _skill_install_intent_from_row(updated), True

    async def settle_skill_install_retry(
        self,
        intent_id: str,
        *,
        expected_state_version: int,
        retry_command_id: str,
        attempt_generation: int,
        retry_state: str,
        error: Mapping[str, JsonValue],
        failure_receipt_ref: str | None = None,
        success_intent_id: str | None = None,
    ) -> CapabilitySkillInstallIntent:
        if retry_state not in {"failed", "succeeded"}:
            raise CapabilityStoreError(
                "skill_install_retry_state_invalid", "Retry terminal state is invalid"
            )
        async with self.write_transaction() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM capability_skill_install_intents WHERE intent_id=?",
                    (intent_id,),
                )
            ).fetchone()
            if row is None:
                raise CapabilityStoreError("skill_install_intent_not_found", intent_id)
            current = _skill_install_intent_from_row(row)
            current_error = dict(current.error or {})
            if (
                current.status != "stage_failed"
                or current.state_version != expected_state_version
                or current_error.get("retry_command_id") != retry_command_id
                or int(current_error.get("attempt_generation") or 0) != attempt_generation
                or current_error.get("retry_state") != "claimed"
            ):
                raise CapabilityStoreConflict(
                    "skill_install_retry_settlement_conflict",
                    "Skill install retry settlement differs from its durable claim",
                )
            next_error = {
                **current_error,
                **dict(error),
                "attempt_generation": attempt_generation,
                "retry_command_id": retry_command_id,
                "retry_state": retry_state,
            }
            if success_intent_id is not None:
                next_error["success_intent_id"] = str(success_intent_id)
            settlement_ref = failure_receipt_ref or current.settlement_ref
            cursor = await db.execute(
                "UPDATE capability_skill_install_intents SET "
                "state_version=state_version+1,settlement_ref=?,error_json=?,updated_at=? "
                "WHERE intent_id=? AND state_version=? AND status='stage_failed'",
                (
                    settlement_ref,
                    canonical_json(next_error),
                    self._clock(),
                    intent_id,
                    expected_state_version,
                ),
            )
            if cursor.rowcount != 1:
                raise CapabilityStoreConflict(
                    "skill_install_retry_settlement_conflict",
                    "Skill install retry settlement was committed concurrently",
                )
            updated = await (
                await db.execute(
                    "SELECT * FROM capability_skill_install_intents WHERE intent_id=?",
                    (intent_id,),
                )
            ).fetchone()
            return _skill_install_intent_from_row(updated)

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
                (
                    confirmation_nonce,
                    confirmation_version,
                    self._clock(),
                    intent_id,
                    expected_state_version,
                ),
            )
            if cursor.rowcount != 1:
                current = await (
                    await db.execute(
                        "SELECT * FROM capability_skill_install_intents WHERE intent_id=?",
                        (intent_id,),
                    )
                ).fetchone()
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
            row = await (
                await db.execute(
                    "SELECT * FROM capability_skill_install_intents WHERE intent_id=?",
                    (intent_id,),
                )
            ).fetchone()
            return _skill_install_intent_from_row(row)

    async def _handoff_skill_install_intent_tx(
        self,
        db: aiosqlite.Connection,
        intent_id: str,
        *,
        expected_state_version: int,
        operation_id: str,
        idempotency_key: str,
        confirmation_receipt_hash: str,
    ) -> CapabilitySkillInstallHandoff:
        existing = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_handoffs WHERE intent_id=? OR operation_id=?",
                (intent_id, operation_id),
            )
        ).fetchone()
        if existing is not None:
            handoff = CapabilitySkillInstallHandoff(
                intent_id=str(existing["intent_id"]),
                operation_id=str(existing["operation_id"]),
                confirmation_receipt_hash=str(existing["confirmation_receipt_hash"]),
                member_set_stamp=str(existing["member_set_stamp"]),
                created_at=float(existing["created_at"]),
            )
            if (
                handoff.intent_id != intent_id
                or handoff.operation_id != operation_id
                or handoff.confirmation_receipt_hash != confirmation_receipt_hash
            ):
                raise CapabilityStoreConflict(
                    "skill_install_handoff_conflict", "handoff identity changed"
                )
            return handoff
        intent_row = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_intents WHERE intent_id=?", (intent_id,)
            )
        ).fetchone()
        if intent_row is None:
            raise CapabilityStoreError("skill_install_intent_not_found", intent_id)
        intent = _skill_install_intent_from_row(intent_row)
        if (
            intent.status != "awaiting_confirmation"
            or intent.state_version != expected_state_version
        ):
            raise CapabilityStoreConflict(
                "skill_install_handoff_cas_conflict", "confirmation is not consumable"
            )
        members = await self._skill_install_members_tx(db, intent_id)
        await self._create_operation_tx(
            db,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            kind="skill_install_batch",
            request={"install_intent_id": intent_id, "member_set_stamp": intent.member_set_stamp},
            root_run_id=intent.root_run_id,
            requested_scope=intent.install_scope,
            requested_scope_key=intent.install_scope_key,
        )
        for member in members:
            await db.execute(
                """INSERT INTO capability_operation_members(operation_id,ordinal,normalized_name,
                   pack_id,version,manifest_hash,content_hash,source_digest) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    operation_id,
                    member.ordinal,
                    member.normalized_name,
                    member.pack_id,
                    member.version,
                    member.manifest_hash,
                    member.content_hash,
                    member.source_digest,
                ),
            )
        now = self._clock()
        await db.execute(
            "INSERT INTO capability_skill_install_handoffs VALUES(?,?,?,?,?)",
            (intent_id, operation_id, confirmation_receipt_hash, intent.member_set_stamp, now),
        )
        await self._cas_skill_install_intent_tx(
            db,
            intent_id,
            expected_state_version=expected_state_version,
            status="publishing",
            settlement_ref=confirmation_receipt_hash,
        )
        return CapabilitySkillInstallHandoff(
            intent_id, operation_id, confirmation_receipt_hash, intent.member_set_stamp, now
        )

    async def handoff_skill_install_intent(
        self, intent_id: str, **kwargs: Any
    ) -> CapabilitySkillInstallHandoff:
        async with self.write_transaction() as db:
            return await self._handoff_skill_install_intent_tx(db, intent_id, **kwargs)

    async def operation_members(self, operation_id: str) -> tuple[CapabilityOperationMember, ...]:
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    "SELECT * FROM capability_operation_members WHERE operation_id=? ORDER BY ordinal",
                    (operation_id,),
                )
            ).fetchall()
            return tuple(_operation_member_from_row(row) for row in rows)

    async def put_publish_intent_members(
        self,
        publish_intent_id: str,
        members: Sequence[CapabilityPublishIntentMember],
    ) -> tuple[CapabilityPublishIntentMember, ...]:
        if tuple(member.ordinal for member in members) != tuple(range(len(members))):
            raise CapabilityStoreError(
                "invalid_publish_intent_members", "member ordinals must be dense and ordered"
            )
        async with self.write_transaction() as db:
            existing = await (
                await db.execute(
                    "SELECT * FROM capability_publish_intent_members WHERE publish_intent_id=? ORDER BY ordinal",
                    (publish_intent_id,),
                )
            ).fetchall()
            if existing:
                current = tuple(
                    CapabilityPublishIntentMember(
                        publish_intent_id=str(row["publish_intent_id"]),
                        operation_id=str(row["operation_id"]),
                        ordinal=int(row["ordinal"]),
                        old_binding=_json_object(
                            None
                            if row["old_binding_json"] is None
                            else str(row["old_binding_json"])
                        ),
                        new_binding=_json_object(str(row["new_binding_json"])) or {},
                    )
                    for row in existing
                )
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
                await db.execute(
                    "INSERT INTO capability_publish_intent_members VALUES(?,?,?,?,?)",
                    (
                        member.publish_intent_id,
                        member.operation_id,
                        member.ordinal,
                        None
                        if member.old_binding is None
                        else canonical_json(dict(member.old_binding)),
                        canonical_json(dict(member.new_binding)),
                    ),
                )
            return tuple(members)

    async def publish_intent_members(
        self, publish_intent_id: str
    ) -> tuple[CapabilityPublishIntentMember, ...]:
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    "SELECT * FROM capability_publish_intent_members WHERE publish_intent_id=? ORDER BY ordinal",
                    (publish_intent_id,),
                )
            ).fetchall()
            return tuple(
                CapabilityPublishIntentMember(
                    publish_intent_id=str(row["publish_intent_id"]),
                    operation_id=str(row["operation_id"]),
                    ordinal=int(row["ordinal"]),
                    old_binding=_json_object(
                        None if row["old_binding_json"] is None else str(row["old_binding_json"])
                    ),
                    new_binding=_json_object(str(row["new_binding_json"])) or {},
                )
                for row in rows
            )

    async def put_operation_evidence(
        self, evidence: CapabilityOperationEvidence
    ) -> CapabilityOperationEvidence:
        payload = canonical_json(dict(evidence.evidence))
        async with self.write_transaction() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM capability_operation_evidence WHERE idempotency_key=?",
                    (evidence.idempotency_key,),
                )
            ).fetchone()
            if row is not None:
                current = CapabilityOperationEvidence(
                    str(row["operation_id"]),
                    str(row["evidence_kind"]),
                    str(row["idempotency_key"]),
                    _json_object(str(row["evidence_json"])) or {},
                    float(row["created_at"]),
                )
                if current != evidence:
                    raise CapabilityStoreConflict(
                        "operation_evidence_conflict", "evidence identity changed"
                    )
                return current
            await db.execute(
                "INSERT INTO capability_operation_evidence VALUES(?,?,?,?,?)",
                (
                    evidence.operation_id,
                    evidence.evidence_kind,
                    evidence.idempotency_key,
                    payload,
                    evidence.created_at,
                ),
            )
            return evidence

    async def operation_evidence(
        self, operation_id: str
    ) -> tuple[CapabilityOperationEvidence, ...]:
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    "SELECT * FROM capability_operation_evidence WHERE operation_id=? ORDER BY created_at,evidence_kind,idempotency_key",
                    (operation_id,),
                )
            ).fetchall()
            return tuple(
                CapabilityOperationEvidence(
                    operation_id=str(row["operation_id"]),
                    evidence_kind=str(row["evidence_kind"]),
                    idempotency_key=str(row["idempotency_key"]),
                    evidence=_json_object(str(row["evidence_json"])) or {},
                    created_at=float(row["created_at"]),
                )
                for row in rows
            )

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
            self._db,
            mode,
            expected_generation=expected_generation,
            provenance=provenance,
            user_set_receipt_ref=user_set_receipt_ref,
        )

    async def get_legacy_authorization_import(
        self, source_key: str
    ) -> LegacyAuthorizationImportRecord | None:
        return await self._store._get_legacy_authorization_import_tx(self._db, source_key)

    async def put_legacy_authorization_import(
        self, record: LegacyAuthorizationImportRecord
    ) -> LegacyAuthorizationImportRecord:
        return await self._store._put_legacy_authorization_import_tx(self._db, record)


class CapabilityStoreCore:
    """Connection, transactions, generations and owner detail keys."""

    if TYPE_CHECKING:

        async def _bind_snapshot_lease_intent_tx(
            self,
            db: aiosqlite.Connection,
            lease_intent_id: str,
            *,
            owner_record_ref: str,
            owner_record_hash: str,
            start_fingerprint: str,
        ) -> CapabilitySnapshotLeaseIntent: ...
        async def _drain_unreferenced_runtimes_tx(
            self, db: aiosqlite.Connection, *, pack_id: str, version: str, manifest_hash: str
        ) -> tuple[CapabilityRuntimeLease, ...]: ...
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
        ) -> CapabilitySnapshotLeaseIntent: ...
        async def _put_run_catalog_snapshot_tx(
            self,
            db: aiosqlite.Connection,
            stamp: RunCatalogContentStamp,
            *,
            request_owner_key: str,
            catalog_generation_vector: Mapping[str, int],
            created_at: float | None = ...,
            require_complete: bool = ...,
        ) -> str: ...
        async def _put_snapshot_lease_rows_tx(
            self, db: aiosqlite.Connection, intent: CapabilitySnapshotLeaseIntent
        ) -> None: ...
        async def release_snapshot_lease_intent_in_tx(
            self,
            db: aiosqlite.Connection,
            lease_intent_id: str,
            *,
            intent_hash: str,
            release_reason: str,
            owner_terminal_or_transition_ref: str,
            owner_terminal_or_transition_hash: str,
        ) -> CapabilitySnapshotLeaseReleaseReceipt: ...

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
        self.product_owned = bool(getattr(execution_database, "is_product_state_owner", False))
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
                        "SELECT schema_version FROM capability_schema_state WHERE singleton_id=1"
                    )
                ).fetchone()
                version = int(state[0]) if state else 0
                expected_version = 2 if self.product_owned else CAPABILITY_SCHEMA_VERSION
                if version != expected_version:
                    raise CapabilitySchemaMissing(
                        "unsupported_capability_schema",
                        f"capability schema is {version}, expected {expected_version}",
                    )
                await self._migrate_authorization_policy_provenance(db)
                await db.commit()
            self._initialized = True

    async def _migrate_authorization_policy_provenance(self, db: aiosqlite.Connection) -> None:
        """Add the v2 provenance columns and classify legacy rows once."""

        columns = {
            str(row[1])
            for row in await (
                await db.execute("PRAGMA table_info(authorization_policy_state)")
            ).fetchall()
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
            raise CapabilityStoreError("owner_detail_missing", "owner detail row was not created")
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
            raise CapabilityStoreError("owner_detail_missing", "owner detail row disappeared")
        return _detail_token_from_row(row)
