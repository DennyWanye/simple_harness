# SPDX-License-Identifier: Apache-2.0
"""做法前提被推翻，直接交规划器（HTN 一致性补改 H-1，原计划 §6.6 规则 3、§9.1）。

根目标的做法有两步：先写出 out.md，再整理出 NOTES.md（有先后），做法前提是"out.md 不在"。取证看到
不在（成立），采用这个做法；第 1 步写出 out.md、验收通过，系统重读观察，前提翻成假。第 2 步还没开工，
派发前的开工许可说前提为假——这时规划器应**立刻**收到一条"证据失效"请求，写明是哪一步、哪条前提
不成立，而不是等判停滞才以"没有可派发的工作"笼统问一次。怎么办由规划器定，这里只核请求来了、来得早、
内容如实。

**改坏检验**（H-01）：系统扫描不看前提许可 → 只剩停滞请求 → 变红。
"""
from __future__ import annotations

import asyncio
import contextlib
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
from test_desktop_preconditions import ask_file_present, file_present

REQUESTED = "PlanningRepairRequested"
NOT_YET = {"op": "not", "item": file_present("out.md")}


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def write_then_tidy_in_one_method(context: dict[str, Any]) -> dict[str, Any]:
    method = one_step_method(context)
    first, second = [item["id"] for item in context["request"]["criterion_evidence"]]
    tidy = dict(method["steps"][0], local_id="tidy")
    method["steps"].append(tidy)
    method["ordering"] = [{"before": "write", "after": "tidy"}]
    method["applicable_when"] = [NOT_YET]
    method["composition"]["criterion_links"] = [
        {"parent_criterion_id": first, "child_step": "write", "child_criterion_id": first,
         "evidence_requirement": "write 这一步写出 out.md"},
        {"parent_criterion_id": second, "child_step": "tidy", "child_criterion_id": second,
         "evidence_requirement": "tidy 这一步整理出 NOTES.md"}]
    method["composition"]["finalizer_step"] = "tidy"
    return method


def _requests(store: Any, mission_id: str) -> list[dict[str, Any]]:
    return [event.payload for event in store.list_events(mission_id) if event.type == REQUESTED]


def _detail(payload: dict[str, Any]) -> dict[str, Any]:
    return dict((payload.get("request") or {}).get("context") or {})


def test_an_overturned_precondition_reaches_the_planner_before_any_stall(tmp_path):
    state = {"asked": False, "proposed": False}

    def planner(request: Any) -> Any:
        package = package_of(request)
        contexts = package.get("method_proposal_contexts") or []
        goals = [item for item in package["views"]["goals"] if item["open"]]
        if not goals or package.get("repair_requests"):
            return planner_reply(request)
        if not state["asked"]:
            state["asked"] = True
            return ask_file_present(goals[0]["subject_key"], "out.md")
        if contexts and not state["proposed"]:
            state["proposed"] = True
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                            {"method_proposal": {"method": write_then_tidy_in_one_method(contexts[0]),
                                                 "rationale": "趁 out.md 还没有，先写再整理。"}}, "提做法。")
        for selection in package.get("method_selection") or ():
            if selection.get("applicable"):
                chosen = selection["applicable"][0]
                goal = next(item for item in goals if item["occurrence_id"] == selection["occurrence_id"])
                return decision(goal["subject_key"], "REFINE", {
                    "method_ref": {"kind": "method", "id": chosen["method_id"],
                                   "semantic_revision": chosen["method_version"],
                                   "content_hash": chosen["method_content_hash"]},
                    "bindings": dict(selection.get("bindings") or goal["params"])}, "采用通过审阅的做法。")
        return planner_reply(request)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner)) as world:
            store = world.store
            mission_id = world.create({"goal": "写 out.md 再整理", "idempotency_key": "overturned-1",
                                       "success_criteria": ["file:out.md", "file:NOTES.md"]})["mission_id"]
            stop = asyncio.Event()

            async def drive() -> None:
                while not stop.is_set():
                    await world.loop.run()
                    await world.deployment.between_cycles(auto=True)
                    await asyncio.sleep(0.05)

            runner = asyncio.create_task(drive())
            found: list[dict[str, Any]] = []
            try:
                for _ in range(1200):
                    found = [r for r in _requests(store, mission_id)
                             if _detail(r).get("reason") == "method_precondition_false"]
                    if found or runner.done() or str(store.get_mission(mission_id).status.value) in {
                            "COMPLETED", "FAILED", "CANCELLED"}:
                        break
                    await asyncio.sleep(0.05)
            finally:
                stop.set()
                runner.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runner
            requests = _requests(store, mission_id)
            assert found, [(r.get("source_key"), str(_detail(r))[:120]) for r in requests]
            [request] = found
            detail = _detail(request)
            assert detail["truth"] == "FALSE" and detail["explanation"]
            assert detail["conditions"] == [NOT_YET]
            assert not store.list_attempts(detail["task_id"])  # 第 2 步没开工
            # 早于任何"没有可派发的工作"：不是等停滞才问
            keys = [str(r.get("source_key")) for r in requests]
            first = keys.index(str(request["source_key"]))
            assert not any(key.startswith("stalled:") for key in keys[:first]), keys

    asyncio.run(case())
