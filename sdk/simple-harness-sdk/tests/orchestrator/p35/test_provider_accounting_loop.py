# SPDX-License-Identifier: Apache-2.0
"""供方记账族（HTN 补齐阶段 A′ 重写，2026-10-03）：产品同形部署上，模型调用的用量说不清、超了、
额度盖不住、排队时任务被取消，记账各自怎么走。

供方记账就是产品原生执行池里的 ``ProviderBudgetGuard``：每次交出调用前按"输入估算 + 输出上限"
准入并占一个槽位，调用结束按实际用量结账。规则（用户 2026-09-24 定）：**宁可多算、不可少算、不冻结**。
测试只模拟外界——提供方回零用量、不回用量、拒绝、断线、回超大用量；用户取消任务。

本文件合并了原 p35 以下用例（旧做法：``leaf_world`` 手工建尝试 + 自己 new 一个守卫 + 旧运行时）：

* ``test_provider_budget_guard``：零用量、未知结果、输入加输出超额度在交出前拒绝、排队时取消；
* ``test_provider_budget_identity``：终止失败没有用量、事后发现超出上限；
* ``test_provider_budget_recovery``：取消落在准入与交出之间、排队不计费的存活状态；
* ``test_succeeded_missing_usage_boundary``（成功但缺用量不猜、不重发）、
  ``test_admission_collection_runtime``（一次空回复不让任务停摆）、
  ``test_first_protected_tail_hooks``（首审尾款：失败尝试的尾款单独释放、不碰尝试自己的账）；
* ``step06/test_step06_review_fixes::p1_6``（未知用量占着的预留上时间线）、
  ``full_target/test_unknown_usage_upper_bound`` 的 1～3（收尾按上限计入未知用量）。

删掉的（产品走不到或已被别处覆盖）写在 ``test_provider_accounting_restart.py`` 文件头的清单里。
"""
from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from provider_world import (
    QUICK,
    HeldStep,
    Scripted,
    attempt_of,
    attempts,
    events,
    grants,
    physical_calls,
    status,
    wire_terminal,
)

from agent_orchestrator.testing.fixtures import role_of
from agent_orchestrator.testing.product_world import product_world
from simple_harness import Message, MessageRole
from simple_harness.providers import ProviderRequestRejectedError, ProviderUsage

NON_MODEL = {"INFRA", "INTERRUPTED"}


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


class Relay(Scripted):
    """第一次尝试的执行者调用按 ``fault`` 出事，其余照常（真机见过的几种中转站行为）。"""

    def __init__(self, fault: str) -> None:
        super().__init__()
        self.fault = fault
        self.first_attempt_calls = 0

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        first = role_of(request) == "worker" and attempt_of(request).endswith(":attempt-1")
        if first:
            self.first_attempt_calls += 1
            if self.first_attempt_calls == 1 and self.fault == "rejected":
                raise ProviderRequestRejectedError()  # 交出去以后被拒，没有用量
            if self.first_attempt_calls == 1 and self.fault == "broken":
                raise RuntimeError("peer closed connection without sending complete message body")
        response = await super().invoke(request, cancel=cancel)
        if first and self.fault == "zero":  # 2026-09-24 中转站：每次都回 0/0/0
            response = replace(response, usage=ProviderUsage(0, 0, 0))
        if first and self.fault == "missing" and self.first_attempt_calls == 1:
            response = replace(response, usage=None)
        if first and self.fault == "empty" and self.first_attempt_calls == 1:  # 日卡网关：空回复、无用量
            response = replace(response, message=Message(MessageRole.ASSISTANT, ""), tool_calls=(),
                               usage=None, finish_reason="stop")
        return response


