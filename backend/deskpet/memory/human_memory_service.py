# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Typed Host facade for the fresh human-memory composition.

Identity and authority are constructor-bound.  Public request DTOs deliberately
contain no subject, allowed-scope set, composition mode, database path, binding
revision, or worker authority fields.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
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
    EvidenceRef,
    EvidenceSourceKind,
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
from deskpet.memory.writer_fence import assert_human_memory_ingress_open
from deskpet.task_scope.projections import TaskScopeProjectionStore
from deskpet.task_scope.protocol import (
    canonical_hash,
    canonical_json,
    digest,
    identifier,
    reject_private_payload,
)
from deskpet.task_scope.search import TaskScopeSearchStore
from deskpet.task_scope.store import CanonicalTaskScopeStore

logger = logging.getLogger(__name__)
SCHEDULER_WAKE_TIMEOUT_SECONDS = 0.5


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
class ListTaskScopesRequest:
    """Bounded active/recent listing; no query, no authority, no archive content."""

    limit: int = 20
    cursor: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or not isinstance(self.limit, int):
            raise TypeError("limit must be an integer")
        if not 1 <= self.limit <= 32:
            raise ValueError("limit must be between 1 and 32")
        if self.cursor is not None:
            identifier(self.cursor, "cursor", 4096)


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
    expected_filesystem_identity_hash: str | None = None

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)
        identifier(self.root, "root", 4096)
        identifier(self.idempotency_key, "idempotency_key", 512)

        if self.expected_filesystem_identity_hash is not None:
            value = self.expected_filesystem_identity_hash
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("expected_filesystem_identity_hash must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class DecideManualBindingRequest:
    challenge_ref: str
    decision: str
    idempotency_key: str

    def __post_init__(self) -> None:
        identifier(self.challenge_ref, "challenge_ref", 512)
        if self.decision not in {"allow", "deny"}:
            raise ValueError("decision must be allow or deny")
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
class ExactControlRunRequest:
    expected_run_ref: str
    expected_generation: int
    control: str
    reason: str
    idempotency_key: str

    def __post_init__(self) -> None:
        from deskpet.memory.primary_read_model import PrimaryReadError

        try:
            identifier(self.expected_run_ref, "expected_run_ref", 512)
            if (type(self.expected_generation) is not int
                    or not 1 <= self.expected_generation <= 2**63 - 1):
                raise ValueError("invalid generation")
            if self.control not in {"pause", "stop", "cancel"}:
                raise ValueError("invalid control")
            identifier(self.reason, "reason", 2048)
            identifier(self.idempotency_key, "idempotency_key", 512)
        except (TypeError, ValueError) as exc:
            raise PrimaryReadError("primary_exact_control_invalid") from exc


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
    scope_ref: str | None
    delivery_key: str
    text: str
    disclosure_binding_ref: str | None = None
    input_declaration: dict | None = None

    def __post_init__(self) -> None:
        if self.scope_ref is not None:
            identifier(self.scope_ref, "scope_ref", 512)
        identifier(self.delivery_key, "delivery_key", 512)
        identifier(self.text, "text", 16_384)
        if self.disclosure_binding_ref is not None:
            identifier(self.disclosure_binding_ref, "disclosure_binding_ref", 512)


@dataclass(frozen=True, slots=True)
class ReadTaskScopeViewRequest:
    scope_ref: str
    kind: str

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)
        identifier(self.kind, "kind", 64)


@dataclass(frozen=True, slots=True)
class ListEvidenceGroupsRequest:
    scope_ref: str
    source_ref: str
    source_hash: str
    cursor: str | None = None
    limit: int = 8

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)
        identifier(self.source_ref, "source_ref", 512)
        digest(self.source_hash, "source_hash")
        if self.cursor is not None:
            identifier(self.cursor, "cursor", 4096)
        if isinstance(self.limit, bool) or not isinstance(self.limit, int):
            raise TypeError("limit must be an integer")
        if not 1 <= self.limit <= 16:
            raise ValueError("limit must be between 1 and 16")


@dataclass(frozen=True, slots=True)
class ReadEvidencePageRequest:
    scope_ref: str
    source_ref: str
    source_hash: str
    group_ref: str
    group_hash: str
    cursor: str | None = None

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)
        identifier(self.source_ref, "source_ref", 512)
        digest(self.source_hash, "source_hash")
        identifier(self.group_ref, "group_ref", 512)
        digest(self.group_hash, "group_hash")
        if self.cursor is not None:
            identifier(self.cursor, "cursor", 4096)


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
        interaction_evidence_id: str,
        interaction_evidence_hash: str,
        expected_filesystem_identity_hash: str | None = None,
    ) -> Mapping[str, object]: ...

    async def propose_manual_binding(
        self,
        *,
        subject: str,
        task_scope_id: str,
        root: str,
        idempotency_key: str,
        interaction_evidence_id: str,
        interaction_evidence_hash: str,
        expected_filesystem_identity_hash: str | None = None,
    ) -> Mapping[str, object]: ...

    async def decide_manual_binding(
        self,
        *,
        subject: str,
        challenge_ref: str,
        decision: str,
        idempotency_key: str,
        interaction_evidence_id: str,
        interaction_evidence_hash: str,
    ) -> Mapping[str, object]: ...


class RecoveryLifecyclePort(Protocol):
    async def manifest(self, *, subject: str) -> Mapping[str, object]: ...

    async def emergency_export(self, *, subject: str) -> Mapping[str, object]: ...


