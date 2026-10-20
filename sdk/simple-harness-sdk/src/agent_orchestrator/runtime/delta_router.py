# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""逐字输出（AgentOS 迁移第 5 块，2026-10-21）：把模型正在生成的正文片段交给订阅者。

提供方在流式回复里每收到一段**用户可见正文**（``content``；思考 ``reasoning_content`` 与
工具调用参数不算）就调一次 ``delta_sink(request, text)``。这里按请求号里的运行号
（``<run_id>:provider-turn:<n>``）找到订阅者转发；最外层再套一层 :class:`StreamSignals`，
一次模型调用结束发 ``end``、抛错发 ``error``。

事件格式 v1（加字段不升版本，改字段含义才升）::

    {"v": 1, "kind": "piece" | "end" | "error",
     "scope": "chat" | "mission" | "unknown", "run_id": "...",
     "generation": "<请求号>", "seq": 3,
     "text": "片",                        # piece
     "finish": "stop", "tool_calls": 1,   # end
     "error": "provider_timeout",         # error
     ...scope 字段（chat: session_id / turn_id；mission: mission_id / subject_id / view）}

- ``generation`` 是一次模型调用的请求号，``seq`` 在一次 generation 里从 1 递增：换了 generation 就是
  新一次生成，消费端从头拼，不会把两次生成拼在一起。去重键 ``(generation, seq)``。
- 片段只是预览，不落盘、不重放：断线后读持久记录（聊天回执 / 任务快照与产物）。
- 订阅者抛异常只记日志，不影响模型调用；没人订阅就丢弃。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from .model_fallback import run_of

logger = logging.getLogger(__name__)

EVENT_VERSION = 1
OutputCallback = Callable[[Mapping[str, Any]], None]
ScopeResolver = Callable[[str], Mapping[str, Any] | None]
_WRAPPER_FIELDS = ("_primary", "_fallback", "_inner")
_MAX_CACHED = 1000


def leaf_providers(provider: Any) -> list[Any]:
    """装饰器（回退、边际学习、校准、信号）里面真正发请求的提供方；没有装饰就是它自己。"""

    out: list[Any] = []
    seen: set[int] = set()
    stack = [provider]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        inner = [
            getattr(current, name)
            for name in _WRAPPER_FIELDS
            if name in getattr(current, "__dict__", {})
        ]
        if inner:
            stack.extend(inner)
        else:
            out.append(current)
    return out


def _error_code(error: BaseException) -> str:
    code = getattr(error, "error_code", None)
    return str(code) if code else type(error).__name__


