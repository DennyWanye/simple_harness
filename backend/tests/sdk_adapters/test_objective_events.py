# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b Task 2：客观事件映射表（design-freeze §2）、executor / provider 接线。

映射是确定性的（工具名 + 退出码 + 注册 test runner 白名单），禁关键词/正则；
清单 §1 的每个工具都必须有映射（exhaustiveness）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from simple_harness import CallId, RequestId, RunId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import CancelToken, ProviderRequest
from simple_harness.tools import ToolResult

_MESSAGES = (Message(role=MessageRole.USER, content="hi"),)

from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress
from deskpet.sdk_adapters.effect_gate import (
    OBJECTIVE_EVENT_MAP,
    OBJECTIVE_FILE_EVENT,
    OBJECTIVE_TEST_RUNNER_RULE,
    TEST_RUNNER_COMMANDS,
    classify_objective_event,
)
from deskpet.sdk_adapters.provider import ProductProviderInvocationCoordinator
from deskpet.sdk_adapters.tool_authority import PROJECT_EFFECT_TOOL_NAMES
from tests.execution import test_foreground_queue as fq
from tests.sdk_adapters import s5b_effect_gate_harness as h


def _ok(value: object = None) -> ToolResult:
    return ToolResult.succeeded(CallId("c"), value)


# --- 映射表 exhaustiveness ------------------------------------------------------


def test_objective_event_map_is_exhaustive_over_project_effect_list() -> None:
    assert set(OBJECTIVE_EVENT_MAP) == set(PROJECT_EFFECT_TOOL_NAMES)
    for name in PROJECT_EFFECT_TOOL_NAMES:
        assert OBJECTIVE_EVENT_MAP[name] in {OBJECTIVE_FILE_EVENT, OBJECTIVE_TEST_RUNNER_RULE}
    assert {name for name, rule in OBJECTIVE_EVENT_MAP.items() if rule == OBJECTIVE_TEST_RUNNER_RULE} == {
        "run_shell", "process_start",
    }
    # 白名单冻结：{pytest, python -m pytest, npm test, pnpm test, cargo test, go test, vitest, jest}
    assert TEST_RUNNER_COMMANDS == (
        ("pytest",), ("python", "-m", "pytest"), ("npm", "test"), ("pnpm", "test"),
        ("cargo", "test"), ("go", "test"), ("vitest",), ("jest",),
    )
    # 每个工具在成功/失败下都能产出一条 host.* 事件（读取类与未知工具 → None）。
    for name in PROJECT_EFFECT_TOOL_NAMES:
        spec = classify_objective_event(name, {"path": "x.txt", "command": "ls"}, _ok({"ok": True}))
        assert spec is not None and spec.event_kind in {"host.file", "host.test"}
        failed = classify_objective_event(
            name, {"path": "x.txt", "command": "ls"},
            ToolResult.failed(CallId("c"), "tool_failed", "failed"),
        )
        assert failed is not None and failed.payload["outcome"] == "failed"
    assert classify_objective_event("read_file", {"path": "x"}, _ok()) is None
    assert classify_objective_event("context_route", {}, _ok()) is None


def test_test_runner_rule_is_deterministic_by_leading_tokens_and_exit_code() -> None:
    # 首 token ∈ 白名单 且 有退出码 → host.test。
    spec = classify_objective_event("run_shell", {"command": "pytest -q tests"}, _ok({"exit_code": 1}))
    assert spec is not None and spec.event_kind == "host.test"
    assert spec.payload["command_head"] == "pytest" and spec.payload["exit_code"] == 1
    spec = classify_objective_event("run_shell", {"command": "python -m pytest tests/x.py"}, _ok({"returncode": 0}))
    assert spec is not None and spec.event_kind == "host.test" and spec.payload["command_head"] == "python -m pytest"
    spec = classify_objective_event("process_start", {"command": "npm", "args": ["test"]}, _ok({"exit_code": 0}))
    assert spec is not None and spec.event_kind == "host.test" and spec.payload["command_head"] == "npm test"
    # 白名单命中但无退出码 → host.file；非白名单 → host.file；关键词出现在中间不算（无正则/关键词）。
    assert classify_objective_event("run_shell", {"command": "pytest"}, _ok({"ok": True})).event_kind == "host.file"
    assert classify_objective_event("run_shell", {"command": "ls -la"}, _ok({"exit_code": 0})).event_kind == "host.file"
    assert classify_objective_event("run_shell", {"command": "echo pytest"}, _ok({"exit_code": 0})).event_kind == "host.file"
    # 载荷不携带命令全文/参数/输出（只有 command_head + exit_code）。
    spec = classify_objective_event("run_shell", {"command": "pytest -k secret_token"}, _ok({"exit_code": 0, "stdout": "xxx"}))
    assert "secret_token" not in str(spec.payload) and "stdout" not in spec.payload


