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
    # task_scope_update（design-freeze §1 第二段，Task 3 实装）：direct kernel、non_project_effect、
    # route/TaskScope REQUIRED；它不在 PROJECT_EFFECT 清单内。
    assert SDK_TOOL_EXECUTION_POLICY_OVERRIDES["task_scope_update"] == (
        "non_project_effect", "required", "required"
    )
    assert "task_scope_update" not in PROJECT_EFFECT_FROZEN_LIST

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


@pytest.mark.asyncio
async def test_write_file_effect_commits_execution_effect_row_and_host_file_event_same_tx(
    tmp_path: Path,
) -> None:
    """Task 2（S5B-AC-1①/§2 映射）：write_file 成功 → execution_effects 行 + host.file material 事件同事务。

    口径（design-freeze §3）：SDK effect 账本与 state.db 是两个库，不可能同一 SQLite 事务；
    Host 侧 ``host.file`` 事件 + ``human_memory_evidence`` 行 + ``harness.tool_invocation``
    导入 **同一 state.db 事务** 提交，并经 ``harness_evidence_reservations``（source_event_id=
    ``effect:{effect_id}``，物理 dispatch 前预留 seq）与 SDK ``execution_effects`` 行幂等关联。
    """
    from deskpet.execution.semantic_closure import dirty_state
    from deskpet.task_scope.store import CanonicalTaskScopeStore
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
    [record] = env.effects.write_file_calls
    effect_id = record["context"].effect_id.value
    source_event_id = f"effect:{effect_id}"

    # ① host.file material 事件：由 Host 在物理执行处直接写 S4 recorder（不经 LLM）。
    [(event_id, kind, payload_json)] = h.rows(
        env.db_path,
        "SELECT event_id,event_kind,payload_json FROM task_scope_events "
        "WHERE task_scope_id=? AND source_kind='host' AND source_event_id=?",
        scope_a, source_event_id,
    )
    assert kind == "host.file"
    payload = json.loads(payload_json)
    assert payload["tool_name"] == "write_file"
    assert payload["effect_id"] == effect_id
    assert payload["outcome"] == "succeeded"
    assert payload["targets"] == ["a.txt"]
    assert "alpha" not in payload_json  # 只记路径，不记文件内容

    # ② evidence 行（refs 可用）：host-typed-ingress/v1 sanitized envelope，链接到该事件。
    [(evidence_id, content_hash, link_created_at)] = h.rows(
        env.db_path,
        "SELECT evidence_id,content_hash,created_at FROM task_scope_evidence_links WHERE event_id=?",
        event_id,
    )
    [(envelope_sha256, filter_policy, run_id)] = h.rows(
        env.db_path,
        "SELECT e.envelope_sha256,r.filter_policy_version,e.run_id FROM human_memory_evidence e "
        "JOIN human_memory_sanitization_receipts r ON r.receipt_id=e.receipt_id "
        "WHERE e.evidence_id=?",
        evidence_id,
    )
    assert envelope_sha256 == content_hash
    assert filter_policy == "host-typed-ingress/v1"
    assert run_id == h.RUN.value

    # ③ 同一 SDK effect 的 Harness 证据（harness.tool_invocation，PROJECT_EFFECT → material）
    #    经预留 seq 导入，refs 指向同一 evidence 行。
    [(reservation_status, reservation_seq, resolved_at)] = h.rows(
        env.db_path,
        "SELECT status,source_sequence,resolved_at FROM harness_evidence_reservations "
        "WHERE run_id=? AND source_event_id=?",
        h.RUN.value, source_event_id,
    )
    assert reservation_status == "ingested"
    [(receipt_seq, harness_kind, harness_payload, committed_at)] = h.rows(
        env.db_path,
        "SELECT r.source_sequence,e.event_kind,e.payload_json,r.committed_at "
        "FROM task_scope_execution_ingest_receipts r "
        "JOIN task_scope_events e ON e.event_id=r.event_id "
        "WHERE r.run_id=? AND r.source_event_id=?",
        h.RUN.value, source_event_id,
    )
    assert harness_kind == "harness.tool_invocation"
    assert receipt_seq == reservation_seq
    harness = json.loads(harness_payload)
    assert harness["public_payload"]["tool_name"] == "write_file"
    assert harness["public_payload"]["effect_state"] == "succeeded"
    assert [ref["evidence_id"] for ref in harness["evidence_refs"]] == [evidence_id]
    # 同事务：预留解决时刻 == 导入回执提交时刻 == 链接创建时刻（一个 now）。
    assert resolved_at == committed_at == link_created_at

    # ④ 脏标记：host.file + PROJECT_EFFECT tool_invocation 都是 material。
    dirty = await dirty_state(CanonicalTaskScopeStore(env.db_path), scope_a)
    assert dirty.is_dirty
    assert sorted(item.event_kind for item in dirty.material_events) == [
        "harness.tool_invocation", "host.file",
    ]


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
@pytest.mark.asyncio
async def test_material_event_sets_dirty_state_from_last_closure_receipt(tmp_path: Path) -> None:
    """Task 2（§2 dirty_state）：自最后一条 outcome∈{mutate,no_mutation} 的 closure receipt 的
    closure_watermark 之后的 material 事件集合；无 receipt 则自 0；pending 不清脏。
    （原用例中「终态门要求 receipt」半段属 Task 3，拆为下方 strict xfail。）"""
    import sqlite3

    from deskpet.execution.semantic_closure import dirty_state
    from deskpet.task_scope.store import CanonicalTaskScopeStore, TaskEventRecorder
    from tests.sdk_adapters import s5b_effect_gate_harness as h

    env = await h.build_env(tmp_path)
    scope_a, root_a = await h.make_bound_scope(env, "a", "root-a")
    h.freeze_run(env, task_scope_id=scope_a, workspace_root=root_a)
    store = CanonicalTaskScopeStore(env.db_path)
    recorder = TaskEventRecorder(store)
    # 无 receipt、无 material 事件：不脏（host.turn 是 trivial）。
    await recorder.record_turn(task_scope_id=scope_a, source_event_id="turn-1", payload={"turn_id": "t1"})
    clean = await dirty_state(store, scope_a)
    assert clean.closure_watermark == 0 and not clean.is_dirty and clean.material_events == ()

    provider = h.ScriptedProvider(
        [
            h.tool_call(
                "context_route",
                {"route": "resume_existing", "task_scope_id": scope_a},
                raw_id="raw-route",
            ),
            h.tool_call("write_file", {"path": "a.txt", "content": "alpha"}, raw_id="raw-write"),
            h.tool_call("read_file", {"path": "a.txt"}, raw_id="raw-read"),
            h.answer("done"),
        ]
    )
    out = await h.run_capture(env, provider)
    assert out["exception"] is None
    dirty = await dirty_state(store, scope_a)
    assert dirty.is_dirty
    # write_file → host.file(material) + harness.tool_invocation(PROJECT_EFFECT, material)；
    # read_file / context_route → harness.tool_invocation(trivial)。
    assert sorted(item.event_kind for item in dirty.material_events) == [
        "harness.tool_invocation", "host.file",
    ]
    watermark = max(item.event_sequence for item in dirty.material_events)

    def receipt(receipt_id: str, wm: int, outcome: str) -> None:
        with sqlite3.connect(env.db_path) as db:
            db.execute(
                "INSERT INTO task_scope_closure_receipts(receipt_id,task_scope_id,sdk_run_id,"
                "host_run_id,closure_watermark,outcome,plan_id,reason_code,attempt_id,created_at) "
                "VALUES (?,?,?,?,?,?,NULL,?,NULL,?)",
                (receipt_id, scope_a, h.RUN.value, "host-run-1", wm, outcome, "test", 1.0),
            )
            db.commit()

    # pending 不清脏。
    receipt("r-pending", watermark, "pending")
    assert (await dirty_state(store, scope_a)).is_dirty
    # mutate 覆盖到 watermark → 清脏。
    receipt("r-mutate", watermark, "mutate")
    after = await dirty_state(store, scope_a)
    assert after.closure_watermark == watermark and not after.is_dirty
    # 新 material 事件在 watermark 之后 → 再脏；no_mutation receipt 覆盖 → 清脏。
    late = await recorder.record_test(
        task_scope_id=scope_a,
        source_event_id="test-late",
        payload={"command_head": "pytest", "exit_code": 0},
    )
    again = await dirty_state(store, scope_a)
    assert [item.event_sequence for item in again.material_events] == [late.event_sequence]
    receipt("r-nomut", late.event_sequence, "no_mutation")
    assert not (await dirty_state(store, scope_a)).is_dirty


