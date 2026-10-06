# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""第 4 批车道 M（V05）：Assurance 原计划改坏清单里没有现成用例可绑的两条，在这里补用例。

* F-M12（drop resolved schema hashes）：查询集读数的指纹要含库结构（迁移表）哈希——迁移变了，
  同一份行集合也不是同一次读；把哈希丢掉，证书就比不出库结构漂移。
* M02（fake_official：信模型自报的"正式"、不核派发）：导入正式审阅时，回合回执里的审阅员会话、
  回合号、原始输出哈希都必须和派发记录一致；派发记录上的会话换成别人，这一回合就不是派出去的
  那次，不能成为正式审阅（``REVIEW_TURN_SOURCE_MISMATCH``）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from agent_orchestrator.assurance.codec import AssuranceError
from agent_orchestrator.orchestrator.assurance_review_consumer import ref_from_event
from agent_orchestrator.orchestrator.assurance_review_import import read_imported_review_locked
from agent_orchestrator.storage.assurance_reads import AssuranceReader, CompleteRead, EpochSnapshot

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review_world import ReviewScript, reviewed_mission  # noqa: E402


# --------------------------------------------------------------------------- F-M12
def test_fm12_a_query_set_read_item_changes_with_the_schema_hash() -> None:
    """同一条查询、同一份行集合，库结构哈希不同就是两次不同的读。"""
    epochs = EpochSnapshot(mission=1, environment=1, clock_generation=1, wall_high_ms=0, clock_state="STABLE")
    rows = ('{"a":1}',)
    one = CompleteRead("query-1", "a" * 64, epochs, rows, "c" * 64)
    same = CompleteRead("query-1", "a" * 64, epochs, rows, "c" * 64)
    other_schema = CompleteRead("query-1", "b" * 64, epochs, rows, "c" * 64)
    assert one.read_item.channel == "QUERY_SET" and one.read_item.key == other_schema.read_item.key
    assert one.read_item.fingerprint == same.read_item.fingerprint
    assert one.read_item.fingerprint != other_schema.read_item.fingerprint


# --------------------------------------------------------------------------- M02
@pytest.mark.replay_audit_exempt("用例故意把派发记录上的审阅员会话改成别人，造'回合不是派出去的那次'的局面")
def test_m02_a_review_turn_from_another_session_than_dispatched_is_not_official(tmp_path):
    """正式审阅的来源是派发记录，不是模型回合自己说了算。"""

    async def body() -> None:
        async with reviewed_mission(tmp_path, ReviewScript()) as case:
            mission = await case.settle()
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            store = case.store
            row = store.connection.execute(
                "SELECT review_key FROM assurance_review_bindings WHERE mission_id=? "
                "AND review_key LIKE 'assurance-content:%'", (case.mission_id,)).fetchone()
            assert row is not None
            intent_id = store.connection.execute(
                "SELECT dispatch_intent_id FROM assurance_review_invocations WHERE review_key=? AND ordinal=1",
                (row["review_key"],)).fetchone()[0]
            commit_id = store.connection.execute(
                "SELECT commit_id FROM commit_receipts WHERE kind='AssuranceReviewClassified' AND subject_id=?",
                (intent_id,)).fetchone()[0]
            [event] = [e for e in case.events("AssuranceReviewClassified")
                       if ((e.payload or {}).get("classification_receipt_ref") or {}).get("pin", {}).get("id")
                       == commit_id]
            ref = ref_from_event(event)
            tenant = store.connection.execute(
                "SELECT tenant_id FROM missions WHERE mission_id=?", (case.mission_id,)).fetchone()[0]
            reader = AssuranceReader(store, tenant_id=tenant, mission_id=case.mission_id)
            commit = case.world.loop.commit

            # 没动过：这一回合读得出来，就是派出去的那次。
            with store.transaction():
                imported = read_imported_review_locked(commit, reader, ref)
            assert imported.classification.ref == ref
            dispatched = store.get_intent(intent_id)
            assert dispatched is not None and dispatched.agent_id

            # 派发记录上的会话换成别人：回合回执仍写着原来的会话，两边对不上，不是正式审阅。
            with store.transaction():
                store.connection.execute(
                    "UPDATE dispatch_intents SET agent_id=? WHERE intent_id=?", ("someone-else", intent_id))
                with pytest.raises(AssuranceError) as refused:
                    read_imported_review_locked(commit, reader, ref)
                assert refused.value.code == "REVIEW_TURN_SOURCE_MISMATCH"

    asyncio.run(body())
