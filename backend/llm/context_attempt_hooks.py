# SPDX-License-Identifier: BUSL-1.1
"""上下文尝试挂钩：模型注册表、路由、OpenAI 兼容提供方经这里调用上下文尝试记录。

下层只认这四个入口；真正的记录由上层 `agent.context_report` 在设置尝试记录库时注册进来
（`set_context_attempt_store`），清空时撤销。没注册时四个入口都直接放行，
与上层"没有记录库就放行"的语义一致（代码质量整治 8.7：分层例外改为依赖倒置）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContextAttemptHooks:
    call: Callable[..., Awaitable[Any]]
    iterate: Callable[..., AsyncIterator[Any]]
    mark_sent: Callable[[], None]
    mark_transport_retry: Callable[[], None]


class _Installed:
    """当前注册的实现（类属性保存，避免模块级 global 赋值）。"""

    hooks: ContextAttemptHooks | None = None


def install_context_attempt_hooks(hooks: ContextAttemptHooks | None) -> None:
    """上层注册（传 None 撤销）。"""

    _Installed.hooks = hooks


async def auto_context_attempt_call(*, invoke: Callable[[], Awaitable[Any]], **kwargs: Any) -> Any:
    hooks = _Installed.hooks
    if hooks is None:
        return await invoke()
    return await hooks.call(invoke=invoke, **kwargs)


async def auto_context_attempt_iter(
    *, iterator: Callable[[], AsyncIterator[Any]], **kwargs: Any
) -> AsyncIterator[Any]:
    hooks = _Installed.hooks
    source = iterator() if hooks is None else hooks.iterate(iterator=iterator, **kwargs)
    async for item in source:
        yield item


def mark_current_attempt_sent() -> None:
    """传输协程第一句的标记；故意不 await。"""

    hooks = _Installed.hooks
    if hooks is not None:
        hooks.mark_sent()


def mark_current_attempt_transport_retry() -> None:
    hooks = _Installed.hooks
    if hooks is not None:
        hooks.mark_transport_retry()


__all__ = [
    "ContextAttemptHooks",
    "auto_context_attempt_call",
    "auto_context_attempt_iter",
    "install_context_attempt_hooks",
    "mark_current_attempt_sent",
    "mark_current_attempt_transport_retry",
]
