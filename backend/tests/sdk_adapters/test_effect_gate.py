# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b Task 1 单测：EffectGate reason code（design-freeze §4 的 1→3→4→5→6）、
BindingRootResolver 与 ProductEffectExecutor 前置门。

真 S4 binding store / v45 ledger / canonical TaskScope store；envelope 由真 ReActLoop 签发
（基座见 s5b_effect_gate_harness）。inode 漂移六类矩阵、sticky memo 与 confirm-only 留 Task 6。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from simple_harness import CallId, RequestId, RunId
from simple_harness.tools import ToolCall, ToolContext
from simple_harness.tools.contracts import CancellationToken
from simple_harness.tools.executor import EffectExecution

from deskpet.memory.human_memory_service import MutateTaskScopeRequest
from deskpet.sdk_adapters.effect_gate import (
    EFFECT_GATE_PUBLIC_MESSAGE,
    PROJECT_EFFECT_ACTIVE_SCOPE_STATUSES,
)
from deskpet.sdk_adapters.task_execution import (
    BindingRootResolver,
    ProductTaskExecutionAuthority,
    TaskExecutionAuthorityError,
)
from deskpet.task_scope.store import CanonicalTaskScopeStore
from tests.sdk_adapters import s5b_effect_gate_harness as h


async def _routed_write(tmp_path: Path):
    """Run one real ROUTED_TASK write so a genuine envelope + route receipt exist."""

    env = await h.build_env(tmp_path)
    scope_a, root_a = await h.make_bound_scope(env, "a", "root-a")
    h.freeze_run(env, task_scope_id=scope_a, workspace_root=root_a)
    provider = h.ScriptedProvider(
        [
            h.tool_call(
                "context_route",
                {"route": "resume_existing", "task_scope_id": scope_a},
                raw_id="raw-route",
            ),
            h.tool_call("write_file", {"path": "a.txt", "content": "alpha"}, raw_id="raw-write"),
            h.answer("done"),
        ]
    )
    out = await h.run_capture(env, provider)
    assert out["exception"] is None
    [record] = env.effects.write_file_calls
    return env, scope_a, root_a, record["envelope"]


def _context(envelope, *, call_id: CallId | None = None) -> ToolContext:
    return ToolContext(
        h.RUN,
        h.REQUEST,
        CancellationToken(),
        call_id=envelope.call_id if call_id is None else call_id,
        effect_id=envelope.effect_id,
        task_execution_envelope=envelope,
    )


def _code(result) -> str | None:
    if result is None:
        return None
    assert result.outcome.value == "rejected"
    assert result.public_message == EFFECT_GATE_PUBLIC_MESSAGE
    return result.error_code


# --- reason codes ------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_accepts_exact_envelope_and_ignores_non_project_tools(tmp_path) -> None:
    env, _scope, _root, envelope = await _routed_write(tmp_path)
    assert await env.gate.verify(_context(envelope), "write_file") is None
    # NON_PROJECT_EFFECT：不看 envelope（无 envelope 也放行）。
    bare = ToolContext(h.RUN, h.REQUEST, CancellationToken(), call_id=CallId("call-x"))
    assert await env.gate.verify(bare, "read_file") is None
    assert await env.gate.verify(bare, "task_scope_search") is None


@pytest.mark.asyncio
async def test_gate_envelope_missing_and_identity_mismatch(tmp_path) -> None:
    env, _scope, _root, envelope = await _routed_write(tmp_path)
    bare = ToolContext(h.RUN, h.REQUEST, CancellationToken(), call_id=CallId("call-x"))
    assert _code(await env.gate.verify(bare, "write_file")) == "effect_gate_envelope_missing"
    # 工具名回声不一致（envelope 签给 write_file，却用于另一个 PROJECT_EFFECT 调用）。
    assert (
        _code(await env.gate.verify(_context(envelope), "edit_file"))
        == "effect_gate_envelope_identity_mismatch"
    )
    # 缺 TaskScope 六元组（SDK 允许 NON_PROJECT envelope，但 PROJECT_EFFECT 必须齐全）。
    stripped = replace(
        envelope,
        task_scope_id=None, root_id=None, root_identity_hash=None,
        binding_set_revision=None, binding_set_receipt_id=None,
        binding_set_receipt_hash=None,
    )
    assert (
        _code(await env.gate.verify(_context(stripped), "write_file"))
        == "effect_gate_envelope_missing"
    )


