"""控制消息：能力列表与操作、插件、技能安装与卸载"""

from __future__ import annotations

import asyncio

from deskpet.capabilities.contracts import CapabilityScope
from deskpet.capabilities.manifest import load_and_validate_pack
from deskpet.capabilities.ui_service import CapabilityCenterError

from host.control_connection import _ControlConnection
from host.memory_messages import _settings_skill_install_adapter, _skill_install_error_payload
from host.runtime import logger, service_context
from host.tools_bootstrap import plugin_manager, skill_registry_client


async def _cc_capability_list(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：capability_list。"""
    ws = conn.ws
    center = service_context.get("capability_center")
    if center is None:
        await ws.send_json(
            {
                "type": "capability_list_response",
                "payload": {
                    "capabilities": [],
                    "error": "capability center is unavailable",
                },
            }
        )
        return
    try:
        payload = raw.get("payload", {}) or {}
        target_session_id = str(payload.get("session_id") or "").strip()
        capability_scope = None
        capability_owner_key = None
        if target_session_id:
            project_bindings = service_context.get("project_binding_service")
            if project_bindings is None:
                raise RuntimeError("Project binding service is unavailable")
            workspace = await project_bindings.resolve_session(target_session_id)
            if workspace.kind == "project":
                capability_owner_key = "sdk-runtime"
                capability_scope = CapabilityScope.for_run(
                    f"capability-center:{target_session_id}",
                    project_id=workspace.project_id,
                    project_revision=workspace.project_revision,
                    project_identity=workspace.project_identity,
                    user_key=capability_owner_key,
                )
            elif workspace.kind == "missing":
                raise RuntimeError(workspace.error_code)
        capabilities = await center.list_capabilities(
            capability_scope,
            owner_key=capability_owner_key,
        )
        await ws.send_json(
            {
                "type": "capability_list_response",
                "payload": {"capabilities": capabilities},
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("capability_center_list_failed", error=str(exc), exc_info=True)
        await ws.send_json(
            {
                "type": "capability_list_response",
                "payload": {
                    "capabilities": [],
                    "error": str(exc),
                },
            }
        )


async def _cc_capability_operations_list(
    conn: _ControlConnection, raw: dict, msg_type: str
) -> None:
    """控制消息：capability_operations_list。"""
    ws = conn.ws
    center = service_context.get("capability_center")
    if center is None:
        await ws.send_json(
            {
                "type": "capability_operations_response",
                "payload": {
                    "operations": [],
                    "error": "capability center is unavailable",
                },
            }
        )
        return
    payload = raw.get("payload", {}) or {}
    try:
        limit = max(1, min(int(payload.get("limit", 100)), 500))
    except (TypeError, ValueError):
        limit = 100
    try:
        operations = await center.list_operations(limit=limit)
        await ws.send_json(
            {
                "type": "capability_operations_response",
                "payload": {"operations": operations},
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("capability_center_operations_failed", error=str(exc), exc_info=True)
        await ws.send_json(
            {
                "type": "capability_operations_response",
                "payload": {
                    "operations": [],
                    "error": str(exc),
                },
            }
        )


async def _cc_capability_install(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：capability_install、capability_activate、capability_repair、capability_uninstall、capability_rollback、capability_operation_cancel、capability_operation_retry。"""
    ws = conn.ws
    center = service_context.get("capability_center")
    payload = raw.get("payload", {}) or {}
    action = msg_type.removeprefix("capability_").removeprefix("operation_")
    if center is None:
        await ws.send_json(
            {
                "type": "capability_action_response",
                "payload": {
                    "ok": False,
                    "action": action,
                    "error_code": "capability_center_unavailable",
                    "error": "capability center is unavailable",
                },
            }
        )
        return
    try:
        if msg_type == "capability_install":
            (
                _si_adapter,
                _si_project,
                _si_principal,
            ) = await _settings_skill_install_adapter(ws)
            _si_result = await _si_adapter.stage(
                url=str(payload.get("source_url") or payload.get("url") or ""),
                owner=_si_project,  # the Settings adapter yields the global owner
                principal_id=_si_principal,
            )
            await ws.send_json(
                {
                    "type": "capability_action_response",
                    "payload": {
                        "ok": True,
                        "action": action,
                        "status": "awaiting_confirmation",
                        "install": _si_result,
                    },
                }
            )
            return
        if msg_type in {
            "capability_activate",
            "capability_repair",
        }:
            raise CapabilityCenterError(
                "model_driven_action_required",
                "Install, activation, and repair require the main "
                "agent to resolve a trusted source or failure receipt.",
            )
        if msg_type == "capability_uninstall":
            operation = await center.request_uninstall(
                capability_id=str(payload.get("capability_id") or ""),
                source_operation_id=(
                    str(payload["operation_id"]) if payload.get("operation_id") else None
                ),
            )
        elif msg_type == "capability_rollback":
            operation = await center.request_rollback(
                operation_id=str(payload.get("operation_id") or ""),
                capability_id=(
                    str(payload["capability_id"]) if payload.get("capability_id") else None
                ),
            )
        elif msg_type == "capability_operation_retry":
            operation = await center.request_retry(
                operation_id=str(payload.get("operation_id") or "")
            )
        else:
            operation = await center.request_cancel(
                operation_id=str(payload.get("operation_id") or "")
            )
        await ws.send_json(
            {
                "type": "capability_action_response",
                "payload": {
                    "ok": True,
                    "action": action,
                    "operation_id": operation.operation_id,
                    "status": operation.status,
                    "phase": operation.phase,
                },
            }
        )
    except Exception as exc:  # noqa: BLE001
        error_code = str(getattr(exc, "code", "capability_action_failed"))
        logger.warning(
            "capability_center_action_failed",
            action=action,
            error_code=error_code,
            error=str(exc),
            exc_info=True,
        )
        await ws.send_json(
            {
                "type": "capability_action_response",
                "payload": {
                    "ok": False,
                    "action": action,
                    "error_code": error_code,
                    "error": str(exc),
                },
            }
        )


async def _cc_plugin_list(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：plugin_list。"""
    ws = conn.ws
    if plugin_manager is None:
        await ws.send_json(
            {
                "type": "plugin_list_response",
                "payload": {"plugins": [], "error": "plugin manager not initialized"},
            }
        )
        return
    await ws.send_json(
        {
            "type": "plugin_list_response",
            "payload": {"plugins": plugin_manager.list_plugins()},
        }
    )


async def _cc_plugin_enable(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：plugin_enable。"""
    ws = conn.ws
    payload = raw.get("payload", {}) or {}
    name = payload.get("name", "")
    if plugin_manager is None or not plugin_manager.enable(name):
        await ws.send_json(
            {
                "type": "plugin_enable_response",
                "payload": {"ok": False, "name": name, "error": "unknown plugin"},
            }
        )
        return
    # Best-effort SkillLoader hot-reload so the plugin's skills appear.
    try:
        loader = service_context.get("skill_loader")
        if loader is not None and hasattr(loader, "reload"):
            loader.reload()
    except Exception as exc:  # noqa: BLE001
        logger.warning("skill_loader_reload_failed", error=str(exc), exc_info=True)
    await ws.send_json(
        {
            "type": "plugin_enable_response",
            "payload": {"ok": True, "name": name},
        }
    )


async def _cc_plugin_disable(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：plugin_disable。"""
    ws = conn.ws
    payload = raw.get("payload", {}) or {}
    name = payload.get("name", "")
    if plugin_manager is None:
        await ws.send_json(
            {
                "type": "plugin_disable_response",
                "payload": {
                    "ok": False,
                    "name": name,
                    "error": "plugin manager not initialized",
                },
            }
        )
        return
    plugin_manager.disable(name)
    try:
        loader = service_context.get("skill_loader")
        if loader is not None and hasattr(loader, "reload"):
            loader.reload()
    except Exception as exc:  # noqa: BLE001
        logger.warning("skill_loader_reload_failed", error=str(exc), exc_info=True)
    await ws.send_json(
        {
            "type": "plugin_disable_response",
            "payload": {"ok": True, "name": name},
        }
    )


async def _cc_skill_marketplace_list(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：skill_marketplace_list。"""
    ws = conn.ws
    if skill_registry_client is None:
        await ws.send_json(
            {
                "type": "skill_marketplace_list_response",
                "payload": {"skills": [], "error": "marketplace not initialized"},
            }
        )
        return
    _data = await skill_registry_client.fetch()
    await ws.send_json(
        {
            "type": "skill_marketplace_list_response",
            "payload": _data,
        }
    )


async def _cc_skill_list_installed(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：skill_list_installed。"""
    ws = conn.ws
    _capability_platform = service_context.get("capability_platform")
    _global_service = service_context.get("project_skill_install_service")
    if _capability_platform is None or _global_service is None:
        await ws.send_json(
            {
                "type": "skill_list_installed_response",
                "payload": {"skills": [], "error": "global Skill catalog unavailable"},
            }
        )
        return
    _installed = []

    _global_scope_key = str(_global_service.global_owner_key)
    _catalog_snapshot = await _capability_platform.hub.snapshot(
        CapabilityScope(user_key=_global_scope_key)
    )
    for _descriptor in _catalog_snapshot.descriptors:
        # 2026-09-25 UI 全量点击：内置 Skill 以前不算"已安装"，列表为空，
        # 与能力中心"操作"页的 17 个已装 Skill 对不上。内置的也列出，标 builtin。
        _binding = next(
            (
                item
                for item in _descriptor.visible_bindings
                if item.active
                and (
                    (item.scope == "user" and item.scope_key == _global_scope_key)
                    or item.scope == "builtin"
                )
            ),
            None,
        )
        if _binding is None:
            continue
        _record = await _capability_platform.store.get_version(
            _descriptor.version.capability_id,
            _descriptor.version.version,
            _descriptor.version.manifest_hash,
        )
        if _record is None:
            continue
        _manifest = load_and_validate_pack(_record.install_path).manifest
        for _skill in _manifest.skills:
            _installed.append(
                {
                    "name": _skill.id,
                    "description": _manifest.name,
                    "version": _manifest.version,
                    "scope": "builtin" if _binding.scope == "builtin" else "global",
                    "capability_id": _descriptor.version.capability_id,
                    "catalog_generation": _catalog_snapshot.stamp.catalog_generation,
                    "allowed_tools": list(_skill.allowed_tools),
                }
            )
    await ws.send_json(
        {
            "type": "skill_list_installed_response",
            "payload": {"skills": _installed},
        }
    )


async def _cc_skill_install_from_url(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：skill_install_from_url。"""
    ws = conn.ws
    payload = raw.get("payload", {}) or {}
    try:
        _si_adapter, _si_owner, _si_principal = await _settings_skill_install_adapter(ws)
        _si_result = await _si_adapter.stage(
            url=str(payload.get("url") or ""),
            owner=_si_owner,
            principal_id=_si_principal,
        )
        await ws.send_json(
            {
                "type": "skill_install_pending",
                "request_id": raw.get("request_id"),
                "payload": {"ok": True, **_si_result},
            }
        )
    except Exception as exc:  # noqa: BLE001
        await ws.send_json(
            {
                "type": "skill_install_pending",
                "request_id": raw.get("request_id"),
                "payload": _skill_install_error_payload(exc),
            }
        )


async def _cc_skill_install_confirm(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：skill_install_confirm。"""
    ws = conn.ws
    payload = raw.get("payload", {}) or {}
    try:
        _si_adapter, _si_project, _si_principal = await _settings_skill_install_adapter(ws)
        _si_decision = str(payload.get("decision") or "").strip()
        if _si_decision not in {"approve", "deny"}:
            raise ValueError("skill_install_decision_invalid")
        _si_result = await _si_adapter.settle(
            intent_id=str(payload.get("intent_id") or payload.get("staging_id") or ""),
            digest=str(payload.get("digest") or ""),
            decision_nonce=str(payload.get("decision_nonce") or ""),
            decision_version=int(payload.get("decision_version") or 0),
            decision=_si_decision,
        )
        await ws.send_json(
            {
                "type": "skill_install_confirm_response",
                "request_id": raw.get("request_id"),
                "payload": {"ok": True, **_si_result},
            }
        )
    except Exception as exc:  # noqa: BLE001
        await ws.send_json(
            {
                "type": "skill_install_confirm_response",
                "request_id": raw.get("request_id"),
                "payload": _skill_install_error_payload(exc),
            }
        )


async def _cc_skill_install_status(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：skill_install_status。"""
    ws = conn.ws
    payload = raw.get("payload", {}) or {}
    try:
        _si_adapter, _si_project, _si_principal = await _settings_skill_install_adapter(ws)
        _si_result = await _si_adapter.status(
            intent_id=str(payload.get("intent_id") or ""),
            project=_si_project,
        )
        await ws.send_json(
            {
                "type": "skill_install_status_response",
                "request_id": raw.get("request_id"),
                "payload": {"ok": True, **_si_result},
            }
        )
    except Exception as exc:  # noqa: BLE001
        await ws.send_json(
            {
                "type": "skill_install_status_response",
                "request_id": raw.get("request_id"),
                "payload": _skill_install_error_payload(exc),
            }
        )


async def _cc_skill_uninstall(conn: _ControlConnection, raw: dict, msg_type: str) -> None:
    """控制消息：skill_uninstall。"""
    ws = conn.ws
    payload = raw.get("payload", {}) or {}
    name = str(payload.get("name") or "")
    capability_id = str(payload.get("capability_id") or name)
    center = service_context.get("capability_center")
    _global_service = service_context.get("project_skill_install_service")
    if center is None or _global_service is None:
        await ws.send_json(
            {
                "type": "skill_uninstall_response",
                "payload": {
                    "ok": False,
                    "name": name,
                    "error": "capability center is unavailable",
                },
            }
        )
        return
    try:
        operation = await center.request_uninstall(
            capability_id=capability_id,
            scope=CapabilityScope(user_key=str(_global_service.global_owner_key)),
        )
        for _ in range(60):
            if operation.status in {"succeeded", "failed", "cancelled", "unknown"}:
                break
            await asyncio.sleep(0.5)
            operation = await center.store.get_operation(operation.operation_id) or operation
        ok = operation.status == "succeeded"
        await ws.send_json(
            {
                "type": "skill_uninstall_response",
                "payload": {
                    "ok": ok,
                    "name": name,
                    "status": operation.status,
                    **({} if ok else {"error": f"卸载未完成（{operation.status}）"}),
                },
            }
        )
    except Exception as exc:  # noqa: BLE001
        await ws.send_json(
            {
                "type": "skill_uninstall_response",
                "payload": {
                    "ok": False,
                    "name": name,
                    "error": f"{getattr(exc, 'code', type(exc).__name__)}: {exc}",
                },
            }
        )
