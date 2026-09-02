# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b Task 6：EffectGate hardening（Task 1 审查 F-2/F-3/F-7/F-8/F-10、Task 2 审查 F-7、Task 3 审查 F-8）。

- F-2：route receipt / verify / head / status 四次读同一 SQLite 读快照；预留写事务内再核 head
- F-3：sticky memo ``effect_gate_rejections(sdk_run_id, route_receipt_id)``，直到新 route receipt
- F-7：binding head 缺行 → ``workspace_binding_effect_authority_missing``（非 superseded）
- F-8：``context.effect_id`` 缺失 → ``effect_gate_envelope_identity_mismatch``
- F-10：inode 漂移 / symlink 根 / 跨 Run envelope / exposure 无 execution_policy 直通 / memo 多 Run 隔离
- Task 2 F-7：SDK 拒绝（effect=None）后的预留立即 ``rejected`` tombstone
- Task 3 F-8：排空对 ``commit_fact`` 的确定性拒绝降级为 ``rejected_fact`` tombstone（仍 material）
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from simple_harness import CallId, EffectId, RunId
from simple_harness.tools import ToolCall, ToolContext
from simple_harness.tools.contracts import CancellationToken

from deskpet.sdk_adapters.effect_gate import (
    EFFECT_GATE_STICKY_REASON,
    EffectGate,
    EffectGateRejected,
)
from deskpet.sdk_adapters.run_faults import RunFaultMemo
from deskpet.task_scope.store import TaskScopeNotFound
from tests.sdk_adapters import s5b_effect_gate_harness as h
from tests.sdk_adapters.test_effect_gate import _code, _context, _routed_write


def _write_call(envelope, path: str = "z.txt") -> ToolCall:  # type: ignore[no-untyped-def]
    return ToolCall(envelope.call_id, "write_file", {"path": path, "content": "zeta"})


async def _execute(env, envelope, *, path: str = "z.txt", context: ToolContext | None = None):  # type: ignore[no-untyped-def]
    return await env.effects.execute(
        effect_id=envelope.effect_id,
        call=_write_call(envelope, path),
        context=context or _context(envelope),
        execution_lease=h.LEASE,
        run_fence=h.FENCE,
        raw_call_id="raw-write",
        turn_ordinal=2,
        call_ordinal=0,
    )


def _memo_rows(env) -> list[tuple]:  # type: ignore[no-untyped-def]
    return h.rows(
        env.db_path,
        "SELECT sdk_run_id,route_receipt_id,reason_code FROM effect_gate_rejections ORDER BY created_at",
    )


async def _new_route_receipt(env, scope_id: str, *, turn: int, tag: str):  # type: ignore[no-untyped-def]
    """Issue a fresh ``context_route`` receipt for the same Run through the production executor."""

    from simple_harness.execution.context_authority import TaskExecutionEnvelopeRequest

    policy = env.exposure.execution_policy(h.RUN, "context_route")
    call_id = CallId(f"call-route-{tag}")
    effect_id = EffectId(f"effect-route-{tag}")
    envelope = await env.task_authority.issue_envelope(
        TaskExecutionEnvelopeRequest(
            h.RUN, call_id.value, effect_id.value, f"raw-route-{tag}", turn, 0, "context_route", policy, None
        )
    )
    context = ToolContext(
        h.RUN, h.REQUEST, CancellationToken(), call_id=call_id, effect_id=effect_id,
        task_execution_envelope=envelope,
    )
    execution = await env.effects.execute(
        effect_id=effect_id,
        call=ToolCall(call_id, "context_route", {"route": "resume_existing", "task_scope_id": scope_id}),
        context=context, execution_lease=h.LEASE, run_fence=h.FENCE,
        raw_call_id=f"raw-route-{tag}", turn_ordinal=turn, call_ordinal=0,
    )
    assert execution.result.outcome.value == "succeeded", execution.result
    receipt_id = execution.result.value["context_route_receipt"]["receipt_id"]
    return await env.ledger.read_route_receipt(h.RUN.value, receipt_id)


async def _write_envelope(env, receipt, *, turn: int, tag: str):  # type: ignore[no-untyped-def]
    from simple_harness.execution.context_authority import TaskExecutionEnvelopeRequest

    policy = env.exposure.execution_policy(h.RUN, "write_file")
    return await env.task_authority.issue_envelope(
        TaskExecutionEnvelopeRequest(
            h.RUN, f"call-write-{tag}", f"effect-write-{tag}", f"raw-write-{tag}", turn, 1, "write_file", policy, receipt
        )
    )