@pytest.mark.asyncio
@pytest.mark.parametrize("sdk_state,terminal", [("completed", "COMPLETED"), ("failed", "FAILED")])
async def test_terminal_gate_requires_closure_receipt(tmp_path: Path, sdk_state: str, terminal: str) -> None:
    """Task 3（S5B-AC-1③ 三水位）：Harness 证据水位放行后，语义收敛水位仍要求 closure receipt。

    scope 有 material 脏事件而无 receipt → ``record_sdk_terminal`` 稳定拒绝
    ``foreground_terminal_closure_pending``（COMPLETED/FAILED/CANCELLED/STOPPED 一律生效，零半状态）；
    本 Run 写下 pending receipt 后照常提交；closing receipt 覆盖 watermark 同样放行。
    """
    from deskpet.execution.foreground_queue import ForegroundQueueError
    from deskpet.execution.semantic_closure import dirty_state, write_closure_receipt
    from deskpet.task_scope.store import CanonicalTaskScopeStore
    from tests.sdk_adapters import s5b_closure_harness as ch

    env = await ch.bound_run(tmp_path, "sdk-run-gate")
    await ch.material_write(env, "e-1")
    facts = ch.FakeRunFacts(env.run_id, state=sdk_state)
    observed = await ch.observe_terminal(env, facts)
    assert observed is not None and observed.terminal_state.value == terminal
    # Harness 水位已满足（run_terminal 已导入、authorize_terminal 已放行），但语义收敛水位未满足。
    dirty = await dirty_state(CanonicalTaskScopeStore(env.db_path), ch.SCOPE)
    assert dirty.is_dirty
    before = ch.rows(env.db_path, "SELECT COUNT(*) FROM foreground_terminal_receipts")
    with pytest.raises(ForegroundQueueError) as blocked:
        await ch.record_terminal(env, observed)
    assert blocked.value.code == "foreground_terminal_closure_pending"
    assert ch.rows(env.db_path, "SELECT COUNT(*) FROM foreground_terminal_receipts") == before
    assert ch.rows(env.db_path, "SELECT current_state FROM foreground_run_heads WHERE host_run_id=?", env.admission.host_run_id) == [("RUNNING",)]

    # 本 Run 的 pending receipt（兜底失败/非 COMPLETED 终态）→ 终态照常提交，脏标记仍在。
    await write_closure_receipt(
        env.db_path, task_scope_id=ch.SCOPE, sdk_run_id=env.run_id, host_run_id=env.admission.host_run_id,
        closure_watermark=dirty.event_watermark, outcome="pending", plan_id=None,
        reason_code="closure_run_not_completed", attempt_id=None,
    )
    receipt = await ch.record_terminal(env, observed)
    assert receipt.terminal_state.value == terminal
    assert (await dirty_state(CanonicalTaskScopeStore(env.db_path), ch.SCOPE)).is_dirty
    # 幂等重放同一终态：同一 receipt。
    again = await ch.record_terminal(env, observed)
    assert again.terminal_receipt_id == receipt.terminal_receipt_id