@pytest.mark.asyncio
async def test_gate_frozen_authority_consistency_codes(tmp_path) -> None:
    """Task 6 起每次拒绝对 (Run, route receipt) sticky（§4 步骤 2），三种冻结口径各用一份新 route。"""

    # 冻结 scope ≠ envelope scope。
    env, scope_a, root_a, envelope = await _routed_write(tmp_path / "scope")
    h.freeze_run(env, task_scope_id="another-scope", workspace_root=root_a)
    assert (
        _code(await env.gate.verify(_context(envelope), "write_file"))
        == "effect_gate_frozen_scope_mismatch"
    )
    # 恢复冻结也不解封：同 receipt sticky，直到下一次 context_route。
    h.freeze_run(env, task_scope_id=scope_a, workspace_root=root_a)
    assert (
        _code(await env.gate.verify(_context(envelope), "write_file"))
        == "effect_gate_route_receipt_rejected"
    )
    # Run 冻结为 projectless（零/多 root 冻结口径）。
    env, scope_a, root_a, envelope = await _routed_write(tmp_path / "projectless")
    h.freeze_run(env, task_scope_id=scope_a, workspace_root=None)
    assert (
        _code(await env.gate.verify(_context(envelope), "write_file"))
        == "effect_gate_projectless_project_effect"
    )
    # 冻结写根 ≠ verify 出的 canonical root。
    env, scope_a, root_a, envelope = await _routed_write(tmp_path / "root")
    other = env.workspace_base / "root-other"
    other.mkdir(parents=True)
    h.freeze_run(env, task_scope_id=scope_a, workspace_root=other)
    assert (
        _code(await env.gate.verify(_context(envelope), "write_file"))
        == "effect_gate_frozen_root_mismatch"
    )
    # 未被拒过的 receipt 在一致冻结下放行。
    env, scope_a, root_a, envelope = await _routed_write(tmp_path / "ok")
    h.freeze_run(env, task_scope_id=scope_a, workspace_root=root_a)
    assert await env.gate.verify(_context(envelope), "write_file") is None


@pytest.mark.asyncio
async def test_gate_passes_s4_codes_through_unchanged(tmp_path) -> None:
    env, _scope, _root, envelope = await _routed_write(tmp_path)
    # route receipt 不在 v45 ledger → S4 route authority missing（memo 键是那个不存在的 receipt）。
    unknown = replace(envelope, route_receipt_id="not-a-receipt")
    assert (
        _code(await env.gate.verify(_context(unknown), "write_file"))
        == "workspace_binding_route_authority_missing"
    )
    assert await env.gate.verify(_context(envelope), "write_file") is None
    # 七元组血缘（receipt hash 被改）→ S4 lineage mismatch。
    tampered = replace(envelope, route_receipt_hash="f" * 64)
    assert (
        _code(await env.gate.verify(_context(tampered), "write_file"))
        == "workspace_binding_envelope_lineage_mismatch"
    )
    # root_id 不属于该 receipt → S4 root mismatch（新 route，避开上一条的 sticky memo）。
    env, _scope, _root, envelope = await _routed_write(tmp_path / "root")
    foreign_root = replace(envelope, root_id="root-foreign")
    assert (
        _code(await env.gate.verify(_context(foreign_root), "write_file"))
        == "workspace_binding_envelope_root_mismatch"
    )
    # 根被重命名（文件系统身份）→ S4 root unavailable（inode 漂移 / symlink 见 hardening）。
    env, _scope, _root, envelope = await _routed_write(tmp_path / "moved")
    moved = _root.parent / "moved-a"
    _root.rename(moved)
    assert (
        _code(await env.gate.verify(_context(envelope), "write_file"))
        == "workspace_root_unavailable"
    )


@pytest.mark.asyncio
async def test_gate_receipt_superseded_after_mid_run_append(tmp_path) -> None:
    env, scope_a, _root, envelope = await _routed_write(tmp_path)
    root_2 = env.workspace_base / "root-a2"
    root_2.mkdir(parents=True)
    await h.bind_scope_root(env.db_path, scope_a, root_2, base_revision=1, tag="r2")
    head = await env.binding_store.current_receipt(scope_a)
    assert head.binding_set_revision == 2 and envelope.binding_set_revision == 1
    # S4 exact receipt 仍接受（append 不扩大当前 Run），strict 口径由 Host 门拒绝。
    assert (
        _code(await env.gate.verify(_context(envelope), "write_file"))
        == "workspace_binding_receipt_superseded"
    )
    # Task 6：sticky 至下一 context_route（Manual/Auto 同一规则）。
    assert (
        _code(await env.gate.verify(_context(envelope), "write_file"))
        == "effect_gate_route_receipt_rejected"
    )