@pytest.mark.parametrize("fault", ["zero", "missing", "empty", "rejected", "broken"])
def test_a_charge_nobody_can_state_is_held_at_its_bound_and_counted_at_closeout(tmp_path, fault):
    """用量说不清的调用：额度按上限留着（不当零、不重发），槽位放掉（一个槽位的部署照样往下跑），
    任务不停摆；收尾时按上限计入，任务完成。调用本身失败的，尝试不扣次数、预留上时间线。"""

    async def case():
        provider = Relay(fault)
        root = tmp_path / "root"
        async with product_world(root, provider, **QUICK) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "unknown-" + fault})["mission_id"]
            store = world.store

            async def drive():
                while status(store, mission_id) not in {"COMPLETED", "FAILED", "CANCELLED"}:
                    await world.drain(timeout=10)
            await asyncio.wait_for(drive(), 60)
            assert status(store, mission_id) == "COMPLETED", world.loop.progress_log[-10:]

            tried = attempts(store, mission_id)
            first = next(attempt for key, attempt in tried.items() if key.endswith(":attempt-1"))
            first_grants = grants(store, first.id)
            unknown = [row for row in first_grants if row["actual_tokens"] is None]
            # 说不清的那次：UNKNOWN，额度按上限算，调用已在线路上结束、不再占槽位
            assert unknown and all(row["state"] == "UNKNOWN" for row in unknown), first_grants
            assert {(row["invocation_id"], row["handoff_ordinal"]) for row in unknown} <= wire_terminal(store)
            # 它只交出过一次：没有重发、没有猜
            calls = physical_calls(root)
            assert all(calls[row["invocation_id"]][1] == row["handoff_ordinal"] == 1 for row in unknown), calls
            # 账上：这次尝试的用量是"未知"，不是 0；整局用量不算"全部已知"
            assert world.loop.commit.ledger.has_unknown_usage(first.id)
            assert world.loop.commit.ledger.usage_flags(mission_id) == {
                "usage_fully_known": False, "budget_conserved": True}
            # 收尾按上限计入：计的是整笔预留（不小于这几次调用的上限之和），一次
            [counted] = [event.payload for event in events(store, mission_id, "ReservationCountedAtUpperBound")
                         if event.payload["subject_id"] == first.id]
            reservation = world.loop.commit.ledger.reservation(first.id)
            assert reservation["state"] == "SETTLED"
            assert counted["counted_tokens"] == reservation["settled_tokens"] >= sum(
                row["total_upper"] for row in unknown)
            assert counted["reason"] == "unknown_usage"
            # 从没有因为"用量没结清"停摆
            assert not [event for event in store.list_events(mission_id) if event.type == "TaskPaused"]

            if fault in {"empty", "rejected", "broken"}:
                # 调用失败：尝试不扣次数，换一次新尝试做完；被占着的预留上时间线，只记一次
                [released] = [event.payload for event in events(store, mission_id, "AttemptChargeReleased")
                              if event.attempt_id == first.id]
                assert released["failure_class"] in NON_MODEL
                [held] = [event.payload for event in events(store, mission_id, "ReservationHeld")
                          if event.payload["subject_id"] == first.id]
                assert held["reason"] == "unknown_usage" and held["reserved_tokens"] == counted["counted_tokens"]
                second = next(attempt for key, attempt in tried.items() if key.endswith(":attempt-2"))
                assert str(second.status.value) == "COMPLETED" and second.retry_of == first.id
                # 首审尾款：失败尝试的那份单独释放，没有转给审阅
                [tail] = store.connection.execute(
                    "SELECT state FROM budget_tail_holds WHERE subject_id=?",
                    ("tail:first-critic:" + first.id,)).fetchall()
                assert tail[0] == "RELEASED"
            else:
                assert list(tried) == [first.id] and str(first.status.value) == "COMPLETED"

    asyncio.run(case())


class Overrun(Scripted):
    """执行者的第一次调用报回的用量超出了准入时算的上限（输出 9000 > 输出上限 8192）。"""

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        response = await super().invoke(request, cancel=cancel)
        if role_of(request) == "worker":
            response = replace(response, usage=ProviderUsage(100, 9000, 9100))
        return response


class ZeroUsage(Scripted):
    """执行者的调用都回 0/0/0：每次都按上限计入已花的额度。"""

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        response = await super().invoke(request, cancel=cancel)
        if role_of(request) == "worker":
            response = replace(response, usage=ProviderUsage(0, 0, 0))
        return response


#: 第二种情形的额度：建尝试要先留 9000（尝试预留）+ 294912（首审尾款 = 256k 池的输入上限 + 输出上限），
#: 规划三次各结 150；剩下约 3700，盖得住第一次调用（8622 在 9000 的预留里），盖不住第二次要追加的
#: 约 8200（第一次零用量、按上限 8622 算进已花）。
TIGHT = {"budget": {"max_tokens": 308_000, "max_attempts": 12}}


@pytest.mark.parametrize("refusal", ["overrun", "budget"])
def test_an_admission_refusal_that_is_not_an_interruption_stops_the_step(tmp_path, refusal):
    """准入拒绝不是"被打断"：这一步停下、任务按原因结束，不原地打转。

    * ``overrun``：实际用量先记上账（OVERRUN，实际数照记），再拒绝这一任务之后的所有调用；
    * ``budget``：零用量的那次按上限算进已花，下一次"输入估算 + 输出上限"盖不住，在交出之前就拒绝，
      提供方没被调用，这次调用没有授权行。"""

    async def case():
        provider = Overrun() if refusal == "overrun" else ZeroUsage()
        root = tmp_path / "root"
        config = dict(QUICK, attempt_reserve_tokens=9_000) if refusal == "budget" else dict(QUICK)
        async with product_world(root, provider, **config) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "refused-" + refusal,
                                       **(TIGHT if refusal == "budget" else {})})["mission_id"]
            store = world.store

            async def drive():
                while status(store, mission_id) not in {"COMPLETED", "FAILED", "CANCELLED"}:
                    await world.drain(timeout=10)
            await asyncio.wait_for(drive(), 60)
            assert status(store, mission_id) == "FAILED", world.loop.progress_log[-10:]
            [attempt] = attempts(store, mission_id).values()
            [failed] = [event.payload for event in events(store, mission_id, "TaskFailed")]
            admission = failed["detail"]["admission"]
            assert provider.asked.count("worker") == 1  # 拒绝之后再没有调用交出去
            calls = physical_calls(root)
            [granted] = grants(store, attempt.id)
            if refusal == "overrun":
                assert failed["stop_reason"] == "runtime_unavailable"
                assert admission["reason_code"] == "bound_overrun"
                # 实际用量先落账：超出的那次照实记，尝试按实际结账
                assert granted["state"] == "OVERRUN"
                assert (granted["actual_tokens"], granted["actual_output_tokens"]) == (9100, 9000)
                assert granted["actual_tokens"] > granted["total_upper"]
                assert world.loop.commit.ledger.reservation(attempt.id)["settled_tokens"] == 9100
            else:
                assert failed["stop_reason"] == "budget_exhausted"
                assert admission["reason_code"] == "budget_exhausted" and admission["dimension"] == "tokens"
                # 被拒的那次：没交出去（交出次数 0），也没有授权行；挡住它的是按上限算的零用量那次
                refused = [state for invocation, state in calls.items() if invocation != granted["invocation_id"]
                           and state[1] == 0]
                assert refused, calls
                assert granted["state"] == "UNKNOWN" and granted["actual_tokens"] is None
                assert admission["request_tokens"] + granted["total_upper"] > 9_000
                assert admission["requested"] > admission["remaining"]

    asyncio.run(case())


