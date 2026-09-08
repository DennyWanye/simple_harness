# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Strict WebSocket command adapter for the S4 Host memory facade."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from deskpet.memory.human_memory_service import (
    AppendBindingRequest,
    AppendPrimaryEventRequest,
    AuditRefsRequest,
    AuthenticatedHostSnapshot,
    ControlRunRequest,
    CreateTaskScopeRequest,
    DecideManualBindingRequest,
    ExactControlRunRequest,
    HumanMemoryHostServiceError,
    HumanMemoryHostServiceFactory,
    ListEvidenceGroupsRequest,
    ListTaskScopesRequest,
    MutateTaskScopeRequest,
    OpenTaskScopeRequest,
    QueueTurnRequest,
    ReadEvidencePageRequest,
    ReadTaskScopeViewRequest,
    SearchTaskScopesRequest,
)

HUMAN_MEMORY_COMMAND = "human_memory_request"
HUMAN_AUDIT_OPERATIONS = frozenset({
    "primary.audit.open", "primary.audit.page", "primary.audit.host.page", "primary.audit.close",
})
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
    request_id = raw.get("request_id")
    operation = str(raw.get("operation") or "").strip()
    request = raw.get("request") or {}
    if (
        not isinstance(request_id, str)
        or not request_id.strip()
        or len(request_id) > 512
        or "\x00" in request_id
        or not operation
        or not isinstance(request, Mapping)
    ):
        return _error(
            request_id if isinstance(request_id, str) else "",
            "human_memory_invalid_request",
        )
    try:
        _reject_authority_fields(request)
        if factory is None:
            raise HumanMemoryHostServiceError("human_memory_legacy_epoch_unsupported")
        service = factory.bind(
            auth,
            binding_append=binding_append,  # type: ignore[arg-type]
            recovery=recovery,  # type: ignore[arg-type]
            scheduler_wake=scheduler_wake,  # type: ignore[arg-type]
        )
        # Server-owned display generation only; never accepted in the wire DTO.
        # Capture before the read and validate AFTER the final async identity fence.
        changes = getattr(factory, "display_invalidation", None) if operation == "primary.memory.graph" else None
        generation = changes.generation if changes is not None else None
        payload = await _dispatch(service, operation, dict(request), request_id)
        if operation in HUMAN_AUDIT_OPERATIONS:
            from deskpet.memory.writer_fence import human_memory_request_boundary

            async with human_memory_request_boundary():
                service.check_primary_audit_response(operation, payload)
        if operation in {
            "primary.state",
            "primary.messages.page",
            "primary.messages.detail",
            "primary.memory.list",
            "primary.memory.graph",
            "primary.memory.forget",
            "primary.bindings.pending",
            "primary.bindings.status",
            "primary.bindings.decide",
        }:
            from deskpet.memory.writer_fence import human_memory_request_boundary

            # Production /ws/control already supplies the verified connection scope.
            # A reconnect during a slow read must not disclose through the old lease.
            async with human_memory_request_boundary():
                pass
        if changes is not None and changes.generation != generation:
            raise HumanMemoryHostServiceError("primary_memory_view_invalidated")
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


