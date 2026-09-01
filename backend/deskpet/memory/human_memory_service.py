# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Typed Host facade for the fresh human-memory composition.

Identity and authority are constructor-bound.  Public request DTOs deliberately
contain no subject, allowed-scope set, composition mode, database path, binding
revision, or worker authority fields.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from simple_harness import (
    DeliveryRecipient,
    DisclosureContext,
    DisclosureGeneration,
    DisclosurePurpose,
    DisclosureReasonCode,
    DisclosureSource,
    DisclosureTrust,
    EvidenceReasonCode,
    EvidenceSourceKind,
    EvidenceRef,
    IntendedAudience,
    SanitizedEvidenceEnvelope,
    SanitizedEvidenceReceipt,
    TaskScopeMutationKind,
    TaskScopeMutationOperation,
    TaskScopeMutationOutcome,
    TaskScopeMutationPlan,
)

from deskpet.execution.foreground_queue import ForegroundQueueStore
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.schema import (
    StartupCompositionMode,
    StartupEpochDecision,
)
from deskpet.task_scope.projections import TaskScopeProjectionStore
from deskpet.task_scope.protocol import (
    canonical_hash,
    canonical_json,
    identifier,
    reject_private_payload,
)
from deskpet.task_scope.search import TaskScopeSearchStore
from deskpet.task_scope.store import CanonicalTaskScopeStore


class HumanMemoryHostServiceError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AuthenticatedHostSnapshot:
    subject: str
    principal_id: str
    authority_ref: str

    def __post_init__(self) -> None:
        identifier(self.subject, "subject", 512)
        identifier(self.principal_id, "principal_id", 512)
        identifier(self.authority_ref, "authority_ref", 512)


@dataclass(frozen=True, slots=True)
class CreateTaskScopeRequest:
    fixture_key: str
    title: str
    goal: str
    idempotency_key: str

    def __post_init__(self) -> None:
        identifier(self.fixture_key, "fixture_key", 512)
        identifier(self.title, "title", 4096)
        identifier(self.goal, "goal", 16_384)
        identifier(self.idempotency_key, "idempotency_key", 512)


@dataclass(frozen=True, slots=True)
class AppendDeterministicEventsRequest:
    scope_ref: str
    count: int
    canary: str
    idempotency_key: str

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)
        identifier(self.canary, "canary", 4096)
        identifier(self.idempotency_key, "idempotency_key", 512)
        if isinstance(self.count, bool) or not isinstance(self.count, int):
            raise TypeError("count must be an integer")
        if self.count < 1 or self.count > 100_000:
            raise ValueError("count must be between 1 and 100000")


@dataclass(frozen=True, slots=True)
class SaveCheckpointRequest:
    scope_ref: str
    checkpoint: Mapping[str, object]
    idempotency_key: str

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)
        identifier(self.idempotency_key, "idempotency_key", 512)
        reject_private_payload(dict(self.checkpoint), "checkpoint")


@dataclass(frozen=True, slots=True)
class SearchTaskScopesRequest:
    query: str
    max_candidates: int = 8
    cursor: str | None = None

    def __post_init__(self) -> None:
        identifier(self.query, "query", 65_536)
        if isinstance(self.max_candidates, bool) or not isinstance(
            self.max_candidates, int
        ):
            raise TypeError("max_candidates must be an integer")
        if not 1 <= self.max_candidates <= 100:
            raise ValueError("max_candidates must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class AppendPrimaryEventRequest:
    event: Mapping[str, object]
    idempotency_key: str

    def __post_init__(self) -> None:
        reject_private_payload(dict(self.event), "primary_event")
        identifier(self.idempotency_key, "idempotency_key", 512)


@dataclass(frozen=True, slots=True)
class MutateTaskScopeRequest:
    scope_ref: str
    kind: str
    value: str
    idempotency_key: str

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)
        identifier(self.kind, "mutation_kind", 64)
        identifier(self.value, "mutation_value", 32_768)
        identifier(self.idempotency_key, "idempotency_key", 512)


@dataclass(frozen=True, slots=True)
class AppendBindingRequest:
    scope_ref: str
    root: str
    idempotency_key: str

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)
        identifier(self.root, "root", 4096)
        identifier(self.idempotency_key, "idempotency_key", 512)


@dataclass(frozen=True, slots=True)
class ControlRunRequest:
    control: str
    reason: str
    idempotency_key: str

    def __post_init__(self) -> None:
        identifier(self.control, "control", 64)
        identifier(self.reason, "reason", 2048)
        identifier(self.idempotency_key, "idempotency_key", 512)


@dataclass(frozen=True, slots=True)
class AuditRefsRequest:
    scope_ref: str

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)


