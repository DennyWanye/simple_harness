"""V2 and settlement are distinct public call observations, never SDK grants."""
import asyncio
import json
import sqlite3
from dataclasses import replace

import pytest
import simple_harness_memory as m

from deskpet.operation_audit.prospective_sources import ProspectiveSourceJournal
from tests.operation_audit.test_prospective_sources import world, exact
from tests.memory.test_s5c_store import P

V2 = "read_prospective_outbox_source_v2"
SETTLE = "settle_prospective_invalidation"


async def args(world):
    manager, entry, *_ = world
    source = await manager.read_prospective_outbox_source_v2(
        principal=P, outbox_id=entry.outbox_id, payload_hash=entry.payload_hash)
    return dict(principal=P, outbox_id=entry.outbox_id, payload_hash=entry.payload_hash,
                expected_source_hash=source.source_hash)


@pytest.mark.asyncio
async def test_v2_result_cannot_promote_real_v1_observation(world, tmp_path, monkeypatch):
    manager, entry, *_ = world
    request = dict(principal=P, outbox_id=entry.outbox_id, payload_hash=entry.payload_hash)
    old = await manager.read_prospective_outbox_source(**request)
    original = manager.read_prospective_outbox_source_v2
    async def swapped(**kwargs):
        return replace(await original(**kwargs), operation_observation=old.operation_observation)
    monkeypatch.setattr(manager, V2, swapped)
    journal = ProspectiveSourceJournal(tmp_path / "successor-audit.db")
    result = await journal.read_prospective_outbox_source(manager, **request, operation=V2)
    assert type(result) is m.ProspectiveOutboxSourceViewV2
    row = (await journal.page(principal=P, operation=V2))["items"][0]
    assert row["state"] == "returned" and row["observation_status"] == "unverifiable"
    assert row["observation_json"] is None and row["observation_hash"] is None


@pytest.mark.asyncio
async def test_settlement_public_rejection_retains_exact_operation_binding(world, tmp_path):
    from simple_harness_memory.core.operation_audit import _hash
    request = await args(world)
    request["expected_source_hash"] = "0" * 64
    journal = ProspectiveSourceJournal(tmp_path / "successor-audit.db")
    with pytest.raises(m.MemoryValidationError) as rejected:
        await journal.settle_prospective_invalidation(world[0], **request)
    observation = rejected.value.operation_observation
    assert observation.operation == SETTLE and observation.reason == "input_or_binding_rejected"
    assert observation.request_hash == _hash("memory.prospective.invalidation.settlement.request.v1",
        [request["outbox_id"], request["payload_hash"], request["expected_source_hash"]])
    row = (await journal.page(principal=P, operation=SETTLE))["items"][0]
    exact(row, observation, "raised")
    assert json.loads(row["observation_json"])["source_hash"] is None


@pytest.mark.asyncio
async def test_settlement_sdk_cancellation_preserves_its_distinct_reason(world, tmp_path, monkeypatch):
    request = await args(world)
    entered = asyncio.Event()
    async def suspended(**kwargs):
        entered.set()
        await asyncio.Future()
    # Only hold the backend seam. The real public Manager produces and attaches
    # the cancellation observation, and the Host captures that original value.
    monkeypatch.setattr(world[0]._backend, SETTLE, suspended)
    journal = ProspectiveSourceJournal(tmp_path / "successor-audit.db")
    task = asyncio.create_task(journal.settle_prospective_invalidation(world[0], **request))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await task
    observation = cancelled.value.operation_observation
    assert observation.reason == "settlement_cancelled" and observation.operation == SETTLE
    row = (await journal.page(principal=P, operation=SETTLE))["items"][0]
    exact(row, observation, "cancelled")


@pytest.mark.asyncio
async def test_settlement_host_precall_cancel_has_no_sdk_finding(world, tmp_path, monkeypatch):
    request = await args(world)
    called = False
    async def forbidden(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("SDK should not be invoked")
    monkeypatch.setattr(world[0], SETTLE, forbidden)
    def fault(point):
        if point == "prospective_audit.after_started":
            raise asyncio.CancelledError()
    journal = ProspectiveSourceJournal(tmp_path / "successor-audit.db", fault=fault)
    with pytest.raises(asyncio.CancelledError):
        await journal.settle_prospective_invalidation(world[0], **request)
    assert not called
    with sqlite3.connect(journal.path) as db:
        assert db.execute("SELECT count(*) FROM memory_call_findings").fetchone() == (0,)
