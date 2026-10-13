"""产品运行控制：取消运行、权威运行投影、在途子运行判断、决定信号、执行工作流决定、续跑与续跑启动"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from deskpet.memory.primary_decisions import is_primary_sdk_target
from deskpet.sdk_adapters.causal_groups import plan_recent_causal_groups
from deskpet.sdk_adapters.context_partitions import effective_input_budget
from fastapi import WebSocket
from simple_harness import RunId as SdkRunId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.runtime import ConversationContinuationInput

from host.chat_delivery import _broadcast_default_chat_peers, _send_chat_error
from host.runtime import logger, service_context
from host.sdk_context import _bounded_sdk_history
from host.sdk_recovery import _ensure_sdk_recovery_watcher
from host.sdk_registry import (
    _sdk_cancel_requested_run_ids,
    _sdk_retained_presentations,
    _sdk_run_ids_by_root,
)
from host.state import host_state
from host.stores_bootstrap import _state_db_path


async def _cancel_product_harness_run(
    session_id: str,
    run_id: str,
    *,
    reason: str,
) -> bool:
    """Cancel one authenticated Run without consulting a process-local task map."""

    if host_state.sdk_ingress is None or not host_state.sdk_ingress.accepting or not run_id:
        return False
    # SDK RunClient.cancel(run_id) resolves the persisted Run and its frozen
    # start snapshot (which carries the session identity), so cancellation is
    # already authenticated by run_id alone — no host / expected_session_id is
    # needed (the old harness signature took those, the SDK one does not).

    sdk_run_id = _sdk_run_ids_by_root.get(run_id, run_id)
    logger.info(
        "product_harness_cancel",
        run_id=run_id,
        sdk_run_id=sdk_run_id,
        reason=reason,
    )
    _sdk_cancel_requested_run_ids.add(sdk_run_id)
    try:
        cancelled_record = await host_state.sdk_ingress.require_ready().client.cancel(
            SdkRunId(sdk_run_id)
        )
        cancelled_state = str(
            getattr(
                getattr(cancelled_record, "state", None),
                "value",
                getattr(cancelled_record, "state", ""),
            )
        ).lower()
    except KeyError:
        # A stale/terminal projection or a legacy caller may no longer have a
        # live SDK identity. Treat that as an idempotent miss instead of
        # tearing down the control WebSocket.
        logger.info(
            "product_harness_cancel_not_found",
            run_id=run_id,
            sdk_run_id=sdk_run_id,
            reason=reason,
        )
        _sdk_cancel_requested_run_ids.discard(sdk_run_id)
        return False
    if cancelled_state != "cancelled":
        logger.info(
            "product_harness_cancel_not_applied",
            run_id=run_id,
            sdk_run_id=sdk_run_id,
            state=cancelled_state,
            reason=reason,
        )
        _sdk_cancel_requested_run_ids.discard(sdk_run_id)
        return False
    provider_workload_router = service_context.get("provider_workload_router")
    if provider_workload_router is not None:
        await provider_workload_router.cancel_root(run_id)
    session_db = service_context.get("session_db")
    if session_db is not None:
        try:
            await session_db.clear_session_plan_awaiting(session_id)
        except Exception as exc:  # sidecar cleanup must not undo Run cancel
            logger.warning(
                "cancelled_run_plan_sidecar_clear_failed",
                session_id=session_id,
                run_id=run_id,
                error=str(exc),
            )
    return True


def _authoritative_sdk_run_projection(root_run_id: str, sdk_run_id: str = "") -> dict:
    """Read one Run projection from the SDK authority, never UI/process state."""

    resolved_sdk_run_id = str(
        sdk_run_id or _sdk_run_ids_by_root.get(root_run_id, root_run_id)
    ).strip()
    if (
        host_state.sdk_ingress is None
        or not host_state.sdk_ingress.accepting
        or not resolved_sdk_run_id
    ):
        return {
            "sdk_run_id": resolved_sdk_run_id,
            "run_version": 0,
            "state": "unavailable",
            "waiting_reason": "sdk_runtime_unavailable",
            "allowed_actions": ["cancel"],
            "retryable": True,
            "terminal_event_ref": None,
        }
    record = host_state.sdk_ingress.query(resolved_sdk_run_id)
    if record is None:
        return {
            "sdk_run_id": resolved_sdk_run_id,
            "run_version": 0,
            "state": "unknown",
            "waiting_reason": "run_not_found",
            "allowed_actions": ["cancel"],
            "retryable": False,
            "terminal_event_ref": None,
        }
    raw_state = getattr(record, "state", record)
    state = str(getattr(raw_state, "value", raw_state)).lower()
    version = int(getattr(record, "version", 0) or 0)
    terminal = state in {"completed", "failed", "cancelled"}
    retained = resolved_sdk_run_id in _sdk_retained_presentations
    waiting_reason = None
    allowed_actions: list[str] = []
    if state == "waiting":
        waiting_reason = "sdk_recovery_pending" if retained else "unowned_waiting"
        allowed_actions = ["resume", "cancel"] if retained else ["cancel"]
    elif not terminal:
        allowed_actions = ["cancel"]
    return {
        "sdk_run_id": resolved_sdk_run_id,
        "run_version": version,
        "state": state,
        "waiting_reason": waiting_reason,
        "allowed_actions": allowed_actions,
        "retryable": state in {"waiting", "unknown", "unavailable"},
        "terminal_event_ref": (
            f"sdk-run:{resolved_sdk_run_id}:terminal:v{version}" if terminal else None
        ),
    }


async def _product_harness_has_live_attached_child(
    session_id: str,
    run_id: str,
) -> bool:
    """Legacy diagnostic: report a non-terminal attached child Run.

    Active-budget enforcement no longer consults this presentation-layer
    helper. Durable waiting transitions pause the root budget directly, and
    RunKernel owns cancellation when the active budget is actually exhausted.

    TEMPORARILY DISABLED: kernel access needs SDK Runtime adapter update.
    """
    # TODO: Re-enable when SDK Runtime provides kernel access through ingress
    return False


async def _signal_product_harness_decision(
    session_id: str,
    payload: dict[str, Any],
    response: dict[str, Any],
    *,
    authorization: bool = False,
    projection_ws: WebSocket | None = None,
):
    """Apply one fenced UI decision to the durable execution authority."""

    if host_state.sdk_ingress is None or not host_state.sdk_ingress.accepting:
        raise RuntimeError("SDK Runtime decision ingress is closed")
    run_id = str(payload.get("run_id") or "").strip()
    decision_id = str(payload.get("decision_id") or payload.get("request_id") or "").strip()
    nonce = str(payload.get("nonce") or "").strip()
    version = payload.get("version")
    if not run_id or not decision_id or not nonce or version is None:
        raise ValueError("decision response requires run_id/decision_id/nonce/version")
    sdk_run_id = _sdk_run_ids_by_root.get(run_id, run_id)

    if is_primary_sdk_target(_state_db_path, sdk_run_id):
        raise ValueError("primary_decision_requires_authenticated_control")
    if authorization:
        receipt = await host_state.sdk_ingress.decide_authorization(
            run_id=sdk_run_id,
            decision_id=decision_id,
            nonce=nonce,
            expected_version=int(version),
            decision=str(response.get("decision") or "deny"),
        )
    else:
        receipt = await host_state.sdk_ingress.signal(
            run_id=sdk_run_id,
            signal_id=decision_id,
            payload={
                "decision_id": decision_id,
                "nonce": nonce,
                "version": int(version),
                "response": response,
            },
        )
    if projection_ws is None:
        _ensure_sdk_recovery_watcher(sdk_run_id)
    else:
        _ensure_sdk_recovery_watcher(
            sdk_run_id,
            authorization_ws=projection_ws,
        )
    # 前台 Run 的驱动循环在观察到 BOUND_WAITING（等待用户授权）后就返回，
    # 而它只被 ``after_enqueue`` / ``after_control`` 唤醒——``after_control`` 在生产上
    # **零调用者**（此前只有测试调）。于是用户批准之后没有任何东西把驱动叫醒，
    # Run 虽然跑完却永远收不了尾：回合停在 CLAIMED、终态提交不执行、
    # Memory ingestion outbox 行（写在终态事务里）永远不产生。
    # 实测证据 .local-test-evidence/real-ui-channel/prod-lane-04。
    foreground_runtime = service_context.get("human_memory_foreground_runtime_execution_authority")
    if foreground_runtime is not None:
        try:
            await foreground_runtime.after_control(subject="deskpet-local-owner-v1")
        except Exception as exc:  # noqa: BLE001 - 唤醒失败不得影响本次决策落地
            logger.warning(
                "foreground_runtime_wake_after_decision_failed error_type=%s",
                type(exc).__name__,
            )
    return receipt


async def _try_resolve_execution_workflow_decision(
    ws: WebSocket,
    *,
    session_id: str,
    raw: Mapping[str, Any],
    workflow_service: Any,
) -> bool:
    """Route ContextTrace decisions owned by the execution ledger.

    ``workflow_runs`` and ``execution_runs`` intentionally share the same
    public inspector, but their decision authorities are different.  The
    legacy workflow IPC only knows ``workflow_decisions``; execution-owned
    children must be fenced and resumed through RunKernel.  Return ``False``
    when the id is not an open execution decision so legacy IPC remains the
    compatibility fallback.
    """

    if str(raw.get("type") or "") != "workflow_decision_resolve":
        return False
    payload = raw.get("payload")
    if not isinstance(payload, Mapping):
        return False
    decision_id = str(payload.get("decision_id") or "").strip()
    if not decision_id:
        return False
    execution_uow = getattr(workflow_service, "execution_uow", None)
    if execution_uow is None or not hasattr(execution_uow, "list_open_decision_projections"):
        return False
    projection = next(
        (
            item
            for item in await execution_uow.list_open_decision_projections(session_id)
            if item["decision"].request.decision_id == decision_id
        ),
        None,
    )
    if projection is None and hasattr(execution_uow, "get_open_decision_projection_by_id"):
        projection = await execution_uow.get_open_decision_projection_by_id(decision_id)
    if projection is None:
        return False
    decision = projection["decision"]
    authoritative_session_id = str(projection["session_id"])
    response = payload.get("response")
    normalized_response = (
        dict(response) if isinstance(response, Mapping) else {"action": str(response or "cancel")}
    )
    try:
        receipt = await _signal_product_harness_decision(
            authoritative_session_id,
            {
                "run_id": decision.request.run_id,
                "decision_id": decision.request.decision_id,
                "nonce": str(payload.get("nonce") or ""),
                "version": payload.get("expected_version"),
            },
            normalized_response,
        )
    except Exception as exc:  # noqa: BLE001
        await ws.send_json(
            {
                "type": "workflow_ipc_error",
                "request_type": "workflow_decision_resolve",
                "request_id": raw.get("request_id"),
                "ok": False,
                "error": {
                    "code": getattr(exc, "code", type(exc).__name__),
                    "message": str(exc),
                    "retryable": False,
                },
                "payload": {
                    "error": {
                        "code": getattr(exc, "code", type(exc).__name__),
                        "message": str(exc),
                        "retryable": False,
                    }
                },
            }
        )
        return True
    await ws.send_json(
        {
            "type": "workflow_decision_resolve_response",
            "request_id": raw.get("request_id"),
            "ok": True,
            "payload": {
                "decision_id": decision.request.decision_id,
                "run_id": decision.request.run_id,
                "status": "accepted",
                "audit": "Execution decision recorded by RunKernel",
                "duplicate": bool(getattr(receipt, "duplicate", False)),
            },
        }
    )
    logger.info(
        "execution_workflow_decision_resolved",
        session_id=authoritative_session_id,
        run_id=decision.request.run_id,
        decision_id=decision.request.decision_id,
        action=normalized_response.get("action"),
    )
    return True


async def _run_product_harness_continuation(
    websocket,
    text: str,
    session_id: str,
    *,
    root_run_id: str,
    task_scope_id: str,
    expected_boundary_version: int,
    request_id: str,
) -> None:
    """Append one user message to an existing nonterminal root Run."""

    workflow_service = service_context.get("workflow_service")
    execution_uow = getattr(workflow_service, "execution_uow", None)
    if (
        host_state.sdk_ingress is None
        or not host_state.sdk_ingress.accepting
        or execution_uow is None
    ):
        raise RuntimeError("SDK Runtime continuation ingress is closed")
    projection = await execution_uow.get_task_run_projection(root_run_id)
    boundary = await execution_uow.get_conversation_boundary(root_run_id)
    if (
        projection is None
        or boundary is None
        or projection.session_id != session_id
        or projection.task_scope_id != task_scope_id
        or boundary.session_id != session_id
        or boundary.task_scope_id != task_scope_id
    ):
        raise ValueError("continuation root/task/session identity does not match")
    message_ref = f"request:{request_id}"
    sdk_run_id = _sdk_run_ids_by_root.get(root_run_id, root_run_id)
    session_db = service_context.get("session_db")
    context_sources = service_context.get("sdk_context_source_repository")
    if context_sources is None or session_db is None:
        raise RuntimeError("SDK continuation context authority is unavailable")

    current_message = Message(MessageRole.USER, text)
    continuation_value = None

    # S5a: the continuation lane shares the frozen 32k-tier budget and causal
    # grouping with the per-turn Context authority — no second source of truth.
    history, _truncated = await _bounded_sdk_history(
        session_db,
        session_id=session_id,
        root_run_id=root_run_id,
        token_budget=effective_input_budget(32_768),
    )
    _group_plan = plan_recent_causal_groups(history)
    history = [item.to_history_row() for group in _group_plan.groups for item in group.items]
    if _truncated or _group_plan.dropped_group_count:
        logger.info(
            "sdk_continuation_history_bounded truncated=%s dropped_groups=%s",
            _truncated,
            _group_plan.dropped_group_count,
        )
    source_payload = {
        "schema_version": 1,
        "provider_messages": [
            *({"role": row["role"], "content": row["content"]} for row in history),
            current_message.to_dict(),
        ],
        "current_message": current_message.to_dict(),
    }
    context_binding_id, source_ref = await context_sources.put_pending(
        root_run_id=sdk_run_id,
        continuation_id=message_ref,
        payload=source_payload,
    )
    continuation_value = ConversationContinuationInput(
        current_message,
        text,
        context_source_snapshot_ref=source_ref,
    )
    receipt = await host_state.sdk_ingress.signal_conversation(
        run_id=sdk_run_id,
        continuation_id=message_ref,
        value=continuation_value,
    )
    accepted = bool(
        receipt.get("accepted")
        if isinstance(receipt, dict)
        else getattr(receipt, "accepted", False)
    )
    duplicate = bool(
        receipt.get("duplicate")
        if isinstance(receipt, dict)
        else getattr(receipt, "duplicate", False)
    )
    if not accepted and not duplicate:
        reason = (
            receipt.get("reason") if isinstance(receipt, dict) else getattr(receipt, "reason", None)
        )
        raise RuntimeError(str(reason or "continuation was not accepted"))
    await context_sources.mark_claimed(
        context_binding_id,
        claim_token=receipt.delivery_id,
    )
    _ensure_sdk_recovery_watcher(sdk_run_id)
    receipt_reason = (
        receipt.get("reason") if isinstance(receipt, dict) else getattr(receipt, "reason", "")
    )
    updated = await execution_uow.get_conversation_boundary(root_run_id)
    if updated is None:
        raise RuntimeError("accepted continuation lost its boundary")
    vector_worker = service_context.get("vector_worker")
    try:
        if session_db is not None:
            message_id = await session_db.append_message(
                session_id=session_id,
                role="user",
                content=text,
                workflow_event_id=(f"user-continuation:{root_run_id}:{message_ref}"),
                root_run_id=root_run_id,
                task_scope_id=task_scope_id,
                memory_authority="harness",
            )
            if vector_worker is not None:
                await vector_worker.enqueue(message_id, text)
    except Exception as exc:  # noqa: BLE001
        # The execution boundary is canonical and was already committed.
        # The stable workflow_event_id lets any client retry rebuild this
        # derived history projection without duplicating the message.
        logger.warning(
            "continuation_history_projection_failed",
            root_run_id=root_run_id,
            task_scope_id=task_scope_id,
            error=str(exc),
        )
    await _broadcast_default_chat_peers(
        websocket,
        {
            "type": "chat_v2_user_echo",
            "payload": {
                "session_id": session_id,
                "text": text,
                "request_id": request_id,
                "run_id": root_run_id,
                "task_scope_id": task_scope_id,
                "conversation_boundary_ref": updated.boundary_ref,
                "conversation_boundary_version": updated.version,
            },
        },
    )
    accepted_event = {
        "type": "chat_v2_continuation_accepted",
        "payload": {
            "session_id": session_id,
            "request_id": request_id,
            "run_id": root_run_id,
            "task_scope_id": task_scope_id,
            "conversation_boundary_ref": updated.boundary_ref,
            "conversation_boundary_version": updated.version,
            "queued": receipt_reason == "continuation_queued",
        },
    }
    await websocket.send_json(accepted_event)
    await _broadcast_default_chat_peers(websocket, accepted_event)


def _launch_product_harness_continuation(*args, **kwargs) -> asyncio.Task:
    async def guarded() -> None:
        try:
            await _run_product_harness_continuation(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            websocket = args[0]
            session_id = str(args[2] if len(args) > 2 else kwargs.get("session_id") or "default")
            try:
                request_id = str(kwargs.get("request_id") or "")
                await _send_chat_error(
                    websocket,
                    {
                        "type": "chat_v2_error",
                        "payload": {
                            "error": str(exc) or type(exc).__name__,
                            "detail": type(exc).__name__,
                            "session_id": session_id,
                            "run_id": str(kwargs.get("root_run_id") or ""),
                            "task_scope_id": str(kwargs.get("task_scope_id") or ""),
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
            logger.warning("product_harness_continuation_failed", error=str(error))

    task.add_done_callback(report_failure)
    return task