@pytest.mark.asyncio
async def test_gate_task_scope_not_active(tmp_path) -> None:
    env, scope_a, _root, envelope = await _routed_write(tmp_path)
    assert PROJECT_EFFECT_ACTIVE_SCOPE_STATUSES == frozenset({"active", "open"})
    await env.service.mutate_task_scope(
        MutateTaskScopeRequest(scope_a, "status", "complete", "complete-a")
    )
    assert await CanonicalTaskScopeStore(env.db_path).read_head_status(scope_a) == "complete"
    assert (
        _code(await env.gate.verify(_context(envelope), "write_file"))
        == "effect_gate_task_scope_not_active"
    )
    await env.service.mutate_task_scope(
        MutateTaskScopeRequest(scope_a, "status", "active", "resume-a")
    )
    # Task 6：scope 重新 active 也不解封同 receipt（sticky 至下一 context_route）。
    assert (
        _code(await env.gate.verify(_context(envelope), "write_file"))
        == "effect_gate_route_receipt_rejected"
    )


# --- executor front ----------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_rejection_returns_effect_none_without_dispatch(tmp_path) -> None:
    env, scope_a, root_a, envelope = await _routed_write(tmp_path)
    call = ToolCall(envelope.call_id, "write_file", {"path": "z.txt", "content": "zeta"})
    h.freeze_run(env, task_scope_id="another-scope", workspace_root=root_a)
    before = list(env.effects.calls)
    execution = await env.effects.execute(
        effect_id=envelope.effect_id,
        call=call,
        context=_context(envelope),
        execution_lease=h.LEASE,
        run_fence=h.FENCE,
        raw_call_id="raw-write",
        turn_ordinal=2,
        call_ordinal=0,
    )
    assert isinstance(execution, EffectExecution)
    assert execution.effect is None
    assert execution.result.outcome.value == "rejected"
    assert execution.result.error_code == "effect_gate_frozen_scope_mismatch"
    assert execution.result.call_id == envelope.call_id
    assert env.effects.calls == before
    assert not (root_a / "z.txt").exists()
    # Task 6：恢复冻结后同 receipt 仍 sticky（零写入）。
    h.freeze_run(env, task_scope_id=scope_a, workspace_root=root_a)
    execution = await env.effects.execute(
        effect_id=envelope.effect_id,
        call=call,
        context=_context(envelope),
        execution_lease=h.LEASE,
        run_fence=h.FENCE,
        raw_call_id="raw-write",
        turn_ordinal=2,
        call_ordinal=0,
    )
    assert execution.effect is None
    assert execution.result.error_code == "effect_gate_route_receipt_rejected"
    assert env.effects.calls == before
    assert not (root_a / "z.txt").exists()
    # 未被拒过的 route（新 env）：放行后才到物理层。
    env, scope_a, root_a, envelope = await _routed_write(tmp_path / "ok")
    call = ToolCall(envelope.call_id, "write_file", {"path": "z.txt", "content": "zeta"})
    before = list(env.effects.calls)
    execution = await env.effects.execute(
        effect_id=envelope.effect_id,
        call=call,
        context=_context(envelope),
        execution_lease=h.LEASE,
        run_fence=h.FENCE,
        raw_call_id="raw-write",
        turn_ordinal=2,
        call_ordinal=0,
    )
    assert execution.result.outcome.value == "succeeded"
    assert env.effects.calls == [*before, "write_file"]
    assert (root_a / "z.txt").read_text(encoding="utf-8") == "zeta"


# --- BindingRootResolver -----------------------------------------------------


def _receipt(scope_id: str, head) -> SimpleNamespace:
    return SimpleNamespace(
        task_scope_id=scope_id,
        binding_set_revision=head.binding_set_revision,
        binding_set_receipt_id=head.receipt_id,
        binding_set_receipt_hash=head.receipt_hash,
    )


@pytest.mark.asyncio
async def test_binding_root_resolver_exactly_one_root(tmp_path) -> None:
    env = await h.build_env(tmp_path)
    scope_a, _root = await h.make_bound_scope(env, "a", "root-a")
    head = await env.binding_store.current_receipt(scope_a)
    resolver = BindingRootResolver(env.binding_store)
    assert await resolver(_receipt(scope_a, head)) == (
        head.appended_root.root_id, head.root_identity_hashes[0]
    )