@pytest.mark.asyncio
async def test_harness_evidence_reservations_drained_before_run_terminal_no_row_loss(
    tmp_path: Path,
) -> None:
    """probe A9 场景：迟到 seq 不再被 terminal 永久拒绝。

    seq1 已导入、seq2/seq3 已预留未导入 → observer 的 next_sequence=4（不是 2）；迟到 seq2 仍被
    接受；terminal 前 observer 排空：seq3 无事实 → tombstone(abandoned)；run_terminal=seq4；
    ``authorize_terminal`` 只在排空后放行；crash 后重放排空幂等。
    """
    from deskpet.execution import RunState
    from deskpet.execution.evidence_ingress import (
        ExecutionEvidenceIngress,
        TerminalWatermarkPending,
    )
    from deskpet.execution.foreground_queue import ForegroundQueueStore
    from deskpet.execution.foreground_runtime import SqliteSdkTerminalObserver
    from tests.execution import test_foreground_queue as fq
    from tests.sdk_adapters import s5b_effect_gate_harness as h

    run_id = "sdk-run-a9"
    queue_db, primary_id, clock = await fq._ready(tmp_path / "queue")
    store = ForegroundQueueStore(queue_db, clock=clock)
    await fq._enqueue(store, primary_id, 1)
    admission = await fq._claim_and_bind(store, sdk_run_id=run_id)
    await store.record_sdk_started(
        host_run_id=admission.host_run_id, sdk_run_id=run_id, owner_id=admission.owner_id,
        generation=admission.generation, sdk_event_id="sdk-start-a9",
        idempotency_key="sdk-start-a9",
    )
    ingress = ExecutionEvidenceIngress(queue_db)
    first = await ingress.reserve(
        run_id=run_id, task_scope_id=fq.SCOPE, kind="provider_invocation",
        source_event_id="prov-1",
    )
    assert first.source_sequence == 1
    await ingress.ingest(
        task_scope_id=fq.SCOPE,
        evidence=fq._execution_evidence(
            sdk_run_id=run_id, source_event_id="prov-1", kind="provider_invocation",
            source_sequence=1,
        ),
    )
    tool = await ingress.reserve(
        run_id=run_id, task_scope_id=fq.SCOPE, kind="tool_invocation", source_event_id="tool-2"
    )
    snap = await ingress.reserve(
        run_id=run_id, task_scope_id=fq.SCOPE, kind="context_snapshot", source_event_id="snap-3"
    )
    assert (tool.source_sequence, snap.source_sequence) == (2, 3)
    # 预留幂等：同 source_event_id 返回同 seq。
    replay = await ingress.reserve(
        run_id=run_id, task_scope_id=fq.SCOPE, kind="tool_invocation", source_event_id="tool-2"
    )
    assert replay.source_sequence == 2
    assert await ingress.next_sequence(run_id) == 4
    # 迟到的 seq2（probe P1 中被 execution_source_sequence_conflict 永久拒绝的那一行）被接受。
    late = await ingress.ingest(
        task_scope_id=fq.SCOPE,
        evidence=fq._execution_evidence(
            sdk_run_id=run_id, source_event_id="tool-2", kind="tool_invocation", source_sequence=2
        ),
    )
    assert late.source_sequence == 2 and late.durable_source_sequence == 2
    # 排空前不得放行终态（seq3 仍 reserved）。
    with pytest.raises(TerminalWatermarkPending):
        await ingress.authorize_terminal(run_id)

    class _Ingress:
        async def wait_idle(self, r: str) -> None:
            assert r == run_id

        def query(self, r: str):  # type: ignore[no-untyped-def]
            return SimpleNamespace(state=SimpleNamespace(value="completed"))

    class _Stack:
        def read_run_terminal_evidence(self, r: str):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                run_id=r, state="completed", event_id="sdk-terminal-a9",
                event_hash="7" * 64, occurred_at=9.0, error_code=None,
            )

        async def read_reserved_fact(self, reservation):  # type: ignore[no-untyped-def]
            return None  # SDK/coordinator 读不到事实 → tombstone

    observed = await SqliteSdkTerminalObserver(
        str(queue_db), _Ingress(), _Stack()  # type: ignore[arg-type]
    ).observe(
        host_run_id=admission.host_run_id, sdk_run_id=run_id, subject=fq.SUBJECT,
        owner_id=admission.owner_id, generation=admission.generation,
    )
    assert observed is not None and observed.terminal_state is RunState.COMPLETED
    rows = h.rows(
        queue_db,
        "SELECT r.source_sequence,r.evidence_kind,e.payload_json "
        "FROM task_scope_execution_ingest_receipts r "
        "JOIN task_scope_events e ON e.event_id=r.event_id WHERE r.run_id=? "
        "ORDER BY r.source_sequence",
        run_id,
    )
    assert [(seq, kind) for seq, kind, _ in rows] == [
        (1, "provider_invocation"), (2, "tool_invocation"),
        (3, "context_snapshot"), (4, "run_terminal"),
    ]
    tombstone = json.loads(rows[2][2])["public_payload"]
    assert tombstone == {"status": "abandoned", "reservation_id": snap.reservation_id}
    statuses = h.rows(
        queue_db,
        "SELECT source_event_id,status FROM harness_evidence_reservations WHERE run_id=? "
        "ORDER BY source_sequence",
        run_id,
    )
    assert statuses == [("prov-1", "ingested"), ("tool-2", "ingested"), ("snap-3", "abandoned")]
    gate = await ingress.authorize_terminal(run_id)
    assert gate.durable_source_sequence == gate.terminal_source_sequence == 4
    # 重放排空（crash 后新 owner）：幂等，无新行。
    report = await ingress.drain_reservations(run_id, fact_reader=_Stack())
    assert report.ingested == () and report.abandoned == ()


