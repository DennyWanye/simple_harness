# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""fault-matrix lane `foreground-fifo-closure` runner。

terminal oracle 见 fixtures/fault-matrix.json：max foreground run one；FIFO preserved；
mutation-or-no-mutation closure complete。runner_contract 输出由 `_runner_contract.emit` 统一产生。

S5b Task 2 实装三条 seam（kill → replay 收敛，append-only 表 before/after hash 守恒）：
- ``objective-event-commit``：host.file + evidence 行 + harness.tool_invocation 同事务；提交前 kill →
  零半状态；重放同一事实 → 恰一份，再重放 hash 不变。
- ``terminal-watermark``：terminal 前排空过程中 kill → 已解决的预留保持、未解决的仍 reserved、
  终态不放行；新 owner 重放排空 → 收敛，run_terminal 恰一条。
- ``observer-next-sequence-race``：observer 与迟到的预留行竞争 seq（probe A9）→ 迟到行不丢，
  terminal seq = MAX(reservations ∪ receipts)+1。
其余 seam 在对应 Task 实装前保持 strict xfail。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.execution import RunState
from deskpet.execution.evidence_ingress import (
    ExecutionEvidenceIngress,
    ObjectiveEventSpec,
    TerminalWatermarkPending,
    ToolInvocationFact,
)
from deskpet.execution.foreground_queue import (
    ForegroundQueueError,
    ForegroundQueueStore,
)
from deskpet.execution.foreground_runtime import SqliteSdkTerminalObserver
from deskpet.task_scope.store import TaskScopeConflict
from tests.execution import test_foreground_queue as fq
from tests.faults._runner_contract import LANE_SEAMS, emit, new_root_run_id, state_hash

LANE = "foreground-fifo-closure"
IMPLEMENTED = {
    "objective-event-commit",
    "terminal-watermark",
    "observer-next-sequence-race",
    # S5b Task 3：closure 状态机五 seam（kill → replay 收敛，Provider 零重发）。
    "semantic-closure-commit",
    "attempt-reserved",
    "attempt-handed-off",
    "cross-run-pending-plan",
    "lease-second-owner",
}
CANONICAL_TABLES = (
    "task_scope_events",
    "task_scope_evidence_links",
    "task_scope_execution_ingest_receipts",
    "task_scope_run_watermarks",
    "task_scope_terminal_gate_receipts",
    "harness_evidence_reservations",
    "human_memory_evidence",
)


class _OneShot:
    def __init__(self, point: str) -> None:
        self.point = point
        self.fired = False

    def __call__(self, point: str) -> None:
        if point == self.point and not self.fired:
            self.fired = True
            raise RuntimeError(f"injected:{point}")


def _rows(db_path: Path, sql: str, *params):
    with sqlite3.connect(db_path) as db:
        return db.execute(sql, params).fetchall()


async def _bound_run(tmp_path: Path, run_id: str):
    queue_db, primary_id, clock = await fq._ready(tmp_path / "queue")
    store = ForegroundQueueStore(queue_db, clock=clock)
    await fq._enqueue(store, primary_id, 1)
    admission = await fq._claim_and_bind(store, sdk_run_id=run_id)
    await store.record_sdk_started(
        host_run_id=admission.host_run_id, sdk_run_id=run_id, owner_id=admission.owner_id,
        generation=admission.generation, sdk_event_id=f"sdk-start:{run_id}",
        idempotency_key=f"sdk-start:{run_id}",
    )
    return queue_db, store, admission


def _fact(run_id: str, effect_id: str) -> ToolInvocationFact:
    return ToolInvocationFact(
        run_id=run_id, effect_id=effect_id, call_id="call-1", tool_name="write_file",
        effect_state="succeeded", outcome="succeeded", error_code=None,
        objective=ObjectiveEventSpec(
            event_kind="host.file",
            payload={"tool_name": "write_file", "effect_id": effect_id, "call_id": "call-1",
                     "outcome": "succeeded", "error_code": None, "targets": ["a.txt"]},
        ),
    )


