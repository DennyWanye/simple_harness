"""Async execution bridge for synchronous tool handlers."""

from __future__ import annotations

import asyncio
import contextvars
from concurrent.futures import Executor
from typing import Any, Callable, Protocol, cast

from ..effects import NormalizedToolOutcome, PreparedToolCall
from ..store import RunFence


class _LateEffectFinalizer(Protocol):
    async def mark_uncertain(self, fence: RunFence, effect_id: str, reason: str) -> object: ...

    async def finalize_late(
        self,
        fence: RunFence,
        *,
        effect_id: str,
        args_hash: str,
        lease_epoch: int,
        outcome: NormalizedToolOutcome,
    ) -> object: ...


class SyncHandlerTimedOut(TimeoutError):
    def __init__(self, effect_id: str, timeout_seconds: float) -> None:
        super().__init__(f"sync tool effect {effect_id} exceeded {timeout_seconds:g}s")
        self.effect_id = effect_id
        self.timeout_seconds = timeout_seconds


def _normalize(
    normalizer: Callable[[Any], NormalizedToolOutcome] | object, raw: Any
) -> NormalizedToolOutcome:
    if callable(normalizer):
        outcome = normalizer(raw)
    else:
        outcome = cast(Any, normalizer).normalize(raw)
    if not isinstance(outcome, NormalizedToolOutcome):
        return NormalizedToolOutcome.malformed("outcome parser returned an unsupported value")
    return outcome


def _consume_task(task: asyncio.Task[object]) -> None:
    if task.cancelled():
        return
    try:
        task.result()
    except BaseException:
        # The durable effect remains uncertain and will be handled by recovery.
        return


async def execute_sync_handler(
    handler: Callable[[], Any],
    *,
    timeout_seconds: float,
    normalizer: Callable[[Any], NormalizedToolOutcome] | object,
    journal: _LateEffectFinalizer,
    fence: RunFence,
    effect_id: str,
    prepared: PreparedToolCall,
    executor: Executor | None = None,
) -> NormalizedToolOutcome:
    """Run a sync handler without cancelling its thread when the await times out.

    The immediate caller receives ``SyncHandlerTimedOut`` after the effect is
    marked uncertain. The still-observable worker future hands any late result
    back to the journal, which commits only under the original live fence and
    otherwise records ``late_orphan``.
    """

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    loop = asyncio.get_running_loop()
    context = contextvars.copy_context()
    future = loop.run_in_executor(executor, context.run, handler)
    try:
        raw = await asyncio.wait_for(asyncio.shield(future), timeout_seconds)
    except asyncio.TimeoutError as exc:
        await journal.mark_uncertain(fence, effect_id, "sync_handler_timeout")

        def finish_late(completed: asyncio.Future[Any]) -> None:
            try:
                raw_result = completed.result()
                outcome = _normalize(normalizer, raw_result)
            except BaseException as handler_error:
                outcome = NormalizedToolOutcome.failure(
                    "sync_handler_error", type(handler_error).__name__
                )

            def submit() -> None:
                task = asyncio.create_task(
                    journal.finalize_late(
                        fence,
                        effect_id=effect_id,
                        args_hash=prepared.args_hash,
                        lease_epoch=fence.lease_epoch,
                        outcome=outcome,
                    )
                )
                task.add_done_callback(_consume_task)

            if not loop.is_closed():
                loop.call_soon_threadsafe(submit)

        future.add_done_callback(finish_late)
        raise SyncHandlerTimedOut(effect_id, timeout_seconds) from exc
    except BaseException as exc:
        return NormalizedToolOutcome.failure("sync_handler_error", type(exc).__name__)
    return _normalize(normalizer, raw)


__all__ = ["SyncHandlerTimedOut", "execute_sync_handler"]
