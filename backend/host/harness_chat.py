"""产品对话：项目目录解析、预检拦截提交、桌面 SDK 对话、产品对话主流程与超时、对话启动、自动续跑提示"""

from __future__ import annotations

import asyncio
import inspect
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deskpet.agent.assembler.components.persona import _resolve_persona
from deskpet.agent.product_domain_sink import LegacyProductDomainSink
from deskpet.agent.run_presenter import RunPresentationContext
from deskpet.agent.turn_preparer import TurnInput
from deskpet.capabilities.contracts import CapabilityScope
from deskpet.execution.contracts import RunRef, root_idempotency_key
from deskpet.execution.run_block_signals import PreflightBlocked, RootBlockReasonV1
from deskpet.sdk_adapters.capability_catalog import ProductCapabilityCatalogSourceAdapter
from deskpet.sdk_adapters.context_authority import DefaultDenySnapshotRedactor
from deskpet.sdk_adapters.ingress import SdkRuntimeIngress
from deskpet.sdk_adapters.sdk_candidate import SDK_VERSION
from deskpet.sdk_adapters.tool_authority import (
    SDK_DIRECT_TOOL_KERNEL,
    SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY,
)
from deskpet.sdk_adapters.tools import filter_sdk_catalog_for_workspace
from deskpet.types.task_work_context import TaskWorkContextResolver
from llm.resolution import SessionProviderUnavailable
from simple_harness.contracts.messages import ContentBlock, Message, MessageRole
from simple_harness.runtime import ConversationTurnInput

from host.chat_delivery import _broadcast_default_chat_peers, _send_chat_error, _send_chat_final
from host.llm_providers import _chat_turn_timeout_s, billing_ledger
from host.runtime import config, logger, service_context
from host.sdk_activation import _issue_product_harness_host
from host.sdk_context import (
    _intent_label_from_turn,
    _prepare_sdk_context_snapshot,
    _sdk_context_usage_from_projection_only,
)
from host.sdk_execution import (
    _execute_sdk_run,
    _freeze_sdk_provider_authority,
    _sdk_desktop_test_enabled,
    _snapshot_context_usage_basis_for_run,
    _snapshot_context_usage_binding_for_run,
)
from host.sdk_registry import _sdk_unavailable_tool_authority_runs
from host.state import host_state
from host.tools_bootstrap import deskpet_tool_registry_v2

_WINDOWS_RESERVED_PROJECT_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _resolve_selected_project_directory(
    parent_directory: object,
    folder_name: object,
    *,
    allow_existing: bool = False,
) -> str:
    """Resolve a native-picker selection for a new or existing project."""

    parent_raw = str(parent_directory or "").strip()
    if not parent_raw:
        raise ValueError("project_directory_required")
    parent = Path(parent_raw).expanduser()
    if not parent.is_absolute():
        raise ValueError("project_parent_must_be_absolute")
    parent = parent.resolve(strict=False)
    if not parent.is_dir():
        raise ValueError("project_parent_not_directory")
    # In existing-project mode the native picker selects the project root
    # itself. The suggested folder name is presentation metadata only.
    if allow_existing:
        return str(parent)

    child = str(folder_name or "").strip()
    if not child:
        raise ValueError("project_directory_required")
    if (
        child in {".", ".."}
        or child.endswith((".", " "))
        or any(char in child for char in '<>:"/\\|?*')
        or any(ord(char) < 32 for char in child)
        or child.split(".", 1)[0].upper() in _WINDOWS_RESERVED_PROJECT_NAMES
    ):
        raise ValueError("project_folder_name_invalid")
    project_root = (parent / child).resolve(strict=False)
    try:
        project_root.relative_to(parent)
    except ValueError as exc:
        raise ValueError("project_directory_escaped_parent") from exc
    if project_root.exists():
        if not project_root.is_dir():
            raise ValueError("project_directory_is_file")
        try:
            if any(project_root.iterdir()):
                raise ValueError("project_directory_not_empty")
        except OSError as exc:
            raise ValueError("project_directory_unreadable") from exc
    return str(project_root)


