"""Actual optional UP-Aries -> original collector -> guarded Plan Commit.

The round is the adoption round the main loop opened on the product's deployment after
the planner's proposed method passed its independent review (``h1i_seed.reviewed``).
"""
from __future__ import annotations

import asyncio

import pytest
from h1i_seed import plan_reply, reviewed

from agent_orchestrator.planning.htn.backend_port import PlanningLimits
from agent_orchestrator.planning.htn.backends.up_aries import UpAriesPlanningBackend
from agent_orchestrator.storage.planning_admission_store import PlanningAdmissionStore


@pytest.mark.parametrize("revoke_before_commit", (False, True))
def test_actual_solver_cannot_bypass_the_original_commit_guard(tmp_path, monkeypatch, revoke_before_commit):
    pytest.importorskip("up_aries")
    from agent_orchestrator.orchestrator import planning_backend_runtime

    async def case():
        backend = {"planning_backend": UpAriesPlanningBackend(),
                   "planning_backend_limits": PlanningLimits(timeout_seconds=30, max_expansions=None,
                                                             max_tokens=131072)}
        async with reviewed(tmp_path, key="actual-solver", **backend) as ((loop, mission, _world, _root, dispatch, product), intent, _provider):
            binding = PlanningAdmissionStore(loop.store).get_request_binding(intent.intent_id)
            original = planning_backend_runtime.solve_decision

            async def solve_then_revoke(*args, **kwargs):
                result = await original(*args, **kwargs)
                product.control.planning_authorization({
                    "operation": "revoke", "grant_id": binding["grant_id"],
                    "expected_revision": int(binding["grant_revision"]),
                    "command_id": "revoke-after-solving", "reason": "operator withdrew authority"})
                return result

            if revoke_before_commit:
                monkeypatch.setattr(planning_backend_runtime, "solve_decision", solve_then_revoke)
            await loop._collect_plan_decision(intent, object(), mission,
                plan_reply(intent.config["planning_package"]), dispatch)
            events = tuple(loop.store.iter_events(mission.id))
            solved = [e for e in events if e.type == "PlanningBackendReturned"]
            assert len(solved) == 1 and solved[0].payload["status"] == "SOLVED"
            assert solved[0].payload["witness"]["hierarchy_json"]
            committed = [e for e in events if e.type == "PlanRevisionCommitted"]
            decisions = [e for e in events if e.type == "PlanningDecisionEvaluated"]
            if revoke_before_commit:
                assert not committed
                assert int(dispatch.network(mission.id).plan_revision) == 0
                assert decisions[-1].payload["status"] == "COMMIT_REJECTED"
                assert decisions[-1].payload["rejection_codes"]
            else:
                assert len(committed) == 1 and int(dispatch.network(mission.id).plan_revision) == 1
                assert committed[0].payload["source"]["intent_id"] == intent.intent_id
                assert decisions[-1].payload["status"] == "COMMITTED"

    asyncio.run(case())
