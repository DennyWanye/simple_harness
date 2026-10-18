"""控制通道：连接握手、伴侣特权帧、伴侣详情查询、有序路由表与分发、编排与工作流转发"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

import p4_ipc
from deskpet.companion.control_ingress import (
    CompanionControlIngressError,
    is_privileged_control_kind,
)
from deskpet.companion.detail_query import CompanionDetailQueryError
from deskpet.memory.control_binding import HumanMemoryControlBinding
from deskpet.memory.primary_authority import assert_not_primary_authority
from deskpet.orchestration.handlers import handle as _orchestration_handle
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from observability.startup import registry as startup_errors

from host.auth import _validate_secret
from host.chat_delivery import _initial_chat_peer_group, _register_chat_peer
from host.companion_services import _dispatch_companion_action_command
from host.connections import _chat_peer_groups, _control_connections
from host.control_capabilities import (
    _cc_capability_install,
    _cc_capability_list,
    _cc_capability_operations_list,
    _cc_plugin_disable,
    _cc_plugin_enable,
    _cc_plugin_list,
    _cc_skill_install_confirm,
    _cc_skill_install_from_url,
    _cc_skill_install_status,
    _cc_skill_list_installed,
    _cc_skill_marketplace_list,
    _cc_skill_uninstall,
)
from host.control_chat import _cc_chat
from host.control_connection import _ControlConnection
from host.control_decisions import (
    _cc_chat_v2_interrupt,
    _cc_clarification_response,
    _cc_external_wait_response,
    _cc_interrupt,
    _cc_permission_response,
    _cc_plan_confirm,
    _cc_project_directory_response,
    _cc_protected_path_decision,
    _cc_session_run_status_query,
)
from host.control_memory import (
    _cc_human_memory_request,
    _cc_memory_archive_list,
    _cc_memory_list,
    _cc_memory_summarize_now,
    _cc_memory_thumbs_up,
)
from host.control_messages import _cancel_harness_inspector_tasks, _handle_control_ws_message
from host.control_providers import (
    _cc_models_list,
    _cc_session_provider_get,
    _cc_session_set_provider,
    _cc_settings_providers_list_request,
    _cc_settings_providers_probe_models,
)
from host.control_sessions import (
    _cc_context_breakdown_request,
    _cc_context_usage_request,
    _cc_project_preview_register,
    _cc_session_delete,
    _cc_session_messages_load,
    _cc_session_rename,
    _cc_sessions_list,
    _cc_slash_command,
)
from host.control_settings import (
    _cc_budget_status,
    _cc_chat_turn_timeout_get,
    _cc_chat_turn_timeout_set,
    _cc_debug_dump_session_activity,
    _cc_debug_force_error_pending,
    _cc_debug_inject_supervisor_alert,
    _cc_orchestration_publish_dir_get,
    _cc_permission_auto_mode_get,
    _cc_permission_auto_mode_set,
    _cc_provider_test_connection,
    _cc_supervisor_toggle,
    _cc_supervisor_user_choice,
)
from host.harness_runs import _try_resolve_execution_workflow_decision
from host.history_events import _ensure_companion_inbox_route, _settle_companion_bind_projection
from host.runtime import logger, service_context
from host.sdk_execution import (
    _sdk_desktop_test_enabled,
    _trigger_harness_recovery_after_identity_bind,
)
from host.state import host_state

control_router = APIRouter()


async def _cc_ping(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：ping。"""
    ws = conn.ws
    await ws.send_json({"type": "pong"})