async def _commit_product_preflight_block(
    *,
    websocket: Any,
    text: str,
    session_id: str,
    request_id: str,
    turn_id: str,
    task_scope_id: str,
    host: Any,
    block: Any,
) -> None:
    """Commit and project a typed failure without provider or Tool execution."""

    if host_state.sdk_ingress is None or host_state.sdk_runtime_stack is None:
        raise RuntimeError("SDK Runtime is unavailable")
    del host
    root_run_id = RunRef(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"deskpet:{root_idempotency_key(session_id, request_id, turn_id)}",
        ).hex,
        session_id,
    ).run_id
    await host_state.sdk_runtime_stack.commit_preflight_blocked_root(
        execution_session_id=session_id,
        run_id=root_run_id,
        request_id=request_id,
        turn_id=turn_id,
        task_scope_id=task_scope_id,
        text=text,
        reason_code=block.reason_code.value,
        evidence_refs=tuple(block.evidence_refs),
    )
    started = {
        "type": "chat_v2_run_started",
        "payload": {
            "session_id": session_id,
            "run_id": root_run_id,
            "request_id": request_id,
            "turn_id": turn_id,
            "task_scope_id": task_scope_id,
            "projection_version": 0,
        },
    }
    await websocket.send_json(started)
    await _broadcast_default_chat_peers(websocket, started)
    public_messages = {
        "workspace_unavailable": "项目目录不可用，请重新定位同一项目后再试。",
        "provider_binding_unavailable": "模型服务尚未就绪，请检查当前会话的模型设置后再试。",
        "capability_unavailable": "当前请求所需能力暂不可用，请检查工具设置后再试。",
    }
    await _send_chat_error(
        websocket,
        {
            "type": "chat_v2_error",
            "payload": {
                "session_id": session_id,
                "run_id": root_run_id,
                "request_id": request_id,
                "turn_id": turn_id,
                "task_scope_id": task_scope_id,
                "error": public_messages.get(
                    block.reason_code.value,
                    "请求暂时无法执行，请检查当前会话设置后再试。",
                ),
                "code": block.reason_code.value,
            },
        },
        session_id=session_id,
        request_id=request_id,
    )


async def _run_sdk_desktop_chat(
    websocket,
    text: str,
    session_id: str,
    *,
    client_request_id: str | None,
    client_turn_id: str | None,
) -> None:
    """Present one real SDK ReAct Run through the existing desktop protocol."""

    bridge = host_state.sdk_desktop_bridge
    if bridge is None:
        raise RuntimeError("SDK desktop test bridge is unavailable")
    request_id = str(client_request_id or "").strip() or uuid.uuid4().hex
    turn_id = str(client_turn_id or client_request_id or "").strip() or uuid.uuid4().hex
    run_id = bridge.run_id_for(session_id, request_id, turn_id)
    task_scope_id = f"sdk-desktop:{run_id}"
    reserved = {
        "type": "chat_v2_run_reserved",
        "payload": {
            "session_id": session_id,
            "run_id": run_id,
            "request_id": request_id,
            "turn_id": turn_id,
            "task_scope_id": task_scope_id,
            "projection_version": 0,
        },
    }
    await websocket.send_json(reserved)
    await _broadcast_default_chat_peers(websocket, reserved)
    echo = {
        "type": "chat_v2_user_echo",
        "payload": {
            "session_id": session_id,
            "text": text,
            "request_id": request_id,
            "turn_id": turn_id,
            "run_id": run_id,
            "task_scope_id": task_scope_id,
        },
    }
    await _broadcast_default_chat_peers(websocket, echo)
    started = {
        "type": "chat_v2_run_started",
        "payload": {
            "session_id": session_id,
            "run_id": run_id,
            "request_id": request_id,
            "turn_id": turn_id,
            "task_scope_id": task_scope_id,
            "projection_version": 0,
        },
    }
    await websocket.send_json(started)
    await _broadcast_default_chat_peers(websocket, started)
    result = await bridge.run_chat(
        text=text,
        session_id=session_id,
        request_id=request_id,
        turn_id=turn_id,
    )
    logger.info(
        "sdk_desktop_test_run_completed",
        run_id=result.run_id,
        state=result.state,
        tools=result.tool_names,
    )
    await _send_chat_final(
        websocket,
        {
            "type": "chat_v2_final",
            "payload": {
                "text": result.text,
                "session_id": session_id,
                "run_id": result.run_id,
                "request_id": request_id,
                "turn_id": turn_id,
                "task_scope_id": task_scope_id,
                "sdk_version": SDK_VERSION,
                "tool_names": list(result.tool_names),
            },
        },
        session_id=session_id,
        request_id=request_id,
    )


@dataclass
class _HarnessChatState:
    """_run_product_harness_chat 的运行状态：原函数的局部变量与参数（切段用）。"""

    flow: Any = None
    _global_entries: Any = None
    _provider: Any = None
    _provider_snapshot: Any = None
    _providers: Any = None
    activity: Any = None
    binding_service: Any = None
    capability_platform: Any = None
    client_request_id: Any = None
    client_turn_id: Any = None
    context: Any = None
    context_binding_id: Any = None
    context_run: Any = None
    context_run_state: Any = None
    context_sources: Any = None
    conversation: Any = None
    creation_service: Any = None
    current_content: Any = None
    deferred_tool_names: Any = None
    direct_tool_names: Any = None
    frozen_context_usage_basis_sample_id: Any = None
    frozen_owner: Any = None
    frozen_session_binding: Any = None
    global_owner_key: Any = None
    global_scope: Any = None
    global_skill_records: Any = None
    global_skill_service: Any = None
    global_snapshot: Any = None
    host: Any = None
    identity: Any = None
    identity_authority: Any = None
    is_sentinel: Any = None
    memory_policy_override: Any = None
    persona_text: Any = None
    prepared_skill_scope: Any = None
    prepared_snapshot: Any = None
    priority: Any = None
    provider: Any = None
    provider_authority: Any = None
    provider_chain: Any = None
    provider_snapshot: Any = None
    public_snapshot: Any = None
    request_id: Any = None
    reserved: Any = None
    reserved_send: Any = None
    retry_handle: Any = None
    root_ref: Any = None
    run_binding: Any = None
    run_tool_catalog: Any = None
    run_tool_inventory: Any = None
    sdk_run_id: Any = None
    session: Any = None
    session_context: Any = None
    session_db: Any = None
    session_id: Any = None
    sink: Any = None
    skill_arguments: Any = None
    source_ref: Any = None
    task_scope_explicit_new: Any = None
    task_scope_id: Any = None
    text: Any = None
    turn: Any = None
    turn_id: Any = None
    user_attachment_blocks: Any = None
    user_message_id: Any = None
    vector_worker: Any = None
    venue: Any = None
    visible_tool_names: Any = None
    websocket: Any = None
    workspace: Any = None
    workspace_admission_retained: Any = None
    workspace_admitted: Any = None
    workspace_binding: Any = None
    workspace_resolution: Any = None
    write_scope_root: Any = None


