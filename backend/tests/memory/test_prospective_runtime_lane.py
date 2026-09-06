"""New production lifecycle, real public SDK registration/time/recovery.

The analysis worker wait is a scheduling control, not a model execution.
"""
import asyncio
from dataclasses import replace

import pytest
import simple_harness_memory as m

from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.memory.s5c_store import S5cStore
from deskpet.memory.prospective_runtime import REMINDER_CAPABILITY
from tests.memory.test_prospective_consumer_m617 import World
from tests.memory.test_s5c_store import P


async def until(predicate, *, timeout=4.0):
    async def wait():
        while not await predicate():
            await asyncio.sleep(.01)
    await asyncio.wait_for(wait(), timeout)


async def world(root):
    w = World(root)
    def no_provider(*args, **kwargs):
        raise AssertionError("this control must not invoke a model")
    w.runtime = compose_human_memory_runtime(w.path, root / 'memory.db',
        adapter_factory=no_provider, principal=P, clock=lambda: w.clock[0])
    # Existing public setup uses this ACTUAL composition-owned Manager. It
    # does not inject a test signal authority or call a consumer/timer itself.
    async def open_actual():
        w.manager = await w.runtime.manager()
    w.open = open_actual
    await w.setup()
    w.runtime.prospective_lane.poll_seconds = .01
    return w


def activate_main(monkeypatch, w):
    import main
    from context import ServiceContext
    services = ServiceContext()
    services.register('human_memory_v7_runtime', w.runtime)
    services.register('sdk_provider_binding_resolver', object())  # not used by timer
    monkeypatch.setattr(main, 'service_context', services)
    monkeypatch.setattr(main, '_state_db_path', w.path)
    monkeypatch.setattr(main, '_memory_analysis_lane', None)
    monkeypatch.setattr(main, '_provider_registry', None)
    main._activate_memory_analysis_lane()
    return main._memory_analysis_lane


@pytest.mark.asyncio
async def test_default_main_lane_ticks_while_analysis_waits_and_reopens_once(tmp_path, monkeypatch):
    w = await world(tmp_path)
    memory_id = await w.mutate('actual-time-reminder')
    entered, release = asyncio.Event(), asyncio.Event()
    class WaitingAnalysis:
        async def run_once(self):
            entered.set()
            await release.wait()
            return m.WorkerRunOutcome.IDLE
    async def job_runner(*args, **kwargs):
        return WaitingAnalysis()
    monkeypatch.setattr(w.runtime, 'job_runner', job_runner)
    lane = activate_main(monkeypatch, w)
    timer_task = w.runtime.prospective_lane._task
    lane.start()
    assert w.runtime.prospective_lane._task is timer_task
    try:
        await entered.wait()
        async def registered():
            return await S5cStore(w.path, P).accepted_registration(memory_id=memory_id, revision=1) is not None
        await until(registered)
        assert not (await w.manager.read_occurrence_inbox(principal=P)).entries
        w.clock[0] = 30.0
        async def due():
            return bool((await w.manager.read_occurrence_inbox(principal=P)).entries)
        await until(due)
        assert not release.is_set()  # timer did not wait for model/job completion
        entry, = (await w.manager.read_occurrence_inbox(principal=P)).entries
        assert entry.memory_id == memory_id
        assert w.runtime.prospective_lane.last_registration_error is None
        assert w.runtime.prospective_lane.last_timer_error is None
    finally:
        release.set()
        await lane.close()
        assert timer_task.done() and lane._task is None
        await w.runtime.close()
    reopened = compose_human_memory_runtime(w.path, tmp_path / 'memory.db',
        adapter_factory=lambda *a, **k: None, principal=P, clock=lambda: w.clock[0])
    reopened.prospective_lane.poll_seconds = .01
    try:
        reopened.prospective_lane.start()
        async def ticked():
            return reopened.prospective_lane.completed_ticks >= 2
        await until(ticked)
        items = (await (await reopened.manager()).read_occurrence_inbox(principal=P)).entries
        assert [e.occurrence_key for e in items] == [entry.occurrence_key]
        assert 'do not claim a reminder is already scheduled' in REMINDER_CAPABILITY
    finally:
        await reopened.prospective_lane.close()
        await reopened.close()


