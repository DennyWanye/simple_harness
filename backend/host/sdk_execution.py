"""SDK 执行：单次运行执行、提供方授权冻结与绑定解析、上下文用量绑定快照、桌面测试桥、身份绑定后恢复、本机所有者认证"""

from __future__ import annotations

from collections.abc import Mapping

import paths as _paths
from agent.agent_loop import FinalEvent
from deskpet.agent.run_presenter import (
    CanonicalRunEventPresentationAdapter,
    PresentationState,
    build_product_run_presenter,
)
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.sdk_adapters.delivery import ProductDeliveryAdapter
from deskpet.sdk_adapters.desktop_runtime import DesktopSdkRuntimeBridge, _delivery_adapters
from deskpet.sdk_adapters.ingress import SdkRuntimeIngress
from deskpet.sdk_adapters.provider import provider_endpoint_identity
from deskpet.sdk_adapters.sdk_candidate import SDK_VERSION
from llm.code_params import code_params_to_request
from llm.model_catalog import model_context_window
from llm.provider_registry import ProviderMutationConflict
from simple_harness import RunId as SdkRunId

from host.chat_delivery import _broadcast_default_chat_peers, _send_chat_error
from host.logging_setup import _sdk_observability
from host.runtime import logger, service_context
from host.sdk_activation import _issue_product_harness_host
from host.sdk_recovery import _project_open_sdk_authorizations
from host.sdk_registry import (
    _sdk_cancel_requested_run_ids,
    _sdk_retained_presentations,
    _sdk_run_ids_by_root,
)
from host.state import host_state


def _assert_sdk_provider_mutation_allowed(provider_ids: Any) -> None:
    """Fence mutations that would invalidate active or WAITING Run leases."""

    if host_state.sdk_provider_binding_resolver is None:
        return
    active = host_state.sdk_provider_binding_resolver.active_provider_ids()
    conflicts = active.intersection(str(item) for item in provider_ids)
    if conflicts:
        raise ProviderMutationConflict(sorted(conflicts)[0])


def _sdk_desktop_test_enabled() -> bool:
    # SDK测试模式已完全禁用（2026-08-17）。
    # 该模式造成用户困惑且限制核心功能（只暴露2个工具）。
    # SDK集成测试应使用专门的测试脚本，不在生产代码中保留受限分支。
    return False


def _initialize_sdk_desktop_test_bridge() -> None:
    """Install the narrow SDK desktop-test ingress without changing normal boots."""

    if not _sdk_desktop_test_enabled():
        return
    provider_registry = service_context.get("provider_registry")
    if provider_registry is None:
        raise RuntimeError("SDK desktop test requires the product provider registry")

    host_state.sdk_desktop_bridge = DesktopSdkRuntimeBridge(
        user_data_root=_paths.user_data_dir(),
        provider_registry=provider_registry,
        ready_publisher=lambda ready: service_context.register("sdk_runtime_ready", ready),
    )
    logger.info(
        "sdk_desktop_test_bridge_ready",
        sdk_version=SDK_VERSION,
        ingress="lazy",
        tools=("process_list", "ppt_create"),
    )


def _trigger_harness_recovery_after_identity_bind() -> bool:
    """Retry durable Runs only after the process identity gate is open."""

    if host_state.sdk_ingress is None:
        return False
    reconciler = getattr(host_state.sdk_ingress, "reconciler", None)
    trigger = getattr(reconciler, "trigger", None)
    if not callable(trigger):
        return False
    trigger()
    return True


def _local_owner_auth():
    """Single Host authority template for the authenticated local owner."""

    return local_owner_auth()


def _resolve_sdk_provider_binding_resolver():  # type: ignore[no-untyped-def]
    """The Run→Provider binding resolver: service_context slot first, module global second.

    S5b Task 6 P0: the slot is registered by `_build_product_sdk_runtime_stack`
    while the module global is only assigned after the stack is built, so any
    composition step running during the build must read the slot.
    """

    resolver = service_context.get("sdk_provider_binding_resolver")
    return resolver if resolver is not None else host_state.sdk_provider_binding_resolver


def _provider_endpoint_identity_for_binding(record):  # type: ignore[no-untyped-def]
    """Endpoint identity of the Run's provider entry (same recipe as ``ProductProviderAdapter``)."""

    registry = host_state.provider_registry
    if registry is None:
        return None
    entry = registry.get_entry(str(record.get("provider_id") or ""))
    if entry is None:
        return None
    return provider_endpoint_identity(entry)