async def _run_product_harness_chat(
    websocket,
    text: str,
    session_id: str,
    memory_policy_override=None,
    task_scope_explicit_new: bool = False,
    user_attachment_blocks=(),
    client_request_id: str | None = None,
    client_turn_id: str | None = None,
    prepared_skill_scope: Any | None = None,
    skill_arguments: tuple[str, ...] = (),
) -> None:
    """The single production chat ingress into ProductVenue/RunKernel."""

    st = _HarnessChatState(
        websocket=websocket,
        text=text,
        session_id=session_id,
        memory_policy_override=memory_policy_override,
        task_scope_explicit_new=task_scope_explicit_new,
        user_attachment_blocks=user_attachment_blocks,
        client_request_id=client_request_id,
        client_turn_id=client_turn_id,
        prepared_skill_scope=prepared_skill_scope,
        skill_arguments=skill_arguments,
    )
    if _sdk_desktop_test_enabled():
        await _run_sdk_desktop_chat(
            st.websocket,
            st.text,
            st.session_id,
            client_request_id=st.client_request_id,
            client_turn_id=st.client_turn_id,
        )
        return

    if host_state.sdk_ingress is None or not host_state.sdk_ingress.accepting:
        raise RuntimeError("SDK Runtime ingress is closed")
    await _init_turn_identity(st)
    _resolve_workspace(st)

    await _announce_run_reserved(st)

    st.venue = "text"
    st.flow = None
    await _block_missing_workspace(st)
    if st.flow == "stop":
        return
    st.flow = None
    await _issue_host_or_block(st)
    if st.flow == "stop":
        return
    st.frozen_session_binding = _snapshot_context_usage_binding_for_run(st.host)
    # TODO: SDK Runtime client.resume() method not yet implemented
    # This is only needed for retry/resume scenarios (when turn_id is provided)
    st.retry_handle = None  # await _sdk_ingress.require_ready().client.resume(request_id, turn_id, host) if turn_id else None
    st.frozen_context_usage_basis_sample_id = (
        None
        if st.retry_handle is not None
        else await _snapshot_context_usage_basis_for_run(st.session_db, st.session_id)
    )
    await _persist_user_message(st)
    st.session_context: dict[str, Any] = {"_session_id": st.session_id}
    if st.workspace is not None:
        st.session_context.update(
            _project_root=st.workspace,
            _write_scope_root=st.workspace,
        )
    else:
        st.write_scope_root = st.host.write_scope_root
        if st.write_scope_root:
            st.session_context["_write_scope_root"] = st.write_scope_root
    deskpet_tool_registry_v2.set_session_context(st.session_id, st.session_context)

    _build_turn_presentation(st)
    st.session = None
    st.workspace_admitted = False
    st.workspace_admission_retained = False
    try:
        await _prepare_run_context(st)

        st.public_snapshot = DefaultDenySnapshotRedactor().redact(st.prepared_snapshot)
        await _bind_run_and_skills(st)
        await _execute_and_settle_context(st)
    except PreflightBlocked as block:
        await _commit_product_preflight_block(
            websocket=st.websocket,
            text=st.text,
            session_id=st.session_id,
            request_id=st.request_id,
            turn_id=st.turn_id,
            task_scope_id=st.task_scope_id,
            host=st.host,
            block=block,
        )
        return
    except asyncio.CancelledError:
        # A WebSocket disconnect only detaches presentation. The durable Run
        # remains owned by Kernel and can be resumed with the same client ids.
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "product_harness_chat_failed sid=%s error=%s",
            st.session_id,
            str(exc),
            exc_info=True,
            traceback=traceback.format_exc(),
        )
        try:
            await _send_chat_error(
                st.websocket,
                {
                    "type": "chat_v2_error",
                    "payload": {
                        "error": str(exc) or type(exc).__name__,
                        "detail": type(exc).__name__,
                        "session_id": st.session_id,
                        "run_id": st.root_ref.run_id,
                        "task_scope_id": st.task_scope_id,
                        "request_id": st.request_id,
                    },
                },
                session_id=st.session_id,
                request_id=st.request_id,
            )
        except Exception:  # noqa: BLE001 - 连接已断时发不出去，不处理
            logger.debug("harness_chat_error_frame_failed", exc_info=True)
    finally:
        await _release_chat_run(st)


