# SPDX-License-Identifier: Apache-2.0
"""暂缓 / 拒绝过的结果，只有可信宿主显式重评才会再问策略；重评幂等、不复活旧状态。"""

from __future__ import annotations

import asyncio

import pytest
from test_continuation import parent_with_result, tick
from test_session import SPEC, Crash, FakeHost
from test_session_async import FakeChat, eventually

from agent_orchestrator.host import ContinuationDecision, Session
from agent_orchestrator.host.session_ledger import SessionError


class SwitchPolicy:
    """先 hold，宿主把开关拨到允许后才 enqueue；记录每次被问的上下文。"""

    def __init__(self, first="hold"):
        self.allowed, self.calls, self.contexts, self.first = False, 0, [], first

    async def decide(self, context):
        self.calls += 1
        self.contexts.append(context)
        if self.allowed:
            return ContinuationDecision.enqueue()
        return getattr(ContinuationDecision, self.first)("temporary")


def test_capabilities_name_the_three_new_abilities_and_bump_versions(tmp_path):
    caps = Session(FakeHost(tmp_path), session_id="main").capabilities()
    assert {
        "continuation_controls",
        "continuation_reevaluation",
        "goal_acceptance_records",
    } <= set(caps["capabilities"])
    assert caps["protocol_version"] == 3 and caps["session_config_version"] == 4


def test_hold_then_trusted_reevaluation_consumes_the_result_exactly_once(tmp_path):
    async def case():
        policy = SwitchPolicy()
        _, parent = await parent_with_result(tmp_path, policy=policy)
        await tick(parent)
        [held] = parent.continuations()
        assert held["state"] == "hold" and policy.calls == 1
        # 普通唤醒不再问策略：策略改成允许也原地不动
        policy.allowed = True
        for _ in range(3):
            await tick(parent)
        assert parent.continuations()[0]["state"] == "hold"
        assert policy.calls == 1 and not parent.queued_inputs() and parent._chat.calls == 0

        receipt = parent.reevaluate_continuation(command_id="re-1", policy_revision="policy-2")
        assert receipt["status"] == "accepted"
        assert receipt["event_ids"] == [held["event_id"]] and receipt["reevaluation_id"]
        # 同号同参原回执；异参冲突
        assert (
            parent.reevaluate_continuation(command_id="re-1", policy_revision="policy-2") == receipt
        )
        with pytest.raises(SessionError, match="idempotency_conflict"):
            parent.reevaluate_continuation(command_id="re-1", policy_revision="policy-3")

        await tick(parent)
        await eventually(lambda: bool(parent.inbox()[0]["applied_turn_id"]))
        assert policy.calls == 2
        [context] = policy.contexts[1:]
        assert context.reevaluation_id == receipt["reevaluation_id"]
        assert context.event_id == held["event_id"]
        [row] = parent.continuations()
        assert row["state"] == "applied"
        [decision] = row["decisions"]
        assert decision["from"] == "hold" and decision["to"] == "pending"
        assert decision["policy_revision"] == "policy-2"
        assert decision["command_id"] == "re-1"
        assert len(parent.queued_inputs()) == 1 and parent._chat.calls == 1
        kinds = [e["kind"] for e in parent._store.events(0)]
        assert "continuation_reevaluation_requested" in kinds
        assert "continuation_reevaluated" in kinds
        assert kinds.count("result_applied") == 1
        # 已应用的结果再重评：不问策略、不重放
        again = parent.reevaluate_continuation(command_id="re-2")
        assert again["event_ids"] == []
        await tick(parent)
        assert policy.calls == 2 and parent._chat.calls == 1
        await parent.close()

    asyncio.run(case())


