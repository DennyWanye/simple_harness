# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b 黑盒验收矩阵骨架（Task 0，oracle 先于实现）。

每个用例的断言口径来自 acceptance S5B-AC-1/2/3/6 与 design-freeze.md；实装前保持 strict xfail，
实装某条后把对应 xfail 去掉——禁止照实现改断言。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

XF = pytest.mark.xfail(strict=True, reason="S5b 实装前占位（NOT_IMPLEMENTED）")


# ---- S5B-AC-3 / Task 1：effect gate 最小闭环 ----
# 基座：tests/sdk_adapters/s5b_effect_gate_harness.py（真 ReActLoop + 真三 authority + v45 ledger
# + S4 binding store + 确定性 provider + 生产 ProductEffectExecutor 前置门）。

PROJECT_EFFECT_FROZEN_LIST = (
    "write_file", "file_write", "edit_file", "move_file", "file_organize",
    "run_shell", "process_start", "doc_create", "doc_edit", "excel_create",
    "ppt_create", "pdf_export", "download_file", "workspace_prepare",
)
READ_TOOLS_KEEP_DEFAULT = (
    "read_file", "file_read", "glob", "file_glob", "grep", "file_grep",
    "list_directory", "doc_read",
)


@dataclass(frozen=True)
class _Inventory:
    name: str
    dispatch_kind: str
    permission_category: str
    source: str = "real-tool-manifest"
    version: str = "v1"
    execution_identity: str = "execution-identity-v1"
    projectless_admission: str = "requires_project"
    availability_reason: str | None = None


