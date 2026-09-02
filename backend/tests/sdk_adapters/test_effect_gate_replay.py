# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Task 1 审查 F-1（P1）回归：EffectGate 步骤 0「exact replay 不重验」。

真实 SDK ``EffectExecutor.execute``（不覆写）+ 真实 SDK sqlite ``SqliteExecutionUnitOfWork``：
已有 durable effect 记录（任一状态）时 ``ProductEffectExecutor.execute`` 跳过 gate，直接交 SDK
replay / reconcile；仅首次出现的 effect 才跑 gate。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from simple_harness import CallId, EffectId, RequestId, RunId
from simple_harness.execution.effects import EffectState
from simple_harness.execution.sqlite import Database, SqliteExecutionUnitOfWork
from simple_harness.tools import (
    FunctionTool,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from simple_harness.tools.authorization import (
    AuthorizationDecision,
    AuthorizationReceipt,
    AuthorizationResult,
)
from simple_harness.tools.contracts import CancellationToken
from simple_harness.tools.reconciliation import (
    ReconciliationObservation,
    ReconciliationState,
)
from simple_harness.tools.registry import ToolRegistry

from deskpet.sdk_adapters.tools import ProductEffectExecutor

RUN = RunId("run-replay-1")
WRITE_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}


class _Registry(ToolRegistry):
    def assert_workspace_current(self, run_id) -> None:
        del run_id


class _AllowAuthorization:
    async def prepare(self, prepared) -> AuthorizationResult:  # type: ignore[no-untyped-def]
        del prepared
        return AuthorizationResult(AuthorizationDecision.ALLOW, receipt_ref="host:allow:1")

    async def bind_effect_handoff(self, prepared, authorization_receipt_ref, sdk_receipt):  # type: ignore[no-untyped-def]
        del prepared, authorization_receipt_ref
        return AuthorizationReceipt(
            receipt_ref="host:handoff:1",
            receipt_hash=sdk_receipt.receipt_hash,
            bound_sdk_receipt_hash=sdk_receipt.receipt_hash,
        )


class _Reconciliation:
    def __init__(self) -> None:
        self.observation: ReconciliationObservation | None = None
        self.observed: list[str] = []

    async def observe(self, record):  # type: ignore[no-untyped-def]
        self.observed.append(record.effect_id.value)
        assert self.observation is not None
        return self.observation


