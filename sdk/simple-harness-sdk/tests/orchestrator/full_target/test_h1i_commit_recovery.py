from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from h1i_seed import CONFIG, grant_for

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi
from agent_orchestrator.contracts import ContractError
from agent_orchestrator.contracts.planning_decisions import PlanningDecisionStatus
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from agent_orchestrator.testing.fixtures import RoleScriptedProvider
from agent_orchestrator.testing.product_world import product_world
from simple_harness.contracts import canonical_json

# The child runs the product's own main loop (proposal, its independent review, adoption)
# and dies at the fault point of the round that commits the first plan revision; the
# fixture records only that round's identity and the planner's raw reply.
_CHILD = r"""
import asyncio
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
mode = sys.argv[2]
full_target = Path.cwd() / "tests" / "orchestrator" / "full_target"
sys.path.insert(0, str(full_target))

from h1i_seed import CONFIG, CRITERIA, GOAL, planner
from agent_orchestrator.contracts.planning_decisions import PlanningDecisionStatus
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

fixture = {}

def recording_planner(request):
    raw = planner(request)
    if '"decision_type": "REFINE"' in raw:
        fixture["raw"] = raw
    return raw

def write_fixture(intent_id):
    fixture["intent_id"] = intent_id
    (root / "recovery-fixture.json").write_text(json.dumps(fixture), encoding="utf-8")

if mode == "before_final_row":
    original = PlanningDecisionStore.record_planning_decision
    def crash_before_final_row(self, *args, **kwargs):
        if kwargs.get("status") == PlanningDecisionStatus.COMMITTED:
            write_fixture(kwargs["request_id"])
            os._exit(91)
        return original(self, *args, **kwargs)
    PlanningDecisionStore.record_planning_decision = crash_before_final_row
else:
    collect = Orchestrator._collect_plan_decision
    async def exit_after_durable_commit(self, intent, *args, **kwargs):
        result = await collect(self, intent, *args, **kwargs)
        row = PlanningDecisionStore(self.store).get_planning_decision_by_attempt(intent.intent_id, 0)
        if row is not None and row["status"] == str(PlanningDecisionStatus.COMMITTED):
            write_fixture(intent.intent_id)
            os._exit(92)
        return result
    Orchestrator._collect_plan_decision = exit_after_durable_commit

async def main():
    provider = LayeredScriptedProvider(planner=recording_planner)
    async with product_world(root / "root", provider, **CONFIG) as world:
        created = world.create({"goal": GOAL, "idempotency_key": "h1i-i05-process",
                                "success_criteria": list(CRITERIA)})
        fixture["mission_id"] = created["mission_id"]
        await world.run_until_settled(created["mission_id"], rounds=12)
    raise AssertionError("COMMITTED fault point was not reached")

asyncio.run(main())
"""


