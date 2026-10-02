# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Actual SDK admission -> durable terminal -> Orchestrator collect, no fake errors.

Oracle: a denied physical handoff never spends another Attempt or escalates a
model. A genuine unknown earlier handoff keeps its reservation while waiting.
The original Task budget is immutable; Mission spare budget is not a top-up.
"""

import asyncio
from dataclasses import replace

from agent_orchestrator.contracts import Budget, TaskStatus
from agent_orchestrator.graph.task_graph import TaskGraphProposal
from agent_orchestrator.orchestrator.commit_service import MissionSpec
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
            mission = await orch.submit_mission(
                MissionSpec(
                    "Write the full original deliverable", ("file:answer.txt",), "test", "admission",
                    allowed_tools=("workspace_list", "workspace_write_file"), budget=Budget(max_tokens=400000, max_attempts=10),
                    orchestration_semantics_version="legacy",
                )
            )
            planning = orch.commit.begin_planning(mission.id)
            tasks, _ = orch.commit.commit_task_graph(
                mission.id,
                TaskGraphProposal.from_json({"tasks": [{
                    "key": "A", "goal": "Write the full original deliverable", "rationale": "empty reply", "dependencies": [],
                    "success_criteria": ["file:answer.txt"], "verification_policy": ["format_check", "rule_check"],
                    "allowed_tools": ["workspace_list", "workspace_write_file"], "budget": {"max_tokens": 90000, "max_attempts": 3},
                }]}),
                base_version=planning.version, source={"planner": "fixture"},
            )
            task = tasks[0]
            for _ in range(800):
                await orch._cycle()
                current = orch.store.get_task(task.id)
                if provider.calls >= 2 or current.paused or current.status is TaskStatus.FAILED:
                    break
                await asyncio.sleep(0.002)
            current = orch.store.get_task(task.id)
            assert not (current.paused and current.pause_reason == "provider_admission:usage_unresolved"), current.pause_reason
            assert provider.calls >= 2, "the Task must go on after one empty reply"
            first = orch.store.list_attempts(task.id)[0]
            with orch.store.transaction():
                assert orch.commit.ledger.has_unknown_usage(first.id)
                assert orch.commit.ledger.reservation(first.id)["reserved_tokens"] > 0

    asyncio.run(exercise())
