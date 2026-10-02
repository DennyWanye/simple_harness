"""Capacity survives real BaseAgent request restoration and meter nesting."""

import asyncio
import os

import pytest
from test_meter_terminal_admission import ScriptedPhysicalProvider, make_meter, request

from simple_harness.execution.deployment_capacity import CapacityProvider, DeploymentCapacity
from simple_harness.execution.shared_capacity import CapacityLedger
from simple_harness.providers import CancelToken, ProviderUsage


@pytest.mark.anyio
async def test_direct_meter_cancel_while_shared_queue_is_known_zero(tmp_path):
    ledger = CapacityLedger(tmp_path / "capacity.db", pool_id="local", max_slots=1, max_tokens=100)
    cap = DeploymentCapacity(ledger, estimate=lambda request: 60)
    held = ledger.enqueue("holder", weight=100, owner="test", pid=os.getpid())
    assert ledger.try_acquire(held)
    physical = ScriptedPhysicalProvider(ProviderUsage(1, 1, 2))
    meter = make_meter(CapacityProvider(physical, cap))
    task = asyncio.create_task(meter.invoke(request("queued"), cancel=CancelToken()))
    async with asyncio.timeout(2):
        while not cap.waiting("queued"):
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert physical.calls == meter.counters.calls == meter.unknown_usage_calls == 0
    assert meter.admission_denials[0]["physical_calls"] == 0
    ledger.cancel_waiter(held)
    assert ledger.snapshot().held_slots == 0


@pytest.mark.anyio
async def test_direct_meter_and_capacity_provider_do_not_double_reserve(tmp_path):
    ledger = CapacityLedger(tmp_path / "capacity.db", pool_id="local", max_slots=1, max_tokens=100)
    cap = DeploymentCapacity(ledger, estimate=lambda request: 60)
    physical = ScriptedPhysicalProvider(ProviderUsage(1, 1, 2))
    meter = make_meter(CapacityProvider(physical, cap))
    await meter.invoke(request("one"), cancel=CancelToken())
    assert physical.calls == meter.counters.calls == 1
    assert len(ledger.snapshot().rows) == 1
    assert ledger.snapshot().held_slots == 0


@pytest.mark.anyio
async def test_response_deadline_is_bounded_and_unknown_capacity_stays_held(tmp_path):
    ledger = CapacityLedger(tmp_path / "capacity.db", pool_id="local", max_slots=1, max_tokens=100)
    cap = DeploymentCapacity(ledger, estimate=lambda request: 60, call_seconds=0.1)
    entered = asyncio.Event()

    class HungProvider:
        async def invoke(self, request, *, cancel):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(
        CapacityProvider(HungProvider(), cap).invoke(request("hung"), cancel=CancelToken())
    )
    await entered.wait()
    assert cap.response_waiting("hung") and not cap.waiting("hung")
    with pytest.raises(TimeoutError):
        await task
    assert not cap.response_waiting("hung")
    assert ledger.snapshot().rows[0].state == "UNKNOWN"
    assert ledger.snapshot().held_slots == 1
