# SPDX-License-Identifier: Apache-2.0
"""供方记账族：进程在模型调用进行中退出、重开同一个证据根（HTN 补齐阶段 A′ 重写，2026-10-03）。

Host 每个进程一个新的编排 owner；重开用 ``product_world_as`` 给新 owner（见 ``provider_world``）。
两条用例：

1. 执行者的调用在路上时进程退出（另一步做完一次调用、正排队等下一次的槽位）——死掉的调用放出槽位但按上限留账；排队的
   那一轮在新进程里接着跑，被准入以"执行权不在这个进程手里"（``lease_lost``）拒绝，原地重做、不扣
   次数（2026-10-02 真机 ``mission-baddf1eb2442858e``）。
2. 内容审阅员的调用在路上时进程退出（2026-09-28 真机强杀）——那次审阅只能等一个永远答不上来的
   原调用，它不挡收尾：步骤重做，原调用的额度在收尾时按上限计入，任务完成。

原用例去向（旧做法都是 ``leaf_world`` 手工建尝试 + 测试自己 new 守卫 / 旧运行时，已不可用）：

* 重写进本文件：``test_provider_budget_guard`` 的"死进程的调用放出槽位"、"重启后接着跑的一轮按
  lease_lost 拒绝"；``test_provider_budget_recovery`` 的"新租约不让旧的在途调用继续活着"；
  ``full_target/test_lease_lost_is_redone`` 两条（第二条"别的准入拒绝照旧停步"在
  ``test_provider_accounting_loop`` 的拒绝用例里）；``full_target/test_unknown_usage_upper_bound``
  的 4、5（只等答不上来的原调用的审阅不挡收尾）。
* 删除，产品走不到：
  - 晚到账单（``test_provider_accounting`` 4 条、``test_provider_accounting_boundaries`` 3 条、
    ``test_provider_budget_recovery`` 的"对账后结清一次""确认未开始"两条）：产品执行池的对账口是
    ``ConsumerRuntimePolicies.local_default()``，对任何调用都答"仍未知"，没有晚到账单来源，
    ``record_provider_accounting`` 在产品里没有调用方；
  - 同一个库上两个编排进程同时活着（``test_live_new_owner_refuses_without_cancelling_its_lease``、
    ``test_expired_service_claim_does_not_release_current_sdk_reserved_grant``）：Host 单实例；
  - 已释放授权的再准入身份比对（``test_released_never_handed_off_grant_requires_identical_request_and_allowance``）
    与"取消正好落在准入和交出之间"：都要替换守卫方法才够得着，外界事件造不出那个窗口
    （取消落在排队期间由 ``test_provider_accounting_loop`` 覆盖）；
  - 排队到点（守卫时钟快进 901 秒）：要改守卫时钟；
  - 多执行池"旧授权没有执行池身份"（``test_multi_profile_load``）：产品不给执行池单独的槽位数
    （``profile_slots`` 恒为空），且开发期不兼容旧授权；
  - 候选计数器（``test_admission_estimator_candidates``）：产品每个池只给一个计数器；
  - 首审尾款的提交层钩子三条（``test_first_protected_tail_hooks``：篡改金额/版本/账户/主体、
    虚构尝试）：产品只在建尝试的同一事务里用真实尝试号调用；"尾款与尝试一起回滚"由
    ``taskgraph_exec/test_process_recovery::...[after_reserve]`` 覆盖（进程在建尝试事务里退出，
    盘上没有那一事务写的预留）；
  - 首审输入上限（``test_first_request_guard_integration``）：篡改冻结上限是 ①b2，不重写；
    "审阅材料真的超过首审输入上限（256k 池 262144）、在交出前被拒"本可以用预置超大工作区文件造，
    这一轮没做（偏离，见迁移报告）；
  - 未知用量收尾的账本细则（``test_unknown_usage_upper_bound`` 的 2、3：已知事实超过预留不截、
    有一笔不是未知用量占着的预留就不按上限计）：准入总把预留增长到覆盖每次调用的上限，超出即
    OVERRUN 停步，产品里造不出"已知用量超过预留又有未知"的组合；正向规则由两个文件里的收尾
    断言覆盖。
* 随删除删（分诊裁决②金额计价）：``test_tail_and_priced_budget`` 5、``test_priced_budget_cold_reopen`` 1、
  ``test_provider_accounting`` 计价 1、``test_provider_budget_identity`` 计价 1；随旧执行池删：
  ``test_succeeded_missing_usage_boundary::test_legacy_execution_reconciliation_cannot_replace_succeeded_usage``。
"""
from __future__ import annotations

