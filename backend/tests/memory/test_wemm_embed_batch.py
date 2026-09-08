# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""`WeMMEmbedder.embed_batch`：一次物理调用，而不是基类的 N 次串行 encode。

背景（`plans/2026-09-08-hm-to-a6/DIAG-RECALL-TIMEOUT.md` §2.3）：
`WeMMEmbedder` 此前没有覆写 `embed_batch`，走 SDK 基类的
`[await self.embed(t) for t in texts]`。实测本机 WeMM-Embedding-2B 即使编码
54 字符也要 ~0.9 s，固定开销被 chunk 数直接乘一遍；短时域世代重建的 6 条
chunk 合计 45.7 s，而 Host 维护 tick 的超时只有 5 s，形成永不收敛的活锁。

默认用例走假模型工厂（沿用 `test_wemm_lazy` 的 fixture，无权重、无模型包）：
钉死"一个分组 = 一次物理 encode"、输入顺序、分组边界与取消语义。
真模型对照（等价性、亚线性耗时）标 `model_required`，权重缺失时 skip——
这条纪律来自 G5：宁可 skip，也不用 mock 假装验证过真模型行为。
"""
import asyncio
import time

import pytest

from deskpet.memory.wemm_embedder import (
    _BATCH_MAX_PADDED_CHARS,
    _BATCH_MAX_ITEMS,
    WeMMEmbedder,
)
from tests.memory.test_wemm_lazy import fake_model, until  # noqa: F401

# 真模型对照用的固定输入：无会话、无身份、无记忆内容。
_REAL_TEXTS = [
    "校对脚本的 Python 版本执行流程",
    "The maintenance tick rebuilds the short horizon generation.",
    "短时域 chunk 的公开文本没有长度上限",
    "recall budget deadline 与嵌入抖动余量",
    "写锁内做嵌入会让前台召回饿死",
    "exponential backoff with a capped delay",
]


def _real_model_dir():
    """完整的本地 WeMM 快照；缺任何一件就当作不可用（绝不退化成 mock）。"""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from paths import resolve_model_dir

    directory = resolve_model_dir("wemm-embedding-2b")
    required = ("config.json", "tokenizer.json", "modules.json")
    if not directory.is_dir() or not all((directory / name).is_file() for name in required):
        return None
    if not any(directory.glob("*.safetensors")):
        return None
    return directory


# ---------------------------------------------------------------------------
# 假模型：分组、顺序、校验、取消
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_is_one_physical_call_and_keeps_input_order(fake_model):
    embedder = WeMMEmbedder('/local/wemm')
    texts = [f'chunk-{index}' for index in range(6)]
    vectors = await embedder.embed_batch(texts)
    assert len(vectors) == 6 and all(len(vector) == 2048 for vector in vectors)
    # 基类实现会产生 6 次物理调用；这里必须只有 1 次，且顺序即输入顺序。
    assert fake_model.batches == [tuple(texts)]
    assert fake_model.calls == texts
    assert fake_model.peak == 1 and fake_model.loads == 1


@pytest.mark.asyncio
async def test_batch_equals_per_item_embed_and_is_deterministic(fake_model):
    embedder = WeMMEmbedder('/local/wemm')
    texts = ['甲', 'lorem ipsum', '丙丁戊', '四']
    batched = await embedder.embed_batch(texts)
    per_item = [await embedder.embed(text) for text in texts]
    assert batched == per_item
    assert len({tuple(vector) for vector in batched}) == 3  # 不是同一个常量向量
    assert batched == await embedder.embed_batch(texts)  # 同输入同输出
    assert await embedder.embed_batch(list(reversed(texts))) == list(reversed(batched))


@pytest.mark.asyncio
async def test_oversize_text_is_alone_in_its_group_and_output_order_survives(fake_model):
    embedder = WeMMEmbedder('/local/wemm')
    # 现场分布的形状：几条短 chunk + 一条超长 chunk（实测 29 778 字符）。
    texts = ['a' * 54, 'b' * 108, 'c' * 1682, 'd' * (_BATCH_MAX_PADDED_CHARS + 1), 'e' * 8]
    vectors = await embedder.embed_batch(texts)
    assert len(vectors) == 5
    # 一次 encode 里所有输入都会被补齐到最长的一条，所以长 chunk 必须独占一次
    # 调用，不能把短 chunk 拖进同一次分配里各付一遍它的长度。
    assert [len(batch) for batch in fake_model.batches] == [3, 1, 1]
    # 分组按长度递增访问：1025 字符的那条先于 1682 字符的那条各自独占一次调用。
    assert fake_model.batches[1] == (texts[3],) and fake_model.batches[2] == (texts[2],)
    assert sorted(fake_model.calls) == sorted(texts)  # 不丢、不重、不截断
    # 输出按输入位置回填，与分组内部的访问顺序无关。
    assert vectors == [await embedder.embed(text) for text in texts]


@pytest.mark.asyncio
async def test_grouping_is_independent_of_input_order(fake_model):
    embedder = WeMMEmbedder('/local/wemm')
    lengths = [54, 108, 1682, 7798, 19655, 29778]  # 现场实测分布
    texts = ['z' * length for length in lengths]
    shuffled = [texts[index] for index in (3, 0, 5, 2, 4, 1)]
    first = WeMMEmbedder._encode_groups(texts)
    second = WeMMEmbedder._encode_groups(shuffled)
    assert [sorted(len(texts[i]) for i in g) for g in first] == [
        sorted(len(shuffled[i]) for i in g) for g in second]
    vectors = await embedder.embed_batch(shuffled)
    assert vectors == [await embedder.embed(text) for text in shuffled]


@pytest.mark.asyncio
async def test_group_boundaries_respect_the_item_ceiling(fake_model):
    embedder = WeMMEmbedder('/local/wemm')
    texts = [f'{index}' for index in range(_BATCH_MAX_ITEMS + 3)]
    await embedder.embed_batch(texts)
    assert [len(batch) for batch in fake_model.batches] == [_BATCH_MAX_ITEMS, 3]


@pytest.mark.asyncio
async def test_single_text_still_uses_one_call_and_matches_embed(fake_model):
    embedder = WeMMEmbedder('/local/wemm')
    assert await embedder.embed_batch(['solo']) == [await embedder.embed('solo')]
    assert fake_model.batches == [('solo',), ('solo',)]


@pytest.mark.asyncio
async def test_empty_and_invalid_inputs(fake_model):
    embedder = WeMMEmbedder('/local/wemm')
    assert await embedder.embed_batch([]) == []
    assert fake_model.loads == 0  # 空批次不加载模型
    for bad in ('not a list', b'bytes', 42, [b'bytes'], [None], ['ok', 3]):
        with pytest.raises(TypeError, match='sequence of strings'):
            await embedder.embed_batch(bad)
    # 元组等具体序列照收（SDK 契约写的是 list，但没理由为此在生产上炸）。
    assert len(await embedder.embed_batch(('one', 'two'))) == 2


@pytest.mark.asyncio
async def test_batch_rejects_count_or_dimension_mismatch(fake_model, monkeypatch):
    embedder = WeMMEmbedder('/local/wemm')
    await embedder.embed('prime')

    truncating = lambda texts, *, normalize_embeddings: [[1.0] + [0.0] * 2047]  # noqa: E731
    monkeypatch.setattr(embedder._model, 'encode_document', truncating)
    with pytest.raises(ValueError, match='output count'):
        await embedder.embed_batch(['one', 'two'])

    monkeypatch.setattr(embedder._model, 'encode_document',
                        lambda texts, *, normalize_embeddings: [[1.0, 0.0] for _ in texts])
    with pytest.raises(ValueError, match='output dimension'):
        await embedder.embed_batch(['one', 'two'])


@pytest.mark.asyncio
async def test_cancelled_batch_keeps_physical_mutex_and_does_not_interleave(fake_model):
    embedder = WeMMEmbedder('/local/wemm')
    await embedder.embed('prime')
    fake_model.encode_gate.clear()
    running = asyncio.create_task(embedder.embed_batch(['x' * 8, 'y' * 8]))
    kept = None
    try:
        await until(lambda: fake_model.active == 1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        kept = asyncio.create_task(embedder.embed('kept'))
        await asyncio.sleep(.01)
        # 被取消的批次仍占着物理队列直到线程结束；后来的请求不会插进批次中间。
        assert fake_model.active == 1 and fake_model.calls == ['prime', 'x' * 8, 'y' * 8]
        fake_model.encode_gate.set()
        assert len(await kept) == 2048
        assert fake_model.peak == 1 and fake_model.loads == 1
        assert fake_model.calls[-1] == 'kept'
    finally:
        fake_model.encode_gate.set()
        await asyncio.gather(*(t for t in (running, kept) if t is not None),
                             return_exceptions=True)


# ---------------------------------------------------------------------------
# 真模型对照（无权重则 skip）
# ---------------------------------------------------------------------------


@pytest.mark.model_required
@pytest.mark.asyncio
async def test_real_batch_matches_per_item_embeds_within_tolerance():
    directory = _real_model_dir()
    if directory is None:
        pytest.skip('WeMM-Embedding-2B 权重未找到；装模型或设 DESKPET_MODEL_ROOT 后再跑')
    from simple_harness_memory.embedders.base import cosine_similarity

    embedder = WeMMEmbedder(directory, revision='product-bundled')
    await embedder.warmup()
    batched = await embedder.embed_batch(list(_REAL_TEXTS))
    per_item = [await embedder.embed(text) for text in _REAL_TEXTS]
    assert len(batched) == len(_REAL_TEXTS)
    # 逐条路径本身是逐位确定的；下面的偏差只可能来自批内 padding 与批矩阵乘。
    assert [await embedder.embed(text) for text in _REAL_TEXTS] == per_item
    for index, (left, right) in enumerate(zip(batched, per_item)):
        assert len(left) == len(right) == 2048
        # 实测（本机 mps / bf16）：cos ≥ 0.99988、逐元素 ≤ 0.0018、模长 0.9986–1.0027。
        # 容差按实测最坏值留约 3 倍余量，不是凑一个刚好能过的数。
        assert cosine_similarity(left, right) >= 0.9995, index
        assert max(abs(a - b) for a, b in zip(left, right)) <= 5e-3, index
        assert abs(sum(value * value for value in left) ** 0.5 - 1.0) <= 1e-2  # 仍 L2 归一
    # 顺序由输入决定，不由模型内部批次决定。
    reversed_batch = await embedder.embed_batch(list(reversed(_REAL_TEXTS)))
    for index, vector in enumerate(reversed_batch):
        assert cosine_similarity(vector, batched[len(batched) - 1 - index]) >= 0.9995


@pytest.mark.model_required
@pytest.mark.asyncio
async def test_real_batch_cost_is_sublinear_for_six_inputs():
    directory = _real_model_dir()
    if directory is None:
        pytest.skip('WeMM-Embedding-2B 权重未找到；装模型或设 DESKPET_MODEL_ROOT 后再跑')
    from simple_harness_memory.embedders.base import Embedder

    embedder = WeMMEmbedder(directory, revision='product-bundled')
    await embedder.warmup()
    texts = list(_REAL_TEXTS)
    assert len(texts) >= 6
    # 两条路径各跑一次丢弃：首次遇到一种新的输入形状要额外付一次图/内核准备
    # 成本，先热的一边会被系统性高估。计时只比稳态。
    await embedder.embed_batch(texts)
    await Embedder.embed_batch(embedder, texts)

    started = time.monotonic()
    await embedder.embed_batch(texts)
    batched_seconds = time.monotonic() - started

    # 对照组就是基类实现本身（N 次 embed），不是重新写一遍的近似。
    started = time.monotonic()
    await Embedder.embed_batch(embedder, texts)
    serial_seconds = time.monotonic() - started

    # 实测稳态 ratio 0.21–0.23（322 ms vs 1 430 ms），阈值留 3 倍余量。
    assert batched_seconds <= serial_seconds * 0.7, (
        f'batched={batched_seconds:.3f}s serial={serial_seconds:.3f}s '
        f'n={len(texts)}：一次物理调用没有比 N 次串行便宜，批处理未生效'
    )