@pytest.mark.asyncio
async def test_task_scope_update_mutate_closes_and_no_mutation_requires_reason(tmp_path: Path) -> None:
    """Task 3（S5B-AC-1②）：``task_scope_update`` 是常暴露的 direct kernel 工具（strict schema，
    NON_PROJECT_EFFECT / route REQUIRED / TaskScope REQUIRED）。

    ROUTED_TASK 下 write_file 产生 material 脏事件 → 模型在终答前提交 ``mutate``（refs 属本 scope）→
    ``apply_mutation_plan`` 与 closure receipt **同一事务**：revision +1、receipt(outcome=mutate,
    plan_id=sha256(idempotency_key+scope), closure_watermark 覆盖全部 material 事件)、脏标记清零。
    第二个 Run：``no_mutation`` 缺 ``closure_reason`` → 稳定拒绝 ``task_scope_update_payload_invalid``
    （不递增 revision、无 receipt、写 pre-admission audit 行），补上 closure_reason 后 no_mutation 收口。
    """
    import hashlib

    from simple_harness.contracts.messages import Message, MessageRole

    from deskpet.execution.semantic_closure import dirty_state
    from deskpet.sdk_adapters.task_scope_mutation import derive_plan_id
    from deskpet.sdk_adapters.tool_authority import (
        SDK_DIRECT_TOOL_KERNEL,
        SDK_TOOL_EXECUTION_POLICY_OVERRIDES,
    )
    from deskpet.sdk_adapters.tools import (
        PRODUCT_TOOL_NAMES,
        PROJECTLESS_SAFE_TOOL_NAMES,
    )
    from deskpet.task_scope.store import CanonicalTaskScopeStore
    from tests.sdk_adapters import s5b_effect_gate_harness as h

    # 注册面：direct kernel、projectless safe、覆盖表 (non_project_effect, required, required)。
    assert "task_scope_update" in SDK_DIRECT_TOOL_KERNEL
    assert "task_scope_update" in PROJECTLESS_SAFE_TOOL_NAMES
    assert "task_scope_update" in PRODUCT_TOOL_NAMES
    assert SDK_TOOL_EXECUTION_POLICY_OVERRIDES["task_scope_update"] == ("non_project_effect", "required", "required")

    env = await h.build_env(tmp_path)
    scope_a, root_a = await h.make_bound_scope(env, "a", "root-a")
    h.freeze_run(env, task_scope_id=scope_a, workspace_root=root_a)
    store = CanonicalTaskScopeStore(env.db_path)

    def evidence_ids() -> list[str]:
        return [
            str(r[0])
            for r in h.rows(
                env.db_path,
                "SELECT DISTINCT evidence_id FROM task_scope_evidence_links WHERE task_scope_id=?",
                scope_a,
            )
        ]

    def closure_arguments(outcome: str, key: str, **extra: object) -> dict:
        [(revision,)] = h.rows(env.db_path, "SELECT current_revision FROM task_scope_heads WHERE task_scope_id=?", scope_a)
        refs = evidence_ids()
        arguments: dict = {
            "outcome": outcome, "base_revision": int(revision), "evidence_refs": refs,
            "idempotency_key": key, **extra,
        }
        if outcome == "mutate":
            arguments["operations"] = [
                {"operation_id": "op-1", "kind": "plan.step.add", "value": "a.txt 已写入",
                 "reason_code": "objective_file_change", "evidence_refs": refs},
            ]
        return arguments

    class LazyProvider(h.ScriptedProvider):
        """Closure arguments are computed lazily so they see the live head/refs."""

        async def invoke(self, run_id, request, *, cancel, execution_lease):  # type: ignore[no-untyped-def]
            if self.responses and callable(self.responses[0]):
                self.responses[0] = self.responses[0]()
            return await super().invoke(run_id, request, cancel=cancel, execution_lease=execution_lease)

    def deferred(outcome: str, key: str, **extra: object):  # type: ignore[no-untyped-def]
        return lambda: h.tool_call("task_scope_update", closure_arguments(outcome, key, **extra), raw_id=f"raw-{key}")

    provider = LazyProvider(
        [
            h.tool_call("context_route", {"route": "resume_existing", "task_scope_id": scope_a}, raw_id="raw-route"),
            h.tool_call("write_file", {"path": "a.txt", "content": "alpha"}, raw_id="raw-write"),
            deferred("mutate", "closure-1"),
            h.answer("done"),
        ]
    )
    out = await h.run_capture(env, provider)
    assert out["exception"] is None
    # task_scope_update 每一轮都暴露（不按脏标记控制可见性）。
    assert all("task_scope_update" in names for names in h.provider_tool_names(provider))
    [(revision, watermark)] = h.rows(env.db_path, "SELECT current_revision,event_watermark FROM task_scope_heads WHERE task_scope_id=?", scope_a)
    assert revision == 2
    receipts = h.rows(
        env.db_path,
        "SELECT sdk_run_id,closure_watermark,outcome,plan_id,reason_code FROM task_scope_closure_receipts WHERE task_scope_id=?",
        scope_a,
    )
    plan_id = derive_plan_id("closure-1", scope_a)
    assert plan_id == hashlib.sha256(("closure-1" + scope_a).encode("utf-8")).hexdigest()
    assert receipts == [(h.RUN.value, watermark, "mutate", plan_id, "model_closure")]
    # receipt 与 apply 同事务：decision 与 receipt 的 plan_id 一致，created_at 同一 now。
    [(decision_plan_id, decision_outcome, decision_created)] = h.rows(
        env.db_path, "SELECT plan_id,outcome,created_at FROM task_scope_mutation_decisions WHERE task_scope_id=?", scope_a,
    )
    [(receipt_created,)] = h.rows(env.db_path, "SELECT created_at FROM task_scope_closure_receipts WHERE plan_id=?", plan_id)
    assert (decision_plan_id, decision_outcome) == (plan_id, "mutate") and decision_created == receipt_created
    assert not (await dirty_state(store, scope_a)).is_dirty
    result_json = json.loads(h.tool_messages(env, "task_scope_update")[0])
    assert result_json["outcome"] == "succeeded"
    assert result_json["value"]["ok"] is True and result_json["value"]["closure_receipt"]["outcome"] == "mutate"

    # 第二个 Run（同 scope）：先制造新的 material 事件，再提交缺 closure_reason 的 no_mutation → 拒绝；
    # 补上 closure_reason → 收口。
    env.context.messages.append(Message(role=MessageRole.USER, content="再改一次"))
    env.checkpoint.value = None
    provider2 = LazyProvider(
        [
            h.tool_call("context_route", {"route": "resume_existing", "task_scope_id": scope_a}, raw_id="raw-route-2"),
            h.tool_call("write_file", {"path": "b.txt", "content": "beta"}, raw_id="raw-write-2"),
            deferred("no_mutation", "closure-2"),
            deferred("no_mutation", "closure-3", closure_reason="model_no_change"),
            h.answer("done again"),
        ]
    )
    out = await h.run_capture(env, provider2)
    assert out["exception"] is None
    messages = h.tool_messages(env, "task_scope_update")
    assert len(messages) == 3
    rejected = json.loads(messages[1])
    assert rejected["outcome"] == "rejected" and rejected["error_code"] == "task_scope_update_payload_invalid"
    accepted = json.loads(messages[2])
    assert accepted["outcome"] == "succeeded"
    assert accepted["value"]["ok"] is True and accepted["value"]["closure_receipt"]["outcome"] == "no_mutation"
    [(revision2, watermark2)] = h.rows(env.db_path, "SELECT current_revision,event_watermark FROM task_scope_heads WHERE task_scope_id=?", scope_a)
    assert revision2 == 3  # no_mutation 也是一次 apply（S4 语义），被拒的那次未递增
    outcomes = h.rows(env.db_path, "SELECT outcome,closure_watermark FROM task_scope_closure_receipts WHERE task_scope_id=? ORDER BY created_at", scope_a)
    assert [o for o, _ in outcomes] == ["mutate", "no_mutation"] and outcomes[1][1] == watermark2
    assert not (await dirty_state(store, scope_a)).is_dirty
    audits = h.rows(
        env.db_path,
        "SELECT sdk_run_id,payload_kind,reason_code FROM host_pre_admission_audit ORDER BY created_at",
    )
    assert audits == [(h.RUN.value, "task_scope_update", "task_scope_update_payload_invalid")]


