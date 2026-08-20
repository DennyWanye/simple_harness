"""Delivery adapter - event delivery to UI bridge.

Implements SDK delivery sink protocol by bridging to product's
RunPresenter, SessionDB, WebSocket, and artifact systems.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_PUBLIC_TOOL_ACTIONS = {
    "memory_recall": "查找相关记忆和上下文",
    "memory_search": "查找相关记忆和上下文",
    "file_read": "读取目标文件进行核对",
    "read_file": "读取目标文件进行核对",
    "file_glob": "定位并检查相关文件",
    "file_grep": "定位并检查相关文件",
    "web_search": "检索相关公开资料",
    "web_fetch": "读取相关公开资料",
    "run_shell": "运行必要的检查命令",
    "write_file": "更新相关文件",
    "edit_file": "更新相关文件",
}


def public_tool_turn_narration(
    provider_content: str,
    tool_names: Sequence[str],
) -> str:
    """Build bounded public work narration from public-only inputs."""

    public_content = str(provider_content).strip()
    if public_content:
        return public_content
    actions: list[str] = []
    for raw_name in tool_names:
        action = _PUBLIC_TOOL_ACTIONS.get(
            str(raw_name).strip().lower(),
            "执行下一步工具操作",
        )
        if action not in actions:
            actions.append(action)
        if len(actions) >= 2:
            break
    if not actions:
        return ""
    if len(actions) == 1:
        return f"我正在{actions[0]}，完成后会整理结果。"
    return f"我先{actions[0]}，再{actions[1]}，完成后会整理结果。"


class ProductDeliveryAdapter:
    """Adapter between SDK execution events and product UI delivery.

    Converts SDK delivery payloads to RunEvents and delegates to RunPresenter
    for formatting, persistence, and WebSocket delivery. RunPresenter handles
    all SessionDB updates, WebSocket sends, and artifact card generation.
    """

    def __init__(
        self,
        *,
        session_id: str,
        request_id: str,
        run_id: str,
        presenter: Any,  # RunPresenter
        adapter: Any,  # RunEventPresentationAdapter
        context: Any,  # RunPresentationContext
        state: Any,  # PresentationState
    ) -> None:
        """Initialize delivery adapter with product presentation services.

        Args:
            session_id: Session identifier
            request_id: Request identifier
            run_id: Run identifier (for logging)
            presenter: RunPresenter for event formatting and delivery
            adapter: RunEventPresentationAdapter for RunEvent → AgentEvent conversion
            context: RunPresentationContext with WebSocket/SessionDB/etc
            state: PresentationState tracking tool calls and final text
        """
        self._session_id = session_id
        self._request_id = request_id
        self._run_id = run_id
        self._presenter = presenter
        self._adapter = adapter
        self._context = context
        self._state = state
        self._idempotency_seen: set[str] = set()
        self._pending_public_narration: dict[int, str] = {}
        self._projected_narration_iterations: set[int] = set()
        self._staged_call_iterations: dict[str, int] = {}
        self._tool_iterations: dict[str, int] = {}
        self._next_tool_iteration = 0

    async def capture_public_narration(
        self,
        content: str,
        *,
        iteration: int,
        call_ids: tuple[str, ...],
    ) -> None:
        """Stage one Provider turn's public content and complete call identity set.

        This method accepts either the Provider adapter's normalized public
        ``message.content`` or the host's bounded fallback derived only from
        public tool names. Private provider reasoning is intentionally not part
        of this boundary.
        """

        stable_iteration = max(0, int(iteration))
        narration = str(content).strip()
        if narration:
            self._pending_public_narration[stable_iteration] = narration
        for call_id in call_ids:
            normalized_call_id = str(call_id).strip()
            if normalized_call_id:
                self._staged_call_iterations[normalized_call_id] = stable_iteration
        self._next_tool_iteration = max(
            self._next_tool_iteration,
            stable_iteration + 1,
        )

    async def handle_event(
        self,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> None:
        """Handle one SDK delivery event.

        Deserializes payload to RunEvent and delegates to RunPresenter for
        presentation. RunPresenter internally handles WebSocket delivery,
        SessionDB persistence, and artifact generation.

        Args:
            payload: SDK delivery payload (contains RunEvent data)
            idempotency_key: Unique key for exactly-once delivery semantics
        """
        # Idempotency: skip if already delivered
        if idempotency_key in self._idempotency_seen:
            logger.debug(
                "delivery_idempotency_skip",
                key=idempotency_key,
                session_id=self._session_id,
                run_id=self._run_id,
            )
            return

        try:
            # Deserialize SDK payload → RunEvent
            run_event = self._deserialize_run_event(payload)

            # Delegate to RunPresenter for presentation
            # (This internally handles WebSocket, SessionDB, formatting, etc.)
            await self._presenter.present_run_event(
                event=run_event,
                adapter=self._adapter,
                context=self._context,
                state=self._state,
            )

            # Mark as delivered
            self._idempotency_seen.add(idempotency_key)

            logger.debug(
                "delivery_handled",
                key=idempotency_key,
                event_kind=run_event.candidate.kind,
                session_id=self._session_id,
                run_id=self._run_id,
            )

        except Exception as exc:
            logger.error(
                "delivery_handler_failed",
                key=idempotency_key,
                error=str(exc),
                session_id=self._session_id,
                run_id=self._run_id,
                exc_info=True,
            )
            # Don't re-raise - delivery failure should not crash SDK Runtime

    async def present_tool_call(self, call: Any) -> None:
        """Project an SDK Tool invocation through the existing chat presenter."""

        call_id = call.call_id.value
        staged_iteration = self._staged_call_iterations.pop(call_id, None)
        if staged_iteration is None:
            iteration = self._next_tool_iteration
            self._next_tool_iteration += 1
        else:
            iteration = staged_iteration
        narration = self._pending_public_narration.pop(iteration, "")
        self._tool_iterations[call_id] = iteration

        if iteration in self._projected_narration_iterations:
            narration = ""
        else:
            if not narration:
                narration = public_tool_turn_narration("", (call.name,))
            self._projected_narration_iterations.add(iteration)

        if narration:
            try:
                from agent.agent_loop import AssistantMessageEvent

                await self._presenter.present(
                    AssistantMessageEvent(
                        content=narration,
                        reasoning_content="",
                        tool_calls=[],
                        iteration=iteration,
                    ),
                    self._context,
                    self._state,
                )
            except Exception as exc:  # noqa: BLE001 - projection is best-effort
                logger.warning(
                    "sdk_public_narration_projection_failed",
                    tool_name=getattr(call, "name", ""),
                    run_id=self._run_id,
                    error_type=type(exc).__name__,
                )

        try:
            from agent.agent_loop import ToolCallEvent
            from llm.types import ToolCall

            await self._presenter.present(
                ToolCallEvent(
                    tool_call=ToolCall(
                        id=call.call_id.value,
                        name=call.name,
                        arguments=dict(call.arguments),
                    ),
                    iteration=iteration,
                ),
                self._context,
                self._state,
            )
        except Exception as exc:  # noqa: BLE001 - projection is best-effort
            logger.warning(
                "sdk_tool_call_projection_failed",
                tool_name=getattr(call, "name", ""),
                run_id=self._run_id,
                error=str(exc),
            )

    async def present_tool_result(self, call: Any, result: Any) -> None:
        """Project and persist one settled SDK Tool result for live/history UI."""

        iteration = self._tool_iterations.pop(call.call_id.value, 0)
        try:
            import json

            from agent.agent_loop import ToolResultEvent
            from simple_harness import thaw_json

            envelope = {
                "outcome": result.outcome.value,
                "value": thaw_json(result.value),
                "error_code": result.error_code,
                "public_message": result.public_message,
                "retryable": result.retryable,
            }
            error = None
            if result.error_code is not None:
                error = {
                    "code": result.error_code,
                    "message": result.public_message or "Tool execution failed.",
                }
            await self._presenter.present(
                ToolResultEvent(
                    tool_call_id=call.call_id.value,
                    tool_name=call.name,
                    result=json.dumps(envelope, ensure_ascii=False),
                    outcome_status=result.outcome.value,
                    outcome_error=error,
                    iteration=iteration,
                ),
                self._context,
                self._state,
            )
        except Exception as exc:  # noqa: BLE001 - projection is best-effort
            logger.warning(
                "sdk_tool_result_projection_failed",
                tool_name=getattr(call, "name", ""),
                run_id=self._run_id,
                error=str(exc),
            )

    def _deserialize_run_event(self, payload: dict[str, Any]) -> "RunEvent":
        """Deserialize SDK delivery payload → RunEvent.

        Args:
            payload: SDK delivery payload (serialized RunEvent)

        Returns:
            RunEvent dataclass instance

        Raises:
            ValueError: If payload structure is invalid
        """
        # Import here to avoid circular dependencies
        from deskpet.execution.contracts import RunEvent, RunEventCandidate, LiveCursor

        try:
            # Reconstruct nested candidate object
            candidate_data = payload.get("candidate", {})
            candidate = RunEventCandidate(
                event_key=candidate_data.get("event_key") or payload.get("event_id", "unknown"),
                kind=candidate_data.get("kind", "status_changed"),
                status=candidate_data.get("status", "accepted"),  # Use valid OutcomeStatus value
                driver_kind=candidate_data.get("driver_kind", "react"),
                correlation=candidate_data.get("correlation", {}),
                payload=candidate_data.get("payload", {}),
                error=candidate_data.get("error"),
                artifact_refs=candidate_data.get("artifact_refs", []),
            )

            # Reconstruct LiveCursor if present
            live_cursor = None
            if "live_cursor" in payload and payload["live_cursor"] is not None:
                cursor_data = payload["live_cursor"]
                live_cursor = LiveCursor(
                    live_seq=cursor_data["live_seq"],
                    sent_at=cursor_data["sent_at"],
                )

            # Construct RunEvent
            run_event = RunEvent(
                event_id=payload["event_id"],
                run_id=payload["run_id"],
                root_run_id=payload["root_run_id"],
                session_id=payload["session_id"],
                durable_seq=payload.get("durable_seq"),
                candidate=candidate,
                created_at=payload["created_at"],
                live_cursor=live_cursor,
            )

            return run_event

        except (KeyError, TypeError) as exc:
            logger.error(
                "run_event_deserialization_failed",
                error=str(exc),
                payload_keys=list(payload.keys()),
                session_id=self._session_id,
                run_id=self._run_id,
            )
            raise ValueError(f"Invalid RunEvent payload: {exc}") from exc

    async def finish(self) -> None:
        """Finalize presentation after Run completes.

        Calls RunPresenter.finish_turn() to perform any cleanup actions
        like recording billing usage or assembler feedback.

        Also clears idempotency tracking.
        """
        try:
            await self._presenter.finish_turn(self._context, self._state)
            logger.debug(
                "delivery_adapter_finished",
                session_id=self._session_id,
                run_id=self._run_id,
            )
        except Exception as exc:
            logger.error(
                "delivery_adapter_finish_failed",
                error=str(exc),
                session_id=self._session_id,
                run_id=self._run_id,
                exc_info=True,
            )
        finally:
            # Clear idempotency tracking
            self._idempotency_seen.clear()
            self._pending_public_narration.clear()
            self._projected_narration_iterations.clear()
            self._staged_call_iterations.clear()
            self._tool_iterations.clear()
            self._next_tool_iteration = 0
            # Don't re-raise - finish failure should not block Run completion


__all__ = ("ProductDeliveryAdapter",)
