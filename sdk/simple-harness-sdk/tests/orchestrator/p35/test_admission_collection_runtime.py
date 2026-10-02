# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Actual SDK admission -> durable terminal -> Orchestrator collect, no fake errors.

Oracle: a denied physical handoff never spends another Attempt or escalates a
model. A genuine unknown earlier handoff keeps its reservation while waiting.
The original Task budget is immutable; Mission spare budget is not a top-up.
"""

import asyncio
from dataclasses import replace

from leaf_world import loop_leaf

from agent_orchestrator.contracts import AttemptStatus, TaskStatus
from agent_orchestrator.orchestrator.event_handler import Orchestrator, OrchestratorConfig
from agent_orchestrator.runtime.model_router import RuntimeProfile
from agent_orchestrator.testing.fixtures import MODEL, RoleScriptedProvider


class Counter:
    fingerprint = "admission-collection-fixture-v1"
    bound_protocol = "fixture-input-allowance-v1"
    requires_prior_output_reserve = True

    def __init__(self, reason):
        self.reason = reason

    def estimate_input_tokens(self, request):
        if self.reason == "estimator_unavailable":
            raise ValueError("counter unavailable")
        return 90_001 if self.reason == "budget_exhausted" else 100


class EmptyFirstProvider(RoleScriptedProvider):
    """The day-card gateway's failure mode: the first call is an empty completion with no
    usage (a definite failure); later calls are normal."""

    empty_served = False

    async def invoke(self, request, *, cancel):
        if not self.empty_served:
            self.empty_served = True
            response = await super().invoke(request, cancel=cancel)
            from simple_harness import Message, MessageRole

            return replace(response, message=Message(MessageRole.ASSISTANT, ""), tool_calls=(), usage=None, finish_reason="stop")
        return await super().invoke(request, cancel=cancel)


def test_one_empty_reply_does_not_pause_the_task(tmp_path):
    """RP-E4 (real model): an empty reply without usage must not stall the Mission.  Each
    retry is a new Attempt (a new Agent run), so no prior-usage hold applies; the failed
    Attempt's own usage stays UNKNOWN and keeps its reservation held — conservative
    accounting, never a freeze."""

    async def exercise():
        provider = EmptyFirstProvider({"worker": [("workspace_list", {})] * 6})
        profile = RuntimeProfile("default", provider, MODEL, default_max_output_tokens=1000, max_output_tokens_ceiling=1000)
        config = OrchestratorConfig(evidence_root=tmp_path, max_concurrency=1, attempt_reserve_tokens=4000)
        async with Orchestrator(config, profiles={"default": profile}, provider_token_estimator=Counter("ok")) as orch:
            # 分层任务里的一个步骤（删旧平面模式 第三刀：原来是平面任务 A）。失败后的"再试一次"
            # 按生产顺序先要规划器的"原样重试"决定——这一轮由测试代为提交。
            leaf = await loop_leaf(orch, tmp_path, key="admission")
            task_id = leaf.task_id

            async def cycle_until(done):
                for _ in range(800):
                    await orch._cycle()
                    current = orch.store.get_task(task_id)
                    if done() or current.paused or current.status is TaskStatus.FAILED:
                        return
                    await asyncio.sleep(0.002)

            def first_failed():
                attempts = orch.store.list_attempts(task_id)
                return bool(attempts) and attempts[0].status is AttemptStatus.RETRY_WAIT

            await cycle_until(first_failed)
            current = orch.store.get_task(task_id)
            assert not (current.paused and current.pause_reason == "provider_admission:usage_unresolved"), current.pause_reason
            assert first_failed() and provider.calls == 1, orch.progress_log
            await leaf.authorize_retry(orch.store.list_attempts(task_id)[0])
            await cycle_until(lambda: provider.calls >= 2)
            current = orch.store.get_task(task_id)
            assert not (current.paused and current.pause_reason == "provider_admission:usage_unresolved"), current.pause_reason
            assert provider.calls >= 2, "the Task must go on after one empty reply"
            first = orch.store.list_attempts(task_id)[0]
            with orch.store.transaction():
                assert orch.commit.ledger.has_unknown_usage(first.id)
                assert orch.commit.ledger.reservation(first.id)["reserved_tokens"] > 0

    asyncio.run(exercise())