@pytest.mark.asyncio
async def test_timer_lost_ack_recovers_actual_reference_without_registration_scan(tmp_path, monkeypatch):
    from deskpet.memory.s5c_consumer import ProspectiveRegistrationConsumer
    w = await world(tmp_path)
    await w.mutate('recover-reminder')
    lane = w.runtime.prospective_lane
    references = []
    original = w.manager.apply_prospective_signal
    async def lost(**kwargs):
        result = await original(**kwargs)
        if kwargs['reference'].authority_id.startswith('host:time-authority:'):
            references.append(kwargs['reference'])
            if len(references) == 1:
                raise TimeoutError('actual SDK commit followed by lost Host ACK')
        return result
    monkeypatch.setattr(w.manager, 'apply_prospective_signal', lost)
    try:
        lane.start()
        # The real registration count is checked from its actual returned page.
        async def acked():
            rows, _, _ = await S5cStore(w.path, P).page_accepted_registrations(after=0, upper=None, limit=1)
            return bool(rows)
        await until(acked)
        w.clock[0] = 30.0
        async def lost_once():
            return len(references) == 1 and lane.last_timer_error is not None
        await until(lost_once)
        first, = (await w.manager.read_occurrence_inbox(principal=P)).entries
        await lane.close()
        await w.runtime.close()
        w.clock[0] = 400.0  # past lease and grant; consumed SDK result must replay
        reopened = compose_human_memory_runtime(w.path, tmp_path / 'memory.db',
            adapter_factory=lambda *a, **k: None, principal=P, clock=lambda: w.clock[0])
        reopened.prospective_lane.poll_seconds = .01
        actual = await reopened.manager()
        apply = actual.apply_prospective_signal
        async def replay(**kwargs):
            if kwargs['reference'].authority_id.startswith('host:time-authority:'):
                references.append(kwargs['reference'])
            return await apply(**kwargs)
        monkeypatch.setattr(actual, 'apply_prospective_signal', replay)
        async def registration_broken(self, **kwargs):
            raise OSError('controlled public registration read failure')
        monkeypatch.setattr(ProspectiveRegistrationConsumer, 'run_once', registration_broken)
        try:
            reopened.prospective_lane.start()
            async def recovered():
                return len(references) >= 2 and reopened.prospective_lane.last_applied_count == 1
            await until(recovered)
            assert references == [references[0], references[0]]
            assert reopened.prospective_lane.last_registration_error is not None
            entries = (await actual.read_occurrence_inbox(principal=P)).entries
            assert [e.occurrence_key for e in entries] == [first.occurrence_key]
        finally:
            await reopened.prospective_lane.close()
            await reopened.close()
    finally:
        await lane.close()
        await w.runtime.close()


@pytest.mark.asyncio
async def test_shutdown_joins_cancelled_worker_and_suppression_prevents_time_match(tmp_path):
    w = await world(tmp_path)
    memory_id = await w.mutate('forgotten-reminder')
    lane = w.runtime.prospective_lane
    lane.start()
    try:
        async def registered():
            return await S5cStore(w.path, P).accepted_registration(memory_id=memory_id, revision=1) is not None
        await until(registered)
        await w.manager.suppress(principal=P, request=m.SuppressionRequest(
            'forget-reminder', P.actor_id, m.SuppressionScopeKind.MEMORY, memory_id, 'user_forget', 25.0))
        w.clock[0] = 30.0
        initial = lane.completed_ticks
        async def after_due():
            return lane.completed_ticks >= initial + 2
        await until(after_due)
        assert not [e for e in (await w.manager.read_occurrence_inbox(principal=P)).entries if not e.suppressed and e.outcome == 'matched']
        task = lane._task
        await lane.close()
        assert task.done() and lane._task is None
        lane.start()
        restarted = lane._task
        assert restarted is not None and restarted is not task
        lane.start()
        assert lane._task is restarted
        await lane.close()
        assert restarted.done()
    finally:
        await lane.close()
        await w.runtime.close()


@pytest.mark.asyncio
async def test_parent_repeated_cancel_waits_for_owned_timer_and_index_cleanup():
    from types import SimpleNamespace
    from deskpet.memory.memory_ingestion_outbox import MemoryAnalysisLane
    timer_started, timer_release = asyncio.Event(), asyncio.Event()
    index_started, index_release = asyncio.Event(), asyncio.Event()
    class Child:
        async def close(self):
            timer_started.set()
            await timer_release.wait()
    class Index:
        async def close(self):
            index_started.set()
            await index_release.wait()
    lane = MemoryAnalysisLane(worker=None, runtime=SimpleNamespace(prospective_lane=Child()),
        executor=None, config=None, worker_id='cleanup-owner')
    lane.short_indexer = Index()
    lane._task = asyncio.create_task(asyncio.sleep(0))
    worker = lane._task
    closing = asyncio.create_task(lane.close())
    await timer_started.wait()
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()
    timer_release.set()
    await index_started.wait()
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()
    index_release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert worker.done() and lane._task is None
    assert not [t for t in asyncio.all_tasks() if t.get_name() == 'memory-analysis-lane-cleanup' and not t.done()]
