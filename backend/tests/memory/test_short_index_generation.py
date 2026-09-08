"""Public worker/SDK generation wiring with a tiny test-only embedder, never WeMM."""
import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
import pytest_asyncio
from simple_harness_memory.embedders.base import Embedder, EmbeddingLineage

from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.memory.short_index_worker import PrimaryShortIndexWorker
from tests.memory.test_short_index_worker import seed  # noqa: F401
from tests.memory.test_primary_short_ingestion import recall


class TinyEmbedder(Embedder):
    kind = 'test-short-worker-public'
    dim = 2

    def __init__(self):
        self.batches = 0
        self.revision = '1'
        self.fingerprint = 'json-float-array:v1'
        self.mode = 'ok'
        self.entered = asyncio.Event()

    @property
    def lineage(self):
        return EmbeddingLineage(self.kind, 'test-only', 'two-dimensional', self.revision,
            self.dim, 'l2', self.fingerprint)

    async def embed(self, text):
        return [1.0, 0.0]

    async def embed_batch(self, texts):
        self.batches += 1
        self.entered.set()
        if self.mode == 'error':
            raise RuntimeError('test-generation-failed')
        if self.mode == 'wait':
            await asyncio.Event().wait()
        return [await self.embed(text) for text in texts]


@pytest_asyncio.fixture
async def gen(tmp_path, seed):
    state = tmp_path / 'state.db'
    with sqlite3.connect(seed) as source, sqlite3.connect(state) as dest:
        source.backup(dest)
    embedder = TinyEmbedder()
    runtime = compose_human_memory_runtime(state, tmp_path / 'memory.db',
        embedder_getter=lambda: embedder,
        adapter_factory=lambda *_: pytest.fail('no analysis transport in this test'))
    assert runtime.build_kwargs()['short_horizon_embedder'] is embedder
    delivery = MemoryIngestionOutboxWorker(state, runtime.manager, owner_id='generation-test')
    for _ in range(11):
        assert await delivery.run_once() == 'delivered'
    clock = [0.0]
    worker = PrimaryShortIndexWorker(runtime, monotonic=lambda: clock[0])
    try:
        yield SimpleNamespace(runtime=runtime, embedder=embedder, worker=worker, clock=clock)
    finally:
        await worker.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_worker_public_generation_query_maintenance_and_reopen(gen):
    first = await gen.worker.step()
    assert first.generation.activated and first.generation.vector_count == 1
    assert gen.embedder.batches == 1
    hit = await recall(await gen.runtime.manager(), 'quartznebula')
    assert hit.used_generation_id == first.generation.generation_id and len(hit.hits) == 1
    assert (await gen.worker.step()).generation is None
    gen.clock[0] = 61.0
    replay = await gen.worker.step()
    assert replay.generation.replayed and replay.generation.generation_id == first.generation.generation_id
    await gen.runtime.close()
    reopened = await gen.worker.step()
    assert reopened.generation.replayed and reopened.generation.generation_id == first.generation.generation_id
    assert gen.embedder.batches == 1


@pytest.mark.asyncio
async def test_worker_generation_failure_does_not_confirm_and_lost_ack_reuses(gen):
    gen.embedder.mode = 'error'
    with pytest.raises(RuntimeError, match='test-generation-failed'):
        await gen.worker.step()
    assert not gen.worker._confirmed and gen.worker._generation_pending
    assert gen.worker._last_projection is None
    # 2026-09-08 HM-TO-A6：失败后进入退避窗口，同一次注定失败的重建不再每个
    # tick 全额重试（那正是 65% 写锁占空比的来源）。窗口内的 tick 只跳过。
    assert gen.worker._maintenance_failures == 1
    skipped = await gen.worker.step()
    assert skipped.maintenance_skipped and skipped.generation is None
    assert skipped.retry_after_seconds == pytest.approx(60.0)
    assert gen.embedder.batches == 1  # 跳过的 tick 没有产生任何嵌入
    gen.embedder.mode = 'ok'
    def fail(point):
        if point == 'short.after_generation':
            raise RuntimeError('test-generation-lost-ack')
    gen.worker._fault_hook = fail
    gen.clock[0] = 61.0  # 越过第一次退避
    with pytest.raises(RuntimeError, match='test-generation-lost-ack'):
        await gen.worker.step()
    assert not gen.worker._confirmed and gen.worker._generation_pending
    assert gen.embedder.batches == 2
    # 连续第二次失败：退避指数增长（60 → 120），仍受上限约束。
    assert gen.worker._maintenance_failures == 2
    gen.worker._fault_hook = None
    gen.clock[0] = 182.0
    replay = await gen.worker.step()
    assert replay.generation.replayed and len(gen.worker._confirmed) == 11
    assert gen.embedder.batches == 2
    # 成功即清零：下一次失败重新从基数开始，不带着历史惩罚。
    assert gen.worker._maintenance_failures == 0 and gen.worker._retry_after is None


