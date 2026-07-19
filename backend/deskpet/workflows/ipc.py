"""Framework-neutral workflow IPC validation and dispatch."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, MutableSet
from dataclasses import dataclass
from typing import Any

from .errors import WorkflowContractError
from .outbox import MAX_PAGE_SIZE, OutboxError
from .service import WorkflowService, WorkflowServiceError, _plain


logger = logging.getLogger(__name__)


class IPCValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _Route:
    canonical_type: str
    response_type: str
    handler_name: str


_CANONICAL_ROUTES = {
    "workflow_runs_list": _Route(
        "workflow_runs_list", "workflow_runs_list_response", "_runs_list"
    ),
    "workflow_run_detail": _Route(
        "workflow_run_detail", "workflow_run_detail_response", "_run_detail"
    ),
    "workflow_trace_tree": _Route(
        "workflow_trace_tree", "workflow_trace_tree_response", "_trace_tree"
    ),
    "workflow_checkpoint_history": _Route(
        "workflow_checkpoint_history",
        "workflow_checkpoint_history_response",
        "_checkpoint_history",
    ),
    "workflow_checkpoint_fork": _Route(
        "workflow_checkpoint_fork", "workflow_checkpoint_fork_response", "_checkpoint_fork"
    ),
    "workflow_decision_resolve": _Route(
        "workflow_decision_resolve", "workflow_decision_resolve_response", "_decision_resolve"
    ),
    "workflow_run_resume": _Route(
        "workflow_run_resume", "workflow_run_resume_response", "_run_resume"
    ),
    "workflow_run_cancel": _Route(
        "workflow_run_cancel", "workflow_run_cancel_response", "_run_cancel"
    ),
    "workflow_run_retry_from_start": _Route(
        "workflow_run_retry_from_start",
        "workflow_run_retry_from_start_response",
        "_run_retry_from_start",
    ),
    "workflow_run_action": _Route(
        "workflow_run_action", "workflow_run_action_response", "_run_action"
    ),
    "workflow_events_after_seq": _Route(
        "workflow_events_after_seq", "workflow_events_after_seq_response", "_events_after"
    ),
    "workflow_history_hydrate": _Route(
        "workflow_history_hydrate", "workflow_history_hydrate_response", "_history_hydrate"
    ),
    "workflow_delivery_list": _Route(
        "workflow_delivery_list", "workflow_delivery_list_response", "_delivery_list"
    ),
    "workflow_delivery_retry": _Route(
        "workflow_delivery_retry", "workflow_delivery_retry_response", "_delivery_retry"
    ),
    "workflow_delivery_discard": _Route(
        "workflow_delivery_discard", "workflow_delivery_discard_response", "_delivery_discard"
    ),
    "workflow_evaluation_submit": _Route(
        "workflow_evaluation_submit",
        "workflow_evaluation_submit_response",
        "_evaluation_submit",
    ),
    "workflow_experiment_compare": _Route(
        "workflow_experiment_compare",
        "workflow_experiment_compare_response",
        "_experiment_compare",
    ),
}

_ALIASES = {
    "runs.list": "workflow_runs_list",
    "workflow.runs.list": "workflow_runs_list",
    "run.detail": "workflow_run_detail",
    "workflow.run.detail": "workflow_run_detail",
    "trace.tree": "workflow_trace_tree",
    "workflow.trace.tree": "workflow_trace_tree",
    "checkpoints.history": "workflow_checkpoint_history",
    "workflow.checkpoints.history": "workflow_checkpoint_history",
    "checkpoint.fork": "workflow_checkpoint_fork",
    "workflow.checkpoint.fork": "workflow_checkpoint_fork",
    "decision.resolve": "workflow_decision_resolve",
    "workflow.decision.resolve": "workflow_decision_resolve",
    "run.resume": "workflow_run_resume",
    "workflow.run.resume": "workflow_run_resume",
    "run.cancel": "workflow_run_cancel",
    "workflow.run.cancel": "workflow_run_cancel",
    "workflow.run.retry_from_start": "workflow_run_retry_from_start",
    "workflow.run.action": "workflow_run_action",
    "events.after": "workflow_events_after_seq",
    "workflow.events.after": "workflow_events_after_seq",
    "history.hydrate": "workflow_history_hydrate",
    "workflow.history.hydrate": "workflow_history_hydrate",
    "deliveries.list": "workflow_delivery_list",
    "workflow.deliveries.list": "workflow_delivery_list",
    "delivery.retry": "workflow_delivery_retry",
    "workflow.delivery.retry": "workflow_delivery_retry",
    "delivery.discard": "workflow_delivery_discard",
    "workflow.delivery.discard": "workflow_delivery_discard",
    "evaluation.submit": "workflow_evaluation_submit",
    "workflow.evaluation.submit": "workflow_evaluation_submit",
    "experiments.compare": "workflow_experiment_compare",
    "workflow.experiments.compare": "workflow_experiment_compare",
}


def _string(payload: Mapping[str, Any], field: str, *, required: bool = True) -> str | None:
    value = payload.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise IPCValidationError("invalid_payload", f"{field} must be a non-empty string")
    return value.strip()


def _integer(
    payload: Mapping[str, Any],
    field: str,
    *,
    required: bool = True,
    default: int | None = None,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    value = payload.get(field, default)
    if value is None and not required:
        return default if default is not None else 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise IPCValidationError("invalid_payload", f"{field} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        suffix = f" between {minimum} and {maximum}" if maximum is not None else f" >= {minimum}"
        raise IPCValidationError("invalid_payload", f"{field} must be{suffix}")
    return value


def _mapping(payload: Mapping[str, Any], field: str, *, required: bool = True) -> dict[str, Any]:
    value = payload.get(field)
    if value is None and not required:
        return {}
    if not isinstance(value, Mapping):
        raise IPCValidationError("invalid_payload", f"{field} must be an object")
    return dict(value)


def _strings(payload: Mapping[str, Any], field: str) -> list[str]:
    value = payload.get(field, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise IPCValidationError("invalid_payload", f"{field} must be an array of strings")
    return value


def _boolean(payload: Mapping[str, Any], field: str, *, default: bool = False) -> bool:
    value = payload.get(field, default)
    if not isinstance(value, bool):
        raise IPCValidationError("invalid_payload", f"{field} must be a boolean")
    return value


def _number(payload: Mapping[str, Any], field: str) -> float | None:
    value = payload.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IPCValidationError("invalid_payload", f"{field} must be a number")
    return float(value)


class WorkflowIPCDispatcher:
    """Validate plain mappings and return plain response mappings."""

    def __init__(self, service: WorkflowService | object) -> None:
        self.service = service

    @staticmethod
    def _request(request: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
        request_type = request.get("type")
        request_id = request.get("request_id")
        payload = request.get("payload", {})
        if not isinstance(request_type, str) or not request_type.strip():
            raise IPCValidationError("invalid_request", "type must be a non-empty string")
        if not isinstance(request_id, str) or not request_id.strip():
            raise IPCValidationError("invalid_request", "request_id must be a non-empty string")
        if not isinstance(payload, Mapping):
            raise IPCValidationError("invalid_payload", "payload must be an object")
        merged = dict(payload)
        for field in ("expected_version", "cursor", "limit"):
            if field in request:
                if field in merged and merged[field] != request[field]:
                    raise IPCValidationError(
                        "invalid_payload", f"conflicting top-level and payload {field}"
                    )
                merged[field] = request[field]
        return request_type.strip(), request_id.strip(), merged

    async def dispatch(self, request: Mapping[str, Any] | object) -> dict[str, Any]:
        request_id: str | None = None
        request_type: str | None = None
        route: _Route | None = None
        try:
            if not isinstance(request, Mapping):
                raise IPCValidationError("invalid_request", "IPC request must be an object")
            request_type, request_id, payload = self._request(request)
            canonical = _ALIASES.get(request_type, request_type)
            route = _CANONICAL_ROUTES.get(canonical)
            if route is None:
                raise IPCValidationError(
                    "unknown_request_type", f"Unknown workflow IPC type: {request_type}"
                )
            result = _plain(await getattr(self, route.handler_name)(payload))
            return {
                "type": route.response_type,
                "request_id": request_id,
                "ok": True,
                "payload": result,
            }
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = self._error(exc)
            return {
                "type": "workflow_ipc_error",
                "request_type": route.canonical_type if route else request_type,
                "request_id": request_id,
                "ok": False,
                "error": error,
                "payload": {"error": error},
            }

    @staticmethod
    def _error(exc: BaseException) -> dict[str, Any]:
        if isinstance(exc, IPCValidationError):
            code, retryable, current_version, details = exc.code, False, None, {}
        elif isinstance(exc, (WorkflowServiceError, OutboxError)):
            code = exc.code
            retryable = exc.retryable
            current_version = exc.current_version
            details = dict(getattr(exc, "details", {}) or {})
        elif isinstance(exc, WorkflowContractError):
            details = dict(exc.details)
            code, retryable = exc.code, False
            current_version = details.get("current_version")
        elif isinstance(exc, KeyError):
            code, retryable, current_version, details = "not_found", False, None, {}
        elif isinstance(exc, (TypeError, ValueError)):
            code, retryable, current_version, details = "invalid_payload", False, None, {}
        else:
            code, retryable, current_version, details = "workflow_ipc_failed", True, None, {}
        result = {
            "code": str(code),
            "message": str(exc),
            "retryable": bool(retryable),
        }
        if current_version is not None:
            result["current_version"] = int(current_version)
        if details:
            result["details"] = details
        return result

    async def _runs_list(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self.service.list_runs(
            session_id=_string(payload, "session_id", required=False),
            cursor=_string(payload, "cursor", required=False),
            limit=_integer(payload, "limit", default=MAX_PAGE_SIZE, maximum=MAX_PAGE_SIZE),
        )

    async def _run_detail(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self.service.run_detail(_string(payload, "run_id"))

    async def _trace_tree(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self.service.trace_tree(
            run_id=_string(payload, "run_id", required=False),
            trace_id=_string(payload, "trace_id", required=False),
        )

    async def _checkpoint_history(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        limit = payload.get("limit")
        if limit is not None:
            limit = _integer(payload, "limit", maximum=MAX_PAGE_SIZE)
        return await self.service.checkpoint_history(_string(payload, "run_id"), limit=limit)

    async def _checkpoint_fork(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self.service.fork_checkpoint(
            run_id=_string(payload, "run_id"),
            checkpoint_id=_string(payload, "checkpoint_id"),
            expected_version=_integer(payload, "expected_version"),
            state_patch=_mapping(payload, "state_patch", required=False),
            fork_key=_string(payload, "fork_key", required=False),
            confirm_dangerous_effects=_boolean(
                payload, "confirm_dangerous_effects", default=False
            ),
        )

    async def _decision_resolve(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if "response" not in payload:
            raise IPCValidationError("invalid_payload", "response is required")
        return await self.service.resolve_decision(
            _string(payload, "decision_id"),
            nonce=_string(payload, "nonce"),
            response=payload["response"],
            expected_version=_integer(payload, "expected_version"),
        )

    async def _run_resume(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self.service.resume_run(
            _string(payload, "run_id"), _mapping(payload, "responses")
        )

    async def _run_cancel(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self.service.cancel_run(
            _string(payload, "run_id"),
            reason=_string(payload, "reason", required=False) or "user",
        )

    async def _run_retry_from_start(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if set(payload) != {"run_id", "action_id", "retry_key"}:
            raise IPCValidationError(
                "invalid_payload", "retry payload must contain exactly run_id/action_id/retry_key"
            )
        return await self.service.retry_run_from_start(
            _string(payload, "run_id"),
            action_id=_string(payload, "action_id"),
            retry_key=_string(payload, "retry_key"),
        )

    async def _run_action(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "run_id", "action_id", "idempotency_key", "expected_version", "action_payload"
        }
        required = {"run_id", "action_id", "idempotency_key", "expected_version"}
        if not required.issubset(payload) or set(payload) - allowed:
            raise IPCValidationError(
                "invalid_payload",
                "run action requires run_id/action_id/idempotency_key/expected_version",
            )
        return await self.service.execute_run_action(
            _string(payload, "run_id"),
            action_id=_string(payload, "action_id"),
            idempotency_key=_string(payload, "idempotency_key"),
            expected_version=_integer(payload, "expected_version"),
            payload=_mapping(payload, "action_payload", required=False),
        )

    async def _events_after(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self.service.events_after_seq(
            _string(payload, "run_id"),
            _integer(payload, "after_seq", default=0),
            limit=_integer(payload, "limit", default=MAX_PAGE_SIZE, maximum=MAX_PAGE_SIZE),
        )

    async def _history_hydrate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self.service.hydrate_history_event_ids(
            _string(payload, "run_id"), _strings(payload, "event_ids")
        )

    async def _delivery_list(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self.service.list_deliveries(
            run_id=_string(payload, "run_id", required=False),
            status=_string(payload, "status", required=False),
            cursor=_string(payload, "cursor", required=False),
            limit=_integer(payload, "limit", default=MAX_PAGE_SIZE, maximum=MAX_PAGE_SIZE),
        )

    async def _delivery_retry(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self.service.retry_delivery(
            _string(payload, "delivery_id"),
            expected_version=_integer(payload, "expected_version"),
            reason=_string(payload, "reason", required=False),
        )

    async def _delivery_discard(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self.service.discard_delivery(
            _string(payload, "delivery_id"),
            expected_version=_integer(payload, "expected_version"),
            reason=_string(payload, "reason", required=False),
        )

    async def _evaluation_submit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self.service.submit_evaluation(
            trace_id=_string(payload, "trace_id"),
            run_id=_string(payload, "run_id", required=False),
            span_id=_string(payload, "span_id", required=False),
            evaluator_name=_string(payload, "evaluator_name"),
            evaluator_version=_string(payload, "evaluator_version"),
            evaluator_type=_string(payload, "evaluator_type", required=False) or "human",
            verdict=_string(payload, "verdict"),
            score=_number(payload, "score"),
            labels=_strings(payload, "labels"),
            explanation=_string(payload, "explanation", required=False),
            evidence_refs=_strings(payload, "evidence_refs"),
            evaluation_id=_string(payload, "evaluation_id", required=False),
            degraded=_boolean(payload, "degraded"),
            left_version_key=_string(payload, "left_version_key", required=False),
            right_version_key=_string(payload, "right_version_key", required=False),
        )

    async def _experiment_compare(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self.service.compare_experiments(
            _string(payload, "left_experiment_id"),
            _string(payload, "right_experiment_id"),
        )


async def dispatch_workflow_ipc(
    service: WorkflowService | object, request: Mapping[str, Any] | object
) -> dict[str, Any]:
    return await WorkflowIPCDispatcher(service).dispatch(request)


def start_workflow_ipc_dispatch(
    service: WorkflowService | object,
    request: Mapping[str, Any] | object,
    send_response: Callable[[dict[str, Any]], Awaitable[object]],
    background_tasks: MutableSet[asyncio.Task[Any]],
) -> asyncio.Task[Any]:
    """Dispatch without occupying the caller's WebSocket receive loop.

    Resume and fork requests may execute a graph until its next interrupt.
    Keeping that await inside the receive loop deadlocks any permission reply
    sent over the same socket, so the response is delivered by a tracked task.
    """

    async def dispatch_and_send() -> None:
        response = await dispatch_workflow_ipc(service, request)
        try:
            await send_response(response)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # The client may disconnect while the graph runs.
            logger.info(
                "workflow_ipc_response_send_failed",
                extra={"error": str(exc), "request_id": response.get("request_id")},
            )

    task = asyncio.create_task(dispatch_and_send())
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return task


__all__ = [
    "IPCValidationError",
    "WorkflowIPCDispatcher",
    "dispatch_workflow_ipc",
    "start_workflow_ipc_dispatch",
]
