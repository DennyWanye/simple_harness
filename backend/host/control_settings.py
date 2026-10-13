"""控制消息：授权模式、发布目录、对话超时、预算、监督开关与选择、调试、提供方连通测试"""

from __future__ import annotations

from pathlib import Path

from host.capability_runtime import _authorization_policy_snapshot, _set_authorization_auto_mode
from host.connections import _control_connections
from host.control_connection import _ControlConnection
from host.harness_chat import _launch_product_harness_chat
from host.harness_runs import _cancel_product_harness_run
from host.llm_providers import (
    _load_llm_runtime_overrides,
    _save_llm_runtime_overrides,
    billing_ledger,
)
from host.runtime import _CONFIG_PATH, logger, service_context
from host.settings_persistence import _persist_supervisor_enabled, _restart_orchestration


async def _cc_permission_auto_mode_set(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：permission_auto_mode_set。"""
    ws = conn.ws
    payload = raw.get("payload", {}) or {}
    requested = bool(payload.get("enabled", False))
    await _set_authorization_auto_mode(requested)
    snapshot = await _authorization_policy_snapshot()
    logger.info(
        "permission_auto_mode_set",
        requested=requested,
        mode=snapshot["mode"],
        generation=snapshot["generation"],
        provenance=snapshot["provenance"],
    )
    await ws.send_json(
        {
            "type": "permission_auto_mode_response",
            "request_id": raw.get("request_id"),
            "payload": snapshot,
        }
    )


async def _cc_orchestration_publish_dir_get(
    conn: _ControlConnection, raw: dict, msg_type: str
) -> None:
    """控制消息：orchestration_publish_dir_get、orchestration_publish_dir_set。"""
    ws = conn.ws
    from deskpet.orchestration import publish_settings as _publish_settings
    from deskpet.orchestration.wiring import read_section as _read_orch_section
    from paths import user_data_dir as _publish_user_data_dir

    _rid = raw.get("request_id")
    _body = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    _reply: dict = {"request_id": _rid, "ok": True}
    try:
        if msg_type == "orchestration_publish_dir_set":
            await _publish_settings.set_publish_dir(
                _body.get("path"),
                config_path=Path(_CONFIG_PATH),
                protected=[_publish_user_data_dir()],
                restart=_restart_orchestration,
            )
        _configured = str(_read_orch_section(_CONFIG_PATH).get("publish_dir") or "")
        _reply["data"] = _publish_settings.current(
            service_context.get("orchestration"), _configured
        )
    except _publish_settings.PublishDirRefused as _refused:
        _reply.update(ok=False, error_code=_refused.code, error=str(_refused))
    except Exception as _exc:  # noqa: BLE001
        logger.warning("orchestration_publish_dir_failed err=%s", _exc)
        _reply.update(ok=False, error_code="internal", error="发布目录设置失败，请查看日志")
    await ws.send_json({"type": msg_type + "_response", "payload": _reply})


async def _cc_permission_auto_mode_get(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：permission_auto_mode_get。"""
    ws = conn.ws
    await ws.send_json(
        {
            "type": "permission_auto_mode_response",
            "request_id": raw.get("request_id"),
            "payload": await _authorization_policy_snapshot(),
        }
    )


async def _cc_chat_turn_timeout_set(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：chat_turn_timeout_set。"""
    ws = conn.ws
    payload = raw.get("payload", {}) or {}
    try:
        _mins = int(round(float(payload.get("minutes", 15))))
    except Exception:  # noqa: BLE001
        _mins = 15
    _mins = max(1, min(60, _mins))
    _ov = _load_llm_runtime_overrides()
    _ov["chat_turn_timeout_minutes"] = _mins
    _save_llm_runtime_overrides(_ov)
    logger.info("chat_turn_timeout_set minutes=%d", _mins)
    await ws.send_json(
        {
            "type": "chat_turn_timeout_response",
            "request_id": raw.get("request_id"),
            "payload": {"minutes": _mins},
        }
    )


async def _cc_chat_turn_timeout_get(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：chat_turn_timeout_get。"""
    ws = conn.ws
    try:
        _cur = int(round(float(_load_llm_runtime_overrides().get("chat_turn_timeout_minutes", 15))))
    except Exception:  # noqa: BLE001
        _cur = 15
    await ws.send_json(
        {
            "type": "chat_turn_timeout_response",
            "request_id": raw.get("request_id"),
            "payload": {"minutes": max(1, min(60, _cur))},
        }
    )


async def _cc_budget_status(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：budget_status。"""
    ws = conn.ws
    try:
        status = await billing_ledger.status()
        await ws.send_json({"type": "budget_status", "payload": status})
    except Exception as exc:
        logger.warning("budget_status_failed", error=str(exc))
        await ws.send_json(
            {
                "type": "error",
                "payload": {"message": f"budget_status failed: {exc}"},
            }
        )


async def _cc_provider_test_connection(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：provider_test_connection。"""
    ws = conn.ws
    from provider_test_connection import handle_provider_test_connection

    await handle_provider_test_connection(ws, raw.get("payload", {}) or {})


async def _cc_supervisor_user_choice(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：supervisor_user_choice。"""
    ws = conn.ws
    session_id = conn.session_id
    _payload = raw.get("payload", {}) or {}
    _target_sid = _payload.get("session_id") or session_id
    _alert_id = str(_payload.get("alert_id") or "")
    _btn_idx = int(_payload.get("button_index") or 0)
    _btn_text = str(_payload.get("button_text") or "")
    logger.info(
        "supervisor_user_choice sid=%s alert=%s button_idx=%d text=%s",
        _target_sid,
        _alert_id,
        _btn_idx,
        _btn_text,
    )
    _sdb_for_choice = service_context.get("session_db")
    if _sdb_for_choice is not None:
        try:
            await _sdb_for_choice.append_supervisor_hint(
                session_id=_target_sid,
                alert_id=_alert_id,
                hint_text=_btn_text,
                action="user_choice",
                severity="green",
                user_button=_btn_text,
            )
        except Exception as _exc:
            logger.debug("supervisor_user_choice_audit_failed error=%s", _exc)

    # Heuristic intent matching by button text. We accept
    # both Chinese and English labels since the LLM picks
    # them dynamically. Stop/cancel takes priority over
    # continue if both keywords appear.
    _btn_lower = _btn_text.lower().strip()
    _is_stop = any(k in _btn_text for k in ("中断", "停止", "取消", "终止")) or any(
        k in _btn_lower for k in ("cancel", "stop", "abort", "interrupt")
    )
    _is_continue = any(k in _btn_text for k in ("继续", "重试", "再试", "让它", "再等")) or any(
        k in _btn_lower for k in ("continue", "retry", "let", "wait")
    )

    if _is_stop:
        # Cancel any in-flight task + clear the nudge queue
        await _cancel_product_harness_run(
            _target_sid,
            str(_payload.get("run_id") or "").strip(),
            reason="supervisor_user_choice",
        )
        _nq_for_stop = service_context.get("nudge_queue")
        if _nq_for_stop is not None:
            try:
                await _nq_for_stop.clear(_target_sid)
            except Exception:
                pass
    elif _is_continue:
        try:
            _launch_product_harness_chat(ws, "<<supervisor_followup>>", _target_sid)
            logger.info(
                "supervisor_user_choice_followup_scheduled sid=%s",
                _target_sid,
            )
        except Exception as _ex_fu:
            logger.warning(
                "supervisor_user_choice_followup_failed sid=%s error=%s",
                _target_sid,
                _ex_fu,
            )


async def _cc_supervisor_toggle(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：supervisor_toggle。"""
    ws = conn.ws
    _payload = raw.get("payload", {}) or {}
    _enabled = bool(_payload.get("enabled", True))
    _persisted = _persist_supervisor_enabled(_enabled)
    _wd_inst = service_context.get("watchdog")
    if _enabled:
        if _wd_inst is None or not _wd_inst.is_running():
            logger.info("supervisor_toggle_enable_requested (restart_recommended)")
        else:
            _wd_inst.enable()
            logger.info("supervisor_toggle_enabled")
    else:
        if _wd_inst is not None:
            _wd_inst.disable()
        _nq_inst = service_context.get("nudge_queue")
        if _nq_inst is not None:
            try:
                await _nq_inst.clear()
            except Exception:
                pass
        logger.info("supervisor_toggle_disabled")
    await ws.send_json(
        {
            "type": "supervisor_toggle_ack",
            "payload": {"enabled": _enabled, "persisted": _persisted},
        }
    )


async def _cc_debug_force_error_pending(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：__debug_force_error_pending。"""
    ws = conn.ws
    _payload = raw.get("payload", {}) or {}
    target_sid = _payload.get("session_id") or ""
    _sa = service_context.get("session_activity")
    if _sa is not None and target_sid:
        await _sa.bump(target_sid, event_type="error", snippet="forced for E2E test")
        await _sa.mark_error_pending(target_sid)
        # Also reset watchdog dedup so next scan fires
        _wd = service_context.get("watchdog")
        if _wd is not None:
            _wd.reset_dedup(target_sid)
        await ws.send_json(
            {
                "type": "__debug_force_error_pending_ack",
                "payload": {"sid": target_sid, "ok": True},
            }
        )


async def _cc_debug_dump_session_activity(
    conn: _ControlConnection, raw: dict, msg_type: str
) -> None:
    """控制消息：__debug_dump_session_activity。"""
    ws = conn.ws
    _sa = service_context.get("session_activity")
    _wd = service_context.get("watchdog")
    if _sa is not None:
        _all = await _sa.snapshot_all()
    else:
        _all = {}
    _wd_state = {
        "running": _wd.is_running() if _wd else False,
        "last_scans": dict(_wd._last_scan_ts) if _wd else {},
    }
    await ws.send_json(
        {
            "type": "__debug_dump_response",
            "payload": {"session_activity": _all, "watchdog": _wd_state},
        }
    )


async def _cc_debug_inject_supervisor_alert(
    conn: _ControlConnection, raw: dict, msg_type: str
) -> None:
    """控制消息：__debug_inject_supervisor_alert。"""
    ws = conn.ws
    _payload = raw.get("payload", {}) or {}
    # Fan out to every control WS, identical path to the
    # real supervisor's broadcast.
    msg = {"type": "supervisor_alert", "payload": _payload}
    for _sid_key, _ws_obj in list(_control_connections.items()):
        try:
            await _ws_obj.send_json(msg)
        except Exception as _bex:
            logger.debug(
                "debug_supervisor_alert_send_failed sid=%s error=%s",
                _sid_key,
                _bex,
            )
    logger.info(
        "__debug_inject_supervisor_alert dispatched payload=%s",
        _payload,
    )
    await ws.send_json(
        {
            "type": "__debug_inject_ack",
            "payload": {"ok": True},
        }
    )
