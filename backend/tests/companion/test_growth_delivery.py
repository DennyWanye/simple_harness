from __future__ import annotations

import pytest

from deskpet.companion.signals import (
    GrowthDeliveryTarget,
    GrowthTerminalDeliverySink,
)
from deskpet.companion.store import CompanionStore
from deskpet.execution.contracts import (
    OutcomeStatus,
    RunEvent,
    RunEventCandidate,
)
from deskpet.harness.projector import DeliveryDiscarded


def _terminal(event_id: str = "event-final") -> RunEvent:
    return RunEvent(
        event_id=event_id,
        run_id="run-1",
        root_run_id="run-1",
        session_id="session-1",
        durable_seq=7,
        candidate=RunEventCandidate(
            event_key="final",
            kind="final",
            status=OutcomeStatus.SUCCEEDED,
            driver_kind="react",
            payload={"text": "not copied into growth evidence"},
            artifact_refs=("artifact:1",),
        ),
        created_at=1.0,
    )


@pytest.mark.asyncio
async def test_terminal_sink_is_idempotent_and_stores_only_result_hash(tmp_path):
    store = CompanionStore(tmp_path / "companion.db")
    owner = store.create_profile(
        profile_id="profile-a",
        generation=1,
        identity_namespace_hash="a" * 64,
    )
    target = GrowthDeliveryTarget(owner).target_id
    sink = GrowthTerminalDeliverySink(store)

    await sink.deliver(_terminal(), target)
    await sink.deliver(_terminal(), target)

    with store.read() as db:
        rows = db.execute(
            "SELECT source_ref,payload_json FROM growth_events"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["source_ref"] == "terminal:event-final"
    assert "not copied into growth evidence" not in rows[0]["payload_json"]
    assert "result_payload_hash" in rows[0]["payload_json"]


@pytest.mark.asyncio
async def test_deleted_owner_is_tombstoned_not_retargeted(tmp_path):
    store = CompanionStore(tmp_path / "companion.db")
    owner = store.create_profile(
        profile_id="profile-a",
        generation=1,
        identity_namespace_hash="a" * 64,
    )
    target = GrowthDeliveryTarget(owner).target_id
    store.delete_profile_generation(owner, reason_code="user_deleted")
    sink = GrowthTerminalDeliverySink(store)

    assert await sink.is_bound(target) is False
    with pytest.raises(DeliveryDiscarded, match="owner_deleted"):
        await sink.deliver(_terminal(), target)


@pytest.mark.asyncio
async def test_child_terminal_cannot_create_a_duplicate_growth_projection(tmp_path):
    store = CompanionStore(tmp_path / "companion.db")
    owner = store.create_profile(
        profile_id="profile-a",
        generation=1,
        identity_namespace_hash="a" * 64,
    )
    target = GrowthDeliveryTarget(owner).target_id
    sink = GrowthTerminalDeliverySink(store)
    event = _terminal()
    child = RunEvent(
        event_id=event.event_id,
        run_id="child-1",
        root_run_id=event.root_run_id,
        session_id=event.session_id,
        durable_seq=event.durable_seq,
        candidate=event.candidate,
        created_at=event.created_at,
    )

    with pytest.raises(ValueError, match="child_terminal_forbidden"):
        await sink.deliver(child, target)
