"""控制通道消息处理：控制消息分发、未决决定重放、运行检查任务调度"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent.agent_events import PipelineEvent
from deskpet.agent.run_presenter import CanonicalRunEventPresentationAdapter
from deskpet.execution.contracts import OutcomeStatus, thaw_json
from deskpet.execution.delivery_contracts import RunEvent, RunEventCandidate
from deskpet.execution.run_read_model import PublicReadError
from fastapi import WebSocket

from host.connections import _harness_inspector_tasks
from host.runtime import logger, service_context
from host.sdk_recovery import _project_open_sdk_authorizations


def _cancel_harness_inspector_tasks(
    ws: WebSocket,
    *,
    root_run_id: str | None = None,
    request_kind: str | None = None,
) -> int:
    cancelled = 0
    connection_id = id(ws)
    for key, task in tuple(_harness_inspector_tasks.items()):
        owner, kind, root = key
        if owner != connection_id:
            continue
        if root_run_id is not None and root != root_run_id:
            continue
        if request_kind is not None and kind != request_kind:
            continue
        if not task.done():
            task.cancel()
            cancelled += 1
        _harness_inspector_tasks.pop(key, None)
    return cancelled


def _schedule_harness_inspector_task(
    ws: WebSocket,
    *,
    request_kind: str,
    root_run_id: str,
    coroutine: Any,
) -> None:
    key = (id(ws), request_kind, root_run_id)
    previous = _harness_inspector_tasks.pop(key, None)
    if previous is not None and not previous.done():
        previous.cancel()
    task = asyncio.create_task(
        coroutine,
        name=f"harness-inspector:{request_kind}:{root_run_id}",
    )
    _harness_inspector_tasks[key] = task

    def _done(completed: asyncio.Task) -> None:
        if _harness_inspector_tasks.get(key) is completed:
            _harness_inspector_tasks.pop(key, None)

    task.add_done_callback(_done)


async def _replay_open_harness_decisions(
    ws: WebSocket,
    *,
    execution_uow: Any,
    session_id: str,
) -> None:
    """Rebuild blocking UI cards from durable open decisions after reconnect."""

    if not hasattr(execution_uow, "list_open_decision_projections"):
        return

    adapter = CanonicalRunEventPresentationAdapter()
    for projection in await execution_uow.list_open_decision_projections(session_id):
        decision = projection["decision"]
        request = decision.request
        prompt = thaw_json(request.prompt)
        restored_parent = ""
        restored_root = str(projection.get("workspace_root") or "").strip()
        restored_folder = str(prompt.get("folder_name") or "").strip()
        if projection.get("workspace_source") == "user_path" and restored_root:
            if str(prompt.get("directory_mode") or "") == "use_existing":
                restored_parent = restored_root
            elif (
                restored_folder
                and Path(restored_root).name.casefold() == restored_folder.casefold()
            ):
                restored_parent = str(Path(restored_root).parent)
        candidate = RunEventCandidate(
            event_key=(f"decision-replay:{request.decision_id}:v{decision.decision_version}"),
            kind="decision",
            status=OutcomeStatus.WAITING,
            driver_kind=str(projection["driver_kind"]),
            correlation={"decision_id": request.decision_id},
            payload={
                "run_id": request.run_id,
                "request_id": request.decision_id,
                "decision_id": request.decision_id,
                "nonce": request.nonce,
                "version": decision.decision_version,
                "kind": request.kind.value,
                "prompt": prompt,
                "prompt_schema_version": request.prompt_schema_version,
                "expires_at": request.expires_at,
                "domain_kind": request.domain_kind,
                "domain_id": request.domain_id,
                "call_id": request.call_id,
                "effect_id": request.effect_id,
                "tool_name": request.tool_name,
                "args_hash": request.args_hash,
                "capability_hash": request.capability_hash,
                "scope_hash": request.scope_hash,
                "parent_directory": restored_parent or None,
            },
        )
        replay = RunEvent(
            event_id=candidate.event_key,
            run_id=request.run_id,
            root_run_id=str(projection["root_run_id"]),
            session_id=session_id,
            durable_seq=1,
            candidate=candidate,
            created_at=decision.created_at,
        )
        for event in adapter.to_presentation_events(replay):
            if not isinstance(event, PipelineEvent):
                continue
            await ws.send_json(
                {
                    "type": event.type,
                    "payload": {
                        **event.payload,
                        "session_id": session_id,
                        "run_id": request.run_id,
                        "root_run_id": str(projection["root_run_id"]),
                        "request_id": request.decision_id,
                        "turn_id": str(projection["turn_id"]),
                        "rehydrated": True,
                    },
                }
            )
    await _project_open_sdk_authorizations(
        ws,
        session_id=session_id,
        rehydrated=True,
    )


async def _handle_control_ws_message(raw: dict, *, session_id: str, ws: WebSocket) -> bool:  # noqa: ARG001
    msg_type = raw.get("type", "")
    if msg_type == "harness_inspector_snapshot_request":
        return await _handle_inspector_snapshot_request(raw, session_id=session_id, ws=ws)
    if msg_type == "harness_inspector_details_request":
        return await _handle_inspector_details_request(raw, session_id=session_id, ws=ws)
    if msg_type == "harness_inspector_cancel":
        return await _handle_inspector_cancel(raw, ws=ws)
    if msg_type == "harness_inspector_snapshot":
        return await _handle_inspector_snapshot(raw, session_id=session_id, ws=ws)
    if msg_type in {"task_projections_list", "task_projection_update"}:
        return await _handle_task_projection_message(
            raw, session_id=session_id, ws=ws, msg_type=msg_type
        )
    return False


async def _handle_inspector_snapshot_request(raw, *, session_id, ws) -> bool:
    payload = raw.get("payload", raw) or {}
    request_id = str(raw.get("request_id") or payload.get("request_id") or "")
    target_session_id = str(payload.get("session_id") or session_id).strip()
    root_run_id = str(payload.get("root_run_id") or payload.get("run_id") or "").strip()
    schema_version = str(payload.get("schema_version") or "3")

    async def _create_public_snapshot() -> None:
        service = service_context.get("harness_public_read_service")
        try:
            if schema_version != "3":
                raise PublicReadError(
                    "unsupported_schema",
                    "only Harness Inspector schema V3 is supported",
                )
            if service is None:
                raise RuntimeError("harness_public_read_service_unavailable")
            manifest = await service.create_manifest(
                session_id=target_session_id,
                root_run_id=root_run_id,
            )
            await ws.send_json(
                {
                    "type": "harness_inspector_snapshot_response",
                    "request_id": request_id,
                    "ok": True,
                    "snapshot": service.snapshot(manifest).to_dict(),
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("create_public_snapshot_public_read_error_failed", exc_info=True)
            await ws.send_json(
                {
                    "type": "harness_inspector_error",
                    "request_id": request_id,
                    "ok": False,
                    "code": getattr(exc, "code", type(exc).__name__),
                    "message": str(exc),
                    "session_id": target_session_id,
                    "root_run_id": root_run_id,
                }
            )

    if not root_run_id:
        await ws.send_json(
            {
                "type": "harness_inspector_error",
                "request_id": request_id,
                "ok": False,
                "code": "invalid_request",
                "message": "root_run_id is required",
            }
        )
        return True
    _schedule_harness_inspector_task(
        ws,
        request_kind="snapshot",
        root_run_id=root_run_id,
        coroutine=_create_public_snapshot(),
    )
    return True


async def _handle_inspector_details_request(raw, *, session_id, ws) -> bool:
    payload = raw.get("payload", raw) or {}
    request_id = str(raw.get("request_id") or payload.get("request_id") or "")
    target_session_id = str(payload.get("session_id") or session_id).strip()
    root_run_id = str(payload.get("root_run_id") or "").strip()

    async def _query_public_details() -> None:
        service = service_context.get("harness_public_read_service")
        try:
            if service is None:
                raise RuntimeError("harness_public_read_service_unavailable")
            page = await service.query_details(
                projection_id=str(payload.get("projection_id") or ""),
                session_id=target_session_id,
                root_run_id=root_run_id,
                query_kind=str(payload.get("query_kind") or ""),
                page_size=int(payload.get("page_size") or 50),
                cursor=(str(payload["cursor"]) if payload.get("cursor") else None),
                schema_version=str(payload.get("schema_version") or "3"),
            )
            await ws.send_json(
                {
                    "type": "harness_inspector_details_response",
                    "request_id": request_id,
                    "ok": True,
                    **page.to_dict(),
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("query_public_details_runtime_error_failed", exc_info=True)
            await ws.send_json(
                {
                    "type": "harness_inspector_error",
                    "request_id": request_id,
                    "ok": False,
                    "code": getattr(exc, "code", type(exc).__name__),
                    "message": str(exc),
                    "session_id": target_session_id,
                    "root_run_id": root_run_id,
                }
            )

    if not root_run_id:
        await ws.send_json(
            {
                "type": "harness_inspector_error",
                "request_id": request_id,
                "ok": False,
                "code": "invalid_request",
                "message": "root_run_id is required",
            }
        )
        return True
    _schedule_harness_inspector_task(
        ws,
        request_kind="details",
        root_run_id=root_run_id,
        coroutine=_query_public_details(),
    )
    return True


async def _handle_inspector_cancel(raw, *, ws) -> bool:
    payload = raw.get("payload", raw) or {}
    root_run_id = str(payload.get("root_run_id") or "").strip() or None
    kind = str(payload.get("request_kind") or "all").strip()
    request_kind = None if kind == "all" else kind
    cancelled = _cancel_harness_inspector_tasks(
        ws,
        root_run_id=root_run_id,
        request_kind=request_kind,
    )
    await ws.send_json(
        {
            "type": "harness_inspector_cancelled",
            "request_id": str(raw.get("request_id") or ""),
            "cancelled": cancelled,
            "root_run_id": root_run_id,
        }
    )
    return True


async def _handle_inspector_snapshot(raw, *, session_id, ws) -> bool:
    payload = raw.get("payload", {}) or {}
    request_id = str(raw.get("request_id") or "")
    target_session_id = str(payload.get("session_id") or session_id).strip() or session_id
    run_id = str(payload.get("run_id") or "").strip()
    execution_uow = service_context.get("execution_uow")
    try:
        if execution_uow is None:
            raise RuntimeError("execution_uow_unavailable")
        if not run_id:
            raise ValueError("harness inspector requires run_id")
        snapshot = await execution_uow.inspect_harness_run(
            run_id,
            expected_session_id=target_session_id,
        )
        if snapshot is None:
            raise ValueError("run does not belong to the requested session")
        await ws.send_json(
            {
                "type": "harness_inspector_snapshot_response",
                "request_id": request_id,
                "ok": True,
                "payload": snapshot,
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "harness_inspector_snapshot_failed",
            session_id=target_session_id,
            run_id=run_id,
            error_type=type(exc).__name__,
            error=str(exc),
            exc_info=True,
        )
        await ws.send_json(
            {
                "type": "harness_inspector_error",
                "request_id": request_id,
                "ok": False,
                "payload": {
                    "session_id": target_session_id,
                    "run_id": run_id,
                    "code": getattr(exc, "code", type(exc).__name__),
                    "detail": str(exc),
                },
            }
        )
    return True


async def _handle_task_projection_message(raw, *, session_id, ws, msg_type) -> bool:
    payload = raw.get("payload", {}) or {}
    target_session_id = str(payload.get("session_id") or session_id).strip() or session_id
    execution_uow = service_context.get("execution_uow")
    if execution_uow is None:
        await ws.send_json(
            {
                "type": "task_projection_error",
                "payload": {
                    "session_id": target_session_id,
                    "code": "execution_uow_unavailable",
                    "detail": "Task projection storage is unavailable",
                },
            }
        )
        return True
    try:
        if msg_type == "task_projections_list":
            records = await execution_uow.list_task_run_projections(
                target_session_id,
                include_closed=bool(payload.get("include_closed", False)),
            )
            projections = []
            for projection in records:
                boundary = await execution_uow.get_conversation_boundary(projection.root_run_id)
                work_context = await execution_uow.get_task_work_context(projection.root_run_id)
                lifecycle = await execution_uow.get_task_run_lifecycle(
                    projection.root_run_id,
                    expected_session_id=target_session_id,
                )
                projections.append(
                    {
                        "projection_id": projection.projection_id,
                        "session_id": projection.session_id,
                        "run_id": projection.root_run_id,
                        "task_scope_id": projection.task_scope_id,
                        "ui_state": projection.ui_state,
                        "version": projection.version,
                        "conversation_boundary_ref": (
                            None if boundary is None else boundary.boundary_ref
                        ),
                        "conversation_boundary_version": (
                            None if boundary is None else boundary.version
                        ),
                        "workspace_root": (
                            None if work_context is None else work_context.workspace_root
                        ),
                        "workspace_source": (
                            None if work_context is None else work_context.workspace_source
                        ),
                        "status": (None if lifecycle is None else lifecycle["status"]),
                        "started_at": (None if lifecycle is None else lifecycle["started_at"]),
                        "updated_at": (None if lifecycle is None else lifecycle["updated_at"]),
                        "ended_at": (None if lifecycle is None else lifecycle["ended_at"]),
                    }
                )
            await ws.send_json(
                {
                    "type": "task_projections_response",
                    "payload": {
                        "session_id": target_session_id,
                        "projections": projections,
                    },
                }
            )
            await _replay_open_harness_decisions(
                ws,
                execution_uow=execution_uow,
                session_id=target_session_id,
            )
            return True

        run_id = str(payload.get("run_id") or "").strip()
        ui_state = str(payload.get("ui_state") or "").strip()
        expected_version = payload.get("expected_version")
        if not run_id or expected_version is None:
            raise ValueError("task projection update requires run_id/expected_version")
        current = await execution_uow.get_task_run_projection(run_id)
        if current is None or current.session_id != target_session_id:
            raise ValueError("task projection does not belong to the requested session")
        projection = await execution_uow.set_task_run_projection_state(
            run_id,
            ui_state,
            expected_version=int(expected_version),
        )
        await ws.send_json(
            {
                "type": "task_projection_updated",
                "payload": {
                    "projection_id": projection.projection_id,
                    "session_id": projection.session_id,
                    "run_id": projection.root_run_id,
                    "task_scope_id": projection.task_scope_id,
                    "ui_state": projection.ui_state,
                    "version": projection.version,
                },
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "task_projection_request_failed",
            request_type=msg_type,
            session_id=target_session_id,
            error=str(exc),
            exc_info=True,
        )
        await ws.send_json(
            {
                "type": "task_projection_error",
                "payload": {
                    "session_id": target_session_id,
                    "run_id": str(payload.get("run_id") or ""),
                    "code": getattr(exc, "code", type(exc).__name__),
                    "detail": str(exc),
                },
            }
        )
    return True
