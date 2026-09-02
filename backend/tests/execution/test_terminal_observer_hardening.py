# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b Task 6：terminal observer / RunFaultMemo 加固（Task 1 审查 F-6 / F-9，Task 2 审查 F-6）。

- F-6：memo 在 run_terminal durable 之后即释放（后续步骤抛错也释放）；drain 失败（terminal 未 durable）
  保留 memo 供重试；非 foreground 终态路径由 ``SdkRunToolAuthorityRegistry.mark_terminal`` 释放；容量有界
- F-9：终态 ``error_code`` 只接受 ``^[a-z][a-z0-9_]{2,63}$``，否则 ``driver_failed``
- Task 2 F-6：run_terminal 走预留协议（``terminal:{run_id}`` 预留 + 同事务导入）
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.execution import RunState
from deskpet.execution.evidence_ingress import (
    ExecutionEvidenceIngress,
    TerminalWatermarkPending,
)
from deskpet.execution.foreground_runtime import (
    TERMINAL_ERROR_CODE_FALLBACK,
    SqliteSdkTerminalObserver,
)
from deskpet.sdk_adapters.run_faults import RunFaultMemo
from tests.execution import test_evidence_reservations as er
from tests.execution import test_foreground_queue as fq

RUN = "sdk-run-observer"


