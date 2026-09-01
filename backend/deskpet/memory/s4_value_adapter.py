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
import uuid
from pathlib import Path
from typing import Any, Mapping

from deskpet.memory.human_memory_service import (
    AppendDeterministicEventsRequest,
    AuthenticatedHostSnapshot,
    CreateTaskScopeRequest,
    HumanMemoryHostService,
    HumanMemoryHostServiceError,
    HumanMemoryHostServiceFactory,
    OpenTaskScopeRequest,
    QueueTurnRequest,
    ReadTaskScopeViewRequest,
    SaveCheckpointRequest,
    SearchTaskScopesRequest,
)
from deskpet.memory.schema import dispatch_startup_epoch, inspect_startup_epoch
from deskpet.task_scope.store import CanonicalTaskScopeStore


class _CanonicalBulkSeed:
    def __init__(self, db_path: Path) -> None:
        self._store = CanonicalTaskScopeStore(db_path)

    async def append_deterministic_events(self, **values):  # type: ignore[no-untyped-def]
        operation = getattr(self._store, "append_deterministic_events", None)
        if not callable(operation):
            raise HumanMemoryHostServiceError(
                "human_memory_bulk_seed_authority_unavailable"
            )
        values.pop("idempotency_key", None)
        receipt = await operation(**values)
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
            )
            return {
                "format_epoch": startup.composition_mode.value,
                "startup_epoch": startup.epoch.value,
            }

        service = self._require_service()
        self._assert_fixture_principal(request)
        if operation == "primary.open":
            return await service.open_primary()
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
                    text=str(request.get("text", "")),
                )
            )
        if operation == "authority.snapshot":
            return await service.authority_snapshot()
        if operation == "derived.drop_rebuildable":
            return await service.drop_rebuildable(str(request["scope_ref"]))
        if operation == "derived.rebuild":
            return await service.rebuild_derived(str(request["scope_ref"]))
        if operation == "task_scope.search":
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
        if operation == "view.read":
            return await service.read_view(
                ReadTaskScopeViewRequest(
                    scope_ref=str(request["scope_ref"]),
                    kind=str(request["kind"]),
                )
            )
        if operation == "queue.snapshot":
            return await service.queue_snapshot()
        if operation == "recovery.manifest":
            return await service.raw_integrity_manifest()
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