import asyncio

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
    product_world_as,
    settle,
    wire_terminal,
)

from agent_orchestrator.orchestrator.failure_classes import INTERRUPTED, classify_failure
from agent_orchestrator.testing.fixtures import role_of
from agent_orchestrator.testing.scripted_replies import review_input


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


async def _until(predicate, seconds: float = 30.0) -> None:
    async def poll():
        while not predicate():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), seconds)


async def _run_then_exit(world, held, *, also) -> None:  # type: ignore[no-untyped-def]
    """Drive the loop until the held call is on the wire (and ``also()`` holds), then stop: the
    process exits with that call in flight."""
    stop = asyncio.Event()

    async def drive():
        while not stop.is_set():
            await world.drain(timeout=0.5)
            await asyncio.sleep(0.02)

    runner = asyncio.create_task(drive())
    try:
        await asyncio.wait_for(held.wait(), 30)
        await _until(also)
    finally:
        stop.set()
        await asyncio.wait_for(runner, 30)


def test_a_restart_frees_the_dead_calls_slot_and_redoes_the_queued_turn_as_lease_lost(tmp_path):
    async def case():
        root = tmp_path / "root"
        first = HeldStep("a.md")
        async with product_world_as(root, first, owner="process-1", **QUICK) as world:
            mission_id = world.create({"goal": "写两份文件", "success_criteria": ["file:a.md", "file:b.md"],
                                       "idempotency_key": "restart-lease"})["mission_id"]
            store = world.store
            pools = list(world.loop.assembled.pools.values())

            def queued() -> str | None:
                for subject, agent_id, turn_id in store.connection.execute(
                        "SELECT subject_id, agent_id, expected_turn_id FROM dispatch_intents"
                        " WHERE mission_id=? AND kind='attempt' AND agent_id IS NOT NULL", (mission_id,)):
                    if any(pool.bridge.runtime.ports.provider_admission.waiting_for_slot(
                            agent_id=agent_id, turn_id=turn_id) for pool in pools):
                        return subject
                return None

            await _run_then_exit(world, first.stuck, also=lambda: queued() is not None)
            waiter = queued()
            [(dead, dead_grant)] = [(row["subject_id"], row) for row in grants(store)
                                    if row["subject_id"].startswith("task-") and row["actual_tokens"] is None]
            # 排队的这一轮已经做完过一次调用（结了账），正在等下一次的槽位
            before_restart = grants(store, waiter)
            assert dead != waiter and [row["state"] for row in before_restart] == ["SETTLED"]
            # 进程活着时，在路上的调用占着唯一的槽位
            assert (dead_grant["invocation_id"], dead_grant["handoff_ordinal"]) not in wire_terminal(store)

        second = Scripted()
        async with product_world_as(root, second, owner="process-2", **QUICK) as world:
            store = world.store
            assert await settle(world, mission_id) == "COMPLETED", world.loop.progress_log[-10:]
            tried = attempts(store, mission_id)

            # 死掉的调用：槽位放出（否则一个槽位的部署之后什么都跑不了），额度按上限留着、收尾时计入
            [row] = grants(store, dead)
            assert row["state"] == "UNKNOWN" and row["actual_tokens"] is None
            assert (row["invocation_id"], row["handoff_ordinal"]) in wire_terminal(store)
            assert physical_calls(root)[row["invocation_id"]][1] == 1  # 只交出过那一次
            assert str(tried[dead].status.value) == "LOST"
            assert classify_failure(tried[dead].failure) == INTERRUPTED
            counted = {event.payload["subject_id"]: event.payload
                       for event in events(store, mission_id, "ReservationCountedAtUpperBound")}
            assert counted[dead]["counted_tokens"] >= row["total_upper"]

            # 排队的那一轮：新进程接着跑，准入以 lease_lost 拒绝、没交出去；原地重做，不扣次数
            assert str(tried[waiter].status.value) == "RETRY_WAIT"
            assert classify_failure(tried[waiter].failure) == INTERRUPTED
            assert tried[waiter].failure["error"]["detail"]["reason_code"] == "lease_lost"
            assert grants(store, waiter) == before_restart  # 被拒的那次没有授权、没交出去
            released = {event.attempt_id: event.payload for event in events(store, mission_id, "AttemptChargeReleased")}
            assert released[waiter]["failure_class"] == released[dead]["failure_class"] == "INTERRUPTED"
            redone = {attempt.retry_of: attempt for attempt in tried.values() if attempt.retry_of}
            assert set(redone) == {dead, waiter}
            assert all(str(attempt.status.value) == "COMPLETED" for attempt in redone.values())
            # 新进程没有替旧的两轮向模型发过任何请求（不重发、不猜）
            assert not [request for request in second.requests if attempt_of(request) in {dead, waiter}]

    asyncio.run(case())


