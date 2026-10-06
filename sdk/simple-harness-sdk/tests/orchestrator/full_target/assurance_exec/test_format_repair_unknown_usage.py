# SPDX-License-Identifier: Apache-2.0
"""端到端：审阅员第一次回了一段不是 JSON 的话（格式错），而且那次调用中转站没报用量（用量说不清）。

真机见过的两件外界的事叠在一起（第 2 批车道 S，独立复核 7.3 第 1 条）。用户硬规则（2026-09-24）：
用量可以多算、不可以少算，但不冻结。所以与"这一轮没提交"（TURN_FAILED）同一口径：

* 第一次调用的预留按上限挂着计数（账上可见），第二次调用（格式修复）用它自己的预留开出来；
* 审阅最终形成结论、任务走完；收尾时第一次调用那笔按上限计入（不当作 0 释放）。

以前格式修复要求先把第一次调用结清；用量说不清就结不清，而用量说不清的调用要到收尾才按上限
结清，收尾又要等这次审阅——成环，审阅永远卡在预算等待（BUDGET_WAIT）。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _review_world import ReviewScript, quick_waits, reviewed_mission  # noqa: E402

from agent_orchestrator.testing.scripted_replies import review_input  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    quick_waits(monkeypatch)


class _ProseWithoutUsage(ReviewScript):
    """内容审查第一次：回一段不是 JSON 的话，并且这次调用没有用量。"""

    def __init__(self) -> None:
        super().__init__(verdicts={"TASK_CONTENT": ["PROSE"]})

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        response = await super().invoke(request, cancel=cancel)
        data = review_input(request)
        if data is not None and (data.get("package") or {}).get("purpose") == "TASK_CONTENT" \
                and self.review_calls.get("TASK_CONTENT") == 1:
            response = dataclasses.replace(response, usage=None)
        return response


def _content_invocations(case):
    return case.store.connection.execute(
        "SELECT ordinal, json_extract(invocation_json,'$.reason') AS reason, dispatch_intent_id "
        "FROM assurance_review_invocations WHERE mission_id=? AND review_key LIKE 'assurance-content:%' "
        "ORDER BY ordinal", (case.mission_id,)).fetchall()


def _review_waits(case):
    return [tuple(row) for row in case.store.connection.execute(
        "SELECT work_key,state,wait_reason FROM assurance_pending_work WHERE mission_id=? AND consumer='REVIEW'",
        (case.mission_id,))]


def test_malformed_reply_with_unknown_usage_is_repaired_and_counted_at_upper_bound(tmp_path):
    provider = _ProseWithoutUsage()

    async def run() -> None:
        async with reviewed_mission(tmp_path, provider) as case:
            store, ledger = case.store, case.world.loop.commit.ledger
            try:
                await case.run_until(lambda: len(_content_invocations(case)) >= 2, timeout=40)
            except TimeoutError:
                pytest.fail(f"格式修复没有开出来（审阅卡住）：{_review_waits(case)} "
                            f"调用={[tuple(r) for r in _content_invocations(case)]}")
            first_row, second_row = _content_invocations(case)[:2]
            assert (first_row["ordinal"], first_row["reason"]) == (1, "INITIAL")
            assert (second_row["ordinal"], second_row["reason"]) == (2, "FORMAT_REPAIR")
            first = store.get_intent(first_row["dispatch_intent_id"])
            second = store.get_intent(second_row["dispatch_intent_id"])
            assert first.subject_id != second.subject_id
            # 第一次调用：格式错，且用量说不清（如实记成不明）
            [rejected] = [json.loads(r[0]) for r in store.connection.execute(
                "SELECT receipt_json FROM commit_receipts WHERE kind='AssuranceReviewFormatRejected'")]
            assert rejected["classification"] == "FORMAT_INVALID" and rejected["invocation_ordinal"] == 1
            assert ledger.has_unknown_usage(first.subject_id)
            # 开出第二次时：第一次的预留仍按上限挂着计数（账上可见、没有当 0 释放），第二次有它自己的预留
            held = ledger.reservation(first.subject_id)
            assert held["state"] == "RESERVED" and held["reserved_tokens"] > 0, held
            own = ledger.reservation(second.subject_id)
            assert own is not None and own["reservation_id"] != held["reservation_id"]
            account = ledger.account(held["account_id"])
            assert account.reserved_tokens >= held["reserved_tokens"] + own["reserved_tokens"], account

            # 审阅最终形成结论，任务走完（不卡）
            mission = await case.settle(timeout=60)
            assert str(mission.status.value) == "COMPLETED", (mission.final_report, _review_waits(case))
            assert provider.review_calls["TASK_CONTENT"] == 2
            assert len(case.events("AcceptanceCommitted")) == 1
            assert not any(row[2] == "BUDGET_WAIT" for row in _review_waits(case)), _review_waits(case)
            # 收尾：第一次调用那笔按上限计入（多算不少算），用量事实本身仍记作不明（如实）
            counted = [e.payload for e in case.events("ReservationCountedAtUpperBound")
                       if e.payload.get("subject_id") == first.subject_id]
            assert counted and counted[0]["counted_tokens"] >= held["reserved_tokens"], counted
            settled = ledger.reservation(first.subject_id)
            assert settled["state"] == "SETTLED" and settled["settled_tokens"] >= held["reserved_tokens"]
            assert ledger.has_unknown_usage(first.subject_id)
            assert counted[0]["reason"] == "unknown_usage", counted

    asyncio.run(run())