def test_deny_and_closed_or_cancelled_results_are_not_revived_by_plain_ticks(tmp_path):
    """拒绝只有显式重评才会变；已取消 / Goal 已关 / 已排除的结果重评也不复活。"""

    async def case():
        policy = SwitchPolicy(first="deny")
        host, parent = await parent_with_result(tmp_path, policy=policy)
        await tick(parent)
        [denied] = parent.continuations()
        assert denied["state"] == "deny"
        policy.allowed = True
        for _ in range(3):
            await tick(parent)
        assert parent.continuations()[0]["state"] == "deny" and policy.calls == 1

        # 策略异常：重评后仍 hold，原因 policy_unavailable，不调模型
        async def broken(context):
            policy.calls += 1
            raise RuntimeError("private")

        policy.decide = broken
        parent.reevaluate_continuation(command_id="re-broken")
        await tick(parent)
        [row] = parent.continuations()
        assert row["state"] == "hold" and row["reason"] == "policy_unavailable"
        assert policy.calls == 2 and parent._chat.calls == 0

        # 已排除的结果：重评范围里有它，但记为 skipped，不问策略
        policy.decide = SwitchPolicy.decide.__get__(policy)
        parent.exclude_result(row["event_id"], "宿主不采用", command_id="exclude")
        receipt = parent.reevaluate_continuation(command_id="re-excluded")
        assert receipt["event_ids"] == []
        await tick(parent)
        assert policy.calls == 2 and parent.continuations()[0]["state"] == "excluded"

        # 第二条结果：先 hold，再取消整棵树，重评不复活
        await parent.create_child("b", command_id="b")
        policy.allowed = False
        second = await parent._delegations.delegate("turn-2", "call-2", {**SPEC, "child_key": "b"})
        host.finish(second["mission_id"])
        parent._inbox.catch_up()
        await tick(parent)
        held = next(r for r in parent.continuations() if r["state"] == "deny")
        parent.cancel("delegation", second["delegation_id"], command_id="cancel-2")
        policy.allowed = True
        receipt = parent.reevaluate_continuation(
            command_id="re-cancelled", event_ids=[held["event_id"]]
        )
        await tick(parent)
        request = parent._store.get("continuation_reevaluations", receipt["reevaluation_id"])
        assert request["state"] == "done"
        assert request["outcomes"][held["event_id"]].startswith("skipped:")
        assert parent._chat.calls == 0
        await parent.close()

    asyncio.run(case())


def test_reevaluation_survives_crash_and_concurrent_requests_consume_once(tmp_path):
    """受理后崩溃：重开仍处理；两次重评并发：同一结果只产生一个输入。"""

    async def case():
        policy = SwitchPolicy()
        host, parent = await parent_with_result(tmp_path, policy=policy)
        await tick(parent)
        policy.allowed = True
        fired = []

        def crash(name):
            if name == "reevaluation_requested" and not fired:
                fired.append(name)
                raise Crash(name)

        parent._checkpoint = crash
        with pytest.raises(Crash):
            parent.reevaluate_continuation(command_id="re-crash")
        await parent.close()

        reopened = Session(host, session_id="main", continuation_policy=policy)
        reopened._chat = FakeChat(reopened)
        await reopened.recover()
        # 重开前受理已持久：同号返回原回执，第二个并发重评命中同一结果
        assert first_pending(reopened) in reopened.recovery_report()["resumed_refs"]
        first = reopened.reevaluate_continuation(command_id="re-crash")
        second = reopened.reevaluate_continuation(command_id="re-twin")
        assert first["event_ids"] == second["event_ids"] != []
        await tick(reopened)
        await eventually(lambda: bool(reopened.inbox()[0]["applied_turn_id"]))
        for _ in range(3):
            await tick(reopened)
        assert len(reopened.queued_inputs()) == 1 and reopened._chat.calls == 1
        assert policy.calls == 2
        twin = reopened._store.get("continuation_reevaluations", second["reevaluation_id"])
        assert twin["state"] == "done"
        assert all(value.startswith("skipped:") for value in twin["outcomes"].values())
        await reopened.close()

    asyncio.run(case())


def first_pending(session):
    [row] = [
        r for r in session._store.rows("continuation_reevaluations") if r["state"] == "requested"
    ]
    return row["reevaluation_id"]