class _Gate:
    """EffectGate 替身：只关心「是否被调用」与「拒绝时的稳定码」。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.reject_with: str | None = None

    async def verify(self, context, tool_name, *, call_id=None):  # type: ignore[no-untyped-def]
        self.calls.append(tool_name)
        if self.reject_with is None:
            return None
        return ToolResult.rejected(call_id or context.call_id, self.reject_with, "gate")


class _Case:
    def __init__(self, root: Path, *, evidence_ingress=None) -> None:  # type: ignore[no-untyped-def]
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = Database.open(self.root / "sdk-execution.sqlite3")
        self.uow = SqliteExecutionUnitOfWork(self.database)
        self.uow.create_with_start_snapshot(
            execution_session_id="session-1",
            run_id=RUN.value,
            request_id="request-1",
            profile_key="agent.general",
            driver_kind="react",
            snapshot={"schema_version": 1},
            event_id="run-created",
            now=1.0,
        )
        _, self.lease = self.uow.claim_runtime_activation(
            run_id=RUN.value, owner_id="runtime-1", namespace="runtime.kernel",
            now=2.0, lease_ttl_seconds=1000.0,
        )
        self.fence = None
        self.handler_calls = 0
        self.fail_handler = False
        self.gate = _Gate()
        self.reconciliation = _Reconciliation()

        async def write_file(arguments, context):  # type: ignore[no-untyped-def]
            self.handler_calls += 1
            if self.fail_handler:
                # 进程级中断（BaseException 语义）：registry 不吞，executor 记 UNKNOWN 后上抛。
                raise asyncio.CancelledError("handler crashed mid-flight")
            target = self.root / str(arguments["path"])
            target.write_text("written", encoding="utf-8")
            return ToolResult.succeeded(context.call_id, {"ok": True, "written": str(target)})

        self.registry = _Registry(
            [FunctionTool(ToolSpec("write_file", "Write a file.", WRITE_SCHEMA), write_file)]
        )
        self.executor = ProductEffectExecutor(
            uow=self.uow,
            registry=self.registry,
            authorization=_AllowAuthorization(),
            reconciliation=self.reconciliation,
            effect_gate=self.gate,
            evidence_ingress=evidence_ingress,
            clock=lambda: 3.0,  # 与 SDK lease（now=2.0, ttl=1000s）同一时钟
        )

    async def initialize(self) -> None:
        self.fence = await self.uow.acquire(RUN, self.lease, now=2.0)

    async def execute(self, effect_id: str = "effect-1", path: str = "note.txt"):  # type: ignore[no-untyped-def]
        call = ToolCall(CallId(f"call:{effect_id}"), "write_file", {"path": path})
        context = ToolContext(
            RUN, RequestId("request-1"), CancellationToken(),
            call_id=call.call_id, effect_id=EffectId(effect_id),
        )
        return await self.executor.execute(
            effect_id=EffectId(effect_id), call=call, context=context,
            execution_lease=self.lease, run_fence=self.fence,
            raw_call_id=f"raw:{effect_id}", turn_ordinal=1, call_ordinal=0,
        )

    def ledger_state(self, effect_id: str = "effect-1") -> str:
        with sqlite3.connect(self.root / "sdk-execution.sqlite3") as db:
            row = db.execute(
                "SELECT state FROM execution_effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
        return "" if row is None else str(row[0])

    def close(self) -> None:
        self.database.close()


@pytest.mark.asyncio
async def test_gate_skips_replay_of_settled_effect_and_returns_original_result(tmp_path: Path) -> None:
    """settle 后 gate 条件失效（例如 binding 追加）→ 重放同 effect：返回原 SUCCEEDED 结果，
    gate 不再调用、handler 调用次数不变、磁盘/账本一致。"""
    case = _Case(tmp_path / "case")
    await case.initialize()
    try:
        first = await case.execute()
        assert first.effect is not None and first.effect.state is EffectState.SUCCEEDED
        assert first.result.outcome.value == "succeeded"
        assert case.gate.calls == ["write_file"]
        assert case.handler_calls == 1
        assert case.ledger_state() == "succeeded"

        # gate 条件变化（模拟 binding 被追加 → superseded）。
        case.gate.reject_with = "workspace_binding_receipt_superseded"
        replay = await case.execute()
        assert replay.result.outcome.value == "succeeded"
        assert replay.result == first.result
        assert replay.effect is not None and replay.effect.state is EffectState.SUCCEEDED
        assert case.gate.calls == ["write_file"]  # 步骤 0：exact replay 不重验
        assert case.handler_calls == 1
        assert (tmp_path / "case" / "note.txt").read_text(encoding="utf-8") == "written"

        # 首次出现的新 effect 仍要过门：被拒且无 execution_effects 行。
        rejected = await case.execute(effect_id="effect-2", path="other.txt")
        assert rejected.effect is None
        assert rejected.result.outcome.value == "rejected"
        assert rejected.result.error_code == "workspace_binding_receipt_superseded"
        assert case.gate.calls == ["write_file", "write_file"]
        assert case.ledger_state("effect-2") == ""
        assert not (tmp_path / "case" / "other.txt").exists()
    finally:
        case.close()


@pytest.mark.asyncio
async def test_gate_does_not_preempt_unknown_effect_reconciliation(tmp_path: Path) -> None:
    """handler 中途崩溃 → effect UNKNOWN；恢复后即使 gate 条件失效也必须先走 SDK reconcile
    （COMPLETED → 返回对账结果），而不是把一个 rejected 伪装成终态。"""
    case = _Case(tmp_path / "case")
    await case.initialize()
    try:
        case.fail_handler = True
        with pytest.raises(asyncio.CancelledError):
            await case.execute()
        assert case.ledger_state() == "unknown"
        assert case.gate.calls == ["write_file"]

        case.fail_handler = False
        case.gate.reject_with = "effect_gate_frozen_scope_mismatch"
        reconciled_result = ToolResult.succeeded(CallId("call:effect-1"), {"ok": True, "reconciled": True})
        case.reconciliation.observation = ReconciliationObservation(
            ReconciliationState.COMPLETED, "external:receipt:1", reconciled_result
        )
        execution = await case.execute()
        assert case.reconciliation.observed == ["effect-1"]
        assert case.gate.calls == ["write_file"]  # gate 未抢在 reconcile 之前
        assert execution.effect is not None and execution.effect.state is EffectState.SUCCEEDED
        assert execution.result.outcome.value == "succeeded"
        assert execution.result.value == {"ok": True, "reconciled": True}
        assert case.handler_calls == 1  # reconcile 不再进 handler
        assert case.ledger_state() == "succeeded"
    finally:
        case.close()


# --- Task 2 审查 F-1 / F-2（Task 3 附带修复；真实 SDK executor + 真实 ingress + foreground 绑定）------


async def _bound_case(tmp_path: Path):  # type: ignore[no-untyped-def]
    from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress
    from tests.sdk_adapters import s5b_closure_harness as ch

    env = await ch.bound_run(tmp_path / "state", RUN.value)
    case = _Case(tmp_path / "case", evidence_ingress=ExecutionEvidenceIngress(env.db_path))
    await case.initialize()
    return ch, env, case


@pytest.mark.asyncio
async def test_commit_fact_redacts_credential_like_path_and_never_raises_after_settle(tmp_path: Path) -> None:
    """F-1（P1）：模型给的路径命中凭据形状（``docs/bearer authentication.md``）时，settle 之后的 Host 记账
    **永不抛出**：路径片段确定性脱敏为 ``[redacted:credential]``、事件 payload ``redacted=true``、事件照写、
    material 照标；重放同错不再发生；terminal 排空与放行收敛。"""
    import json

    from deskpet.execution.semantic_closure import dirty_state
    from deskpet.task_scope.store import CanonicalTaskScopeStore

    ch, env, case = await _bound_case(tmp_path)
    try:
        path = "docs/bearer authentication.md"
        first = await case.execute(effect_id="effect-cred", path=path)
        assert first.effect is not None and first.effect.state is EffectState.SUCCEEDED
        assert first.result.outcome.value == "succeeded"
        assert (tmp_path / "case" / path).read_text(encoding="utf-8") == "written"
        [(kind, payload_json)] = ch.rows(
            env.db_path,
            "SELECT event_kind,payload_json FROM task_scope_events WHERE source_kind='host' AND source_event_id='effect:effect-cred'",
        )
        payload = json.loads(payload_json)
        assert kind == "host.file" and payload["redacted"] is True
        assert payload["targets"] == ["docs/[redacted:credential].md"]
        assert "bearer authentication" not in payload_json
        assert ch.rows(env.db_path, "SELECT status FROM harness_evidence_reservations WHERE source_event_id='effect:effect-cred'") == [("ingested",)]
        # 重放（exact replay）：同一结果、不再抛错、恰一份事件。
        replay = await case.execute(effect_id="effect-cred", path=path)
        assert replay.result == first.result
        assert ch.rows(env.db_path, "SELECT COUNT(*) FROM task_scope_events WHERE source_event_id='effect:effect-cred'") == [(1,)]
        # 另一种凭据形状（sk-…）同样脱敏、不抛。
        second = await case.execute(effect_id="effect-key", path="keys/sk-abcdefghijklmnopqrst.txt")
        assert second.result.outcome.value == "succeeded"
        [(payload_json2,)] = ch.rows(env.db_path, "SELECT payload_json FROM task_scope_events WHERE source_kind='host' AND source_event_id='effect:effect-key'")
        assert json.loads(payload_json2)["targets"] == ["keys/[redacted:credential].txt"]
        dirty = await dirty_state(CanonicalTaskScopeStore(env.db_path), ch.SCOPE)
        assert sorted(e.event_kind for e in dirty.material_events) == ["harness.tool_invocation", "harness.tool_invocation", "host.file", "host.file"]
        # terminal：排空 + run_terminal + 放行，收敛。
        observed = await ch.observe_terminal(env, ch.FakeRunFacts(env.run_id))
        assert observed is not None
        gate = await env.ingress.authorize_terminal(env.run_id)
        assert gate.terminal_source_sequence == 3
    finally:
        case.close()


@pytest.mark.asyncio
async def test_abandoned_project_effect_reservation_is_material_dirty(tmp_path: Path) -> None:
    """F-2（P1）：Run 终态时仍 UNKNOWN/HANDED_OFF 的 PROJECT_EFFECT 预留 → tombstone 保留 kind 并携带
    ``effect_class=project_effect``（预留行记 tool_name）；映射表把它判为 **material**（closure 必须记账，
    文件可能已写），dirty_state 不再静默"干净"。"""
    import json

    from deskpet.execution.semantic_closure import dirty_state, is_material_event
    from deskpet.task_scope.store import CanonicalTaskScopeStore

    ch, env, case = await _bound_case(tmp_path)
    try:
        case.fail_handler = True
        with pytest.raises(asyncio.CancelledError):
            await case.execute(effect_id="effect-unknown", path="maybe.txt")
        assert case.ledger_state("effect-unknown") == "unknown"
        [(status, tool_name)] = ch.rows(env.db_path, "SELECT status,tool_name FROM harness_evidence_reservations WHERE source_event_id='effect:effect-unknown'")
        assert (status, tool_name) == ("reserved", "write_file")
        assert not (await dirty_state(CanonicalTaskScopeStore(env.db_path), ch.SCOPE)).is_dirty
        # terminal 排空：SDK 账本非终态 → tombstone（同 kind），携带 tool_name / effect_class。
        observed = await ch.observe_terminal(env, ch.FakeRunFacts(env.run_id))
        assert observed is not None
        [(kind, payload_json)] = ch.rows(
            env.db_path,
            "SELECT e.event_kind,e.payload_json FROM task_scope_execution_ingest_receipts r JOIN task_scope_events e ON e.event_id=r.event_id WHERE r.source_event_id='effect:effect-unknown'",
        )
        public = json.loads(payload_json)["public_payload"]
        assert kind == "harness.tool_invocation"
        assert public["status"] == "abandoned" and public["tool_name"] == "write_file" and public["effect_class"] == "project_effect"
        assert is_material_event("harness.tool_invocation", json.loads(payload_json)) is True
        dirty = await dirty_state(CanonicalTaskScopeStore(env.db_path), ch.SCOPE)
        assert dirty.is_dirty and [e.event_kind for e in dirty.material_events] == ["harness.tool_invocation"]
        assert dirty.material_events[0].source_event_id == "effect:effect-unknown"
        # 非 PROJECT_EFFECT 的 abandoned tombstone 仍是 trivial。
        assert is_material_event(
            "harness.tool_invocation",
            {"public_payload": {"status": "abandoned", "tool_name": "read_file", "effect_class": "non_project_effect"}},
        ) is False
        assert ch.rows(env.db_path, "SELECT status FROM harness_evidence_reservations WHERE source_event_id='effect:effect-unknown'") == [("abandoned",)]
    finally:
        case.close()