@pytest.mark.asyncio
async def test_missed_call_fallback_invokes_once_and_unknown_never_resends(tmp_path: Path) -> None:
    """Task 3（S5B-AC-1③ A3）：模型漏调用 → SDK terminal 之后、Host 终态之前，Host 以 Run 绑定的
    同一主模型发起**恰一次** closure 调用（仅暴露 task_scope_update）；合法 plan → receipt 与 apply
    同事务；sent_unknown → durable pending(closure_attempt_unknown)，重启/新 owner 重放 0 重发；
    timeout/拒绝 → pending 且 Host 终态照常提交。
    """
    import asyncio

    from simple_harness.providers.errors import ProviderTimeoutError

    from deskpet.execution.semantic_closure import (
        closure_plan_id,
        closure_request_hash,
        dirty_state,
    )
    from deskpet.task_scope.store import CanonicalTaskScopeStore
    from tests.sdk_adapters import s5b_closure_harness as ch

    # ① 漏调用 → 兜底一次 mutate（Provider 计数 1），receipt 与 apply 同事务，终态放行。
    env = await ch.bound_run(tmp_path / "one", "sdk-run-fb-1")
    await ch.material_write(env, "e-1")
    dirty = await dirty_state(CanonicalTaskScopeStore(env.db_path), ch.SCOPE)
    facts = ch.FakeRunFacts(env.run_id)
    observed = await ch.observe_terminal(env, facts)
    # run_terminal 已导入（其 refs 链接了本轮 turn evidence）→ allowed refs 以此刻的 scope 链接为准。
    refs = ch.scope_evidence_ids(env.db_path)
    revision, _ = ch.head(env.db_path)
    adapter = ch.FakeAdapter([ch.closure_call(ch.mutate_arguments(refs, base_revision=revision))])
    fallback, _invoker = ch.build_fallback(env, facts, adapter)
    settlement = await ch.settle(env, fallback)
    assert settlement.status == "mutate" and settlement.provider_calls == 1
    assert len(adapter.calls) == 1
    request = adapter.calls[0]
    assert [spec.name for spec in request.tools] == ["task_scope_update"]
    observation = json.loads(request.messages[-1].content.split("\n", 1)[1])
    assert observation["staged_final_answer"] == ch.LAST_ANSWER
    assert observation["allowed_evidence_refs"] == refs
    assert observation["task_scope"]["current_revision"] == revision
    request_hash = closure_request_hash(env.run_id, ch.SCOPE, dirty.event_watermark, ch.sha256_text(ch.LAST_ANSWER))
    plan_id = closure_plan_id(request_hash)
    assert ch.receipts(env.db_path)[-1][2:5] == ("mutate", plan_id, "fallback_closure")
    assert ch.attempts(env.db_path) == [(1, "succeeded", None, None, plan_id, 1)]
    assert ch.head(env.db_path)[0] == revision + 1
    assert not (await dirty_state(CanonicalTaskScopeStore(env.db_path), ch.SCOPE)).is_dirty
    await ch.record_terminal(env, observed)
    # 重放（同 owner 再 settle）：已收口，0 调用。
    again = await ch.settle(env, fallback)
    assert again.status == "already_closed" and len(adapter.calls) == 1

    # ② sent_unknown（请求已发出，结果不明）→ pending(closure_attempt_unknown)，终态照常；
    #    重启（新 fallback/invoker 实例）重放 → 绝不重发（Provider 计数不变）。
    env2 = await ch.bound_run(tmp_path / "two", "sdk-run-fb-2")
    await ch.material_write(env2, "e-2")
    adapter2 = ch.FakeAdapter([asyncio.CancelledError()])
    facts2 = ch.FakeRunFacts(env2.run_id)
    observed2 = await ch.observe_terminal(env2, facts2)
    fallback2, _ = ch.build_fallback(env2, facts2, adapter2)
    settlement2 = await ch.settle(env2, fallback2)
    assert settlement2.status == "pending" and settlement2.reason_code == "closure_attempt_unknown"
    assert len(adapter2.calls) == 1
    [(ordinal, status, unknown_class, reason, attempt_plan_id, generation)] = ch.attempts(env2.db_path)
    assert (ordinal, status, unknown_class, reason, generation) == (1, "unknown", "sent_unknown", "closure_attempt_unknown", 1)
    assert isinstance(attempt_plan_id, str) and len(attempt_plan_id) == 64  # 预留时即记录派生 plan_id（reconcile 依据）
    assert ch.receipts(env2.db_path)[-1][2:5] == ("pending", None, "closure_attempt_unknown")
    await ch.record_terminal(env2, observed2)
    restarted, _ = ch.build_fallback(env2, facts2, ch.FakeAdapter([ch.closure_call(ch.mutate_arguments(refs, base_revision=1))]))
    replay = await ch.settle(env2, restarted)
    # 本 Run 已有 durable pending(closure_attempt_unknown) → 重放视为已收口（pending），零调用。
    assert replay.status == "already_closed" and replay.reason_code == "closure_attempt_unknown"
    assert replay.provider_calls == 0 and replay.receipt is not None and replay.receipt.outcome == "pending"
    assert len(ch.attempts(env2.db_path)) == 1  # 无新 attempt
    assert (await dirty_state(CanonicalTaskScopeStore(env2.db_path), ch.SCOPE)).is_dirty  # pending 不清脏

    # ③ timeout（读超时：已发出）→ pending(closure_timeout)；模型拒绝调用（无 tool call）→ pending；
    #    handler 拒绝（refs 不属本 scope）→ pending(reason=拒绝码)；三者 Host 终态都照常提交。
    for suffix, script, reason in (
        ("three", [ProviderTimeoutError(public_message="timeout")], "closure_timeout"),
        ("four", [ch.plain_answer()], "closure_model_declined"),
        ("five", [ch.closure_call(ch.mutate_arguments(["ev-outside"], base_revision=1))], "task_scope_update_refs_outside_scope"),
    ):
        env_n = await ch.bound_run(tmp_path / suffix, f"sdk-run-fb-{suffix}")
        await ch.material_write(env_n, f"e-{suffix}")
        adapter_n = ch.FakeAdapter(script)
        facts_n = ch.FakeRunFacts(env_n.run_id)
        observed_n = await ch.observe_terminal(env_n, facts_n)
        fallback_n, _ = ch.build_fallback(env_n, facts_n, adapter_n)
        settlement_n = await ch.settle(env_n, fallback_n)
        assert (settlement_n.status, settlement_n.reason_code) == ("pending", reason), suffix
        assert len(adapter_n.calls) == 1
        assert ch.receipts(env_n.db_path)[-1][2:5] == ("pending", None, reason)
        assert ch.head(env_n.db_path)[0] == 1  # 零 apply
        await ch.record_terminal(env_n, observed_n)


@pytest.mark.asyncio
async def test_pending_closure_belongs_to_admission_scope_and_next_run_merges(tmp_path: Path) -> None:
    """Task 3（S5B-AC-1③ ⑥）：pending 是 admission scope 的债务。

    Run 1 留下 pending → STATUS 投影显式 ``semantic_closure_pending``；同 scope 的 Run 2 的 snapshot
    注入合并前序 pending 与 material 事件；Run 2 兜底 closure 的 plan 以提交时 head revision 为
    base_revision、refs 引用 Run 1 已链接的 evidence → 一条 receipt 覆盖两个 Run 的水位、脏标记清零、
    STATUS 不再 pending。``force_close_pending``：scope complete/checkpoint 时仍 pending → Host
    ``no_mutation(closure_abandoned, host_forced)`` 零 Provider 调用。
    """
    from deskpet.execution.semantic_closure import (
        closure_instruction_for_run,
        dirty_state,
        force_close_pending,
        pending_receipts,
    )
    from deskpet.task_scope.projections import TaskScopeProjectionStore
    from deskpet.task_scope.store import CanonicalTaskScopeStore
    from tests.sdk_adapters import s5b_closure_harness as ch

    env = await ch.bound_run(tmp_path, "sdk-run-p1")
    await ch.material_write(env, "e-1")
    facts = ch.FakeRunFacts(env.run_id)
    observed = await ch.observe_terminal(env, facts)
    adapter = ch.FakeAdapter([ch.plain_answer()])
    fallback, _ = ch.build_fallback(env, facts, adapter)
    assert (await ch.settle(env, fallback)).status == "pending"
    await ch.record_terminal(env, observed)
    store = CanonicalTaskScopeStore(env.db_path)
    assert [r.reason_code for r in await pending_receipts(store, ch.SCOPE)] == ["closure_model_declined"]

    async def status_view() -> dict:
        views = await TaskScopeProjectionStore(env.db_path).materialize(task_scope_id=ch.SCOPE)
        return json.loads(views["STATUS"].content)

    status = await status_view()
    assert status["semantic_closure_pending"] is True and status["pending_closure_count"] == 1

    # Run 2（同 admission scope）：snapshot 注入合并前序 pending + material 事件（protected 分区，Host 权威）。
    env2 = await ch.next_run(env, "sdk-run-p2")
    await ch.material_write(env2, "e-2", path="b.txt")
    instruction = await closure_instruction_for_run(env2.db_path, env2.run_id)
    assert instruction is not None and instruction.metadata["source"] == "semantic_closure"
    body = json.loads(instruction.content)
    assert body["task_scope_id"] == ch.SCOPE
    assert [p["reason_code"] for p in body["pending_receipts"]] == ["closure_model_declined"]
    assert sorted(e["event_kind"] for e in body["material_events"]) == sorted(
        ["host.file", "harness.tool_invocation", "host.file", "harness.tool_invocation"]
    )
    assert set(body["allowed_evidence_refs"]) == set(ch.scope_evidence_ids(env2.db_path))
    # 未绑定的 Run（或无脏无 pending）不注入。
    assert await closure_instruction_for_run(env2.db_path, "sdk-run-unbound") is None

    # Run 2 兜底：跨 Run plan 的口径 = base_revision 取提交时 head、refs 允许引用 Run 1 的 evidence。
    refs = ch.scope_evidence_ids(env2.db_path)
    revision, _ = ch.head(env2.db_path)
    adapter2 = ch.FakeAdapter([ch.closure_call(ch.mutate_arguments(refs, base_revision=revision))])
    facts2 = ch.FakeRunFacts(env2.run_id)
    observed2 = await ch.observe_terminal(env2, facts2)
    fallback2, _ = ch.build_fallback(env2, facts2, adapter2)
    settlement = await ch.settle(env2, fallback2)
    assert settlement.status == "mutate" and settlement.provider_calls == 1
    dirty = await dirty_state(store, ch.SCOPE)
    assert not dirty.is_dirty
    assert await pending_receipts(store, ch.SCOPE) == ()
    await ch.record_terminal(env2, observed2)
    status = await status_view()
    assert status["semantic_closure_pending"] is False and status["pending_closure_count"] == 0

    # force_close_pending：再留一个 pending，然后 Host 强制 no_mutation(closure_abandoned, host_forced)，零调用。
    env3 = await ch.next_run(env2, "sdk-run-p3", claim_key="claim-3", index=3)
    await ch.material_write(env3, "e-3", path="c.txt")
    facts3 = ch.FakeRunFacts(env3.run_id)
    observed3 = await ch.observe_terminal(env3, facts3)
    adapter3 = ch.FakeAdapter([ch.plain_answer()])
    fallback3, _ = ch.build_fallback(env3, facts3, adapter3)
    assert (await ch.settle(env3, fallback3)).status == "pending"
    await ch.record_terminal(env3, observed3)
    assert (await status_view())["semantic_closure_pending"] is True
    forced = await force_close_pending(env3.db_path, task_scope_id=ch.SCOPE, subject=ch.SUBJECT)
    assert forced is not None and forced.outcome == "no_mutation" and forced.reason_code == "host_forced"
    [(closure_reason,)] = ch.rows(
        env3.db_path, "SELECT json_extract(plan_json,'$.closure_reason') FROM task_scope_mutation_decisions WHERE plan_id=?", forced.plan_id,
    )
    assert closure_reason == "closure_abandoned"
    assert len(adapter3.calls) == 1  # 强制收口零 Provider 调用
    assert not (await dirty_state(store, ch.SCOPE)).is_dirty
    assert (await status_view())["semantic_closure_pending"] is False
    # 幂等：无 pending 时再强制 → None，无新 receipt。
    count = len(ch.receipts(env3.db_path))
    assert await force_close_pending(env3.db_path, task_scope_id=ch.SCOPE, subject=ch.SUBJECT) is None
    assert len(ch.receipts(env3.db_path)) == count


