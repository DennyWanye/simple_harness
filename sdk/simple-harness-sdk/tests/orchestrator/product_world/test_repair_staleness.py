# SPDX-License-Identifier: Apache-2.0
"""修复之后，知识与摘要跟着失效（HTN 补齐 F1：阶段 C、C3 欠的三条用例）。

一个剧本：两步任务（第 1 步写出结论、审阅员确认入库、摘要核对属实；第 2 步经工具读到这条知识，
在结果里带"编号@版本"引用）→ 最终审查打回 → 规划器把根目标换成新做法 → 新做法的两步重做。

- 第 2 步原来的带版本引用被接受，它的审查包"相关条目"带同一版本（阶段 C 用例 6、8 的端到端部分）；
- 换做法后旧知识读时"已过时"、从黑板目录消失，知识行本身不变（阶段 C 用例 7）；
- 旧摘要不再列出，新步骤通过后新摘要出现（阶段 C3 用例 9 的修复路径）。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.context import knowledge_tools
from agent_orchestrator.memory.knowledge_standing import CURRENT, knowledge_standing
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import DEFAULT_TOOLS, product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    planner_reply,
    review_input,
    review_reply,
    worker_reply,
)

KNOWLEDGE_TOOLS = ("knowledge_list", "knowledge_read")


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _two_steps(context: dict[str, Any]) -> dict[str, Any]:
    request = context["request"]
    operator = next(item for item in request["operators"]
                    if str(item["task_type_ref"]["id"]).endswith("prepare-delivery"))
    first, second = [item["id"] for item in request["criterion_evidence"]]
    identity = request["new_method_identity"]
    step = {"task_type_ref": operator["task_type_ref"], "form": "primitive", "arguments": {},
            "required_capabilities": list(operator["required_capabilities"]), "obligation_relation": "refines_parent"}
    return {
        "schema_version": 1, "method_id": identity["method_id"], "method_version": identity["method_version"],
        "goal_type_ref": request["goal_type_ref"],
        "parameter_schema_ref": request["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [],
        "steps": [{"local_id": "a", **step}, {"local_id": "b", **step}],
        "ordering": [{"before": "a", "after": "b"}],
        "required_capabilities": [], "expected_effects": [],
        "composition": {"criterion_links": [
            {"parent_criterion_id": first, "child_step": "a", "child_criterion_id": first, "evidence_requirement": "a 写第一份"},
            {"parent_criterion_id": second, "child_step": "b", "child_criterion_id": second, "evidence_requirement": "b 写第二份"}],
            "outputs": {}, "finalizer_step": "b", "independent_review_required": True},
        "basis_refs": [],
    }


def _find(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        value = list(value.values())
    if isinstance(value, list):
        for item in value:
            found = _find(item, key)
            if found is not None:
                return found
    return None


def _tool_results(request: Any) -> list[str]:
    return [str(message.content) for message in request.messages if "tool" in str(message.role).lower()]


def scenario(state: dict[str, Any]):
    def planner(request: Any) -> Any:
        package = package_of(request)
        contexts = package.get("method_proposal_contexts") or []
        if not package.get("repair_requests"):
            if contexts and not (package.get("method_selection") or [{}])[0].get("applicable"):
                method = _two_steps(contexts[0])
                state["methods"].append(method["method_id"])
                return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                                {"method_proposal": {"method": method, "rationale": "两步各写一份。"}}, "先 a 后 b。")
            return planner_reply(request)
        goal = next(item for item in package["views"]["goals"]
                    if item["form"] == "compound" and item.get("adopted_method"))
        current = goal["adopted_method"]["method_ref"]
        fresh = [item["method_ref"] for item in package["views"]["methods"]
                 if item["method_ref"] != current and (item.get("review") or {}).get("outcome") == "PASSED"
                 and item["method_ref"]["id"] in state["methods"]]
        if not fresh:
            [context] = [item for item in contexts if item["subject_key"] == goal["subject_key"]]
            method = _two_steps(context)
            state["methods"].append(method["method_id"])
            return decision(goal["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": method, "rationale": "重做一遍。"}}, "最终审查打回，换一个做法。")
        instance = next(item for item in package["visible_refs"] if item["kind"] == "method_instance"
                        and item["id"] == goal["adopted_method"]["method_instance_id"])
        return decision(goal["subject_key"], "REPAIR", {
            "repair_kind": "REPLACE_METHOD", "rejected_method_instance": instance,
            "replacement_method_ref": dict(fresh[-1]), "bindings": goal["params"]}, "换上新做法。")

    def worker(request: Any) -> Any:
        package = package_of(request)
        contract = package.get("task_contract", {})
        if list(contract.get("outputs") or []) != ["notes/b.md"]:
            return worker_reply(request)
        results = _tool_results(request)
        if not results:
            return ("knowledge_list", {})
        if len(results) == 1:
            items = _find(json.loads(results[0]), "items") or []
            state["cited"] = [item["ref"] for item in items if item.get("layer") == "verified"]
            return ("workspace_write_file", {"path": "notes/b.md", "content": "# 第二份\n\n- 接着第一份\n"})
        envelope = {
            "task_id": contract.get("task_id", ""), "attempt_id": package.get("attempt", {}).get("attempt_id", ""),
            "outcome": "candidate", "summary": "写好了 notes/b.md，接着第一份",
            "claims": [{"content": "notes/b.md 已写出", "confidence": 0.8, "evidence": ["notes/b.md"]}],
            "evidence": ["notes/b.md"], "artifacts": ["notes/b.md"],
            "outputs": {port["port"]: "notes/b.md" for port in (package.get("declared_output_ports") or {}).get(
                "ports", ()) if port.get("required", True)},
            "proposed_tasks": [], "used_knowledge": list(state.get("cited") or []), "risks": [],
            "cost": {"tool_calls": 2}}
        return "<result_envelope>" + json.dumps(envelope, ensure_ascii=False) + "</result_envelope>"

    def reviewer(request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        package = data.get("package") or {}
        if package.get("purpose") == "MISSION_FINAL" and not state.get("rework"):
            state["rework"] = True
            return review_reply(data, verdict="REWORK", grade="FAIL")
        if package.get("purpose") != "TASK_CONTENT":
            return review_reply(data)
        body = json.loads(review_reply(data, summary=lambda row: {"faithful": True, "reason": "与结果一致"}))
        listed = package.get("claims_to_confirm") or []
        artifacts = [item["label"] for item in data.get("evidence", ()) if item["ref"]["kind"] == "artifact"]
        body["claims"] = [{"claim_id": listed[0]["claim_id"], "confirmed": True, "evidence_ids": artifacts[:1],
                           "reason": "文件确实写出。"}] if listed and artifacts else []
        return json.dumps(body, ensure_ascii=False)

    return planner, worker, reviewer


def test_knowledge_and_summaries_follow_a_repair(tmp_path):
    """**改坏检验**：知识是否当前不看来源验收 → 旧知识仍"当前"；引用核对不比版本 → 见用例里
    的版本断言；摘要不看验收是否仍站着 → 旧摘要仍列出。"""
    state: dict[str, Any] = {"methods": []}
    planner, worker, reviewer = scenario(state)

    async def case():
        provider = LayeredScriptedProvider(planner=planner, worker=worker, reviewer=reviewer)
        async with product_world(tmp_path / "root", provider, allowed_tools=DEFAULT_TOOLS + KNOWLEDGE_TOOLS) as world:
            mission_id = world.create({"goal": "写两份笔记", "idempotency_key": "repair-staleness",
                                       "success_criteria": ["file:notes/a.md", "file:notes/b.md"]})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=40)
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            assert state.get("rework") and len(state["methods"]) == 2
            store, htn = world.store, HtnStore(world.store)

            # 第 1 次做法里第 1 步的知识：行本身不变，读时"已过时"，不在目录里
            records = sorted(store.list_knowledge(mission_id), key=lambda item: item.created_at)
            assert len(records) >= 2
            old = records[0]
            assert old.status == "VERIFIED" and old.version == 1 and old.superseded_by is None
            # 过时的原因是来源那一步已不在现行计划里撑着（不是文件被覆盖碰巧带出来的）
            standing = knowledge_standing(store, old)
            assert standing.startswith("STALE:") and not standing.startswith("STALE:artifact_replaced"), standing
            listed = knowledge_tools.read_knowledge_tool(store, mission_id, "knowledge_list", {"limit": 5})
            ids = [item["id"] for item in listed["items"]]
            assert old.id not in ids
            assert any(knowledge_standing(store, record) == CURRENT and record.id in ids for record in records[1:])

            # 第 1 次做法里第 2 步：带"编号@版本"引用被接受，审查包相关条目带同一版本
            cited = f"{old.id}@{old.version}"
            accepted_b = [store.get_result(task.accepted_result_id) for task in store.list_tasks(mission_id)
                          if task.accepted_result_id and "notes/b.md" in (task.outputs or ())]
            first_b = min(accepted_b, key=lambda item: item.received_at)
            assert cited in first_b.envelope.used_knowledge
            packages = [p for p in htn.list_review_packages(mission_id)
                        if p.candidate_refs and str(p.candidate_refs[0].id) == first_b.envelope.id]
            related = [row for p in packages for row in p.related_entries if row["kind"] == "used_knowledge"]
            assert related and {(row["id"], row["version"]) for row in related} == {(old.id, old.version)}

            # 摘要：旧做法两步的摘要不再列出，新做法两步通过后的摘要列出
            rows = knowledge_tools.step_summaries(store, mission_id)
            current_tasks = {row["source_task"] for row in rows}
            stale_tasks = {task.id for task in store.list_tasks(mission_id) if task.accepted_result_id} - current_tasks
            assert len(rows) == 2 and len(stale_tasks) == 2

    asyncio.run(case())
