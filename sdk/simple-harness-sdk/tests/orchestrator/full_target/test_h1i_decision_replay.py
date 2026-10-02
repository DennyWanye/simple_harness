"""Committed H1-I decisions replay before authority and mutation checks.

The decision that committed is the main loop's own (``h1i_seed.committed``: proposal,
independent review, adoption); the case replays that round's exact raw reply.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from h1i_seed import committed, committing_round, grant_for
from h1i_seed import events as _events

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi
from agent_orchestrator.contracts.planning_decisions import PlanningDecisionStatus
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from agent_orchestrator.storage.store import StoreConflict


def _receipt(loop: Orchestrator, mission_id: str, intent_id: str) -> tuple[Any, int, int, int, int]:
    decision = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(intent_id, 0)
    assert decision is not None
    action_count = loop.store.connection.execute(
        "SELECT COUNT(*) FROM actions WHERE mission_id = ?", (mission_id,)
    ).fetchone()[0]
    return (
        decision,
        len(_events(loop, mission_id, "PlanRevisionCommitted")),
        len(
            loop.store.list_intents(
                "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED", "SETTLED", "FAILED"
            )
        ),
        int(action_count),
        len(loop.store.list_events(mission_id)),
    )


def test_committed_refine_replays_after_grant_revocation_without_new_mutation(tmp_path) -> None:
    async def case() -> None:
        async with committed(tmp_path, key="h1i-committed-replay") as (loop, mission, _world, _root, dispatch, product):
            opener, raw = committing_round(loop, mission.id)
            before = _receipt(loop, mission.id, opener.intent_id)
            assert before[0]["status"] == str(PlanningDecisionStatus.COMMITTED)
            assert before[0]["decision_type"] == "REFINE"

            grant_id, revision = grant_for(loop, opener.intent_id)
            PlanningAuthorizationApi(
                loop.store, tenant_id=mission.tenant_id, principal=product.deployment.principal
            ).revoke(
                grant_id,
                expected_revision=revision,
                command_id="revoke-committed-replay",
                reason="replay must not need live authority",
            )
            before = _receipt(loop, mission.id, opener.intent_id)
            await loop._collect_plan_decision(opener, object(), mission, raw, dispatch)
            after = _receipt(loop, mission.id, opener.intent_id)
            assert after == before
            assert PlanningDecisionStore(loop.store).get_planning_decision(
                before[0]["decision_id"]
            )["status"] == str(PlanningDecisionStatus.COMMITTED)

    asyncio.run(case())


def test_committed_refine_rejects_different_raw_at_same_ordinal_without_rewriting_receipt(
    tmp_path,
) -> None:
    async def case() -> None:
        async with committed(tmp_path, key="h1i-committed-replay-conflict") as (loop, mission, _world, _root, dispatch, _product):
            opener, raw = committing_round(loop, mission.id)
            before = _receipt(loop, mission.id, opener.intent_id)
            assert before[0]["status"] == str(PlanningDecisionStatus.COMMITTED)
            conflicting_raw = raw + "\n"  # Even equivalent JSON has a different raw identity.
            assert conflicting_raw != raw
            with pytest.raises(StoreConflict):
                await loop._collect_plan_decision(
                    opener, object(), mission, conflicting_raw, dispatch
                )
            assert _receipt(loop, mission.id, opener.intent_id) == before

    asyncio.run(case())