def _run_child(tmp_path: Path, mode: str, code: int) -> dict:
    child = subprocess.run(
        [sys.executable, "-c", _CHILD, str(tmp_path), mode],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == code, (child.stdout, child.stderr)
    return json.loads((tmp_path / "recovery-fixture.json").read_text(encoding="utf-8"))


def _reopen(tmp_path: Path, provider: RoleScriptedProvider):
    """The restarted process: the same library, the product's deployment, nobody answering."""

    return product_world(tmp_path / "root", provider, auto=False, **CONFIG)


def _revoke(world, mission, intent_id: str, command_id: str, reason: str) -> None:
    grant_id, revision = grant_for(world.loop, intent_id)
    PlanningAuthorizationApi(
        world.store, tenant_id=mission.tenant_id, principal=world.deployment.principal
    ).revoke(grant_id, expected_revision=revision, command_id=command_id, reason=reason)


def _revision_hashes(store, mission_id: str) -> tuple[str, ...]:
    rows = store.connection.execute(
        "SELECT mission_id,revision,state,base_revision,snapshot_hash,delta_id,read_set_json "
        "FROM plan_revisions WHERE mission_id = ? ORDER BY revision",
        (mission_id,),
    ).fetchall()
    return tuple(
        hashlib.sha256(
            canonical_json(
                {
                    "mission_id": row["mission_id"],
                    "revision": row["revision"],
                    "state": row["state"],
                    "base_revision": row["base_revision"],
                    "snapshot_hash": row["snapshot_hash"],
                    "delta_id": row["delta_id"],
                    "read_set": json.loads(row["read_set_json"]),
                }
            ).encode()
        ).hexdigest()
        for row in rows
    )


@pytest.mark.parametrize("revoke_after_crash", (False, True))
def test_i05_commit_then_process_exit_replays_from_frozen_request(
    tmp_path: Path, revoke_after_crash: bool
) -> None:
    fixture = _run_child(tmp_path, "before_final_row", 91)

    async def recover() -> None:
        provider = RoleScriptedProvider({"planner": []})
        async with _reopen(tmp_path, provider) as world:
            loop = world.loop
            mission = loop.store.get_mission(fixture["mission_id"])
            intent = loop.store.get_intent(fixture["intent_id"])
            assert mission is not None and intent is not None
            dispatch = loop._dispatch_for(mission.id)
            before_hashes = _revision_hashes(loop.store, mission.id)
            assert len(before_hashes) in {0, 1}
            if revoke_after_crash:
                _revoke(world, mission, intent.intent_id, "revoke-h1i-i05-after-crash",
                        "recovery must reread current authority")

            await loop._collect_plan_decision(intent, object(), mission, fixture["raw"], dispatch)

            after_hashes = _revision_hashes(loop.store, mission.id)
            if revoke_after_crash:
                assert after_hashes == before_hashes
            else:
                assert len(after_hashes) == 1
                assert (
                    sum(
                        event.type == "PlanRevisionCommitted"
                        for event in loop.store.list_events(mission.id)
                    )
                    == 1
                )
            if before_hashes and not revoke_after_crash:
                assert after_hashes == before_hashes
            decision = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(
                intent.intent_id, 0
            )
            assert decision is not None
            assert decision["status"] == str(
                PlanningDecisionStatus.COMMIT_REJECTED
                if revoke_after_crash
                else PlanningDecisionStatus.COMMITTED
            )
            assert decision["canonical_hash"] is not None
            assert provider.calls == 0

    asyncio.run(recover())


def test_i05_recovery_source_failure_is_commit_rejected_without_retry_or_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _run_child(tmp_path, "before_final_row", 91)

    async def recover() -> None:
        provider = RoleScriptedProvider({"planner": []})
        async with _reopen(tmp_path, provider) as world:
            loop = world.loop
            mission = loop.store.get_mission(fixture["mission_id"])
            intent = loop.store.get_intent(fixture["intent_id"])
            assert mission is not None and intent is not None
            dispatch = loop._dispatch_for(mission.id)
            before_hashes = _revision_hashes(loop.store, mission.id)
            before_intents = tuple(
                row[0]
                for row in loop.store.connection.execute(
                    "SELECT intent_id FROM dispatch_intents ORDER BY intent_id"
                )
            )

            def unavailable(**_kwargs):
                raise ContractError("producer contract unavailable")

            # The admission sources become unreadable after the restart (an external
            # failure); the collector must refuse, not retry and not mutate.
            monkeypatch.setattr(loop, "_hierarchical_admission_context", unavailable)
            await loop._collect_plan_decision(intent, object(), mission, fixture["raw"], dispatch)

            assert _revision_hashes(loop.store, mission.id) == before_hashes
            assert (
                tuple(
                    row[0]
                    for row in loop.store.connection.execute(
                        "SELECT intent_id FROM dispatch_intents ORDER BY intent_id"
                    )
                )
                == before_intents
            )
            decision = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(
                intent.intent_id, 0
            )
            assert decision is not None
            assert decision["status"] == str(PlanningDecisionStatus.COMMIT_REJECTED)
            assert decision["rejection_codes"] == ["INTERNAL_CONTRACT_ERROR"]
            assert decision["canonical_hash"] is not None
            assert provider.calls == 0

    asyncio.run(recover())


@pytest.mark.parametrize("revoke_before_replay", (False, True))
def test_i05_exit_after_durable_commit_replays_exactly_without_new_writes(
    tmp_path: Path, revoke_before_replay: bool
) -> None:
    fixture = _run_child(tmp_path, "after_durable_commit", 92)

    async def recover() -> None:
        provider = RoleScriptedProvider({"planner": []})
        async with _reopen(tmp_path, provider) as world:
            loop = world.loop
            mission = loop.store.get_mission(fixture["mission_id"])
            intent = loop.store.get_intent(fixture["intent_id"])
            assert mission is not None and intent is not None
            dispatch = loop._dispatch_for(mission.id)
            if revoke_before_replay:
                _revoke(world, mission, intent.intent_id, "revoke-h1i-i05-durable",
                        "terminal replay must not reauthorize")

            before = (
                _revision_hashes(loop.store, mission.id),
                HtnStore(loop.store).list_commit_receipts(mission.id),
                tuple(loop.store.list_events(mission.id)),
                tuple(
                    tuple(row)
                    for row in loop.store.connection.execute(
                        "SELECT * FROM dispatch_intents ORDER BY intent_id"
                    )
                ),
                tuple(loop.store.list_actions(mission.id)),
            )
            stored_before = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(
                intent.intent_id, 0
            )
            assert stored_before is not None
            assert stored_before["status"] == str(PlanningDecisionStatus.COMMITTED)

            await loop._collect_plan_decision(intent, object(), mission, fixture["raw"], dispatch)

            after = (
                _revision_hashes(loop.store, mission.id),
                HtnStore(loop.store).list_commit_receipts(mission.id),
                tuple(loop.store.list_events(mission.id)),
                tuple(
                    tuple(row)
                    for row in loop.store.connection.execute(
                        "SELECT * FROM dispatch_intents ORDER BY intent_id"
                    )
                ),
                tuple(loop.store.list_actions(mission.id)),
            )
            assert before[0] and before[1] and before[3]
            assert after == before
            assert (
                PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(
                    intent.intent_id, 0
                )
                == stored_before
            )
            assert provider.calls == 0

    asyncio.run(recover())
