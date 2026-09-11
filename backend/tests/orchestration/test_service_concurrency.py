# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-21: control-channel writes arrive while ``Orchestrator.run()`` is in progress on the
same event loop.  ``run()`` yields at every ``await`` (turn collection, polling), so writes
from another task land between its steps.  The Store is one connection with an RLock and
no transaction spans an await — create / cancel from another task must neither raise
``StoreError`` nor lose a write, and the loop must stay up.

Scripted steps run synchronously on the loop thread, so the test never blocks inside a
step (that would freeze the loop it is testing); it interleaves writes with the running
driver loop instead.

Draft written before the implementation (plan 2026-09-11 H2).
"""

from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.testing.fixtures import RoleScriptedProvider, critic_step, graph_proposal_step
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import NOTES_TASK, _notes_worker, notes_request, review_provider


async def _until(service, mission_id: str, status: str, seconds: float = 30.0) -> str:  # type: ignore[no-untyped-def]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    current = ""
    while loop.time() < deadline:
        current = service.mission_detail(mission_id)["mission"]["status"]
        if current == status:
            break
        await asyncio.sleep(0.02)
    return current


@pytest.mark.asyncio
async def test_writes_interleaved_with_a_running_loop_do_not_collide(orchestration_root, principal):
    provider = RoleScriptedProvider(
        {
            # identical steps per role, so the order two Missions consume them does not matter
            "planner": [graph_proposal_step([NOTES_TASK]) for _ in range(3)],
            "worker": _notes_worker() + _notes_worker(),
            "critic": [critic_step(verdict="PASS", criteria_met=True) for _ in range(6)],
        }
    )
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(tick_active_seconds=0.01, tick_idle_seconds=0.05),
        provider=provider,
        principal=principal,
    )
    await service.start()
    try:
        first = service.create_mission(notes_request("k-c1"))
        await asyncio.sleep(0.05)  # the driver loop is inside run() now
        second = service.create_mission(notes_request("k-c2"))
        await asyncio.sleep(0)
        assert service.cancel_mission(second["mission_id"])["status"] == "CANCELLED"
        await asyncio.sleep(0)
        third = service.create_mission(notes_request("k-c3"))
        assert await _until(service, first["mission_id"], "COMPLETED") == "COMPLETED"
        assert await _until(service, third["mission_id"], "COMPLETED") == "COMPLETED"
        assert service.mission_detail(second["mission_id"])["mission"]["status"] == "CANCELLED"
        assert service.status()["state"] == "available"  # no StoreError took the loop down
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_a_decision_and_a_comment_land_while_the_loop_runs(orchestration_root, principal):
    """HA-21 approve (review P2-10): a person's decision and a comment arrive back to back
    while the driver loop is active — neither is lost, nothing raises, the Mission ends."""

    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(tick_active_seconds=0.01, tick_waiting_seconds=0.01, tick_idle_seconds=0.05),
        provider=review_provider(),
        principal=principal,
    )
    await service.start()
    try:
        created = service.create_mission(notes_request("k-review-live"))
        mission_id = created["mission_id"]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 30
        pending: list = []
        while loop.time() < deadline and not pending:
            pending = service.approvals(mission_id)
            await asyncio.sleep(0.02)
        assert pending and pending[0]["kind"] == "review"
        service.decide(pending[0]["request_id"], "review_pass", note="看过了")
        service.comment(mission_id, "复核时顺手留一句")  # the loop has been woken by the decision
        assert await _until(service, mission_id, "COMPLETED") == "COMPLETED"
        events = service.events(mission_id, limit=200)["events"]
        assert any(e["type"] == "HumanCommentAdded" and e.get("summary") == "复核时顺手留一句" for e in events)
        decided = [a for a in service.mission_detail(mission_id)["approvals"] if a["request_id"] == pending[0]["request_id"]]
        assert decided and decided[0]["state"] != "PENDING"
        assert service.status()["state"] == "available"
    finally:
        await service.close()
