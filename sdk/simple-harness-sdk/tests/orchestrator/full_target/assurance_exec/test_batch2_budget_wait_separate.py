# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""第 2 批车道 I1，A05：预算等待与"需重算"分开记原因，预算等待不计入重算次数。

此前轮询把准备阶段抛出的 ``BudgetError`` 也送进 ``recheck``（原因 ``BUDGET_UNAVAILABLE``），和
``RECHECK_REQUIRED`` 共用 32 次 / 300 秒的累计上限：一次长时间的预算等待就把这项工作打成
MANUAL_REQUIRED，要新事件才能重开。原计划 §9 第 5 步：BUDGET_WAIT 与 RECHECK_REQUIRED 各自保存
WAITING + next_due / reason。现在：预算不够 → ``wait(reason=BUDGET_WAIT)``，退避有自己的上限（60 秒），
不动 ``rechecks``；``recheck`` 拒收 ``BUDGET_WAIT``。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_orchestrator.assurance.codec import AssuranceError
from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.governance.budgets import BudgetError
from agent_orchestrator.orchestrator.assurance_tick import AssuranceTick
from agent_orchestrator.storage.assurance_work import (
    BUDGET_WAIT,
    BUDGET_WAIT_MAX_MS,
    AssuranceWorkStore,
    budget_wait_delay_ms,
)

NOW = 1_000_000


def _claim(tries: int = 1) -> SimpleNamespace:
    return SimpleNamespace(mission_id="m-1", consumer="REVIEW", work_key="review:m-1:r1", tries=tries)


def _tick(calls: list, *, settle_ms: int | None = NOW) -> SimpleNamespace:
    work = SimpleNamespace(
        wait=lambda claim, **kw: calls.append(("wait", claim.work_key, kw)),
        recheck=lambda claim, **kw: calls.append(("recheck", claim.work_key, kw)),
    )
    return SimpleNamespace(work=work, _settlement_time=lambda claim: settle_ms)


def test_a05_a_budget_error_waits_under_its_own_reason_and_never_rechecks():
    calls: list = []
    AssuranceTick._settle_failure(_tick(calls), _claim(tries=1), BudgetError("mission budget exhausted"))
    assert calls == [("wait", "review:m-1:r1", {
        "now_ms": NOW, "reason": BUDGET_WAIT, "not_before_ms": NOW + budget_wait_delay_ms(1)})]


def test_a05_rechecks_keep_their_own_reasons():
    calls: list = []
    tick = _tick(calls)
    AssuranceTick._settle_failure(tick, _claim(), AssuranceError("SOURCE_UNAVAILABLE"))
    AssuranceTick._settle_failure(tick, _claim(), TimeoutError())
    AssuranceTick._settle_failure(tick, _claim(), ContractError("NETWORK_DOCUMENT_INVALID"))
    assert [(kind, kw["reason"]) for kind, _key, kw in calls] == [
        ("recheck", "SOURCE_UNAVAILABLE"), ("recheck", "RECHECK_REQUIRED"), ("recheck", "MISSION_DATA_UNREADABLE")]


def test_a05_an_unstable_clock_moves_nothing():
    calls: list = []
    AssuranceTick._settle_failure(_tick(calls, settle_ms=None), _claim(), BudgetError("x"))
    assert calls == []


def test_a05_budget_wait_backoff_has_its_own_ceiling():
    delays = [budget_wait_delay_ms(tries) for tries in range(1, 9)]
    assert delays == sorted(delays) and delays[0] >= 1_000
    assert delays[-1] == BUDGET_WAIT_MAX_MS == 60_000
    assert budget_wait_delay_ms(400) == BUDGET_WAIT_MAX_MS


def test_a05_the_recheck_budget_refuses_a_budget_wait():
    """预算等待不计入重算次数：``recheck`` 连 BUDGET_WAIT 这个原因都不收。"""
    store = AssuranceWorkStore(SimpleNamespace())
    with pytest.raises(AssuranceError) as raised:
        store.recheck(_claim(), now_ms=NOW, reason=BUDGET_WAIT)
    assert raised.value.code == "WORK_RECHECK_REASON_INVALID"
