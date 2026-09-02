# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b Task 3：``task_scope_update`` handler 拒绝码矩阵（design-freeze §7）。

真实 state.db + 真实 S5a route ledger + 真实 ``CanonicalTaskScopeStore``；route 由真 ReActLoop
（``s5b_effect_gate_harness``）先建立，随后直接调用 handler 逐条验证：
``task_scope_update_scope_unbound`` / ``task_scope_update_nothing_to_close``（不递增 revision）/
``task_scope_update_refs_outside_scope`` / ``task_scope_update_illegal_transition`` /
``task_scope_update_after_complete`` / ``task_scope_update_payload_invalid`` /
``mutation_base_revision_conflict``（原样透传、可重试）/ 重复 plan 同 receipt；
非法载荷在拒绝点写 ``host_pre_admission_audit(payload_kind='task_scope_update')``。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from simple_harness import CallId, EffectId
from simple_harness.tools import ToolContext, ToolResult
from simple_harness.tools.contracts import CancellationToken

from deskpet.execution.evidence_ingress import ObjectiveEventSpec, ToolInvocationFact
from deskpet.execution.semantic_closure import dirty_state
from deskpet.sdk_adapters.task_scope_mutation import (
    TASK_SCOPE_UPDATE_SCHEMA,
    derive_plan_id,
)
from deskpet.task_scope.store import CanonicalTaskScopeStore
from tests.sdk_adapters import s5b_effect_gate_harness as h


def _context(call: str) -> ToolContext:
    return ToolContext(
        h.RUN, h.REQUEST, CancellationToken(), call_id=CallId(f"call:{call}"), effect_id=EffectId(f"effect:{call}"),
    )


async def _routed_env(tmp_path: Path):  # type: ignore[no-untyped-def]
    env = await h.build_env(tmp_path)
    scope_a, root_a = await h.make_bound_scope(env, "a", "root-a")
    h.freeze_run(env, task_scope_id=scope_a, workspace_root=root_a)
    provider = h.ScriptedProvider(
        [
            h.tool_call("context_route", {"route": "resume_existing", "task_scope_id": scope_a}, raw_id="raw-route"),
            h.tool_call("write_file", {"path": "a.txt", "content": "alpha"}, raw_id="raw-write"),
            h.answer("done"),
        ]
    )
    out = await h.run_capture(env, provider)
    assert out["exception"] is None
    return env, scope_a


def _refs(env, scope: str) -> list[str]:  # type: ignore[no-untyped-def]
    return [
        str(r[0])
        for r in h.rows(env.db_path, "SELECT DISTINCT evidence_id FROM task_scope_evidence_links WHERE task_scope_id=?", scope)
    ]


def _revision(env, scope: str) -> int:  # type: ignore[no-untyped-def]
    [(revision,)] = h.rows(env.db_path, "SELECT current_revision FROM task_scope_heads WHERE task_scope_id=?", scope)
    return int(revision)


def _arguments(env, scope: str, key: str, *, outcome: str = "mutate", kind: str = "plan.step.add",  # type: ignore[no-untyped-def]
               refs: list[str] | None = None, base_revision: int | None = None, **extra: object) -> dict:
    refs = _refs(env, scope) if refs is None else refs
    arguments: dict = {
        "outcome": outcome,
        "base_revision": _revision(env, scope) if base_revision is None else base_revision,
        "evidence_refs": refs,
        "idempotency_key": key,
        **extra,
    }
    if outcome == "mutate":
        arguments["operations"] = [
            {"operation_id": f"op-{key}", "kind": kind, "value": f"值 {key}", "reason_code": "test", "evidence_refs": refs},
        ]
    else:
        arguments.setdefault("closure_reason", "model_no_change")
    return arguments