def _snapshot_context_usage_binding_for_run(host: Any) -> dict[str, Any]:
    """Read Context provenance from the immutable provider start snapshot."""

    bindings = tuple(getattr(host, "provider_bindings", ()) or ())
    if not bindings:
        return {}
    binding = bindings[0]
    if not isinstance(binding, (tuple, list)) or len(binding) < 5:
        # Context provenance must come from the typed frozen HostContext
        # binding.  Do not guess from provider_plan names or legacy pairs.
        return {}
    provider_id, model_id, incarnation_id, revision, binding_epoch = binding[:5]
    provider_id = str(provider_id or "").strip()
    model_id = str(model_id or "").strip()
    if not provider_id or not model_id:
        return {}
    return {
        "provider_id": provider_id,
        "preferred_model": model_id,
        "provider_incarnation_id": str(incarnation_id or ""),
        "provider_config_revision": int(revision or 0),
        "binding_epoch": int(binding_epoch or 0),
    }


async def _freeze_sdk_provider_authority(
    session_db: Any, session_id: str, host: Any
) -> dict[str, Any]:
    """Project the already-resolved Host route into one immutable SDK binding."""

    frozen = _snapshot_context_usage_binding_for_run(host)
    model_id = str(frozen.get("preferred_model") or "")
    session_binding = await session_db.get_session_provider_binding_authority(session_id)
    if int(session_binding.get("binding_epoch") or 0) != int(frozen.get("binding_epoch") or 0):
        raise RuntimeError("sdk_provider_binding_changed_during_start")
    for session_key, frozen_key in (
        ("provider_id", "provider_id"),
        ("preferred_model", "preferred_model"),
    ):
        selected = str(session_binding.get(session_key) or "")
        if selected and selected != str(frozen.get(frozen_key) or ""):
            raise RuntimeError("sdk_provider_binding_changed_during_start")
    model_params = dict(session_binding.get("model_params") or {})
    request_params = code_params_to_request(model_params)
    extra_body = request_params.get("extra_body")
    context_override = (
        int(extra_body.get("context_window") or 0) if isinstance(extra_body, Mapping) else 0
    )
    context_window = context_override or model_context_window(model_id)
    if not context_window:
        raise RuntimeError("sdk_context_window_unavailable")
    return {
        "provider_id": str(frozen["provider_id"]),
        "model_id": model_id,
        "provider_incarnation_id": str(frozen["provider_incarnation_id"]),
        "provider_config_revision": int(frozen["provider_config_revision"]),
        "binding_epoch": int(frozen["binding_epoch"]),
        "model_params": model_params,
        "context_window": int(context_window),
    }


async def _snapshot_context_usage_basis_for_run(session_db: Any, session_id: str) -> str | None:
    """Freeze the exact measured sample a new durable Run may compact."""

    if session_db is None or not hasattr(session_db, "get_context_usage_state"):
        return None
    authority = await session_db.get_context_usage_state(session_id)
    if not bool(authority.get("has_measurement")):
        return None
    return str(authority.get("sample_id") or "").strip() or None


async def _voice_run_host(session_id: str):
    if host_state.sdk_ingress is None:
        raise RuntimeError("SDK Runtime is unavailable")
    host, _provider, _providers, _provider_snapshot = await _issue_product_harness_host(
        session_id=session_id, workspace=None
    )
    return host