# --- F-2：单快照 + 预留事务内再核 ----------------------------------------------


@pytest.mark.asyncio
async def test_gate_head_and_scope_status_read_in_single_snapshot(tmp_path: Path) -> None:
    """verify 与 head 读之间注入 Manual append（另一线程、另一连接）：

    ① 门的裁决按同一读快照计算（append 只能在快照释放后提交，裁决为放行）；
    ② 物理 dispatch 前的预留事务（BEGIN IMMEDIATE）再核 head → ``workspace_binding_receipt_superseded``，
       零预留、零写入，并进入 sticky memo。"""

    env, scope_a, root_a, envelope = await _routed_write(tmp_path)
    root_2 = env.workspace_base / "root-a2"
    root_2.mkdir(parents=True)
    worker: dict[str, threading.Thread] = {}
    points: list[str] = []

    def hook(point: str) -> None:
        points.append(point)
        if point == "effect-gate-after-verify":
            # Inside the gate's read snapshot: the append commits only after the
            # snapshot closes (rollback-journal: COMMIT waits for SHARED readers).
            thread = threading.Thread(
                target=lambda: asyncio.run(
                    h.bind_scope_root(env.db_path, scope_a, root_2, base_revision=1, tag="r2")
                )
            )
            thread.start()
            worker["append"] = thread
        elif point == "effect-gate-snapshot-closed":
            worker["append"].join(timeout=30)
            assert not worker["append"].is_alive()

    env.gate._fault_inject = hook
    execution = await _execute(env, envelope)
    assert points == ["effect-gate-after-verify", "effect-gate-snapshot-closed"]
    head = await env.binding_store.current_receipt(scope_a)
    assert head.binding_set_revision == 2 and envelope.binding_set_revision == 1
    # ② 预留事务再核：拒绝、零预留、零写入。
    assert execution.effect is None
    assert execution.result.outcome.value == "rejected"
    assert execution.result.error_code == "workspace_binding_receipt_superseded"
    assert not (root_a / "z.txt").exists()
    assert env.effects.calls.count("write_file") == 1  # 只有 _routed_write 的那次
    assert h.rows(
        env.db_path,
        "SELECT status FROM harness_evidence_reservations WHERE source_event_id=?",
        f"effect:{envelope.effect_id.value}",
    ) == [("ingested",)]  # 首次写的预留；本次被拒未新增
    assert _memo_rows(env) == [(h.RUN.value, envelope.route_receipt_id, "workspace_binding_receipt_superseded")]
    # ① 直接看门：同 receipt 现在是 sticky（快照裁决曾放行，是写锁再核抓住的）。
    env.gate._fault_inject = None
    assert _code(await env.gate.verify(_context(envelope), "write_file")) == EFFECT_GATE_STICKY_REASON


@pytest.mark.asyncio
async def test_reservation_check_raises_inside_reservation_transaction(tmp_path: Path) -> None:
    """``reservation_check`` 只对 PROJECT_EFFECT 返回钩子；head 不变放行、变了在预留事务内抛
    ``EffectGateRejected``（同一 BEGIN IMMEDIATE 内回滚）。"""

    env, scope_a, _root, envelope = await _routed_write(tmp_path)
    bare = ToolContext(h.RUN, h.REQUEST, CancellationToken(), call_id=CallId("call-x"), effect_id=EffectId("e-x"))
    assert env.gate.reservation_check(bare, "read_file") is None
    check = env.gate.reservation_check(_context(envelope), "write_file")
    assert check is not None
    async with env.gate._scope_store._connection() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            await check(db)  # head == receipt → 放行
        finally:
            await db.rollback()
    root_2 = env.workspace_base / "root-a2"
    root_2.mkdir(parents=True)
    await h.bind_scope_root(env.db_path, scope_a, root_2, base_revision=1, tag="r2")
    async with env.gate._scope_store._connection() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(EffectGateRejected) as rejected:
                await check(db)
        finally:
            await db.rollback()
    assert rejected.value.result.error_code == "workspace_binding_receipt_superseded"
    assert rejected.value.result.call_id == envelope.call_id


# --- F-3：sticky memo，直到新 route receipt -----------------------------------


