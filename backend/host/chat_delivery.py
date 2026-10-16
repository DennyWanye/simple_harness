"""聊天投递与广播：控制通道广播、按对端分组投递最终回复与错误、新会话回显、聊天对端分组与会话解析。"""

from __future__ import annotations

import asyncio

from deskpet.session.task_scope import TaskScopeDecision, task_session_manager
from fastapi import WebSocket

from host.connections import _chat_peer_groups, _control_connections
from host.runtime import logger


async def _broadcast_control(msg: dict) -> None:
    """Best-effort fan-out to every control websocket."""
    for _sid, _ws in list(_control_connections.items()):
        try:
            await _ws.send_json(msg)
        except Exception as exc:  # noqa: BLE001
            logger.debug("control_broadcast_failed sid=%s err=%s", _sid, exc, exc_info=True)


def _initial_chat_peer_group(transport_sid: str) -> str:
    if transport_sid in {"default", "message-panel-main"}:
        return task_session_manager.active_sid("default")
    return task_session_manager.active_sid(transport_sid)


def _register_chat_peer(transport_sid: str) -> str:
    sid = transport_sid or "default"
    task_session_manager.register_peer(sid)
    group = _chat_peer_groups.get(sid)
    if group is None:
        group = _initial_chat_peer_group(sid)
        _chat_peer_groups[sid] = group
    return group


def _remap_chat_peer_group(transport_sid: str, effective_sid: str) -> None:
    sid = transport_sid or "default"
    old_group = _chat_peer_groups.get(sid, _initial_chat_peer_group(sid))
    _chat_peer_groups.setdefault(sid, old_group)
    for peer_sid, group in list(_chat_peer_groups.items()):
        if group == old_group:
            _chat_peer_groups[peer_sid] = effective_sid
    task_session_manager.remap_peer_group(sid, effective_sid)


def _resolve_chat_source_session(
    transport_sid: str,
    requested_session_id: object,
) -> str:
    """Resolve the user-visible source Session for a new conversation.

    An empty ChatView sends no session id. The WebSocket transport id is a
    stable window identity (often ``message-panel-main``), not a model-binding
    authority. In that case inherit from the transport's currently mapped
    peer group so a new topic follows the model the user was actually using.
    """

    requested = str(requested_session_id or "").strip()
    if requested:
        return requested
    transport = str(transport_sid or "default")
    mapped = str(_chat_peer_groups.get(transport) or "").strip()
    return mapped or _initial_chat_peer_group(transport)


def _resolve_chat_task_scope(
    *,
    base_sid: str,
    text: str,
    payload: dict,
) -> TaskScopeDecision:
    payload = payload or {}
    force_l2 = bool(payload.get("force_l2")) or (text or "").startswith("/continue")
    decision = task_session_manager.resolve(
        base_sid,
        text or "",
        explicit_new=bool(payload.get("new_session")),
        force_l2=force_l2,
    )
    return decision


_PEER_BROADCAST_TIMEOUT_S = 1.0
_GLOBAL_BLOCKING_UI_EVENTS = frozenset(
    {
        "permission_request",
        "external_wait_request",
        "project_directory_request",
        "clarification_request",
    }
)


async def _broadcast_default_chat_peers(originator_ws: WebSocket | None, msg: dict) -> None:
    """2026-05-28 — 多窗口共享 "default" 会话同步广播。

    主桌宠窗口 (`session_id=default`) 和左侧消息面板窗口
    (`session_id=message-panel-main`) 是两条独立的控制通道，但都展示
    同一个 "default" 聊天会话。原有 chat_v2_* 事件只发回给原始 WS，
    导致 panel 看不到主桌宠发出的对话。

    本 helper 在事件 `payload.session_id == "default"` 时把消息 fan-out
    给所有 *其它* 控制通道（跳过 originator 避免重复 push）。
    其它会话（per-tile code-*）保持单发不变。
    Best-effort：peer 关闭/出错只 swallow。
    """
    if not isinstance(msg, dict):
        return
    payload = msg.get("payload") or {}
    if not isinstance(payload, dict):
        return
    payload_sid = payload.get("session_id") or payload.get("new_sid")
    if not payload_sid:
        return
    # Blocking decisions are owned by the product shell, not by whichever
    # conversation tile happened to launch the Run.  The pet window hosts the
    # permission/clarification/external-wait overlays, while a task can be
    # launched from the separate message-panel websocket.  Deliver these
    # events to every other product control peer so the user can always act on
    # them.  The originator already received the frame in ``_send_both``.
    global_blocking_event = str(msg.get("type") or "") in (_GLOBAL_BLOCKING_UI_EVENTS)
    for _peer_sid, _peer_ws in list(_control_connections.items()):
        if _peer_ws is originator_ws:
            continue
        if not global_blocking_event:
            _main_group = _chat_peer_groups.get(_peer_sid, _initial_chat_peer_group(_peer_sid))
            _manager_group = task_session_manager.peer_group(_peer_sid)
            if _main_group != payload_sid and _manager_group != payload_sid:
                continue
        try:
            await asyncio.wait_for(
                _peer_ws.send_json(msg),
                timeout=_PEER_BROADCAST_TIMEOUT_S,
            )
        except TimeoutError:
            logger.debug(
                "default_chat_peer_broadcast_timeout sid=%s timeout_s=%.1f",
                _peer_sid,
                _PEER_BROADCAST_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "default_chat_peer_broadcast_failed sid=%s err=%s",
                _peer_sid,
                exc,
                exc_info=True,
            )


