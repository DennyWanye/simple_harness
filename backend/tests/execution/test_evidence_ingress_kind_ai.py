# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""事件 AI：读工具效应 + 有界重采回执穿过归档入口，未知种类只失败一个效应。

第 12 次 HM-TO-A6 第 18 轮的失败形状（证据
`.local-test-evidence/2026-09-09/native-a6-run12/primary-ui-z9j48osx/`）：

1. F-Z1 新暴露的 `glob` 连续三次成功（`execution_effects` turn 12 call 0/1/2），
   三条 `tool_invocation` 预留全部 `ingested`（`harness_evidence_reservations`
   seq 43/44/45）——**读工具本身从来不是被拒的那一条**；
2. 紧接着 seq 46 落了 turn 13 的 `context_snapshot` 回执；
3. 然后组装 provider 请求时，用途围栏租约进入 10s 余量，事件 AA 的有界重采触发，
   `record_context_use_recollection` 以 `kind="context_use_recollection"` 走
   `ingest_ledger_fact_tx` → `reserve_tx` 词表校验 → `ValueError`
   （`native.log:2403` `execution_evidence_kind_rejected` / `ValueError` /
   `sdk_run_driver_failed`），整个 Run 被打死。

本文件按这个顺序复现：读效应先落，重采回执后落。修复前第 3 步红，修复后绿。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from deskpet.execution.evidence_ingress import (
    HOST_HARNESS_FACT_AUTHORITY_REF,
    EvidenceKindRejected,
    ExecutionEvidenceIngress,
    _host_evidence,
)
from deskpet.execution.semantic_closure import TRIVIAL_EVENT_KINDS, is_material_event
from deskpet.memory.evidence_kind_schema import initialize_evidence_kind_state_db
from deskpet.execution.foreground_queue import ForegroundQueueStore
from tests.execution import test_foreground_queue as fq
from tests.execution.test_evidence_reservations import _rows, _tool_fact

RUN = "sdk-run-ai"

#: 事故里模型实际连续调用成功的三个读工具效应（F-Z1 `glob`）。
GLOB_EFFECTS = ("effect-glob-1", "effect-glob-2", "effect-glob-3")


def host_evidence(*, kind: str, run_id: str = RUN, subject: str = "subject-ai"):
    """一条最小的 Host ExecutionEvidence，仅 `kind` 变化（协议侧词表用例复用）。"""

    return _host_evidence(
        run_id=run_id,
        subject=subject,
        kind=kind,
        event_id=f"host-fact:{kind}",
        public_payload={"probe": kind},
        refs=[],
        idempotency_key=f"harness-fact:{kind}",
        occurred_at=1788931788.0,
        authority_ref=HOST_HARNESS_FACT_AUTHORITY_REF,
    )


async def _ready_run(tmp_path: Path):
    """事故形状的 Run：先把档案库迁到 v57，再让前台 Run 进入 RUNNING。

    顺序不能反：v57 重建 `harness_evidence_reservations`，沿用 v46 起的割接前置
    条件——有非终态前台 Run 时拒绝迁移（正在写这张表的 Run 不能被搬走）。
    """

    queue_db, primary_id, clock = await fq._ready(tmp_path / "queue")
    await initialize_evidence_kind_state_db(queue_db)
    store = ForegroundQueueStore(queue_db, clock=clock)
    await fq._enqueue(store, primary_id, 1)
    admission = await fq._claim_and_bind(store, sdk_run_id=RUN)
    await store.record_sdk_started(
        host_run_id=admission.host_run_id, sdk_run_id=RUN, owner_id=admission.owner_id,
        generation=admission.generation, sdk_event_id=f"sdk-start:{RUN}",
        idempotency_key=f"sdk-start:{RUN}",
    )
    return queue_db, store, admission


async def _ledger_fact(ingress: ExecutionEvidenceIngress, *, kind: str, source_event_id: str,
                       payload: dict[str, object] | None = None):
    """按 ledger 的用法在调用方写事务内 reserve+ingest 一条 state.db 事实。"""

    await ingress._store.initialize()
    async with ingress._store._connection() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            receipt = await ingress.ingest_ledger_fact_tx(
                db,
                run_id=RUN,
                kind=kind,
                source_event_id=source_event_id,
                public_payload=payload or {"effect_id": "effect-glob-3", "generation": 1},
                occurred_at=1788931788.0,
            )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
    return receipt


async def _settle_read_effects(ingress: ExecutionEvidenceIngress) -> None:
    """三次成功的 `glob`：预留 → 物理动作 → 导入（与事故完全同形）。"""

    for effect_id in GLOB_EFFECTS:
        await ingress.reserve(run_id=RUN, task_scope_id=fq.SCOPE, kind="tool_invocation",
                              source_event_id=f"effect:{effect_id}", tool_name="glob")
        await ingress.commit_fact(
            task_scope_id=fq.SCOPE, subject=fq.SUBJECT,
            fact=_tool_fact(RUN, effect_id, tool_name="glob"),
        )


# --- 复现：读效应 + 重采回执 -----------------------------------------------------