@pytest.mark.asyncio
async def test_binding_root_resolver_multi_root_is_ambiguous_run_fault(tmp_path) -> None:
    env = await h.build_env(tmp_path)
    scope_a, _root = await h.make_bound_scope(env, "a", "root-a")
    root_2 = env.workspace_base / "root-a2"
    root_2.mkdir(parents=True)
    await h.bind_scope_root(env.db_path, scope_a, root_2, base_revision=1, tag="r2")
    head = await env.binding_store.current_receipt(scope_a)
    assert len(head.root_identity_hashes) == 2
    resolver = BindingRootResolver(env.binding_store)
    with pytest.raises(TaskExecutionAuthorityError) as ambiguous:
        await resolver(_receipt(scope_a, head))
    assert ambiguous.value.code == "sdk_task_execution_root_authority_ambiguous"
    # 旧 revision 1 的 exact receipt 仍恰一 root：resolver 只看 exact receipt，不看 head。
    exact = await env.binding_store.exact_receipt(
        task_scope_id=scope_a,
        binding_set_revision=1,
        binding_set_receipt_id=head.parent_receipt_id,
        binding_set_receipt_hash=head.parent_receipt_hash,
    )
    assert await resolver(_receipt(scope_a, exact)) == (
        exact.appended_root.root_id, exact.root_identity_hashes[0]
    )


@pytest.mark.asyncio
async def test_binding_root_resolver_zero_root_and_stale_receipt(tmp_path) -> None:
    class _ZeroRootStore:
        async def exact_receipt(self, **kwargs):
            return SimpleNamespace(root_identity_hashes=(), appended_root=None)

    resolver = BindingRootResolver(_ZeroRootStore())  # type: ignore[arg-type]
    with pytest.raises(TaskExecutionAuthorityError) as missing:
        await resolver(SimpleNamespace(
            task_scope_id="s", binding_set_revision=1,
            binding_set_receipt_id="r", binding_set_receipt_hash="a" * 64,
        ))
    assert missing.value.code == "sdk_task_execution_root_authority_missing"

    env = await h.build_env(tmp_path)
    scope_a, _root = await h.make_bound_scope(env, "a", "root-a")
    head = await env.binding_store.current_receipt(scope_a)
    stale = SimpleNamespace(**{**vars(_receipt(scope_a, head)), "binding_set_receipt_hash": "b" * 64})
    with pytest.raises(TaskExecutionAuthorityError) as exact_stale:
        await BindingRootResolver(env.binding_store)(stale)
    assert exact_stale.value.code == "workspace_binding_exact_receipt_stale"


@pytest.mark.asyncio
async def test_authority_records_run_fault_code_in_memo(tmp_path) -> None:
    from simple_harness.execution.context_authority import TaskExecutionEnvelopeRequest
    from simple_harness.tools.runtime_catalog import (
        ToolEffectClass,
        ToolExecutionPolicy,
        ToolRouteRequirement,
        ToolTaskScopeRequirement,
    )

    from deskpet.sdk_adapters.run_faults import RunFaultMemo

    memo = RunFaultMemo()
    authority = ProductTaskExecutionAuthority(root_resolver=None, fault_sink=memo)
    policy = ToolExecutionPolicy(
        "builtin:write_file", "d" * 64, ToolEffectClass.PROJECT_EFFECT,
        ToolRouteRequirement.REQUIRED, ToolTaskScopeRequirement.REQUIRED,
    )
    request = TaskExecutionEnvelopeRequest(
        RunId("run-memo"), "call-1", "effect-1", "raw-1", 1, 0, "write_file", policy, None
    )
    with pytest.raises(TaskExecutionAuthorityError) as fault:
        await authority.issue_envelope(request)
    assert fault.value.code == "sdk_task_execution_route_authority_missing"
    assert memo.read("run-memo") == "sdk_task_execution_route_authority_missing"
    # 首码优先：后续故障不覆盖；release 后清空。
    memo.record("run-memo", "later")
    assert memo.read("run-memo") == "sdk_task_execution_route_authority_missing"
    memo.release("run-memo")
    assert memo.read("run-memo") is None
    assert authority.root_resolver is None
    assert RequestId("x").value == "x"  # keep RequestId import honest for the harness
