"""Small services used by the product venue open coordinator."""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from deskpet.agent.turn_preparer import (
    ProductTurnPreparer,
    RoutedTurnIntent,
    TurnInput,
)
from deskpet.execution.contracts import RunRef
from deskpet.execution.run_block_signals import (
    PreflightBlocked,
    RootBlockReasonV1,
)
from deskpet.harness.contracts import HostContext
from deskpet.harness.kernel import root_run_identity
from deskpet.types.task_work_context import (
    ConversationBoundary,
    TaskWorkContextResolver,
)
from deskpet.types.task_work_context import (
    TaskRunProjection as DurableRunProjection,
)
from deskpet.types.task_work_context import (
    TaskWorkContext as DurableWorkContext,
)


def _trace_jsonable(
    value: Any,
    *,
    _depth: int = 0,
    _seen: set[int] | None = None,
) -> Any:
    if _depth >= 10:
        return {"type": type(value).__name__, "truncated": "max_depth"}
    if _seen is None:
        _seen = set()
    if value is not None and not isinstance(value, (str, int, float, bool)):
        identity = id(value)
        if identity in _seen:
            return {"type": type(value).__name__, "cycle": True}
        _seen.add(identity)
    child = {"_depth": _depth + 1, "_seen": _seen}
    if dataclasses.is_dataclass(value):
        return {
            field.name: _trace_jsonable(getattr(value, field.name), **child)
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key): _trace_jsonable(item, **child)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_trace_jsonable(item, **child) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    compact = getattr(value, "to_dict", None)
    if callable(compact):
        try:
            return _trace_jsonable(compact())
        except Exception:
            pass
    return {"type": type(value).__name__, "repr": repr(value)[:20_000]}


def _stage_trace(
    step: str,
    started_at: float,
    ended_at: float,
    *,
    input_value: Any,
    output_value: Any,
) -> dict[str, Any]:
    return {
        "step": step,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": max(0.0, (ended_at - started_at) * 1000.0),
        "input": _trace_jsonable(input_value),
        "output": _trace_jsonable(output_value),
    }


@dataclass(frozen=True, slots=True)
class ResolvedProductTurn:
    turn: TurnInput
    host: HostContext
    root_ref: RunRef
    task_scope_id: str
    work_context: DurableWorkContext
    conversation: ConversationBoundary
    projection: DurableRunProjection


class ProductTurnIdentityResolver:
    """Resolve trusted Run/task/workspace identity before product preparation."""

    def resolve(
        self,
        turn: TurnInput,
        host: HostContext,
        *,
        current_message_id: int | None,
    ) -> ResolvedProductTurn:
        if host.session_id != turn.session_id:
            raise ValueError("turn and trusted host session_id must match")
        if not turn.request_id or not turn.turn_id:
            raise ValueError("new tasks require stable request_id and turn_id")

        _, root_ref = root_run_identity(
            host.session_id,
            turn.request_id,
            turn.turn_id,
        )
        task_resolver = TaskWorkContextResolver(host.write_scope_root)
        task_scope_id = task_resolver.task_scope_id(
            host.session_id,
            turn.request_id,
            turn.turn_id,
        )
        if turn.root_run_id and turn.root_run_id != root_ref.run_id:
            raise ValueError("turn root_run_id does not match request identity")
        if turn.task_scope_id and turn.task_scope_id != task_scope_id:
            raise ValueError("turn task_scope_id does not match request identity")

        try:
            work_context = task_resolver.resolve(
                session_id=host.session_id,
                root_run_id=root_ref.run_id,
                task_scope_id=task_scope_id,
                explicit_workspace=turn.workspace_ref,
                existing_workspace=host.workspace,
                require_workspace=bool(
                    turn.workspace_ref or host.workspace or host.write_scope_root
                ),
                create_default=bool(
                    host.write_scope_root
                    and not turn.workspace_ref
                    and not host.workspace
                ),
            )
        except (OSError, RuntimeError) as exc:
            raise PreflightBlocked(
                RootBlockReasonV1.WORKSPACE_UNAVAILABLE,
                (
                    "workspace-resolution:"
                    + str(getattr(exc, "code", type(exc).__name__)),
                ),
            ) from exc
        seed_message_ref = (
            f"message:{current_message_id}"
            if current_message_id is not None
            else f"request:{turn.request_id}"
        )
        conversation = task_resolver.conversation_boundary(
            work_context,
            (seed_message_ref,),
        )
        projection = task_resolver.projection(work_context)
        effective_host = replace(
            host,
            workspace=work_context.workspace_root,
            write_scope_root=work_context.workspace_root,
        )
        resolved_turn = replace(
            turn,
            workspace_ref=work_context.workspace_root,
            root_run_id=root_ref.run_id,
            task_scope_id=task_scope_id,
        )
        return ResolvedProductTurn(
            turn=resolved_turn,
            host=effective_host,
            root_ref=root_ref,
            task_scope_id=task_scope_id,
            work_context=work_context,
            conversation=conversation,
            projection=projection,
        )


