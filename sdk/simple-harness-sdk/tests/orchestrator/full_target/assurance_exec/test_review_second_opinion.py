# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""审阅判不下来：换新会话复审一次，仍判不下来交给人（2026-09-30，完成度评估第 5 项）。

此前保证通道下审阅员回"判不下来"（INCONCLUSIVE）时，取结论那一步直接报错，校验按"没通过"
让执行者重做——活可能本来就对，三次后按反复失败处理。现在：
* 第 1 次回复判不下来 → 不导入为正式记录，按现有"第二次调用"的路开一次复审（新 Agent 会话、
  同一冻结请求、不带第一次的结论）；第 2 次的结论照常导入。
* 复审仍判不下来 → 结论标"需要人定"，交给现成的挂起 → 复核审批 → 用户点通过/不通过。
* 用户复核时写裁决回执；准备通过证书时读它，"判不下来 + 用户通过"判为可用。

走 AssuredRuntime：真实存储 / 提交 / Agent 运行时 / 审阅消费者 / 官方导入器；只有模型回复是脚本。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from agent_orchestrator.contracts.resolution import ReviewVerdict
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.verification.human_review import review_request_id

SDK_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(SDK_ROOT / "scripts/assurance_seams"))

ACCEPT_REPLY = {"schema_version": 2, "verdict": "ACCEPT", "assessments": [
    {"criterion_id": "criterion-report", "verdict": "PASS", "evidence_ids": [], "reason": "fixture",
     "limitations": []}], "findings": []}
INCONCLUSIVE_REPLY = {"schema_version": 2, "verdict": "INCONCLUSIVE", "assessments": [
    {"criterion_id": "criterion-report", "verdict": "UNKNOWN", "evidence_ids": [],
     "reason": "the cited line is not in the disclosed evidence", "limitations": ["evidence insufficient"]}],
    "findings": []}


def _invocations(rt):
    return [(r["ordinal"], r["reason"], r["dispatch_intent_id"]) for r in rt.store.connection.execute(
        "SELECT ordinal, json_extract(invocation_json,'$.reason') AS reason, dispatch_intent_id "
        "FROM assurance_review_invocations WHERE mission_id=? ORDER BY ordinal", (rt.mission.id,)).fetchall()]


def _official_verdicts(rt):
    return [row[0] for row in rt.store.connection.execute(
        "SELECT verdict FROM review_records WHERE official=1").fetchall()]


def _human_reviews(rt, record, verdict, note):
    """The production path after a NEEDS_HUMAN critic layer: suspend → the person decides."""
    result_id = rt.stored.envelope.id
    rt.record_critic_layer(record, status="NEEDS_HUMAN")
    rt.commit.suspend_verification(result_id, owner=None, reason="needs_human",
                                   layers=[{"layer": "critic_review", "status": "NEEDS_HUMAN"}])
    request, _receipt = rt.commit.review_result(
        review_request_id(result_id), principal=Principal("user-1"), verdict=verdict, note=note, nonce="n-1")
    assert request["state"] == ("GRANTED" if verdict == "pass" else "REJECTED")
    rt.commit.record_verification_layer(result_id, layer="human_review",
                                        status="PASS" if verdict == "pass" else "FAIL",
                                        detail={"summary": note, "verifier_version": "human"})


def test_an_inconclusive_first_review_gets_one_independent_second_opinion(tmp_path):
    async def case():
        from _assured_fixture import AssuredRuntime
        async with AssuredRuntime(tmp_path, [INCONCLUSIVE_REPLY, ACCEPT_REPLY]) as rt:
            verdict, record = await asyncio.wait_for(rt.run_critic(), 30)
            assert verdict.passed and not verdict.needs_human
            assert rt.provider.calls == 2
            rows = _invocations(rt)
            assert [(o, r) for o, r, _ in rows] == [(1, "INITIAL"), (2, "SECOND_OPINION")]
            intents = [rt.store.get_intent(i) for _, _, i in rows]
            assert intents[0].agent_id != intents[1].agent_id  # a new session, not the same reviewer
            assert intents[0].config["message"] == intents[1].config["message"]  # no first verdict leaks in
            assert _official_verdicts(rt) == ["ACCEPT"] and record.verdict is ReviewVerdict.ACCEPT
            assert rt.store.get_receipt("assurance-review-second-opinion:" + intents[0].intent_id) is not None
            rt.record_critic_layer(record)
            rt.settle_fixture_worker()
            assert rt.accept_now().accepted_result_id == rt.stored.envelope.id
    asyncio.run(case())