@pytest.mark.asyncio
async def test_glob_effects_then_recollection_receipt_reach_the_archive(tmp_path: Path) -> None:
    """事故轮的完整形状：3×glob → context_snapshot → context_use_recollection。

    修复前最后一步抛 `ValueError("execution_evidence_kind_rejected")`（红）。
    """

    queue_db, _store, _admission = await _ready_run(tmp_path)
    ingress = ExecutionEvidenceIngress(queue_db)

    await _settle_read_effects(ingress)
    await _ledger_fact(ingress, kind="context_snapshot", source_event_id="snapshot:ctx-snap:13",
                       payload={"snapshot_id": "ctx-snap:13", "provider_turn_ordinal": 13})
    receipt = await _ledger_fact(
        ingress, kind="context_use_recollection",
        source_event_id="context-use-recollect:effect-glob-3:1",
        payload={"effect_id": "effect-glob-3", "generation": 1,
                 "reason_code": "context_use_recollected", "bound_source_count": 2},
    )

    assert receipt is not None
    kinds = _rows(
        queue_db,
        "SELECT source_sequence,evidence_kind,source_event_id "
        "FROM task_scope_execution_ingest_receipts WHERE run_id=? ORDER BY source_sequence", RUN,
    )
    assert kinds == [
        (1, "tool_invocation", "effect:effect-glob-1"),
        (2, "tool_invocation", "effect:effect-glob-2"),
        (3, "tool_invocation", "effect:effect-glob-3"),
        (4, "context_snapshot", "snapshot:ctx-snap:13"),
        (5, "context_use_recollection", "context-use-recollect:effect-glob-3:1"),
    ]
    # 读工具效应按 tool_invocation 投影，工具名进 reservation（不是自成一类）。
    assert _rows(queue_db, "SELECT DISTINCT tool_name FROM harness_evidence_reservations "
                 "WHERE run_id=? AND kind='tool_invocation'", RUN) == [("glob",)]
    # 重采回执是 Harness 事实，不是工具效应：没有 tool_name。
    assert _rows(queue_db, "SELECT tool_name FROM harness_evidence_reservations "
                 "WHERE run_id=? AND kind='context_use_recollection'", RUN) == [(None,)]
    events = _rows(queue_db, "SELECT event_kind FROM task_scope_events WHERE task_scope_id=? "
                   "ORDER BY event_sequence", fq.SCOPE)
    assert "harness.context_use_recollection" in {kind for (kind,) in events}


def test_read_tool_and_recollection_projections_are_trivial() -> None:
    """投影规则：读工具效应与重采回执都不脏化任务档案。"""

    assert not is_material_event(
        "harness.tool_invocation", {"public_payload": {"tool_name": "glob"}}
    )
    assert "harness.context_use_recollection" in TRIVIAL_EVENT_KINDS
    assert not is_material_event("harness.context_use_recollection", None)


# --- 反例：真正未知的种类，只失败一个效应，Run 继续 --------------------------------


@pytest.mark.asyncio
async def test_unknown_kind_fails_one_effect_and_the_run_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    queue_db, _store, _admission = await _ready_run(tmp_path)
    ingress = ExecutionEvidenceIngress(queue_db)
    await _settle_read_effects(ingress)
    before = _rows(queue_db, "SELECT count(*) FROM harness_evidence_reservations WHERE run_id=?", RUN)

    with caplog.at_level(logging.ERROR, logger="deskpet.execution.evidence_ingress"):
        with pytest.raises(EvidenceKindRejected) as caught:
            await _ledger_fact(ingress, kind="harness_tool_read",
                               source_event_id="effect:effect-unknown-1")

    # 稳定码 + 种类名可归因，且仍是 ValueError（旧调用方期望不变）。
    assert caught.value.code == "execution_evidence_kind_rejected"
    assert caught.value.kind == "harness_tool_read"
    assert isinstance(caught.value, ValueError)
    assert str(caught.value) == "execution_evidence_kind_rejected"
    assert "harness_tool_read" in caplog.text
    assert "execution_evidence_kind_rejected" in caplog.text

    # 只失败这一个事实：没有预留、没有回执、没有序号被吃掉。
    assert _rows(queue_db, "SELECT count(*) FROM harness_evidence_reservations WHERE run_id=?", RUN) == before
    assert _rows(queue_db, "SELECT count(*) FROM task_scope_execution_ingest_receipts "
                 "WHERE source_event_id=?", "effect:effect-unknown-1") == [(0,)]

    # Run 还活着：下一条合法事实照常入档，水位连续推进。
    receipt = await _ledger_fact(ingress, kind="context_snapshot",
                                 source_event_id="snapshot:ctx-snap:14",
                                 payload={"snapshot_id": "ctx-snap:14"})
    assert receipt is not None and receipt.source_sequence == 4
    [(durable, terminal)] = _rows(
        queue_db, "SELECT durable_source_sequence,terminal_source_sequence "
        "FROM task_scope_run_watermarks WHERE run_id=?", RUN)
    assert (durable, terminal) == (4, None)


@pytest.mark.asyncio
async def test_unknown_kind_is_refused_before_the_write_lock(tmp_path: Path) -> None:
    """`reserve()` 在拿 BEGIN IMMEDIATE 之前就拒：未知种类不会阻塞其它写者。"""

    queue_db, _store, _admission = await _ready_run(tmp_path)
    ingress = ExecutionEvidenceIngress(queue_db)
    with pytest.raises(EvidenceKindRejected):
        await ingress.reserve(run_id=RUN, task_scope_id=fq.SCOPE, kind="workspace_read",
                              source_event_id="effect:effect-unknown-2")
    with sqlite3.connect(queue_db) as db:
        assert db.execute("SELECT count(*) FROM harness_evidence_reservations").fetchone()[0] == 0
