# SPDX-License-Identifier: Apache-2.0
"""h1h / h1i 门禁用例的种子：产品同形部署上建好一个刚开始规划的任务（HTN 补齐阶段 A′）。

任务经产品那一份部署组装建出（建任务时初始化根、绑定执行图、走保证通道、原生执行池），
完成映射由部署职责确认（与产品自动模式同一条路），然后像主循环开工那样 ``begin_planning``。
规划轮次由用例自己经 ``_create_planner_intent`` / ``_collect_plan_decision`` 推进；规划授权
不自动签（``auto=False``），由用例按需签发。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, NamedTuple

from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.fixtures import RoleScriptedProvider
from agent_orchestrator.testing.product_world import USER_GOAL_NAMES, ProductWorld, product_world
from agent_orchestrator.testing.scripted_replies import decision, one_step_method

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


__all__ = ("CONFIG", "CRITERIA", "GOAL", "Seed", "plan_reply", "root_duty", "root_task", "seeded", "start")