@pytest.mark.asyncio
async def test_gate_rejection_sticky_until_new_route_receipt(tmp_path: Path) -> None:
    """可逆条件（根临时改名）首拒 ``workspace_root_unavailable``；改回后同 receipt 仍拒
    ``effect_gate_route_receipt_rejected``（durable memo，首码保留）；同 Run 新 ``context_route`` 收据
    → 新 envelope 放行。"""

    env, scope_a, root_a, envelope = await _routed_write(tmp_path)
    moved = root_a.parent / "moved-a"
    root_a.rename(moved)
    assert _code(await env.gate.verify(_context(envelope), "write_file")) == "workspace_root_unavailable"
    moved.rename(root_a)
    # 条件恢复，同 receipt：sticky。
    assert _code(await env.gate.verify(_context(envelope), "write_file")) == EFFECT_GATE_STICKY_REASON
    execution = await _execute(env, envelope)
    assert execution.effect is None and execution.result.error_code == EFFECT_GATE_STICKY_REASON
    assert not (root_a / "z.txt").exists()
    # memo 只记首码，重复拒绝不改写（append-only + UNIQUE）。
    assert _memo_rows(env) == [(h.RUN.value, envelope.route_receipt_id, "workspace_root_unavailable")]
    # 同 Run 新 route receipt → 新 envelope 不受旧 memo 影响。
    receipt = await _new_route_receipt(env, scope_a, turn=3, tag="again")
    assert receipt.receipt_id != envelope.route_receipt_id
    fresh = await _write_envelope(env, receipt, turn=3, tag="again")
    assert fresh.route_receipt_id == receipt.receipt_id
    assert await env.gate.verify(_context(fresh), "write_file") is None
    execution = await env.effects.execute(
        effect_id=fresh.effect_id, call=_write_call(fresh, "fresh.txt"), context=_context(fresh),
        execution_lease=h.LEASE, run_fence=h.FENCE, raw_call_id="raw-write-again", turn_ordinal=3, call_ordinal=1,
    )
    assert execution.result.outcome.value == "succeeded"
    assert (root_a / "fresh.txt").read_text(encoding="utf-8") == "zeta"
    # 旧 receipt 依旧 sticky（memo 按 receipt 键，不是按 Run 清空）。
    assert _code(await env.gate.verify(_context(envelope), "write_file")) == EFFECT_GATE_STICKY_REASON


@pytest.mark.asyncio
async def test_step_one_rejections_are_not_sticky(tmp_path: Path) -> None:
    """步骤 1（envelope 缺失/身份回声）没有可信 receipt → 不写 memo；同 receipt 的后续合法调用照常放行。"""

    env, _scope, _root, envelope = await _routed_write(tmp_path)
    assert _code(await env.gate.verify(_context(envelope), "edit_file")) == "effect_gate_envelope_identity_mismatch"
    assert _memo_rows(env) == []
    assert await env.gate.verify(_context(envelope), "write_file") is None


# --- F-7 / F-8 ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_missing_head_is_authority_missing_not_superseded(tmp_path: Path) -> None:
    env, _scope, _root, envelope = await _routed_write(tmp_path)

    class _NoHeadStore:
        def __init__(self, inner) -> None:  # type: ignore[no-untyped-def]
            self._inner = inner

        async def verify_task_execution_envelope(self, envelope, receipt, *, db=None):  # type: ignore[no-untyped-def]
            return await self._inner.verify_task_execution_envelope(envelope, receipt, db=db)

        async def current_receipt(self, task_scope_id, *, db=None):  # type: ignore[no-untyped-def]
            raise TaskScopeNotFound("workspace_binding_set_not_found")

    env.gate._binding_store = _NoHeadStore(env.binding_store)
    assert (
        _code(await env.gate.verify(_context(envelope), "write_file"))
        == "workspace_binding_effect_authority_missing"
    )


@pytest.mark.asyncio
async def test_gate_requires_effect_id_echo(tmp_path: Path) -> None:
    env, _scope, _root, envelope = await _routed_write(tmp_path)
    no_effect = ToolContext(
        h.RUN, h.REQUEST, CancellationToken(), call_id=envelope.call_id, effect_id=None,
        task_execution_envelope=envelope,
    )
    assert _code(await env.gate.verify(no_effect, "write_file")) == "effect_gate_envelope_identity_mismatch"
    # 非 None 但不同的 effect_id 由 SDK ToolContext 契约本身拒绝（Host 是最后一道，不是唯一一道）。
    with pytest.raises(ValueError, match="TaskExecutionEnvelope effect differs"):
        ToolContext(
            h.RUN, h.REQUEST, CancellationToken(), call_id=envelope.call_id, effect_id=EffectId("effect-other"),
            task_execution_envelope=envelope,
        )
    assert _memo_rows(env) == []


