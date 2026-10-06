# SPDX-License-Identifier: Apache-2.0
"""第 2 批 K05：读取知识后再复核一次有效性（原计划 §11.3 两道：检索前过滤 + 读取后复核）。

目录（``knowledge_list``）是检索前的过滤；``knowledge_read`` 把正文取出之后、交出去之前，用同一个
判定（``knowledge_standing``，不写第二套）再判一次：

* 目录判它当前、复核判它不再当前 → 不返回正文，如实写明现状；
* 编号是本任务的一条知识、只是已过时 → 同样如实说明，不当成"没有这条"；
* 编号对不上 → 照旧拒绝。

**改坏检验**：去掉交出去之前的那次复核 → 第一条变红（正文照样返回）。
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


def test_knowledge_that_goes_stale_between_the_catalogue_and_the_handover_is_not_served(store, monkeypatch):
    """目录那一次判它当前；正文取出后的复核判它不再当前 → 没有正文，只有现状。"""
    calls = {"n": 0}

    def judged(store_, record, **kwargs):
        calls["n"] += 1
        # 第一次（编目录）当前；之后（交出去之前的复核）它所依赖的验收已经不算数了
        return standing_module.CURRENT if calls["n"] == 1 else f"{standing_module.STALE}:acceptance_not_current"

    monkeypatch.setattr(knowledge_tools, "knowledge_standing", judged)
    listing = read_knowledge_tool(store, MISSION, "knowledge_list", {"limit": 5})
    assert [row["id"] for row in listing["items"] if row["layer"] == "verified"] == ["k1"]

    calls["n"] = 0
    reply = read_knowledge_tool(store, MISSION, "knowledge_read", {"id": "k1"})
    assert calls["n"] >= 2, "the handover must judge again, not reuse the catalogue's verdict"
    assert reply["content"] is None and reply["sha256"] is None
    assert reply["standing"] == "STALE:acceptance_not_current"
    assert reply["id"] == "k1" and reply["ref"] == "k1@1" and reply["layer"] == "verified"
    assert "不再当前" in reply["notice"] and "不能引用" in reply["notice"]


def test_a_stale_entry_of_this_mission_is_explained_not_denied(store):
    """这条知识在库里、属于本任务，只是没有任何依据撑着（不再当前）：如实说明，不是"没有这条"。"""
    reply = read_knowledge_tool(store, MISSION, "knowledge_read", {"id": "k1"})
    assert reply["content"] is None
    assert reply["standing"] == "STALE:no_support"
    assert reply["notice"].startswith("读取后复核")


def test_an_unknown_id_is_still_refused(store):
    with pytest.raises(ValueError, match="not current or not available"):
        read_knowledge_tool(store, MISSION, "knowledge_read", {"id": "nobody"})


def test_the_current_entry_is_served_whole_when_both_judgements_agree(store, monkeypatch):
    monkeypatch.setattr(knowledge_tools, "knowledge_standing", lambda *_a, **_k: standing_module.CURRENT)
    reply = read_knowledge_tool(store, MISSION, "knowledge_read", {"id": "k1"})
    assert reply["content"] == _record().content and "standing" not in reply
    assert reply["ref"] == "k1@1" and reply["layer"] == "verified"
