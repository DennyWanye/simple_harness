"""Stateless bridge for legacy global subagent completions."""
from __future__ import annotations
from typing import Any

class LegacySubagentBridge:
    """Drain currently safe completions without retaining the queue or run state."""

    __slots__ = ()

    def drain(
        self,
        registry: Any,
        working_messages: list[dict[str, Any]],
        *,
        context_metadata_enabled: bool,
        task_id: str,
        iteration: int,
    ) -> tuple[Any, ...]:
        from agent import agent_loop as _loop_api

        if registry is None:
            return ()
        queue = registry.completion_queue
        events: list[Any] = []
        while not queue.empty():
            done = queue.get_nowait()
            last = working_messages[-1] if working_messages else None
            safe = (
                last is None
                or last.get("role") in ("tool", "user")
                or (last.get("role") == "assistant" and not last.get("tool_calls"))
            )
            if not safe:
                queue.put_nowait(done)
                break
            _loop_api._append_loop_control(
                working_messages,
                {
                    "role": "user",
                    "content": f"[子代理完成] {done.task_id}({done.kind}): {done.summary}",
                },
                metadata_enabled=context_metadata_enabled,
                source="agent_loop.subagent_completion",
                anchor_after=_loop_api._latest_context_anchor(
                    working_messages,
                    fallback=f"agent-loop:{task_id}:iteration:{iteration}:start",
                ),
                fragment_id=(
                    f"agent-loop:{task_id}:iteration:{iteration}:subagent:{done.task_id}"
                ),
            )
            events.append(_loop_api.SubagentCompletionEvent(
                run_id=done.run_id,
                task_id=done.task_id,
                kind=done.kind,
                summary=done.summary,
            ))
        return tuple(events)