async def _send_chat_final(
    originator_ws: WebSocket,
    msg: dict,
    *,
    session_id: str,
    request_id: str = "",
) -> None:
    """Deliver a terminal frame without one stale socket blocking its peers."""
    payload = msg.get("payload") if isinstance(msg, dict) else {}
    text_len = len(str((payload or {}).get("text") or ""))
    run_id = payload.get("run_id", "")
    task_scope_id = payload.get("task_scope_id", "")

    logger.info(
        "chat_v2_final_send_started sid=%s request_id=%s run_id=%s task_scope_id=%s chars=%d",
        session_id,
        request_id,
        run_id,
        task_scope_id,
        text_len,
    )

    async def _send_originator() -> None:
        try:
            await asyncio.wait_for(
                originator_ws.send_json(msg),
                timeout=_PEER_BROADCAST_TIMEOUT_S,
            )
            logger.info(
                "chat_v2_final_send_completed sid=%s request_id=%s run_id=%s chars=%d",
                session_id,
                request_id,
                run_id,
                text_len,
            )
        except TimeoutError:
            logger.warning(
                "chat_v2_final_send_timeout sid=%s request_id=%s run_id=%s timeout_s=%.1f chars=%d",
                session_id,
                request_id,
                run_id,
                _PEER_BROADCAST_TIMEOUT_S,
                text_len,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "chat_v2_final_send_failed sid=%s request_id=%s run_id=%s err=%s err_type=%s",
                session_id,
                request_id,
                run_id,
                exc,
                type(exc).__name__,
                exc_info=True,
            )

    await asyncio.gather(
        _send_originator(),
        _broadcast_default_chat_peers(originator_ws, msg),
    )


async def _send_chat_error(
    originator_ws: WebSocket,
    msg: dict,
    *,
    session_id: str,
    request_id: str = "",
) -> None:
    """Deliver a failed terminal frame to every UI peer for the session."""
    logger.info(
        "chat_v2_error_send_started sid=%s request_id=%s",
        session_id,
        request_id,
    )

    async def _send_originator() -> None:
        try:
            await asyncio.wait_for(
                originator_ws.send_json(msg),
                timeout=_PEER_BROADCAST_TIMEOUT_S,
            )
            logger.info(
                "chat_v2_error_send_completed sid=%s request_id=%s",
                session_id,
                request_id,
            )
        except TimeoutError:
            logger.warning(
                "chat_v2_error_send_timeout sid=%s request_id=%s timeout_s=%.1f",
                session_id,
                request_id,
                _PEER_BROADCAST_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "chat_v2_error_send_failed sid=%s request_id=%s err=%s",
                session_id,
                request_id,
                exc,
                exc_info=True,
            )

    await asyncio.gather(
        _send_originator(),
        _broadcast_default_chat_peers(originator_ws, msg),
    )


async def _send_new_session_origin_user_echo(
    originator_ws: WebSocket,
    session_id: str,
    text: str,
) -> None:
    """Echo the first draft message back after a new session id is known."""
    if not (text or "").strip():
        return
    try:
        await originator_ws.send_json(
            {
                "type": "chat_v2_user_echo",
                "payload": {"session_id": session_id, "text": text},
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("new_session_origin_echo_failed sid=%s err=%s", session_id, exc, exc_info=True)
