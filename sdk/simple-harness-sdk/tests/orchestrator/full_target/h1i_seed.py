# SPDX-License-Identifier: Apache-2.0
"""h1h / h1i 门禁用例的种子：产品同形部署上建好一个刚开始规划的任务（HTN 补齐阶段 A′）。

任务经产品那一份部署组装建出（建任务时初始化根、绑定执行图、走保证通道、原生执行池），
完成映射由部署职责确认（与产品自动模式同一条路），然后像主循环开工那样 ``begin_planning``。
规划轮次由用例自己经 ``_create_planner_intent`` / ``_collect_plan_decision`` 推进；规划授权
不自动签（``auto=False``），由用例按需签发。

要"已提交第一版计划"的用例用 :func:`committed`：主循环真跑脚本化回合（提做法 → 独立审阅 →
采用做法提交），执行者的调用被扣住，停在叶子刚派发出去的那一刻；提交那一轮的意图与原始回复
由 :func:`committing_round` 从库里读回（原始回复读自规划决定记下的原文件）。

要"做法已过审、采用那一轮由用例自己答"的用例用 :func:`reviewed`：主循环开出采用轮并派发，
规划器那次调用被扣住，用例经收集器递交规划器的回复。:func:`resume` 放开被扣住的调用、改扣别的
角色；:func:`run_until` 跑主循环（连同部署每轮职责）直到条件成立。:func:`refuse_tampered_first`
是裁决①a 的唯一包装器：先向计划提交入口递一份篡改过的命令、确认按名拒绝且一字未写，再放行真命令。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, NamedTuple

from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from agent_orchestrator.testing.fixtures import RoleScriptedProvider, package_of
from agent_orchestrator.testing.product_world import USER_GOAL_NAMES, ProductWorld, product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    one_step_method,
)

GOAL = "写一份 NOTES.md，列出三条要点。"
CRITERIA = ("file:NOTES.md",)
CONFIG = {
    "max_concurrency": 1,
    "max_concurrent_model_calls": 1,
    "max_planning_attempts": 3,
    "test_timeout_seconds": 30,
}


class Seed(NamedTuple):
    loop: Any
    mission: Any
    world: Any  # the Mission's planning world
    binding: Any  # the root's task semantics
    dispatch: Any
    product: ProductWorld


def root_task(mission_id: str) -> str:
    return USER_GOAL_NAMES.task_prefix + mission_id


def root_duty(mission_id: str) -> str:
    return USER_GOAL_NAMES.duty_prefix + mission_id


def start(product: ProductWorld, *, key: str, goal: str = GOAL,
          criteria: tuple[str, ...] = CRITERIA) -> Seed:
    loop = product.loop
    created = product.create({"goal": goal, "idempotency_key": key, "success_criteria": list(criteria)})
    mission = loop.store.get_mission(created["mission_id"])
    # The user's confirmation of the content-only completion mapping (the product's auto mode).
    product.deployment.duties.auto_confirm_content_completion(auto=True)
    loop.commit.begin_planning(mission.id)
    dispatch = loop._dispatch_for(mission.id)
    binding = HtnStore(loop.store).latest_task_semantics(root_task(mission.id))
    return Seed(loop, loop.store.get_mission(mission.id), dispatch.planning, binding, dispatch, product)


@asynccontextmanager
async def seeded(tmp_path: Path, *, key: str, provider: Any = None, goal: str = GOAL,
                 criteria: tuple[str, ...] = CRITERIA, **config: Any) -> AsyncIterator[Seed]:
    provider = provider if provider is not None else RoleScriptedProvider({"planner": []})
    async with product_world(tmp_path / "root", provider, auto=False, **{**CONFIG, **config}) as product:
        yield start(product, key=key, goal=goal, criteria=criteria)


def plan_reply(package: dict[str, Any]) -> str:
    """The planner's first answer: adopt an applicable method, else propose a one-step one."""

    selection = (package.get("method_selection") or [{}])[0]
    if selection.get("applicable"):
        chosen = selection["applicable"][0]
        goal = next(item for item in package["views"]["goals"] if item["open"])
        return decision(goal["subject_key"], "REFINE", {
            "method_ref": {"kind": "method", "id": chosen["method_id"],
                           "semantic_revision": chosen["method_version"],
                           "content_hash": chosen["method_content_hash"]},
            "bindings": dict(selection.get("bindings") or goal["params"]),
        }, "采用已通过独立审阅的做法。")
    context = package["method_proposal_contexts"][0]
    return decision(context["subject_key"], "PROPOSE_METHOD",
                    {"method_proposal": {"method": one_step_method(context), "rationale": "一步写出要求的文件。"}},
                    "库里没有适用的做法，提出一个一步完成的做法。")