@dataclass(frozen=True, slots=True)
class OpenTaskScopeRequest:
    scope_ref: str
    live_probe: Mapping[str, object] | None = None
    expected_source_hash: str | None = None

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)
        if self.live_probe is not None:
            reject_private_payload(dict(self.live_probe), "live_probe")


@dataclass(frozen=True, slots=True)
class QueueTurnRequest:
    scope_ref: str
    delivery_key: str
    text: str

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)
        identifier(self.delivery_key, "delivery_key", 512)
        identifier(self.text, "text", 16_384)


@dataclass(frozen=True, slots=True)
class ReadTaskScopeViewRequest:
    scope_ref: str
    kind: str

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)
        identifier(self.kind, "kind", 64)


class DeterministicEventSeedPort(Protocol):
    """Verification-only bulk authority implemented by the canonical producer."""

    def append_deterministic_events(
        self,
        *,
        subject: str,
        task_scope_id: str,
        count: int,
        canary: str,
        idempotency_key: str,
    ) -> Awaitable[Mapping[str, object]]: ...


class WorkspaceBindingAppendPort(Protocol):
    async def append_binding(
        self,
        *,
        subject: str,
        task_scope_id: str,
        root: str,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...


class RecoveryLifecyclePort(Protocol):
    async def manifest(self, *, subject: str) -> Mapping[str, object]: ...

    async def emergency_export(self, *, subject: str) -> Mapping[str, object]: ...


class ForegroundSchedulerWakePort(Protocol):
    async def after_enqueue(self, *, subject: str) -> None: ...


@dataclass(frozen=True, slots=True)
class _RawSetSpec:
    logical_name: str
    table: str


_RAW_SET_ALLOWLIST = (
    _RawSetSpec("program.bootstrap", "human_memory_program_bootstrap"),
    _RawSetSpec("program.marker", "human_memory_program_marker"),
    _RawSetSpec("program.migration_chain", "human_memory_migration_chain"),
    _RawSetSpec("program.primary", "human_memory_primary_conversations"),
    _RawSetSpec("program.init_receipts", "human_memory_init_receipts"),
    _RawSetSpec("program.evidence", "human_memory_evidence"),
    _RawSetSpec("program.evidence_receipts", "human_memory_sanitization_receipts"),
    _RawSetSpec("scope.identities", "task_scopes"),
    _RawSetSpec("scope.revisions", "task_scope_canonical_revisions"),
    _RawSetSpec("scope.heads", "task_scope_heads"),
    _RawSetSpec("scope.events", "task_scope_events"),
    _RawSetSpec("scope.steps", "task_scope_steps"),
    _RawSetSpec("scope.evidence_links", "task_scope_evidence_links"),
    _RawSetSpec("scope.mutation_attempts", "task_scope_mutation_attempts"),
    _RawSetSpec("scope.mutation_decisions", "task_scope_mutation_decisions"),
    _RawSetSpec("scope.checkpoints", "task_scope_checkpoints"),
    _RawSetSpec("scope.provisions", "task_scope_provisions"),
    _RawSetSpec("scope.provision_receipts", "task_scope_provision_receipts"),
    _RawSetSpec("binding.proposals", "task_workspace_binding_proposals"),
    _RawSetSpec("binding.run_modes", "task_workspace_run_mode_snapshots"),
    _RawSetSpec("binding.revisions", "task_workspace_binding_revisions"),
    _RawSetSpec("binding.roots", "task_workspace_binding_roots"),
    _RawSetSpec("binding.heads", "task_workspace_binding_heads"),
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _json_cell(value: object) -> object:
    if isinstance(value, bytes):
        return {"blob_sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError("unsupported SQLite value in raw integrity manifest")


class HumanMemoryHostService:
    def __init__(
        self,
        db_path: str | Path,
        *,
        auth: AuthenticatedHostSnapshot,
        startup: StartupEpochDecision,
        deterministic_event_seed: DeterministicEventSeedPort | None = None,
        binding_append: WorkspaceBindingAppendPort | None = None,
        recovery: RecoveryLifecyclePort | None = None,
        scheduler_wake: ForegroundSchedulerWakePort | None = None,
    ) -> None:
        if startup.composition_mode is not StartupCompositionMode.HUMAN:
            raise HumanMemoryHostServiceError(
                "human_memory_program_legacy_database_unsupported"
            )
        self._db_path = Path(db_path)
        self._auth = auth
        self._startup = startup
        self._program = HumanMemoryProgramStore(self._db_path)
        self._scopes = CanonicalTaskScopeStore(self._db_path)
        self._projections = TaskScopeProjectionStore(self._db_path)
        self._search = TaskScopeSearchStore(self._db_path)
        self._foreground = ForegroundQueueStore(self._db_path)
        self._deterministic_event_seed = deterministic_event_seed
        self._binding_append = binding_append
        self._recovery = recovery
        self._scheduler_wake = scheduler_wake

    @property
    def startup_decision(self) -> StartupEpochDecision:
        return self._startup

    @property
    def recovery_available(self) -> bool:
        return self._recovery is not None

    async def open_primary(self) -> Mapping[str, object]:
        receipt = await self._program.initialize_subject(self._auth.subject)
        return {
            "primary_ref": receipt.primary_conversation_id,
            "receipt_ref": receipt.receipt_id,
            "receipt_hash": receipt.receipt_sha256,
        }

    async def append_primary_event(
        self, request: AppendPrimaryEventRequest
    ) -> Mapping[str, object]:
        committed = await self._append_host_evidence(
            payload=dict(request.event),
            idempotency_key=request.idempotency_key,
            source_ref=f"host-primary:{request.idempotency_key}",
        )
        return {
            "evidence_ref": committed.evidence_id,
            "primary_ref": committed.primary_conversation_id,
            "receipt_ref": committed.receipt_id,
            "evidence_hash": committed.envelope_sha256,
        }

    async def create_task_scope(
        self, request: CreateTaskScopeRequest
    ) -> Mapping[str, object]:
        scope_ref = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "simple-harness:host-scope:"
                f"{self._auth.subject}:{request.idempotency_key}:{request.fixture_key}",
            )
        )
        receipt = await self._scopes.create_task_scope(
            task_scope_id=scope_ref,
            subject=self._auth.subject,
            title=request.title,
            goal=request.goal,
        )
        return {
            "scope_ref": receipt.task_scope_id,
            "ref": receipt.task_scope_id,
            "revision": receipt.revision,
            "state_hash": receipt.state_hash,
            "goal": request.goal,
        }

    async def append_deterministic_events(
        self, request: AppendDeterministicEventsRequest
    ) -> Mapping[str, object]:
        await self._assert_owned_scope(request.scope_ref)
        if self._deterministic_event_seed is None:
            raise HumanMemoryHostServiceError(
                "human_memory_bulk_seed_authority_unavailable"
            )
        return await self._deterministic_event_seed.append_deterministic_events(
            subject=self._auth.subject,
            task_scope_id=request.scope_ref,
            count=request.count,
            canary=request.canary,
            idempotency_key=request.idempotency_key,
        )

    async def save_checkpoint(
        self, request: SaveCheckpointRequest
    ) -> Mapping[str, object]:
        await self._assert_owned_scope(request.scope_ref)
        checkpoint_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "simple-harness:host-checkpoint:"
                f"{self._auth.subject}:{request.scope_ref}:{request.idempotency_key}",
            )
        )
        receipt = await self._scopes.create_checkpoint(
            checkpoint_id=checkpoint_id,
            task_scope_id=request.scope_ref,
            metadata=dict(request.checkpoint),
        )
        return {
            "checkpoint_ref": receipt.checkpoint_id,
            "scope_ref": receipt.task_scope_id,
            "revision": receipt.revision,
            "checkpoint_hash": receipt.checkpoint_hash,
            "event_watermark": receipt.event_watermark,
        }

    async def mutate_task_scope(
        self, request: MutateTaskScopeRequest
    ) -> Mapping[str, object]:
        await self._assert_owned_scope(request.scope_ref)
        status_kind = {
            ("status", "active"): TaskScopeMutationKind.TASK_RESUME,
            ("status", "paused"): TaskScopeMutationKind.TASK_PAUSE,
            ("status", "blocked"): TaskScopeMutationKind.TASK_BLOCK,
            ("status", "complete"): TaskScopeMutationKind.TASK_COMPLETE,
        }.get((request.kind, request.value))
        try:
            kind = status_kind or TaskScopeMutationKind(request.kind)
        except ValueError as exc:
            raise HumanMemoryHostServiceError(
                "task_scope_mutation_kind_rejected"
            ) from exc
        plan_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"simple-harness:host-mutation-plan:{self._auth.subject}:{request.idempotency_key}",
            )
        )
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT current_revision FROM task_scope_heads WHERE task_scope_id=?",
                (request.scope_ref,),
            ).fetchone()
            replay = db.execute(
                "SELECT plan_json FROM task_scope_mutation_attempts WHERE plan_id=?",
                (plan_id,),
            ).fetchone()
        if row is None:
            raise HumanMemoryHostServiceError("human_memory_permission_denied")
        if replay is not None:
            plan = TaskScopeMutationPlan.from_json(json.loads(str(replay[0])))
            operations = tuple(plan.operations)
            if (
                plan.subject != self._auth.subject
                or plan.task_scope_id != request.scope_ref
                or plan.idempotency_key != request.idempotency_key
                or len(operations) != 1
                or operations[0].kind is not kind
                or operations[0].value != request.value
            ):
                raise HumanMemoryHostServiceError(
                    "task_scope_mutation_idempotency_conflict"
                )
            receipt = await self._scopes.apply_mutation_plan(plan)
            return self._mutation_result(receipt)
        run_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"simple-harness:host-mutation-run:{self._auth.subject}:{request.idempotency_key}",
            )
        )
        committed = await self._append_host_evidence(
            payload={
                "schema_version": 1,
                "scope_ref": request.scope_ref,
                "kind": request.kind,
                "value": request.value,
            },
            idempotency_key=f"mutation-evidence:{request.idempotency_key}",
            source_ref=f"host-mutation:{request.idempotency_key}",
            run_id=run_id,
        )
        evidence_ref = EvidenceRef(
            committed.evidence_id, committed.envelope_sha256, 1
        )
        disclosure = self._disclosure(run_id)
        plan = TaskScopeMutationPlan(
            plan_id=plan_id,
            run_id=run_id,
            subject=self._auth.subject,
            task_scope_id=request.scope_ref,
            base_revision=int(row[0]),
            outcome=TaskScopeMutationOutcome.MUTATE,
            operations=(
                TaskScopeMutationOperation(
                    operation_id=str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"simple-harness:host-mutation-operation:{request.idempotency_key}",
                        )
                    ),
                    kind=kind,
                    value=request.value,
                    evidence_refs=(evidence_ref,),
                    reason_code="host_typed_mutation",
                ),
            ),
            closure_reason=None,
            source_turn_id=f"host-request:{request.idempotency_key}",
            disclosure_context=disclosure,
            evidence_refs=(evidence_ref,),
            idempotency_key=request.idempotency_key,
        )
        receipt = await self._scopes.apply_mutation_plan(plan)
        return self._mutation_result(receipt)

    @staticmethod
    def _mutation_result(receipt) -> Mapping[str, object]:  # type: ignore[no-untyped-def]
        return {
            "scope_ref": receipt.task_scope_id,
            "receipt_ref": receipt.decision_id,
            "revision": receipt.committed_revision,
            "decision_ref": receipt.decision_id,
            "plan_hash": receipt.plan_hash,
            "state_hash": receipt.state_hash,
            "event_ref": receipt.event_id,
        }

    async def append_binding(
        self, request: AppendBindingRequest
    ) -> Mapping[str, object]:
        await self._assert_owned_scope(request.scope_ref)
        if self._binding_append is None:
            raise HumanMemoryHostServiceError(
                "human_memory_binding_authority_unavailable"
            )
        return await self._binding_append.append_binding(
            subject=self._auth.subject,
            task_scope_id=request.scope_ref,
            root=request.root,
            idempotency_key=request.idempotency_key,
        )

    async def control_current_run(
        self, request: ControlRunRequest
    ) -> Mapping[str, object]:
        current = await self._foreground.current_snapshot(self._auth.subject)
        if current is None:
            raise HumanMemoryHostServiceError("human_memory_foreground_run_not_found")
        receipt = await self._foreground.request_control(
            host_run_id=current.host_run_id,
            subject=self._auth.subject,
            generation=current.generation,
            control_kind=request.control,
            reason=request.reason,
            idempotency_key=request.idempotency_key,
        )
        return {
            "control_ref": receipt.control_id,
            "receipt_ref": receipt.control_id,
            "run_ref": receipt.host_run_id,
            "generation": receipt.generation,
            "outcome": receipt.outcome,
            "state": receipt.reduced_state.value,
            "receipt_hash": receipt.control_hash,
            "signal_ref": receipt.signal_id,
        }

    async def audit_refs(self, request: AuditRefsRequest) -> Mapping[str, object]:
        await self._assert_owned_scope(request.scope_ref)
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            source = db.execute(
                "SELECT source_id,source_hash FROM task_scope_projection_sources "
                "WHERE task_scope_id=? ORDER BY source_sequence DESC LIMIT 1",
                (request.scope_ref,),
            ).fetchone()
            checkpoint = db.execute(
                "SELECT checkpoint_id,checkpoint_hash FROM task_scope_checkpoints "
                "WHERE task_scope_id=? ORDER BY created_at DESC,checkpoint_id DESC LIMIT 1",
                (request.scope_ref,),
            ).fetchone()
            binding = db.execute(
                "SELECT current_receipt_id,current_receipt_hash FROM "
                "task_workspace_binding_heads WHERE task_scope_id=?",
                (request.scope_ref,),
            ).fetchone()
        refs = []
        if source is not None:
            refs.append({"kind": "projection_source", "ref": source[0], "hash": source[1]})
        if checkpoint is not None:
            refs.append({"kind": "checkpoint", "ref": checkpoint[0], "hash": checkpoint[1]})
        if binding is not None:
            refs.append({"kind": "binding", "ref": binding[0], "hash": binding[1]})
        receipt_hash = canonical_hash({"scope_ref": request.scope_ref, "refs": refs})
        return {
            "scope_ref": request.scope_ref,
            "audit_refs": refs,
            "receipt_ref": f"sha256:{receipt_hash}",
            "receipt_hash": receipt_hash,
        }

    async def recovery_manifest(self) -> Mapping[str, object]:
        if self._recovery is None:
            raise HumanMemoryHostServiceError(
                "human_memory_recovery_authority_unavailable"
            )
        return await self._recovery.manifest(subject=self._auth.subject)

    async def emergency_export(self) -> Mapping[str, object]:
        if self._recovery is None:
            raise HumanMemoryHostServiceError(
                "human_memory_recovery_authority_unavailable"
            )
        return await self._recovery.emergency_export(subject=self._auth.subject)

    async def enqueue_turn(self, request: QueueTurnRequest) -> Mapping[str, object]:
        await self._assert_owned_scope(request.scope_ref)
        primary = await self._program.initialize_subject(self._auth.subject)
        payload = {
            "schema_version": 1,
            "delivery_key": request.delivery_key,
            "text": request.text,
        }
        payload_hash = canonical_hash(payload)
        run_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"simple-harness:foreground-evidence-run:{self._auth.subject}",
            )
        )
        evidence_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "simple-harness:foreground-evidence:"
                f"{self._auth.subject}:{request.delivery_key}",
            )
        )
        disclosure = DisclosureContext(
            run_id=run_id,
            subject=self._auth.subject,
            recipient=DeliveryRecipient.USER_SELF,
            recipient_id=self._auth.subject,
            intended_audience=IntendedAudience.USER_SELF,
            purpose=DisclosurePurpose.TASK_EXECUTION,
            source=DisclosureSource.AUTHENTICATED_HOST,
            trust=DisclosureTrust.TRUSTED_AUTHORITY,
            generation=DisclosureGeneration.CURRENT,
            authority_ref=self._auth.authority_ref,
            reason_codes=(DisclosureReasonCode.MINIMUM_NECESSARY,),
        )
        envelope = SanitizedEvidenceEnvelope(
            evidence_id=evidence_id,
            run_id=run_id,
            subject=self._auth.subject,
            source_kind=EvidenceSourceKind.USER_MESSAGE,
            source_ref=f"foreground-turn:{request.delivery_key}",
            source_hash=payload_hash,
            sanitized_payload=payload,
            sanitized_hash=payload_hash,
            filter_policy_version="host-public-turn/v1",
            removed_spans=(),
            disclosure_context=disclosure,
            evidence_refs=(),
        )
        receipt = SanitizedEvidenceReceipt(
            receipt_id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"simple-harness:foreground-evidence-receipt:{evidence_id}",
                )
            ),
            run_id=run_id,
            subject=self._auth.subject,
            evidence_id=evidence_id,
            envelope_hash=envelope.envelope_hash,
            source_hash=payload_hash,
            sanitized_hash=payload_hash,
            filter_policy_version="host-public-turn/v1",
            accepted=True,
            reason_codes=(EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
            disclosure_context=disclosure,
            evidence_refs=(),
            admitted_at=0.0,
        )
        committed = await self._program.append_evidence(envelope, receipt)
        queued = await self._foreground.enqueue_turn(
            subject=self._auth.subject,
            primary_conversation_id=primary.primary_conversation_id,
            evidence_id=committed.evidence_id,
            evidence_hash=committed.envelope_sha256,
            idempotency_key=request.delivery_key,
            turn_payload=payload,
            task_scope_id=request.scope_ref,
        )
        if self._scheduler_wake is not None:
            await self._scheduler_wake.after_enqueue(subject=self._auth.subject)
        return {
            "turn_ref": queued.turn_id,
            "receipt_ref": queued.turn_id,
            "scope_ref": queued.task_scope_id,
            "enqueue_sequence": queued.enqueue_sequence,
            "content_sha256": queued.turn_hash,
            "delivery_key": request.delivery_key,
        }

    async def search_task_scopes(
        self, request: SearchTaskScopesRequest
    ) -> Mapping[str, object]:
        allowed = await self._owned_scope_ids()
        result = await self._search.search(
            subject=self._auth.subject,
            allowed_scope_ids=allowed,
            query=request.query,
            limit=request.max_candidates,
            cursor=request.cursor,
        )
        return {
            "candidates": [
                {
                    "scope_ref": item.task_scope_id,
                    "source_ref": item.source_id,
                    "source_hash": item.source_hash,
                    "title": item.title,
                    "goal": item.goal,
                    "project": item.project,
                    "status": item.status,
                    "snippet": item.snippet,
                    "rank": item.rank,
                }
                for item in result.candidates
            ],
            "next_cursor": result.next_cursor,
            "receipt_hash": result.receipt_hash,
        }

    async def open_task_scope(
        self, request: OpenTaskScopeRequest
    ) -> Mapping[str, object]:
        allowed = await self._owned_scope_ids()
        result = await self._search.open_exact(
            subject=self._auth.subject,
            allowed_scope_ids=allowed,
            task_scope_id=request.scope_ref,
            expected_source_hash=request.expected_source_hash,
            live_probe=request.live_probe,
        )
        drift = result.drift_report
        return {
            "scope_ref": result.task_scope_id,
            "receipt_ref": f"sha256:{result.receipt_hash}",
            "source_ref": result.source_id,
            "source_hash": result.source_hash,
            "resume_package": result.resume_package,
            "resume_sha256": result.resume_package_hash,
            "receipt_hash": result.receipt_hash,
            "drift_report": None
            if drift is None
            else {
                "drifted": drift.drifted,
                "changed_fields": list(drift.changed_fields),
                "checkpoint_ref": drift.checkpoint_id,
                "checkpoint_hash": drift.checkpoint_hash,
                "report_hash": drift.report_hash,
            },
        }

    async def read_view(
        self, request: ReadTaskScopeViewRequest
    ) -> Mapping[str, object]:
        await self._assert_owned_scope(request.scope_ref)
        view = await self._projections.read_view(
            request.kind, task_scope_id=request.scope_ref
        )
        pages: list[dict[str, object]] = []
        if view.view_kind == "EVIDENCE":
            groups = await self._projections.list_evidence_groups(
                task_scope_id=request.scope_ref
            )
            for group in groups:
                events = await self._projections.read_evidence_group(
                    str(group["block_id"])
                )
                pending: list[dict[str, object]] = []
                for raw_event in events:
                    event = dict(raw_event)
                    payload = event.get("payload")
                    if isinstance(payload, Mapping):
                        event_index = payload.get("event_index")
                        if isinstance(event_index, int) and not isinstance(
                            event_index, bool
                        ):
                            event["event_index"] = event_index
                    candidate = canonical_json(
                        {"schema_version": 1, "events": [*pending, event]}
                    )
                    if pending and len(candidate.encode("utf-8")) > 32 * 1024:
                        pages.append(self._event_page(pending))
                        pending = []
                    single = canonical_json({"schema_version": 1, "events": [event]})
                    if len(single.encode("utf-8")) <= 32 * 1024:
                        pending.append(event)
                        continue
                    event_bytes = canonical_json(event).encode("utf-8")
                    event_hash = hashlib.sha256(event_bytes).hexdigest()
                    chunks = [
                        event_bytes[offset : offset + 20 * 1024]
                        for offset in range(0, len(event_bytes), 20 * 1024)
                    ]
                    for ordinal, chunk in enumerate(chunks, 1):
                        chunk_payload = {
                            "schema_version": 1,
                            "event_chunk": {
                                "event_sequence": event.get("event_sequence"),
                                "ordinal": ordinal,
                                "count": len(chunks),
                                "event_content_sha256": event_hash,
                                "encoding": "base64",
                                "content": base64.b64encode(chunk).decode("ascii"),
                            },
                        }
                        content = canonical_json(chunk_payload)
                        if len(content.encode("utf-8")) > 32 * 1024:
                            raise HumanMemoryHostServiceError(
                                "task_scope_projection_public_page_too_large"
                            )
                        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                        pages.append(
                            {
                                "page_id": f"sha256:{content_hash}",
                                "content": content,
                                "content_sha256": content_hash,
                                "event_chunk": chunk_payload["event_chunk"],
                            }
                        )
                if pending:
                    pages.append(self._event_page(pending))
        return {
            "scope_ref": request.scope_ref,
            "source_ref": view.source_id,
            "kind": view.view_kind,
            "content": view.content,
            "content_sha256": view.content_sha256,
            "root_block_id": view.root_block_id,
            "block_count": view.block_count,
            "receipt_hash": view.receipt_hash,
            "pages": pages,
        }

    @staticmethod
    def _event_page(events: list[dict[str, object]]) -> dict[str, object]:
        content = canonical_json({"schema_version": 1, "events": events})
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > 32 * 1024:
            raise HumanMemoryHostServiceError(
                "task_scope_projection_public_page_too_large"
            )
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        return {
            "page_id": f"sha256:{content_hash}",
            "content": content,
            "content_sha256": content_hash,
            "events": events,
        }

    async def drop_rebuildable(self, scope_ref: str) -> Mapping[str, object]:
        await self._assert_owned_scope(scope_ref)
        with sqlite3.connect(self._db_path) as db:
            db.execute(
                "DELETE FROM task_scope_search_fts WHERE task_scope_id=?",
                (scope_ref,),
            )
            db.execute(
                "DELETE FROM task_scope_projection_cache WHERE task_scope_id=?",
                (scope_ref,),
            )
            db.commit()
        return {"scope_ref": scope_ref, "dropped": ["fts", "legacy_projection_cache"]}

    async def rebuild_derived(self, scope_ref: str) -> Mapping[str, object]:
        await self._assert_owned_scope(scope_ref)
        views = await self._projections.materialize(task_scope_id=scope_ref)
        document_hash = await self._search.rebuild_scope(scope_ref)
        return {
            "scope_ref": scope_ref,
            "view_hashes": {kind: view.content_sha256 for kind, view in views.items()},
            "search_document_hash": document_hash,
        }

    async def authority_snapshot(self) -> Mapping[str, object]:
        primary = await self._program.initialize_subject(self._auth.subject)
        owned = await self._owned_scope_ids()
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            if owned:
                placeholders = ",".join("?" for _ in owned)
                rows = db.execute(
                    "SELECT task_scope_id,current_revision,current_receipt_id,"
                    "current_receipt_hash FROM task_workspace_binding_heads "
                    f"WHERE task_scope_id IN ({placeholders}) ORDER BY task_scope_id",
                    owned,
                ).fetchall()
            else:
                rows = []
        current = await self._foreground.current_snapshot(self._auth.subject)
        return {
            "primary_ref": primary.primary_conversation_id,
            "active_cursor": None,
            "binding_heads": [dict(row) for row in rows],
            "tool_authority_refs": [],
            "foreground_run_ref": None if current is None else current.host_run_id,
        }

    async def queue_snapshot(self) -> Mapping[str, object]:
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT t.turn_id,t.enqueue_sequence,t.turn_json,h.current_state "
                "FROM foreground_turns t JOIN foreground_turn_heads h "
                "ON h.turn_id=t.turn_id WHERE t.subject=? "
                "ORDER BY t.enqueue_sequence,t.turn_id",
                (self._auth.subject,),
            ).fetchall()
        turns = []
        for row in rows:
            stored = json.loads(str(row["turn_json"]))
            payload = stored.get("payload", {})
            turns.append(
                {
                    "turn_ref": str(row["turn_id"]),
                    "enqueue_sequence": int(row["enqueue_sequence"]),
                    "state": str(row["current_state"]),
                    "delivery_key": payload.get("delivery_key"),
                }
            )
        return {"turns": turns}

    async def raw_integrity_manifest(self) -> Mapping[str, object]:
        """Return a bounded read-only raw-set digest, not a v42 sealed receipt."""

        owned_scopes = await self._owned_scope_ids()
        raw_sets: dict[str, Mapping[str, object]] = {}
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            available = {
                str(row[0])
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for spec in _RAW_SET_ALLOWLIST:
                if spec.table not in available:
                    continue
                columns = [
                    str(row[1])
                    for row in db.execute(f'PRAGMA table_info("{spec.table}")')
                ]
                if "subject" in columns:
                    rows = db.execute(
                        f'SELECT * FROM "{spec.table}" WHERE subject=?',
                        (self._auth.subject,),
                    ).fetchall()
                elif "task_scope_id" in columns:
                    if not owned_scopes:
                        rows = []
                    else:
                        placeholders = ",".join("?" for _ in owned_scopes)
                        rows = db.execute(
                            f'SELECT * FROM "{spec.table}" '
                            f"WHERE task_scope_id IN ({placeholders})",
                            tuple(owned_scopes),
                        ).fetchall()
                else:
                    rows = db.execute(f'SELECT * FROM "{spec.table}"').fetchall()
                canonical_rows = sorted(
                    _canonical(
                        {
                            key: _json_cell(row[key])
                            for key in row.keys()
                        }
                    )
                    for row in rows
                )
                digest = hashlib.sha256()
                for row_bytes in canonical_rows:
                    digest.update(len(row_bytes).to_bytes(8, "big"))
                    digest.update(row_bytes)
                raw_sets[spec.logical_name] = {
                    "row_count": len(canonical_rows),
                    "content_sha256": digest.hexdigest(),
                }
        manifest_hash = hashlib.sha256(_canonical(raw_sets)).hexdigest()
        return {
            "schema_version": 1,
            "format_epoch": self._startup.composition_mode.value,
            "raw_sets": raw_sets,
            "content_sha256": manifest_hash,
            "sealed": False,
        }

    async def _owned_scope_ids(self) -> tuple[str, ...]:
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            rows = db.execute(
                "SELECT task_scope_id FROM task_scopes WHERE subject=? "
                "ORDER BY task_scope_id",
                (self._auth.subject,),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    async def _assert_owned_scope(self, scope_ref: str) -> None:
        identifier(scope_ref, "scope_ref", 512)
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT subject FROM task_scopes WHERE task_scope_id=?",
                (scope_ref,),
            ).fetchone()
        if row is None or str(row[0]) != self._auth.subject:
            raise HumanMemoryHostServiceError("human_memory_permission_denied")

    def _disclosure(self, run_id: str) -> DisclosureContext:
        return DisclosureContext(
            run_id=run_id,
            subject=self._auth.subject,
            recipient=DeliveryRecipient.USER_SELF,
            recipient_id=self._auth.subject,
            intended_audience=IntendedAudience.USER_SELF,
            purpose=DisclosurePurpose.TASK_EXECUTION,
            source=DisclosureSource.AUTHENTICATED_HOST,
            trust=DisclosureTrust.TRUSTED_AUTHORITY,
            generation=DisclosureGeneration.CURRENT,
            authority_ref=self._auth.authority_ref,
            reason_codes=(DisclosureReasonCode.MINIMUM_NECESSARY,),
        )

    async def _append_host_evidence(
        self,
        *,
        payload: Mapping[str, object],
        idempotency_key: str,
        source_ref: str,
        run_id: str | None = None,
    ):
        reject_private_payload(dict(payload), "host_evidence")
        run_ref = run_id or str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"simple-harness:host-evidence-run:{self._auth.subject}:{idempotency_key}",
            )
        )
        evidence_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"simple-harness:host-evidence:{self._auth.subject}:{idempotency_key}",
            )
        )
        payload_dict = dict(payload)
        payload_hash = canonical_hash(payload_dict)
        disclosure = self._disclosure(run_ref)
        envelope = SanitizedEvidenceEnvelope(
            evidence_id=evidence_id,
            run_id=run_ref,
            subject=self._auth.subject,
            source_kind=EvidenceSourceKind.USER_MESSAGE,
            source_ref=source_ref,
            source_hash=payload_hash,
            sanitized_payload=payload_dict,
            sanitized_hash=payload_hash,
            filter_policy_version="host-typed-ingress/v1",
            removed_spans=(),
            disclosure_context=disclosure,
            evidence_refs=(),
        )
        receipt = SanitizedEvidenceReceipt(
            receipt_id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"simple-harness:host-evidence-receipt:{evidence_id}",
                )
            ),
            run_id=run_ref,
            subject=self._auth.subject,
            evidence_id=evidence_id,
            envelope_hash=envelope.envelope_hash,
            source_hash=payload_hash,
            sanitized_hash=payload_hash,
            filter_policy_version="host-typed-ingress/v1",
            accepted=True,
            reason_codes=(EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
            disclosure_context=disclosure,
            evidence_refs=(),
            admitted_at=0.0,
        )
        return await self._program.append_evidence(envelope, receipt)


