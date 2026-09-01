# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Public-only adapter for the frozen S4 value-smoke runner.

This adapter is a verification seam, not an HTTP payload model.  The fixture's
principal is bound once as a trusted Host snapshot; request ``subject`` fields
are assertions used to exercise wrong-principal rejection and never enter a
facade DTO.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from deskpet.execution.foreground_queue import ContextLineage, ForegroundQueueStore
from deskpet.memory.human_memory_service import (
    AppendBindingRequest,
    AppendDeterministicEventsRequest,
    AppendPrimaryEventRequest,
    AuditRefsRequest,
    AuthenticatedHostSnapshot,
    ControlRunRequest,
    CreateTaskScopeRequest,
    HumanMemoryHostService,
    HumanMemoryHostServiceError,
    HumanMemoryHostServiceFactory,
    ListEvidenceGroupsRequest,
    MutateTaskScopeRequest,
    OpenTaskScopeRequest,
    QueueTurnRequest,
    ReadEvidencePageRequest,
    ReadTaskScopeViewRequest,
    SaveCheckpointRequest,
    SearchTaskScopesRequest,
)
from deskpet.memory.recovery_fence import build_recovery_lifecycle_port
from deskpet.memory.schema import dispatch_startup_epoch, inspect_startup_epoch
from deskpet.task_scope.store import CanonicalTaskScopeStore


class _CanonicalBulkSeed:
    def __init__(self, db_path: Path) -> None:
        self._store = CanonicalTaskScopeStore(db_path)

    async def append_deterministic_events(self, **values):  # type: ignore[no-untyped-def]
        values.pop("idempotency_key", None)
        receipt = await self._store.append_deterministic_events(**values)
        return {
            "scope_ref": receipt.task_scope_id,
            "event_count": receipt.count,
            "first_event_ref": receipt.first_event_id,
            "first_event_sequence": receipt.first_event_sequence,
            "last_event_ref": receipt.last_event_id,
            "last_event_sequence": receipt.last_event_sequence,
            "source_ref": receipt.source_id,
            "source_hash": receipt.source_hash,
        }


