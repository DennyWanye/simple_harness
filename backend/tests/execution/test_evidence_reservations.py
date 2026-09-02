# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b Task 2：Harness 证据预留/排空契约（design-freeze §3）。

- state.db 内事实（snapshot receipt / route decision / route tool invocation）在 v45 ledger 的
  写事务内 ``reserve + ingest`` 同事务；
- 其它 DB 事实（SDK effect / provider invocation）先 ``reserve`` 拿 seq，动作完成后 ``ingest``；
- ``next_sequence = MAX(reservations ∪ receipts) + 1``；
- terminal observer 唯一排空者：能读到事实则 ingest，否则 tombstone(abandoned)；
- ``authorize_terminal`` 只在无 reserved 行且 durable == terminal 时放行。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from simple_harness.execution.context_authority import (
    ContextRouteReceipt,
    TaskScopeRoute,
)

from deskpet.execution import RunState
from deskpet.execution.evidence_ingress import (
    ExecutionEvidenceIngress,
    ObjectiveEventSpec,
    ProviderInvocationFact,
    TerminalWatermarkPending,
    ToolInvocationFact,
)
from deskpet.execution.foreground_queue import ForegroundQueueStore
from deskpet.execution.foreground_runtime import SqliteSdkTerminalObserver
from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
from deskpet.task_scope.store import TaskScopeConflict
from tests.execution import test_foreground_queue as fq

RUN = "sdk-run-res"


def _rows(db_path: Path, sql: str, *params):
    with sqlite3.connect(db_path) as db:
        return db.execute(sql, params).fetchall()


async def _bound_run(tmp_path: Path, run_id: str = RUN):
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


def _tool_fact(run_id: str, effect_id: str, *, tool_name: str = "write_file", state: str = "succeeded") -> ToolInvocationFact:
    objective = None
    if tool_name == "write_file":
        objective = ObjectiveEventSpec(
            event_kind="host.file",
            payload={"tool_name": tool_name, "effect_id": effect_id, "call_id": "call-1",
                     "outcome": state, "error_code": None, "targets": ["a.txt"]},
        )
    return ToolInvocationFact(
        run_id=run_id, effect_id=effect_id, call_id="call-1", tool_name=tool_name,
        effect_state=state, outcome=state, error_code=None, objective=objective,
    )


def _provider_fact(run_id: str, ordinal: int) -> ProviderInvocationFact:
    return ProviderInvocationFact(
        run_id=run_id, request_id=f"{run_id}:provider-turn:{ordinal}", model="model-1",
        finish_reason="stop", tool_call_count=0, error_code=None,
        usage={"input_tokens": 1, "output_tokens": 1},
    )


class _Stack:
    def __init__(self, facts: dict[str, object] | None = None) -> None:
        self.facts = facts or {}
        self.asked: list[str] = []

    def read_run_terminal_evidence(self, r: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            run_id=r, state="completed", event_id=f"sdk-terminal:{r}", event_hash="7" * 64,
            occurred_at=9.0, error_code=None,
        )

    async def read_reserved_fact(self, reservation):  # type: ignore[no-untyped-def]
        self.asked.append(reservation.source_event_id)
        return self.facts.get(reservation.source_event_id)