class ForegroundSchedulerWakePort(Protocol):
    async def after_enqueue(self, *, subject: str) -> None: ...

    async def after_control(self, *, subject: str) -> None: ...


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
        settled_run_reader: object | None = None,
        suppression_resolver: object | None = None,
        run_binding_reader: object | None = None,
        history_visibility_checker: object | None = None,
        decision_ingress_getter: object | None = None,
        cognitive_runtime_getter: object | None = None,
        display_invalidation: object | None = None,
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
        self._cognitive_runtime_getter = cognitive_runtime_getter
        self._display_invalidation = display_invalidation
        from deskpet.memory.primary_read_model import PrimaryReadModel
        from deskpet.memory.prospective_notice import ProspectiveNoticeReader

        self._primary_read = PrimaryReadModel(
            self._db_path,
            subject=auth.subject,
            settled_run_reader=settled_run_reader,
            suppression_resolver=suppression_resolver,
            run_binding_reader=run_binding_reader,
            history_visibility_checker=history_visibility_checker,
            prospective_notice_reader=ProspectiveNoticeReader(
                path=self._db_path, subject=auth.subject,
                runtime_getter=cognitive_runtime_getter, terminal_reader=settled_run_reader),
        )
        from deskpet.memory.primary_decisions import PrimaryDecisions
        self._primary_decisions = PrimaryDecisions(
            self._db_path, subject=auth.subject, read_model=self._primary_read,
            ingress_getter=decision_ingress_getter,
        )
        self._evidence_group_ref_cache: dict[
            tuple[str, str, str], tuple[dict[str, object], ...]
        ] = {}

    @property
    def startup_decision(self) -> StartupEpochDecision:
        return self._startup

    async def open_primary(self) -> Mapping[str, object]:
        receipt = await self._program.initialize_subject(self._auth.subject)
        return {
            "primary_ref": receipt.primary_conversation_id,
            "receipt_ref": receipt.receipt_id,
            "receipt_hash": receipt.receipt_sha256,
        }

    def _history_disclosure(self, request_id: str) -> DisclosureContext:
        identifier(request_id, "request_id", 512)
        return DisclosureContext(
            run_id=request_id, subject=self._auth.subject,
            recipient=DeliveryRecipient.USER_SELF, recipient_id=self._auth.subject,
            intended_audience=IntendedAudience.USER_SELF, purpose=DisclosurePurpose.USER_REVIEW,
            source=DisclosureSource.AUTHENTICATED_HOST, trust=DisclosureTrust.TRUSTED_AUTHORITY,
            generation=DisclosureGeneration.CURRENT, authority_ref=self._auth.authority_ref,
            reason_codes=(DisclosureReasonCode.MINIMUM_NECESSARY,),
        )

    async def read_primary_state(self, *, request_id: str) -> Mapping[str, object]:
        return await self._primary_read.state(disclosure_context=self._history_disclosure(request_id))

    async def read_primary_messages(self, *, request_id: str, **request) -> Mapping[str, object]:
        return await self._primary_read.page(disclosure_context=self._history_disclosure(request_id), **request)

    async def read_primary_message_detail(self, *, request_id: str, **request) -> Mapping[str, object]:
        return await self._primary_read.detail(disclosure_context=self._history_disclosure(request_id), **request)

    async def list_primary_decisions(self, *, request_id: str, **request):
        return await self._primary_decisions.list(
            disclosure_context=self._history_disclosure(request_id), **request
        )

    def _primary_workspace_bindings(self):
        from deskpet.memory.primary_workspace_bindings import PrimaryWorkspaceBindings
        return PrimaryWorkspaceBindings(self._db_path, subject=self._auth.subject,
            authority=self._binding_append, decide=self.decide_manual_binding)

    async def list_primary_bindings(self, **request):
        return await self._primary_workspace_bindings().pending(**request)

    async def read_primary_binding(self, **request):
        return await self._primary_workspace_bindings().status(**request)

    async def respond_primary_binding(self, **request):
        return await self._primary_workspace_bindings().respond(**request)

    async def respond_primary_decision(self, *, request_id: str, **request):
        result = await self._primary_decisions.respond(
            disclosure_context=self._history_disclosure(request_id), **request
        )
        if self._scheduler_wake is not None:
            await self._wake_committed("after_control", result["decision_id"])
        return result

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

    def _cognitive_controls(self):
        from deskpet.memory.primary_cognitive_controls import PrimaryCognitiveControls

        return PrimaryCognitiveControls(
            self._db_path, auth=self._auth,
            runtime_getter=self._cognitive_runtime_getter,
            display_invalidation=self._display_invalidation,
        )

    async def list_primary_memories(self, **request):
        return await self._cognitive_controls().list(**request)

    async def read_primary_memory_graph(self, **request):
        return await self._cognitive_controls().graph(**request)

    async def forget_primary_memory(self, **request):
        return await self._cognitive_controls().forget(**request)

    def _human_audit_runtime(self):
        from deskpet.memory.writer_fence import require_human_audit_request
        from deskpet.sdk_adapters.context_route import local_owner_auth

        require_human_audit_request()
        if self._auth != local_owner_auth():
            raise HumanMemoryHostServiceError("primary_audit_subject_mismatch")
        runtime = self._cognitive_runtime_getter() if self._cognitive_runtime_getter else None
        access = getattr(runtime, "audit_access_authority", None)
        if access is None:
            raise HumanMemoryHostServiceError("primary_audit_capability_unavailable")
        return runtime, access

    async def primary_audit(self, operation, **request):
        from deskpet.memory.writer_fence import require_human_audit_request

        runtime, access = self._human_audit_runtime()
        if operation == "primary.audit.close":
            return await access.close(auth=self._auth, **request)
        # Lazy SDK initialization can be slow; it must not retain a stale lease.
        lease = require_human_audit_request()
        manager = await runtime.manager()
        if require_human_audit_request() != lease:
            raise HumanMemoryHostServiceError("primary_audit_connection_changed")
        method = access.open if operation == "primary.audit.open" else access.page
        return await method(manager=manager, principal=runtime.principal(), auth=self._auth, **request)

    def check_primary_audit_response(self, operation, payload):
        _, access = self._human_audit_runtime()
        access.final_check(auth=self._auth, primary_ref=payload["primary_ref"],
                           audit_ref=payload["audit_ref"], allow_closed=operation == "primary.audit.close")

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
        # S5b Task 3: a checkpoint over a scope with an open pending closure
        # first force-closes it (no_mutation(closure_abandoned, host_forced)).
        from deskpet.execution.semantic_closure import force_close_pending

        await force_close_pending(
            self._db_path, task_scope_id=request.scope_ref, subject=self._auth.subject
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
        if kind in {TaskScopeMutationKind.TASK_COMPLETE, TaskScopeMutationKind.RESUME_UPDATE}:
            # S5b Task 3 (design-freeze §7 / A3): a scope completed or resumed
            # by the Host while a closure is still pending is force-closed with
            # no_mutation(closure_abandoned, host_forced) — zero Provider calls.
            from deskpet.execution.semantic_closure import force_close_pending

            await force_close_pending(
                self._db_path, task_scope_id=request.scope_ref, subject=self._auth.subject
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
        expected = ({} if request.expected_filesystem_identity_hash is None else
                    {"expected_filesystem_identity_hash": request.expected_filesystem_identity_hash})
        committed = await self._append_host_evidence(
            payload={
                **expected,
                "schema_version": 1,
                "action": "binding.append",
                "scope_ref": request.scope_ref,
                "root": request.root,
                "idempotency_key": request.idempotency_key,
            },
            idempotency_key=f"binding-proposal:{request.idempotency_key}",
            source_ref=f"host-binding-append:{request.idempotency_key}",
        )
        return await self._binding_append.append_binding(
            subject=self._auth.subject,
            task_scope_id=request.scope_ref,
            root=request.root,
            idempotency_key=request.idempotency_key,
            interaction_evidence_id=committed.evidence_id,
            interaction_evidence_hash=committed.envelope_sha256,
            **expected,
        )

    async def propose_manual_binding(
        self, request: AppendBindingRequest
    ) -> Mapping[str, object]:
        await self._assert_owned_scope(request.scope_ref)
        if self._binding_append is None:
            raise HumanMemoryHostServiceError(
                "human_memory_binding_authority_unavailable"
            )
        expected = ({} if request.expected_filesystem_identity_hash is None else
                    {"expected_filesystem_identity_hash": request.expected_filesystem_identity_hash})
        committed = await self._append_host_evidence(
            payload={
                **expected,
                "schema_version": 1,
                "action": "binding.manual.propose",
                "scope_ref": request.scope_ref,
                "root": request.root,
                "idempotency_key": request.idempotency_key,
            },
            idempotency_key=f"binding-proposal:{request.idempotency_key}",
            source_ref=f"host-binding-manual-proposal:{request.idempotency_key}",
        )
        return await self._binding_append.propose_manual_binding(
            subject=self._auth.subject,
            task_scope_id=request.scope_ref,
            root=request.root,
            idempotency_key=request.idempotency_key,
            interaction_evidence_id=committed.evidence_id,
            interaction_evidence_hash=committed.envelope_sha256,
            **expected,
        )

    async def decide_manual_binding(
        self, request: DecideManualBindingRequest
    ) -> Mapping[str, object]:
        if self._binding_append is None:
            raise HumanMemoryHostServiceError(
                "human_memory_binding_authority_unavailable"
            )
        committed = await self._append_host_evidence(
            payload={
                "schema_version": 1,
                "action": "binding.manual.decide",
                "challenge_ref": request.challenge_ref,
                "decision": request.decision,
            },
            idempotency_key=f"binding-decision:{request.idempotency_key}",
            source_ref=f"host-binding-manual-decision:{request.challenge_ref}",
        )
        return await self._binding_append.decide_manual_binding(
            subject=self._auth.subject,
            challenge_ref=request.challenge_ref,
            decision=request.decision,
            idempotency_key=request.idempotency_key,
            interaction_evidence_id=committed.evidence_id,
            interaction_evidence_hash=committed.envelope_sha256,
        )

    async def control_current_run(
        self, request: ControlRunRequest | ExactControlRunRequest
    ) -> Mapping[str, object]:
        if isinstance(request, ExactControlRunRequest):
            run_ref = request.expected_run_ref
            generation = request.expected_generation
        else:
            current = await self._foreground.current_snapshot(self._auth.subject)
            if current is None:
                raise HumanMemoryHostServiceError(
                    "human_memory_foreground_run_not_found"
                )
            run_ref, generation = current.host_run_id, current.generation
        receipt = await self._foreground.request_control(
            host_run_id=run_ref,
            subject=self._auth.subject,
            generation=generation,
            control_kind=request.control,
            reason=request.reason,
            idempotency_key=request.idempotency_key,
        )
        # The durable control intent, reduced state, and signal outbox are
        # committed above; wake the active Runtime so pause/stop/cancel are
        # delivered immediately instead of waiting for the next poll.
        if self._scheduler_wake is not None and receipt.outcome == "signalled":
            await self._wake_committed("after_control", receipt.control_id)
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

    async def _wake_committed(self, operation: str, receipt_ref: str) -> None:
        # Called only after the store returned a committed receipt. Durable
        # queue/signal rows remain the recovery source if this hint fails.
        try:
            async with asyncio.timeout(SCHEDULER_WAKE_TIMEOUT_SECONDS):
                await getattr(self._scheduler_wake, operation)(
                    subject=self._auth.subject
                )
        except Exception:
            logger.warning(
                "human_memory_scheduler_wake_deferred",
                extra={
                    "operation": operation,
                    "receipt_ref": receipt_ref,
                    "recovery_state": "durable_work_pending",
                },
            )

    async def enqueue_turn(self, request: QueueTurnRequest) -> Mapping[str, object]:
        # Recovery fencing is a global Host lifecycle boundary.  Check it
        # before scope authorization so callers cannot observe a lower-level
        # authorization result after ingress has closed; the actual writer
        # transaction performs the same check again to close the race.
        await assert_human_memory_ingress_open(self._db_path)
        if request.scope_ref is not None:
            await self._assert_owned_scope(request.scope_ref)
        primary = await self._program.initialize_subject(self._auth.subject)
        envelope, receipt = build_foreground_turn_evidence(
            subject=self._auth.subject,
            authority_ref=self._auth.authority_ref,
            delivery_key=request.delivery_key,
            text=request.text,
        )
        payload = dict(envelope.sanitized_payload)
        queued = await self._foreground.enqueue_turn(
            subject=self._auth.subject,
            primary_conversation_id=primary.primary_conversation_id,
            evidence_id=envelope.evidence_id,
            evidence_hash=envelope.envelope_hash,
            idempotency_key=request.delivery_key,
            turn_payload=payload,
            task_scope_id=request.scope_ref,
            admitted_evidence_pair=(envelope, receipt),
            disclosure_binding_ref=request.disclosure_binding_ref,
            input_declaration=request.input_declaration,
            input_auth=self._auth,
        )
        if self._scheduler_wake is not None:
            await self._wake_committed("after_enqueue", queued.turn_id)
        return {
            "turn_ref": queued.turn_id,
            "receipt_ref": queued.turn_id,
            "scope_ref": queued.task_scope_id,
            "enqueue_sequence": queued.enqueue_sequence,
            "content_sha256": queued.turn_hash,
            "delivery_key": request.delivery_key,
        }

    async def configure_disclosure(self, *, request_id, expected_ref, selection):
        from deskpet.memory.trusted_disclosure import TrustedDisclosureStore
        return await TrustedDisclosureStore(self._db_path).configure(
            auth=self._auth, request_id=request_id, expected_ref=expected_ref, selection=selection)

    async def current_disclosure_configuration(self):
        from deskpet.memory.trusted_disclosure import TrustedDisclosureStore
        return await TrustedDisclosureStore(self._db_path).current(auth=self._auth)

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
        # Checkpoint drift compares checkpoint metadata against a probe the
        # trusted Run path observes; the Host cannot invent those fields from a
        # workspace binding, so the public channel reports the probe origin
        # explicitly instead of a fabricated report. Binding freshness is the
        # Host's own re-stat in binding_summary (read-only, not authority).
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
            "drift_probe": "host_unavailable" if request.live_probe is None else "trusted_run",
            "binding_summary": self._binding_summary(result.task_scope_id),
        }

    async def list_task_scopes(
        self, request: ListTaskScopesRequest
    ) -> Mapping[str, object]:
        """Active/recent owned scopes as search-shaped candidates: no archive
        content, no authority; keyset order (head updated_at desc, scope id asc)."""
        subject = self._auth.subject
        after_updated: float | None = None
        after_scope: str | None = None
        if request.cursor is not None:
            cursor = self._decode_cursor(request.cursor, "task_scope_list")
            if cursor.get("subject") != subject:
                raise HumanMemoryHostServiceError("human_memory_evidence_cursor_invalid")
            updated = cursor.get("updated_at")
            scope = cursor.get("scope_ref")
            if (isinstance(updated, bool) or not isinstance(updated, (int, float))
                    or not isinstance(scope, str) or not scope):
                raise HumanMemoryHostServiceError("human_memory_evidence_cursor_invalid")
            after_updated, after_scope = float(updated), scope
        sql = (
            "SELECT s.task_scope_id,s.title,h.updated_at,h.current_revision,h.event_watermark,"
            "r.state_json,p.source_id,p.source_hash,COALESCE(d.project,'') AS project "
            "FROM task_scopes s "
            "JOIN task_scope_heads h ON h.task_scope_id=s.task_scope_id "
            "JOIN task_scope_canonical_revisions r ON r.task_scope_id=s.task_scope_id "
            "AND r.revision=h.current_revision "
            "JOIN task_scope_projection_source_heads p ON p.task_scope_id=s.task_scope_id "
            "LEFT JOIN task_scope_search_heads sh ON sh.task_scope_id=s.task_scope_id "
            "LEFT JOIN task_scope_search_documents d ON d.document_id=sh.document_id "
            "WHERE s.subject=? "
        )
        params: list[object] = [subject]
        if after_updated is not None:
            sql += "AND (h.updated_at<? OR (h.updated_at=? AND s.task_scope_id>?)) "
            params += [after_updated, after_updated, after_scope]
        sql += "ORDER BY h.updated_at DESC,s.task_scope_id ASC LIMIT ?"
        params.append(request.limit + 1)
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(sql, params).fetchall()
        has_more = len(rows) > request.limit
        rows = rows[: request.limit]
        items = []
        for row in rows:
            state = json.loads(str(row["state_json"]))
            goal = "" if state.get("goal") is None else str(state["goal"])
            items.append({
                "scope_ref": str(row["task_scope_id"]),
                "source_ref": str(row["source_id"]),
                "source_hash": str(row["source_hash"]),
                "title": str(row["title"])[:2048],
                "goal": goal[:4096],
                "project": str(row["project"])[:2048],
                "status": str(state.get("status", "unknown"))[:256],
                "snippet": "",
                "rank": 0.0,
                "updated_at": float(row["updated_at"]),
                "canonical_revision": int(row["current_revision"]),
                "event_watermark": int(row["event_watermark"]),
                "binding_summary": self._binding_summary(str(row["task_scope_id"])),
            })
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = self._encode_cursor({
                "kind": "task_scope_list",
                "subject": subject,
                "updated_at": last["updated_at"],
                "scope_ref": last["scope_ref"],
            })
        result = {
            "items": items,
            "next_cursor": next_cursor,
            "receipt_hash": canonical_hash({
                "schema_version": 1,
                "operation": "list",
                "subject": subject,
                "items": [
                    {"scope_ref": i["scope_ref"], "source_ref": i["source_ref"], "source_hash": i["source_hash"]}
                    for i in items
                ],
                "next_cursor": next_cursor,
            }),
        }
        self._assert_public_bound(result)
        return result

    def _binding_summary(self, task_scope_id: str) -> dict[str, object] | None:
        """Read-only view of the Host's durable workspace binding for one scope.

        ``mode`` is the grant source (manual/auto) the Host recorded; ``state``
        is the Host's own re-stat of each root (active / missing / drifted).
        Nothing here grants a path: it only reports what the append-only
        binding tables and the filesystem currently say.
        """
        import os
        import stat as stat_module

        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            head = db.execute(
                "SELECT binding_id,current_revision,current_receipt_id,current_receipt_hash,"
                "root_set_digest,updated_at FROM task_workspace_binding_heads "
                "WHERE task_scope_id=? AND subject=?",
                (task_scope_id, self._auth.subject),
            ).fetchone()
            if head is None:
                return None
            roots = db.execute(
                "SELECT o.root_id,o.canonical_path,o.root_identity_hash,o.filesystem_identity_kind,"
                "o.filesystem_volume_id,o.filesystem_object_id,o.first_binding_set_revision,"
                "v.receipt_hash,g.source FROM task_workspace_binding_roots o "
                "JOIN task_workspace_binding_revisions v ON v.receipt_id=o.receipt_id "
                "JOIN task_workspace_binding_grants g ON g.grant_id=v.grant_id "
                "WHERE o.task_scope_id=? ORDER BY o.first_binding_set_revision ASC,o.root_id ASC",
                (task_scope_id,),
            ).fetchall()
        summaries = []
        for root in roots:
            try:
                raw = os.lstat(str(root["canonical_path"]))
            except OSError:
                state = "missing"
            else:
                if (
                    stat_module.S_ISLNK(raw.st_mode)
                    or not stat_module.S_ISDIR(raw.st_mode)
                    or str(root["filesystem_identity_kind"]) != "posix_inode"
                    or str(root["filesystem_volume_id"]) != str(raw.st_dev)
                    or str(root["filesystem_object_id"]) != str(raw.st_ino)
                ):
                    state = "drifted"
                else:
                    state = "active"
            summaries.append({
                "root_ref": str(root["root_id"])[:512],
                "root_path": str(root["canonical_path"])[:4096],
                "root_digest": str(root["root_identity_hash"]),
                "mode": str(root["source"]),
                "revision": int(root["first_binding_set_revision"]),
                "receipt_hash": str(root["receipt_hash"]),
                "state": state,
            })
        modes = {item["mode"] for item in summaries}
        states = [item["state"] for item in summaries]
        return {
            "binding_ref": str(head["binding_id"]),
            "revision": int(head["current_revision"]),
            "receipt_ref": str(head["current_receipt_id"]),
            "receipt_hash": str(head["current_receipt_hash"]),
            "root_set_digest": str(head["root_set_digest"]),
            "mode": next(iter(modes)) if len(modes) == 1 else ("mixed" if modes else "unknown"),
            "state": ("missing" if "missing" in states else "drifted" if "drifted" in states
                      else "active" if states else "unknown"),
            "roots": summaries,
        }

    async def read_view(
        self, request: ReadTaskScopeViewRequest
    ) -> Mapping[str, object]:
        await self._assert_owned_scope(request.scope_ref)
        view = await self._projections.read_materialized_view(
            request.kind, task_scope_id=request.scope_ref
        )
        result = {
            "scope_ref": request.scope_ref,
            "source_ref": view.source_id,
            "source_hash": view.source_hash,
            "kind": view.view_kind,
            "content": view.content,
            "content_sha256": view.content_sha256,
            "root_block_id": view.root_block_id,
            "block_count": view.block_count,
            "receipt_hash": view.receipt_hash,
        }
        self._assert_public_bound(result)
        return result

    async def list_evidence_groups(
        self, request: ListEvidenceGroupsRequest
    ) -> Mapping[str, object]:
        await self._assert_current_source(
            request.scope_ref, request.source_ref, request.source_hash
        )
        offset = 0
        if request.cursor is not None:
            cursor = self._decode_cursor(request.cursor, "evidence-groups")
            self._assert_cursor_source(cursor, request)
            offset = self._cursor_int(cursor, "offset", minimum=0)
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT block_id,content_sha256,content FROM task_scope_read_blocks "
                "WHERE source_id=? AND view_kind='EVIDENCE' AND block_kind='group' "
                "ORDER BY CAST(json_extract(CAST(content AS TEXT),'$.logical_group') AS INTEGER) "
                "LIMIT ? OFFSET ?",
                (request.source_ref, request.limit + 1, offset),
            ).fetchall()
        descriptors = [self._group_descriptor(row) for row in rows[: request.limit]]
        next_cursor = None
        if len(rows) > request.limit:
            next_cursor = self._encode_cursor(
                {
                    "kind": "evidence-groups",
                    "scope_ref": request.scope_ref,
                    "source_ref": request.source_ref,
                    "source_hash": request.source_hash,
                    "offset": offset + request.limit,
                }
            )
        result = {
            "scope_ref": request.scope_ref,
            "source_ref": request.source_ref,
            "source_hash": request.source_hash,
            "groups": descriptors,
            "next_cursor": next_cursor,
            "receipt_hash": canonical_hash(
                {
                    "scope_ref": request.scope_ref,
                    "source_ref": request.source_ref,
                    "source_hash": request.source_hash,
                    "offset": offset,
                    "groups": descriptors,
                    "next_cursor": next_cursor,
                }
            ),
        }
        self._assert_public_bound(result)
        return result

    async def read_evidence_page(
        self, request: ReadEvidencePageRequest
    ) -> Mapping[str, object]:
        await self._assert_current_source(
            request.scope_ref, request.source_ref, request.source_hash
        )
        group = self._load_group(
            request.source_ref, request.group_ref, request.group_hash
        )
        cache_key = (request.source_ref, request.group_ref, request.group_hash)
        cached_refs = self._evidence_group_ref_cache.get(cache_key)
        if cached_refs is None:
            leaf_refs = self._collect_group_leaf_refs(request.source_ref, group)
            if len(self._evidence_group_ref_cache) >= 64:
                self._evidence_group_ref_cache.pop(
                    next(iter(self._evidence_group_ref_cache))
                )
            self._evidence_group_ref_cache[cache_key] = tuple(leaf_refs)
        else:
            leaf_refs = [dict(item) for item in cached_refs]
        if not leaf_refs:
            raise HumanMemoryHostServiceError("human_memory_evidence_group_empty")
        leaf_index = 0
        event_offset = 0
        chunk_index = 0
        chunk_offset = 0
        prior_page_hash = None
        if request.cursor is not None:
            cursor = self._decode_cursor(request.cursor, "evidence-page")
            self._assert_cursor_source(cursor, request)
            if (
                cursor.get("group_ref") != request.group_ref
                or cursor.get("group_hash") != request.group_hash
            ):
                raise HumanMemoryHostServiceError(
                    "human_memory_evidence_cursor_binding_mismatch"
                )
            leaf_index = self._cursor_int(cursor, "leaf_index", minimum=0)
            event_offset = self._cursor_int(cursor, "event_offset", minimum=0)
            chunk_index = self._cursor_int(cursor, "chunk_index", minimum=0)
            chunk_offset = self._cursor_int(cursor, "chunk_offset", minimum=0)
            prior_page_hash = cursor.get("prior_page_hash")
            self._assert_position_ref(cursor, leaf_refs, leaf_index)
        if leaf_index >= len(leaf_refs):
            raise HumanMemoryHostServiceError("human_memory_evidence_cursor_exhausted")
        page, next_position = self._read_dag_page(
            request.source_ref,
            leaf_refs,
            leaf_index=leaf_index,
            event_offset=event_offset,
            chunk_index=chunk_index,
            chunk_offset=chunk_offset,
        )
        page_hash = str(page["content_sha256"])
        next_cursor = None
        if next_position is not None:
            next_leaf, next_event, next_chunk, next_offset = next_position
            next_ref = leaf_refs[next_leaf]
            next_cursor = self._encode_cursor(
                {
                    "kind": "evidence-page",
                    "scope_ref": request.scope_ref,
                    "source_ref": request.source_ref,
                    "source_hash": request.source_hash,
                    "group_ref": request.group_ref,
                    "group_hash": request.group_hash,
                    "leaf_index": next_leaf,
                    "event_offset": next_event,
                    "chunk_index": next_chunk,
                    "chunk_offset": next_offset,
                    "block_ref": next_ref["block_id"],
                    "block_hash": next_ref["content_sha256"],
                    "prior_page_hash": page_hash,
                }
            )
        result = {
            "scope_ref": request.scope_ref,
            "source_ref": request.source_ref,
            "source_hash": request.source_hash,
            "group_ref": request.group_ref,
            "group_hash": request.group_hash,
            "prior_page_hash": prior_page_hash,
            "page": page,
            "next_cursor": next_cursor,
        }
        self._assert_public_bound(result)
        return result

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
        }

    @staticmethod
    def _encode_cursor(payload: Mapping[str, object]) -> str:
        body = dict(payload)
        envelope = {"payload": body, "sha256": canonical_hash(body)}
        return base64.urlsafe_b64encode(canonical_json(envelope).encode()).decode()

    @staticmethod
    def _decode_cursor(token: str, expected_kind: str) -> dict[str, object]:
        try:
            raw = base64.urlsafe_b64decode(token.encode("ascii"))
            envelope = json.loads(raw)
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise HumanMemoryHostServiceError(
                "human_memory_evidence_cursor_invalid"
            ) from exc
        if not isinstance(envelope, Mapping) or set(envelope) != {"payload", "sha256"}:
            raise HumanMemoryHostServiceError("human_memory_evidence_cursor_invalid")
        payload = envelope["payload"]
        if (
            not isinstance(payload, Mapping)
            or envelope["sha256"] != canonical_hash(dict(payload))
            or payload.get("kind") != expected_kind
        ):
            raise HumanMemoryHostServiceError("human_memory_evidence_cursor_invalid")
        return dict(payload)

    @staticmethod
    def _cursor_int(
        payload: Mapping[str, object], name: str, *, minimum: int
    ) -> int:
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise HumanMemoryHostServiceError("human_memory_evidence_cursor_invalid")
        return value

    @staticmethod
    def _assert_cursor_source(
        cursor: Mapping[str, object],
        request: ListEvidenceGroupsRequest | ReadEvidencePageRequest,
    ) -> None:
        if (
            cursor.get("scope_ref") != request.scope_ref
            or cursor.get("source_ref") != request.source_ref
            or cursor.get("source_hash") != request.source_hash
        ):
            raise HumanMemoryHostServiceError(
                "human_memory_evidence_cursor_binding_mismatch"
            )

    async def _assert_current_source(
        self, scope_ref: str, source_ref: str, source_hash: str
    ) -> None:
        await self._assert_owned_scope(scope_ref)
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT h.source_id,h.source_hash FROM task_scope_projection_source_heads h "
                "WHERE h.task_scope_id=?",
                (scope_ref,),
            ).fetchone()
        if row is None:
            raise HumanMemoryHostServiceError("human_memory_evidence_source_unavailable")
        if str(row[0]) != source_ref or str(row[1]) != source_hash:
            raise HumanMemoryHostServiceError("human_memory_evidence_source_stale")

    @staticmethod
    def _group_descriptor(row: sqlite3.Row) -> dict[str, object]:
        content = bytes(row["content"])
        content_hash = hashlib.sha256(content).hexdigest()
        if content_hash != row["content_sha256"] or len(content) > 32 * 1024:
            raise HumanMemoryHostServiceError(
                "human_memory_evidence_group_integrity_failed"
            )
        try:
            group = json.loads(content)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise HumanMemoryHostServiceError(
                "human_memory_evidence_group_integrity_failed"
            ) from exc
        if not isinstance(group, Mapping):
            raise HumanMemoryHostServiceError(
                "human_memory_evidence_group_integrity_failed"
            )
        logical_group = group.get("logical_group")
        first = group.get("first_event_sequence")
        last = group.get("last_event_sequence")
        count = group.get("event_count")
        if (
            group.get("schema_version") != 1
            or isinstance(logical_group, bool)
            or not isinstance(logical_group, int)
            or logical_group < 1
            or isinstance(first, bool)
            or not isinstance(first, int)
            or first < 1
            or isinstance(last, bool)
            or not isinstance(last, int)
            or last < first
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count != last - first + 1
            or count > 500
            or not isinstance(group.get("leaves_root"), Mapping)
        ):
            raise HumanMemoryHostServiceError(
                "human_memory_evidence_group_integrity_failed"
            )
        return {
            "group_ref": str(row["block_id"]),
            "group_hash": content_hash,
            "logical_group": logical_group,
            "first_event_sequence": first,
            "last_event_sequence": last,
            "event_count": count,
        }

    def _load_group(
        self, source_ref: str, group_ref: str, group_hash: str
    ) -> dict[str, object]:
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT block_id,content_sha256,content FROM task_scope_read_blocks "
                "WHERE block_id=? AND source_id=? AND view_kind='EVIDENCE' "
                "AND block_kind='group'",
                (group_ref, source_ref),
            ).fetchone()
        if row is None or str(row["content_sha256"]) != group_hash:
            raise HumanMemoryHostServiceError(
                "human_memory_evidence_group_binding_mismatch"
            )
        self._group_descriptor(row)
        try:
            group = json.loads(bytes(row["content"]))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise HumanMemoryHostServiceError(
                "human_memory_evidence_group_integrity_failed"
            ) from exc
        if not isinstance(group, dict):
            raise HumanMemoryHostServiceError(
                "human_memory_evidence_group_integrity_failed"
            )
        return group

    def _collect_group_leaf_refs(
        self, source_ref: str, group: Mapping[str, object]
    ) -> list[dict[str, object]]:
        root = group.get("leaves_root")
        if not isinstance(root, Mapping):
            raise HumanMemoryHostServiceError(
                "human_memory_evidence_group_integrity_failed"
            )
        refs: list[dict[str, object]] = []
        visiting: set[str] = set()

        def visit(raw_ref: Mapping[str, object], depth: int) -> None:
            if depth > 64:
                raise HumanMemoryHostServiceError(
                    "human_memory_evidence_index_depth_rejected"
                )
            row, content = self._read_exact_block(source_ref, raw_ref)
            block_id = str(row["block_id"])
            if block_id in visiting:
                raise HumanMemoryHostServiceError(
                    "human_memory_evidence_index_cycle_rejected"
                )
            kind = str(row["block_kind"])
            if kind == "leaf":
                refs.append(self._block_ref(row))
                return
            if kind != "index":
                raise HumanMemoryHostServiceError(
                    "human_memory_evidence_index_kind_rejected"
                )
            try:
                index = json.loads(content)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise HumanMemoryHostServiceError(
                    "human_memory_evidence_index_invalid"
                ) from exc
            children = index.get("children") if isinstance(index, Mapping) else None
            if not isinstance(children, list):
                raise HumanMemoryHostServiceError(
                    "human_memory_evidence_index_invalid"
                )
            visiting.add(block_id)
            for child in children:
                if not isinstance(child, Mapping):
                    raise HumanMemoryHostServiceError(
                        "human_memory_evidence_index_invalid"
                    )
                visit(child, depth + 1)
            visiting.remove(block_id)

        visit(root, 0)
        return refs

    def _read_exact_block(
        self, source_ref: str, raw_ref: Mapping[str, object]
    ) -> tuple[sqlite3.Row, bytes]:
        block_id = raw_ref.get("block_id")
        block_kind = raw_ref.get("block_kind")
        content_hash = raw_ref.get("content_sha256")
        byte_length = raw_ref.get("byte_length")
        if (
            not isinstance(block_id, str)
            or block_kind not in {"chunk", "leaf", "index"}
            or not isinstance(content_hash, str)
            or isinstance(byte_length, bool)
            or not isinstance(byte_length, int)
            or byte_length < 0
        ):
            raise HumanMemoryHostServiceError("human_memory_evidence_ref_invalid")
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT block_id,source_id,block_kind,content_sha256,content "
                "FROM task_scope_read_blocks WHERE block_id=? AND source_id=?",
                (block_id, source_ref),
            ).fetchone()
        if row is None:
            raise HumanMemoryHostServiceError("human_memory_evidence_block_missing")
        content = bytes(row["content"])
        if (
            str(row["content_sha256"]) != content_hash
            or str(row["block_kind"]) != block_kind
            or len(content) != byte_length
            or hashlib.sha256(content).hexdigest() != content_hash
        ):
            raise HumanMemoryHostServiceError(
                "human_memory_evidence_block_integrity_failed"
            )
        return row, content

    @staticmethod
    def _block_ref(row: sqlite3.Row) -> dict[str, object]:
        return {
            "block_id": str(row["block_id"]),
            "block_kind": str(row["block_kind"]),
            "content_sha256": str(row["content_sha256"]),
            "byte_length": len(bytes(row["content"])),
        }

    @staticmethod
    def _assert_position_ref(
        cursor: Mapping[str, object], refs: list[dict[str, object]], leaf_index: int
    ) -> None:
        if leaf_index >= len(refs):
            raise HumanMemoryHostServiceError("human_memory_evidence_cursor_exhausted")
        ref = refs[leaf_index]
        if (
            cursor.get("block_ref") != ref["block_id"]
            or cursor.get("block_hash") != ref["content_sha256"]
        ):
            raise HumanMemoryHostServiceError(
                "human_memory_evidence_cursor_block_mismatch"
            )

    def _read_dag_page(
        self,
        source_ref: str,
        leaf_refs: list[dict[str, object]],
        *,
        leaf_index: int,
        event_offset: int,
        chunk_index: int,
        chunk_offset: int,
    ) -> tuple[dict[str, object], tuple[int, int, int, int] | None]:
        _, leaf_content = self._read_exact_block(source_ref, leaf_refs[leaf_index])
        try:
            leaf = json.loads(leaf_content)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise HumanMemoryHostServiceError("human_memory_evidence_leaf_invalid") from exc
        if not isinstance(leaf, Mapping):
            raise HumanMemoryHostServiceError("human_memory_evidence_leaf_invalid")
        events = leaf.get("events")
        if isinstance(events, list):
            if chunk_index or chunk_offset or event_offset >= len(events):
                raise HumanMemoryHostServiceError("human_memory_evidence_cursor_invalid")
            selected: list[dict[str, object]] = []
            for raw_event in events[event_offset:]:
                if not isinstance(raw_event, dict):
                    raise HumanMemoryHostServiceError("human_memory_evidence_event_invalid")
                event = dict(raw_event)
                payload = event.get("payload")
                if isinstance(payload, Mapping):
                    event_index = payload.get("event_index")
                    if isinstance(event_index, int) and not isinstance(event_index, bool):
                        event["event_index"] = event_index
                candidate = canonical_json(
                    {"schema_version": 1, "events": [*selected, event]}
                )
                if selected and len(candidate.encode("utf-8")) > 20 * 1024:
                    break
                if len(candidate.encode("utf-8")) > 20 * 1024:
                    raise HumanMemoryHostServiceError(
                        "human_memory_evidence_leaf_event_too_large"
                    )
                selected.append(event)
            page = self._event_page(selected)
            next_event = event_offset + len(selected)
            if next_event < len(events):
                return page, (leaf_index, next_event, 0, 0)
            if leaf_index + 1 < len(leaf_refs):
                return page, (leaf_index + 1, 0, 0, 0)
            return page, None
        chunks = leaf.get("chunks")
        if event_offset or not isinstance(chunks, list) or not chunks:
            raise HumanMemoryHostServiceError("human_memory_evidence_leaf_invalid")
        if chunk_index >= len(chunks) or not isinstance(chunks[chunk_index], Mapping):
            raise HumanMemoryHostServiceError("human_memory_evidence_cursor_invalid")
        event_hasher = hashlib.sha256()
        total = 0
        response_chunk_size = 12 * 1024
        absolute_offset = 0
        for prior_ref in chunks[:chunk_index]:
            if not isinstance(prior_ref, Mapping):
                raise HumanMemoryHostServiceError("human_memory_evidence_leaf_invalid")
            _, prior_content = self._read_exact_block(source_ref, prior_ref)
            absolute_offset += len(prior_content)
        absolute_offset += chunk_offset
        response = bytearray()
        next_chunk_index = chunk_index
        next_chunk_offset = chunk_offset
        for index, chunk_ref in enumerate(chunks):
            if not isinstance(chunk_ref, Mapping):
                raise HumanMemoryHostServiceError("human_memory_evidence_leaf_invalid")
            _, content = self._read_exact_block(source_ref, chunk_ref)
            event_hasher.update(content)
            total += len(content)
            if index < chunk_index or len(response) >= response_chunk_size:
                continue
            start = chunk_offset if index == chunk_index else 0
            if start >= len(content):
                raise HumanMemoryHostServiceError("human_memory_evidence_cursor_invalid")
            take = min(response_chunk_size - len(response), len(content) - start)
            response.extend(content[start : start + take])
            if start + take < len(content):
                next_chunk_index = index
                next_chunk_offset = start + take
            else:
                next_chunk_index = index + 1
                next_chunk_offset = 0
        if total != leaf.get("byte_length") or event_hasher.hexdigest() != leaf.get(
            "content_sha256"
        ):
            raise HumanMemoryHostServiceError(
                "human_memory_evidence_event_chunks_invalid"
            )
        if not response:
            raise HumanMemoryHostServiceError("human_memory_evidence_cursor_invalid")
        event_length = int(leaf["byte_length"])
        payload = {
            "schema_version": 1,
            "event_chunk": {
                "event_sequence": leaf.get("event_sequence"),
                "ordinal": absolute_offset // response_chunk_size + 1,
                "count": (event_length + response_chunk_size - 1) // response_chunk_size,
                "event_content_sha256": leaf.get("content_sha256"),
                "encoding": "base64",
                "content": base64.b64encode(bytes(response)).decode("ascii"),
            },
        }
        content = canonical_json(payload)
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        page = {
            "page_id": f"sha256:{content_hash}",
            "content": content,
            "content_sha256": content_hash,
        }
        if next_chunk_index < len(chunks):
            return page, (leaf_index, 0, next_chunk_index, next_chunk_offset)
        if leaf_index + 1 < len(leaf_refs):
            return page, (leaf_index + 1, 0, 0, 0)
        return page, None

    @staticmethod
    def _assert_public_bound(value: Mapping[str, object]) -> None:
        if len(canonical_json(dict(value)).encode("utf-8")) > 32 * 1024:
            raise HumanMemoryHostServiceError(
                "task_scope_projection_public_response_too_large"
            )

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
        import aiosqlite
        from deskpet.execution.admission_rejection import read_admission_rejection_tx
        rejections = {}
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            for row in rows:
                rejection = await read_admission_rejection_tx(db, turn_id=row["turn_id"], subject=self._auth.subject)
                if rejection is not None:
                    rejections[row["turn_id"]] = rejection
        turns = []
        for row in rows:
            stored = json.loads(str(row["turn_json"]))
            payload = stored.get("payload", {})
            turns.append(
                {
                    "turn_ref": str(row["turn_id"]),
                    "enqueue_sequence": int(row["enqueue_sequence"]),
                    "state": "REJECTED" if row["turn_id"] in rejections else str(row["current_state"]),
                    **({"rejection_reason": rejections[row["turn_id"]]["reason"]} if row["turn_id"] in rejections else {}),
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
                    canonical_json(
                        {
                            key: _json_cell(row[key])
                            # sqlite3.Row iteration yields values, unlike dict.
                            for key in row.keys()  # noqa: SIM118
                        }
                    ).encode("utf-8")
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
        manifest_hash = hashlib.sha256(
            canonical_json(raw_sets).encode("utf-8")
        ).hexdigest()
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
        envelope, receipt = build_host_typed_evidence(
            subject=self._auth.subject,
            authority_ref=self._auth.authority_ref,
            payload=payload,
            idempotency_key=idempotency_key,
            source_ref=source_ref,
            run_id=run_id,
        )
        return await self._program.append_evidence(envelope, receipt)


HOST_TYPED_INGRESS_FILTER_POLICY = "host-typed-ingress/v1"
HOST_PUBLIC_TURN_FILTER_POLICY = "host-public-turn/v1"


def build_foreground_turn_evidence(
    *,
    subject: str,
    authority_ref: str,
    delivery_key: str,
    text: str,
) -> tuple[SanitizedEvidenceEnvelope, SanitizedEvidenceReceipt]:
    """Deterministic sanitized envelope + receipt of one foreground user turn.

    Payload keys ``schema_version`` / ``delivery_key`` / ``text`` (``/text`` is the
    only quotable pointer of the S5b analysis proposal); ``run_id`` is the
    per-subject foreground evidence run so every turn of the subject batches
    together in Memory; the same recipe is shared with the S5b test harness.
    """

    payload = {
        "schema_version": 1,
        "delivery_key": delivery_key,
        "text": text,
    }
    payload_hash = canonical_hash(payload)
    run_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"simple-harness:foreground-evidence-run:{subject}",
        )
    )
    evidence_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "simple-harness:foreground-evidence:"
            f"{subject}:{delivery_key}",
        )
    )
    disclosure = DisclosureContext(
        run_id=run_id,
        subject=subject,
        recipient=DeliveryRecipient.USER_SELF,
        recipient_id=subject,
        intended_audience=IntendedAudience.USER_SELF,
        purpose=DisclosurePurpose.TASK_EXECUTION,
        source=DisclosureSource.AUTHENTICATED_HOST,
        trust=DisclosureTrust.TRUSTED_AUTHORITY,
        generation=DisclosureGeneration.CURRENT,
        authority_ref=authority_ref,
        reason_codes=(DisclosureReasonCode.MINIMUM_NECESSARY,),
    )
    envelope = SanitizedEvidenceEnvelope(
        evidence_id=evidence_id,
        run_id=run_id,
        subject=subject,
        source_kind=EvidenceSourceKind.USER_MESSAGE,
        source_ref=f"foreground-turn:{delivery_key}",
        source_hash=payload_hash,
        sanitized_payload=payload,
        sanitized_hash=payload_hash,
        filter_policy_version=HOST_PUBLIC_TURN_FILTER_POLICY,
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
        subject=subject,
        evidence_id=evidence_id,
        envelope_hash=envelope.envelope_hash,
        source_hash=payload_hash,
        sanitized_hash=payload_hash,
        filter_policy_version=HOST_PUBLIC_TURN_FILTER_POLICY,
        accepted=True,
        reason_codes=(EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
        disclosure_context=disclosure,
        evidence_refs=(),
        admitted_at=0.0,
    )
    return envelope, receipt


def build_host_typed_evidence(
    *,
    subject: str,
    authority_ref: str,
    payload: Mapping[str, object],
    idempotency_key: str,
    source_ref: str,
    run_id: str | None = None,
) -> tuple[SanitizedEvidenceEnvelope, SanitizedEvidenceReceipt]:
    """Deterministic sanitized envelope + receipt for one Host-typed payload.

    Shared by the Host service ingress and the S5b objective-event recorder
    (``execution/evidence_ingress.py``): same ``host-typed-ingress/v1`` filter
    policy, same id derivation, so replays converge on the same evidence row.
    """

    reject_private_payload(dict(payload), "host_evidence")
    run_ref = run_id or str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"simple-harness:host-evidence-run:{subject}:{idempotency_key}",
        )
    )
    evidence_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"simple-harness:host-evidence:{subject}:{idempotency_key}",
        )
    )
    payload_dict = dict(payload)
    payload_hash = canonical_hash(payload_dict)
    disclosure = DisclosureContext(
        run_id=run_ref,
        subject=subject,
        recipient=DeliveryRecipient.USER_SELF,
        recipient_id=subject,
        intended_audience=IntendedAudience.USER_SELF,
        purpose=DisclosurePurpose.TASK_EXECUTION,
        source=DisclosureSource.AUTHENTICATED_HOST,
        trust=DisclosureTrust.TRUSTED_AUTHORITY,
        generation=DisclosureGeneration.CURRENT,
        authority_ref=authority_ref,
        reason_codes=(DisclosureReasonCode.MINIMUM_NECESSARY,),
    )
    envelope = SanitizedEvidenceEnvelope(
        evidence_id=evidence_id,
        run_id=run_ref,
        subject=subject,
        source_kind=EvidenceSourceKind.USER_MESSAGE,
        source_ref=source_ref,
        source_hash=payload_hash,
        sanitized_payload=payload_dict,
        sanitized_hash=payload_hash,
        filter_policy_version=HOST_TYPED_INGRESS_FILTER_POLICY,
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
        subject=subject,
        evidence_id=evidence_id,
        envelope_hash=envelope.envelope_hash,
        source_hash=payload_hash,
        sanitized_hash=payload_hash,
        filter_policy_version=HOST_TYPED_INGRESS_FILTER_POLICY,
        accepted=True,
        reason_codes=(EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
        disclosure_context=disclosure,
        evidence_refs=(),
        admitted_at=0.0,
    )
    return envelope, receipt


@dataclass(frozen=True, slots=True)
class HumanMemoryHostServiceFactory:
    db_path: Path
    startup: StartupEpochDecision
    settled_run_reader: object | None = None
    suppression_resolver: object | None = None
    run_binding_reader: object | None = None
    history_visibility_checker: object | None = None
    decision_ingress_getter: object | None = None
    cognitive_runtime_getter: object | None = None
    display_invalidation: object | None = None

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
            settled_run_reader=self.settled_run_reader,
            suppression_resolver=self.suppression_resolver,
            run_binding_reader=self.run_binding_reader,
            history_visibility_checker=self.history_visibility_checker,
            decision_ingress_getter=self.decision_ingress_getter,
            cognitive_runtime_getter=self.cognitive_runtime_getter,
            display_invalidation=self.display_invalidation,
        )


__all__ = (
    "HOST_TYPED_INGRESS_FILTER_POLICY",
    "AppendBindingRequest",
    "AppendDeterministicEventsRequest",
    "AppendPrimaryEventRequest",
    "AuditRefsRequest",
    "AuthenticatedHostSnapshot",
    "ControlRunRequest",
    "ExactControlRunRequest",
    "CreateTaskScopeRequest",
    "ListTaskScopesRequest",
    "DecideManualBindingRequest",
    "DeterministicEventSeedPort",
    "ForegroundSchedulerWakePort",
    "HumanMemoryHostService",
    "HumanMemoryHostServiceError",
    "HumanMemoryHostServiceFactory",
    "ListEvidenceGroupsRequest",
    "MutateTaskScopeRequest",
    "OpenTaskScopeRequest",
    "QueueTurnRequest",
    "ReadEvidencePageRequest",
    "ReadTaskScopeViewRequest",
    "RecoveryLifecyclePort",
    "SaveCheckpointRequest",
    "SearchTaskScopesRequest",
    "WorkspaceBindingAppendPort",
    "build_host_typed_evidence",
)