class OutputStream:
    """按运行号分发正文片段与结束 / 出错信号；``attach`` 把自己装到提供方上。"""

    def __init__(self, *, resolver: ScopeResolver | None = None) -> None:
        #: 运行号 → 作用域字段（宿主给：聊天会话或任务绑定）；解析失败记 ``unknown``
        self.resolver = resolver
        self._all: list[OutputCallback] = []
        self._by_run: dict[str, list[OutputCallback]] = {}
        self._seq: dict[str, int] = {}  # generation → 已发片段数
        self._scopes: dict[str, dict[str, Any]] = {}

    # ---- 装到提供方上 ----------------------------------------------------------------------

    def attach(self, provider: Any) -> Any:
        """装到 ``provider`` 内层每个带 ``delta_sink`` 槽位的提供方上，外面套结束 / 出错信号；
        返回套好的提供方（内核就用它）。"""

        if provider is None:
            return None
        for leaf in leaf_providers(provider):
            if hasattr(type(leaf), "delta_sink") or "delta_sink" in getattr(leaf, "__dict__", {}):
                leaf.delta_sink = self
        return provider if isinstance(provider, StreamSignals) else StreamSignals(provider, self)

    # ---- 订阅 --------------------------------------------------------------------------------

    def subscribe(
        self, callback: OutputCallback, *, run_id: str | None = None
    ) -> Callable[[], None]:
        if not callable(callback):
            raise TypeError("callback must be callable")
        if run_id is not None and (not isinstance(run_id, str) or not run_id):
            raise ValueError("run_id must be a non-empty string")
        listeners = self._all if run_id is None else self._by_run.setdefault(run_id, [])
        listeners.append(callback)

        def unsubscribe() -> None:
            try:
                listeners.remove(callback)
            except ValueError:
                return
            if run_id is not None and not listeners:
                self._by_run.pop(run_id, None)

        return unsubscribe

    def forget_run(self, run_id: str) -> None:
        """一个运行结束了（聊天回合收尾）：丢掉它缓存的作用域。"""

        self._scopes.pop(run_id, None)

    # ---- 提供方那头 ----------------------------------------------------------------------

    def __call__(self, request: Any, text: str) -> None:
        """一段正文（``delta_sink`` 协议）。"""

        if not text:
            return
        generation = self._generation(request)
        if generation is None:
            return
        seq = self._seq.get(generation, 0) + 1
        self._seq[generation] = seq
        if len(self._seq) > _MAX_CACHED:  # 没收到结束信号的旧 generation 不能无限积累
            for stale in list(self._seq)[: len(self._seq) - _MAX_CACHED]:
                self._seq.pop(stale, None)
        self._emit(request, {"kind": "piece", "generation": generation, "seq": seq, "text": text})

    def ended(self, request: Any, response: Any) -> None:
        generation = self._generation(request)
        if generation is None:
            return
        self._emit(
            request,
            {
                "kind": "end",
                "generation": generation,
                "seq": self._seq.pop(generation, 0),
                "finish": getattr(response, "finish_reason", None),
                "tool_calls": len(getattr(response, "tool_calls", ()) or ()),
            },
        )

    def failed(self, request: Any, error: BaseException) -> None:
        generation = self._generation(request)
        if generation is None:
            return
        self._emit(
            request,
            {
                "kind": "error",
                "generation": generation,
                "seq": self._seq.pop(generation, 0),
                "error": _error_code(error),
            },
        )

    # ---- 内部 --------------------------------------------------------------------------------

    @staticmethod
    def _generation(request: Any) -> str | None:
        try:
            return str(request.request_id.value)
        except AttributeError:
            return None

    def _scope(self, run_id: str) -> dict[str, Any]:
        cached = self._scopes.get(run_id)
        if cached is not None:
            return cached
        scope: dict[str, Any] = {"scope": "unknown"}
        if self.resolver is not None:
            try:
                resolved = self.resolver(run_id)
                if isinstance(resolved, Mapping) and resolved.get("scope"):
                    scope = dict(resolved)
            except Exception:  # noqa: BLE001 - 作用域查不到不碰模型调用
                logger.debug("output scope unresolved for run %s", run_id, exc_info=True)
        # 只缓存任务作用域（绑定在一次尝试里不变）；聊天作用域带会变的回合号，每次现查
        if scope["scope"] == "mission":
            if len(self._scopes) >= _MAX_CACHED:
                self._scopes.pop(next(iter(self._scopes)))
            self._scopes[run_id] = scope
        return scope

    def _emit(self, request: Any, body: Mapping[str, Any]) -> None:
        try:
            run_id = run_of(request)
        except Exception:  # noqa: BLE001 - 请求号形状不对就没人可转
            return
        listeners = (*self._all, *self._by_run.get(run_id, ()))
        if not listeners:
            return
        event = {"v": EVENT_VERSION, "run_id": run_id, **self._scope(run_id), **body}
        for callback in listeners:
            try:
                callback(event)
            except Exception:  # noqa: BLE001 - 订阅者出错不碰模型调用
                logger.warning("output subscriber failed for run %s", run_id, exc_info=True)


class StreamSignals:
    """提供方最外层：一次调用结束发 ``end``、抛错发 ``error``；别的都透传。"""

    def __init__(self, inner: Any, stream: OutputStream) -> None:
        self._inner = inner
        self._stream = stream

    @property
    def target(self) -> Any:
        return self._inner.target

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def invoke(self, request: Any, *, cancel: Any) -> Any:
        try:
            response = await self._inner.invoke(request, cancel=cancel)
        except BaseException as error:
            self._stream.failed(request, error)
            raise
        self._stream.ended(request, response)
        return response


__all__ = (
    "EVENT_VERSION",
    "OutputCallback",
    "OutputStream",
    "ScopeResolver",
    "StreamSignals",
    "leaf_providers",
)
