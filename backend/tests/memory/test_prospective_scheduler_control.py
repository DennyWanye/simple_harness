"""Scheduler control ordering only; spies do not claim SQLite/SDK proof."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from deskpet.memory.prospective_scheduler import ProspectiveScheduler


def setup(monkeypatch, *, handed_off):
    monkeypatch.setattr('deskpet.memory.prospective_scheduler.assert_human_memory_ingress_open',AsyncMock())
    order=[]
    claim=NS(handed_off=handed_off,reference=object(),
        authority=NS(intent=NS(scope=NS(kind=NS(value='personal'),owner_id='actor'))))
    queue=[claim]
    async def acquire(**kwargs):
        return queue.pop(0) if queue else None
    async def handoff(value,**kwargs):
        order.append('handoff');return value
    async def apply(**kwargs):
        order.append('apply');assert kwargs['reference'] is claim.reference
        return object()
    async def applied(*args,**kwargs):order.append('applied')
    store=NS(path='unused',principal=object(),claim=acquire,assert_claim=AsyncMock(),
        handoff=handoff,applied=applied,invalidate=AsyncMock(),prepare=AsyncMock())
    source=NS(registration_is_live=AsyncMock(return_value=False),prepare_due=AsyncMock(return_value=()))
    memory=NS(apply_prospective_signal=AsyncMock(side_effect=apply))
    return ProspectiveScheduler(store=store,source=source,memory=memory,clock=lambda:100),source,store,memory,order


@pytest.mark.asyncio
async def test_handoff_recovery_precedes_source_scan_and_skips_old_head(monkeypatch):
    scheduler,source,store,memory,order=setup(monkeypatch,handed_off=True)
    async def scan(**kwargs):
        assert order==['handoff','apply','applied']
        return ()
    source.prepare_due.side_effect=scan
    assert await scheduler.tick(claim_owner='worker')==1
    source.registration_is_live.assert_not_awaited()
    store.invalidate.assert_not_awaited()
    assert memory.apply_prospective_signal.await_count==1


@pytest.mark.asyncio
async def test_fresh_invalidated_after_claim_never_handed_to_memory(monkeypatch):
    scheduler,source,store,memory,order=setup(monkeypatch,handed_off=False)
    assert await scheduler.tick(claim_owner='worker')==0
    source.registration_is_live.assert_awaited_once()
    store.invalidate.assert_awaited_once()
    memory.apply_prospective_signal.assert_not_awaited()
    assert order==[]


@pytest.mark.asyncio
async def test_exception_after_handoff_is_not_invalidated_or_settled(monkeypatch):
    scheduler,source,store,memory,order=setup(monkeypatch,handed_off=False)
    source.registration_is_live.return_value=True
    memory.apply_prospective_signal.side_effect=TimeoutError('lost ACK')
    with pytest.raises(TimeoutError,match='lost ACK'):
        await scheduler.tick(claim_owner='worker')
    assert order==['handoff']
    store.invalidate.assert_not_awaited()
