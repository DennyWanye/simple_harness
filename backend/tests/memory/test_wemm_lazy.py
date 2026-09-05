"""No model packages/weights: exercise real thread cancellation with fake factory."""
import asyncio
import builtins
import sys
import threading
from types import SimpleNamespace

import pytest

from deskpet.memory.wemm_embedder import WeMMEmbedder


@pytest.fixture
def fake_model(monkeypatch):
    state = SimpleNamespace(loads=0, calls=[], active=0, peak=0, dim=2048,
                            fail=False, load_gate=threading.Event(), encode_gate=threading.Event(),
                            entered=threading.Event())
    state.load_gate.set()
    state.encode_gate.set()

    class Model:
        def __init__(self, path, **kwargs):
            state.loads += 1
            state.kwargs = kwargs
            state.entered.set()
            assert state.load_gate.wait(3)
            if state.fail:
                raise RuntimeError('sensitive model failure')

        def get_sentence_embedding_dimension(self):
            return state.dim

        def encode_document(self, texts, *, normalize_embeddings):
            assert normalize_embeddings is True
            state.active += 1
            state.peak = max(state.peak, state.active)
            state.calls.extend(texts)
            try:
                assert state.encode_gate.wait(3)
                return [[1.0] + [0.0] * (state.dim - 1)]
            finally:
                state.active -= 1

    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=Model))
    try:
        yield state
    finally:
        state.load_gate.set()
        state.encode_gate.set()


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.001)


def test_constructor_metadata_do_not_import_model(monkeypatch):
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.split('.')[0] in {'sentence_transformers', 'torch', 'transformers'}:
            raise AssertionError('model import before actual embedding')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    emb = WeMMEmbedder('/local/wemm', revision='product-bundled')
    assert emb.kind == 'wemm' and emb.dim == 2048
    assert emb.lineage.normalization == 'l2'
    assert emb.lineage.revision == 'product-bundled'
    assert emb.lineage.format_fingerprint == 'wemm-embedding-2b:product-bundled:2048'
    assert emb.status_snapshot()['state'] == 'cold'
    assert not emb.is_ready()


@pytest.mark.asyncio
async def test_cancel_loading_does_not_duplicate_or_encode_cancelled_request(fake_model):
    fake_model.load_gate.clear()
    emb = WeMMEmbedder('/local/wemm')
    first = asyncio.create_task(emb.embed('cancelled'))
    await until(fake_model.entered.is_set)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert emb.status_snapshot()['state'] == 'loading'
    second = asyncio.create_task(emb.embed('kept'))
    await asyncio.sleep(.01)
    assert fake_model.loads == 1 and fake_model.calls == []
    fake_model.load_gate.set()
    assert len(await second) == 2048
    assert fake_model.loads == 1 and fake_model.calls == ['kept']
    assert fake_model.kwargs['local_files_only'] is True
    assert fake_model.kwargs['trust_remote_code'] is True
    assert emb.is_ready()


@pytest.mark.asyncio
async def test_cancel_encode_keeps_physical_mutex_and_skips_cancelled_queue(fake_model):
    emb = WeMMEmbedder('/local/wemm')
    await emb.embed('prime')
    fake_model.encode_gate.clear()
    first = asyncio.create_task(emb.embed('running'))
    await until(lambda: fake_model.active == 1)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    queued = asyncio.create_task(emb.embed('skip'))
    await asyncio.sleep(.01)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    kept = asyncio.create_task(emb.embed('kept'))
    await asyncio.sleep(.01)
    assert fake_model.active == 1 and fake_model.calls == ['prime', 'running']
    fake_model.encode_gate.set()
    await kept
    assert fake_model.peak == 1 and fake_model.loads == 1
    assert fake_model.calls == ['prime', 'running', 'kept']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['factory', 'dimension'])
async def test_failed_load_is_honest_and_retries_only_on_explicit_use(fake_model, failure):
    fake_model.fail = failure == 'factory'
    fake_model.dim = 12 if failure == 'dimension' else 2048
    emb = WeMMEmbedder('/local/wemm')
    with pytest.raises((RuntimeError, ValueError)):
        await emb.embed('bad')
    status = emb.status_snapshot()
    assert status['state'] == 'failed' and not status['is_ready']
    assert 'sensitive' not in str(status)
    for _ in range(3):
        assert emb.dim == 2048
        assert emb.status_snapshot() == status
    await asyncio.sleep(.01)
    assert fake_model.loads == 1
    fake_model.fail = False
    fake_model.dim = 2048
    assert len(await emb.embed('retry')) == 2048
    assert fake_model.loads == 2


@pytest.mark.asyncio
async def test_output_dimension_is_checked(fake_model):
    emb = WeMMEmbedder('/local/wemm')
    await emb.embed('valid')
    fake_model.dim = 5
    with pytest.raises(ValueError, match='dimension'):
        await emb.embed('invalid')


@pytest.mark.asyncio
async def test_installed_public_production_empty_db_and_status_stay_cold(fake_model, tmp_path):
    from importlib.metadata import version
    from simple_harness_memory import MemoryManager
    import p4_ipc

    assert version('simple-harness-memory-sdk') == '0.6.12'
    resources = tmp_path / 'local-model'
    resources.mkdir()
    emb = WeMMEmbedder(resources, revision='product-bundled')
    manager = await MemoryManager.build_production(
        tmp_path / 'memory.sqlite', embedder=emb, resource_path=resources,
    )
    class Socket:
        def __init__(self):
            self.sent = []
        async def send_json(self, value):
            self.sent.append(value)
    try:
        ws = Socket()
        await p4_ipc.handle(ws, 'test', 'embedder_status', {}, SimpleNamespace(embedder=emb))
        status = ws.sent[0]['payload']
        assert status['state'] == 'cold' and status['is_ready'] is False
        assert status['model_path'] == str(resources)
        assert status['model_name'] == 'tencent/WeMM-Embedding-2B'
        assert fake_model.loads == 0 and fake_model.calls == []
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_async_queue_does_not_occupy_executor_threads(fake_model, monkeypatch):
    emb = WeMMEmbedder('/local/wemm')
    await emb.embed('prime')
    original = asyncio.to_thread
    submitted = []
    async def observed(function, *args, **kwargs):
        if getattr(function, '__name__', '') == '_encode':
            submitted.append(args[0])
        return await original(function, *args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', observed)
    fake_model.encode_gate.clear()
    first = asyncio.create_task(emb.embed('running'))
    queued = []
    try:
        await until(lambda: fake_model.active == 1)
        queued = [asyncio.create_task(emb.embed(f'queued-{i}')) for i in range(12)]
        await asyncio.sleep(.01)
        assert submitted == ['running']
        first.cancel()
        for task in queued[:-1]:
            task.cancel()
        await asyncio.gather(first, *queued[:-1], return_exceptions=True)
        await asyncio.sleep(.01)
        assert submitted == ['running'] and fake_model.active == 1
        fake_model.encode_gate.set()
        assert len(await queued[-1]) == 2048
        assert submitted == ['running', 'queued-11']
        assert fake_model.calls == ['prime', 'running', 'queued-11']
        assert fake_model.peak == 1 and fake_model.loads == 1
    finally:
        fake_model.encode_gate.set()
        await asyncio.gather(first, *queued, return_exceptions=True)
