"""Projection-only bridge from prepared product facts to an SDK RunStart."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from simple_harness import ExecutionSessionId, JsonValue, RequestId, RunId
from simple_harness.runtime import RunStart


class ProductContextAdapter:
    """Does not implement ContextPort; durable messages remain SDK-owned."""

    def project_run_start(
        self,
        *,
        execution_session_id: str,
        run_id: str,
        request_id: str,
        turn_id: str,
        messages: Sequence[Mapping[str, JsonValue]],
        capability_snapshot: Mapping[str, JsonValue],
        tool_catalog_generation: int,
        trusted_input: Mapping[str, JsonValue] | None = None,
    ) -> RunStart:
        projected_messages: list[dict[str, JsonValue]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role not in {"system", "user", "assistant", "tool"}:
                raise ValueError("prepared message role is invalid")
            if not isinstance(content, str):
                raise TypeError("prepared message content must be text")
            projected_messages.append({"role": role, "content": content})
        start_input = dict(trusted_input or {})
        if "messages" in start_input or "capability_snapshot" in start_input:
            raise ValueError("trusted_input cannot override projected authority")
        start_input.update(
            {
                "messages": projected_messages,
                "capability_snapshot": dict(capability_snapshot),
            }
        )
        return RunStart(
            ExecutionSessionId(execution_session_id),
            RunId(run_id),
            RequestId(request_id),
            turn_id,
            start_input,
            tool_catalog_generation,
        )


__all__ = ("ProductContextAdapter",)
