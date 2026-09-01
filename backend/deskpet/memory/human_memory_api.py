# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Strict WebSocket command adapter for the S4 Host memory facade."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from deskpet.memory.human_memory_service import (
    AppendBindingRequest,
    AppendPrimaryEventRequest,
    AuditRefsRequest,
    AuthenticatedHostSnapshot,
    ControlRunRequest,
    CreateTaskScopeRequest,
    DecideManualBindingRequest,
    HumanMemoryHostServiceError,
    HumanMemoryHostServiceFactory,
    ListEvidenceGroupsRequest,
    MutateTaskScopeRequest,
    OpenTaskScopeRequest,
    QueueTurnRequest,
    ReadEvidencePageRequest,
    ReadTaskScopeViewRequest,
    SearchTaskScopesRequest,
)

HUMAN_MEMORY_COMMAND = "human_memory_request"
_AUTHORITY_FIELDS = frozenset(
    {
        "subject",
        "allowed_scope_ids",
        "allowed_set",
        "mode",
        "authority",
        "authority_ref",
        "database_path",
        "db",
        "db_path",
        "table",
        "worker_authority",
        "worker",
        "principal_id",
    }
)


def _reject_authority_fields(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).strip().lower().replace("-", "_") in _AUTHORITY_FIELDS:
                raise HumanMemoryHostServiceError(
                    "human_memory_public_authority_field_rejected"
                )
            _reject_authority_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_authority_fields(child)


async def handle_human_memory_command(
    raw: Mapping[str, Any],
    *,
    factory: HumanMemoryHostServiceFactory | None,
    auth: AuthenticatedHostSnapshot,
    binding_append: object | None = None,
    recovery: object | None = None,
    scheduler_wake: object | None = None,
) -> dict[str, Any] | None:
    if raw.get("type") != HUMAN_MEMORY_COMMAND:
        return None
    request_id = str(raw.get("request_id") or "").strip()
    operation = str(raw.get("operation") or "").strip()
    request = raw.get("request") or {}
    if not request_id or not operation or not isinstance(request, Mapping):
        return _error(request_id, "human_memory_invalid_request")
    try:
        _reject_authority_fields(request)
        if factory is None:
            raise HumanMemoryHostServiceError(
                "human_memory_legacy_epoch_unsupported"
            )
        service = factory.bind(
            auth,
            binding_append=binding_append,  # type: ignore[arg-type]
            recovery=recovery,  # type: ignore[arg-type]
            scheduler_wake=scheduler_wake,  # type: ignore[arg-type]
        )
        payload = await _dispatch(service, operation, dict(request), request_id)
    except Exception as exc:  # noqa: BLE001 - stable public error projection
        return _error(
            request_id,
            str(getattr(exc, "code", "human_memory_request_rejected")),
        )
    return {
        "type": "human_memory_response",
        "request_id": request_id,
        "payload": {"ok": True, "operation": operation, "result": dict(payload)},
    }


async def _dispatch(  # type: ignore[no-untyped-def]
    service, operation: str, request: dict[str, Any], request_id: str
):
    if operation == "primary.open":
        return await service.open_primary()
    if operation == "primary.append":
        event = request.get("event")
        if not isinstance(event, Mapping):
            raise HumanMemoryHostServiceError("primary_event_payload_rejected")
        return await service.append_primary_event(
            AppendPrimaryEventRequest(dict(event), request_id)
        )
    if operation == "task_scope.create":
        return await service.create_task_scope(
            CreateTaskScopeRequest(
                str(request.get("fixture_key") or request_id),
                str(request["title"]),
                str(request["goal"]),
                request_id,
            )
        )
    if operation == "task_scope.search":
        return await service.search_task_scopes(
            SearchTaskScopesRequest(
                str(request["query"]),
                int(request.get("max_candidates", 8)),
                None if request.get("cursor") is None else str(request["cursor"]),
            )
        )
    if operation == "task_scope.open_exact":
        probe = request.get("live_probe")
        return await service.open_task_scope(
            OpenTaskScopeRequest(
                str(request["scope_ref"]),
                None if probe is None else dict(probe),
                None
                if request.get("expected_source_hash") is None
                else str(request["expected_source_hash"]),
            )
        )
    if operation == "task_scope.view":
        return await service.read_view(
            ReadTaskScopeViewRequest(
                str(request["scope_ref"]), str(request["kind"])
            )
        )
    if operation == "task_scope.evidence_groups":
        return await service.list_evidence_groups(
            ListEvidenceGroupsRequest(
                scope_ref=str(request["scope_ref"]),
                source_ref=str(request["source_ref"]),
                source_hash=str(request["source_hash"]),
                cursor=None
                if request.get("cursor") is None
                else str(request["cursor"]),
                limit=int(request.get("limit", 8)),
            )
        )
    if operation == "task_scope.evidence_page":
        return await service.read_evidence_page(
            ReadEvidencePageRequest(
                scope_ref=str(request["scope_ref"]),
                source_ref=str(request["source_ref"]),
                source_hash=str(request["source_hash"]),
                group_ref=str(request["group_ref"]),
                group_hash=str(request["group_hash"]),
                cursor=None
                if request.get("cursor") is None
                else str(request["cursor"]),
            )
        )
    if operation == "task_scope.mutate":
        mutation = request.get("mutation")
        if not isinstance(mutation, Mapping):
            raise HumanMemoryHostServiceError("task_scope_mutation_payload_rejected")
        return await service.mutate_task_scope(
            MutateTaskScopeRequest(
                str(request["scope_ref"]),
                str(mutation["kind"]),
                str(mutation["value"]),
                request_id,
            )
        )
    if operation == "binding.append":
        return await service.append_binding(
            AppendBindingRequest(
                str(request["scope_ref"]), str(request["root"]), request_id
            )
        )
    if operation == "binding.manual.propose":
        return await service.propose_manual_binding(
            AppendBindingRequest(
                str(request["scope_ref"]), str(request["root"]), request_id
            )
        )
    if operation == "binding.manual.decide":
        return await service.decide_manual_binding(
            DecideManualBindingRequest(
                str(request["challenge_ref"]),
                str(request["decision"]),
                request_id,
            )
        )
    if operation == "queue.enqueue":
        return await service.enqueue_turn(
            QueueTurnRequest(
                str(request["scope_ref"]),
                str(request.get("delivery_key") or request_id),
                str(request.get("text") or ""),
            )
        )
    if operation == "queue.control":
        return await service.control_current_run(
            ControlRunRequest(
                str(request["control"]),
                str(request.get("reason") or "user_requested"),
                request_id,
            )
        )
    if operation == "audit.refs":
        return await service.audit_refs(AuditRefsRequest(str(request["scope_ref"])))
    if operation == "recovery.manifest":
        return await service.recovery_manifest()
    if operation == "recovery.emergency_export":
        return await service.emergency_export()
    raise HumanMemoryHostServiceError("human_memory_operation_unavailable")


def _error(request_id: str, code: str) -> dict[str, Any]:
    return {
        "type": "human_memory_response",
        "request_id": request_id,
        "payload": {"ok": False, "error": {"code": code}},
    }


__all__ = ("HUMAN_MEMORY_COMMAND", "handle_human_memory_command")
