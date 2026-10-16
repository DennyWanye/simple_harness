"""控制通道的记忆管理消息与设置页技能安装适配"""

from __future__ import annotations

from deskpet.capabilities.skill_install_ui import (
    ProjectSkillInstallUIAdapter,
    ProjectSkillInstallUIError,
    TrustedGlobalInstallContext,
)
from fastapi import WebSocket

from host.runtime import logger, service_context


async def _handle_memory_message(
    ws: WebSocket, session_id: str, msg_type: str, payload: dict
) -> None:
    # SessionDB is the production MemoryStore implementation.
    store = service_context.get("session_db")
    if store is None:
        await ws.send_json(
            {
                "type": "error",
                "payload": {"message": "memory store not registered"},
            }
        )
        return

    try:
        if msg_type == "memory_list":
            # Scope defaults to the current session; ``scope: "all"`` returns
            # every session's turns (export-style). The UI asks per-session.
            scope = payload.get("scope") or "session"
            target_session = None if scope == "all" else payload.get("session_id", session_id)
            limit = payload.get("limit")
            turns = await store.list_turns(target_session, limit)
            await ws.send_json(
                {
                    "type": "memory_list_response",
                    "payload": {
                        "scope": scope,
                        "session_id": target_session,
                        "turns": [
                            {
                                "id": t.id,
                                "session_id": t.session_id,
                                "role": t.role,
                                "content": t.content,
                                "created_at": t.created_at,
                            }
                            for t in turns
                        ],
                    },
                }
            )

        elif msg_type == "memory_delete":
            turn_id = payload.get("id")
            if not isinstance(turn_id, int):
                await ws.send_json(
                    {
                        "type": "error",
                        "payload": {"message": "memory_delete requires integer id"},
                    }
                )
                return
            deleted = await store.delete_turn(turn_id)
            await ws.send_json(
                {
                    "type": "memory_delete_ack",
                    "payload": {"id": turn_id, "deleted": deleted},
                }
            )

        elif msg_type == "memory_clear":
            scope = payload.get("scope") or "session"
            if scope == "all":
                removed = await store.clear_all()
                await ws.send_json(
                    {
                        "type": "memory_clear_ack",
                        "payload": {"scope": "all", "removed": removed},
                    }
                )
            else:
                target_session = payload.get("session_id", session_id)
                await store.clear(target_session)
                await ws.send_json(
                    {
                        "type": "memory_clear_ack",
                        "payload": {"scope": "session", "session_id": target_session},
                    }
                )

        elif msg_type == "memory_export":
            # Dump everything — user asked for their data, they get all of it.
            turns = await store.list_turns(None, None)
            sessions = await store.list_sessions()
            await ws.send_json(
                {
                    "type": "memory_export_response",
                    "payload": {
                        "exported_at": __import__("time").time(),
                        "sessions": [
                            {
                                "session_id": s.session_id,
                                "turn_count": s.turn_count,
                                "last_message_at": s.last_message_at,
                            }
                            for s in sessions
                        ],
                        "turns": [
                            {
                                "id": t.id,
                                "session_id": t.session_id,
                                "role": t.role,
                                "content": t.content,
                                "created_at": t.created_at,
                            }
                            for t in turns
                        ],
                    },
                }
            )
    except AttributeError as exc:
        # Inner store without list_turns/delete_turn/list_sessions/clear_all —
        # surface a clean error instead of a 500 on the wire.
        logger.warning("memory_admin_unsupported", error=str(exc), type=msg_type)
        await ws.send_json(
            {
                "type": "error",
                "payload": {"message": f"{msg_type} not supported by active memory store"},
            }
        )


def _skill_install_error_payload(exc: Exception) -> dict[str, Any]:
    code = str(getattr(exc, "code", "") or "skill_install_failed")
    if isinstance(exc, ValueError) and str(exc).startswith("skill_install_"):
        code = str(exc)
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": str(exc),
            "retryable": bool(getattr(exc, "retryable", False)),
        },
        # Compatibility text for older clients. It mirrors the structured
        # message and carries no authority or install state.
        "error_code": code,
        "message": str(exc),
    }


async def _settings_skill_install_adapter(ws: WebSocket):

    service = service_context.get("project_skill_install_service")
    if service is None:
        raise ProjectSkillInstallUIError(
            "skill_install_service_unavailable",
            "The managed global Skill installer is unavailable.",
            retryable=True,
        )
    authorizer_factory = service_context.get("project_skill_install_settings_authorizer")
    bind_request = getattr(authorizer_factory, "bind_control_request", None)
    if not callable(bind_request):
        raise ProjectSkillInstallUIError(
            "settings_install_authorization_unavailable",
            "Settings cannot establish trusted Host window authority in this build.",
            retryable=True,
        )
    # The authorizer owns request/window authentication. Query parameters and
    # UI payload fields are deliberately not supplied as identity inputs.
    bound_authorizer = await bind_request(ws)
    global_owner_key = str(getattr(service, "global_owner_key", "") or "").strip()
    if not global_owner_key:
        raise ProjectSkillInstallUIError(
            "settings_install_authorization_unavailable",
            "Settings cannot resolve the trusted global Skill owner.",
            retryable=True,
        )
    principal_id = str(getattr(bound_authorizer, "principal_id", "") or "").strip()
    if not principal_id:
        raise ProjectSkillInstallUIError(
            "settings_install_authorization_unavailable",
            "Settings cannot resolve an authenticated Host principal.",
            retryable=True,
        )
    # This is deliberately a separate Host adapter. The legacy PermissionGate
    # returns only an in-memory decision and cannot mint the durable receipt
    # required by GlobalSkillInstallService.confirm/cancel.
    return (
        ProjectSkillInstallUIAdapter(service=service, authorizer=bound_authorizer),
        TrustedGlobalInstallContext(
            principal_id=principal_id,
            global_owner_key=global_owner_key,
        ),
        principal_id,
    )