# --- F-10：inode 漂移 / symlink / 跨 Run / 直通 / memo 隔离 --------------------


@pytest.mark.asyncio
async def test_gate_rejects_inode_drift_and_symlink_root(tmp_path: Path) -> None:
    env, _scope, root_a, envelope = await _routed_write(tmp_path)
    before = os.stat(root_a).st_ino
    # 删目录再同名重建：dev/ino 变化 → identity drift。
    for child in root_a.iterdir():
        child.unlink()
    root_a.rmdir()
    root_a.mkdir()
    assert os.stat(root_a).st_ino != before
    assert _code(await env.gate.verify(_context(envelope), "write_file")) == "workspace_root_identity_drift"
    # symlink 根：路径解析不等于自身 → not canonical（另一 env，避免 sticky）。
    env2, _scope2, root_b, envelope2 = await _routed_write(tmp_path / "two")
    target = root_b.parent / "real-b"
    root_b.rename(target)
    root_b.symlink_to(target, target_is_directory=True)
    assert _code(await env2.gate.verify(_context(envelope2), "write_file")) == "workspace_root_not_canonical"


@pytest.mark.asyncio
async def test_gate_rejects_cross_run_envelope(tmp_path: Path) -> None:
    """跨 Run envelope：SDK ``ToolContext`` 契约先拒（两种形状），Host 步骤 1 为最后一道（同码）。"""

    env, _scope, _root, envelope = await _routed_write(tmp_path)
    with pytest.raises(ValueError, match="belongs to another Run"):
        ToolContext(
            RunId("run-other"), h.REQUEST, CancellationToken(), call_id=envelope.call_id,
            effect_id=envelope.effect_id, task_execution_envelope=envelope,
        )
    foreign = replace(envelope, run_id=RunId("run-other"))
    with pytest.raises(ValueError, match="belongs to another Run"):
        _context(foreign)
    # Host 最后一道：绕过 SDK 构造校验的上下文（属性级伪造）仍被步骤 1 拒绝。
    forged = _context(envelope)
    object.__setattr__(forged, "run_id", RunId("run-other"))
    assert _code(await env.gate.verify(forged, "write_file")) == "effect_gate_envelope_identity_mismatch"
    assert _memo_rows(env) == []


def test_snapshot_passthrough_when_exposure_lacks_execution_policy() -> None:
    from simple_harness.execution.context_authority import ContextRouteState
    from simple_harness.providers import ProviderToolSpec

    from deskpet.sdk_adapters.context_authority import _visible_provider_specs

    specs = (
        ProviderToolSpec("write_file", "w", {"type": "object"}),
        ProviderToolSpec("read_file", "r", {"type": "object"}),
    )
    bare = SimpleNamespace(provider_specs=lambda run_id: specs)
    assert _visible_provider_specs(bare, h.RUN, ContextRouteState.UNROUTED) == specs
    # 有 execution_policy 的 exposure：ROUTED_TASK 之前隐藏 PROJECT_EFFECT。
    exposure = h.RouteExposure()
    unrouted = [s.name for s in _visible_provider_specs(exposure, h.RUN, ContextRouteState.UNROUTED)]
    assert "write_file" not in unrouted and "read_file" in unrouted
    routed = [s.name for s in _visible_provider_specs(exposure, h.RUN, ContextRouteState.ROUTED_TASK)]
    assert "write_file" in routed


def test_run_fault_memo_isolated_per_run() -> None:
    memo = RunFaultMemo()
    memo.record("run-a", "sdk_task_execution_route_authority_missing")
    assert memo.read("run-b") is None
    memo.record("run-b", "catalog_execution_policy_unavailable")
    assert memo.read("run-a") == "sdk_task_execution_route_authority_missing"
    memo.release("run-a")
    assert memo.read("run-a") is None and memo.read("run-b") == "catalog_execution_policy_unavailable"


