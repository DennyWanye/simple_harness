# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""第 1 批车道 B，A04：``use_check`` 诊断只读——不写、不清共用候选缓存。

此前 ``AssuranceApi.use_check`` 走 ``prepare_accept_use_for_result``（把候选记进共用缓存，顶掉同一条
记录的真实候选）再 ``forget``（把它清掉）。一次诊断能让正在进行的真实验收 / 根结论提交丢掉候选。
原计划 ``schema-source-rules.json``："use_check diagnostic only, not a certificate issuer"。
"""
from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_a04_use_check_leaves_the_shared_candidate_cache_untouched(tmp_path):
    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            store = world.store
            mission_id = world.create({"goal": "写一份 NOTES.md，列出三条要点。", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "a04"})["mission_id"]
            done = await world.run_until_settled(mission_id, timeout=60)
            assert str(done.status.value) == "COMPLETED", done.final_report
            validity = world.loop.commit._assurance_validity
            [result_id] = [r[0] for r in store.connection.execute(
                "SELECT result_id FROM results WHERE mission_id=?", (mission_id,))]
            # 真实路径准备的候选（验收 / 沿用都从这里拿）
            real = validity.prepare_accept_use_for_result(mission_id, result_id)
            record_id = str(real.record.record_id)
            assert validity.candidate_for(mission_id, record_id) is real
            locked_before = dict(validity._locked)
            certificates_before = store.connection.execute(
                "SELECT COUNT(*) FROM assurance_use_certificates WHERE mission_id=?", (mission_id,)).fetchone()[0]

            use = world.control.assurance_use_check({
                "schema_version": 1, "request_id": "r-a04", "mission_id": mission_id,
                "subject_ref": {"kind": "result", "pin": {"id": result_id, "revision": 0, "content_hash": "0" * 64}},
                "view": "CURRENT", "at_event_seq": None})
            assert use["decision"] == "USABLE" and use["diagnostic_only"] is True and use["certificate_ref"] is None

            # 诊断之后：真实候选还在、还是同一个对象；锁表没动；没有多出证书
            assert validity.candidate_for(mission_id, record_id) is real
            assert dict(validity._locked) == locked_before
            assert store.connection.execute(
                "SELECT COUNT(*) FROM assurance_use_certificates WHERE mission_id=?", (mission_id,)
            ).fetchone()[0] == certificates_before

            # 根目标的诊断同样只读
            [root_task] = [r[0] for r in store.connection.execute(
                "SELECT goal_task_id FROM goal_resolutions WHERE mission_id=? AND adopted=1", (mission_id,))]
            cached = dict(validity._candidates)
            root_use = world.control.assurance_use_check({
                "schema_version": 1, "request_id": "r-a04-root", "mission_id": mission_id,
                "subject_ref": {"kind": "task", "pin": {"id": root_task, "revision": 0, "content_hash": "0" * 64}},
                "view": "CURRENT", "at_event_seq": None})
            assert root_use["diagnostic_only"] is True and root_use["decision"] in {"USABLE", "RECHECK_REQUIRED"}
            assert dict(validity._candidates) == cached
    asyncio.run(case())