async def _init_turn_identity(st: _HarnessChatState) -> None:
    """取会话服务、生成请求与轮次标识，迁移无项目会话并读工作区绑定。"""

    st.session_db = service_context.get("session_db")
    st.vector_worker = service_context.get("vector_worker")
    st.user_message_id = None
    st.frozen_owner = None
    st.is_sentinel = (st.text or "").startswith("<<") and (st.text or "").endswith(">>")
    st.request_id = str(st.client_request_id or "").strip() or uuid.uuid4().hex
    st.turn_id = str(st.client_turn_id or st.client_request_id or "").strip() or uuid.uuid4().hex

    st.root_ref = RunRef(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"deskpet:{root_idempotency_key(st.session_id, st.request_id, st.turn_id)}",
        ).hex,
        st.session_id,
    )
    st.task_scope_id = TaskWorkContextResolver.task_scope_id(
        st.session_id, st.request_id, st.turn_id
    )
    st.binding_service = service_context.get("project_binding_service")
    if st.binding_service is None:
        raise RuntimeError("project_binding_service_unavailable")
    st.creation_service = service_context.get("session_creation_service")
    if st.creation_service is None:
        raise RuntimeError("session_creation_service_unavailable")
    # Admission is the migration fence for legacy projectless Sessions. Any
    # allocation/persistence error propagates and prevents a projectless Tool
    # catalog from being constructed for this Run.
    await st.creation_service.migrate_projectless_session(st.session_id)
    st.workspace_binding = await st.binding_service.resolve_session(st.session_id)


def _resolve_workspace(st: _HarnessChatState) -> None:
    """解析会话的工作区绑定。"""

    if st.workspace_binding.kind == "project":
        st.workspace = st.workspace_binding.effective_root
        st.workspace_resolution = {
            "kind": "project_bound",
            "session_id": st.workspace_binding.session_id,
            "project_id": st.workspace_binding.project_id,
            "project_name": st.workspace_binding.project_name,
            "project_root": st.workspace_binding.project_root,
            "execution_kind": st.workspace_binding.execution_kind,
            "effective_root": st.workspace_binding.effective_root,
            "project_identity": st.workspace_binding.project_identity,
            "execution_identity": st.workspace_binding.execution_identity,
            "project_revision": st.workspace_binding.project_revision,
            "binding_version": st.workspace_binding.binding_version,
            "handoff": None,
        }
    elif st.workspace_binding.kind == "projectless":
        st.workspace = None
        st.workspace_resolution = {
            "kind": "projectless",
            "session_id": st.workspace_binding.session_id,
            "effective_root": None,
            "binding_version": 1,
        }
    else:
        st.workspace = None
        st.workspace_resolution = {
            "kind": "missing",
            "session_id": st.workspace_binding.session_id,
            "project_id": st.workspace_binding.project_id,
            "project_name": st.workspace_binding.project_name,
            "project_root": st.workspace_binding.project_root,
            "execution_kind": st.workspace_binding.execution_kind,
            "effective_root": st.workspace_binding.effective_root,
            "project_revision": st.workspace_binding.project_revision,
            "binding_version": st.workspace_binding.binding_version,
            "error_code": st.workspace_binding.error_code,
        }


async def _announce_run_reserved(st: _HarnessChatState) -> None:
    """通知前端与同会话其他连接：运行已预留。"""

    # Bind the client turn to its deterministic root Run before provider,
    # host, or Context preparation can block. The UI uses this reservation
    # to keep the new task visible and queues later steering until the
    # conversation boundary is ready.
    st.reserved = {
        "type": "chat_v2_run_reserved",
        "payload": {
            "session_id": st.session_id,
            "run_id": st.root_ref.run_id,
            "request_id": st.request_id,
            "turn_id": st.turn_id,
            "task_scope_id": st.task_scope_id,
            "projection_version": 0,
        },
    }
    st.reserved_send = st.websocket.send_json(st.reserved)
    if inspect.isawaitable(st.reserved_send):
        await st.reserved_send
    await _broadcast_default_chat_peers(st.websocket, st.reserved)