def test_effect_gate_constructor_rejects_missing_pieces(tmp_path: Path) -> None:
    from deskpet.task_scope.store import CanonicalTaskScopeStore

    good = {
        "binding_store": object(),
        "route_ledger": object(),
        "scope_store": CanonicalTaskScopeStore(tmp_path / "s.db"),
        "authority_resolver": lambda run_id: None,
        "exposure_resolver": lambda run_id: None,
    }
    EffectGate(**good)
    for name in good:
        with pytest.raises(TypeError, match=f"sdk_effect_gate_composition_missing:{name}"):
            EffectGate(**{**good, name: None})
    with pytest.raises(TypeError, match="scope_store"):
        EffectGate(**{**good, "scope_store": object()})


# --- Task 2 审查 F-7：SDK 拒绝后的预留立即 rejected tombstone ---------------------


@pytest.mark.asyncio
async def test_rejected_effect_reservation_abandoned_immediately(tmp_path: Path) -> None:
    """foreground 绑定的 Run：预留之后 SDK 授权 DENY（effect=None，零 dispatch）→ 预留行立即
    ``abandoned``、tombstone ``status=rejected``（trivial，不脏），不留给 terminal 排空；
    正常成功的 effect 仍 ingested。真实 SDK executor + 真实 ingress。"""

    from simple_harness.tools.authorization import (
        AuthorizationDecision,
        AuthorizationResult,
    )

    from deskpet.execution.semantic_closure import dirty_state, is_material_event
    from deskpet.task_scope.store import CanonicalTaskScopeStore
    from tests.sdk_adapters.test_effect_gate_replay import _bound_case

    ch, env, case = await _bound_case(tmp_path)
    try:
        ok = await case.execute(effect_id="effect-ok", path="ok.txt")
        assert ok.effect is not None and ok.result.outcome.value == "succeeded"

        class _Deny:
            async def prepare(self, prepared):  # type: ignore[no-untyped-def]
                return AuthorizationResult(AuthorizationDecision.DENY, reason_code="product_policy:deny")

        case.executor._authorization = _Deny()
        denied = await case.execute(effect_id="effect-denied", path="denied.txt")
        assert denied.effect is None and denied.result.outcome.value == "rejected"
        assert case.handler_calls == 1
        assert not (tmp_path / "case" / "denied.txt").exists()
        rows = h.rows(
            env.db_path,
            "SELECT r.source_event_id,r.status,e.payload_json FROM harness_evidence_reservations r "
            "LEFT JOIN task_scope_execution_ingest_receipts i ON i.source_event_id=r.source_event_id "
            "LEFT JOIN task_scope_events e ON e.event_id=i.event_id WHERE r.run_id=? ORDER BY r.source_sequence",
            env.run_id,
        )
        by_source = {s: (status, json.loads(payload)["public_payload"]) for s, status, payload in rows}
        assert by_source["effect:effect-ok"][0] == "ingested"
        status, public = by_source["effect:effect-denied"]
        assert status == "abandoned"
        assert public["status"] == "rejected" and public["tool_name"] == "write_file"
        assert public["effect_class"] == "project_effect"
        assert public["reason_code"] == denied.result.error_code
        assert is_material_event("harness.tool_invocation", {"public_payload": public}) is False
        # 无 reserved 残留；脏只来自 effect-ok；terminal 无需排空即可放行。
        assert h.rows(env.db_path, "SELECT COUNT(*) FROM harness_evidence_reservations WHERE run_id=? AND status='reserved'", env.run_id) == [(0,)]
        dirty = await dirty_state(CanonicalTaskScopeStore(env.db_path), ch.SCOPE)
        assert [e.source_event_id for e in dirty.material_events] == ["execution:effect:effect-ok"] or sorted(
            e.event_kind for e in dirty.material_events
        ) == ["harness.tool_invocation", "host.file"]
        observed = await ch.observe_terminal(env, ch.FakeRunFacts(env.run_id))
        assert observed is not None
        assert (await env.ingress.authorize_terminal(env.run_id)).terminal_source_sequence == 3
    finally:
        case.close()


# --- Task 3 审查 F-8：排空对确定性拒绝降级 ----------------------------------------