def planner(request: Any) -> str:
    """:func:`plan_reply` as a scripted provider's planner."""

    return plan_reply(package_of(request))


def events(loop: Any, mission_id: str, event_type: str) -> list[Any]:
    return [event for event in loop.store.list_events(mission_id) if event.type == event_type]


async def run_until(product: ProductWorld, done: Callable[[], bool], *, timeout: float = 30.0) -> None:
    """Run the main loop (with the deployment's per-round duties) until ``done()``; then stop it
    the way the product's ``drain`` deadline does (the run is cancelled at its next await)."""

    async def drive() -> None:
        while True:
            await product.loop.run()
            await product.deployment.between_cycles(auto=product.auto)
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


@asynccontextmanager
async def committed(tmp_path: Path, *, key: str, planner: Callable[[Any], Any] = planner,
                    provider: LayeredScriptedProvider | None = None, goal: str = GOAL,
                    criteria: tuple[str, ...] = CRITERIA, auto: bool = True,
                    **config: Any) -> AsyncIterator[Seed]:
    """A Mission whose first plan revision the main loop committed for real (proposal, its
    independent review, adoption), stopped once its first leaf is dispatched: the executor's
    model call is held and never answers while the case runs (see :func:`resume`).

    ``provider`` replaces the default scripted provider (its worker is held all the same)."""

    provider = provider if provider is not None else LayeredScriptedProvider(planner=planner)
    provider.held.add("worker")
    try:
        async with product_world(tmp_path / "root", provider, **{**CONFIG, **config}) as product:
            created = product.create({"goal": goal, "idempotency_key": key, "success_criteria": list(criteria)})
            await run_until(product, provider.entered.is_set)
            product.auto = auto
            loop = product.loop
            mission = loop.store.get_mission(created["mission_id"])
            dispatch = loop._dispatch_for(mission.id)
            binding = HtnStore(loop.store).latest_task_semantics(root_task(mission.id))
            yield Seed(loop, mission, dispatch.planning, binding, dispatch, product)
    finally:
        provider.release.set()


class Reviewed(NamedTuple):
    seed: Seed
    intent: Any  # the adoption round: created and dispatched by the loop, its planner call held
    provider: LayeredScriptedProvider


@asynccontextmanager
async def reviewed(tmp_path: Path, *, key: str, planner: Callable[[Any], Any] = planner, goal: str = GOAL,
                   criteria: tuple[str, ...] = CRITERIA, **config: Any) -> AsyncIterator[Reviewed]:
    """A Mission whose planner proposed a method that passed its independent review, all on
    the real main loop; the loop then opened the next planning round (the adoption round)
    and dispatched it, and that planner call is held.  The case answers the round itself
    through ``_collect_plan_decision`` (the planner's reply, delivered by the collector)."""

    holder: dict[str, Any] = {}

    def first_then_hold(request: Any) -> Any:
        reply = planner(request)
        holder["provider"].held.add("planner")
        return reply

    provider = LayeredScriptedProvider(planner=first_then_hold)
    holder["provider"] = provider
    try:
        async with product_world(tmp_path / "root", provider, **{**CONFIG, **config}) as product:
            created = product.create({"goal": goal, "idempotency_key": key, "success_criteria": list(criteria)})
            await run_until(product, provider.entered.is_set)
            loop = product.loop
            mission = loop.store.get_mission(created["mission_id"])
            assert events(loop, mission.id, "PlanningMethodReviewed"), "the proposal was not reviewed"
            [intent] = [item for item in loop.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED")
                        if item.mission_id == mission.id and item.kind == "plan"
                        and ":planner:" in item.intent_id]
            dispatch = loop._dispatch_for(mission.id)
            binding = HtnStore(loop.store).latest_task_semantics(root_task(mission.id))
            yield Reviewed(Seed(loop, mission, dispatch.planning, binding, dispatch, product), intent, provider)
    finally:
        provider.release.set()