class _Stack:
    def __init__(self, run_id: str, facts: dict[str, object] | None = None) -> None:
        self.run_id = run_id
        self.facts = facts or {}

    def read_run_terminal_evidence(self, r: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            run_id=r, state="completed", event_id=f"sdk-terminal:{r}", event_hash="7" * 64,
            occurred_at=9.0, error_code=None,
        )

    async def read_reserved_fact(self, reservation):  # type: ignore[no-untyped-def]
        return self.facts.get(reservation.source_event_id)


class _SdkIngress:
    async def wait_idle(self, r: str) -> None:
        del r

    def query(self, r: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(state=SimpleNamespace(value="completed"))


async def _observe(queue_db: Path, admission, stack: _Stack, run_id: str, *, fault=None):  # type: ignore[no-untyped-def]
    return await SqliteSdkTerminalObserver(
        str(queue_db), _SdkIngress(), stack, fault_inject=fault  # type: ignore[arg-type]
    ).observe(
        host_run_id=admission.host_run_id, sdk_run_id=run_id, subject=fq.SUBJECT,
        owner_id=admission.owner_id, generation=admission.generation,
    )


async def _objective_event_commit(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    queue_db, _store, admission = await _bound_run(tmp_path, run_id)
    ingress = ExecutionEvidenceIngress(queue_db, fault_inject=_OneShot("objective-event-commit"))
    await ingress.reserve(run_id=run_id, task_scope_id=fq.SCOPE, kind="tool_invocation", source_event_id="effect:e-1")
    before = state_hash(queue_db, CANONICAL_TABLES)
    # kill：提交前崩溃 → 零半状态（无 host 事件、无 evidence 行、无导入回执、预留仍 reserved）。
    with pytest.raises(RuntimeError, match="injected:objective-event-commit"):
        await ingress.commit_fact(task_scope_id=fq.SCOPE, subject=fq.SUBJECT, fact=_fact(run_id, "e-1"))
    assert state_hash(queue_db, CANONICAL_TABLES) == before
    assert _rows(queue_db, "SELECT status FROM harness_evidence_reservations WHERE source_event_id='effect:e-1'") == [("reserved",)]
    assert _rows(queue_db, "SELECT COUNT(*) FROM task_scope_events WHERE task_scope_id=?", fq.SCOPE) == [(0,)]
    assert _rows(queue_db, "SELECT COUNT(*) FROM human_memory_evidence WHERE run_id=?", run_id) == [(0,)]
    with pytest.raises(TerminalWatermarkPending):
        await ingress.authorize_terminal(run_id)
    # replay：同一事实 → 恰一份（host.file + evidence + tool_invocation）。
    receipt = await ingress.commit_fact(task_scope_id=fq.SCOPE, subject=fq.SUBJECT, fact=_fact(run_id, "e-1"))
    assert receipt.source_sequence == 1
    converged = state_hash(queue_db, CANONICAL_TABLES)
    assert converged != before
    events = _rows(queue_db, "SELECT event_kind FROM task_scope_events WHERE task_scope_id=? ORDER BY event_sequence", fq.SCOPE)
    assert events == [("host.file",), ("harness.tool_invocation",)]
    assert _rows(queue_db, "SELECT COUNT(*) FROM human_memory_evidence WHERE run_id=?", run_id) == [(1,)]
    # 再重放（新 owner）：幂等，hash 不变；不同事实同 id → 拒绝，hash 不变。
    await ingress.commit_fact(task_scope_id=fq.SCOPE, subject=fq.SUBJECT, fact=_fact(run_id, "e-1"))
    assert state_hash(queue_db, CANONICAL_TABLES) == converged
    with pytest.raises(TaskScopeConflict):
        await ingress.commit_fact(
            task_scope_id=fq.SCOPE, subject=fq.SUBJECT,
            fact=ToolInvocationFact(run_id=run_id, effect_id="e-1", call_id="call-1", tool_name="write_file",
                                    effect_state="failed", outcome="failed", error_code="tool_failed", objective=None),
        )
    assert state_hash(queue_db, CANONICAL_TABLES) == converged
    observed = await _observe(queue_db, admission, _Stack(run_id), run_id)
    assert observed is not None and observed.terminal_state is RunState.COMPLETED
    gate = await ingress.authorize_terminal(run_id)
    return before, state_hash(queue_db, CANONICAL_TABLES), {"terminal_sequence": gate.terminal_source_sequence}


async def _terminal_watermark(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    queue_db, _store, admission = await _bound_run(tmp_path, run_id)
    ingress = ExecutionEvidenceIngress(queue_db)
    await ingress.reserve(run_id=run_id, task_scope_id=fq.SCOPE, kind="provider_invocation", source_event_id="p-1")
    await ingress.reserve(run_id=run_id, task_scope_id=fq.SCOPE, kind="tool_invocation", source_event_id="effect:e-2")
    await ingress.reserve(run_id=run_id, task_scope_id=fq.SCOPE, kind="context_snapshot", source_event_id="s-3")
    before = state_hash(queue_db, CANONICAL_TABLES)
    stack = _Stack(run_id, {"effect:e-2": _fact(run_id, "e-2")})
    # kill：排空中途（第一条已解决之后）崩溃 → 终态未写、未放行。
    with pytest.raises(RuntimeError, match="injected:terminal-watermark"):
        await _observe(queue_db, admission, stack, run_id, fault=_OneShot("terminal-watermark"))
    statuses = _rows(queue_db, "SELECT source_event_id,status FROM harness_evidence_reservations WHERE run_id=? ORDER BY source_sequence", run_id)
    assert statuses[0] == ("p-1", "abandoned") and ("s-3", "reserved") in statuses
    assert _rows(queue_db, "SELECT COUNT(*) FROM task_scope_execution_ingest_receipts WHERE run_id=? AND evidence_kind='run_terminal'", run_id) == [(0,)]
    with pytest.raises(TerminalWatermarkPending):
        await ingress.authorize_terminal(run_id)
    # replay（新 owner）：同一排空收敛 → 全部解决、run_terminal 恰一条（seq 4）、放行。
    observed = await _observe(queue_db, admission, stack, run_id)
    assert observed is not None and observed.terminal_state is RunState.COMPLETED
    statuses = _rows(queue_db, "SELECT source_event_id,status FROM harness_evidence_reservations WHERE run_id=? ORDER BY source_sequence", run_id)
    assert statuses == [("p-1", "abandoned"), ("effect:e-2", "ingested"), ("s-3", "abandoned")]
    kinds = _rows(queue_db, "SELECT source_sequence,evidence_kind FROM task_scope_execution_ingest_receipts WHERE run_id=? ORDER BY source_sequence", run_id)
    assert kinds == [(1, "provider_invocation"), (2, "tool_invocation"), (3, "context_snapshot"), (4, "run_terminal")]
    gate = await ingress.authorize_terminal(run_id)
    assert gate.durable_source_sequence == gate.terminal_source_sequence == 4
    converged = state_hash(queue_db, CANONICAL_TABLES)
    # 第三次 observe（再一个 owner）：幂等。
    assert await _observe(queue_db, admission, stack, run_id) is not None
    assert state_hash(queue_db, CANONICAL_TABLES) == converged
    return before, converged, {"terminal_sequence": 4}


async def _observer_next_sequence_race(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    queue_db, _store, admission = await _bound_run(tmp_path, run_id)
    ingress = ExecutionEvidenceIngress(queue_db)
    await ingress.reserve(run_id=run_id, task_scope_id=fq.SCOPE, kind="provider_invocation", source_event_id="p-1")
    await ingress.ingest(task_scope_id=fq.SCOPE, evidence=fq._execution_evidence(sdk_run_id=run_id, source_event_id="p-1", kind="provider_invocation", source_sequence=1))
    # 竞争：迟到的 tool 行只预留了 seq2，observer 此刻计算 next_sequence。
    await ingress.reserve(run_id=run_id, task_scope_id=fq.SCOPE, kind="tool_invocation", source_event_id="effect:e-2")
    before = state_hash(queue_db, CANONICAL_TABLES)
    assert await ingress.next_sequence(run_id) == 3  # probe P1 的 observer 会算出 2 → 丢行
    # 迟到行在 terminal 前到达：接受（不再 execution_source_sequence_conflict）。
    late = await ingress.commit_fact(task_scope_id=fq.SCOPE, subject=fq.SUBJECT, fact=_fact(run_id, "e-2"))
    assert late.source_sequence == 2
    observed = await _observe(queue_db, admission, _Stack(run_id), run_id)
    assert observed is not None
    kinds = _rows(queue_db, "SELECT source_sequence,evidence_kind FROM task_scope_execution_ingest_receipts WHERE run_id=? ORDER BY source_sequence", run_id)
    assert kinds == [(1, "provider_invocation"), (2, "tool_invocation"), (3, "run_terminal")]
    gate = await ingress.authorize_terminal(run_id)
    assert gate.durable_source_sequence == gate.terminal_source_sequence == 3
    # terminal 之后的迟到行被稳定拒绝（不是丢失，而是可见冲突）。
    with pytest.raises(TaskScopeConflict, match="execution_after_terminal_rejected"):
        await ingress.reserve(run_id=run_id, task_scope_id=fq.SCOPE, kind="tool_invocation", source_event_id="effect:e-9")
    converged = state_hash(queue_db, CANONICAL_TABLES)
    assert await _observe(queue_db, admission, _Stack(run_id), run_id) is not None
    assert state_hash(queue_db, CANONICAL_TABLES) == converged
    return before, converged, {"terminal_sequence": 3}


# --- S5b Task 3 seams（closure 状态机；真实 store/ingress/invoker/handler，确定性 adapter）----------

CLOSURE_TABLES = (
    *CANONICAL_TABLES,
    "task_scope_closure_receipts",
    "post_turn_invocation_attempts",
    "post_turn_invocation_members",
    "task_scope_mutation_decisions",
    "task_scope_mutation_attempts",
    "task_scope_canonical_revisions",
)


async def _closure_env(tmp_path: Path, run_id: str):  # type: ignore[no-untyped-def]
    from tests.sdk_adapters import s5b_closure_harness as ch

    env = await ch.bound_run(tmp_path, run_id)
    await ch.material_write(env, "e-1")
    facts = ch.FakeRunFacts(run_id)
    observed = await ch.observe_terminal(env, facts)
    assert observed is not None
    return ch, env, facts, observed


async def _semantic_closure_commit(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """handler 在 apply_mutation_plan + closure receipt 同一事务提交前 kill → 零半状态
    （无 receipt、无 decision、revision 不变）；重放同一 plan → 恰一份；再重放 → 同 receipt、hash 不变。"""
    ch, env, facts, observed = await _closure_env(tmp_path, run_id)
    refs = ch.scope_evidence_ids(env.db_path)
    revision, _ = ch.head(env.db_path)
    arguments = ch.mutate_arguments(refs, base_revision=revision, idempotency_key="seam-closure")
    before = state_hash(env.db_path, CLOSURE_TABLES)
    fallback, _ = ch.build_fallback(env, facts, ch.FakeAdapter([ch.closure_call(arguments)]), fault=_OneShot("semantic-closure-commit"))
    with pytest.raises(RuntimeError, match="injected:semantic-closure-commit"):
        await ch.settle(env, fallback)
    # 零半状态：receipt / decision / revision 全无；attempt 仍 handed_off（Provider 已调用一次，结果未落库）。
    assert ch.receipts(env.db_path) == []
    assert ch.head(env.db_path)[0] == revision
    assert _rows(env.db_path, "SELECT COUNT(*) FROM task_scope_mutation_decisions") == [(0,)]
    assert [(o, s) for o, s, *_ in ch.attempts(env.db_path)] == [(1, "handed_off")]
    with pytest.raises(ForegroundQueueError) as blocked:
        await ch.record_terminal(env, observed)
    assert blocked.value.code == "foreground_terminal_closure_pending"
    # replay（新 owner）：handed_off → 绝不重发 → pending(closure_attempt_unknown)，终态照常。
    adapter2 = ch.FakeAdapter([])
    fallback2, _ = ch.build_fallback(env, facts, adapter2)
    replay = await ch.settle(env, fallback2)
    assert (replay.status, replay.reason_code, replay.provider_calls) == ("pending", "closure_attempt_unknown", 0)
    assert len(adapter2.calls) == 0
    await ch.record_terminal(env, observed)
    converged = state_hash(env.db_path, CLOSURE_TABLES)
    assert converged != before
    # 再重放：幂等。
    fallback3, _ = ch.build_fallback(env, facts, ch.FakeAdapter([]))
    assert (await ch.settle(env, fallback3)).provider_calls == 0
    assert state_hash(env.db_path, CLOSURE_TABLES) == converged
    return before, converged, {"replay_status": replay.status}


async def _attempt_reserved(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """reserved 行落库之后、handed_off 之前 kill → 新 owner reconcile：reserved → failed(not_sent)，
    新 ordinal 2 发起恰一次 Provider 调用 → mutate；attempt 1 零调用。"""
    ch, env, facts, observed = await _closure_env(tmp_path, run_id)
    refs = ch.scope_evidence_ids(env.db_path)
    revision, _ = ch.head(env.db_path)
    arguments = ch.mutate_arguments(refs, base_revision=revision, idempotency_key="seam-reserved")
    before = state_hash(env.db_path, CLOSURE_TABLES)
    adapter = ch.FakeAdapter([ch.closure_call(arguments)])
    fallback, _ = ch.build_fallback(env, facts, adapter, fault=_OneShot("attempt-reserved"))
    with pytest.raises(RuntimeError, match="injected:attempt-reserved"):
        await ch.settle(env, fallback)
    assert len(adapter.calls) == 0
    assert [(o, s) for o, s, *_ in ch.attempts(env.db_path)] == [(1, "reserved")]
    fallback2, _ = ch.build_fallback(env, facts, adapter)
    replay = await ch.settle(env, fallback2)
    assert replay.status == "mutate" and replay.provider_calls == 1 and len(adapter.calls) == 1
    assert [(o, s, u) for o, s, u, *_ in ch.attempts(env.db_path)] == [(1, "failed", "not_sent"), (2, "succeeded", None)]
    await ch.record_terminal(env, observed)
    converged = state_hash(env.db_path, CLOSURE_TABLES)
    fallback3, _ = ch.build_fallback(env, facts, adapter)
    assert (await ch.settle(env, fallback3)).provider_calls == 0
    assert state_hash(env.db_path, CLOSURE_TABLES) == converged
    return before, converged, {"attempts": 2}


async def _attempt_handed_off(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """handed_off 落库之后、Provider 返回之前 kill → 新 owner **绝不重发** → pending(closure_attempt_unknown)，
    Host 终态照常；再重放仍 0 调用。"""
    ch, env, facts, observed = await _closure_env(tmp_path, run_id)
    refs = ch.scope_evidence_ids(env.db_path)
    revision, _ = ch.head(env.db_path)
    arguments = ch.mutate_arguments(refs, base_revision=revision, idempotency_key="seam-handed-off")
    before = state_hash(env.db_path, CLOSURE_TABLES)
    adapter = ch.FakeAdapter([ch.closure_call(arguments), ch.closure_call(arguments)])
    fallback, _ = ch.build_fallback(env, facts, adapter, fault=_OneShot("attempt-handed-off"))
    with pytest.raises(RuntimeError, match="injected:attempt-handed-off"):
        await ch.settle(env, fallback)
    assert len(adapter.calls) == 0
    assert [(o, s) for o, s, *_ in ch.attempts(env.db_path)] == [(1, "handed_off")]
    fallback2, _ = ch.build_fallback(env, facts, adapter)
    replay = await ch.settle(env, fallback2)
    assert replay.status == "pending" and replay.reason_code == "closure_attempt_unknown" and replay.provider_calls == 0
    assert len(adapter.calls) == 0
    assert [(o, s) for o, s, *_ in ch.attempts(env.db_path)] == [(1, "handed_off")]
    assert ch.receipts(env.db_path)[-1][2:5] == ("pending", None, "closure_attempt_unknown")
    await ch.record_terminal(env, observed)
    converged = state_hash(env.db_path, CLOSURE_TABLES)
    fallback3, _ = ch.build_fallback(env, facts, adapter)
    assert (await ch.settle(env, fallback3)).provider_calls == 0
    assert state_hash(env.db_path, CLOSURE_TABLES) == converged
    return before, converged, {"resent": 0}


async def _cross_run_pending_plan(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """Run 1 pending；Run 2（同 admission scope）的 closure plan 以提交时 head revision 为 base_revision、
    refs 引用 Run 1 的 evidence → 一条 receipt 覆盖两个 Run；过期 base_revision（CAS 冲突）→ pending 可重试。"""
    from deskpet.execution.semantic_closure import dirty_state
    from deskpet.task_scope.store import CanonicalTaskScopeStore

    ch, env, facts, observed = await _closure_env(tmp_path, run_id)
    fallback, _ = ch.build_fallback(env, facts, ch.FakeAdapter([ch.plain_answer()]))
    assert (await ch.settle(env, fallback)).status == "pending"
    await ch.record_terminal(env, observed)
    before = state_hash(env.db_path, CLOSURE_TABLES)
    run2 = f"{run_id}-r2"
    env2 = await ch.next_run(env, run2)
    await ch.material_write(env2, "e-2", path="b.txt")
    facts2 = ch.FakeRunFacts(run2)
    observed2 = await ch.observe_terminal(env2, facts2)
    refs = ch.scope_evidence_ids(env2.db_path)
    run1_refs = [
        str(r[0]) for r in _rows(env2.db_path, "SELECT DISTINCT l.evidence_id FROM task_scope_evidence_links l JOIN human_memory_evidence e ON e.evidence_id=l.evidence_id WHERE e.run_id=?", run_id)
    ]
    assert run1_refs and set(run1_refs) <= set(refs)
    revision, _ = ch.head(env2.db_path)
    # 过期的 base_revision → mutation_base_revision_conflict 原样透传 → pending（可重试，Provider 已用 1 次）；
    # Run 2 终态照常提交，两条 pending 都归 admission scope。
    stale = ch.mutate_arguments(refs, base_revision=revision + 5, idempotency_key="seam-cross-stale")
    fallback2, _ = ch.build_fallback(env2, facts2, ch.FakeAdapter([ch.closure_call(stale)]))
    stale_settlement = await ch.settle(env2, fallback2)
    assert (stale_settlement.status, stale_settlement.reason_code) == ("pending", "mutation_base_revision_conflict")
    assert ch.head(env2.db_path)[0] == revision
    await ch.record_terminal(env2, observed2)
    # Run 3（同 scope）：新 request_hash → 新 attempt；正确 base_revision + Run 1 的 refs → 一条 receipt 覆盖三个 Run。
    run3 = f"{run_id}-r3"
    env3 = await ch.next_run(env2, run3, claim_key="claim-3", index=3)
    await ch.material_write(env3, "e-3", path="c.txt")
    facts3 = ch.FakeRunFacts(run3)
    observed3 = await ch.observe_terminal(env3, facts3)
    refs = ch.scope_evidence_ids(env3.db_path)
    revision, _ = ch.head(env3.db_path)
    good = ch.mutate_arguments(run1_refs + [r for r in refs if r not in run1_refs], base_revision=revision, idempotency_key="seam-cross-good")
    adapter3 = ch.FakeAdapter([ch.closure_call(good)])
    fallback3, _ = ch.build_fallback(env3, facts3, adapter3)
    settlement = await ch.settle(env3, fallback3)
    assert settlement.status == "mutate" and settlement.provider_calls == 1
    dirty = await dirty_state(CanonicalTaskScopeStore(env3.db_path), ch.SCOPE)
    assert not dirty.is_dirty
    assert [r[2] for r in ch.receipts(env3.db_path)] == ["pending", "pending", "mutate"]
    linked = [str(r[0]) for r in _rows(env3.db_path, "SELECT evidence_id FROM task_scope_evidence_links l JOIN task_scope_events e ON e.event_id=l.event_id WHERE e.source_event_id=?", f"mutation-plan:{settlement.receipt.plan_id}")]
    assert set(run1_refs) <= set(linked)
    await ch.record_terminal(env3, observed3)
    converged = state_hash(env3.db_path, CLOSURE_TABLES)
    fallback4, _ = ch.build_fallback(env3, facts3, adapter3)
    assert (await ch.settle(env3, fallback4)).provider_calls == 0
    assert state_hash(env3.db_path, CLOSURE_TABLES) == converged
    return before, converged, {"runs": 3}


async def _lease_second_owner(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """owner-1 reserved+handed_off 后 lease 到期；owner-2 reclaim（generation+1）→ 0 调用、pending(closure_attempt_unknown)
    并提交终态；owner-1 迟到的 Provider 结果在 apply 前复验 lease 失败 → 不写 receipt/不 apply。"""
    import asyncio

    ch, env, facts, observed = await _closure_env(tmp_path, run_id)
    refs = ch.scope_evidence_ids(env.db_path)
    revision, _ = ch.head(env.db_path)
    arguments = ch.mutate_arguments(refs, base_revision=revision, idempotency_key="seam-lease")
    before = state_hash(env.db_path, CLOSURE_TABLES)
    gate = asyncio.Event()

    async def slow_response(request):  # type: ignore[no-untyped-def]
        await gate.wait()
        return ch.closure_call(arguments)

    adapter1 = ch.FakeAdapter([slow_response])
    fallback1, _ = ch.build_fallback(env, facts, adapter1)
    owner1 = asyncio.create_task(ch.settle(env, fallback1))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if [(o, s) for o, s, *_ in ch.attempts(env.db_path)] == [(1, "handed_off")]:
            break
    assert [(o, s) for o, s, *_ in ch.attempts(env.db_path)] == [(1, "handed_off")]
    # lease 到期 → owner-2 reclaim。
    env.clock.now += 1000
    await env.store.reclaim_expired(
        host_run_id=env.admission.host_run_id, new_owner_id="owner-2",
        expected_generation=env.admission.generation, lease_seconds=10, idempotency_key="reclaim-2",
    )
    generation2 = env.admission.generation + 1
    adapter2 = ch.FakeAdapter([ch.closure_call(arguments)])
    fallback2, _ = ch.build_fallback(env, facts, adapter2, owner_id="owner-2", generation=generation2)
    second = await ch.settle(env, fallback2, owner_id="owner-2", generation=generation2)
    assert second.status == "pending" and second.reason_code == "closure_attempt_unknown" and second.provider_calls == 0
    assert len(adapter2.calls) == 0
    await ch.record_terminal(env, observed, owner_id="owner-2", generation=generation2)
    # owner-1 迟到返回：apply 前复验 lease → 拒绝，不 apply、无第二条 receipt。
    gate.set()
    first = await owner1
    assert first.status == "lease_lost" and first.provider_calls == 1
    assert ch.head(env.db_path)[0] == revision
    assert [r[2] for r in ch.receipts(env.db_path)] == ["pending"]
    assert [(o, s, g) for o, s, _u, _r, _p, g in ch.attempts(env.db_path)] == [(1, "succeeded", 1)]
    converged = state_hash(env.db_path, CLOSURE_TABLES)
    fallback3, _ = ch.build_fallback(env, facts, adapter2, owner_id="owner-2", generation=generation2)
    third = await ch.settle(env, fallback3, owner_id="owner-2", generation=generation2)
    assert third.provider_calls == 0
    assert state_hash(env.db_path, CLOSURE_TABLES) == converged
    return before, converged, {"second_owner_calls": 0}


SEAM_RUNNERS = {
    "objective-event-commit": _objective_event_commit,
    "terminal-watermark": _terminal_watermark,
    "observer-next-sequence-race": _observer_next_sequence_race,
    "semantic-closure-commit": _semantic_closure_commit,
    "attempt-reserved": _attempt_reserved,
    "attempt-handed-off": _attempt_handed_off,
    "cross-run-pending-plan": _cross_run_pending_plan,
    "lease-second-owner": _lease_second_owner,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("seam", [s for s in LANE_SEAMS[LANE] if s in IMPLEMENTED])
async def test_seam_kill_replay_converges(seam: str, tmp_path: Path) -> None:
    root_run_id = new_root_run_id(LANE)
    before, after, extra = await SEAM_RUNNERS[seam](tmp_path, root_run_id)
    assert before != after
    emit(LANE, root_run_id, before, after, {"seam": seam, **extra})


@pytest.mark.parametrize("seam", [s for s in LANE_SEAMS[LANE] if s not in IMPLEMENTED])
@pytest.mark.xfail(strict=True, reason="S5b 实装前：seam 注入点尚未接线（NOT_IMPLEMENTED）")
def test_seam_kill_replay_converges_pending(seam: str) -> None:
    raise NotImplementedError(f"{LANE}:{seam}")
