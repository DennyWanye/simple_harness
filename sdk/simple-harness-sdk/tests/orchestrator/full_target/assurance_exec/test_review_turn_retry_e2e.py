# SPDX-License-Identifier: Apache-2.0
"""端到端：保证审查第一次调用"这一轮没提交"（TURN_FAILED）→ 唯一一次第二次调用（TURN_RETRY，原样
重发冻结请求，不带 format_feedback）；第二次正常回答则正式导入，第二次也失败则写
AssuranceReviewFormatExhausted（REVIEW_TURN_RETRY_EXHAUSTED），不会有第三次调用。
（2026-10-03 迁到产品同形世界。）

让这一轮失败的外界情形：
* 模型回了空答复（只有思考没有正文）：确定性失败；
* 模型服务端错误（503，2026-09-25 UI 全量点击的真实触发）：失败的那次调用没有用量，按记账硬规则
  （多算不少算、不冻结）它的预留按上限计入；
* 调用在交接之后断了（结果不明）：原调用的预留按上限计入；这一步由系统原样重做，不交给规划器。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _review_world import ReviewScript, quick_waits, reviewed_mission  # noqa: E402

from agent_orchestrator.assurance.codec import decode  # noqa: E402
from simple_harness.providers.errors import ProviderServerError, ProviderTransportError  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    quick_waits(monkeypatch)


def _content_invocations(case):
    return case.store.connection.execute(
        "SELECT ordinal, json_extract(invocation_json,'$.reason') AS reason, dispatch_intent_id "
        "FROM assurance_review_invocations WHERE mission_id=? AND review_key LIKE 'assurance-content:%' "
        "ORDER BY ordinal", (case.mission_id,)).fetchall()


def _content_classes(case):
    rows = [json.loads(row[0]) for row in case.store.connection.execute(
        "SELECT receipt_json FROM commit_receipts WHERE kind IN "
        "('AssuranceReviewClassified','AssuranceReviewFormatRejected') ORDER BY rowid").fetchall()]
    return [row for row in rows if str(row.get("review_key", "")).startswith("assurance-content:")] or rows


def _assert_retried_then_imported(case, provider):
    assert provider.review_calls["TASK_CONTENT"] == 2
    rows = _content_invocations(case)
    assert [(r["ordinal"], r["reason"]) for r in rows] == [(1, "INITIAL"), (2, "TURN_RETRY")]
    failed = [c for c in _content_classes(case) if c["classification"] == "TURN_FAILED"]
    assert [(c["invocation_ordinal"], c["error_code"]) for c in failed] == [(1, "REVIEW_TURN_NOT_COMMITTED")]
    # 第二次原样重发冻结的请求：审查输入里的 format_feedback 仍是空的（格式修复才会填）。
    first, second = (case.store.get_intent(r["dispatch_intent_id"]) for r in rows)
    assert first.intent_id != second.intent_id
    assert first.config["message"] == second.config["message"]
    assert decode(second.config["message"]["content"])["format_feedback"] == ""
    assert case.store.connection.execute(
        "SELECT count(*) FROM commit_receipts WHERE kind='AssuranceReviewFormatExhausted'").fetchone()[0] == 0
    return first


def test_failed_first_turn_is_retried_once_and_imported(tmp_path):
    provider = ReviewScript(verdicts={"TASK_CONTENT": ["EMPTY"]})

    async def run() -> None:
        async with reviewed_mission(tmp_path, provider) as case:
            mission = await case.settle()
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            _assert_retried_then_imported(case, provider)
            assert len(case.events("AcceptanceCommitted")) == 1

    asyncio.run(run())


def test_failed_second_turn_is_exhausted_without_a_third_call(tmp_path):
    provider = ReviewScript(verdicts={"TASK_CONTENT": ["EMPTY", "EMPTY", "ACCEPT"]})

    async def run() -> None:
        async with reviewed_mission(tmp_path, provider) as case:
            await case.run_until(provider.repair_asked.is_set)
            assert provider.review_calls["TASK_CONTENT"] == 2  # 第三条脚本回复只有第三次调用才会用到
            rows = _content_invocations(case)
            assert [(r["ordinal"], r["reason"]) for r in rows] == [(1, "INITIAL"), (2, "TURN_RETRY")]
            [exhausted] = case.store.connection.execute(
                "SELECT receipt_json FROM commit_receipts WHERE kind='AssuranceReviewFormatExhausted'").fetchall()
            assert json.loads(exhausted[0])["reason"] == "REVIEW_TURN_RETRY_EXHAUSTED"
            assert not case.events("AcceptanceCommitted")
            # 这一步按验证失败交给规划器（事实如实），不再有第三次审阅调用。
            [entry] = provider.repair_packages[0]["repair_requests"]
            assert entry["request"]["context"]["event_type"] == "VerificationFailed"

    asyncio.run(run())


def test_provider_server_error_first_turn_is_retried_once_and_imported(tmp_path):
    """外加 C08：迟到 / 重复的记账与审阅结论、生命周期互不影响。"""

    from agent_orchestrator.governance.budgets import UsageFact
    from agent_orchestrator.orchestrator.accounting_recovery import import_late_accounting

    provider = ReviewScript(failures={"TASK_CONTENT": [ProviderServerError(status_code=503)]})

    async def run() -> None:
        async with reviewed_mission(tmp_path, provider) as case:
            mission = await case.settle()
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            first = _assert_retried_then_imported(case, provider)
            # 记账硬规则：503 那次调用没有用量事实，它的预留按上限计入，而不是当作 0 释放。
            counted = [e.payload for e in case.events("ReservationCountedAtUpperBound")]
            assert any(first.subject_id in json.dumps(item) for item in counted), counted

            commit, store = case.world.loop.commit, case.store
            usage = [tuple(r) for r in store.connection.execute(
                "SELECT usage_ref, subject_id, input_tokens, output_tokens, unknown FROM imported_usage "
                "WHERE mission_id=? ORDER BY usage_ref", (case.mission_id,))]
            records = store.connection.execute("SELECT count(*) FROM review_records WHERE official=1").fetchone()[0]
            lifecycle = (str(case.mission().status), sorted((t.id, str(t.status)) for t in store.list_tasks(case.mission_id)))
            # 同一批用量事实迟到 / 重复导入：一条都不重记；离线补账扫描也不复活任何东西。
            for usage_ref, subject, tokens_in, tokens_out, unknown in usage:
                assert commit.import_usage(subject, case.mission_id, (
                    UsageFact(usage_ref, tokens_in, tokens_out, unknown=bool(unknown)),)) == 0
            for _ in range(2):
                import_late_accounting(case.world.loop)
            assert [tuple(r) for r in store.connection.execute(
                "SELECT usage_ref, subject_id, input_tokens, output_tokens, unknown FROM imported_usage "
                "WHERE mission_id=? ORDER BY usage_ref", (case.mission_id,))] == usage
            assert store.connection.execute("SELECT count(*) FROM review_records WHERE official=1").fetchone()[0] == records
            assert (str(case.mission().status), sorted((t.id, str(t.status)) for t in store.list_tasks(case.mission_id))) == lifecycle

    asyncio.run(run())


def test_an_interrupted_review_is_redone_by_the_system_and_its_hold_is_counted(tmp_path):
    """2026-09-28 真机：审阅员那次调用在交接之后断了，结果不明。原调用不能当作没发生：它的预留按
    上限计入；这一步由系统原样重做（不交给规划器，规划器一次修复都没被问到）。"""

    provider = ReviewScript(failures={"TASK_CONTENT": [
        ProviderTransportError(public_message="scripted transport loss after handoff")]})

    async def run() -> None:
        async with reviewed_mission(tmp_path, provider, stall_seconds=3.0) as case:
            mission = await case.settle()
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            assert not provider.repair_packages  # 没有交给规划器
            [first, *_] = _content_invocations(case)
            interrupted = case.store.get_intent(first["dispatch_intent_id"])
            counted = [e.payload for e in case.events("ReservationCountedAtUpperBound")]
            assert any(interrupted.subject_id in json.dumps(item) for item in counted), counted
            assert len(case.events("AcceptanceCommitted")) == 1

    asyncio.run(run())