async def _block_missing_workspace(st: _HarnessChatState) -> None:
    """工作区缺失：提交拦截记录并结束。"""

    if st.workspace_resolution["kind"] == "missing":
        (
            st.host,
            st._provider,
            st._providers,
            st._provider_snapshot,
        ) = await _issue_product_harness_host(
            session_id=st.session_id,
            workspace=None,
            allow_provider_unavailable=True,
            allow_workspace_unavailable=True,
            projectless=True,
        )
        await _commit_product_preflight_block(
            websocket=st.websocket,
            text=st.text,
            session_id=st.session_id,
            request_id=st.request_id,
            turn_id=st.turn_id,
            task_scope_id=st.task_scope_id,
            host=st.host,
            block=PreflightBlocked(
                RootBlockReasonV1.WORKSPACE_UNAVAILABLE,
                ("workspace-binding:" + str(st.workspace_resolution["error_code"]),),
            ),
        )
        st.flow = "stop"
        return


async def _issue_host_or_block(st: _HarnessChatState) -> None:
    """签发 Harness 宿主；预检拦截时提交拦截记录并结束。"""

    try:
        (
            st.host,
            st._provider,
            st._providers,
            st._provider_snapshot,
        ) = await _issue_product_harness_host(
            session_id=st.session_id,
            workspace=st.workspace,
            projectless=st.workspace_resolution["kind"] == "projectless",
            project_id=str(st.workspace_resolution.get("project_id") or ""),
            project_revision=int(st.workspace_resolution.get("project_revision") or 0),
            project_identity=str(st.workspace_resolution.get("project_identity") or ""),
        )
        st.provider = st._provider
        st.provider_chain = st._providers
        st.provider_snapshot = st._provider_snapshot
    except Exception as exc:
        if isinstance(exc, PreflightBlocked):
            (
                st.host,
                st._provider,
                st._providers,
                st._provider_snapshot,
            ) = await _issue_product_harness_host(
                session_id=st.session_id,
                workspace=st.workspace,
                allow_workspace_unavailable=True,
                projectless=st.workspace_resolution["kind"] == "projectless",
                project_id=str(st.workspace_resolution.get("project_id") or ""),
                project_revision=int(st.workspace_resolution.get("project_revision") or 0),
                project_identity=str(st.workspace_resolution.get("project_identity") or ""),
            )
            await _commit_product_preflight_block(
                websocket=st.websocket,
                text=st.text,
                session_id=st.session_id,
                request_id=st.request_id,
                turn_id=st.turn_id,
                task_scope_id=st.task_scope_id,
                host=st.host,
                block=exc,
            )
            st.flow = "stop"
            return
        if not isinstance(exc, SessionProviderUnavailable):
            raise
        (
            st.host,
            st._provider,
            st._providers,
            st._provider_snapshot,
        ) = await _issue_product_harness_host(
            session_id=st.session_id,
            workspace=st.workspace,
            allow_provider_unavailable=True,
            projectless=st.workspace_resolution["kind"] == "projectless",
            project_id=str(st.workspace_resolution.get("project_id") or ""),
            project_revision=int(st.workspace_resolution.get("project_revision") or 0),
            project_identity=str(st.workspace_resolution.get("project_identity") or ""),
        )
        await _commit_product_preflight_block(
            websocket=st.websocket,
            text=st.text,
            session_id=st.session_id,
            request_id=st.request_id,
            turn_id=st.turn_id,
            task_scope_id=st.task_scope_id,
            host=st.host,
            block=PreflightBlocked(
                RootBlockReasonV1.PROVIDER_BINDING_UNAVAILABLE,
                ("provider-binding:" + str(getattr(exc, "reason", type(exc).__name__)),),
            ),
        )
        st.flow = "stop"
        return


async def _persist_user_message(st: _HarnessChatState) -> None:
    """保存用户消息并投递回显与成长信号。"""

    if st.retry_handle is None and st.session_db is not None and not st.is_sentinel:
        st.frozen_owner = host_state.companion_identity_gate.freeze()
        # New Sessions are bound atomically during creation. This idempotent
        # admission also claims older empty ownerless Sessions created before
        # the dynamic identity resolver fix; messageful or mismatched Sessions
        # remain fail-closed inside SessionDB.
        await st.session_db.bind_session_owner_if_absent(
            st.session_id,
            {
                "profile_id": st.frozen_owner.owner.profile_id,
                "profile_generation": st.frozen_owner.owner.profile_generation,
                "binding_epoch": st.frozen_owner.binding_epoch,
                "owner_kind": "companion_profile",
            },
        )
        # Semantic growth intent is interpreted by the active model.  Ingress
        # scheduling must not recreate a second keyword/regex classifier.
        st.priority = "normal"
        st.user_message_id = await st.session_db.append_user_message_with_growth_outbox(
            st.session_id,
            st.text,
            trusted_owner={
                "profile_id": st.frozen_owner.owner.profile_id,
                "profile_generation": (st.frozen_owner.owner.profile_generation),
                "binding_epoch": st.frozen_owner.binding_epoch,
                "owner_kind": "companion_profile",
            },
            request_id=st.request_id,
            turn_id=st.turn_id,
            run_id=st.root_ref.run_id,
            priority=st.priority,
        )
        if host_state.companion_ingress_dispatcher is None:
            raise RuntimeError("Companion ingress dispatcher is unavailable")
        if st.vector_worker is not None:
            await st.vector_worker.enqueue(st.user_message_id, st.text)
        await _broadcast_default_chat_peers(
            st.websocket,
            {
                "type": "chat_v2_user_echo",
                "payload": {
                    "session_id": st.session_id,
                    "text": st.text,
                    "request_id": st.request_id,
                    "turn_id": st.turn_id,
                    "run_id": st.root_ref.run_id,
                    "task_scope_id": st.task_scope_id,
                },
            },
        )


