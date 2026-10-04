# SPDX-License-Identifier: Apache-2.0
"""两个分支点名共用一个写文件的步骤（TaskGraph 补全第三批，产品同形用例）。

根做法 = 左右两个子目标；out.md 这条要求两边都承接（右边以它为依据），NOTES.md 归右边。左子目标
一步写出 out.md；右子目标的做法是"写 + 接着写"，"写"负责 out.md，规划器在采用时点名共用左边那一步
（``reuse``），"接着写"读它的产出写 NOTES.md。
共用步骤只做一次、有两个上级做法持有它，下游照常读到它的产出，任务完成。

另一条：规划器拿"一步负责 out.md 和 NOTES.md"的做法点名共用左边那一步——共用不会让已有步骤
多负责一条要求 → 这次决定被退回（``REUSE_NOT_ALLOWED``，说明里写清多出来的要求），之后照常完成。

**改坏检验**：TG3-04 预览不再比较"交给共用步骤的要求必须是它已负责的要求" → 第二条用例变红；
TG3-06 范围没变也不算沿用（在跑的步骤跨计划版本交的结果一律归档）→ 第一条用例变红。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, decision, one_step_method

LEFT, RIGHT = "写出 out.md", "据 out.md 写出 NOTES.md"


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _type_of(context: dict[str, Any]) -> str:
    return str((context["request"].get("goal_type_ref") or {}).get("id"))


def two_branches(context: dict[str, Any]) -> dict[str, Any]:
    """根做法：左右两个子目标，各承接一条要求。"""
    method = one_step_method(context)
    part = next(item for item in context["request"]["subgoal_types"] if item["task_type_ref"]["id"] == "sub-goal-1")
    first, second = [item["id"] for item in context["request"]["criterion_evidence"]]
    method["steps"] = [
        {"local_id": side, "task_type_ref": part["task_type_ref"], "form": "compound",
         "arguments": {"goal": {"op": "constant", "value": text}},
         "required_capabilities": [], "obligation_relation": "refines_parent"}
        for side, text in (("left", LEFT), ("right", RIGHT))]
    # 两个子目标之间不排先后：共用的那一步同时在两边，若"左先于右"，它既在左边出口之前又在右边入口之后，成环。
    method["ordering"] = []
    method["composition"]["criterion_links"] = [
        {"parent_criterion_id": first, "child_step": "left", "child_criterion_id": first,
         "evidence_requirement": "left 这个子目标写出 out.md"},
        {"parent_criterion_id": first, "child_step": "right", "child_criterion_id": first,
         "evidence_requirement": "right 这个子目标以 out.md 为依据"},
        {"parent_criterion_id": second, "child_step": "right", "child_criterion_id": second,
         "evidence_requirement": "right 这个子目标写出 NOTES.md"}]
    method["composition"]["finalizer_step"] = "right"
    return method


def write_then_continue(context: dict[str, Any]) -> dict[str, Any]:
    """右子目标的做法："写"负责 out.md（准备共用左边那一步，它负责的正是这一条），"接着写"读它的
    产出负责 NOTES.md。"""
    request = context["request"]
    method = one_step_method(context)
    follow = next(item for item in request["operators"]
                  if str(item["task_type_ref"]["id"]).endswith("continue-delivery"))
    first, second = [item["id"] for item in request["criterion_evidence"]]
    method["steps"].append({
        "local_id": "continue", "task_type_ref": follow["task_type_ref"], "form": "primitive",
        "arguments": {"delivery": {"op": "output", "step": "write", "port": "delivery"}},
        "required_capabilities": list(follow["required_capabilities"]), "obligation_relation": "refines_parent"})
    method["composition"]["criterion_links"] = [
        {"parent_criterion_id": first, "child_step": "write", "child_criterion_id": first,
         "evidence_requirement": "write 写出 out.md"},
        {"parent_criterion_id": second, "child_step": "continue", "child_criterion_id": second,
         "evidence_requirement": "continue 读 write 的产出写出 NOTES.md"}]
    method["composition"]["finalizer_step"] = "continue"
    return method


def _refine(goal: dict[str, Any], chosen: dict[str, Any], bindings: dict[str, Any],
            reuse: dict[str, str] | None = None) -> str:
    payload: dict[str, Any] = {
        "method_ref": {"kind": "method", "id": chosen["method_id"], "semantic_revision": chosen["method_version"],
                       "content_hash": chosen["method_content_hash"]},
        "bindings": bindings}
    if reuse:
        payload["reuse"] = reuse
    return decision(goal["subject_key"], "REFINE", payload, "采用通过审阅的做法。")


def _run(tmp_path: Any, *, overreach_first: bool):
    holder: dict[str, Any] = {}
    state: dict[str, Any] = {"root": False, "left": None, "right": None, "wide": None, "overreached": False,
                             "named": []}

    def planner(request: Any) -> Any:
        package = package_of(request)
        goals = {item["occurrence_id"]: item for item in package["views"]["goals"] if item["open"]}
        contexts = package.get("method_proposal_contexts") or []
        if not goals:
            return None
        roots = [c for c in contexts if _type_of(c) == "user-goal"]
        if roots and not state["root"]:
            state["root"] = True
            return decision(roots[0]["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": two_branches(roots[0]), "rationale": "左右两个子目标。"}}, "根目标拆成两个子目标。")
        selections = {item["occurrence_id"]: item for item in package.get("method_selection") or ()}
        by_text = {str(goal["params"].get("goal")): goal for goal in goals.values()}
        for occurrence, selection in selections.items():
            goal = goals.get(occurrence)
            if goal is None or str(goal["params"].get("goal")) == RIGHT:
                continue
            if selection.get("applicable"):
                applicable = selection["applicable"]
                chosen = next((item for item in applicable
                               if (item["method_id"], item["method_version"]) == state["left"]), applicable[0])
                return _refine(goal, chosen, dict(selection.get("bindings") or goal["params"]))
        sub = {str(c["subject_key"]): c for c in contexts if _type_of(c) == "sub-goal-1"}
        left = by_text.get(LEFT)
        if left is not None and state["left"] is None and str(left["subject_key"]) in sub:
            method = one_step_method(sub[str(left["subject_key"])])
            state["left"] = (method["method_id"], method["method_version"])
            return decision(left["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": method, "rationale": "一步写出 out.md。"}}, "左子目标一步完成。")
        right = by_text.get(RIGHT)
        if right is None:
            return None
        if overreach_first and state["wide"] is None and str(right["subject_key"]) in sub:
            method = one_step_method(sub[str(right["subject_key"])])
            state["wide"] = (method["method_id"], method["method_version"])
            return decision(right["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": method, "rationale": "一步写出 out.md 和 NOTES.md。"}}, "右子目标一步完成。")
        if ((state["overreached"] or not overreach_first) and state["right"] is None
                and str(right["subject_key"]) in sub):
            method = write_then_continue(sub[str(right["subject_key"])])
            state["right"] = (method["method_id"], method["method_version"])
            return decision(right["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": method, "rationale": "写这一步共用左边那一步，再接着写。"}}, "右子目标两步。")
        selection = selections.get(right["occurrence_id"]) or {}
        applicable = {(item["method_id"], item["method_version"]): item for item in selection.get("applicable") or ()}
        candidates = [row for row in package.get("sharing_candidates") or ()
                      if row["task_type"] == "prepare-delivery"]
        if not candidates:
            step = next((item for item in package["views"]["goals"]
                         if item["form"] == "primitive" and item.get("task_id")), None)
            if step is None:
                return None
            semantics = holder["world"].loop._dispatch_for(holder["mission_id"]).semantics().task_semantics_of(
                holder["mission_id"], step["task_id"])
            return decision(right["subject_key"], "WAIT", {"wait_for": [{
                "kind": "task", "id": step["task_id"], "semantic_revision": int(semantics.contract_revision),
                "content_hash": semantics.content_hash()}], "reason": "等左边那一步跑起来"}, "还没有可共用的步骤。")
        [shared] = candidates
        bindings = dict(selection.get("bindings") or right["params"])
        if overreach_first and not state["overreached"] and state["wide"] in applicable:
            state["overreached"] = True  # 这一步要负责两条要求，而被点名的那一步只负责 out.md
            return _refine(right, applicable[state["wide"]], bindings, {"write": shared["occurrence_id"]})
        if state["right"] in applicable:
            state["named"].append(shared["occurrence_id"])
            return _refine(right, applicable[state["right"]], bindings, {"write": shared["occurrence_id"]})
        return None

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner)) as world:
            mission_id = world.create({"goal": "写 out.md，再据它写 NOTES.md", "idempotency_key": "shared-1",
                                       "success_criteria": ["file:out.md", "file:NOTES.md"]})["mission_id"]
            holder.update(world=world, mission_id=mission_id)
            mission = await world.run_until_settled(mission_id, rounds=40)
            evaluated = [e.payload for e in world.store.list_events(mission_id)
                         if e.type == "PlanningDecisionEvaluated" and e.payload.get("status") == "REJECTED"]
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report, state,
                                                              evaluated[-3:])
            network = world.loop._dispatch_for(mission_id).network(mission_id)
            rows = world.control.snapshot(mission_id)["snapshot"]["budget_by_duty"]
            return network, evaluated, state, rows

    return asyncio.run(case())


def test_a_named_shared_step_is_done_once_and_feeds_the_other_branch(tmp_path):
    network, _, state, rows = _run(tmp_path, overreach_first=False)
    writes = [item.occurrence_id for item in network.occurrences
              if str(network.binding_for_occurrence(item.occurrence_id).goal_signature.signature_id)
              == "prepare-delivery"]
    assert [str(item) for item in writes] == state["named"][-1:]  # 只有一个"写"，就是被点名的那一个
    [shared] = writes
    adopted = set(network.adopted_instance_ids)
    holders = [instance for instance in network.method_instances if instance.instance_id in adopted
               and any(child.occurrence_id == shared for child in instance.child_bindings)]
    assert len(holders) == 2  # 左右两个子目标的做法都持有它
    assert any(item.producer_occurrence == shared for item in network.data_requirements)  # 右边下游读它
    # 预算去向（第六批）：共用的步骤只出现一次，标两个分支共用
    task = str(network.binding_for_occurrence(shared).task_id)
    listed = [step for row in rows for step in row["steps"] if step["task_id"] == task]
    assert len(listed) == 1 and listed[0]["branches"] == 2 and listed[0]["attempts"] == 1, listed


def test_sharing_does_not_widen_what_the_named_step_answers_for(tmp_path):
    _, rejected, state, _ = _run(tmp_path, overreach_first=True)
    assert state["overreached"]
    refused = [item for item in rejected if "REUSE_NOT_ALLOWED" in (item.get("rejection_codes") or ())]
    assert refused, rejected
    assert "sharing a step does not add to what it answers for" in str(refused[0])


def test_naming_a_step_that_reads_upstream_requires_naming_the_upstream_too(tmp_path):
    """被点名的步骤读别的步骤的产出时，上游要一并点名：左右两个子目标都用"写 + 接着写"的做法；
    右边只点名共用左边的"接着写"（它读的是左边的"写"，而右边做法里它读右边自己的"写"）→ 输入
    不同，整份退回；连"写"一起点名后通过，两步都只做一次，任务完成。

    **改坏检验**：TG3-07 两端都是共用步骤的数据边照样再声明一次 → 连上游一起点名也提交不了 → 变红。"""
    holder: dict[str, Any] = {}
    state: dict[str, Any] = {"root": False, "left": None, "right": None, "partial": False, "full": False}

    def planner(request: Any) -> Any:
        package = package_of(request)
        goals = {item["occurrence_id"]: item for item in package["views"]["goals"] if item["open"]}
        contexts = package.get("method_proposal_contexts") or []
        if not goals:
            return None
        roots = [c for c in contexts if _type_of(c) == "user-goal"]
        if roots and not state["root"]:
            state["root"] = True
            method = two_branches(roots[0])
            second = method["composition"]["criterion_links"][-1]["parent_criterion_id"]
            method["composition"]["criterion_links"].append(  # 这条用例里左边也承接第二条要求
                {"parent_criterion_id": second, "child_step": "left", "child_criterion_id": second,
                 "evidence_requirement": "left 这个子目标也写出 NOTES.md"})
            return decision(roots[0]["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": method, "rationale": "左右两个子目标。"}}, "根目标拆成两个子目标。")
        selections = {item["occurrence_id"]: item for item in package.get("method_selection") or ()}
        by_text = {str(goal["params"].get("goal")): goal for goal in goals.values()}
        sub = {str(c["subject_key"]): c for c in contexts if _type_of(c) == "sub-goal-1"}
        left, right = by_text.get(LEFT), by_text.get(RIGHT)
        for occurrence, goal in goals.items():  # 根目标：采用刚通过审阅的做法
            selection = selections.get(occurrence) or {}
            if goal not in (left, right) and selection.get("applicable"):
                return _refine(goal, selection["applicable"][0], dict(selection.get("bindings") or goal["params"]))
        if left is not None:
            if state["left"] is None and str(left["subject_key"]) in sub:
                method = write_then_continue(sub[str(left["subject_key"])])
                state["left"] = (method["method_id"], method["method_version"])
                return decision(left["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                    "method": method, "rationale": "写，再接着写。"}}, "左子目标两步。")
            selection = selections.get(left["occurrence_id"]) or {}
            chosen = next((item for item in selection.get("applicable") or ()
                           if (item["method_id"], item["method_version"]) == state["left"]), None)
            if chosen is not None:
                return _refine(left, chosen, dict(selection.get("bindings") or left["params"]))
            return None
        if right is None:
            return None
        if state["right"] is None and str(right["subject_key"]) in sub:
            method = write_then_continue(sub[str(right["subject_key"])])
            state["right"] = (method["method_id"], method["method_version"])
            return decision(right["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": method, "rationale": "两步都共用左边的。"}}, "右子目标两步。")
        selection = selections.get(right["occurrence_id"]) or {}
        applicable = {(item["method_id"], item["method_version"]): item for item in selection.get("applicable") or ()}
        rows = {row["task_type"]: row for row in package.get("sharing_candidates") or ()}
        if not {"prepare-delivery", "continue-delivery"} <= set(rows) or state["right"] not in applicable:
            store = holder["world"].store
            step = next((item for item in package["views"]["goals"]
                         if item["form"] == "primitive" and item.get("task_id")
                         and any(str(attempt.status.value) in {"RUNNING", "SUBMITTED", "VERIFYING"}
                                 for attempt in store.list_attempts(item["task_id"]))), None)
            if step is None:
                return None
            semantics = holder["world"].loop._dispatch_for(holder["mission_id"]).semantics().task_semantics_of(
                holder["mission_id"], step["task_id"])
            return decision(right["subject_key"], "WAIT", {"wait_for": [{
                "kind": "task", "id": step["task_id"], "semantic_revision": int(semantics.contract_revision),
                "content_hash": semantics.content_hash()}], "reason": "等左边两步都能共用"}, "还不能共用。")
        assert rows["continue-delivery"]["reads_from"] == [rows["prepare-delivery"]["occurrence_id"]]
        bindings = dict(selection.get("bindings") or right["params"])
        both = {"write": rows["prepare-delivery"]["occurrence_id"],
                "continue": rows["continue-delivery"]["occurrence_id"]}
        if not state["partial"]:
            state["partial"] = True  # 只点名下游那一步：它读的上游没点名
            return _refine(right, applicable[state["right"]], bindings, {"continue": both["continue"]})
        state["full"] = True
        return _refine(right, applicable[state["right"]], bindings, both)

    async def case():
        import json

        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner)) as world:
            mission_id = world.create({"goal": "写 out.md，再据它写 NOTES.md", "idempotency_key": "shared-upstream",
                                       "success_criteria": ["file:out.md", "file:NOTES.md"]})["mission_id"]
            holder.update(world=world, mission_id=mission_id)
            try:
                mission = await asyncio.wait_for(world.run_until_settled(mission_id, rounds=60), 240)
            except TimeoutError as error:
                raise AssertionError(f"the mission never settled: {state}") from error
            refused = [json.dumps(e.payload, ensure_ascii=False) for e in world.store.list_events(mission_id)
                       if e.type == "PlanningDecisionEvaluated" and e.payload.get("status") != "COMMITTED"
                       and e.payload.get("decision_type") == "REFINE"]
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report, state,
                                                              [item[:600] for item in refused[-2:]])
            assert state["partial"] and state["full"]
            # 退回原因写明：被共用的那一步的输入端口有了两个来源（它原来的上游 + 右边自己的"写"）
            assert any("single_port_overbound" in item and "delivery" in item for item in refused), [
                item[:600] for item in refused]
            network = world.loop._dispatch_for(mission_id).network(mission_id)
            kinds = [str(network.binding_for_occurrence(item.occurrence_id).goal_signature.signature_id)
                     for item in network.occurrences]
            assert kinds.count("prepare-delivery") == 1 and kinds.count("continue-delivery") == 1

    asyncio.run(case())
