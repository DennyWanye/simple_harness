# SPDX-License-Identifier: Apache-2.0
"""A hierarchical Mission's plan is committed only onto its TaskGraph binding.

The product binds the TaskGraph in the creation transaction (user decision 2026-10-03), so
there is no "required but not bound yet" waiting period any more.  The commit gate still
holds for a Mission that somehow has no binding: here one is created by bypassing the
deployment's create path (the authenticated facade, then the deployment's own root
initializer — everything except the binding), and the Planner's real reply cannot commit
an unbound plan: the commit is refused by name (``TASKGRAPH_NOT_BOUND``).

2026-10-03 (A′ step 2): today an unbound Mission without the old creation-time requirement
row commits an unbound plan (the participant is only built for a bound Mission and the
"required, not bound yet" check reads the requirement row the deployment no longer writes);
the single ``TASKGRAPH_NOT_BOUND`` refusal arrives with the removal of the waiting mechanism
(A′ plan §2 item 1).  Strict xfail until then.
"""
from __future__ import annotations

import asyncio

import pytest

from production_fixture import enabled_world

from agent_orchestrator.deployment.root import initialize_root
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.product_world import USER_GOAL_NAMES, user_goal_world


@pytest.mark.xfail(strict=True, reason="TASKGRAPH_NOT_BOUND for every unbound plan commit lands with the "
                   "removal of the TaskGraph waiting mechanism (A′ plan §2 item 1); src unchanged in step 2")
def test_no_unbound_plan_is_committed_for_a_hierarchical_mission(tmp_path):
    """**Mutation**: drop the ``TASKGRAPH_NOT_BOUND`` refusal in the plan commit → red (the
    plan commits without a TaskGraph revision record)."""

    async def case():
        async with enabled_world(tmp_path, key="tg-bound-mission") as world:
            loop, product = world.loop, world.product
            created = product.control.create({"goal": world.mission.goal, "idempotency_key": "tg-unbound",
                                              "success_criteria": list(world.mission.success_criteria),
                                              "budget": {"max_tokens": 8_000_000, "max_attempts": 12}})
            unbound = loop.store.get_mission(created["mission_id"])
            initialize_root(loop, unbound, product.deployment.principal, world_factory=user_goal_world,
                            root_type=USER_GOAL_NAMES.root_type, task_prefix=USER_GOAL_NAMES.task_prefix,
                            duty_prefix=USER_GOAL_NAMES.duty_prefix)
            assert loop.store.connection.execute(
                "SELECT 1 FROM taskgraph_policy_bindings WHERE mission_id=?", (unbound.id,)).fetchone() is None

            def refused():
                return [e for e in loop.store.iter_events(unbound.id) if "TASKGRAPH_NOT_BOUND" in str(e.payload)]

            def planned():
                return refused() or HtnStore(loop.store).active_plan_revision(unbound.id)

            await world.until(planned)
            assert refused()
            assert HtnStore(loop.store).active_plan_revision(unbound.id) is None
            assert loop.store.connection.execute(
                "SELECT COUNT(*) FROM taskgraph_revision_records WHERE mission_id=?", (unbound.id,)).fetchone()[0] == 0

    asyncio.run(case())
