# SPDX-License-Identifier: Apache-2.0
"""写入目标与写入冲突（HTN 补齐阶段 D，补全方案 2.3）。

做法把 ``file:X`` 要求链接到哪一步，那一步就以 X 为写入目标。两个没有先后的步骤写同一个文件，
编译期由资源冲突检查退回规划器；加上先后就可以提交。

**改坏检验**：不从 ``file:`` 链接生成写入目标 → 第一份做法照样提交 → 变红。
"""
from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    one_step_method,
    planner_reply,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def two_writers(context: dict[str, Any], *, ordered: bool) -> dict[str, Any]:
    """Two steps that both answer for the goal's one ``file:`` requirement."""
    method = one_step_method(context)
    [step] = method["steps"]
    second = copy.deepcopy(step)
    second["local_id"] = "polish"
    method["steps"] = [step, second]
    [link] = method["composition"]["criterion_links"]
    other = dict(link, child_step="polish", evidence_requirement="polish 这一步也写这个文件")
    method["composition"]["criterion_links"] = [link, other]
    method["composition"]["finalizer_step"] = "polish"
    method["ordering"] = [{"before": "write", "after": "polish"}] if ordered else []
    return method


@pytest.mark.parametrize("ordered", [False, True])
def test_parallel_steps_declaring_same_file_refused(tmp_path, ordered):
    """没有先后 → 采用这份做法时被退回，明细点名资源冲突与文件；有先后 → 计划提交。"""
    proposed: list[str] = []

    def planner(request: Any) -> Any:
        package = package_of(request)
        selection = (package.get("method_selection") or [{}])[0]
        usable = [item for item in selection.get("applicable") or () if item["method_id"] in proposed]
        if usable:
            goal = next(item for item in package["views"]["goals"] if item["open"])
            return decision(goal["subject_key"], "REFINE", {
                "method_ref": {"kind": "method", "id": usable[0]["method_id"],
                               "semantic_revision": usable[0]["method_version"],
                               "content_hash": usable[0]["method_content_hash"]},
                "bindings": dict(selection.get("bindings") or goal["params"])}, "采用刚通过审阅的做法。")
        contexts = package.get("method_proposal_contexts") or []
        if not contexts or proposed:
            return planner_reply(request)
        method = two_writers(contexts[0], ordered=ordered)
        proposed.append(method["method_id"])
        return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                        {"method_proposal": {"method": method, "rationale": "两步写同一个文件。"}}, "提一个两步做法。")

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner)) as world:
            mission_id = world.create({"goal": "写一份 report.md", "success_criteria": ["file:report.md"],
                                       "idempotency_key": f"write-targets-{ordered}"})["mission_id"]
            refusals: list[Any] = []
            adopted = False
            for _ in range(12):
                await world.drain(timeout=20)
                events = list(world.store.list_events(mission_id))
                refusals = [e.payload for e in events if e.type == "PlanningRejected"]
                adopted = world.loop._dispatch_for(mission_id).network(mission_id).plan_revision > 1 or any(
                    e.type == "AttemptCreated" for e in events)
                if refusals or adopted:
                    break
            if ordered:
                assert adopted and not refusals, refusals[:1]
            else:
                assert refusals and not adopted
                text = str(refusals[0])
                assert "resource" in text.lower() and "report.md" in text, text[:800]

    asyncio.run(case())