def _build_turn_presentation(st: _HarnessChatState) -> None:
    """建本轮输入、展示上下文与产品域输出。"""

    st.turn = TurnInput(
        text=st.text,
        session_id=st.session_id,
        request_id=st.request_id,
        turn_id=st.turn_id,
        venue=st.venue,
        memory_policy=st.memory_policy_override,
        explicit_new=bool(st.task_scope_explicit_new),
        attachment_blocks=tuple(st.user_attachment_blocks),
        provider_ref=str(getattr(st.provider, "model", "") or "") or None,
        capability_ref=f"catalog:{deskpet_tool_registry_v2.catalog_snapshot().revision}",
        # This value comes only from the immutable Session binding resolution;
        # the frontend cannot override it.  Passing the verified root lets
        # ProjectRules and TaskWorkContext consume the same Run authority.
        workspace_ref=st.workspace,
        root_run_id=st.root_ref.run_id,
        task_scope_id=st.task_scope_id,
        active_execution_budget_seconds=_chat_turn_timeout_s(),
        context_usage_basis_sample_id=(st.frozen_context_usage_basis_sample_id),
    )

    st.activity = None
    st.context = RunPresentationContext(
        session_id=st.session_id,
        text=st.text,
        websocket=st.websocket,
        config=config,
        messages=[],
        session_db=st.session_db,
        vector_worker=st.vector_worker,
        activity_store=st.activity,
        provider_chain=st.provider_chain,
        fallback_provider=st.provider,
        request_id=st.request_id,
        max_iterations=50,
        is_sentinel=st.is_sentinel,
        broadcast=_broadcast_default_chat_peers,
        send_final=_send_chat_final,
        emit_context_usage=_sdk_context_usage_from_projection_only,
        intent_label_from_turn=_intent_label_from_turn,
        billing_ledger=billing_ledger,
        provider=st.provider,
        run_id=st.root_ref.run_id,
        task_scope_id=st.task_scope_id,
        provider_binding_epoch=int(st.frozen_session_binding.get("binding_epoch") or 0),
        provider_binding_provider_id=(
            str(st.frozen_session_binding.get("provider_id") or "") or None
        ),
        provider_binding_model_id=(
            str(st.frozen_session_binding.get("preferred_model") or "") or None
        ),
    )
    st.sink = LegacyProductDomainSink(
        websocket=st.websocket,
        services=service_context,
        session_db=st.session_db,
        broadcast=_broadcast_default_chat_peers,
        plan_waiters={},
        tool_registry=deskpet_tool_registry_v2,
    )


async def _prepare_run_context(st: _HarnessChatState) -> None:
    """准备运行：工作区准入、工具目录、身份与上下文快照。"""

    if (
        host_state.sdk_provider_binding_resolver is None
        or host_state.sdk_runtime_catalog is None
        or host_state.sdk_tool_authority_registry is None
        or host_state.sdk_runtime_tool_inventory is None
    ):
        raise RuntimeError("SDK per-Run authority is unavailable")
    st.sdk_run_id = SdkRuntimeIngress._compute_run_id(  # noqa: SLF001
        st.session_id, st.request_id, str(st.turn_id)
    ).value
    if st.workspace_resolution["kind"] == "project_bound":
        await st.binding_service.admit_run(st.workspace_resolution, st.sdk_run_id)
        st.workspace_admitted = True
        st.workspace_resolution["handoff"] = await st.binding_service.claim_handoff(
            st.session_id, st.sdk_run_id
        )
    if st.sdk_run_id in _sdk_unavailable_tool_authority_runs:
        raise RuntimeError("sdk_tool_authority_unavailable")
    st.provider_authority = await _freeze_sdk_provider_authority(
        st.session_db, st.session_id, st.host
    )

    st.run_tool_catalog, st.run_tool_inventory = filter_sdk_catalog_for_workspace(
        host_state.sdk_runtime_catalog,
        host_state.sdk_runtime_tool_inventory,
        workspace_resolution_kind=str(st.workspace_resolution["kind"]),
    )

    st.current_content: Any = st.text
    if st.user_attachment_blocks:
        st.current_content = (
            ContentBlock.from_dict({"type": "text", "text": st.text}),
            *tuple(ContentBlock.from_dict(dict(block)) for block in st.user_attachment_blocks),
        )
    st.identity_authority = service_context.get("memory_identity_authority")
    if st.identity_authority is None:
        raise RuntimeError("validated Memory identity authority is unavailable")
    st.identity = await st.identity_authority.bind(session_id=st.session_id)
    st.persona_text = _resolve_persona(dict(config.raw))
    st.prepared_snapshot = await _prepare_sdk_context_snapshot(
        session_db=st.session_db,
        session_id=st.session_id,
        request_id=st.request_id,
        root_run_id=st.root_ref.run_id,
        sdk_run_id=st.sdk_run_id,
        turn_id=str(st.turn_id),
        text=st.text,
        provider_binding=st.provider_authority,
        catalog=st.run_tool_catalog,
        attachment_blocks=tuple(st.user_attachment_blocks),
        project=st.workspace_resolution,
        task_scope_id=st.task_scope_id,
        persona_text=st.persona_text,
        memory_items=None,
        prepared_skill_scope=st.prepared_skill_scope,
        skill_arguments=st.skill_arguments,
        skill_instruction_resolver=service_context.get("frozen_skill_instruction_resolver"),
    )
    st.context_sources = service_context.get("sdk_context_source_repository")
    if st.context_sources is None:
        raise RuntimeError("SDK context source repository is unavailable")
    st.context_binding_id, st.source_ref = await st.context_sources.put_pending(
        root_run_id=st.sdk_run_id,
        continuation_id=None,
        payload=st.prepared_snapshot.private_record(),
    )
    st.conversation = ConversationTurnInput(
        identity=st.identity,
        message=Message(MessageRole.USER, st.current_content),
        memory_text=st.text,
        context_source_snapshot_ref=st.source_ref,
    )


