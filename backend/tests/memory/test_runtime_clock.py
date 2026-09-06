"""Real public SDK clock behavior; no Provider, model, or backend replacement.

Run this file directly under scripts/run_resource_bounded.py with the Host
backend on PYTHONPATH. Empty stores deliberately provide no quality evidence.
"""

import asyncio
from datetime import datetime
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from deskpet.memory.analysis_executor import AnalysisLeaseExpired, AnalysisLeaseFence
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from simple_harness.runtime import (
    DeliveryRecipient, DisclosureContext, DisclosureGeneration,
    DisclosurePurpose, DisclosureReasonCode, DisclosureSource,
    DisclosureTrust, IntendedAudience,
)
from simple_harness_memory import HistoryRecallBinding


C04_DEFAULT = datetime.fromisoformat("2026-09-06T10:00:00+08:00").timestamp()
C04_15 = datetime.fromisoformat("2026-09-30T18:00:00+08:00").timestamp()


class MutableClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def no_adapter(*args, **kwargs):
    raise AssertionError("clock contract must never construct a Provider adapter")


class RuntimeClockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="clock-store-", dir=os.environ.get("CLOCK_TEST_ARTIFACT_ROOT"),
        )
        self.root = Path(self.temp.name)
        self.runtimes = []
        self.ordinal = 0

    async def asyncTearDown(self):
        try:
            for runtime in self.runtimes:
                await runtime.close()
        finally:
            self.temp.cleanup()

    def compose(self, **kwargs):
        runtime = compose_human_memory_runtime(
            self.root / "state.db", self.root / "memory.db",
            adapter_factory=no_adapter, **kwargs,
        )
        self.runtimes.append(runtime)
        return runtime

    async def observe(self, runtime, **kwargs):
        self.ordinal += 1
        run_id = f"clock-contract-{self.ordinal}"
        lanes = await runtime.typed_recall(
            query="三季度的记录", run_id=run_id, turn_ordinal=self.ordinal,
            memory_types=("episode", "prospective"),
            include_short_horizon=False, **kwargs,
        )
        result = lanes.execution.result
        self.assertEqual(result.items, ())
        subject = runtime.principal().actor_id
        disclosure = DisclosureContext(
            run_id, subject, DeliveryRecipient.USER_SELF, subject,
            IntendedAudience.USER_SELF, DisclosurePurpose.TASK_EXECUTION,
            DisclosureSource.AUTHENTICATED_HOST, DisclosureTrust.TRUSTED_AUTHORITY,
            DisclosureGeneration.CURRENT, "host:clock-contract:v1",
            (DisclosureReasonCode.MINIMUM_NECESSARY,),
        )
        # Public independent backend time: this API has no request `now`.
        # The deliberately missing item must stay denied, never a legal seed.
        manager = await runtime.manager()
        history = await manager.check_history_visibility(
            principal=runtime.principal(), disclosure_context=disclosure,
            bindings=(HistoryRecallBinding(
                result.result_id, result.result_hash, "absent-item", "0" * 64,
            ),),
        )
        self.assertEqual(len(history.items), 1)
        self.assertFalse(history.items[0].visible)
        return lanes.execution, history

    async def test_c04_instants_reach_typed_recall_and_independent_sdk_clock(self):
        clock = MutableClock(C04_DEFAULT)
        runtime = self.compose(clock=clock)
        for instant in (C04_DEFAULT, C04_15):
            with self.subTest(instant=instant):
                clock.value = instant
                execution, history = await self.observe(runtime)
                self.assertEqual(execution.result.evaluated_at, instant)
                self.assertEqual(execution.decision.decided_at, instant)
                self.assertEqual(history.checked_at, instant)

    async def test_reopen_uses_new_constructor_clock(self):
        first = self.compose(clock=lambda: C04_DEFAULT)
        await self.observe(first)
        await first.close()
        second = self.compose(clock=lambda: C04_15)
        execution, history = await self.observe(second)
        self.assertEqual(execution.result.evaluated_at, C04_15)
        self.assertEqual(history.checked_at, C04_15)

    async def test_default_clock_remains_real_time(self):
        before = time.time()
        execution, history = await self.observe(self.compose())
        after = time.time()
        for value in (execution.result.evaluated_at, history.checked_at):
            self.assertGreaterEqual(value, before)
            self.assertLessEqual(value, after)

    async def test_internal_now_does_not_reconfigure_sdk_clock(self):
        runtime = self.compose(clock=lambda: C04_DEFAULT)
        execution, history = await self.observe(runtime, now=C04_15)
        self.assertEqual(execution.result.evaluated_at, C04_15)
        self.assertEqual(history.checked_at, C04_DEFAULT)
        execution, history = await self.observe(runtime)
        self.assertEqual(execution.result.evaluated_at, C04_DEFAULT)
        self.assertEqual(history.checked_at, C04_DEFAULT)

    async def test_non_callable_clock_rejected_without_store_creation(self):
        for constructor in (
            lambda: self.compose(clock=C04_15),
            lambda: HumanMemoryV7Runtime(self.root / "invalid.db", clock=None),
        ):
            with self.assertRaisesRegex(TypeError, "clock must be callable"):
                constructor()
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_physical_lease_ignores_wall_clock_jumps_and_expires(self):
        store = AsyncMock()
        fence = AnalysisLeaseFence(store, lease_seconds=60)
        fence.bind(host_run_id="lease-host", sdk_run_id="lease-sdk")
        await fence.reserve_attempt({}, ())
        for wall_time in (0.0, C04_15, C04_15 + 1e12):
            with patch("time.time", return_value=wall_time):
                await fence.revalidate()
        # A frozen business date cannot stop the physical elapsed lease.
        short = AnalysisLeaseFence(store, lease_seconds=0.005)
        short.bind(host_run_id="short-host", sdk_run_id="short-sdk")
        with patch("time.time", return_value=C04_15):
            await short.reserve_attempt({}, ())
            await asyncio.sleep(0.02)
            with self.assertRaises(AnalysisLeaseExpired):
                await short.revalidate()


if __name__ == "__main__":
    unittest.main(verbosity=2)
