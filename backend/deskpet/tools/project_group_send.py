# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Explicitly-confirmed project-group broadcast tool.

The ToolSpec is always present so capability risk analysis sees the real
``external_send`` topology.  A physical transport must be bound by the host;
without one the handler returns a typed unavailable result and performs no
send.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Mapping, Protocol

from deskpet.types.task_grants import ResourceSelector



class ProjectGroupTransport(Protocol):
    """Host-owned physical delivery adapter."""

    def send_all_project_groups(
        self, *, content: str, idempotency_key: str
    ) -> Mapping[str, Any]: ...


_transport_lock = threading.Lock()
_transport: ProjectGroupTransport | None = None


def configure_project_group_transport(
    transport: ProjectGroupTransport | None,
) -> None:
    """Atomically bind or remove the physical transport."""

    global _transport
    with _transport_lock:
        _transport = transport


def _resource_scope(
    args: Mapping[str, Any], _context: Any
) -> tuple[ResourceSelector, ...]:
    if args.get("target_scope") != "all_project_groups":
        return ()
    return (
        ResourceSelector(
            "application",
            "deskpet:all_project_groups",
            ("send",),
        ),
    )


def _error(code: str, message: str, *, retriable: bool) -> str:
    return json.dumps(
        {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "retriable": retriable,
            },
            "physical_send_count": 0,
        },
        ensure_ascii=False,
    )


def _handle_project_group_send(args: dict[str, Any], task_id: str) -> str:
    if args.get("target_scope") != "all_project_groups":
        return _error(
            "project_group_target_invalid",
            "target_scope must be all_project_groups",
            retriable=False,
        )
    content = args.get("content")
    if not isinstance(content, str) or not content.strip():
        return _error(
            "project_group_content_required",
            "content must be a non-empty string",
            retriable=False,
        )
    with _transport_lock:
        transport = _transport
    if transport is None:
        return _error(
            "project_group_transport_unavailable",
            "No project-group transport is connected.",
            retriable=True,
        )
    try:
        raw = transport.send_all_project_groups(
            content=content.strip(),
            idempotency_key=str(task_id or ""),
        )
    except Exception as exc:  # noqa: BLE001 - transport failure is a typed outcome
        return json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "project_group_transport_failed",
                    "message": type(exc).__name__,
                    "retriable": False,
                },
                "delivery_state": "unknown",
            },
            ensure_ascii=False,
        )
    result = dict(raw)
    if result.get("ok") is not True:
        return _error(
            "project_group_transport_rejected",
            str(result.get("message") or "Project-group transport rejected delivery."),
            retriable=False,
        )
    return json.dumps(
        {
            "ok": True,
            "delivery_ref": str(result.get("delivery_ref") or ""),
            "physical_send_count": int(result.get("physical_send_count") or 1),
        },
        ensure_ascii=False,
    )


_SCHEMA = {
    "name": "project_group_send",
    "description": (
        "Send one completed message to all connected project groups. "
        "This is an irreversible external action and always requires a "
        "separate one-shot action confirmation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target_scope": {
                "type": "string",
                "enum": ["all_project_groups"],
                "description": "The exact broadcast target.",
            },
            "content": {
                "type": "string",
                "minLength": 1,
                "description": "The final message to send.",
            },
        },
        "required": ["target_scope", "content"],
        "additionalProperties": False,
    },
}


def register_static_tools(registry) -> None:
    registry.register(
        "project_group_send", "external_action", _SCHEMA,
        _handle_project_group_send, permission_category="external_action",
        source="builtin", dangerous=True, concurrency_safe=False,
        resource_scope_resolver=_resource_scope,
        resource_scope_resolver_id="builtin:project_group_send:all_project_groups",
        resource_scope_resolver_version="v1",
        outcome_parser_id="json_error_envelope_v1",
    )


__all__ = [
    "ProjectGroupTransport",
    "configure_project_group_transport",
    "register_static_tools",
]
