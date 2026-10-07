# SPDX-License-Identifier: Apache-2.0
"""披露经使用证书（推后第 2 批 A03；原计划 ASSURANCE-EXEC-1.1 附录 C.1 第 488、496 行，§8.4）。

把材料交给人或审阅模型的四处——原生下载、审阅员初始材料、审阅员取证读、审阅员读黑板——都在交出前
过同一个签发方（``assurance_point_use.certify_point_use_locked``，用途 DISCLOSE）：对象精确存在、
授权当前、根实例、时钟可信。交出就留一张 DISCLOSE 证书；签不出就不交，原因具名。

**改坏检验**（记录见车道 Q2 记录）：签发方不核时钟 / 原生下载不问签发方 / 交接不签 / 取证读不签 /
读黑板不签 → 对应用例变红。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.api.facade import FacadeError, MissionControlV1
from agent_orchestrator.assurance.certificates import POINT_PURPOSES
from agent_orchestrator.assurance.codec import decode
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.observability.business_replay import CONSISTENT, verify_mission
from agent_orchestrator.orchestrator.assurance_point_use import (
    NATIVE_READ_CONSUMER,
    REVIEW_INTENT_CONSUMER,
    TOOL_CALL_CONSUMER,
    EvidenceClaim,
)
from agent_orchestrator.orchestrator.assurance_recheck import live_usable_certificates
from agent_orchestrator.testing.product_world import DEFAULT_TOOLS, TENANT, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, review_input, review_reply


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _disclosures(store: Any, mission_id: str, consumer_kind: str) -> list[Any]:
    return store.connection.execute(
        "SELECT * FROM assurance_use_certificates WHERE mission_id=? AND purpose='DISCLOSE' "
        "AND consumer_kind=? ORDER BY rowid", (mission_id, consumer_kind)).fetchall()


def _evidence(store: Any, certificate_id: str) -> list[dict[str, Any]]:
    [row] = store.connection.execute(
        "SELECT receipt_json FROM commit_receipts WHERE kind='AssuranceUseCertified' AND subject_id=?",
        (certificate_id,)).fetchall()
    return json.loads(row[0])["evidence"]


def _object_kinds(row: Any) -> set[str]:
    body = decode(row["certificate_json"])
    assert body["decision"] == "USABLE" and body["purpose"] == "DISCLOSE"
    return {decode(item["key"])["kind"] for item in body["read_set"] if item["channel"] == "OBJECT"}


def _roll_back_clock(store: Any) -> None:
    with store.transaction():
        # 本机时钟回拨：高水位在"现在"之后，状态 ROLLBACK、代次加一
        store.connection.execute(
            "UPDATE assurance_environment_state SET clock_state='ROLLBACK', "
            "clock_generation=clock_generation+1, wall_high_ms=MAX(wall_high_ms, ?), "
            "row_version=row_version+1 WHERE singleton=1", (int(store.now * 1000) + 10 ** 9,))


async def _complete(world: Any, key: str) -> str:
    mission_id = world.create({"goal": "写两份笔记", "idempotency_key": key,
                               "success_criteria": ["file:notes/a.md", "file:notes/b.md"]})["mission_id"]
    mission = await world.run_until_settled(mission_id, rounds=40)
    assert str(mission.status.value) == "COMPLETED", mission.final_report
    return mission_id


# ------------------------------------------------------------------- 产品同形（默认路径）
def test_review_disclosure_and_native_download_carry_a_certificate_on_the_default_path(tmp_path):
    """默认路径：每个发给审阅模型的请求，交出前都签了 DISCLOSE 证书（消费方是那次派发意图），读集钉着
    审阅包与审阅对象；用户在界面下载一个产物，也签一张（消费方是本人这次读）。时点证书不进有效性观察；
    它们是使用回执，按事件重建这件事仍一致（重启时不会因此被隔离）。"""

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            store = world.store
            mission_id = await _complete(world, "disclose-default")

            sent = [row[0] for row in store.connection.execute(
                "SELECT dispatch_intent_id FROM assurance_review_invocations WHERE mission_id=?", (mission_id,))]
            assert sent
            certified = {row["consumer_id"]: row for row in _disclosures(store, mission_id, REVIEW_INTENT_CONSUMER)}
            for intent_id in sent:
                assert intent_id in certified, intent_id
                assert {"review_package"} <= _object_kinds(certified[intent_id])

            [artifact, *_] = [row for row in store.connection.execute(
                "SELECT artifact_id, content_hash FROM artifacts WHERE mission_id=?", (mission_id,))]
            read = world.control.artifact_read(artifact["artifact_id"])
            assert read["content_hash"] == artifact["content_hash"] and read["content"]
            [native] = _disclosures(store, mission_id, NATIVE_READ_CONSUMER)
            assert "artifact" in _object_kinds(native)
            [claim] = _evidence(store, native["certificate_id"])
            assert claim["kind"] == "exact" and json.loads(claim["id"])["pin"]["id"] == artifact["artifact_id"]

            live = live_usable_certificates(store.connection, mission_id, limit=4096)
            assert "DISCLOSE" in POINT_PURPOSES
            assert not [row for row in live if row["purpose"] == "DISCLOSE"]
            # 证书是使用回执，没有领域事件也不算静默改动：重启时按事件重建这件事仍一致
            replay = verify_mission(store, mission_id)
            assert replay["status"] == CONSISTENT, replay["silent_changes"]

    asyncio.run(case())


def test_native_download_is_refused_without_a_trusted_clock_or_for_another_caller(tmp_path):
    """原生下载签不出 DISCLOSE 证书就不给正文：时钟不可信（§8.4）；调用者不是这个部署的主体。"""

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            store = world.store
            mission_id = await _complete(world, "disclose-native-refused")
            [artifact_id, *_] = [row[0] for row in store.connection.execute(
                "SELECT artifact_id FROM artifacts WHERE mission_id=?", (mission_id,))]

            stranger = MissionControlV1(world.loop, tenant_id=TENANT, principal=Principal("someone-else"))
            with pytest.raises(FacadeError) as other:
                stranger.artifact_read(artifact_id)
            assert other.value.code == "USE_EVIDENCE_NOT_CURRENT"
            assert "ROOT_READ_NOT_AUTHORIZED" in str(other.value)

            _roll_back_clock(store)
            with pytest.raises(FacadeError) as clock:
                world.control.artifact_read(artifact_id)
            assert clock.value.code == "USE_EVIDENCE_NOT_CURRENT"
            assert "TIME_DISCONTINUITY" in str(clock.value)
            assert _disclosures(store, mission_id, NATIVE_READ_CONSUMER) == []

    asyncio.run(case())


# --------------------------------------------------------------------- 审阅员自己取
def _tool_results(request: Any) -> list[str]:
    return [str(message.content) for message in request.messages if "tool" in str(message.role).lower()]


def test_a_reviewer_reading_evidence_goes_through_the_same_issuer(tmp_path):
    """内容审阅员先列证据、再读一条：读那一次签 DISCLOSE 证书（消费方是这次工具调用），证据就是读的
    那个精确对象；审阅照常给结论，任务完成。"""
    read: list[dict[str, Any]] = []

    def reviewer(request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        if str((data.get("package") or {}).get("purpose")) != "TASK_CONTENT" or read:
            return review_reply(data)
        results = _tool_results(request)
        if not results:
            return ("assurance_find_evidence", {})
        if len(results) == 1:
            entries = json.loads(results[0])["value"]["entries"]
            return ("assurance_read_evidence", {"label": entries[0]["label"]})
        read.append(json.loads(results[1])["value"])
        return review_reply(data)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(reviewer=reviewer)) as world:
            store = world.store
            mission_id = await _complete(world, "disclose-evidence-read")
            assert read and read[0]["content"] is not None, read
            tool_reads = _disclosures(store, mission_id, TOOL_CALL_CONSUMER)
            assert len(tool_reads) == 1
            [claim] = _evidence(store, tool_reads[0]["certificate_id"])
            assert claim["kind"] == "exact" and json.loads(claim["id"]) == read[0]["ref"]

    asyncio.run(case())


def test_a_reviewer_reading_the_blackboard_is_a_disclosure(tmp_path, monkeypatch):
    """审阅员读黑板算披露（推后第 1 批裁决第 5 件第 5 点）：与执行者同一个签发方，用途 DISCLOSE，
    消费方是这次工具调用；知识当前 → 交出并留证书；知识过时、时钟不可信 → 不交，原因具名。"""
    import agent_orchestrator.memory.knowledge_standing as standing
    from test_point_use_certificates import _knowledge, _run, _scenario_provider
    from test_repair_staleness import KNOWLEDGE_TOOLS

    state: dict[str, Any] = {"methods": []}

    async def case():
        async with product_world(tmp_path / "root", _scenario_provider(state),
                                 allowed_tools=DEFAULT_TOOLS + KNOWLEDGE_TOOLS) as world:
            store = world.store
            mission_id = await _run(world, "disclose-blackboard")
            current, _ = _knowledge(store, mission_id)
            [review_key] = [row[0] for row in store.connection.execute(
                "SELECT review_key FROM assurance_review_bindings WHERE mission_id=? "
                "AND json_extract(binding_json,'$.subject.purpose')='TASK_CONTENT' ORDER BY rowid LIMIT 1",
                (mission_id,))]
            handover = world.loop._knowledge_handover(
                mission_id, {"attempt_id": None, "call_id": "review-run:call-1", "review_key": review_key})
            claim = EvidenceClaim.knowledge(current.id, current.version)
            assert handover(claim) == ()
            [row] = _disclosures(store, mission_id, TOOL_CALL_CONSUMER)
            assert row["consumer_id"] == "review-run:call-1"
            assert _evidence(store, row["certificate_id"]) == [claim.to_json()]

            with monkeypatch.context() as patch:
                patch.setattr(standing, "acceptance_is_current", lambda *args: (False, "test_gone"))
                assert handover(claim) == (f"knowledge:{current.id}@{current.version}:STALE:test_gone",)
            _roll_back_clock(store)
            assert handover(claim) == ("TIME_DISCONTINUITY",)
            assert len(_disclosures(store, mission_id, TOOL_CALL_CONSUMER)) == 1

    asyncio.run(case())