async def _bind_run_and_skills(st: _HarnessChatState) -> None:
    """建运行绑定、核对直连工具、收集全局技能并登记工具授权。"""

    if st.session_db is not None:
        await st.session_db.put_sdk_context_public_snapshot(st.public_snapshot)
    st.run_binding = host_state.sdk_provider_binding_resolver.create_binding(
        run_id=st.sdk_run_id,
        session_id=st.session_id,
        request_id=st.request_id,
        snapshot_id=st.prepared_snapshot.snapshot_id,
        provider_id=st.provider_authority["provider_id"],
        provider_incarnation_id=st.provider_authority["provider_incarnation_id"],
        provider_config_revision=st.provider_authority["provider_config_revision"],
        binding_epoch=st.provider_authority["binding_epoch"],
        model_id=st.provider_authority["model_id"],
        model_params=st.provider_authority["model_params"],
        context_window=st.provider_authority["context_window"],
        catalog_generation=int(st.run_tool_catalog["generation"]),
        catalog_fingerprint=str(st.run_tool_catalog["content_fingerprint"]),
    )

    st.visible_tool_names = frozenset(str(name) for name in st.run_tool_catalog["tool_names"])
    st.direct_tool_names = st.visible_tool_names & SDK_DIRECT_TOOL_KERNEL
    st.deferred_tool_names = st.visible_tool_names - st.direct_tool_names
    if not {"tool_search", "tool_describe", "tool_activate"} <= st.direct_tool_names:
        raise RuntimeError("SDK direct Tool kernel is incomplete")

    st.global_skill_records = ()
    st.capability_platform = service_context.get("capability_platform")
    st.global_skill_service = service_context.get("project_skill_install_service")
    if st.capability_platform is not None and st.global_skill_service is not None:
        st.global_owner_key = str(st.global_skill_service.global_owner_key)
        st.global_scope = CapabilityScope(user_key=st.global_owner_key)
        async with st.capability_platform.publish_lock:
            (
                st.global_snapshot,
                st._global_entries,
            ) = await st.capability_platform.hub.snapshot_for_atomic_lease(st.global_scope)
            st.global_skill_records = await ProductCapabilityCatalogSourceAdapter().sdk_global_resource_records_from_snapshot(
                store=st.capability_platform.store,
                snapshot=st.global_snapshot,
                owner_key=st.global_owner_key,
                user_scope_key=st.global_owner_key,
            )
        logger.info(
            "sdk_global_skill_catalog_captured count=%s skills=%s",
            len(st.global_skill_records),
            [record.skill_locator for record in st.global_skill_records],
        )

    try:
        host_state.sdk_tool_authority_registry.prepare_run(
            run_id=st.sdk_run_id,
            session_id=st.session_id,
            request_id=st.request_id,
            root_run_id=st.root_ref.run_id,
            task_scope_id=st.task_scope_id,
            workspace_root=st.workspace,
            catalog=st.run_tool_catalog,
            inventory=st.run_tool_inventory,
            deferred_names=st.deferred_tool_names,
            disclosure_policy=SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY,
            binding_version=int(st.workspace_resolution.get("binding_version", 1)),
            workspace_resolution=st.workspace_resolution,
            resource_records=st.global_skill_records,
        )
    except BaseException:
        host_state.sdk_provider_binding_resolver.mark_terminal(st.sdk_run_id, "failed")
        raise


