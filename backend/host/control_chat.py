"""控制消息：对话"""

from __future__ import annotations

import asyncio
import uuid

from host.chat_delivery import (
    _broadcast_default_chat_peers,
    _remap_chat_peer_group,
    _resolve_chat_source_session,
    _resolve_chat_task_scope,
    _send_new_session_origin_user_echo,
)
from host.control_connection import _ControlConnection
from host.harness_chat import _launch_product_harness_chat
from host.history_events import _bind_companion_inbox_route
from host.runtime import logger, service_context
from host.sdk_execution import _sdk_desktop_test_enabled
from host.tools_bootstrap import deskpet_tool_registry_v2, permission_gate_v2


async def _cc_chat(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：chat、chat_v2。"""
    ws = conn.ws
    session_id = conn.session_id
    _payload = raw.get("payload", {}) or {}
    text = _payload.get("text", "")
    from deskpet.agent.attachment_budget import (
        normalize_user_attachment_blocks as _normalize_attachment_blocks,
    )

    _attachment_blocks = _normalize_attachment_blocks(_payload.get("attachments"))
    _msg_sid = _resolve_chat_source_session(
        session_id,
        _payload.get("session_id"),
    )
    _base_msg_sid = _msg_sid
    from deskpet.session.task_scope import (
        source_session_for_created_conversation as _creation_source_sid,
    )

    _source_session_id = _creation_source_sid(
        _payload.get("session_id"),
        _base_msg_sid,
    )
    _scope_decision = _resolve_chat_task_scope(
        base_sid=_msg_sid,
        text=text,
        payload=_payload,
    )
    _msg_sid = _scope_decision.effective_sid
    text = _scope_decision.stripped_text
    _memory_policy_override = (
        {"l2_page_in": "always"}
        if getattr(
            _scope_decision,
            "force_l2_page_in",
            None,
        )
        == "always"
        else None
    )
    if _sdk_desktop_test_enabled():
        if (text or "").strip():
            _launch_product_harness_chat(
                ws,
                text,
                _msg_sid,
                None,
                False,
                _attachment_blocks,
                str(_payload.get("request_id") or raw.get("request_id") or "") or None,
                str(_payload.get("turn_id") or raw.get("turn_id") or "") or None,
            )
        return
    if _scope_decision.created:
        _legacy_creation = service_context.get("session_creation_service")
        _legacy_request_id = str(
            _payload.get("request_id") or raw.get("request_id") or uuid.uuid4()
        )
        try:
            _legacy_created = await _legacy_creation.create_conversation_session(
                request_id=f"legacy-new-session:{_legacy_request_id}",
                project_id=None,
                source_session_id=_source_session_id,
            )
            _msg_sid = str(_legacy_created["session"]["session_id"])
        except Exception as _legacy_create_exc:  # noqa: BLE001
            await ws.send_json(
                {
                    "type": "chat_v2_error",
                    "payload": {
                        "error": "新会话创建失败，请重试。",
                        "code": str(getattr(_legacy_create_exc, "code", "session_create_failed")),
                        "retryable": True,
                        # Empty-state failures have no Session owner.
                        # Keep this empty so App's global error banner
                        # does not filter it as an inactive pseudo sid.
                        "session_id": _source_session_id or "",
                        "request_id": _legacy_request_id,
                    },
                }
            )
            return
    _identity_gate = service_context.get("companion_identity_gate")
    try:
        if _identity_gate is None:
            raise RuntimeError("companion_identity_not_ready")
        # Freeze now, before a background Run is created. Later
        # owner-aware boundaries consume this immutable snapshot;
        # account switches never rewrite an in-flight Turn.
        _frozen_companion_owner = _identity_gate.freeze()
        _companion_route_bound = await _bind_companion_inbox_route(
            _msg_sid,
            _frozen_companion_owner,
            create_if_absent=True,
        )
    except Exception as _identity_exc:  # noqa: BLE001
        # request_id 必须回传：这一轮在 Run 被预约之前就夭折了，
        # 前端的 pending_root_request_id 只能靠它认领并清除。少了
        # 它，该会话此后每条消息都会被判成 "等待 root run" 而永远
        # 不发出去 —— 会话被静默卡死（r4 S18 实测 default 会话）。
        # 这个 try 覆盖的不只是身份门：_bind_companion_inbox_route
        # 的属主围栏错误（owner_tombstoned / rebind_forbidden 等）
        # 也会落到这里。原实现一律报 companion_identity_not_ready，
        # 把"会话属主坏了"说成"身份没就绪"，排查时会被带偏很远
        # （r5 真机实测：default 被墓碑化后一直显示身份未就绪）。
        # 真实原因放进 detail，code 仍保持稳定供前端分支。
        _identity_detail = str(
            getattr(_identity_exc, "code", "") or _identity_exc or type(_identity_exc).__name__
        )
        await ws.send_json(
            {
                "type": "chat_v2_error",
                "payload": {
                    "error": "companion_identity_not_ready",
                    "code": "companion_identity_not_ready",
                    "detail": _identity_detail,
                    "retryable": True,
                    "session_id": _msg_sid,
                    "request_id": str(_payload.get("request_id") or raw.get("request_id") or "")
                    or None,
                    "turn_id": str(_payload.get("turn_id") or raw.get("turn_id") or "") or None,
                },
            }
        )
        logger.info(
            "companion_chat_blocked_identity_not_ready",
            session_id=_msg_sid,
            error=type(_identity_exc).__name__,
            # 只记异常类名会把所有属主围栏错误压成 'RuntimeError'，
            # 日志里看不出到底是身份没就绪还是会话属主坏了。
            detail=_identity_detail,
        )
        return
    if not _companion_route_bound:
        _route_error = (
            "新会话创建失败，请重试。"
            if _scope_decision.created
            else "当前选择的是只读历史会话，请重新发送。"
        )
        _route_code = (
            "companion_session_create_failed"
            if _scope_decision.created
            else "companion_session_read_only"
        )
        # 同上：Run 预约前夭折的一轮必须带 request_id 回去，
        # 否则前端 pending_root_request_id 永远挂着。
        await ws.send_json(
            {
                "type": "chat_v2_error",
                "payload": {
                    "error": _route_error,
                    "code": _route_code,
                    "retryable": bool(_scope_decision.created),
                    "session_id": _msg_sid,
                    "request_id": str(_payload.get("request_id") or raw.get("request_id") or "")
                    or None,
                    "turn_id": str(_payload.get("turn_id") or raw.get("turn_id") or "") or None,
                },
            }
        )
        logger.info(
            "companion_chat_blocked_read_only_session",
            session_id=_msg_sid,
        )
        return
    if _scope_decision.created:
        try:
            # 2026-08-09：这里原先直接引用局部名 `session_db`——它只在
            # `session_provider_get/set` 两个分支里被赋值，而 Python 的
            # 函数作用域让它在整个 control_channel 里都是局部名。以前不炸
            # 是因为 ChatView 挂载必发 session_provider_get 把它绑上；
            # 取消保留会话后空态 activeSid="" ⇒ hydration 直接 return ⇒
            # 该消息永不发出 ⇒ 空态直发新建会话时 UnboundLocalError
            # （真机实测）。改为每次从 service_context 取，不依赖执行顺序。
            _inherit_sdb = service_context.get("session_db")
            if _inherit_sdb is None:
                raise RuntimeError("session_db_unavailable")
            _inherited_model_binding = await _inherit_sdb.get_session_provider_binding(_msg_sid)
        except Exception as _model_inherit_exc:  # noqa: BLE001
            await ws.send_json(
                {
                    "type": "chat_v2_error",
                    "payload": {
                        "error": "新会话模型配置继承失败，请重试。",
                        "code": "session_model_inherit_failed",
                        "retryable": True,
                        "session_id": _base_msg_sid,
                    },
                }
            )
            logger.exception(
                "session_model_inherit_failed",
                source_session_id=_base_msg_sid,
                target_session_id=_msg_sid,
                error=type(_model_inherit_exc).__name__,
                detail=str(_model_inherit_exc)[:240],
            )
            return
        # The control socket keeps a stable transport id while
        # the visible conversation switches to this fresh,
        # owner-fenced UUID session.
        _remap_chat_peer_group(session_id, _msg_sid)
        _switch_payload = {
            "old_sid": _base_msg_sid,
            "new_sid": _msg_sid,
            "reason": _scope_decision.reason,
            "provider_id": _inherited_model_binding.get("provider_id"),
            "preferred_model": _inherited_model_binding.get("preferred_model"),
            "model_params": _inherited_model_binding.get("model_params"),
        }
        for _evt_type in (
            "session_switched",
            "task_session_started",
        ):
            _switch_evt = {
                "type": _evt_type,
                "payload": dict(_switch_payload),
            }
            try:
                await ws.send_json(_switch_evt)
            except Exception:
                pass
            await _broadcast_default_chat_peers(ws, _switch_evt)
        await _send_new_session_origin_user_echo(ws, _msg_sid, text)
    # Clicking an empty "new topic" still creates and switches
    # session above, but must not launch an empty AgentLoop turn.
    if not (text or "").strip():
        return
    if deskpet_tool_registry_v2 is None or permission_gate_v2 is None:
        await ws.send_json(
            {
                "type": "chat_v2_error",
                "payload": {
                    "error": "v2 stack not initialized",
                    "session_id": _msg_sid,
                },
            }
        )
        return
    # 方案 B（2026-08-20）：continuation 退化为 fresh run。SDK 0.1.4
    # 的 ReAct driver 是单轮语义，多轮对话 = 每条用户消息一个 fresh run、
    # 历史由 SDK context 组装。旧的 projection/boundary + signal 契约是
    # 8/17 迁移遗留的半成品（写投影的 create_task_context 已失联、signal
    # 签名未随 SDK 0.1.3 变更），继续走它只会报 identity mismatch。因此
    # 忽略 target_root_run_id/task_scope_id，直接落入下方 fresh run 分支。

    # P5-S2 Phase 4: a fresh user-initiated chat message means
    # the user has implicitly granted a new auto-resume budget.
    # Reset the attempts counter so subsequent failures may
    # auto-resume up to ``max_attempts`` times again. Skipped
    # for synthetic re-entry texts (`<<supervisor_followup>>`,
    # `<<auto_resume>>`) so they consume the existing budget.
    if not (text or "").startswith("<<"):
        _sa_for_reset = service_context.get("session_activity")
        if _sa_for_reset is not None:
            try:
                await _sa_for_reset.reset_auto_resume_attempts(_msg_sid)
            except Exception as _ex:  # noqa: BLE001
                logger.debug("auto_resume_reset_failed sid=%s err=%s", _msg_sid, _ex)

    _chat_task = _launch_product_harness_chat(
        ws,
        text,
        _msg_sid,
        _memory_policy_override,
        bool(_scope_decision.created),
        _attachment_blocks,
        str(_payload.get("request_id") or raw.get("request_id") or "") or None,
        str(_payload.get("turn_id") or raw.get("turn_id") or "") or None,
    )

    def _make_followup_cb(target_sid: str, target_ws):
        def _cb(_t):
            async def _maybe_followup():
                try:
                    _nq_check = service_context.get("nudge_queue")
                    if _nq_check is None or not await _nq_check.peek(target_sid):
                        return
                    new_task = _launch_product_harness_chat(
                        target_ws, "<<supervisor_followup>>", target_sid
                    )
                    new_task.add_done_callback(_make_followup_cb(target_sid, target_ws))
                    logger.info(
                        "supervisor_followup_scheduled sid=%s",
                        target_sid,
                    )
                except Exception as _exc:  # noqa: BLE001
                    logger.debug(
                        "supervisor_followup_failed sid=%s error=%s",
                        target_sid,
                        _exc,
                    )

            try:
                asyncio.create_task(_maybe_followup())
            except RuntimeError:
                # No running loop (shutdown); silently skip.
                pass

        return _cb

    _chat_task.add_done_callback(_make_followup_cb(_msg_sid, ws))
