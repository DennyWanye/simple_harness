# SPDX-License-Identifier: Apache-2.0
"""随机动作序列（HTN 补齐 F1-6）：默认 1 个种子 50 步，证明驱动可用；联测（F2）用环境变量放大。

    RANDOM_SEQ_SEEDS=1,2,3,4 RANDOM_SEQ_STEPS=500 uv run --frozen pytest tests/orchestrator/product_world/test_random_sequences.py

失败时把种子、动作序列、缩小后的反例写进 ``.local-test-evidence/<日期>/random-sequences/``。

**改坏检验**（改坏清单 F1-16）：离线重建取最新修订而不是指定修订 → 对照物①抓到。
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
from pathlib import Path

import pytest

from random_sequences import InvariantBroken, plan_actions, run_actions, shrink

SEEDS = [int(item) for item in os.environ.get("RANDOM_SEQ_SEEDS", "20261004").split(",") if item]
STEPS = int(os.environ.get("RANDOM_SEQ_STEPS", "50"))
EVIDENCE = Path(__file__).resolve().parents[5] / ".local-test-evidence"


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


@pytest.mark.parametrize("seed", SEEDS)
def test_random_sequence_keeps_the_invariants(tmp_path, seed):
    actions = plan_actions(seed, STEPS)
    try:
        log = asyncio.run(run_actions(tmp_path / "root", seed, actions))
    except InvariantBroken as error:
        smallest = shrink(seed, actions)
        out = EVIDENCE / datetime.date.today().isoformat() / "random-sequences"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"seed-{seed}.json").write_text(json.dumps(
            {"seed": seed, "steps": STEPS, "actions": actions, "smallest": smallest, "error": str(error)},
            ensure_ascii=False, indent=1))
        raise AssertionError(str(error)) from error  # 改坏检验只认断言失败
    assert len(log) >= 1 and set(actions) >= {"new"}
