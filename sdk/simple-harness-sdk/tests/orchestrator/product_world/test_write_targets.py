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


def two_writers_then_reader(context: dict[str, Any]) -> dict[str, Any]:
    """a、b 两步没有先后、都不负责 ``file:`` 要求；c 接着 a 的产出往下做并负责全部要求。"""
    request = context["request"]
    method = one_step_method(context)
    [step] = method["steps"]
    relay = next(item for item in request["operators"]
                 if str(item["task_type_ref"]["id"]).endswith("continue-delivery"))
    first, second = dict(copy.deepcopy(step), local_id="a"), dict(copy.deepcopy(step), local_id="b")
    reader = {"local_id": "c", "task_type_ref": relay["task_type_ref"], "form": "primitive",
              "arguments": {"delivery": {"op": "output", "step": "a", "port": "delivery"}},
              "required_capabilities": list(relay["required_capabilities"]),
              "obligation_relation": "refines_parent"}
    method["steps"] = [first, second, reader]
    method["ordering"] = [{"before": "b", "after": "c"}]
    for link in method["composition"]["criterion_links"]:
        link.update(child_step="c", evidence_requirement="c 这一步写出要求的文件")
    method["composition"]["finalizer_step"] = "c"
    return method


def test_runtime_same_path_becomes_write_conflict(tmp_path):
    """两个没有先后的步骤各写了一份 notes.md（内容不同）并都通过 → 接着 a 往下做的第三步不开工，
    规划器收到一条写入冲突修复请求，点名路径与这两步。

    **改坏检验**：``write_conflicts`` 恒返回空 → 第三步照常开工、没有修复请求 → 变红。"""
    from agent_orchestrator.testing.scripted_replies import worker_reply

    state: dict[str, Any] = {"proposed": False, "asked": []}

    def planner(request: Any) -> Any:
        package = package_of(request)
        clashes = [entry["request"] for entry in package.get("repair_requests") or ()
                   if (entry.get("request") or {}).get("trigger_source") == "WRITE_CONFLICT"]
        if clashes:
            state["asked"].extend(clashes)
            goal = package["views"]["goals"][0]
            return decision(goal["subject_key"], "NO_CHANGE", {"reason": "测试到此为止"}, "不改计划。")
        contexts = package.get("method_proposal_contexts") or []
        if contexts and not state["proposed"]:
            state["proposed"] = True
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": two_writers_then_reader(contexts[0]), "rationale": "两步各写草稿，第三步收尾。"}}, "三步做法。")
        return planner_reply(request)

    def worker(request: Any) -> Any:
        package = package_of(request)
        contract = package.get("task_contract", {})
        if contract.get("outputs"):
            return worker_reply(request)
        if not any("tool" in str(message.role).lower() for message in request.messages):
            return ("workspace_write_file", {"path": "notes.md", "content": f"# 草稿 {contract.get('task_id')}\n"})
        ports = [item["port"] for item in (package.get("declared_output_ports") or {}).get("ports", ())
                 if item.get("required", True)]
        import json
        return "<result_envelope>" + json.dumps({
            "task_id": contract.get("task_id", ""), "attempt_id": package.get("attempt", {}).get("attempt_id", ""),
            "outcome": "candidate", "summary": "写了草稿",
            "claims": [{"content": "notes.md 已写出", "confidence": 0.8, "evidence": ["notes.md"]}],
            "evidence": ["notes.md"], "artifacts": ["notes.md"], "outputs": {port: "notes.md" for port in ports[:1]},
            "proposed_tasks": [], "used_knowledge": [], "risks": [], "cost": {"tool_calls": 1},
        }, ensure_ascii=False) + "</result_envelope>"

    async def case():
        provider = LayeredScriptedProvider(planner=planner, worker=worker)
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写草稿再写 report.md", "success_criteria": ["file:report.md"],
                                       "idempotency_key": "write-conflict-runtime"})["mission_id"]
            for _ in range(14):
                await world.drain(timeout=20)
                if state["asked"]:
                    break
            events = list(world.store.list_events(mission_id))
            assert state["asked"], [e.payload for e in events if e.type == "PlanningRejected"][-1:]
            requests = [e.payload for e in events if e.type == "PlanningRepairRequested"
                        and e.payload["request"]["trigger_source"] == "WRITE_CONFLICT"]
            assert len(requests) == 1
            detail = requests[0]["request"]["context"]
            writers = {task.id for task in world.store.list_tasks(mission_id) if "notes.md" in {
                world.store.get_artifact(item).path for item in task.accepted_artifacts}}
            assert detail["path"] == "notes.md" and set(detail["steps"]) == writers and len(writers) == 2
            reader = next(task for task in world.store.list_tasks(mission_id)
                          if task.id not in writers and not task.id.startswith("user-root-"))
            assert world.store.list_attempts(reader.id) == []  # 第三步没有开工

    asyncio.run(case())