class _FixtureBindingAppend:
    """Constructor-bound verification authority for the public API smoke.

    The binding state machine itself is covered by its focused suite.  This
    seam proves the facade cannot mint a grant from request fields.
    """

    async def append_binding(self, **values):  # type: ignore[no-untyped-def]
        payload = {
            "subject": values["subject"],
            "task_scope_id": values["task_scope_id"],
            "root": values["root"],
            "idempotency_key": values["idempotency_key"],
        }
        digest = __import__("hashlib").sha256(
            __import__("json").dumps(
                payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        return {
            "scope_ref": values["task_scope_id"],
            "receipt_ref": f"fixture-binding:{digest}",
            "receipt_hash": digest,
        }


class _FixtureSchedulerWake:
    def __init__(self, db_path: Path) -> None:
        self._store = ForegroundQueueStore(db_path)

    async def after_enqueue(self, *, subject: str) -> None:
        if await self._store.current_snapshot(subject) is not None:
            return
        await self._store.claim_next(
            subject=subject,
            owner_id="fixture-foreground-scheduler",
            claim_idempotency_key="fixture-claim-current",
            context=ContextLineage(
                context_snapshot_id="fixture-context",
                context_snapshot_revision=1,
                context_snapshot_hash="c" * 64,
            ),
            lease_seconds=3600.0,
        )


class S4ValuePublicAdapter:
    def __init__(self, *, fixture: Mapping[str, Any], artifact_dir: Path) -> None:
        self._fixture = dict(fixture)
        self._artifact_dir = artifact_dir.resolve()
        if ".local-test-evidence" not in self._artifact_dir.parts:
            raise ValueError("S4 adapter artifacts require .local-test-evidence")
        subject = str(self._fixture["subject"])
        self._auth = AuthenticatedHostSnapshot(
            subject=subject,
            principal_id=f"fixture-principal:{subject}",
            authority_ref=f"fixture-auth:{self._fixture['fixture_id']}",
        )
        self._db_path: Path | None = None
        self._service: HumanMemoryHostService | None = None
        self._primary_ref: str | None = None

    def invoke(self, operation: str, request: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = asyncio.run(self._invoke(operation, dict(request)))
        except Exception as exc:  # noqa: BLE001 - stable black-box projection
            return {
                "ok": False,
                "status": 409,
                "code": getattr(exc, "code", str(exc) or type(exc).__name__),
            }
        return {"ok": True, "status": 200, "payload": payload}

    async def _invoke(
        self, operation: str, request: dict[str, Any]
    ) -> Mapping[str, object]:
        if operation == "host.reset_fresh":
            if request != {"data_format": "human-memory-v1"}:
                raise HumanMemoryHostServiceError("human_memory_data_format_rejected")
            self._db_path = self._artifact_dir / f"s4-value-{uuid.uuid4().hex}.db"
            startup = await dispatch_startup_epoch(
                self._db_path, approved_fresh_lane=True
            )
            self._service = HumanMemoryHostServiceFactory(
                self._db_path, startup
            ).bind(
                self._auth,
                deterministic_event_seed=_CanonicalBulkSeed(self._db_path),
                binding_append=_FixtureBindingAppend(),
                recovery=build_recovery_lifecycle_port(
                    db_path=self._db_path, artifact_dir=self._artifact_dir
                ),
                scheduler_wake=_FixtureSchedulerWake(self._db_path),
            )
            return {
                "format_epoch": startup.composition_mode.value,
                "startup_epoch": startup.epoch.value,
            }
        if operation == "host.cold_restart":
            path = self._require_db()
            startup = inspect_startup_epoch(path, approved_fresh_lane=False)
            self._service = HumanMemoryHostServiceFactory(path, startup).bind(
                self._auth,
                deterministic_event_seed=_CanonicalBulkSeed(path),
                binding_append=_FixtureBindingAppend(),
                recovery=build_recovery_lifecycle_port(
                    db_path=path, artifact_dir=self._artifact_dir
                ),
                scheduler_wake=_FixtureSchedulerWake(path),
            )
            return {
                "format_epoch": startup.composition_mode.value,
                "startup_epoch": startup.epoch.value,
            }

        service = self._require_service()
        self._assert_fixture_principal(request)
        if operation == "primary.open":
            result = await service.open_primary()
            self._primary_ref = str(result["primary_ref"])
            return result
        if operation == "primary.append":
            event = request.get("event")
            if not isinstance(event, Mapping):
                raise HumanMemoryHostServiceError("primary_event_payload_rejected")
            return await service.append_primary_event(
                AppendPrimaryEventRequest(
                    event=dict(event),
                    idempotency_key="fixture-primary-append",
                )
            )
        if operation == "task_scope.create":
            scope = request.get("scope")
            if not isinstance(scope, Mapping):
                raise HumanMemoryHostServiceError("task_scope_payload_rejected")
            return await service.create_task_scope(
                CreateTaskScopeRequest(
                    fixture_key=str(scope["fixture_key"]),
                    title=str(scope["title"]),
                    goal=str(scope["goal"]),
                    idempotency_key=f"fixture-create:{scope['fixture_key']}",
                )
            )
        if operation == "task_scope.append_deterministic_events":
            return await service.append_deterministic_events(
                AppendDeterministicEventsRequest(
                    scope_ref=str(request["scope_ref"]),
                    count=request["count"],
                    canary=str(request["canary"]),
                    idempotency_key=f"fixture-events:{request['scope_ref']}",
                )
            )
        if operation == "task_scope.save_checkpoint":
            checkpoint = request.get("checkpoint")
            if not isinstance(checkpoint, Mapping):
                raise HumanMemoryHostServiceError("checkpoint_payload_rejected")
            return await service.save_checkpoint(
                SaveCheckpointRequest(
                    scope_ref=str(request["scope_ref"]),
                    checkpoint=dict(checkpoint),
                    idempotency_key=f"fixture-checkpoint:{request['scope_ref']}",
                )
            )
        if operation == "queue.enqueue":
            return await service.enqueue_turn(
                QueueTurnRequest(
                    scope_ref=str(request["scope_ref"]),
                    delivery_key=str(request["delivery_key"]),
                    text=str(request.get("text") or "fixture queued turn"),
                )
            )
        if operation == "authority.snapshot":
            return await service.authority_snapshot()
        if operation == "derived.drop_rebuildable":
            return await service.drop_rebuildable(str(request["scope_ref"]))
        if operation == "derived.rebuild":
            return await service.rebuild_derived(str(request["scope_ref"]))
        if operation == "task_scope.search":
            if request.get("public_fault") == "fts-unavailable":
                raise HumanMemoryHostServiceError("human_memory_search_unavailable")
            return await service.search_task_scopes(
                SearchTaskScopesRequest(
                    query=str(request["query"]),
                    max_candidates=int(request.get("max_candidates", 8)),
                    cursor=None
                    if request.get("cursor") is None
                    else str(request["cursor"]),
                )
            )
        if operation == "task_scope.open_exact":
            probe = request.get("live_probe")
            return await service.open_task_scope(
                OpenTaskScopeRequest(
                    scope_ref=str(request["scope_ref"]),
                    live_probe=None if probe is None else dict(probe),
                    expected_source_hash=None
                    if request.get("expected_source_hash") is None
                    else str(request["expected_source_hash"]),
                )
            )
        if operation == "task_scope.mutate":
            mutation = request.get("mutation")
            if not isinstance(mutation, Mapping):
                raise HumanMemoryHostServiceError("task_scope_mutation_payload_rejected")
            return await service.mutate_task_scope(
                MutateTaskScopeRequest(
                    scope_ref=str(request["scope_ref"]),
                    kind=str(mutation["kind"]),
                    value=str(mutation["value"]),
                    idempotency_key="fixture-scope-mutation",
                )
            )
        if operation == "binding.append":
            return await service.append_binding(
                AppendBindingRequest(
                    scope_ref=str(request["scope_ref"]),
                    root=str(request["root"]),
                    idempotency_key="fixture-binding-append",
                )
            )
        if operation == "queue.control":
            return await service.control_current_run(
                ControlRunRequest(
                    control=str(request["control"]),
                    reason="fixture-public-control",
                    idempotency_key=f"fixture-control:{request['control']}",
                )
            )
        if operation == "audit.refs":
            return await service.audit_refs(
                AuditRefsRequest(scope_ref=str(request["scope_ref"]))
            )
        if operation == "view.read":
            view = dict(
                await service.read_view(
                    ReadTaskScopeViewRequest(
                        scope_ref=str(request["scope_ref"]),
                        kind=str(request["kind"]),
                    )
                )
            )
            if str(request["kind"]) != "EVIDENCE":
                return view
            pages: list[dict[str, object]] = []
            groups_cursor = None
            while True:
                group_page = await service.list_evidence_groups(
                    ListEvidenceGroupsRequest(
                        scope_ref=str(view["scope_ref"]),
                        source_ref=str(view["source_ref"]),
                        source_hash=str(view["source_hash"]),
                        cursor=groups_cursor,
                    )
                )
                for group in group_page["groups"]:
                    page_cursor = None
                    while True:
                        result = await service.read_evidence_page(
                            ReadEvidencePageRequest(
                                scope_ref=str(view["scope_ref"]),
                                source_ref=str(view["source_ref"]),
                                source_hash=str(view["source_hash"]),
                                group_ref=str(group["group_ref"]),
                                group_hash=str(group["group_hash"]),
                                cursor=page_cursor,
                            )
                        )
                        page = dict(result["page"])
                        decoded = json.loads(str(page["content"]))
                        if "events" in decoded:
                            page["events"] = decoded["events"]
                        if "event_chunk" in decoded:
                            page["event_chunk"] = decoded["event_chunk"]
                        pages.append(page)
                        page_cursor = result["next_cursor"]
                        if page_cursor is None:
                            break
                groups_cursor = group_page["next_cursor"]
                if groups_cursor is None:
                    break
            view["pages"] = pages
            return view
        if operation == "queue.snapshot":
            return await service.queue_snapshot()
        if operation == "recovery.manifest":
            return await service.recovery_manifest()
        if operation == "recovery.emergency_export":
            return await service.emergency_export()
        if operation.startswith("legacy_session."):
            target = str(request.get("target") or "")
            if target and target == self._primary_ref:
                raise HumanMemoryHostServiceError(
                    "human_memory_primary_authority_immutable"
                )
            raise HumanMemoryHostServiceError("human_memory_legacy_session_unavailable")
        raise HumanMemoryHostServiceError("human_memory_operation_unavailable")

    def _assert_fixture_principal(self, request: Mapping[str, object]) -> None:
        asserted = request.get("subject")
        if asserted is not None and asserted != self._auth.subject:
            raise HumanMemoryHostServiceError("human_memory_permission_denied")

    def _require_db(self) -> Path:
        if self._db_path is None:
            raise HumanMemoryHostServiceError("human_memory_adapter_not_initialized")
        return self._db_path

    def _require_service(self) -> HumanMemoryHostService:
        if self._service is None:
            raise HumanMemoryHostServiceError("human_memory_adapter_not_initialized")
        return self._service


def create_adapter(*, fixture: dict[str, Any], artifact_dir: Path) -> S4ValuePublicAdapter:
    return S4ValuePublicAdapter(fixture=fixture, artifact_dir=artifact_dir)


__all__ = ("S4ValuePublicAdapter", "create_adapter")
