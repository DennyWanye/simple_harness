"""产品对话：项目目录解析、预检拦截提交、桌面 SDK 对话、产品对话主流程与超时、对话启动、自动续跑提示"""

from __future__ import annotations

import asyncio
import inspect
import traceback
import uuid
from pathlib import Path

from deskpet.agent.turn_preparer import TurnInput
from deskpet.sdk_adapters.sdk_candidate import SDK_VERSION

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

    from deskpet.execution.contracts import RunRef, root_idempotency_key

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

    if _sdk_desktop_test_enabled():
        await _run_sdk_desktop_chat(
            websocket,
            text,
            session_id,
            client_request_id=client_request_id,
            client_turn_id=client_turn_id,
        )
        return

    from deskpet.execution.run_block_signals import (
        PreflightBlocked,
        RootBlockReasonV1,
    )

    if host_state.sdk_ingress is None or not host_state.sdk_ingress.accepting:
        raise RuntimeError("SDK Runtime ingress is closed")
    session_db = service_context.get("session_db")
    vector_worker = service_context.get("vector_worker")
    user_message_id = None
    frozen_owner = None
    is_sentinel = (text or "").startswith("<<") and (text or "").endswith(">>")
    request_id = str(client_request_id or "").strip() or uuid.uuid4().hex
    turn_id = str(client_turn_id or client_request_id or "").strip() or uuid.uuid4().hex
    from deskpet.execution.contracts import RunRef, root_idempotency_key
    from deskpet.types.task_work_context import TaskWorkContextResolver

    root_ref = RunRef(
        uuid.uuid5(
            uuid.NAMESPACE_URL, f"deskpet:{root_idempotency_key(session_id, request_id, turn_id)}"
        ).hex,
        session_id,
    )
    task_scope_id = TaskWorkContextResolver.task_scope_id(session_id, request_id, turn_id)
    binding_service = service_context.get("project_binding_service")
    if binding_service is None:
        raise RuntimeError("project_binding_service_unavailable")
    creation_service = service_context.get("session_creation_service")
    if creation_service is None:
        raise RuntimeError("session_creation_service_unavailable")
    # Admission is the migration fence for legacy projectless Sessions. Any
    # allocation/persistence error propagates and prevents a projectless Tool
    # catalog from being constructed for this Run.
    await creation_service.migrate_projectless_session(session_id)
    workspace_binding = await binding_service.resolve_session(session_id)
    if workspace_binding.kind == "project":
        workspace = workspace_binding.effective_root
        workspace_resolution = {
            "kind": "project_bound",
            "session_id": workspace_binding.session_id,
            "project_id": workspace_binding.project_id,
            "project_name": workspace_binding.project_name,
            "project_root": workspace_binding.project_root,
            "execution_kind": workspace_binding.execution_kind,
            "effective_root": workspace_binding.effective_root,
            "project_identity": workspace_binding.project_identity,
            "execution_identity": workspace_binding.execution_identity,
            "project_revision": workspace_binding.project_revision,
            "binding_version": workspace_binding.binding_version,
            "handoff": None,
        }
    elif workspace_binding.kind == "projectless":
        workspace = None
        workspace_resolution = {
            "kind": "projectless",
            "session_id": workspace_binding.session_id,
            "effective_root": None,
            "binding_version": 1,
        }
    else:
        workspace = None
        workspace_resolution = {
            "kind": "missing",
            "session_id": workspace_binding.session_id,
            "project_id": workspace_binding.project_id,
            "project_name": workspace_binding.project_name,
            "project_root": workspace_binding.project_root,
            "execution_kind": workspace_binding.execution_kind,
            "effective_root": workspace_binding.effective_root,
            "project_revision": workspace_binding.project_revision,
            "binding_version": workspace_binding.binding_version,
            "error_code": workspace_binding.error_code,
        }

    # Bind the client turn to its deterministic root Run before provider,
    # host, or Context preparation can block. The UI uses this reservation
    # to keep the new task visible and queues later steering until the
    # conversation boundary is ready.
    reserved = {
        "type": "chat_v2_run_reserved",
        "payload": {
            "session_id": session_id,
            "run_id": root_ref.run_id,
            "request_id": request_id,
            "turn_id": turn_id,
            "task_scope_id": task_scope_id,
            "projection_version": 0,
        },
    }
    reserved_send = websocket.send_json(reserved)
    if inspect.isawaitable(reserved_send):
        await reserved_send
    await _broadcast_default_chat_peers(websocket, reserved)

    venue = "text"
    if workspace_resolution["kind"] == "missing":
        host, _provider, _providers, _provider_snapshot = await _issue_product_harness_host(
            session_id=session_id,
            workspace=None,
            allow_provider_unavailable=True,
            allow_workspace_unavailable=True,
            projectless=True,
        )
        await _commit_product_preflight_block(
            websocket=websocket,
            text=text,
            session_id=session_id,
            request_id=request_id,
            turn_id=turn_id,
            task_scope_id=task_scope_id,
            host=host,
            block=PreflightBlocked(
                RootBlockReasonV1.WORKSPACE_UNAVAILABLE,
                ("workspace-binding:" + str(workspace_resolution["error_code"]),),
            ),
        )
        return
    try:
        host, _provider, _providers, _provider_snapshot = await _issue_product_harness_host(
            session_id=session_id,
            workspace=workspace,
            projectless=workspace_resolution["kind"] == "projectless",
            project_id=str(workspace_resolution.get("project_id") or ""),
            project_revision=int(workspace_resolution.get("project_revision") or 0),
            project_identity=str(workspace_resolution.get("project_identity") or ""),
        )
        provider = _provider
        provider_chain = _providers
        provider_snapshot = _provider_snapshot
    except Exception as exc:
        from llm.resolution import SessionProviderUnavailable

        if isinstance(exc, PreflightBlocked):
            host, _provider, _providers, _provider_snapshot = await _issue_product_harness_host(
                session_id=session_id,
                workspace=workspace,
                allow_workspace_unavailable=True,
                projectless=workspace_resolution["kind"] == "projectless",
                project_id=str(workspace_resolution.get("project_id") or ""),
                project_revision=int(workspace_resolution.get("project_revision") or 0),
                project_identity=str(workspace_resolution.get("project_identity") or ""),
            )
            await _commit_product_preflight_block(
                websocket=websocket,
                text=text,
                session_id=session_id,
                request_id=request_id,
                turn_id=turn_id,
                task_scope_id=task_scope_id,
                host=host,
                block=exc,
            )
            return
        if not isinstance(exc, SessionProviderUnavailable):
            raise
        host, _provider, _providers, _provider_snapshot = await _issue_product_harness_host(
            session_id=session_id,
            workspace=workspace,
            allow_provider_unavailable=True,
            projectless=workspace_resolution["kind"] == "projectless",
            project_id=str(workspace_resolution.get("project_id") or ""),
            project_revision=int(workspace_resolution.get("project_revision") or 0),
            project_identity=str(workspace_resolution.get("project_identity") or ""),
        )
        await _commit_product_preflight_block(
            websocket=websocket,
            text=text,
            session_id=session_id,
            request_id=request_id,
            turn_id=turn_id,
            task_scope_id=task_scope_id,
            host=host,
            block=PreflightBlocked(
                RootBlockReasonV1.PROVIDER_BINDING_UNAVAILABLE,
                ("provider-binding:" + str(getattr(exc, "reason", type(exc).__name__)),),
            ),
        )
        return
    frozen_session_binding = _snapshot_context_usage_binding_for_run(host)
    # TODO: SDK Runtime client.resume() method not yet implemented
    # This is only needed for retry/resume scenarios (when turn_id is provided)
    retry_handle = None  # await _sdk_ingress.require_ready().client.resume(request_id, turn_id, host) if turn_id else None
    frozen_context_usage_basis_sample_id = (
        None
        if retry_handle is not None
        else await _snapshot_context_usage_basis_for_run(session_db, session_id)
    )
    if retry_handle is None and session_db is not None and not is_sentinel:
        frozen_owner = host_state.companion_identity_gate.freeze()
        # New Sessions are bound atomically during creation. This idempotent
        # admission also claims older empty ownerless Sessions created before
        # the dynamic identity resolver fix; messageful or mismatched Sessions
        # remain fail-closed inside SessionDB.
        await session_db.bind_session_owner_if_absent(
            session_id,
            {
                "profile_id": frozen_owner.owner.profile_id,
                "profile_generation": frozen_owner.owner.profile_generation,
                "binding_epoch": frozen_owner.binding_epoch,
                "owner_kind": "companion_profile",
            },
        )
        # Semantic growth intent is interpreted by the active model inside
        # ProductTurnPreparer.  Ingress scheduling must not recreate a second
        # keyword/regex classifier.
        priority = "normal"
        user_message_id = await session_db.append_user_message_with_growth_outbox(
            session_id,
            text,
            trusted_owner={
                "profile_id": frozen_owner.owner.profile_id,
                "profile_generation": (frozen_owner.owner.profile_generation),
                "binding_epoch": frozen_owner.binding_epoch,
                "owner_kind": "companion_profile",
            },
            request_id=request_id,
            turn_id=turn_id,
            run_id=root_ref.run_id,
            priority=priority,
        )
        if host_state.companion_ingress_dispatcher is None:
            raise RuntimeError("Companion ingress dispatcher is unavailable")
        if vector_worker is not None:
            await vector_worker.enqueue(user_message_id, text)
        await _broadcast_default_chat_peers(
            websocket,
            {
                "type": "chat_v2_user_echo",
                "payload": {
                    "session_id": session_id,
                    "text": text,
                    "request_id": request_id,
                    "turn_id": turn_id,
                    "run_id": root_ref.run_id,
                    "task_scope_id": task_scope_id,
                },
            },
        )
    session_context: dict[str, Any] = {"_session_id": session_id}
    if workspace is not None:
        session_context.update(
            _project_root=workspace,
            _write_scope_root=workspace,
        )
    else:
        session_context["_image_worker"] = service_context.get("image_worker")
        write_scope_root = host.write_scope_root
        if write_scope_root:
            session_context["_write_scope_root"] = write_scope_root
    deskpet_tool_registry_v2.set_session_context(session_id, session_context)

    turn = TurnInput(
        text=text,
        session_id=session_id,
        request_id=request_id,
        turn_id=turn_id,
        venue=venue,
        memory_policy=memory_policy_override,
        explicit_new=bool(task_scope_explicit_new),
        attachment_blocks=tuple(user_attachment_blocks),
        provider_ref=str(getattr(provider, "model", "") or "") or None,
        capability_ref=f"catalog:{deskpet_tool_registry_v2.catalog_snapshot().revision}",
        # This value comes only from the immutable Session binding resolution;
        # the frontend cannot override it.  Passing the verified root lets
        # ProjectRules and TaskWorkContext consume the same Run authority.
        workspace_ref=workspace,
        root_run_id=root_ref.run_id,
        task_scope_id=task_scope_id,
        active_execution_budget_seconds=_chat_turn_timeout_s(),
        context_usage_basis_sample_id=(frozen_context_usage_basis_sample_id),
    )
    from deskpet.agent.product_domain_sink import LegacyProductDomainSink
    from deskpet.agent.run_presenter import RunPresentationContext

    activity = None
    context = RunPresentationContext(
        session_id=session_id,
        text=text,
        websocket=websocket,
        services=service_context,
        config=config,
        messages=[],
        session_db=session_db,
        vector_worker=vector_worker,
        activity_store=activity,
        provider_chain=provider_chain,
        fallback_provider=provider,
        request_id=request_id,
        max_iterations=50,
        is_sentinel=is_sentinel,
        broadcast=_broadcast_default_chat_peers,
        send_final=_send_chat_final,
        emit_context_usage=_sdk_context_usage_from_projection_only,
        intent_label_from_turn=_intent_label_from_turn,
        billing_ledger=billing_ledger,
        provider=provider,
        run_id=root_ref.run_id,
        task_scope_id=task_scope_id,
        provider_binding_epoch=int(frozen_session_binding.get("binding_epoch") or 0),
        provider_binding_provider_id=(str(frozen_session_binding.get("provider_id") or "") or None),
        provider_binding_model_id=(
            str(frozen_session_binding.get("preferred_model") or "") or None
        ),
    )
    sink = LegacyProductDomainSink(
        websocket=websocket,
        services=service_context,
        session_db=session_db,
        broadcast=_broadcast_default_chat_peers,
        plan_waiters={},
        tool_registry=deskpet_tool_registry_v2,
    )
    session = None
    workspace_admitted = False
    workspace_admission_retained = False
    try:
        from deskpet.sdk_adapters.context_authority import (
            DefaultDenySnapshotRedactor,
        )
        from deskpet.sdk_adapters.ingress import SdkRuntimeIngress

        if (
            host_state.sdk_provider_binding_resolver is None
            or host_state.sdk_runtime_catalog is None
            or host_state.sdk_tool_authority_registry is None
            or host_state.sdk_runtime_tool_inventory is None
        ):
            raise RuntimeError("SDK per-Run authority is unavailable")
        sdk_run_id = SdkRuntimeIngress._compute_run_id(  # noqa: SLF001
            session_id, request_id, str(turn_id)
        ).value
        if workspace_resolution["kind"] == "project_bound":
            await binding_service.admit_run(workspace_resolution, sdk_run_id)
            workspace_admitted = True
            workspace_resolution["handoff"] = await binding_service.claim_handoff(
                session_id, sdk_run_id
            )
        if sdk_run_id in _sdk_unavailable_tool_authority_runs:
            raise RuntimeError("sdk_tool_authority_unavailable")
        provider_authority = await _freeze_sdk_provider_authority(session_db, session_id, host)
        from deskpet.sdk_adapters.tools import filter_sdk_catalog_for_workspace

        run_tool_catalog, run_tool_inventory = filter_sdk_catalog_for_workspace(
            host_state.sdk_runtime_catalog,
            host_state.sdk_runtime_tool_inventory,
            workspace_resolution_kind=str(workspace_resolution["kind"]),
        )
        from deskpet.agent.assembler.components.persona import _resolve_persona
        from simple_harness.contracts.messages import (
            ContentBlock,
            Message,
            MessageRole,
        )
        from simple_harness.runtime import ConversationTurnInput

        current_content: Any = text
        if user_attachment_blocks:
            current_content = (
                ContentBlock.from_dict({"type": "text", "text": text}),
                *tuple(ContentBlock.from_dict(dict(block)) for block in user_attachment_blocks),
            )
        identity_authority = service_context.get("memory_identity_authority")
        if identity_authority is None:
            raise RuntimeError("validated Memory identity authority is unavailable")
        identity = await identity_authority.bind(session_id=session_id)
        persona_text = _resolve_persona(dict(config.raw))
        prepared_snapshot = await _prepare_sdk_context_snapshot(
            session_db=session_db,
            session_id=session_id,
            request_id=request_id,
            root_run_id=root_ref.run_id,
            sdk_run_id=sdk_run_id,
            turn_id=str(turn_id),
            text=text,
            provider_binding=provider_authority,
            catalog=run_tool_catalog,
            attachment_blocks=tuple(user_attachment_blocks),
            project=workspace_resolution,
            task_scope_id=task_scope_id,
            persona_text=persona_text,
            memory_items=None,
            prepared_skill_scope=prepared_skill_scope,
            skill_arguments=skill_arguments,
            skill_instruction_resolver=service_context.get("frozen_skill_instruction_resolver"),
        )
        context_sources = service_context.get("sdk_context_source_repository")
        if context_sources is None:
            raise RuntimeError("SDK context source repository is unavailable")
        context_binding_id, source_ref = await context_sources.put_pending(
            root_run_id=sdk_run_id,
            continuation_id=None,
            payload=prepared_snapshot.private_record(),
        )
        conversation = ConversationTurnInput(
            identity=identity,
            message=Message(MessageRole.USER, current_content),
            memory_text=text,
            context_source_snapshot_ref=source_ref,
        )
        from deskpet.sdk_adapters.context_authority import (
            DefaultDenySnapshotRedactor,
        )

        public_snapshot = DefaultDenySnapshotRedactor().redact(prepared_snapshot)
        if session_db is not None:
            await session_db.put_sdk_context_public_snapshot(public_snapshot)
        run_binding = host_state.sdk_provider_binding_resolver.create_binding(
            run_id=sdk_run_id,
            session_id=session_id,
            request_id=request_id,
            snapshot_id=prepared_snapshot.snapshot_id,
            provider_id=provider_authority["provider_id"],
            provider_incarnation_id=provider_authority["provider_incarnation_id"],
            provider_config_revision=provider_authority["provider_config_revision"],
            binding_epoch=provider_authority["binding_epoch"],
            model_id=provider_authority["model_id"],
            model_params=provider_authority["model_params"],
            context_window=provider_authority["context_window"],
            catalog_generation=int(run_tool_catalog["generation"]),
            catalog_fingerprint=str(run_tool_catalog["content_fingerprint"]),
        )
        from deskpet.sdk_adapters.tool_authority import (
            SDK_DIRECT_TOOL_KERNEL,
            SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY,
        )

        visible_tool_names = frozenset(str(name) for name in run_tool_catalog["tool_names"])
        direct_tool_names = visible_tool_names & SDK_DIRECT_TOOL_KERNEL
        deferred_tool_names = visible_tool_names - direct_tool_names
        if not {"tool_search", "tool_describe", "tool_activate"} <= direct_tool_names:
            raise RuntimeError("SDK direct Tool kernel is incomplete")

        global_skill_records = ()
        capability_platform = service_context.get("capability_platform")
        global_skill_service = service_context.get("project_skill_install_service")
        if capability_platform is not None and global_skill_service is not None:
            from deskpet.capabilities.contracts import CapabilityScope
            from deskpet.sdk_adapters.capability_catalog import (
                ProductCapabilityCatalogSourceAdapter,
            )

            global_owner_key = str(global_skill_service.global_owner_key)
            global_scope = CapabilityScope(user_key=global_owner_key)
            async with capability_platform.publish_lock:
                (
                    global_snapshot,
                    _global_entries,
                ) = await capability_platform.hub.snapshot_for_atomic_lease(global_scope)
                global_skill_records = await ProductCapabilityCatalogSourceAdapter().sdk_global_resource_records_from_snapshot(
                    store=capability_platform.store,
                    snapshot=global_snapshot,
                    owner_key=global_owner_key,
                    user_scope_key=global_owner_key,
                )
            logger.info(
                "sdk_global_skill_catalog_captured count=%s skills=%s",
                len(global_skill_records),
                [record.skill_locator for record in global_skill_records],
            )

        try:
            host_state.sdk_tool_authority_registry.prepare_run(
                run_id=sdk_run_id,
                session_id=session_id,
                request_id=request_id,
                root_run_id=root_ref.run_id,
                task_scope_id=task_scope_id,
                workspace_root=workspace,
                catalog=run_tool_catalog,
                inventory=run_tool_inventory,
                deferred_names=deferred_tool_names,
                disclosure_policy=SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY,
                binding_version=int(workspace_resolution.get("binding_version", 1)),
                workspace_resolution=workspace_resolution,
                resource_records=global_skill_records,
            )
        except BaseException:
            host_state.sdk_provider_binding_resolver.mark_terminal(sdk_run_id, "failed")
            raise
        # Execute Agent via SDK Runtime. Concurrent Sessions resolve their own
        # immutable binding; no global stack mutation or full-Run lock occurs.
        await _execute_sdk_run(
            session_id=session_id,
            request_id=request_id,
            turn_id=turn_id,
            task_scope_id=task_scope_id,
            prepared_snapshot=prepared_snapshot,
            run_binding=run_binding,
            context=context,
            websocket=websocket,
            root_run_id=root_ref.run_id,
            conversation=conversation,
            context_stage=None,
            context_binding_id=context_binding_id,
        )
        context_run = host_state.sdk_ingress.query(sdk_run_id)
        context_run_state = str(
            getattr(
                getattr(context_run, "state", None),
                "value",
                getattr(context_run, "state", ""),
            )
        ).lower()
        if context_run_state in {"completed", "failed", "cancelled"}:
            await context_sources.consume(context_binding_id)
        else:
            workspace_admission_retained = context_run_state == "waiting"
            await context_sources.mark_staged(context_binding_id)
    except PreflightBlocked as block:
        await _commit_product_preflight_block(
            websocket=websocket,
            text=text,
            session_id=session_id,
            request_id=request_id,
            turn_id=turn_id,
            task_scope_id=task_scope_id,
            host=host,
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
            session_id,
            str(exc),
            exc_info=True,
            traceback=traceback.format_exc(),
        )
        try:
            await _send_chat_error(
                websocket,
                {
                    "type": "chat_v2_error",
                    "payload": {
                        "error": str(exc) or type(exc).__name__,
                        "detail": type(exc).__name__,
                        "session_id": session_id,
                        "run_id": root_ref.run_id,
                        "task_scope_id": task_scope_id,
                        "request_id": request_id,
                    },
                },
                session_id=session_id,
                request_id=request_id,
            )
        except Exception:
            pass
    finally:
        if workspace_admitted and not workspace_admission_retained:
            try:
                await binding_service.release_run(sdk_run_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "project_run_admission_release_failed",
                    run_id=sdk_run_id,
                    error_type=type(exc).__name__,
                )
        if session is not None:
            await session.close()
        if (
            user_message_id is not None
            and frozen_owner is not None
            and host_state.companion_ingress_dispatcher is not None
        ):
            try:
                # Idempotent failure fallback. A prior model promotion is
                # monotonic and cannot be downgraded by this "none" settle.
                await host_state.companion_ingress_dispatcher.settle_semantic_intent(
                    session_id=session_id,
                    message_id=user_message_id,
                    request_id=request_id,
                    turn_id=turn_id,
                    owner=frozen_owner,
                    growth_signal_kind="none",
                )
            except Exception as exc:
                logger.warning(
                    "companion_ingress_fallback_settle_failed",
                    session_id=session_id,
                    message_id=user_message_id,
                    error=str(exc),
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
                    from deskpet.execution.contracts import RunRef, root_idempotency_key
                    from deskpet.types.task_work_context import TaskWorkContextResolver

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
                except Exception:  # pragma: no cover - terminal fallback only
                    pass
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
            except Exception:
                pass
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


async def _enqueue_auto_resume_hint(sid: str, messages: list[dict]) -> None:
    content = next(
        (
            str(item.get("content") or "")
            for item in reversed(messages)
            if item.get("_is_supervisor_hint")
        ),
        "",
    )
    text = content.removeprefix("[Supervisor Hint] ")
    queue = service_context.get("nudge_queue")
    if not text or queue is None:
        return
    try:
        from agent.nudge_queue import Hint

        await queue.push(sid, Hint(text=text, alert_id="auto_resume", severity="yellow"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("auto_resume_hint_push_failed sid=%s err=%s", sid, exc)
