# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Lane-aware 有界并发调度器 — 子代理并发驱动的调度地基。

plan: plans/2026-06-21-subagent-concurrency-driver/ WI-0.2

偷师 OpenClaw 的 lane 队列（纯 asyncio，无外部依赖）：每种事务（kind）一个
``asyncio.Semaphore`` lane + 一个全局 cap。超 cap 自然**排队**（背压，不丢任务）。
进度通过 ``progress_sink`` 回调发出（queued → running → completed/failed）。

为什么纯 asyncio 够用
--------------------
deskpet 是单进程单事件循环。``asyncio.Semaphore`` 的 acquire 即排队等待，
天然背压；不需要线程池 / Redis / celery（feedback_no_sandbox_constraints：
单机桌宠不过度工程）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)


class SubagentScheduler:
    """全局 cap + 每 kind lane cap 的有界并发调度器。

    Args:
        global_concurrency: 全局同时运行的子代理上限（背压第一道闸）。
        lane_caps: ``{kind: cap}`` 每 kind lane 并发上限（第二道闸）。
        progress_sink: 可选回调，收 ``dict`` 进度事件（queued/running/
            completed/failed）。**永不阻断调度**——回调抛异常被吞。
    """

    def __init__(
        self,
        *,
        global_concurrency: int = 4,
        lane_caps: dict[str, int] | None = None,
        progress_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._global = asyncio.Semaphore(max(1, int(global_concurrency)))
        self._lane_caps: dict[str, int] = dict(lane_caps or {})
        self._lanes: dict[str, asyncio.Semaphore] = {}
        self._progress = progress_sink
        self._running = 0
        self._queued = 0

    def _lane(self, kind: str) -> asyncio.Semaphore:
        """惰性创建该 kind 的 lane semaphore（默认 cap=2）。"""
        if kind not in self._lanes:
            cap = max(1, int(self._lane_caps.get(kind, 2)))
            self._lanes[kind] = asyncio.Semaphore(cap)
        return self._lanes[kind]

    def _emit(self, payload: dict[str, Any]) -> None:
        if self._progress is None:
            return
        try:
            self._progress(payload)
        except Exception as exc:  # noqa: BLE001 — 进度永不阻断调度
            log.debug("subagent scheduler progress emit failed: %s", exc)

    async def run(
        self,
        *,
        kind: str,
        run_id: str,
        task_id: str,
        parent_sid: str,
        coro_factory: Callable[[], Awaitable[Any]],
    ) -> Any:
        """在双闸（全局 + lane）背压下跑一个子代理协程。

        Args:
            kind: 事务类型，决定 lane。
            run_id / task_id / parent_sid: 仅用于进度事件标识。
            coro_factory: 无参可调用，返回子代理协程（**延迟构造**——
                只有在两道闸都拿到后才真正起子代理，避免占位浪费）。
        """
        lane = self._lane(kind)
        self._queued += 1
        self._emit(
            {
                "run_id": run_id,
                "kind": kind,
                "task_id": task_id,
                "parent_sid": parent_sid,
                "status": "queued",
                "ts": time.time(),
            }
        )
        # 双闸背压：全局 cap → kind-lane cap。超 cap 在此 await 排队。
        async with self._global:
            async with lane:
                self._queued -= 1
                self._running += 1
                # F9: 日志锚点供真机 E2E grep
                log.info(
                    "subagent_scheduled kind=%s run_id=%s task_id=%s",
                    kind,
                    run_id,
                    task_id,
                )
                self._emit(
                    {
                        "run_id": run_id,
                        "kind": kind,
                        "task_id": task_id,
                        "parent_sid": parent_sid,
                        "status": "running",
                        "ts": time.time(),
                    }
                )
                t0 = time.time()
                try:
                    out = await coro_factory()
                    self._emit(
                        {
                            "run_id": run_id,
                            "kind": kind,
                            "task_id": task_id,
                            "parent_sid": parent_sid,
                            "status": "completed",
                            "duration_ms": int((time.time() - t0) * 1000),
                            "ts": time.time(),
                        }
                    )
                    return out
                except BaseException:
                    # BaseException 含 CancelledError —— 取消也要发 failed 进度
                    self._emit(
                        {
                            "run_id": run_id,
                            "kind": kind,
                            "task_id": task_id,
                            "parent_sid": parent_sid,
                            "status": "failed",
                            "duration_ms": int((time.time() - t0) * 1000),
                            "ts": time.time(),
                        }
                    )
                    raise
                finally:
                    self._running -= 1

    def snapshot(self) -> dict[str, int]:
        """当前调度状态快照（observability）。"""
        return {"running": self._running, "queued": self._queued}


__all__ = ["SubagentScheduler"]
