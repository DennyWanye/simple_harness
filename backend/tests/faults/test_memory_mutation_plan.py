# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""fault-matrix lane `memory-mutation-plan` runner（S5b Task 4 实装）。

terminal oracle 见 fixtures/fault-matrix.json：raw evidence permanent；no partial plan；idempotent replay。
runner_contract 输出由 `_runner_contract.emit` 统一产生。

全部 seam 用真实 state.db + 真实 Memory 0.6.1 backend + 生产 worker/executor/delivery authority，
Provider 用确定性 ``memory_analysis_proposal`` 替身（``s5b_memory_harness``）。每条 seam：kill →
（Host raw evidence 与 Memory evidence_envelopes 的 before/after hash 守恒）→ replay 收敛 → 再 replay 幂等；
Provider 调用计数在每条 seam 内 ≤ 1 次成功发送。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import httpx
import pytest

from tests.faults._runner_contract import LANE_SEAMS, emit, new_root_run_id, state_hash
from tests.sdk_adapters import s5b_closure_harness as ch
from tests.sdk_adapters import s5b_memory_harness as mh

LANE = "memory-mutation-plan"
IMPLEMENTED = set(LANE_SEAMS[LANE])


def _raw_hash(env, menv) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    return state_hash(env.db_path, mh.RAW_HOST_TABLES), state_hash(menv.runtime.db_path, mh.RAW_MEMORY_TABLES)


async def _delivered(tmp_path: Path, run_id: str, adapter, **kwargs):  # type: ignore[no-untyped-def]
    env = await mh.bound_turn_run(tmp_path, run_id)
    await mh.finish_clean_run(env)
    menv = mh.memory_env(env, adapter, **kwargs)
    assert await menv.worker.run_once() == "delivered"
    return env, menv


def _proposal(env, *ops):  # type: ignore[no-untyped-def]
    return mh.proposal_call(list(ops) or [mh.semantic_op(mh.item_id(env), "版本号改成 1.2.0")])


# --- Host commit seams -------------------------------------------------------------


