# SPDX-License-Identifier: Apache-2.0
"""审阅员逐条确认的结论进知识库（HTN 补齐阶段 C；知识进库方案）。

一条结论成为"已验证"知识：独立审阅整体通过（或判不下来而人裁决通过），审阅员在回复的
``claims`` 里逐条确认，且引用了审查对象之外的证据。知识记录自带它的依据（``support``）。

**改坏检验**：验收里不读审阅确认 → 第一条变红（没有知识）。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    review_input,
    review_reply,
    worker_reply,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def two_claims(request: Any) -> Any:
    """The ordinary worker, with a second claim nobody will confirm."""
    reply = worker_reply(request)
    if isinstance(reply, tuple):
        return reply
    body = json.loads(reply[len("<result_envelope>"):-len("</result_envelope>")])
    body["claims"].append({"content": "这份笔记以后不用再改", "confidence": 0.5, "evidence": body["evidence"]})
    return "<result_envelope>" + json.dumps(body, ensure_ascii=False) + "</result_envelope>"


def labels(data: dict[str, Any], kind: str) -> list[str]:
    return [item["label"] for item in data.get("evidence", ()) if item["ref"]["kind"] == kind]


def confirming(pick: Any, *, verdict: str = "ACCEPT", grade: str = "PASS") -> Any:
    """The reviewer: content reviews also carry ``claims`` built by ``pick(data)``."""

    def reviewer(request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        if str((data.get("package") or {}).get("purpose")) != "TASK_CONTENT":
            return review_reply(data)
        body = json.loads(review_reply(data, verdict=verdict, grade=grade))
        body["claims"] = pick(data)
        return json.dumps(body, ensure_ascii=False)

    return reviewer


def first_claim_by_artifact(data: dict[str, Any]) -> list[dict[str, Any]]:
    listed = data["package"]["claims_to_confirm"]
    return [{"claim_id": listed[0]["claim_id"], "confirmed": True,
             "evidence_ids": labels(data, "artifact")[:1], "reason": "文件确实写出，内容与结论一致。"}]


async def _run(tmp_path, reviewer: Any, key: str) -> tuple[Any, str, Any]:
    context = product_world(tmp_path / "root", LayeredScriptedProvider(worker=two_claims, reviewer=reviewer))
    world = await context.__aenter__()
    mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                               "idempotency_key": key})["mission_id"]
    mission = await world.run_until_settled(mission_id, rounds=20)
    return context, mission_id, (world, mission)


def test_confirmed_claim_becomes_verified_knowledge(tmp_path):
    async def case():
        context, mission_id, (world, mission) = await _run(tmp_path, confirming(first_claim_by_artifact), "kn-confirm")
        try:
            assert mission.status.value == "COMPLETED", mission.final_report
            store = world.store
            [record] = store.list_knowledge(mission_id)
            assert record.status == "VERIFIED" and record.verifier["basis"] == "review_confirmed"
            assert record.verifier["record_id"].startswith("assurance-review:")
            claims = {claim.id: claim for claim in store.list_mission_claims(mission_id)}
            assert str(claims[record.claim_id].status) == "VERIFIED"
            [other] = [claim for claim in claims.values() if claim.id != record.claim_id]
            assert str(other.status) != "VERIFIED"  # nobody confirmed it
            support = record.support
            assert support["acceptance_id"].startswith("acc-") and support["knowledge"] == []
            [artifact] = support["artifacts"]
            assert set(artifact) == {"id", "version", "content_hash"}
            # 依据记在知识记录里；写知识不让本任务的验收证书"依据已变"
            events = list(store.list_events(mission_id))
            assert not [e for e in events if e.type == "AssuranceCloseoutEvaluated"
                        and "EVIDENCE_STALE" in e.payload.get("reasons", ())]
            [event] = [e.payload for e in events if e.type == "KnowledgeCommitted"]
            assert event["basis"] == "review_confirmed" and event["support"] == support
        finally:
            await context.__aexit__(None, None, None)

    asyncio.run(case())


def test_confirmation_without_independent_evidence_not_upgraded(tmp_path):
    """确认只引审查对象自己（结果信封）→ 不入库：结果不能当自己结论的证明。"""

    def only_the_result(data: dict[str, Any]) -> list[dict[str, Any]]:
        listed = data["package"]["claims_to_confirm"]
        return [{"claim_id": listed[0]["claim_id"], "confirmed": True,
                 "evidence_ids": labels(data, "result")[:1], "reason": "执行者自己是这么说的。"}]

    async def case():
        context, mission_id, (world, mission) = await _run(tmp_path, confirming(only_the_result), "kn-self")
        try:
            assert mission.status.value == "COMPLETED", mission.final_report
            assert world.store.list_knowledge(mission_id) == []
        finally:
            await context.__aexit__(None, None, None)

    asyncio.run(case())


def test_confirmation_in_a_review_that_did_not_pass_is_not_knowledge(tmp_path):
    """整体结论是"返工"的那次审阅里确认的结论不入库。"""
    calls = {"n": 0}

    def reviewer(request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        if str((data.get("package") or {}).get("purpose")) != "TASK_CONTENT":
            return review_reply(data)
        calls["n"] += 1
        if calls["n"] == 1:
            body = json.loads(review_reply(data, verdict="REWORK", grade="FAIL", reason="内容不够"))
            body["claims"] = first_claim_by_artifact(data)
            return json.dumps(body, ensure_ascii=False)
        return review_reply(data)

    async def case():
        context, mission_id, (world, mission) = await _run(tmp_path, reviewer, "kn-rework")
        try:
            # 返工之后怎么走不是这条用例的事（脚本规划器不接修复轮）；只看那次确认没有入库
            assert calls["n"] >= 1 and world.store.list_knowledge(mission_id) == []
            assert not [claim for claim in world.store.list_mission_claims(mission_id)
                        if str(claim.status) == "VERIFIED"]
        finally:
            await context.__aexit__(None, None, None)

    asyncio.run(case())


def test_person_pass_on_inconclusive_upgrades_confirmed_claims(tmp_path):
    """两位审阅员都判不下来、但逐条确认了第 1 条；用户复核"通过" → 这条才入库，并记着人的裁决。"""

    async def case():
        reviewer = confirming(first_claim_by_artifact, verdict="INCONCLUSIVE", grade="UNKNOWN")
        context = product_world(tmp_path / "root", LayeredScriptedProvider(worker=two_claims, reviewer=reviewer))
        world = await context.__aenter__()
        try:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "kn-person"})["mission_id"]
            pending: list[dict[str, Any]] = []
            for _ in range(20):
                await world.drain()
                pending = [a for a in world.control.approvals(mission_id) if a.get("state") == "PENDING"]
                if pending:
                    break
            assert len(pending) == 1 and world.store.list_knowledge(mission_id) == []
            world.control.decide(pending[0]["request_id"], "review_pass", note="我看过，合格")
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert mission.status.value == "COMPLETED", mission.final_report
            [record] = world.store.list_knowledge(mission_id)
            assert record.verifier["basis"] == "review_confirmed"
            assert record.verifier["adjudication"]["decision"] == "pass"
        finally:
            await context.__aexit__(None, None, None)

    asyncio.run(case())
