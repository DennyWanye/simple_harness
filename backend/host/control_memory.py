"""控制消息：记忆整理、点赞、归档列表、记忆管理转发、人类记忆请求"""

from __future__ import annotations

import logging

from deskpet.memory.human_memory_api import handle_human_memory_command, send_human_memory_response

from host.control_connection import _ControlConnection
from host.memory_messages import _handle_memory_message
from host.runtime import config, service_context
from host.state import host_state
from host.stores_bootstrap import _summarizer_state_db_path

logger = logging.getLogger(__name__)


async def _cc_memory_summarize_now(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：memory_summarize_now。"""
    ws = conn.ws
    payload = raw.get("payload", {}) or {}
    if _summarizer_state_db_path is None or host_state.local_llm is None:
        await ws.send_json(
            {
                "type": "memory_summarize_response",
                "payload": {
                    "ok": False,
                    "error": "summarizer not initialized (state_db or local_llm missing)",
                },
            }
        )
        return


async def _cc_memory_thumbs_up(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：memory_thumbs_up。"""
    ws = conn.ws
    payload = raw.get("payload", {}) or {}
    if not config.memory.v2.feedback_loop:
        await ws.send_json(
            {
                "type": "memory_thumbs_up_response",
                "payload": {"ok": False, "reason": "feedback_loop_disabled"},
            }
        )
        return
    if _summarizer_state_db_path is None:
        await ws.send_json(
            {
                "type": "memory_thumbs_up_response",
                "payload": {"ok": False, "reason": "state_db_unavailable"},
            }
        )
        return


async def _cc_memory_archive_list(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：memory_archive_list。"""
    ws = conn.ws
    payload = raw.get("payload", {}) or {}
    if _summarizer_state_db_path is None:
        await ws.send_json(
            {
                "type": "memory_archive_list_response",
                "payload": {"ok": False, "error": "state_db not initialized"},
            }
        )
        return
    _limit = int(payload.get("limit", 100))
    _filter_sid = payload.get("session_id")
    try:
        # 按需导入：可选依赖
        import aiosqlite

        rows = []
        async with aiosqlite.connect(_summarizer_state_db_path) as _db:
            if _filter_sid:
                cur = await _db.execute(
                    "SELECT id, session_id, role, content, created_at, "
                    "archived_at, archived_into_id "
                    "FROM messages_archive WHERE session_id = ? "
                    "ORDER BY archived_at DESC, created_at DESC LIMIT ?",
                    (_filter_sid, _limit),
                )
            else:
                cur = await _db.execute(
                    "SELECT id, session_id, role, content, created_at, "
                    "archived_at, archived_into_id "
                    "FROM messages_archive "
                    "ORDER BY archived_at DESC, created_at DESC LIMIT ?",
                    (_limit,),
                )
            async for r in cur:
                rows.append(
                    {
                        "id": r[0],
                        "session_id": r[1],
                        "role": r[2],
                        "content": r[3],
                        "created_at": r[4],
                        "archived_at": r[5],
                        "archived_into_id": r[6],
                    }
                )
            await cur.close()
        await ws.send_json(
            {
                "type": "memory_archive_list_response",
                "payload": {"ok": True, "rows": rows, "count": len(rows)},
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("cc_memory_archive_list_import_failed", exc_info=True)
        await ws.send_json(
            {
                "type": "memory_archive_list_response",
                "payload": {"ok": False, "error": str(exc)},
            }
        )


async def _cc_human_memory_request(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：human_memory_request。"""
    ws = conn.ws
    if raw.get("operation") == "queue.enqueue":
        await ws.send_json({
            "type": "human_memory_response", "request_id": raw.get("request_id"),
            "payload": {"ok": False, "error": {"code": "human_memory_operation_unavailable"}},
        })
        return
    companion_challenge = conn.companion_challenge
    human_memory_control_binding = conn.human_memory_control_binding

    try:
        _hm_auth = human_memory_control_binding.authenticate(
            host_state.companion_control_ingress, companion_challenge
        )
    except Exception as exc:
        logger.debug("cc_human_memory_request_authenticate_failed", exc_info=True)
        await ws.send_json(
            {
                "type": "human_memory_response",
                "request_id": str(raw.get("request_id") or ""),
                "payload": {
                    "ok": False,
                    "error": {"code": getattr(exc, "code", "human_memory_connection_unbound")},
                },
            }
        )
        return

    with human_memory_control_binding.request_scope(
        host_state.companion_control_ingress, companion_challenge
    ):
        _hm_response = await handle_human_memory_command(
            raw,
            factory=service_context.get("human_memory_host_service_factory"),
            auth=_hm_auth,
            binding_append=service_context.get("human_memory_binding_append_authority"),
            recovery=service_context.get("human_memory_recovery_lifecycle"),
            scheduler_wake=service_context.get("human_memory_foreground_scheduler_wake"),
        )
        if _hm_response is not None:
            await send_human_memory_response(
                _hm_response,
                factory=service_context.get("human_memory_host_service_factory"),
                auth=_hm_auth,
                send=ws.send_json,
            )


async def _cc_memory_list(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：memory_list、memory_delete、memory_clear、memory_export。"""
    ws = conn.ws
    session_id = conn.session_id
    await _handle_memory_message(ws, session_id, msg_type, raw.get("payload", {}) or {})
