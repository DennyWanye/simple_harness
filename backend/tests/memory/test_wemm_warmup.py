"""Public warmup only: fake model factory, real shared loader and cancellation.

No model packages or weights; native startup/first-query evidence stays separate.
"""
import asyncio

import pytest

from deskpet.memory.wemm_embedder import WeMMEmbedder
from tests.memory.test_wemm_lazy import fake_model, until  # noqa: F401


@pytest.mark.asyncio
async def test_public_warmup_loads_without_encode_then_query_reuses_model(fake_model):
    fake_model.load_gate.clear()
    embedder = WeMMEmbedder('/local/wemm')
    first = asyncio.create_task(embedder.warmup())
    second = None
    try:
        await until(fake_model.entered.is_set)
        assert embedder.status_snapshot()['state'] == 'loading'
        second = asyncio.create_task(embedder.warmup())
        await asyncio.sleep(0)
        assert fake_model.loads == 1 and fake_model.calls == []
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        fake_model.load_gate.set()
        await second
        assert embedder.is_ready() and fake_model.calls == []
        await embedder.warmup()
        assert fake_model.loads == 1 and fake_model.calls == []
        assert len(await embedder.embed('first ready query')) == 2048
        assert fake_model.loads == 1 and fake_model.calls == ['first ready query']
    finally:
        fake_model.load_gate.set()
        await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)


@pytest.mark.asyncio
async def test_public_warmup_failure_stays_failed_until_explicit_retry(fake_model):
    fake_model.fail = True
    embedder = WeMMEmbedder('/local/wemm')
    with pytest.raises(RuntimeError):
        await embedder.warmup()
    assert embedder.status_snapshot()['state'] == 'failed'
    assert not embedder.is_ready() and fake_model.calls == []
    await asyncio.sleep(0)
    assert fake_model.loads == 1
    fake_model.fail = False
    await embedder.warmup()
    assert embedder.is_ready() and fake_model.loads == 2 and fake_model.calls == []
