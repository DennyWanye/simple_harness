"""SQLite execution and workflow unit of work."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import math
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping, Sequence

import aiosqlite

from deskpet.types.task_work_context import (
    ConversationBoundary,
    QueuedUserContinuation,
    TaskRunProjection,
    TaskWorkContext,
)
from deskpet.execution.contracts import (
    ActorAction,
    ActorContext,
    AdmissionBoundary,
    AdmissionLaunchClaim,
    AdmissionLaunchUnknownFence,
    AdmissionPhase,
    AdmissionResolution,
    AdmissionSpec,
    AttemptFailureSet,
    AttemptRecord,
    AttemptStatus,
    AttachmentPolicy,
    AuthorizationError,
    ChildCommandIntent,
    ChildCommandRecord,
    ChildCommandStatus,
    ChildSignalRecord,
    CreateRunResult,
    DecisionAuthorization,
    DecisionConflict,
    DecisionKind,
    DecisionNotFound,
    DecisionOpen,
    DecisionRecord,
    DecisionSignal,
    DecisionStatus,
    DeliveryClaimConflict,
    DeliveryNotFound,
    DeliveryPolicy,
    DeliveryRecord,
    DeliverySpec,
    DeliveryStatus,
    EventNotFound,
    FinalizeRunResult,
    ExternalWaitState,
    FailureBackfillState,
    GrantConsume,
    GrantConsumeConflict,
    GrantNotFound,
    IdempotencyConflict,
    LegacyRunProjection,
    LinkKind,
    OutcomeStatus,
    ParentCycleError,
    PersistenceLevel,
    PersistenceRequired,
    PlanVersionRecord,
    ProfileLaunchConsumeResult,
    ProfileLaunchTicket,
    ProfileLaunchTicketState,
    ProviderActionAdmissionState,
    ProviderActionBatch,
    ProviderActionCall,
    ProviderBatchAdmissionResult,
    ProviderBatchStatus,
    ProviderInvocationOutcome,
    ProviderInvocationRecord,
    ProviderInvocationStatus,
    ProviderResumeState,
    ProviderTurnFence,
    ProviderTurnState,
    RecoveryLease,
    RunContext,
    RunCreate,
    RunEvent,
    RunEventCandidate,
    RunIdentityConflict,
    RunLinkSpec,
    RunNotFound,
    RunRecord,
    RunRef,
    RunStartSnapshotRecord,
    RunStatus,
    ExecutionRunFenceRecord,
    ExecutionRunFenceStatus,
    StaleRecoveryLease,
    TaskExternalWait,
    TaskFailureReport,
    TaskGoalRecord,
    TaskGoalStatus,
    TERMINAL_ADMISSION_PHASES,
    TERMINAL_RUN_STATUSES,
    TerminalConflict,
    VersionConflict,
    WorkflowRunSeed,
    WorkflowStartResult,
    assert_idempotent_run_intent,
    canonical_json,
    fingerprint_json,
    stable_delivery_id,
    stable_decision_grant_id,
    stable_event_id,
    thaw_json,
    validate_json_value,
)
from deskpet.execution.ports import RunView
from deskpet.execution.evidence import (
    CompletionEvidence,
    EvidenceContext,
    EvidenceSelection,
    UNKNOWN_EVIDENCE,
)
from deskpet.permissions.policy import (
    AuthorizationPolicy,
    AuthorizationPolicyState,
    DecisionContext,
)
from deskpet.types.task_grants import (
    PreparedAuthorizationCommit,
    TaskGrant,
)

from .schema import initialize_workflow_db
from .run_store import RunFence
from .write_lane import ExecutionWriteLane, get_execution_write_lane


_FaultInjector = Callable[[str], None]


# Stable crash windows exercised by the R1 restart matrix.  This is kept
# separate from migration/compatibility fault points: changing this set is a
# durable-protocol change and therefore requires updating fault_matrix.json.
FAULT_HOOKS = frozenset(
    {
        "batch_boundary_after_promotion",
        "batch_boundary_after_continuation",
        "batch_boundary_after_waiting_event",
        "batch_boundary_before_commit",
        "decision_resolve_after_cas",
        "decision_resolve_after_grant",
        "decision_resolve_after_boundary",
        "decision_resolve_before_commit",
        "effect_claim_after_grant",
        "effect_claim_after_effect",
        "effect_claim_before_commit",
        "effect_settle_after_attempt",
        "effect_settle_after_effect",
        "effect_settle_after_link",
        "effect_settle_after_boundary",
        "effect_settle_before_commit",
        "grant_consume_before_commit",
        "finalize_after_outbox",
        "finalize_before_commit",
        "child_command_before_commit",
        "child_schedule_after_run",
        "child_schedule_before_commit",
        "child_terminal_before_commit",
        "child_signal_ack_before_commit",
        "child_apply_after_boundary",
        "child_apply_after_event",
        "child_apply_before_commit",
        "child_finalize_after_terminal",
        "child_finalize_after_parent_signal",
        "checkpoint_consume_decisions_after_write",
        "checkpoint_open_decision_after_write",
        "checkpoint_append_event_after_write",
        "checkpoint_link_effects_after_write",
        "checkpoint_finalize_run_after_write",
    }
)

ATOMIC_OPERATIONS = (
    ("decision_boundary", ("commit_decision",)),
    ("effect_settle_boundary", ("settle_effect",)),
    ("terminal_delivery", ("commit_run_outcome",)),
    ("child_apply_ack", ("ack_child_signal",)),
    ("grant_effect_claim", ("claim_tool_call",)),
    ("durable_promotion", ("persist_react_boundary",)),
    ("durable_child_schedule", ("commit_child_command", "schedule_child_command")),
    ("child_terminal_signal", ("finalize_child_and_enqueue_parent_signal",)),
)


@dataclass(frozen=True, slots=True)
class ExecutionRuntimeState:
    """Durable deployment fence for execution-row ownership."""

    generation: int
    phase: str
    drain_manifest_hash: str | None
    drain_count: int
    created_at: float
    activated_at: float | None
    updated_at: float


@dataclass(frozen=True, order=True, slots=True)
class LegacyDrainRef:
    """Stable identity for one legacy durable run that must be drained."""

    source_kind: str
    source_run_id: str

    @property
    def drain_item_id(self) -> str:
        payload = f"{self.source_kind}\0{self.source_run_id}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class LegacyDrainLease:
    """Fenced claim for one item in the deployment drain manifest."""

    ref: LegacyDrainRef
    manifest_generation: int
    owner: str
    epoch: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class RuntimeActivationCommand:
    """Typed operation routed through the single activation transaction starter."""

    kind: str
    owner: str | None = None
    lease_seconds: float = 30.0
    lease: LegacyDrainLease | None = None
    error: str | None = None

    @classmethod
    def advance(cls) -> "RuntimeActivationCommand":
        return cls("advance")

    @classmethod
    def claim(cls, owner: str, *, lease_seconds: float = 30.0) -> "RuntimeActivationCommand":
        return cls("claim_drain", owner=owner, lease_seconds=lease_seconds)

    @classmethod
    def settle(
        cls, lease: LegacyDrainLease, *, error: str | None = None
    ) -> "RuntimeActivationCommand":
        return cls("settle_drain", lease=lease, error=error)

    @classmethod
    def rollback(cls) -> "RuntimeActivationCommand":
        return cls("rollback")


class RuntimeActivationError(RuntimeError):
    """Fail-closed activation or execution-owner invariant violation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(message)


class CheckpointExecutionError(RuntimeError):
    """Stable failure raised before a joined checkpoint transaction commits."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ContinuationRecord:
    """Product-neutral JSON continuation owned by the execution UoW."""

    run_id: str
    payload: Mapping[str, Any]
    version: int
    pending_decision_id: str | None
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class ExecutionEffectClaim:
    effect_id: str
    run_id: str
    attempt_no: int
    status: str
    worker_owner: str
    worker_epoch: int
    effect_version: int
    action: str = "execute"
    authorization: DecisionAuthorization | None = None


@dataclass(frozen=True, slots=True)
class ExecutionEffectSettlement:
    effect_id: str
    status: str
    effect_version: int
    continuation: ContinuationRecord
    event: RunEvent


@dataclass(frozen=True, slots=True)
class ExecutionEffectHandoff:
    effect_id: str
    attempt_no: int
    status: str
    handoff_state: str
    completion_disposition: str
    effect_version: int


@dataclass(frozen=True, slots=True)
class ExecutionTx:
    """Typed execution operations bound to a caller-owned SQLite transaction."""

    _unit_of_work: "SqliteExecutionUnitOfWork"
    _db: aiosqlite.Connection

    async def freeze_tool_presentation_specs(
        self,
        *,
        root_run_id: str,
        policies: Sequence[Mapping[str, Any]],
        created_at: float,
    ) -> None:
        """Insert-or-verify the immutable Root presentation snapshot."""

        for policy in policies:
            tool_name = str(policy.get("tool_name") or "").strip()
            policy_hash = str(policy.get("policy_hash") or "").strip()
            if not tool_name or len(policy_hash) != 64:
                raise ValueError("invalid tool presentation policy descriptor")
            payload = dict(policy)
            payload.pop("policy_hash", None)
            policy_json = canonical_json(payload)
            actual_hash = hashlib.sha256(policy_json.encode("utf-8")).hexdigest()
            if actual_hash != policy_hash:
                raise ValueError("tool presentation policy hash mismatch")
            existing = await (
                await self._db.execute(
                    "SELECT policy_json,policy_hash FROM "
                    "execution_run_tool_presentation_specs "
                    "WHERE root_run_id=? AND tool_name=?",
                    (root_run_id, tool_name),
                )
            ).fetchone()
            if existing is not None:
                if (str(existing["policy_json"]), str(existing["policy_hash"])) != (
                    policy_json,
                    policy_hash,
                ):
                    raise IdempotencyConflict(
                        "tool_presentation_snapshot_conflict",
                        "Root replay supplied different tool presentation metadata",
                    )
                continue
            await self._db.execute(
                "INSERT INTO execution_run_tool_presentation_specs("
                "root_run_id,tool_name,schema_version,policy_json,policy_hash,created_at) "
                "VALUES(?,?,1,?,?,?)",
                (root_run_id, tool_name, policy_json, policy_hash, created_at),
            )

    async def store_tool_public_projection(
        self,
        *,
        effect_id: str,
        root_run_id: str,
        run_id: str,
        tool_name: str,
        projection: Mapping[str, Any],
        projection_hash: str,
        status: str,
        created_at: float,
    ) -> None:
        """Insert-or-verify one terminal public projection in the open tx."""

        projection_json = canonical_json(dict(projection))
        actual_hash = hashlib.sha256(projection_json.encode("utf-8")).hexdigest()
        if actual_hash != projection_hash:
            raise ValueError("tool public projection hash mismatch")
        await self._db.execute(
            "INSERT INTO execution_tool_public_projections("
            "effect_id,root_run_id,run_id,tool_name,schema_version,projection_json,"
            "projection_hash,status,created_at) VALUES(?,?,?,?,1,?,?,?,?) "
            "ON CONFLICT(effect_id) DO NOTHING",
            (
                effect_id,
                root_run_id,
                run_id,
                tool_name,
                projection_json,
                projection_hash,
                status,
                created_at,
            ),
        )
        stored = await (
            await self._db.execute(
                "SELECT root_run_id,run_id,tool_name,projection_json,projection_hash,status "
                "FROM execution_tool_public_projections WHERE effect_id=?",
                (effect_id,),
            )
        ).fetchone()
        assert stored is not None
        if tuple(str(stored[name]) for name in stored.keys()) != (
            root_run_id,
            run_id,
            tool_name,
            projection_json,
            projection_hash,
            status,
        ):
            raise IdempotencyConflict(
                "tool_public_projection_conflict",
                "effect already has a different public projection",
            )

    async def consume_workflow_decisions(
        self,
        *,
        run_id: str,
        decisions: Sequence[str | Mapping[str, Any]],
        checkpoint_id: str,
        now: float,
    ) -> list[str]:
        return await self._unit_of_work._consume_workflow_decisions_tx(
            self._db,
            run_id=run_id,
            decisions=decisions,
            checkpoint_id=checkpoint_id,
            now=now,
        )

    async def open_workflow_decision(
        self,
        *,
        run: Mapping[str, Any],
        interrupt_id: str,
        checkpoint_id: str,
        task_id: str | None,
        kind: str,
        prompt: object,
        expires_at: float | None,
        now: float,
    ) -> Mapping[str, Any]:
        return await self._unit_of_work._open_workflow_decision_tx(
            self._db,
            run=run,
            interrupt_id=interrupt_id,
            checkpoint_id=checkpoint_id,
            task_id=task_id,
            kind=kind,
            prompt=prompt,
            expires_at=expires_at,
            now=now,
        )

    async def append_workflow_event(
        self, *, run: Mapping[str, Any], intent: Mapping[str, Any], now: float
    ) -> str:
        return await self._unit_of_work._append_workflow_event_tx(
            self._db, run=run, intent=intent, now=now
        )

    async def link_workflow_effects(
        self,
        *,
        run_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        links: Sequence[Mapping[str, Any]],
        now: float,
    ) -> None:
        await self._unit_of_work._link_workflow_effects_tx(
            self._db,
            run_id=run_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_id=checkpoint_id,
            links=links,
            now=now,
        )

    async def finalize_workflow_run(
        self,
        *,
        run: Mapping[str, Any],
        terminal_status: str,
        terminal_error: Mapping[str, Any] | None,
        recovery_action: str | None,
        event_ids: Sequence[str],
        now: float,
    ) -> str:
        return await self._unit_of_work._finalize_workflow_run_tx(
            self._db,
            run=run,
            terminal_status=terminal_status,
            terminal_error=terminal_error,
            recovery_action=recovery_action,
            event_ids=event_ids,
            now=now,
        )

    async def save_continuation_payload(
        self,
        *,
        run_id: str,
        expected_version: int,
        payload: Mapping[str, Any],
    ) -> ContinuationRecord:
        """CAS one continuation inside a caller-owned cross-domain transaction."""

        self._unit_of_work._validate_continuation_version(expected_version)
        run = await self._unit_of_work._continuation_run_tx(self._db, run_id)
        continuation, _ = await self._unit_of_work._save_continuation_tx(
            self._db,
            run=run,
            expected_version=expected_version,
            payload_json=self._unit_of_work._continuation_payload_json(payload),
            decision=None,
            now=float(self._unit_of_work._clock()),
        )
        return continuation

    async def insert_candidate_draft_receipt(
        self,
        values: Mapping[str, str],
        *,
        created_at: str,
    ) -> None:
        """Insert or verify one immutable candidate receipt in this UoW.

        The execution ledger owns this DML so a child terminal event and its
        host-issued draft receipt can only become visible in the same commit.
        """

        fields = (
            "receipt_id",
            "builder_launch_id",
            "child_run_id",
            "child_start_hash",
            "proposal_ref",
            "proposal_hash",
            "evidence_set_hash",
            "target_fence_hash",
            "validated_draft_hash",
            "manifest_hash",
            "archive_hash",
            "file_set_hash",
            "effect_topology_hash",
            "receipt_hash",
        )
        if set(values) != set(fields):
            raise TypeError("candidate draft receipt fields are incomplete")
        normalized = {name: str(values[name]).strip() for name in fields}
        if any(not value for value in normalized.values()) or not str(
            created_at
        ).strip():
            raise ValueError("candidate draft receipt values must be non-empty")
        existing = await (
            await self._db.execute(
                """SELECT receipt_id,builder_launch_id,child_run_id,
                child_start_hash,proposal_ref,proposal_hash,evidence_set_hash,
                target_fence_hash,validated_draft_hash,manifest_hash,
                archive_hash,file_set_hash,effect_topology_hash,receipt_hash
                FROM execution_candidate_draft_receipts
                WHERE receipt_id=? OR builder_launch_id=?""",
                (
                    normalized["receipt_id"],
                    normalized["builder_launch_id"],
                ),
            )
        ).fetchall()
        if existing:
            if len(existing) != 1 or any(
                str(existing[0][name]) != normalized[name] for name in fields
            ):
                raise IdempotencyConflict(
                    "candidate_draft_receipt_conflict",
                    "candidate draft receipt replay differs from durable row",
                )
            return
        await self._db.execute(
            """INSERT INTO execution_candidate_draft_receipts(
            receipt_id,builder_launch_id,child_run_id,child_start_hash,
            proposal_ref,proposal_hash,evidence_set_hash,target_fence_hash,
            validated_draft_hash,manifest_hash,archive_hash,file_set_hash,
            effect_topology_hash,receipt_hash,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            tuple(normalized[name] for name in fields) + (str(created_at).strip(),),
        )

    async def verify_candidate_draft_receipt(
        self,
        values: Mapping[str, str],
    ) -> None:
        """Fail closed unless this transaction sees the exact receipt row."""

        fields = tuple(values)
        if not fields:
            raise ValueError("candidate draft receipt values are required")
        row = await (
            await self._db.execute(
                """SELECT receipt_id,builder_launch_id,child_run_id,
                child_start_hash,proposal_ref,proposal_hash,evidence_set_hash,
                target_fence_hash,validated_draft_hash,manifest_hash,
                archive_hash,file_set_hash,effect_topology_hash,receipt_hash
                FROM execution_candidate_draft_receipts
                WHERE receipt_id=? AND receipt_hash=?""",
                (values.get("receipt_id"), values.get("receipt_hash")),
            )
        ).fetchone()
        if row is None or any(
            name not in row.keys() or str(row[name]) != str(value)
            for name, value in values.items()
        ):
            raise IdempotencyConflict(
                "candidate_draft_receipt_missing_or_mismatch",
                "terminal replay does not match the durable candidate receipt",
            )

    async def insert_candidate_draft_material(
        self,
        *,
        receipt_id: str,
        archive_bytes: bytes,
        validated_draft_hash: str,
        manifest_hash: str,
        archive_hash: str,
        file_set_hash: str,
        effect_topology_hash: str,
        created_at: str,
    ) -> None:
        """Freeze exact candidate bytes beside their receipt in this UoW."""

        import hashlib

        receipt_id = str(receipt_id).strip()
        hashes = tuple(
            str(value).strip()
            for value in (
                validated_draft_hash,
                manifest_hash,
                archive_hash,
                file_set_hash,
                effect_topology_hash,
            )
        )
        if (
            not receipt_id
            or not isinstance(archive_bytes, bytes)
            or not archive_bytes
            or any(len(value) != 64 for value in hashes)
            or hashlib.sha256(archive_bytes).hexdigest() != hashes[2]
            or not str(created_at).strip()
        ):
            raise ValueError("candidate draft material is invalid")
        row = await (
            await self._db.execute(
                """SELECT archive_blob,validated_draft_hash,manifest_hash,
                          archive_hash,file_set_hash,effect_topology_hash
                   FROM execution_candidate_draft_materials
                   WHERE receipt_id=?""",
                (receipt_id,),
            )
        ).fetchone()
        expected = (archive_bytes, *hashes)
        if row is not None:
            actual = (
                bytes(row["archive_blob"]),
                str(row["validated_draft_hash"]),
                str(row["manifest_hash"]),
                str(row["archive_hash"]),
                str(row["file_set_hash"]),
                str(row["effect_topology_hash"]),
            )
            if actual != expected:
                raise IdempotencyConflict(
                    "candidate_draft_material_conflict",
                    "candidate draft material replay differs from durable bytes",
                )
            return
        await self._db.execute(
            """INSERT INTO execution_candidate_draft_materials(
               receipt_id,archive_blob,validated_draft_hash,manifest_hash,
               archive_hash,file_set_hash,effect_topology_hash,created_at
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (receipt_id, archive_bytes, *hashes, str(created_at).strip()),
        )

    async def verify_candidate_draft_material(
        self,
        *,
        receipt_id: str,
        archive_hash: str,
        validated_draft_hash: str,
        manifest_hash: str,
        file_set_hash: str,
        effect_topology_hash: str,
    ) -> None:
        row = await (
            await self._db.execute(
                """SELECT validated_draft_hash,manifest_hash,archive_hash,
                          file_set_hash,effect_topology_hash
                   FROM execution_candidate_draft_materials
                   WHERE receipt_id=?""",
                (str(receipt_id).strip(),),
            )
        ).fetchone()
        expected = (
            str(validated_draft_hash),
            str(manifest_hash),
            str(archive_hash),
            str(file_set_hash),
            str(effect_topology_hash),
        )
        if row is None or tuple(str(row[index]) for index in range(5)) != expected:
            raise IdempotencyConflict(
                "candidate_draft_material_missing_or_mismatch",
                "terminal replay does not match durable candidate bytes",
            )

    async def insert_or_verify_skill_scope_activation(
        self,
        values: Mapping[str, Any],
        *,
        created_at: float,
    ) -> Mapping[str, Any]:
        """Insert one scope activation beside its continuation outcome."""

        text_fields = (
            "activation_id",
            "run_id",
            "root_run_id",
            "scope_id",
            "scope_hash",
            "capability_snapshot_ref",
            "run_catalog_content_stamp",
            "effective_tool_refs_hash",
            "instruction_content_hash",
            "boundary_ref",
            "boundary_hash",
            "status",
            "receipt_ref",
            "receipt_hash",
        )
        string_list_fields = (
            "allowed_tool_names",
            "effective_tool_ref_hashes",
        )
        object_list_fields = ("allowed_tool_refs",)
        required = {
            *text_fields,
            *string_list_fields,
            *object_list_fields,
            "continuation_version",
        }
        if set(values) != required:
            raise TypeError("skill scope activation fields are incomplete")
        normalized = {
            name: str(values[name]).strip() for name in text_fields
        }
        if any(not item for item in normalized.values()):
            raise ValueError("skill scope activation values must be non-empty")
        if normalized["status"] != "committed":
            raise ValueError("skill scope activation must be committed")
        digest_fields = (
            "scope_hash",
            "capability_snapshot_ref",
            "effective_tool_refs_hash",
            "instruction_content_hash",
            "boundary_hash",
            "receipt_hash",
        )
        if any(
            len(normalized[name]) != 64
            or any(ch not in "0123456789abcdef" for ch in normalized[name])
            for name in digest_fields
        ):
            raise ValueError("skill scope activation hash is invalid")
        continuation_version = values["continuation_version"]
        if (
            isinstance(continuation_version, bool)
            or not isinstance(continuation_version, int)
            or continuation_version < 1
        ):
            raise ValueError("skill scope continuation version is invalid")
        string_lists: dict[str, tuple[str, ...]] = {}
        for name in string_list_fields:
            raw = values[name]
            if not isinstance(raw, (list, tuple)) or any(
                not isinstance(item, str) or not item.strip() for item in raw
            ):
                raise ValueError(f"{name} must be a string list")
            canonical = tuple(sorted(set(raw)))
            if tuple(raw) != canonical:
                raise ValueError(f"{name} must be sorted and unique")
            if name == "effective_tool_ref_hashes" and any(
                len(item) != 64
                or any(ch not in "0123456789abcdef" for ch in item)
                for item in canonical
            ):
                raise ValueError(
                    "effective_tool_ref_hashes must contain exact hashes"
                )
            string_lists[name] = canonical
        raw_refs = values["allowed_tool_refs"]
        if not isinstance(raw_refs, (list, tuple)) or any(
            not isinstance(item, Mapping) for item in raw_refs
        ):
            raise ValueError("allowed_tool_refs must be an object list")
        allowed_tool_refs = tuple(
            sorted(
                (
                    thaw_json(dict(item))
                    for item in raw_refs
                ),
                key=canonical_json,
            )
        )
        if len({canonical_json(item) for item in allowed_tool_refs}) != len(
            allowed_tool_refs
        ):
            raise ValueError(
                "allowed_tool_refs must be unique"
            )
        if normalized["scope_id"] != (
            "skill-scope:" + normalized["scope_hash"]
        ):
            raise ValueError("skill scope id does not match scope hash")
        identity = {
            "schema": "skill-scope-activation/v1",
            "run_id": normalized["run_id"],
            "scope_id": normalized["scope_id"],
            "scope_hash": normalized["scope_hash"],
            "capability_snapshot_ref": normalized[
                "capability_snapshot_ref"
            ],
            "instruction_content_hash": normalized[
                "instruction_content_hash"
            ],
            "effective_tool_refs_hash": normalized[
                "effective_tool_refs_hash"
            ],
        }
        expected_activation_id = (
            "skill-scope-activation:" + fingerprint_json(identity)
        )
        if normalized["activation_id"] != expected_activation_id:
            raise ValueError("skill scope activation id mismatch")
        receipt_payload = {
            "schema": "skill-scope-activation-receipt-v1",
            **normalized,
            "receipt_hash": None,
            **{
                name: list(string_lists[name])
                for name in string_list_fields
            },
            "allowed_tool_refs": list(allowed_tool_refs),
            "continuation_version": continuation_version,
        }
        receipt_payload.pop("receipt_hash")
        if normalized["receipt_hash"] != fingerprint_json(receipt_payload):
            raise ValueError("skill scope activation receipt hash mismatch")

        columns = (
            *text_fields,
            "allowed_tool_names_json",
            "allowed_tool_refs_json",
            "effective_tool_ref_hashes_json",
            "continuation_version",
            "schema_version",
            "created_at",
        )
        row = await (
            await self._db.execute(
                """SELECT * FROM execution_skill_scope_activations
                WHERE activation_id=? OR (run_id=? AND scope_id=?)""",
                (
                    normalized["activation_id"],
                    normalized["run_id"],
                    normalized["scope_id"],
                ),
            )
        ).fetchone()
        expected = {
            **normalized,
            "allowed_tool_names_json": canonical_json(
                list(string_lists["allowed_tool_names"])
            ),
            "allowed_tool_refs_json": canonical_json(
                list(allowed_tool_refs)
            ),
            "effective_tool_ref_hashes_json": canonical_json(
                list(string_lists["effective_tool_ref_hashes"])
            ),
            "continuation_version": continuation_version,
            "schema_version": 1,
            "created_at": float(created_at),
        }
        if row is not None:
            for name in columns:
                actual = row[name]
                wanted = expected[name]
                if name == "created_at":
                    continue
                if actual != wanted:
                    raise IdempotencyConflict(
                        "skill_scope_activation_conflict",
                        "scope activation replay differs from durable row",
                    )
            return dict(row)
        await self._db.execute(
            """INSERT INTO execution_skill_scope_activations(
            activation_id,run_id,root_run_id,scope_id,scope_hash,
            capability_snapshot_ref,run_catalog_content_stamp,
            effective_tool_refs_hash,instruction_content_hash,boundary_ref,
            boundary_hash,status,receipt_ref,receipt_hash,
            allowed_tool_names_json,allowed_tool_refs_json,
            effective_tool_ref_hashes_json,continuation_version,
            schema_version,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
            tuple(expected[name] for name in columns if name != "schema_version"),
        )
        inserted = await (
            await self._db.execute(
                """SELECT * FROM execution_skill_scope_activations
                WHERE activation_id=?""",
                (normalized["activation_id"],),
            )
        ).fetchone()
        assert inserted is not None
        return dict(inserted)

    async def read_skill_scope_activation(
        self, activation_id: str
    ) -> Mapping[str, Any] | None:
        row = await (
            await self._db.execute(
                """SELECT * FROM execution_skill_scope_activations
                WHERE activation_id=?""",
                (str(activation_id).strip(),),
            )
        ).fetchone()
        return None if row is None else dict(row)

    async def insert_or_verify_run_block_signal(
        self, values: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Write one presentation-only block signal in the terminal tx.

        The v29 schema owns this sidecar.  This typed operation intentionally
        exposes no generic SQL surface to terminal extensions.
        """

        required = {
            "signal_id",
            "root_run_id",
            "reason_code",
            "evidence_refs_json",
            "producer",
            "created_event_id",
            "payload_hash",
            "schema_version",
            "created_at",
        }
        if set(values) != required:
            raise TypeError("run block signal fields are incomplete")
        signal_id = str(values["signal_id"]).strip()
        root_run_id = str(values["root_run_id"]).strip()
        if not signal_id or not root_run_id:
            raise ValueError("run block signal identity is empty")
        existing = await (
            await self._db.execute(
                """SELECT signal_id,root_run_id,reason_code,evidence_refs_json,
                producer,created_event_id,payload_hash,schema_version,created_at
                FROM execution_run_block_signals
                WHERE signal_id=? OR root_run_id=?""",
                (signal_id, root_run_id),
            )
        ).fetchone()
        ordered_fields = (
            "signal_id",
            "root_run_id",
            "reason_code",
            "evidence_refs_json",
            "producer",
            "created_event_id",
            "payload_hash",
            "schema_version",
            "created_at",
        )
        expected = tuple(values[name] for name in ordered_fields)
        if existing is not None:
            actual = tuple(existing[name] for name in ordered_fields)
            if actual != expected:
                raise IdempotencyConflict(
                    "run_block_signal_conflict",
                    "root terminal replay supplied a different block signal",
                )
            return dict(existing)
        await self._db.execute(
            """INSERT INTO execution_run_block_signals(
            signal_id,root_run_id,reason_code,evidence_refs_json,producer,
            created_event_id,payload_hash,schema_version,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            expected,
        )
        inserted = await (
            await self._db.execute(
                """SELECT signal_id,root_run_id,reason_code,evidence_refs_json,
                producer,created_event_id,payload_hash,schema_version,created_at
                FROM execution_run_block_signals WHERE signal_id=?""",
                (signal_id,),
            )
        ).fetchone()
        assert inserted is not None
        return dict(inserted)

    async def read_run_block_signal(
        self, root_run_id: str
    ) -> Mapping[str, Any] | None:
        row = await (
            await self._db.execute(
                """SELECT signal_id,root_run_id,reason_code,evidence_refs_json,
                producer,created_event_id,payload_hash,schema_version,created_at
                FROM execution_run_block_signals WHERE root_run_id=?""",
                (str(root_run_id).strip(),),
            )
        ).fetchone()
        return None if row is None else dict(row)


class SqliteExecutionUnitOfWork:
    """CAS-based execution ledger backed by the workflow SQLite database."""

    _WORKFLOW_TERMINAL_EFFECTS = frozenset(
        {"succeeded", "failed", "unknown", "cancelled", "late_reconciled"}
    )
    _WORKFLOW_DECISION_KINDS = frozenset(
        {"permission", "plan", "clarification", "ppt_outline", "skill_candidate"}
    )

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        fault_injector: _FaultInjector | None = None,
        write_lane: ExecutionWriteLane | None = None,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self._fault_injector = fault_injector
        self._initialize_lock = asyncio.Lock()
        self._initialized = False
        self._write_lane = write_lane or get_execution_write_lane(self.path)
        self._write_lane.acquire_owner()
        self._write_lane_owner_active = True

    def __del__(self) -> None:
        if getattr(self, "_write_lane_owner_active", False):
            self._write_lane_owner_active = False
            self._write_lane.release_owner_nowait()

    def bind(self, db: aiosqlite.Connection) -> ExecutionTx:
        """Bind typed execution DML to a caller-owned open transaction."""

        return ExecutionTx(self, db)

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            if not self._write_lane_owner_active:
                self._write_lane = get_execution_write_lane(self.path)
                self._write_lane.acquire_owner()
                self._write_lane_owner_active = True
            await initialize_workflow_db(self.path)
            await self._write_lane.open()
            self._initialized = True

    async def close(self) -> None:
        """Bounded shutdown for the shared execution writer lane."""

        if self._write_lane_owner_active:
            self._write_lane_owner_active = False
            await self._write_lane.release_owner()
        self._initialized = False

    async def get_skill_scope_activation(
        self, activation_id: str
    ) -> Mapping[str, Any] | None:
        """Resolve commit-unknown without opening a new continuation writer."""

        async with self._read_connection() as db:
            return await self.bind(db).read_skill_scope_activation(
                activation_id
            )

    async def commit_skill_scope_activation(
        self,
        run_id: str,
        *,
        expected_continuation_version: int,
        continuation_payload: Mapping[str, Any],
        activation_values: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], ContinuationRecord]:
        """Commit a scope receipt and the continuation that activates it."""

        self._validate_continuation_version(
            expected_continuation_version
        )
        expected_activation_version = expected_continuation_version + 1
        if activation_values.get(
            "continuation_version"
        ) != expected_activation_version:
            raise IdempotencyConflict(
                "skill_scope_continuation_conflict",
                "scope receipt does not name the committed continuation",
            )
        if str(activation_values.get("run_id") or "") != run_id:
            raise IdempotencyConflict(
                "skill_scope_run_conflict",
                "scope receipt belongs to another Run",
            )
        continuation = await self.persist_react_boundary(
            run_id,
            expected_continuation_version,
            continuation_payload,
            skill_scope_activation_values=activation_values,
        )
        if not isinstance(continuation, ContinuationRecord):
            raise RuntimeError("skill scope activation returned a promoted Run")
        activation = await self.get_skill_scope_activation(
            str(activation_values["activation_id"])
        )
        if activation is None:
            raise RuntimeError("skill scope activation receipt missing after commit")
        return activation, continuation

    async def _connect(self) -> aiosqlite.Connection:
        await self.initialize()
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=FULL")
        await db.execute("PRAGMA busy_timeout=5000")
        return db

    async def read_workflow_resume_payload(
        self,
        run_id: str,
    ) -> Mapping[str, JsonValue]:
        """Read resolved, unconsumed Native workflow decisions for crash recovery."""

        async with self._read_connection() as db:
            run = await (
                await db.execute(
                    "SELECT driver_kind,status FROM execution_runs WHERE run_id=?",
                    (run_id,),
                )
            ).fetchone()
            if run is None:
                raise RunNotFound(
                    "run_not_found",
                    f"execution run does not exist: {run_id}",
                )
            if str(run["driver_kind"]) != "workflow":
                raise CheckpointExecutionError(
                    "execution_driver_conflict",
                    "workflow resume payload cannot read another driver",
                )
            rows = await (
                await db.execute(
                    """SELECT nonce,response_json FROM execution_decisions
                    WHERE run_id=? AND status IN ('allowed','denied')
                    AND consumed_at IS NULL
                    ORDER BY resolved_at,decision_id""",
                    (run_id,),
                )
            ).fetchall()
        payload: dict[str, JsonValue] = {}
        for row in rows:
            response = (
                json.loads(str(row["response_json"]))
                if row["response_json"] is not None
                else {}
            )
            if not isinstance(response, dict):
                raise CheckpointExecutionError(
                    "decision_response_invalid",
                    "workflow decision response must be an object",
                )
            validate_json_value(response, path="$.workflow_resume")
            payload[str(row["nonce"])] = response
        return payload

    @asynccontextmanager
    async def _read_connection(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await self._connect()
        try:
            yield db
        finally:
            await db.close()

    @asynccontextmanager
    async def _write_transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        await self.initialize()
        async with self._write_lane.transaction() as db:
            yield db  # type: ignore[misc]

    def _fault(self, point: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(point)

    @staticmethod
    def _checkpoint_stable_id(*parts: object) -> str:
        return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()

    @staticmethod
    async def _workflow_execution_run_tx(
        db: aiosqlite.Connection, run_id: str
    ) -> aiosqlite.Row:
        row = await (
            await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (run_id,))
        ).fetchone()
        if row is None:
            raise CheckpointExecutionError(
                "execution_run_not_found", f"generic execution run does not exist: {run_id}"
            )
        if str(row["driver_kind"]) != "workflow":
            raise CheckpointExecutionError(
                "execution_driver_conflict", "workflow checkpoint cannot mutate another driver"
            )
        return row

    async def _consume_workflow_decisions_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        decisions: Sequence[str | Mapping[str, Any]],
        checkpoint_id: str,
        now: float,
    ) -> list[str]:
        await self._workflow_execution_run_tx(db, run_id)
        consumed: list[str] = []
        for item in decisions:
            decision_id = str(item if isinstance(item, str) else item["decision_id"])
            expected_version = None if isinstance(item, str) else item.get("expected_version")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_decisions WHERE decision_id=? AND run_id=?",
                    (decision_id, run_id),
                )
            ).fetchone()
            if row is None and isinstance(item, str):
                row = await (
                    await db.execute(
                        "SELECT * FROM execution_decisions WHERE nonce=? AND run_id=?",
                        (decision_id, run_id),
                    )
                ).fetchone()
                if row is not None:
                    decision_id = str(row["decision_id"])
            if row is None:
                raise CheckpointExecutionError(
                    "decision_not_found", "generic resume decision was not found"
                )
            if row["consumed_at"] is not None:
                if str(row["consumed_checkpoint_id"] or "") != checkpoint_id:
                    raise CheckpointExecutionError(
                        "decision_already_consumed", "decision belongs to another checkpoint"
                    )
                consumed.append(decision_id)
                continue
            if str(row["status"]) not in {"allowed", "denied"}:
                raise CheckpointExecutionError(
                    "decision_not_resolved", "generic decision is not resumable"
                )
            if expected_version is not None and int(row["decision_version"]) != int(
                expected_version
            ):
                raise CheckpointExecutionError("stale_decision", "decision version changed")
            cursor = await db.execute(
                """UPDATE execution_decisions SET consumed_at=?,consumed_checkpoint_id=?,
                decision_version=decision_version+1 WHERE decision_id=? AND run_id=?
                AND status IN ('allowed','denied') AND consumed_at IS NULL
                AND (? IS NULL OR decision_version=?)""",
                (now, checkpoint_id, decision_id, run_id, expected_version, expected_version),
            )
            if cursor.rowcount != 1:
                raise CheckpointExecutionError(
                    "stale_decision", "generic decision changed while consumed"
                )
            response = (
                json.loads(str(row["response_json"]))
                if row["response_json"] is not None
                else {}
            )
            child = response.get("child_signal") if isinstance(response, Mapping) else None
            if isinstance(child, Mapping):
                signal_id = str(child.get("signal_id") or "")
                signal = await (
                    await db.execute(
                        """SELECT * FROM execution_child_signal_inbox
                        WHERE signal_id=? AND parent_run_id=?""",
                        (signal_id, run_id),
                    )
                ).fetchone()
                if signal is None or any(
                    str(signal[key]) != str(child.get(field) or "")
                    for key, field in (
                        ("command_id", "command_id"),
                        ("child_run_id", "child_run_id"),
                    )
                ) or f"child_{signal['kind']}" != str(child.get("kind") or ""):
                    raise CheckpointExecutionError(
                        "workflow_child_signal_conflict",
                        "Native checkpoint child response differs from its durable inbox",
                    )
                if str(signal["kind"]) == "terminal":
                    terminal = json.loads(str(signal["payload_json"]))
                    if terminal.get("status") != child.get("status") or terminal.get(
                        "value"
                    ) != child.get("value"):
                        raise CheckpointExecutionError(
                            "workflow_child_signal_conflict",
                            "Native checkpoint child terminal payload differs from its inbox",
                        )
                await db.execute(
                    """UPDATE execution_child_signal_inbox SET delivered_at=?,updated_at=?
                    WHERE signal_id=? AND delivered_at IS NULL""",
                    (now, now, signal_id),
                )
            consumed.append(decision_id)
        self._fault("checkpoint_consume_decisions_after_write")
        return consumed

    async def _open_workflow_decision_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run: Mapping[str, Any],
        interrupt_id: str,
        checkpoint_id: str,
        task_id: str | None,
        kind: str,
        prompt: object,
        expires_at: float | None,
        now: float,
    ) -> Mapping[str, Any]:
        run_id = str(run["run_id"])
        await self._workflow_execution_run_tx(db, run_id)
        decision_id = self._checkpoint_stable_id("execution-decision", run_id, interrupt_id)
        normalized_kind = str(kind).strip().lower()
        if normalized_kind not in self._WORKFLOW_DECISION_KINDS:
            normalized_kind = "workflow_hitl"
        prompt_json = canonical_json(
            {
                "interrupt_id": interrupt_id,
                "checkpoint_id": checkpoint_id,
                "task_id": task_id,
                "prompt": copy.deepcopy(prompt),
            }
        )
        existing = await (
            await db.execute(
                "SELECT * FROM execution_decisions WHERE run_id=? AND nonce=?",
                (run_id, interrupt_id),
            )
        ).fetchone()
        if existing is None:
            await db.execute(
                """INSERT INTO execution_decisions(
                decision_id,schema_version,run_id,nonce,kind,status,prompt_schema_version,
                prompt_json,decision_version,expires_at,created_at
                ) VALUES(?,1,?,?,?,'open',1,?,0,?,?)""",
                (
                    decision_id,
                    run_id,
                    interrupt_id,
                    normalized_kind,
                    prompt_json,
                    expires_at,
                    now,
                ),
            )
            row = await (
                await db.execute(
                    "SELECT * FROM execution_decisions WHERE decision_id=?", (decision_id,)
                )
            ).fetchone()
            assert row is not None
        else:
            row = existing
            if (
                str(row["decision_id"]) != decision_id
                or str(row["kind"]) != normalized_kind
                or str(row["prompt_json"]) != prompt_json
            ):
                raise CheckpointExecutionError(
                    "decision_intent_conflict", "interrupt nonce names another decision"
                )
        cursor = await db.execute(
            """UPDATE execution_runs SET status='waiting',version=version+1,updated_at=?
            WHERE run_id=? AND terminal_event_id IS NULL
            AND status IN ('created','queued','running','waiting')""",
            (now, run_id),
        )
        if cursor.rowcount != 1:
            raise CheckpointExecutionError(
                "execution_waiting_conflict", "generic execution cannot enter waiting"
            )
        self._fault("checkpoint_open_decision_after_write")
        return {
            "decision_id": str(row["decision_id"]),
            "kind": str(row["kind"]),
            "nonce": str(row["nonce"]),
            "version": int(row["decision_version"]),
        }

    @staticmethod
    def _workflow_outcome(event_type: str, payload: Mapping[str, Any]) -> OutcomeStatus:
        kind = str(payload.get("kind") or "").lower()
        status = str(payload.get("status") or "").lower()
        if event_type == "workflow.accepted" or kind == "accepted":
            return OutcomeStatus.ACCEPTED
        if event_type == "workflow.final" or kind == "final":
            return {
                "completed": OutcomeStatus.SUCCEEDED,
                "succeeded": OutcomeStatus.SUCCEEDED,
                "failed": OutcomeStatus.FAILED,
                "cancelled": OutcomeStatus.CANCELLED,
            }.get(status, OutcomeStatus.UNKNOWN)
        if event_type in {"workflow.decision", "workflow.progress"} or kind in {
            "decision",
            "progress",
        }:
            return OutcomeStatus.WAITING
        if status == "cancel_requested":
            return OutcomeStatus.CANCEL_REQUESTED
        return OutcomeStatus.UNKNOWN

    @staticmethod
    async def _workflow_delivery_specs_tx(
        db: aiosqlite.Connection,
        *,
        run_id: str,
        intent: Mapping[str, Any],
    ) -> tuple[DeliverySpec, ...]:
        raw = list(intent.get("deliveries") or [])
        structured = list(intent.get("delivery_specs") or [])
        if structured:
            ref = await (
                await db.execute(
                    """SELECT session_id FROM workflow_session_refs WHERE run_id=?
                    AND session_kind='delivery' AND deleted_at IS NULL""",
                    (run_id,),
                )
            ).fetchone()
            if ref is None:
                raise CheckpointExecutionError(
                    "delivery_session_not_bound", "delivery specs require a delivery binding"
                )
            for item in structured:
                if not isinstance(item, Mapping) or not str(item.get("channel") or "").strip():
                    raise CheckpointExecutionError(
                        "invalid_delivery_intent", "delivery specs require a channel"
                    )
                raw.append(
                    {
                        "channel": str(item["channel"]),
                        "target_id": str(ref["session_id"]),
                        "required_durable": item.get("required_durable") is True,
                    }
                )
        elif not raw:
            ref = await (
                await db.execute(
                    """SELECT session_id FROM workflow_session_refs WHERE run_id=?
                    AND session_kind='delivery' AND deleted_at IS NULL""",
                    (run_id,),
                )
            ).fetchone()
            if ref is not None:
                business_channel = str(intent.get("channel") or "").strip().lower()
                target_id = str(ref["session_id"])
                if business_channel in {"artifact", "artifact_message"}:
                    raw.extend((
                        {"channel": "artifact", "target_id": target_id, "required_durable": True},
                        {"channel": "websocket", "target_id": target_id},
                    ))
                elif business_channel == "workflow_report":
                    raw.append({"channel": "websocket", "target_id": target_id})
                elif business_channel in {"receipt", "receipt_jsonl"}:
                    raw.append(
                        {"channel": "receipt", "target_id": target_id, "required_durable": True}
                    )
                else:
                    raw.extend((
                        {"channel": "session_message", "target_id": target_id, "required_durable": True},
                        {"channel": "websocket", "target_id": target_id},
                    ))
                    if business_channel == "final":
                        raw.append(
                            {"channel": "receipt", "target_id": target_id, "required_durable": True}
                        )
        result: list[DeliverySpec] = []
        for delivery in raw:
            policy = (
                DeliveryPolicy.DURABLE_REQUIRED
                if delivery.get("required_durable") is True
                else DeliveryPolicy.RETRY_WHILE_BOUND
            )
            result.append(
                DeliverySpec(
                    sink_kind=str(delivery["channel"]).strip().lower(),
                    sink_instance="workflow",
                    target_id=str(delivery["target_id"]).strip(),
                    policy=policy,
                )
            )
        identities = {
            (item.sink_kind, item.sink_instance, item.target_id) for item in result
        }
        if len(identities) != len(result):
            raise CheckpointExecutionError(
                "duplicate_delivery", "generic intent contains duplicate deliveries"
            )
        return tuple(result)

    async def _append_workflow_event_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run: Mapping[str, Any],
        intent: Mapping[str, Any],
        now: float,
    ) -> str:
        run_id = str(run["run_id"])
        await self._workflow_execution_run_tx(db, run_id)
        intent_id = str(intent.get("intent_id") or "").strip()
        event_type = str(intent.get("event_type") or "").strip()
        if not intent_id or not event_type:
            raise CheckpointExecutionError(
                "invalid_delivery_intent", "intent_id and event_type are required"
            )
        event_key = str(intent.get("event_key") or f"intent:{intent_id}").strip()
        payload = copy.deepcopy(dict(intent.get("payload") or {}))
        payload.update(
            {
                "run_id": run_id,
                "request_id": run["request_id"],
                "turn_id": run["turn_id"],
                "workflow_name": run["workflow_name"],
                "workflow_version": run["workflow_version"],
            }
        )
        outcome = self._workflow_outcome(event_type, payload)
        event_id = stable_event_id(run_id, event_key)
        correlation = canonical_json(
            {
                "request_id": run["request_id"],
                "turn_id": run["turn_id"],
                "workflow_name": run["workflow_name"],
                "workflow_version": run["workflow_version"],
            }
        )
        payload_json = canonical_json(payload)
        error = payload.get("error")
        error_json = canonical_json(dict(error)) if isinstance(error, Mapping) else None
        artifact_refs = list(payload.get("artifact_refs") or [])
        if payload.get("manifest_ref") and str(payload["manifest_ref"]) not in artifact_refs:
            artifact_refs.append(str(payload["manifest_ref"]))
        artifact_refs_json = canonical_json(artifact_refs)
        deliveries = await self._workflow_delivery_specs_tx(
            db, run_id=run_id, intent=intent
        )
        existing = await (
            await db.execute(
                "SELECT * FROM execution_events WHERE run_id=? AND event_key=?",
                (run_id, event_key),
            )
        ).fetchone()
        if existing is None:
            seq_row = await (
                await db.execute(
                    """UPDATE execution_runs SET durable_seq=durable_seq+1,
                    version=version+1,updated_at=? WHERE run_id=? AND terminal_event_id IS NULL
                    RETURNING durable_seq""",
                    (now, run_id),
                )
            ).fetchone()
            if seq_row is None:
                raise CheckpointExecutionError(
                    "execution_run_terminal", "terminal execution cannot accept another event"
                )
            await db.execute(
                """INSERT INTO execution_events(
                event_id,schema_version,event_key,run_id,durable_seq,kind,status,driver_kind,
                correlation_json,payload_json,error_json,artifact_refs_json,created_at
                ) VALUES(?,1,?,?,?,?,?,'workflow',?,?,?,?,?)""",
                (
                    event_id,
                    event_key,
                    run_id,
                    int(seq_row["durable_seq"]),
                    event_type,
                    outcome.value,
                    correlation,
                    payload_json,
                    error_json,
                    artifact_refs_json,
                    now,
                ),
            )
        else:
            expected = {
                "event_id": event_id,
                "kind": event_type,
                "status": outcome.value,
                "driver_kind": "workflow",
                "correlation_json": correlation,
                "payload_json": payload_json,
                "error_json": error_json,
                "artifact_refs_json": artifact_refs_json,
            }
            if any(existing[key] != value for key, value in expected.items()):
                raise CheckpointExecutionError(
                    "event_intent_conflict", "event key names different generic content"
                )
        for delivery in deliveries:
            delivery_id = stable_delivery_id(event_id, delivery)
            await db.execute(
                """INSERT INTO execution_deliveries(
                delivery_id,schema_version,event_id,run_id,sink_kind,sink_instance,target_id,
                policy,status,created_at,updated_at
                ) VALUES(?,1,?,?,?,?,?,?,'pending',?,?)
                ON CONFLICT(event_id,sink_kind,sink_instance,target_id) DO NOTHING""",
                (
                    delivery_id,
                    event_id,
                    run_id,
                    delivery.sink_kind,
                    delivery.sink_instance,
                    delivery.target_id,
                    delivery.policy.value,
                    now,
                    now,
                ),
            )
        self._fault("checkpoint_append_event_after_write")
        return event_id

    async def _link_workflow_effects_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        links: Sequence[Mapping[str, Any]],
        now: float,
    ) -> None:
        await self._workflow_execution_run_tx(db, run_id)
        for link in links:
            effect_id = str(link.get("effect_id") or "").strip()
            node_execution_id = str(link.get("node_execution_id") or "").strip()
            if not effect_id:
                raise CheckpointExecutionError(
                    "invalid_effect_link", "generic effect link requires effect_id"
                )
            effect = await (
                await db.execute(
                    "SELECT status FROM execution_effects WHERE effect_id=? AND run_id=?",
                    (effect_id, run_id),
                )
            ).fetchone()
            if effect is None or str(effect["status"]) not in self._WORKFLOW_TERMINAL_EFFECTS:
                raise CheckpointExecutionError(
                    "effect_not_committed", "only a committed generic effect may be checkpointed"
                )
            grant = await (
                await db.execute(
                    "SELECT status FROM execution_grants WHERE run_id=? AND effect_id=?",
                    (run_id, effect_id),
                )
            ).fetchone()
            if grant is not None and str(grant["status"]) != "consumed":
                raise CheckpointExecutionError(
                    "effect_grant_not_consumed", "effect authorization is not consumed"
                )
            await db.execute(
                """INSERT OR IGNORE INTO execution_effect_links(
                run_id,node_execution_id,effect_id,checkpoint_ns,checkpoint_id,created_at
                ) VALUES(?,?,?,?,?,?)""",
                (run_id, node_execution_id, effect_id, checkpoint_ns, checkpoint_id, now),
            )
        self._fault("checkpoint_link_effects_after_write")

    async def _finalize_workflow_run_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run: Mapping[str, Any],
        terminal_status: str,
        terminal_error: Mapping[str, Any] | None,
        recovery_action: str | None,
        event_ids: Sequence[str],
        now: float,
    ) -> str:
        run_id = str(run["run_id"])
        execution = await self._workflow_execution_run_tx(db, run_id)
        expected_outcome = {
            "completed": "succeeded",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(terminal_status)
        if expected_outcome is None:
            raise CheckpointExecutionError(
                "invalid_terminal_status", f"unsupported generic terminal status: {terminal_status}"
            )
        terminal_event_id: str | None = None
        for event_id in reversed(tuple(event_ids)):
            event = await (
                await db.execute(
                    "SELECT status FROM execution_events WHERE event_id=? AND run_id=?",
                    (event_id, run_id),
                )
            ).fetchone()
            if event is not None and str(event["status"]) == expected_outcome:
                terminal_event_id = str(event_id)
                break
        if terminal_event_id is None:
            terminal_event_id = await self._append_workflow_event_tx(
                db,
                run=run,
                intent={
                    "intent_id": f"{run_id}:run-final",
                    "event_key": "run:terminal",
                    "event_type": "workflow.final",
                    "channel": "final",
                    "payload": {
                        "kind": "final",
                        "status": terminal_status,
                        "error": copy.deepcopy(terminal_error),
                        "recovery_action": recovery_action,
                    },
                },
                now=now,
            )
            execution = await self._workflow_execution_run_tx(db, run_id)
        artifact_items: list[dict[str, Any]] = []
        artifact_refs: list[str] = []
        if (
            terminal_status == "completed"
            and str(run["workflow_name"] or "") == "durable_task"
        ):
            effect_rows = await (
                await db.execute(
                    """SELECT effect_id,prepared_json,outcome_json,artifact_refs_json
                    FROM workflow_effects
                    WHERE run_id=? AND status='committed'
                    AND artifact_refs_json!='[]'
                    ORDER BY ended_at,effect_id""",
                    (run_id,),
                )
            ).fetchall()
            seen_artifacts: set[tuple[str, str]] = set()
            for effect_row in effect_rows:
                try:
                    refs = [
                        str(item)
                        for item in json.loads(str(effect_row["artifact_refs_json"]))
                    ]
                    outcome_payload = json.loads(str(effect_row["outcome_json"]))
                    prepared_payload = json.loads(str(effect_row["prepared_json"]))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise CheckpointExecutionError(
                        "artifact_effect_invalid",
                        "committed workflow artifact metadata is not valid JSON",
                    ) from exc
                tool_name = (
                    str(prepared_payload.get("tool_name") or "")
                    if isinstance(prepared_payload, Mapping)
                    else ""
                )
                value = (
                    outcome_payload.get("value")
                    if isinstance(outcome_payload, Mapping)
                    else None
                )
                raw_artifacts = (
                    value.get("artifacts")
                    if isinstance(value, Mapping)
                    else None
                )
                if refs and not isinstance(raw_artifacts, list):
                    if tool_name == "register_artifacts":
                        raise CheckpointExecutionError(
                            "artifact_effect_payload_missing",
                            "register_artifacts refs require an artifacts outcome envelope",
                        )
                    # Some workflow effects own input/dependency blobs. They
                    # are durable evidence, not user-facing output artifacts.
                    continue
                registered_digests: set[str] = set()
                for raw_artifact in raw_artifacts or ():
                    if not isinstance(raw_artifact, Mapping):
                        raise CheckpointExecutionError(
                            "artifact_effect_payload_invalid",
                            "workflow artifact entries must be objects",
                        )
                    item = copy.deepcopy(dict(raw_artifact))
                    digest = str(item.get("sha256") or "").strip()
                    path = str(item.get("path") or "").strip()
                    if not digest:
                        if tool_name == "register_artifacts":
                            raise CheckpointExecutionError(
                                "artifact_effect_payload_invalid",
                                "register_artifacts entries require a sha256 digest",
                            )
                        # write_file and other producer tools may publish a
                        # provisional UI envelope while the receipt digest is
                        # owned only by prepared metadata.  It is not a final
                        # registered artifact and must not poison terminal
                        # aggregation.
                        continue
                    if digest not in refs:
                        raise CheckpointExecutionError(
                            "artifact_effect_ref_mismatch",
                            "workflow artifact digest is absent from its committed refs",
                        )
                    registered_digests.add(digest)
                    if digest not in artifact_refs:
                        artifact_refs.append(digest)
                    identity = (digest, path)
                    if identity not in seen_artifacts:
                        seen_artifacts.add(identity)
                        artifact_items.append(item)
                if tool_name == "register_artifacts" and set(refs) != registered_digests:
                    raise CheckpointExecutionError(
                        "artifact_effect_ref_mismatch",
                        "register_artifacts refs and artifact digests must match exactly",
                    )
            if artifact_items:
                await self._append_workflow_event_tx(
                    db,
                    run=run,
                    intent={
                        "intent_id": f"{run_id}:registered-artifacts",
                        "event_key": "workflow:registered-artifacts",
                        "event_type": "workflow.artifact_card",
                        "channel": "artifact",
                        "payload": {
                            "kind": "artifact_card",
                            "status": "completed",
                            "tool": "register_artifacts",
                            "text": (
                                f"Registered {len(artifact_items)} workflow "
                                "artifact(s)."
                            ),
                            "artifacts": artifact_items,
                            "artifact_refs": artifact_refs,
                        },
                    },
                    now=now,
                )
        child_command = await (
            await db.execute(
                """SELECT operation_id FROM execution_child_commands
                WHERE child_run_id=?""",
                (run_id,),
            )
        ).fetchone()
        parent_signal_value: dict[str, Any] = {
            "run_id": run_id,
            "status": terminal_status,
            "terminal_event_id": terminal_event_id,
            "error": copy.deepcopy(terminal_error),
            "recovery_action": recovery_action,
            "artifacts": artifact_items,
            "artifact_refs": artifact_refs,
        }
        for event_kind, value_key in (
            ("workflow.final_assistant", "final_assistant"),
            ("workflow.workflow_report", "workflow_report"),
        ):
            result_event = await (
                await db.execute(
                    """SELECT payload_json FROM execution_events
                    WHERE run_id=? AND kind=? ORDER BY durable_seq DESC LIMIT 1""",
                    (run_id, event_kind),
                )
            ).fetchone()
            if result_event is None:
                continue
            result_payload = json.loads(str(result_event["payload_json"]))
            parent_signal_value[value_key] = copy.deepcopy(
                result_payload.get("payload", result_payload)
            )
        if str(execution["status"]) in {"completed", "failed", "cancelled"}:
            if (
                str(execution["status"]) != terminal_status
                or str(execution["terminal_event_id"] or "") != terminal_event_id
            ):
                raise CheckpointExecutionError(
                    "terminal_conflict", "another generic terminal intent already won"
                )
            if child_command is not None:
                await self._enqueue_child_terminal_signal_tx(
                    db,
                    str(child_command["operation_id"]),
                    terminal_status=terminal_status,
                    value=parent_signal_value,
                )
            return terminal_event_id
        cursor = await db.execute(
            """UPDATE execution_runs SET status=?,terminal_event_id=?,version=version+1,
            updated_at=?,ended_at=? WHERE run_id=? AND terminal_event_id IS NULL
            AND status NOT IN ('completed','failed','cancelled')""",
            (terminal_status, terminal_event_id, now, now, run_id),
        )
        if cursor.rowcount != 1:
            raise CheckpointExecutionError(
                "terminal_conflict", "generic terminal compare-and-set lost"
            )
        if child_command is not None:
            await self._enqueue_child_terminal_signal_tx(
                db,
                str(child_command["operation_id"]),
                terminal_status=terminal_status,
                value=parent_signal_value,
            )
        self._fault("checkpoint_finalize_run_after_write")
        return terminal_event_id

    @staticmethod
    def _row_to_runtime_state(row: Mapping[str, Any]) -> ExecutionRuntimeState:
        return ExecutionRuntimeState(
            generation=int(row["generation"]),
            phase=str(row["phase"]),
            drain_manifest_hash=(
                str(row["drain_manifest_hash"])
                if row["drain_manifest_hash"] is not None
                else None
            ),
            drain_count=int(row["drain_count"]),
            created_at=float(row["created_at"]),
            activated_at=(
                float(row["activated_at"])
                if row["activated_at"] is not None
                else None
            ),
            updated_at=float(row["updated_at"]),
        )

    async def _runtime_state_tx(
        self, db: aiosqlite.Connection
    ) -> ExecutionRuntimeState:
        row = await (
            await db.execute(
                "SELECT * FROM execution_runtime_state WHERE singleton_id=1"
            )
        ).fetchone()
        if row is None:
            raise RuntimeActivationError(
                "runtime_state_missing", "execution runtime state is not initialized"
            )
        return self._row_to_runtime_state(row)

    async def get_runtime_state(self) -> ExecutionRuntimeState:
        async with self._read_connection() as db:
            return await self._runtime_state_tx(db)

    async def get_execution_owner(self, run_id: str) -> tuple[str, int] | None:
        """Return the authoritative owner only when an execution row exists."""

        async with self._read_connection() as db:
            row = await (await db.execute(
                "SELECT owner_kind,owner_generation FROM execution_runs WHERE run_id=?",
                (run_id,),
            )).fetchone()
            return None if row is None else (str(row["owner_kind"]), int(row["owner_generation"]))

    @staticmethod
    def _manifest_hash(items: Sequence[LegacyDrainRef]) -> str:
        payload = [
            {"source_kind": item.source_kind, "source_run_id": item.source_run_id}
            for item in sorted(items)
        ]
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    async def _scan_legacy_drain_tx(
        self, db: aiosqlite.Connection
    ) -> tuple[LegacyDrainRef, ...]:
        execution_rows = await (
            await db.execute(
                """SELECT run_id FROM execution_runs
                WHERE owner_kind='legacy' AND owner_generation=0
                AND terminal_event_id IS NULL
                ORDER BY run_id"""
            )
        ).fetchall()
        workflow_rows = await (
            await db.execute(
                """SELECT workflow_runs.run_id FROM workflow_runs
                LEFT JOIN execution_runs
                    ON execution_runs.run_id=workflow_runs.run_id
                WHERE execution_runs.run_id IS NULL
                AND workflow_runs.ended_at IS NULL
                AND lower(workflow_runs.status) NOT IN (
                    'completed','failed','cancelled','canceled'
                )
                AND NOT (
                    lower(workflow_runs.status)='blocked'
                    AND COALESCE(workflow_runs.recovery_action, '')='read_only'
                )
                ORDER BY workflow_runs.run_id"""
            )
        ).fetchall()
        return tuple(
            sorted(
                (
                    LegacyDrainRef("execution_run", str(row["run_id"]))
                    for row in execution_rows
                ),
            )
        ) + tuple(
            sorted(
                LegacyDrainRef("workflow_run", str(row["run_id"]))
                for row in workflow_rows
            )
        )

    async def scan_legacy_drain_manifest(self) -> tuple[LegacyDrainRef, ...]:
        """Read the current set of active durable rows owned by legacy."""

        async with self._read_connection() as db:
            return await self._scan_legacy_drain_tx(db)

    async def _claim_legacy_drain_item_tx(
        self,
        db: aiosqlite.Connection,
        state: ExecutionRuntimeState,
        *,
        owner: str,
        lease_seconds: float,
    ) -> LegacyDrainLease | None:
        """Lease the next persisted legacy item while runtime ingress is closed."""

        if not owner.strip() or lease_seconds <= 0:
            raise ValueError("drain owner and positive lease_seconds are required")
        if state.phase != "draining" or state.generation != 0:
            raise RuntimeActivationError(
                "legacy_drain_not_active", "legacy drain claims require draining phase"
            )
        await self._assert_persisted_manifest_tx(db, state)
        now = float(self._clock())
        expiry = now + float(lease_seconds)
        row = await (
            await db.execute(
                """UPDATE execution_legacy_drain_items
                SET status='leased',lease_owner=?,lease_epoch=lease_epoch+1,
                    lease_expires_at=?,last_error=NULL,updated_at=?
                WHERE drain_item_id=(
                    SELECT drain_item_id FROM execution_legacy_drain_items
                    WHERE status IN ('pending','failed')
                       OR (status='leased' AND lease_expires_at<=?)
                    ORDER BY created_at,drain_item_id LIMIT 1
                )
                RETURNING manifest_generation,source_kind,source_run_id,
                          lease_epoch,lease_expires_at""",
                (owner, expiry, now, now),
            )
        ).fetchone()
        if row is None:
            return None
        return LegacyDrainLease(
            LegacyDrainRef(str(row["source_kind"]), str(row["source_run_id"])),
            int(row["manifest_generation"]), owner,
            int(row["lease_epoch"]), float(row["lease_expires_at"]),
        )

    async def _settle_legacy_drain_item_tx(
        self,
        db: aiosqlite.Connection,
        state: ExecutionRuntimeState,
        lease: LegacyDrainLease,
        *,
        error: str | None,
    ) -> bool:
        """CAS a leased item to drained/failed after checking source liveness."""

        if state.phase != "draining" or state.generation != 0:
            raise RuntimeActivationError(
                "legacy_drain_not_active",
                "legacy drain settlement requires draining phase",
            )
        await self._assert_persisted_manifest_tx(db, state)
        now = float(self._clock())
        if error is None:
            if lease.ref in set(await self._scan_legacy_drain_tx(db)):
                raise RuntimeActivationError(
                    "legacy_drain_item_active",
                    "legacy drain item cannot settle while its source is active",
                )
            status, drained_at, last_error = "drained", now, None
        else:
            status, drained_at, last_error = "failed", None, str(error)
        cursor = await db.execute(
            """UPDATE execution_legacy_drain_items
            SET status=?,lease_owner=NULL,lease_expires_at=NULL,last_error=?,
                drained_at=?,updated_at=?
            WHERE drain_item_id=? AND manifest_generation=? AND status='leased'
              AND lease_owner=? AND lease_epoch=? AND lease_expires_at>?""",
            (
                status, last_error, drained_at, now, lease.ref.drain_item_id,
                lease.manifest_generation, lease.owner, lease.epoch, now,
            ),
        )
        return cursor.rowcount == 1

    async def _assert_persisted_manifest_tx(
        self,
        db: aiosqlite.Connection,
        state: ExecutionRuntimeState,
    ) -> tuple[LegacyDrainRef, ...]:
        rows = await (
            await db.execute(
                """SELECT drain_item_id,manifest_generation,source_kind,source_run_id
                FROM execution_legacy_drain_items
                ORDER BY source_kind,source_run_id"""
            )
        ).fetchall()
        refs = tuple(
            LegacyDrainRef(str(row["source_kind"]), str(row["source_run_id"]))
            for row in rows
        )
        expected_generation = state.generation if state.generation > 0 else 1
        if any(int(row["manifest_generation"]) != expected_generation for row in rows):
            raise RuntimeActivationError(
                "drain_manifest_generation_mismatch",
                "legacy drain manifest contains another activation generation",
            )
        if any(str(row["drain_item_id"]) != ref.drain_item_id for row, ref in zip(rows, refs)):
            raise RuntimeActivationError(
                "drain_manifest_identity_mismatch",
                "legacy drain manifest contains an invalid stable identity",
            )
        if len(refs) != state.drain_count or self._manifest_hash(refs) != state.drain_manifest_hash:
            raise RuntimeActivationError(
                "drain_manifest_mismatch",
                "legacy drain manifest no longer matches the durable runtime fence",
            )
        return refs

    async def _advance_runtime_activation_tx(
        self, db: aiosqlite.Connection, state: ExecutionRuntimeState
    ) -> ExecutionRuntimeState:
        """Advance exactly one durable activation phase in the open transaction."""

        if state.phase == "legacy":
            items = await self._scan_legacy_drain_tx(db)
            manifest_hash = self._manifest_hash(items)
            now = float(self._clock())
            target_generation = 1
            for item in items:
                await db.execute(
                    """INSERT INTO execution_legacy_drain_items(
                    drain_item_id,manifest_generation,source_kind,source_run_id,status,
                    created_at,updated_at
                    ) VALUES(?,?,?,?, 'pending',?,?)""",
                    (
                        item.drain_item_id,
                        target_generation,
                        item.source_kind,
                        item.source_run_id,
                        now,
                        now,
                    ),
                )
            self._fault("activation_after_manifest")
            cursor = await db.execute(
                """UPDATE execution_runtime_state
                SET phase='draining',drain_manifest_hash=?,drain_count=?,updated_at=?
                WHERE singleton_id=1 AND phase='legacy' AND generation=0""",
                (manifest_hash, len(items), now),
            )
            if cursor.rowcount != 1:
                raise RuntimeActivationError(
                    "activation_cas_conflict", "legacy runtime activation CAS lost"
                )
            self._fault("activation_after_draining")
            return await self._runtime_state_tx(db)
        if state.phase == "draining":
            await self._assert_persisted_manifest_tx(db, state)
            pending = await (
                await db.execute(
                    """SELECT COUNT(*) FROM execution_legacy_drain_items
                    WHERE status<>'drained'"""
                )
            ).fetchone()
            if pending is None or int(pending[0]) != 0:
                raise RuntimeActivationError(
                    "legacy_drain_incomplete",
                    "all registered legacy durable rows must drain before activation",
                )
            if await self._scan_legacy_drain_tx(db):
                raise RuntimeActivationError(
                    "legacy_drain_changed",
                    "active legacy rows appeared after the drain manifest was frozen",
                )
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_runtime_state
                SET phase='activated',generation=1,activated_at=?,updated_at=?
                WHERE singleton_id=1 AND phase='draining' AND generation=0""",
                (now, now),
            )
            if cursor.rowcount != 1:
                raise RuntimeActivationError(
                    "activation_cas_conflict", "draining runtime activation CAS lost"
                )
            self._fault("activation_after_activated")
            return await self._runtime_state_tx(db)
        if state.phase == "activated" and state.generation > 0:
            await self._assert_persisted_manifest_tx(db, state)
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_runtime_state SET phase='open',updated_at=?
                WHERE singleton_id=1 AND phase='activated' AND generation=?""",
                (now, state.generation),
            )
            if cursor.rowcount != 1:
                raise RuntimeActivationError(
                    "activation_cas_conflict", "activated runtime open CAS lost"
                )
            self._fault("activation_after_open")
            return await self._runtime_state_tx(db)
        if state.phase == "open":
            await self._assert_persisted_manifest_tx(db, state)
            return state
        raise RuntimeActivationError(
            "invalid_runtime_phase", f"unsupported runtime phase: {state.phase}"
        )

    async def activate_runtime(
        self,
        *,
        require_empty: bool = True,
        command: RuntimeActivationCommand | None = None,
    ) -> ExecutionRuntimeState | LegacyDrainLease | bool | None:
        """Roll the durable runtime fence forward through its typed phases."""

        if command is not None:
            async with self._write_transaction() as db:
                state = await self._runtime_state_tx(db)
                if command.kind == "advance":
                    result = await self._advance_runtime_activation_tx(db, state)
                elif command.kind == "claim_drain":
                    result = await self._claim_legacy_drain_item_tx(
                        db,
                        state,
                        owner=str(command.owner or ""),
                        lease_seconds=command.lease_seconds,
                    )
                elif command.kind == "settle_drain":
                    if command.lease is None:
                        raise ValueError("settle_drain requires a lease")
                    result = await self._settle_legacy_drain_item_tx(
                        db, state, command.lease, error=command.error
                    )
                elif command.kind == "rollback":
                    result = await self._rollback_runtime_activation_tx(db, state)
                else:
                    raise ValueError(f"unknown runtime activation command: {command.kind}")
                await db.commit()
                return result

        for _ in range(3):
            async with self._write_transaction() as db:
                state = await self._runtime_state_tx(db)
                if state.phase == "open":
                    await self._assert_persisted_manifest_tx(db, state)
                    await db.commit()
                    return state
                state = await self._advance_runtime_activation_tx(db, state)
                await db.commit()
            if state.phase == "draining" and state.drain_count:
                if require_empty:
                    raise RuntimeActivationError(
                        "legacy_drain_required",
                        "empty-runtime activation cannot bypass registered legacy durable rows",
                    )
                return state
        return state

    async def _rollback_runtime_activation_tx(
        self, db: aiosqlite.Connection, state: ExecutionRuntimeState
    ) -> ExecutionRuntimeState:
        """Return an unused closed activation to legacy before any kernel row exists."""

        if state.phase == "legacy":
            return state
        if state.phase == "open":
            raise RuntimeActivationError(
                "runtime_ingress_open", "open runtime activation cannot be rolled back"
            )
        kernel_rows = await (
            await db.execute(
                """SELECT COUNT(*) FROM execution_runs
                WHERE owner_kind='kernel' AND owner_generation=?""",
                (state.generation,),
            )
        ).fetchone()
        if kernel_rows is not None and int(kernel_rows[0]):
            raise RuntimeActivationError(
                "activation_irreversible",
                "runtime generation already owns execution rows; roll forward is required",
            )
        active_drain = await (
            await db.execute(
                """SELECT COUNT(*) FROM execution_legacy_drain_items
                WHERE status IN ('pending','leased')"""
            )
        ).fetchone()
        if active_drain is not None and int(active_drain[0]):
            raise RuntimeActivationError(
                "legacy_drain_active", "active legacy drain claims prevent rollback"
            )
        await db.execute("DELETE FROM execution_legacy_drain_items")
        now = float(self._clock())
        cursor = await db.execute(
            """UPDATE execution_runtime_state
            SET generation=0,phase='legacy',drain_manifest_hash=NULL,drain_count=0,
                activated_at=NULL,updated_at=?
            WHERE singleton_id=1 AND phase IN ('draining','activated')""",
            (now,),
        )
        if cursor.rowcount != 1:
            raise RuntimeActivationError(
                "activation_cas_conflict", "activation rollback CAS lost"
            )
        return await self._runtime_state_tx(db)

    async def _required_start_owner_tx(
        self, db: aiosqlite.Connection
    ) -> tuple[str, int]:
        state = await self._runtime_state_tx(db)
        if state.phase == "legacy" and state.generation == 0:
            return "legacy", 0
        if state.phase == "open" and state.generation > 0:
            return "kernel", state.generation
        raise RuntimeActivationError(
            "runtime_ingress_closed",
            f"execution starts are closed while runtime phase is {state.phase}",
        )

    async def _required_recovery_owner_tx(
        self, db: aiosqlite.Connection
    ) -> tuple[str, int]:
        """Return the fenced owner whose durable runs may be recovered.

        Kernel bootstrap deliberately recovers already-owned work while the
        runtime is ``activated`` but before ingress becomes ``open``.  This is
        narrower than the start boundary: activation must keep rejecting new
        runs until bootstrap reconciliation completes.
        """

        state = await self._runtime_state_tx(db)
        if state.phase == "legacy" and state.generation == 0:
            return "legacy", 0
        if state.phase in {"activated", "open"} and state.generation > 0:
            return "kernel", state.generation
        raise RuntimeActivationError(
            "runtime_recovery_closed",
            f"execution recovery is closed while runtime phase is {state.phase}",
        )

    @staticmethod
    def _workspace_json(spec: RunCreate) -> str:
        return canonical_json(thaw_json(spec.context.workspace))

    @staticmethod
    def _provider_plan_json(spec: RunCreate) -> str:
        return canonical_json(thaw_json(spec.context.provider_plan))

    @staticmethod
    def _initial_started_at(spec: RunCreate, now: float) -> float | None:
        return now if spec.status is RunStatus.RUNNING else None

    @staticmethod
    def _row_to_record(row: Mapping[str, Any]) -> RunRecord:
        context = RunContext(
            session_id=str(row["session_id"]),
            root_run_id=str(row["root_run_id"]),
            parent_run_id=(
                str(row["parent_run_id"]) if row["parent_run_id"] is not None else None
            ),
            request_id=str(row["request_id"]),
            turn_id=str(row["turn_id"]),
            venue=str(row["venue"]),
            workspace=json.loads(str(row["workspace_json"])),
            capability_hash=str(row["capability_hash"]),
            provider_plan=json.loads(str(row["provider_plan_json"])),
            trace_id=str(row["trace_id"]),
            principal_id=str(row["principal_id"]),
            auth_epoch=int(row["auth_epoch"]),
            owner_key=(
                None
                if row["context_owner_key"] is None
                else str(row["context_owner_key"])
            ),
            profile_generation=int(row["context_profile_generation"]),
            binding_epoch=int(row["context_binding_epoch"]),
            schema_version=int(row["schema_version"]),
        )
        spec = RunCreate(
            run_id=str(row["run_id"]),
            idempotency_key=str(row["idempotency_key"]),
            context=context,
            payload_fingerprint=str(row["payload_fingerprint"]),
            capability_fingerprint=str(row["capability_fingerprint"]),
            driver_kind=str(row["driver_kind"]),
            profile_key=str(row["profile_key"]),
            persistence_level=str(row["persistence_level"]),
            # Creation status is not mutable execution state.  It is kept
            # non-terminal when reconstructing the immutable creation intent.
            status=RunStatus.CREATED,
            schema_version=int(row["schema_version"]),
        )
        return RunRecord(
            spec=spec,
            status=str(row["status"]),
            persistence_level=str(row["persistence_level"]),
            version=int(row["version"]),
            durable_seq=int(row["durable_seq"]),
            terminal_event_id=(
                str(row["terminal_event_id"])
                if row["terminal_event_id"] is not None
                else None
            ),
            cancel_reason=(
                str(row["cancel_reason"]) if row["cancel_reason"] is not None else None
            ),
            created_at=float(row["created_at"]),
            started_at=(float(row["started_at"]) if row["started_at"] is not None else None),
            updated_at=float(row["updated_at"]),
            ended_at=float(row["ended_at"]) if row["ended_at"] is not None else None,
            schema_version=int(row["schema_version"]),
        )

    async def read_run(self, run_id: str) -> RunRecord | None:
        """Read the immutable Run intent plus its current lifecycle state."""

        await self.initialize()
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?",
                    (str(run_id),),
                )
            ).fetchone()
            return None if row is None else self._row_to_record(row)

    @staticmethod
    def _row_to_event(row: Mapping[str, Any]) -> RunEvent:
        candidate = RunEventCandidate(
            event_key=str(row["event_key"]),
            kind=str(row["kind"]),
            status=str(row["status"]),
            driver_kind=str(row["driver_kind"]),
            correlation=json.loads(str(row["correlation_json"])),
            payload=json.loads(str(row["payload_json"])),
            error=(
                json.loads(str(row["error_json"]))
                if row["error_json"] is not None
                else None
            ),
            artifact_refs=json.loads(str(row["artifact_refs_json"])),
            schema_version=int(row["schema_version"]),
        )
        return RunEvent(
            event_id=str(row["event_id"]),
            run_id=str(row["run_id"]),
            root_run_id=str(row["root_run_id"]),
            session_id=str(row["session_id"]),
            durable_seq=int(row["durable_seq"]),
            candidate=candidate,
            created_at=float(row["created_at"]),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_delivery(row: Mapping[str, Any]) -> DeliveryRecord:
        values = dict(row)
        return DeliveryRecord(
            delivery_id=str(row["delivery_id"]),
            event_id=str(row["event_id"]),
            run_id=str(row["run_id"]),
            sink_kind=str(row["sink_kind"]),
            sink_instance=str(row["sink_instance"]),
            target_id=str(row["target_id"]),
            policy=str(row["policy"]),
            status=str(row["status"]),
            attempts=int(row["attempts"]),
            delivery_version=int(row["delivery_version"]),
            next_attempt_at=(
                float(row["next_attempt_at"])
                if row["next_attempt_at"] is not None
                else None
            ),
            last_error=(
                str(row["last_error"]) if row["last_error"] is not None else None
            ),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            delivered_at=(
                float(row["delivered_at"])
                if row["delivered_at"] is not None
                else None
            ),
            delivery_fence_epoch=(
                None
                if values.get("delivery_fence_epoch") is None
                else int(values["delivery_fence_epoch"])
            ),
            delivery_owner_id=(
                None
                if values.get("delivery_owner_id") is None
                else str(values["delivery_owner_id"])
            ),
            delivery_owner_generation=(
                None
                if values.get("delivery_owner_generation") is None
                else int(values["delivery_owner_generation"])
            ),
            delivery_dependency_hash=(
                None
                if values.get("delivery_dependency_hash") is None
                else str(values["delivery_dependency_hash"])
            ),
            delivery_snapshot_ref=(
                None
                if values.get("delivery_snapshot_ref") is None
                else str(values["delivery_snapshot_ref"])
            ),
            delivery_snapshot_hash=(
                None
                if values.get("delivery_snapshot_hash") is None
                else str(values["delivery_snapshot_hash"])
            ),
            release_receipt_ref=(
                None
                if values.get("release_receipt_ref") is None
                else str(values["release_receipt_ref"])
            ),
            release_receipt_hash=(
                None
                if values.get("release_receipt_hash") is None
                else str(values["release_receipt_hash"])
            ),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_child_command(row: Mapping[str, Any]) -> ChildCommandRecord:
        child_spec = RunCreate.from_dict(json.loads(str(row["child_spec_json"])))
        intent = ChildCommandIntent(
            operation_id=str(row["operation_id"]),
            parent_run_id=str(row["parent_run_id"]),
            command_id=str(row["command_id"]),
            child_spec=child_spec,
            child_request=json.loads(str(row["child_request_json"])),
            capability_subset=tuple(
                str(item)
                for item in json.loads(str(row["capability_subset_json"]))
            ),
            attachment_policy=str(row["join_policy"]),
            capability_snapshot_ref=str(row["capability_snapshot_ref"]),
            schema_version=int(row["schema_version"]),
        )
        if intent.intent_fingerprint != str(row["intent_fingerprint"]):
            raise IdempotencyConflict(
                "child_intent_corrupt", "persisted child command fingerprint differs"
            )
        return ChildCommandRecord(
            intent=intent,
            status=str(row["status"]),
            schedule_lease_owner=(
                str(row["schedule_lease_owner"])
                if row["schedule_lease_owner"] is not None
                else None
            ),
            schedule_lease_epoch=int(row["schedule_lease_epoch"]),
            schedule_lease_expires_at=(
                float(row["schedule_lease_expires_at"])
                if row["schedule_lease_expires_at"] is not None
                else None
            ),
            attempts=int(row["attempts"]),
            next_attempt_at=(
                float(row["next_attempt_at"])
                if row["next_attempt_at"] is not None
                else None
            ),
            last_error=(str(row["last_error"]) if row["last_error"] else None),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            ack_at=float(row["ack_at"]) if row["ack_at"] is not None else None,
        )

    @staticmethod
    def _row_to_child_signal(row: Mapping[str, Any]) -> ChildSignalRecord:
        return ChildSignalRecord(
            signal_id=str(row["signal_id"]),
            operation_id=str(row["operation_id"]),
            parent_run_id=str(row["parent_run_id"]),
            command_id=str(row["command_id"]),
            child_run_id=str(row["child_run_id"]),
            kind=str(row["kind"]),
            payload=json.loads(str(row["payload_json"])),
            attempts=int(row["attempts"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            delivered_at=(
                float(row["delivered_at"])
                if row["delivered_at"] is not None
                else None
            ),
        )

    @staticmethod
    def _row_to_decision(row: Mapping[str, Any]) -> DecisionRecord:
        request = DecisionOpen(
            decision_id=str(row["decision_id"]),
            run_id=str(row["run_id"]),
            nonce=str(row["nonce"]),
            kind=str(row["kind"]),
            prompt_schema_version=int(row["prompt_schema_version"]),
            prompt=json.loads(str(row["prompt_json"])),
            expires_at=(
                float(row["expires_at"]) if row["expires_at"] is not None else None
            ),
            domain_kind=(
                str(row["domain_kind"]) if row["domain_kind"] is not None else None
            ),
            domain_id=(str(row["domain_id"]) if row["domain_id"] is not None else None),
            call_id=(str(row["call_id"]) if row["call_id"] is not None else None),
            effect_id=(
                str(row["effect_id"]) if row["effect_id"] is not None else None
            ),
            tool_name=(
                str(row["tool_name"]) if row["tool_name"] is not None else None
            ),
            args_hash=(
                str(row["args_hash"]) if row["args_hash"] is not None else None
            ),
            capability_hash=(
                str(row["capability_hash"])
                if row["capability_hash"] is not None
                else None
            ),
            scope_hash=(
                str(row["scope_hash"]) if row["scope_hash"] is not None else None
            ),
            schema_version=int(row["schema_version"]),
        )
        response = (
            json.loads(str(row["response_json"]))
            if row["response_json"] is not None
            else None
        )
        return DecisionRecord(
            request=request,
            status=str(row["status"]),
            response_schema_version=(
                int(row["response_schema_version"])
                if row["response_schema_version"] is not None
                else None
            ),
            response=response,
            decision_version=int(row["decision_version"]),
            created_at=float(row["created_at"]),
            resolved_at=(
                float(row["resolved_at"]) if row["resolved_at"] is not None else None
            ),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_authorization(row: Mapping[str, Any]) -> DecisionAuthorization:
        return DecisionAuthorization(
            grant_id=str(row["grant_id"]),
            decision_id=str(row["decision_id"]),
            run_id=str(row["run_id"]),
            call_id=str(row["call_id"]),
            effect_id=str(row["effect_id"]),
            tool_name=str(row["tool_name"]),
            args_hash=str(row["args_hash"]),
            capability_hash=str(row["capability_hash"]),
            scope_hash=str(row["scope_hash"]),
            expires_at=float(row["expires_at"]),
            version=int(row["grant_version"]),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _assert_idempotent_intent(row: Mapping[str, Any], spec: RunCreate) -> None:
        existing = SqliteExecutionUnitOfWork._row_to_record(row).spec
        assert_idempotent_run_intent(existing, spec)

    async def _existing_for_spec(
        self, db: aiosqlite.Connection, spec: RunCreate
    ) -> aiosqlite.Row | None:
        by_key = await (
            await db.execute(
                "SELECT * FROM execution_runs WHERE idempotency_key=?",
                (spec.idempotency_key,),
            )
        ).fetchone()
        if by_key is not None:
            self._assert_idempotent_intent(by_key, spec)
            return by_key
        by_id = await (
            await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (spec.run_id,))
        ).fetchone()
        if by_id is not None:
            raise RunIdentityConflict(
                "run_identity_conflict", "run_id already names another idempotency intent"
            )
        return None

    @staticmethod
    def _assert_run_owner(
        row: Mapping[str, Any], expected_owner: tuple[str, int]
    ) -> None:
        stored_owner = (str(row["owner_kind"]), int(row["owner_generation"]))
        if stored_owner != expected_owner:
            raise RuntimeActivationError(
                "run_owner_conflict",
                "execution row belongs to another runtime owner generation",
            )

    async def _validate_parent_tx(
        self,
        db: aiosqlite.Connection,
        spec: RunCreate,
        expected_owner: tuple[str, int],
    ) -> None:
        parent_id = spec.context.parent_run_id
        if parent_id is None:
            return
        parent = await (
            await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (parent_id,))
        ).fetchone()
        if parent is None:
            raise RunNotFound("parent_not_found", f"parent run does not exist: {parent_id}")
        self._assert_run_owner(parent, expected_owner)
        expected = {
            "root_run_id": spec.context.root_run_id,
            "session_id": spec.context.session_id,
            "principal_id": spec.context.principal_id,
            "auth_epoch": spec.context.auth_epoch,
        }
        for field_name, value in expected.items():
            stored = parent[field_name]
            stored = int(stored) if field_name == "auth_epoch" else str(stored)
            if stored != value:
                raise RunIdentityConflict(
                    "parent_scope_conflict",
                    f"parent and child differ at {field_name}",
                )

    async def _insert_run_tx(
        self,
        db: aiosqlite.Connection,
        spec: RunCreate,
        *,
        version: int,
    ) -> tuple[aiosqlite.Row, bool]:
        expected_owner = await self._required_start_owner_tx(db)
        return await self._insert_run_for_owner_tx(
            db, spec, version=version, expected_owner=expected_owner
        )

    async def _insert_run_for_owner_tx(
        self,
        db: aiosqlite.Connection,
        spec: RunCreate,
        *,
        version: int,
        expected_owner: tuple[str, int],
    ) -> tuple[aiosqlite.Row, bool]:
        """Insert or reuse a run for an owner already fenced by the caller."""

        owner_kind, owner_generation = expected_owner
        existing = await self._existing_for_spec(db, spec)
        if existing is not None:
            self._assert_run_owner(existing, expected_owner)
            return existing, False
        await self._validate_parent_tx(db, spec, expected_owner)
        now = float(self._clock())
        await db.execute(
            """INSERT INTO execution_runs(
            run_id,schema_version,idempotency_key,session_id,root_run_id,parent_run_id,
            request_id,turn_id,venue,workspace_json,capability_hash,provider_plan_json,
            trace_id,principal_id,auth_epoch,payload_fingerprint,capability_fingerprint,
            driver_kind,profile_key,persistence_level,status,version,durable_seq,
            owner_kind,owner_generation,context_owner_key,
            context_profile_generation,context_binding_epoch,
            created_at,started_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?)""",
            (
                spec.run_id,
                spec.schema_version,
                spec.idempotency_key,
                spec.context.session_id,
                spec.context.root_run_id,
                spec.context.parent_run_id,
                spec.context.request_id,
                spec.context.turn_id,
                spec.context.venue,
                self._workspace_json(spec),
                spec.context.capability_hash,
                self._provider_plan_json(spec),
                spec.context.trace_id,
                spec.context.principal_id,
                spec.context.auth_epoch,
                spec.payload_fingerprint,
                spec.capability_fingerprint,
                spec.driver_kind,
                spec.profile_key,
                spec.persistence_level.value,
                spec.status.value,
                version,
                owner_kind,
                owner_generation,
                spec.context.owner_key,
                spec.context.profile_generation,
                spec.context.binding_epoch,
                now,
                self._initial_started_at(spec, now),
                now,
            ),
        )
        row = await (
            await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (spec.run_id,))
        ).fetchone()
        assert row is not None
        return row, True

    async def create(
        self, spec: RunCreate, *, initial_event: RunEventCandidate | None = None,
    ) -> CreateRunResult:
        if spec.persistence_level is not PersistenceLevel.DURABLE:
            raise PersistenceRequired(
                "durable_create_required",
                "SQLite create is reserved for runs that have crossed a durable boundary",
            )
        async with self._write_transaction() as db:
            row, created = await self._insert_run_tx(db, spec, version=0)
            self._fault("create_after_run")
            if initial_event is not None:
                _, row, _ = await self._append_event_tx(
                    db, row, expected_version=int(row["version"]),
                    event=initial_event, deliveries=(),
                )
            await db.commit()
            return CreateRunResult(self._row_to_record(row), created)

    @staticmethod
    def _row_to_start_snapshot(row: Mapping[str, Any]) -> RunStartSnapshotRecord:
        return RunStartSnapshotRecord(
            run_id=str(row["run_id"]),
            snapshot_schema_version=int(row["snapshot_schema_version"]),
            start_fingerprint=str(row["start_fingerprint"]),
            canonical_messages_json=str(row["canonical_messages_json"]),
            session_cursor_json=str(row["session_cursor_json"]),
            prepared_refs_json=str(row["prepared_refs_json"]),
            sanitized_request_json=str(row["sanitized_request_json"]),
            run_context_json=str(row["run_context_json"]),
            run_spec_json=str(row["run_spec_json"]),
            capability_snapshot_json=str(row["capability_snapshot_json"]),
            capability_snapshot_hash=str(row["capability_snapshot_hash"]),
            provider_launch_policy_json=str(row["provider_launch_policy_json"]),
            terminal_deliveries_json=str(row["terminal_deliveries_json"]),
            terminal_deliveries_hash=str(row["terminal_deliveries_hash"]),
            capability_lease_intent_ref=(
                None
                if row["capability_lease_intent_ref"] is None
                else str(row["capability_lease_intent_ref"])
            ),
            capability_lease_intent_hash=(
                None
                if row["capability_lease_intent_hash"] is None
                else str(row["capability_lease_intent_hash"])
            ),
            created_at=float(row["created_at"]),
            start_extension_receipts_json=str(
                row["start_extension_receipts_json"]
            ),
            start_extension_receipts_hash=str(
                row["start_extension_receipts_hash"]
            ),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _start_snapshot_values(
        snapshot: RunStartSnapshotRecord,
    ) -> tuple[object, ...]:
        return (
            snapshot.run_id,
            snapshot.schema_version,
            snapshot.snapshot_schema_version,
            snapshot.start_fingerprint,
            snapshot.canonical_messages_json,
            snapshot.session_cursor_json,
            snapshot.prepared_refs_json,
            snapshot.sanitized_request_json,
            snapshot.run_context_json,
            snapshot.run_spec_json,
            snapshot.capability_snapshot_json,
            snapshot.capability_snapshot_hash,
            snapshot.provider_launch_policy_json,
            snapshot.terminal_deliveries_json,
            snapshot.terminal_deliveries_hash,
            snapshot.capability_lease_intent_ref,
            snapshot.capability_lease_intent_hash,
            snapshot.start_extension_receipts_json,
            snapshot.start_extension_receipts_hash,
            snapshot.created_at,
        )

    async def _ensure_start_snapshot_tx(
        self,
        db: aiosqlite.Connection,
        *,
        spec: RunCreate,
        start_snapshot: RunStartSnapshotRecord,
        run_created: bool,
        start_commit_extensions: Sequence[Any],
    ) -> RunStartSnapshotRecord:
        existing_snapshot_row = await (
            await db.execute(
                """SELECT * FROM execution_run_start_snapshots
                WHERE run_id=?""",
                (spec.run_id,),
            )
        ).fetchone()
        if not run_created:
            if existing_snapshot_row is None:
                raise IdempotencyConflict(
                    "run_start_snapshot_missing",
                    "existing durable Run has no immutable start snapshot",
                )
            existing = self._row_to_start_snapshot(existing_snapshot_row)
            comparable = replace(
                existing,
                start_extension_receipts_json=(
                    start_snapshot.start_extension_receipts_json
                ),
                start_extension_receipts_hash=(
                    start_snapshot.start_extension_receipts_hash
                ),
            )
            if comparable != start_snapshot:
                raise IdempotencyConflict(
                    "run_start_snapshot_conflict",
                    "Run replay supplied a different start snapshot",
                )
            return existing

        receipts: list[Mapping[str, Any]] = []
        transaction = self.bind(db)
        for extension in start_commit_extensions:
            apply = getattr(extension, "apply_start_commit", None)
            if not callable(apply):
                raise TypeError("start commit extension has no apply_start_commit")
            receipt = apply(
                transaction,
                spec=spec,
                start_snapshot=start_snapshot,
            )
            if inspect.isawaitable(receipt):
                receipt = await receipt
            if hasattr(receipt, "to_dict"):
                receipt = receipt.to_dict()
            if not isinstance(receipt, Mapping):
                raise TypeError(
                    "start commit extension must return a typed descriptor"
                )
            receipts.append(dict(receipt))
        receipts_json = canonical_json(receipts)
        receipts_hash = hashlib.sha256(receipts_json.encode("utf-8")).hexdigest()
        snapshot = replace(
            start_snapshot,
            start_extension_receipts_json=receipts_json,
            start_extension_receipts_hash=receipts_hash,
        )
        await db.execute(
            """INSERT INTO execution_run_start_snapshots(
            run_id,schema_version,snapshot_schema_version,
            start_fingerprint,canonical_messages_json,
            session_cursor_json,prepared_refs_json,
            sanitized_request_json,run_context_json,run_spec_json,
            capability_snapshot_json,capability_snapshot_hash,
            provider_launch_policy_json,terminal_deliveries_json,
            terminal_deliveries_hash,capability_lease_intent_ref,
            capability_lease_intent_hash,
            start_extension_receipts_json,
            start_extension_receipts_hash,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            self._start_snapshot_values(snapshot),
        )
        return snapshot

    async def create_with_start_snapshot(
        self,
        spec: RunCreate,
        start_snapshot: RunStartSnapshotRecord,
        *,
        initial_event: RunEventCandidate | None = None,
        start_commit_extensions: Sequence[Any] = (),
    ) -> CreateRunResult:
        """Atomically create a durable Run, immutable start and host receipts."""

        if spec.persistence_level is not PersistenceLevel.DURABLE:
            raise PersistenceRequired(
                "durable_create_required",
                "a Run start snapshot requires durable persistence",
            )
        if start_snapshot.run_id != spec.run_id:
            raise IdempotencyConflict(
                "run_start_scope_conflict",
                "start snapshot is bound to another Run",
            )
        async with self._write_transaction() as db:
            row, created = await self._insert_run_tx(db, spec, version=0)
            start_snapshot = await self._ensure_start_snapshot_tx(
                db,
                spec=spec,
                start_snapshot=start_snapshot,
                run_created=created,
                start_commit_extensions=start_commit_extensions,
            )
            self._fault("create_with_start_after_snapshot")
            if initial_event is not None:
                _, row, _ = await self._append_event_tx(
                    db,
                    row,
                    expected_version=int(row["version"]),
                    event=initial_event,
                    deliveries=(),
                )
            await db.commit()
            return CreateRunResult(self._row_to_record(row), created)

    async def read_run_start_snapshot(
        self, run_id: str
    ) -> RunStartSnapshotRecord | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_run_start_snapshots
                    WHERE run_id=?""",
                    (run_id,),
                )
            ).fetchone()
            return None if row is None else self._row_to_start_snapshot(row)

    @staticmethod
    def _row_to_provider_invocation(
        row: Mapping[str, Any],
    ) -> ProviderInvocationRecord:
        return ProviderInvocationRecord(
            run_id=str(row["run_id"]),
            invocation_id=str(row["invocation_id"]),
            provider_id=str(row["provider_id"]),
            model_id=str(row["model_id"]),
            adapter_id=str(row["adapter_id"]),
            policy_snapshot_json=str(row["policy_snapshot_json"]),
            request_hash=str(row["request_hash"]),
            idempotency_group_id=str(row["idempotency_group_id"]),
            attempt_ordinal=int(row["attempt_ordinal"]),
            status=str(row["status"]),
            claim_owner=str(row["claim_owner"]),
            claim_epoch=int(row["claim_epoch"]),
            claimed_at=float(row["claimed_at"]),
            revocation_epoch=int(row["revocation_epoch"]),
            dispatch_started_ack_ref=(
                None
                if row["dispatch_started_ack_ref"] is None
                else str(row["dispatch_started_ack_ref"])
            ),
            dispatch_started_ack_hash=(
                None
                if row["dispatch_started_ack_hash"] is None
                else str(row["dispatch_started_ack_hash"])
            ),
            dispatch_started_at=(
                None
                if row["dispatch_started_at"] is None
                else float(row["dispatch_started_at"])
            ),
            stream_epoch=int(row["stream_epoch"]),
            outcome_ref=(
                None if row["outcome_ref"] is None else str(row["outcome_ref"])
            ),
            outcome_hash=(
                None if row["outcome_hash"] is None else str(row["outcome_hash"])
            ),
            updated_at=float(row["updated_at"]),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_provider_outcome(
        row: Mapping[str, Any],
    ) -> ProviderInvocationOutcome:
        return ProviderInvocationOutcome(
            run_id=str(row["run_id"]),
            invocation_id=str(row["invocation_id"]),
            payload_json=str(row["payload_json"]),
            payload_hash=str(row["payload_hash"]),
            created_at=float(row["created_at"]),
            schema_version=int(row["schema_version"]),
        )

    async def _record_provider_invocation_input_tx(
        self,
        db: Any,
        run_id: str,
        invocation_id: str,
        *,
        payload_json: str,
        payload_hash: str,
        created_at: float,
    ) -> None:
        try:
            normalized_payload = json.dumps(
                json.loads(payload_json),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                "provider input projection must be canonical JSON"
            ) from exc
        expected_hash = hashlib.sha256(
            normalized_payload.encode("utf-8")
        ).hexdigest()
        if payload_hash != expected_hash:
            raise ValueError("provider input projection hash mismatch")
        existing = await (
            await db.execute(
                """SELECT payload_json,payload_hash
                FROM execution_provider_invocation_inputs
                WHERE run_id=? AND invocation_id=?""",
                (run_id, invocation_id),
            )
        ).fetchone()
        if existing is not None:
            if (
                str(existing["payload_json"]) != normalized_payload
                or str(existing["payload_hash"]) != payload_hash
            ):
                raise IdempotencyConflict(
                    "provider_invocation_input_conflict",
                    "invocation already has a different input projection",
                )
            return
        await db.execute(
            """INSERT INTO execution_provider_invocation_inputs(
            run_id,invocation_id,schema_version,payload_json,payload_hash,
            created_at
            ) VALUES(?,?,1,?,?,?)""",
            (
                run_id,
                invocation_id,
                normalized_payload,
                payload_hash,
                float(created_at),
            ),
        )

    async def claim_provider_invocation(
        self,
        record: ProviderInvocationRecord,
        *,
        input_payload_json: str | None = None,
        input_payload_hash: str | None = None,
        input_created_at: float | None = None,
    ) -> ProviderInvocationRecord:
        if record.status is not ProviderInvocationStatus.CLAIMED:
            raise IdempotencyConflict(
                "provider_invocation_not_claimed",
                "new provider invocation rows must begin as claimed",
            )
        if record.outcome_ref is not None:
            raise IdempotencyConflict(
                "provider_invocation_has_outcome",
                "claimed provider invocation cannot already have an outcome",
            )
        input_values = (
            input_payload_json,
            input_payload_hash,
            input_created_at,
        )
        if any(value is not None for value in input_values) and not all(
            value is not None for value in input_values
        ):
            raise ValueError(
                "provider input projection fields must be supplied together"
            )
        async with self._write_transaction() as db:
            run = await (
                await db.execute(
                    "SELECT 1 FROM execution_runs WHERE run_id=?", (record.run_id,)
                )
            ).fetchone()
            if run is None:
                raise RunNotFound(
                    "run_not_found",
                    "provider invocation requires a durable Run",
                )
            existing = await (
                await db.execute(
                    """SELECT * FROM execution_provider_invocations
                    WHERE run_id=? AND invocation_id=?""",
                    (record.run_id, record.invocation_id),
                )
            ).fetchone()
            if existing is not None:
                stored = self._row_to_provider_invocation(existing)
                immutable_fields = (
                    "run_id",
                    "invocation_id",
                    "schema_version",
                    "provider_id",
                    "model_id",
                    "adapter_id",
                    "policy_snapshot_json",
                    "request_hash",
                    "idempotency_group_id",
                    "attempt_ordinal",
                    "status",
                    "claim_owner",
                    "claim_epoch",
                    "revocation_epoch",
                    "dispatch_started_ack_ref",
                    "dispatch_started_ack_hash",
                    "dispatch_started_at",
                    "stream_epoch",
                    "outcome_ref",
                    "outcome_hash",
                )
                if any(
                    getattr(stored, name) != getattr(record, name)
                    for name in immutable_fields
                ):
                    raise IdempotencyConflict(
                        "provider_invocation_conflict",
                        "invocation id already names a different dispatch",
                    )
                if input_payload_json is not None:
                    await self._record_provider_invocation_input_tx(
                        db,
                        record.run_id,
                        record.invocation_id,
                        payload_json=input_payload_json,
                        payload_hash=str(input_payload_hash),
                        created_at=float(input_created_at),
                    )
                return stored
            await db.execute(
                """INSERT INTO execution_provider_invocations(
                run_id,invocation_id,schema_version,provider_id,model_id,
                adapter_id,policy_snapshot_json,request_hash,
                idempotency_group_id,attempt_ordinal,status,claim_owner,
                claim_epoch,claimed_at,revocation_epoch,
                dispatch_started_ack_ref,dispatch_started_ack_hash,
                dispatch_started_at,stream_epoch,outcome_ref,outcome_hash,
                updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,'claimed',?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.run_id,
                    record.invocation_id,
                    record.schema_version,
                    record.provider_id,
                    record.model_id,
                    record.adapter_id,
                    record.policy_snapshot_json,
                    record.request_hash,
                    record.idempotency_group_id,
                    record.attempt_ordinal,
                    record.claim_owner,
                    record.claim_epoch,
                    record.claimed_at,
                    record.revocation_epoch,
                    record.dispatch_started_ack_ref,
                    record.dispatch_started_ack_hash,
                    record.dispatch_started_at,
                    record.stream_epoch,
                    record.outcome_ref,
                    record.outcome_hash,
                    record.updated_at,
                ),
            )
            if input_payload_json is not None:
                await self._record_provider_invocation_input_tx(
                    db,
                    record.run_id,
                    record.invocation_id,
                    payload_json=input_payload_json,
                    payload_hash=str(input_payload_hash),
                    created_at=float(input_created_at),
                )
            row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_invocations
                    WHERE run_id=? AND invocation_id=?""",
                    (record.run_id, record.invocation_id),
                )
            ).fetchone()
            assert row is not None
            return self._row_to_provider_invocation(row)

    async def read_provider_invocation(
        self, run_id: str, invocation_id: str
    ) -> ProviderInvocationRecord | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_invocations
                    WHERE run_id=? AND invocation_id=?""",
                    (run_id, invocation_id),
                )
            ).fetchone()
            return None if row is None else self._row_to_provider_invocation(row)

    async def read_provider_invocation_outcome(
        self, run_id: str, invocation_id: str
    ) -> ProviderInvocationOutcome | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_invocation_outcomes
                    WHERE run_id=? AND invocation_id=?""",
                    (run_id, invocation_id),
                )
            ).fetchone()
            return None if row is None else self._row_to_provider_outcome(row)

    @staticmethod
    def _validate_dispatch_ack(
        ref: str | None, digest: str | None, at: float | None
    ) -> None:
        if (ref is None, digest is None, at is None) not in {
            (True, True, True),
            (False, False, False),
        }:
            raise ValueError("dispatch ack ref/hash/time must be supplied together")
        if digest is not None and (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("dispatch ack hash must be lowercase SHA-256")

    async def complete_provider_invocation(
        self,
        run_id: str,
        invocation_id: str,
        *,
        expected_claim_epoch: int,
        outcome: ProviderInvocationOutcome,
        dispatch_started_ack_ref: str | None,
        dispatch_started_ack_hash: str | None,
        dispatch_started_at: float | None,
    ) -> ProviderInvocationRecord:
        if (outcome.run_id, outcome.invocation_id) != (run_id, invocation_id):
            raise IdempotencyConflict(
                "provider_outcome_scope_conflict",
                "provider outcome is bound to another invocation",
            )
        self._validate_dispatch_ack(
            dispatch_started_ack_ref,
            dispatch_started_ack_hash,
            dispatch_started_at,
        )
        outcome_ref = f"provider-outcome:{run_id}:{invocation_id}"
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_invocations
                    WHERE run_id=? AND invocation_id=?""",
                    (run_id, invocation_id),
                )
            ).fetchone()
            if row is None:
                raise IdempotencyConflict(
                    "provider_invocation_missing",
                    "provider outcome has no claimed invocation",
                )
            stored = self._row_to_provider_invocation(row)
            if stored.status is ProviderInvocationStatus.COMPLETED:
                existing_outcome = await (
                    await db.execute(
                        """SELECT * FROM execution_provider_invocation_outcomes
                        WHERE run_id=? AND invocation_id=?""",
                        (run_id, invocation_id),
                    )
                ).fetchone()
                if (
                    existing_outcome is None
                    or self._row_to_provider_outcome(existing_outcome) != outcome
                    or stored.outcome_ref != outcome_ref
                    or stored.outcome_hash != outcome.payload_hash
                ):
                    raise IdempotencyConflict(
                        "provider_outcome_conflict",
                        "completed invocation has a different outcome",
                    )
                return stored
            if (
                stored.status is not ProviderInvocationStatus.CLAIMED
                or stored.claim_epoch != expected_claim_epoch
            ):
                raise IdempotencyConflict(
                    "provider_invocation_not_claimed",
                    "provider invocation cannot complete from its current fence",
                )
            await db.execute(
                """INSERT INTO execution_provider_invocation_outcomes(
                run_id,invocation_id,schema_version,payload_json,payload_hash,
                created_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    outcome.run_id,
                    outcome.invocation_id,
                    outcome.schema_version,
                    outcome.payload_json,
                    outcome.payload_hash,
                    outcome.created_at,
                ),
            )
            cursor = await db.execute(
                """UPDATE execution_provider_invocations
                SET status='completed',dispatch_started_ack_ref=?,
                    dispatch_started_ack_hash=?,dispatch_started_at=?,
                    outcome_ref=?,outcome_hash=?,updated_at=?
                WHERE run_id=? AND invocation_id=? AND status='claimed'
                  AND claim_epoch=?""",
                (
                    dispatch_started_ack_ref,
                    dispatch_started_ack_hash,
                    dispatch_started_at,
                    outcome_ref,
                    outcome.payload_hash,
                    float(self._clock()),
                    run_id,
                    invocation_id,
                    expected_claim_epoch,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "provider_invocation_cas_conflict",
                    "provider invocation completion lost its claim fence",
                )
            updated = await (
                await db.execute(
                    """SELECT * FROM execution_provider_invocations
                    WHERE run_id=? AND invocation_id=?""",
                    (run_id, invocation_id),
                )
            ).fetchone()
            assert updated is not None
            return self._row_to_provider_invocation(updated)

    async def _settle_noncompleted_provider_invocation(
        self,
        status: ProviderInvocationStatus,
        run_id: str,
        invocation_id: str,
        *,
        expected_claim_epoch: int,
        outcome_ref: str | None,
        outcome_hash: str | None,
        dispatch_started_ack_ref: str | None,
        dispatch_started_ack_hash: str | None,
        dispatch_started_at: float | None,
        audit_reason: str | None,
        audit_error_type: str | None,
        audit_error_message: str | None,
    ) -> ProviderInvocationRecord:
        if status not in {
            ProviderInvocationStatus.FAILED,
            ProviderInvocationStatus.UNKNOWN,
        }:
            raise ValueError("noncompleted provider status is invalid")
        self._validate_dispatch_ack(
            dispatch_started_ack_ref,
            dispatch_started_ack_hash,
            dispatch_started_at,
        )
        if (outcome_ref is None) != (outcome_hash is None):
            raise ValueError("provider audit outcome ref/hash must be paired")
        normalized_reason = str(audit_reason or status.value).strip()[:500]
        if not normalized_reason:
            normalized_reason = status.value
        normalized_error_type = (
            str(audit_error_type).strip()[:200]
            if audit_error_type is not None
            else None
        )
        normalized_error_message = (
            str(audit_error_message).replace("\x00", "").strip()[:1000]
            if audit_error_message is not None
            else None
        )
        if normalized_error_type == "":
            normalized_error_type = None
        if normalized_error_message == "":
            normalized_error_message = None
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_invocations
                    WHERE run_id=? AND invocation_id=?""",
                    (run_id, invocation_id),
                )
            ).fetchone()
            if row is None:
                raise IdempotencyConflict(
                    "provider_invocation_missing",
                    "provider settlement has no claimed invocation",
                )
            stored = self._row_to_provider_invocation(row)
            if stored.status is status:
                expected = (
                    outcome_ref,
                    outcome_hash,
                    dispatch_started_ack_ref,
                    dispatch_started_ack_hash,
                    dispatch_started_at,
                )
                actual = (
                    stored.outcome_ref,
                    stored.outcome_hash,
                    stored.dispatch_started_ack_ref,
                    stored.dispatch_started_ack_hash,
                    stored.dispatch_started_at,
                )
                if actual != expected:
                    raise IdempotencyConflict(
                        "provider_settlement_conflict",
                        "provider settlement replay differs",
                    )
                existing_audit = await (
                    await db.execute(
                        """SELECT terminal_status,reason,error_type,error_message
                        FROM execution_provider_invocation_audits
                        WHERE run_id=? AND invocation_id=?""",
                        (run_id, invocation_id),
                    )
                ).fetchone()
                expected_audit = (
                    status.value,
                    normalized_reason,
                    normalized_error_type,
                    normalized_error_message,
                )
                if existing_audit is None:
                    await db.execute(
                        """INSERT INTO execution_provider_invocation_audits(
                        run_id,invocation_id,schema_version,terminal_status,
                        reason,error_type,error_message,created_at
                        ) VALUES(?,?,1,?,?,?,?,?)""",
                        (
                            run_id,
                            invocation_id,
                            status.value,
                            normalized_reason,
                            normalized_error_type,
                            normalized_error_message,
                            float(self._clock()),
                        ),
                    )
                elif tuple(existing_audit) != expected_audit:
                    raise IdempotencyConflict(
                        "provider_audit_conflict",
                        "provider invocation audit replay differs",
                    )
                return stored
            if (
                stored.status is not ProviderInvocationStatus.CLAIMED
                or stored.claim_epoch != expected_claim_epoch
            ):
                raise IdempotencyConflict(
                    "provider_invocation_not_claimed",
                    "provider invocation cannot settle from its current fence",
                )
            cursor = await db.execute(
                """UPDATE execution_provider_invocations
                SET status=?,dispatch_started_ack_ref=?,
                    dispatch_started_ack_hash=?,dispatch_started_at=?,
                    outcome_ref=?,outcome_hash=?,updated_at=?
                WHERE run_id=? AND invocation_id=? AND status='claimed'
                  AND claim_epoch=?""",
                (
                    status.value,
                    dispatch_started_ack_ref,
                    dispatch_started_ack_hash,
                    dispatch_started_at,
                    outcome_ref,
                    outcome_hash,
                    float(self._clock()),
                    run_id,
                    invocation_id,
                    expected_claim_epoch,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "provider_invocation_cas_conflict",
                    "provider invocation settlement lost its claim fence",
                )
            await db.execute(
                """INSERT INTO execution_provider_invocation_audits(
                run_id,invocation_id,schema_version,terminal_status,
                reason,error_type,error_message,created_at
                ) VALUES(?,?,1,?,?,?,?,?)""",
                (
                    run_id,
                    invocation_id,
                    status.value,
                    normalized_reason,
                    normalized_error_type,
                    normalized_error_message,
                    float(self._clock()),
                ),
            )
            updated = await (
                await db.execute(
                    """SELECT * FROM execution_provider_invocations
                    WHERE run_id=? AND invocation_id=?""",
                    (run_id, invocation_id),
                )
            ).fetchone()
            assert updated is not None
            return self._row_to_provider_invocation(updated)

    async def fail_provider_invocation(
        self,
        run_id: str,
        invocation_id: str,
        *,
        expected_claim_epoch: int,
        outcome_ref: str | None = None,
        outcome_hash: str | None = None,
        dispatch_started_ack_ref: str | None = None,
        dispatch_started_ack_hash: str | None = None,
        dispatch_started_at: float | None = None,
        audit_reason: str | None = None,
        audit_error_type: str | None = None,
        audit_error_message: str | None = None,
    ) -> ProviderInvocationRecord:
        return await self._settle_noncompleted_provider_invocation(
            ProviderInvocationStatus.FAILED,
            run_id,
            invocation_id,
            expected_claim_epoch=expected_claim_epoch,
            outcome_ref=outcome_ref,
            outcome_hash=outcome_hash,
            dispatch_started_ack_ref=dispatch_started_ack_ref,
            dispatch_started_ack_hash=dispatch_started_ack_hash,
            dispatch_started_at=dispatch_started_at,
            audit_reason=audit_reason,
            audit_error_type=audit_error_type,
            audit_error_message=audit_error_message,
        )

    async def mark_unknown_provider_invocation(
        self,
        run_id: str,
        invocation_id: str,
        *,
        expected_claim_epoch: int,
        outcome_ref: str | None = None,
        outcome_hash: str | None = None,
        dispatch_started_ack_ref: str | None = None,
        dispatch_started_ack_hash: str | None = None,
        dispatch_started_at: float | None = None,
        audit_reason: str | None = None,
        audit_error_type: str | None = None,
        audit_error_message: str | None = None,
    ) -> ProviderInvocationRecord:
        return await self._settle_noncompleted_provider_invocation(
            ProviderInvocationStatus.UNKNOWN,
            run_id,
            invocation_id,
            expected_claim_epoch=expected_claim_epoch,
            outcome_ref=outcome_ref,
            outcome_hash=outcome_hash,
            dispatch_started_ack_ref=dispatch_started_ack_ref,
            dispatch_started_ack_hash=dispatch_started_ack_hash,
            dispatch_started_at=dispatch_started_at,
            audit_reason=audit_reason,
            audit_error_type=audit_error_type,
            audit_error_message=audit_error_message,
        )

    @staticmethod
    def _row_to_run_fence(row: Mapping[str, Any]) -> ExecutionRunFenceRecord:
        return ExecutionRunFenceRecord(
            run_id=str(row["run_id"]),
            fence_kind=str(row["fence_kind"]),
            fence_version=int(row["fence_version"]),
            status=str(row["status"]),
            reason=str(row["reason"]),
            source_ref=str(row["source_ref"]),
            created_at=float(row["created_at"]),
            schema_version=int(row["schema_version"]),
        )

    async def write_run_fence(
        self, record: ExecutionRunFenceRecord
    ) -> ExecutionRunFenceRecord:
        async with self._write_transaction() as db:
            existing = await (
                await db.execute(
                    """SELECT * FROM execution_run_fences
                    WHERE run_id=? AND fence_kind=? AND fence_version=?""",
                    (record.run_id, record.fence_kind, record.fence_version),
                )
            ).fetchone()
            if existing is not None:
                stored = self._row_to_run_fence(existing)
                if stored != record:
                    raise IdempotencyConflict(
                        "run_fence_conflict",
                        "run fence identity already names different facts",
                    )
                return stored
            latest = await (
                await db.execute(
                    """SELECT fence_version FROM execution_run_fences
                    WHERE run_id=? AND fence_kind=?
                    ORDER BY fence_version DESC LIMIT 1""",
                    (record.run_id, record.fence_kind),
                )
            ).fetchone()
            expected_version = 0 if latest is None else int(latest[0]) + 1
            if record.fence_version != expected_version:
                raise VersionConflict(
                    "run_fence_version_not_monotonic",
                    "run fence version must advance by exactly one",
                )
            await db.execute(
                """INSERT INTO execution_run_fences(
                run_id,fence_kind,fence_version,schema_version,status,reason,
                source_ref,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    record.run_id,
                    record.fence_kind,
                    record.fence_version,
                    record.schema_version,
                    record.status.value,
                    record.reason,
                    record.source_ref,
                    record.created_at,
                ),
            )
            return record

    async def read_run_fence(
        self,
        run_id: str,
        fence_kind: str,
        fence_version: int | None = None,
    ) -> ExecutionRunFenceRecord | None:
        async with self._read_connection() as db:
            if fence_version is None:
                row = await (
                    await db.execute(
                        """SELECT * FROM execution_run_fences
                        WHERE run_id=? AND fence_kind=?
                        ORDER BY fence_version DESC LIMIT 1""",
                        (run_id, fence_kind),
                    )
                ).fetchone()
            else:
                row = await (
                    await db.execute(
                        """SELECT * FROM execution_run_fences
                        WHERE run_id=? AND fence_kind=? AND fence_version=?""",
                        (run_id, fence_kind, fence_version),
                    )
                ).fetchone()
            return None if row is None else self._row_to_run_fence(row)

    @staticmethod
    def _event_json(candidate: RunEventCandidate) -> dict[str, str | None]:
        return {
            "correlation_json": canonical_json(thaw_json(candidate.correlation)),
            "payload_json": canonical_json(thaw_json(candidate.payload)),
            "error_json": (
                canonical_json(thaw_json(candidate.error))
                if candidate.error is not None
                else None
            ),
            "artifact_refs_json": canonical_json(list(candidate.artifact_refs)),
        }

    def _assert_event_matches(
        self,
        row: Mapping[str, Any],
        run: Mapping[str, Any],
        candidate: RunEventCandidate,
    ) -> None:
        expected_id = stable_event_id(str(run["run_id"]), candidate.event_key)
        payload = self._event_json(candidate)
        expected: dict[str, object] = {
            "event_id": expected_id,
            "kind": candidate.kind,
            "status": candidate.status.value,
            "driver_kind": candidate.driver_kind,
            **payload,
        }
        for field_name, value in expected.items():
            if row[field_name] != value:
                raise IdempotencyConflict(
                    "event_intent_conflict",
                    f"event_key already names different {field_name}",
                )

    @staticmethod
    def _delivery_identity(delivery: DeliverySpec) -> tuple[str, str, str, str]:
        return (
            delivery.sink_kind,
            delivery.sink_instance,
            delivery.target_id,
            delivery.policy.value,
        )

    async def _assert_delivery_set_tx(
        self,
        db: aiosqlite.Connection,
        event_id: str,
        deliveries: Sequence[DeliverySpec],
    ) -> None:
        rows = await (
            await db.execute(
                """SELECT sink_kind,sink_instance,target_id,policy
                FROM execution_deliveries WHERE event_id=?""",
                (event_id,),
            )
        ).fetchall()
        stored = {
            (str(row["sink_kind"]), str(row["sink_instance"]), str(row["target_id"]), str(row["policy"]))
            for row in rows
        }
        requested = {self._delivery_identity(item) for item in deliveries}
        if stored != requested:
            raise IdempotencyConflict(
                "delivery_intent_conflict",
                "event replay supplied a different delivery set",
            )

    async def _insert_deliveries_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        event_id: str,
        deliveries: Sequence[DeliverySpec],
        now: float,
        delivery_fence: Mapping[str, Any] | None = None,
        release_receipt: Mapping[str, Any] | None = None,
    ) -> None:
        identities = [self._delivery_identity(item) for item in deliveries]
        if len(set(identities)) != len(identities):
            raise IdempotencyConflict(
                "duplicate_delivery", "one event cannot contain duplicate sink intents"
            )
        for delivery in deliveries:
            fence_values: tuple[object | None, ...] = (None,) * 8
            if delivery_fence is not None:
                required = {
                    "delivery_fence_epoch",
                    "owner_id",
                    "owner_generation",
                    "dependency_hash",
                    "snapshot_ref",
                    "snapshot_hash",
                }
                if set(delivery_fence) != required:
                    raise IdempotencyConflict(
                        "invalid_delivery_fence",
                        "terminal delivery fence fields differ",
                    )
                for hash_name in ("dependency_hash", "snapshot_hash"):
                    digest = str(delivery_fence[hash_name])
                    if len(digest) != 64 or any(
                        ch not in "0123456789abcdef" for ch in digest
                    ):
                        raise IdempotencyConflict(
                            "invalid_delivery_fence",
                            f"{hash_name} must be lowercase SHA-256",
                        )
                receipt_ref = None
                receipt_hash = None
                if release_receipt is not None:
                    receipt_ref = str(release_receipt.get("ref") or "")
                    receipt_hash = str(
                        release_receipt.get("content_hash") or ""
                    )
                    if not receipt_ref or len(receipt_hash) != 64:
                        raise IdempotencyConflict(
                            "invalid_release_receipt",
                            "release receipt ref/hash are required",
                        )
                fence_values = (
                    int(delivery_fence["delivery_fence_epoch"]),
                    str(delivery_fence["owner_id"]),
                    int(delivery_fence["owner_generation"]),
                    str(delivery_fence["dependency_hash"]),
                    str(delivery_fence["snapshot_ref"]),
                    str(delivery_fence["snapshot_hash"]),
                    receipt_ref,
                    receipt_hash,
                )
            await db.execute(
                """INSERT INTO execution_deliveries(
                delivery_id,schema_version,event_id,run_id,sink_kind,sink_instance,
                target_id,policy,status,created_at,updated_at,
                delivery_fence_epoch,delivery_owner_id,
                delivery_owner_generation,delivery_dependency_hash,
                delivery_snapshot_ref,delivery_snapshot_hash,
                release_receipt_ref,release_receipt_hash
                ) VALUES(?,1,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?,?,?,?)""",
                (
                    stable_delivery_id(event_id, delivery),
                    event_id,
                    run_id,
                    delivery.sink_kind,
                    delivery.sink_instance,
                    delivery.target_id,
                    delivery.policy.value,
                    now,
                    now,
                    *fence_values,
                ),
            )

    async def _append_event_tx(
        self,
        db: aiosqlite.Connection,
        run: aiosqlite.Row,
        *,
        expected_version: int,
        event: RunEventCandidate,
        deliveries: Sequence[DeliverySpec],
    ) -> tuple[RunEvent, aiosqlite.Row, bool]:
        if event.driver_kind != str(run["driver_kind"]):
            raise RunIdentityConflict(
                "driver_event_conflict", "event driver differs from the run owner"
            )
        existing = await (
            await db.execute(
                "SELECT * FROM execution_events WHERE run_id=? AND event_key=?",
                (run["run_id"], event.event_key),
            )
        ).fetchone()
        if existing is not None:
            self._assert_event_matches(existing, run, event)
            await self._assert_delivery_set_tx(db, str(existing["event_id"]), deliveries)
            hydrated = dict(existing)
            hydrated["root_run_id"] = run["root_run_id"]
            hydrated["session_id"] = run["session_id"]
            return self._row_to_event(hydrated), run, True
        if str(run["status"]) in {status.value for status in TERMINAL_RUN_STATUSES}:
            raise TerminalConflict(
                "run_already_terminal", "a terminal run cannot append more events"
            )
        if int(run["version"]) != expected_version:
            raise VersionConflict(
                "stale_run_version",
                f"expected run version {expected_version}, found {run['version']}",
            )
        now = float(self._clock())
        durable_seq = int(run["durable_seq"]) + 1
        event_id = stable_event_id(str(run["run_id"]), event.event_key)
        payload = self._event_json(event)
        await db.execute(
            """INSERT INTO execution_events(
            event_id,schema_version,event_key,run_id,durable_seq,kind,status,driver_kind,
            correlation_json,payload_json,error_json,artifact_refs_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event_id,
                event.schema_version,
                event.event_key,
                run["run_id"],
                durable_seq,
                event.kind,
                event.status.value,
                event.driver_kind,
                payload["correlation_json"],
                payload["payload_json"],
                payload["error_json"],
                payload["artifact_refs_json"],
                now,
            ),
        )
        await self._insert_deliveries_tx(
            db,
            run_id=str(run["run_id"]),
            event_id=event_id,
            deliveries=deliveries,
            now=now,
        )
        cursor = await db.execute(
            """UPDATE execution_runs SET durable_seq=?,version=version+1,updated_at=?
            WHERE run_id=? AND version=? AND terminal_event_id IS NULL""",
            (durable_seq, now, run["run_id"], expected_version),
        )
        if cursor.rowcount != 1:
            raise VersionConflict(
                "stale_run_version", "run changed before event commit"
            )
        updated = await (
            await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (run["run_id"],))
        ).fetchone()
        stored = await (
            await db.execute("SELECT * FROM execution_events WHERE event_id=?", (event_id,))
        ).fetchone()
        assert updated is not None and stored is not None
        hydrated = dict(stored)
        hydrated["root_run_id"] = updated["root_run_id"]
        hydrated["session_id"] = updated["session_id"]
        return self._row_to_event(hydrated), updated, False

    async def get_event(self, event_id: str) -> RunEvent:
        event_key = str(event_id).strip()
        if not event_key:
            raise EventNotFound("event_not_found", "event_id must be non-empty")
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT execution_events.*,execution_runs.root_run_id,
                    execution_runs.session_id FROM execution_events
                    JOIN execution_runs USING(run_id)
                    WHERE execution_events.event_id=?""",
                    (event_key,),
                )
            ).fetchone()
            if row is None:
                raise EventNotFound(
                    "event_not_found", f"execution event does not exist: {event_key}"
                )
            return self._row_to_event(row)

    async def read_terminal_extension_receipts(
        self, run_id: str, event_id: str
    ) -> tuple[Mapping[str, str], ...]:
        """Read immutable host-extension receipts in their committed order."""

        run_key = str(run_id).strip()
        event_key = str(event_id).strip()
        if not run_key or not event_key:
            raise ValueError("run_id and event_id must be non-empty")
        async with self._read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT kind,ref,content_hash
                    FROM execution_terminal_extension_receipts
                    WHERE run_id=? AND event_id=?
                    ORDER BY receipt_order""",
                    (run_key, event_key),
                )
            ).fetchall()
            return tuple(
                {
                    "kind": str(row["kind"]),
                    "ref": str(row["ref"]),
                    "content_hash": str(row["content_hash"]),
                }
                for row in rows
            )

    async def list_events(
        self,
        run_id: str,
        *,
        after_durable_seq: int = 0,
    ) -> tuple[RunEvent, ...]:
        if (
            not isinstance(after_durable_seq, int)
            or isinstance(after_durable_seq, bool)
            or after_durable_seq < 0
        ):
            raise ValueError("after_durable_seq must be a non-negative integer")
        async with self._read_connection() as db:
            run = await (
                await db.execute(
                    "SELECT root_run_id,session_id FROM execution_runs WHERE run_id=?",
                    (run_id,),
                )
            ).fetchone()
            if run is None:
                raise RunNotFound(
                    "run_not_found", f"execution run does not exist: {run_id}"
                )
            rows = await (
                await db.execute(
                    """SELECT execution_events.*,? AS root_run_id,? AS session_id
                    FROM execution_events WHERE run_id=? AND durable_seq>?
                    ORDER BY durable_seq,event_id""",
                    (run["root_run_id"], run["session_id"], run_id, after_durable_seq),
                )
            ).fetchall()
            return tuple(self._row_to_event(row) for row in rows)

    async def list_event_deliveries(
        self, event_id: str
    ) -> tuple[DeliveryRecord, ...]:
        async with self._read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT * FROM execution_deliveries WHERE event_id=?
                    ORDER BY sink_kind,sink_instance,target_id""",
                    (event_id,),
                )
            ).fetchall()
            return tuple(self._row_to_delivery(row) for row in rows)

    async def list_session_terminal_events(
        self,
        session_id: str,
        target_id: str,
        *,
        session_epoch: int,
        limit: int | None = None,
    ) -> tuple[RunEvent, ...]:
        """Read every current-epoch root failure visible at one DB snapshot.

        ``None`` is intentionally unbounded: product consistency gates must
        not silently omit old failures merely because more than one repair
        page accumulated while the dispatcher was unavailable.
        """

        session_key = str(session_id or "").strip()
        target_key = str(target_id or "").strip()
        if not session_key or not target_key:
            return ()
        bounded = None if limit is None else max(1, min(int(limit), 64))
        limit_sql = "" if bounded is None else " LIMIT ?"
        params: list[object] = [
            session_key,
            target_key,
            int(session_epoch),
        ]
        if bounded is not None:
            params.append(bounded)
        async with self._read_connection() as db:
            rows = await (
                await db.execute(
                    f"""SELECT e.*,r.root_run_id,r.session_id
                    FROM execution_events AS e
                    JOIN execution_runs AS r ON r.run_id=e.run_id
                    WHERE r.session_id=? AND r.run_id=r.root_run_id
                      AND e.kind='run.final'
                      AND e.status IN ('failed','cancelled')
                      AND (
                        EXISTS (
                          SELECT 1 FROM execution_deliveries AS d
                          WHERE d.event_id=e.event_id AND d.run_id=e.run_id
                            AND d.sink_kind='session_terminal'
                            AND d.sink_instance='session-transcript-v1'
                            AND d.target_id=? AND d.status!='discarded'
                        )
                        OR (
                          r.auth_epoch=?
                          AND NOT EXISTS (
                            SELECT 1 FROM execution_deliveries AS d0
                            WHERE d0.event_id=e.event_id
                              AND d0.run_id=e.run_id
                              AND d0.sink_kind='session_terminal'
                          )
                        )
                      )
                    ORDER BY e.created_at,e.event_id{limit_sql}""",
                    tuple(params),
                )
            ).fetchall()
        return tuple(self._row_to_event(row) for row in rows)

    async def claim_delivery_tx(
        self,
        connection: aiosqlite.Connection,
        *,
        owner_generation: int,
        sink_keys: Sequence[tuple[str, str]] = (),
        claim_ttl_seconds: float = 30.0,
        now: float,
    ) -> DeliveryRecord | None:
        """Claim one execution delivery inside the caller-owned transaction."""

        if isinstance(owner_generation, bool) or not isinstance(owner_generation, int) or owner_generation < 1:
            raise ValueError("owner_generation must be a positive kernel generation")
        ttl = float(claim_ttl_seconds)
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("claim_ttl_seconds must be finite and positive")
        normalized_keys = tuple(
            (str(kind).strip(), str(instance).strip())
            for kind, instance in sink_keys
        )
        if any(not kind or not instance for kind, instance in normalized_keys):
            raise ValueError("sink keys require non-empty kind and instance")
        if len(dict.fromkeys(normalized_keys)) != len(normalized_keys):
            raise ValueError("sink keys must be unique")

        now = float(now)
        if not math.isfinite(now):
            raise ValueError("delivery claim time must be finite")
        eligible = """(
            status='pending'
            OR (status='failed' AND COALESCE(next_attempt_at,0)<=?)
            OR (status='delivering' AND next_attempt_at IS NOT NULL
                AND next_attempt_at<=? AND policy!='best_effort')
        )"""
        select_eligible = """(
            d.status='pending'
            OR (d.status='failed' AND COALESCE(d.next_attempt_at,0)<=?)
            OR (d.status='delivering' AND d.next_attempt_at IS NOT NULL
                AND d.next_attempt_at<=? AND d.policy!='best_effort')
        )"""
        sink_clause = ""
        sink_params: list[object] = []
        if normalized_keys:
            sink_clause = " AND (" + " OR ".join(
                "(d.sink_kind=? AND d.sink_instance=?)" for _ in normalized_keys
            ) + ")"
            for kind, instance in normalized_keys:
                sink_params.extend((kind, instance))

        # BEST_EFFORT is at-most-one physical attempt.  If its worker
        # vanished after claim, persistently discard the expired claim;
        # replaying TTS after restart is worse than dropping the cue.
        await connection.execute(
            """UPDATE execution_deliveries SET status='discarded',
            delivery_version=delivery_version+1,next_attempt_at=NULL,
            last_error='best-effort claim expired',updated_at=?
            WHERE status='delivering' AND policy='best_effort'
            AND next_attempt_at IS NOT NULL AND next_attempt_at<=?
            AND EXISTS(SELECT 1 FROM execution_runs r
                JOIN execution_runtime_state s ON s.singleton_id=1
                WHERE r.run_id=execution_deliveries.run_id
                AND r.owner_kind='kernel' AND r.owner_generation=?
                AND s.generation=? AND s.phase IN ('activated','open'))""",
            (now, now, owner_generation, owner_generation),
        )
        row = await (
            await connection.execute(
                f"""SELECT d.* FROM execution_deliveries d
                JOIN execution_runs r ON r.run_id=d.run_id
                JOIN execution_runtime_state s ON s.singleton_id=1
                WHERE r.owner_kind='kernel' AND r.owner_generation=?
                AND s.generation=? AND s.phase IN ('activated','open')
                AND {select_eligible}{sink_clause}
                ORDER BY CASE d.policy
                    WHEN 'durable_required' THEN 0
                    WHEN 'retry_while_bound' THEN 1 ELSE 2 END,
                    d.created_at,d.delivery_id LIMIT 1""",
                (owner_generation, owner_generation, now, now, *sink_params),
            )
        ).fetchone()
        if row is None:
            return None
        lease_expires_at = now + ttl
        cursor = await connection.execute(
            f"""UPDATE execution_deliveries SET status='delivering',
            attempts=attempts+1,delivery_version=delivery_version+1,
            next_attempt_at=?,updated_at=?
            WHERE delivery_id=? AND delivery_version=? AND {eligible}""",
            (
                lease_expires_at,
                now,
                row["delivery_id"],
                row["delivery_version"],
                now,
                now,
            ),
        )
        if cursor.rowcount != 1:
            raise DeliveryClaimConflict(
                "delivery_claim_conflict", "delivery changed before claim CAS"
            )
        claimed = await (
            await connection.execute(
                "SELECT * FROM execution_deliveries WHERE delivery_id=?",
                (row["delivery_id"],),
            )
        ).fetchone()
        assert claimed is not None
        return self._row_to_delivery(claimed)

    async def _assert_delivery_owner_tx(
        self, connection: aiosqlite.Connection, delivery_id: str, owner_generation: int
    ) -> None:
        row = await (await connection.execute(
            """SELECT r.owner_kind,r.owner_generation,s.generation,s.phase
            FROM execution_deliveries d JOIN execution_runs r ON r.run_id=d.run_id
            JOIN execution_runtime_state s ON s.singleton_id=1 WHERE d.delivery_id=?""",
            (delivery_id,),
        )).fetchone()
        if row is None:
            raise DeliveryNotFound("delivery_not_found", f"execution delivery does not exist: {delivery_id}")
        if (
            str(row["owner_kind"]) != "kernel"
            or int(row["owner_generation"]) != owner_generation
            or int(row["generation"]) != owner_generation
            or str(row["phase"]) not in {"activated", "open"}
        ):
            raise DeliveryClaimConflict(
                "delivery_owner_fence", "delivery owner generation is stale or inactive"
            )

    async def claim_delivery(
        self,
        *,
        owner_generation: int,
        sink_keys: Sequence[tuple[str, str]] = (),
        claim_ttl_seconds: float = 30.0,
    ) -> DeliveryRecord | None:
        now = float(self._clock())
        async with self._write_transaction() as db:
            claimed = await self.claim_delivery_tx(
                db,
                owner_generation=owner_generation,
                sink_keys=sink_keys,
                claim_ttl_seconds=claim_ttl_seconds,
                now=now,
            )
            await db.commit()
            return claimed

    async def complete_delivery_tx(
        self,
        connection: aiosqlite.Connection,
        delivery_id: str,
        *,
        expected_version: int,
        owner_generation: int,
        now: float,
    ) -> DeliveryRecord:
        """Complete one claimed execution delivery in the caller transaction."""

        await self._assert_delivery_owner_tx(connection, delivery_id, owner_generation)
        now = float(now)
        if not math.isfinite(now):
            raise ValueError("delivery completion time must be finite")
        cursor = await connection.execute(
            """UPDATE execution_deliveries SET status='delivered',
            delivery_version=delivery_version+1,next_attempt_at=NULL,last_error=NULL,
            updated_at=?,delivered_at=? WHERE delivery_id=?
            AND delivery_version=? AND status='delivering'
            """,
            (now, now, delivery_id, expected_version),
        )
        if cursor.rowcount != 1:
            existing = await (
                await connection.execute(
                    "SELECT * FROM execution_deliveries WHERE delivery_id=?",
                    (delivery_id,),
                )
            ).fetchone()
            if existing is None:
                raise DeliveryNotFound(
                    "delivery_not_found",
                    f"execution delivery does not exist: {delivery_id}",
                )
            if (
                str(existing["status"]) == DeliveryStatus.DELIVERED.value
                and int(existing["delivery_version"]) == expected_version + 1
            ):
                return self._row_to_delivery(existing)
            raise DeliveryClaimConflict(
                "delivery_claim_conflict", "delivery completion lost its claim fence"
            )
        row = await (
            await connection.execute(
                "SELECT * FROM execution_deliveries WHERE delivery_id=?",
                (delivery_id,),
            )
        ).fetchone()
        assert row is not None
        return self._row_to_delivery(row)

    async def settle_delivery(
        self,
        delivery_id: str,
        *,
        expected_version: int,
        owner_generation: int,
        error: str | None = None,
        retry_at: float | None = None,
        discard: bool = False,
    ) -> DeliveryRecord:
        now = float(self._clock())
        async with self._write_transaction() as db:
            if error is None:
                completed = await self.complete_delivery_tx(
                    db, delivery_id, expected_version=expected_version,
                    owner_generation=owner_generation, now=now,
                )
            else:
                completed = await self.release_delivery_tx(
                    db, delivery_id, expected_version=expected_version,
                    owner_generation=owner_generation, error=error,
                    retry_at=retry_at, discard=discard, now=now,
                )
            await db.commit()
            return completed

    async def release_delivery_tx(
        self,
        connection: aiosqlite.Connection,
        delivery_id: str,
        *,
        expected_version: int,
        owner_generation: int,
        error: str,
        retry_at: float | None,
        discard: bool,
        now: float,
    ) -> DeliveryRecord:
        """Release one claimed execution delivery in the caller transaction."""

        await self._assert_delivery_owner_tx(connection, delivery_id, owner_generation)
        message = str(error).strip()
        if not message:
            raise ValueError("delivery error must be non-empty")
        if not discard and retry_at is None:
            raise ValueError("retryable delivery release requires retry_at")
        if retry_at is not None and not math.isfinite(float(retry_at)):
            raise ValueError("retry_at must be finite")
        status = DeliveryStatus.DISCARDED if discard else DeliveryStatus.FAILED
        now = float(now)
        if not math.isfinite(now):
            raise ValueError("delivery release time must be finite")
        cursor = await connection.execute(
            """UPDATE execution_deliveries SET status=?,
            delivery_version=delivery_version+1,next_attempt_at=?,last_error=?,
            updated_at=?,delivered_at=NULL WHERE delivery_id=?
            AND delivery_version=? AND status='delivering'
            """,
            (
                status.value,
                None if discard else float(retry_at),
                message,
                now,
                delivery_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            existing = await (
                await connection.execute(
                    "SELECT delivery_id FROM execution_deliveries WHERE delivery_id=?",
                    (delivery_id,),
                )
            ).fetchone()
            if existing is None:
                raise DeliveryNotFound(
                    "delivery_not_found",
                    f"execution delivery does not exist: {delivery_id}",
                )
            raise DeliveryClaimConflict(
                "delivery_claim_conflict", "delivery release lost its claim fence"
            )
        row = await (
            await connection.execute(
                "SELECT * FROM execution_deliveries WHERE delivery_id=?",
                (delivery_id,),
            )
        ).fetchone()
        assert row is not None
        return self._row_to_delivery(row)

    @staticmethod
    def _row_to_continuation(row: Mapping[str, Any]) -> ContinuationRecord:
        payload = json.loads(str(row["pending_prepared_call_json"]))
        if not isinstance(payload, dict):
            raise RuntimeActivationError(
                "continuation_payload_corrupt",
                "persisted continuation payload must be a JSON object",
            )
        return ContinuationRecord(
            run_id=str(row["run_id"]),
            payload=payload,
            version=int(row["continuation_version"]),
            pending_decision_id=(
                str(row["pending_decision_id"])
                if row["pending_decision_id"] is not None
                else None
            ),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    async def _load_continuation_tx(
        self, db: aiosqlite.Connection, run_id: str,
    ) -> ContinuationRecord | None:
        row = await (await db.execute("SELECT * FROM execution_continuations WHERE run_id=?", (run_id,))).fetchone()
        return None if row is None else self._row_to_continuation(row)

    async def _continuation_run_tx(
        self, db: aiosqlite.Connection, run_id: str
    ) -> aiosqlite.Row:
        row = await self._recovery_run_tx(db, run_id)
        if str(row["persistence_level"]) != PersistenceLevel.DURABLE.value:
            raise PersistenceRequired(
                "continuation_requires_durable_run",
                "continuations require a durable execution run",
            )
        return row

    async def _recovery_run_tx(
        self, db: aiosqlite.Connection, run_id: str
    ) -> aiosqlite.Row:
        """Load an existing run only when its durable owner is currently active."""

        row = await (
            await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (run_id,))
        ).fetchone()
        if row is None:
            raise RunNotFound("run_not_found", f"execution run does not exist: {run_id}")
        self._assert_run_owner(row, await self._required_recovery_owner_tx(db))
        return row

    async def _insert_continuation_decision_tx(
        self,
        db: aiosqlite.Connection,
        decision: DecisionOpen,
        *,
        run: Mapping[str, Any],
        now: float,
    ) -> None:
        if decision.run_id != str(run["run_id"]):
            raise DecisionConflict(
                "decision_run_mismatch",
                "continuation and pending decision must belong to the same run",
            )
        existing_row = await (
            await db.execute(
                """SELECT * FROM execution_decisions
                WHERE decision_id=? OR (run_id=? AND nonce=?)
                ORDER BY CASE WHEN decision_id=? THEN 0 ELSE 1 END LIMIT 1""",
                (
                    decision.decision_id,
                    decision.run_id,
                    decision.nonce,
                    decision.decision_id,
                ),
            )
        ).fetchone()
        if existing_row is not None:
            self._assert_decision_intent(self._row_to_decision(existing_row), decision)
            return
        if str(run["status"]) in {
            status.value for status in TERMINAL_RUN_STATUSES
        } or str(run["status"]) == RunStatus.CANCEL_REQUESTED.value:
            raise DecisionConflict(
                "run_not_signalable",
                "cancelled or terminal runs cannot open decisions",
            )
        if decision.expires_at is not None and decision.expires_at <= now:
            raise DecisionConflict(
                "decision_expired", "cannot open an already expired decision"
            )
        if (
            decision.capability_hash is not None
            and decision.capability_hash != str(run["capability_hash"])
        ):
            raise DecisionConflict(
                "decision_binding_mismatch",
                "decision capability does not match the durable run context",
            )
        await db.execute(
            """INSERT INTO execution_decisions(
            decision_id,schema_version,run_id,nonce,kind,status,
            prompt_schema_version,prompt_json,response_schema_version,response_json,
            domain_kind,domain_id,call_id,effect_id,tool_name,args_hash,
            capability_hash,scope_hash,decision_version,expires_at,created_at,resolved_at
            ) VALUES(?,?,?,?,?,'open',?,?,NULL,NULL,?,?,?,?,?,?,?,?,0,?,?,NULL)""",
            (
                decision.decision_id,
                decision.schema_version,
                decision.run_id,
                decision.nonce,
                decision.kind.value,
                decision.prompt_schema_version,
                canonical_json(thaw_json(decision.prompt)),
                decision.domain_kind,
                decision.domain_id,
                decision.call_id,
                decision.effect_id,
                decision.tool_name,
                decision.args_hash,
                decision.capability_hash,
                decision.scope_hash,
                decision.expires_at,
                now,
            ),
        )

    @staticmethod
    def _continuation_payload_json(payload: Mapping[str, Any]) -> str:
        normalized = thaw_json(payload)
        if not isinstance(normalized, dict):
            raise ValueError("continuation payload must be a JSON object")
        return canonical_json(normalized)

    @staticmethod
    def _validate_continuation_version(expected_version: int) -> None:
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 0
        ):
            raise ValueError("expected_version must be non-negative")

    async def _save_continuation_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run: Mapping[str, Any],
        expected_version: int,
        payload_json: str,
        decision: DecisionOpen | None,
        now: float,
    ) -> tuple[ContinuationRecord, bool]:
        run_id = str(run["run_id"])
        existing = await (
            await db.execute(
                "SELECT * FROM execution_continuations WHERE run_id=?", (run_id,)
            )
        ).fetchone()
        decision_id = decision.decision_id if decision is not None else None
        if existing is None:
            if expected_version != 0:
                raise VersionConflict(
                    "stale_continuation_version",
                    "new continuation must start from expected version zero",
                )
        elif int(existing["continuation_version"]) == expected_version + 1:
            replay = self._row_to_continuation(existing)
            if (
                canonical_json(thaw_json(replay.payload)) == payload_json
                and replay.pending_decision_id == decision_id
            ):
                if decision is not None:
                    await self._insert_continuation_decision_tx(
                        db, decision, run=run, now=now
                    )
                return replay, True
            raise VersionConflict(
                "continuation_replay_conflict",
                "continuation retry supplied different payload or decision intent",
            )
        elif int(existing["continuation_version"]) != expected_version:
            raise VersionConflict(
                "stale_continuation_version",
                "continuation changed before the save CAS",
            )
        if decision is not None:
            await self._insert_continuation_decision_tx(db, decision, run=run, now=now)
        self._fault("continuation_after_decision")
        next_version = expected_version + 1
        if existing is None:
            await db.execute(
                """INSERT INTO execution_continuations(
                run_id,schema_version,command_schema_version,canonical_messages_json,
                session_projection_cursor,prepared_context_ref,tool_set_snapshot_ref,
                pending_prepared_call_json,pending_decision_id,iteration,
                provider_state_json,continuation_version,created_at,updated_at
                ) VALUES(?,1,1,'[]',0,NULL,NULL,?,?,0,'{}',?,?,?)""",
                (run_id, payload_json, decision_id, next_version, now, now),
            )
        else:
            cursor = await db.execute(
                """UPDATE execution_continuations
                SET pending_prepared_call_json=?,pending_decision_id=?,
                    continuation_version=?,updated_at=?
                WHERE run_id=? AND continuation_version=?""",
                (
                    payload_json,
                    decision_id,
                    next_version,
                    now,
                    run_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_continuation_version",
                    "continuation changed before the save CAS",
                )
        row = await (
            await db.execute(
                "SELECT * FROM execution_continuations WHERE run_id=?", (run_id,)
            )
        ).fetchone()
        assert row is not None
        return self._row_to_continuation(row), False

    @staticmethod
    def _admission(record: ContinuationRecord) -> AdmissionBoundary:
        value = record.payload.get("_admission")
        if not isinstance(value, Mapping):
            raise DecisionNotFound("admission_not_found", "continuation has no admission boundary")
        boundary = AdmissionBoundary.from_dict(value)
        if boundary.run_id != record.run_id or boundary.boundary_version != record.version:
            raise RuntimeActivationError("admission_boundary_corrupt", "admission and continuation fences differ")
        return boundary

    async def _admission_tx(self, db: aiosqlite.Connection, run_id: str):
        record = await self._load_continuation_tx(db, run_id)
        if record is None:
            raise DecisionNotFound("admission_not_found", "run has no admission continuation")
        return record, self._admission(record)

    @staticmethod
    def _with_admission(payload: Mapping[str, Any], boundary: AdmissionBoundary) -> dict[str, Any]:
        return {**payload, "_admission": boundary.to_dict()}

    @staticmethod
    def _advance_admission(boundary: AdmissionBoundary, phase: AdmissionPhase,
                           version: int, *, consumed: bool | None = None,
                           resolution_fingerprint: str | None = None) -> AdmissionBoundary:
        terminal = phase in TERMINAL_ADMISSION_PHASES if consumed is None else consumed
        value = boundary.to_dict()
        value.update(phase=phase.value, boundary_version=version, consumed=terminal)
        if resolution_fingerprint is not None:
            value["resolution_fingerprint"] = resolution_fingerprint
        return AdmissionBoundary.from_dict(value)

    @staticmethod
    def _resolved_admission_event(boundary: AdmissionBoundary,
                                  phase: AdmissionPhase | None = None) -> RunEventCandidate:
        phase = phase or boundary.phase
        terminal = phase in {AdmissionPhase.REJECTED, AdmissionPhase.CANCELLED, AdmissionPhase.EXPIRED}
        return RunEventCandidate(
            event_key="run:final" if terminal else "admission:accepted",
            kind="run.final" if terminal else "admission.accepted",
            status=OutcomeStatus.CANCELLED if terminal else OutcomeStatus.ACCEPTED,
            driver_kind=boundary.driver_kind,
            correlation={"decision_id": boundary.decision_id, "launch_operation_id": boundary.launch_operation_id},
            payload={"admission_phase": phase.value, "consumed": terminal},
        )

    async def start_admission(
        self, spec: RunCreate, admission: AdmissionSpec, start_snapshot: AdmissionBoundary,
        waiting_event: RunEventCandidate, *,
        run_start_snapshot: RunStartSnapshotRecord | None = None,
        start_commit_extensions: Sequence[Any] = (),
        deliveries: Sequence[DeliverySpec] = (),
    ) -> AdmissionBoundary:
        if spec.persistence_level is not PersistenceLevel.DURABLE:
            raise PersistenceRequired("admission_must_be_durable", "admission runs are durable")
        expected = (spec.run_id, spec.driver_kind, spec.profile_key, admission, AdmissionPhase.PENDING, 1, False)
        actual = (start_snapshot.run_id, start_snapshot.driver_kind, start_snapshot.profile_key, start_snapshot.admission, start_snapshot.phase, start_snapshot.boundary_version, start_snapshot.consumed)
        if actual != expected or waiting_event.status is not OutcomeStatus.WAITING:
            raise DecisionConflict("invalid_admission_boundary", "admission start facts do not align")
        decision = DecisionOpen(start_snapshot.decision_id, spec.run_id, start_snapshot.nonce,
            admission.kind, admission.prompt_schema_version,
            thaw_json(admission.prompt), admission.expires_at)
        association = (RunEventCandidate.from_dict(start_snapshot.association_event)
            if start_snapshot.association_event is not None else None)
        async with self._write_transaction() as db:
            run, created = await self._insert_run_tx(db, spec, version=0)
            if run_start_snapshot is not None:
                if run_start_snapshot.run_id != spec.run_id:
                    raise IdempotencyConflict(
                        "run_start_scope_conflict",
                        "admission Run start snapshot belongs to another Run",
                    )
                await self._ensure_start_snapshot_tx(
                    db,
                    spec=spec,
                    start_snapshot=run_start_snapshot,
                    run_created=created,
                    start_commit_extensions=start_commit_extensions,
                )
            elif start_commit_extensions:
                raise IdempotencyConflict(
                    "start_extension_without_snapshot",
                    "admission start extensions require a Run start snapshot",
                )
            prior = await self._load_continuation_tx(db, spec.run_id)
            if prior is not None:
                current = self._admission(prior)
                if self._advance_admission(current, AdmissionPhase.PENDING, 1, consumed=False) != start_snapshot:
                    raise IdempotencyConflict("admission_intent_conflict", "admission replay changed immutable facts")
                if association is not None:
                    await self._append_event_tx(db, run, expected_version=int(run["version"]),
                        event=association, deliveries=())
                await self._append_event_tx(db, run, expected_version=int(run["version"]),
                    event=waiting_event, deliveries=deliveries)
                return current
            if not created:
                raise IdempotencyConflict("admission_run_conflict", "existing run has no admission")
            self._fault("batch_boundary_after_promotion")
            await self._save_continuation_tx(db, run=run, expected_version=0,
                payload_json=self._continuation_payload_json(self._with_admission({}, start_snapshot)),
                decision=decision, now=float(self._clock()))
            self._fault("batch_boundary_after_continuation")
            await db.execute("UPDATE execution_runs SET status='waiting',updated_at=? WHERE run_id=?",
                (self._clock(), spec.run_id))
            run = await (await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (spec.run_id,))).fetchone()
            assert run is not None
            if association is not None:
                _, run, _ = await self._append_event_tx(
                    db, run, expected_version=int(run["version"]),
                    event=association, deliveries=())
            await self._append_event_tx(db, run, expected_version=int(run["version"]), event=waiting_event, deliveries=deliveries)
            self._fault("batch_boundary_after_waiting_event")
            self._fault("batch_boundary_before_commit")
            return start_snapshot

    async def resolve_admission(
        self, ref: RunRef, actor: ActorContext, signal: DecisionSignal, *,
        expected_boundary_version: int, terminal_deliveries: Sequence[DeliverySpec] = (),
        admission_task_grant: TaskGrant | None = None,
    ) -> AdmissionResolution:
        async with self._write_transaction() as db:
            run = await self._continuation_run_tx(db, ref.run_id)
            run_record = self._authorize_run_row(
                run,
                expected_session_id=ref.expected_session_id,
                actor=actor,
            )
            record, boundary = await self._admission_tx(db, ref.run_id)
            resolution_fingerprint = fingerprint_json(signal.to_dict())
            decision_row = await (await db.execute("SELECT * FROM execution_decisions WHERE decision_id=?", (boundary.decision_id,))).fetchone()
            assert decision_row is not None
            decision = self._row_to_decision(decision_row)
            cancel_accepted = (
                boundary.phase is AdmissionPhase.ACCEPTED_START_PENDING
                and not signal.allow and signal.response.get("resolution") == "cancelled"
            )
            if cancel_accepted:
                self._assert_signal_binding(decision, signal)
            if boundary.phase is not AdmissionPhase.PENDING and not cancel_accepted:
                same = signal.run_id == ref.run_id and boundary.resolution_fingerprint == resolution_fingerprint
                if not same:
                    raise DecisionConflict("admission_already_resolved", "another resolution already won")
                event, _, _ = await self._append_event_tx(
                    db, run, expected_version=int(run["version"]),
                    event=self._resolved_admission_event(boundary,
                        AdmissionPhase.ACCEPTED_START_PENDING if signal.allow and boundary.phase is not AdmissionPhase.EXPIRED else None),
                    deliveries=terminal_deliveries if boundary.phase in {AdmissionPhase.REJECTED, AdmissionPhase.CANCELLED, AdmissionPhase.EXPIRED} else ())
                return AdmissionResolution(boundary, decision, event, True)
            if record.version != expected_boundary_version:
                raise VersionConflict("stale_admission_version", "admission changed before resolve")
            expired = False
            if not cancel_accepted:
                decision, _, expired = await self._resolve_decision_tx(
                    db, signal, actor, now=float(self._clock()))
            now = float(self._clock())
            grant_fields = (
                "task_grant_id",
                "task_grant_version",
                "authorization_source",
                "policy_generation",
            )
            response_has_grant = any(
                name in signal.response for name in grant_fields
            )
            if admission_task_grant is None and response_has_grant:
                raise DecisionConflict(
                    "unexpected_admission_task_grant",
                    "admission response cannot assert TaskGrant provenance",
                )
            if admission_task_grant is not None:
                if not signal.allow or expired:
                    raise DecisionConflict(
                        "unexpected_admission_task_grant",
                        "TaskGrant is only valid for an accepted admission",
                    )
                grant = admission_task_grant
                expected_response = {
                    "task_grant_id": grant.task_grant_id,
                    "task_grant_version": grant.version,
                    "authorization_source": grant.source,
                    "policy_generation": grant.policy_generation,
                }
                if any(
                    signal.response.get(name) != value
                    for name, value in expected_response.items()
                ):
                    raise DecisionConflict(
                        "admission_task_grant_binding_mismatch",
                        "TaskGrant differs from the durable admission response",
                    )
                if (
                    grant.root_run_id != run_record.context.root_run_id
                    or actor.root_run_id
                    not in {None, run_record.context.root_run_id}
                    or grant.principal_id != actor.principal_id
                    or grant.source != "user"
                ):
                    raise DecisionConflict(
                        "admission_task_grant_principal_mismatch",
                        "TaskGrant principal, root, or source is invalid",
                    )
                policy_row = await (
                    await db.execute(
                        """SELECT mode,generation,updated_at
                        FROM authorization_policy_state
                        WHERE singleton_id=1"""
                    )
                ).fetchone()
                if (
                    policy_row is None
                    or str(policy_row["mode"]) != "manual"
                    or int(policy_row["generation"])
                    != grant.policy_generation
                    or (
                        grant.expires_at is not None
                        and now >= grant.expires_at
                    )
                ):
                    raise DecisionConflict(
                        "admission_authorization_policy_changed",
                        "authorization policy changed before admission commit",
                    )
                grant_json = canonical_json(grant.to_dict())
                grant_row = await (
                    await db.execute(
                        "SELECT * FROM task_grants WHERE task_grant_id=?",
                        (grant.task_grant_id,),
                    )
                ).fetchone()
                if grant_row is None:
                    await db.execute(
                        """INSERT INTO task_grants(
                        task_grant_id,root_run_id,source,policy_generation,
                        version,grant_fingerprint,grant_json,status,created_at,
                        revoked_at
                        ) VALUES(?,?,?,?,?,?,?,'active',?,NULL)""",
                        (
                            grant.task_grant_id,
                            grant.root_run_id,
                            grant.source,
                            grant.policy_generation,
                            grant.version,
                            grant.fingerprint,
                            grant_json,
                            now,
                        ),
                    )
                elif (
                    str(grant_row["status"]) != "active"
                    or str(grant_row["grant_fingerprint"])
                    != grant.fingerprint
                    or str(grant_row["grant_json"]) != grant_json
                ):
                    raise DecisionConflict(
                        "admission_task_grant_conflict",
                        "TaskGrant identity belongs to another grant",
                    )
                self._fault("decision_resolve_after_grant")
                boundary_payload = boundary.to_dict()
                boundary_payload["request_payload"] = {
                    **dict(boundary.request_payload),
                    **expected_response,
                }
                boundary = AdmissionBoundary.from_dict(boundary_payload)
            phase = AdmissionPhase.EXPIRED if expired else AdmissionPhase.ACCEPTED_START_PENDING if signal.allow else AdmissionPhase.CANCELLED if signal.response.get("resolution") == "cancelled" else AdmissionPhase.REJECTED
            updated = self._advance_admission(boundary, phase, record.version + 1,
                resolution_fingerprint=resolution_fingerprint)
            continuation, _ = await self._save_continuation_tx(db, run=run, expected_version=record.version,
                payload_json=self._continuation_payload_json(self._with_admission(record.payload, updated)),
                decision=None, now=float(self._clock()))
            self._fault("decision_resolve_after_boundary")
            event, changed, _ = await self._append_event_tx(db, run, expected_version=int(run["version"]),
                event=self._resolved_admission_event(updated),
                deliveries=terminal_deliveries if updated.phase in {AdmissionPhase.REJECTED, AdmissionPhase.CANCELLED, AdmissionPhase.EXPIRED} else ())
            if updated.consumed:
                cursor = await db.execute("""UPDATE execution_runs SET status='cancelled',terminal_event_id=?,
                    version=version+1,ended_at=?,updated_at=? WHERE run_id=? AND version=? AND terminal_event_id IS NULL""",
                    (event.event_id, now, now, ref.run_id, int(changed["version"])))
                if cursor.rowcount != 1:
                    raise TerminalConflict("terminal_conflict", "admission terminalization lost its CAS")
            else:
                await db.execute("UPDATE execution_runs SET status='queued',updated_at=? WHERE run_id=?", (now, ref.run_id))
            self._fault("decision_resolve_before_commit")
            return AdmissionResolution(self._admission(continuation), decision, event)

    async def claim_admission_launch(
        self, recovery_lease: RecoveryLease, *, expected_boundary_version: int,
    ) -> AdmissionLaunchClaim:
        async with self._write_transaction() as db:
            await self._assert_recovery_fence_tx(db, recovery_lease, run_id=recovery_lease.run_id)
            run = await self._continuation_run_tx(db, recovery_lease.run_id)
            if RunStatus(str(run["status"])) is not RunStatus.QUEUED:
                raise TerminalConflict("admission_launch_cancelled", "run is no longer queued for launch")
            record, boundary = await self._admission_tx(db, recovery_lease.run_id)
            if boundary.phase is AdmissionPhase.LAUNCH_CLAIMED and record.version == expected_boundary_version + 1:
                return AdmissionLaunchClaim(boundary, recovery_lease, True)
            if boundary.phase is not AdmissionPhase.ACCEPTED_START_PENDING or record.version != expected_boundary_version:
                raise VersionConflict("stale_admission_version", "admission is not launch-claimable")
            updated = self._advance_admission(boundary, AdmissionPhase.LAUNCH_CLAIMED, record.version + 1)
            saved, _ = await self._save_continuation_tx(db, run=run, expected_version=record.version,
                payload_json=self._continuation_payload_json(self._with_admission(record.payload, updated)),
                decision=None, now=float(self._clock()))
            self._fault("effect_claim_after_grant")
            self._fault("effect_claim_after_effect")
            self._fault("effect_claim_before_commit")
            return AdmissionLaunchClaim(self._admission(saved), recovery_lease)

    async def persist_react_boundary(
        self,
        run: str | RunCreate,
        expected_continuation_version: int,
        payload: Mapping[str, Any],
        decision: DecisionOpen | None = None,
        *,
        expected_run_version: int | None = None,
        waiting_event: RunEventCandidate | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        recovery_lease: RecoveryLease | None = None,
        admission_launch: AdmissionLaunchClaim | None = None,
        skill_scope_activation_values: Mapping[str, Any] | None = None,
    ) -> ContinuationRecord | tuple[CreateRunResult, ContinuationRecord]:
        """Persist either an existing or newly promoted ReAct boundary."""

        self._validate_continuation_version(expected_continuation_version)
        payload_json = self._continuation_payload_json(payload)
        async with self._write_transaction() as db:
            if isinstance(run, RunCreate):
                if admission_launch is not None:
                    raise DecisionConflict("admission_run_conflict", "admission launch requires its precreated run")
                if run.persistence_level is not PersistenceLevel.DURABLE:
                    raise PersistenceRequired(
                        "promotion_target_not_durable",
                        "batch-boundary promotion target must be durable",
                    )
                if expected_run_version is None or expected_run_version < 0:
                    raise ValueError("expected_run_version must be non-negative")
                if waiting_event is None and deliveries:
                    raise IdempotencyConflict(
                        "delivery_without_event",
                        "batch-boundary deliveries require a waiting event",
                    )
                if waiting_event is not None and waiting_event.status is not OutcomeStatus.WAITING:
                    raise IdempotencyConflict(
                        "invalid_waiting_event",
                        "batch-boundary event must carry waiting status",
                    )
                row, created = await self._insert_run_tx(
                    db, run, version=expected_run_version + 1
                )
                if str(row["persistence_level"]) != PersistenceLevel.DURABLE.value:
                    raise PersistenceRequired(
                        "promotion_not_durable", "persisted promotion is not durable"
                    )
                self._fault("batch_boundary_after_promotion")
                now = float(self._clock())
                record, replayed = await self._save_continuation_tx(
                    db, run=row, expected_version=expected_continuation_version,
                    payload_json=payload_json, decision=decision, now=now,
                )
                if not created and not replayed and int(row["version"]) != expected_run_version:
                    raise VersionConflict(
                        "stale_run_version",
                        f"expected run version {expected_run_version}, found {row['version']}",
                    )
                self._fault("batch_boundary_after_continuation")
                if decision is not None or waiting_event is not None:
                    cursor = await db.execute(
                        """UPDATE execution_runs SET status='waiting',updated_at=?
                        WHERE run_id=? AND terminal_event_id IS NULL
                        AND status IN ('created','queued','running','waiting')""",
                        (now, run.run_id),
                    )
                    if cursor.rowcount != 1:
                        raise DecisionConflict(
                            "run_not_signalable",
                            "cancelled or terminal runs cannot persist a waiting boundary",
                        )
                if waiting_event is not None:
                    _, row, _ = await self._append_event_tx(
                        db, row, expected_version=int(row["version"]),
                        event=waiting_event, deliveries=deliveries,
                    )
                else:
                    row = await (
                        await db.execute(
                            "SELECT * FROM execution_runs WHERE run_id=?", (run.run_id,)
                        )
                    ).fetchone()
                    assert row is not None
                self._fault("batch_boundary_after_waiting_event")
                result = CreateRunResult(self._row_to_record(row), created)
                self._fault("batch_boundary_before_commit")
                await db.commit()
                return result, record
            run_id = run
            if skill_scope_activation_values is not None:
                expected_activation_version = expected_continuation_version + 1
                if (
                    skill_scope_activation_values.get("continuation_version")
                    != expected_activation_version
                    or str(skill_scope_activation_values.get("run_id") or "")
                    != run_id
                ):
                    raise IdempotencyConflict(
                        "skill_scope_activation_conflict",
                        "scope receipt does not name this continuation",
                    )
            if recovery_lease is not None:
                await self._assert_recovery_fence_tx(db, recovery_lease, run_id=run_id)
            run_row = await self._continuation_run_tx(db, run_id)
            existing = await self._load_continuation_tx(db, run_id)
            if existing is None and admission_launch is not None:
                raise DecisionNotFound("admission_not_found", "launch claim has no continuation")
            if existing is not None:
                try:
                    boundary = self._admission(existing)
                except DecisionNotFound:
                    boundary = None
                if (boundary is not None and not boundary.consumed
                        and admission_launch is None):
                    raise DecisionConflict("admission_launch_required", "unconsumed admission requires its typed launch claim")
                if admission_launch is not None:
                    if boundary != admission_launch.boundary:
                        raise VersionConflict("stale_admission_version", "launch claim differs from continuation")
                    await self._assert_recovery_fence_tx(db, admission_launch.recovery_lease, run_id=run_id)
                if boundary is not None:
                    phase = AdmissionPhase.LAUNCHED if admission_launch is not None else boundary.phase
                    boundary = self._advance_admission(boundary, phase,
                        expected_continuation_version + 1)
                    payload_json = self._continuation_payload_json(self._with_admission(payload, boundary))
            now = float(self._clock())
            if skill_scope_activation_values is not None:
                await self.bind(db).insert_or_verify_skill_scope_activation(
                    skill_scope_activation_values,
                    created_at=now,
                )
            record, _ = await self._save_continuation_tx(
                db,
                run=run_row,
                expected_version=expected_continuation_version,
                payload_json=payload_json,
                decision=decision,
                now=now,
            )
            if skill_scope_activation_values is not None:
                self._fault("skill_scope_activation_before_commit")
            self._fault("continuation_before_commit")
            await db.commit()
            return record

    async def load_continuation(self, run_id: str) -> ContinuationRecord | None:
        async with self._read_connection() as db:
            return await self._load_continuation_tx(db, run_id)

    @staticmethod
    def _assert_decision_intent(
        existing: DecisionRecord, requested: DecisionOpen
    ) -> None:
        if existing.request != requested:
            raise DecisionConflict(
                "decision_identity_conflict",
                "decision id or run nonce is already bound to another intent",
            )

    def _authorize_run_row(
        self,
        row: Mapping[str, Any],
        *,
        expected_session_id: str,
        actor: ActorContext,
    ) -> RunRecord:
        record = self._row_to_record(row)
        self._authorize_view(
            RunRef(record.run_id, expected_session_id), actor, record
        )
        return record

    async def get_decision(
        self,
        decision_id: str,
        *,
        ref: RunRef,
        actor: ActorContext,
    ) -> DecisionRecord:
        async with self._read_connection() as db:
            decision_row = await (
                await db.execute(
                    "SELECT * FROM execution_decisions WHERE decision_id=?",
                    (decision_id,),
                )
            ).fetchone()
            if decision_row is None:
                raise DecisionNotFound(
                    "decision_not_found", f"execution decision does not exist: {decision_id}"
                )
            decision = self._row_to_decision(decision_row)
            if decision.run_id != ref.run_id:
                raise DecisionConflict(
                    "decision_binding_mismatch", "decision is not bound to the supplied run"
                )
            run_row = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (decision.run_id,)
                )
            ).fetchone()
            assert run_row is not None
            self._authorize_run_row(
                run_row,
                expected_session_id=ref.expected_session_id,
                actor=actor,
            )
            return decision

    async def get_decision_projection(
        self,
        decision_id: str,
        *,
        expected_run_id: str,
        expected_session_id: str,
    ) -> DecisionRecord:
        """Return current decision state for a session-owned history row.

        History hydration starts from a SessionDB row that already carries a
        canonical ``workflow_event_id``.  Re-check both the event's run and
        session bindings here so the product projection cannot cross either
        durable boundary while refreshing an open/settled decision card.
        """

        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT execution_decisions.*,execution_runs.session_id
                    FROM execution_decisions
                    JOIN execution_runs USING(run_id)
                    WHERE execution_decisions.decision_id=?""",
                    (str(decision_id).strip(),),
                )
            ).fetchone()
            if row is None:
                raise DecisionNotFound(
                    "decision_not_found",
                    f"execution decision does not exist: {decision_id}",
                )
            if (
                str(row["run_id"]) != str(expected_run_id).strip()
                or str(row["session_id"]) != str(expected_session_id).strip()
            ):
                raise AuthorizationError(
                    "decision_projection_scope_mismatch",
                    "decision projection differs from the durable run/session binding",
                )
            return self._row_to_decision(row)

    async def list_open_decision_projections(
        self,
        session_id: str,
    ) -> tuple[dict[str, Any], ...]:
        """Return session-fenced waiting decisions for UI rehydration.

        Blocking cards are live projections of durable decisions.  A control
        WebSocket reconnect must be able to rebuild those cards without
        reopening or mutating the decision itself.
        """

        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        async with self._read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT execution_decisions.*,execution_runs.session_id,
                    execution_runs.root_run_id,execution_runs.request_id,
                    execution_runs.turn_id,execution_runs.driver_kind,
                    execution_task_work_contexts.workspace_root,
                    execution_task_work_contexts.workspace_source
                    FROM execution_decisions
                    JOIN execution_runs USING(run_id)
                    LEFT JOIN execution_task_work_contexts
                      ON execution_task_work_contexts.root_run_id=
                         execution_runs.root_run_id
                    WHERE execution_runs.session_id=?
                      AND execution_runs.status='waiting'
                      AND execution_decisions.status='open'
                    ORDER BY execution_decisions.created_at,
                             execution_decisions.decision_id""",
                    (normalized_session_id,),
                )
            ).fetchall()
            return tuple(
                {
                    "decision": self._row_to_decision(row),
                    "session_id": str(row["session_id"]),
                    "root_run_id": str(row["root_run_id"]),
                    "request_id": str(row["request_id"]),
                    "turn_id": str(row["turn_id"]),
                    "driver_kind": str(row["driver_kind"]),
                    "workspace_root": (
                        None
                        if row["workspace_root"] is None
                        else str(row["workspace_root"])
                    ),
                    "workspace_source": (
                        None
                        if row["workspace_source"] is None
                        else str(row["workspace_source"])
                    ),
                }
                for row in rows
            )

    @staticmethod
    def _assert_signal_binding(
        decision: DecisionRecord, signal: DecisionSignal
    ) -> None:
        request = decision.request
        scalar_pairs = (
            (signal.decision_id, request.decision_id),
            (signal.run_id, request.run_id),
            (signal.nonce, request.nonce),
            (signal.domain_kind, request.domain_kind),
            (signal.domain_id, request.domain_id),
            (signal.call_id, request.call_id),
            (signal.effect_id, request.effect_id),
            (signal.tool_name, request.tool_name),
            (signal.args_hash, request.args_hash),
            (signal.capability_hash, request.capability_hash),
            (signal.scope_hash, request.scope_hash),
        )
        if any(actual != expected for actual, expected in scalar_pairs):
            raise DecisionConflict(
                "decision_binding_mismatch",
                "signal nonce/run/domain/effect/capability/scope fence differs",
            )
        if signal.expected_version != decision.decision_version:
            raise DecisionConflict(
                "stale_decision_version",
                f"expected decision version {signal.expected_version}, "
                f"found {decision.decision_version}",
            )

    async def _resolve_decision_tx(
        self,
        db: aiosqlite.Connection,
        signal: DecisionSignal,
        actor: ActorContext,
        *,
        now: float,
        authorization_commit: PreparedAuthorizationCommit | None = None,
    ) -> tuple[
        DecisionRecord,
        DecisionAuthorization | None,
        DecisionConflict | None,
    ]:
        row = await (
            await db.execute(
                "SELECT * FROM execution_decisions WHERE decision_id=?",
                (signal.decision_id,),
            )
        ).fetchone()
        if row is None:
            raise DecisionNotFound(
                "decision_not_found",
                f"execution decision does not exist: {signal.decision_id}",
            )
        decision = self._row_to_decision(row)
        run_row = await (
            await db.execute(
                "SELECT * FROM execution_runs WHERE run_id=?", (decision.run_id,)
            )
        ).fetchone()
        assert run_row is not None
        run = self._authorize_run_row(
            run_row,
            expected_session_id=signal.expected_session_id,
            actor=actor,
        )
        self._assert_signal_binding(decision, signal)
        if run.status is RunStatus.CANCEL_REQUESTED or run.status in TERMINAL_RUN_STATUSES:
            raise DecisionConflict(
                "run_not_signalable", "cancelled or terminal run rejects decision signals"
            )
        if decision.status is not DecisionStatus.OPEN:
            raise DecisionConflict(
                "decision_already_resolved",
                "duplicate or late decision signal was rejected",
            )
        if decision.request.expires_at is not None and decision.request.expires_at <= now:
            cursor = await db.execute(
                """UPDATE execution_decisions SET status='expired',
                decision_version=decision_version+1,resolved_at=?
                WHERE decision_id=? AND decision_version=? AND status='open'""",
                (now, decision.decision_id, signal.expected_version),
            )
            if cursor.rowcount != 1:
                raise DecisionConflict(
                    "stale_decision_version", "decision changed before expiry CAS"
                )
            expired = await (
                await db.execute(
                    "SELECT * FROM execution_decisions WHERE decision_id=?",
                    (decision.decision_id,),
                )
            ).fetchone()
            assert expired is not None
            return (
                self._row_to_decision(expired),
                None,
                DecisionConflict("decision_expired", "expired decision signal was rejected"),
            )
        prepared_authorization = (
            decision.request.kind is DecisionKind.PERMISSION
            and decision.request.prompt.get("authorization_intent_fingerprint")
            is not None
        )

        if prepared_authorization and signal.allow and authorization_commit is None:
            raise DecisionConflict(
                "authorization_commit_required",
                "prepared-tool permission allow requires a fenced authorization commit",
            )
        if authorization_commit is not None and (
            decision.request.kind is not DecisionKind.PERMISSION or not signal.allow
        ):
            raise DecisionConflict(
                "unexpected_authorization_commit",
                "authorization commit is only valid for an allowed permission decision",
            )

        authorization_provenance: dict[str, object] | None = None
        if authorization_commit is not None:
            exact = authorization_commit.exact_request
            task_grant = authorization_commit.task_grant
            expected_intent = decision.request.prompt.get(
                "authorization_intent_fingerprint"
            )
            if (
                not isinstance(expected_intent, str)
                or expected_intent != exact.fingerprint
            ):
                raise DecisionConflict(
                    "authorization_intent_mismatch",
                    "authorization commit differs from the immutable decision intent",
                )
            root_run_id = run.context.root_run_id
            exact_bindings = (
                exact.root_run_id,
                exact.run_id,
                exact.call_id,
                exact.effect_id,
                exact.tool_name,
                exact.args_hash,
                exact.capability_hash,
                exact.scope_hash,
            )
            decision_bindings = (
                root_run_id,
                decision.run_id,
                decision.request.call_id,
                decision.request.effect_id,
                decision.request.tool_name,
                decision.request.args_hash,
                decision.request.capability_hash,
                decision.request.scope_hash,
            )
            if exact_bindings != decision_bindings:
                raise DecisionConflict(
                    "authorization_binding_mismatch",
                    "authorization commit differs from the durable decision fences",
                )
            if (
                exact.expires_at > float(decision.request.expires_at or 0.0)
                or exact.expires_at <= now
            ):
                raise DecisionConflict(
                    "authorization_expiry_mismatch",
                    "exact authorization expiry is outside the durable decision",
                )
            if (
                task_grant.root_run_id != root_run_id
                or actor.root_run_id not in {None, root_run_id}
                or task_grant.principal_id != actor.principal_id
            ):
                raise DecisionConflict(
                    "authorization_principal_mismatch",
                    "TaskGrant principal or root differs from the authenticated run",
                )
            policy_row = await (
                await db.execute(
                    """SELECT mode,generation,updated_at
                    FROM authorization_policy_state WHERE singleton_id=1"""
                )
            ).fetchone()
            if policy_row is None:
                raise DecisionConflict(
                    "authorization_policy_missing",
                    "authorization policy singleton is absent",
                )
            policy_state = AuthorizationPolicyState(
                mode=str(policy_row["mode"]),  # type: ignore[arg-type]
                generation=int(policy_row["generation"]),
                updated_at=float(policy_row["updated_at"]),
            )
            if (
                policy_state.mode != authorization_commit.expected_policy_mode
                or policy_state.generation
                != authorization_commit.expected_policy_generation
            ):
                raise DecisionConflict(
                    "authorization_policy_changed",
                    "authorization policy changed after the decision was planned",
                )
            if authorization_commit.authorization_origin == "explicit_decision":
                expected_ref = decision.request.prompt.get(
                    "confirm_only_snapshot_ref"
                )
                expected_hash = decision.request.prompt.get(
                    "confirm_only_snapshot_hash"
                )
                if (
                    authorization_commit.decision_id != decision.decision_id
                    or authorization_commit.decision_nonce
                    != decision.request.nonce
                    or expected_ref
                    != authorization_commit.confirm_only_snapshot_ref
                    or expected_hash
                    != authorization_commit.confirm_only_snapshot_hash
                    or decision.request.prompt.get("authorization_origin")
                    != "explicit_decision"
                ):
                    raise DecisionConflict(
                        "explicit_authorization_fence_mismatch",
                        "explicit authorization differs from the durable confirm-only decision",
                    )
            grant_row = await (
                await db.execute(
                    "SELECT * FROM task_grants WHERE task_grant_id=?",
                    (task_grant.task_grant_id,),
                )
            ).fetchone()
            grant_json = canonical_json(task_grant.to_dict())
            if authorization_commit.proposed_task_grant:
                expected_source = (
                    "user"
                    if authorization_commit.authorization_origin
                    == "explicit_decision"
                    else "policy:auto"
                    if policy_state.mode == "auto"
                    else "user"
                )
                if task_grant.source != expected_source:
                    raise DecisionConflict(
                        "authorization_source_mismatch",
                        "proposed TaskGrant source differs from the active policy mode",
                    )
                if grant_row is None:
                    await db.execute(
                        """INSERT INTO task_grants(
                        task_grant_id,root_run_id,source,policy_generation,version,
                        grant_fingerprint,grant_json,status,created_at,revoked_at
                        ) VALUES(?,?,?,?,?,?,?,'active',?,NULL)""",
                        (
                            task_grant.task_grant_id,
                            task_grant.root_run_id,
                            task_grant.source,
                            task_grant.policy_generation,
                            task_grant.version,
                            task_grant.fingerprint,
                            grant_json,
                            now,
                        ),
                    )
                elif (
                    str(grant_row["status"]) != "active"
                    or str(grant_row["grant_fingerprint"])
                    != task_grant.fingerprint
                    or str(grant_row["grant_json"]) != grant_json
                ):
                    raise DecisionConflict(
                        "task_grant_conflict",
                        "proposed TaskGrant identity belongs to another grant",
                    )
            elif (
                grant_row is None
                or str(grant_row["status"]) != "active"
                or str(grant_row["grant_fingerprint"]) != task_grant.fingerprint
                or str(grant_row["grant_json"]) != grant_json
            ):
                raise DecisionConflict(
                    "task_grant_not_active",
                    "referenced TaskGrant is missing, revoked, or changed",
                )

            policy_decision = AuthorizationPolicy().evaluate(
                state=policy_state,
                context=DecisionContext(
                    1,
                    "permission",
                    str(decision.request.domain_kind or ""),
                    str(decision.request.domain_id or ""),
                    root_run_id,
                ),
                task_grant=task_grant,
                exact_request=exact,
                actual_root_run_id=root_run_id,
                now=now,
            )
            if (
                policy_decision.action != "allow"
                or policy_decision.exact_grant is None
            ):
                raise DecisionConflict(
                    "authorization_policy_rejected",
                    f"authorization policy rejected commit: {policy_decision.reason}",
                )
            authorization_provenance = {
                "task_grant_id": task_grant.task_grant_id,
                "task_grant_version": task_grant.version,
                "authorization_source": task_grant.source,
                "authorization_origin": authorization_commit.authorization_origin,
                "policy_generation": policy_state.generation,
                "exact_grant_id": policy_decision.exact_grant.grant_id,
                "authorization_intent_fingerprint": exact.fingerprint,
            }
            if authorization_commit.authorization_origin == "explicit_decision":
                authorization_provenance.update(
                    {
                        "confirm_only_snapshot_ref": (
                            authorization_commit.confirm_only_snapshot_ref
                        ),
                        "confirm_only_snapshot_hash": (
                            authorization_commit.confirm_only_snapshot_hash
                        ),
                    }
                )

        resolved_status = DecisionStatus.ALLOWED if signal.allow else DecisionStatus.DENIED
        response_payload = thaw_json(signal.response)
        if authorization_provenance is not None:
            response_payload = {
                **dict(response_payload),
                **authorization_provenance,
            }
        cursor = await db.execute(
            """UPDATE execution_decisions SET status=?,response_schema_version=?,
            response_json=?,decision_version=decision_version+1,resolved_at=?
            WHERE decision_id=? AND decision_version=? AND status='open'""",
            (
                resolved_status.value,
                signal.response_schema_version,
                canonical_json(response_payload),
                now,
                decision.decision_id,
                signal.expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise DecisionConflict(
                "stale_decision_version", "decision changed before resolve CAS"
            )
        self._fault("decision_resolve_after_cas")
        authorization: DecisionAuthorization | None = None
        if decision.request.kind is DecisionKind.PERMISSION and signal.allow:
            expires_at = decision.request.expires_at
            assert expires_at is not None
            grant_id = stable_decision_grant_id(decision.decision_id)
            await db.execute(
                """INSERT INTO execution_grants(
                grant_id,schema_version,decision_id,run_id,call_id,effect_id,
                tool_name,args_hash,capability_hash,scope_hash,status,
                grant_version,expires_at,created_at,consumed_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,'issued',0,?,?,NULL)""",
                (
                    grant_id,
                    decision.request.schema_version,
                    decision.decision_id,
                    decision.run_id,
                    decision.request.call_id,
                    decision.request.effect_id,
                    decision.request.tool_name,
                    decision.request.args_hash,
                    decision.request.capability_hash,
                    decision.request.scope_hash,
                    expires_at,
                    now,
                ),
            )
            grant_row = await (
                await db.execute(
                    "SELECT * FROM execution_grants WHERE grant_id=?", (grant_id,)
                )
            ).fetchone()
            assert grant_row is not None
            authorization = self._row_to_authorization(grant_row)
            self._fault("decision_resolve_after_grant")
        resolved_row = await (
            await db.execute(
                "SELECT * FROM execution_decisions WHERE decision_id=?",
                (decision.decision_id,),
            )
        ).fetchone()
        assert resolved_row is not None
        return self._row_to_decision(resolved_row), authorization, None

    async def commit_decision(
        self,
        signal: DecisionOpen | DecisionSignal,
        actor: ActorContext,
        *,
        expected_run_version: int | None = None,
        expected_continuation_version: int | None = None,
        continuation_payload: Mapping[str, Any] | None = None,
        resumed_event: RunEventCandidate | None = None,
        next_decision: DecisionOpen | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        authorization_commit: PreparedAuthorizationCommit | None = None,
    ) -> tuple[DecisionRecord, DecisionAuthorization | None] | tuple[
        DecisionRecord, DecisionAuthorization | None, ContinuationRecord, RunEvent
    ]:
        """Open or resolve a decision, optionally with its resumed boundary."""

        advances_boundary = continuation_payload is not None or resumed_event is not None
        if isinstance(signal, DecisionOpen):
            if authorization_commit is not None:
                raise ValueError(
                    "authorization commit cannot accompany a decision open"
                )
            if advances_boundary or expected_run_version is None or expected_run_version < 0:
                raise ValueError("decision open requires a non-negative run version")
            async with self._write_transaction() as db:
                now = float(self._clock())
                run_row = await (await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (signal.run_id,)
                )).fetchone()
                if run_row is None:
                    raise RunNotFound(
                        "run_not_found", f"execution run does not exist: {signal.run_id}"
                    )
                run = self._authorize_run_row(
                    run_row, expected_session_id=actor.session_id, actor=actor
                )
                existing = await (await db.execute(
                    """SELECT * FROM execution_decisions
                    WHERE decision_id=? OR (run_id=? AND nonce=?)
                    ORDER BY CASE WHEN decision_id=? THEN 0 ELSE 1 END LIMIT 1""",
                    (signal.decision_id, signal.run_id, signal.nonce, signal.decision_id),
                )).fetchone()
                if existing is not None:
                    record = self._row_to_decision(existing)
                    self._assert_decision_intent(record, signal)
                    await db.commit()
                    return record, None
                if run.persistence_level is not PersistenceLevel.DURABLE:
                    raise PersistenceRequired(
                        "decision_requires_durable_run",
                        "a run must be durable before opening a decision",
                    )
                if run.version != expected_run_version:
                    raise VersionConflict(
                        "stale_run_version",
                        f"expected run version {expected_run_version}, found {run.version}",
                    )
                cursor = await db.execute(
                    """UPDATE execution_runs SET status='waiting',version=version+1,updated_at=?
                    WHERE run_id=? AND version=? AND terminal_event_id IS NULL
                    AND status IN ('created','queued','running','waiting')""",
                    (now, signal.run_id, expected_run_version),
                )
                if cursor.rowcount != 1:
                    raise DecisionConflict(
                        "run_not_signalable",
                        "cancelled or terminal runs cannot open decisions",
                    )
                await self._insert_continuation_decision_tx(
                    db, signal, run=run_row, now=now
                )
                self._fault("decision_open_before_commit")
                row = await (await db.execute(
                    "SELECT * FROM execution_decisions WHERE decision_id=?",
                    (signal.decision_id,),
                )).fetchone()
                assert row is not None
                await db.commit()
                return self._row_to_decision(row), None
        if advances_boundary:
            if expected_continuation_version is None or resumed_event is None or continuation_payload is None:
                raise ValueError("decision boundary requires version, payload and resumed event")
            self._validate_continuation_version(expected_continuation_version)
            payload_json = self._continuation_payload_json(continuation_payload)
        async with self._write_transaction() as db:
            now = float(self._clock())
            decision, authorization, expired = await self._resolve_decision_tx(
                db,
                signal,
                actor,
                now=now,
                authorization_commit=authorization_commit,
            )
            if expired is not None:
                await db.commit()
                raise expired
            if not advances_boundary:
                self._fault("decision_resolve_before_commit")
                await db.commit()
                return decision, authorization
            run = await self._continuation_run_tx(db, signal.run_id)
            continuation, _ = await self._save_continuation_tx(
                db,
                run=run,
                expected_version=int(expected_continuation_version),
                payload_json=payload_json,
                decision=next_decision,
                now=now,
            )
            self._fault("decision_resolve_after_boundary")
            event, _, _ = await self._append_event_tx(
                db,
                run,
                expected_version=int(run["version"]),
                event=resumed_event,
                deliveries=deliveries,
            )
            self._fault("decision_resolve_before_commit")
            await db.commit()
            return decision, authorization, continuation, event

    async def cancel_open_decisions(
        self,
        ref: RunRef,
        actor: ActorContext,
        *,
        expected_run_version: int,
    ) -> tuple[DecisionRecord, ...]:
        if (
            not isinstance(expected_run_version, int)
            or isinstance(expected_run_version, bool)
            or expected_run_version < 0
        ):
            raise ValueError("expected_run_version must be non-negative")
        now = float(self._clock())
        async with self._write_transaction() as db:
            run_row = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (ref.run_id,)
                )
            ).fetchone()
            if run_row is None:
                raise RunNotFound(
                    "run_not_found", f"execution run does not exist: {ref.run_id}"
                )
            run = self._authorize_run_row(
                run_row,
                expected_session_id=ref.expected_session_id,
                actor=actor,
            )
            if run.version != expected_run_version:
                raise VersionConflict(
                    "stale_run_version",
                    f"expected run version {expected_run_version}, found {run.version}",
                )
            if run.status not in (RunStatus.CANCEL_REQUESTED, RunStatus.CANCELLED):
                raise DecisionConflict(
                    "run_not_cancelling",
                    "decision cleanup requires an already persisted run cancellation",
                )
            open_rows = await (
                await db.execute(
                    """SELECT * FROM execution_decisions
                    WHERE run_id=? AND status='open' ORDER BY created_at,decision_id""",
                    (ref.run_id,),
                )
            ).fetchall()
            cancelled_ids = {str(row["decision_id"]) for row in open_rows}
            if open_rows:
                cursor = await db.execute(
                    """UPDATE execution_decisions SET status='expired',
                    decision_version=decision_version+1,resolved_at=?
                    WHERE run_id=? AND status='open'""",
                    (now, ref.run_id),
                )
                if cursor.rowcount != len(open_rows):
                    raise DecisionConflict(
                        "decision_cancel_conflict", "open decision set changed during cancel CAS"
                    )
            await db.execute(
                """UPDATE execution_grants SET status='revoked',
                grant_version=grant_version+1 WHERE run_id=? AND status='issued'""",
                (ref.run_id,),
            )
            open_wait_rows = await (
                await db.execute(
                    """SELECT waits.*,attempts.provider_batch_id
                    FROM execution_task_external_waits AS waits
                    JOIN execution_attempt_records AS attempts
                      ON attempts.attempt_id=waits.attempt_id
                    WHERE attempts.run_id=? AND waits.state='open'
                    ORDER BY waits.created_at,waits.wait_ref""",
                    (ref.run_id,),
                )
            ).fetchall()
            for wait_row in open_wait_rows:
                wait_ref = str(wait_row["wait_ref"])
                attempt_id = str(wait_row["attempt_id"])
                provider_batch_id = str(wait_row["provider_batch_id"])
                wait_cursor = await db.execute(
                    """UPDATE execution_task_external_waits
                    SET state='cancelled',wait_version=wait_version+1,
                        updated_at=?,resolved_at=?
                    WHERE wait_ref=? AND state='open'
                      AND wait_version=?""",
                    (
                        now,
                        now,
                        wait_ref,
                        int(wait_row["wait_version"]),
                    ),
                )
                attempt_cursor = await db.execute(
                    """UPDATE execution_attempt_records
                    SET status='cancelled',budget_eligible=0,
                        attempt_version=attempt_version+1,updated_at=?,
                        ended_at=?
                    WHERE attempt_id=? AND status='waiting_external'""",
                    (now, now, attempt_id),
                )
                if wait_cursor.rowcount != 1 or attempt_cursor.rowcount != 1:
                    raise DecisionConflict(
                        "external_wait_cancel_conflict",
                        "external wait set changed during run cancellation",
                    )
                await db.execute(
                    """UPDATE execution_provider_action_calls
                    SET admission_state='settled',
                        terminal_outcome_ref=(
                            'cancelled:' || ? || ':' || call_record_id
                        ),
                        call_version=call_version+1,updated_at=?
                    WHERE provider_batch_id=? AND admission_state IN (
                        'admitted','prepared','waiting_external'
                    )""",
                    (wait_ref, now, provider_batch_id),
                )
                await db.execute(
                    """UPDATE execution_provider_action_batches
                    SET pending_call_count=0,status='settled',
                        batch_version=batch_version+1,updated_at=?,settled_at=?
                    WHERE provider_batch_id=? AND status='waiting_external'""",
                    (now, now, provider_batch_id),
                )
                await db.execute(
                    """UPDATE execution_task_goals
                    SET status='cancelled',goal_version=goal_version+1,
                        updated_at=?,ended_at=?
                    WHERE root_run_id=? AND status='waiting_external'""",
                    (
                        now,
                        now,
                        str(wait_row["root_run_id"]),
                    ),
                )
            self._fault("decision_cancel_before_commit")
            cancelled_rows = await (
                await db.execute(
                    """SELECT * FROM execution_decisions WHERE run_id=? AND status='expired'
                    ORDER BY created_at,decision_id""",
                    (ref.run_id,),
                )
            ).fetchall()
            await db.commit()
            return tuple(
                self._row_to_decision(item)
                for item in cancelled_rows
                if str(item["decision_id"]) in cancelled_ids
            )

    async def _inspect_authorization_tx(
        self,
        db: aiosqlite.Connection,
        request: GrantConsume,
        actor: ActorContext,
        *,
        allow_consumed: bool = False,
    ) -> aiosqlite.Row:
        grant_row = await (
            await db.execute(
                "SELECT * FROM execution_grants WHERE grant_id=?", (request.grant_id,)
            )
        ).fetchone()
        if grant_row is None:
            raise GrantNotFound(
                "grant_not_found", f"execution grant does not exist: {request.grant_id}"
            )
        decision_row = await (
            await db.execute(
                "SELECT * FROM execution_decisions WHERE decision_id=?",
                (grant_row["decision_id"],),
            )
        ).fetchone()
        assert decision_row is not None
        decision = self._row_to_decision(decision_row)
        run_row = await (
            await db.execute(
                "SELECT * FROM execution_runs WHERE run_id=?", (decision.run_id,)
            )
        ).fetchone()
        assert run_row is not None
        run = self._authorize_run_row(
            run_row,
            expected_session_id=request.expected_session_id,
            actor=actor,
        )
        expected = (
            request.grant_id,
            request.decision_id,
            request.run_id,
            request.call_id,
            request.effect_id,
            request.tool_name,
            request.args_hash,
            request.capability_hash,
            request.scope_hash,
            request.decision_nonce,
        )
        actual = (
            str(grant_row["grant_id"]),
            str(grant_row["decision_id"]),
            str(grant_row["run_id"]),
            str(grant_row["call_id"]),
            str(grant_row["effect_id"]),
            str(grant_row["tool_name"]),
            str(grant_row["args_hash"]),
            str(grant_row["capability_hash"]),
            str(grant_row["scope_hash"]),
            decision.request.nonce,
        )
        if expected != actual or request.capability_hash != run.context.capability_hash:
            raise GrantConsumeConflict(
                "grant_binding_mismatch",
                "grant nonce/run/effect/capability/scope fence differs",
            )
        expected_version = request.expected_version + (1 if allow_consumed else 0)
        if int(grant_row["grant_version"]) != expected_version:
            raise GrantConsumeConflict(
                "stale_grant_version",
                f"expected grant version {expected_version}, "
                f"found {grant_row['grant_version']}",
            )
        if run.status is RunStatus.CANCEL_REQUESTED or run.status in TERMINAL_RUN_STATUSES:
            raise GrantConsumeConflict(
                "run_not_executable", "cancelled or terminal run rejects authorization"
            )
        if decision.status is not DecisionStatus.ALLOWED:
            raise GrantConsumeConflict(
                "grant_not_authorized", "grant decision is not allowed"
            )
        expected_status = "consumed" if allow_consumed else "issued"
        if str(grant_row["status"]) != expected_status:
            raise GrantConsumeConflict(
                "grant_already_consumed",
                "duplicate, expired, or revoked authorization was rejected",
            )
        return grant_row

    async def get_prepared_authorization_provenance(
        self,
        *,
        run_id: str,
        call_id: str,
        effect_id: str,
    ) -> Mapping[str, Any] | None:
        """Return host-committed TaskGrant provenance for one exact call."""

        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT response_json FROM execution_decisions
                       WHERE run_id=? AND call_id=? AND effect_id=?
                         AND kind='permission' AND status='allowed'
                       ORDER BY resolved_at DESC,created_at DESC
                       LIMIT 1""",
                    (run_id, call_id, effect_id),
                )
            ).fetchone()
            if row is None:
                return None
            response = json.loads(str(row["response_json"] or "{}"))
            if not isinstance(response, dict):
                return None
            task_grant_id = str(response.get("task_grant_id") or "")
            if not task_grant_id:
                return None
            grant_row = await (
                await db.execute(
                    """SELECT grant_json,status FROM task_grants
                       WHERE task_grant_id=?""",
                    (task_grant_id,),
                )
            ).fetchone()
            if grant_row is None or str(grant_row["status"]) != "active":
                return None
            grant = json.loads(str(grant_row["grant_json"] or "{}"))
            if not isinstance(grant, dict):
                return None
            policy = await (
                await db.execute(
                    """SELECT mode,generation,updated_at
                       FROM authorization_policy_state WHERE singleton_id=1"""
                )
            ).fetchone()
            if policy is None:
                return None
            return {
                "task_grant": grant,
                "policy_state": {
                    "mode": str(policy["mode"]),
                    "generation": int(policy["generation"]),
                    "updated_at": float(policy["updated_at"]),
                },
                "authorization_source": str(
                    response.get("authorization_source") or ""
                ),
                "authorization_intent_fingerprint": str(
                    response.get("authorization_intent_fingerprint") or ""
                ),
            }

    async def _consume_authorization_tx(
        self,
        db: aiosqlite.Connection,
        request: GrantConsume,
        actor: ActorContext,
        *,
        now: float,
    ) -> tuple[DecisionAuthorization, GrantConsumeConflict | None]:
        grant_row = await self._inspect_authorization_tx(db, request, actor)
        if float(grant_row["expires_at"]) <= now:
            cursor = await db.execute(
                """UPDATE execution_grants SET status='expired',
                grant_version=grant_version+1 WHERE grant_id=?
                AND grant_version=? AND status='issued'""",
                (request.grant_id, request.expected_version),
            )
            if cursor.rowcount != 1:
                raise GrantConsumeConflict(
                    "stale_grant_version", "grant changed before expiry CAS"
                )
            expired = await (
                await db.execute(
                    "SELECT * FROM execution_grants WHERE grant_id=?", (request.grant_id,)
                )
            ).fetchone()
            assert expired is not None
            return (
                self._row_to_authorization(expired),
                GrantConsumeConflict("grant_expired", "expired authorization was rejected"),
            )
        cursor = await db.execute(
            """UPDATE execution_grants SET status='consumed',
            grant_version=grant_version+1,consumed_at=? WHERE grant_id=?
            AND grant_version=? AND status='issued'""",
            (now, request.grant_id, request.expected_version),
        )
        if cursor.rowcount != 1:
            raise GrantConsumeConflict(
                "grant_consume_conflict", "grant changed before consume CAS"
            )
        consumed = await (
            await db.execute(
                "SELECT * FROM execution_grants WHERE grant_id=?", (request.grant_id,)
            )
        ).fetchone()
        assert consumed is not None
        return self._row_to_authorization(consumed), None

    async def claim_tool_call(
        self,
        request: GrantConsume | None,
        actor: ActorContext,
        *,
        run_id: str | None = None,
        expected_session_id: str | None = None,
        call_id: str | None = None,
        effect_id: str | None = None,
        tool_name: str | None = None,
        args_hash: str | None = None,
        capability_hash: str | None = None,
        scope_hash: str | None = None,
        effect_type: str | None = None,
        policy: Mapping[str, Any] | None = None,
        prepared: Mapping[str, Any] | None = None,
        worker_owner: str = "",
        worker_epoch: int = 0,
        recovery_lease: RecoveryLease | None = None,
    ) -> DecisionAuthorization | ExecutionEffectClaim:
        """Consume authorization, optionally claiming its fenced tool effect."""

        if effect_type is None:
            if request is None:
                raise ValueError("authorization consumption requires a grant")
            async with self._write_transaction() as db:
                authorization, expired = await self._consume_authorization_tx(
                    db, request, actor, now=float(self._clock())
                )
                self._fault("grant_consume_before_commit")
                await db.commit()
                if expired is not None:
                    raise expired
                return authorization

        if not effect_type or not worker_owner or worker_epoch < 1:
            raise ValueError("effect type, worker owner and positive epoch are required")
        if policy is None or prepared is None:
            raise ValueError("effect claim requires policy and prepared payload")
        if request is not None:
            run_id, expected_session_id, call_id, effect_id, tool_name = (
                request.run_id, request.expected_session_id, request.call_id,
                request.effect_id, request.tool_name,
            )
            args_hash, capability_hash, scope_hash = (
                request.args_hash, request.capability_hash, request.scope_hash,
            )
        identity = (run_id, expected_session_id, call_id, effect_id, tool_name,
                    args_hash, capability_hash, scope_hash)
        if any(not str(value or "").strip() for value in identity):
            raise ValueError("complete effect identity is required")
        if actor.session_id != expected_session_id:
            raise AuthorizationError("effect_actor_mismatch", "effect actor does not own the session")
        policy_json = canonical_json(thaw_json(policy))
        prepared_json = canonical_json(thaw_json(prepared))
        effect_fingerprint = fingerprint_json(
            {
                "run_id": run_id,
                "call_id": call_id,
                "effect_id": effect_id,
                "tool_name": tool_name,
                "args_hash": args_hash,
                "capability_hash": capability_hash,
                "scope_hash": scope_hash,
                "effect_type": effect_type,
            }
        )
        async with self._write_transaction() as db:
            await self._assert_optional_recovery_fence_tx(db, recovery_lease, str(run_id))
            run = await (
                await db.execute(
                    "SELECT session_id,root_run_id FROM execution_runs WHERE run_id=?",
                    (run_id,),
                )
            ).fetchone()
            if run is None or (str(run["session_id"]), str(run["root_run_id"])) != (
                actor.session_id, actor.root_run_id,
            ):
                raise AuthorizationError("effect_actor_mismatch", "effect actor does not own the run tree")
            existing = await (
                await db.execute(
                    "SELECT * FROM execution_effects WHERE effect_id=?",
                    (effect_id,),
                )
            ).fetchone()
            if existing is not None:
                expected = (
                    run_id,
                    effect_fingerprint,
                    call_id,
                    tool_name,
                    args_hash,
                    capability_hash,
                    scope_hash,
                    effect_type,
                    policy_json,
                    prepared_json,
                )
                actual = tuple(
                    str(existing[name] or "")
                    for name in (
                        "run_id",
                        "effect_fingerprint",
                        "call_id",
                        "tool_name",
                        "args_hash",
                        "capability_hash",
                        "scope_hash",
                        "effect_type",
                        "policy_json",
                        "prepared_json",
                    )
                )
                if actual != expected:
                    raise IdempotencyConflict(
                        "effect_claim_conflict", "effect id already names another intent"
                    )
                attempt = await (
                    await db.execute(
                        """SELECT * FROM execution_effect_attempts
                        WHERE effect_id=? AND attempt_no=1""",
                        (effect_id,),
                    )
                ).fetchone()
                assert attempt is not None
                authorization = None
                if request is not None:
                    consumed = await self._inspect_authorization_tx(
                        db, request, actor, allow_consumed=True
                    )
                    authorization = self._row_to_authorization(consumed)
                await db.commit()
                return ExecutionEffectClaim(
                    str(effect_id),
                    str(run_id),
                    1,
                    str(existing["status"]),
                    str(attempt["worker_owner"]),
                    int(attempt["worker_epoch"]),
                    int(existing["effect_version"]),
                    (
                        "reconcile"
                        if recovery_lease is not None
                        and str(existing["status"]) in {"running", "unknown"}
                        else "reuse"
                        if str(existing["status"]) != "running"
                        else "in_flight"
                    ),
                    authorization,
                )
            authorization = None
            if request is not None:
                authorization, expired = await self._consume_authorization_tx(
                    db, request, actor, now=float(self._clock())
                )
                if expired is not None:
                    await db.commit()
                    raise expired
            self._fault("effect_claim_after_grant")
            now = float(self._clock())
            await db.execute(
                """INSERT INTO execution_effects(
                effect_id,schema_version,run_id,effect_fingerprint,call_id,tool_name,
                args_hash,capability_hash,scope_hash,effect_type,status,
                handoff_state,completion_disposition,policy_json,
                prepared_json,effect_version,created_at,updated_at
                ) VALUES(?,1,?,?,?,?,?,?,?,?, 'running','unresolved','normal',
                    ?,?,0,?,?)""",
                (
                    effect_id,
                    run_id,
                    effect_fingerprint,
                    call_id,
                    tool_name,
                    args_hash,
                    capability_hash,
                    scope_hash,
                    effect_type,
                    policy_json,
                    prepared_json,
                    now,
                    now,
                ),
            )
            self._fault("effect_claim_after_effect")
            await db.execute(
                """INSERT INTO execution_effect_attempts(
                effect_id,attempt_no,status,handoff_state,
                completion_disposition,worker_owner,worker_epoch,
                started_at,updated_at
                ) VALUES(?,1,'running','unresolved','normal',?,?,?,?)""",
                (effect_id, worker_owner, worker_epoch, now, now),
            )
            self._fault("effect_claim_before_commit")
            await db.commit()
            return ExecutionEffectClaim(
                str(effect_id),
                str(run_id),
                1,
                "running",
                worker_owner,
                worker_epoch,
                0,
                "execute",
                authorization,
            )

    @staticmethod
    def _effect_handoff_record(
        effect: Mapping[str, Any], attempt_no: int
    ) -> ExecutionEffectHandoff:
        return ExecutionEffectHandoff(
            effect_id=str(effect["effect_id"]),
            attempt_no=attempt_no,
            status=str(effect["status"]),
            handoff_state=str(effect["handoff_state"]),
            completion_disposition=str(effect["completion_disposition"]),
            effect_version=int(effect["effect_version"]),
        )

    async def _effect_attempt_tx(
        self,
        db: aiosqlite.Connection,
        effect_id: str,
        attempt_no: int,
    ) -> tuple[aiosqlite.Row, aiosqlite.Row]:
        effect = await (
            await db.execute(
                "SELECT * FROM execution_effects WHERE effect_id=?", (effect_id,)
            )
        ).fetchone()
        attempt = await (
            await db.execute(
                """SELECT * FROM execution_effect_attempts
                WHERE effect_id=? AND attempt_no=?""",
                (effect_id, attempt_no),
            )
        ).fetchone()
        if effect is None or attempt is None:
            raise RunNotFound(
                "effect_not_found", "execution effect attempt does not exist"
            )
        return effect, attempt

    async def read_effect_handoff(
        self, effect_id: str
    ) -> ExecutionEffectHandoff | None:
        async with self._read_connection() as db:
            effect = await (
                await db.execute(
                    "SELECT execution_effects.*, ("
                    "SELECT MAX(attempt_no) FROM execution_effect_attempts "
                    "WHERE execution_effect_attempts.effect_id="
                    "execution_effects.effect_id"
                    ") AS latest_attempt_no FROM execution_effects "
                    "WHERE effect_id=?",
                    (effect_id,),
                )
            ).fetchone()
            if effect is None:
                return None
            attempt_no = int(effect["latest_attempt_no"] or 0)
            if attempt_no < 1:
                return None
            return self._effect_handoff_record(effect, attempt_no)

    @staticmethod
    def _receipt_pair(ref: str, digest: str) -> None:
        if not ref.strip():
            raise ValueError("effect handoff receipt ref is required")
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError("effect handoff receipt hash must be lowercase SHA-256")

    async def mark_effect_dispatch_not_started(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        receipt_ref: str,
        receipt_hash: str,
    ) -> ExecutionEffectHandoff:
        self._receipt_pair(receipt_ref, receipt_hash)
        async with self._write_transaction() as db:
            effect, attempt = await self._effect_attempt_tx(
                db, effect_id, attempt_no
            )
            if str(effect["status"]) == "cancelled":
                expected = (
                    "not_started",
                    "confirmed_not_started",
                    receipt_ref,
                    receipt_hash,
                )
                actual = (
                    str(effect["handoff_state"]),
                    str(effect["completion_disposition"]),
                    str(effect["handoff_ack_ref"] or ""),
                    str(effect["handoff_ack_hash"] or ""),
                )
                if actual != expected:
                    raise IdempotencyConflict(
                        "effect_not_started_conflict",
                        "effect has another not-started receipt",
                    )
                return self._effect_handoff_record(effect, attempt_no)
            if (
                str(effect["status"]) != "running"
                or str(effect["handoff_state"]) != "unresolved"
                or int(effect["effect_version"]) != expected_effect_version
                or str(attempt["status"]) != "running"
            ):
                raise VersionConflict(
                    "stale_effect_handoff",
                    "effect cannot be marked not-started from its current state",
                )
            now = float(self._clock())
            await db.execute(
                """UPDATE execution_effect_attempts
                SET status='cancelled',handoff_state='not_started',
                    completion_disposition='confirmed_not_started',
                    handoff_ack_ref=?,handoff_ack_hash=?,handoff_ack_at=?,
                    updated_at=?,ended_at=?
                WHERE effect_id=? AND attempt_no=? AND status='running'
                  AND handoff_state='unresolved'""",
                (
                    receipt_ref,
                    receipt_hash,
                    now,
                    now,
                    now,
                    effect_id,
                    attempt_no,
                ),
            )
            cursor = await db.execute(
                """UPDATE execution_effects
                SET status='cancelled',handoff_state='not_started',
                    completion_disposition='confirmed_not_started',
                    handoff_ack_ref=?,handoff_ack_hash=?,handoff_ack_at=?,
                    effect_version=effect_version+1,updated_at=?,ended_at=?
                WHERE effect_id=? AND status='running'
                  AND handoff_state='unresolved' AND effect_version=?""",
                (
                    receipt_ref,
                    receipt_hash,
                    now,
                    now,
                    now,
                    effect_id,
                    expected_effect_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_effect_handoff", "effect not-started CAS lost"
                )
            updated, _ = await self._effect_attempt_tx(db, effect_id, attempt_no)
            return self._effect_handoff_record(updated, attempt_no)

    async def mark_effect_dispatch_started(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        ack_ref: str,
        ack_hash: str,
        *,
        ack_at: float | None = None,
    ) -> ExecutionEffectHandoff:
        self._receipt_pair(ack_ref, ack_hash)
        async with self._write_transaction() as db:
            effect, attempt = await self._effect_attempt_tx(
                db, effect_id, attempt_no
            )
            if str(effect["handoff_state"]) == "started":
                if (
                    str(effect["handoff_ack_ref"] or ""),
                    str(effect["handoff_ack_hash"] or ""),
                ) != (ack_ref, ack_hash):
                    raise IdempotencyConflict(
                        "effect_started_conflict",
                        "effect has another dispatch-start ack",
                    )
                return self._effect_handoff_record(effect, attempt_no)
            if (
                str(effect["status"]) != "running"
                or str(effect["handoff_state"]) != "unresolved"
                or int(effect["effect_version"]) != expected_effect_version
                or str(attempt["handoff_state"]) != "unresolved"
            ):
                raise VersionConflict(
                    "stale_effect_handoff",
                    "effect cannot be marked started from its current state",
                )
            now = float(self._clock()) if ack_at is None else float(ack_at)
            await db.execute(
                """UPDATE execution_effect_attempts
                SET handoff_state='started',handoff_ack_ref=?,
                    handoff_ack_hash=?,handoff_ack_at=?,updated_at=?
                WHERE effect_id=? AND attempt_no=? AND status='running'
                  AND handoff_state='unresolved'""",
                (ack_ref, ack_hash, now, now, effect_id, attempt_no),
            )
            cursor = await db.execute(
                """UPDATE execution_effects
                SET handoff_state='started',handoff_ack_ref=?,
                    handoff_ack_hash=?,handoff_ack_at=?,
                    effect_version=effect_version+1,updated_at=?
                WHERE effect_id=? AND status='running'
                  AND handoff_state='unresolved' AND effect_version=?""",
                (
                    ack_ref,
                    ack_hash,
                    now,
                    now,
                    effect_id,
                    expected_effect_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_effect_handoff", "effect dispatch-start CAS lost"
                )
            updated, _ = await self._effect_attempt_tx(db, effect_id, attempt_no)
            return self._effect_handoff_record(updated, attempt_no)

    async def mark_effect_inflight_may_complete(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        receipt_ref: str,
        receipt_hash: str,
    ) -> ExecutionEffectHandoff:
        self._receipt_pair(receipt_ref, receipt_hash)
        async with self._write_transaction() as db:
            effect, _ = await self._effect_attempt_tx(db, effect_id, attempt_no)
            if str(effect["status"]) == "unknown":
                if (
                    str(effect["handoff_unknown_receipt_ref"] or ""),
                    str(effect["handoff_unknown_receipt_hash"] or ""),
                ) != (receipt_ref, receipt_hash):
                    raise IdempotencyConflict(
                        "effect_unknown_conflict",
                        "effect has another unknown-start receipt",
                    )
                return self._effect_handoff_record(effect, attempt_no)
            if (
                str(effect["status"]) != "running"
                or str(effect["handoff_state"]) not in {"unresolved", "started"}
                or int(effect["effect_version"]) != expected_effect_version
            ):
                raise VersionConflict(
                    "stale_effect_handoff",
                    "effect cannot become may-complete from its current state",
                )
            now = float(self._clock())
            await db.execute(
                """UPDATE execution_effect_attempts
                SET status='unknown',handoff_state='started_may_complete',
                    completion_disposition='inflight_effect_may_complete',
                    handoff_unknown_receipt_ref=?,
                    handoff_unknown_receipt_hash=?,handoff_unknown_at=?,
                    updated_at=?,ended_at=?
                WHERE effect_id=? AND attempt_no=? AND status='running'""",
                (
                    receipt_ref,
                    receipt_hash,
                    now,
                    now,
                    now,
                    effect_id,
                    attempt_no,
                ),
            )
            cursor = await db.execute(
                """UPDATE execution_effects
                SET status='unknown',handoff_state='started_may_complete',
                    completion_disposition='inflight_effect_may_complete',
                    handoff_unknown_receipt_ref=?,
                    handoff_unknown_receipt_hash=?,handoff_unknown_at=?,
                    effect_version=effect_version+1,updated_at=?,ended_at=?
                WHERE effect_id=? AND status='running'
                  AND effect_version=?""",
                (
                    receipt_ref,
                    receipt_hash,
                    now,
                    now,
                    now,
                    effect_id,
                    expected_effect_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_effect_handoff", "effect may-complete CAS lost"
                )
            updated, _ = await self._effect_attempt_tx(db, effect_id, attempt_no)
            return self._effect_handoff_record(updated, attempt_no)

    async def suppress_late_effect_completion(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        late_outcome_hash: str,
    ) -> ExecutionEffectHandoff:
        self._receipt_pair("late-outcome", late_outcome_hash)
        async with self._write_transaction() as db:
            effect, _ = await self._effect_attempt_tx(db, effect_id, attempt_no)
            if str(effect["late_outcome_hash"] or "") == late_outcome_hash:
                return self._effect_handoff_record(effect, attempt_no)
            if (
                str(effect["status"]) != "unknown"
                or str(effect["handoff_state"]) != "started_may_complete"
                or int(effect["effect_version"]) != expected_effect_version
            ):
                raise VersionConflict(
                    "stale_effect_handoff",
                    "only an unknown may-complete effect can suppress late completion",
                )
            now = float(self._clock())
            await db.execute(
                """UPDATE execution_effect_attempts SET late_outcome_hash=?,
                    updated_at=? WHERE effect_id=? AND attempt_no=?
                    AND status='unknown'""",
                (late_outcome_hash, now, effect_id, attempt_no),
            )
            await db.execute(
                """UPDATE execution_effects SET late_outcome_hash=?,
                    effect_version=effect_version+1,updated_at=?
                WHERE effect_id=? AND effect_version=? AND status='unknown'""",
                (
                    late_outcome_hash,
                    now,
                    effect_id,
                    expected_effect_version,
                ),
            )
            updated, _ = await self._effect_attempt_tx(db, effect_id, attempt_no)
            return self._effect_handoff_record(updated, attempt_no)

    async def reconcile_effect_handoff(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        receipt_ref: str,
        receipt_hash: str,
        *,
        completed_suppressed: bool,
    ) -> ExecutionEffectHandoff:
        self._receipt_pair(receipt_ref, receipt_hash)
        disposition = (
            "reconciled_completed_suppressed"
            if completed_suppressed
            else "reconciled_not_completed"
        )
        async with self._write_transaction() as db:
            effect, _ = await self._effect_attempt_tx(db, effect_id, attempt_no)
            if str(effect["status"]) == "late_reconciled":
                if (
                    str(effect["reconcile_receipt_ref"] or ""),
                    str(effect["reconcile_receipt_hash"] or ""),
                    str(effect["completion_disposition"]),
                ) != (receipt_ref, receipt_hash, disposition):
                    raise IdempotencyConflict(
                        "effect_reconcile_conflict",
                        "effect has another reconciliation receipt",
                    )
                return self._effect_handoff_record(effect, attempt_no)
            if (
                str(effect["status"]) != "unknown"
                or str(effect["handoff_state"]) != "started_may_complete"
                or int(effect["effect_version"]) != expected_effect_version
            ):
                raise VersionConflict(
                    "stale_effect_handoff",
                    "effect cannot reconcile from its current state",
                )
            now = float(self._clock())
            await db.execute(
                """UPDATE execution_effect_attempts
                SET status='late_reconciled',handoff_state='reconciled',
                    completion_disposition=?,reconcile_receipt_ref=?,
                    reconcile_receipt_hash=?,updated_at=?,ended_at=?
                WHERE effect_id=? AND attempt_no=? AND status='unknown'""",
                (
                    disposition,
                    receipt_ref,
                    receipt_hash,
                    now,
                    now,
                    effect_id,
                    attempt_no,
                ),
            )
            cursor = await db.execute(
                """UPDATE execution_effects
                SET status='late_reconciled',handoff_state='reconciled',
                    completion_disposition=?,reconcile_receipt_ref=?,
                    reconcile_receipt_hash=?,
                    effect_version=effect_version+1,updated_at=?,ended_at=?
                WHERE effect_id=? AND status='unknown' AND effect_version=?""",
                (
                    disposition,
                    receipt_ref,
                    receipt_hash,
                    now,
                    now,
                    effect_id,
                    expected_effect_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_effect_handoff", "effect reconciliation CAS lost"
                )
            updated, _ = await self._effect_attempt_tx(db, effect_id, attempt_no)
            return self._effect_handoff_record(updated, attempt_no)

    async def _mark_effect_unknown_tx(
        self,
        db: aiosqlite.Connection,
        effect_id: str,
        *,
        expected_effect_version: int,
        attempt_no: int,
        worker_owner: str,
        worker_epoch: int,
        outcome_json: str,
        recovery_lease: RecoveryLease | None = None,
    ) -> None:
        effect = await (await db.execute(
            "SELECT * FROM execution_effects WHERE effect_id=?", (effect_id,)
        )).fetchone()
        if effect is None:
            raise RunNotFound("effect_not_found", "execution effect does not exist")
        await self._assert_optional_recovery_fence_tx(
            db, recovery_lease, str(effect["run_id"])
        )
        if str(effect["status"]) == "unknown":
            if (int(effect["effect_version"]) == expected_effect_version + 1
                    and str(effect["outcome_json"] or "") == outcome_json):
                return
            raise IdempotencyConflict(
                "effect_unknown_conflict", "effect has another unknown intent"
            )
        if (str(effect["status"]) != "running"
                or int(effect["effect_version"]) != expected_effect_version):
            raise VersionConflict(
                "stale_effect_version", "effect changed before unknown marking"
            )
        now = float(self._clock())
        attempt = await db.execute(
            """UPDATE execution_effect_attempts SET status='unknown',
            handoff_state='started_may_complete',
            completion_disposition='inflight_effect_may_complete',
            outcome_json=?,updated_at=?,ended_at=?
            WHERE effect_id=? AND attempt_no=?
            AND status='running' AND worker_owner=? AND worker_epoch=?""",
            (outcome_json, now, now, effect_id, attempt_no, worker_owner, worker_epoch),
        )
        if attempt.rowcount != 1:
            raise VersionConflict(
                "stale_effect_attempt", "effect attempt owner or epoch changed"
            )
        updated = await db.execute(
            """UPDATE execution_effects SET status='unknown',
            handoff_state='started_may_complete',
            completion_disposition='inflight_effect_may_complete',
            outcome_json=?,effect_version=effect_version+1,
            updated_at=?,ended_at=?
            WHERE effect_id=? AND effect_version=? AND status='running'""",
            (outcome_json, now, now, effect_id, expected_effect_version),
        )
        if updated.rowcount != 1:
            raise VersionConflict(
                "stale_effect_version", "effect changed before unknown marking"
            )
        self._fault("effect_unknown_before_commit")

    async def settle_effect(
        self,
        effect_id: str,
        *,
        expected_effect_version: int,
        attempt_no: int,
        worker_owner: str,
        worker_epoch: int,
        status: str = "unknown",
        outcome: Mapping[str, Any],
        receipt_ref: str | None = None,
        artifact_refs: Sequence[str] = (),
        node_execution_id: str | None = None,
        checkpoint_ns: str | None = None,
        checkpoint_id: str | None = None,
        expected_continuation_version: int | None = None,
        continuation_payload: Mapping[str, Any] | None = None,
        event: RunEventCandidate | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        recovery_lease: RecoveryLease | None = None,
        reconciliation: bool = False,
        evidence_verified: bool = False,
        transaction_hook: Callable[
            [aiosqlite.Connection, ExecutionEffectSettlement],
            Any,
        ]
        | None = None,
    ) -> ExecutionEffectSettlement | None:
        """Fence an unknown effect or settle it with the next durable boundary."""

        advances_boundary = continuation_payload is not None or event is not None
        allowed = {"succeeded", "failed", "accepted", "unknown", "cancelled"}
        if status not in allowed or attempt_no < 1 or worker_epoch < 1 or not worker_owner:
            raise ValueError("invalid effect settlement fence or status")
        outcome_json = canonical_json(thaw_json(outcome))
        if not advances_boundary:
            async with self._write_transaction() as db:
                await self._mark_effect_unknown_tx(
                    db, effect_id, expected_effect_version=expected_effect_version,
                    attempt_no=attempt_no, worker_owner=worker_owner,
                    worker_epoch=worker_epoch, outcome_json=outcome_json,
                    recovery_lease=recovery_lease,
                )
                effect = await (
                    await db.execute(
                        "SELECT * FROM execution_effects WHERE effect_id=?",
                        (effect_id,),
                    )
                ).fetchone()
                assert effect is not None
                await self._materialize_tool_public_projection_tx(
                    db,
                    effect=effect,
                    status="unknown",
                    outcome=thaw_json(outcome),
                    created_at=float(self._clock()),
                )
                await db.commit()
                return None
        if (expected_continuation_version is None or continuation_payload is None
                or event is None or node_execution_id is None
                or checkpoint_ns is None or checkpoint_id is None):
            raise ValueError("effect boundary requires continuation, event and checkpoint identity")
        if reconciliation:
            if recovery_lease is None:
                raise StaleRecoveryLease("effect reconciliation requires a recovery lease")
            state = str(outcome.get("state") or "")
            status = (
                "accepted" if status == "accepted"
                else "failed" if state == "failure"
                else "succeeded" if state == "success" and evidence_verified
                else "unknown"
            )
        self._validate_continuation_version(expected_continuation_version)
        artifacts_json = canonical_json([str(item) for item in artifact_refs])
        payload_json = self._continuation_payload_json(continuation_payload)
        async with self._write_transaction() as db:
            effect = await (
                await db.execute(
                    "SELECT * FROM execution_effects WHERE effect_id=?", (effect_id,)
                )
            ).fetchone()
            if effect is None:
                raise RunNotFound("effect_not_found", "execution effect does not exist")
            await self._assert_optional_recovery_fence_tx(db, recovery_lease, str(effect["run_id"]))
            source_status = str(effect["status"])
            if source_status != "running" and not (reconciliation and source_status == "unknown"):
                replay_event = await (
                    await db.execute(
                        """SELECT event.*,run.root_run_id,run.session_id
                        FROM execution_events AS event JOIN execution_runs AS run
                        ON run.run_id=event.run_id
                        WHERE event.run_id=? AND event.event_key=?""",
                        (effect["run_id"], event.event_key),
                    )
                ).fetchone()
                replay_continuation = await (
                    await db.execute(
                        "SELECT * FROM execution_continuations WHERE run_id=?",
                        (effect["run_id"],),
                    )
                ).fetchone()
                if (
                    int(effect["effect_version"]) == expected_effect_version + 1
                    and str(effect["status"]) == status
                    and str(effect["outcome_json"] or "") == outcome_json
                    and str(effect["receipt_ref"] or "") == str(receipt_ref or "")
                    and str(effect["artifact_refs_json"]) == artifacts_json
                    and replay_event is not None
                    and replay_continuation is not None
                    and int(replay_continuation["continuation_version"])
                    == expected_continuation_version + 1
                    and str(replay_continuation["pending_prepared_call_json"])
                    == payload_json
                ):
                    await db.commit()
                    return ExecutionEffectSettlement(
                        effect_id,
                        status,
                        expected_effect_version + 1,
                        self._row_to_continuation(replay_continuation),
                        self._row_to_event(replay_event),
                    )
                raise IdempotencyConflict(
                    "effect_already_settled", "effect is no longer running"
                )
            if int(effect["effect_version"]) != expected_effect_version:
                raise VersionConflict(
                    "stale_effect_version", "effect changed before settlement CAS"
                )
            now = float(self._clock())
            target_handoff_state = (
                "started_may_complete" if status == "unknown" else "reconciled"
            )
            target_disposition = (
                "inflight_effect_may_complete" if status == "unknown" else "normal"
            )
            if not reconciliation:
                cursor = await db.execute(
                    """UPDATE execution_effect_attempts SET status=?,
                    handoff_state=?,completion_disposition=?,outcome_json=?,
                    updated_at=?,ended_at=? WHERE effect_id=? AND attempt_no=?
                    AND status='running' AND worker_owner=? AND worker_epoch=?""",
                    (status, target_handoff_state, target_disposition,
                     outcome_json, now, now, effect_id, attempt_no,
                     worker_owner, worker_epoch),
                )
                if cursor.rowcount != 1:
                    raise VersionConflict(
                        "stale_effect_attempt", "effect attempt owner or epoch changed"
                    )
            self._fault("effect_settle_after_attempt")
            cursor = await db.execute(
                """UPDATE execution_effects SET status=?,handoff_state=?,
                completion_disposition=?,outcome_json=?,receipt_ref=?,
                artifact_refs_json=?,effect_version=effect_version+1,
                updated_at=?,ended_at=?
                WHERE effect_id=? AND effect_version=? AND status=?""",
                (
                    status,
                    target_handoff_state,
                    target_disposition,
                    outcome_json,
                    receipt_ref,
                    artifacts_json,
                    now,
                    now,
                    effect_id,
                    expected_effect_version,
                    source_status,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_effect_version", "effect changed before settlement CAS"
                )
            await self._materialize_tool_public_projection_tx(
                db,
                effect=effect,
                status=status,
                outcome=thaw_json(outcome),
                created_at=now,
            )
            self._fault("effect_settle_after_effect")
            run_id = str(effect["run_id"])
            await db.execute(
                """INSERT INTO execution_effect_links(
                run_id,node_execution_id,effect_id,checkpoint_ns,checkpoint_id,created_at
                ) VALUES(?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                (
                    run_id,
                    node_execution_id,
                    effect_id,
                    checkpoint_ns,
                    checkpoint_id,
                    now,
                ),
            )
            self._fault("effect_settle_after_link")
            run = await self._continuation_run_tx(db, run_id)
            continuation, _ = await self._save_continuation_tx(
                db,
                run=run,
                expected_version=int(expected_continuation_version),
                payload_json=payload_json,
                decision=None,
                now=now,
            )
            self._fault("effect_settle_after_boundary")
            stored_event, _, _ = await self._append_event_tx(
                db,
                run,
                expected_version=int(run["version"]),
                event=event,
                deliveries=deliveries,
            )
            settlement = ExecutionEffectSettlement(
                effect_id,
                status,
                expected_effect_version + 1,
                continuation,
                stored_event,
            )
            if transaction_hook is not None:
                hook_result = transaction_hook(db, settlement)
                if inspect.isawaitable(hook_result):
                    await hook_result
            self._fault("effect_settle_before_commit")
            await db.commit()
            return settlement

    async def _materialize_tool_public_projection_tx(
        self,
        db: aiosqlite.Connection,
        *,
        effect: Mapping[str, Any],
        status: str,
        outcome: Mapping[str, Any],
        created_at: float,
    ) -> None:
        """Write the fail-closed public effect view in the effect transaction.

        ``unknown`` is a durable recovery state, not an immutable final result.  A
        reconciler may later advance that same effect to a verified terminal
        state.  Keep the effect identity immutable, but allow the public view to
        follow that one legal CAS transition in the same transaction.
        """

        from deskpet.security.tool_public_projection import (
            ToolPresentationPolicyV1,
            ToolPublicProjectorV1,
            legacy_unknown_tool_policy,
        )

        run = await (
            await db.execute(
                "SELECT root_run_id FROM execution_runs WHERE run_id=?",
                (effect["run_id"],),
            )
        ).fetchone()
        if run is None:
            raise RunNotFound("run_not_found", "effect owner Run does not exist")
        root_run_id = str(run["root_run_id"])
        tool_name = str(effect["tool_name"])
        policy_row = await (
            await db.execute(
                "SELECT policy_json FROM execution_run_tool_presentation_specs "
                "WHERE root_run_id=? AND tool_name=?",
                (root_run_id, tool_name),
            )
        ).fetchone()
        policy = None
        if policy_row is not None:
            try:
                raw_policy = json.loads(str(policy_row["policy_json"]))
                if not isinstance(raw_policy, dict):
                    raise TypeError("policy payload must be an object")
                raw_policy["safe_arg_paths"] = tuple(
                    raw_policy.get("safe_arg_paths", ())
                )
                raw_policy["safe_result_paths"] = tuple(
                    raw_policy.get("safe_result_paths", ())
                )
                policy = ToolPresentationPolicyV1(**raw_policy)
            except (TypeError, ValueError, json.JSONDecodeError):
                policy = None
        try:
            projection = ToolPublicProjectorV1().project(
                policy or legacy_unknown_tool_policy(tool_name),
                arguments=json.loads(str(effect["prepared_json"] or "{}")),
                result=outcome,
                status=status,
            ).to_dict()
        except Exception:  # noqa: BLE001 - default deny is the safety boundary
            projection = ToolPublicProjectorV1().project(
                legacy_unknown_tool_policy(tool_name),
                arguments={},
                result={},
                status=status,
            ).to_dict()
            projection["projection_error"] = "default_deny"
        projection_json = canonical_json(projection)
        projection_hash = hashlib.sha256(
            projection_json.encode("utf-8")
        ).hexdigest()
        await db.execute(
            "INSERT INTO execution_tool_public_projections("
            "effect_id,root_run_id,run_id,tool_name,schema_version,"
            "projection_json,projection_hash,status,created_at) "
            "VALUES(?,?,?,?,1,?,?,?,?) ON CONFLICT(effect_id) DO NOTHING",
            (
                str(effect["effect_id"]),
                root_run_id,
                str(effect["run_id"]),
                tool_name,
                projection_json,
                projection_hash,
                status,
                created_at,
            ),
        )
        stored = await (
            await db.execute(
                "SELECT root_run_id,run_id,tool_name,projection_json,"
                "projection_hash,status FROM execution_tool_public_projections "
                "WHERE effect_id=?",
                (str(effect["effect_id"]),),
            )
        ).fetchone()
        assert stored is not None
        immutable_identity = (
            root_run_id,
            str(effect["run_id"]),
            tool_name,
        )
        stored_identity = tuple(str(stored[index]) for index in range(3))
        if stored_identity != immutable_identity:
            raise IdempotencyConflict(
                "tool_public_projection_conflict",
                "effect replay supplied a different public projection identity",
            )
        expected = (
            *immutable_identity,
            projection_json,
            projection_hash,
            status,
        )
        actual = tuple(str(stored[index]) for index in range(6))
        if actual == expected:
            return

        source_status = str(effect["status"])
        stored_status = str(stored["status"])
        legal_reconciliation = (
            source_status == "unknown"
            and stored_status == source_status
            and status in {"succeeded", "failed", "accepted", "cancelled"}
        )
        if not legal_reconciliation:
            raise IdempotencyConflict(
                "tool_public_projection_conflict",
                "effect replay supplied a different public projection",
            )
        cursor = await db.execute(
            "UPDATE execution_tool_public_projections SET "
            "projection_json=?,projection_hash=?,status=? "
            "WHERE effect_id=? AND root_run_id=? AND run_id=? AND tool_name=? "
            "AND status=? AND projection_hash=?",
            (
                projection_json,
                projection_hash,
                status,
                str(effect["effect_id"]),
                *immutable_identity,
                stored_status,
                str(stored["projection_hash"]),
            ),
        )
        if cursor.rowcount != 1:
            raise IdempotencyConflict(
                "tool_public_projection_conflict",
                "effect public projection changed before reconciliation",
            )

    @staticmethod
    def _terminal_outcome(status: RunStatus) -> OutcomeStatus:
        return {
            RunStatus.COMPLETED: OutcomeStatus.SUCCEEDED,
            RunStatus.FAILED: OutcomeStatus.FAILED,
            RunStatus.CANCELLED: OutcomeStatus.CANCELLED,
        }[status]

    async def _consume_admission_failure_tx(
        self, db: aiosqlite.Connection, fence: AdmissionLaunchUnknownFence,
    ) -> None:
        run = await self._continuation_run_tx(db, fence.run_id)
        record, boundary = await self._admission_tx(db, fence.run_id)
        identity = (boundary.decision_id, boundary.launch_operation_id)
        if boundary.phase is AdmissionPhase.LAUNCH_UNKNOWN and record.version == fence.expected_boundary_version + 1:
            if identity == (fence.decision_id, fence.launch_operation_id):
                return
        if boundary.phase not in {AdmissionPhase.LAUNCH_CLAIMED, AdmissionPhase.LAUNCHED} or record.version != fence.expected_boundary_version or identity != (fence.decision_id, fence.launch_operation_id):
            raise VersionConflict("stale_admission_version", "launch-unknown fence differs from continuation")
        updated = self._advance_admission(boundary, AdmissionPhase.LAUNCH_UNKNOWN,
            record.version + 1)
        await self._save_continuation_tx(db, run=run, expected_version=record.version,
            payload_json=self._continuation_payload_json(self._with_admission(record.payload, updated)),
            decision=None, now=float(self._clock()))

    async def commit_run_outcome(
        self,
        run_id: str,
        *,
        expected_version: int,
        terminal_status: RunStatus | None = None,
        event: RunEventCandidate,
        deliveries: Sequence[DeliverySpec] = (),
        parent_signal_operation_id: str | None = None,
        parent_signal_value: Any = None,
        recovery_lease: RecoveryLease | None = None,
        cancel_reason: str | None = None,
        admission_failure: AdmissionLaunchUnknownFence | None = None,
        terminal_commit_extensions: Sequence[Any] = (),
        delivery_fence: Mapping[str, Any] | None = None,
        release_receipt_kind: str | None = None,
    ) -> FinalizeRunResult | RunRecord:
        if terminal_status is None:
            reason = str(cancel_reason or "").strip()
            if not reason or event.status != OutcomeStatus.CANCEL_REQUESTED:
                raise TerminalConflict(
                    "invalid_cancel_request",
                    "cancel requires a reason and cancel_requested event",
                )
            async with self._write_transaction() as db:
                run = await (await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (run_id,)
                )).fetchone()
                if run is None:
                    raise RunNotFound(
                        "run_not_found", f"execution run does not exist: {run_id}"
                    )
                if str(run["status"]) == RunStatus.CANCEL_REQUESTED.value:
                    existing = await (await db.execute(
                        "SELECT * FROM execution_events WHERE run_id=? AND event_key=?",
                        (run_id, event.event_key),
                    )).fetchone()
                    if existing is None or str(run["cancel_reason"] or "") != reason:
                        raise IdempotencyConflict(
                            "cancel_intent_conflict",
                            "another cancel intent already owns the run",
                        )
                    self._assert_event_matches(existing, run, event)
                    await self._assert_delivery_set_tx(
                        db, str(existing["event_id"]), deliveries
                    )
                    await db.commit()
                    return self._row_to_record(run)
                _, updated, idempotent = await self._append_event_tx(
                    db, run, expected_version=expected_version,
                    event=event, deliveries=deliveries,
                )
                if idempotent:
                    if (str(updated["status"]) != RunStatus.CANCEL_REQUESTED.value
                            or str(updated["cancel_reason"] or "") != reason):
                        raise IdempotencyConflict(
                            "cancel_intent_conflict",
                            "cancel event exists without the same coarse cancel intent",
                        )
                else:
                    cursor = await db.execute(
                        """UPDATE execution_runs SET status='cancel_requested',cancel_reason=?,
                        updated_at=? WHERE run_id=? AND version=?
                        AND terminal_event_id IS NULL""",
                        (reason, self._clock(), run_id, int(updated["version"])),
                    )
                    if cursor.rowcount != 1:
                        raise VersionConflict(
                            "stale_run_version", "run changed before cancel commit"
                        )
                    await db.execute(
                        """UPDATE workflow_runs SET status='cancel_requested',cancel_reason=?,
                        lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,
                        lease_epoch=lease_epoch+1,run_version=run_version+1,updated_at=?
                        WHERE run_id=? AND status NOT IN ('completed','failed','cancelled')""",
                        (reason, self._clock(), run_id),
                    )
                self._fault("cancel_before_commit")
                result = await (await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (run_id,)
                )).fetchone()
                assert result is not None
                await db.commit()
                return self._row_to_record(result)
        terminal_status = RunStatus(terminal_status)
        if terminal_status not in TERMINAL_RUN_STATUSES:
            raise TerminalConflict(
                "non_terminal_finalize", "finalize requires a terminal RunStatus"
            )
        if event.status != self._terminal_outcome(terminal_status):
            raise TerminalConflict(
                "terminal_outcome_mismatch", "terminal event outcome differs from run status"
            )
        if admission_failure is not None and (
            terminal_status is not RunStatus.FAILED or event.kind != "run.final"
            or event.payload.get("error_code") != "launch_outcome_unknown"
            or event.payload.get("retry_safe") is not False
            or event.payload.get("launch_operation_id") != admission_failure.launch_operation_id
            or admission_failure.run_id != run_id
        ):
            raise TerminalConflict("invalid_launch_unknown_outcome", "invalid launch-unknown final payload")
        async with self._write_transaction() as db:
            run = await (
                await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if run is None:
                raise RunNotFound("run_not_found", f"execution run does not exist: {run_id}")
            if run["parent_run_id"] is not None and any(item.sink_kind in {
                "goal_projection", "session_projection"} for item in deliveries):
                raise RunIdentityConflict("child_terminal_projection",
                                          "child runs cannot project the root terminal")
            child_command = await (
                await db.execute(
                    "SELECT operation_id FROM execution_child_commands WHERE child_run_id=?",
                    (run_id,),
                )
            ).fetchone()
            if child_command is not None:
                operation_id = str(child_command["operation_id"])
                if parent_signal_operation_id != operation_id:
                    raise RunIdentityConflict(
                        "child_terminal_requires_parent_signal",
                        "child runs must use atomic terminal-to-parent signal finalization",
                    )
            if recovery_lease is not None:
                await self._assert_recovery_fence_tx(db, recovery_lease, run_id=run_id)
            if event.driver_kind != str(run["driver_kind"]):
                raise RunIdentityConflict(
                    "driver_event_conflict", "terminal event driver differs from run owner"
                )
            event_id = stable_event_id(run_id, event.event_key)
            if str(run["status"]) in {status.value for status in TERMINAL_RUN_STATUSES}:
                existing = await (
                    await db.execute(
                        "SELECT * FROM execution_events WHERE event_id=?", (event_id,)
                    )
                ).fetchone()
                if (
                    str(run["status"]) != terminal_status.value
                    or str(run["terminal_event_id"] or "") != event_id
                    or existing is None
                ):
                    raise TerminalConflict(
                        "terminal_conflict", "another terminal intent already won"
                    )
                try:
                    self._assert_event_matches(existing, run, event)
                except IdempotencyConflict as exc:
                    # A terminal event key is deliberately stable per run.  If
                    # two terminal candidates race, the second candidate must
                    # observe the already-settled winner instead of looking
                    # like a retry that changed its payload.
                    raise TerminalConflict(
                        "terminal_conflict", "another terminal intent already won"
                    ) from exc
                await self._assert_delivery_set_tx(db, event_id, deliveries)
                transaction = self.bind(db)
                for extension in terminal_commit_extensions:
                    verify = getattr(extension, "verify_terminal_replay", None)
                    if not callable(verify):
                        raise TerminalConflict(
                            "terminal_extension_replay_unverifiable",
                            "terminal extension cannot verify an idempotent replay",
                        )
                    verified = verify(
                        transaction,
                        record=self._row_to_record(run),
                        terminal_event=event,
                    )
                    if inspect.isawaitable(verified):
                        await verified
                if admission_failure is not None:
                    await self._consume_admission_failure_tx(db, admission_failure)
                hydrated = dict(existing)
                hydrated["root_run_id"] = run["root_run_id"]
                hydrated["session_id"] = run["session_id"]
                if parent_signal_operation_id is not None:
                    await self._enqueue_child_terminal_signal_tx(
                        db,
                        parent_signal_operation_id,
                        terminal_status=terminal_status.value,
                        value=parent_signal_value,
                    )
                await db.commit()
                return FinalizeRunResult(
                    record=self._row_to_record(run),
                    event=self._row_to_event(hydrated),
                    idempotent=True,
                )
            if int(run["version"]) != expected_version:
                raise VersionConflict(
                    "stale_run_version",
                    f"expected run version {expected_version}, found {run['version']}",
                )
            now = float(self._clock())
            durable_seq = int(run["durable_seq"]) + 1
            payload = self._event_json(event)
            extension_receipts: list[Mapping[str, Any]] = []
            transaction = self.bind(db)
            for extension in terminal_commit_extensions:
                apply = getattr(extension, "apply_terminal_commit", None)
                if not callable(apply):
                    raise TypeError(
                        "terminal commit extension has no apply_terminal_commit"
                    )
                receipt = apply(
                    transaction,
                    record=self._row_to_record(run),
                    terminal_event=event,
                )
                if inspect.isawaitable(receipt):
                    receipt = await receipt
                if hasattr(receipt, "to_dict"):
                    receipt = receipt.to_dict()
                if not isinstance(receipt, Mapping) or set(receipt) != {
                    "kind",
                    "ref",
                    "content_hash",
                }:
                    raise TypeError(
                        "terminal extension must return kind/ref/content_hash"
                    )
                content_hash = str(receipt["content_hash"])
                if len(content_hash) != 64 or any(
                    ch not in "0123456789abcdef" for ch in content_hash
                ):
                    raise TypeError(
                        "terminal extension receipt hash must be lowercase SHA-256"
                    )
                extension_receipts.append(dict(receipt))
            release_receipt = None
            if release_receipt_kind is not None:
                matches = [
                    receipt
                    for receipt in extension_receipts
                    if str(receipt["kind"]) == release_receipt_kind
                ]
                if len(matches) != 1:
                    raise TerminalConflict(
                        "terminal_release_receipt_missing",
                        "terminal delivery requires exactly one release receipt",
                    )
                release_receipt = matches[0]
            if delivery_fence is not None and release_receipt_kind is None:
                raise TerminalConflict(
                    "terminal_release_receipt_missing",
                    "fenced terminal deliveries require a release receipt kind",
                )
            await db.execute(
                """INSERT INTO execution_events(
                event_id,schema_version,event_key,run_id,durable_seq,kind,status,driver_kind,
                correlation_json,payload_json,error_json,artifact_refs_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    event.schema_version,
                    event.event_key,
                    run_id,
                    durable_seq,
                    event.kind,
                    event.status.value,
                    event.driver_kind,
                    payload["correlation_json"],
                    payload["payload_json"],
                    payload["error_json"],
                    payload["artifact_refs_json"],
                    now,
                ),
            )
            for receipt_order, receipt in enumerate(extension_receipts):
                await db.execute(
                    """INSERT INTO execution_terminal_extension_receipts(
                    run_id,event_id,receipt_order,kind,ref,content_hash,created_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        run_id,
                        event_id,
                        receipt_order,
                        str(receipt["kind"]),
                        str(receipt["ref"]),
                        str(receipt["content_hash"]),
                        now,
                    ),
                )
            await self._insert_deliveries_tx(
                db,
                run_id=run_id,
                event_id=event_id,
                deliveries=deliveries,
                now=now,
                delivery_fence=delivery_fence,
                release_receipt=release_receipt,
            )
            self._fault("finalize_after_outbox")
            if admission_failure is not None:
                await self._consume_admission_failure_tx(db, admission_failure)
            cursor = await db.execute(
                """UPDATE execution_runs SET status=?,terminal_event_id=?,durable_seq=?,
                version=version+1,updated_at=?,ended_at=?
                WHERE run_id=? AND version=? AND terminal_event_id IS NULL
                  AND status NOT IN ('completed','failed','cancelled')""",
                (
                    terminal_status.value,
                    event_id,
                    durable_seq,
                    now,
                    now,
                    run_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise TerminalConflict(
                    "terminal_conflict", "another terminal intent already won"
                )
            workflow = await (
                await db.execute("SELECT status FROM workflow_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if workflow is not None and str(workflow["status"]) not in {
                "completed",
                "failed",
                "cancelled",
            }:
                await db.execute(
                    """UPDATE workflow_runs SET status=?,lease_owner=NULL,
                    lease_expires_at=NULL,heartbeat_at=NULL,run_version=run_version+1,
                    updated_at=?,ended_at=COALESCE(ended_at,?) WHERE run_id=?""",
                    (terminal_status.value, now, now, run_id),
                )
            if parent_signal_operation_id is not None:
                self._fault("child_finalize_after_terminal")
                await self._enqueue_child_terminal_signal_tx(
                    db,
                    parent_signal_operation_id,
                    terminal_status=terminal_status.value,
                    value=parent_signal_value,
                )
                self._fault("child_finalize_after_parent_signal")
                self._fault("child_terminal_before_commit")
            self._fault("finalize_before_commit")
            updated = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (run_id,)
                )
            ).fetchone()
            stored = await (
                await db.execute(
                    "SELECT * FROM execution_events WHERE event_id=?", (event_id,)
                )
            ).fetchone()
            assert updated is not None and stored is not None
            await db.commit()
            hydrated = dict(stored)
            hydrated["root_run_id"] = updated["root_run_id"]
            hydrated["session_id"] = updated["session_id"]
            return FinalizeRunResult(
                record=self._row_to_record(updated),
                event=self._row_to_event(hydrated),
                idempotent=False,
            )

    async def start_blocked_root(
        self,
        spec: RunCreate,
        start_snapshot: RunStartSnapshotRecord,
        *,
        event: RunEventCandidate,
        block_reporter: Any,
        start_commit_extensions: Sequence[Any] = (),
        deliveries: Sequence[DeliverySpec] = (),
    ) -> FinalizeRunResult:
        """Atomically create and fail a preflight-blocked durable Root.

        Provider/workspace/capability preflight can fail before the ordinary
        driver starts.  Persisting the minimum Root, immutable start evidence,
        failed final and structured block sidecar in one transaction makes that
        outcome visible without inventing a provider invocation.
        """

        if spec.persistence_level is not PersistenceLevel.DURABLE:
            raise PersistenceRequired(
                "durable_create_required",
                "a blocked preflight Root requires durable persistence",
            )
        if spec.context.parent_run_id is not None or spec.context.root_run_id != spec.run_id:
            raise RunIdentityConflict(
                "blocked_preflight_requires_root",
                "blocked preflight may only create a Root",
            )
        if start_snapshot.run_id != spec.run_id:
            raise IdempotencyConflict(
                "run_start_scope_conflict",
                "start snapshot belongs to another Run",
            )
        if event.status is not OutcomeStatus.FAILED or not event.is_terminal:
            raise TerminalConflict(
                "blocked_preflight_requires_failed_final",
                "blocked preflight requires a failed final event",
            )
        if event.driver_kind != spec.driver_kind:
            raise RunIdentityConflict(
                "driver_event_conflict",
                "terminal event driver differs from run owner",
            )
        async with self._write_transaction() as db:
            run, created = await self._insert_run_tx(db, spec, version=0)
            await self._ensure_start_snapshot_tx(
                db,
                spec=spec,
                start_snapshot=start_snapshot,
                run_created=created,
                start_commit_extensions=start_commit_extensions,
            )
            event_id = stable_event_id(spec.run_id, event.event_key)
            transaction = self.bind(db)
            if not created:
                if (
                    str(run["status"]) != RunStatus.FAILED.value
                    or str(run["terminal_event_id"] or "") != event_id
                ):
                    raise IdempotencyConflict(
                        "blocked_preflight_conflict",
                        "Root identity is already owned by another outcome",
                    )
                existing = await (
                    await db.execute(
                        "SELECT * FROM execution_events WHERE event_id=?", (event_id,)
                    )
                ).fetchone()
                if existing is None:
                    raise IdempotencyConflict(
                        "blocked_preflight_event_missing",
                        "blocked preflight terminal event is missing",
                    )
                self._assert_event_matches(existing, run, event)
                await self._assert_delivery_set_tx(db, event_id, deliveries)
                verified = block_reporter.verify_terminal_replay(
                    transaction,
                    record=self._row_to_record(run),
                    terminal_event=event,
                )
                if inspect.isawaitable(verified):
                    await verified
                hydrated = dict(existing)
                hydrated["root_run_id"] = run["root_run_id"]
                hydrated["session_id"] = run["session_id"]
                await db.commit()
                return FinalizeRunResult(
                    record=self._row_to_record(run),
                    event=self._row_to_event(hydrated),
                    idempotent=True,
                )

            receipt = block_reporter.apply_terminal_commit(
                transaction,
                record=self._row_to_record(run),
                terminal_event=event,
            )
            if inspect.isawaitable(receipt):
                receipt = await receipt
            if hasattr(receipt, "to_dict"):
                receipt = receipt.to_dict()
            if not isinstance(receipt, Mapping) or set(receipt) != {
                "kind",
                "ref",
                "content_hash",
            }:
                raise TypeError("block reporter returned an invalid terminal receipt")
            content_hash = str(receipt["content_hash"])
            if len(content_hash) != 64 or any(
                ch not in "0123456789abcdef" for ch in content_hash
            ):
                raise TypeError("block reporter receipt hash must be lowercase SHA-256")
            now = float(self._clock())
            durable_seq = int(run["durable_seq"]) + 1
            payload = self._event_json(event)
            await db.execute(
                """INSERT INTO execution_events(
                event_id,schema_version,event_key,run_id,durable_seq,kind,status,driver_kind,
                correlation_json,payload_json,error_json,artifact_refs_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    event.schema_version,
                    event.event_key,
                    spec.run_id,
                    durable_seq,
                    event.kind,
                    event.status.value,
                    event.driver_kind,
                    payload["correlation_json"],
                    payload["payload_json"],
                    payload["error_json"],
                    payload["artifact_refs_json"],
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO execution_terminal_extension_receipts(
                run_id,event_id,receipt_order,kind,ref,content_hash,created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    spec.run_id,
                    event_id,
                    0,
                    str(receipt["kind"]),
                    str(receipt["ref"]),
                    content_hash,
                    now,
                ),
            )
            await self._insert_deliveries_tx(
                db,
                run_id=spec.run_id,
                event_id=event_id,
                deliveries=deliveries,
                now=now,
            )
            cursor = await db.execute(
                """UPDATE execution_runs SET status='failed',terminal_event_id=?,
                durable_seq=?,version=version+1,updated_at=?,ended_at=?
                WHERE run_id=? AND version=0 AND terminal_event_id IS NULL""",
                (event_id, durable_seq, now, now, spec.run_id),
            )
            if cursor.rowcount != 1:
                raise TerminalConflict(
                    "terminal_conflict", "another terminal intent already won"
                )
            updated = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (spec.run_id,)
                )
            ).fetchone()
            stored = await (
                await db.execute(
                    "SELECT * FROM execution_events WHERE event_id=?", (event_id,)
                )
            ).fetchone()
            assert updated is not None and stored is not None
            await db.commit()
            hydrated = dict(stored)
            hydrated["root_run_id"] = updated["root_run_id"]
            hydrated["session_id"] = updated["session_id"]
            return FinalizeRunResult(
                record=self._row_to_record(updated),
                event=self._row_to_event(hydrated),
                idempotent=False,
            )

    async def finalize_child_and_enqueue_parent_signal(
        self,
        operation_id: str,
        *,
        expected_version: int,
        terminal_status: RunStatus,
        event: RunEventCandidate,
        value: Any = None,
        deliveries: Sequence[DeliverySpec] = (),
        recovery_lease: RecoveryLease | None = None,
        terminal_commit_extensions: Sequence[Any] = (),
    ) -> FinalizeRunResult:
        """Finalize the acknowledged child and enqueue its parent signal atomically."""

        command = await self.get_child_command(operation_id)
        if command is None:
            raise RunNotFound("child_command_not_found", "child command does not exist")
        result = await self.commit_run_outcome(
            command.child_run_id,
            expected_version=expected_version,
            terminal_status=terminal_status,
            event=event,
            deliveries=deliveries,
            parent_signal_operation_id=operation_id,
            parent_signal_value=value,
            recovery_lease=recovery_lease,
            terminal_commit_extensions=terminal_commit_extensions,
        )
        assert isinstance(result, FinalizeRunResult)
        return result

    async def _commit_child_command_tx(
        self,
        db: aiosqlite.Connection,
        intent: ChildCommandIntent,
        *,
        recovery_owner: bool = False,
        transaction_hook: Any | None = None,
    ) -> aiosqlite.Row:
        parent = await (
            await db.execute(
                "SELECT * FROM execution_runs WHERE run_id=?",
                (intent.parent_run_id,),
            )
        ).fetchone()
        if parent is None:
            raise RunNotFound(
                "parent_not_found", "delegate parent must already be durable"
            )
        existing = await (
            await db.execute(
                """SELECT * FROM execution_child_commands
                WHERE operation_id=? OR (parent_run_id=? AND command_id=?)
                OR child_run_id=? ORDER BY operation_id=? DESC LIMIT 1""",
                (
                    intent.operation_id,
                    intent.parent_run_id,
                    intent.command_id,
                    intent.child_run_id,
                    intent.operation_id,
                ),
            )
        ).fetchone()
        owner = (
            await self._required_recovery_owner_tx(db)
            if existing is not None or recovery_owner
            else await self._required_start_owner_tx(db)
        )
        self._assert_run_owner(parent, owner)
        if existing is not None:
            if (
                str(existing["operation_id"]) != intent.operation_id
                or str(existing["intent_fingerprint"]) != intent.intent_fingerprint
            ):
                raise IdempotencyConflict(
                    "child_operation_conflict",
                    "operation, parent command, or child id names another intent",
                )
            row = existing
        else:
            now = float(self._clock())
            await db.execute(
                """INSERT INTO execution_child_commands(
                operation_id,schema_version,parent_run_id,command_id,child_run_id,
                profile_key,join_policy,capability_snapshot_ref,
                capability_subset_json,child_request_json,child_spec_json,
                intent_fingerprint,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'pending',?,?)""",
                (
                    intent.operation_id,
                    intent.schema_version,
                    intent.parent_run_id,
                    intent.command_id,
                    intent.child_run_id,
                    intent.child_spec.profile_key,
                    intent.attachment_policy.value,
                    intent.capability_snapshot_ref,
                    canonical_json(list(intent.capability_subset)),
                    canonical_json(thaw_json(intent.child_request)),
                    canonical_json(intent.child_spec.to_dict()),
                    intent.intent_fingerprint,
                    now,
                    now,
                ),
            )
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (intent.operation_id,),
                )
            ).fetchone()
            assert row is not None
        if transaction_hook is not None:
            hooked = transaction_hook(db, intent)
            if inspect.isawaitable(hooked):
                await hooked
        return row

    async def commit_child_command(
        self,
        intent: ChildCommandIntent,
        *,
        start_snapshot: RunStartSnapshotRecord | None = None,
        start_commit_extensions: Sequence[Any] = (),
        recovery_lease: RecoveryLease | None = None,
        transaction_hook: Any | None = None,
    ) -> ChildCommandRecord:
        """Commit the complete delegate intent before a child row can exist."""

        if start_snapshot is not None and start_snapshot.run_id != intent.child_run_id:
            raise IdempotencyConflict(
                "child_start_scope_conflict",
                "child start snapshot is bound to another Run",
            )
        async with self._write_transaction() as db:
            await self._assert_optional_recovery_fence_tx(db, recovery_lease, intent.parent_run_id)
            expected_owner = None
            if start_snapshot is not None:
                expected_owner = (
                    await self._required_recovery_owner_tx(db)
                    if recovery_lease is not None
                    else await self._required_start_owner_tx(db)
                )
            row = await self._commit_child_command_tx(
                db,
                intent,
                recovery_owner=recovery_lease is not None,
                transaction_hook=(
                    transaction_hook if start_snapshot is None else None
                ),
            )
            if start_snapshot is not None:
                _, child_created = await self._insert_run_for_owner_tx(
                    db,
                    intent.child_spec,
                    version=0,
                    expected_owner=expected_owner,
                )
                await self._ensure_start_snapshot_tx(
                    db,
                    spec=intent.child_spec,
                    start_snapshot=start_snapshot,
                    run_created=child_created,
                    start_commit_extensions=start_commit_extensions,
                )
                await self._ensure_child_run_link_tx(
                    db, intent, expected_owner
                )
                self._fault("child_precreate_after_start_snapshot")
                if transaction_hook is not None:
                    hooked = transaction_hook(db, intent)
                    if inspect.isawaitable(hooked):
                        await hooked
                self._fault("child_precreate_before_commit")
            self._fault("child_command_before_commit")
            return self._row_to_child_command(row)

    async def commit_child_command_and_precreate_child(
        self,
        intent: ChildCommandIntent,
        start_snapshot: RunStartSnapshotRecord,
        *,
        start_commit_extensions: Sequence[Any] = (),
        recovery_lease: RecoveryLease | None = None,
        transaction_hook: Any | None = None,
    ) -> ChildCommandRecord:
        """Atomically freeze a child command, Run, start snapshot and lease clone."""

        return await self.commit_child_command(
            intent,
            start_snapshot=start_snapshot,
            start_commit_extensions=start_commit_extensions,
            recovery_lease=recovery_lease,
            transaction_hook=transaction_hook,
        )

    async def get_child_command(self, operation_id: str) -> ChildCommandRecord | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            return self._row_to_child_command(row) if row is not None else None

    async def get_child_command_for_run(
        self, child_run_id: str
    ) -> ChildCommandRecord | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE child_run_id=?",
                    (child_run_id,),
                )
            ).fetchone()
            return self._row_to_child_command(row) if row is not None else None

    async def lease_child_commands(
        self,
        *,
        owner: str,
        limit: int,
        lease_seconds: float,
        parent_run_id: str | None = None,
        recovery_lease: RecoveryLease | None = None,
    ) -> tuple[ChildCommandRecord, ...]:
        if not owner or limit <= 0 or lease_seconds <= 0:
            raise ValueError("owner, positive limit and positive lease_seconds are required")
        async with self._write_transaction() as db:
            owner_kind, owner_generation = await self._required_recovery_owner_tx(db)
            if recovery_lease is not None:
                if parent_run_id != recovery_lease.run_id:
                    raise StaleRecoveryLease(
                        "recovery child scan must be scoped to its parent run"
                    )
                await self._assert_recovery_fence_tx(db, recovery_lease, run_id=parent_run_id)
            now = float(self._clock())
            rows = await (
                await db.execute(
                    """SELECT operation_id FROM execution_child_commands
                    WHERE (
                        status='pending'
                        OR (
                            status IN ('leased','scheduled')
                            AND schedule_lease_expires_at<=?
                        )
                    ) AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                    AND (? IS NULL OR parent_run_id=?)
                    AND EXISTS(
                        SELECT 1 FROM execution_runs AS parent
                        WHERE parent.run_id=execution_child_commands.parent_run_id
                        AND parent.owner_kind=? AND parent.owner_generation=?
                    )
                    ORDER BY created_at,operation_id LIMIT ?""",
                    (
                        now, now, parent_run_id, parent_run_id,
                        owner_kind, owner_generation, limit,
                    ),
                )
            ).fetchall()
            leased: list[ChildCommandRecord] = []
            for selected in rows:
                operation_id = str(selected["operation_id"])
                cursor = await db.execute(
                    """UPDATE execution_child_commands
                    SET status='leased',schedule_lease_owner=?,
                        schedule_lease_epoch=schedule_lease_epoch+1,
                        schedule_lease_expires_at=?,attempts=attempts+1,updated_at=?
                    WHERE operation_id=? AND (
                        status='pending'
                        OR (status IN ('leased','scheduled') AND schedule_lease_expires_at<=?)
                    )""",
                    (owner, now + lease_seconds, now, operation_id, now),
                )
                if cursor.rowcount != 1:
                    continue
                row = await (
                    await db.execute(
                        "SELECT * FROM execution_child_commands WHERE operation_id=?",
                        (operation_id,),
                    )
                ).fetchone()
                assert row is not None
                leased.append(self._row_to_child_command(row))
            self._fault("child_lease_before_commit")
            await db.commit()
            return tuple(leased)

    @staticmethod
    def _stable_child_link_id(operation_id: str) -> str:
        return hashlib.sha256(
            f"execution-child-link|{operation_id}".encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _stable_child_signal_id(operation_id: str, kind: str) -> str:
        return hashlib.sha256(
            f"execution-child-signal|{operation_id}|{kind}".encode("utf-8")
        ).hexdigest()

    async def _ensure_child_run_link_tx(
        self,
        db: aiosqlite.Connection,
        intent: ChildCommandIntent,
        expected_owner: tuple[str, int],
    ) -> None:
        child, child_created = await self._insert_run_for_owner_tx(
            db, intent.child_spec, version=0, expected_owner=expected_owner
        )
        link_id = self._stable_child_link_id(intent.operation_id)
        existing = await (
            await db.execute(
                "SELECT * FROM execution_run_links WHERE link_id=?", (link_id,)
            )
        ).fetchone()
        expected = {
            "root_run_id": intent.child_spec.context.root_run_id,
            "parent_run_id": intent.parent_run_id,
            "child_run_id": intent.child_run_id,
            "attachment_policy": intent.attachment_policy.value,
            "link_kind": LinkKind.STRUCTURAL.value,
            "domain_kind": "",
            "domain_id": "",
        }
        if existing is None:
            await db.execute(
                """INSERT INTO execution_run_links(
                link_id,schema_version,root_run_id,parent_run_id,child_run_id,
                attachment_policy,link_kind,domain_kind,domain_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    link_id, 1, expected["root_run_id"],
                    expected["parent_run_id"], expected["child_run_id"],
                    expected["attachment_policy"], expected["link_kind"],
                    "", "", self._clock(),
                ),
            )
            cursor = await db.execute(
                """UPDATE execution_runs SET version=version+1,updated_at=?
                WHERE run_id=? AND version=? AND terminal_event_id IS NULL""",
                (self._clock(), intent.child_run_id, int(child["version"])),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_run_version", "child changed before link commit"
                )
        elif any(str(existing[name]) != value for name, value in expected.items()):
            raise IdempotencyConflict(
                "child_link_conflict", "child link differs from committed command"
            )
        elif child_created:
            raise IdempotencyConflict(
                "child_link_without_run", "child link existed before its child run"
            )

    async def schedule_child_command(
        self,
        operation_id: str,
        *,
        lease_owner: str,
        lease_epoch: int,
        recovery_lease: RecoveryLease | None = None,
    ) -> ChildCommandRecord:
        """Atomically create/link the child and mark its command scheduled."""

        async with self._write_transaction() as db:
            command = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            if command is None:
                raise RunNotFound("child_command_not_found", "child command does not exist")
            parent_run_id = str(command["parent_run_id"])
            await self._assert_optional_recovery_fence_tx(
                db, recovery_lease, parent_run_id
            )
            expected_owner = await self._required_recovery_owner_tx(db)
            parent = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (parent_run_id,)
                )
            ).fetchone()
            if parent is None:
                raise RunNotFound("parent_not_found", "delegate parent does not exist")
            self._assert_run_owner(parent, expected_owner)
            now = float(self._clock())
            if (
                str(command["status"]) not in {"leased", "scheduled"}
                or str(command["schedule_lease_owner"] or "") != lease_owner
                or int(command["schedule_lease_epoch"]) != lease_epoch
                or command["schedule_lease_expires_at"] is None
                or float(command["schedule_lease_expires_at"]) <= now
            ):
                raise VersionConflict(
                    "stale_child_lease",
                    "child command lease owner, epoch, or expiry changed",
                )
            intent = self._row_to_child_command(command).intent
            await self._ensure_child_run_link_tx(db, intent, expected_owner)
            self._fault("child_schedule_after_run")
            cursor = await db.execute(
                """UPDATE execution_child_commands SET status='scheduled',updated_at=?
                WHERE operation_id=? AND schedule_lease_owner=?
                AND schedule_lease_epoch=? AND schedule_lease_expires_at>?
                AND status IN ('leased','scheduled')""",
                (now, operation_id, lease_owner, lease_epoch, now),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_child_lease", "child command changed before schedule commit"
                )
            self._fault("child_schedule_before_commit")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            assert row is not None
            await db.commit()
            return self._row_to_child_command(row)

    async def _enqueue_child_accepted_signal_tx(
        self, db: aiosqlite.Connection, command: Mapping[str, Any], *, now: float
    ) -> None:
        operation_id = str(command["operation_id"])
        await db.execute(
            """INSERT INTO execution_child_signal_inbox(
            signal_id,schema_version,operation_id,parent_run_id,command_id,
            child_run_id,kind,payload_json,created_at,updated_at
            ) VALUES(?,1,?,?,?,?, 'accepted',?, ?,?)
            ON CONFLICT(operation_id,kind) DO NOTHING""",
            (
                self._stable_child_signal_id(operation_id, "accepted"), operation_id,
                str(command["parent_run_id"]), str(command["command_id"]),
                str(command["child_run_id"]), canonical_json({"accepted": True}),
                now, now,
            ),
        )

    async def acknowledge_child_command(
        self,
        operation_id: str,
        *,
        lease_owner: str,
        lease_epoch: int,
        recovery_lease: RecoveryLease | None = None,
    ) -> ChildCommandRecord:
        async with self._write_transaction() as db:
            command = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            if command is None:
                raise RunNotFound("child_command_not_found", "child command does not exist")
            parent_run_id = str(command["parent_run_id"])
            await self._assert_optional_recovery_fence_tx(
                db, recovery_lease, parent_run_id
            )
            parent = await self._recovery_run_tx(db, parent_run_id)
            if str(command["status"]) == ChildCommandStatus.ACKED.value:
                await db.commit()
                return self._row_to_child_command(command)
            now = float(self._clock())
            if (
                str(command["status"]) != ChildCommandStatus.SCHEDULED.value
                or str(command["schedule_lease_owner"] or "") != lease_owner
                or int(command["schedule_lease_epoch"]) != lease_epoch
                or command["schedule_lease_expires_at"] is None
                or float(command["schedule_lease_expires_at"]) <= now
            ):
                raise VersionConflict(
                    "stale_child_lease", "only the scheduling lease may ack the child"
                )
            await self._enqueue_child_accepted_signal_tx(db, command, now=now)
            cursor = await db.execute(
                """UPDATE execution_child_commands
                SET status='acked',schedule_lease_owner=NULL,
                    schedule_lease_expires_at=NULL,ack_at=?,updated_at=?
                WHERE operation_id=? AND status='scheduled'
                AND schedule_lease_owner=? AND schedule_lease_epoch=?
                AND schedule_lease_expires_at>?""",
                (now, now, operation_id, lease_owner, lease_epoch, now),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_child_lease", "child command changed before ack commit"
                )
            self._fault("child_ack_before_commit")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            assert row is not None
            await db.commit()
            return self._row_to_child_command(row)

    async def _enqueue_child_terminal_signal_tx(
        self,
        db: aiosqlite.Connection,
        operation_id: str,
        *,
        terminal_status: str,
        value: Any = None,
    ) -> ChildSignalRecord:
        if terminal_status not in {"completed", "failed", "cancelled"}:
            raise ValueError("child terminal status is invalid")
        command = await (
            await db.execute(
                "SELECT * FROM execution_child_commands WHERE operation_id=?",
                (operation_id,),
            )
        ).fetchone()
        if command is None or str(command["status"]) not in {
            ChildCommandStatus.SCHEDULED.value,
            ChildCommandStatus.ACKED.value,
        }:
            raise RunNotFound(
                "scheduled_child_command_not_found",
                "terminal signal requires a scheduled child command",
            )
        child = await (
            await db.execute(
                "SELECT status FROM execution_runs WHERE run_id=?",
                (str(command["child_run_id"]),),
            )
        ).fetchone()
        if child is None or str(child["status"]) != terminal_status:
            raise RunIdentityConflict(
                "child_not_terminal",
                "terminal inbox status must match the authoritative child run",
            )
        await self._enqueue_child_accepted_signal_tx(
            db, command, now=float(self._clock())
        )
        signal_id = self._stable_child_signal_id(operation_id, "terminal")
        payload_json = canonical_json({"status": terminal_status, "value": value})
        existing = await (
            await db.execute(
                """SELECT * FROM execution_child_signal_inbox
                WHERE operation_id=? AND kind='terminal'""",
                (operation_id,),
            )
        ).fetchone()
        if existing is not None:
            if str(existing["payload_json"]) != payload_json:
                raise IdempotencyConflict(
                    "child_terminal_conflict",
                    "terminal signal replay differs from the first outcome",
                )
            return self._row_to_child_signal(existing)
        now = float(self._clock())
        await db.execute(
            """INSERT INTO execution_child_signal_inbox(
            signal_id,schema_version,operation_id,parent_run_id,command_id,
            child_run_id,kind,payload_json,created_at,updated_at
            ) VALUES(?,1,?,?,?,?, 'terminal',?,?,?)""",
            (
                signal_id,
                operation_id,
                str(command["parent_run_id"]),
                str(command["command_id"]),
                str(command["child_run_id"]),
                payload_json,
                now,
                now,
            ),
        )
        row = await (
            await db.execute(
                "SELECT * FROM execution_child_signal_inbox WHERE signal_id=?",
                (signal_id,),
            )
        ).fetchone()
        assert row is not None
        return self._row_to_child_signal(row)

    async def list_pending_child_signals(
        self, parent_run_id: str, *, limit: int = 16,
        recovery_lease: RecoveryLease | None = None,
    ) -> tuple[ChildSignalRecord, ...]:
        if isinstance(limit, bool) or limit < 1:
            raise ValueError("pending child signal limit must be positive")
        async with self._write_transaction() as db:
            await self._assert_optional_recovery_fence_tx(db, recovery_lease, parent_run_id)
            await self._recovery_run_tx(db, parent_run_id)
            now = float(self._clock())
            await db.execute(
                """UPDATE execution_child_signal_inbox
                SET attempts=attempts+1,updated_at=? WHERE signal_id IN (
                  SELECT signal_id FROM execution_child_signal_inbox
                  WHERE parent_run_id=? AND delivered_at IS NULL
                  ORDER BY created_at,
                  CASE kind WHEN 'accepted' THEN 0 ELSE 1 END,signal_id LIMIT ?
                )""",
                (now, parent_run_id, limit),
            )
            rows = await (
                await db.execute(
                    """SELECT * FROM execution_child_signal_inbox
                    WHERE parent_run_id=? AND delivered_at IS NULL
                    ORDER BY created_at,
                    CASE kind WHEN 'accepted' THEN 0 ELSE 1 END,signal_id LIMIT ?""",
                    (parent_run_id, limit),
                )
            ).fetchall()
            await db.commit()
            return tuple(self._row_to_child_signal(row) for row in rows)

    async def list_pending_child_signal_parents(
        self, *, limit: int = 10_000
    ) -> tuple[RunRecord, ...]:
        if isinstance(limit, bool) or limit < 1:
            raise ValueError("pending child parent limit must be positive")
        async with self._read_connection() as db:
            owner_kind, owner_generation = await self._required_recovery_owner_tx(db)
            rows = await (
                await db.execute(
                    """SELECT run.* FROM execution_runs AS run
                    WHERE EXISTS(
                      SELECT 1 FROM execution_child_signal_inbox AS signal
                      WHERE signal.parent_run_id=run.run_id
                      AND signal.delivered_at IS NULL
                    ) AND run.owner_kind=? AND run.owner_generation=?
                    ORDER BY run.created_at,run.run_id LIMIT ?""",
                    (owner_kind, owner_generation, limit),
                )
            ).fetchall()
            return tuple(self._row_to_record(row) for row in rows)

    async def _acknowledge_child_signal_tx(
        self, db: aiosqlite.Connection, signal_id: str, *, now: float
    ) -> ChildSignalRecord:
        row = await (
            await db.execute(
                "SELECT * FROM execution_child_signal_inbox WHERE signal_id=?",
                (signal_id,),
            )
        ).fetchone()
        if row is None:
            raise RunNotFound("child_signal_not_found", "child signal does not exist")
        if row["delivered_at"] is None:
            await db.execute(
                """UPDATE execution_child_signal_inbox
                SET delivered_at=?,updated_at=? WHERE signal_id=?
                AND delivered_at IS NULL""",
                (now, now, signal_id),
            )
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_signal_inbox WHERE signal_id=?",
                    (signal_id,),
                )
            ).fetchone()
            assert row is not None
        return self._row_to_child_signal(row)

    async def prepare_workflow_child_resume(
        self, signal_id: str, *, recovery_lease: RecoveryLease
    ) -> DecisionRecord:
        """Bind a pending child signal to the Native interrupt that will consume it."""

        async with self._write_transaction() as db:
            signal = await (
                await db.execute(
                    """SELECT signal.*,parent.driver_kind
                    FROM execution_child_signal_inbox AS signal
                    JOIN execution_runs AS parent ON parent.run_id=signal.parent_run_id
                    WHERE signal.signal_id=?""",
                    (signal_id,),
                )
            ).fetchone()
            if signal is None:
                raise RunNotFound("child_signal_not_found", "child signal does not exist")
            parent_run_id = str(signal["parent_run_id"])
            await self._assert_recovery_fence_tx(
                db, recovery_lease, run_id=parent_run_id
            )
            if str(signal["driver_kind"]) != "workflow":
                raise RunIdentityConflict(
                    "workflow_child_signal_owner_required",
                    "only a workflow parent may prepare a Native child resume",
                )
            decisions = await (
                await db.execute(
                    """SELECT * FROM execution_decisions WHERE run_id=?
                    AND kind='workflow_hitl' AND consumed_at IS NULL
                    AND status IN ('open','allowed') ORDER BY created_at,decision_id""",
                    (parent_run_id,),
                )
            ).fetchall()
            matching = []
            for row in decisions:
                envelope = json.loads(str(row["prompt_json"]))
                prompt = envelope.get("prompt", envelope)
                if (
                    isinstance(prompt, Mapping)
                    and prompt.get("kind") == "child_run"
                    and str(prompt.get("command_id") or "") == str(signal["command_id"])
                ):
                    matching.append(row)
            if len(matching) != 1:
                raise DecisionConflict(
                    "workflow_child_interrupt_not_found",
                    "child signal requires exactly one matching Native child interrupt",
                )
            row = matching[0]
            payload = {
                "kind": f"child_{signal['kind']}",
                "signal_id": signal_id,
                "command_id": str(signal["command_id"]),
                "child_run_id": str(signal["child_run_id"]),
            }
            if str(signal["kind"]) == "terminal":
                payload.update(json.loads(str(signal["payload_json"])))
            response_json = canonical_json({"child_signal": payload})
            if str(row["status"]) == "open":
                cursor = await db.execute(
                    """UPDATE execution_decisions SET status='allowed',
                    response_schema_version=1,response_json=?,
                    decision_version=decision_version+1,resolved_at=?
                    WHERE decision_id=? AND status='open' AND consumed_at IS NULL""",
                    (response_json, float(self._clock()), str(row["decision_id"])),
                )
                if cursor.rowcount != 1:
                    raise DecisionConflict(
                        "stale_decision_version", "workflow child interrupt changed"
                    )
            elif str(row["response_json"] or "") != response_json:
                raise DecisionConflict(
                    "workflow_child_signal_conflict",
                    "workflow child interrupt is bound to another response",
                )
            resolved = await (
                await db.execute(
                    "SELECT * FROM execution_decisions WHERE decision_id=?",
                    (str(row["decision_id"]),),
                )
            ).fetchone()
            assert resolved is not None
            await db.commit()
            return self._row_to_decision(resolved)

    async def ack_child_signal(
        self,
        signal_id: str,
        *,
        expected_continuation_version: int | None = None,
        continuation_payload: Mapping[str, Any] | None = None,
        event: RunEventCandidate | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        recovery_lease: RecoveryLease | None = None,
        transaction_hook: Any | None = None,
    ) -> ChildSignalRecord | tuple[ChildSignalRecord, ContinuationRecord, RunEvent]:
        """Discard a detached terminal signal or apply and ack a live boundary."""

        applies_boundary = continuation_payload is not None or event is not None
        if not applies_boundary:
            if transaction_hook is not None:
                raise ValueError(
                    "child signal transaction hook requires a boundary apply"
                )
            async with self._write_transaction() as db:
                row = await (await db.execute(
                    """SELECT parent.status AS parent_status,parent.cancel_reason,
                    parent.owner_kind,parent.owner_generation,command.join_policy
                    FROM execution_child_signal_inbox AS signal
                    JOIN execution_child_commands AS command
                      ON command.operation_id=signal.operation_id
                    JOIN execution_runs AS parent ON parent.run_id=signal.parent_run_id
                    WHERE signal.signal_id=?""", (signal_id,)
                )).fetchone()
                if row is None:
                    raise RunNotFound(
                        "child_signal_not_found", "child signal does not exist"
                    )
                owner = await self._required_recovery_owner_tx(db)
                if (str(row["owner_kind"]), int(row["owner_generation"])) != owner:
                    raise RuntimeActivationError(
                        "child_signal_owner_fenced",
                        "parent belongs to another runtime owner",
                    )
                parent_status = RunStatus(str(row["parent_status"]))
                cancelling_parent = (
                    parent_status is RunStatus.CANCEL_REQUESTED
                    or (
                        parent_status is RunStatus.CANCELLED
                        and row["cancel_reason"] is not None
                    )
                )
                if (
                    parent_status not in TERMINAL_RUN_STATUSES
                    and not cancelling_parent
                ):
                    raise RunIdentityConflict(
                        "parent_not_terminal",
                        "only a terminal parent may discard a child signal",
                    )
                if (
                    str(row["join_policy"]) != AttachmentPolicy.DETACHED.value
                    and not cancelling_parent
                ):
                    raise RunIdentityConflict(
                        "attached_child_signal_after_parent_terminal",
                        "attached child signal cannot be discarded after parent terminal",
                    )
                record = await self._acknowledge_child_signal_tx(
                    db, signal_id, now=float(self._clock())
                )
                await db.commit()
                return record
        if expected_continuation_version is None or continuation_payload is None or event is None:
            raise ValueError("child boundary requires version, payload and event")
        self._validate_continuation_version(expected_continuation_version)
        payload_json = self._continuation_payload_json(continuation_payload)
        applied_event = replace(
            event,
            payload=thaw_json(event.payload),
            error=None if event.error is None else thaw_json(event.error),
            correlation={
                **thaw_json(event.correlation),
                "child_signal_id": signal_id,
                "continuation_version": expected_continuation_version + 1,
                "continuation_payload_hash": fingerprint_json(continuation_payload),
            },
        )
        async with self._write_transaction() as db:
            inbox = await (
                await db.execute(
                    "SELECT * FROM execution_child_signal_inbox WHERE signal_id=?",
                    (signal_id,),
                )
            ).fetchone()
            if inbox is None:
                raise RunNotFound("child_signal_not_found", "child signal does not exist")
            parent_run_id = str(inbox["parent_run_id"])
            await self._assert_optional_recovery_fence_tx(db, recovery_lease, parent_run_id)
            run = await self._continuation_run_tx(db, parent_run_id)
            if inbox["delivered_at"] is not None:
                continuation_row = await (await db.execute(
                    "SELECT created_at FROM execution_continuations WHERE run_id=?",
                    (parent_run_id,),
                )).fetchone()
                if continuation_row is None:
                    raise IdempotencyConflict(
                        "child_signal_replay_conflict",
                        "duplicate child signal lost its parent continuation",
                    )
                stored_event, _, _ = await self._append_event_tx(
                    db,
                    run,
                    expected_version=int(run["version"]),
                    event=applied_event,
                    deliveries=deliveries,
                )
                await db.commit()
                continuation = ContinuationRecord(
                    run_id=parent_run_id, payload=dict(continuation_payload),
                    version=expected_continuation_version + 1, pending_decision_id=None,
                    created_at=float(continuation_row["created_at"]),
                    updated_at=float(inbox["delivered_at"]),
                )
                return self._row_to_child_signal(inbox), continuation, stored_event
            now = float(self._clock())
            continuation, _ = await self._save_continuation_tx(
                db,
                run=run,
                expected_version=expected_continuation_version,
                payload_json=payload_json,
                decision=None,
                now=now,
            )
            self._fault("child_apply_after_boundary")
            stored_event, _, _ = await self._append_event_tx(
                db,
                run,
                expected_version=int(run["version"]),
                event=applied_event,
                deliveries=deliveries,
            )
            self._fault("child_apply_after_event")
            if transaction_hook is not None:
                hooked = transaction_hook(db, continuation)
                if inspect.isawaitable(hooked):
                    await hooked
            record = await self._acknowledge_child_signal_tx(db, signal_id, now=now)
            self._fault("child_signal_ack_before_commit")
            self._fault("child_apply_before_commit")
            await db.commit()
            return record, continuation, stored_event

    @staticmethod
    def _legacy_status(value: str) -> RunStatus:
        return {
            "created": RunStatus.CREATED,
            "running": RunStatus.RUNNING,
            "waiting": RunStatus.WAITING,
            "retryable": RunStatus.QUEUED,
            "cancel_requested": RunStatus.CANCEL_REQUESTED,
            "cancelling": RunStatus.CANCEL_REQUESTED,
            "blocked": RunStatus.WAITING,
            "completed": RunStatus.COMPLETED,
            "failed": RunStatus.FAILED,
            "cancelled": RunStatus.CANCELLED,
        }.get(value, RunStatus.FAILED)

    async def _legacy_projection_tx(
        self, db: aiosqlite.Connection, run_id: str
    ) -> LegacyRunProjection | None:
        row = await (
            await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,))
        ).fetchone()
        if row is None:
            return None
        root_id = run_id
        parent_id = str(row["parent_run_id"]) if row["parent_run_id"] is not None else None
        current = parent_id
        seen = {run_id}
        while current is not None:
            if current in seen:
                raise ParentCycleError(
                    "legacy_parent_cycle", "legacy workflow lineage contains a cycle"
                )
            seen.add(current)
            ancestor = await (
                await db.execute(
                    "SELECT run_id,parent_run_id FROM workflow_runs WHERE run_id=?", (current,)
                )
            ).fetchone()
            if ancestor is None:
                break
            root_id = str(ancestor["run_id"])
            current = (
                str(ancestor["parent_run_id"])
                if ancestor["parent_run_id"] is not None
                else None
            )
        return LegacyRunProjection(
            run_id=run_id,
            session_id=str(row["session_id"]),
            root_run_id=root_id,
            parent_run_id=parent_id,
            status=self._legacy_status(str(row["status"])),
            driver_kind="workflow",
            profile_key=f"{row['workflow_name']}/{row['workflow_version']}",
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            ended_at=float(row["ended_at"]) if row["ended_at"] is not None else None,
        )

    def _authorize_view(
        self,
        ref: RunRef,
        actor: ActorContext,
        view: RunView,
    ) -> None:
        session_id = view.context.session_id if isinstance(view, RunRecord) else view.session_id
        root_run_id = view.context.root_run_id if isinstance(view, RunRecord) else view.root_run_id
        if ref.expected_session_id != session_id or actor.session_id != session_id:
            raise AuthorizationError(
                "actor_not_authorized", "actor does not own the run session"
            )
        if isinstance(view, LegacyRunProjection):
            if actor.internal:
                raise AuthorizationError(
                    "legacy_internal_authority_unavailable",
                    "legacy projections do not carry a verifiable internal authority",
                )
            return
        if actor.internal:
            if (
                actor.root_run_id != root_run_id
                or actor.capability_hash != view.context.capability_hash
                or actor.expires_at is None
                or actor.expires_at <= self._clock()
            ):
                raise AuthorizationError(
                    "actor_not_authorized", "internal authority scope is invalid or expired"
                )
            return
        if (
            actor.principal_id != view.context.principal_id
            or actor.auth_epoch != view.context.auth_epoch
            or (
                actor.root_run_id is not None
                and actor.root_run_id != root_run_id
            )
        ):
            raise AuthorizationError(
                "actor_not_authorized",
                "actor principal, authentication epoch, or run root differs",
            )

    async def authorize(
        self,
        ref: RunRef,
        actor: ActorContext,
        action: ActorAction | str,
    ) -> RunView:
        ActorAction(action)
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?", (ref.run_id,)
                )
            ).fetchone()
            view: RunView | None
            if row is not None:
                view = self._row_to_record(row)
            else:
                view = await self._legacy_projection_tx(db, ref.run_id)
            if view is None:
                raise RunNotFound("run_not_found", "execution run does not exist")
            self._authorize_view(ref, actor, view)
            return view

    async def query(self, ref: RunRef, actor: ActorContext) -> RunView:
        return await self.authorize(ref, actor, ActorAction.OBSERVE)

    async def list_child_links(
        self,
        ref: RunRef,
        actor: ActorContext,
    ) -> tuple[RunLinkSpec, ...]:
        parent = await self.authorize(ref, actor, ActorAction.OBSERVE)
        if isinstance(parent, LegacyRunProjection):
            return ()
        async with self._read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT * FROM execution_run_links
                    WHERE parent_run_id=? AND link_kind='structural'
                    ORDER BY created_at,link_id""",
                    (ref.run_id,),
                )
            ).fetchall()
            return tuple(
                RunLinkSpec(
                    link_id=str(row["link_id"]),
                    root_run_id=str(row["root_run_id"]),
                    parent_run_id=str(row["parent_run_id"]),
                    child_run_id=str(row["child_run_id"]),
                    attachment_policy=str(row["attachment_policy"]),
                    link_kind=str(row["link_kind"]),
                    domain_kind=(
                        str(row["domain_kind"])
                        if row["domain_kind"] not in (None, "")
                        else None
                    ),
                    domain_id=(
                        str(row["domain_id"])
                        if row["domain_id"] not in (None, "")
                        else None
                    ),
                )
                for row in rows
            )

    async def list_recoverable(
        self, *, limit: int = 10_000, run_ids: Sequence[str] = (),
    ) -> tuple[RunRecord, ...]:
        if isinstance(limit, bool) or limit < 1:
            raise ValueError("recoverable run limit must be positive")
        selected = tuple(dict.fromkeys(run_ids))
        run_filter = "" if not selected else f"AND run_id IN ({','.join('?' for _ in selected)})"
        async with self._read_connection() as db:
            owner_kind, owner_generation = await self._required_recovery_owner_tx(db)
            rows = await (
                await db.execute(
                    f"""SELECT * FROM execution_runs
                    WHERE terminal_event_id IS NULL
                    AND status IN ('created','queued','running','waiting','cancel_requested')
                    AND owner_kind=? AND owner_generation=?
                    {run_filter}
                    ORDER BY created_at,run_id LIMIT ?""",
                    (owner_kind, owner_generation, *selected, int(limit)),
                )
            ).fetchall()
            return tuple(self._row_to_record(row) for row in rows)

    async def configure_run_active_budget(
        self,
        run_id: str,
        *,
        limit_seconds: float,
    ) -> None:
        """Freeze one root Run's active-time budget without resetting retries."""

        limit = float(limit_seconds)
        if not math.isfinite(limit) or limit <= 0:
            raise ValueError("active execution budget must be positive and finite")
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT run.parent_run_id,budget.configured
                    FROM execution_runs AS run
                    LEFT JOIN execution_run_active_budgets AS budget
                        ON budget.run_id=run.run_id
                    WHERE run.run_id=?""",
                    (run_id,),
                )
            ).fetchone()
            if row is None:
                raise RunNotFound("run_not_found", f"run does not exist: {run_id}")
            if row["parent_run_id"] is not None:
                return
            if row["configured"] is None:
                raise RuntimeError("root Run active budget was not initialized")
            if not bool(row["configured"]):
                now = float(self._clock())
                await db.execute(
                    """UPDATE execution_run_active_budgets
                    SET limit_seconds=?,configured=1,
                        budget_version=budget_version+1,updated_at=?
                    WHERE run_id=? AND configured=0""",
                    (limit, now, run_id),
                )
            await db.commit()

    async def claim_expired_active_budget_runs(
        self,
        *,
        limit: int = 16,
    ) -> tuple[RunRecord, ...]:
        """Fence due root budgets so cancellation can be retried after crashes."""

        if isinstance(limit, bool) or limit < 1:
            raise ValueError("active budget claim limit must be positive")
        async with self._write_transaction() as db:
            now = float(self._clock())
            rows = await (
                await db.execute(
                    """SELECT budget.run_id,budget.budget_state,
                        budget.budget_version,budget.consumed_seconds,
                        budget.active_since,budget.limit_seconds
                    FROM execution_run_active_budgets AS budget
                    JOIN execution_runs AS run ON run.run_id=budget.run_id
                    WHERE run.terminal_event_id IS NULL
                    AND (
                        budget.budget_state='expired'
                        OR (
                            budget.budget_state='active'
                            AND budget.active_since IS NOT NULL
                            AND budget.consumed_seconds
                                + MAX(0.0,?-budget.active_since)
                                >= budget.limit_seconds
                        )
                    )
                    ORDER BY budget.updated_at,budget.run_id
                    LIMIT ?""",
                    (now, int(limit)),
                )
            ).fetchall()
            claimed_ids: list[str] = []
            for row in rows:
                run_id = str(row["run_id"])
                if str(row["budget_state"]) == "active":
                    consumed = float(row["consumed_seconds"]) + max(
                        0.0, now - float(row["active_since"])
                    )
                    cursor = await db.execute(
                        """UPDATE execution_run_active_budgets
                        SET consumed_seconds=?,active_since=NULL,
                            budget_state='expired',
                            budget_version=budget_version+1,updated_at=?
                        WHERE run_id=? AND budget_state='active'
                        AND budget_version=?""",
                        (
                            consumed,
                            now,
                            run_id,
                            int(row["budget_version"]),
                        ),
                    )
                    if cursor.rowcount != 1:
                        continue
                claimed_ids.append(run_id)
            records: list[RunRecord] = []
            for run_id in claimed_ids:
                run = await (
                    await db.execute(
                        """SELECT * FROM execution_runs
                        WHERE run_id=? AND terminal_event_id IS NULL""",
                        (run_id,),
                    )
                ).fetchone()
                if run is not None:
                    records.append(self._row_to_record(run))
            await db.commit()
            return tuple(records)

    async def next_active_budget_delay(self) -> float | None:
        """Return the next active root deadline; paused waits have no deadline."""

        async with self._read_connection() as db:
            now = float(self._clock())
            expired = await (
                await db.execute(
                    """SELECT 1
                    FROM execution_run_active_budgets AS budget
                    JOIN execution_runs AS run ON run.run_id=budget.run_id
                    WHERE budget.budget_state='expired'
                    AND run.terminal_event_id IS NULL LIMIT 1"""
                )
            ).fetchone()
            if expired is not None:
                return 1.0
            row = await (
                await db.execute(
                    """SELECT MIN(MAX(
                        0.0,
                        budget.limit_seconds-budget.consumed_seconds
                            -MAX(0.0,?-budget.active_since)
                    )) AS delay
                    FROM execution_run_active_budgets AS budget
                    JOIN execution_runs AS run ON run.run_id=budget.run_id
                    WHERE budget.budget_state='active'
                    AND budget.active_since IS NOT NULL
                    AND run.terminal_event_id IS NULL""",
                    (now,),
                )
            ).fetchone()
            if row is None or row["delay"] is None:
                return None
            return max(0.0, float(row["delay"]))

    async def lookup_completion_evidence(
        self,
        context: EvidenceContext,
    ) -> EvidenceSelection:
        """Project exact completion evidence without creating another ledger.

        A normal tool call resolves to its own settled execution effect.  An
        attached ``workflow_spawn`` is different: the parent control call is
        completed by a durable child terminal signal, while the actual tool
        effects live in ``workflow_effects`` under that child Run.  Resolve
        that lineage only when every durable identity agrees and the child's
        workflow audit explicitly passed.  This keeps the query fail-closed
        without discarding the receipts that prove delegated work.
        """

        # execution_effects currently has no explicit target/resource digest.
        # An effect fingerprint is a different identity and must not substitute.
        if context.target_digest is not None:
            return UNKNOWN_EVIDENCE
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT effect.run_id,effect.call_id,effect.effect_id,
                    effect.tool_name,effect.status,effect.receipt_ref,
                    effect.artifact_refs_json,run.turn_id
                    FROM execution_effects AS effect
                    JOIN execution_runs AS run ON run.run_id=effect.run_id
                    WHERE effect.effect_id=?""",
                    (context.effect_id,),
                )
            ).fetchone()
            if row is not None:
                expected = (
                    context.run_id,
                    context.turn_id,
                    context.call_id,
                    context.effect_id,
                )
                actual = (
                    str(row["run_id"]),
                    str(row["turn_id"]),
                    str(row["call_id"]),
                    str(row["effect_id"]),
                )
                artifacts = tuple(
                    str(item)
                    for item in json.loads(str(row["artifact_refs_json"]))
                )
                receipt_ref = str(row["receipt_ref"] or "")
                if (
                    actual != expected
                    or str(row["status"]) != "succeeded"
                    or not receipt_ref
                    or (
                        context.artifact_ref is not None
                        and context.artifact_ref not in artifacts
                    )
                ):
                    return UNKNOWN_EVIDENCE
                record = CompletionEvidence(
                    context=context,
                    tool_name=str(row["tool_name"]),
                    receipt_ref=receipt_ref,
                    artifact_refs=artifacts,
                )
                return EvidenceSelection(status="matched", records=(record,))

            expected_effect_id = hashlib.sha256(
                f"effect|{context.run_id}|{context.call_id}".encode("utf-8")
            ).hexdigest()
            if context.effect_id != expected_effect_id:
                return UNKNOWN_EVIDENCE

            lineage = await (
                await db.execute(
                    """SELECT parent.turn_id,child.run_id AS child_run_id,
                    child.status AS child_status,
                    child.terminal_event_id,signal.payload_json
                    FROM execution_profile_launch_tickets AS ticket
                    JOIN execution_runs AS parent
                      ON parent.run_id=ticket.parent_run_id
                    JOIN execution_child_commands AS command
                      ON command.parent_run_id=ticket.parent_run_id
                     AND command.command_id=ticket.child_command_id
                     AND command.child_run_id=ticket.child_run_id
                    JOIN execution_child_signal_inbox AS signal
                      ON signal.operation_id=command.operation_id
                     AND signal.kind='terminal'
                    JOIN execution_runs AS child
                      ON child.run_id=ticket.child_run_id
                    WHERE ticket.parent_run_id=?
                      AND ticket.spawn_call_id=?
                      AND ticket.state='consumed'
                      AND command.status='acked'
                      AND signal.delivered_at IS NOT NULL""",
                    (context.run_id, context.call_id),
                )
            ).fetchone()
            if (
                lineage is None
                or str(lineage["turn_id"]) != context.turn_id
                or str(lineage["child_status"]) != "completed"
                or not str(lineage["terminal_event_id"] or "")
            ):
                return UNKNOWN_EVIDENCE
            try:
                terminal = json.loads(str(lineage["payload_json"]))
                value = terminal.get("value")
                if not isinstance(value, Mapping):
                    return UNKNOWN_EVIDENCE
                workflow_report = value.get("workflow_report")
                audit = (
                    workflow_report.get("audit")
                    if isinstance(workflow_report, Mapping)
                    else None
                )
                if (
                    terminal.get("status") != "completed"
                    or value.get("status") != "completed"
                    or value.get("run_id") != str(lineage["child_run_id"])
                    or value.get("terminal_event_id")
                    != str(lineage["terminal_event_id"])
                    or not isinstance(audit, Mapping)
                    or audit.get("passed") is not True
                ):
                    return UNKNOWN_EVIDENCE
            except (TypeError, ValueError, json.JSONDecodeError):
                return UNKNOWN_EVIDENCE

            effect_rows = await (
                await db.execute(
                    """SELECT prepared_json,receipt_ref,artifact_refs_json
                    FROM workflow_effects
                    WHERE run_id=? AND status='committed'
                      AND receipt_ref IS NOT NULL AND receipt_ref<>''
                    ORDER BY started_at,effect_id""",
                    (str(lineage["child_run_id"]),),
                )
            ).fetchall()

        records: list[CompletionEvidence] = []
        all_artifacts: set[str] = set()
        for effect in effect_rows:
            try:
                prepared = json.loads(str(effect["prepared_json"]))
                tool_name = str(prepared.get("tool_name") or "").strip()
                artifacts = tuple(
                    str(item)
                    for item in json.loads(str(effect["artifact_refs_json"]))
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                return UNKNOWN_EVIDENCE
            receipt_ref = str(effect["receipt_ref"] or "").strip()
            if not tool_name or not receipt_ref:
                return UNKNOWN_EVIDENCE
            all_artifacts.update(artifacts)
            records.append(
                CompletionEvidence(
                    context=context,
                    tool_name=tool_name,
                    receipt_ref=receipt_ref,
                    artifact_refs=artifacts,
                )
            )
        if (
            not records
            or (
                context.artifact_ref is not None
                and context.artifact_ref not in all_artifacts
            )
        ):
            return UNKNOWN_EVIDENCE
        return EvidenceSelection(status="matched", records=tuple(records))

    async def read_effect_outcome(
        self, *, run_id: str, call_id: str, effect_id: str, args_hash: str,
        capability_hash: str, scope_hash: str,
    ) -> tuple[str, Mapping[str, Any], str | None, tuple[str, ...]] | None:
        """Read the authoritative execution-effect settlement; never reconciles."""
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT run_id,call_id,args_hash,capability_hash,scope_hash,
                    status,outcome_json,receipt_ref,artifact_refs_json
                    FROM execution_effects WHERE effect_id=? AND status!='running'""",
                    (effect_id,),
                )
            ).fetchone()

        if row is None or row["outcome_json"] is None:
            return None
        expected = (run_id, call_id, args_hash, capability_hash, scope_hash)
        actual = tuple(str(row[name]) for name in (
            "run_id", "call_id", "args_hash", "capability_hash", "scope_hash"
        ))
        if actual != expected:
            raise IdempotencyConflict(
                "effect_read_binding_mismatch", "effect outcome belongs to another intent"
            )
        status = str(row["status"])
        if status == "late_reconciled":
            status = "unknown"  # legacy rows had no durable evidence-verification bit
        return (
            status,
            thaw_json(json.loads(str(row["outcome_json"]))),
            str(row["receipt_ref"]) if row["receipt_ref"] else None,
            tuple(str(item) for item in json.loads(str(row["artifact_refs_json"]))),
        )

    async def recovery_scope(
        self,
        subject: str | RecoveryLease,
        *,
        owner: str | None = None,
        lease_seconds: float | None = 30.0,
    ) -> RecoveryLease | bool:
        """Claim, renew, or release one typed recovery lease."""

        if isinstance(subject, RecoveryLease):
            async with self._write_transaction() as db:
                await self._recovery_run_tx(db, subject.run_id)
                if lease_seconds is None:
                    cursor = await db.execute(
                        """UPDATE execution_runs SET recovery_owner=NULL,
                        recovery_expires_at=NULL,recovery_heartbeat_at=NULL
                        WHERE run_id=? AND recovery_owner=? AND recovery_epoch=?""",
                        (subject.run_id, subject.owner, subject.epoch),
                    )
                    await db.commit()
                    return cursor.rowcount == 1
                if lease_seconds <= 0:
                    raise ValueError("lease_seconds must be positive")
                now = float(self._clock())
                new_expiry = now + float(lease_seconds)
                cursor = await db.execute(
                    """UPDATE execution_runs SET recovery_expires_at=?,recovery_heartbeat_at=?
                    WHERE run_id=? AND recovery_owner=? AND recovery_epoch=?
                    AND recovery_expires_at>? AND terminal_event_id IS NULL""",
                    (new_expiry, now, subject.run_id, subject.owner, subject.epoch, now),
                )
                if cursor.rowcount != 1:
                    raise StaleRecoveryLease("recovery lease is stale or expired")
                await db.commit()
                return RecoveryLease(
                    subject.run_id, subject.owner, subject.epoch, new_expiry
                )
        run_id = subject
        owner = str(owner or "")
        lease_seconds = 30.0 if lease_seconds is None else lease_seconds
        if not owner.strip() or lease_seconds <= 0:
            raise ValueError("recovery owner and positive lease_seconds are required")
        async with self._write_transaction() as db:
            now = float(self._clock())
            row = await self._recovery_run_tx(db, run_id)
            if RunStatus(str(row["status"])) in TERMINAL_RUN_STATUSES:
                raise StaleRecoveryLease("terminal runs cannot be claimed for recovery")
            active_owner = row["recovery_owner"]
            expires_at = row["recovery_expires_at"]
            if (
                active_owner is not None
                and str(active_owner) == owner
                and expires_at is not None
                and float(expires_at) > now
            ):
                new_expiry = now + float(lease_seconds)
                epoch = int(row["recovery_epoch"])
                await db.execute(
                    """UPDATE execution_runs SET recovery_expires_at=?,
                    recovery_heartbeat_at=? WHERE run_id=? AND recovery_owner=?
                    AND recovery_epoch=? AND recovery_expires_at>?""",
                    (new_expiry, now, run_id, owner, epoch, now),
                )
                await db.commit()
                return RecoveryLease(run_id, owner, epoch, new_expiry)
            if (
                active_owner is not None
                and str(active_owner) != owner
                and expires_at is not None
                and float(expires_at) > now
            ):
                raise StaleRecoveryLease("run recovery lease is held by another owner")
            epoch = int(row["recovery_epoch"]) + 1
            new_expiry = now + float(lease_seconds)
            cursor = await db.execute(
                """UPDATE execution_runs SET recovery_owner=?,recovery_epoch=?,
                recovery_expires_at=?,recovery_heartbeat_at=?
                WHERE run_id=? AND recovery_epoch=?""",
                (owner, epoch, new_expiry, now, run_id, int(row["recovery_epoch"])),
            )
            if cursor.rowcount != 1:
                raise StaleRecoveryLease("recovery lease changed while being claimed")
            await db.commit()
            return RecoveryLease(run_id, owner, epoch, new_expiry)

    async def claim_workflow_recovery_handoff(
        self, recovery_lease: RecoveryLease, *, workflow_owner: str,
        ttl_seconds: float = 90.0) -> RunFence:
        """Atomically validate execution ownership and claim the Native run lease."""

        if not workflow_owner.strip() or ttl_seconds <= 0:
            raise ValueError("workflow owner and positive ttl_seconds are required")
        async with self._write_transaction() as db:
            run_id = recovery_lease.run_id
            await self._assert_recovery_fence_tx(db, recovery_lease, run_id=run_id)
            execution = await (
                await db.execute(
                    """SELECT driver_kind,status,started_at
                    FROM execution_runs WHERE run_id=?""",
                    (run_id,),
                )
            ).fetchone()
            if execution is None or str(execution["driver_kind"]) != "workflow":
                raise CheckpointExecutionError(
                    "execution_driver_conflict",
                    "workflow recovery handoff cannot claim another driver",
                )
            now = float(self._clock())
            expires_at = now + ttl_seconds
            row = await (
                await db.execute(
                    """UPDATE workflow_runs SET lease_owner=:owner,
                    lease_epoch=lease_epoch+CASE WHEN lease_owner=:owner
                        AND lease_expires_at>:now THEN 0 ELSE 1 END,
                    run_version=run_version+CASE WHEN lease_owner=:owner
                        AND lease_expires_at>:now THEN 0 ELSE 1 END,
                    lease_expires_at=:expiry,heartbeat_at=:now,status='running',
                    started_at=COALESCE(started_at,:now),updated_at=:now
                    WHERE run_id=:run_id
                    AND (
                        status IN ('created','retryable','running')
                        OR (
                            status='waiting'
                            AND EXISTS (
                                SELECT 1 FROM execution_decisions
                                WHERE execution_decisions.run_id=workflow_runs.run_id
                                AND execution_decisions.status IN ('allowed','denied')
                                AND execution_decisions.consumed_at IS NULL
                            )
                        )
                    )
                    AND (lease_owner IS NULL OR lease_owner=:owner OR lease_expires_at<=:now)
                    RETURNING lease_epoch,run_version""",
                    {"owner": workflow_owner, "expiry": expires_at, "now": now, "run_id": run_id},
                )
            ).fetchone()
            if row is None:
                raise StaleRecoveryLease(
                    "native workflow lease cannot be claimed by this recovery owner"
                )
            execution_cursor = await db.execute(
                """UPDATE execution_runs
                SET status='running',
                    started_at=COALESCE(started_at,:now),
                    version=version+CASE
                        WHEN status!='running' OR started_at IS NULL THEN 1 ELSE 0
                    END,
                    updated_at=CASE
                        WHEN status!='running' OR started_at IS NULL
                        THEN :now ELSE updated_at
                    END
                WHERE run_id=:run_id AND terminal_event_id IS NULL
                AND status IN ('created','queued','running','waiting')""",
                {"now": now, "run_id": run_id},
            )
            if execution_cursor.rowcount != 1:
                raise StaleRecoveryLease(
                    "generic workflow execution is no longer launchable"
                )
            await db.commit()
            return RunFence(
                run_id, workflow_owner, int(row["lease_epoch"]), int(row["run_version"])
            )

    async def assert_recovery_fence(self, lease: RecoveryLease) -> None:
        async with self._read_connection() as db:
            await self._assert_recovery_fence_tx(db, lease, run_id=lease.run_id)

    async def _assert_optional_recovery_fence_tx(
        self, db: aiosqlite.Connection, lease: RecoveryLease | None, run_id: str) -> None:
        if lease is not None:
            await self._assert_recovery_fence_tx(db, lease, run_id=run_id)

    async def _assert_recovery_fence_tx(
        self,
        db: aiosqlite.Connection,
        lease: RecoveryLease,
        *,
        run_id: str,
    ) -> None:
        if lease.run_id != run_id:
            raise StaleRecoveryLease("recovery lease is bound to another run")
        now = float(self._clock())
        row = await (
            await db.execute(
                """SELECT 1 FROM execution_runs WHERE run_id=?
                AND recovery_owner=? AND recovery_epoch=?
                AND recovery_expires_at>? AND terminal_event_id IS NULL""",
                (run_id, lease.owner, lease.epoch, now),
            )
        ).fetchone()
        if row is None:
            raise StaleRecoveryLease("recovery writer lost its lease fence")

    async def _insert_workflow_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run: RunRecord,
        workflow: WorkflowRunSeed,
    ) -> bool:
        snapshot = thaw_json(workflow.capability_snapshot)
        if fingerprint_json(snapshot) != workflow.capability_hash:
            raise RunIdentityConflict(
                "workflow_capability_conflict",
                "workflow capability hash does not match its immutable snapshot",
            )
        if workflow.trace_id != run.context.trace_id:
            raise RunIdentityConflict(
                "workflow_trace_conflict", "workflow and execution trace ids differ"
            )
        capability_json = canonical_json(snapshot)
        existing_capability = await (
            await db.execute(
                "SELECT snapshot_json FROM workflow_capabilities WHERE capability_hash=?",
                (workflow.capability_hash,),
            )
        ).fetchone()
        if existing_capability is not None:
            if canonical_json(json.loads(str(existing_capability["snapshot_json"]))) != capability_json:
                raise IdempotencyConflict(
                    "workflow_capability_conflict",
                    "capability hash already names different workflow capability content",
                )
        else:
            await db.execute(
                """INSERT INTO workflow_capabilities(capability_hash,snapshot_json,created_at)
                VALUES(?,?,?)""",
                (workflow.capability_hash, capability_json, self._clock()),
            )
        existing_start = await (
            await db.execute(
                "SELECT * FROM workflow_start_requests WHERE request_key=?",
                (workflow.request_key,),
            )
        ).fetchone()
        if existing_start is not None and str(existing_start["run_id"]) != run.run_id:
            raise IdempotencyConflict(
                "workflow_start_conflict", "workflow request key already names another run"
            )
        existing_run = await (
            await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run.run_id,))
        ).fetchone()
        if existing_run is not None:
            expected = {
                "trace_id": workflow.trace_id,
                "thread_id": workflow.thread_id,
                "checkpoint_ns": workflow.checkpoint_ns,
                "session_id": run.context.session_id,
                "request_id": run.context.request_id,
                "turn_id": run.context.turn_id,
                "workflow_name": workflow.workflow_name,
                "workflow_version": workflow.workflow_version,
                "manifest_hash": workflow.manifest_hash,
                "implementation_hash": workflow.implementation_hash,
                "capability_hash": workflow.capability_hash,
                "state_schema_version": workflow.state_schema_version,
            }
            for field_name, value in expected.items():
                stored = existing_run[field_name]
                stored = int(stored) if field_name == "state_schema_version" else str(stored or "")
                if stored != value:
                    raise IdempotencyConflict(
                        "workflow_start_conflict",
                        f"workflow run differs at {field_name}",
                    )
        else:
            now = float(self._clock())
            await db.execute(
                """INSERT INTO workflow_runs(
                run_id,trace_id,thread_id,checkpoint_ns,parent_run_id,source_checkpoint_id,
                session_id,request_id,turn_id,workflow_name,workflow_version,manifest_hash,
                implementation_hash,capability_hash,state_schema_version,status,
                active_nodes_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'created','[]',?,?)""",
                (
                    run.run_id,
                    workflow.trace_id,
                    workflow.thread_id,
                    workflow.checkpoint_ns,
                    run.context.parent_run_id,
                    workflow.source_checkpoint_id,
                    run.context.session_id,
                    run.context.request_id,
                    run.context.turn_id,
                    workflow.workflow_name,
                    workflow.workflow_version,
                    workflow.manifest_hash,
                    workflow.implementation_hash,
                    workflow.capability_hash,
                    workflow.state_schema_version,
                    now,
                    now,
                ),
            )
        if existing_start is None:
            await db.execute(
                """INSERT INTO workflow_start_requests(
                request_key,session_id,request_id,turn_id,workflow_name,
                capability_hash,run_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    workflow.request_key,
                    run.context.session_id,
                    run.context.request_id,
                    run.context.turn_id,
                    workflow.workflow_name,
                    workflow.capability_hash,
                    run.run_id,
                    self._clock(),
                ),
            )
        for ref in workflow.session_refs:
            existing_ref = await (
                await db.execute(
                    """SELECT session_id,session_epoch FROM workflow_session_refs
                    WHERE run_id=? AND session_kind=?""",
                    (run.run_id, ref.session_kind),
                )
            ).fetchone()
            if existing_ref is not None:
                if (
                    str(existing_ref["session_id"]) != ref.session_id
                    or int(existing_ref["session_epoch"]) != ref.session_epoch
                ):
                    raise IdempotencyConflict(
                        "workflow_session_ref_conflict",
                        "workflow session kind already names another session fence",
                    )
                continue
            await db.execute(
                """INSERT INTO workflow_session_refs(
                run_id,session_kind,session_id,session_epoch,deleted_at
                ) VALUES(?,?,?,?,NULL)""",
                (run.run_id, ref.session_kind, ref.session_id, ref.session_epoch),
            )
        return existing_run is None

    @staticmethod
    def _row_to_task_goal(row: Mapping[str, Any]) -> TaskGoalRecord:
        return TaskGoalRecord(
            goal_id=str(row["goal_id"]),
            root_run_id=str(row["root_run_id"]),
            task_scope_id=str(row["task_scope_id"]),
            objective_ref=str(row["objective_ref"]),
            status=str(row["status"]),
            version=int(row["goal_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            ended_at=(
                float(row["ended_at"]) if row["ended_at"] is not None else None
            ),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_task_work_context(row: Mapping[str, Any]) -> TaskWorkContext:
        return TaskWorkContext(
            session_id=str(row["session_id"]),
            root_run_id=str(row["root_run_id"]),
            task_scope_id=str(row["task_scope_id"]),
            workspace_root=(
                str(row["workspace_root"])
                if row["workspace_root"] is not None
                else None
            ),
            workspace_source=str(row["workspace_source"]),
            binding_version=int(row["binding_version"]),
        )

    @staticmethod
    def _row_to_conversation_boundary(
        row: Mapping[str, Any],
    ) -> ConversationBoundary:
        return ConversationBoundary(
            boundary_ref=str(row["boundary_ref"]),
            session_id=str(row["session_id"]),
            root_run_id=str(row["root_run_id"]),
            task_scope_id=str(row["task_scope_id"]),
            seed_message_refs=tuple(
                str(item)
                for item in json.loads(str(row["seed_message_refs_json"]))
            ),
            continuation_message_refs=tuple(
                str(item)
                for item in json.loads(
                    str(row["continuation_message_refs_json"])
                )
            ),
            version=int(row["boundary_version"]),
        )

    @staticmethod
    def _row_to_user_continuation(
        row: Mapping[str, Any],
    ) -> QueuedUserContinuation:
        return QueuedUserContinuation(
            root_run_id=str(row["root_run_id"]),
            task_scope_id=str(row["task_scope_id"]),
            message_ref=str(row["message_ref"]),
            content=str(row["content"]),
            reserved_boundary_version=int(row["reserved_boundary_version"]),
            status=str(row["status"]),
            created_at=float(row["created_at"]),
            settled_at=(
                None
                if row["settled_at"] is None
                else float(row["settled_at"])
            ),
            error=(None if row["error"] is None else str(row["error"])),
        )

    @staticmethod
    def _row_to_task_run_projection(
        row: Mapping[str, Any],
    ) -> TaskRunProjection:
        return TaskRunProjection(
            projection_id=str(row["projection_id"]),
            session_id=str(row["session_id"]),
            root_run_id=str(row["root_run_id"]),
            task_scope_id=str(row["task_scope_id"]),
            ui_state=str(row["ui_state"]),
            version=int(row["projection_version"]),
        )

    @staticmethod
    def _row_to_plan_version(row: Mapping[str, Any]) -> PlanVersionRecord:
        return PlanVersionRecord(
            root_run_id=str(row["root_run_id"]),
            plan_version=int(row["plan_version"]),
            trigger_failure_set_id=(
                str(row["trigger_failure_set_id"])
                if row["trigger_failure_set_id"] is not None
                else None
            ),
            created_at=float(row["created_at"]),
        )

    @staticmethod
    def _row_to_provider_turn(row: Mapping[str, Any]) -> ProviderTurnFence:
        return ProviderTurnFence(
            provider_turn_id=str(row["provider_turn_id"]),
            root_run_id=str(row["root_run_id"]),
            idempotency_key=str(row["idempotency_key"]),
            request_hash=str(row["request_hash"]),
            state=str(row["state"]),
            accepted_batch_id=(
                str(row["accepted_batch_id"])
                if row["accepted_batch_id"] is not None
                else None
            ),
            version=int(row["fence_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            accepted_at=(
                float(row["accepted_at"])
                if row["accepted_at"] is not None
                else None
            ),
        )

    @staticmethod
    def _row_to_provider_batch(row: Mapping[str, Any]) -> ProviderActionBatch:
        return ProviderActionBatch(
            provider_batch_id=str(row["provider_batch_id"]),
            root_run_id=str(row["root_run_id"]),
            provider_turn_id=str(row["provider_turn_id"]),
            canonical_assistant_batch_ref=str(
                row["canonical_assistant_batch_ref"]
            ),
            batch_fingerprint=str(row["batch_fingerprint"]),
            pending_call_count=int(row["pending_call_count"]),
            status=str(row["status"]),
            failure_set_id=(
                str(row["failure_set_id"])
                if row["failure_set_id"] is not None
                else None
            ),
            version=int(row["batch_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            settled_at=(
                float(row["settled_at"]) if row["settled_at"] is not None else None
            ),
        )

    @staticmethod
    def _row_to_provider_call(row: Mapping[str, Any]) -> ProviderActionCall:
        return ProviderActionCall(
            call_record_id=str(row["call_record_id"]),
            root_run_id=str(row["root_run_id"]),
            provider_batch_id=str(row["provider_batch_id"]),
            call_order=int(row["call_order"]),
            provider_call_id=str(row["provider_call_id"]),
            raw_tool_name=str(row["raw_tool_name"]),
            raw_arguments_ref=str(row["raw_arguments_ref"]),
            raw_arguments_hash=str(row["raw_arguments_hash"]),
            parsed_arguments_hash=(
                str(row["parsed_arguments_hash"])
                if row["parsed_arguments_hash"] is not None
                else None
            ),
            admission_state=str(row["admission_state"]),
            prepared_call_ref=(
                str(row["prepared_call_ref"])
                if row["prepared_call_ref"] is not None
                else None
            ),
            command_boundary_ref=(
                str(row["command_boundary_ref"])
                if row["command_boundary_ref"] is not None
                else None
            ),
            terminal_outcome_ref=(
                str(row["terminal_outcome_ref"])
                if row["terminal_outcome_ref"] is not None
                else None
            ),
            version=int(row["call_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _row_to_attempt(row: Mapping[str, Any]) -> AttemptRecord:
        return AttemptRecord(
            attempt_id=str(row["attempt_id"]),
            root_run_id=str(row["root_run_id"]),
            run_id=str(row["run_id"]),
            provider_turn_id=str(row["provider_turn_id"]),
            provider_batch_id=str(row["provider_batch_id"]),
            plan_version=int(row["plan_version"]),
            trigger_failure_set_id=(
                str(row["trigger_failure_set_id"])
                if row["trigger_failure_set_id"] is not None
                else None
            ),
            supersedes_attempt_id=(
                str(row["supersedes_attempt_id"])
                if row["supersedes_attempt_id"] is not None
                else None
            ),
            strategy_fingerprint=str(row["strategy_fingerprint"]),
            planned_call_refs=tuple(
                str(item)
                for item in json.loads(str(row["planned_call_refs_json"]))
            ),
            checkpoint_ref=(
                str(row["checkpoint_ref"])
                if row["checkpoint_ref"] is not None
                else None
            ),
            status=str(row["status"]),
            budget_eligible=bool(row["budget_eligible"]),
            version=int(row["attempt_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            ended_at=(
                float(row["ended_at"]) if row["ended_at"] is not None else None
            ),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_failure_report(row: Mapping[str, Any]) -> TaskFailureReport:
        return TaskFailureReport(
            report_ref=str(row["report_ref"]),
            root_run_id=str(row["root_run_id"]),
            run_id=str(row["run_id"]),
            task_scope_id=str(row["task_scope_id"]),
            attempt_id=str(row["attempt_id"]),
            plan_version=int(row["plan_version"]),
            call_record_id=str(row["call_record_id"]),
            source_kind=str(row["source_kind"]),
            source_identity=str(row["source_identity"]),
            provider_call_id=(
                str(row["provider_call_id"])
                if row["provider_call_id"] is not None
                else None
            ),
            child_run_id=(
                str(row["child_run_id"])
                if row["child_run_id"] is not None
                else None
            ),
            inner_failure_ref=(
                str(row["inner_failure_ref"])
                if row["inner_failure_ref"] is not None
                else None
            ),
            failed_call_id=(
                str(row["failed_call_id"])
                if row["failed_call_id"] is not None
                else None
            ),
            failed_effect_id=(
                str(row["failed_effect_id"])
                if row["failed_effect_id"] is not None
                else None
            ),
            failed_step=str(row["failed_step"]),
            error_class=str(row["error_class"]),
            error_code=str(row["error_code"]),
            error_fingerprint=str(row["error_fingerprint"]),
            action_fingerprint=str(row["action_fingerprint"]),
            exit_code=(
                int(row["exit_code"]) if row["exit_code"] is not None else None
            ),
            evidence_refs=tuple(
                str(item) for item in json.loads(str(row["evidence_refs_json"]))
            ),
            completed_step_refs=tuple(
                str(item)
                for item in json.loads(str(row["completed_step_refs_json"]))
            ),
            artifact_refs=tuple(
                str(item) for item in json.loads(str(row["artifact_refs_json"]))
            ),
            checkpoint_ref=(
                str(row["checkpoint_ref"])
                if row["checkpoint_ref"] is not None
                else None
            ),
            prior_strategy_fingerprints=tuple(
                str(item)
                for item in json.loads(
                    str(row["prior_strategy_fingerprints_json"])
                )
            ),
            created_at=float(row["created_at"]),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_external_wait(row: Mapping[str, Any]) -> TaskExternalWait:
        return TaskExternalWait(
            wait_ref=str(row["wait_ref"]),
            root_run_id=str(row["root_run_id"]),
            attempt_id=str(row["attempt_id"]),
            call_record_id=str(row["call_record_id"]),
            provider_call_id=str(row["provider_call_id"]),
            command_boundary_ref=(
                str(row["command_boundary_ref"])
                if row["command_boundary_ref"] is not None
                else None
            ),
            effect_id=(
                str(row["effect_id"]) if row["effect_id"] is not None else None
            ),
            wait_kind=str(row["wait_kind"]),
            required_action_ref=str(row["required_action_ref"]),
            checkpoint_ref=(
                str(row["checkpoint_ref"])
                if row["checkpoint_ref"] is not None
                else None
            ),
            resume_admission_state=str(row["resume_admission_state"]),
            evidence_refs=tuple(
                str(item) for item in json.loads(str(row["evidence_refs_json"]))
            ),
            state=str(row["state"]),
            response_ref=(
                str(row["response_ref"])
                if row["response_ref"] is not None
                else None
            ),
            version=int(row["wait_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            resolved_at=(
                float(row["resolved_at"])
                if row["resolved_at"] is not None
                else None
            ),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_profile_ticket(row: Mapping[str, Any]) -> ProfileLaunchTicket:
        return ProfileLaunchTicket(
            ticket_ref=str(row["ticket_ref"]),
            parent_run_id=str(row["parent_run_id"]),
            root_run_id=str(row["root_run_id"]),
            task_scope_id=str(row["task_scope_id"]),
            attempt_id=str(row["attempt_id"]),
            provider_turn_id=str(row["provider_turn_id"]),
            profile_key=str(row["profile_key"]),
            driver_kind=str(row["driver_kind"]),
            profile_catalog_generation=int(row["profile_catalog_generation"]),
            capability_snapshot_ref=str(row["capability_snapshot_ref"]),
            task_grant_ref=str(row["task_grant_ref"]),
            spawn_call_id=str(row["spawn_call_id"]),
            personal_selection_id=(
                str(row["personal_selection_id"])
                if row["personal_selection_id"] is not None
                else None
            ),
            personal_selection_fingerprint=(
                str(row["personal_selection_fingerprint"])
                if row["personal_selection_fingerprint"] is not None
                else None
            ),
            trigger_failure_set_id=(
                str(row["trigger_failure_set_id"])
                if row["trigger_failure_set_id"] is not None
                else None
            ),
            request_fingerprint=str(row["request_fingerprint"]),
            state=str(row["state"]),
            child_command_id=(
                str(row["child_command_id"])
                if row["child_command_id"] is not None
                else None
            ),
            child_run_id=(
                str(row["child_run_id"])
                if row["child_run_id"] is not None
                else None
            ),
            version=int(row["ticket_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            consumed_at=(
                float(row["consumed_at"])
                if row["consumed_at"] is not None
                else None
            ),
            cancelled_at=(
                float(row["cancelled_at"])
                if row["cancelled_at"] is not None
                else None
            ),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    async def _failure_set_from_row(
        db: aiosqlite.Connection, row: Mapping[str, Any]
    ) -> AttemptFailureSet:
        members = await (
            await db.execute(
                """SELECT report_ref FROM execution_attempt_failure_set_members
                WHERE failure_set_id=? ORDER BY provider_call_order,report_ref""",
                (str(row["failure_set_id"]),),
            )
        ).fetchall()
        return AttemptFailureSet(
            failure_set_id=str(row["failure_set_id"]),
            root_run_id=str(row["root_run_id"]),
            failed_attempt_id=str(row["failed_attempt_id"]),
            report_refs=tuple(str(member["report_ref"]) for member in members),
            primary_report_ref=str(row["primary_report_ref"]),
            backfill_state=str(row["backfill_state"]),
            provider_resume_state=str(row["provider_resume_state"]),
            version=int(row["set_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _same_goal_intent(
        existing: TaskGoalRecord, requested: TaskGoalRecord
    ) -> bool:
        return (
            existing.goal_id,
            existing.root_run_id,
            existing.task_scope_id,
            existing.objective_ref,
        ) == (
            requested.goal_id,
            requested.root_run_id,
            requested.task_scope_id,
            requested.objective_ref,
        )

    @staticmethod
    def _same_batch_intent(
        existing: ProviderActionBatch, requested: ProviderActionBatch
    ) -> bool:
        return (
            existing.provider_batch_id,
            existing.root_run_id,
            existing.provider_turn_id,
            existing.canonical_assistant_batch_ref,
            existing.batch_fingerprint,
        ) == (
            requested.provider_batch_id,
            requested.root_run_id,
            requested.provider_turn_id,
            requested.canonical_assistant_batch_ref,
            requested.batch_fingerprint,
        )

    @staticmethod
    def _same_attempt_intent(
        existing: AttemptRecord, requested: AttemptRecord
    ) -> bool:
        return (
            existing.attempt_id,
            existing.root_run_id,
            existing.run_id,
            existing.provider_turn_id,
            existing.provider_batch_id,
            existing.plan_version,
            existing.trigger_failure_set_id,
            existing.supersedes_attempt_id,
            existing.strategy_fingerprint,
            tuple(existing.planned_call_refs),
            existing.checkpoint_ref,
        ) == (
            requested.attempt_id,
            requested.root_run_id,
            requested.run_id,
            requested.provider_turn_id,
            requested.provider_batch_id,
            requested.plan_version,
            requested.trigger_failure_set_id,
            requested.supersedes_attempt_id,
            requested.strategy_fingerprint,
            tuple(requested.planned_call_refs),
            requested.checkpoint_ref,
        )

    @staticmethod
    def _same_call_intent(
        existing: ProviderActionCall, requested: ProviderActionCall
    ) -> bool:
        identity_matches = (
            existing.call_record_id,
            existing.root_run_id,
            existing.provider_batch_id,
            existing.call_order,
            existing.provider_call_id,
            existing.raw_tool_name,
            existing.raw_arguments_ref,
            existing.raw_arguments_hash,
        ) == (
            requested.call_record_id,
            requested.root_run_id,
            requested.provider_batch_id,
            requested.call_order,
            requested.provider_call_id,
            requested.raw_tool_name,
            requested.raw_arguments_ref,
            requested.raw_arguments_hash,
        )
        if not identity_matches:
            return False
        if requested.admission_state is ProviderActionAdmissionState.REJECTED:
            return (
                existing.admission_state is ProviderActionAdmissionState.REJECTED
                and existing.terminal_outcome_ref
                == requested.terminal_outcome_ref
            )
        return existing.admission_state is not ProviderActionAdmissionState.REJECTED

    @staticmethod
    def _same_ticket_intent(
        existing: ProfileLaunchTicket, requested: ProfileLaunchTicket
    ) -> bool:
        fields = (
            "ticket_ref",
            "parent_run_id",
            "root_run_id",
            "task_scope_id",
            "attempt_id",
            "provider_turn_id",
            "profile_key",
            "driver_kind",
            "profile_catalog_generation",
            "capability_snapshot_ref",
            "task_grant_ref",
            "spawn_call_id",
            "personal_selection_id",
            "personal_selection_fingerprint",
            "trigger_failure_set_id",
            "request_fingerprint",
        )
        return all(
            getattr(existing, field_name) == getattr(requested, field_name)
            for field_name in fields
        )

    async def create_task_context(
        self,
        work_context: TaskWorkContext,
        conversation: ConversationBoundary,
        projection: TaskRunProjection,
    ) -> tuple[TaskWorkContext, ConversationBoundary, TaskRunProjection]:
        """Atomically bind one root to its task-local work and UI identity."""

        common = (
            work_context.session_id,
            work_context.root_run_id,
            work_context.task_scope_id,
        )
        if (
            common
            != (
                conversation.session_id,
                conversation.root_run_id,
                conversation.task_scope_id,
            )
            or common
            != (
                projection.session_id,
                projection.root_run_id,
                projection.task_scope_id,
            )
        ):
            raise IdempotencyConflict(
                "task_context_identity_mismatch",
                "work, conversation, and projection identities must match",
            )
        if (
            conversation.version != 1
            or conversation.continuation_message_refs
            or projection.version != 0
        ):
            raise IdempotencyConflict(
                "invalid_initial_task_context",
                "new task context must begin at boundary v1/projection v0",
            )

        async with self._write_transaction() as db:
            run = await (
                await db.execute(
                    """SELECT session_id,root_run_id,parent_run_id
                    FROM execution_runs WHERE run_id=?""",
                    (work_context.root_run_id,),
                )
            ).fetchone()
            if (
                run is None
                or str(run["session_id"]) != work_context.session_id
                or str(run["root_run_id"]) != work_context.root_run_id
                or run["parent_run_id"] is not None
            ):
                raise IdempotencyConflict(
                    "task_context_root_conflict",
                    "task context requires the matching persisted root run",
                )

            work_row = await (
                await db.execute(
                    """SELECT * FROM execution_task_work_contexts
                    WHERE root_run_id=? OR task_scope_id=?""",
                    (
                        work_context.root_run_id,
                        work_context.task_scope_id,
                    ),
                )
            ).fetchone()
            if work_row is None:
                now = float(self._clock())
                await db.execute(
                    """INSERT INTO execution_task_work_contexts(
                    root_run_id,schema_version,session_id,task_scope_id,
                    workspace_root,workspace_source,binding_version,
                    created_at,updated_at
                    ) VALUES(?,1,?,?,?,?,?,?,?)""",
                    (
                        work_context.root_run_id,
                        work_context.session_id,
                        work_context.task_scope_id,
                        work_context.workspace_root,
                        work_context.workspace_source,
                        work_context.binding_version,
                        now,
                        now,
                    ),
                )
                work_row = await (
                    await db.execute(
                        """SELECT * FROM execution_task_work_contexts
                        WHERE root_run_id=?""",
                        (work_context.root_run_id,),
                    )
                ).fetchone()
            stored_work = self._row_to_task_work_context(work_row)
            if stored_work != work_context:
                raise IdempotencyConflict(
                    "task_work_context_conflict",
                    "root task workspace identity already differs",
                )

            conversation_row = await (
                await db.execute(
                    """SELECT * FROM execution_conversation_boundaries
                    WHERE boundary_ref=? OR root_run_id=? OR task_scope_id=?""",
                    (
                        conversation.boundary_ref,
                        conversation.root_run_id,
                        conversation.task_scope_id,
                    ),
                )
            ).fetchone()
            if conversation_row is None:
                now = float(self._clock())
                await db.execute(
                    """INSERT INTO execution_conversation_boundaries(
                    boundary_ref,schema_version,session_id,root_run_id,
                    task_scope_id,seed_message_refs_json,
                    continuation_message_refs_json,boundary_version,
                    created_at,updated_at
                    ) VALUES(?,1,?,?,?,?,?,1,?,?)""",
                    (
                        conversation.boundary_ref,
                        conversation.session_id,
                        conversation.root_run_id,
                        conversation.task_scope_id,
                        json.dumps(
                            list(conversation.seed_message_refs),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "[]",
                        now,
                        now,
                    ),
                )
                conversation_row = await (
                    await db.execute(
                        """SELECT * FROM execution_conversation_boundaries
                        WHERE boundary_ref=?""",
                        (conversation.boundary_ref,),
                    )
                ).fetchone()
            stored_conversation = self._row_to_conversation_boundary(
                conversation_row
            )
            if stored_conversation != conversation:
                raise IdempotencyConflict(
                    "conversation_boundary_conflict",
                    "root conversation identity already differs",
                )

            projection_row = await (
                await db.execute(
                    """SELECT * FROM execution_task_run_projections
                    WHERE projection_id=? OR root_run_id=? OR task_scope_id=?""",
                    (
                        projection.projection_id,
                        projection.root_run_id,
                        projection.task_scope_id,
                    ),
                )
            ).fetchone()
            if projection_row is None:
                now = float(self._clock())
                await db.execute(
                    """INSERT INTO execution_task_run_projections(
                    projection_id,schema_version,session_id,root_run_id,
                    task_scope_id,ui_state,projection_version,
                    created_at,updated_at
                    ) VALUES(?,1,?,?,?,?,0,?,?)""",
                    (
                        projection.projection_id,
                        projection.session_id,
                        projection.root_run_id,
                        projection.task_scope_id,
                        projection.ui_state,
                        now,
                        now,
                    ),
                )
                projection_row = await (
                    await db.execute(
                        """SELECT * FROM execution_task_run_projections
                        WHERE projection_id=?""",
                        (projection.projection_id,),
                    )
                ).fetchone()
            stored_projection = self._row_to_task_run_projection(
                projection_row
            )
            if stored_projection != projection:
                raise IdempotencyConflict(
                    "task_run_projection_conflict",
                    "root task projection identity already differs",
                )
            return stored_work, stored_conversation, stored_projection

    async def get_task_work_context(
        self, root_run_id: str
    ) -> TaskWorkContext | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_task_work_contexts
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            return (
                None if row is None else self._row_to_task_work_context(row)
            )

    async def rebind_task_workspace_for_project(
        self,
        root_run_id: str,
        project_root: str,
        *,
        expected_binding_version: int,
    ) -> TaskWorkContext:
        """Fenced one-time user rebind before any write or child execution.

        A fresh task may start in its generated default workspace or inherit the
        Session's previous project.  Creating a different project must be able
        to replace either provisional binding after the native picker confirms
        the new root.
        """

        resolved = str(Path(project_root).expanduser().resolve(strict=False))
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_task_work_contexts
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            if row is None:
                raise IdempotencyConflict(
                    "task_work_context_missing",
                    "project directory selection requires a task context",
                )
            current = self._row_to_task_work_context(row)
            if (
                current.workspace_source == "user_path"
                and current.workspace_root == resolved
                and current.binding_version == expected_binding_version + 1
            ):
                return current
            if (
                current.workspace_source not in {"task_default", "existing"}
                or current.binding_version != expected_binding_version
            ):
                raise IdempotencyConflict(
                    "task_workspace_rebind_conflict",
                    "the task workspace is already bound",
                )
            child = await (
                await db.execute(
                    """SELECT run_id FROM execution_runs
                    WHERE root_run_id=? AND parent_run_id IS NOT NULL
                    LIMIT 1""",
                    (root_run_id,),
                )
            ).fetchone()
            if child is not None:
                raise IdempotencyConflict(
                    "task_workspace_rebind_after_child",
                    "project location must be selected before child work starts",
                )
            write_effect = await (
                await db.execute(
                    """SELECT effect_id FROM execution_effects
                    WHERE run_id=? AND effect_type<>'idempotent_read'
                    LIMIT 1""",
                    (root_run_id,),
                )
            ).fetchone()
            if write_effect is not None:
                raise IdempotencyConflict(
                    "task_workspace_rebind_after_effect",
                    "project location must be selected before project writes",
                )
            run_row = await (
                await db.execute(
                    """SELECT session_id,venue,workspace_json
                    FROM execution_runs WHERE run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            if run_row is None:
                raise IdempotencyConflict(
                    "task_workspace_run_missing",
                    "project directory selection lost its root Run",
                )
            workspace = json.loads(str(run_row["workspace_json"]))
            workspace.update(root=resolved, write_scope_root=resolved)
            workspace["scope_hash"] = fingerprint_json(
                {
                    "session_id": str(run_row["session_id"]),
                    "workspace": resolved,
                    "write_scope_root": resolved,
                    "venue": str(run_row["venue"]),
                }
            )
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_task_work_contexts
                SET workspace_root=?,workspace_source='user_path',
                    binding_version=binding_version+1,updated_at=?
                WHERE root_run_id=?
                  AND workspace_source IN ('task_default','existing')
                  AND binding_version=?""",
                (
                    resolved,
                    now,
                    root_run_id,
                    expected_binding_version,
                ),
            )
            if cursor.rowcount != 1:
                raise IdempotencyConflict(
                    "task_workspace_rebind_conflict",
                    "the task workspace binding changed concurrently",
                )
            await db.execute(
                """UPDATE execution_runs
                SET workspace_json=?,version=version+1,updated_at=?
                WHERE run_id=?""",
                (
                    json.dumps(
                        workspace,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now,
                    root_run_id,
                ),
            )
            updated = await (
                await db.execute(
                    """SELECT * FROM execution_task_work_contexts
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            return self._row_to_task_work_context(updated)

    async def get_conversation_boundary(
        self, root_run_id: str
    ) -> ConversationBoundary | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_conversation_boundaries
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            return (
                None
                if row is None
                else self._row_to_conversation_boundary(row)
            )

    async def enqueue_user_continuation(
        self,
        root_run_id: str,
        message_ref: str,
        content: str,
        *,
        task_scope_id: str,
        expected_boundary_version: int,
    ) -> tuple[ConversationBoundary, QueuedUserContinuation, bool]:
        """Reserve one root-local message and durable FIFO position atomically."""

        content = str(content)
        if not message_ref or not content.strip():
            raise ValueError("continuation message reference and content are required")
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_conversation_boundaries
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            if row is None:
                raise RunNotFound(
                    "conversation_boundary_not_found",
                    f"conversation boundary does not exist: {root_run_id}",
                )
            current = self._row_to_conversation_boundary(row)
            if current.task_scope_id != task_scope_id:
                raise IdempotencyConflict(
                    "conversation_task_scope_mismatch",
                    "continuation does not target the root task scope",
                )
            queued_row = await (
                await db.execute(
                    """SELECT * FROM execution_user_continuations
                    WHERE root_run_id=? AND message_ref=?""",
                    (root_run_id, message_ref),
                )
            ).fetchone()
            if queued_row is not None:
                queued = self._row_to_user_continuation(queued_row)
                if queued.task_scope_id != task_scope_id or queued.content != content:
                    raise IdempotencyConflict(
                        "user_continuation_replay_conflict",
                        "continuation replay differs from the reserved message",
                    )
                return current, queued, True
            if message_ref in current.seed_message_refs:
                raise IdempotencyConflict(
                    "continuation_message_ref_conflict",
                    "a seed message cannot be replayed as a continuation",
                )
            now = float(self._clock())
            if message_ref in current.continuation_message_refs:
                reserved_version = (
                    current.continuation_message_refs.index(message_ref) + 2
                )
                await db.execute(
                    """INSERT INTO execution_user_continuations(
                    root_run_id,message_ref,schema_version,task_scope_id,
                    content,reserved_boundary_version,status,created_at,
                    settled_at,error
                    ) VALUES(?,?,1,?,?,?,'bound',?,?,NULL)""",
                    (
                        root_run_id,
                        message_ref,
                        task_scope_id,
                        content,
                        reserved_version,
                        now,
                        now,
                    ),
                )
                queued_row = await (
                    await db.execute(
                        """SELECT * FROM execution_user_continuations
                        WHERE root_run_id=? AND message_ref=?""",
                        (root_run_id, message_ref),
                    )
                ).fetchone()
                return current, self._row_to_user_continuation(queued_row), True
            if current.version != expected_boundary_version:
                raise VersionConflict(
                    "stale_conversation_boundary",
                    "conversation boundary version changed",
                )
            run = await self._continuation_run_tx(db, root_run_id)
            status = RunStatus(str(run["status"]))
            if (
                status in TERMINAL_RUN_STATUSES
                or status is RunStatus.CANCEL_REQUESTED
            ):
                raise TerminalConflict(
                    "run_already_terminal",
                    "a terminal or cancelling run cannot accept continuation",
                )
            updated = current.append(
                message_ref,
                expected_version=expected_boundary_version,
            )
            run_cursor = await db.execute(
                """UPDATE execution_runs
                SET version=version+1,updated_at=?
                WHERE run_id=? AND version=? AND status IN (
                    'created','queued','running','waiting'
                )""",
                (
                    now,
                    root_run_id,
                    int(run["version"]),
                ),
            )
            if run_cursor.rowcount != 1:
                raise VersionConflict(
                    "user_continuation_run_fence_conflict",
                    "run changed while reserving the continuation",
                )
            await db.execute(
                """INSERT INTO execution_user_continuations(
                root_run_id,message_ref,schema_version,task_scope_id,content,
                reserved_boundary_version,status,created_at,settled_at,error
                ) VALUES(?,?,1,?,?,?,'pending',?,NULL,NULL)""",
                (
                    root_run_id,
                    message_ref,
                    task_scope_id,
                    content,
                    updated.version,
                    now,
                ),
            )
            cursor = await db.execute(
                """UPDATE execution_conversation_boundaries
                SET continuation_message_refs_json=?,
                    boundary_version=?,updated_at=?
                WHERE root_run_id=? AND boundary_version=?""",
                (
                    json.dumps(
                        list(updated.continuation_message_refs),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    updated.version,
                    now,
                    root_run_id,
                    expected_boundary_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_conversation_boundary",
                    "conversation boundary version changed",
                )
            queued = QueuedUserContinuation(
                root_run_id=root_run_id,
                task_scope_id=task_scope_id,
                message_ref=message_ref,
                content=content,
                reserved_boundary_version=updated.version,
                created_at=now,
            )
            return updated, queued, False

    async def get_next_user_continuation(
        self, root_run_id: str
    ) -> QueuedUserContinuation | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_user_continuations
                    WHERE root_run_id=? AND status='pending'
                    ORDER BY reserved_boundary_version
                    LIMIT 1""",
                    (root_run_id,),
                )
            ).fetchone()
            return (
                None if row is None else self._row_to_user_continuation(row)
            )

    async def get_user_continuation(
        self, root_run_id: str, message_ref: str
    ) -> QueuedUserContinuation | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_user_continuations
                    WHERE root_run_id=? AND message_ref=?""",
                    (root_run_id, message_ref),
                )
            ).fetchone()
            return (
                None if row is None else self._row_to_user_continuation(row)
            )

    async def fail_user_continuation(
        self, root_run_id: str, message_ref: str, error: str
    ) -> QueuedUserContinuation:
        error = str(error).strip() or "continuation_apply_failed"
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_user_continuations
                    WHERE root_run_id=? AND message_ref=?""",
                    (root_run_id, message_ref),
                )
            ).fetchone()
            if row is None:
                raise RunNotFound(
                    "user_continuation_not_found",
                    f"queued continuation does not exist: {message_ref}",
                )
            current = self._row_to_user_continuation(row)
            if current.status == "failed":
                return current
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_user_continuations
                SET status='failed',settled_at=?,error=?
                WHERE root_run_id=? AND message_ref=?
                    AND status IN ('pending','bound')""",
                (now, error, root_run_id, message_ref),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "user_continuation_failure_conflict",
                    "continuation state changed while recording failure",
                )
            failed = await (
                await db.execute(
                    """SELECT * FROM execution_user_continuations
                    WHERE root_run_id=? AND message_ref=?""",
                    (root_run_id, message_ref),
                )
            ).fetchone()
            return self._row_to_user_continuation(failed)

    async def commit_user_continuation(
        self,
        root_run_id: str,
        message_ref: str,
        *,
        task_scope_id: str,
        expected_boundary_version: int,
        expected_continuation_version: int,
        continuation_payload: Mapping[str, Any],
        event: RunEventCandidate,
    ) -> tuple[ConversationBoundary, ContinuationRecord, RunEvent]:
        """Atomically append one root-local user message and Driver boundary."""

        if not message_ref:
            raise ValueError("continuation message reference is required")
        payload_json = self._continuation_payload_json(continuation_payload)
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_conversation_boundaries
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            if row is None:
                raise RunNotFound(
                    "conversation_boundary_not_found",
                    f"conversation boundary does not exist: {root_run_id}",
                )
            current = self._row_to_conversation_boundary(row)
            if current.task_scope_id != task_scope_id:
                raise IdempotencyConflict(
                    "conversation_task_scope_mismatch",
                    "continuation does not target the root task scope",
                )
            if message_ref in current.seed_message_refs:
                raise IdempotencyConflict(
                    "continuation_message_ref_conflict",
                    "a seed message cannot be replayed as a continuation",
                )
            run = await self._continuation_run_tx(db, root_run_id)
            if message_ref in current.continuation_message_refs:
                saved = await self._load_continuation_tx(db, root_run_id)
                if saved is None:
                    raise IdempotencyConflict(
                        "continuation_record_missing",
                        "root conversation exists without a Driver continuation",
                    )
                stored_event, _, _ = await self._append_event_tx(
                    db,
                    run,
                    expected_version=int(run["version"]),
                    event=event,
                    deliveries=(),
                )
                return current, saved, stored_event
            if current.version != expected_boundary_version:
                raise VersionConflict(
                    "stale_conversation_boundary",
                    "conversation boundary version changed",
                )
            saved, _ = await self._save_continuation_tx(
                db,
                run=run,
                expected_version=expected_continuation_version,
                payload_json=payload_json,
                decision=None,
                now=float(self._clock()),
            )
            updated = current.append(
                message_ref,
                expected_version=expected_boundary_version,
            )
            cursor = await db.execute(
                """UPDATE execution_conversation_boundaries
                SET continuation_message_refs_json=?,
                    boundary_version=?,updated_at=?
                WHERE root_run_id=? AND boundary_version=?""",
                (
                    json.dumps(
                        list(updated.continuation_message_refs),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    updated.version,
                    float(self._clock()),
                    root_run_id,
                    expected_boundary_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_conversation_boundary",
                    "conversation boundary version changed",
                )
            stored_event, _, _ = await self._append_event_tx(
                db,
                run,
                expected_version=int(run["version"]),
                event=event,
                deliveries=(),
            )
            return updated, saved, stored_event

    async def commit_queued_user_continuation(
        self,
        root_run_id: str,
        message_ref: str,
        *,
        task_scope_id: str,
        expected_continuation_version: int,
        continuation_payload: Mapping[str, Any],
        event: RunEventCandidate,
    ) -> tuple[ConversationBoundary, ContinuationRecord, RunEvent]:
        """Apply the oldest reserved message with the Driver boundary atomically."""

        payload_json = self._continuation_payload_json(continuation_payload)
        async with self._write_transaction() as db:
            queue_row = await (
                await db.execute(
                    """SELECT * FROM execution_user_continuations
                    WHERE root_run_id=? AND message_ref=?""",
                    (root_run_id, message_ref),
                )
            ).fetchone()
            if queue_row is None:
                raise RunNotFound(
                    "user_continuation_not_found",
                    f"queued continuation does not exist: {message_ref}",
                )
            queued = self._row_to_user_continuation(queue_row)
            if queued.task_scope_id != task_scope_id:
                raise IdempotencyConflict(
                    "conversation_task_scope_mismatch",
                    "continuation does not target the root task scope",
                )
            conversation_row = await (
                await db.execute(
                    """SELECT * FROM execution_conversation_boundaries
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            if conversation_row is None:
                raise RunNotFound(
                    "conversation_boundary_not_found",
                    f"conversation boundary does not exist: {root_run_id}",
                )
            conversation = self._row_to_conversation_boundary(
                conversation_row
            )
            if (
                conversation.task_scope_id != task_scope_id
                or message_ref not in conversation.continuation_message_refs
            ):
                raise IdempotencyConflict(
                    "queued_conversation_boundary_mismatch",
                    "queued continuation is outside the root conversation",
                )
            run = await self._continuation_run_tx(db, root_run_id)
            if queued.status == "bound":
                saved = await self._load_continuation_tx(db, root_run_id)
                if saved is None:
                    raise IdempotencyConflict(
                        "continuation_record_missing",
                        "applied continuation has no Driver boundary",
                    )
                stored_event, _, _ = await self._append_event_tx(
                    db,
                    run,
                    expected_version=int(run["version"]),
                    event=event,
                    deliveries=(),
                )
                return conversation, saved, stored_event
            if queued.status == "failed":
                raise TerminalConflict(
                    "user_continuation_failed",
                    "failed continuation cannot be rebound",
                )
            oldest = await (
                await db.execute(
                    """SELECT message_ref FROM execution_user_continuations
                    WHERE root_run_id=? AND status='pending'
                    ORDER BY reserved_boundary_version
                    LIMIT 1""",
                    (root_run_id,),
                )
            ).fetchone()
            if oldest is None or str(oldest["message_ref"]) != message_ref:
                raise IdempotencyConflict(
                    "user_continuation_order_conflict",
                    "queued continuations must be applied in FIFO order",
                )
            saved, _ = await self._save_continuation_tx(
                db,
                run=run,
                expected_version=expected_continuation_version,
                payload_json=payload_json,
                decision=None,
                now=float(self._clock()),
            )
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_user_continuations
                SET status='bound',settled_at=?
                WHERE root_run_id=? AND message_ref=? AND status='pending'""",
                (now, root_run_id, message_ref),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "user_continuation_claim_conflict",
                    "queued continuation was already applied",
                )
            stored_event, _, _ = await self._append_event_tx(
                db,
                run,
                expected_version=int(run["version"]),
                event=event,
                deliveries=(),
            )
            return conversation, saved, stored_event

    async def get_task_run_projection(
        self, root_run_id: str
    ) -> TaskRunProjection | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_task_run_projections
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            return (
                None if row is None else self._row_to_task_run_projection(row)
            )

    async def list_task_run_projections(
        self,
        session_id: str,
        *,
        include_closed: bool = False,
    ) -> tuple[TaskRunProjection, ...]:
        where = "session_id=?" if include_closed else (
            "session_id=? AND ui_state<>'closed'"
        )
        async with self._read_connection() as db:
            rows = await (
                await db.execute(
                    f"""SELECT * FROM execution_task_run_projections
                    WHERE {where}
                    ORDER BY updated_at DESC,projection_id ASC""",
                    (session_id,),
                )
            ).fetchall()
            return tuple(
                self._row_to_task_run_projection(row) for row in rows
            )

    async def get_task_run_lifecycle(
        self,
        root_run_id: str,
        *,
        expected_session_id: str,
    ) -> Mapping[str, Any] | None:
        """Return the durable lifecycle used to restore a task's UI projection.

        The session fence is part of the query so projection hydration cannot
        disclose or attach lifecycle state from another main session.
        """

        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT status,started_at,updated_at,ended_at
                    FROM execution_runs
                    WHERE run_id=? AND root_run_id=run_id AND session_id=?""",
                    (root_run_id, expected_session_id),
                )
            ).fetchone()
            if row is None:
                return None
            return {
                "status": str(row["status"]),
                "started_at": (
                    None
                    if row["started_at"] is None
                    else float(row["started_at"])
                ),
                "updated_at": float(row["updated_at"]),
                "ended_at": (
                    None if row["ended_at"] is None else float(row["ended_at"])
                ),
            }

    async def inspect_harness_run(
        self,
        root_run_id: str,
        *,
        expected_session_id: str,
    ) -> Mapping[str, Any] | None:
        """Return a payload-safe, durable projection of one Harness Run.

        This is an observability read model only. It intentionally projects
        the existing execution ledger instead of maintaining a second state
        machine. Detailed payloads are consumed by the local inspector UI and
        shown verbatim when the user expands a record.
        """

        run_key = str(root_run_id or "").strip()
        session_key = str(expected_session_id or "").strip()
        if not run_key or not session_key:
            raise ValueError("run_id and expected_session_id are required")

        def _json(value: Any, default: Any) -> Any:
            if value is None:
                return default
            try:
                parsed = json.loads(str(value))
            except (TypeError, ValueError, json.JSONDecodeError):
                return default
            return parsed

        def _prepared_call_id(value: Any) -> str:
            prepared = _json(value, {})
            if not isinstance(prepared, Mapping):
                return ""
            return str(prepared.get("stable_call_id") or "")

        def _optional_float(value: Any) -> float | None:
            return None if value is None else float(value)

        def _iteration(group_id: str) -> int | None:
            marker = ":iter:"
            if marker not in group_id:
                return None
            suffix = group_id.split(marker, 1)[1].split(":", 1)[0]
            try:
                return int(suffix)
            except ValueError:
                return None

        def _checkpoint_payload(value: Any) -> Mapping[str, Any]:
            if value is None:
                return {}
            try:
                raw = bytes(value) if not isinstance(value, bytes) else value
                parsed = json.loads(raw.decode("utf-8"))
            except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                return {}
            return parsed if isinstance(parsed, Mapping) else {}

        def _public_message(value: Any) -> str:
            if not isinstance(value, str):
                return ""
            return re.sub(
                r"<think>[\s\S]*?(?:</think>|$)",
                "",
                value,
                flags=re.IGNORECASE,
            ).strip()

        async with self._read_connection() as db:
            run = await (
                await db.execute(
                    """SELECT * FROM execution_runs
                    WHERE run_id=? AND root_run_id=run_id AND session_id=?""",
                    (run_key, session_key),
                )
            ).fetchone()
            if run is None:
                return None

            snapshot = await (
                await db.execute(
                    """SELECT * FROM execution_run_start_snapshots
                    WHERE run_id=?""",
                    (run_key,),
                )
            ).fetchone()
            continuation = await (
                await db.execute(
                    """SELECT iteration,continuation_version,
                    pending_decision_id,pending_prepared_call_json,updated_at
                    FROM execution_continuations WHERE run_id=?""",
                    (run_key,),
                )
            ).fetchone()
            projection = await (
                await db.execute(
                    """SELECT task_scope_id,ui_state,projection_version,
                    created_at,updated_at
                    FROM execution_task_run_projections
                    WHERE root_run_id=?""",
                    (run_key,),
                )
            ).fetchone()
            goal = await (
                await db.execute(
                    """SELECT goal_id,task_scope_id,status,goal_version,
                    created_at,updated_at,ended_at
                    FROM execution_task_goals WHERE root_run_id=?""",
                    (run_key,),
                )
            ).fetchone()
            latest_plan = await (
                await db.execute(
                    """SELECT plan_version,trigger_failure_set_id,created_at
                    FROM execution_plan_versions WHERE root_run_id=?
                    ORDER BY plan_version DESC LIMIT 1""",
                    (run_key,),
                )
            ).fetchone()

            lineage_rows = await (
                await db.execute(
                    """SELECT run_id,parent_run_id,driver_kind,profile_key,
                    status,version,created_at,started_at,updated_at,ended_at
                    FROM execution_runs WHERE root_run_id=?
                    ORDER BY created_at,run_id LIMIT 200""",
                    (run_key,),
                )
            ).fetchall()
            provider_rows = await (
                await db.execute(
                    """SELECT i.run_id,i.invocation_id,i.provider_id,i.model_id,
                    i.adapter_id,i.idempotency_group_id,i.attempt_ordinal,
                    i.status,i.claimed_at,i.dispatch_started_at,i.updated_at,
                    i.outcome_ref,i.request_hash,i.policy_snapshot_json,
                    p.payload_json AS input_payload_json,
                    o.payload_json AS outcome_payload_json,
                    a.reason AS audit_reason,
                    a.error_type AS audit_error_type,
                    a.error_message AS audit_error_message
                    FROM execution_provider_invocations AS i
                    JOIN execution_runs AS r ON r.run_id=i.run_id
                    LEFT JOIN execution_provider_invocation_inputs AS p
                      ON p.run_id=i.run_id
                     AND p.invocation_id=i.invocation_id
                    LEFT JOIN execution_provider_invocation_outcomes AS o
                      ON o.run_id=i.run_id
                     AND o.invocation_id=i.invocation_id
                    LEFT JOIN execution_provider_invocation_audits AS a
                      ON a.run_id=i.run_id
                     AND a.invocation_id=i.invocation_id
                    WHERE r.root_run_id=?
                    ORDER BY i.claimed_at,i.invocation_id LIMIT 200""",
                    (run_key,),
                )
            ).fetchall()
            batch_rows = await (
                await db.execute(
                    """SELECT provider_batch_id,provider_turn_id,
                    pending_call_count,failure_set_id,status,batch_version,
                    created_at,updated_at,settled_at
                    FROM execution_provider_action_batches
                    WHERE root_run_id=?
                    ORDER BY created_at,provider_batch_id LIMIT 200""",
                    (run_key,),
                )
            ).fetchall()
            call_rows = await (
                await db.execute(
                    """SELECT call_record_id,provider_batch_id,call_order,
                    provider_call_id,raw_tool_name,admission_state,
                    raw_arguments_ref,raw_arguments_hash,
                    parsed_arguments_hash,prepared_call_ref,
                    terminal_outcome_ref,call_version,created_at,updated_at
                    FROM execution_provider_action_calls
                    WHERE root_run_id=?
                    ORDER BY created_at,provider_batch_id,call_order LIMIT 400""",
                    (run_key,),
                )
            ).fetchall()
            effect_rows = await (
                await db.execute(
                    """SELECT e.effect_id,e.run_id,e.call_id,e.tool_name,
                    e.effect_type,e.status,e.handoff_state,
                    e.completion_disposition,e.receipt_ref,e.policy_json,
                    e.prepared_json,e.outcome_json,e.artifact_refs_json,
                    e.created_at,e.updated_at,e.ended_at
                    FROM execution_effects AS e
                    JOIN execution_runs AS r ON r.run_id=e.run_id
                    WHERE r.root_run_id=?
                    ORDER BY e.created_at,e.effect_id LIMIT 400""",
                    (run_key,),
                )
            ).fetchall()
            workflow_effect_rows = await (
                await db.execute(
                    """SELECT e.effect_id,e.run_id,e.status,e.prepared_json,
                    e.outcome_json,e.receipt_ref,e.artifact_refs_json,
                    e.started_at,e.updated_at,e.ended_at
                    FROM workflow_effects AS e
                    JOIN execution_runs AS r ON r.run_id=e.run_id
                    WHERE r.root_run_id=?
                    ORDER BY e.started_at,e.effect_id LIMIT 400""",
                    (run_key,),
                )
            ).fetchall()
            workflow_checkpoint_rows = await (
                await db.execute(
                    """SELECT wr.run_id,wc.checkpoint_blob
                    FROM workflow_runs AS wr
                    JOIN execution_runs AS er ON er.run_id=wr.run_id
                    LEFT JOIN workflow_checkpoints AS wc
                      ON wc.thread_id=wr.thread_id
                     AND wc.checkpoint_ns=wr.head_checkpoint_ns
                     AND wc.checkpoint_id=wr.head_checkpoint_id
                    WHERE er.root_run_id=?
                      AND wr.workflow_name='durable_task'
                    ORDER BY er.created_at,wr.run_id LIMIT 100""",
                    (run_key,),
                )
            ).fetchall()
            attempt_rows = await (
                await db.execute(
                    """SELECT attempt_id,run_id,provider_turn_id,
                    provider_batch_id,plan_version,trigger_failure_set_id,
                    supersedes_attempt_id,status,attempt_version,created_at,
                    updated_at,ended_at
                    FROM execution_attempt_records WHERE root_run_id=?
                    ORDER BY created_at,attempt_id LIMIT 200""",
                    (run_key,),
                )
            ).fetchall()
            failure_rows = await (
                await db.execute(
                    """SELECT report_ref,run_id,attempt_id,plan_version,
                    provider_call_id,child_run_id,failed_call_id,
                    failed_effect_id,failed_step,error_class,error_code,
                    source_kind,source_identity,created_at
                    FROM execution_task_failure_reports WHERE root_run_id=?
                    ORDER BY created_at,report_ref LIMIT 400""",
                    (run_key,),
                )
            ).fetchall()
            event_rows = await (
                await db.execute(
                    """SELECT e.event_id,e.run_id,e.durable_seq,e.kind,
                    e.status,e.driver_kind,e.correlation_json,e.payload_json,
                    e.error_json,e.created_at
                    FROM execution_events AS e
                    JOIN execution_runs AS r ON r.run_id=e.run_id
                    WHERE r.root_run_id=?
                    ORDER BY e.created_at,e.run_id,e.durable_seq LIMIT 400""",
                    (run_key,),
                )
            ).fetchall()

            pending = (
                _json(continuation["pending_prepared_call_json"], {})
                if continuation is not None
                else {}
            )
            pending_calls = (
                pending.get("calls", [])
                if isinstance(pending, Mapping)
                else []
            )
            pending_tool_names = [
                str(item.get("tool_name") or "")
                for item in pending_calls
                if isinstance(item, Mapping) and item.get("tool_name")
            ]
            tool_observations_by_call: dict[str, dict[str, str | None]] = {}
            completion_state = (
                pending.get("completion_state")
                if isinstance(pending, Mapping)
                else None
            )
            canonical_messages = (
                pending.get("canonical_messages", [])
                if isinstance(pending, Mapping)
                else []
            )
            if isinstance(canonical_messages, list):
                for message in canonical_messages:
                    if (
                        not isinstance(message, Mapping)
                        or message.get("role") != "tool"
                    ):
                        continue
                    call_id = str(
                        message.get("tool_call_id") or ""
                    ).strip()
                    content = message.get("content")
                    payload = (
                        _json(content, {})
                        if isinstance(content, str)
                        else content
                    )
                    if not call_id or not isinstance(payload, Mapping):
                        continue
                    outcome = payload.get("outcome")
                    outcome = (
                        outcome if isinstance(outcome, Mapping) else {}
                    )
                    raw_status = str(
                        payload.get("outcome_status")
                        or payload.get("status")
                        or outcome.get("state")
                        or ""
                    ).strip()
                    status = {
                        "success": "succeeded",
                        "failure": "failed",
                    }.get(raw_status, raw_status)
                    error = outcome.get("error")
                    if isinstance(error, Mapping):
                        error_code = str(error.get("code") or "").strip()
                        error_message = str(
                            error.get("message") or ""
                        ).strip()
                    else:
                        error_code = ""
                        error_message = (
                            str(error).strip() if error is not None else ""
                        )
                    tool_observations_by_call[call_id] = {
                        "status": status or None,
                        "error_code": error_code or None,
                        "error_message": (
                            error_message[:1000] or None
                        ),
                    }
            feedback = (
                completion_state.get("agent_loop_feedback")
                if isinstance(completion_state, Mapping)
                else None
            )
            tool_history = (
                feedback.get("tool_history")
                if isinstance(feedback, Mapping)
                else None
            )
            if isinstance(tool_history, list):
                for item in tool_history:
                    if not isinstance(item, Mapping):
                        continue
                    call_id = str(item.get("call_id") or "").strip()
                    ok = item.get("ok")
                    if not call_id or not isinstance(ok, bool):
                        continue
                    observation = tool_observations_by_call.setdefault(
                        call_id,
                        {
                            "status": None,
                            "error_code": None,
                            "error_message": None,
                        },
                    )
                    if not observation.get("status"):
                        observation["status"] = (
                            "succeeded" if ok else "failed"
                        )

            pending_outcome_status_by_call: dict[str, str] = {
                call_id: str(observation["status"])
                for call_id, observation in tool_observations_by_call.items()
                if observation.get("status")
            }
            pending_outcome_statuses = (
                pending.get("outcome_statuses", [])
                if isinstance(pending, Mapping)
                else []
            )
            if isinstance(pending_outcome_statuses, list):
                for index, call in enumerate(pending_calls):
                    if (
                        not isinstance(call, Mapping)
                        or index >= len(pending_outcome_statuses)
                    ):
                        continue
                    call_id = str(call.get("stable_call_id") or "").strip()
                    outcome_status = str(
                        pending_outcome_statuses[index] or ""
                    ).strip()
                    if call_id and outcome_status:
                        pending_outcome_status_by_call[call_id] = (
                            outcome_status
                        )
            observed_statuses_by_batch: dict[str, list[str | None]] = {}
            for item in call_rows:
                batch_id = str(item["provider_batch_id"])
                call_id = str(item["provider_call_id"])
                observed_statuses_by_batch.setdefault(batch_id, []).append(
                    pending_outcome_status_by_call.get(call_id)
                )
            observed_batch_status: dict[str, str] = {}
            terminal_tool_statuses = {
                "succeeded",
                "failed",
                "cancelled",
                "unknown",
                "rejected",
                "skipped",
            }
            for batch_id, statuses in observed_statuses_by_batch.items():
                concrete = [status for status in statuses if status]
                if (
                    len(concrete) != len(statuses)
                    or any(
                        status not in terminal_tool_statuses
                        for status in concrete
                    )
                ):
                    continue
                if any(
                    status in {"failed", "unknown", "rejected"}
                    for status in concrete
                ):
                    observed_batch_status[batch_id] = "failed"
                elif any(status == "cancelled" for status in concrete):
                    observed_batch_status[batch_id] = "cancelled"
                else:
                    observed_batch_status[batch_id] = "succeeded"
            pending_current_attempt = None
            if isinstance(completion_state, Mapping):
                current_attempt = completion_state.get("current_attempt")
                if isinstance(current_attempt, Mapping):
                    pending_current_attempt = current_attempt
            providers = []
            for item in provider_rows:
                started_at = _optional_float(item["dispatch_started_at"])
                updated_at = float(item["updated_at"])
                status = str(item["status"])
                audit_reason = (
                    None
                    if item["audit_reason"] is None
                    else str(item["audit_reason"])
                )
                if status == "unknown" and audit_reason is None:
                    audit_reason = "legacy_unknown_reason_not_recorded"
                providers.append(
                    {
                        "run_id": str(item["run_id"]),
                        "invocation_id": str(item["invocation_id"]),
                        "provider_id": str(item["provider_id"]),
                        "model_id": str(item["model_id"]),
                        "adapter_id": str(item["adapter_id"]),
                        "iteration": _iteration(
                            str(item["idempotency_group_id"])
                        ),
                        "attempt_ordinal": int(item["attempt_ordinal"]),
                        "status": status,
                        "claimed_at": float(item["claimed_at"]),
                        "dispatch_started_at": started_at,
                        "updated_at": updated_at,
                        "duration_ms": max(
                            0.0,
                            (
                                updated_at
                                - (
                                    started_at
                                    if started_at is not None
                                    else float(item["claimed_at"])
                                )
                            )
                            * 1000.0,
                        ),
                        "audit_reason": audit_reason,
                        "request_hash": str(item["request_hash"]),
                        "policy": _json(item["policy_snapshot_json"], {}),
                        "input": _json(item["input_payload_json"], None),
                        "output": _json(
                            item["outcome_payload_json"], None
                        ),
                        "error_type": (
                            None
                            if item["audit_error_type"] is None
                            else str(item["audit_error_type"])
                        ),
                        "error_message": (
                            None
                            if item["audit_error_message"] is None
                            else str(item["audit_error_message"])
                        ),
                    }
                )

            workflow_step_by_call: dict[tuple[str, str], str] = {}
            workflow_plans = []
            for item in workflow_checkpoint_rows:
                workflow_run_id = str(item["run_id"])
                checkpoint = _checkpoint_payload(item["checkpoint_blob"])
                state = checkpoint.get("state")
                values = (
                    state.get("values")
                    if isinstance(state, Mapping)
                    else None
                )
                if not isinstance(values, Mapping):
                    continue
                proposal = values.get("proposal_state")
                proposal = (
                    proposal if isinstance(proposal, Mapping) else {}
                )
                committed = proposal.get("committed_tool_results")
                current_results = values.get("tool_results")
                for raw_results in (committed, current_results):
                    if not isinstance(raw_results, Mapping):
                        continue
                    for raw_call_id, raw_result in raw_results.items():
                        if not isinstance(raw_result, Mapping):
                            continue
                        call_id = str(
                            raw_result.get("stable_call_id")
                            or raw_call_id
                            or ""
                        ).strip()
                        step_id = str(
                            raw_result.get("workflow_step_id") or ""
                        ).strip()
                        if call_id and step_id:
                            workflow_step_by_call[
                                (workflow_run_id, call_id)
                            ] = step_id

                raw_todos = values.get("todos")
                steps = []
                if isinstance(raw_todos, list):
                    for index, raw_todo in enumerate(raw_todos):
                        if not isinstance(raw_todo, Mapping):
                            continue
                        step_id = str(
                            raw_todo.get("workflow_step_id") or ""
                        ).strip()
                        title = str(raw_todo.get("title") or "").strip()
                        if not step_id or not title:
                            continue
                        steps.append(
                            {
                                "workflow_step_id": step_id,
                                "index": int(
                                    raw_todo.get("index", index)
                                ),
                                "title": title[:4000],
                                "status": str(
                                    raw_todo.get("status") or "pending"
                                ),
                            }
                        )

                messages = []
                raw_messages = proposal.get("messages")
                if isinstance(raw_messages, list):
                    for index, raw_message in enumerate(raw_messages):
                        if (
                            not isinstance(raw_message, Mapping)
                            or str(raw_message.get("role") or "")
                            != "assistant"
                        ):
                            continue
                        text = _public_message(raw_message.get("content"))
                        raw_calls = raw_message.get("tool_calls")
                        call_ids = []
                        if isinstance(raw_calls, list):
                            call_ids = [
                                str(call.get("id") or "").strip()
                                for call in raw_calls
                                if isinstance(call, Mapping)
                                and str(call.get("id") or "").strip()
                            ]
                        if not text and not call_ids:
                            continue
                        step_id = next(
                            (
                                workflow_step_by_call.get(
                                    (workflow_run_id, call_id)
                                )
                                for call_id in call_ids
                                if workflow_step_by_call.get(
                                    (workflow_run_id, call_id)
                                )
                            ),
                            None,
                        )
                        messages.append(
                            {
                                "message_id": str(
                                    raw_message.get("message_id")
                                    or f"assistant-{index}"
                                ),
                                "text": text,
                                "tool_call_ids": call_ids,
                                "workflow_step_id": step_id,
                            }
                        )
                workflow_plans.append(
                    {
                        "run_id": workflow_run_id,
                        "plan_id": (
                            str(proposal.get("active_plan_id"))
                            if proposal.get("active_plan_id")
                            else None
                        ),
                        "active_step_id": (
                            str(proposal.get("active_step_id"))
                            if proposal.get("active_step_id")
                            else None
                        ),
                        "steps": steps,
                        "public_messages": messages,
                    }
                )

            events = []
            for item in event_rows:
                error = _json(item["error_json"], {})
                correlation = _json(item["correlation_json"], {})
                events.append(
                    {
                        "event_id": str(item["event_id"]),
                        "run_id": str(item["run_id"]),
                        "seq": int(item["durable_seq"]),
                        "kind": str(item["kind"]),
                        "status": str(item["status"]),
                        "driver_kind": str(item["driver_kind"]),
                        "failure_layer": (
                            str(correlation.get("failure_layer"))
                            if isinstance(correlation, Mapping)
                            and correlation.get("failure_layer") is not None
                            else None
                        ),
                        "failure_code": (
                            str(correlation.get("failure_code"))
                            if isinstance(correlation, Mapping)
                            and correlation.get("failure_code") is not None
                            else None
                        ),
                        "error_code": (
                            str(error.get("code"))
                            if isinstance(error, Mapping)
                            and error.get("code") is not None
                            else None
                        ),
                        "error_message": (
                            str(error.get("message"))[:1000]
                            if isinstance(error, Mapping)
                            and error.get("message") is not None
                            else None
                        ),
                        "payload": _json(item["payload_json"], {}),
                        "correlation": correlation,
                        "created_at": float(item["created_at"]),
                    }
                )

            snapshot_request = (
                {}
                if snapshot is None
                else _json(snapshot["sanitized_request_json"], {})
            )
            snapshot_payload = (
                snapshot_request.get("payload", {})
                if isinstance(snapshot_request, Mapping)
                else {}
            )
            product_turn_steps = (
                snapshot_payload.get("product_turn_trace", [])
                if isinstance(snapshot_payload, Mapping)
                else []
            )
            if not isinstance(product_turn_steps, list):
                product_turn_steps = []

            now = float(self._clock())
            run_status = str(run["status"]).split(".")[-1].lower()
            latest_provider = providers[-1] if providers else None
            latest_effect = effect_rows[-1] if effect_rows else None
            active_children = [
                item
                for item in lineage_rows
                if str(item["run_id"]) != run_key
                and str(item["status"]).split(".")[-1].lower()
                not in {"completed", "failed", "cancelled"}
            ]
            progress_timestamps = [
                float(run["updated_at"]),
                *[float(item["updated_at"]) for item in provider_rows],
                *[float(item["updated_at"]) for item in effect_rows],
                *[float(item["updated_at"]) for item in attempt_rows],
                *[float(item["created_at"]) for item in event_rows],
            ]
            if continuation is not None:
                progress_timestamps.append(float(continuation["updated_at"]))
            last_progress_at = max(progress_timestamps)
            stalled_for_seconds = max(0.0, now - last_progress_at)

            phase = "agent"
            waiting_reason = None
            summary = "Agent 正在规划下一步"
            terminal_statuses = {"completed", "failed", "cancelled"}
            if run_status in terminal_statuses:
                phase = "terminal"
                summary = {
                    "completed": "任务已完成",
                    "failed": "任务已失败",
                    "cancelled": "任务已取消",
                }[run_status]
            elif (
                continuation is not None
                and continuation["pending_decision_id"] is not None
            ):
                phase = "authorization"
                waiting_reason = "user_decision"
                summary = "正在等待你的授权或选择"
            elif (
                latest_provider is not None
                and str(latest_provider["status"]).split(".")[-1].lower()
                == "claimed"
            ):
                phase = "provider"
                waiting_reason = "provider_response"
                summary = (
                    f"正在等待模型 {latest_provider['model_id']} 返回结果"
                )
            elif latest_effect is not None and str(
                latest_effect["status"]
            ).split(".")[-1].lower() not in terminal_statuses | {
                "succeeded",
                "rejected",
                "unknown",
            }:
                phase = "tool"
                waiting_reason = "tool_execution"
                summary = f"正在执行工具 {latest_effect['tool_name']}"
            elif active_children:
                phase = "delegated_workflow"
                waiting_reason = "child_run"
                child_profile = str(active_children[-1]["profile_key"])
                child_label = {
                    "workflow.durable_task": "多步骤任务",
                    "workflow.capability_build": "新能力构建任务",
                    "workflow.deep_research": "深度调研任务",
                    "workflow.presentation": "演示文稿任务",
                    "workflow.personal_v1": "个人工作流",
                }.get(child_profile, "委派任务")
                summary = f"正在处理{child_label}，完成后会自动回到当前对话"
            elif run_status == "waiting":
                phase = "waiting"
                waiting_reason = "no_active_operation"
                summary = "任务处于等待状态，但当前没有可见的执行动作"

            error_candidates: list[tuple[float, dict[str, Any]]] = []
            for provider_item in providers:
                if (
                    provider_item.get("error_type")
                    or provider_item.get("error_message")
                    or provider_item.get("audit_reason")
                ):
                    error_candidates.append(
                        (
                            float(provider_item["updated_at"]),
                            {
                                "source": "provider",
                                "code": provider_item.get("audit_reason"),
                                "type": provider_item.get("error_type"),
                                "message": provider_item.get("error_message"),
                            },
                        )
                    )
            for failure_item in failure_rows:
                error_candidates.append(
                    (
                        float(failure_item["created_at"]),
                        {
                            "source": "task_failure",
                            "code": str(failure_item["error_code"]),
                            "type": str(failure_item["error_class"]),
                            "message": tool_observations_by_call.get(
                                str(failure_item["provider_call_id"] or ""),
                                {},
                            ).get("error_message"),
                        },
                    )
                )
            for event_item in events:
                if event_item.get("error_code") or event_item.get(
                    "error_message"
                ):
                    error_candidates.append(
                        (
                            float(event_item["created_at"]),
                            {
                                "source": "event",
                                "code": event_item.get("error_code"),
                                "type": event_item.get("failure_layer"),
                                "message": event_item.get("error_message"),
                            },
                        )
                    )
            last_error = (
                None
                if not error_candidates
                else max(error_candidates, key=lambda item: item[0])[1]
            )
            artifact_count = sum(
                len(refs)
                for refs in (
                    _json(item["artifact_refs_json"], [])
                    for item in effect_rows
                )
                if isinstance(refs, list)
            )

            return {
                "schema_version": 2,
                "refreshed_at": now,
                "session_id": session_key,
                "activity": {
                    "phase": phase,
                    "summary": summary,
                    "waiting_reason": waiting_reason,
                    "last_progress_at": last_progress_at,
                    "stalled_for_seconds": stalled_for_seconds,
                    "is_stalled": (
                        run_status not in terminal_statuses
                        and stalled_for_seconds >= 60.0
                    ),
                    "last_error": last_error,
                    "artifact_count": artifact_count,
                },
                "run": {
                    "run_id": str(run["run_id"]),
                    "root_run_id": str(run["root_run_id"]),
                    "request_id": str(run["request_id"]),
                    "turn_id": str(run["turn_id"]),
                    "trace_id": str(run["trace_id"]),
                    "driver_kind": str(run["driver_kind"]),
                    "profile_key": str(run["profile_key"]),
                    "persistence_level": str(run["persistence_level"]),
                    "status": str(run["status"]),
                    "version": int(run["version"]),
                    "durable_seq": int(run["durable_seq"]),
                    "owner_kind": str(run["owner_kind"]),
                    "owner_generation": int(run["owner_generation"]),
                    "created_at": float(run["created_at"]),
                    "started_at": _optional_float(run["started_at"]),
                    "updated_at": float(run["updated_at"]),
                    "ended_at": _optional_float(run["ended_at"]),
                },
                "projection": (
                    None
                    if projection is None
                    else {
                        "task_scope_id": str(projection["task_scope_id"]),
                        "ui_state": str(projection["ui_state"]),
                        "version": int(projection["projection_version"]),
                        "created_at": float(projection["created_at"]),
                        "updated_at": float(projection["updated_at"]),
                    }
                ),
                "prepared_context": {
                    "available": snapshot is not None,
                    "start_fingerprint": (
                        None
                        if snapshot is None
                        else str(snapshot["start_fingerprint"])
                    ),
                    "prepared_ref_count": (
                        0
                        if snapshot is None
                        else len(
                            _json(snapshot["prepared_refs_json"], {})
                            if isinstance(
                                _json(snapshot["prepared_refs_json"], {}),
                                Mapping,
                            )
                            else {}
                        )
                    ),
                    "terminal_delivery_count": (
                        0
                        if snapshot is None
                        else len(
                            _json(snapshot["terminal_deliveries_json"], [])
                            if isinstance(
                                _json(snapshot["terminal_deliveries_json"], []),
                                list,
                            )
                            else []
                        )
                    ),
                    "created_at": (
                        None
                        if snapshot is None
                        else float(snapshot["created_at"])
                    ),
                    "steps": product_turn_steps,
                    "details": (
                        None
                        if snapshot is None
                        else {
                            "canonical_messages": _json(
                                snapshot["canonical_messages_json"], []
                            ),
                            "sanitized_request": snapshot_request,
                            "run_context": _json(
                                snapshot["run_context_json"], {}
                            ),
                            "run_spec": _json(
                                snapshot["run_spec_json"], {}
                            ),
                            "prepared_refs": _json(
                                snapshot["prepared_refs_json"], {}
                            ),
                            "capability_snapshot": _json(
                                snapshot["capability_snapshot_json"], {}
                            ),
                            "provider_launch_policy": _json(
                                snapshot["provider_launch_policy_json"], {}
                            ),
                        }
                    ),
                },
                "continuation": (
                    None
                    if continuation is None
                    else {
                        "iteration": int(continuation["iteration"]),
                        "version": int(
                            continuation["continuation_version"]
                        ),
                        "pending_decision_id": (
                            None
                            if continuation["pending_decision_id"] is None
                            else str(continuation["pending_decision_id"])
                        ),
                        "pending_call_count": len(pending_calls),
                        "pending_tool_names": pending_tool_names[:50],
                        "updated_at": float(continuation["updated_at"]),
                    }
                ),
                "goal": (
                    None
                    if goal is None
                    else {
                        "goal_id": str(goal["goal_id"]),
                        "task_scope_id": str(goal["task_scope_id"]),
                        "status": str(goal["status"]),
                        "version": int(goal["goal_version"]),
                        "plan_version": (
                            None
                            if latest_plan is None
                            else int(latest_plan["plan_version"])
                        ),
                        "trigger_failure_set_id": (
                            None
                            if latest_plan is None
                            or latest_plan["trigger_failure_set_id"] is None
                            else str(latest_plan["trigger_failure_set_id"])
                        ),
                        "created_at": float(goal["created_at"]),
                        "updated_at": float(goal["updated_at"]),
                        "ended_at": _optional_float(goal["ended_at"]),
                    }
                ),
                "lineage": [
                    {
                        "run_id": str(item["run_id"]),
                        "parent_run_id": (
                            None
                            if item["parent_run_id"] is None
                            else str(item["parent_run_id"])
                        ),
                        "driver_kind": str(item["driver_kind"]),
                        "profile_key": str(item["profile_key"]),
                        "status": str(item["status"]),
                        "version": int(item["version"]),
                        "created_at": float(item["created_at"]),
                        "started_at": _optional_float(item["started_at"]),
                        "updated_at": float(item["updated_at"]),
                        "ended_at": _optional_float(item["ended_at"]),
                    }
                    for item in lineage_rows
                ],
                "provider_invocations": providers,
                "action_batches": [
                    {
                        "batch_id": str(item["provider_batch_id"]),
                        "provider_turn_id": str(item["provider_turn_id"]),
                        "pending_call_count": int(item["pending_call_count"]),
                        "failure_set_id": (
                            None
                            if item["failure_set_id"] is None
                            else str(item["failure_set_id"])
                        ),
                        "status": str(item["status"]),
                        "observed_status": observed_batch_status.get(
                            str(item["provider_batch_id"])
                        ),
                        "version": int(item["batch_version"]),
                        "created_at": float(item["created_at"]),
                        "updated_at": float(item["updated_at"]),
                        "settled_at": _optional_float(item["settled_at"]),
                    }
                    for item in batch_rows
                ],
                "tool_calls": [
                    {
                        "call_record_id": str(item["call_record_id"]),
                        "batch_id": str(item["provider_batch_id"]),
                        "order": int(item["call_order"]),
                        "call_id": str(item["provider_call_id"]),
                        "tool_name": str(item["raw_tool_name"]),
                        "admission_state": str(item["admission_state"]),
                        "outcome_status": (
                            pending_outcome_status_by_call.get(
                                str(item["provider_call_id"])
                            )
                        ),
                        "error_code": (
                            tool_observations_by_call.get(
                                str(item["provider_call_id"]), {}
                            ).get("error_code")
                        ),
                        "error_message": (
                            tool_observations_by_call.get(
                                str(item["provider_call_id"]), {}
                            ).get("error_message")
                        ),
                        "terminal_outcome_ref": (
                            None
                            if item["terminal_outcome_ref"] is None
                            else str(item["terminal_outcome_ref"])
                        ),
                        "details": {
                            "raw_arguments_ref": (
                                None
                                if item["raw_arguments_ref"] is None
                                else str(item["raw_arguments_ref"])
                            ),
                            "raw_arguments_hash": (
                                None
                                if item["raw_arguments_hash"] is None
                                else str(item["raw_arguments_hash"])
                            ),
                            "parsed_arguments_hash": (
                                None
                                if item["parsed_arguments_hash"] is None
                                else str(item["parsed_arguments_hash"])
                            ),
                            "prepared_call_ref": (
                                None
                                if item["prepared_call_ref"] is None
                                else str(item["prepared_call_ref"])
                            ),
                        },
                        "version": int(item["call_version"]),
                        "created_at": float(item["created_at"]),
                        "updated_at": float(item["updated_at"]),
                    }
                    for item in call_rows
                ],
                "effects": [
                    {
                        "effect_id": str(item["effect_id"]),
                        "run_id": str(item["run_id"]),
                        "call_id": str(item["call_id"]),
                        "tool_name": str(item["tool_name"]),
                        "effect_type": str(item["effect_type"]),
                        "status": str(item["status"]),
                        "handoff_state": str(item["handoff_state"]),
                        "completion_disposition": str(
                            item["completion_disposition"]
                        ),
                        "receipt_ref": (
                            None
                            if item["receipt_ref"] is None
                            else str(item["receipt_ref"])
                        ),
                        "details": {
                            "policy": _json(item["policy_json"], {}),
                            "prepared": _json(item["prepared_json"], {}),
                            "outcome": _json(item["outcome_json"], None),
                            "artifact_refs": _json(
                                item["artifact_refs_json"], []
                            ),
                        },
                        "created_at": float(item["created_at"]),
                        "updated_at": float(item["updated_at"]),
                        "ended_at": _optional_float(item["ended_at"]),
                    }
                    for item in effect_rows
                ],
                "workflow_effects": [
                    {
                        "effect_id": str(item["effect_id"]),
                        "run_id": str(item["run_id"]),
                        "status": str(item["status"]),
                        "workflow_step_id": workflow_step_by_call.get(
                            (
                                str(item["run_id"]),
                                _prepared_call_id(item["prepared_json"]),
                            )
                        ),
                        "receipt_ref": (
                            None
                            if item["receipt_ref"] is None
                            else str(item["receipt_ref"])
                        ),
                        "details": {
                            "prepared": _json(item["prepared_json"], {}),
                            "outcome": _json(item["outcome_json"], None),
                            "artifact_refs": _json(
                                item["artifact_refs_json"], []
                            ),
                        },
                        "created_at": float(item["started_at"]),
                        "updated_at": float(item["updated_at"]),
                        "ended_at": _optional_float(item["ended_at"]),
                    }
                    for item in workflow_effect_rows
                ],
                "workflow_plans": workflow_plans,
                "attempts": [
                    {
                        "attempt_id": str(item["attempt_id"]),
                        "run_id": str(item["run_id"]),
                        "provider_turn_id": str(item["provider_turn_id"]),
                        "batch_id": str(item["provider_batch_id"]),
                        "plan_version": int(item["plan_version"]),
                        "trigger_failure_set_id": (
                            None
                            if item["trigger_failure_set_id"] is None
                            else str(item["trigger_failure_set_id"])
                        ),
                        "supersedes_attempt_id": (
                            None
                            if item["supersedes_attempt_id"] is None
                            else str(item["supersedes_attempt_id"])
                        ),
                        "status": str(item["status"]),
                        "continuation_status": (
                            str(pending_current_attempt["status"])
                            if pending_current_attempt is not None
                            and str(
                                pending_current_attempt.get("attempt_id") or ""
                            )
                            == str(item["attempt_id"])
                            and pending_current_attempt.get("status")
                            is not None
                            else observed_batch_status.get(
                                str(item["provider_batch_id"])
                            )
                        ),
                        "version": int(item["attempt_version"]),
                        "created_at": float(item["created_at"]),
                        "updated_at": float(item["updated_at"]),
                        "ended_at": _optional_float(item["ended_at"]),
                    }
                    for item in attempt_rows
                ],
                "failures": [
                    {
                        "report_ref": str(item["report_ref"]),
                        "run_id": str(item["run_id"]),
                        "attempt_id": str(item["attempt_id"]),
                        "plan_version": int(item["plan_version"]),
                        "provider_call_id": (
                            None
                            if item["provider_call_id"] is None
                            else str(item["provider_call_id"])
                        ),
                        "child_run_id": (
                            None
                            if item["child_run_id"] is None
                            else str(item["child_run_id"])
                        ),
                        "failed_call_id": (
                            None
                            if item["failed_call_id"] is None
                            else str(item["failed_call_id"])
                        ),
                        "failed_effect_id": (
                            None
                            if item["failed_effect_id"] is None
                            else str(item["failed_effect_id"])
                        ),
                        "failed_step": str(item["failed_step"]),
                        "error_class": str(item["error_class"]),
                        "error_code": str(item["error_code"]),
                        "error_message": (
                            tool_observations_by_call.get(
                                str(item["provider_call_id"] or ""), {}
                            ).get("error_message")
                        ),
                        "source_kind": str(item["source_kind"]),
                        "source_identity": str(item["source_identity"]),
                        "created_at": float(item["created_at"]),
                    }
                    for item in failure_rows
                ],
                "events": events,
            }

    async def get_latest_session_project_context(
        self,
        session_id: str,
    ) -> Mapping[str, str | None]:
        """Return the Session's latest durable user-selected project root.

        A later root without a workspace must not erase the project selected by
        an earlier ``project_directory_select`` decision in the same Session.
        Legacy Runs that predate task work contexts still fall back to their
        latest persisted Run workspace.
        """

        session_key = str(session_id or "").strip()
        if not session_key:
            return {"project_name": None, "project_root": None}
        async with self._read_connection() as db:
            selected = await (
                await db.execute(
                    """SELECT workspace_root
                    FROM execution_task_work_contexts
                    WHERE session_id=? AND workspace_source='user_path'
                      AND workspace_root IS NOT NULL
                      AND TRIM(workspace_root)<>''
                    ORDER BY updated_at DESC,created_at DESC,root_run_id DESC
                    LIMIT 1""",
                    (session_key,),
                )
            ).fetchone()
            if selected is not None:
                root = str(selected["workspace_root"] or "").strip()
                name = root.rstrip("\\/").replace("\\", "/").rsplit("/", 1)[-1]
                return {
                    "project_name": name or None,
                    "project_root": root or None,
                }
            row = await (
                await db.execute(
                    """SELECT s.run_context_json,r.workspace_json
                    FROM execution_runs AS r
                    LEFT JOIN execution_run_start_snapshots AS s
                      ON s.run_id=r.run_id
                    WHERE r.session_id=? AND r.root_run_id=r.run_id
                    ORDER BY r.created_at DESC,r.run_id DESC
                    LIMIT 1""",
                    (session_key,),
                )
            ).fetchone()
        if row is None:
            return {"project_name": None, "project_root": None}
        try:
            context = (
                {}
                if row["run_context_json"] is None
                else json.loads(str(row["run_context_json"]))
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            context = {}
        workspace = (
            context.get("workspace", {})
            if isinstance(context, Mapping)
            else {}
        )
        root = (
            str(workspace.get("root") or workspace.get("write_scope_root") or "")
            if isinstance(workspace, Mapping)
            else ""
        ).strip()
        if not root:
            try:
                run_workspace = json.loads(str(row["workspace_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                run_workspace = {}
            if isinstance(run_workspace, Mapping):
                root = str(
                    run_workspace.get("root")
                    or run_workspace.get("write_scope_root")
                    or ""
                ).strip()
        name = root.rstrip("\\/").replace("\\", "/").rsplit("/", 1)[-1]
        return {
            "project_name": name or None,
            "project_root": root or None,
        }

    async def set_task_run_projection_state(
        self,
        root_run_id: str,
        ui_state: str,
        *,
        expected_version: int,
    ) -> TaskRunProjection:
        if ui_state not in {"open", "background", "closed"}:
            raise ValueError(f"unsupported projection state: {ui_state}")
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_task_run_projections
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            if row is None:
                raise RunNotFound(
                    "task_run_projection_not_found",
                    f"task run projection does not exist: {root_run_id}",
                )
            current = self._row_to_task_run_projection(row)
            if current.ui_state == ui_state:
                return current
            if current.version != expected_version:
                raise VersionConflict(
                    "stale_task_run_projection",
                    "task run projection version changed",
                )
            next_version = current.version + 1
            cursor = await db.execute(
                """UPDATE execution_task_run_projections
                SET ui_state=?,projection_version=?,updated_at=?
                WHERE root_run_id=? AND projection_version=?""",
                (
                    ui_state,
                    next_version,
                    float(self._clock()),
                    root_run_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_task_run_projection",
                    "task run projection version changed",
                )
            return replace(
                current,
                ui_state=ui_state,
                version=next_version,
            )

    async def create_task_goal(
        self,
        goal: TaskGoalRecord,
        initial_plan: PlanVersionRecord,
    ) -> tuple[TaskGoalRecord, PlanVersionRecord]:
        """Create the stable root goal and plan v1 in one idempotent transaction."""

        if (
            goal.status is not TaskGoalStatus.ACTIVE
            or goal.version != 0
            or goal.ended_at is not None
        ):
            raise IdempotencyConflict(
                "invalid_initial_goal", "a new task goal must start active at version zero"
            )
        if (
            initial_plan.root_run_id != goal.root_run_id
            or initial_plan.plan_version != 1
            or initial_plan.trigger_failure_set_id is not None
        ):
            raise IdempotencyConflict(
                "invalid_initial_plan", "a task goal must begin with plan version one"
            )
        async with self._write_transaction() as db:
            run = await (
                await db.execute(
                    "SELECT root_run_id,parent_run_id FROM execution_runs WHERE run_id=?",
                    (goal.root_run_id,),
                )
            ).fetchone()
            if (
                run is None
                or str(run["root_run_id"]) != goal.root_run_id
                or run["parent_run_id"] is not None
            ):
                raise IdempotencyConflict(
                    "task_goal_root_conflict",
                    "task goals can only bind an existing root execution run",
                )
            existing_row = await (
                await db.execute(
                    """SELECT * FROM execution_task_goals
                    WHERE goal_id=? OR root_run_id=? OR task_scope_id=?""",
                    (goal.goal_id, goal.root_run_id, goal.task_scope_id),
                )
            ).fetchone()
            if existing_row is not None:
                existing = self._row_to_task_goal(existing_row)
                if not self._same_goal_intent(existing, goal):
                    raise IdempotencyConflict(
                        "task_goal_conflict", "task goal identity already names another intent"
                    )
                plan_row = await (
                    await db.execute(
                        """SELECT * FROM execution_plan_versions
                        WHERE root_run_id=? AND plan_version=1""",
                        (goal.root_run_id,),
                    )
                ).fetchone()
                if plan_row is None:
                    raise IdempotencyConflict(
                        "task_goal_partial", "task goal exists without its initial plan"
                    )
                return existing, self._row_to_plan_version(plan_row)
            now = float(self._clock())
            await db.execute(
                """INSERT INTO execution_task_goals(
                goal_id,schema_version,root_run_id,task_scope_id,objective_ref,
                status,goal_version,created_at,updated_at,ended_at
                ) VALUES(?,?,?,?,?,'active',0,?,?,NULL)""",
                (
                    goal.goal_id,
                    goal.schema_version,
                    goal.root_run_id,
                    goal.task_scope_id,
                    goal.objective_ref,
                    now,
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO execution_plan_versions(
                root_run_id,plan_version,trigger_failure_set_id,created_at
                ) VALUES(?,1,NULL,?)""",
                (goal.root_run_id, now),
            )
            self._fault("task_goal_before_commit")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_task_goals WHERE goal_id=?",
                    (goal.goal_id,),
                )
            ).fetchone()
            plan_row = await (
                await db.execute(
                    """SELECT * FROM execution_plan_versions
                    WHERE root_run_id=? AND plan_version=1""",
                    (goal.root_run_id,),
                )
            ).fetchone()
            assert row is not None and plan_row is not None
            return self._row_to_task_goal(row), self._row_to_plan_version(plan_row)

    async def get_task_goal(self, root_run_id: str) -> TaskGoalRecord | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_task_goals WHERE root_run_id=?",
                    (root_run_id,),
                )
            ).fetchone()
            return None if row is None else self._row_to_task_goal(row)

    async def append_plan_version(
        self, plan: PlanVersionRecord
    ) -> PlanVersionRecord:
        """Append one monotonic plan version after its failure trigger is durable."""

        async with self._write_transaction() as db:
            existing = await (
                await db.execute(
                    """SELECT * FROM execution_plan_versions
                    WHERE root_run_id=? AND plan_version=?""",
                    (plan.root_run_id, plan.plan_version),
                )
            ).fetchone()
            if existing is not None:
                record = self._row_to_plan_version(existing)
                if record.trigger_failure_set_id != plan.trigger_failure_set_id:
                    raise IdempotencyConflict(
                        "plan_version_conflict", "plan version trigger differs"
                    )
                return record
            goal = await (
                await db.execute(
                    "SELECT status FROM execution_task_goals WHERE root_run_id=?",
                    (plan.root_run_id,),
                )
            ).fetchone()
            if goal is None or str(goal["status"]) not in {
                TaskGoalStatus.ACTIVE.value,
                TaskGoalStatus.WAITING_EXTERNAL.value,
            }:
                raise IdempotencyConflict(
                    "plan_goal_not_active", "new plans require a nonterminal task goal"
                )
            if plan.plan_version > 1:
                if plan.trigger_failure_set_id is None:
                    raise IdempotencyConflict(
                        "plan_trigger_required", "replans require a failure set trigger"
                    )
                trigger = await (
                    await db.execute(
                        """SELECT 1 FROM execution_attempt_failure_sets
                        WHERE failure_set_id=? AND root_run_id=?""",
                        (plan.trigger_failure_set_id, plan.root_run_id),
                    )
                ).fetchone()
                if trigger is None:
                    raise IdempotencyConflict(
                        "plan_trigger_missing", "replan failure set is not durable"
                    )
            now = float(self._clock())
            try:
                await db.execute(
                    """INSERT INTO execution_plan_versions(
                    root_run_id,plan_version,trigger_failure_set_id,created_at
                    ) VALUES(?,?,?,?)""",
                    (
                        plan.root_run_id,
                        plan.plan_version,
                        plan.trigger_failure_set_id,
                        now,
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                raise IdempotencyConflict(
                    "plan_version_not_monotonic",
                    "plan versions must be appended without gaps",
                ) from exc
            row = await (
                await db.execute(
                    """SELECT * FROM execution_plan_versions
                    WHERE root_run_id=? AND plan_version=?""",
                    (plan.root_run_id, plan.plan_version),
                )
            ).fetchone()
            assert row is not None
            return self._row_to_plan_version(row)

    async def open_provider_turn_fence(
        self, fence: ProviderTurnFence
    ) -> ProviderTurnFence:
        """Persist the provider response idempotency fence before accepting calls."""

        if (
            fence.state is not ProviderTurnState.PENDING
            or fence.version != 0
            or fence.accepted_batch_id is not None
        ):
            raise IdempotencyConflict(
                "invalid_provider_turn", "new provider turns must start pending"
            )
        async with self._write_transaction() as db:
            existing = await (
                await db.execute(
                    """SELECT * FROM execution_provider_turn_fences
                    WHERE provider_turn_id=?
                       OR (root_run_id=? AND idempotency_key=?)""",
                    (
                        fence.provider_turn_id,
                        fence.root_run_id,
                        fence.idempotency_key,
                    ),
                )
            ).fetchone()
            if existing is not None:
                record = self._row_to_provider_turn(existing)
                if (
                    record.provider_turn_id,
                    record.root_run_id,
                    record.idempotency_key,
                    record.request_hash,
                ) != (
                    fence.provider_turn_id,
                    fence.root_run_id,
                    fence.idempotency_key,
                    fence.request_hash,
                ):
                    raise IdempotencyConflict(
                        "provider_turn_conflict",
                        "provider turn fence already names another request",
                    )
                return record
            goal = await (
                await db.execute(
                    "SELECT status FROM execution_task_goals WHERE root_run_id=?",
                    (fence.root_run_id,),
                )
            ).fetchone()
            if goal is None or str(goal["status"]) != TaskGoalStatus.ACTIVE.value:
                raise IdempotencyConflict(
                    "provider_turn_goal_inactive",
                    "a provider turn requires an active task goal",
                )
            now = float(self._clock())
            await db.execute(
                """INSERT INTO execution_provider_turn_fences(
                provider_turn_id,root_run_id,idempotency_key,request_hash,state,
                accepted_batch_id,fence_version,created_at,updated_at,accepted_at
                ) VALUES(?,?,?,?,'pending',NULL,0,?,?,NULL)""",
                (
                    fence.provider_turn_id,
                    fence.root_run_id,
                    fence.idempotency_key,
                    fence.request_hash,
                    now,
                    now,
                ),
            )
            row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_turn_fences
                    WHERE provider_turn_id=?""",
                    (fence.provider_turn_id,),
                )
            ).fetchone()
            assert row is not None
            return self._row_to_provider_turn(row)

    async def accept_provider_batch_and_create_attempt(
        self,
        batch: ProviderActionBatch,
        attempt: AttemptRecord,
        calls: Sequence[ProviderActionCall],
    ) -> ProviderBatchAdmissionResult:
        """Atomically accept one provider batch and create exactly one attempt."""

        ordered_calls = tuple(sorted(calls, key=lambda item: item.call_order))
        if (
            batch.status is not ProviderBatchStatus.ADMITTED
            or batch.version != 0
            or attempt.status is not AttemptStatus.RUNNING
            or attempt.version != 0
            or not attempt.budget_eligible
            or not ordered_calls
            or batch.pending_call_count != len(ordered_calls)
            or tuple(item.call_order for item in ordered_calls)
            != tuple(range(len(ordered_calls)))
            or tuple(attempt.planned_call_refs)
            != tuple(item.call_record_id for item in ordered_calls)
        ):
            raise IdempotencyConflict(
                "invalid_provider_batch",
                "provider batch, attempt and ordered calls do not form one initial admission",
            )
        if (
            batch.root_run_id != attempt.root_run_id
            or batch.provider_turn_id != attempt.provider_turn_id
            or batch.provider_batch_id != attempt.provider_batch_id
            or any(
                call.root_run_id != batch.root_run_id
                or call.provider_batch_id != batch.provider_batch_id
                or call.admission_state
                not in {
                    ProviderActionAdmissionState.ADMITTED,
                    ProviderActionAdmissionState.REJECTED,
                }
                or call.version != 0
                for call in ordered_calls
            )
        ):
            raise IdempotencyConflict(
                "provider_batch_scope_conflict",
                "provider batch members must share root, turn and batch identity",
            )
        async with self._write_transaction() as db:
            existing_batch_row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_batches
                    WHERE provider_batch_id=? OR provider_turn_id=?""",
                    (batch.provider_batch_id, batch.provider_turn_id),
                )
            ).fetchone()
            if existing_batch_row is not None:
                existing_batch = self._row_to_provider_batch(existing_batch_row)
                existing_attempt_row = await (
                    await db.execute(
                        """SELECT * FROM execution_attempt_records
                        WHERE provider_batch_id=?""",
                        (existing_batch.provider_batch_id,),
                    )
                ).fetchone()
                existing_call_rows = await (
                    await db.execute(
                        """SELECT * FROM execution_provider_action_calls
                        WHERE provider_batch_id=? ORDER BY call_order""",
                        (existing_batch.provider_batch_id,),
                    )
                ).fetchall()
                if existing_attempt_row is None:
                    raise IdempotencyConflict(
                        "provider_batch_partial",
                        "provider batch exists without its attempt",
                    )
                existing_attempt = self._row_to_attempt(existing_attempt_row)
                existing_calls = tuple(
                    self._row_to_provider_call(row) for row in existing_call_rows
                )
                if (
                    not self._same_batch_intent(existing_batch, batch)
                    or not self._same_attempt_intent(existing_attempt, attempt)
                    or len(existing_calls) != len(ordered_calls)
                    or any(
                        not self._same_call_intent(existing, requested)
                        for existing, requested in zip(existing_calls, ordered_calls)
                    )
                ):
                    raise IdempotencyConflict(
                        "provider_batch_conflict",
                        "provider batch replay differs from the accepted intent",
                    )
                return ProviderBatchAdmissionResult(
                    existing_attempt, existing_calls, True
                )
            fence = await (
                await db.execute(
                    """SELECT * FROM execution_provider_turn_fences
                    WHERE provider_turn_id=? AND root_run_id=?""",
                    (batch.provider_turn_id, batch.root_run_id),
                )
            ).fetchone()
            if (
                fence is None
                or str(fence["state"]) != ProviderTurnState.PENDING.value
            ):
                raise IdempotencyConflict(
                    "provider_turn_not_pending",
                    "provider batch admission requires its pending turn fence",
                )
            plan = await (
                await db.execute(
                    """SELECT 1 FROM execution_plan_versions
                    WHERE root_run_id=? AND plan_version=?""",
                    (attempt.root_run_id, attempt.plan_version),
                )
            ).fetchone()
            run = await (
                await db.execute(
                    "SELECT root_run_id FROM execution_runs WHERE run_id=?",
                    (attempt.run_id,),
                )
            ).fetchone()
            if (
                plan is None
                or run is None
                or str(run["root_run_id"]) != attempt.root_run_id
            ):
                raise IdempotencyConflict(
                    "attempt_scope_conflict",
                    "attempt requires a durable plan and run in the same root",
                )
            if attempt.trigger_failure_set_id is not None:
                trigger = await (
                    await db.execute(
                        """SELECT 1 FROM execution_attempt_failure_sets
                        WHERE failure_set_id=? AND root_run_id=?""",
                        (attempt.trigger_failure_set_id, attempt.root_run_id),
                    )
                ).fetchone()
                if trigger is None:
                    raise IdempotencyConflict(
                        "attempt_trigger_missing",
                        "attempt trigger failure set is not durable",
                    )
            now = float(self._clock())
            await db.execute(
                """INSERT INTO execution_provider_action_batches(
                provider_batch_id,root_run_id,provider_turn_id,
                canonical_assistant_batch_ref,batch_fingerprint,pending_call_count,
                failure_set_id,status,batch_version,created_at,updated_at,settled_at
                ) VALUES(?,?,?,?,?,?,NULL,'admitted',0,?,?,NULL)""",
                (
                    batch.provider_batch_id,
                    batch.root_run_id,
                    batch.provider_turn_id,
                    batch.canonical_assistant_batch_ref,
                    batch.batch_fingerprint,
                    batch.pending_call_count,
                    now,
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO execution_attempt_records(
                attempt_id,schema_version,root_run_id,run_id,provider_turn_id,
                provider_batch_id,plan_version,trigger_failure_set_id,
                supersedes_attempt_id,strategy_fingerprint,planned_call_refs_json,
                checkpoint_ref,status,budget_eligible,attempt_version,created_at,
                updated_at,ended_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'running',1,0,?,?,NULL)""",
                (
                    attempt.attempt_id,
                    attempt.schema_version,
                    attempt.root_run_id,
                    attempt.run_id,
                    attempt.provider_turn_id,
                    attempt.provider_batch_id,
                    attempt.plan_version,
                    attempt.trigger_failure_set_id,
                    attempt.supersedes_attempt_id,
                    attempt.strategy_fingerprint,
                    canonical_json(list(attempt.planned_call_refs)),
                    attempt.checkpoint_ref,
                    now,
                    now,
                ),
            )
            self._fault("provider_batch_after_attempt")
            for call in ordered_calls:
                await db.execute(
                    """INSERT INTO execution_provider_action_calls(
                    call_record_id,schema_version,root_run_id,provider_batch_id,
                    call_order,provider_call_id,raw_tool_name,raw_arguments_ref,
                    raw_arguments_hash,parsed_arguments_hash,admission_state,
                    prepared_call_ref,command_boundary_ref,terminal_outcome_ref,
                    call_version,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)""",
                    (
                        call.call_record_id,
                        call.schema_version,
                        call.root_run_id,
                        call.provider_batch_id,
                        call.call_order,
                        call.provider_call_id,
                        call.raw_tool_name,
                        call.raw_arguments_ref,
                        call.raw_arguments_hash,
                        call.parsed_arguments_hash,
                        call.admission_state.value,
                        call.prepared_call_ref,
                        call.command_boundary_ref,
                        call.terminal_outcome_ref,
                        now,
                        now,
                    ),
                )
            self._fault("provider_batch_after_calls")
            cursor = await db.execute(
                """UPDATE execution_provider_turn_fences
                SET state='accepted',accepted_batch_id=?,fence_version=fence_version+1,
                    updated_at=?,accepted_at=?
                WHERE provider_turn_id=? AND root_run_id=? AND state='pending'
                  AND fence_version=?""",
                (
                    batch.provider_batch_id,
                    now,
                    now,
                    batch.provider_turn_id,
                    batch.root_run_id,
                    int(fence["fence_version"]),
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "provider_turn_cas_conflict",
                    "provider turn acceptance compare-and-set lost",
                )
            self._fault("provider_batch_before_commit")
            attempt_row = await (
                await db.execute(
                    "SELECT * FROM execution_attempt_records WHERE attempt_id=?",
                    (attempt.attempt_id,),
                )
            ).fetchone()
            call_rows = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_calls
                    WHERE provider_batch_id=? ORDER BY call_order""",
                    (batch.provider_batch_id,),
                )
            ).fetchall()
            assert attempt_row is not None
            return ProviderBatchAdmissionResult(
                self._row_to_attempt(attempt_row),
                tuple(self._row_to_provider_call(row) for row in call_rows),
                False,
            )

    async def get_attempt(self, attempt_id: str) -> AttemptRecord | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_attempt_records WHERE attempt_id=?",
                    (attempt_id,),
                )
            ).fetchone()
            return None if row is None else self._row_to_attempt(row)

    async def list_provider_action_calls(
        self, provider_batch_id: str
    ) -> tuple[ProviderActionCall, ...]:
        async with self._read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_calls
                    WHERE provider_batch_id=? ORDER BY call_order""",
                    (provider_batch_id,),
                )
            ).fetchall()
            return tuple(self._row_to_provider_call(row) for row in rows)

    async def mark_provider_action_prepared(
        self,
        call_record_id: str,
        *,
        expected_version: int,
        parsed_arguments_hash: str,
        prepared_call_ref: str,
        command_boundary_ref: str | None = None,
    ) -> ProviderActionCall:
        """CAS an admitted provider call to its prepared durable boundary."""

        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_calls
                    WHERE call_record_id=?""",
                    (call_record_id,),
                )
            ).fetchone()
            if row is None:
                raise IdempotencyConflict(
                    "provider_call_missing", "provider action call does not exist"
                )
            existing = self._row_to_provider_call(row)
            if existing.admission_state in {
                ProviderActionAdmissionState.PREPARED,
                ProviderActionAdmissionState.SETTLED,
            }:
                if (
                    existing.parsed_arguments_hash,
                    existing.prepared_call_ref,
                    existing.command_boundary_ref,
                ) != (
                    parsed_arguments_hash,
                    prepared_call_ref,
                    command_boundary_ref,
                ):
                    raise IdempotencyConflict(
                        "prepared_call_conflict", "prepared call replay differs"
                    )
                return existing
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_provider_action_calls
                SET parsed_arguments_hash=?,admission_state='prepared',
                    prepared_call_ref=?,command_boundary_ref=?,
                    call_version=call_version+1,updated_at=?
                WHERE call_record_id=? AND admission_state='admitted'
                  AND call_version=?""",
                (
                    parsed_arguments_hash,
                    prepared_call_ref,
                    command_boundary_ref,
                    now,
                    call_record_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "provider_call_cas_conflict",
                    "provider call preparation compare-and-set lost",
                )
            updated = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_calls
                    WHERE call_record_id=?""",
                    (call_record_id,),
                )
            ).fetchone()
            assert updated is not None
            return self._row_to_provider_call(updated)

    async def settle_provider_action_success(
        self,
        attempt_id: str,
        outcome_refs_by_call: Mapping[str, str],
    ) -> AttemptRecord:
        """Atomically settle every call, its batch and the successful attempt."""

        normalized_refs = {
            str(call_record_id).strip(): str(outcome_ref).strip()
            for call_record_id, outcome_ref in outcome_refs_by_call.items()
        }
        if (
            not normalized_refs
            or any(not key or not value for key, value in normalized_refs.items())
            or len(normalized_refs) != len(outcome_refs_by_call)
        ):
            raise IdempotencyConflict(
                "attempt_success_outcomes_invalid",
                "successful attempt settlement requires one outcome per call",
            )
        async with self._write_transaction() as db:
            attempt_row = await (
                await db.execute(
                    """SELECT * FROM execution_attempt_records
                    WHERE attempt_id=?""",
                    (attempt_id,),
                )
            ).fetchone()
            if attempt_row is None:
                raise IdempotencyConflict(
                    "attempt_missing",
                    "successful attempt settlement requires an existing attempt",
                )
            attempt = self._row_to_attempt(attempt_row)
            batch_row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_batches
                    WHERE provider_batch_id=?""",
                    (attempt.provider_batch_id,),
                )
            ).fetchone()
            call_rows = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_calls
                    WHERE provider_batch_id=? ORDER BY call_order""",
                    (attempt.provider_batch_id,),
                )
            ).fetchall()
            if batch_row is None or not call_rows:
                raise IdempotencyConflict(
                    "attempt_success_scope_missing",
                    "successful attempt lost its provider batch or calls",
                )
            calls = tuple(self._row_to_provider_call(row) for row in call_rows)
            call_ids = tuple(call.call_record_id for call in calls)
            if (
                tuple(attempt.planned_call_refs) != call_ids
                or set(normalized_refs) != set(call_ids)
            ):
                raise IdempotencyConflict(
                    "attempt_success_scope_conflict",
                    "successful outcomes must exactly cover the planned calls",
                )
            batch = self._row_to_provider_batch(batch_row)
            if attempt.status is AttemptStatus.SUCCEEDED:
                if (
                    batch.status is not ProviderBatchStatus.SETTLED
                    or batch.pending_call_count != 0
                    or any(
                        call.admission_state
                        is not ProviderActionAdmissionState.SETTLED
                        or call.terminal_outcome_ref
                        != normalized_refs[call.call_record_id]
                        for call in calls
                    )
                ):
                    raise IdempotencyConflict(
                        "attempt_success_partial_settlement",
                        "successful attempt replay found a partially settled ledger",
                    )
                return attempt
            if (
                attempt.status is not AttemptStatus.RUNNING
                or batch.status
                not in {ProviderBatchStatus.ADMITTED, ProviderBatchStatus.RUNNING}
                or any(
                    call.admission_state
                    not in {
                        ProviderActionAdmissionState.ADMITTED,
                        ProviderActionAdmissionState.PREPARED,
                    }
                    for call in calls
                )
            ):
                raise IdempotencyConflict(
                    "attempt_not_success_settleable",
                    "only one fully completed live attempt can be settled",
                )
            now = float(self._clock())
            for call in calls:
                cursor = await db.execute(
                    """UPDATE execution_provider_action_calls
                    SET admission_state='settled',terminal_outcome_ref=?,
                        call_version=call_version+1,updated_at=?
                    WHERE call_record_id=? AND call_version=?
                      AND admission_state IN ('admitted','prepared')""",
                    (
                        normalized_refs[call.call_record_id],
                        now,
                        call.call_record_id,
                        call.version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise VersionConflict(
                        "attempt_success_call_cas_conflict",
                        "provider call changed during successful settlement",
                    )
            self._fault("attempt_success_after_calls")
            batch_cursor = await db.execute(
                """UPDATE execution_provider_action_batches
                SET pending_call_count=0,status='settled',
                    batch_version=batch_version+1,updated_at=?,settled_at=?
                WHERE provider_batch_id=? AND batch_version=?
                  AND status IN ('admitted','running')""",
                (
                    now,
                    now,
                    attempt.provider_batch_id,
                    batch.version,
                ),
            )
            attempt_cursor = await db.execute(
                """UPDATE execution_attempt_records
                SET status='succeeded',attempt_version=attempt_version+1,
                    updated_at=?,ended_at=?
                WHERE attempt_id=? AND attempt_version=? AND status='running'""",
                (now, now, attempt.attempt_id, attempt.version),
            )
            if batch_cursor.rowcount != 1 or attempt_cursor.rowcount != 1:
                raise VersionConflict(
                    "attempt_success_cas_conflict",
                    "batch or attempt changed during successful settlement",
                )
            self._fault("attempt_success_before_commit")
            updated = await (
                await db.execute(
                    """SELECT * FROM execution_attempt_records
                    WHERE attempt_id=?""",
                    (attempt.attempt_id,),
                )
            ).fetchone()
            assert updated is not None
            return self._row_to_attempt(updated)

    async def stage_attempt_failure_set(
        self,
        failure_set: AttemptFailureSet,
        reports: Sequence[TaskFailureReport],
    ) -> AttemptFailureSet:
        """Persist every call failure and fail the attempt in one transaction."""

        report_by_ref = {report.report_ref: report for report in reports}
        if (
            len(report_by_ref) != len(reports)
            or set(report_by_ref) != set(failure_set.report_refs)
            or failure_set.backfill_state is not FailureBackfillState.READY
            or failure_set.provider_resume_state is not ProviderResumeState.PENDING
            or failure_set.version != 0
        ):
            raise IdempotencyConflict(
                "invalid_failure_set",
                "failure set must contain unique reports and begin ready for backfill",
            )
        async with self._write_transaction() as db:
            existing_row = await (
                await db.execute(
                    """SELECT * FROM execution_attempt_failure_sets
                    WHERE failure_set_id=? OR failed_attempt_id=?""",
                    (failure_set.failure_set_id, failure_set.failed_attempt_id),
                )
            ).fetchone()
            if existing_row is not None:
                existing = await self._failure_set_from_row(db, existing_row)
                if (
                    existing.failure_set_id,
                    existing.root_run_id,
                    existing.failed_attempt_id,
                    tuple(existing.report_refs),
                    existing.primary_report_ref,
                ) != (
                    failure_set.failure_set_id,
                    failure_set.root_run_id,
                    failure_set.failed_attempt_id,
                    tuple(failure_set.report_refs),
                    failure_set.primary_report_ref,
                ):
                    raise IdempotencyConflict(
                        "failure_set_conflict", "failure set replay differs"
                    )
                return existing
            attempt_row = await (
                await db.execute(
                    """SELECT * FROM execution_attempt_records
                    WHERE attempt_id=? AND root_run_id=?""",
                    (failure_set.failed_attempt_id, failure_set.root_run_id),
                )
            ).fetchone()
            if (
                attempt_row is None
                or str(attempt_row["status"])
                not in {
                    AttemptStatus.RUNNING.value,
                    AttemptStatus.WAITING_EXTERNAL.value,
                }
            ):
                raise IdempotencyConflict(
                    "attempt_not_failurable",
                    "failure facts require a live attempt in the same root",
                )
            attempt = self._row_to_attempt(attempt_row)
            call_rows = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_calls
                    WHERE provider_batch_id=? ORDER BY call_order""",
                    (attempt.provider_batch_id,),
                )
            ).fetchall()
            call_order = {
                str(row["call_record_id"]): int(row["call_order"])
                for row in call_rows
            }
            ordered_reports = sorted(
                reports,
                key=lambda report: (
                    call_order.get(report.call_record_id, 2**31),
                    report.source_kind.value,
                    report.report_ref,
                ),
            )
            if (
                not ordered_reports
                or tuple(report.report_ref for report in ordered_reports)
                != tuple(failure_set.report_refs)
                or failure_set.primary_report_ref != ordered_reports[0].report_ref
                or len({call_order.get(report.call_record_id) for report in reports})
                != len(reports)
                or any(
                    report.root_run_id != attempt.root_run_id
                    or report.run_id != attempt.run_id
                    or report.attempt_id != attempt.attempt_id
                    or report.plan_version != attempt.plan_version
                    or report.call_record_id not in call_order
                    for report in reports
                )
            ):
                raise IdempotencyConflict(
                    "failure_set_order_conflict",
                    "failure reports must be complete, call-ordered and share the attempt",
                )
            goal_row = await (
                await db.execute(
                    """SELECT task_scope_id FROM execution_task_goals
                    WHERE root_run_id=?""",
                    (attempt.root_run_id,),
                )
            ).fetchone()
            if goal_row is None or any(
                report.task_scope_id != str(goal_row["task_scope_id"])
                for report in reports
            ):
                raise IdempotencyConflict(
                    "failure_scope_conflict",
                    "failure reports must use the task goal scope",
                )
            now = float(self._clock())
            for report in ordered_reports:
                await db.execute(
                    """INSERT INTO execution_task_failure_reports(
                    report_ref,schema_version,root_run_id,run_id,task_scope_id,
                    attempt_id,plan_version,call_record_id,source_kind,
                    source_identity,provider_call_id,child_run_id,inner_failure_ref,
                    failed_call_id,failed_effect_id,failed_step,error_class,
                    error_code,error_fingerprint,action_fingerprint,exit_code,
                    evidence_refs_json,completed_step_refs_json,artifact_refs_json,
                    checkpoint_ref,prior_strategy_fingerprints_json,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        report.report_ref,
                        report.schema_version,
                        report.root_run_id,
                        report.run_id,
                        report.task_scope_id,
                        report.attempt_id,
                        report.plan_version,
                        report.call_record_id,
                        report.source_kind.value,
                        report.source_identity,
                        report.provider_call_id,
                        report.child_run_id,
                        report.inner_failure_ref,
                        report.failed_call_id,
                        report.failed_effect_id,
                        report.failed_step,
                        report.error_class.value,
                        report.error_code,
                        report.error_fingerprint,
                        report.action_fingerprint,
                        report.exit_code,
                        canonical_json(list(report.evidence_refs)),
                        canonical_json(list(report.completed_step_refs)),
                        canonical_json(list(report.artifact_refs)),
                        report.checkpoint_ref,
                        canonical_json(list(report.prior_strategy_fingerprints)),
                        now,
                    ),
                )
            self._fault("failure_set_after_reports")
            await db.execute(
                """INSERT INTO execution_attempt_failure_sets(
                failure_set_id,schema_version,root_run_id,failed_attempt_id,
                primary_report_ref,backfill_state,provider_resume_state,
                set_version,created_at,updated_at
                ) VALUES(?,?,?,?,?,'ready','pending',0,?,?)""",
                (
                    failure_set.failure_set_id,
                    failure_set.schema_version,
                    failure_set.root_run_id,
                    failure_set.failed_attempt_id,
                    failure_set.primary_report_ref,
                    now,
                    now,
                ),
            )
            for report in ordered_reports:
                await db.execute(
                    """INSERT INTO execution_attempt_failure_set_members(
                    failure_set_id,report_ref,provider_call_order,created_at
                    ) VALUES(?,?,?,?)""",
                    (
                        failure_set.failure_set_id,
                        report.report_ref,
                        call_order[report.call_record_id],
                        now,
                    ),
                )
            cursor = await db.execute(
                """UPDATE execution_attempt_records
                SET status='failed',attempt_version=attempt_version+1,
                    updated_at=?,ended_at=?
                WHERE attempt_id=? AND attempt_version=?
                  AND status IN ('running','waiting_external')""",
                (now, now, attempt.attempt_id, attempt.version),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "attempt_failure_cas_conflict",
                    "attempt failure compare-and-set lost",
                )
            await db.execute(
                """UPDATE execution_provider_action_batches
                SET status='ready_backfill',failure_set_id=?,
                    batch_version=batch_version+1,updated_at=?
                WHERE provider_batch_id=?""",
                (
                    failure_set.failure_set_id,
                    now,
                    attempt.provider_batch_id,
                ),
            )
            self._fault("failure_set_before_commit")
            row = await (
                await db.execute(
                    """SELECT * FROM execution_attempt_failure_sets
                    WHERE failure_set_id=?""",
                    (failure_set.failure_set_id,),
                )
            ).fetchone()
            assert row is not None
            return await self._failure_set_from_row(db, row)

    async def get_attempt_failure_set(
        self, failure_set_id: str
    ) -> AttemptFailureSet | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_attempt_failure_sets
                    WHERE failure_set_id=?""",
                    (failure_set_id,),
                )
            ).fetchone()
            return (
                None
                if row is None
                else await self._failure_set_from_row(db, row)
            )

    async def list_task_failure_reports(
        self, attempt_id: str
    ) -> tuple[TaskFailureReport, ...]:
        async with self._read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT reports.* FROM execution_task_failure_reports AS reports
                    JOIN execution_attempt_failure_set_members AS members
                      ON members.report_ref=reports.report_ref
                    JOIN execution_attempt_failure_sets AS sets
                      ON sets.failure_set_id=members.failure_set_id
                    WHERE sets.failed_attempt_id=?
                    ORDER BY members.provider_call_order,reports.report_ref""",
                    (attempt_id,),
                )
            ).fetchall()
            return tuple(self._row_to_failure_report(row) for row in rows)

    async def stage_external_wait(
        self, wait: TaskExternalWait
    ) -> TaskExternalWait:
        """Pause the same attempt/call without manufacturing a failure fact."""

        if wait.state is not ExternalWaitState.OPEN or wait.version != 0:
            raise IdempotencyConflict(
                "invalid_external_wait", "a new external wait must start open"
            )
        async with self._write_transaction() as db:
            existing_row = await (
                await db.execute(
                    """SELECT * FROM execution_task_external_waits
                    WHERE wait_ref=? OR (root_run_id=? AND call_record_id=?)""",
                    (wait.wait_ref, wait.root_run_id, wait.call_record_id),
                )
            ).fetchone()
            if existing_row is not None:
                existing = self._row_to_external_wait(existing_row)
                fields = (
                    "wait_ref",
                    "root_run_id",
                    "attempt_id",
                    "call_record_id",
                    "provider_call_id",
                    "command_boundary_ref",
                    "effect_id",
                    "wait_kind",
                    "required_action_ref",
                    "checkpoint_ref",
                    "resume_admission_state",
                    "evidence_refs",
                )
                if not all(
                    getattr(existing, field_name) == getattr(wait, field_name)
                    for field_name in fields
                ):
                    raise IdempotencyConflict(
                        "external_wait_conflict", "external wait replay differs"
                    )
                return existing
            attempt_row = await (
                await db.execute(
                    """SELECT * FROM execution_attempt_records
                    WHERE attempt_id=? AND root_run_id=?""",
                    (wait.attempt_id, wait.root_run_id),
                )
            ).fetchone()
            call_row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_calls
                    WHERE call_record_id=? AND root_run_id=?""",
                    (wait.call_record_id, wait.root_run_id),
                )
            ).fetchone()
            if attempt_row is None or call_row is None:
                raise IdempotencyConflict(
                    "external_wait_scope_missing",
                    "external wait requires an existing attempt and call",
                )
            attempt = self._row_to_attempt(attempt_row)
            call = self._row_to_provider_call(call_row)
            if (
                attempt.status is not AttemptStatus.RUNNING
                or attempt.provider_batch_id != call.provider_batch_id
                or call.provider_call_id != wait.provider_call_id
                or call.admission_state != wait.resume_admission_state
                or call.admission_state
                not in {
                    ProviderActionAdmissionState.ADMITTED,
                    ProviderActionAdmissionState.PREPARED,
                }
            ):
                raise IdempotencyConflict(
                    "external_wait_scope_conflict",
                    "external wait must pause the live call in its current admission state",
                )
            now = float(self._clock())
            await db.execute(
                """INSERT INTO execution_task_external_waits(
                wait_ref,schema_version,root_run_id,attempt_id,call_record_id,
                provider_call_id,command_boundary_ref,effect_id,wait_kind,
                required_action_ref,checkpoint_ref,resume_admission_state,
                evidence_refs_json,state,response_ref,wait_version,created_at,
                updated_at,resolved_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'open',NULL,0,?,?,NULL)""",
                (
                    wait.wait_ref,
                    wait.schema_version,
                    wait.root_run_id,
                    wait.attempt_id,
                    wait.call_record_id,
                    wait.provider_call_id,
                    wait.command_boundary_ref,
                    wait.effect_id,
                    wait.wait_kind.value,
                    wait.required_action_ref,
                    wait.checkpoint_ref,
                    wait.resume_admission_state.value,
                    canonical_json(list(wait.evidence_refs)),
                    now,
                    now,
                ),
            )
            call_cursor = await db.execute(
                """UPDATE execution_provider_action_calls
                SET admission_state='waiting_external',call_version=call_version+1,
                    updated_at=?
                WHERE call_record_id=? AND call_version=?
                  AND admission_state=?""",
                (
                    now,
                    call.call_record_id,
                    call.version,
                    call.admission_state.value,
                ),
            )
            attempt_cursor = await db.execute(
                """UPDATE execution_attempt_records
                SET status='waiting_external',budget_eligible=0,
                    attempt_version=attempt_version+1,updated_at=?
                WHERE attempt_id=? AND attempt_version=? AND status='running'""",
                (now, attempt.attempt_id, attempt.version),
            )
            if call_cursor.rowcount != 1 or attempt_cursor.rowcount != 1:
                raise VersionConflict(
                    "external_wait_cas_conflict",
                    "external wait compare-and-set lost",
                )
            await db.execute(
                """UPDATE execution_task_goals
                SET status='waiting_external',goal_version=goal_version+1,
                    updated_at=?
                WHERE root_run_id=? AND status='active'""",
                (now, wait.root_run_id),
            )
            await db.execute(
                """UPDATE execution_provider_action_batches
                SET status='waiting_external',batch_version=batch_version+1,
                    updated_at=?
                WHERE provider_batch_id=?""",
                (now, attempt.provider_batch_id),
            )
            self._fault("external_wait_before_commit")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_task_external_waits WHERE wait_ref=?",
                    (wait.wait_ref,),
                )
            ).fetchone()
            assert row is not None
            return self._row_to_external_wait(row)

    async def resume_external_wait(
        self,
        wait_ref: str,
        response_ref: str,
        *,
        expected_version: int,
    ) -> TaskExternalWait:
        """Satisfy a wait and resume the exact same attempt, plan and call."""

        if not str(response_ref).strip():
            raise ValueError("response_ref is required")
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_task_external_waits WHERE wait_ref=?",
                    (wait_ref,),
                )
            ).fetchone()
            if row is None:
                raise IdempotencyConflict(
                    "external_wait_missing", "external wait does not exist"
                )
            wait = self._row_to_external_wait(row)
            if wait.state is ExternalWaitState.SATISFIED:
                if wait.response_ref != response_ref:
                    raise IdempotencyConflict(
                        "external_wait_response_conflict",
                        "external wait already has another response",
                    )
                return wait
            if wait.state is ExternalWaitState.CANCELLED:
                raise TerminalConflict(
                    "external_wait_cancelled", "cancelled external wait cannot resume"
                )
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_task_external_waits
                SET state='satisfied',response_ref=?,wait_version=wait_version+1,
                    updated_at=?,resolved_at=?
                WHERE wait_ref=? AND state='open' AND wait_version=?""",
                (response_ref, now, now, wait_ref, expected_version),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "external_wait_cas_conflict",
                    "external wait response compare-and-set lost",
                )
            call_row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_action_calls
                    WHERE call_record_id=?""",
                    (wait.call_record_id,),
                )
            ).fetchone()
            attempt_row = await (
                await db.execute(
                    "SELECT * FROM execution_attempt_records WHERE attempt_id=?",
                    (wait.attempt_id,),
                )
            ).fetchone()
            if call_row is None or attempt_row is None:
                raise IdempotencyConflict(
                    "external_wait_orphaned", "external wait lost its call or attempt"
                )
            call = self._row_to_provider_call(call_row)
            attempt = self._row_to_attempt(attempt_row)
            call_cursor = await db.execute(
                """UPDATE execution_provider_action_calls
                SET admission_state=?,call_version=call_version+1,updated_at=?
                WHERE call_record_id=? AND admission_state='waiting_external'
                  AND call_version=?""",
                (
                    wait.resume_admission_state.value,
                    now,
                    call.call_record_id,
                    call.version,
                ),
            )
            attempt_cursor = await db.execute(
                """UPDATE execution_attempt_records
                SET status='running',budget_eligible=1,
                    attempt_version=attempt_version+1,updated_at=?
                WHERE attempt_id=? AND status='waiting_external'
                  AND attempt_version=?""",
                (now, attempt.attempt_id, attempt.version),
            )
            if call_cursor.rowcount != 1 or attempt_cursor.rowcount != 1:
                raise VersionConflict(
                    "external_wait_resume_conflict",
                    "external wait resume boundary is stale",
                )
            await db.execute(
                """UPDATE execution_task_goals
                SET status='active',goal_version=goal_version+1,updated_at=?
                WHERE root_run_id=? AND status='waiting_external'""",
                (now, wait.root_run_id),
            )
            await db.execute(
                """UPDATE execution_provider_action_batches
                SET status='running',batch_version=batch_version+1,updated_at=?
                WHERE provider_batch_id=? AND status='waiting_external'""",
                (now, attempt.provider_batch_id),
            )
            self._fault("external_wait_resume_before_commit")
            updated = await (
                await db.execute(
                    "SELECT * FROM execution_task_external_waits WHERE wait_ref=?",
                    (wait_ref,),
                )
            ).fetchone()
            assert updated is not None
            return self._row_to_external_wait(updated)

    async def get_external_wait(self, wait_ref: str) -> TaskExternalWait | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_task_external_waits WHERE wait_ref=?",
                    (wait_ref,),
                )
            ).fetchone()
            return None if row is None else self._row_to_external_wait(row)

    async def issue_profile_launch_ticket(
        self, ticket: ProfileLaunchTicket
    ) -> ProfileLaunchTicket:
        """Issue one immutable launch authorization for a provider spawn call."""

        if (
            ticket.state is not ProfileLaunchTicketState.ISSUED
            or ticket.version != 0
            or ticket.child_command_id is not None
            or ticket.child_run_id is not None
        ):
            raise IdempotencyConflict(
                "invalid_profile_ticket", "new launch tickets must start issued"
            )
        async with self._write_transaction() as db:
            personal_selection_id = ticket.personal_selection_id
            personal_selection_fingerprint = (
                ticket.personal_selection_fingerprint
            )
            if ticket.profile_key == "workflow.personal_v1":
                if (
                    personal_selection_id is None
                    or personal_selection_fingerprint is None
                ):
                    raise IdempotencyConflict(
                        "personal_selection_required",
                        "personal workflow tickets require one frozen selection",
                    )
                consumed_selection = await (
                    await db.execute(
                        """SELECT * FROM execution_profile_launch_tickets
                        WHERE parent_run_id=? AND personal_selection_id=?""",
                        (ticket.parent_run_id, personal_selection_id),
                    )
                ).fetchone()
                if consumed_selection is not None and (
                    str(consumed_selection["ticket_ref"]) != ticket.ticket_ref
                    or str(consumed_selection["spawn_call_id"])
                    != ticket.spawn_call_id
                ):
                    raise IdempotencyConflict(
                        "selection_already_consumed",
                        "personal workflow selection already owns a child launch",
                    )
            elif (
                personal_selection_id is not None
                or personal_selection_fingerprint is not None
            ):
                raise IdempotencyConflict(
                    "personal_selection_profile_mismatch",
                    "only workflow.personal_v1 may bind a personal selection",
                )
            existing_row = await (
                await db.execute(
                    """SELECT * FROM execution_profile_launch_tickets
                    WHERE ticket_ref=?
                       OR (parent_run_id=? AND spawn_call_id=?)""",
                    (
                        ticket.ticket_ref,
                        ticket.parent_run_id,
                        ticket.spawn_call_id,
                    ),
                )
            ).fetchone()
            if existing_row is not None:
                existing = self._row_to_profile_ticket(existing_row)
                if not self._same_ticket_intent(existing, ticket):
                    raise IdempotencyConflict(
                        "profile_ticket_conflict",
                        "parent spawn call already names another launch intent",
                    )
                return existing
            parent = await (
                await db.execute(
                    """SELECT root_run_id,status FROM execution_runs
                    WHERE run_id=?""",
                    (ticket.parent_run_id,),
                )
            ).fetchone()
            goal = await (
                await db.execute(
                    """SELECT task_scope_id,status FROM execution_task_goals
                    WHERE root_run_id=?""",
                    (ticket.root_run_id,),
                )
            ).fetchone()
            attempt = await (
                await db.execute(
                    """SELECT run_id,root_run_id,provider_turn_id,
                    provider_batch_id,status FROM execution_attempt_records
                    WHERE attempt_id=?""",
                    (ticket.attempt_id,),
                )
            ).fetchone()
            call = await (
                await db.execute(
                    """SELECT admission_state FROM execution_provider_action_calls
                    WHERE root_run_id=? AND provider_batch_id=?
                      AND provider_call_id=?""",
                    (
                        ticket.root_run_id,
                        str(attempt["provider_batch_id"]) if attempt is not None else "",
                        ticket.spawn_call_id,
                    ),
                )
            ).fetchone()
            if (
                parent is None
                or goal is None
                or attempt is None
                or call is None
                or str(parent["root_run_id"]) != ticket.root_run_id
                or str(parent["status"]) in {
                    RunStatus.COMPLETED.value,
                    RunStatus.FAILED.value,
                    RunStatus.CANCELLED.value,
                }
                or str(goal["task_scope_id"]) != ticket.task_scope_id
                or str(goal["status"])
                not in {
                    TaskGoalStatus.ACTIVE.value,
                    TaskGoalStatus.WAITING_EXTERNAL.value,
                }
                or str(attempt["run_id"]) != ticket.parent_run_id
                or str(attempt["root_run_id"]) != ticket.root_run_id
                or str(attempt["provider_turn_id"]) != ticket.provider_turn_id
                or str(attempt["status"])
                not in {
                    AttemptStatus.RUNNING.value,
                    AttemptStatus.WAITING_EXTERNAL.value,
                }
                or str(call["admission_state"])
                not in {
                    ProviderActionAdmissionState.ADMITTED.value,
                    ProviderActionAdmissionState.PREPARED.value,
                    ProviderActionAdmissionState.WAITING_EXTERNAL.value,
                }
            ):
                raise IdempotencyConflict(
                    "profile_ticket_scope_conflict",
                    "launch ticket must bind the live parent attempt and spawn call",
                )
            if ticket.trigger_failure_set_id is not None:
                trigger = await (
                    await db.execute(
                        """SELECT 1 FROM execution_attempt_failure_sets
                        WHERE failure_set_id=? AND root_run_id=?""",
                        (ticket.trigger_failure_set_id, ticket.root_run_id),
                    )
                ).fetchone()
                if trigger is None:
                    raise IdempotencyConflict(
                        "profile_ticket_trigger_missing",
                        "ticket trigger failure set is not durable",
                    )
            now = float(self._clock())
            try:
                await db.execute(
                    """INSERT INTO execution_profile_launch_tickets(
                    ticket_ref,schema_version,parent_run_id,root_run_id,
                    task_scope_id,attempt_id,provider_turn_id,profile_key,
                    driver_kind,profile_catalog_generation,
                    capability_snapshot_ref,task_grant_ref,spawn_call_id,
                    personal_selection_id,personal_selection_fingerprint,
                    trigger_failure_set_id,request_fingerprint,state,
                    child_command_id,child_run_id,ticket_version,created_at,
                    updated_at,consumed_at,cancelled_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'issued',
                    NULL,NULL,0,?,?,NULL,NULL)""",
                    (
                        ticket.ticket_ref,
                        ticket.schema_version,
                        ticket.parent_run_id,
                        ticket.root_run_id,
                        ticket.task_scope_id,
                        ticket.attempt_id,
                        ticket.provider_turn_id,
                        ticket.profile_key,
                        ticket.driver_kind,
                        ticket.profile_catalog_generation,
                        ticket.capability_snapshot_ref,
                        ticket.task_grant_ref,
                        ticket.spawn_call_id,
                        personal_selection_id,
                        personal_selection_fingerprint,
                        ticket.trigger_failure_set_id,
                        ticket.request_fingerprint,
                        now,
                        now,
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                if personal_selection_id is not None:
                    raise IdempotencyConflict(
                        "selection_already_consumed",
                        "personal workflow selection already owns a child launch",
                    ) from exc
                raise IdempotencyConflict(
                    "profile_ticket_unique_conflict",
                    "parent spawn call already owns a launch ticket",
                ) from exc
            self._fault("profile_ticket_issue_after_insert")
            self._fault("profile_ticket_issue_before_commit")
            row = await (
                await db.execute(
                    """SELECT * FROM execution_profile_launch_tickets
                    WHERE ticket_ref=?""",
                    (ticket.ticket_ref,),
                )
            ).fetchone()
            assert row is not None
            return self._row_to_profile_ticket(row)

    async def get_profile_launch_ticket(
        self, ticket_ref: str
    ) -> ProfileLaunchTicket | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_profile_launch_tickets
                    WHERE ticket_ref=?""",
                    (ticket_ref,),
                )
            ).fetchone()
            return None if row is None else self._row_to_profile_ticket(row)

    async def claim_profile_launch_and_commit_child(
        self,
        ticket_ref: str,
        intent: ChildCommandIntent,
        launch_request: Mapping[str, Any],
        *,
        expected_ticket_version: int,
        start_snapshot: RunStartSnapshotRecord | None = None,
        start_commit_extensions: Sequence[Any] = (),
        recovery_lease: RecoveryLease | None = None,
        transaction_hook: Any | None = None,
    ) -> ChildCommandRecord:
        """Consume a profile ticket and create its child command/link atomically."""

        frozen_request = thaw_json(dict(launch_request))
        assert isinstance(frozen_request, dict)
        async with self._write_transaction() as db:
            await self._assert_optional_recovery_fence_tx(
                db, recovery_lease, intent.parent_run_id
            )
            ticket_row = await (
                await db.execute(
                    """SELECT * FROM execution_profile_launch_tickets
                    WHERE ticket_ref=?""",
                    (ticket_ref,),
                )
            ).fetchone()
            if ticket_row is None:
                raise IdempotencyConflict(
                    "profile_ticket_missing",
                    "profile launch ticket does not exist",
                )
            ticket = self._row_to_profile_ticket(ticket_row)
            if fingerprint_json(frozen_request) != ticket.request_fingerprint:
                raise IdempotencyConflict(
                    "profile_ticket_fingerprint_conflict",
                    "launch request fingerprint differs from the issued ticket",
                )
            expected_launch = {
                "profile_key": ticket.profile_key,
                "parent_run_id": ticket.parent_run_id,
                "root_run_id": ticket.root_run_id,
                "task_scope_id": ticket.task_scope_id,
                "trigger_failure_set_id": ticket.trigger_failure_set_id,
                "profile_catalog_generation": ticket.profile_catalog_generation,
            }
            if any(
                frozen_request.get(field_name) != expected
                for field_name, expected in expected_launch.items()
            ):
                raise IdempotencyConflict(
                    "profile_ticket_request_binding_conflict",
                    "launch request identity differs from the durable ticket",
                )
            if (
                intent.parent_run_id != ticket.parent_run_id
                or intent.command_id != ticket.child_command_id
                and ticket.child_command_id is not None
                or intent.child_run_id != ticket.child_run_id
                and ticket.child_run_id is not None
                or intent.child_spec.context.root_run_id != ticket.root_run_id
                or intent.child_spec.profile_key != ticket.profile_key
                or intent.child_spec.driver_kind != ticket.driver_kind
                or intent.capability_snapshot_ref
                != ticket.capability_snapshot_ref
            ):
                raise IdempotencyConflict(
                    "profile_ticket_child_binding_conflict",
                    "child command differs from the durable profile ticket",
                )
            if ticket.profile_key == "workflow.personal_v1":
                selection = intent.child_request.get(
                    "personal_workflow_selection"
                )
                if not isinstance(selection, Mapping) or (
                    str(selection.get("selection_id") or "")
                    != str(ticket.personal_selection_id)
                    or str(selection.get("selection_fingerprint") or "")
                    != str(ticket.personal_selection_fingerprint)
                ):
                    raise IdempotencyConflict(
                        "personal_selection_child_binding_conflict",
                        "child start does not contain the ticket's frozen selection",
                    )

            parent = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?",
                    (ticket.parent_run_id,),
                )
            ).fetchone()
            if parent is None:
                raise RunNotFound(
                    "parent_not_found", "delegate parent must already be durable"
                )
            existing_command = await (
                await db.execute(
                    """SELECT * FROM execution_child_commands
                    WHERE operation_id=? OR (parent_run_id=? AND command_id=?)
                    OR child_run_id=? ORDER BY operation_id=? DESC LIMIT 1""",
                    (
                        intent.operation_id,
                        intent.parent_run_id,
                        intent.command_id,
                        intent.child_run_id,
                        intent.operation_id,
                    ),
                )
            ).fetchone()
            if existing_command is not None and (
                str(existing_command["operation_id"]) != intent.operation_id
                or str(existing_command["intent_fingerprint"])
                != intent.intent_fingerprint
            ):
                raise IdempotencyConflict(
                    "child_operation_conflict",
                    "operation, parent command, or child id names another intent",
                )

            if ticket.state is ProfileLaunchTicketState.CANCELLED:
                raise TerminalConflict(
                    "profile_ticket_cancelled",
                    "cancelled profile launch ticket cannot create a child",
                )
            if ticket.state is ProfileLaunchTicketState.ISSUED:
                goal = await (
                    await db.execute(
                        "SELECT status FROM execution_task_goals WHERE root_run_id=?",
                        (ticket.root_run_id,),
                    )
                ).fetchone()
                attempt = await (
                    await db.execute(
                        "SELECT status FROM execution_attempt_records WHERE attempt_id=?",
                        (ticket.attempt_id,),
                    )
                ).fetchone()
                call = await (
                    await db.execute(
                        """SELECT admission_state
                        FROM execution_provider_action_calls
                        WHERE root_run_id=? AND provider_call_id=?""",
                        (ticket.root_run_id, ticket.spawn_call_id),
                    )
                ).fetchone()
                if (
                    goal is None
                    or attempt is None
                    or call is None
                    or str(parent["status"]) in {
                        RunStatus.COMPLETED.value,
                        RunStatus.FAILED.value,
                        RunStatus.CANCELLED.value,
                    }
                    or str(goal["status"])
                    not in {
                        TaskGoalStatus.ACTIVE.value,
                        TaskGoalStatus.WAITING_EXTERNAL.value,
                    }
                    or str(attempt["status"])
                    not in {
                        AttemptStatus.RUNNING.value,
                        AttemptStatus.WAITING_EXTERNAL.value,
                    }
                    or str(call["admission_state"])
                    not in {
                        ProviderActionAdmissionState.ADMITTED.value,
                        ProviderActionAdmissionState.PREPARED.value,
                        ProviderActionAdmissionState.WAITING_EXTERNAL.value,
                    }
                ):
                    raise TerminalConflict(
                        "profile_ticket_parent_terminal",
                        "launch ticket parent boundary is no longer live",
                    )
                now = float(self._clock())
                try:
                    cursor = await db.execute(
                        """UPDATE execution_profile_launch_tickets
                        SET state='consumed',child_command_id=?,child_run_id=?,
                            ticket_version=ticket_version+1,updated_at=?,
                            consumed_at=?
                        WHERE ticket_ref=? AND state='issued'
                          AND ticket_version=?""",
                        (
                            intent.command_id,
                            intent.child_run_id,
                            now,
                            now,
                            ticket_ref,
                            expected_ticket_version,
                        ),
                    )
                except aiosqlite.IntegrityError as exc:
                    raise IdempotencyConflict(
                        "profile_ticket_child_unique_conflict",
                        "child identity is already bound to another launch ticket",
                    ) from exc
                if cursor.rowcount != 1:
                    raise VersionConflict(
                        "profile_ticket_cas_conflict",
                        "launch ticket consume compare-and-set lost",
                    )
                self._fault("profile_ticket_claim_after_ticket_cas")
            elif (
                ticket.child_command_id,
                ticket.child_run_id,
            ) != (intent.command_id, intent.child_run_id):
                raise IdempotencyConflict(
                    "profile_ticket_child_conflict",
                    "launch ticket already names another child",
                )

            expected_owner = (
                await self._required_recovery_owner_tx(db)
                if recovery_lease is not None
                else await self._required_start_owner_tx(db)
            )
            self._assert_run_owner(parent, expected_owner)
            if existing_command is None:
                now = float(self._clock())
                await db.execute(
                    """INSERT INTO execution_child_commands(
                    operation_id,schema_version,parent_run_id,command_id,
                    child_run_id,profile_key,join_policy,
                    capability_snapshot_ref,capability_subset_json,
                    child_request_json,child_spec_json,intent_fingerprint,
                    status,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'pending',?,?)""",
                    (
                        intent.operation_id,
                        intent.schema_version,
                        intent.parent_run_id,
                        intent.command_id,
                        intent.child_run_id,
                        intent.child_spec.profile_key,
                        intent.attachment_policy.value,
                        intent.capability_snapshot_ref,
                        canonical_json(list(intent.capability_subset)),
                        canonical_json(thaw_json(intent.child_request)),
                        canonical_json(intent.child_spec.to_dict()),
                        intent.intent_fingerprint,
                        now,
                        now,
                    ),
                )
            self._fault("profile_ticket_claim_after_child_command")

            child, child_created = await self._insert_run_for_owner_tx(
                db, intent.child_spec, version=0, expected_owner=expected_owner
            )
            link_id = self._stable_child_link_id(intent.operation_id)
            existing_link = await (
                await db.execute(
                    "SELECT * FROM execution_run_links WHERE link_id=?",
                    (link_id,),
                )
            ).fetchone()
            expected_link = {
                "root_run_id": intent.child_spec.context.root_run_id,
                "parent_run_id": intent.parent_run_id,
                "child_run_id": intent.child_run_id,
                "attachment_policy": intent.attachment_policy.value,
                "link_kind": LinkKind.STRUCTURAL.value,
                "domain_kind": "",
                "domain_id": "",
            }
            if existing_link is None:
                await db.execute(
                    """INSERT INTO execution_run_links(
                    link_id,schema_version,root_run_id,parent_run_id,
                    child_run_id,attachment_policy,link_kind,domain_kind,
                    domain_id,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        link_id,
                        1,
                        expected_link["root_run_id"],
                        expected_link["parent_run_id"],
                        expected_link["child_run_id"],
                        expected_link["attachment_policy"],
                        expected_link["link_kind"],
                        "",
                        "",
                        self._clock(),
                    ),
                )
                cursor = await db.execute(
                    """UPDATE execution_runs SET version=version+1,updated_at=?
                    WHERE run_id=? AND version=? AND terminal_event_id IS NULL""",
                    (self._clock(), intent.child_run_id, int(child["version"])),
                )
                if cursor.rowcount != 1:
                    raise VersionConflict(
                        "stale_run_version",
                        "child changed before launch link commit",
                    )
            elif any(
                str(existing_link[field_name]) != value
                for field_name, value in expected_link.items()
            ):
                raise IdempotencyConflict(
                    "child_link_conflict",
                    "profile launch child link differs from the durable ticket",
                )
            elif child_created:
                raise IdempotencyConflict(
                    "child_link_without_run",
                    "profile launch link existed before its child run",
                )
            self._fault("profile_ticket_claim_after_child_link")
            if start_snapshot is not None:
                if start_snapshot.run_id != intent.child_run_id:
                    raise IdempotencyConflict(
                        "child_start_scope_conflict",
                        "child start snapshot is bound to another Run",
                    )
                await self._ensure_start_snapshot_tx(
                    db,
                    spec=intent.child_spec,
                    start_snapshot=start_snapshot,
                    run_created=child_created,
                    start_commit_extensions=start_commit_extensions,
                )
                self._fault(
                    "profile_ticket_claim_after_child_start_snapshot"
                )
            if transaction_hook is not None:
                hooked = transaction_hook(db, intent)
                if inspect.isawaitable(hooked):
                    await hooked
            self._fault("profile_ticket_claim_before_commit")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (intent.operation_id,),
                )
            ).fetchone()
            assert row is not None
            return self._row_to_child_command(row)

    async def consume_profile_launch_ticket(
        self,
        ticket_ref: str,
        *,
        request_fingerprint: str,
        child_command_id: str,
        child_run_id: str,
        expected_version: int,
    ) -> ProfileLaunchConsumeResult:
        """One-shot CAS consume; exact retries return the original child identity."""

        if not child_command_id.strip() or not child_run_id.strip():
            raise ValueError("child command and run identities are required")
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_profile_launch_tickets
                    WHERE ticket_ref=?""",
                    (ticket_ref,),
                )
            ).fetchone()
            if row is None:
                raise IdempotencyConflict(
                    "profile_ticket_missing", "profile launch ticket does not exist"
                )
            ticket = self._row_to_profile_ticket(row)
            if ticket.request_fingerprint != request_fingerprint:
                raise IdempotencyConflict(
                    "profile_ticket_fingerprint_conflict",
                    "launch request fingerprint differs from the issued ticket",
                )
            if ticket.state is ProfileLaunchTicketState.CONSUMED:
                if (
                    ticket.child_command_id,
                    ticket.child_run_id,
                ) != (child_command_id, child_run_id):
                    raise IdempotencyConflict(
                        "profile_ticket_child_conflict",
                        "launch ticket already names another child",
                    )
                return ProfileLaunchConsumeResult(ticket, True)
            if ticket.state is ProfileLaunchTicketState.CANCELLED:
                raise TerminalConflict(
                    "profile_ticket_cancelled",
                    "cancelled profile launch ticket cannot be consumed",
                )
            parent = await (
                await db.execute(
                    "SELECT status FROM execution_runs WHERE run_id=?",
                    (ticket.parent_run_id,),
                )
            ).fetchone()
            goal = await (
                await db.execute(
                    "SELECT status FROM execution_task_goals WHERE root_run_id=?",
                    (ticket.root_run_id,),
                )
            ).fetchone()
            attempt = await (
                await db.execute(
                    "SELECT status FROM execution_attempt_records WHERE attempt_id=?",
                    (ticket.attempt_id,),
                )
            ).fetchone()
            call = await (
                await db.execute(
                    """SELECT admission_state FROM execution_provider_action_calls
                    WHERE root_run_id=? AND provider_call_id=?""",
                    (ticket.root_run_id, ticket.spawn_call_id),
                )
            ).fetchone()
            if (
                parent is None
                or goal is None
                or attempt is None
                or call is None
                or str(parent["status"])
                in {
                    RunStatus.COMPLETED.value,
                    RunStatus.FAILED.value,
                    RunStatus.CANCELLED.value,
                }
                or str(goal["status"])
                not in {
                    TaskGoalStatus.ACTIVE.value,
                    TaskGoalStatus.WAITING_EXTERNAL.value,
                }
                or str(attempt["status"])
                not in {
                    AttemptStatus.RUNNING.value,
                    AttemptStatus.WAITING_EXTERNAL.value,
                }
                or str(call["admission_state"])
                not in {
                    ProviderActionAdmissionState.ADMITTED.value,
                    ProviderActionAdmissionState.PREPARED.value,
                    ProviderActionAdmissionState.WAITING_EXTERNAL.value,
                }
            ):
                raise TerminalConflict(
                    "profile_ticket_parent_terminal",
                    "launch ticket parent boundary is no longer live",
                )
            now = float(self._clock())
            try:
                cursor = await db.execute(
                    """UPDATE execution_profile_launch_tickets
                    SET state='consumed',child_command_id=?,child_run_id=?,
                        ticket_version=ticket_version+1,updated_at=?,consumed_at=?
                    WHERE ticket_ref=? AND state='issued' AND ticket_version=?""",
                    (
                        child_command_id,
                        child_run_id,
                        now,
                        now,
                        ticket_ref,
                        expected_version,
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                raise IdempotencyConflict(
                    "profile_ticket_child_unique_conflict",
                    "child identity is already bound to another launch ticket",
                ) from exc
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "profile_ticket_cas_conflict",
                    "launch ticket consume compare-and-set lost",
                )
            self._fault("profile_ticket_consume_after_cas")
            self._fault("profile_ticket_consume_before_commit")
            updated = await (
                await db.execute(
                    """SELECT * FROM execution_profile_launch_tickets
                    WHERE ticket_ref=?""",
                    (ticket_ref,),
                )
            ).fetchone()
            assert updated is not None
            return ProfileLaunchConsumeResult(
                self._row_to_profile_ticket(updated), False
            )

    async def cancel_task_goal(
        self, root_run_id: str, *, expected_version: int
    ) -> TaskGoalRecord:
        """CAS the goal cancelled; ticket recovery performs scoped cancellation."""

        async with self._write_transaction() as db:
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_task_goals
                SET status='cancelled',goal_version=goal_version+1,
                    updated_at=?,ended_at=?
                WHERE root_run_id=? AND goal_version=?
                  AND status IN ('active','waiting_external')""",
                (now, now, root_run_id, expected_version),
            )
            if cursor.rowcount != 1:
                row = await (
                    await db.execute(
                        "SELECT * FROM execution_task_goals WHERE root_run_id=?",
                        (root_run_id,),
                    )
                ).fetchone()
                if row is not None and str(row["status"]) == TaskGoalStatus.CANCELLED.value:
                    return self._row_to_task_goal(row)
                raise VersionConflict(
                    "task_goal_cas_conflict",
                    "task goal cancellation compare-and-set lost",
                )
            row = await (
                await db.execute(
                    "SELECT * FROM execution_task_goals WHERE root_run_id=?",
                    (root_run_id,),
                )
            ).fetchone()
            assert row is not None
            return self._row_to_task_goal(row)

    async def recover_profile_launch_tickets(
        self,
        *,
        parent_run_id: str | None = None,
        limit: int = 100,
    ) -> tuple[ProfileLaunchTicket, ...]:
        """Cancel explicitly cancelled parents, then return live issued tickets."""

        if limit <= 0:
            return ()
        async with self._write_transaction() as db:
            now = float(self._clock())
            if parent_run_id is not None:
                await db.execute(
                    """UPDATE execution_profile_launch_tickets AS tickets
                    SET state='cancelled',ticket_version=ticket_version+1,
                        updated_at=?,cancelled_at=?
                    WHERE tickets.state='issued'
                      AND tickets.parent_run_id=?
                      AND (
                        EXISTS(
                            SELECT 1 FROM execution_runs AS parent
                            WHERE parent.run_id=tickets.parent_run_id
                              AND parent.status='cancelled'
                        )
                        OR EXISTS(
                            SELECT 1 FROM execution_task_goals AS goal
                            WHERE goal.root_run_id=tickets.root_run_id
                              AND goal.status='cancelled'
                        )
                        OR EXISTS(
                            SELECT 1 FROM execution_attempt_records AS attempt
                            WHERE attempt.attempt_id=tickets.attempt_id
                              AND attempt.status='cancelled'
                        )
                      )""",
                    (now, now, parent_run_id),
                )
                rows = await (
                    await db.execute(
                        """SELECT tickets.*
                        FROM execution_profile_launch_tickets AS tickets
                        JOIN execution_runs AS parent
                          ON parent.run_id=tickets.parent_run_id
                        JOIN execution_task_goals AS goal
                          ON goal.root_run_id=tickets.root_run_id
                        JOIN execution_attempt_records AS attempt
                          ON attempt.attempt_id=tickets.attempt_id
                        JOIN execution_provider_action_calls AS calls
                          ON calls.root_run_id=tickets.root_run_id
                         AND calls.provider_call_id=tickets.spawn_call_id
                        WHERE tickets.state='issued'
                          AND tickets.parent_run_id=?
                          AND parent.status NOT IN ('completed','failed','cancelled')
                          AND goal.status IN ('active','waiting_external')
                          AND attempt.status IN ('running','waiting_external')
                          AND calls.admission_state IN (
                            'admitted','prepared','waiting_external'
                          )
                        ORDER BY tickets.created_at,tickets.ticket_ref
                        LIMIT ?""",
                        (parent_run_id, limit),
                    )
                ).fetchall()
            else:
                await db.execute(
                    """UPDATE execution_profile_launch_tickets AS tickets
                    SET state='cancelled',ticket_version=ticket_version+1,
                        updated_at=?,cancelled_at=?
                    WHERE tickets.state='issued'
                      AND (
                        EXISTS(
                            SELECT 1 FROM execution_runs AS parent
                            WHERE parent.run_id=tickets.parent_run_id
                              AND parent.status='cancelled'
                        )
                        OR EXISTS(
                            SELECT 1 FROM execution_task_goals AS goal
                            WHERE goal.root_run_id=tickets.root_run_id
                              AND goal.status='cancelled'
                        )
                        OR EXISTS(
                            SELECT 1 FROM execution_attempt_records AS attempt
                            WHERE attempt.attempt_id=tickets.attempt_id
                              AND attempt.status='cancelled'
                        )
                      )""",
                    (now, now),
                )
                rows = await (
                    await db.execute(
                        """SELECT tickets.*
                    FROM execution_profile_launch_tickets AS tickets
                    JOIN execution_runs AS parent
                      ON parent.run_id=tickets.parent_run_id
                    JOIN execution_task_goals AS goal
                      ON goal.root_run_id=tickets.root_run_id
                    JOIN execution_attempt_records AS attempt
                      ON attempt.attempt_id=tickets.attempt_id
                    JOIN execution_provider_action_calls AS calls
                      ON calls.root_run_id=tickets.root_run_id
                     AND calls.provider_call_id=tickets.spawn_call_id
                    WHERE tickets.state='issued'
                      AND parent.status NOT IN ('completed','failed','cancelled')
                      AND goal.status IN ('active','waiting_external')
                      AND attempt.status IN ('running','waiting_external')
                      AND calls.admission_state IN (
                        'admitted','prepared','waiting_external'
                    )
                    ORDER BY tickets.created_at,tickets.ticket_ref
                    LIMIT ?""",
                        (limit,),
                    )
                ).fetchall()
            self._fault("profile_ticket_recover_before_commit")
            return tuple(self._row_to_profile_ticket(row) for row in rows)

    async def start_workflow(
        self,
        spec: RunCreate,
        workflow: WorkflowRunSeed,
        *,
        start_snapshot: RunStartSnapshotRecord | None = None,
        start_commit_extensions: Sequence[Any] = (),
        association_event: RunEventCandidate | None = None,
        accepted_event: RunEventCandidate | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        admission_launch: AdmissionLaunchClaim | None = None,
    ) -> WorkflowStartResult:
        if spec.persistence_level is not PersistenceLevel.DURABLE:
            raise PersistenceRequired(
                "workflow_must_be_durable", "workflow runs are durable from creation"
            )
        if accepted_event is None and deliveries:
            raise IdempotencyConflict(
                "delivery_without_event", "workflow deliveries require an accepted event"
            )
        if accepted_event is not None and accepted_event.status != OutcomeStatus.ACCEPTED:
            raise IdempotencyConflict(
                "invalid_accepted_event", "workflow start event must be accepted"
            )
        async with self._write_transaction() as db:
            row, created = await self._insert_run_tx(db, spec, version=0)
            if start_snapshot is not None:
                if start_snapshot.run_id != spec.run_id:
                    raise IdempotencyConflict(
                        "run_start_scope_conflict",
                        "workflow start snapshot belongs to another Run",
                    )
                await self._ensure_start_snapshot_tx(
                    db,
                    spec=spec,
                    start_snapshot=start_snapshot,
                    run_created=created,
                    start_commit_extensions=start_commit_extensions,
                )
            elif start_commit_extensions:
                raise IdempotencyConflict(
                    "start_extension_without_snapshot",
                    "workflow start extensions require a start snapshot",
                )
            record = self._row_to_record(row)
            self._fault("start_workflow_after_execution")
            if admission_launch is not None:
                self._fault("batch_boundary_after_promotion")
            workflow_created = await self._insert_workflow_tx(db, run=record, workflow=workflow)
            self._fault("start_workflow_after_workflow")
            admission_consumed = created
            if admission_launch is not None:
                await self._assert_recovery_fence_tx(db, admission_launch.recovery_lease, run_id=spec.run_id)
                continuation, boundary = await self._admission_tx(db, spec.run_id)
                if workflow_created:
                    if boundary != admission_launch.boundary:
                        raise VersionConflict("stale_admission_version", "workflow launch claim differs")
                    launched = self._advance_admission(boundary, AdmissionPhase.LAUNCHED,
                        continuation.version + 1)
                    await self._save_continuation_tx(db, run=row, expected_version=continuation.version,
                        payload_json=self._continuation_payload_json(self._with_admission(continuation.payload, launched)),
                        decision=None, now=float(self._clock()))
                    admission_consumed = True
                    self._fault("batch_boundary_after_continuation")
                elif boundary.phase is not AdmissionPhase.LAUNCHED:
                    raise IdempotencyConflict("workflow_start_conflict", "workflow replay has unconsumed admission")
            elif not created and workflow_created:
                admitted = await (await db.execute(
                    "SELECT 1 FROM execution_continuations WHERE run_id=? AND json_type(pending_prepared_call_json,'$._admission') IS NOT NULL",
                    (spec.run_id,),
                )).fetchone()
                if admitted is not None:
                    raise DecisionConflict("admission_required", "an admitted workflow requires its launch claim")
            if association_event is not None:
                _, row, _ = await self._append_event_tx(
                    db, row, expected_version=record.version,
                    event=association_event, deliveries=(),
                )
                record = self._row_to_record(row)
            if accepted_event is not None:
                _, updated, _ = await self._append_event_tx(
                    db,
                    row,
                    expected_version=record.version,
                    event=accepted_event,
                    deliveries=deliveries,
                )
                record = self._row_to_record(updated)
            if admission_launch is not None:
                self._fault("batch_boundary_after_waiting_event")
                self._fault("batch_boundary_before_commit")
            self._fault("start_workflow_before_commit")
            await db.commit()
            return WorkflowStartResult(record, created, workflow_created,
                admission_consumed, workflow_created and (created or admission_consumed))


__all__ = [
    "CheckpointExecutionError",
    "ContinuationRecord",
    "ExecutionTx",
    "ExecutionRuntimeState",
    "LegacyDrainLease",
    "LegacyDrainRef",
    "RuntimeActivationCommand",
    "RuntimeActivationError",
    "SqliteExecutionUnitOfWork",
]