async def _dirty(env, scope: str, effect_id: str) -> None:  # type: ignore[no-untyped-def]
    """Host-side material event (write_file settled) so the scope has something to close."""

    await env.evidence_ingress.commit_fact(
        task_scope_id=scope, subject=h.AUTH.subject,
        fact=ToolInvocationFact(
            run_id=h.RUN.value, effect_id=effect_id, call_id=f"call:{effect_id}", tool_name="write_file",
            effect_state="succeeded", outcome="succeeded", error_code=None,
            objective=ObjectiveEventSpec("host.file", {"tool_name": "write_file", "effect_id": effect_id,
                                                        "call_id": f"call:{effect_id}", "outcome": "succeeded",
                                                        "error_code": None, "targets": ["x.txt"]}),
        ),
    )


async def _call(env, call: str, arguments: dict):  # type: ignore[no-untyped-def]
    token = h.tool_context_var.set(_context(call))
    try:
        return await env.closure_service.handle_task_scope_update(arguments)
    finally:
        h.tool_context_var.reset(token)


def _audits(env) -> list[tuple]:  # type: ignore[no-untyped-def]
    return h.rows(env.db_path, "SELECT sdk_run_id,payload_kind,reason_code FROM host_pre_admission_audit ORDER BY created_at,audit_id")


def test_schema_is_strict_and_model_never_fills_host_fields() -> None:
    schema = TASK_SCOPE_UPDATE_SCHEMA
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"outcome", "base_revision", "evidence_refs", "idempotency_key"}
    assert set(schema["properties"]) == {"outcome", "base_revision", "operations", "closure_reason", "evidence_refs", "idempotency_key"}
    for host_field in ("plan_id", "run_id", "subject", "task_scope_id", "source_turn_id", "disclosure_context"):
        assert host_field not in schema["properties"]
    operation = schema["properties"]["operations"]["items"]
    assert operation["additionalProperties"] is False
    assert set(operation["required"]) == {"operation_id", "kind", "value", "reason_code", "evidence_refs"}
    assert "task.complete" in operation["properties"]["kind"]["enum"] and "plan.step.add" in operation["properties"]["kind"]["enum"]


