"""控制消息：运行状态查询、授权与受保护路径决定、外部等待与项目目录回复、澄清、计划确认、中断"""

from __future__ import annotations

from deskpet.execution.contracts import ExecutionError
from deskpet.permissions import protected_paths as _protected_paths

from host.chat_delivery import _broadcast_default_chat_peers
from host.control_connection import _ControlConnection
from host.harness_chat import _resolve_selected_project_directory
from host.harness_runs import (
    _authoritative_sdk_run_projection,
    _cancel_product_harness_run,
    _signal_product_harness_decision,
)
from host.routes_voice import _voice_transports
from host.runtime import logger, service_context
from host.state import host_state


async def _cc_session_run_status_query(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：session_run_status_query、session_health_check。"""
    ws = conn.ws
    session_id = conn.session_id
    payload = raw.get("payload", {}) or {}
    target_sid = str(payload.get("session_id") or session_id)
    root_run_id = str(payload.get("root_run_id") or payload.get("run_id") or "").strip()
    projection = _authoritative_sdk_run_projection(
        root_run_id, str(payload.get("sdk_run_id") or "")
    )
    await ws.send_json(
        {
            "type": "session_run_status_ack",
            "payload": {
                "query_id": str(payload.get("query_id") or ""),
                "session_id": target_sid,
                "root_run_id": root_run_id,
                **projection,
            },
        }
    )


async def _cc_permission_response(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：permission_response。"""
    ws = conn.ws
    session_id = conn.session_id
    payload = raw.get("payload", {}) or {}
    target_sid = str(payload.get("session_id") or session_id)
    decision_id = payload.get("decision_id") or payload.get("request_id")
    try:
        receipt = await _signal_product_harness_decision(
            target_sid,
            payload,
            {"decision": str(payload.get("decision") or "deny")},
            authorization=True,
            projection_ws=ws,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "permission_response_rejected",
            decision_id=decision_id,
            error_type=type(exc).__name__,
            error=str(exc),
            exc_info=True,
        )
        await ws.send_json(
            {
                "type": "permission_response_applied",
                "payload": {
                    "ok": False,
                    "request_id": payload.get("request_id"),
                    "decision_id": decision_id,
                    "error": {
                        "code": getattr(exc, "code", type(exc).__name__),
                        "message": str(exc),
                    },
                },
            }
        )
        return
    logger.info(
        "permission_response_applied",
        decision_id=decision_id,
        duplicate=receipt.duplicate,
    )
    await ws.send_json(
        {
            "type": "permission_response_applied",
            "payload": {
                "ok": True,
                "request_id": payload.get("request_id"),
                "decision_id": decision_id,
                "duplicate": receipt.duplicate,
            },
        }
    )


async def _cc_protected_path_decision(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：protected_path_decision。"""
    ws = conn.ws

    payload = raw.get("payload", {}) or {}
    decision = str(payload.get("decision") or "")
    if decision not in {"allow_once", "allow_session", "deny"}:
        decision = "deny"
    applied = _protected_paths.decide(str(payload.get("request_id") or ""), decision)
    await ws.send_json(
        {
            "type": "protected_path_decision_applied",
            "payload": {
                "request_id": payload.get("request_id"),
                "decision": decision,
                "found": applied is not None,
            },
        }
    )


async def _cc_external_wait_response(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：external_wait_response。"""
    session_id = conn.session_id
    payload = raw.get("payload", {}) or {}
    target_sid = str(payload.get("session_id") or session_id)
    receipt = await _signal_product_harness_decision(
        target_sid,
        payload,
        {
            "decision": "complete",
            "completed": True,
        },
    )
    logger.info(
        "external_wait_response_applied",
        decision_id=(payload.get("decision_id") or payload.get("request_id")),
        wait_ref=payload.get("wait_ref"),
        duplicate=receipt.duplicate,
    )


async def _cc_project_directory_response(
    conn: _ControlConnection, raw: dict, msg_type: str
) -> None:
    """控制消息：project_directory_response。"""
    ws = conn.ws
    session_id = conn.session_id
    payload = raw.get("payload", {}) or {}
    target_sid = str(payload.get("session_id") or session_id)
    try:
        project_root = _resolve_selected_project_directory(
            payload.get("parent_directory"),
            payload.get("folder_name"),
            allow_existing=(payload.get("directory_mode") == "use_existing"),
        )
    except ValueError as exc:
        await ws.send_json(
            {
                "type": "project_directory_error",
                "payload": {
                    "decision_id": (payload.get("decision_id") or payload.get("request_id")),
                    "code": str(exc),
                    "error": ("这个位置不能安全创建项目，请重新选择空目录或修改文件夹名。"),
                },
            }
        )
        return
    try:
        receipt = await _signal_product_harness_decision(
            target_sid,
            payload,
            {
                "decision": "complete",
                "completed": True,
                "project_root": project_root,
                "parent_directory": str(payload.get("parent_directory") or ""),
                "folder_name": str(payload.get("folder_name") or ""),
                "directory_mode": str(payload.get("directory_mode") or "create_new"),
            },
        )
    except Exception as exc:  # execution contracts are user-retryable
        if not isinstance(exc, ExecutionError):
            raise
        logger.warning(
            "project_directory_response_rejected",
            session_id=target_sid,
            decision_id=(payload.get("decision_id") or payload.get("request_id")),
            code=exc.code,
            error=str(exc),
        )
        await ws.send_json(
            {
                "type": "project_directory_error",
                "payload": {
                    "decision_id": (payload.get("decision_id") or payload.get("request_id")),
                    "code": exc.code,
                    "error": (
                        "项目位置未能应用，请重新选择；若任务状态已经变化，请停止当前任务后重试。"
                    ),
                },
            }
        )
        return
    logger.info(
        "project_directory_response_applied",
        decision_id=(payload.get("decision_id") or payload.get("request_id")),
        project_root=project_root,
        duplicate=receipt.duplicate,
    )


async def _cc_clarification_response(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：clarification_response。"""
    session_id = conn.session_id
    payload = raw.get("payload", {}) or {}
    target_sid = str(payload.get("session_id") or session_id)
    receipt = await _signal_product_harness_decision(
        target_sid,
        payload,
        {"answer": str(payload.get("answer") or "")},
    )
    logger.info(
        "clarification_response_applied",
        decision_id=payload.get("decision_id") or payload.get("request_id"),
        duplicate=receipt.duplicate,
    )


async def _cc_chat_v2_interrupt(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：chat_v2_interrupt。"""
    ws = conn.ws
    session_id = conn.session_id
    payload = raw.get("payload", {}) or {}
    target_sid = payload.get("session_id") or session_id
    target_run_id = str(payload.get("root_run_id") or payload.get("run_id") or "").strip()
    before = _authoritative_sdk_run_projection(target_run_id, str(payload.get("sdk_run_id") or ""))
    expected_version = payload.get("expected_run_version")
    stale = expected_version is not None and int(expected_version) != int(before["run_version"])
    terminal_before = before["state"] in {"completed", "failed", "cancelled"}
    cancelled = False
    if not stale and not terminal_before:
        cancelled = await _cancel_product_harness_run(
            target_sid,
            target_run_id,
            reason=str(payload.get("reason") or "user_interrupt"),
        )
    after = _authoritative_sdk_run_projection(target_run_id, str(before.get("sdk_run_id") or ""))
    _interrupt_evt = {
        "type": "chat_v2_interrupted",
        "payload": {
            "cancel_command_id": str(payload.get("cancel_command_id") or ""),
            "session_id": target_sid,
            "run_id": target_run_id,
            "cancelled": cancelled,
            "stale_expected_version": stale,
            **after,
        },
    }
    await ws.send_json(_interrupt_evt)
    await _broadcast_default_chat_peers(ws, _interrupt_evt)


async def _cc_plan_confirm(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：plan_confirm。"""
    session_id = conn.session_id
    payload = raw.get("payload", {}) or {}
    _csid = payload.get("session_id") or session_id
    _decision = payload.get("decision") or "go"
    if host_state.sdk_ingress is not None and host_state.sdk_ingress.accepting:
        _run_id = str(payload.get("run_id") or "").strip()
        _decision_id = str(payload.get("decision_id") or "").strip()
        _nonce = str(payload.get("nonce") or "").strip()
        _version = payload.get("version")
        if not _run_id or not _decision_id or not _nonce or _version is None:
            raise ValueError("plan confirmation requires the durable decision fence")
        _receipt = await _signal_product_harness_decision(
            _csid,
            payload,
            {"decision": _decision},
        )
        logger.info(
            "harness_plan_decision sid=%s run_id=%s accepted=%s duplicate=%s",
            _csid,
            _run_id,
            _receipt.accepted,
            _receipt.duplicate,
        )
        _sdb_pc = service_context.get("session_db")
        if _sdb_pc is not None:
            await _sdb_pc.clear_session_plan_awaiting(_csid)
        return


async def _cc_interrupt(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：interrupt。"""
    ws = conn.ws
    session_id = conn.session_id
    payload = raw.get("payload", {}) or {}
    voice_transport = _voice_transports.get(session_id)
    if voice_transport is not None:
        voice_transport.interrupt()
    await _cancel_product_harness_run(
        session_id,
        str(payload.get("run_id") or "").strip(),
        reason="voice_interrupt",
    )
    await ws.send_json({"type": "interrupt_ack"})