@dataclass(frozen=True, slots=True)
class HumanMemoryHostServiceFactory:
    db_path: Path
    startup: StartupEpochDecision

    def bind(
        self,
        auth: AuthenticatedHostSnapshot,
        *,
        deterministic_event_seed: DeterministicEventSeedPort | None = None,
        binding_append: WorkspaceBindingAppendPort | None = None,
        recovery: RecoveryLifecyclePort | None = None,
        scheduler_wake: ForegroundSchedulerWakePort | None = None,
    ) -> HumanMemoryHostService:
        return HumanMemoryHostService(
            self.db_path,
            auth=auth,
            startup=self.startup,
            deterministic_event_seed=deterministic_event_seed,
            binding_append=binding_append,
            recovery=recovery,
            scheduler_wake=scheduler_wake,
        )


__all__ = (
    "AppendDeterministicEventsRequest",
    "AppendBindingRequest",
    "AppendPrimaryEventRequest",
    "AuditRefsRequest",
    "AuthenticatedHostSnapshot",
    "ControlRunRequest",
    "CreateTaskScopeRequest",
    "DeterministicEventSeedPort",
    "HumanMemoryHostService",
    "HumanMemoryHostServiceError",
    "HumanMemoryHostServiceFactory",
    "ForegroundSchedulerWakePort",
    "MutateTaskScopeRequest",
    "OpenTaskScopeRequest",
    "QueueTurnRequest",
    "ReadTaskScopeViewRequest",
    "SaveCheckpointRequest",
    "SearchTaskScopesRequest",
    "RecoveryLifecyclePort",
    "WorkspaceBindingAppendPort",
)
