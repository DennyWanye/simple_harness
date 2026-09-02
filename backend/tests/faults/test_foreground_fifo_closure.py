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
from deskpet.execution.foreground_queue import ForegroundQueueStore
from deskpet.execution.foreground_runtime import SqliteSdkTerminalObserver
from deskpet.task_scope.store import TaskScopeConflict
from tests.execution import test_foreground_queue as fq
from tests.faults._runner_contract import LANE_SEAMS, emit, new_root_run_id, state_hash

LANE = "foreground-fifo-closure"
IMPLEMENTED = {"objective-event-commit", "terminal-watermark", "observer-next-sequence-race"}
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


SEAM_RUNNERS = {
    "objective-event-commit": _objective_event_commit,
    "terminal-watermark": _terminal_watermark,
    "observer-next-sequence-race": _observer_next_sequence_race,
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
