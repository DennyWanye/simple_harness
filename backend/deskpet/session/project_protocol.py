# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Bounded WebSocket protocol adapter for Project-scoped Sessions."""
from __future__ import annotations

from typing import Any

from deskpet.session.project_binding import (
    ProjectBindingService,
    ProjectSessionError,
    SessionCreationService,
)


PROJECT_SESSION_COMMANDS = frozenset({
    "project_preview_register", "project_register", "session_create",
    "project_catalog_page", "project_sessions_page", "project_inspect",
    "project_relocate",
})


async def handle_project_session_command(
    raw: dict[str, Any], *, bindings: ProjectBindingService,
    creation: SessionCreationService,
) -> dict[str, Any] | None:
    command = str(raw.get("type") or "")
    if command not in PROJECT_SESSION_COMMANDS:
        return None
    request_id = str(raw.get("request_id") or "").strip()
    response_type = f"{command}_response"
    if not request_id:
        return {"type": response_type, "request_id": raw.get("request_id"),
                "payload": {"ok": False, "error": {"code": "invalid_request", "message": "request_id is required"}}}
    payload = raw.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        if command == "project_preview_register":
            preview = await bindings.preview_registration(
                str(payload.get("selected_path") or ""), str(payload.get("mode") or "git_root")
            )
            result = {"preview": preview.public_dict()}
        elif command == "project_register":
            project, created = await bindings.register_project(
                str(payload.get("selected_path") or ""), str(payload.get("mode") or "git_root"),
                payload.get("display_name"),
            )
            result = {"project": bindings.project_descriptor(project), "created": created}
        elif command == "session_create":
            result = await creation.create_conversation_session(
                request_id=request_id, project_id=payload.get("project_id"),
                source_session_id=payload.get("source_session_id"),
                execution_kind=str(payload.get("execution_kind") or "project_root"),
                execution_root=payload.get("execution_root"),
            )
        elif command == "project_catalog_page":
            result = await bindings.list_project_page(
                cursor=payload.get("cursor"), limit=payload.get("limit"),
                pinned_project_id=payload.get("pinned_project_id"),
            )
        elif command == "project_sessions_page":
            result = await bindings.list_session_page(
                scope_kind=str(payload.get("scope_kind") or ""), project_id=payload.get("project_id"),
                cursor=payload.get("cursor"), limit=payload.get("limit"),
                pinned_session_id=payload.get("pinned_session_id"),
            )
        elif command == "project_inspect":
            result = await bindings.inspect_project(str(payload.get("project_id") or ""))
        else:
            project = await bindings.relocate_project(
                str(payload.get("project_id") or ""), str(payload.get("new_path") or ""),
                int(payload.get("expected_project_revision") or 0),
            )
            result = {"project": bindings.project_descriptor(project)}
        return {"type": response_type, "request_id": request_id, "payload": {"ok": True, **result}}
    except ProjectSessionError as exc:
        return {"type": response_type, "request_id": request_id,
                "payload": {"ok": False, "error": {"code": exc.code, "message": str(exc)}}}
    except (TypeError, ValueError):
        return {"type": response_type, "request_id": request_id,
                "payload": {"ok": False, "error": {"code": "invalid_request", "message": "invalid request payload"}}}


__all__ = ["PROJECT_SESSION_COMMANDS", "handle_project_session_command"]