@pytest.mark.asyncio
@pytest.mark.parametrize('interrupt', ['timeout', 'cancel'])
async def test_confirmed_cache_does_not_hide_generation_interruption(gen, interrupt):
    await gen.worker.step()
    assert len(gen.worker._confirmed) == 11
    gen.clock[0] = 61.0
    gen.embedder.revision = '2'
    gen.embedder.fingerprint = 'test-short-worker:revision-2:dim-2'
    gen.embedder.mode = 'wait'
    gen.embedder.entered.clear()
    gen.worker.maintenance_timeout = 0.5
    task = asyncio.create_task(gen.worker.step())
    try:
        await asyncio.wait_for(gen.embedder.entered.wait(), 3)
        if interrupt == 'cancel':
            task.cancel()
        with pytest.raises(asyncio.CancelledError if interrupt == 'cancel' else TimeoutError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert gen.worker._generation_pending and len(gen.worker._confirmed) == 11
    # 超时算一次维护失败并进入退避；取消不算（取消不是"这次重建做不完"的证据）。
    assert gen.worker._maintenance_failures == (0 if interrupt == 'cancel' else 1)
    gen.embedder.mode = 'ok'
    gen.worker.maintenance_timeout = 60.0
    gen.clock[0] = 200.0  # 越过退避窗口（timeout 分支）
    recovered = await gen.worker.step()
    assert recovered.generation.activated and not gen.worker._generation_pending
    # One committed old generation, one interrupted attempt, one successful retry.
    assert gen.embedder.batches == 3


@pytest.mark.asyncio
async def test_cold_shared_load_survives_bounded_worker_timeouts_without_confirmation(gen):
    from deskpet.memory.short_indexing import PrimaryShortIndexingService

    # Prepare the real conversation sources only. Generation remains exclusively
    # the worker's public call; no model is loaded or index built by the test.
    manager = await gen.runtime.manager()
    await PrimaryShortIndexingService(gen.runtime.conversation_evidence_authority,
        manager=manager, principal=gen.runtime.principal()).reconcile()
    assert gen.worker.operation_timeout == 5.0
    gen.worker.maintenance_timeout = 0.5  # scaled deadline; the load waits past it
    release = asyncio.Event()
    load_task = None
    loads = active = peak = attempts = 0
    original = gen.embedder.embed_batch

    async def shared_load():
        await release.wait()

    async def cold_batch(texts):
        nonlocal load_task, loads, active, peak, attempts
        attempts += 1
        active += 1
        peak = max(peak, active)
        try:
            if load_task is None:
                loads += 1
                load_task = asyncio.create_task(shared_load())
            await asyncio.shield(load_task)
            return await original(texts)
        finally:
            active -= 1

    gen.embedder.embed_batch = cold_batch
    tasks = [asyncio.create_task(gen.worker.step()) for _ in range(2)]
    try:
        outcomes = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
        # 2026-09-08 HM-TO-A6：第一次超时后进入退避，第二个 tick 只跳过——
        # 它比原来的「两次全额重试」更强：冷加载期间连第二次尝试都不发生。
        assert sum(isinstance(outcome, TimeoutError) for outcome in outcomes) == 1
        skipped = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
        assert len(skipped) == 1 and skipped[0].maintenance_skipped
        assert attempts == 1 and loads == 1 and peak == 1
        assert load_task is not None and not load_task.done()
        assert not gen.worker._confirmed and gen.worker._last_projection is None
        assert gen.worker._generation_pending and gen.clock[0] == 0.0
        release.set()
        await load_task
        gen.worker.maintenance_timeout = 60.0
        gen.clock[0] = 61.0  # 只越过退避窗口，不是 maintenance_seconds 到期
        recovered = await gen.worker.step()
        assert recovered.generation.activated
        assert not gen.worker._generation_pending and gen.worker._confirmed
        assert loads == 1 and peak == 1 and gen.embedder.batches == 1
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        release.set()
        if load_task is not None:
            await load_task