@dataclass(frozen=True, slots=True)
class PreparedProductTurn:
    prepared: Any
    routed: RoutedTurnIntent
    request_payload: dict[str, Any]


class ProductTurnPreparationService:
    """Own Context preparation and the diagnostic trace for a direct Run."""

    def __init__(self, preparer: ProductTurnPreparer) -> None:
        self._preparer = preparer

    async def prepare(
        self,
        turn: TurnInput,
        *,
        services: Mapping[str, Any],
        config: Any,
        local_llm: Any,
        tool_registry: Any,
        provider: Any,
        current_message_id: int | None,
        companion_ingress_owner: Any,
        summary_user_is_confused: Any,
        summary_latest_task_snapshot: Any,
        summary_build_reinject_msg: Any,
    ) -> PreparedProductTurn:
        prepare_started_at = time.time()
        try:
            prepared = await self._preparer.prepare_context(
                turn,
                services=services,
                config=config,
                local_llm=local_llm,
                tool_registry=tool_registry,
                current_message_id=current_message_id,
                companion_ingress_owner=companion_ingress_owner,
                summary_user_is_confused=summary_user_is_confused,
                summary_latest_task_snapshot=summary_latest_task_snapshot,
                summary_build_reinject_msg=summary_build_reinject_msg,
                provider=provider,
            )
        except Exception as exc:
            from deskpet.capabilities.builder import CapabilityBuildError

            if not isinstance(exc, CapabilityBuildError):
                raise
            raise PreflightBlocked(
                RootBlockReasonV1.CAPABILITY_UNAVAILABLE,
                (
                    "capability-preparation:"
                    + str(getattr(exc, "code", type(exc).__name__)),
                ),
            ) from exc
        prepare_ended_at = time.time()

        direct_started_at = time.time()
        prepare_direct_run = getattr(self._preparer, "prepare_direct_run", None)
        if prepare_direct_run is None:
            routed = RoutedTurnIntent(prepared=prepared)
        else:
            routed = await prepare_direct_run(prepared, services=services)
        direct_ended_at = time.time()

        request_payload = self._preparer.prepare_workflow_request_payload(
            routed,
            turn,
            config,
        )
        request_payload["product_turn_trace"] = [
            _stage_trace(
                "prepare_context",
                prepare_started_at,
                prepare_ended_at,
                input_value={
                    "turn": turn,
                    "current_message_id": current_message_id,
                },
                output_value={
                    "messages": prepared.messages,
                    "bundle": prepared.bundle,
                    "prepared_context": prepared.prepared_context,
                    "eligibility": prepared.eligibility,
                    "preference_resolution": prepared.preference_resolution,
                    "companion_authority_state": (
                        prepared.companion_authority_state
                    ),
                },
            ),
            _stage_trace(
                "direct_run",
                direct_started_at,
                direct_ended_at,
                input_value={
                    "message_count": len(prepared.messages),
                    "task_type": getattr(prepared.bundle, "task_type", None),
                },
                output_value={
                    "authority": "main_agent",
                    "context_source": "assembled_session",
                },
            ),
        ]
        return PreparedProductTurn(
            prepared=prepared,
            routed=routed,
            request_payload=request_payload,
        )


__all__ = [
    "PreparedProductTurn",
    "ProductTurnIdentityResolver",
    "ProductTurnPreparationService",
    "ResolvedProductTurn",
]
