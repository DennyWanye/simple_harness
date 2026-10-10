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
        _budget_can_still_change=lambda account_id, mission_id: can_change,
        _end_on_exhausted_budget=lambda claim, error: calls.append(("stop", claim.mission_id, error.remaining)),
    )


def test_a_budget_wait_nothing_can_change_ends_the_step_then_waits_as_before():
    calls: list = []
    AssuranceTick._settle_failure(_tick(calls, can_change=False), _claim(), EXHAUSTED)
    assert calls == [("stop", "m-1", 300_000), ("wait", BUDGET_WAIT)]


def test_a_budget_wait_that_may_change_keeps_waiting():
    calls: list = []
    AssuranceTick._settle_failure(_tick(calls, can_change=True), _claim(), EXHAUSTED)
    assert calls == [("wait", BUDGET_WAIT)]
    calls.clear()  # a budget error that is not an exhaustion carries no account: wait as before
    AssuranceTick._settle_failure(_tick(calls, can_change=False), _claim(), BudgetError("unknown charge"))
    assert calls == [("wait", BUDGET_WAIT)]


def _store_with_reservations(rows, *, live_intents=(), live_attempts=()):
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE budget_reservations(subject_id TEXT, account_id TEXT, state TEXT, mission_id TEXT)")
    connection.executemany("INSERT INTO budget_reservations VALUES (?,?,?,'m-1')", rows)
    return SimpleNamespace(
        connection=connection,
        get_intent_for_subject=lambda s: SimpleNamespace(state="SUBMITTED") if s in live_intents else None,
        get_attempt=lambda s: SimpleNamespace(status="RUNNING") if s in live_attempts else None,
    )


def _real_tick(store) -> SimpleNamespace:
    return SimpleNamespace(store=store)


def test_budget_can_change_only_while_a_live_subject_holds_a_reservation():
    # the first review call's reservation (its intent FAILED) and a settled attempt: nothing can change
    store = _store_with_reservations([("m-1:assurance:x:1", "budget:task-1", "RESERVED"),
                                      ("task-1:attempt-3", "budget:task-1", "SETTLED")])
    assert AssuranceTick._budget_can_still_change(_real_tick(store), "budget:task-1", "m-1") is False
    # another step's running attempt cannot give tokens back to *this* step's account…
    store = _store_with_reservations([("task-2:attempt-1", "budget:task-2", "RESERVED")], live_attempts={"task-2:attempt-1"})
    assert AssuranceTick._budget_can_still_change(_real_tick(store), "budget:task-1", "m-1") is False
    # …but it can to the Mission total (独立核验 opt.183：看下级，不看上级)
    assert AssuranceTick._budget_can_still_change(_real_tick(store), "budget:mission-m-1", "m-1") is True
    # a critic call still on the wire (its intent live) likewise
    store = _store_with_reservations([("m-1:assurance:y:1", "budget:task-1", "RESERVED")], live_intents={"m-1:assurance:y:1"})
    assert AssuranceTick._budget_can_still_change(_real_tick(store), "budget:task-1", "m-1") is True


def test_the_owner_step_comes_from_the_review_binding():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE assurance_review_bindings(review_key TEXT, owner_task_id TEXT)")
    connection.execute("INSERT INTO assurance_review_bindings VALUES ('assurance-content:abc', 'task-1')")
    tick = SimpleNamespace(store=SimpleNamespace(connection=connection))
    assert AssuranceTick._review_owner_task(tick, _claim()) == "task-1"
    assert AssuranceTick._review_owner_task(tick, SimpleNamespace(consumer="CLOSEOUT", work_key="closeout:m-1")) is None


