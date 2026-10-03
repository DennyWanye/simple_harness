# SPDX-License-Identifier: Apache-2.0
"""输入用上游哪一版（HTN 补齐阶段 D，补全方案 2.1）。

做法里一条数据输入默认"跟随"：消费者每次新尝试都用上游当前通过验收、还在计数的那一版；写了
``"pin": true`` 就"钉住"：固定用它第一次冻结的那一版。这里是产品同形世界上的接线检验——两步
接力（write → continue），continue 的内容审阅第一次被打回、同一做法重试：

* 默认：计划里这条数据依赖是"跟随"；两次尝试冻结的都是上游当前通过验收的那一版；
* ``pin``：这条依赖是"钉住"；第一次冻结之后，钉住表里有这一版，重试仍用它。

选择规则本身（跟随取授权版本、钉住取钉的版本、授权读不到如实报）在
``full_target/test_input_manifest_resolution.py`` 函数级覆盖；"上游重做出第二个验收后下游
跟过去"需要一份重做上游的修复剧本，留到整体联测（实施记录阶段 D 有记）。

**改坏检验**：编译器把默认策略改回"钉住" → 默认变体的策略断言变红。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.contracts.htn import SourceRevisionPolicy
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


def relay(context: dict[str, Any], *, pin: bool) -> dict[str, Any]:
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


@pytest.mark.parametrize("pin", [False, True], ids=["follow", "pin"])
def test_retry_follows_authorized_upstream_revision(tmp_path, pin):
    state = {"proposed": False, "content_reviews": 0}

    def planner(request: Any) -> Any:
        package = package_of(request)
        if package.get("repair_requests"):
            return retry_same_method(request)
        contexts = package.get("method_proposal_contexts") or []
        if contexts and not state["proposed"]:
            state["proposed"] = True
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": relay(contexts[0], pin=pin), "rationale": "先写 a.md，再接着写 b.md。"}}, "两步接力。")
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
            mission_id = world.create({"goal": "写 a.md 再写 b.md", "idempotency_key": f"input-revisions-{pin}",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=30)
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            dispatch = world.loop._dispatch_for(mission_id)
            network = dispatch.network(mission_id)
            [edge] = network.data_requirements
            assert edge.source_revision_policy is (
                SourceRevisionPolicy.PINNED if pin else SourceRevisionPolicy.FOLLOW_AUTHORIZED_REVISION)

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
            pinned = dispatch._pinned_revisions(mission_id)
            assert pinned == ({edge.requirement_id: revision} if pin else {})

    asyncio.run(case())