async def send_human_memory_response(
    response: dict[str, Any],
    *,
    factory: HumanMemoryHostServiceFactory | None,
    auth: AuthenticatedHostSnapshot,
    send: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    """Keep audit disclosure inside the actual signed connection's final lease.

    A saved delivery is replayable, but never authority to disclose after expiry,
    close or rebinding. Transport failure propagates; it is not a rejected read.
    """
    from deskpet.memory.writer_fence import human_memory_request_boundary

    payload = response.get("payload", {})
    operation = payload.get("operation")
    binding_operations = {"primary.bindings.pending", "primary.bindings.status", "primary.bindings.decide"}
    if not payload.get("ok") or operation not in HUMAN_AUDIT_OPERATIONS | binding_operations:
        await send(response)
        return
    checked = False
    try:
        async with human_memory_request_boundary():
            if factory is None:
                raise HumanMemoryHostServiceError("primary_audit_capability_unavailable")
            if operation in HUMAN_AUDIT_OPERATIONS:
                factory.bind(auth).check_primary_audit_response(operation, payload["result"])
            checked = True
            # Bound the time a slow socket may retain the shared revocation
            # lease. Timeout is an uncertain delivery, never a new read.
            async with asyncio.timeout(5):
                await send(response)
    except Exception as exc:
        if checked:
            raise
        await send(_error(
            response["request_id"],
            str(getattr(exc, "code", "primary_audit_delivery_rejected")),
        ))


async def _dispatch(  # type: ignore[no-untyped-def]
    service, operation: str, request: dict[str, Any], request_id: str
):
    if operation == "primary.open":
        return await service.open_primary()
    if operation in HUMAN_AUDIT_OPERATIONS:
        from deskpet.operation_audit.human_access import HumanAuditError
        from deskpet.memory.writer_fence import require_human_audit_request

        require_human_audit_request()
        fields = {
            "primary.audit.open": {"primary_ref", "open_action_id"},
            "primary.audit.page": {"primary_ref", "audit_ref", "page_action_id", "cursor_ref"},
            "primary.audit.host.page": {
                "primary_ref", "audit_ref", "page_action_id", "section", "cursor_ref", "target_ref",
            },
            "primary.audit.close": {"primary_ref", "audit_ref"},
        }[operation]
        if set(request) != fields:
            raise HumanAuditError("primary_audit_request_invalid")
        return await service.primary_audit(operation, **request)
    if operation in {"primary.memory.list", "primary.memory.graph", "primary.memory.forget"}:
        from deskpet.memory.primary_cognitive_controls import PrimaryCognitiveError

        if operation == "primary.memory.graph":
            if "primary_ref" not in request or not set(request) <= {"primary_ref", "node_limit", "edge_limit"}:
                raise PrimaryCognitiveError("primary_memory_request_invalid")
            return await service.read_primary_memory_graph(**request)
        if operation == "primary.memory.list":
            if "primary_ref" not in request or not set(request) <= {"primary_ref", "limit", "cursor"}:
                raise PrimaryCognitiveError("primary_memory_request_invalid")
            return await service.list_primary_memories(**request)
        if set(request) != {
            "primary_ref", "memory_id", "expected_revision", "expected_content_hash", "action_id"
        }:
            raise PrimaryCognitiveError("primary_memory_request_invalid")
        return await service.forget_primary_memory(**request)
    if operation in {
        "primary.state",
        "primary.messages.page",
        "primary.messages.detail",
    }:
        from deskpet.memory.primary_read_model import PrimaryReadError

        allowed, required = {
            "primary.state": (set(), set()),
            "primary.messages.page": (
                {"primary_ref", "cursor", "limit"},
                {"primary_ref"},
            ),
            "primary.messages.detail": (
                {"primary_ref", "message_ref", "offset", "limit"},
                {"primary_ref", "message_ref"},
            ),
        }[operation]
        if not required <= set(request) or not set(request) <= allowed:
            raise PrimaryReadError("primary_read_request_invalid")
        if operation == "primary.state":
            return await service.read_primary_state(request_id=request_id)
        if operation == "primary.messages.page":
            return await service.read_primary_messages(request_id=request_id, **request)
        return await service.read_primary_message_detail(
            request_id=request_id, **request
        )
    if operation in {"primary.bindings.pending", "primary.bindings.status", "primary.bindings.decide"}:
        from deskpet.memory.primary_workspace_bindings import IDENTITY_FIELDS
        from deskpet.memory.primary_read_model import PrimaryReadError
        fields = ({"primary_ref", "cursor"} if operation == "primary.bindings.pending" else
                  {"primary_ref", "challenge_ref"} if operation == "primary.bindings.status" else IDENTITY_FIELDS | {"decision"})
        required = {"primary_ref"} if operation == "primary.bindings.pending" else fields
        if not required <= set(request) <= fields:
            raise PrimaryReadError("primary_binding_request_invalid")
        if operation == "primary.bindings.pending":
            return await service.list_primary_bindings(**request)
        if operation == "primary.bindings.status":
            return await service.read_primary_binding(**request)
        return await service.respond_primary_binding(**request)
    if operation in {"primary.decisions.list", "primary.decisions.respond"}:
        from deskpet.memory.primary_read_model import PrimaryReadError
        fields = {"primary_ref", "expected_run_ref", "expected_generation"}
        if operation == "primary.decisions.respond":
            fields |= {"decision_id", "nonce", "version", "decision"}
        if set(request) != fields:
            raise PrimaryReadError("primary_decision_request_invalid")
        if operation == "primary.decisions.list":
            return await service.list_primary_decisions(request_id=request_id, **request)
        return await service.respond_primary_decision(request_id=request_id, **request)
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
    if operation == "task_scope.list":
        if not set(request) <= {"limit", "cursor"}:
            raise HumanMemoryHostServiceError("human_memory_request_invalid")
        limit, cursor = request.get("limit", 20), request.get("cursor")
        if (isinstance(limit, bool) or not isinstance(limit, int)
                or (cursor is not None and not isinstance(cursor, str))):
            raise HumanMemoryHostServiceError("human_memory_request_invalid")
        try:
            return await service.list_task_scopes(ListTaskScopesRequest(limit, cursor))
        except (TypeError, ValueError) as exc:
            raise HumanMemoryHostServiceError("human_memory_request_invalid") from exc
    if operation == "task_scope.open_exact":
        # The public HUMAN channel never accepts a client-reported live_probe:
        # freshness evidence is the Host's to produce, not the UI's to assert.
        if not set(request) <= {"scope_ref", "expected_source_hash"}:
            raise HumanMemoryHostServiceError("human_memory_request_invalid")
        return await service.open_task_scope(
            OpenTaskScopeRequest(
                str(request["scope_ref"]),
                None,
                None
                if request.get("expected_source_hash") is None
                else str(request["expected_source_hash"]),
            )
        )
    if operation == "task_scope.view":
        return await service.read_view(
            ReadTaskScopeViewRequest(str(request["scope_ref"]), str(request["kind"]))
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
    if operation == "disclosure.configure":
        fields = {"recipient", "recipient_id", "intended_audience", "purpose", "expected_ref"}
        if set(request) != fields:
            raise HumanMemoryHostServiceError("host_disclosure_configuration_fields_invalid")
        return await service.configure_disclosure(request_id=request_id,
            expected_ref=request["expected_ref"],
            selection={key: request[key] for key in fields - {"expected_ref"}})
    if operation == "disclosure.current":
        if request:
            raise HumanMemoryHostServiceError("host_disclosure_configuration_fields_invalid")
        return await service.current_disclosure_configuration()
    if operation == "queue.enqueue":
        if not set(request) <= {"scope_ref", "delivery_key", "text", "disclosure_binding_ref", "input_declaration"}:
            raise HumanMemoryHostServiceError("human_memory_queue_fields_invalid")
        return await service.enqueue_turn(
            QueueTurnRequest(
                request.get("scope_ref"),
                str(request.get("delivery_key") or request_id),
                str(request.get("text") or ""),
                request.get("disclosure_binding_ref"),
                request.get("input_declaration"),
            )
        )
    if operation == "queue.control":
        if {"expected_run_ref", "expected_generation"} & set(request):
            from deskpet.memory.primary_read_model import PrimaryReadError

            required = {"expected_run_ref", "expected_generation", "control"}
            if not required <= set(request) or not set(request) <= required | {
                "reason"
            }:
                raise PrimaryReadError("primary_exact_control_invalid")
            return await service.control_current_run(
                ExactControlRunRequest(
                    request["expected_run_ref"],
                    request["expected_generation"],
                    request["control"],
                    request.get("reason", "user_requested"),
                    request_id,
                )
            )
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