def refuse_tampered_first(monkeypatch: Any, tamper: Callable[[Any, Any, dict[str, Any]], tuple[Any, Any, dict[str, Any]]],
                          expect: str) -> list[str]:
    """Wrap the plan-commit entry (adjudication ①a): every delivery is first sent as the
    variant ``tamper(command, principal, kwargs)`` returns, which must be refused by the
    named code without writing anything; then the real delivery goes through unchanged.
    Returns the refusal messages seen."""

    from agent_orchestrator.orchestrator.commit_service import CommitService
    from agent_orchestrator.orchestrator.plan_commits import PlanCommitRejected

    original = CommitService.commit_planning_revision
    refusals: list[str] = []

    def guarded(self: Any, command: Any, principal: Any, **kwargs: Any) -> Any:
        before = self.store.connection.total_changes
        variant = tamper(command, principal, dict(kwargs))
        try:
            original(self, variant[0], variant[1], **variant[2])
        except PlanCommitRejected as refused:
            assert refused.reason == expect, (refused.reason, expect)
            refusals.append(str(refused))
        else:
            raise AssertionError(f"the tampered delivery was not refused ({expect})")
        assert self.store.connection.total_changes == before, "a refused delivery wrote"
        return original(self, command, principal, **kwargs)

    monkeypatch.setattr(CommitService, "commit_planning_revision", guarded)
    return refusals


def resume(product: ProductWorld, *, hold: tuple[str, ...] = ()) -> int:
    """Let the held model calls answer; from now on the roles in ``hold`` are held instead
    (a fresh release).  Returns how many calls had been asked so far, so a case can wait for
    the calls asked after it."""

    provider = product.provider
    released = provider.release
    provider.release = asyncio.Event()
    provider.held = set(hold)
    provider.entered.clear()
    released.set()
    return len(provider.asked)


def grant_for(loop: Any, intent_id: str) -> tuple[str, int]:
    """The planning grant the deployment signed for that request (the product's auto mode):
    ``(grant_id, revision)``."""

    row = loop.store.connection.execute(
        "SELECT grant_id, revision FROM planning_lane_grants WHERE issuer_command_id LIKE ? "
        "ORDER BY revision DESC LIMIT 1",
        ("%:" + intent_id,),
    ).fetchone()
    assert row is not None, intent_id
    return str(row["grant_id"]), int(row["revision"])


def committing_round(loop: Any, mission_id: str) -> tuple[Any, str]:
    """The planner intent whose decision committed the first plan revision, and its raw reply
    (read back from the artifact the collector journalled)."""

    decisions = PlanningDecisionStore(loop.store)
    for intent in sorted(loop.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED",
                                                 "SETTLED", "FAILED"), key=lambda item: item.created_at):
        if intent.mission_id != mission_id or intent.kind != "plan":
            continue
        row = decisions.get_planning_decision_by_attempt(intent.intent_id, 0)
        if row is not None and row["status"] == "COMMITTED":
            raw = loop.assembled.workspaces.artifact_store.read(row["raw_artifact_ref"])
            return intent, raw.decode("utf-8")
    raise AssertionError("no planning round committed a plan revision")


__all__ = ("CONFIG", "CRITERIA", "GOAL", "Seed", "committed", "committing_round", "events", "grant_for", "plan_reply",
           "planner", "refuse_tampered_first", "resume", "reviewed", "Reviewed", "root_duty", "root_task", "run_until", "seeded", "start")