@pytest.mark.asyncio
async def test_handler_rejection_codes_and_audit_rows(tmp_path: Path) -> None:
    env, scope = await _routed_env(tmp_path)
    store = CanonicalTaskScopeStore(env.db_path)
    assert (await dirty_state(store, scope)).is_dirty
    revision = _revision(env, scope)

    # payload invalid（未知 kind）→ 拒绝 + audit；revision 不变。
    bad = _arguments(env, scope, "bad-kind", kind="status.update")
    result = await _call(env, "bad-kind", bad)
    assert isinstance(result, ToolResult) and result.outcome.value == "rejected"
    assert result.error_code == "task_scope_update_payload_invalid"
    assert _revision(env, scope) == revision

    # refs ∉ scope。
    outside = await _call(env, "outside", _arguments(env, scope, "outside", refs=["not-in-scope"]))
    assert outside.outcome.value == "rejected" and outside.error_code == "task_scope_update_refs_outside_scope"

    # CAS 冲突：原样透传、可重试（failed + retryable），不产生 receipt。
    stale = await _call(env, "stale", _arguments(env, scope, "stale", base_revision=revision + 7))
    assert stale.outcome.value == "failed" and stale.error_code == "mutation_base_revision_conflict" and stale.retryable
    assert h.rows(env.db_path, "SELECT COUNT(*) FROM task_scope_closure_receipts WHERE task_scope_id=?", scope) == [(0,)]

    # 合法 mutate（task.pause）→ 收口，receipt 与 apply 同事务。
    ok = await _call(env, "pause", _arguments(env, scope, "pause", kind="task.pause"))
    assert isinstance(ok, dict) and ok["ok"] is True
    assert ok["closure_receipt"]["outcome"] == "mutate" and ok["closure_receipt"]["plan_id"] == derive_plan_id("pause", scope)
    assert ok["committed_revision"] == revision + 1
    assert not (await dirty_state(store, scope)).is_dirty
    assert await store.read_head_status(scope) == "paused"

    # 重复 plan（同 idempotency_key、同载荷）→ 同 receipt，无第二行。
    replay = await _call(env, "pause-replay", _arguments(env, scope, "pause", kind="task.pause", base_revision=revision))
    assert isinstance(replay, dict) and replay["ok"] is True and replay["replayed"] is True
    assert replay["closure_receipt"]["receipt_id"] == ok["closure_receipt"]["receipt_id"]
    assert h.rows(env.db_path, "SELECT COUNT(*) FROM task_scope_closure_receipts WHERE task_scope_id=?", scope) == [(1,)]

    # 无脏无 pending → nothing_to_close（不递增 revision）。
    nothing = await _call(env, "nothing", _arguments(env, scope, "nothing"))
    assert nothing.outcome.value == "rejected" and nothing.error_code == "task_scope_update_nothing_to_close"
    assert _revision(env, scope) == revision + 1

    # 非法迁移：paused --task.pause--> 拒绝；paused --task.resume--> active 合法。
    await _dirty(env, scope, "e-2")
    illegal = await _call(env, "illegal", _arguments(env, scope, "illegal", kind="task.pause"))
    assert illegal.outcome.value == "rejected" and illegal.error_code == "task_scope_update_illegal_transition"
    resumed = await _call(env, "resume", _arguments(env, scope, "resume", kind="task.resume"))
    assert isinstance(resumed, dict) and resumed["ok"] is True
    assert await store.read_head_status(scope) == "active"

    # complete 之后：plan.* / 状态迁移一律 after_complete；no_mutation（pending→closed）任何状态合法。
    await _dirty(env, scope, "e-3")
    done = await _call(env, "complete", _arguments(env, scope, "complete", kind="task.complete"))
    assert isinstance(done, dict) and done["ok"] is True
    await _dirty(env, scope, "e-4")
    after = await _call(env, "after", _arguments(env, scope, "after", kind="plan.step.add"))
    assert after.outcome.value == "rejected" and after.error_code == "task_scope_update_after_complete"
    closed = await _call(env, "closed", _arguments(env, scope, "closed", outcome="no_mutation"))
    assert isinstance(closed, dict) and closed["ok"] is True and closed["closure_receipt"]["outcome"] == "no_mutation"
    assert not (await dirty_state(store, scope)).is_dirty

    assert _audits(env) == [
        (h.RUN.value, "task_scope_update", "task_scope_update_payload_invalid"),
        (h.RUN.value, "task_scope_update", "task_scope_update_refs_outside_scope"),
        (h.RUN.value, "task_scope_update", "mutation_base_revision_conflict"),
        (h.RUN.value, "task_scope_update", "task_scope_update_nothing_to_close"),
        (h.RUN.value, "task_scope_update", "task_scope_update_illegal_transition"),
        (h.RUN.value, "task_scope_update", "task_scope_update_after_complete"),
    ]


@pytest.mark.asyncio
async def test_standalone_route_is_stably_rejected_scope_unbound(tmp_path: Path) -> None:
    """direct_standalone 路由下调用 task_scope_update → rejected(task_scope_update_scope_unbound)，
    零写入、写 audit；模型可见（不是整 Run 故障）。"""
    env = await h.build_env(tmp_path)
    scope_a, root_a = await h.make_bound_scope(env, "a", "root-a")
    h.freeze_run(env, task_scope_id=scope_a, workspace_root=root_a)
    provider = h.ScriptedProvider(
        [
            h.tool_call("context_route", {"route": "direct_standalone"}, raw_id="raw-route"),
            h.tool_call(
                "task_scope_update",
                {"outcome": "no_mutation", "base_revision": 1, "evidence_refs": ["x"], "idempotency_key": "k",
                 "closure_reason": "nothing"},
                raw_id="raw-closure",
            ),
            h.answer("done"),
        ]
    )
    out = await h.run_capture(env, provider)
    assert out["exception"] is None
    [message] = h.tool_messages(env, "task_scope_update")
    payload = json.loads(message)
    assert payload["outcome"] == "rejected" and payload["error_code"] == "task_scope_update_scope_unbound"
    assert h.rows(env.db_path, "SELECT COUNT(*) FROM task_scope_closure_receipts") == [(0,)]
    assert h.rows(env.db_path, "SELECT COUNT(*) FROM task_scope_mutation_attempts") == [(0,)]
    assert _audits(env) == [(h.RUN.value, "task_scope_update", "task_scope_update_scope_unbound")]


