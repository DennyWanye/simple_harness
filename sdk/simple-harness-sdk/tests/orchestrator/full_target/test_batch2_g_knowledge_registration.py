# SPDX-License-Identifier: Apache-2.0
"""第 2 批 K02：知识记录补三个登记项——有效期、允许用途、保障等级（原计划 §11.2、§25.1 第 5 条）。

* 记录有这三个字段，默认 None；有效期只收三键对象（失效时刻可以是 None）；
* 迁移 45 给 knowledge 表加三列（表重建照迁移 43 的做法）；44 版的库能打开、旧行三列为 NULL；
* 审阅确认入库的知识从验收用的使用证书取有效期、从任务的保证通道取保障等级；允许用途没有记录
  来源，是 None；系统测试观察入库的知识三项都是 None（没有审阅记录和证书，不猜）；
* 读工具（目录与原文）与知识视图都展示这三项。

**改坏检验**：写方不传登记项 → 第三条变红（保障等级为 None）。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

import pytest

from agent_orchestrator.context.knowledge_tools import read_knowledge_tool
from agent_orchestrator.context.retrieval import knowledge_view
from agent_orchestrator.contracts import ContractError
from agent_orchestrator.memory.verified_knowledge import KnowledgeRecord
from agent_orchestrator.storage import schema
from agent_orchestrator.storage.store import Store

NEW_COLUMNS = ("validity_interval", "permitted_uses", "assurance_level")


def _record(**extra: Any) -> KnowledgeRecord:
    return KnowledgeRecord(
        id="k1", mission_id="m", claim_id="k1", content="空输入返回空列表", type="statement",
        status="VERIFIED", version=1, key="impl_a.empty_input", stance="affirms", proposed_by="agent",
        source_task="t1", source_attempt="t1:attempt-1", source_result="r1",
        evidence=("pytest:tests/x.py",), verifier={"basis": "review_confirmed"}, created_at=1.0, **extra)


def test_the_three_registrations_default_to_none_and_round_trip() -> None:
    plain = _record()
    assert (plain.validity_interval, plain.permitted_uses, plain.assurance_level) == (None, None, None)
    assert KnowledgeRecord.from_json(plain.to_json()) == plain
    full = _record(validity_interval={"valid_from_ms": 10, "valid_until_ms": None, "mission_epoch": 3},
                   permitted_uses=("DATA",), assurance_level="ASSURANCE_1_1")
    assert full.to_json()["validity_interval"] == {"valid_from_ms": 10, "valid_until_ms": None, "mission_epoch": 3}
    assert full.to_json()["permitted_uses"] == ["DATA"]
    assert KnowledgeRecord.from_json(json.loads(json.dumps(full.to_json()))) == full
    view = knowledge_view(full)
    assert view["assurance_level"] == "ASSURANCE_1_1" and view["permitted_uses"] == ["DATA"]
    assert view["validity_interval"]["mission_epoch"] == 3
    with pytest.raises(ContractError, match="validity_interval"):
        _record(validity_interval={"valid_from_ms": 10})
    with pytest.raises(ContractError, match="valid_from_ms"):
        _record(validity_interval={"valid_from_ms": None, "valid_until_ms": None, "mission_epoch": 3})


def _columns(path, table: str) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
    finally:
        connection.close()


def test_migration_45_adds_the_three_columns_and_a_version_44_library_upgrades(tmp_path, monkeypatch) -> None:
    # 迁移号固定 45（车道约定）；后面的车道还会追加，所以只钉这一条的存在与名字
    [forty_five] = [m for m in schema.MIGRATIONS if m.version == 45]
    assert forty_five.name == "orchestrator-knowledge-validity-uses-level" and schema.SCHEMA_VERSION >= 45
    fresh = tmp_path / "fresh.db"
    Store.open(fresh).close()
    assert set(NEW_COLUMNS) <= set(_columns(fresh, "knowledge"))

    path = tmp_path / "orchestrator.db"
    with monkeypatch.context() as patch:
        patch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:44])
        Store.open(path).close()
    assert not set(NEW_COLUMNS) & set(_columns(path, "knowledge"))
    connection = sqlite3.connect(path)  # foreign keys are off on a raw connection
    # 迁移在开了外键的连接里重建这张表，所以旧行要有它引用的任务
    connection.execute(
        "INSERT INTO missions(mission_id,tenant_id,idempotency_key,status,version,spec_hash,json,created_at,updated_at)"
        " VALUES ('m1','t','k','RUNNING',1,'h','{}',0,0)")
    connection.execute(
        "INSERT INTO knowledge(knowledge_id,mission_id,claim_id,key,status,version,source_task,json,created_at,updated_at)"
        " VALUES ('k1','m1','k1',NULL,'VERIFIED',1,'t1','{}',0,0)")
    connection.commit()
    connection.close()

    Store.open(path).close()
    assert set(NEW_COLUMNS) <= set(_columns(path, "knowledge"))
    connection = sqlite3.connect(path)
    try:
        [row] = connection.execute(
            "SELECT knowledge_id, validity_interval, permitted_uses, assurance_level FROM knowledge").fetchall()
        assert row == ("k1", None, None, None)
        names = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")]
        assert "knowledge_mission_idx" in names
    finally:
        connection.close()


# ------------------------------------------------------------------- the writer
from agent_orchestrator.testing.product_world import product_world  # noqa: E402
from agent_orchestrator.testing.scripted_replies import (  # noqa: E402
    LayeredScriptedProvider,
    review_input,
    review_reply,
    worker_reply,
)


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


def confirming(pick: Any) -> Any:
    """The reviewer: content reviews also carry ``claims`` built by ``pick(data)``."""

    def reviewer(request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        if str((data.get("package") or {}).get("purpose")) != "TASK_CONTENT":
            return review_reply(data)
        body = json.loads(review_reply(data))
        body["claims"] = pick(data)
        return json.dumps(body, ensure_ascii=False)

    return reviewer


def first_claim_by_artifact(data: dict[str, Any]) -> list[dict[str, Any]]:
    listed = data["package"]["claims_to_confirm"]
    return [{"claim_id": listed[0]["claim_id"], "confirmed": True,
             "evidence_ids": labels(data, "artifact")[:1], "reason": "文件确实写出，内容与结论一致。"}]


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_review_confirmed_knowledge_carries_its_registrations_and_the_tools_show_them(tmp_path):
    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(
                worker=two_claims, reviewer=confirming(first_claim_by_artifact))) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "kn-registration"})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert mission.status.value == "COMPLETED", mission.final_report
            store = world.store
            [record] = store.list_knowledge(mission_id)
            assert record.verifier["basis"] == "review_confirmed"
            # 保障等级＝任务的保证通道；有效期＝验收用的使用证书的签发/失效时刻与任务纪元
            assert record.assurance_level == "ASSURANCE_1_1"
            assert set(record.validity_interval) == {"valid_from_ms", "valid_until_ms", "mission_epoch"}
            assert type(record.validity_interval["valid_from_ms"]) is int
            assert type(record.validity_interval["mission_epoch"]) is int
            # 允许用途没有记录来源：None，不猜
            assert record.permitted_uses is None
            # 三列与 JSON 同一次写入
            [row] = store.connection.execute(
                "SELECT validity_interval, permitted_uses, assurance_level FROM knowledge WHERE mission_id=?",
                (mission_id,)).fetchall()
            assert json.loads(row[0]) == dict(record.validity_interval)
            assert row[1] is None and row[2] == "ASSURANCE_1_1"
            # 读工具展示：目录与原文都带这三项
            listing = read_knowledge_tool(store, mission_id, "knowledge_list", {"limit": 5})
            [entry] = [item for item in listing["items"] if item["layer"] == "verified"]
            assert entry["assurance_level"] == "ASSURANCE_1_1" and entry["permitted_uses"] is None
            assert entry["validity_interval"] == dict(record.validity_interval)
            from agent_orchestrator.orchestrator.assurance_point_use import knowledge_handover

            text = read_knowledge_tool(store, mission_id, "knowledge_read", {"id": record.id},
                                       handover=knowledge_handover(world.loop.commit, mission_id=mission_id,
                                                                   consumer_id="registration-read",
                                                                   task_id=record.source_task))
            assert text["assurance_level"] == "ASSURANCE_1_1"
            assert text["validity_interval"] == dict(record.validity_interval)

    asyncio.run(case())