@pytest.mark.asyncio
async def test_drain_degrades_on_objective_evidence_hash_conflict(tmp_path: Path) -> None:
    """可读事实被 ``commit_fact`` 确定性拒绝（私有载荷 / objective hash 冲突）→ 不再抛出让 Run 永不终态：
    tombstone ``rejected_fact``（PROJECT_EFFECT 仍 material）、行 abandoned、run_terminal 照常放行。"""

    from deskpet.execution.evidence_ingress import (
        ObjectiveEventSpec,
        ToolInvocationFact,
    )
    from deskpet.execution.semantic_closure import dirty_state, is_material_event
    from deskpet.memory.human_memory_program import HumanMemoryProgramStore
    from deskpet.memory.human_memory_service import build_host_typed_evidence
    from deskpet.task_scope.store import CanonicalTaskScopeStore
    from tests.sdk_adapters import s5b_closure_harness as ch

    env = await ch.bound_run(tmp_path, "sdk-run-drain")
    # ① 私有载荷：objective payload 命中凭据形状（手工构造，绕过 classify 的脱敏）。
    await env.ingress.reserve(run_id=env.run_id, task_scope_id=ch.SCOPE, kind="tool_invocation", source_event_id="effect:e-private", tool_name="write_file")
    private = ToolInvocationFact(
        run_id=env.run_id, effect_id="e-private", call_id="call:e-private", tool_name="write_file",
        effect_state="succeeded", outcome="succeeded", error_code=None,
        objective=ObjectiveEventSpec("host.file", {"tool_name": "write_file", "targets": ["bearer abcdefghijklmnop"]}),
    )
    # ② objective hash 冲突：同 idempotency key 已有不同载荷的 evidence 行。
    await env.ingress.reserve(run_id=env.run_id, task_scope_id=ch.SCOPE, kind="tool_invocation", source_event_id="effect:e-conflict", tool_name="write_file")
    program = HumanMemoryProgramStore(env.db_path)
    primary = await program.initialize_subject(ch.SUBJECT)
    envelope, receipt = build_host_typed_evidence(
        subject=ch.SUBJECT, authority_ref="host:objective-event-recorder:v1",
        payload={"tool_name": "write_file", "targets": ["first.txt"]},
        idempotency_key="objective:effect:e-conflict", source_ref="host-objective:effect:e-conflict", run_id=env.run_id,
    )
    store = CanonicalTaskScopeStore(env.db_path)
    async with store._connection() as db:
        await db.execute("BEGIN IMMEDIATE")
        await program.append_evidence_tx(db, envelope, receipt, primary_conversation_id=primary.primary_conversation_id, committed_at=1.0)
        await db.commit()
    conflict = ch.write_fact(env.run_id, "e-conflict", path="second.txt")
    facts = ch.FakeRunFacts(env.run_id, facts={"effect:e-private": private, "effect:e-conflict": conflict})

    report = await env.ingress.drain_reservations(env.run_id, fact_reader=facts)
    assert report.ingested == () and set(report.abandoned) == {"effect:e-private", "effect:e-conflict"}
    rows = h.rows(
        env.db_path,
        "SELECT r.source_event_id,r.status,e.payload_json FROM harness_evidence_reservations r "
        "JOIN task_scope_execution_ingest_receipts i ON i.source_event_id=r.source_event_id "
        "JOIN task_scope_events e ON e.event_id=i.event_id WHERE r.run_id=? ORDER BY r.source_sequence",
        env.run_id,
    )
    assert [(s, st) for s, st, _p in rows] == [("effect:e-private", "abandoned"), ("effect:e-conflict", "abandoned")]
    publics = {s: json.loads(p)["public_payload"] for s, _st, p in rows}
    assert publics["effect:e-private"]["status"] == "rejected_fact"
    assert publics["effect:e-private"]["reason_code"].startswith("credential_value_rejected")
    assert publics["effect:e-conflict"]["status"] == "rejected_fact"
    assert publics["effect:e-conflict"]["reason_code"] == "objective_evidence_hash_conflict"
    for public in publics.values():
        assert public["effect_class"] == "project_effect"
        assert is_material_event("harness.tool_invocation", {"public_payload": public}) is True
    dirty = await dirty_state(store, ch.SCOPE)
    assert [e.source_event_id for e in dirty.material_events] == ["execution:effect:e-private", "execution:effect:e-conflict"]
    # 排空幂等 + terminal 收敛。
    again = await env.ingress.drain_reservations(env.run_id, fact_reader=facts)
    assert again.ingested == () and again.abandoned == ()
    observed = await ch.observe_terminal(env, facts)
    assert observed is not None
    gate = await env.ingress.authorize_terminal(env.run_id)
    assert gate.terminal_source_sequence == 3
