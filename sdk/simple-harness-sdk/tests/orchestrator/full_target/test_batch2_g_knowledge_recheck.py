# SPDX-License-Identifier: Apache-2.0
"""第 2 批 K05：读取知识后再过一道门（原计划 §11.3 两道：检索前过滤 + 读取后复核）。

目录（``knowledge_list``）是检索前的过滤；``knowledge_read`` 把正文取出之后、交出去之前过一道门
（推后第 1 批 A26，裁决 2026-10-07 第 5 件）：产品里这道门是使用证书签发方（判定仍是同一个
``knowledge_standing``，另核时钟、授权、根实例并留证书，见 ``product_world/test_point_use_certificates.py``）。
这里只验工具这一侧的约定（库里没有保证通道，门用替身）：

* 门说不能交 → 不返回正文，如实写明原因；每次读都过门，目录那次判定不替这一次作数；
* 没有门 → 不交正文（不留一条绕过门的路）；
* 编号是本任务的一条知识、只是已过时 → 如实说明，不当成"没有这条"；编号对不上 → 照旧拒绝。

**改坏检验**：去掉交出去之前那道门 → 第一条变红（正文照样返回）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.context import knowledge_tools
from agent_orchestrator.context.knowledge_tools import read_knowledge_tool
from agent_orchestrator.contracts import Budget, Mission, MissionStatus
from agent_orchestrator.memory import knowledge_standing as standing_module
from agent_orchestrator.memory.verified_knowledge import KnowledgeRecord
from agent_orchestrator.storage.store import Store

MISSION = "mission-recheck"


def _mission() -> Mission:
    return Mission(
        id=MISSION, goal="g", success_criteria=("ok",), stop_conditions=(), allowed_tools=(),
        risk_level="sandbox", budget=Budget(max_tokens=1000, max_attempts=2), tenant_id="t",
        status=MissionStatus.CREATED, created_at=1.0, version=1, idempotency_key=MISSION)


def _record(kid: str = "k1") -> KnowledgeRecord:
    return KnowledgeRecord(
        id=kid, mission_id=MISSION, claim_id=kid, content="空输入返回空列表。" * 3, type="statement",
        status="VERIFIED", version=1, key="impl_a.empty_input", stance="affirms", proposed_by="agent",
        source_task="t1", source_attempt="t1:attempt-1", source_result="r1",
        evidence=("pytest:tests/x.py",), verifier={"basis": "review_confirmed"}, created_at=1.0)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    store = Store.open(tmp_path / "orchestrator.db")
    store.insert_mission(_mission(), spec_hash="h" * 64)
    store.upsert_knowledge(_record())
    yield store
    store.close()


def test_knowledge_the_gate_refuses_at_handover_is_not_served(store, monkeypatch):
    """目录判它当前；交出去之前那道门说不能交 → 没有正文，只有原因。每次读都过一次门。"""
    from agent_orchestrator.orchestrator.assurance_point_use import EvidenceClaim

    monkeypatch.setattr(knowledge_tools, "knowledge_standing", lambda *_a, **_k: standing_module.CURRENT)
    listing = read_knowledge_tool(store, MISSION, "knowledge_list", {"limit": 5})
    assert [row["id"] for row in listing["items"] if row["layer"] == "verified"] == ["k1"]

    asked: list[EvidenceClaim] = []

    def gate(claim: EvidenceClaim) -> tuple[str, ...]:
        asked.append(claim)
        return ("knowledge:k1@1:STALE:acceptance_not_current",)

    reply = read_knowledge_tool(store, MISSION, "knowledge_read", {"id": "k1"}, handover=gate)
    assert asked == [EvidenceClaim.knowledge("k1", 1)], "the handover must pass the gate, not reuse the catalogue"
    assert reply["content"] is None and reply["sha256"] is None
    assert reply["standing"] == "knowledge:k1@1:STALE:acceptance_not_current"
    assert reply["id"] == "k1" and reply["ref"] == "k1@1" and reply["layer"] == "verified"
    assert "不再当前" in reply["notice"] and "不能引用" in reply["notice"]
    read_knowledge_tool(store, MISSION, "knowledge_read", {"id": "k1"}, handover=gate)
    assert len(asked) == 2


def test_content_is_not_handed_over_without_the_gate(store, monkeypatch):
    monkeypatch.setattr(knowledge_tools, "knowledge_standing", lambda *_a, **_k: standing_module.CURRENT)
    with pytest.raises(ValueError, match="hand-over gate"):
        read_knowledge_tool(store, MISSION, "knowledge_read", {"id": "k1"})


def test_a_stale_entry_of_this_mission_is_explained_not_denied(store):
    """这条知识在库里、属于本任务，只是没有任何依据撑着（不再当前）：如实说明，不是"没有这条"。"""
    reply = read_knowledge_tool(store, MISSION, "knowledge_read", {"id": "k1"})
    assert reply["content"] is None
    assert reply["standing"] == "STALE:no_support"
    assert reply["notice"].startswith("读取后复核")


def test_an_unknown_id_is_still_refused(store):
    with pytest.raises(ValueError, match="not current or not available"):
        read_knowledge_tool(store, MISSION, "knowledge_read", {"id": "nobody"})


def test_the_current_entry_is_served_whole_when_the_gate_lets_it_through(store, monkeypatch):
    monkeypatch.setattr(knowledge_tools, "knowledge_standing", lambda *_a, **_k: standing_module.CURRENT)
    reply = read_knowledge_tool(store, MISSION, "knowledge_read", {"id": "k1"}, handover=lambda claim: ())
    assert reply["content"] == _record().content and "standing" not in reply
    assert reply["ref"] == "k1@1" and reply["layer"] == "verified"