class _SdkIngress:
    async def wait_idle(self, r: str) -> None:
        del r

    def query(self, r: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(state=SimpleNamespace(value="completed"))


async def _observe(queue_db: Path, admission, stack: _Stack, run_id: str = RUN):
    return await SqliteSdkTerminalObserver(str(queue_db), _SdkIngress(), stack).observe(  # type: ignore[arg-type]
        host_run_id=admission.host_run_id, sdk_run_id=run_id, subject=fq.SUBJECT,
        owner_id=admission.owner_id, generation=admission.generation,
    )


# --- 四类 Harness 证据水位推进到 terminal ---------------------------------------


@pytest.mark.asyncio
async def test_four_harness_evidence_kinds_advance_watermark_to_terminal(tmp_path: Path) -> None:
    queue_db, _store, admission = await _bound_run(tmp_path)
    ingress = ExecutionEvidenceIngress(queue_db)
    ledger = ContextRouteLedgerStore(queue_db, evidence_ingress=ingress)
    # ① snapshot receipt：ledger 写事务内 reserve+ingest。
    snapshot_id, revision = await ledger.record_snapshot_receipt(
        sdk_run_id=RUN, provider_turn_ordinal=1, prior_context_revision=0,
        payload_hash="a" * 64, expected_request_fingerprint="a" * 64, source_revisions={"context": 0},
    )
    # ② provider invocation：先预留后导入。
    await ingress.reserve(run_id=RUN, task_scope_id=fq.SCOPE, kind="provider_invocation",
                          source_event_id=f"provider:{RUN}:provider-turn:1")
    await ingress.commit_fact(task_scope_id=fq.SCOPE, subject=fq.SUBJECT, fact=_provider_fact(RUN, 1))
    # ③ route decision + ④ route tool invocation：ledger 写事务内 reserve+ingest。
    receipt = ContextRouteReceipt(
        receipt_id="receipt-1", run_id=RUN, raw_call_id="raw-1", effect_id="effect-route-1",
        route=TaskScopeRoute.RESUME_EXISTING, task_scope_id=fq.SCOPE, recall_refs=(),
        binding_set_revision=1, binding_set_receipt_id="b" * 8, binding_set_receipt_hash="b" * 64,
    )
    await ledger.record_route_decision(receipt=receipt, provider_turn_ordinal=1, origin="context_tool",
                                       idempotency_key="effect-route-1")
    await ledger.record_tool_invocation(
        sdk_run_id=RUN, raw_call_id="raw-1", effect_id="effect-route-1", proposal={"route": "resume_existing"},
        verdict="accepted", decision_id=f"route-decision:{RUN}:effect-route-1", detail={"route": "resume_existing"},
    )
    # ⑤ SDK effect（write_file）：先预留、物理动作后导入（含 host.file + evidence 行）。
    await ingress.reserve(run_id=RUN, task_scope_id=fq.SCOPE, kind="tool_invocation", source_event_id="effect:effect-w1")
    await ingress.commit_fact(task_scope_id=fq.SCOPE, subject=fq.SUBJECT, fact=_tool_fact(RUN, "effect-w1"))

    kinds = _rows(
        queue_db,
        "SELECT r.source_sequence,r.evidence_kind,r.source_event_id FROM task_scope_execution_ingest_receipts r "
        "WHERE r.run_id=? ORDER BY r.source_sequence", RUN,
    )
    assert kinds == [
        (1, "context_snapshot", f"snapshot:{snapshot_id}"),
        (2, "provider_invocation", f"provider:{RUN}:provider-turn:1"),
        (3, "route_decision", f"route:route-decision:{RUN}:effect-route-1"),
        (4, "tool_invocation", "effect:effect-route-1"),
        (5, "tool_invocation", "effect:effect-w1"),
    ]
    assert revision == 1
    [(durable, terminal)] = _rows(queue_db, "SELECT durable_source_sequence,terminal_source_sequence FROM task_scope_run_watermarks WHERE run_id=?", RUN)
    assert (durable, terminal) == (5, None)
    with pytest.raises(TerminalWatermarkPending):
        await ingress.authorize_terminal(RUN)
    # host.file 客观事件 + evidence 行同事务（写在 tool_invocation 之前，序号相邻）。
    events = _rows(queue_db, "SELECT event_kind,source_kind,source_event_id FROM task_scope_events WHERE task_scope_id=? ORDER BY event_sequence", fq.SCOPE)
    assert events[-2:] == [("host.file", "host", "effect:effect-w1"), ("harness.tool_invocation", "harness", "execution:effect:effect-w1")]
    # terminal：排空（无 reserved）→ run_terminal=6 → 放行。
    observed = await _observe(queue_db, admission, _Stack())
    assert observed is not None and observed.terminal_state is RunState.COMPLETED
    gate = await ingress.authorize_terminal(RUN)
    assert gate.durable_source_sequence == gate.terminal_source_sequence == 6


# --- 乱序 / 缺口 ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_gap_in_reservations_keeps_terminal_pending_until_drained(tmp_path: Path) -> None:
    queue_db, _store, _admission = await _bound_run(tmp_path)
    ingress = ExecutionEvidenceIngress(queue_db)
    await ingress.reserve(run_id=RUN, task_scope_id=fq.SCOPE, kind="provider_invocation", source_event_id="p-1")
    await ingress.reserve(run_id=RUN, task_scope_id=fq.SCOPE, kind="tool_invocation", source_event_id="t-2")
    # 乱序：seq2 先到。
    await ingress.ingest(task_scope_id=fq.SCOPE, evidence=fq._execution_evidence(sdk_run_id=RUN, source_event_id="t-2", kind="tool_invocation", source_sequence=2))
    terminal = await ingress.ingest(
        task_scope_id=fq.SCOPE,
        source_sequence=3,
        evidence=fq._execution_evidence(sdk_run_id=RUN, source_event_id="term-3", kind="run_terminal", source_sequence=3, terminal_state=RunState.COMPLETED),
    )
    assert terminal.durable_source_sequence == 0 and terminal.terminal_source_sequence == 3
    with pytest.raises(TerminalWatermarkPending):
        await ingress.authorize_terminal(RUN)
    # 缺口由排空补齐（seq1 tombstone）→ durable 追到 3 → 放行。
    report = await ingress.drain_reservations(RUN, fact_reader=None)
    assert report.abandoned == ("p-1",) and report.ingested == ()
    gate = await ingress.authorize_terminal(RUN)
    assert gate.durable_source_sequence == 3
    # terminal 之后的预留被拒（run 已终态）。
    with pytest.raises(TaskScopeConflict, match="execution_after_terminal_rejected"):
        await ingress.reserve(run_id=RUN, task_scope_id=fq.SCOPE, kind="tool_invocation", source_event_id="late-4")


@pytest.mark.asyncio
async def test_ingest_with_explicit_sequence_must_match_reservation(tmp_path: Path) -> None:
    queue_db, _store, _admission = await _bound_run(tmp_path)
    ingress = ExecutionEvidenceIngress(queue_db)
    await ingress.reserve(run_id=RUN, task_scope_id=fq.SCOPE, kind="tool_invocation", source_event_id="t-1")
    with pytest.raises(TaskScopeConflict, match="execution_reservation_sequence_conflict"):
        await ingress.ingest(task_scope_id=fq.SCOPE, source_sequence=2, evidence=fq._execution_evidence(sdk_run_id=RUN, source_event_id="t-1", kind="tool_invocation", source_sequence=2))
    # 未预留的 seq 若与他人预留冲突 → 拒。
    with pytest.raises(TaskScopeConflict, match="execution_source_sequence_conflict"):
        await ingress.ingest(task_scope_id=fq.SCOPE, source_sequence=1, evidence=fq._execution_evidence(sdk_run_id=RUN, source_event_id="other", kind="tool_invocation", source_sequence=1))
    # 无预留且未给 seq → 无法导入。
    with pytest.raises(ValueError, match="source_sequence_required"):
        await ingress.ingest(task_scope_id=fq.SCOPE, evidence=fq._execution_evidence(sdk_run_id=RUN, source_event_id="other", kind="tool_invocation", source_sequence=1))


# --- 排空：能读到事实则 ingest（含客观事件重放收敛） ---------------------------


@pytest.mark.asyncio
async def test_drain_ingests_readable_facts_and_replays_objective_event(tmp_path: Path) -> None:
    """crash 于 SDK settle 与 Host 提交之间：terminal 前排空从 SDK 账本读到事实 → 同一
    commit_fact 路径写 host.file + evidence + tool_invocation；再次重放幂等。"""
    queue_db, _store, admission = await _bound_run(tmp_path)
    ingress = ExecutionEvidenceIngress(queue_db)
    await ingress.reserve(run_id=RUN, task_scope_id=fq.SCOPE, kind="tool_invocation", source_event_id="effect:e-1")
    await ingress.reserve(run_id=RUN, task_scope_id=fq.SCOPE, kind="provider_invocation", source_event_id=f"provider:{RUN}:provider-turn:2")
    stack = _Stack({"effect:e-1": _tool_fact(RUN, "e-1"), f"provider:{RUN}:provider-turn:2": _provider_fact(RUN, 2)})
    report = await ingress.drain_reservations(RUN, fact_reader=stack)
    assert report.ingested == ("effect:e-1", f"provider:{RUN}:provider-turn:2") and report.abandoned == ()
    host = _rows(queue_db, "SELECT event_kind,payload_json FROM task_scope_events WHERE source_event_id='effect:e-1'")
    assert host[0][0] == "host.file" and json.loads(host[0][1])["targets"] == ["a.txt"]
    links = _rows(queue_db, "SELECT COUNT(*) FROM task_scope_evidence_links l JOIN task_scope_events e ON e.event_id=l.event_id WHERE e.source_event_id IN ('effect:e-1','execution:effect:e-1')")
    assert links == [(2,)]
    # 重放同一事实（hook 路径）：幂等，行数不变。
    again = await ingress.commit_fact(task_scope_id=fq.SCOPE, subject=fq.SUBJECT, fact=_tool_fact(RUN, "e-1"))
    assert again.source_sequence == 1
    assert _rows(queue_db, "SELECT COUNT(*) FROM task_scope_events WHERE task_scope_id=?", fq.SCOPE) == [(3,)]
    # 同 source_event_id 不同事实 → hash 冲突拒绝（不静默覆盖）。
    with pytest.raises(TaskScopeConflict):
        await ingress.commit_fact(task_scope_id=fq.SCOPE, subject=fq.SUBJECT, fact=_tool_fact(RUN, "e-1", state="failed"))
    observed = await _observe(queue_db, admission, stack)
    assert observed is not None
    assert (await ingress.authorize_terminal(RUN)).terminal_source_sequence == 3


@pytest.mark.asyncio
async def test_run_without_foreground_binding_has_no_scope(tmp_path: Path) -> None:
    queue_db, _store, _admission = await _bound_run(tmp_path)
    ingress = ExecutionEvidenceIngress(queue_db)
    bound = await ingress.resolve_run_scope(RUN)
    assert bound is not None and (bound.task_scope_id, bound.subject) == (fq.SCOPE, fq.SUBJECT)
    assert await ingress.resolve_run_scope("sdk-run-unbound") is None
    # 无绑定的 Run：ledger 事实静默跳过（不产 Harness 证据，也不报错）。
    ledger = ContextRouteLedgerStore(queue_db, evidence_ingress=ingress)
    await ledger.record_snapshot_receipt(
        sdk_run_id="sdk-run-unbound", provider_turn_ordinal=1, prior_context_revision=0,
        payload_hash="c" * 64, expected_request_fingerprint="c" * 64, source_revisions={"context": 0},
    )
    assert _rows(queue_db, "SELECT COUNT(*) FROM harness_evidence_reservations WHERE run_id='sdk-run-unbound'") == [(0,)]


@pytest.mark.asyncio
async def test_reservation_and_tombstone_tables_are_append_only_and_monotonic(tmp_path: Path) -> None:
    queue_db, _store, _admission = await _bound_run(tmp_path)
    ingress = ExecutionEvidenceIngress(queue_db)
    await ingress.reserve(run_id=RUN, task_scope_id=fq.SCOPE, kind="tool_invocation", source_event_id="t-1")
    await ingress.drain_reservations(RUN, fact_reader=None)
    with sqlite3.connect(queue_db) as db:
        with pytest.raises(sqlite3.IntegrityError, match="harness_evidence_reservation_monotonic"):
            db.execute("UPDATE harness_evidence_reservations SET status='reserved' WHERE source_event_id='t-1'")
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            db.execute("DELETE FROM harness_evidence_reservations WHERE source_event_id='t-1'")
        db.execute(
            "INSERT INTO task_scope_closure_receipts(receipt_id,task_scope_id,sdk_run_id,host_run_id,"
            "closure_watermark,outcome,plan_id,reason_code,attempt_id,created_at) "
            "VALUES ('c1',?,?,'host-1',1,'pending',NULL,'r',NULL,1.0)",
            (fq.SCOPE, RUN),
        )
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            db.execute("DELETE FROM task_scope_closure_receipts")
