"""SDK 运行恢复：保留运行的看护、恢复看护器、恢复后的投递通道、无法恢复的工具授权隔离、未决授权投影"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from fastapi import WebSocket

from host.chat_delivery import _broadcast_default_chat_peers
from host.runtime import config, logger, service_context
from host.sdk_context import _intent_label_from_turn, _sdk_context_usage_from_projection_only
from host.sdk_registry import (
    _sdk_recovery_watch_tasks,
    _sdk_retained_presentations,
    _sdk_run_ids_by_root,
    _sdk_unavailable_tool_authority_runs,
)
from host.state import host_state


async def _project_open_sdk_authorizations(
    ws: WebSocket,
    *,
    session_id: str | None = None,
    sdk_run_id: str | None = None,
    rehydrated: bool = False,
) -> int:
    """Project durable SDK Tool decisions through the existing desktop UI protocol."""

    ingress = host_state.sdk_ingress
    if ingress is None or not hasattr(ingress, "list_open_authorizations"):
        return 0
    try:
        decisions = ingress.list_open_authorizations(
            run_id=sdk_run_id,
            session_id=session_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "sdk_authorization_projection_failed",
            sdk_run_id=sdk_run_id,
            session_id=session_id,
            error_type=type(exc).__name__,
        )
        return 0
    count = 0
    for decision in decisions:
        root_run_id = str(decision.run_id or "").strip()
        durable_sdk_run_id = str(decision.sdk_run_id or "").strip()
        if not root_run_id or not durable_sdk_run_id:
            logger.warning(
                "sdk_authorization_projection_route_missing",
                decision_id=decision.decision_id,
            )
            continue
        existing_sdk_run_id = _sdk_run_ids_by_root.setdefault(root_run_id, durable_sdk_run_id)
        if existing_sdk_run_id != durable_sdk_run_id:
            logger.error(
                "sdk_authorization_projection_route_conflict",
                decision_id=decision.decision_id,
                root_run_id=root_run_id,
            )
            continue
        frame = {
            "type": "permission_request",
            "payload": {
                "session_id": decision.session_id,
                "run_id": root_run_id,
                "sdk_run_id": durable_sdk_run_id,
                "task_scope_id": decision.task_scope_id,
                "turn_id": decision.turn_id,
                "request_id": decision.request_id,
                "decision_id": decision.decision_id,
                "nonce": decision.nonce,
                "version": decision.version,
                "category": decision.category,
                "summary": (decision.prompt or f"允许 Simple Harness 执行 {decision.tool_name}"),
                "params": {
                    **decision.params,
                    "tool_name": decision.tool_name,
                },
                "default_action": "prompt",
                "dangerous": decision.dangerous,
                "expires_at": decision.expires_at,
                "rehydrated": rehydrated,
            },
        }
        await ws.send_json(frame)
        await _broadcast_default_chat_peers(ws, frame)
        count += 1
    return count


class _RecoveredSdkWebSocket:
    """Detached transport that fans recovered events to current Session peers."""

    async def send_json(self, frame: dict[str, Any]) -> None:
        await _broadcast_default_chat_peers(None, frame)


async def _recovered_sdk_noop_broadcast(_origin: Any, _frame: dict[str, Any]) -> None:
    return None


async def _recovered_sdk_send_final(
    _origin: Any,
    frame: dict[str, Any],
    **_identity: Any,
) -> None:
    await _broadcast_default_chat_peers(None, frame)


def _restore_sdk_delivery_route(record: Any, start: Mapping[str, Any], session_db: Any) -> bool:
    """Rebuild one detached presentation route before SDK startup recovery."""

    from deskpet.agent.run_presenter import (
        CanonicalRunEventPresentationAdapter,
        PresentationState,
        RunPresentationContext,
        build_product_run_presenter,
    )
    from deskpet.sdk_adapters.delivery import ProductDeliveryAdapter
    from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

    start_input = start.get("input")
    metadata = start_input.get("context_metadata") if isinstance(start_input, Mapping) else None
    if not isinstance(metadata, Mapping):
        return False
    run_binding = metadata.get("run_binding")
    if not isinstance(run_binding, Mapping):
        return False
    sdk_run_id = str(record.run_id)
    session_id = str(metadata.get("session_id") or "").strip()
    root_run_id = str(metadata.get("root_run_id") or "").strip()
    request_id = str(metadata.get("request_id") or "").strip()
    task_scope_id = str(metadata.get("task_scope_id") or "").strip()
    if not all((sdk_run_id, session_id, root_run_id, request_id, task_scope_id)):
        return False
    product_input = start_input.get("input")
    text = str(product_input.get("text") or "") if isinstance(product_input, Mapping) else ""
    websocket = _RecoveredSdkWebSocket()
    context = RunPresentationContext(
        session_id=session_id,
        text=text,
        websocket=websocket,
        services=service_context,
        config=config,
        messages=[],
        session_db=session_db,
        vector_worker=service_context.get("vector_worker"),
        activity_store=None,
        provider_chain=None,
        fallback_provider=None,
        request_id=request_id,
        max_iterations=50,
        is_sentinel=False,
        broadcast=_recovered_sdk_noop_broadcast,
        send_final=_recovered_sdk_send_final,
        emit_context_usage=_sdk_context_usage_from_projection_only,
        intent_label_from_turn=_intent_label_from_turn,
        billing_ledger=None,
        provider=None,
        run_id=root_run_id,
        task_scope_id=task_scope_id,
        conversation_boundary_ref=(str(metadata.get("conversation_boundary_ref") or "") or None),
        provider_binding_epoch=int(run_binding.get("binding_epoch") or 0),
        provider_binding_provider_id=str(run_binding.get("provider_id") or "") or None,
        provider_binding_model_id=str(run_binding.get("model_id") or "") or None,
    )
    presenter = build_product_run_presenter()
    adapter = CanonicalRunEventPresentationAdapter()
    state = PresentationState()
    delivery = ProductDeliveryAdapter(
        session_id=session_id,
        request_id=request_id,
        run_id=sdk_run_id,
        presenter=presenter,
        adapter=adapter,
        context=context,
        state=state,
    )
    _delivery_adapters[sdk_run_id] = delivery
    _sdk_run_ids_by_root[root_run_id] = sdk_run_id
    _sdk_retained_presentations[sdk_run_id] = (delivery, presenter, state, context)
    return True


def _isolate_unrestorable_sdk_tool_authority(
    *,
    sdk_run_id: str,
    metadata: Mapping[str, Any],
    provider_binding_resolver: Any,
    tool_authorities: Any,
    error: BaseException,
) -> None:
    """Remove partial recovery leases for one fail-closed legacy Run."""

    from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

    try:
        provider_binding_resolver.mark_terminal(sdk_run_id, "failed")
    except (KeyError, RuntimeError, ValueError):
        pass
    try:
        tool_authorities.mark_terminal(sdk_run_id, "failed")
    except (KeyError, RuntimeError, ValueError):
        pass
    _delivery_adapters.pop(sdk_run_id, None)
    _sdk_retained_presentations.pop(sdk_run_id, None)
    _sdk_unavailable_tool_authority_runs.add(sdk_run_id)
    root_run_id = str(metadata.get("root_run_id") or "")
    if _sdk_run_ids_by_root.get(root_run_id) == sdk_run_id:
        _sdk_run_ids_by_root.pop(root_run_id, None)
    logger.error(
        "sdk_recovery_tool_authority_isolated",
        sdk_run_id=sdk_run_id,
        error_code=getattr(error, "code", type(error).__name__),
    )


async def _watch_retained_sdk_run(
    sdk_run_id: str,
    *,
    authorization_ws: WebSocket | None = None,
) -> None:
    """Project a resumed/recovered Run terminal through its retained route."""

    from agent.agent_loop import ErrorEvent, FinalEvent
    from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters
    from simple_harness import RunId as SdkRunId

    retained = _sdk_retained_presentations.get(sdk_run_id)
    if retained is None or host_state.sdk_ingress is None:
        return
    delivery, presenter, state, context = retained
    terminal_state: str | None = None
    projection_committed = False
    try:
        await host_state.sdk_ingress.wait_idle(sdk_run_id)
        final = host_state.sdk_ingress.query(sdk_run_id)
        raw_state = getattr(final, "state", final)
        state_value = str(getattr(raw_state, "value", raw_state)).lower()
        if state_value == "waiting":
            # One resumed Run may require several sequential Tool decisions
            # (search -> describe -> activate -> execute).  The first waiting
            # projection is emitted by _execute_sdk_run; every later waiting
            # boundary is owned by this recovery watcher.
            if authorization_ws is not None:
                await _project_open_sdk_authorizations(
                    authorization_ws,
                    sdk_run_id=sdk_run_id,
                )
            return
        terminal_state = (
            state_value if state_value in {"completed", "failed", "cancelled"} else "failed"
        )
        if terminal_state == "completed" and host_state.sdk_context_port is not None:
            sdk_context = host_state.sdk_context_port.load(SdkRunId(sdk_run_id))
            assistant_texts = [
                message.content
                for message in sdk_context.messages
                if str(message.role.value) == "assistant" and isinstance(message.content, str)
            ]
            if assistant_texts:
                await presenter.present(FinalEvent(content=assistant_texts[-1]), context, state)
            else:
                await presenter.present(
                    ErrorEvent(
                        reason="run_failed",
                        detail="sdk_recovered_final_missing",
                        error_class="sdk_recovered_final_missing",
                    ),
                    context,
                    state,
                )
                terminal_state = "failed"
        elif terminal_state != "completed":
            await presenter.present(
                ErrorEvent(
                    reason="run_failed",
                    detail=f"sdk_run_{terminal_state}",
                    error_class="run_failed",
                ),
                context,
                state,
            )
        await delivery.finish()
        projection_committed = True
    finally:
        if terminal_state is not None and projection_committed:
            context_sources = service_context.get("sdk_context_source_repository")
            if context_sources is not None:
                try:
                    await context_sources.consume_run(sdk_run_id)
                except Exception as exc:  # retained cleanup retries on later GC
                    logger.warning(
                        "sdk_context_source_terminal_cleanup_deferred",
                        sdk_run_id=sdk_run_id,
                        error=str(exc)[:200],
                    )
            if host_state.sdk_provider_binding_resolver is not None:
                try:
                    host_state.sdk_provider_binding_resolver.mark_terminal(
                        sdk_run_id, terminal_state
                    )
                except KeyError:
                    pass
            if host_state.sdk_tool_authority_registry is not None:
                try:
                    host_state.sdk_tool_authority_registry.mark_terminal(sdk_run_id, terminal_state)
                except KeyError:
                    pass
            _sdk_retained_presentations.pop(sdk_run_id, None)
            _delivery_adapters.pop(sdk_run_id, None)
            root_run_id = str(getattr(context, "run_id", "") or "")
            if _sdk_run_ids_by_root.get(root_run_id) == sdk_run_id:
                _sdk_run_ids_by_root.pop(root_run_id, None)


def _ensure_sdk_recovery_watcher(
    sdk_run_id: str,
    *,
    authorization_ws: WebSocket | None = None,
) -> asyncio.Task | None:
    """Start at most one terminal watcher for a retained presentation route."""

    run_id = str(sdk_run_id or "").strip()
    if not run_id or run_id not in _sdk_retained_presentations:
        return None
    current = _sdk_recovery_watch_tasks.get(run_id)
    if current is not None and not current.done():
        return current
    task = asyncio.create_task(
        _watch_retained_sdk_run(
            run_id,
            authorization_ws=authorization_ws,
        ),
        name=f"sdk-recovered-presentation:{run_id}",
    )
    _sdk_recovery_watch_tasks[run_id] = task

    def settled(_done: asyncio.Task) -> None:
        if _sdk_recovery_watch_tasks.get(run_id) is _done:
            _sdk_recovery_watch_tasks.pop(run_id, None)

    task.add_done_callback(settled)
    return task