def test_file_rule_records_targets_only() -> None:
    spec = classify_objective_event(
        "move_file", {"source": "a.txt", "destination": "b/c.txt", "content": "SECRET"}, _ok()
    )
    assert spec is not None and spec.event_kind == "host.file"
    assert spec.payload["targets"] == ["a.txt", "b/c.txt"]
    assert "SECRET" not in str(spec.payload)
    assert classify_objective_event("workspace_prepare", {}, _ok()).payload["targets"] == []


# --- executor 接线：预留在物理 dispatch 之前、提交在 settle 之后 ---------------------


@pytest.mark.asyncio
async def test_executor_reserves_before_dispatch_and_skips_when_effect_absent(tmp_path: Path) -> None:
    env = await h.build_env(tmp_path)
    scope_a, root_a = await h.make_bound_scope(env, "a", "root-a")
    h.freeze_run(env, task_scope_id=scope_a, workspace_root=root_a)
    provider = h.ScriptedProvider(
        [
            h.tool_call("context_route", {"route": "resume_existing", "task_scope_id": scope_a}, raw_id="raw-route"),
            h.tool_call("write_file", {"path": "a.txt", "content": "alpha"}, raw_id="raw-write"),
            h.tool_call("read_file", {"path": "a.txt"}, raw_id="raw-read"),
            h.answer("done"),
        ]
    )
    out = await h.run_capture(env, provider)
    assert out["exception"] is None
    rows = h.rows(
        env.db_path,
        "SELECT r.kind,r.status,r.reserved_at,r.resolved_at,e.event_kind,e.payload_json "
        "FROM harness_evidence_reservations r "
        "LEFT JOIN task_scope_execution_ingest_receipts i ON i.source_event_id=r.source_event_id "
        "LEFT JOIN task_scope_events e ON e.event_id=i.event_id WHERE r.run_id=? ORDER BY r.source_sequence",
        h.RUN.value,
    )
    # 基座无 foreground 绑定：scope 只能来自 PROJECT_EFFECT envelope（write_file 一条；
    # context_route / read_file 的 envelope 不带 scope → 无预留，生产上由 foreground 绑定解析）。
    # 预留在物理 dispatch 前建立、settle 后导入。
    import json as _json

    assert [(kind, status, ek, _json.loads(pj)["public_payload"]["tool_name"]) for kind, status, _r, _s, ek, pj in rows] == [
        ("tool_invocation", "ingested", "harness.tool_invocation", "write_file"),
    ]
    assert all(reserved <= resolved for _k, _s, reserved, resolved, _e, _p in rows)
    # 只有 PROJECT_EFFECT 工具产客观事件：恰一条 host.file。
    host = h.rows(env.db_path, "SELECT event_kind FROM task_scope_events WHERE task_scope_id=? AND source_kind='host'", scope_a)
    assert host == [("host.file",)]
    # 被门拒绝的调用不预留（无 effect）。
    h.freeze_run(env, task_scope_id="another-scope", workspace_root=root_a)
    from simple_harness.tools import ToolCall, ToolContext
    from simple_harness.tools.contracts import CancellationToken

    [record] = env.effects.write_file_calls
    envelope = record["envelope"]
    before = len(h.rows(env.db_path, "SELECT 1 FROM harness_evidence_reservations WHERE run_id=?", h.RUN.value))
    execution = await env.effects.execute(
        effect_id=envelope.effect_id, call=ToolCall(envelope.call_id, "write_file", {"path": "z.txt", "content": "z"}),
        context=ToolContext(h.RUN, h.REQUEST, CancellationToken(), call_id=envelope.call_id, effect_id=envelope.effect_id, task_execution_envelope=envelope),
        execution_lease=h.LEASE, run_fence=h.FENCE, raw_call_id="raw-write", turn_ordinal=2, call_ordinal=0,
    )
    assert execution.result.outcome.value == "rejected"
    assert len(h.rows(env.db_path, "SELECT 1 FROM harness_evidence_reservations WHERE run_id=?", h.RUN.value)) == before


