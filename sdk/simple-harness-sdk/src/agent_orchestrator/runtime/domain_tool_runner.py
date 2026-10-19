# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""领域工具的执行：同步/异步两种 ``invoke``、每任务一把锁、超时与取消的"不确定"语义。

规则（2026-10-20 裁决）：
- 锁从调用开始持有，到物理调用**真正结束**才放——超时或被取消之后也一样；放锁由后台任务接手，
  调用方（模型的回合）不被拖住。
- 超时从拿到锁后起算。
- ``async def`` 的工具在事件循环上跑：超时/取消时先取消它，再等它结束；它吞掉取消不退出时，
  等到宽限期结束就交给后台任务继续等，结果记为"不确定"。
- 普通函数在线程里跑：线程停不下来，超时/取消时结果记为"不确定"，后台任务等线程结束再放锁。
- 工具抛异常 → "失败"，只记异常类型名，不记文字（可能带凭据）。
- 返回值必须是规范 JSON 对象（与事件、账本同一套校验：不收元组、NaN、非字符串键），否则 →
  "失败"（``tool_result_invalid``）。
- 等锁也有上限（``timeout_seconds``）：上一次超时的调用还没结束、锁还占着时，这次直接判"失败"
  （``tool_busy``），不发起调用，模型/人可稍后再试；锁本身照旧等物理调用结束才放。
- ``on_start`` 在拿到锁、真正发起调用前回调一次（审计"开始"用）；没开始就被取消的调用不算开始。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from simple_harness.contracts import canonical_json

from .domain_tools import DomainTool, ToolCallContext

logger = logging.getLogger(__name__)

#: 被取消/超时的协程不肯退出时，再等它多久才交给后台（秒）。
CANCEL_GRACE_SECONDS = 2.0
#: 结果序列化后允许的最大字节数；超过记为失败（工具该自己分页或裁剪）。
MAX_RESULT_BYTES = 1 * 1024 * 1024

_background: set[asyncio.Task[Any]] = set()


@dataclass(frozen=True, slots=True)
class DomainCallOutcome:
    status: str  # "succeeded" | "failed" | "unknown"
    value: Mapping[str, Any] | None = None
    error_code: str | None = None
    error_type: str | None = None
    public_message: str = ""
    duration_ms: int = 0
    #: 物理调用还没结束，锁交给了后台任务（超时/取消）。
    still_running: bool = False


def _release_when_done(
    pending: asyncio.Task[Any] | asyncio.Future[Any], lock: asyncio.Lock
) -> None:
    async def waiter() -> None:
        try:
            await asyncio.wait((pending,))
            if not pending.cancelled() and pending.exception() is not None:
                logger.debug("domain tool call ended with an error after its caller moved on")
        finally:
            lock.release()

    task = asyncio.get_running_loop().create_task(waiter())
    _background.add(task)
    task.add_done_callback(_background.discard)


def _check_result(value: Any) -> Mapping[str, Any]:
    """与事件、账本同一套 JSON 规则：过不了这里，就不会出现"执行器说成功、事件写不进去"。"""

    if not isinstance(value, Mapping):
        raise TypeError("tool result must be a JSON object")
    result = dict(value)
    try:
        encoded = canonical_json(result)
    except (TypeError, ValueError, KeyError) as error:
        raise ValueError("tool result is not canonical JSON") from error
    if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
        raise ValueError("tool result exceeds the size limit")
    return result


async def run_domain_tool(
    tool: DomainTool,
    arguments: Mapping[str, Any],
    context: ToolCallContext,
    *,
    lock: asyncio.Lock,
    on_start: Callable[[], None] | None = None,
) -> DomainCallOutcome:
    """拿锁 → 跑 ``invoke`` → 结果；锁在物理调用结束时放（可能晚于返回）。"""

    waited = time.monotonic()
    try:
        async with asyncio.timeout(tool.timeout_seconds):
            await lock.acquire()
    except TimeoutError:
        return DomainCallOutcome(
            status="failed",
            error_code="tool_busy",
            public_message=f"{tool.name} is still busy with an earlier call; try again later",
            duration_ms=_elapsed(waited),
        )
    started = time.monotonic()
    if on_start is not None:
        try:
            on_start()
        except Exception:  # noqa: BLE001 - 审计失败不挡调用
            logger.exception("domain tool start audit failed")
    loop = asyncio.get_running_loop()
    pending: asyncio.Future[Any]
    in_thread = not inspect.iscoroutinefunction(tool.invoke)
    try:
        if in_thread:
            pending = loop.run_in_executor(None, tool.invoke, arguments, context)
        else:
            pending = loop.create_task(tool.invoke(arguments, context))  # type: ignore[arg-type]
    except Exception as error:  # noqa: BLE001 - 连调用都没发出去，按失败记
        lock.release()
        return _failed(error, started)

    try:
        await asyncio.wait((pending,), timeout=tool.timeout_seconds)
    except asyncio.CancelledError:
        await _abandon(pending, lock, in_thread=in_thread)
        raise
    if not pending.done():
        settled = await _abandon(pending, lock, in_thread=in_thread)
        return DomainCallOutcome(
            status="unknown",
            error_code="tool_timeout",
            public_message=f"{tool.name} did not finish within {tool.timeout_seconds:g}s;"
            " its outcome is unknown",
            duration_ms=_elapsed(started),
            still_running=not settled,
        )
    lock.release()
    if pending.cancelled():
        return DomainCallOutcome(
            status="unknown",
            error_code="tool_cancelled",
            public_message=f"{tool.name} was cancelled; its outcome is unknown",
            duration_ms=_elapsed(started),
        )
    raised = pending.exception()
    if raised is not None:
        return _failed(raised, started)
    try:
        value = _check_result(pending.result())
    except (TypeError, ValueError) as problem:
        return DomainCallOutcome(
            status="failed",
            error_code="tool_result_invalid",
            error_type=type(problem).__name__,
            # 不回显内容：结果里可能有敏感字段
            public_message=f"{tool.name} returned a result that is not a JSON object within limits",
            duration_ms=_elapsed(started),
        )
    if inspect.isawaitable(value):  # pragma: no cover - _check_result already refused non-mappings
        return _failed(TypeError("coroutine returned as a result"), started)
    return DomainCallOutcome(status="succeeded", value=value, duration_ms=_elapsed(started))


async def _abandon(pending: asyncio.Future[Any], lock: asyncio.Lock, *, in_thread: bool) -> bool:
    """超时或被取消。协程：取消它，给一点宽限，没结束就交给后台放锁。线程：停不下来（取消
    那个 future 只是假装结束），直接交给后台等线程跑完再放锁。返回"已经真正结束"。"""

    if in_thread:
        _release_when_done(pending, lock)
        return False
    pending.cancel()
    try:
        await asyncio.wait((pending,), timeout=CANCEL_GRACE_SECONDS)
    except asyncio.CancelledError:
        # 自己又被取消了：不等了，交给后台
        _release_when_done(pending, lock)
        raise
    if pending.done():
        if not pending.cancelled():
            pending.exception()  # 取回，不让事件循环记"未取回的异常"
        lock.release()
        return True
    _release_when_done(pending, lock)
    return False


def _failed(error: BaseException, started: float) -> DomainCallOutcome:
    return DomainCallOutcome(
        status="failed",
        error_code="tool_failed",
        error_type=type(error).__name__,
        public_message=f"the tool failed ({type(error).__name__})",
        duration_ms=_elapsed(started),
    )


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


__all__ = ("CANCEL_GRACE_SECONDS", "MAX_RESULT_BYTES", "DomainCallOutcome", "run_domain_tool")