async def _execute_and_settle_context(st: _HarnessChatState) -> None:
    """执行 SDK 运行，按终态消费或暂存上下文绑定。"""

    # Execute Agent via SDK Runtime. Concurrent Sessions resolve their own
    # immutable binding; no global stack mutation or full-Run lock occurs.
    await _execute_sdk_run(
        session_id=st.session_id,
        request_id=st.request_id,
        turn_id=st.turn_id,
        task_scope_id=st.task_scope_id,
        prepared_snapshot=st.prepared_snapshot,
        run_binding=st.run_binding,
        context=st.context,
        websocket=st.websocket,
        root_run_id=st.root_ref.run_id,
        conversation=st.conversation,
        context_stage=None,
        context_binding_id=st.context_binding_id,
    )
    st.context_run = host_state.sdk_ingress.query(st.sdk_run_id)
    st.context_run_state = str(
        getattr(
            getattr(st.context_run, "state", None),
            "value",
            getattr(st.context_run, "state", ""),
        )
    ).lower()
    if st.context_run_state in {"completed", "failed", "cancelled"}:
        await st.context_sources.consume(st.context_binding_id)
    else:
        st.workspace_admission_retained = st.context_run_state == "waiting"
        await st.context_sources.mark_staged(st.context_binding_id)


async def _release_chat_run(st: _HarnessChatState) -> None:
    """收尾：释放工作区准入、关会话、按需投递成长信号。"""

    if st.workspace_admitted and not st.workspace_admission_retained:
        try:
            await st.binding_service.release_run(st.sdk_run_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "project_run_admission_release_failed",
                run_id=st.sdk_run_id,
                error_type=type(exc).__name__,
                exc_info=True,
            )
    if st.session is not None:
        await st.session.close()
    if (
        st.user_message_id is not None
        and st.frozen_owner is not None
        and host_state.companion_ingress_dispatcher is not None
    ):
        try:
            # Idempotent failure fallback. A prior model promotion is
            # monotonic and cannot be downgraded by this "none" settle.
            await host_state.companion_ingress_dispatcher.settle_semantic_intent(
                session_id=st.session_id,
                message_id=st.user_message_id,
                request_id=st.request_id,
                turn_id=st.turn_id,
                owner=st.frozen_owner,
                growth_signal_kind="none",
            )
        except Exception as exc:
            logger.warning(
                "companion_ingress_fallback_settle_failed",
                session_id=st.session_id,
                message_id=st.user_message_id,
                error=str(exc),
                exc_info=True,
            )


async def _run_product_harness_chat_with_timeout(*args, **kwargs) -> None:
    """Compatibility wrapper; durable Run authority owns active-time expiry."""

    bound = inspect.signature(_run_product_harness_chat).bind(*args, **kwargs)
    bound.apply_defaults()
    request_id = str(bound.arguments.get("client_request_id") or "").strip() or uuid.uuid4().hex
    turn_id = str(bound.arguments.get("client_turn_id") or "").strip() or uuid.uuid4().hex
    bound.arguments["client_request_id"] = request_id
    bound.arguments["client_turn_id"] = turn_id
    await _run_product_harness_chat(*bound.args, **bound.kwargs)


def _launch_product_harness_chat(*args, **kwargs) -> asyncio.Task:
    """Launch one presentation consumer; RunKernel owns run/task cancellation."""

    async def guarded() -> None:
        try:
            await _run_product_harness_chat_with_timeout(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            websocket = args[0]
            session_id = str(args[2] if len(args) > 2 else kwargs.get("session_id") or "default")
            request_id = str(kwargs.get("client_request_id") or "")
            turn_id = str(kwargs.get("client_turn_id") or kwargs.get("client_request_id") or "")
            run_id = ""
            task_scope_id = ""
            if request_id and turn_id:
                try:
                    root_ref = RunRef(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"deskpet:{root_idempotency_key(session_id, request_id, turn_id)}",
                        ).hex,
                        session_id,
                    )
                    run_id = root_ref.run_id
                    task_scope_id = TaskWorkContextResolver.task_scope_id(
                        session_id, request_id, turn_id
                    )
                except Exception:  # pragma: no cover - terminal fallback only  # noqa: BLE001 - 只在终态兜底时用到，取不到就不带
                    logger.debug("harness_chat_terminal_ref_failed", exc_info=True)
            try:
                await _send_chat_error(
                    websocket,
                    {
                        "type": "chat_v2_error",
                        "payload": {
                            "error": str(exc) or type(exc).__name__,
                            "detail": type(exc).__name__,
                            "session_id": session_id,
                            "run_id": run_id,
                            "task_scope_id": task_scope_id,
                            "request_id": request_id,
                        },
                    },
                    session_id=session_id,
                    request_id=request_id,
                )
            except Exception:  # noqa: BLE001 - 连接已断时发不出去，不处理
                logger.debug("harness_chat_guard_frame_failed", exc_info=True)
            raise

    task = asyncio.create_task(guarded())

    def report_failure(done: asyncio.Task) -> None:
        if done.cancelled():
            return
        try:
            error = done.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.warning("product_harness_transport_task_failed", error=str(error))

    task.add_done_callback(report_failure)
    return task