def test_a_conclusive_first_review_is_not_second_guessed(tmp_path):
    async def case():
        from _assured_fixture import AssuredRuntime
        async with AssuredRuntime(tmp_path, [ACCEPT_REPLY, ACCEPT_REPLY]) as rt:
            verdict, _record = await asyncio.wait_for(rt.run_critic(), 30)
            assert verdict.passed and rt.provider.calls == 1
            assert [(o, r) for o, r, _ in _invocations(rt)] == [(1, "INITIAL")]
    asyncio.run(case())


def test_two_inconclusive_reviews_go_to_the_person_and_a_pass_licenses_acceptance(tmp_path):
    async def case():
        from _assured_fixture import AssuredRuntime
        async with AssuredRuntime(tmp_path, [INCONCLUSIVE_REPLY, INCONCLUSIVE_REPLY]) as rt:
            verdict, record = await asyncio.wait_for(rt.run_critic(), 30)
            assert verdict.passed and verdict.needs_human, verdict  # not a PASS, not a FAIL: a person decides
            assert rt.provider.calls == 2 and _official_verdicts(rt) == ["INCONCLUSIVE"]
            _human_reviews(rt, record, "pass", "看过了，引用是对的")
            receipt = rt.store.get_receipt("assurance-review-adjudicated:" + str(record.record_id))
            assert receipt is not None and receipt["decision"] == "pass" and receipt["principal_id"] == "user-1"
            candidate = rt.validity.prepare_accept_use(record)
            assert candidate.certificate.decision == "USABLE"
            assert any(r.startswith("human_adjudication:") for r in candidate.certificate.reasons)
            rt.settle_fixture_worker()
            assert rt.accept_now().accepted_result_id == rt.stored.envelope.id
            # …and the step now counts as done for its parent (real run 3, 2026-09-30:
            # the root kept "waiting_children" and the Mission died no_dispatchable_work)
            from agent_orchestrator.orchestrator.completion_status import read_occurrence_completion
            occurrence = rt.store.connection.execute(
                "SELECT occurrence_id FROM operation_completion_scopes WHERE mission_id=?",
                (rt.mission.id,)).fetchone()[0]
            with rt.store.read_view():
                status = read_occurrence_completion(rt.store, rt.mission.id, occurrence)
            assert status.content_ready, status
            # …and its acceptance is a usable support for the parent's resolution
            from agent_orchestrator.orchestrator.assurance_validity import acceptance_id_for
            from agent_orchestrator.orchestrator.completion_support import read_completion_support
            with rt.store.read_view():
                support = read_completion_support(
                    rt.store, rt.mission.id, acceptance_id_for(rt.task.id, rt.stored.envelope.id))
            assert support.record.record_id == record.record_id
    asyncio.run(case())


def test_a_human_fail_after_two_inconclusive_reviews_does_not_license_acceptance(tmp_path):
    async def case():
        from _assured_fixture import AssuredRuntime
        async with AssuredRuntime(tmp_path, [INCONCLUSIVE_REPLY, INCONCLUSIVE_REPLY]) as rt:
            verdict, record = await asyncio.wait_for(rt.run_critic(), 30)
            assert verdict.needs_human
            _human_reviews(rt, record, "fail", "引用的那句资料里没有")
            candidate = rt.validity.prepare_accept_use(record)
            assert candidate.certificate.decision != "USABLE"
            rt.settle_fixture_worker()
            with pytest.raises(Exception):
                rt.accept_now()
    asyncio.run(case())
