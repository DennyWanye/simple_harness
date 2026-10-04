# SPDX-License-Identifier: Apache-2.0
"""输入用上游哪一版（HTN 补齐阶段 D；TaskGraph 补全第五批删掉了"钉住旧版本"）。

做法里的数据输入一律"跟随"：消费者每次新尝试都用上游当前算数的那一版验收产出；做法里再写
``"pin"`` 按未知字段拒绝。这里是产品同形世界上的接线检验——两步接力（write → continue），
continue 的内容审阅第一次被打回、同一做法重试：两次尝试冻结的都是上游当前通过验收的那一版。

选择规则本身（取授权版本、授权读不到如实报）在 ``full_target/test_input_manifest_resolution.py``
函数级覆盖。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    one_step_method,
    planner_reply,
    retry_same_method,
    review_input,
    review_reply,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def relay(context: dict[str, Any], *, pin: bool = False) -> dict[str, Any]:
    request = context["request"]
    method = one_step_method(context)
    follow = next(item for item in request["operators"]
                  if str(item["task_type_ref"]["id"]).endswith("continue-delivery"))
    first, second = [item["id"] for item in request["criterion_evidence"]]
    method["steps"].append({
        "local_id": "continue", "task_type_ref": follow["task_type_ref"], "form": "primitive",
        "arguments": {"delivery": {"op": "output", "step": "write", "port": "delivery",
                                   **({"pin": True} if pin else {})}},
        "required_capabilities": list(follow["required_capabilities"]), "obligation_relation": "refines_parent"})
    method["composition"]["criterion_links"] = [
        {"parent_criterion_id": first, "child_step": "write", "child_criterion_id": first,
         "evidence_requirement": "write 写出 a.md"},
        {"parent_criterion_id": second, "child_step": "continue", "child_criterion_id": second,
         "evidence_requirement": "continue 接着写出 b.md"}]
    method["composition"]["finalizer_step"] = "continue"
    return method


def test_retry_follows_authorized_upstream_revision(tmp_path):
    state = {"proposed": False, "content_reviews": 0}

    def planner(request: Any) -> Any:
        package = package_of(request)
        if package.get("repair_requests"):
            return retry_same_method(request)
        contexts = package.get("method_proposal_contexts") or []
        if contexts and not state["proposed"]:
            state["proposed"] = True
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": relay(contexts[0]), "rationale": "先写 a.md，再接着写 b.md。"}}, "两步接力。")
        return planner_reply(request)

    def reviewer(request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        if str((data.get("package") or {}).get("purpose")) == "TASK_CONTENT":
            state["content_reviews"] += 1
            if state["content_reviews"] == 2:  # 第二次内容审阅是 continue 的第一次：打回
                return review_reply(data, verdict="REJECTED", grade="FAIL", reason="脚本化审阅：再做一遍。")
        return review_reply(data)

    async def case():
        provider = LayeredScriptedProvider(planner=planner, reviewer=reviewer)
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写 a.md 再写 b.md", "idempotency_key": "input-revisions",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=30)
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            dispatch = world.loop._dispatch_for(mission_id)
            network = dispatch.network(mission_id)
            [edge] = network.data_requirements
            assert "source_revision_policy" not in edge.to_json()

            frozen = [
                [(b["input_port"], b["bound_input"]["source_revision"]) for b in json.loads(raw)["bindings"]]
                for (raw,) in world.store.connection.execute(
                    "SELECT m.manifest_json FROM taskgraph_attempt_inputs b"
                    " JOIN input_manifests m ON m.manifest_hash=b.manifest_hash"
                    " JOIN attempts a ON a.attempt_id=b.attempt_id"
                    " WHERE b.mission_id=? AND b.occurrence_id=? ORDER BY a.ordinal",
                    (mission_id, str(edge.consumer_occurrence)))]
            assert len(frozen) == 2 and frozen[0] == frozen[1] and frozen[0][0][1]  # 两次尝试、同一版
            revision = frozen[0][0][1]
            accepted = dispatch.accepted_outputs(mission_id, network)
            assert accepted.authorized_revision(edge.producer_occurrence, edge.output_port) == revision

    asyncio.run(case())


def test_a_method_can_no_longer_pin_an_input():
    """``"pin"`` 不再是数据引用的字段：带了按未知字段拒绝。"""
    from agent_orchestrator.contracts.htn import MAX_CONDITION_NODES, StructureBudget, parse_value

    with pytest.raises(ContractError, match="unknown fields"):
        parse_value({"op": "output", "step": "write", "port": "delivery", "pin": True}, "argument",
                        StructureBudget(MAX_CONDITION_NODES))