async def _execute_sdk_run(
    *,
    session_id: str,
    request_id: str,
    turn_id: int,
    task_scope_id: str | None,
    prepared_snapshot: Any,
    run_binding: Any,
    context: Any,  # RunPresentationContext
    websocket: Any,  # WebSocket
    root_run_id: str,
    conversation: Any | None = None,
    context_stage: Any | None = None,
    context_binding_id: str | None = None,
) -> None:
    """Execute Agent via SDK Runtime with event delivery to WebSocket.

    Replaces old ProductVenueRunAdapter.open() flow. Creates a
    ProductDeliveryAdapter, registers it in the global registry,
    starts SDK Runtime execution, and waits for completion.

    Args:
        session_id: Session identifier
        request_id: Request identifier
        turn_id: Turn number
        task_scope_id: Optional task scope identifier
        text: User input text
        context: RunPresentationContext with all presentation services
        websocket: WebSocket connection for real-time delivery
        root_run_id: Root run identifier

    Raises:
        SdkRuntimeNotReady: If SDK Runtime ingress is not ready
        Exception: Any SDK Runtime execution errors
    """

    # The frozen tool authority is keyed by the SDK's deterministic physical
    # Run identity, so compute it before reading any authority into RunStart.
    sdk_run_id = SdkRuntimeIngress._compute_run_id(  # noqa: SLF001
        session_id,
        request_id,
        str(turn_id),
    ).value
    private = prepared_snapshot.private_record()
    catalog = dict(private["catalog"])
    budget = dict(private["budget"])
    tool_authority = (
        host_state.sdk_tool_authority_registry.resolve(sdk_run_id)
        if host_state.sdk_tool_authority_registry is not None
        else None
    )
    if host_state.sdk_tool_authority_registry is not None and tool_authority is None:
        raise RuntimeError("SDK Run Tool authority is unavailable")
    payload = {
        "input": {"text": str(getattr(context, "text", ""))},
        "messages": private["provider_messages"],
        "capability_snapshot": {"tools": list(catalog["tool_names"])},
        "context_metadata": {
            "session_id": session_id,
            "root_run_id": root_run_id,
            "request_id": request_id,
            "task_scope_id": task_scope_id,
            "workspace_root": (
                tool_authority.task_work_context.workspace_root
                if tool_authority is not None
                else None
            ),
            "workspace_binding_version": (
                tool_authority.task_work_context.binding_version
                if tool_authority is not None
                else 1
            ),
            "conversation_boundary_ref": context.conversation_boundary_ref,
            "conversation_boundary_version": 1,
            "snapshot_id": prepared_snapshot.snapshot_id,
            "binding_epoch": run_binding.binding_epoch,
            "context_window": run_binding.context_window,
            "effective_ceiling": int(budget.get("effective_ceiling") or 0),
            "run_binding": run_binding.to_record(),
            "tool_authority": (
                tool_authority.run_start_record() if tool_authority is not None else None
            ),
        },
    }

    # Create presentation infrastructure
    presenter = build_product_run_presenter()
    adapter = CanonicalRunEventPresentationAdapter()
    state = PresentationState()

    # SDK owns a deterministic internal execution id, while the product UI and
    # SessionDB use the canonical root id reserved by Host. Predict the SDK id
    # from the ingress's single identity function so the delivery bridge can be
    # registered *before* start() makes the Run visible to a worker. Registering
    # after awaiting start() races a fast first Provider/tool turn and silently
    # drops its public narration and tool lifecycle.
    # Create ProductDeliveryAdapter for this run
    delivery_adapter = ProductDeliveryAdapter(
        session_id=session_id,
        request_id=request_id,
        run_id=sdk_run_id,
        presenter=presenter,
        adapter=adapter,
        context=context,
        state=state,
    )

    # Register adapter in global registry before SDK start (for Provider,
    # ProductToolsAdapter, and _DeliverySink routing).
    _delivery_adapters[sdk_run_id] = delivery_adapter
    _sdk_run_ids_by_root[root_run_id] = sdk_run_id
    terminal_binding_state: str | None = "failed"
    observability_token = _sdk_observability.bind_ingress(
        run_id=sdk_run_id,
        session_id=session_id,
        request_id=request_id,
    )

    try:
        receipt = await host_state.sdk_ingress.start(
            session_id=session_id,
            request_id=request_id,
            turn_id=str(turn_id),
            payload=payload,
            session_generation=run_binding.catalog_generation,
            tool_catalog_fingerprint=run_binding.catalog_fingerprint,
            provider_budget_fingerprint=run_binding.budget_fingerprint,
            conversation=conversation,
            context_preparation_mode=("consumer_prepared" if context_stage is not None else None),
            context_stage_id=(str(context_stage.stage_id) if context_stage is not None else None),
            context_stage_hash=(
                str(context_stage.private_snapshot_hash) if context_stage is not None else None
            ),
            prepared_context=(
                dict(context_stage.private_snapshot) if context_stage is not None else None
            ),
        )
        if receipt.run_id != sdk_run_id:
            raise RuntimeError("SDK ingress returned an unexpected Run identity")
        if context_binding_id is not None:
            context_sources = service_context.get("sdk_context_source_repository")
            if context_sources is None:
                raise RuntimeError("SDK context source repository is unavailable")
            await context_sources.mark_claimed(
                context_binding_id,
                claim_token=receipt.run_id,
            )

        # Send run_started event to WebSocket
        started = {
            "type": "chat_v2_run_started",
            "payload": {
                "session_id": session_id,
                "run_id": root_run_id,
                "request_id": request_id,
                "turn_id": turn_id,
                "task_scope_id": task_scope_id,
                "conversation_boundary_ref": context.conversation_boundary_ref,
                "conversation_boundary_version": 1,
                "projection_version": 0,
            },
        }
        await websocket.send_json(started)
        await _broadcast_default_chat_peers(websocket, started)

        logger.info(
            "sdk_run_started",
            session_id=session_id,
            request_id=request_id,
            sdk_run_id=sdk_run_id,
            root_run_id=root_run_id,
        )

        # Wait for SDK Runtime to complete
        # Events are pushed via DeliveryDispatcher → _DeliverySink → ProductDeliveryAdapter
        await host_state.sdk_ingress.wait_idle(sdk_run_id)

        # Query final state
        final_state = host_state.sdk_ingress.query(sdk_run_id)
        sdk_state = getattr(final_state, "state", final_state)
        sdk_state_value = str(getattr(sdk_state, "value", sdk_state))
        if sdk_run_id in _sdk_cancel_requested_run_ids:
            # chat_v2_interrupt owns the canonical cancelled projection and
            # acknowledgement. Do not race it with a transient SDK
            # waiting/running read and manufacture run_failed in the UI.
            logger.info(
                "sdk_run_user_cancelled",
                session_id=session_id,
                request_id=request_id,
                root_run_id=root_run_id,
                sdk_run_id=sdk_run_id,
                observed_state=sdk_state_value,
            )
            terminal_binding_state = "cancelled"
            return
        if sdk_state_value == "waiting":
            terminal_binding_state = None
            _sdk_retained_presentations[sdk_run_id] = (delivery_adapter, presenter, state, context)
            if host_state.sdk_provider_binding_resolver is not None:
                host_state.sdk_provider_binding_resolver.mark_waiting(sdk_run_id)
            if host_state.sdk_tool_authority_registry is not None:
                host_state.sdk_tool_authority_registry.mark_waiting(sdk_run_id)
            await _project_open_sdk_authorizations(
                websocket,
                sdk_run_id=sdk_run_id,
            )
            logger.info(
                "sdk_run_waiting_binding_retained",
                sdk_run_id=sdk_run_id,
                root_run_id=root_run_id,
            )
            return
        if sdk_state_value != "completed":
            terminal_binding_state = (
                sdk_state_value if sdk_state_value in {"failed", "cancelled"} else "failed"
            )
            # A failed/cancelled SDK run has no assistant text to present.
            # Emit the product terminal error instead of an empty final frame,
            # so the UI can settle the canonical root projection visibly.
            await _send_chat_error(
                websocket,
                {
                    "type": "chat_v2_error",
                    "payload": {
                        "session_id": session_id,
                        "run_id": root_run_id,
                        "request_id": request_id,
                        "task_scope_id": task_scope_id,
                        "error": "run_failed",
                        "detail": f"sdk_run_{sdk_state_value}",
                    },
                },
                session_id=session_id,
                request_id=request_id,
            )
            logger.warning(
                "sdk_run_terminal_error_projected",
                session_id=session_id,
                request_id=request_id,
                root_run_id=root_run_id,
                sdk_run_id=sdk_run_id,
                state=sdk_state_value,
            )
            return

        # SDK 的 ReAct driver 不产生 delivery 事件，assistant 文本只落在 SDK 的
        # context 里。这里手动把它桥接回 presenter，走 _present_final 的
        # 「写 SessionDB + 推 WebSocket」既有展示链路。
        if host_state.sdk_context_port is not None:
            try:
                sdk_context = host_state.sdk_context_port.load(SdkRunId(sdk_run_id))
                assistant_texts = [
                    message.content
                    for message in sdk_context.messages
                    if str(message.role.value) == "assistant"
                ]
                if assistant_texts:
                    await presenter.present(
                        FinalEvent(content=assistant_texts[-1]),
                        context,
                        state,
                    )
            except Exception as _assistant_exc:  # noqa: BLE001
                logger.warning(
                    "sdk_assistant_presentation_failed",
                    error=str(_assistant_exc)[:200],
                )

        # Finalize presentation
        await delivery_adapter.finish()
        terminal_binding_state = "completed"

        logger.info(
            "sdk_run_completed",
            session_id=session_id,
            request_id=request_id,
            sdk_run_id=sdk_run_id,
            root_run_id=root_run_id,
            state=final_state.value if hasattr(final_state, "value") else str(final_state),
        )

    finally:
        _sdk_observability.reset_ingress(observability_token)
        if (
            terminal_binding_state is not None
            and host_state.sdk_provider_binding_resolver is not None
        ):
            try:
                host_state.sdk_provider_binding_resolver.mark_terminal(
                    sdk_run_id, terminal_binding_state
                )
            except KeyError:
                pass
        if (
            terminal_binding_state is not None
            and host_state.sdk_tool_authority_registry is not None
        ):
            try:
                host_state.sdk_tool_authority_registry.mark_terminal(
                    sdk_run_id, terminal_binding_state
                )
            except KeyError:
                pass
        # WAITING is durable and resumable: retain both delivery and identity
        # routes until a signal causes the Run to reach a terminal state.
        if terminal_binding_state is not None:
            _sdk_retained_presentations.pop(sdk_run_id, None)
            _delivery_adapters.pop(sdk_run_id, None)
            if _sdk_run_ids_by_root.get(root_run_id) == sdk_run_id:
                _sdk_run_ids_by_root.pop(root_run_id, None)
            _sdk_cancel_requested_run_ids.discard(sdk_run_id)
        logger.debug(
            "sdk_run_adapter_cleanup",
            sdk_run_id=sdk_run_id,
            root_run_id=root_run_id,
            remaining_adapters=len(_delivery_adapters),
        )
        await _sdk_observability.export_async()
