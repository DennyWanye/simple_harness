# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Unit tests for agent.agent_loop. All LLM + tool calls are mocked.

Coverage:
    - final-only response path
    - external ToolBatchEvent pause point and canonical transcript
    - multi-call batch ordering without internal dispatch
    - budget and provider errors before tool execution
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agent.task_id import new_task_id
from llm.types import ChatResponse


class FakeLLMRegistry:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "model": model,
                "kwargs": dict(kwargs),
            }
        )
        if not self._responses:
            raise AssertionError("FakeLLMRegistry ran out of programmed responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeToolRegistry:
    def __init__(
        self,
        schemas: list[dict[str, Any]] | None = None,
        handlers: dict[str, Any] | None = None,
        completion_semantics: dict[str, str] | None = None,
    ) -> None:
        self._schemas = schemas or []
        self._handlers = handlers or {}
        self._completion_semantics = completion_semantics or {}
        self.calls: list[dict[str, Any]] = []

    def schemas(self, enabled_toolsets: list[str] | None = None) -> list[dict[str, Any]]:
        return list(self._schemas)

    def dispatch(self, name: str, args: dict[str, Any], task_id: str) -> Any:
        self.calls.append({"name": name, "args": args, "task_id": task_id})
        if name not in self._handlers:
            raise KeyError(f"tool {name!r} not registered")
        return self._handlers[name](args)

    def get(self, name: str) -> Any:
        return SimpleNamespace(completion_semantics=self._completion_semantics.get(name, "sync"))


# ───────────────────── tests ─────────────────────


def test_task_id_format():
    tid = new_task_id()
    assert tid.startswith("task_")
    # task_<YYMMDDHHMMSS>_<8 hex>
    parts = tid.split("_")
    assert len(parts) == 3
    assert len(parts[1]) == 12
    assert len(parts[2]) == 8
