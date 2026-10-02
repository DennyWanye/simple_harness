# SPDX-License-Identifier: Apache-2.0
"""Every creation door yields a Mission bound to its TaskGraph, in one transaction.

User decision 2026-10-03: there is no "required but not bound yet" waiting period.  The
commit layer runs the deployment's completion (root and TaskGraph binding) inside the
creation transaction whichever door the request came through — the deployment's own
create, the authenticated facade, or the SDK's ``MissionApi`` — so an unbound user Mission
cannot exist.

**Mutation**: drop the completion call in ``CommitService.create_mission`` → red (the
facade and ``MissionApi`` Missions have no binding and no root).
"""
from __future__ import annotations

import asyncio

from agent_orchestrator.api.missions import MissionApi
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.product_world import USER_GOAL_NAMES, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


def _bound(loop, mission_id: str) -> bool:
    return loop.store.connection.execute(
        "SELECT 1 FROM taskgraph_policy_bindings WHERE mission_id=?", (mission_id,)).fetchone() is not None


def test_every_creation_door_binds_the_taskgraph_and_the_root(tmp_path):
    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), auto=False) as world:
            body = {"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"]}
            via_deployment = world.create({**body, "idempotency_key": "door-deployment"})["mission_id"]
            via_facade = world.control.create({**body, "idempotency_key": "door-facade"})["mission_id"]
            via_api, created = MissionApi(world.loop.commit, orchestrator=world.loop).create(
                tenant_id=world.deployment.tenant_id, request={**body, "idempotency_key": "door-api"})
            assert created is True
            for mission_id in (via_deployment, via_facade, via_api.id):
                assert _bound(world.loop, mission_id), mission_id
                root = HtnStore(world.loop.store).latest_task_semantics(USER_GOAL_NAMES.task_prefix + mission_id)
                assert root is not None, mission_id
            # A replay of the same request returns the same Mission and binds nothing twice.
            again = world.control.create({**body, "idempotency_key": "door-facade"})
            assert again["mission_id"] == via_facade and again.get("created") is False
            assert world.loop.store.connection.execute(
                "SELECT COUNT(*) FROM taskgraph_policy_bindings WHERE mission_id=?", (via_facade,)).fetchone()[0] == 1

    asyncio.run(case())