async def _raw_evidence_commit(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """终态提交（outbox + link + terminal receipt 同一事务）提交前 kill → 零半状态；重放 → 恰一行；再重放 hash 不变。"""
    from deskpet.execution.foreground_queue import ForegroundQueueStore

    env = await mh.bound_turn_run(tmp_path, run_id)
    facts = ch.FakeRunFacts(run_id)
    observed = await ch.observe_terminal(env, facts)
    assert observed is not None
    before = state_hash(env.db_path, mh.HOST_TABLES)
    raw_before = state_hash(env.db_path, mh.RAW_HOST_TABLES)
    faulty = ForegroundQueueStore(env.db_path, clock=env.clock, fault_hook=ch.OneShot("terminal.before_commit"))
    with pytest.raises(RuntimeError, match="injected:terminal.before_commit"):
        await faulty.record_sdk_terminal(
            host_run_id=env.admission.host_run_id, sdk_run_id=run_id, owner_id=env.admission.owner_id,
            generation=env.admission.generation, terminal_state=observed.terminal_state,
            sdk_event_id=observed.sdk_event_id, sdk_event_hash=observed.sdk_event_hash,
            idempotency_key=f"runtime-terminal:{env.admission.host_run_id}",
            run_binding={**mh.BINDING, "run_id": run_id}, endpoint_identity=mh.ENDPOINT,
        )
    assert state_hash(env.db_path, mh.HOST_TABLES) == before
    assert mh.outbox_rows(env.db_path) == []
    receipt = await mh.record_terminal(env, observed)
    converged = state_hash(env.db_path, mh.HOST_TABLES)
    assert converged != before and len(mh.outbox_rows(env.db_path)) == 1
    assert await mh.record_terminal(env, observed) == receipt
    assert state_hash(env.db_path, mh.HOST_TABLES) == converged
    assert state_hash(env.db_path, mh.RAW_HOST_TABLES) == raw_before
    return before, converged, {"outbox_rows": 1}


async def _outbox_commit(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """worker：Memory ingest 已提交、Host delivered 回写前 kill → lease 到期后重放：Memory 按 source_ref 幂等
    返回同 receipt（evidence_envelopes 仍 1、jobs 仍 1），Host 行 delivered、attempts=2。"""
    env = await mh.bound_turn_run(tmp_path, run_id)
    await mh.finish_clean_run(env)
    adapter = ch.FakeAdapter([])
    menv = mh.memory_env(env, adapter, fault=ch.OneShot("outbox.before_deliver_commit"))
    await menv.runtime.manager()
    before = state_hash(env.db_path, mh.HOST_TABLES)
    raw = _raw_hash(env, menv)
    with pytest.raises(RuntimeError, match="injected:outbox.before_deliver_commit"):
        await menv.worker.run_once()
    assert mh.outbox_rows(env.db_path)[0][2:4] == ("claimed", 1)
    assert (await mh.memory_rows(menv, "SELECT COUNT(*) FROM evidence_envelopes"))[0][0] == 1
    env.clock.now += 31.0
    assert await menv.worker.run_once() == "delivered"
    row = mh.outbox_rows(env.db_path)[0]
    assert row[2:4] == ("delivered", 2) and json.loads(row[6])["receipts"][0]["evidence_id"] == env.evidence_id
    assert (await mh.memory_rows(menv, "SELECT COUNT(*) FROM evidence_envelopes"))[0][0] == 1
    assert (await mh.memory_rows(menv, "SELECT COUNT(*) FROM jobs"))[0][0] == 1
    converged = state_hash(env.db_path, mh.HOST_TABLES)
    assert await menv.worker.run_once() == "idle"
    assert state_hash(env.db_path, mh.HOST_TABLES) == converged
    assert _raw_hash(env, menv)[0] == raw[0]
    await mh.close(menv)
    return before, converged, {"attempts": 2}


async def _commit_before_ack(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """worker claim 之后、Memory ingest 之前 kill（claimed + lease）→ lease 到期 reclaim → delivered；Memory 恰一份。"""
    env = await mh.bound_turn_run(tmp_path, run_id)
    await mh.finish_clean_run(env)
    adapter = ch.FakeAdapter([])
    menv = mh.memory_env(env, adapter, fault=ch.OneShot("outbox.before_ingest"))
    before = state_hash(env.db_path, mh.HOST_TABLES)
    with pytest.raises(RuntimeError, match="injected:outbox.before_ingest"):
        await menv.worker.run_once()
    assert mh.outbox_rows(env.db_path)[0][2:4] == ("claimed", 1)
    assert (await mh.memory_rows(menv, "SELECT COUNT(*) FROM evidence_envelopes"))[0][0] == 0
    # lease 未到期：第二 owner 不能抢。
    assert await menv.worker.run_once() == "idle"
    env.clock.now += 31.0
    assert await menv.worker.run_once() == "delivered"
    assert mh.outbox_rows(env.db_path)[0][2:4] == ("delivered", 2)
    assert (await mh.memory_rows(menv, "SELECT COUNT(*) FROM evidence_envelopes"))[0][0] == 1
    converged = state_hash(env.db_path, mh.HOST_TABLES)
    assert await menv.worker.run_once() == "idle"
    assert state_hash(env.db_path, mh.HOST_TABLES) == converged
    await mh.close(menv)
    return before, converged, {"attempts": 2}


# --- analysis attempt seams --------------------------------------------------------


async def _invocation_evidence_commit(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """attempt handed_off 落库之后、Provider 返回之前 kill（sent_unknown）→ 绝不重发：每次 Memory 重试都被
    open 成员拒绝（0 调用）→ Memory dead_letter + Host durable memory.analysis.blocked；raw 守恒。"""
    from deskpet.memory.analysis_executor import blocked_audit_rows

    adapter = ch.FakeAdapter([])
    env, menv = await _delivered(tmp_path, run_id, adapter, fault=ch.OneShot("attempt-handed-off"))
    before = state_hash(env.db_path, mh.HOST_TABLES)
    raw = _raw_hash(env, menv)
    assert await mh.run_job(menv) == "retry_scheduled"
    assert [(o, s) for o, s, *_ in mh.attempts(env.db_path)] == [(1, "handed_off")]
    assert len(adapter.calls) == 0
    outcomes = []
    for _ in range(4):
        env.clock.now += 5.0
        outcomes.append(await mh.run_job(menv))
        if outcomes[-1] == "dead_letter":
            break
    assert outcomes[-1] == "dead_letter", outcomes
    assert len(adapter.calls) == 0 and menv.executor.provider_calls == 0
    assert [(o, s) for o, s, *_ in mh.attempts(env.db_path)] == [(1, "handed_off")]
    assert (await mh.memory_rows(menv, "SELECT state FROM jobs")) == [("dead_letter",)]
    blocked = await blocked_audit_rows(env.db_path)
    assert blocked and all(r[0] == run_id and r[1].startswith("memory.analysis.blocked") for r in blocked)
    assert _raw_hash(env, menv) == raw
    converged = state_hash(env.db_path, mh.HOST_TABLES)
    assert await mh.run_job(menv) == "idle"
    assert state_hash(env.db_path, mh.HOST_TABLES) == converged
    await mh.close(menv)
    return before, converged, {"provider_calls": 0, "job_state": "dead_letter"}


async def _validation_decision_commit(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """Memory validator 决策（prepare_analysis_application）提交前 kill → lease 到期后 reclaim：结果已 durable，
    executor 不再调用（Provider 计数 1）→ APPLIED 且物化。"""
    adapter = ch.FakeAdapter([])
    env, menv = await _delivered(tmp_path, run_id, adapter, memory_fault=ch.OneShot("job.apply.before_commit"))
    adapter.script.append(_proposal(env))
    before = state_hash(env.db_path, mh.HOST_TABLES)
    raw = _raw_hash(env, menv)
    with pytest.raises(RuntimeError, match="injected:job.apply.before_commit"):
        await mh.run_job(menv)
    assert len(adapter.calls) == 1
    assert (await mh.memory_snapshot(menv))["heads"] == 0
    env.clock.now += 40.0
    assert await mh.run_job(menv) == "applied"
    assert len(adapter.calls) == 1 and menv.executor.provider_calls == 1
    snapshot = await mh.memory_snapshot(menv)
    assert snapshot["heads"] == 1 and snapshot["jobs"] == [("applied", 1)]
    assert _raw_hash(env, menv) == raw
    converged = state_hash(env.db_path, mh.HOST_TABLES)
    assert await mh.run_job(menv) == "idle"
    assert state_hash(env.db_path, mh.HOST_TABLES) == converged and await mh.memory_snapshot(menv) == snapshot
    await mh.close(menv)
    return before, converged, {"provider_calls": 1}


async def _state_mutation_commit(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """Memory finalize（state mutation）提交前 kill → reclaim 收敛 APPLIED，零第二次调用，物化恰一份。"""
    adapter = ch.FakeAdapter([])
    env, menv = await _delivered(tmp_path, run_id, adapter, memory_fault=ch.OneShot("job.finalize.before_commit"))
    adapter.script.append(_proposal(env))
    before = state_hash(env.db_path, mh.HOST_TABLES)
    with pytest.raises(RuntimeError, match="injected:job.finalize.before_commit"):
        await mh.run_job(menv)
    env.clock.now += 40.0
    assert await mh.run_job(menv) == "applied"
    assert len(adapter.calls) == 1
    snapshot = await mh.memory_snapshot(menv)
    assert snapshot["heads"] == 1 and snapshot["decisions"] == 1 and snapshot["batches"] == [("applied",)]
    converged = state_hash(env.db_path, mh.HOST_TABLES)
    assert await mh.run_job(menv) == "idle" and await mh.memory_snapshot(menv) == snapshot
    await mh.close(menv)
    return before, converged, {"provider_calls": 1}


async def _multi_op_finalize(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """≥2 operation 的 proposal → 0.6.1 finalize 按规范序收敛：APPLIED、decision_records=2、heads=2、一次调用。"""
    adapter = ch.FakeAdapter([])
    env, menv = await _delivered(tmp_path, run_id, adapter)
    item = mh.item_id(env)
    adapter.script.append(mh.proposal_call([
        mh.semantic_op(item, "版本号改成 1.2.0", operation_id="zz-semantic"),
        mh.episode_op(item, "把 README 里的版本号改成 1.2.0", operation_id="aa-episode"),
    ]))
    before = state_hash(env.db_path, mh.HOST_TABLES)
    assert await mh.run_job(menv) == "applied"
    snapshot = await mh.memory_snapshot(menv)
    assert snapshot["decisions"] == 2 and snapshot["heads"] == 2 and snapshot["batches"] == [("applied",)]
    assert len(adapter.calls) == 1
    converged = state_hash(env.db_path, mh.HOST_TABLES)
    assert await mh.run_job(menv) == "idle" and await mh.memory_snapshot(menv) == snapshot
    await mh.close(menv)
    return before, converged, {"operations": 2}


async def _audit_pending_stuck(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """0.6.0 的 audit_pending 卡死场景：3 op（hash 序 ≠ plan 序）+ finalize 提交前 kill → 0.6.1 重放收敛 APPLIED
    而不是永久 audit_pending；零第二次调用。"""
    adapter = ch.FakeAdapter([])
    env, menv = await _delivered(tmp_path, run_id, adapter, memory_fault=ch.OneShot("job.finalize.before_commit"))
    item = mh.item_id(env)
    adapter.script.append(mh.proposal_call([
        mh.semantic_op(item, "版本号改成 1.2.0", operation_id="zz", predicate="p-zz"),
        mh.semantic_op(item, "README 里的版本号", operation_id="aa", predicate="p-aa"),
        mh.semantic_op(item, "把 README", operation_id="mm", predicate="p-mm"),
    ]))
    before = state_hash(env.db_path, mh.HOST_TABLES)
    with pytest.raises(RuntimeError, match="injected:job.finalize.before_commit"):
        await mh.run_job(menv)
    env.clock.now += 40.0
    assert await mh.run_job(menv) == "applied"
    snapshot = await mh.memory_snapshot(menv)
    assert snapshot["batches"] == [("applied",)] and snapshot["decisions"] == 3 and snapshot["heads"] == 3
    assert len(adapter.calls) == 1
    converged = state_hash(env.db_path, mh.HOST_TABLES)
    assert await mh.run_job(menv) == "idle"
    await mh.close(menv)
    return before, converged, {"operations": 3}


async def _reconciliation_observer(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """handed_off kill 后注入 reconciliation observer 确认（sent_confirmed）→ 用确认的响应完成分析，
    Provider 计数 0；attempt 行为 unknown/sent_confirmed 且 envelope durable。"""
    adapter = ch.FakeAdapter([])
    env, menv = await _delivered(tmp_path, run_id, adapter, fault=ch.OneShot("attempt-handed-off"))
    response = _proposal(env)
    before = state_hash(env.db_path, mh.HOST_TABLES)
    assert await mh.run_job(menv) == "retry_scheduled"
    assert [(o, s) for o, s, *_ in mh.attempts(env.db_path)] == [(1, "handed_off")]

    async def observer(row):  # type: ignore[no-untyped-def]
        return response if row.status == "handed_off" else None

    menv.executor._reconciliation_observer = observer
    env.clock.now += 5.0
    assert await mh.run_job(menv) == "applied"
    assert len(adapter.calls) == 0 and menv.executor.provider_calls == 0
    rows = mh.attempts(env.db_path)
    # 首行：observer 确认 → unknown/sent_confirmed（永不重发）；次行：Memory 重试的新 request_hash 复用已确认响应
    # （reason_code = reused:<首行 attempt_id>，0 调用），结果 durable 在次行。
    assert rows[0][1:3] == ("unknown", "sent_confirmed")
    assert rows[1][1] == "succeeded" and str(rows[1][3]).startswith("reused:") and rows[1][6] == 1
    assert (await mh.memory_snapshot(menv))["heads"] == 1
    converged = state_hash(env.db_path, mh.HOST_TABLES)
    assert await mh.run_job(menv) == "idle"
    assert state_hash(env.db_path, mh.HOST_TABLES) == converged
    await mh.close(menv)
    return before, converged, {"provider_calls": 0}


async def _lease_reclaim_in_flight(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """worker A 的 Provider 调用在飞（handed_off）时 Memory lease 到期，worker B reclaim → B 被 open 成员拒绝（0 调用）；
    A 返回后 lease 复验失败（lease_lost），结果 durable 到 attempt 行；B 下一次按 evidence_set_key 复用该结果
    （0 调用）→ APPLIED。全程 Provider 调用恰 1 次。"""
    gate = asyncio.Event()
    env = await mh.bound_turn_run(tmp_path, run_id)
    await mh.finish_clean_run(env)
    response = _proposal(env)

    async def slow(request):  # type: ignore[no-untyped-def]
        await gate.wait()
        return response

    adapter_a = ch.FakeAdapter([slow])
    menv_a = mh.memory_env(env, adapter_a)
    assert await menv_a.worker.run_once() == "delivered"
    before = state_hash(env.db_path, mh.HOST_TABLES)
    task = asyncio.create_task(mh.run_job(menv_a))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if adapter_a.calls:
            break
    assert len(adapter_a.calls) == 1
    assert [(o, s) for o, s, *_ in mh.attempts(env.db_path)] == [(1, "handed_off")]
    env.clock.now += 40.0
    # Memory backend 是单写者（进程级 writer lock）：worker B = 同一 store 上的第二个 DurableMemoryJobRunner
    # （worker_id 不同、lease token 不同），executor/delivery authority 仍是 builder 绑定的同一对象。
    menv_b = mh.MemoryEnv(**{**vars(menv_a), "runner": None, "worker_id": "worker-2"})
    assert await mh.run_job(menv_b) == "retry_scheduled"
    assert len(adapter_a.calls) == 1
    gate.set()
    try:
        outcome_a = await task
    except Exception as exc:  # noqa: BLE001 - stale lease surfaces as a Memory error
        outcome_a = f"{type(exc).__name__}"
    assert outcome_a != "applied"
    rows = mh.attempts(env.db_path)
    assert rows[0][1] == "succeeded" and rows[0][6] == 1
    env.clock.now += 5.0
    assert await mh.run_job(menv_b) == "applied"
    assert len(adapter_a.calls) == 1 and menv_a.executor.provider_calls == 1
    snapshot = await mh.memory_snapshot(menv_b)
    assert snapshot["heads"] == 1 and snapshot["batches"][-1] == ("applied",)
    converged = state_hash(env.db_path, mh.HOST_TABLES)
    assert await mh.run_job(menv_b) == "idle"
    assert state_hash(env.db_path, mh.HOST_TABLES) == converged
    await mh.close(menv_a)
    return before, converged, {"provider_calls": 1, "outcome_a": str(outcome_a)}


async def _membership_growth(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """attempt 1（{e1}）not_sent 失败后，第二轮的 e2 进入同一 batch key；重试的成员集 {e1,e2} → 新 request_hash /
    evidence_set_key → 新 attempt 恰一次调用 → APPLIED；e1 只被成功分析一次，两行 attempt 的 key 互异。"""
    env = await mh.bound_turn_run(tmp_path, run_id)
    await mh.finish_clean_run(env)
    adapter = ch.FakeAdapter([httpx.ConnectError("refused")])
    menv = mh.memory_env(env, adapter)
    assert await menv.worker.run_once() == "delivered"
    before = state_hash(env.db_path, mh.HOST_TABLES)
    assert await mh.run_job(menv) == "retry_scheduled"
    assert [(o, s, u) for o, s, u, *_ in mh.attempts(env.db_path)] == [(1, "failed", "not_sent")]
    env2 = await mh.next_turn_run(env, f"{run_id}-r2", text="我平时用 Python 写后端服务", delivery_key="turn-2")
    await mh.finish_clean_run(env2)
    assert await menv.worker.run_once() == "delivered"
    # 注：Memory 0.6.1 把 operation 的 decision evidence refs 取为 plan.evidence_refs 的子集并保留原 ordinal，
    # 只引用第 2 条 evidence 的 operation 会被 ``decision_evidence_refs_ordinal_invalid`` 拒绝（Memory 侧缺陷，
    # 已在 Task 4 报告中登记）；本 seam 的 proposal 引用首条 evidence，考察的是成员集增长下的 attempt 键与零重复调用。
    adapter.script.append(mh.proposal_call([mh.semantic_op(mh.item_id(env), "版本号改成 1.2.0")]))
    menv.config = dataclasses.replace(menv.config, batch_size=2)
    menv.runner = None
    env.clock.now += 5.0
    assert await mh.run_job(menv) == "applied"
    rows = mh.attempts(env.db_path)
    assert [(o, s, u) for o, s, u, *_ in rows] == [(1, "failed", "not_sent"), (1, "succeeded", None)]
    assert rows[0][4] != rows[1][4] and rows[0][5] != rows[1][5]
    members = mh.rows(env.db_path, "SELECT a.attempt_ordinal,a.status,m.evidence_id FROM post_turn_invocation_members m JOIN post_turn_invocation_attempts a ON a.attempt_id=m.attempt_id ORDER BY a.reserved_at,m.evidence_id")
    assert [m[2] for m in members if m[1] == "succeeded"] == sorted([env.evidence_id, env2.evidence_id])
    assert len(adapter.calls) == 2
    snapshot = await mh.memory_snapshot(menv)
    assert snapshot["jobs"] == [("applied", 2), ("applied", 1)] and snapshot["heads"] == 1
    converged = state_hash(env.db_path, mh.HOST_TABLES)
    assert await mh.run_job(menv) == "idle"
    await mh.close(menv)
    return before, converged, {"attempts": 2, "successful_calls": 1}


SEAM_RUNNERS = {
    "raw-evidence-commit": _raw_evidence_commit,
    "invocation-evidence-commit": _invocation_evidence_commit,
    "validation-decision-commit": _validation_decision_commit,
    "state-mutation-commit": _state_mutation_commit,
    "outbox-commit": _outbox_commit,
    "commit-before-ack": _commit_before_ack,
    "multi-op-finalize": _multi_op_finalize,
    "audit-pending-stuck": _audit_pending_stuck,
    "reconciliation-observer": _reconciliation_observer,
    "lease-reclaim-in-flight": _lease_reclaim_in_flight,
    "membership-growth": _membership_growth,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("seam", LANE_SEAMS[LANE])
async def test_seam_kill_replay_converges(seam: str, tmp_path: Path) -> None:
    assert seam in IMPLEMENTED and seam in SEAM_RUNNERS
    root_run_id = new_root_run_id(LANE)
    before, after, extra = await SEAM_RUNNERS[seam](tmp_path, f"sdk-run-{seam}")
    emit(LANE, root_run_id, before, after, {"seam": seam, **extra})
