"""结构修复真机第 5 局（2026-09-30）：换代后的步骤不再等"原样重试"批准。

第二步在修复前做过一次、没通过（尝试停在等重试批准）。后继步骤换掉第一步后，第二步换代
（输入改指新第一步，派发代号 +1）。旧规则只看"最近一次尝试失败了"，于是要规划器先批准原样
重试——可输入已经变了，这不是原样重试；规划器也不会再被问，第二步永远派不出去，任务以
"没有可继续的工作"失败。失败若属于旧一代，新一代直接开工；新一代自己失败时照旧要批准。
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

from test_h1i_production_entry import _config
from test_h4_retry_runtime_entry import attempt, refined

from agent_orchestrator.contracts.htn import ContractRevision, DispatchGeneration
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.orchestrator.planning_retry import _failed_in_an_older_generation, retry_decision_required
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.fixtures import RoleScriptedProvider


def _later(loop, seconds):  # type: ignore[no-untyped-def]
    at = loop.store.now + seconds
    loop.store._clock = lambda: at


def _fail(loop, task_id):  # type: ignore[no-untyped-def]
    failed, intent = attempt(loop, task_id)
    loop.commit.claim_intent(intent.intent_id, owner=loop._owner, lease_seconds=60)
    loop.commit.record_agent_created(intent.intent_id, agent_id="fixture-" + failed.id[-6:], expected_turn_id="t")
    loop.commit.record_submitted(intent.intent_id, receipt={"turn_id": "t", "seq": 1})
    loop.commit.mark_attempt_timed_out(failed.id, reason="stalled", detail={})
    return failed


def test_a_failure_of_an_older_generation_needs_no_retry_decision(tmp_path):
    async def case():  # type: ignore[no-untyped-def]
        async with Orchestrator(_config(tmp_path), RoleScriptedProvider({"planner": []})) as loop:
            mission, _dispatch, task_id = await refined(loop, tmp_path, "retry-regeneration")
            _fail(loop, task_id)
            assert retry_decision_required(loop.store, mission.id, task_id)
            # a structural repair re-versions the Task (what plan commit's binding rewrite writes)
            _later(loop, 1.0)
            htn = HtnStore(loop.store)
            old = htn.task_semantics_of(mission.id, task_id)
            htn.put_task_semantics(mission.id, replace(
                old, contract_revision=ContractRevision(int(old.contract_revision) + 1),
                dispatch_generation=DispatchGeneration(int(old.dispatch_generation) + 1)))
            assert not retry_decision_required(loop.store, mission.id, task_id)
            # a failure of the new generation (an attempt started after the re-version) is an
            # ordinary retry decision again
            _later(loop, 1.0)
            later_attempt = SimpleNamespace(created_at=loop.store.now)
            assert not _failed_in_an_older_generation(loop.store, mission.id, task_id, later_attempt)
    asyncio.run(case())