class HeldReview(Scripted):
    """内容审阅员的调用停在半路（进程会在它回来之前退出）。"""

    def __init__(self) -> None:
        super().__init__()
        self.stuck = asyncio.Event()
        self.never = asyncio.Event()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        package = review_input(request) if role_of(request) == "unknown" else None
        if package is not None and str((package.get("package") or {}).get("purpose")) == "TASK_CONTENT":
            self.stuck.set()
            await self.never.wait()
        return await super().invoke(request, cancel=cancel)


def test_a_review_whose_call_died_with_the_process_does_not_hold_the_closeout(tmp_path):
    async def case():
        root = tmp_path / "root"
        first = HeldReview()
        async with product_world_as(root, first, owner="process-1", **QUICK) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "restart-review"})["mission_id"]
            store = world.store

            def review_on_the_wire() -> bool:
                return any(row["subject_id"].split(":assurance:")[-1].startswith("assurance-content:")
                           for row in grants(store))

            await _run_then_exit(world, first.stuck, also=review_on_the_wire)
            [review] = [row["subject_id"] for row in grants(store)
                        if row["subject_id"].split(":assurance:")[-1].startswith("assurance-content:")]
            [reviewed] = [key for key in attempts(store, mission_id)]

        second = Scripted()
        async with product_world_as(root, second, owner="process-2", **QUICK) as world:
            store = world.store
            assert await settle(world, mission_id) == "COMPLETED", world.loop.progress_log[-10:]
            # 那次审阅记为"等原调用对账"，没有重发
            [row] = grants(store, review)
            assert row["state"] == "UNKNOWN" and (row["invocation_id"], row["handoff_ordinal"]) in wire_terminal(store)
            waiting = [event.payload for event in events(store, mission_id, "AssuranceProviderReconciliationRequired")
                       if event.payload["intent_id"] == "intent-critic-" + review]
            assert waiting, [event.payload for event in events(store, mission_id,
                                                                 "AssuranceProviderReconciliationRequired")]
            assert physical_calls(root)[row["invocation_id"]][1] == 1
            # 被审的那一步被打断、不扣次数，重做后通过
            tried = attempts(store, mission_id)
            released = {event.attempt_id: event.payload for event in events(store, mission_id, "AttemptChargeReleased")}
            assert released[reviewed]["failure_class"] == "INTERRUPTED"
            [redo] = [attempt for attempt in tried.values() if attempt.retry_of == reviewed]
            assert str(redo.status.value) == "COMPLETED"
            # 它不挡收尾：原调用的额度按上限（整笔审阅预留）计入，任务完成
            counted = {event.payload["subject_id"]: event.payload
                       for event in events(store, mission_id, "ReservationCountedAtUpperBound")}
            assert counted[review]["counted_tokens"] >= row["total_upper"]
            assert world.loop.commit.ledger.reservation(review)["state"] == "SETTLED"

    asyncio.run(case())