def test_project_effect_list_frozen_and_route_required() -> None:
    """design-freeze §1：清单内每个工具 policy=(project_effect, required, required)，读取类不变。"""
    from simple_harness import RunId
    from simple_harness.tools.runtime_catalog import (
        ToolEffectClass,
        ToolRouteRequirement,
        ToolTaskScopeRequirement,
    )

    from deskpet.permissions.effect_policy import IrreversibleEffectPolicy
    from deskpet.sdk_adapters.context_authority import canonical_sha256
    from deskpet.sdk_adapters.tool_authority import (
        PROJECT_EFFECT_TOOL_NAMES,
        SDK_TOOL_EXECUTION_POLICY_OVERRIDES,
        SdkRunToolAuthorityRegistry,
    )
    from deskpet.tool_catalog.manifest import load_tool_manifest
    from deskpet.tools.build_identity import EffectClass

    assert tuple(PROJECT_EFFECT_TOOL_NAMES) == PROJECT_EFFECT_FROZEN_LIST
    for name in PROJECT_EFFECT_FROZEN_LIST:
        assert SDK_TOOL_EXECUTION_POLICY_OVERRIDES[name] == (
            "project_effect", "required", "required"
        )
    for name in READ_TOOLS_KEEP_DEFAULT:
        assert name not in SDK_TOOL_EXECUTION_POLICY_OVERRIDES
    # task_scope_update 留 Task 3（design-freeze §1 第二段）。
    assert "task_scope_update" not in SDK_TOOL_EXECUTION_POLICY_OVERRIDES

    # exhaustiveness：清单内每个名字在 manifest 存在，且有冻结 EffectClass。
    manifest = load_tool_manifest()
    by_name = {str(item["name"]): item for item in manifest.tools}
    missing = [name for name in PROJECT_EFFECT_FROZEN_LIST if name not in by_name]
    assert missing == []
    decision = IrreversibleEffectPolicy().classify(
        owner_key="sdk-runtime",
        run_kind="chat",
        specs=[SimpleNamespace(**dict(by_name[name])) for name in PROJECT_EFFECT_FROZEN_LIST],
    )
    frozen_classes = {item.name: item.effect_class for item in decision.classifications}
    valid = {member.value for member in EffectClass}
    for name in PROJECT_EFFECT_FROZEN_LIST:
        assert frozen_classes[name] in valid, name

    # 真 registry：覆盖表进入 ExecutableToolRecord → SDK 执行策略三字段。
    specs = [
        {
            "name": "write_file",
            "description": "Write a project file",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
        {
            "name": "read_file",
            "description": "Read one file",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
        {
            "name": "tool_search",
            "description": "Search capabilities",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    ]
    registry = SdkRunToolAuthorityRegistry()
    registry.prepare_run(
        run_id="run-policy", session_id="session-policy", request_id="request-policy",
        root_run_id="root-policy", task_scope_id="task-policy", workspace_root=None,
        catalog={
            "generation": 1,
            "content_fingerprint": canonical_sha256(specs),
            "specs": specs,
            "schema_fingerprints": {
                item["name"]: canonical_sha256(item["input_schema"]) for item in specs
            },
        },
        inventory=(
            _Inventory("write_file", "async", "write_file"),
            _Inventory("read_file", "async", "read_file"),
            _Inventory("tool_search", "control", "read_file"),
        ),
    )
    exposure = registry.resolve_exposure(RunId("run-policy"))
    exposure.restore(RunId("run-policy"), None)
    write_policy = exposure.execution_policy(RunId("run-policy"), "write_file")
    assert write_policy.effect_class is ToolEffectClass.PROJECT_EFFECT
    assert write_policy.route_requirement is ToolRouteRequirement.REQUIRED
    assert write_policy.task_scope_requirement is ToolTaskScopeRequirement.REQUIRED
    read_policy = exposure.execution_policy(RunId("run-policy"), "read_file")
    assert read_policy.effect_class is ToolEffectClass.NON_PROJECT_EFFECT
    assert read_policy.route_requirement is ToolRouteRequirement.OPTIONAL


@pytest.mark.asyncio
async def test_write_file_envelope_reverified_then_executed_and_host_file_event(
    tmp_path: Path,
) -> None:
    """ROUTED_TASK 单 root：write_file 经 EffectGate 重验通过 → 物理执行；envelope 六元组等于 S4 head。

    execution_effects 行 + host.file 事件同事务由 Task 2 的 objective-event 用例
    （下方 strict xfail）在同一基座上补齐。
    """
    from simple_harness.tools.runtime_catalog import ToolEffectClass

    from tests.sdk_adapters import s5b_effect_gate_harness as h

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
    assert out["result"].termination.route_state == "routed_task"
    assert env.effects.calls == ["context_route", "write_file"]
    assert (root_a / "a.txt").read_text(encoding="utf-8") == "alpha"
    # 门放行：模型看到 succeeded 而非 rejected。
    [message] = h.tool_messages(env, "write_file")
    assert '"outcome":"succeeded"' in message
    assert "rejected" not in message
    assert env.memo.read(h.RUN.value) is None

    # envelope 六元组 == S4 binding head；per-call 签发（context_route 也经 issue_envelope）。
    head = await env.binding_store.current_receipt(scope_a)
    [record] = env.effects.write_file_calls
    envelope = record["envelope"]
    assert envelope is not None
    assert envelope.task_scope_id == scope_a
    assert envelope.root_id == head.appended_root.root_id
    assert envelope.root_identity_hash == head.root_identity_hashes[0]
    assert envelope.binding_set_revision == head.binding_set_revision
    assert envelope.binding_set_receipt_id == head.receipt_id
    assert envelope.binding_set_receipt_hash == head.receipt_hash
    assert envelope.tool_name == "write_file"
    assert record["context"].call_id == envelope.call_id
    assert record["context"].effect_id == envelope.effect_id
    assert [r.tool_name for r in env.task_authority.requests] == ["context_route", "write_file"]
    assert env.task_authority.requests[-1].policy.effect_class is ToolEffectClass.PROJECT_EFFECT
    # 冻结 route receipt 在 v45 ledger 且与 envelope 交叉绑定。
    decisions = h.rows(
        env.db_path,
        "SELECT receipt_id,receipt_hash,task_scope_id FROM context_route_decisions "
        "WHERE sdk_run_id=? AND origin='context_tool'",
        h.RUN.value,
    )
    assert decisions == [(envelope.route_receipt_id, envelope.route_receipt_hash, scope_a)]


@XF
def test_write_file_effect_commits_execution_effect_row_and_host_file_event_same_tx() -> None:
    """Task 2（S5B-AC-1①/§2 映射）：write_file 成功 → execution_effects 行 + host.file material 事件同事务。"""
    raise NotImplementedError


@pytest.mark.asyncio
async def test_reroute_to_other_scope_then_write_rejected_frozen_scope_mismatch(
    tmp_path: Path,
) -> None:
    """同 Run 再路由到另一 scope 后 write_file → effect_gate_frozen_scope_mismatch（rejected，零写入）。"""
    from tests.sdk_adapters import s5b_effect_gate_harness as h

    env = await h.build_env(tmp_path)
    scope_a, root_a = await h.make_bound_scope(env, "a", "root-a")
    scope_b, root_b = await h.make_bound_scope(env, "b", "root-b")
    h.freeze_run(env, task_scope_id=scope_a, workspace_root=root_a)
    provider = h.ScriptedProvider(
        [
            h.tool_call(
                "context_route",
                {"route": "resume_existing", "task_scope_id": scope_a},
                raw_id="raw-route-a",
            ),
            h.tool_call("write_file", {"path": "a.txt", "content": "alpha"}, raw_id="raw-write-a"),
            h.tool_call(
                "context_route",
                {"route": "resume_existing", "task_scope_id": scope_b},
                raw_id="raw-route-b",
            ),
            h.tool_call("write_file", {"path": "b.txt", "content": "beta"}, raw_id="raw-write-b"),
            h.answer("done"),
        ]
    )
    out = await h.run_capture(env, provider)

    assert out["exception"] is None
    assert out["result"].termination.route_state == "routed_task"
    # 第二次 write_file 在物理 dispatch 之前被门拒绝：handler 未被调用、零写入。
    assert env.effects.calls == ["context_route", "write_file", "context_route"]
    assert (root_a / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert not (root_b / "b.txt").exists()
    assert not (root_a / "b.txt").exists()
    first, second = h.tool_messages(env, "write_file")
    assert '"outcome":"succeeded"' in first
    assert '"outcome":"rejected"' in second
    assert '"error_code":"effect_gate_frozen_scope_mismatch"' in second
    # 被拒的 envelope 确实携带了 scope B（SDK 认为合法），是 Host 冻结 authority 拒绝的。
    assert [e.task_scope_id for e in env.task_authority.envelopes if e.tool_name == "write_file"] == [
        scope_a, scope_b
    ]
    assert env.memo.read(h.RUN.value) is None


@pytest.mark.asyncio
async def test_standalone_route_project_effect_is_run_fault_with_stable_code(
    tmp_path: Path,
) -> None:
    """standalone 下写工具不在 snapshot tools；强制调用 → durable FAILED + sdk_task_execution_route_authority_missing。"""
    from deskpet.execution import RunState
    from deskpet.execution.foreground_queue import ForegroundQueueStore
    from deskpet.execution.foreground_runtime import SqliteSdkTerminalObserver
    from deskpet.sdk_adapters.task_execution import TaskExecutionAuthorityError
    from tests.execution import test_foreground_queue as fq
    from tests.sdk_adapters import s5b_effect_gate_harness as h

    env = await h.build_env(tmp_path)
    scope_a, root_a = await h.make_bound_scope(env, "a", "root-a")
    h.freeze_run(env, task_scope_id=scope_a, workspace_root=root_a)
    provider = h.ScriptedProvider(
        [
            h.tool_call("context_route", {"route": "direct_standalone"}, raw_id="raw-route"),
            h.tool_call("write_file", {"path": "a.txt", "content": "alpha"}, raw_id="raw-write"),
            h.answer("never reached"),
        ]
    )
    out = await h.run_capture(env, provider)

    # ① snapshot tools：UNROUTED 与 routed_standalone 两轮都不含 PROJECT_EFFECT 工具，读取类仍在。
    exposed = h.provider_tool_names(provider)
    assert len(exposed) == 2
    for names in exposed:
        assert "write_file" not in names
        assert {"context_route", "task_scope_search", "read_file"} <= set(names)
    # ② 强制调用：整 Run 故障（稳定码），零写入、handler 未被调用、无 TOOL 消息。
    exc = out["exception"]
    assert isinstance(exc, TaskExecutionAuthorityError)
    assert exc.code == "sdk_task_execution_route_authority_missing"
    assert env.effects.calls == ["context_route"]
    assert env.effects.write_file_calls == []
    assert not (root_a / "a.txt").exists()
    assert h.tool_messages(env, "write_file") == []
    assert h.checkpoint(env).get("route_state") == "routed_standalone"
    # ③ Host 侧故障备忘（terminal observer 的稳定码来源）。
    assert env.memo.read(h.RUN.value) == "sdk_task_execution_route_authority_missing"

    # ④ durable FAILED：SDK 只暴露 driver_failed，Host 把稳定码写进 run_terminal
    #    ExecutionEvidence.public_payload.error_code（不新增 host.* 事件种类）。
    queue_db, primary_id, clock = await fq._ready(tmp_path / "queue")
    store = ForegroundQueueStore(queue_db, clock=clock)
    await fq._enqueue(store, primary_id, 1)
    admission = await fq._claim_and_bind(store, sdk_run_id=h.RUN.value)
    await store.record_sdk_started(
        host_run_id=admission.host_run_id,
        sdk_run_id=h.RUN.value,
        owner_id=admission.owner_id,
        generation=admission.generation,
        sdk_event_id="sdk-start-fault",
        idempotency_key="sdk-start-fault",
    )

    class _Ingress:
        async def wait_idle(self, run_id: str) -> None:
            assert run_id == h.RUN.value

        def query(self, run_id: str):  # type: ignore[no-untyped-def]
            return SimpleNamespace(state=SimpleNamespace(value="failed"))

    class _Stack:
        def read_run_terminal_evidence(self, run_id: str):  # type: ignore[no-untyped-def]
            assert run_id == h.RUN.value
            return SimpleNamespace(
                run_id=run_id,
                state="failed",
                event_id="sdk-terminal-fault",
                event_hash="9" * 64,
                occurred_at=101.0,
                error_code="driver_failed",
            )

    observed = await SqliteSdkTerminalObserver(
        str(queue_db), _Ingress(), _Stack(), run_fault_memo=env.memo  # type: ignore[arg-type]
    ).observe(
        host_run_id=admission.host_run_id,
        sdk_run_id=h.RUN.value,
        subject=fq.SUBJECT,
        owner_id=admission.owner_id,
        generation=admission.generation,
    )
    assert observed is not None and observed.terminal_state is RunState.FAILED
    terminal = await store.record_sdk_terminal(
        host_run_id=admission.host_run_id,
        sdk_run_id=h.RUN.value,
        owner_id=admission.owner_id,
        generation=admission.generation,
        terminal_state=observed.terminal_state,
        sdk_event_id=observed.sdk_event_id,
        sdk_event_hash=observed.sdk_event_hash,
        idempotency_key="terminal-fault",
    )
    assert terminal.terminal_state is RunState.FAILED
    heads = h.rows(
        queue_db, "SELECT current_state FROM foreground_run_heads WHERE host_run_id=?",
        admission.host_run_id,
    )
    assert heads == [("FAILED",)]
    [(payload_json, kinds)] = h.rows(
        queue_db,
        "SELECT e.payload_json,e.event_kind FROM task_scope_execution_ingest_receipts r "
        "JOIN task_scope_events e ON e.event_id=r.event_id WHERE r.run_id=? "
        "AND r.evidence_kind='run_terminal'",
        h.RUN.value,
    )
    public = json.loads(payload_json)["public_payload"]
    assert public["terminal_state"] == "FAILED"
    assert public["error_code"] == "sdk_task_execution_route_authority_missing"
    assert kinds == "harness.run_terminal"


# ---- S5B-AC-1 / Task 2-3：客观事件、脏标记、终态门、兜底 ----
@XF
def test_material_event_sets_dirty_and_terminal_gate_requires_closure_receipt() -> None:
    raise NotImplementedError


@XF
def test_harness_evidence_reservations_drained_before_run_terminal_no_row_loss() -> None:
    """probe A9 场景：迟到 seq 不再被 terminal 永久拒绝。"""
    raise NotImplementedError


@XF
def test_task_scope_update_mutate_closes_and_no_mutation_requires_reason() -> None:
    raise NotImplementedError


@XF
def test_missed_call_fallback_invokes_once_and_unknown_never_resends() -> None:
    raise NotImplementedError


@XF
def test_pending_closure_belongs_to_admission_scope_and_next_run_merges() -> None:
    raise NotImplementedError


# ---- S5B-AC-2 / Task 4：终态同事务 outbox、analysis executor、幂等 ----
@XF
def test_terminal_commit_writes_ingestion_outbox_same_tx() -> None:
    raise NotImplementedError


@XF
def test_analysis_executor_three_key_lookup_zero_second_provider_call() -> None:
    raise NotImplementedError


@XF
def test_analysis_proposal_span_derivation_rejects_paraphrase() -> None:
    raise NotImplementedError


# ---- S5B-AC-6 / Task 6：composition、cutover ----
@XF
def test_composition_missing_piece_startup_fail_each() -> None:
    raise NotImplementedError


@XF
def test_v46_forward_migration_and_old_runtime_rejects() -> None:
    raise NotImplementedError