class _FailedStack(er._Stack):
    def __init__(self, *, error_code: str | None, facts=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(facts)
        self.error_code = error_code

    def read_run_terminal_evidence(self, r: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            run_id=r, state="failed", event_id=f"sdk-terminal:{r}", event_hash="8" * 64,
            occurred_at=9.0, error_code=self.error_code,
        )


class _FailedIngress:
    async def wait_idle(self, r: str) -> None:
        del r

    def query(self, r: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(state=SimpleNamespace(value="failed"))


async def _observe_failed(queue_db: Path, admission, stack, *, memo=None, fault=None):  # type: ignore[no-untyped-def]
    return await SqliteSdkTerminalObserver(
        str(queue_db), _FailedIngress(), stack, run_fault_memo=memo, fault_inject=fault  # type: ignore[arg-type]
    ).observe(
        host_run_id=admission.host_run_id, sdk_run_id=RUN, subject=fq.SUBJECT,
        owner_id=admission.owner_id, generation=admission.generation,
    )


def _terminal_public(queue_db: Path) -> dict:
    [(payload_json,)] = er._rows(
        queue_db,
        "SELECT e.payload_json FROM task_scope_execution_ingest_receipts r JOIN task_scope_events e "
        "ON e.event_id=r.event_id WHERE r.run_id=? AND r.evidence_kind='run_terminal'",
        RUN,
    )
    return json.loads(payload_json)["public_payload"]


# --- Task 2 F-6：run_terminal 走预留协议 ----------------------------------------


@pytest.mark.asyncio
async def test_run_terminal_reserved_then_ingested(tmp_path: Path) -> None:
    queue_db, _store, admission = await er._bound_run(tmp_path, RUN)
    ingress = ExecutionEvidenceIngress(queue_db)
    await ingress.reserve(run_id=RUN, task_scope_id=fq.SCOPE, kind="tool_invocation", source_event_id="effect:e-1", tool_name="write_file")
    await ingress.commit_fact(task_scope_id=fq.SCOPE, subject=fq.SUBJECT, fact=er._tool_fact(RUN, "e-1"))
    observed = await er._observe(queue_db, admission, er._Stack(), run_id=RUN)
    assert observed is not None and observed.terminal_state is RunState.COMPLETED
    rows = er._rows(
        queue_db,
        "SELECT r.kind,r.status,r.source_sequence,r.source_event_id,i.source_sequence FROM harness_evidence_reservations r "
        "JOIN task_scope_execution_ingest_receipts i ON i.source_event_id=r.source_event_id "
        "WHERE r.run_id=? ORDER BY r.source_sequence",
        RUN,
    )
    assert rows == [
        ("tool_invocation", "ingested", 1, "effect:e-1", 1),
        ("run_terminal", "ingested", 2, f"sdk-terminal:{RUN}", 2),
    ]
    # 预留 id 由 terminal:{run_id} 派生：一 Run 恰一条 terminal 预留。
    from deskpet.task_scope.store import _uuid

    [(reservation_id,)] = er._rows(queue_db, "SELECT reservation_id FROM harness_evidence_reservations WHERE kind='run_terminal' AND run_id=?", RUN)
    assert reservation_id == _uuid(f"harness-evidence-reservation:terminal:{RUN}")
    [(terminal_seq, durable_seq)] = er._rows(queue_db, "SELECT terminal_source_sequence,durable_source_sequence FROM task_scope_run_watermarks WHERE run_id=?", RUN)
    assert (terminal_seq, durable_seq) == (2, 2)
    # 重放：同一行、同一 receipt（幂等）。
    again = await er._observe(queue_db, admission, er._Stack(), run_id=RUN)
    assert again is not None and again.sdk_event_id == observed.sdk_event_id
    assert er._rows(queue_db, "SELECT COUNT(*) FROM harness_evidence_reservations WHERE run_id=?", RUN) == [(2,)]
    # terminal 之后的迟到预留稳定拒绝。
    from deskpet.task_scope.store import TaskScopeConflict

    with pytest.raises(TaskScopeConflict, match="execution_after_terminal_rejected"):
        await ingress.reserve(run_id=RUN, task_scope_id=fq.SCOPE, kind="tool_invocation", source_event_id="effect:late")


# --- F-6：memo 释放语义 ----------------------------------------------------------


@pytest.mark.asyncio
async def test_run_fault_memo_released_on_observer_failure(tmp_path: Path) -> None:
    """① drain 失败（terminal 未 durable）→ memo 保留，重试仍能写稳定码；
    ② run_terminal durable 之后的步骤抛错 → observe 抛出但 memo 已释放，重试从 durable 行收敛。"""

    queue_db, _store, admission = await er._bound_run(tmp_path, RUN)
    ingress = ExecutionEvidenceIngress(queue_db)
    await ingress.reserve(run_id=RUN, task_scope_id=fq.SCOPE, kind="tool_invocation", source_event_id="effect:e-1", tool_name="write_file")
    memo = RunFaultMemo()
    memo.record(RUN, "sdk_task_execution_root_authority_ambiguous")

    class _BrokenReader(_FailedStack):
        async def read_reserved_fact(self, reservation):  # type: ignore[no-untyped-def]
            raise RuntimeError("ledger unavailable")

    # ① drain 抛错：terminal 未 durable → memo 保留。
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        await _observe_failed(queue_db, admission, _BrokenReader(error_code="driver_failed"), memo=memo)
    assert memo.read(RUN) == "sdk_task_execution_root_authority_ambiguous"
    assert er._rows(queue_db, "SELECT COUNT(*) FROM task_scope_execution_ingest_receipts WHERE run_id=? AND evidence_kind='run_terminal'", RUN) == [(0,)]

    # ② run_terminal 导入之后、authorize_terminal 之前抛错：observe 抛，但 memo 已释放且稳定码已 durable。
    def boom(point: str) -> None:
        if point == "terminal-ingested":
            raise RuntimeError("post-ingest failure")

    with pytest.raises(RuntimeError, match="post-ingest failure"):
        await _observe_failed(queue_db, admission, _FailedStack(error_code="driver_failed"), memo=memo, fault=boom)
    assert memo.read(RUN) is None
    assert _terminal_public(queue_db)["error_code"] == "sdk_task_execution_root_authority_ambiguous"
    # 重试（memo 已空）：从 durable 行收敛，稳定码不退化。
    observed = await _observe_failed(queue_db, admission, _FailedStack(error_code="driver_failed"), memo=memo)
    assert observed is not None and observed.terminal_state is RunState.FAILED
    assert _terminal_public(queue_db)["error_code"] == "sdk_task_execution_root_authority_ambiguous"


def test_run_fault_memo_is_bounded_and_registry_terminal_releases() -> None:
    from simple_harness import RunId

    from deskpet.sdk_adapters.tool_authority import SdkRunToolAuthorityRegistry
    from tests.sdk_adapters.test_tool_authority import _prepare

    memo = RunFaultMemo(capacity=2)
    memo.record("run-1", "a_code")
    memo.record("run-2", "b_code")
    memo.record("run-3", "c_code")
    assert len(memo) == 2 and memo.read("run-1") is None and memo.read("run-3") == "c_code"
    with pytest.raises(ValueError):
        RunFaultMemo(capacity=0)
    # 非 foreground 终态路径：registry.mark_terminal 释放 sink。
    registry = SdkRunToolAuthorityRegistry(run_fault_sink=memo)
    _prepare(registry, "run-x")
    memo.record("run-x", "catalog_execution_policy_unavailable")
    registry.mark_terminal(RunId("run-x"), "failed")
    assert memo.read("run-x") is None


# --- F-9：error_code 白名单 -------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "memo_code,sdk_code,expected",
    [
        ("driver_failed: /Users/x/secret.txt", None, TERMINAL_ERROR_CODE_FALLBACK),
        (None, "Driver Failed", TERMINAL_ERROR_CODE_FALLBACK),
        (None, "x" * 70, TERMINAL_ERROR_CODE_FALLBACK),
        ("sdk_task_execution_route_authority_missing", "driver_failed", "sdk_task_execution_route_authority_missing"),
        (None, "driver_failed", "driver_failed"),
    ],
)
async def test_terminal_error_code_whitelist(tmp_path: Path, memo_code, sdk_code, expected) -> None:
    queue_db, _store, admission = await er._bound_run(tmp_path, RUN)
    memo = RunFaultMemo()
    if memo_code is not None:
        memo.record(RUN, memo_code)
    observed = await _observe_failed(queue_db, admission, _FailedStack(error_code=sdk_code), memo=memo)
    assert observed is not None and observed.terminal_state is RunState.FAILED
    assert _terminal_public(queue_db)["error_code"] == expected
    assert memo.read(RUN) is None


@pytest.mark.asyncio
async def test_terminal_pending_after_durable_terminal_keeps_row(tmp_path: Path) -> None:
    """authorize_terminal 失败（reserved 残留由并发写入插入）不撤销已 durable 的 run_terminal 行。"""

    queue_db, _store, admission = await er._bound_run(tmp_path, RUN)
    ingress = ExecutionEvidenceIngress(queue_db)

    async def late_reserve(point: str) -> None:
        del point

    def inject(point: str) -> None:
        if point == "terminal-ingested":
            raise TerminalWatermarkPending(TerminalWatermarkPending.code)

    with pytest.raises(TerminalWatermarkPending):
        await _observe_failed(queue_db, admission, _FailedStack(error_code="driver_failed"), memo=RunFaultMemo(), fault=inject)
    assert er._rows(queue_db, "SELECT status FROM harness_evidence_reservations WHERE run_id=? AND kind='run_terminal'", RUN) == [("ingested",)]
    assert (await ingress.authorize_terminal(RUN)).terminal_source_sequence == 1
    await late_reserve("noop")
