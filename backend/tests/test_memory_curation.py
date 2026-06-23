# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-OH-4 — memory self-curation nudge 单测。

覆盖：
  ① mock LLM 返 should_remember=True → facts 表新增行
  ② should_remember=False → 不写
  ③ flag OFF / curator=None → agent_loop 不调 nudge（BC，run 一轮零 facts 写）
  ④ 频率门控：每 N 回合才触发，N-1 轮不触发
另含 JSON 3 级 fallback / 畸形输出降级 / 失败隔离的小用例。
"""
from __future__ import annotations

import asyncio

import aiosqlite
import pytest

from deskpet.memory.facts import FactsStore
from deskpet.memory.memory_v2_schema import _reset_cache_for_tests
from deskpet.memory.curation import MemoryCurator, CurationDecision, _extract_json


@pytest.fixture
def db_path(tmp_path):
    _reset_cache_for_tests()
    return tmp_path / "state.db"


_TURNS = [
    {"role": "user", "content": "我每天早上 9 点喜欢先看 A 股行情"},
    {"role": "assistant", "content": "好的，记住了你的晨间习惯。"},
]


async def _count_facts(db_path) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM facts")
        (n,) = await cur.fetchone()
        await cur.close()
    return int(n)


async def _facts_table_exists(db_path) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='facts'"
        )
        row = await cur.fetchone()
        await cur.close()
    return row is not None


# ── ① should_remember=True → facts 表新增行 ──────────────────────────────
@pytest.mark.asyncio
async def test_remember_true_writes_fact(db_path):
    store = FactsStore(db_path)

    async def _llm(prompt: str) -> str:
        return (
            '{"decisions": [{"should_remember": true, "category": "preference", '
            '"key": "morning_routine", "value": "用户晨间先看 A 股行情", '
            '"reason": "稳定的日常习惯"}]}'
        )

    curator = MemoryCurator(store, _llm)
    decisions = await curator.nudge(_TURNS)

    assert len(decisions) == 1
    assert decisions[0].should_remember is True
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM facts WHERE key = 'morning_routine'")
        rows = [dict(r) for r in await cur.fetchall()]
        await cur.close()
    assert len(rows) == 1
    assert rows[0]["category"] == "preference"
    assert "A 股" in rows[0]["value"]


# ── ② should_remember=False → 不写 ──────────────────────────────────────
@pytest.mark.asyncio
async def test_remember_false_no_write(db_path):
    store = FactsStore(db_path)

    async def _llm(prompt: str) -> str:
        return (
            '{"decisions": [{"should_remember": false, "category": "context", '
            '"key": "smalltalk", "value": "闲聊", "reason": "transient"}]}'
        )

    curator = MemoryCurator(store, _llm)
    decisions = await curator.nudge(_TURNS)

    assert len(decisions) == 1
    assert decisions[0].should_remember is False
    # facts 表可能根本没创建（upsert 从未调用）→ 视为 0 行。
    if await _facts_table_exists(db_path):
        assert await _count_facts(db_path) == 0


@pytest.mark.asyncio
async def test_empty_decisions_no_write(db_path):
    store = FactsStore(db_path)

    async def _llm(prompt: str) -> str:
        return '{"decisions": []}'

    curator = MemoryCurator(store, _llm)
    decisions = await curator.nudge(_TURNS)
    assert decisions == []
    if await _facts_table_exists(db_path):
        assert await _count_facts(db_path) == 0


# ── JSON 3 级 fallback：fenced block / 夹带散文 ─────────────────────────
@pytest.mark.asyncio
async def test_fenced_json_fallback(db_path):
    store = FactsStore(db_path)

    async def _llm(prompt: str) -> str:
        return (
            "Sure, here is what I'd remember:\n"
            "```json\n"
            '{"decisions": [{"should_remember": true, "category": "fact", '
            '"key": "city", "value": "用户在上海", "reason": "地理上下文"}]}\n'
            "```\n"
        )

    curator = MemoryCurator(store, _llm)
    decisions = await curator.nudge(_TURNS)
    assert len(decisions) == 1 and decisions[0].key == "city"
    assert await _count_facts(db_path) == 1


def test_extract_json_three_levels():
    # 1. 直 parse
    assert _extract_json('{"decisions": []}') == {"decisions": []}
    # 2. fenced
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    # 3. 夹带散文里的 {...}（贪婪外括号容纳嵌套数组）
    got = _extract_json('noise {"decisions": [{"k": 1}]} tail')
    assert got == {"decisions": [{"k": 1}]}
    # 畸形 → None
    assert _extract_json("not json at all") is None
    assert _extract_json("") is None


# ── 畸形 LLM 输出 / LLM 抛错 → 降级返 []，不抛、不写 ────────────────────
@pytest.mark.asyncio
async def test_malformed_output_degrades(db_path):
    store = FactsStore(db_path)

    async def _llm(prompt: str) -> str:
        return "完全不是 JSON 的一句话"

    curator = MemoryCurator(store, _llm)
    assert await curator.nudge(_TURNS) == []
    if await _facts_table_exists(db_path):
        assert await _count_facts(db_path) == 0


@pytest.mark.asyncio
async def test_llm_raises_is_isolated(db_path):
    store = FactsStore(db_path)

    async def _llm(prompt: str) -> str:
        raise RuntimeError("relay down")

    curator = MemoryCurator(store, _llm)
    # 不抛 — safe-fail
    assert await curator.nudge(_TURNS) == []


@pytest.mark.asyncio
async def test_remember_true_missing_key_dropped(db_path):
    """should_remember=True 但缺 key/value → 丢弃，不写空事实。"""
    store = FactsStore(db_path)

    async def _llm(prompt: str) -> str:
        return (
            '{"decisions": [{"should_remember": true, "category": "fact", '
            '"key": "", "value": "", "reason": "x"}]}'
        )

    curator = MemoryCurator(store, _llm)
    decisions = await curator.nudge(_TURNS)
    assert decisions == []
    if await _facts_table_exists(db_path):
        assert await _count_facts(db_path) == 0


@pytest.mark.asyncio
async def test_no_chat_turns_skips(db_path):
    store = FactsStore(db_path)
    calls = []

    async def _llm(prompt: str) -> str:
        calls.append(prompt)
        return '{"decisions": []}'

    curator = MemoryCurator(store, _llm)
    # 只有 system / 空 → 无可 curate 的 chat turn → 不调 LLM
    assert await curator.nudge([{"role": "system", "content": "x"}]) == []
    assert calls == []


# ──────────────────────────────────────────────────────────────────────────
# AgentLoop 接电：③ BC (curator=None) + ④ 频率门控
# ──────────────────────────────────────────────────────────────────────────


class _StubCurator:
    """记录 nudge 被调用的次数 + 收到的 turns。"""

    def __init__(self) -> None:
        self.calls: list[list] = []

    async def nudge(self, recent_turns, **_kw):
        self.calls.append(list(recent_turns))
        return [CurationDecision(True, "fact", "k", "v", "r")]


def _make_loop(*, curator=None, every=8):
    """构造一个最小 AgentLoop，仅测 _maybe_fire_curation_nudge 门控。"""
    from agent.agent_loop import AgentLoop

    class _StubLLM:
        async def chat_with_fallback(self, *a, **k):  # pragma: no cover - unused
            raise NotImplementedError

    class _StubTools:
        def schemas(self, enabled_toolsets=None):
            return []

        def dispatch(self, name, args, task_id):  # pragma: no cover - unused
            raise NotImplementedError

    return AgentLoop(
        _StubLLM(),
        _StubTools(),
        memory_curator=curator,
        curation_nudge_every_n_turns=every,
    )


@pytest.mark.asyncio
async def test_agentloop_curator_none_bc():
    """③ curator=None → _maybe_fire_curation_nudge 无副作用（BC）。"""
    loop = _make_loop(curator=None)
    # 不应抛、不应调度任何任务
    loop._maybe_fire_curation_nudge("s1", list(_TURNS))
    # turn counter 也不该被建立（提前 return）
    assert loop._curation_turn_counts == {}


@pytest.mark.asyncio
async def test_agentloop_frequency_gate():
    """④ 每 N 回合才触发；N-1 轮不触发。"""
    stub = _StubCurator()
    loop = _make_loop(curator=stub, every=3)

    # 前 2 轮 (N-1) 不触发
    for _ in range(2):
        loop._maybe_fire_curation_nudge("s1", list(_TURNS))
    await asyncio.sleep(0)  # 让任何被调度的 task 跑（不该有）
    assert stub.calls == []

    # 第 3 轮触发一次
    loop._maybe_fire_curation_nudge("s1", list(_TURNS))
    await asyncio.sleep(0.01)  # 让 fire-and-forget task 完成
    assert len(stub.calls) == 1

    # 第 4、5 轮不触发，第 6 轮再触发
    for _ in range(2):
        loop._maybe_fire_curation_nudge("s1", list(_TURNS))
    await asyncio.sleep(0.01)
    assert len(stub.calls) == 1
    loop._maybe_fire_curation_nudge("s1", list(_TURNS))
    await asyncio.sleep(0.01)
    assert len(stub.calls) == 2


@pytest.mark.asyncio
async def test_agentloop_per_session_counters():
    """门控按 session 独立计数（不串话）。"""
    stub = _StubCurator()
    loop = _make_loop(curator=stub, every=2)

    loop._maybe_fire_curation_nudge("sA", list(_TURNS))  # sA:1 不触发
    loop._maybe_fire_curation_nudge("sB", list(_TURNS))  # sB:1 不触发
    await asyncio.sleep(0.01)
    assert stub.calls == []
    loop._maybe_fire_curation_nudge("sA", list(_TURNS))  # sA:2 触发
    await asyncio.sleep(0.01)
    assert len(stub.calls) == 1
