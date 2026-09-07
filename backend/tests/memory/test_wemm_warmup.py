"""Public warmup only: fake model factory, real shared loader and cancellation.

No model packages or weights; native startup/first-query evidence stays separate.
"""
import asyncio

import pytest

from deskpet.memory.wemm_embedder import WeMMEmbedder
from tests.memory.test_wemm_lazy import fake_model, until  # noqa: F401


@pytest.mark.asyncio
async def test_public_warmup_primes_once_and_query_shares_physical_queue(fake_model):
    fake_model.encode_gate.clear()
    embedder = WeMMEmbedder('/local/wemm')
    first = asyncio.create_task(embedder.warmup())
    second = query = None
    try:
        await until(lambda: fake_model.active == 1)
        assert embedder.status_snapshot()['warmup_state'] == 'priming'
        assert not embedder.status_snapshot()['is_primed']
        second = asyncio.create_task(embedder.warmup())
        await asyncio.sleep(0)
        assert fake_model.loads == 1
        assert fake_model.calls == ['这是一条用于初始化文本编码器的固定测试句子。']
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        query = asyncio.create_task(embedder.embed('first ready query'))
        await asyncio.sleep(.01)
        assert len(fake_model.calls) == 1 and fake_model.active == 1
        fake_model.encode_gate.set()
        await second
        assert embedder.is_ready() and embedder.status_snapshot()['is_primed']
        assert len(await query) == 2048
        await embedder.warmup()
        assert fake_model.loads == 1 and fake_model.peak == 1
        assert fake_model.calls == ['这是一条用于初始化文本编码器的固定测试句子。', 'first ready query']
    finally:
        fake_model.encode_gate.set()
        await asyncio.gather(*(task for task in (first, second, query) if task is not None), return_exceptions=True)


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
    assert embedder.is_ready() and fake_model.loads == 2
    assert embedder.status_snapshot()['is_primed'] and len(fake_model.calls) == 1


@pytest.mark.asyncio
async def test_priming_failure_does_not_claim_primed_or_reload_model(fake_model):
    fake_model.encode_gate.clear()
    embedder = WeMMEmbedder('/local/wemm')
    first = asyncio.create_task(embedder.warmup())
    try:
        await until(lambda: fake_model.active == 1)
        # Factory already passed 2048-dim validation; the actual encoder's
        # invalid output now fails the unchanged physical output check.
        fake_model.dim = 1
        fake_model.encode_gate.set()
        with pytest.raises(ValueError, match='output dimension'):
            await first
        assert embedder.status_snapshot()['warmup_state'] == 'failed'
        assert not embedder.status_snapshot()['is_primed']
        await asyncio.sleep(0)
        assert fake_model.loads == 1 and len(fake_model.calls) == 1
        fake_model.dim = 2048
        await embedder.warmup()
        assert embedder.status_snapshot()['is_primed']
        assert fake_model.loads == 1 and len(fake_model.calls) == 2
        await embedder.warmup()
        assert len(fake_model.calls) == 2  # only an explicit retry after failure
    finally:
        fake_model.encode_gate.set()
        await asyncio.gather(first, return_exceptions=True)