def test_the_refusing_account_decides_what_ends(monkeypatch):
    """步骤账户不够 → 停这一步；任务总账户/全局不够，或这一步停不了（复合/根目标 BLOCKED、已验收）→ 停整个任务。"""
    from agent_orchestrator.orchestrator.commit_service import CommitRejected

    calls: list = []

    def tick(task_status, *, stop_raises=False):
        orch = SimpleNamespace(
            _commit_stop_task=lambda tid, *, stop_reason, detail: (calls.append(("stop", tid, str(stop_reason)))
                                                                   if not stop_raises else (_ for _ in ()).throw(CommitRejected("is BLOCKED"))),
            _commit_fail_mission=lambda mid, *, stop_reason, detail: calls.append(("fail", mid, detail["account"])),
        )
        store = SimpleNamespace(get_task=lambda tid: SimpleNamespace(status=task_status) if tid == "task-1" else None)
        return SimpleNamespace(store=store, orchestrator=orch, _review_owner_task=lambda claim: "task-1", _release_after=set())

    claim = _claim()
    t = tick("VERIFYING"); AssuranceTick._end_on_exhausted_budget(t, claim, BudgetExhausted("budget:task-1", "tokens", 1, 0))
    assert calls == [("stop", "task-1", "budget_exhausted")] and t._release_after == {"m-1"}
    calls.clear(); t = tick("VERIFYING")
    AssuranceTick._end_on_exhausted_budget(t, claim, BudgetExhausted("budget:mission-m-1", "tokens", 1, 0))
    assert calls == [("fail", "m-1", "budget:mission-m-1")]
    calls.clear(); t = tick("BLOCKED", stop_raises=True)  # a method/final review's owner cannot be stopped
    AssuranceTick._end_on_exhausted_budget(t, claim, BudgetExhausted("budget:task-1", "tokens", 1, 0))
    assert calls == [("fail", "m-1", "budget:task-1")]
    calls.clear(); t = tick("COMPLETED")  # an accepted step re-reviewed: nothing left to stop but the Mission
    AssuranceTick._end_on_exhausted_budget(t, claim, BudgetExhausted("budget:task-1", "tokens", 1, 0))
    assert calls == [("fail", "m-1", "budget:task-1")]


def test_a_method_review_whose_second_call_cannot_be_paid_ends_the_mission_not_the_loop(tmp_path, monkeypatch):
    """独立核验 opt.183 M1（照核验员探针）：做法审阅的归属任务是 BLOCKED 的根目标，第二次调用在任务总账户
    上预留不下。以前：停步骤被拒（BLOCKED）、异常冒出轮询、工作项停在 RUNNING、任务永远不结束。
    现在：任务按额度用完结束，没有异常逃出，工作项不再被重领。"""
    import asyncio
    import dataclasses

    import agent_orchestrator.orchestrator.event_handler as event_handler
    from agent_orchestrator.governance.budgets import BudgetLedger
    from agent_orchestrator.testing.product_world import product_world
    from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, review_input, review_reply

    state = {"malformed": 0, "escaped": []}

    def reviewer(request):
        data = review_input(request)
        if data is None:
            return None
        if data["package"]["purpose"] == "METHOD_PLAN" and state["malformed"] == 0:
            state["malformed"] = 1
            return "我看过了，这个做法可以。"  # not JSON: the first call is unusable, a second one is owed
        return review_reply(data)

    class Provider(LayeredScriptedProvider):
        async def invoke(self, request, *, cancel):
            before = state["malformed"]
            response = await super().invoke(request, cancel=cancel)
            if before == 0 and state["malformed"] == 1:
                response = dataclasses.replace(response, usage=None)  # and the relay reported no usage
            return response

    reserve = BudgetLedger.reserve

    def tight_reserve(self, *, account_id, subject_id, tokens, **kw):
        # the Mission total cannot pay the method review's second call
        if "assurance-method-plan" in subject_id and subject_id.endswith(":2"):
            raise BudgetExhausted("budget:" + kw.get("mission_id", "") if False else account_id, "tokens", tokens, 100)
        return reserve(self, account_id=account_id, subject_id=subject_id, tokens=tokens, **kw)

    settle = AssuranceTick._settle_failure

    def watched_settle(self, claim, error):
        try:
            return settle(self, claim, error)
        except Exception as error_out:  # noqa: BLE001
            state["escaped"].append(type(error_out).__name__)
            raise

    monkeypatch.setattr(BudgetLedger, "reserve", tight_reserve)
    monkeypatch.setattr(AssuranceTick, "_settle_failure", watched_settle)
    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)

    async def body():
        async with product_world(tmp_path / "root", Provider(reviewer=reviewer)) as product:
            store = product.store
            mission_id = product.create({"goal": "按规格写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                         "idempotency_key": "method-review-unpaid",
                                         "budget": {"max_tokens": 500_000, "max_attempts": 12}})["mission_id"]
            for _ in range(40):
                await product.drain(timeout=2)
                mission = store.get_mission(mission_id)
                if str(mission.status).split(".")[-1] in {"FAILED", "COMPLETED", "CANCELLED"}:
                    break
            mission = store.get_mission(mission_id)
            work = [tuple(r) for r in store.connection.execute(
                "SELECT state, wait_reason FROM assurance_pending_work WHERE mission_id=? AND consumer='REVIEW'", (mission_id,))]
            return str(mission.status).split(".")[-1], str(mission.stop_reason).split(".")[-1].lower(), work

    status, reason, work = asyncio.run(body())
    assert state["malformed"] == 1
    assert state["escaped"] == [], state["escaped"]
    assert status == "FAILED" and "budget_exhausted" in reason, (status, reason)
    assert all(row[0] != "RUNNING" for row in work), work