# ---- S5B-AC-2 / Task 4：终态同事务 outbox、analysis executor、幂等 ----
@pytest.mark.asyncio
async def test_terminal_commit_writes_ingestion_outbox_same_tx(tmp_path: Path) -> None:
    """Task 4（S5B-AC-2①）：foreground Run 终态提交在**同一事务**内写 Host sanitized evidence link +
    ``memory_ingestion_outbox(pending)`` 行（含由 durable SdkRunBindingV1 派生的 model_config_hash /
    analysis_lineage）；kill 在写入前后 → 重放收敛；UNIQUE(sdk_run_id, turn_id)；客观 host.file 证据
    留在 TaskScope 账本，不进 analysis 成员集。
    """
    import sqlite3

    from deskpet.execution.foreground_queue import ForegroundQueueStore
    from tests.sdk_adapters import s5b_closure_harness as ch
    from tests.sdk_adapters import s5b_memory_harness as mh

    env = await mh.bound_turn_run(tmp_path, "sdk-run-outbox-1")
    await ch.material_write(env, "e-1", path="README.md")
    facts = ch.FakeRunFacts(env.run_id)
    observed = await ch.observe_terminal(env, facts)
    assert observed is not None
    refs = ch.scope_evidence_ids(env.db_path)
    revision, _ = ch.head(env.db_path)
    fallback, _ = ch.build_fallback(
        env, facts, ch.FakeAdapter([ch.closure_call(ch.mutate_arguments(refs, base_revision=revision))])
    )
    assert (await ch.settle(env, fallback)).status == "mutate"

    # ① kill 在提交前 → 零半状态：无 terminal receipt、无 outbox 行、无 link。
    faulty = ForegroundQueueStore(env.db_path, clock=env.clock, fault_hook=ch.OneShot("terminal.before_commit"))
    with pytest.raises(RuntimeError, match="injected:terminal.before_commit"):
        await faulty.record_sdk_terminal(
            host_run_id=env.admission.host_run_id, sdk_run_id=env.run_id, owner_id=env.admission.owner_id,
            generation=env.admission.generation, terminal_state=observed.terminal_state,
            sdk_event_id=observed.sdk_event_id, sdk_event_hash=observed.sdk_event_hash,
            idempotency_key=f"runtime-terminal:{env.admission.host_run_id}",
            run_binding={**mh.BINDING, "run_id": env.run_id}, endpoint_identity=mh.ENDPOINT,
        )
    assert mh.outbox_rows(env.db_path) == []
    assert mh.rows(env.db_path, "SELECT COUNT(*) FROM memory_ingestion_evidence_links") == [(0,)]
    assert mh.rows(env.db_path, "SELECT COUNT(*) FROM foreground_terminal_receipts") == [(0,)]

    # ② 重放 → terminal receipt + outbox 行 + link 同一事务（同一 now）。
    receipt = await mh.record_terminal(env, observed)
    [(run, turn, state, attempts, ids_json, config_hash, receipt_json, last_error)] = mh.outbox_rows(env.db_path)
    assert (run, turn, state, attempts, receipt_json, last_error) == (env.run_id, env.turn_id, "pending", 0, None, None)
    assert json.loads(ids_json) == [env.evidence_id]
    assert config_hash == mh.expected_model_config_hash()
    [(outbox_id, created_at, lineage_json, envelope_hash)] = mh.rows(
        env.db_path, "SELECT outbox_id,created_at,analysis_lineage_json,envelope_hash FROM memory_ingestion_outbox"
    )
    [(recorded_at,)] = mh.rows(env.db_path, "SELECT recorded_at FROM foreground_terminal_receipts WHERE host_run_id=?", env.admission.host_run_id)
    assert created_at == recorded_at
    assert mh.rows(env.db_path, "SELECT outbox_id,evidence_id FROM memory_ingestion_evidence_links") == [(outbox_id, env.evidence_id)]
    lineage = json.loads(lineage_json)
    assert (lineage["provider_id"], lineage["model_id"], lineage["model_config_hash"]) == (
        mh.BINDING["provider_id"], mh.BINDING["model_id"], config_hash,
    )
    assert lineage["run_binding"]["run_id"] == env.run_id and lineage["endpoint_identity"] == mh.ENDPOINT
    from deskpet.task_scope.protocol import canonical_hash

    assert envelope_hash == canonical_hash({"envelope_hashes": [env.envelope.envelope_hash]})
    # 客观 host.file 证据（run_id = sdk_run_id）不在成员集：TaskScope closure 账本负责它。
    objective = [str(r[0]) for r in mh.rows(env.db_path, "SELECT evidence_id FROM human_memory_evidence WHERE run_id=?", env.run_id)]
    assert objective and not set(objective) & set(json.loads(ids_json))

    # ③ 幂等：再次 record_sdk_terminal → 同 receipt、仍一行；直接违反 UNIQUE(sdk_run_id, turn_id) 被拒。
    assert await mh.record_terminal(env, observed) == receipt
    assert len(mh.outbox_rows(env.db_path)) == 1
    with sqlite3.connect(env.db_path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO memory_ingestion_outbox(outbox_id,host_run_id,sdk_run_id,turn_id,subject,evidence_ids_json,"
            "envelope_hash,model_config_hash,analysis_lineage_json,state,attempts,created_at,updated_at) "
            "VALUES ('dup',?,?,?,?,'[]',?,?,'{}','pending',0,1,1)",
            (env.admission.host_run_id, env.run_id, env.turn_id, mh.SUBJECT, "a" * 64, "b" * 64),
        )


@pytest.mark.asyncio
async def test_analysis_executor_three_key_lookup_zero_second_provider_call(tmp_path: Path) -> None:
    """Task 4（S5B-AC-2②③④ + A1）：outbox worker 复用 Host durable envelope/receipt 调 0.6.1
    ``ingest_committed_evidence``（lineage 绑在 ingest）→ job runner → Host executor 由 durable binding 重建
    adapter 恰一次调用 → proposal 派生/编译 → accepted **并物化**（cognitive head + memory.cognitive.committed）；
    Host commit 后 Memory result 提交前 kill → 重放三键命中 durable envelope，**Provider 调用计数为 0**；
    delivery authority 只认同一 attempt store；下一轮 typed_recall 读到该记忆。
    """
    from simple_harness.runtime import (
        MemoryAnalysisRequest,
        MemoryAnalysisResultEnvelope,
    )

    from deskpet.memory.analysis_executor import HostMemoryAnalysisExecutor
    from tests.sdk_adapters import s5b_closure_harness as ch
    from tests.sdk_adapters import s5b_memory_harness as mh

    env = await mh.bound_turn_run(tmp_path, "sdk-run-analysis-1")
    _, settlement, closure_adapter = await mh.finish_effect_run(env)
    assert settlement.provider_calls == 1 and len(closure_adapter.calls) == 1
    adapter = ch.FakeAdapter([mh.proposal_call([mh.semantic_op(mh.item_id(env), "版本号改成 1.2.0")])])
    menv = mh.memory_env(env, adapter, memory_fault=ch.OneShot("job.result.before_commit"))

    # ① worker：claim → ingest（不重新封装）→ receipt 回写 delivered。
    assert await menv.worker.run_once() == "delivered"
    [(_, _, state, attempts, _, _, receipt_json, _)] = mh.outbox_rows(env.db_path)
    assert (state, attempts) == ("delivered", 1)
    receipts = json.loads(receipt_json)["receipts"]
    assert [r["evidence_id"] for r in receipts] == [env.evidence_id]
    stored = await mh.memory_rows(menv, "SELECT evidence_id,analysis_lineage_json FROM evidence_envelopes")
    assert [r[0] for r in stored] == [env.evidence_id]
    assert json.loads(str(stored[0][1]))["model_config_hash"] == mh.expected_model_config_hash()

    # ② Host attempt succeeded（envelope durable）后、Memory result 提交前 kill。
    with pytest.raises(RuntimeError, match="injected:job.result.before_commit"):
        await mh.run_job(menv)
    assert len(adapter.calls) == 1 and menv.executor.provider_calls == 1
    assert [(o, s, u, has_envelope) for o, s, u, _, _, _, has_envelope in mh.attempts(env.db_path)] == [(1, "succeeded", None, 1)]
    request_json = (await mh.memory_rows(menv, "SELECT request_json FROM analysis_batches"))[0][0]
    request = MemoryAnalysisRequest.from_json(json.loads(str(request_json)))
    assert (request.provider_id, request.model_id, request.model_config_hash) == (
        mh.BINDING["provider_id"], mh.BINDING["model_id"], mh.expected_model_config_hash(),
    )

    # ③ Memory lease（deadline+30）到期后 reclaim → executor 三键命中 → 同 envelope，Provider 计数 0 → APPLIED 且物化。
    env.clock.now += 40.0
    assert await mh.run_job(menv) == "applied"
    assert len(adapter.calls) == 1 and menv.executor.provider_calls == 1
    snapshot = await mh.memory_snapshot(menv)
    assert snapshot["jobs"] == [("applied", 1)] and snapshot["batches"] == [("applied",)]
    assert snapshot["heads"] == 1 and snapshot["decisions"] == 1
    assert ("memory.cognitive.committed", "pending") in snapshot["outbox"]
    assert snapshot["analysis_head"] == [(2,)] and snapshot["cognitive_head"] == [(2,)]
    assert menv.runtime.evidence_authority.resolutions >= 1
    assert await mh.run_job(menv) == "idle"

    # ④ 同一 request 直接重放 executor → durable envelope，零调用；delivery authority 拒绝篡改。
    replay = await menv.executor.analyze_memory(request)
    assert isinstance(replay, MemoryAnalysisResultEnvelope) and len(adapter.calls) == 1
    await menv.executor.verify_analysis_delivery(request, replay)
    tampered = MemoryAnalysisResultEnvelope.from_json(
        {**replay.to_json(), "delivery_receipt": {**replay.delivery_receipt.to_json(), "issuer_id": "someone-else"}}
    )
    with pytest.raises(ValueError):
        await menv.executor.verify_analysis_delivery(request, tampered)
    other = HostMemoryAnalysisExecutor(tmp_path / "other-state.db", adapter_factory=lambda record: adapter)
    with pytest.raises((ValueError, OSError, RuntimeError)):
        await other.verify_analysis_delivery(request, replay)

    # ⑤ 下一轮 typed_recall（memory_standalone lane）读到含 "1.2.0" 的记忆。
    payloads = await mh.recall_payloads(menv, "README")
    assert any("1.2.0" in json.dumps(p, ensure_ascii=False) for p in payloads), payloads
    await mh.close(menv)


def test_analysis_proposal_span_derivation_rejects_paraphrase() -> None:
    """Task 4（design-freeze §9）：``text.find(exact_quote)`` 唯一命中 → UTF-8 byte range / quote_hash /
    pointer ``/text`` / item_ordinal=1；未命中 / 多命中 / 空 / paraphrase → ``analysis_quote_not_found``；
    四类 payload 编译；全部被拒 → ``no_mutation(analysis_all_operations_rejected)``；常量与 schema 冻结。
    """
    import hashlib

    from simple_harness.runtime import (
        EVIDENCE_NORMALIZATION_IDENTITY_UTF8_V1,
        AnalysisBudget,
        EvidenceActorRole,
        EvidenceProvenance,
        EvidenceRef,
        EvidenceSupportKind,
        LongTermMemoryType,
        MemoryAnalysisRequest,
    )
    from simple_harness_memory.core.mutations import compile_memory_mutation_plan

    from deskpet.memory import analysis_proposal as ap
    from deskpet.memory.human_memory_service import build_foreground_turn_evidence
    from tests.sdk_adapters import s5b_memory_harness as mh

    assert (ap.PROMPT_VERSION, ap.RESULT_SCHEMA_VERSION, ap.POLICY_VERSION, ap.VALIDATOR_VERSION) == (
        "host-analysis-prompt/v1", "memory-analysis-proposal/v1", "host-analysis-policy/v1", "host-analysis-validator/v1",
    )
    assert ap.PROPOSAL_TOOL_NAME == "memory_analysis_proposal"
    schema = ap.PROPOSAL_TOOL_SCHEMA
    assert schema["additionalProperties"] is False and schema["required"] == ["outcome", "operations"]
    op_schema = schema["properties"]["operations"]["items"]
    assert op_schema["required"] == ["operation_id", "memory_type", "evidence_item_id", "exact_quote", "reason_code"]
    assert op_schema["properties"]["memory_type"]["enum"] == ["semantic", "episode", "procedure", "prospective"]
    for host_only in ("start_byte", "end_byte", "quote_hash", "envelope_hash", "admission_receipt_id", "base_revision"):
        assert host_only not in json.dumps(schema)

    text = "把 README 里的版本号改成 1.2.0，顺便把 README 的日期也改一下"
    envelope, receipt = build_foreground_turn_evidence(subject=mh.SUBJECT, authority_ref=mh.AUTHORITY_REF, delivery_key="turn-x", text=text)
    item = ap.admitted_item(envelope, receipt)
    assert item.item_id == "turn-x" and item.text == text and item.quotable

    quote = "版本号改成 1.2.0"
    span = ap.derive_span(item, quote, span_id="span-1")
    start = len(text[: text.find(quote)].encode("utf-8"))
    assert (span.start_byte, span.end_byte) == (start, start + len(quote.encode("utf-8")))
    assert text.encode("utf-8")[span.start_byte:span.end_byte].decode("utf-8") == quote
    assert span.quote_hash == hashlib.sha256(quote.encode("utf-8")).hexdigest()
    assert (span.item_json_pointer, span.item_ordinal, span.item_id) == ("/text", 1, "turn-x")
    assert span.normalization_version == EVIDENCE_NORMALIZATION_IDENTITY_UTF8_V1
    assert (span.actor_role, span.provenance, span.support_kind) == (
        EvidenceActorRole.USER, EvidenceProvenance.AUTHENTICATED_USER, EvidenceSupportKind.EXPLICIT_USER_ASSERTION,
    )
    assert (span.envelope_hash, span.sanitized_hash, span.admission_receipt_id, span.admission_receipt_hash) == (
        envelope.envelope_hash, envelope.sanitized_hash, receipt.receipt_id, receipt.receipt_hash,
    )
    for bad, reason in (("版本号改为 1.2.0", "quote_not_verbatim"), ("README", "quote_ambiguous"), ("", "quote_empty"), ("1.2.0 版本", "quote_not_verbatim")):
        with pytest.raises(ap.AnalysisProposalRejected) as rejected:
            ap.derive_span(item, bad, span_id="span-bad")
        assert rejected.value.code == "analysis_quote_not_found" and rejected.value.detail["reason"] == reason

    request = MemoryAnalysisRequest(
        "analysis-batch-test", envelope.run_id, mh.SUBJECT, (EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),),
        ap.PROMPT_VERSION, ap.RESULT_SCHEMA_VERSION, ap.POLICY_VERSION, "provider-1", "model-1", "a" * 64, 1,
        AnalysisBudget(4096, 1024, 30_000, 1_000_000), envelope.disclosure_context, "analysis-batch-test",
    )
    proposal = {
        "outcome": "mutate",
        "operations": [
            mh.semantic_op("turn-x", quote, operation_id="sem"),
            mh.episode_op("turn-x", "把 README 里的版本号改成 1.2.0", operation_id="epi"),
            mh.procedure_op("turn-x", "日期也改一下", operation_id="proc"),
            mh.prospective_op("turn-x", "顺便把 README 的日期也改一下", operation_id="pro"),
            mh.semantic_op("turn-x", "版本号改为 1.2.0", operation_id="paraphrase"),
            mh.semantic_op("unknown-item", quote, operation_id="unknown"),
        ],
    }
    compiled = ap.compile_proposal(proposal, request=request, items=[item], base_revision=1, plan_id="host-analysis-plan-test", now=100.0)
    assert compiled.outcome == "mutate" and compiled.plan is not None, compiled.rejected
    # MemoryMutationPlan 规范序按 operation_id（无依赖时），故按 id 比较而非插入序。
    by_id = {op.operation_id: op for op in compiled.plan.operations}
    assert sorted(by_id) == ["epi", "pro", "proc", "sem"], compiled.rejected
    assert {k: v.memory_type for k, v in by_id.items()} == {
        "sem": LongTermMemoryType.SEMANTIC, "epi": LongTermMemoryType.EPISODE,
        "proc": LongTermMemoryType.PROCEDURE, "pro": LongTermMemoryType.PROSPECTIVE,
    }
    assert [(r.operation_id, r.code) for r in compiled.rejected] == [
        ("paraphrase", "analysis_quote_not_found"), ("unknown", "analysis_quote_not_found"),
    ]
    assert compiled.plan.turn_id == ap.analysis_turn_id(request.job_id)
    assert compiled.plan.run_id == request.run_id and compiled.plan.subject == request.subject
    assert compiled.plan.idempotency_key == request.idempotency_key and compiled.plan.base_revision == 1
    assert compiled.plan.evidence_refs == request.ordered_evidence_refs
    assert len(compile_memory_mutation_plan(compiled.plan).operations) == 4
    assert compiled.structured_result["outcome"] == "mutate"

    only_bad = ap.compile_proposal(
        {"outcome": "mutate", "operations": [mh.semantic_op("turn-x", "版本号改为 1.2.0")]},
        request=request, items=[item], base_revision=1, plan_id="host-analysis-plan-bad", now=100.0,
    )
    assert only_bad.plan is None and only_bad.outcome == "no_mutation"
    assert only_bad.structured_result == {"outcome": "no_mutation", "operations": [], "closure_reason": "analysis_all_operations_rejected"}
    declined = ap.compile_proposal({"outcome": "no_mutation", "operations": []}, request=request, items=[item], base_revision=1, plan_id="p", now=100.0)
    assert declined.plan is None and declined.structured_result["outcome"] == "no_mutation"
    assert ap.compile_proposal(None, request=request, items=[item], base_revision=1, plan_id="p", now=100.0).structured_result["closure_reason"] == "analysis_model_declined"


# ---- S5B-AC-6 / Task 6：composition、cutover ----
@XF
def test_composition_missing_piece_startup_fail_each() -> None:
    raise NotImplementedError


@XF
def test_v46_forward_migration_and_old_runtime_rejects() -> None:
    raise NotImplementedError
