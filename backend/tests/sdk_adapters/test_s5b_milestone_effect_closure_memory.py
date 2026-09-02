# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b 价值验证里程碑（确定性 Provider 版本，acceptance A1 / S5B-S1 的确定性前置）。

fresh state.db + fresh Memory DB（生产 builder）；workspace 根含 README.md（1.1.3）；用户消息
"把 README 里的版本号改成 1.2.0" → route → ``write_file`` 经 EffectGate → host.file → 终答前
``task_scope_update``（第二用例：漏调 → 兜底恰一次）→ ``record_sdk_terminal`` 同事务 outbox →
worker ingest → job runner → analysis → APPLIED 且物化 → 下一轮 ``typed_recall`` 读到 "1.2.0"。
断言：一个 foreground Run、closure receipt outcome=mutate、outbox delivered、analysis succeeded 恰 1、
Provider 调用计数 = 主 Run N + closure ≤1 + analysis 1、README 已改、终答非空。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.sdk_adapters import s5b_closure_harness as ch
from tests.sdk_adapters import s5b_memory_harness as mh
from tests.sdk_adapters import s5b_milestone_harness as ms


async def _assert_milestone(m, *, main_calls: int, closure_calls: int, analysis_adapter) -> None:  # type: ignore[no-untyped-def]
    assert ms.rows(m.db_path, "SELECT COUNT(*) FROM foreground_runs") == [(1,)]
    assert ms.readme(m) == ms.README_BEFORE.replace("1.1.3", "1.2.0")
    assert ms.last_answer(m).strip()
    receipts = ms.closure_receipts(m)
    assert [r[1] for r in receipts] == ["mutate"], receipts
    [(_, _, state, attempts, ids_json, _config_hash, receipt_json, _)] = mh.outbox_rows(m.db_path)
    assert (state, attempts) == ("delivered", 1) and json.loads(ids_json) == [m.evidence_id]
    assert json.loads(receipt_json)["receipts"][0]["evidence_id"] == m.evidence_id
    rows = ms.analysis_attempts(m)
    assert [(o, s) for o, s, *_ in rows] == [(1, "succeeded")]
    assert len(analysis_adapter.calls) == 1
    assert closure_calls <= 1
    assert main_calls >= 3


@pytest.mark.asyncio
@pytest.mark.parametrize("model_calls_closure", [True, False])
async def test_milestone_effect_closure_memory_deterministic(tmp_path: Path, model_calls_closure: bool) -> None:
    m = await ms.build(tmp_path)
    provider = ms.scripted_main_run(m, call_closure=model_calls_closure)
    out = await ms.run_main(m, provider)
    assert out["exception"] is None, out["exception"]
    assert ms.readme(m) == ms.README_BEFORE.replace("1.1.3", "1.2.0")
    main_calls = len(provider.calls)

    closure_adapter = ch.FakeAdapter([] if model_calls_closure else [lambda request: _closure_response(m)])
    _observed, settlement, receipt = await ms.settle_terminal(m, closure_adapter=closure_adapter)
    if model_calls_closure:
        assert settlement.status in {"clean", "already_closed"} and settlement.provider_calls == 0
    else:
        assert settlement.status == "mutate" and settlement.provider_calls == 1
    assert receipt.terminal_state.value == "COMPLETED"

    analysis_adapter = ch.FakeAdapter([mh.proposal_call([mh.semantic_op(ms.DELIVERY_KEY, "版本号改成 1.2.0")])])
    menv = mh.memory_env(
        m, analysis_adapter, memory_db=tmp_path / "memory" / "human_memory_v7.db",
        subject=ms.SUBJECT, production_builder=True, deadline_ms=30_000,
    )
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
        assert await mh.run_job(menv) == "idle"
        snapshot = await mh.memory_snapshot(menv)
        assert snapshot["jobs"] == [("applied", 1)] and snapshot["heads"] == 1
        assert ("memory.cognitive.committed", "pending") in snapshot["outbox"]
        assert snapshot["analysis_head"] == [(2,)] and snapshot["cognitive_head"] == [(2,)]
        await _assert_milestone(m, main_calls=main_calls, closure_calls=len(closure_adapter.calls), analysis_adapter=analysis_adapter)
        payloads = await mh.recall_payloads(menv, "README", run_id="next-turn")
        assert any("1.2.0" in json.dumps(p, ensure_ascii=False) for p in payloads), payloads
    finally:
        await mh.close(menv)


async def _closure_response(m):  # type: ignore[no-untyped-def]
    return ch.closure_call(ms.closure_arguments(m, key="closure-fallback"))
