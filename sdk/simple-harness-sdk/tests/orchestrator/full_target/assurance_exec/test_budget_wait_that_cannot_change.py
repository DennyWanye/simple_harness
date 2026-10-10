"""2026-10-11 parse 重跑：审阅的第二次调用预留不下（这一步账户 500 万只剩 30 万），记成 BUDGET_WAIT
每分钟再看一次，等了半小时；账户链上没有任何还活着的预留，额度不会变。等待只在"有在跑的东西
可能把额度还回来"时才有意义；否则这一步按额度用完结束，与执行者在准入处被拒相同。"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from agent_orchestrator.governance.budgets import BudgetError, BudgetExhausted
from agent_orchestrator.orchestrator.assurance_tick import AssuranceTick
from agent_orchestrator.storage.assurance_work import BUDGET_WAIT

NOW = 1_000_000
EXHAUSTED = BudgetExhausted("budget:task-1", "tokens", 745_472, 300_000)


def _claim() -> SimpleNamespace:
    return SimpleNamespace(mission_id="m-1", consumer="REVIEW", work_key="review-import:assurance-content:abc:2", tries=3)


def _tick(calls: list, *, can_change: bool) -> SimpleNamespace:
    work = SimpleNamespace(wait=lambda claim, **kw: calls.append(("wait", kw["reason"])),
                           recheck=lambda claim, **kw: calls.append(("recheck", kw["reason"])))
    return SimpleNamespace(
        work=work, _settlement_time=lambda claim: NOW,
        _budget_can_still_change=lambda account_id: can_change,
        _review_owner_task=lambda claim: "task-1",
        _stop_task_budget_exhausted=lambda task_id, error: calls.append(("stop", task_id, error.remaining)),
    )


def test_a_budget_wait_nothing_can_change_ends_the_step_then_waits_as_before():
    calls: list = []
    AssuranceTick._settle_failure(_tick(calls, can_change=False), _claim(), EXHAUSTED)
    assert calls == [("stop", "task-1", 300_000), ("wait", BUDGET_WAIT)]


def test_a_budget_wait_that_may_change_keeps_waiting():
    calls: list = []
    AssuranceTick._settle_failure(_tick(calls, can_change=True), _claim(), EXHAUSTED)
    assert calls == [("wait", BUDGET_WAIT)]
    calls.clear()  # a budget error that is not an exhaustion carries no account: wait as before
    AssuranceTick._settle_failure(_tick(calls, can_change=False), _claim(), BudgetError("unknown charge"))
    assert calls == [("wait", BUDGET_WAIT)]


def _store_with_reservations(rows, *, live_intents=(), live_attempts=()):
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE budget_reservations(subject_id TEXT, account_id TEXT, state TEXT)")
    connection.executemany("INSERT INTO budget_reservations VALUES (?,?,?)", rows)
    return SimpleNamespace(
        connection=connection,
        get_intent_for_subject=lambda s: SimpleNamespace(state="SUBMITTED") if s in live_intents else None,
        get_attempt=lambda s: SimpleNamespace(status="RUNNING") if s in live_attempts else None,
    )


def _real_tick(store) -> SimpleNamespace:
    chain = [SimpleNamespace(account_id="budget:task-1"), SimpleNamespace(account_id="budget:m-1")]
    return SimpleNamespace(store=store, orchestrator=SimpleNamespace(commit=SimpleNamespace(ledger=SimpleNamespace(_chain=lambda a: chain))))


def test_budget_can_change_only_while_a_live_subject_holds_a_reservation():
    # the first review call's reservation (its intent FAILED) and a settled attempt: nothing can change
    store = _store_with_reservations([("m-1:assurance:x:1", "budget:task-1", "RESERVED"),
                                      ("task-1:attempt-3", "budget:task-1", "SETTLED")])
    assert AssuranceTick._budget_can_still_change(_real_tick(store), "budget:task-1") is False
    # a running attempt on the Mission account may settle below its hold: wait
    store = _store_with_reservations([("task-2:attempt-1", "budget:m-1", "RESERVED")], live_attempts={"task-2:attempt-1"})
    assert AssuranceTick._budget_can_still_change(_real_tick(store), "budget:task-1") is True
    # a critic call still on the wire (its intent live) likewise
    store = _store_with_reservations([("m-1:assurance:y:1", "budget:task-1", "RESERVED")], live_intents={"m-1:assurance:y:1"})
    assert AssuranceTick._budget_can_still_change(_real_tick(store), "budget:task-1") is True


def test_the_owner_step_comes_from_the_review_binding():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE assurance_review_bindings(review_key TEXT, owner_task_id TEXT)")
    connection.execute("INSERT INTO assurance_review_bindings VALUES ('assurance-content:abc', 'task-1')")
    tick = SimpleNamespace(store=SimpleNamespace(connection=connection))
    assert AssuranceTick._review_owner_task(tick, _claim()) == "task-1"
    assert AssuranceTick._review_owner_task(tick, SimpleNamespace(consumer="CLOSEOUT", work_key="closeout:m-1")) is None
