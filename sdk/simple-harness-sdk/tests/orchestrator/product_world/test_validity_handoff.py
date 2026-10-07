# SPDX-License-Identifier: Apache-2.0
"""交接前核对凭证与纪元（HTN 补齐阶段 C 第 5 条）。

对外操作真正发出去之前，再核一次它所属步骤的有效性见证：见证是在某个作用域纪元下取的，
纪元动了（或所依据的验收不再当前）就拒绝交接。拒绝不改动作状态、不算一次交接、不让任务
以"动作失败"停。

**改坏检验**：交接前不核对 → 纪元动了照样发出去 → 变红。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.contracts.evidence_state import (
    Availability,
    TruthValue,
    Validity,
    ValidityWitness,
    WitnessDecision,
    WitnessPurpose,
)
from agent_orchestrator.contracts.semantic_base import TypedRef, TypedRefKind
from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, decision, planner_reply
from test_operation import PUBLISH, TARGET, _confirm_completion


def _start_certificates(store: Any, mission_id: str, action_key: str) -> list[Any]:
    return store.connection.execute(
        "SELECT * FROM assurance_use_certificates WHERE mission_id=? AND purpose='START' "
        "AND consumer_kind='ACTION' AND consumer_id=?", (mission_id, action_key)).fetchall()


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_handoff_refused_when_scope_epoch_moved(tmp_path):
    stall_contexts: list[dict[str, Any]] = []

    async def case():
        published = tmp_path / "published"
        published.mkdir()
        connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
        policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
        def planner(request: Any) -> Any:
            package = package_of(request)
            asked = next((entry for entry in package.get("repair_requests") or ()
                          if entry["request"]["trigger_source"] == "NO_DISPATCHABLE_WORK"), None)
            if asked is None:
                return planner_reply(request)
            stall_contexts.append(asked["request"]["context"])
            return decision(package["planning_subjects"][0]["subject_key"], "NO_CHANGE",
                            {"reason": "计划本身没有可改的。"}, "不改计划。")

        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner),
                                 connectors={"file_publish": connector}, deployment_policy=policy) as world:
            store = world.store
            mission_id = world.create({"goal": "写一份周报 reports/weekly.md 并发布",
                                       "success_criteria": ["file:" + TARGET, PUBLISH],
                                       "idempotency_key": "validity-handoff"})["mission_id"]
            await world.drain()
            _confirm_completion(world, mission_id)
            approvals: list[dict[str, Any]] = []
            for _ in range(20):
                await world.drain()
                approvals = [a for a in world.control.approvals(mission_id) if a.get("state") == "PENDING"]
                if approvals:
                    break
            [action] = store.list_actions(mission_id)[-1:]
            # 这一步有上游输入时，派发会给它发有效性见证（单步任务没有上游，这里按同一个写入口
            # 补一条它在纪元 0 下取的见证）；随后纪元动了——真实会发生：上游被修复。
            scope = "validity-scope-upstream"
            HtnStore(store).insert_validity_witness(mission_id, ValidityWitness(
                witness_id="vw-test-upstream", purpose=WitnessPurpose.START,
                consumer_ref=TypedRef(TypedRefKind.TASK, str(action["task_id"]), 1, "0" * 64),
                truth=TruthValue.TRUE, freshness=Validity.CURRENT, availability=Availability.READABLE,
                decision=WitnessDecision.USABLE, scope_id=scope, scope_epoch=0, support_revision=0,
                as_of_ms=int(store.now * 1000)), subject="")
            HtnStore(store).bump_epoch(mission_id, scope, bumped_by="test-upstream-repair")
            world.control.decide(approvals[0]["request_id"], "approve")
            for _ in range(4):
                await world.drain(timeout=10)
            after = store.get_action(action["action_key"])
            refusal = world.loop.actions.last_refusal.get(action["action_key"], "")
            assert refusal.startswith("validity_stale:scope_epoch:"), refusal
            assert int(after.get("handoffs") or 0) == 0 and not any(published.rglob("*.md"))
            # 推后第 1 批 A26：交接前的核对就是签 START 证书；地基不当前就不签
            assert _start_certificates(store, mission_id, action["action_key"]) == []
            # 地基一直不回来：这不是"等人审批"，任务不会永远挂着——停滞确认后如实交给规划器，
            # 规划器不改，就按"没有可派发的工作"停，报告里写着哪次交接为什么被拒。
            mission = await world.run_until_settled(mission_id, rounds=20, timeout=20)
            assert stall_contexts and stall_contexts[0]["handoff_refused"][0]["action_key"] == action["action_key"]
            assert mission.status.value == "FAILED", mission.status
            assert mission.final_report["stop_reason"] == "no_dispatchable_work"  # 不是"动作失败"
            [refused] = mission.final_report["detail"]["handoff_refused"]
            assert refused["reason"].startswith("validity_stale:scope_epoch:")
            assert int(store.get_action(action["action_key"]).get("handoffs") or 0) == 0
            return json.dumps({"state": after["state"], "refusal": refusal}, ensure_ascii=False)

    print(asyncio.run(case()))


def test_handoff_goes_through_once_the_witness_is_reissued(tmp_path):
    """纪元动了、上游验收仍然当前：发布步骤的输入见证每轮按验收现状重发，新纪元下的见证一出来，
    交接就照常放行，只发出去一次（HTN 补齐 F1，阶段 C 用例 12 欠的后一半）。

    **改坏检验**：输入见证不重发 → 一直按旧纪元拒绝 → 没有发出去 → 变红。"""
    from test_input_revisions import relay

    def planner(request: Any) -> Any:
        package = package_of(request)
        contexts = package.get("method_proposal_contexts") or []
        if contexts and not (package.get("method_selection") or [{}])[0].get("applicable"):
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": relay(contexts[0], pin=False), "rationale": "先写笔记，再接着写周报。"}}, "两步。")
        return planner_reply(request)

    async def case():
        published = tmp_path / "published"
        published.mkdir()
        connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
        policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner),
                                 connectors={"file_publish": connector}, deployment_policy=policy) as world:
            store = world.store
            mission_id = world.create({"goal": "写笔记，再接着写周报并发布",
                                       "success_criteria": ["file:notes/a.md", "file:" + TARGET, PUBLISH],
                                       "idempotency_key": "validity-reissue"})["mission_id"]
            await world.drain()
            _confirm_completion(world, mission_id)
            approvals: list[dict[str, Any]] = []
            for _ in range(30):
                await world.drain()
                approvals = [a for a in world.control.approvals(mission_id) if a.get("state") == "PENDING"]
                if approvals:
                    break
            [action] = store.list_actions(mission_id)[-1:]

            def witnesses() -> list[dict[str, Any]]:
                return [json.loads(row[0]) for row in store.connection.execute(
                    "SELECT witness_json FROM validity_witnesses WHERE mission_id=? AND consumer_id=?",
                    (mission_id, action["task_id"]))]

            assert {(w["scope_id"], w["scope_epoch"], w["decision"]) for w in witnesses()} == {("mission", 0, "USABLE")}
            HtnStore(store).bump_epoch(mission_id, "mission", bumped_by="test-world-changed")
            world.control.decide(approvals[0]["request_id"], "approve")
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert mission.status.value == "COMPLETED", mission.final_report
            assert ("mission", 1, "USABLE") in {(w["scope_id"], w["scope_epoch"], w["decision"]) for w in witnesses()}
            assert int(store.get_action(action["action_key"]).get("handoffs") or 0) == 1
            assert len(list(published.rglob("*.md"))) == 1
            # 推后第 1 批 A26：放行的那次交接带一张 START 证书，钉住这一步与它所依据的上游验收
            [certificate] = _start_certificates(store, mission_id, action["action_key"])
            body = json.loads(certificate["certificate_json"])
            assert body["decision"] == "USABLE" and body["consumer_kind"] == "ACTION"
            kinds = {json.loads(item["key"])["kind"] for item in body["read_set"] if item["channel"] == "OBJECT"}
            assert {"task", "acceptance"} <= kinds, kinds

    asyncio.run(case())
