# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Accumulate one chat-completions SSE response; never expose partial tool calls."""

from __future__ import annotations

import json
from typing import Any

from .errors import ProviderProtocolError

#: Memory bound on what one response may make this process hold: the data lines of one
#: event while it is being assembled, and the text kept from the whole stream (content,
#: reasoning, tool arguments).  Transport bytes that are not retained are not counted:
#: 2026-10-10 parse 局，思考每个 token 一个 SSE 事件、每个约 230 字节，原来按原始字节累计到
#: 16 MiB 就拒绝，约 3.5 万个思考 token 的调用一律在 4 分钟左右"回复无法解析"，而留在内存里的
#: 文字只有几百 KB。上限不变，改成只数留下来的。
RETAINED_LIMIT = 16 * 1024 * 1024


def _reject(reason: str) -> ProviderProtocolError:
    """Name the rule the stream broke (no response content), so the record says why."""

    return ProviderProtocolError(public_message=f"invalid chat stream: {reason}")


def _billed(usage: Any) -> bool:
    """A usage object that states a charge: any positive token count."""

    return isinstance(usage, dict) and any(
        type(v) is int and v > 0 for v in usage.values()
    )


def _placeholder(value: dict[str, Any]) -> bool:
    """A chunk with no content, no tool-call delta, no finish and no billing."""

    if _billed(value.get("usage")):
        return False
    choices = value.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return False
    choice = choices[0]
    finish = choice.get("finish_reason")
    if finish not in (None, ""):
        return False
    delta = choice.get("delta")
    if delta is None:
        return True
    if not isinstance(delta, dict):
        return False
    return not delta.get("content") and not delta.get("tool_calls") and not delta.get("reasoning_content")


class ChatStream:
    """Single-choice deltas, with an explicit finish and transport terminator.

    The adapter still returns exactly one ProviderResponse. Reasoning deltas and
    SSE keepalives are consumed without being presented as assistant content.
    """

    def __init__(self) -> None:
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.tools: dict[int, dict[str, Any]] = {}
        self.identity: dict[str, str] = {}
        self.usage: Any = None
        self.finish: str | None = None
        self.done = False
        self._data: list[str] = []
        self._pending = 0  # bytes buffered for the event being assembled
        self._retained = 0  # bytes of text kept from the whole stream

    def line(self, line: str) -> None:
        if not line:
            if self._data:
                data, self._data, self._pending = "\n".join(self._data), [], 0
                self._event(data)
        elif line.startswith("data:"):
            self._pending += len(line.encode("utf-8"))
            if self._pending > RETAINED_LIMIT:
                raise _reject("one event larger than the memory bound")
            self._data.append(line[5:].removeprefix(" "))

    def _keep(self, text: str) -> None:
        self._retained += len(text.encode("utf-8"))
        if self._retained > RETAINED_LIMIT:
            raise _reject("retained text larger than the memory bound")

    def _event(self, data: str) -> None:
        if self.done:
            raise _reject("event after [DONE]")
        if data == "[DONE]":
            if self.finish is None:
                raise _reject("[DONE] without a finish_reason")
            self.done = True
            return
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            raise _reject("event is not JSON") from None
        if not isinstance(value, dict):
            raise _reject("event is not an object")
        if "error" in value:
            raise _reject("event carries an error object")
        if _placeholder(value):
            # Some compatible relays open the stream with an empty delta under a
            # provisional id and repeat a 0/0/0 usage on every delta.  Such a chunk
            # carries nothing, so it pins neither the response identity nor billing.
            return
        for name in ("id", "model"):
            part = value.get(name)
            if part is not None:
                if not isinstance(part, str) or (
                    name in self.identity and self.identity[name] != part
                ):
                    raise _reject(f"{name} changed or is not a string")
                self.identity[name] = part
        usage = value.get("usage")
        if usage is not None and not _billed(usage):
            usage = None  # an all-zero usage is a placeholder, never a billing statement
        if usage is not None:
            if self.usage is not None and self.usage != usage:
                self.usage = None  # contradictory billing must remain unknown
                raise _reject("contradictory usage statements")
            self.usage = usage
        choices = value.get("choices")
        if not isinstance(choices, list) or len(choices) > 1:
            raise _reject("choices is not a list of at most one")
        if not choices:  # OpenAI's trailing usage-only chunk
            return
        choice = choices[0]
        if (
            not isinstance(choice, dict)
            or type(choice.get("index")) is not int
            or choice["index"] != 0
        ):
            raise _reject("choice is not index 0")
        delta = choice.get("delta")
        if not isinstance(delta, dict) or delta.get("role") not in (None, "assistant"):
            raise _reject("delta is not an assistant delta")
        content, calls = delta.get("content"), delta.get("tool_calls")
        reasoning = delta.get("reasoning_content")
        if self.finish is not None and (content or calls or reasoning):
            raise _reject("content after finish_reason")
        if reasoning is not None:
            if not isinstance(reasoning, str):
                raise _reject("reasoning_content is not a string")
            self._keep(reasoning)
            self.reasoning.append(reasoning)
        if content is not None:
            if not isinstance(content, str):
                raise _reject("content is not a string")
            self._keep(content)
            self.content.append(content)
        if calls is not None:
            if not isinstance(calls, list):
                raise _reject("tool_calls is not a list")
            for call in calls:
                self._tool(call)
        finish = choice.get("finish_reason")
        # Some compatible relays emit an empty string on intermediate deltas.
        # It carries no terminal meaning; [DONE] still requires a real finish.
        if finish == "":
            finish = None
        if finish is not None:
            if not isinstance(finish, str) or not finish or self.finish is not None:
                raise _reject("finish_reason repeated or not a string")
            self.finish = finish

    def _tool(self, delta: Any) -> None:
        if not isinstance(delta, dict):
            raise _reject("tool call delta is not an object")
        index = delta.get("index")
        if type(index) is not int or not 0 <= index < 128:
            raise _reject("tool call index missing or out of range")
        call = self.tools.setdefault(index, {"function": {"name": "", "arguments": ""}})
        for name in ("id", "type"):
            value = delta.get(name)
            if value is not None:
                if not isinstance(value, str) or (name in call and call[name] != value):
                    raise _reject(f"tool call {name} changed or is not a string")
                call[name] = value
        function = delta.get("function")
        if function is not None:
            if not isinstance(function, dict):
                raise _reject("tool call function is not an object")
            for name in ("name", "arguments"):
                value = function.get(name)
                if value is not None:
                    if not isinstance(value, str):
                        raise _reject(f"tool call function {name} is not a string")
                    self._keep(value)
                    call["function"][name] += value

    def payload(self) -> dict[str, Any]:
        if not self.done:
            raise _reject("stream ended without [DONE]")
        if self._data:
            raise _reject("stream ended inside an event")
        if set(self.tools) != set(range(len(self.tools))):
            raise _reject("tool call indexes are not contiguous")
        return {
            **self.identity,
            "usage": self.usage,
            "choices": [
                {
                    "finish_reason": self.finish,
                    "message": {
                        "role": "assistant",
                        "content": "".join(self.content),
                        "tool_calls": [self.tools[i] for i in sorted(self.tools)],
                        **({"reasoning_content": "".join(self.reasoning)} if self.reasoning else {}),
                    },
                }
            ],
        }