@pytest.mark.asyncio
async def test_replayed_task_scope_update_returns_original_receipt_and_does_not_clear_newer_dirty(tmp_path: Path) -> None:
    """Task 3 审查 F-1（P1）：已应用 plan 的幂等重放必须返回**首次**写入的 receipt（按 plan_id 查
    ``task_scope_closure_receipts``），不得以当前 head 水位再写一条——重放前追加的 host.file 事件仍脏，
    终态门仍 pending；首次 receipt 的 watermark 与 apply 同事务、只覆盖 ≤ 该水位的事件。"""

    from deskpet.execution.semantic_closure import closure_coverage_tx

    env, scope = await _routed_env(tmp_path)
    store = CanonicalTaskScopeStore(env.db_path)
    revision = _revision(env, scope)
    arguments = _arguments(env, scope, "k1", kind="task.pause")
    first = await _call(env, "k1", arguments)
    assert isinstance(first, dict) and first["ok"] is True
    receipt = first["closure_receipt"]
    [(decision_watermark,)] = h.rows(
        env.db_path,
        "SELECT r.event_watermark FROM task_scope_canonical_revisions r JOIN task_scope_mutation_decisions d "
        "ON d.decision_id=r.decision_id WHERE d.plan_id=?",
        receipt["plan_id"],
    )
    assert receipt["closure_watermark"] == decision_watermark
    assert not (await dirty_state(store, scope)).is_dirty

    # 重放前又落了一个 material 事件（同 turn 内 write_file b）。
    await _dirty(env, scope, "e-2")
    assert (await dirty_state(store, scope)).is_dirty
    replay = await _call(env, "k1-replay", arguments)  # 同一载荷（同 plan hash）
    assert isinstance(replay, dict) and replay["replayed"] is True, replay
    assert replay["closure_receipt"] == receipt
    assert h.rows(env.db_path, "SELECT COUNT(*) FROM task_scope_closure_receipts WHERE task_scope_id=?", scope) == [(1,)]
    assert (await dirty_state(store, scope)).is_dirty
    async with store._connection() as db:
        coverage = await closure_coverage_tx(db, task_scope_id=scope, sdk_run_id=h.RUN.value)
    assert not coverage.satisfied
    # 第二次重放同样返回首条 receipt。
    replay2 = await _call(env, "k1-replay-2", arguments)
    assert replay2["closure_receipt"] == receipt
    del revision
    assert h.rows(env.db_path, "SELECT COUNT(*) FROM task_scope_closure_receipts WHERE task_scope_id=?", scope) == [(1,)]


@pytest.mark.asyncio
async def test_task_scope_update_multibyte_key_rejected_with_audit(tmp_path: Path) -> None:
    """Task 6（Task 3 审查 F-5）：200 个 CJK 字符的 idempotency_key（600 字节 > 256 字节）→ 稳定码
    ``task_scope_update_payload_invalid`` + ``host_pre_admission_audit`` 行，而不是 ``tool_handler_failed``。"""

    env, scope = await _routed_env(tmp_path)
    revision = _revision(env, scope)
    before = len(_audits(env))
    result = await _call(env, "multibyte", _arguments(env, scope, "键" * 200))
    assert isinstance(result, ToolResult) and result.outcome.value == "rejected"
    assert result.error_code == "task_scope_update_payload_invalid"
    assert "idempotency_key" in str(result.public_message)
    audits = _audits(env)
    assert len(audits) == before + 1 and audits[-1][1:] == ("task_scope_update", "task_scope_update_payload_invalid")
    assert _revision(env, scope) == revision
    # 240 字节的多字节 key 合法（按字节计，不按字符计；派生 operation_id "op-…" 仍在 256 字节内）。
    ok_key = "键" * 80  # 240 bytes
    accepted = await _call(env, "multibyte-ok", _arguments(env, scope, ok_key, kind="task.pause"))
    assert isinstance(accepted, dict) and accepted["ok"] is True
