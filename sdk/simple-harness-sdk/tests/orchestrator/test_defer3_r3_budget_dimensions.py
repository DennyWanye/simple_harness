# SPDX-License-Identifier: Apache-2.0
"""推后第 3 批车道 R3：H08 预算补"执行者数""搜索次数"两维（原文 §5 ``max_agents``、§18.1）。

Harness 只做计数与上限：执行者数在建尝试时核、不随"非模型原因失败退次数"退还；搜索次数只数
检索类工具（按工具名查表），建尝试时按账户链剩余预留、网关按预留拦、结清按工具调用表的事实记。
搜索次数用完不停任务：只拒绝检索类工具调用。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.contracts import Budget
from agent_orchestrator.contracts.models import SEARCH_TOOL_NAMES
from agent_orchestrator.governance.budget_limits import inherit_limits
from agent_orchestrator.governance.budgets import BudgetExhausted, BudgetLedger
from agent_orchestrator.storage.store import Store


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _ledger(tmp_path: Any, limits: Budget) -> tuple[Store, BudgetLedger]:
    store = Store.open(tmp_path / "o.db")
    ledger = BudgetLedger(store)
    with store.transaction():
        ledger.open_account(account_id="budget:m", scope="mission", parent_id=None, mission_id="m",
                            limits=limits)
        ledger.open_account(account_id="budget:t", scope="task", parent_id="budget:m", mission_id="m",
                            limits=inherit_limits(Budget(), limits))
    return store, ledger


def test_the_two_dimensions_are_budget_fields_and_inherit_like_the_others() -> None:
    budget = Budget(max_agents=3, max_search_calls=5)
    assert budget.to_json()["max_agents"] == 3 and budget.to_json()["max_search_calls"] == 5
    assert Budget.from_json(budget.to_json()) == budget
    child = inherit_limits(Budget(max_tokens=10), budget)
    assert (child.max_agents, child.max_search_calls) == (3, 5)
    assert not Budget(max_agents=4).fits_within(budget)
    assert SEARCH_TOOL_NAMES == frozenset({"knowledge_list", "assurance_find_evidence"})


def test_the_facade_does_not_open_the_two_dimensions() -> None:
    from agent_orchestrator.api.facade import CLOSED_BUDGET, OPEN_BUDGET

    assert {"max_agents", "max_search_calls"} <= CLOSED_BUDGET
    assert not {"max_agents", "max_search_calls"} & OPEN_BUDGET


def test_executor_count_is_checked_on_the_chain_and_never_given_back(tmp_path: Any) -> None:
    store, ledger = _ledger(tmp_path, Budget(max_agents=2, max_attempts=10))
    with store.transaction():
        ledger.reserve(account_id="budget:t", subject_id="a1", tokens=0, counts_attempt=True)
        ledger.reserve(account_id="budget:t", subject_id="a2", tokens=0, counts_attempt=True)
        # 2026-09-28：非模型原因的失败退尝试次数——执行者数不退，它数的是真起过的会话
        ledger.release_attempt("budget:t")
        assert ledger.account("budget:m").attempts_created == 1
        assert ledger.account("budget:m").agents_started == 2
        assert ledger.account("budget:m").remaining_agents() == 0
        with pytest.raises(BudgetExhausted) as refused:
            ledger.reserve(account_id="budget:t", subject_id="a3", tokens=0, counts_attempt=True)
        assert refused.value.dimension == "agents"
        # 服务调用（规划器、审阅员）不是执行者，不计数
        ledger.reserve(account_id="budget:t", subject_id="planner-1", tokens=0, counts_attempt=False)
    assert ledger.account("budget:m").to_json()["remaining_agents"] == 0


def test_search_calls_are_reserved_then_settled_on_the_executed_search_tools(tmp_path: Any) -> None:
    store, ledger = _ledger(tmp_path, Budget(max_search_calls=3))
    with store.transaction():
        ledger.reserve(account_id="budget:t", subject_id="a1", tokens=0, counts_attempt=True, search_calls=3)
        assert ledger.account("budget:m").remaining_search_calls() == 0
        with pytest.raises(BudgetExhausted) as refused:
            ledger.reserve(account_id="budget:t", subject_id="a2", tokens=0, counts_attempt=True, search_calls=1)
        assert refused.value.dimension == "search_calls"
    store.record_tool_call(call_key="a1:c1", subject_id="a1", mission_id="m", tool="knowledge_list",
                           outcome="succeeded")
    store.record_tool_call(call_key="a1:c2", subject_id="a1", mission_id="m", tool="workspace_read_file",
                           outcome="succeeded")
    store.record_tool_call(call_key="a1:c3", subject_id="a1", mission_id="m", tool="knowledge_list",
                           outcome="rejected:search_budget_exhausted")
    assert store.count_search_calls("a1") == 1
    with store.transaction():
        settled = ledger.settle(subject_id="a1", tool_calls=2)
        assert settled["settled_search_calls"] == 1
        account = ledger.account("budget:m")
        assert (account.reserved_search_calls, account.settled_search_calls) == (0, 1)
        assert account.remaining_search_calls() == 2


# ---------------------------------------------------------------- 产品同形：网关按预留拦，任务照常完成

def _tool_results(request: Any) -> list[str]:
    return [str(message.content) for message in request.messages if "tool" in str(message.role).lower()]


def test_a_spent_search_budget_refuses_search_tools_but_the_mission_still_completes(tmp_path: Any) -> None:
    from agent_orchestrator.testing.product_world import DEFAULT_TOOLS, product_world
    from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, worker_reply

    seen: list[str] = []

    def worker(request: Any) -> Any:
        results = _tool_results(request)
        if len(results) < 2:
            return ("knowledge_list", {})
        if len(results) == 2:
            seen.extend(results)
            return ("workspace_write_file", {"path": "NOTES.md", "content": "# 要点\n\n- 一\n- 二\n- 三\n"})
        return worker_reply(request)

    async def case() -> dict[str, Any]:
        async with product_world(tmp_path / "root", LayeredScriptedProvider(worker=worker),
                                 allowed_tools=DEFAULT_TOOLS + ("knowledge_list", "knowledge_read"),
                                 global_budget=Budget(max_search_calls=1)) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "r3-search"})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=30)
            store = world.loop.store
            attempts = [a for t in store.list_tasks(mission_id) for a in store.list_attempts(t.id)]
            reservations = [store.connection.execute(
                "SELECT reserved_search_calls, settled_search_calls FROM budget_reservations WHERE subject_id=?",
                (a.id,)).fetchone() for a in attempts]
            return {"mission": mission, "reservations": [tuple(r) for r in reservations if r is not None]}

    seen_state = asyncio.run(case())
    assert seen_state["mission"].status.value == "COMPLETED", seen_state["mission"].final_report
    assert len(seen) == 2
    assert "search_budget_exhausted" not in seen[0]
    assert "search_budget_exhausted" in seen[1], seen[1]
    assert (1, 1) in seen_state["reservations"], seen_state["reservations"]
    assert json.dumps(seen_state["reservations"])
