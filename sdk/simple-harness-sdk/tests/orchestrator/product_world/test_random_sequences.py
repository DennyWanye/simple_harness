# SPDX-License-Identifier: Apache-2.0
"""随机动作序列（HTN 补齐 F1-6）：默认 1 个种子 50 步，证明驱动可用；联测（F2）用环境变量放大。

    RANDOM_SEQ_SEEDS=1,2,3,4 RANDOM_SEQ_STEPS=500 uv run --frozen pytest tests/orchestrator/product_world/test_random_sequences.py

``RANDOM_SEQ_REOPEN_EVERY=25`` 每 25 步关库重开一次（阶段 G 验收）；``RANDOM_SEQ_PROGRESS=文件`` 每步写一行心跳。
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
REOPEN_EVERY = int(os.environ.get("RANDOM_SEQ_REOPEN_EVERY", "0"))
EVIDENCE = Path(__file__).resolve().parents[5] / ".local-test-evidence"


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)
    # 已结束任务悬着的预留，结束满 15 分钟后按上限计入——这是按时间到点的收尾，新进程一启动就做一遍。
    # 长跑超过 15 分钟时，它会正好落在"关库重开"两次快照之间，被对照物②当成"重开改了东西"
    # （联测 2000 步第 3 个种子第 449 步的误报）。这条收尾另有用例（test_round_faults、test_late_usage），
    # 这里把时限调到跑不到，只留"用量未知不放预留"那条不变式。
    import agent_orchestrator.orchestrator.accounting_recovery as accounting_recovery

    monkeypatch.setattr(accounting_recovery, "ENDED_MISSION_RECHECK_SECONDS", 1e9)


@pytest.mark.parametrize("seed", SEEDS)
def test_random_sequence_keeps_the_invariants(tmp_path, seed):
    actions = plan_actions(seed, STEPS, REOPEN_EVERY)
    try:
        log = asyncio.run(run_actions(tmp_path / "root", seed, actions))
    except InvariantBroken as error:
        out = EVIDENCE / datetime.date.today().isoformat() / "random-sequences"
        out.mkdir(parents=True, exist_ok=True)
        record = {"seed": seed, "steps": STEPS, "actions": actions, "smallest": None, "error": str(error)}
        target = out / f"seed-{seed}.json"
        target.write_text(json.dumps(record, ensure_ascii=False, indent=1))  # 先留下错误，缩小可能很久
        record["smallest"] = shrink(seed, actions)
        target.write_text(json.dumps(record, ensure_ascii=False, indent=1))
        raise AssertionError(str(error)) from error  # 改坏检验只认断言失败
    assert len(log) >= 1 and set(actions) >= {"new"}