def test_a_call_queued_for_the_only_slot_is_unbilled_and_a_cancel_never_hands_it_off(tmp_path):
    """一个槽位、两步并行：a 的调用在路上（很慢），b 的调用排队等槽位。

    * 排队不计费：b 的回合活着、标明在等槽位且不计费，尝试照旧在跑（不被当成卡死），也没有授权行；
    * 用户这时取消任务：b 那次调用永远不交出去；a 那次随后返回，照实结账（不因取消丢账）；
      两步未用的首审尾款都释放、认领收干净。

    （取消后两次尝试的预留停在 RESERVED「assurance_settlement_pending」、主循环不再重试结账，
    记为疑似产品缺陷，见迁移报告；这里不对它下断言。）"""

    async def case():
        provider = HeldStep("a.md")
        root = tmp_path / "root"
        async with product_world(root, provider, **QUICK) as world:
            mission_id = world.create({"goal": "写两份文件", "success_criteria": ["file:a.md", "file:b.md"],
                                       "idempotency_key": "queued-cancel"})["mission_id"]
            store = world.store
            stop = asyncio.Event()

            async def drive():  # 在路上的那次调用让 run() 不会空闲，自己转
                while not stop.is_set():
                    await world.drain(timeout=0.5)
                    await asyncio.sleep(0.02)

            runner = asyncio.create_task(drive())
            try:
                await asyncio.wait_for(provider.stuck.wait(), 30)
                pools = list(world.loop.assembled.pools.values())

                def queued():
                    for row in store.connection.execute(
                            "SELECT subject_id, agent_id, expected_turn_id FROM dispatch_intents"
                            " WHERE mission_id=? AND kind='attempt' AND agent_id IS NOT NULL", (mission_id,)):
                        for pool in pools:
                            guard = pool.bridge.runtime.ports.provider_admission
                            if guard.waiting_for_slot(agent_id=row[1], turn_id=row[2]):
                                return pool, row
                    return None

                for _ in range(500):
                    if queued():
                        break
                    await asyncio.sleep(0.02)
                pool, (waiter, agent_id, turn_id) = queued()
                live = await pool.bridge.liveness(agent_id=agent_id, turn_id=turn_id)
                assert live.alive and live.blocked
                assert live.blocker["kind"] == "provider_slot_wait" and live.blocker["billable"] is False
                assert str(store.get_attempt(waiter).status.value) == "RUNNING"
                assert grants(store, waiter) == []

                world.control.cancel(mission_id)
                provider.let_go.set()
                for _ in range(500):  # 取消落地，a 那次返回后照实结账
                    finished = [row for row in grants(store) if row["subject_id"].startswith("task-")]
                    if status(store, mission_id) == "CANCELLED" and finished and finished[0]["state"] == "SETTLED":
                        break
                    await asyncio.sleep(0.02)
            finally:
                stop.set()
                provider.let_go.set()
                await asyncio.wait_for(runner, 30)

            assert status(store, mission_id) == "CANCELLED"
            assert provider.asked.count("worker") == 1 and provider.stuck_calls == 1
            assert grants(store, waiter) == []
            assert all(handoffs == 0 for invocation, (state, handoffs) in physical_calls(root).items()
                       if invocation not in {row["invocation_id"] for row in grants(store)}), physical_calls(root)
            [running] = [row for row in grants(store) if row["subject_id"].startswith("task-")]
            assert running["state"] == "SETTLED" and running["actual_tokens"] == 150
            assert not store.list_mission_claims(mission_id)
            assert {row[0] for row in store.connection.execute(
                "SELECT state FROM budget_tail_holds WHERE mission_id=?", (mission_id,))} == {"RELEASED"}

    asyncio.run(case())
