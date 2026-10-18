"""控制消息：会话列表、删除、改名、加载消息、项目会话、上下文用量与明细、斜杠命令"""

from __future__ import annotations

import json
import json as _load_json
from typing import Any

from deskpet.commands import dispatch_slash_command
from deskpet.companion.skills import PreparedSkillInvocationScopeV1
from deskpet.memory.primary_authority import assert_not_primary_authority
from deskpet.session.project_protocol import (
    handle_project_session_command as _handle_project_session_command,
)
from deskpet.tools.public_projection import project_public_tool_calls, project_public_tool_result

from host.chat_delivery import _remap_chat_peer_group, _resolve_chat_source_session
from host.context_usage import _handle_context_breakdown_request, _read_context_usage_authority
from host.control_connection import _ControlConnection
from host.harness_chat import _launch_product_harness_chat
from host.history_events import (
    _attach_workflow_history_events,
    _bind_companion_inbox_route,
)
from host.routes_commands import _current_slash_skill_catalog
from host.runtime import config, logger, service_context
from host.state import host_state


async def _cc_context_usage_request(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：context_usage_request。"""
    ws = conn.ws
    session_id = conn.session_id
    payload = raw.get("payload", {}) or {}
    target_sid = payload.get("session_id") or session_id
    try:
        authoritative = await _read_context_usage_authority(target_sid)
        await ws.send_json({"type": "context_usage", "payload": authoritative})
    except Exception as exc:  # noqa: BLE001
        logger.debug("context_usage_request_failed err=%s", exc, exc_info=True)


async def _cc_context_breakdown_request(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：context_breakdown_request。"""
    ws = conn.ws
    session_id = conn.session_id
    payload = raw.get("payload", {}) or {}
    try:
        await _handle_context_breakdown_request(
            ws,
            payload,
            owner_session_id=_resolve_chat_source_session(session_id, None),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("context_breakdown_send_failed err=%s", exc, exc_info=True)


async def _cc_project_preview_register(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：project_preview_register、project_register、session_create、project_catalog_page、project_sessions_page、project_inspect、project_relocate。"""
    ws = conn.ws

    _project_response = await _handle_project_session_command(
        raw,
        bindings=service_context.get("project_binding_service"),
        creation=service_context.get("session_creation_service"),
    )
    if _project_response is not None:
        await ws.send_json(_project_response)


async def _cc_sessions_list(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：sessions_list。"""
    ws = conn.ws
    await ws.send_json(
        {
            "type": "sessions_list_error",
            "request_id": raw.get("request_id"),
            "payload": {
                "code": "protocol_replaced",
                "error": "use project_catalog_page and project_sessions_page",
            },
        }
    )


async def _cc_session_delete(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：session_delete。"""

    ws = conn.ws
    _sd_payload = raw.get("payload", {}) or {}
    _sd_sid = _sd_payload.get("session_id") or ""
    _sd_sdb = service_context.get("session_db")
    _sd_ok = False
    _sd_code = None
    if _sd_sdb is not None and _sd_sid:
        try:
            await assert_not_primary_authority(_sd_sdb._db_path, _sd_sid)
            _sd_creation = service_context.get("session_creation_service")
            if _sd_creation is not None:
                await _sd_creation.mark_deleted(_sd_sid)
            else:
                await _sd_sdb.clear(_sd_sid)
            _sd_ok = True
            logger.info("session_deleted sid=%s", _sd_sid)
        except Exception as _sd_exc:  # noqa: BLE001
            _sd_code = getattr(_sd_exc, "code", None)
            logger.warning(
                "session_delete_failed",
                error=str(_sd_exc),
                session_id=_sd_sid,
                exc_info=True,
            )
    await ws.send_json(
        {
            "type": "session_deleted",
            "payload": {
                "session_id": _sd_sid,
                "ok": _sd_ok,
                **({"code": _sd_code} if _sd_code else {}),
            },
        }
    )


async def _cc_session_rename(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：session_rename。"""

    ws = conn.ws
    _sr_payload = raw.get("payload", {}) or {}
    _sr_sid = _sr_payload.get("session_id") or ""
    _sr_title = _sr_payload.get("title") or ""
    _sr_sdb = service_context.get("session_db")
    _sr_ok = False
    _sr_stored = ""
    _sr_code = None
    if _sr_sdb is not None and _sr_sid:
        try:
            await assert_not_primary_authority(_sr_sdb._db_path, _sr_sid)
            _sr_stored = await _sr_sdb.set_session_title(_sr_sid, _sr_title)
            _sr_ok = True
            logger.info("session_renamed sid=%s title_len=%d", _sr_sid, len(_sr_stored))
        except Exception as _sr_exc:  # noqa: BLE001
            _sr_code = getattr(_sr_exc, "code", None)
            logger.warning(
                "session_rename_failed",
                error=str(_sr_exc),
                session_id=_sr_sid,
                exc_info=True,
            )
    await ws.send_json(
        {
            "type": "session_renamed",
            "payload": {
                "session_id": _sr_sid,
                "title": _sr_stored,
                "ok": _sr_ok,
                **({"code": _sr_code} if _sr_code else {}),
            },
        }
    )


async def _cc_session_messages_load(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：session_messages_load。"""

    ws = conn.ws
    session_id = conn.session_id
    payload = raw.get("payload", {}) or {}
    target_sid = payload.get("session_id") or session_id
    limit = int(payload.get("limit") or 200)
    _sml_sdb = service_context.get("session_db")
    if _sml_sdb is not None:
        try:
            await assert_not_primary_authority(_sml_sdb._db_path, str(target_sid))
        except Exception as _sml_exc:  # noqa: BLE001
            _sml_code = getattr(_sml_exc, "code", None)
            if _sml_code:
                await ws.send_json(
                    {
                        "type": "session_messages_error",
                        "payload": {
                            "ok": False,
                            "session_id": target_sid,
                            "code": _sml_code,
                        },
                    }
                )
                return
            raise
    # Loading a transcript is also the product shell's explicit
    # declaration of which conversation this transport currently
    # presents.  Rebind the peer group before the consistency
    # gate: a recovered terminal may be committed while the
    # websocket is reconnecting, and its live notification must
    # route back to this selected history session rather than the
    # transport's startup/default topic.
    _remap_chat_peer_group(session_id, str(target_sid))
    sdb = service_context.get("session_db")
    msgs: list = []
    if sdb is not None:
        try:
            try:
                _history_identity = host_state.companion_identity_gate.freeze()
                await _bind_companion_inbox_route(
                    target_sid,
                    _history_identity,
                    create_if_absent=False,
                )
            except Exception as _history_route_exc:  # noqa: BLE001
                logger.debug(
                    "companion_history_route_unchanged sid=%s err=%s",
                    target_sid,
                    type(_history_route_exc).__name__,
                    exc_info=True,
                )
            rows = await sdb.get_messages(target_sid, limit=limit)
            # P6 bugfix 2026-05-14: 历史还原 — 之前过滤了
            # role not in (user,assistant) 和 empty content,
            # 导致 tool_call (role='assistant' content=''
            # tool_calls=[...]) 和 tool_result (role='tool')
            # 全部被丢，UI 重启后只剩 user 气泡。现在全部
            # 返回，前端 ws.ts 决定怎么渲染。
            msgs = []
            _history_tool_names: dict[str, str] = {}
            for _history_row in rows:
                _history_calls = _history_row.get("tool_calls")
                if not _history_calls:
                    continue
                try:
                    _history_calls = (
                        json.loads(_history_calls)
                        if isinstance(_history_calls, str)
                        else _history_calls
                    )
                    if isinstance(_history_calls, list):
                        for _history_call in _history_calls:
                            if not isinstance(_history_call, dict):
                                continue
                            _history_function = _history_call.get("function")
                            if isinstance(_history_function, dict):
                                _history_tool_names[str(_history_call.get("id") or "")] = str(
                                    _history_function.get("name") or ""
                                )
                except Exception:  # noqa: BLE001 - 某条历史消息解析出错时跳过
                    logger.debug("session_history_tool_names_failed", exc_info=True)
                    continue
            for r in rows:
                if str(r.get("projection_kind") or "") == "companion_event":
                    # Companion rows are hydrated through the
                    # owner-fenced visibility overlay below, never
                    # as ordinary assistant text.
                    continue
                _row_role = r.get("role") or ""
                if _row_role not in ("user", "assistant", "tool"):
                    continue  # 仍跳过 system 等
                _entry: dict[str, Any] = {
                    "id": str(r.get("workflow_event_id") or r.get("id") or ""),
                    "role": _row_role,
                    "text": r.get("content") or "",
                    "ts": float(r.get("created_at") or 0) * 1000,
                    "projection_kind": str(r.get("projection_kind") or ""),
                    "workflow_event_id": str(r.get("workflow_event_id") or ""),
                }
                if r.get("root_run_id"):
                    _entry["run_id"] = str(r["root_run_id"])
                if r.get("task_scope_id"):
                    _entry["task_scope_id"] = str(r["task_scope_id"])
                # tool_calls JSON (assistant 行调工具时)
                _tcs_raw = r.get("tool_calls")
                if _tcs_raw:
                    try:
                        _entry["tool_calls"] = project_public_tool_calls(
                            _load_json.loads(_tcs_raw) if isinstance(_tcs_raw, str) else _tcs_raw
                        )
                    except Exception:  # noqa: BLE001 - 工具调用解析出错时不带这一项
                        logger.debug("session_history_tool_calls_failed", exc_info=True)
                # tool_call_id (tool 行的回指)
                _tcid = r.get("tool_call_id")
                if _tcid:
                    _entry["tool_call_id"] = _tcid
                    if _row_role == "tool":
                        _public_history_result = project_public_tool_result(
                            _history_tool_names.get(str(_tcid), ""),
                            r.get("content") or "",
                        )
                        _entry["text"] = (
                            json.dumps(_public_history_result, ensure_ascii=False)
                            if not isinstance(_public_history_result, str)
                            else _public_history_result
                        )
                msgs.append(_entry)

            await _attach_workflow_history_events(
                rows=rows,
                messages=msgs,
                session_id=target_sid,
                session_db=sdb,
                execution_uow=service_context.get("execution_uow"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "session_messages_consistency_failed",
                error=str(exc),
                session_id=target_sid,
                exc_info=True,
            )
            await ws.send_json(
                {
                    "type": "session_messages_error",
                    "payload": {
                        "session_id": target_sid,
                        "code": "product_view_not_current",
                        "error": ("会话记录暂时无法确认是最新状态，请稍后重试。"),
                    },
                }
            )
            return
    # 带回 awaiting plan（不塞进被白名单 3659 过滤的 messages
    # 流，单独挂 payload.plan）。前端 rehydration 据此重建 plan card +
    # [执行]/[取消] 栏。只在 awaiting 时附；否则字段缺省 → 旧行为不变。
    _plan_payload: dict[str, Any] | None = None
    if sdb is not None:
        try:
            _sp = await sdb.get_session_plan(target_sid)
            if _sp is not None and _sp.get("awaiting"):
                _plan_payload = {
                    "rationale": _sp.get("rationale") or "",
                    "steps": _sp.get("steps") or [],
                    "target_directory": _sp.get("target_directory"),
                    "action_categories": _sp.get("action_categories") or [],
                    "auto_confirmed": bool(_sp.get("auto_confirmed")),
                    "awaiting": True,
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "session_plan_load_failed sid=%s err=%s",
                target_sid,
                exc,
                exc_info=True,
            )
    _msgs_payload: dict[str, Any] = {
        "session_id": target_sid,
        "messages": msgs,
    }
    if _plan_payload is not None:
        _msgs_payload["plan"] = _plan_payload
    await ws.send_json(
        {
            "type": "session_messages_response",
            "payload": _msgs_payload,
        }
    )


async def _cc_slash_command(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：slash_command。"""
    ws = conn.ws
    session_id = conn.session_id
    payload = raw.get("payload", {}) or {}
    cmd_name = (payload.get("command") or "").lstrip("/")
    cmd_args = payload.get("args") or ""
    target_sid = payload.get("session_id") or session_id
    _slash_enabled = bool(
        getattr(
            getattr(config, "features", None),
            "slash_commands",
            False,
        )
    )
    if not _slash_enabled:
        await ws.send_json(
            {
                "type": "slash_command_result",
                "payload": {
                    "command": cmd_name,
                    "session_id": target_sid,
                    "result": {
                        "type": "error",
                        "message": "slash_commands feature disabled "
                        "(set [features] slash_commands = true)",
                    },
                },
            }
        )
    else:
        try:
            _slash_skill_loader = await _current_slash_skill_catalog()
            _slash_skill_catalog = _slash_skill_loader
            _slash_result = await dispatch_slash_command(
                cmd_name,
                cmd_args,
                target_sid,
                skill_loader=_slash_skill_loader,
                skill_catalog=_slash_skill_catalog,
            )
        except Exception as _slash_exc:  # noqa: BLE001
            logger.warning(
                "slash_command dispatch failed: %s",
                _slash_exc,
                exc_info=True,
            )
            _slash_result = {
                "type": "error",
                "message": f"dispatch error: {_slash_exc}",
            }
        if _slash_result.get("type") == ("instruction_activation_request"):
            _slash_text = str(_slash_result.get("user_text") or "").strip()
            if not _slash_text:
                raise RuntimeError("slash instruction activation is empty")

            _slash_scope = PreparedSkillInvocationScopeV1.from_dict(
                _slash_result.get("prepared_skill_scope") or {}
            )
            _slash_arguments = tuple(str(item) for item in (_slash_result.get("arguments") or ()))
            _launch_product_harness_chat(
                ws,
                _slash_text,
                target_sid,
                client_request_id=(
                    str(payload.get("request_id") or raw.get("request_id") or "").strip() or None
                ),
                client_turn_id=(str(payload.get("turn_id") or "").strip() or None),
                prepared_skill_scope=_slash_scope,
                skill_arguments=_slash_arguments,
            )
            _slash_result = {
                "type": "accepted",
                "route": "product_harness",
                "skill_name": cmd_name,
                "prepared_skill_scope": _slash_result.get("prepared_skill_scope"),
            }
        await ws.send_json(
            {
                "type": "slash_command_result",
                "payload": {
                    "command": cmd_name,
                    "session_id": target_sid,
                    "result": _slash_result,
                },
            }
        )