# --- provider 接线：先预留，响应后导入；异常时留 reserved 交 terminal 排空 ----------


class _Coordinator(ProductProviderInvocationCoordinator):
    def __init__(self, *, evidence_ingress, responses) -> None:  # type: ignore[no-untyped-def]
        # bypass the SDK constructor: the hook under test wraps ``invoke`` only.
        self._evidence_ingress = evidence_ingress
        self._responses = list(responses)
        self.invoked: list[str] = []


async def _sdk_invoke(self, run_id, request, *, cancel, execution_lease, workflow_lease=None):  # type: ignore[no-untyped-def]
    self.invoked.append(request.request_id.value)
    item = self._responses.pop(0)
    if isinstance(item, BaseException):
        raise item
    return item


@pytest.mark.asyncio
async def test_provider_coordinator_reserves_then_ingests_and_leaves_reserved_on_failure(
    tmp_path: Path, monkeypatch
) -> None:
    from simple_harness.execution.dispatch import ProviderInvocationCoordinator

    monkeypatch.setattr(ProviderInvocationCoordinator, "invoke", _sdk_invoke)
    run_id = "sdk-run-prov"
    queue_db, _store, _admission = await _bound(tmp_path, run_id)
    ingress = ExecutionEvidenceIngress(queue_db)
    coordinator = _Coordinator(
        evidence_ingress=ingress,
        responses=[h.answer("hi"), RuntimeError("boom")],
    )
    lease = SimpleNamespace(run_id=run_id, namespace="runtime.kernel")
    request = ProviderRequest(RequestId(f"{run_id}:provider-turn:1"), _MESSAGES)
    response = await coordinator.invoke(RunId(run_id), request, cancel=CancelToken(), execution_lease=lease)
    assert response.message.content == "hi"
    rows = fq_rows(queue_db, "SELECT source_event_id,status,kind FROM harness_evidence_reservations WHERE run_id=? ORDER BY source_sequence", run_id)
    assert rows == [(f"provider:{run_id}:provider-turn:1", "ingested", "provider_invocation")]
    [(payload,)] = fq_rows(queue_db, "SELECT e.payload_json FROM task_scope_execution_ingest_receipts r JOIN task_scope_events e ON e.event_id=r.event_id WHERE r.run_id=?", run_id)
    import json

    public = json.loads(payload)["public_payload"]
    assert public["request_id"] == f"{run_id}:provider-turn:1" and public["finish_reason"] == "stop"
    assert "hi" not in payload  # 不记消息内容
    with pytest.raises(RuntimeError):
        await coordinator.invoke(RunId(run_id), ProviderRequest(RequestId(f"{run_id}:provider-turn:2"), _MESSAGES), cancel=CancelToken(), execution_lease=lease)
    rows = fq_rows(queue_db, "SELECT source_event_id,status FROM harness_evidence_reservations WHERE run_id=? ORDER BY source_sequence", run_id)
    assert rows[-1] == (f"provider:{run_id}:provider-turn:2", "reserved")
    # 无 foreground 绑定的 Run：直通，不预留。
    await coordinator.__class__(evidence_ingress=ingress, responses=[h.answer("x")]).invoke(
        RunId("sdk-run-unbound"), ProviderRequest(RequestId("sdk-run-unbound:provider-turn:1"), _MESSAGES), cancel=CancelToken(),
        execution_lease=SimpleNamespace(run_id="sdk-run-unbound", namespace="runtime.kernel"),
    )
    assert fq_rows(queue_db, "SELECT COUNT(*) FROM harness_evidence_reservations WHERE run_id='sdk-run-unbound'") == [(0,)]


async def _bound(tmp_path: Path, run_id: str):
    from deskpet.execution.foreground_queue import ForegroundQueueStore

    queue_db, primary_id, clock = await fq._ready(tmp_path / "queue")
    store = ForegroundQueueStore(queue_db, clock=clock)
    await fq._enqueue(store, primary_id, 1)
    admission = await fq._claim_and_bind(store, sdk_run_id=run_id)
    return queue_db, store, admission


def fq_rows(db_path: Path, sql: str, *params):
    import sqlite3

    with sqlite3.connect(db_path) as db:
        return db.execute(sql, params).fetchall()
