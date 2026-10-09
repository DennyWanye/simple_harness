# SPDX-License-Identifier: Apache-2.0
"""保证通道审阅族的产品同形种子（HTN 补齐阶段 A′，取代 ``scripts/assurance_seams/_assured_fixture``）。

任务经产品那一份部署组装建出（执行图建任务时绑定、保证通道、原生执行池），自动模式下部署职责
在两轮之间代签完成映射与规划授权。替身只有外界会发生的事：

* 审阅员（与执行者同一个模型的独立会话）对某一类审查（``package.purpose``）按脚本给结论：
  通过 / 打回 / 判断不了 / 不认可；
* 某一类审查的前几次模型调用出错（服务端报错、回合失败），或者停在半路（一次很慢的调用）；
* 规划器被问到修复时，默认把那次调用扣住不答——用例只看系统把什么事实交到了它手里。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from agent_orchestrator.testing.fixtures import package_of, role_of
from agent_orchestrator.testing.product_world import ProductWorld, product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    planner_reply,
    review_input,
    review_reply,
)

GOAL = "写一份 NOTES.md，列出三条要点。"
CRITERIA = ("file:NOTES.md",)
TERMINAL = {"COMPLETED", "FAILED", "STOPPED", "CANCELLED"}

#: 判断不了 / 打回 / 不认可 的脚本化回复（每条准则的结论随总结论）；"EMPTY" 是一段空的最终回答
#: （比如模型只给了思考没给正文）；"PROSE" 是一段不是 JSON 的话（审阅员没按格式回答）。
GRADES = {"ACCEPT": "PASS", "INCONCLUSIVE": "UNKNOWN", "REWORK": "FAIL", "REJECTED": "FAIL"}


def open_repairs(package: dict[str, Any]) -> list[dict[str, Any]]:
    """修复请求（"目标未细化"不算：那是普通规划）。"""

    return [entry for entry in package.get("repair_requests") or ()
            if ((entry.get("request") or {}).get("context") or {}).get("event_type") != "GoalUnrefined"]


class ReviewScript(LayeredScriptedProvider):
    """``verdicts[purpose]`` 依次给出这一类审查每次调用的总结论（用完以后都通过）；
    ``failures[purpose]`` 是这一类审查前几次调用要抛出的异常（外界的服务端错误）；
    ``hold`` 里的那一类审查第一次调用停在半路，直到 ``go`` 置位；``worker_failures`` 是执行者前几次
    调用要抛出的异常。"""

    def __init__(self, *, verdicts: dict[str, Iterable[str]] | None = None,
                 failures: dict[str, list[BaseException]] | None = None, hold: str | None = None,
                 repair: Callable[[Any], Any] | None = None,
                 worker_failures: list[BaseException] | None = None) -> None:
        self.worker_failures = list(worker_failures or ())
        self.verdicts = {purpose: list(items) for purpose, items in (verdicts or {}).items()}
        self.failures = {purpose: list(items) for purpose, items in (failures or {}).items()}
        self.hold_purpose = hold
        self.purposes: list[str] = []
        self.review_calls: dict[str, int] = {}
        self.repair = repair
        self.repair_packages: list[dict[str, Any]] = []
        self.repair_asked = asyncio.Event()
        self.go = asyncio.Event()
        self.review_entered = asyncio.Event()
        self._forever = asyncio.Event()

        def reviewer(request: Any) -> Any:
            data = review_input(request)
            if data is None:
                return None
            purpose = str((data.get("package") or {}).get("purpose"))
            self.purposes.append(purpose)
            queue = self.verdicts.get(purpose) or []
            verdict = queue.pop(0) if queue else "ACCEPT"
            if verdict == "EMPTY":
                return ""
            if verdict == "PROSE":
                return "我看过了，这份内容可以。"
            if verdict == "LOOP":
                # 审阅员只查不答，直到撞上这一回合的模型调用上限（react_max_turns_exceeded）：
                # 它自己的事，不是被打断（库存题第一局最终审阅的真实失败方式）
                return ("knowledge_list", {})
            return review_reply(data, verdict=verdict, grade=GRADES[verdict],
                                reason=f"脚本化审阅：{verdict}。")

        def planner(request: Any) -> Any:
            package = package_of(request)
            if open_repairs(package) and self.repair is not None:
                return self.repair(request)
            return planner_reply(request)

        super().__init__(planner=planner, reviewer=reviewer)

    def close(self) -> None:
        self.go.set()
        self._forever.set()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        role = role_of(request)
        data = review_input(request) if role == "unknown" else None
        if role == "planner" and self.repair is None and open_repairs(package_of(request)):
            self.repair_packages.append(package_of(request))
            self.repair_asked.set()
            await self._forever.wait()
        if role == "worker" and self.worker_failures:
            raise self.worker_failures.pop(0)
        if data is not None:
            purpose = str((data.get("package") or {}).get("purpose"))
            self.review_calls[purpose] = self.review_calls.get(purpose, 0) + 1
            pending = self.failures.get(purpose) or []
            if pending:
                raise pending.pop(0)
            if purpose == self.hold_purpose and self.review_calls[purpose] == 1:
                self.review_entered.set()
                await self.go.wait()
        return await super().invoke(request, cancel=cancel)


class Reviewed:
    def __init__(self, world: ProductWorld, mission_id: str) -> None:
        self.world, self.mission_id = world, mission_id

    @property
    def store(self) -> Any:
        return self.world.store

    def mission(self) -> Any:
        return self.store.get_mission(self.mission_id)

    def status(self) -> str:
        return str(self.mission().status.value)

    def events(self, *types: str) -> list[Any]:
        return [e for e in self.store.iter_events(self.mission_id) if not types or e.type in types]

    def pending_approvals(self) -> list[dict[str, Any]]:
        return [a for a in self.world.control.approvals(self.mission_id) if a.get("state") == "PENDING"]

    async def run_until(self, done: Callable[[], bool], *, timeout: float = 30.0) -> None:
        """主循环（连同部署每轮职责）一直跑到 ``done()`` 成立；有调用被扣住时用它。"""

        async def drive() -> None:
            while True:
                await self.world.loop.run()
                await self.world.deployment.between_cycles(auto=self.world.auto)
                await asyncio.sleep(0.01)

        task = asyncio.create_task(drive())
        try:
            async with asyncio.timeout(timeout):
                while not done():
                    if task.done():
                        task.result()
                    await asyncio.sleep(0.01)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def settle(self, *, timeout: float = 60.0) -> Any:
        await self.run_until(lambda: self.status() in TERMINAL, timeout=timeout)
        return self.mission()


@asynccontextmanager
async def reviewed_mission(tmp_path: Path, provider: ReviewScript, *, criteria: tuple[str, ...] = CRITERIA,
                           goal: str = GOAL, key: str = "review-1", reopen: str | None = None,
                           sources: tuple[tuple[str, str], ...] = (), **config: Any) -> AsyncIterator[Reviewed]:
    """``sources`` 是用户建任务时附上的资料（路径、正文），走产品"建任务并附资料"那一条写入口。"""
    try:
        async with product_world(tmp_path / "root", provider, **config) as world:
            if reopen is not None:
                yield Reviewed(world, reopen)
                return
            request = {"goal": goal, "success_criteria": list(criteria), "idempotency_key": key,
                       "budget": {"max_tokens": 8_000_000, "max_attempts": 12}}
            if sources:
                created = world.deployment.create_mission_with_sources(world.loop, world.control, {
                    "mission": request,
                    "sources": [{"path": path, "content": content, "kind": "note"} for path, content in sources]})
            else:
                created = world.create(request)
            yield Reviewed(world, created["mission_id"])
    finally:
        provider.close()


def quick_waits(monkeypatch: Any) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


__all__ = ("CRITERIA", "GOAL", "ReviewScript", "Reviewed", "open_repairs", "quick_waits", "reviewed_mission")
