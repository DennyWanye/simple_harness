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
            # 依据记在知识记录里，不写保证通道读的支持集合表——否则本任务所有验收证书一起过期
            assert store.connection.execute(
                "SELECT COUNT(*) FROM justification_sets WHERE mission_id=?", (mission_id,)).fetchone()[0] == 0
            events = list(store.list_events(mission_id))
            assert not [e for e in events if e.type == "AssuranceCloseoutEvaluated"
                        and "EVIDENCE_STALE" in e.payload.get("reasons", ())]
            [event] = [e.payload for e in events if e.type == "KnowledgeCommitted"]
            assert event["basis"] == "review_confirmed" and event["support"] == support
        finally:
            await context.__aexit__(None, None, None)

    asyncio.run(case())