async def _cc_orchestration_routes(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：编排服务转发（mission_、orchestration_、taskgraph. 前缀）。"""
    ws = conn.ws

    # a payload that is not an object is answered with invalid_request inside
    # handle(), never raised into this shared loop (review P2-6)
    await ws.send_json(
        await _orchestration_handle(
            service_context.get("orchestration"),
            msg_type,
            raw.get("payload"),
            request_id=raw.get("request_id"),
        )
    )


async def _cc_p4_ipc(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：P4 进程间消息。"""
    ws = conn.ws
    session_id = conn.session_id
    await p4_ipc.handle(
        ws,
        session_id,
        msg_type,
        raw.get("payload", {}) or {},
        service_context,
    )


async def _cc_workflow_routes(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：工作流消息（workflow_ 前缀）。"""
    ws = conn.ws
    session_id = conn.session_id

    # 执行账本里的决定仍按运行内核处理；后台旧工作流引擎已不启动，其余工作流消息一律回不可用。
    if await _try_resolve_execution_workflow_decision(
        ws,
        session_id=session_id,
        raw=raw,
        execution_uow=service_context.get("execution_uow"),
    ):
        return
    await ws.send_json(
        {
            "type": "workflow_ipc_error",
            "request_type": msg_type,
            "request_id": raw.get("request_id"),
            "ok": False,
            "error": {
                "code": "workflow_service_unavailable",
                "message": "Durable workflow service is unavailable",
                "retryable": False,
            },
        }
    )


async def _cc_unknown(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：未知类型。"""
    ws = conn.ws
    await ws.send_json(
        {
            "type": "error",
            "payload": {"message": f"unknown type: {msg_type}"},
        }
    )


# 有序路由表：与原 if/elif 分发链顺序一致。每项是消息类型集合或判断函数。
_CONTROL_ROUTES: list[tuple[Any, Any]] = [
    (frozenset(["ping"]), _cc_ping),
    (frozenset(["memory_summarize_now"]), _cc_memory_summarize_now),
    (frozenset(["memory_thumbs_up"]), _cc_memory_thumbs_up),
    (frozenset(["memory_archive_list"]), _cc_memory_archive_list),
    (frozenset(["session_health_check", "session_run_status_query"]), _cc_session_run_status_query),
    (frozenset(["permission_response"]), _cc_permission_response),
    (frozenset(["protected_path_decision"]), _cc_protected_path_decision),
    (frozenset(["external_wait_response"]), _cc_external_wait_response),
    (frozenset(["project_directory_response"]), _cc_project_directory_response),
    (frozenset(["clarification_response"]), _cc_clarification_response),
    (frozenset(["permission_auto_mode_set"]), _cc_permission_auto_mode_set),
    (
        frozenset(["orchestration_publish_dir_get", "orchestration_publish_dir_set"]),
        _cc_orchestration_publish_dir_get,
    ),
    (
        lambda msg_type: (
            msg_type.startswith(("mission_", "orchestration_", "taskgraph."))
            or msg_type
            in (
                "agent_skill_request",
                "agent_skill_evaluation_mission",
                "agent_skill_evaluation_dispatch",
            )
        ),
        _cc_orchestration_routes,
    ),
    (frozenset(["permission_auto_mode_get"]), _cc_permission_auto_mode_get),
    (frozenset(["capability_list"]), _cc_capability_list),
    (frozenset(["capability_operations_list"]), _cc_capability_operations_list),
    (
        frozenset(
            [
                "capability_activate",
                "capability_install",
                "capability_operation_cancel",
                "capability_operation_retry",
                "capability_repair",
                "capability_rollback",
                "capability_uninstall",
            ]
        ),
        _cc_capability_install,
    ),
    (frozenset(["chat_turn_timeout_set"]), _cc_chat_turn_timeout_set),
    (frozenset(["chat_turn_timeout_get"]), _cc_chat_turn_timeout_get),
    (frozenset(["plugin_list"]), _cc_plugin_list),
    (frozenset(["plugin_enable"]), _cc_plugin_enable),
    (frozenset(["plugin_disable"]), _cc_plugin_disable),
    (frozenset(["skill_marketplace_list"]), _cc_skill_marketplace_list),
    (frozenset(["skill_list_installed"]), _cc_skill_list_installed),
    (frozenset(["skill_install_from_url"]), _cc_skill_install_from_url),
    (frozenset(["skill_install_confirm"]), _cc_skill_install_confirm),
    (frozenset(["skill_install_status"]), _cc_skill_install_status),
    (frozenset(["skill_uninstall"]), _cc_skill_uninstall),
    (frozenset(["context_usage_request"]), _cc_context_usage_request),
    (frozenset(["context_breakdown_request"]), _cc_context_breakdown_request),
    (frozenset(["human_memory_request"]), _cc_human_memory_request),
    (
        frozenset(
            [
                "project_catalog_page",
                "project_inspect",
                "project_preview_register",
                "project_register",
                "project_relocate",
                "project_sessions_page",
                "session_create",
            ]
        ),
        _cc_project_preview_register,
    ),
    (frozenset(["sessions_list"]), _cc_sessions_list),
    (frozenset(["session_delete"]), _cc_session_delete),
    (frozenset(["session_rename"]), _cc_session_rename),
    (frozenset(["session_messages_load"]), _cc_session_messages_load),
    (frozenset(["slash_command"]), _cc_slash_command),
    (frozenset(["chat_v2_interrupt"]), _cc_chat_v2_interrupt),
    (frozenset(["plan_confirm"]), _cc_plan_confirm),
    (frozenset(["models_list"]), _cc_models_list),
    (frozenset(["session_provider_get"]), _cc_session_provider_get),
    (frozenset(["session_set_model", "session_set_provider"]), _cc_session_set_provider),
    (
        frozenset(
            [
                "settings_providers_add",
                "settings_providers_list_request",
                "settings_providers_remove",
                "settings_providers_reorder",
                "settings_providers_update",
            ]
        ),
        _cc_settings_providers_list_request,
    ),
    (frozenset(["settings_providers_probe_models"]), _cc_settings_providers_probe_models),
    (frozenset(["chat", "chat_v2"]), _cc_chat),
    (frozenset(["interrupt"]), _cc_interrupt),
    (frozenset(["budget_status"]), _cc_budget_status),
    (frozenset(["memory_clear", "memory_delete", "memory_export", "memory_list"]), _cc_memory_list),
    (lambda msg_type: msg_type in p4_ipc.P4_IPC_MESSAGE_TYPES, _cc_p4_ipc),
    (lambda msg_type: msg_type.startswith("workflow_"), _cc_workflow_routes),
    (frozenset(["provider_test_connection"]), _cc_provider_test_connection),
    (frozenset(["supervisor_user_choice"]), _cc_supervisor_user_choice),
    (frozenset(["supervisor_toggle"]), _cc_supervisor_toggle),
    (frozenset(["__debug_force_error_pending"]), _cc_debug_force_error_pending),
    (frozenset(["__debug_dump_session_activity"]), _cc_debug_dump_session_activity),
    (frozenset(["__debug_inject_supervisor_alert"]), _cc_debug_inject_supervisor_alert),
]


async def _handle_privileged_companion_frame(
    conn: _ControlConnection, wire_text: str, msg_type: str
) -> Any:
    """伴侣特权控制帧：严格解析、执行、推进挑战；返回之后要用的挑战（推进后的或重新发出的）。"""

    ws = conn.ws
    companion_challenge = conn.companion_challenge
    human_memory_control_binding = conn.human_memory_control_binding
    try:
        strict_raw = host_state.companion_control_ingress.parse_privileged_frame(wire_text)
        if strict_raw is None:
            raise CompanionControlIngressError("privileged_control_parse_failed")
        if companion_challenge is None:
            raise CompanionControlIngressError("companion_control_challenge_missing")
        response = await host_state.companion_control_ingress.execute(
            strict_raw,
            challenge=companion_challenge,
        )
        if msg_type == "companion_profile_bind":
            human_memory_control_binding.bound(
                host_state.companion_control_ingress, companion_challenge
            )
        elif msg_type == "companion_profile_unbind":
            human_memory_control_binding.clear()
        if msg_type in {
            "companion_growth_evaluation_decision",
            "companion_growth_activation_decision",
            "companion_growth_action_decision",
            "companion_rollback",
            "companion_forget",
        }:
            await _dispatch_companion_action_command(msg_type, strict_raw)
        if msg_type == "companion_profile_bind":
            # 事件 AK：这一段必须有界。就绪帧在其后无条件下发，
            # 失败只降级成稳定码，不再让 UI 无限期停在
            # 「等待主对话就绪」。
            try:
                _bound_identity = host_state.companion_identity_gate.freeze()
            except Exception as _freeze_exc:  # noqa: BLE001
                logger.warning(
                    "companion_projection_bind_drain_failed",
                    error=type(_freeze_exc).__name__,
                    code=str(getattr(_freeze_exc, "code", None) or type(_freeze_exc).__name__),
                    exc_info=True,
                )
            else:
                (
                    _inbox_session_id,
                    _projection_code,
                ) = await _settle_companion_bind_projection(
                    _bound_identity,
                    ensure_route=_ensure_companion_inbox_route,
                    notification_service=(host_state.companion_notification_service),
                )
                _bind_payload = dict(response.get("payload", {}))
                if _inbox_session_id is not None:
                    _bind_payload["session_id"] = _inbox_session_id
                if _projection_code is not None:
                    # 稳定码随就绪帧一起到前端，UI 可读地说明
                    # 「历史补读未完成」，而不是静默。
                    _bind_payload["projection_degraded_code"] = _projection_code
                response = {**response, "payload": _bind_payload}
            # Harness startup can discover recoverable Runs before
            # the main window finishes its signed identity bind.
            # That attempt correctly fails closed; wake the
            # event-driven reconciler now that the same process
            # identity gate is ready so waiting work cannot remain
            # stranded until an unrelated durable event arrives.
            if _trigger_harness_recovery_after_identity_bind():
                # 2026-08-05 fork 前既有 bug 修复：此处原引用局部变量
                # `payload`，但它在本 try 块更下方才赋值——recovery
                # 触发时即 UnboundLocalError，bind 被拒且前端无限
                # 重试（真机测试实锤）。改读 response 的 payload。
                _resp_payload = response.get("payload", {}) if isinstance(response, Mapping) else {}
                logger.info(
                    "harness_recovery_triggered_identity_ready",
                    profile_id=_resp_payload.get("profile_id"),
                    profile_generation=_resp_payload.get("profile_generation"),
                )
        elif msg_type == "companion_profile_unbind":
            if host_state.companion_notification_service is not None:
                host_state.companion_notification_service.close_history()
        await ws.send_json(response)
        payload = response.get("payload", {})
        companion_challenge = companion_challenge.advance(
            request_seq=int(payload.get("next_request_seq") or payload["nextRequestSeq"]),
            binding_epoch=int(payload.get("binding_epoch") or payload["bindingEpoch"]),
        )
        if msg_type in {
            "companion_profile_bind",
            "companion_profile_unbind",
        }:
            identity_status = {
                "type": "companion_identity_status",
                "payload": {
                    "ready": (msg_type == "companion_profile_bind"),
                    "status": ("ready" if msg_type == "companion_profile_bind" else "unready"),
                    "binding_epoch": str(companion_challenge.binding_epoch),
                    **(
                        {
                            "profile_id": payload.get("profile_id"),
                            "profile_generation": payload.get("profile_generation"),
                            "session_id": payload.get("session_id"),
                        }
                        if msg_type == "companion_profile_bind"
                        else {}
                    ),
                },
            }
            for status_ws in list(_control_connections.values()):
                try:
                    await status_ws.send_json(identity_status)
                except Exception:  # noqa: BLE001 - 连接已断时发不出去，不处理
                    logger.debug("companion_identity_status_send_failed", exc_info=True)
    except Exception as _e:  # noqa: BLE001
        code = getattr(_e, "code", str(_e))
        rechallenge = bool(getattr(_e, "rechallenge", True))
        logger.warning(
            "companion_control_rejected",
            command_kind=msg_type,
            code=code,
            rechallenge=rechallenge,
            connection_id=(
                companion_challenge.connection_id if companion_challenge is not None else None
            ),
            exc_info=True,
        )
        await ws.send_json(
            {
                "type": (
                    "companion_control_rechallenge" if rechallenge else "companion_control_error"
                ),
                "payload": {
                    "code": code,
                    "retryable": True,
                },
            }
        )
        # A rejected mutation never mutates the old active lease.
        # Rechallenge via a fresh bounded challenged row so stale
        # seq/epoch clients can recover without backend restart.
        if rechallenge:
            try:
                companion_challenge = host_state.companion_control_ingress.open_challenge(
                    requested_window_label=ws.query_params.get("requested_window_label", ""),
                    requested_scope=ws.query_params.get("requested_scope", ""),
                )
                await ws.send_json(
                    {
                        "type": "companion_control_challenge",
                        "payload": (companion_challenge.public_payload()),
                    }
                )
            except Exception as challenge_error:  # noqa: BLE001
                logger.warning(
                    "companion_rechallenge_failed",
                    error=type(challenge_error).__name__,
                    exc_info=True,
                )
    return companion_challenge


async def _cc_companion_detail_get(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：companion_detail_get。"""
    ws = conn.ws
    requested_scope = conn.requested_scope
    companion_challenge = conn.companion_challenge
    _detail_request_id = str(raw.get("request_id") or "")
    try:
        if (
            host_state.companion_detail_query is None
            or companion_challenge is None
            or requested_scope != "companion_action"
        ):
            raise CompanionDetailQueryError("unavailable")
        _detail_identity = host_state.companion_identity_gate.freeze()
        if int(companion_challenge.binding_epoch) != int(_detail_identity.binding_epoch):
            raise CompanionDetailQueryError("cursor_invalid")
        _detail_payload = raw.get("payload")
        if not isinstance(_detail_payload, dict):
            raise CompanionDetailQueryError("unavailable")
        _detail_response = await host_state.companion_detail_query.query(
            frozen_identity=_detail_identity,
            control_epoch=int(companion_challenge.binding_epoch),
            request=_detail_payload,
        )
        await ws.send_json(
            {
                "type": "companion_detail_response",
                "request_id": _detail_request_id,
                "payload": _detail_response,
            }
        )
    except Exception as _detail_exc:  # noqa: BLE001
        logger.warning("cc_companion_detail_get_companion_detail_query_err_failed", exc_info=True)
        _detail_code = getattr(_detail_exc, "code", "unavailable")
        if _detail_code not in {
            "unavailable",
            "detail_changed",
            "cursor_invalid",
        }:
            _detail_code = "unavailable"
        await ws.send_json(
            {
                "type": "companion_detail_error",
                "request_id": _detail_request_id,
                "payload": {
                    "code": _detail_code,
                    **({"message": str(_detail_exc)} if str(_detail_exc) != _detail_code else {}),
                },
            }
        )


async def _dispatch_control_message(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """按路由表把一条控制消息交给处理函数；都不匹配时回"未知类型"。"""
    for matcher, handler in _CONTROL_ROUTES:
        if (msg_type in matcher) if isinstance(matcher, frozenset) else matcher(msg_type):
            await handler(conn, raw, msg_type)
            return
    await _cc_unknown(conn, raw, msg_type)


@control_router.websocket("/ws/control")
async def control_channel(ws: WebSocket):
    await ws.accept()
    if not _validate_secret(ws):
        try:
            await ws.close(code=4001, reason="invalid secret")
        except Exception:  # noqa: BLE001 - 连接已断时关闭会失败，不处理
            logger.debug("control_ws_close_failed", exc_info=True)
        return

    session_id = ws.query_params.get("session_id", "default")
    requested_scope = ws.query_params.get("requested_scope", "")
    _cc_sdb = service_context.get("session_db")
    if _cc_sdb is not None:
        try:
            await assert_not_primary_authority(_cc_sdb._db_path, session_id)
        except Exception as _cc_exc:  # noqa: BLE001
            _cc_code = getattr(_cc_exc, "code", None)
            if _cc_code:
                await ws.send_json(
                    {
                        "type": "session_authority_error",
                        "payload": {"ok": False, "code": _cc_code},
                    }
                )
                await ws.close(code=4003, reason=_cc_code)
                return
            raise
    # The main-window identity bridge must coexist with the message-panel
    # chat socket.  Its requested scope is not mutation authority (the first
    # Rust-signed command still proves the real label/scope), but it is safe to
    # keep this transport in a unique auxiliary slot because it never owns
    # chat delivery.  The message-panel remains the canonical session socket.
    is_companion_aux = requested_scope == "identity_bind"
    if not is_companion_aux:
        _register_chat_peer(session_id)
    connection_registry_key = (
        f"companion-aux:{session_id}:{uuid.uuid4()}" if is_companion_aux else session_id
    )
    # gracefully kick the previous holder of this session_id
    # (e.g. an old E2E client) so its disconnect callback doesn't
    # later pop OUR entry. Don't kill ourselves if we're the holder.
    if not is_companion_aux:
        _prev_ws = _control_connections.get(session_id)
        if _prev_ws is not None and _prev_ws is not ws:
            try:
                await _prev_ws.close(code=4002, reason="session replaced")
            except Exception:  # noqa: BLE001 - 旧连接已断时关闭会失败，不处理
                logger.debug("control_prev_ws_close_failed", exc_info=True)
    _control_connections[connection_registry_key] = ws
    _chat_peer_groups[connection_registry_key] = _initial_chat_peer_group(session_id)
    logger.info("control channel connected", session_id=session_id)
    # first frame after handshake reports startup-error state so the
    # UI can render "CUDA 缺失" / "模型缺失" banners without polling /health.
    try:
        await ws.send_json(
            {
                "type": "startup_status",
                "degraded": startup_errors.is_degraded(),
                "errors": startup_errors.snapshot(),
            }
        )
    except Exception as _e:
        logger.warning("startup_status_send_failed", error=str(_e), exc_info=True)
    if _sdk_desktop_test_enabled():
        await ws.send_json(
            {
                "type": "companion_identity_status",
                "payload": {
                    "ready": True,
                    "status": "ready",
                    "profile_id": "sdk-desktop-test",
                    "profile_generation": 1,
                    "binding_epoch": "1",
                    "session_id": "default",
                },
            }
        )

    human_memory_control_binding = HumanMemoryControlBinding()
    companion_challenge = None
    if host_state.companion_control_ingress is not None:
        try:
            companion_challenge = host_state.companion_control_ingress.open_challenge(
                requested_window_label=ws.query_params.get("requested_window_label", ""),
                requested_scope=ws.query_params.get("requested_scope", ""),
            )
            await ws.send_json(
                {
                    "type": "companion_control_challenge",
                    "payload": companion_challenge.public_payload(),
                }
            )
            if requested_scope == "companion_action":
                try:
                    frozen_identity = host_state.companion_identity_gate.freeze()
                    await ws.send_json(
                        {
                            "type": "companion_identity_status",
                            "payload": {
                                "ready": True,
                                "status": "ready",
                                "profile_id": frozen_identity.owner.profile_id,
                                "profile_generation": (frozen_identity.owner.profile_generation),
                                "binding_epoch": str(frozen_identity.binding_epoch),
                            },
                        }
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("control_channel_freeze_failed", exc_info=True)
                    await ws.send_json(
                        {
                            "type": "companion_identity_status",
                            "payload": {
                                "ready": False,
                                "status": "unready",
                            },
                        }
                    )
        except Exception as _e:  # noqa: BLE001
            logger.warning(
                "companion_control_challenge_failed", error=type(_e).__name__, exc_info=True
            )
    try:
        while True:
            wire_text = await ws.receive_text()
            try:
                raw = json.loads(wire_text)
            except (json.JSONDecodeError, UnicodeError):
                await ws.send_json(
                    {
                        "type": "error",
                        "payload": {"message": "invalid control JSON"},
                    }
                )
                continue
            if not isinstance(raw, dict):
                await ws.send_json(
                    {
                        "type": "error",
                        "payload": {"message": "control frame must be an object"},
                    }
                )
                continue
            msg_type = raw.get("type", "")
            conn = _ControlConnection(
                ws=ws,
                session_id=session_id,
                requested_scope=requested_scope,
                companion_challenge=companion_challenge,
                human_memory_control_binding=human_memory_control_binding,
            )

            if host_state.companion_control_ingress is not None:
                pass

            if host_state.companion_control_ingress is not None and is_privileged_control_kind(
                msg_type
            ):
                companion_challenge = await _handle_privileged_companion_frame(
                    conn, wire_text, msg_type
                )
                continue

            if msg_type == "companion_detail_get":
                await _cc_companion_detail_get(conn, raw, msg_type)
                continue

            if await _handle_control_ws_message(raw, session_id=session_id, ws=ws):
                continue

            await _dispatch_control_message(conn, raw, msg_type)

    except WebSocketDisconnect:
        _cancel_harness_inspector_tasks(ws)
        # only clear the dict entry if it's still pointing at
        # OUR ws — a later connection may have replaced us already.
        if _control_connections.get(connection_registry_key) is ws:
            _control_connections.pop(connection_registry_key, None)
        _chat_peer_groups.pop(connection_registry_key, None)
        logger.info("control channel disconnected", session_id=session_id)
