# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""收口第 4 项：委派工具接回主对话（方案第 2 版）的服务层测试。

真实 SDK 类型（启动快照、子运行启动请求、对账观察），Host 侧依赖用最小替身：
启动走凭证 + DETACHED；同一调用重放不再启动；子运行工具 = 父目录只读子集；
回答取子运行最后一条 assistant 消息；等待超时/父运行结束会取消没结束的子运行；
对账按"父运行 + 调用 ID"推出子运行。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from deskpet.sdk_adapters.delegation import (
    DELEGATED_CHILD_RUN_KIND,
    MAX_CHILDREN_PER_PARENT,
    DelegationRefused,
    ProductDelegationService,
    child_tool_names,
    delegated_child_run_id,
    delegated_tasks,
    is_delegated_child_start,
)
from simple_harness import Message, MessageRole, thaw_json
from simple_harness.runtime.start_snapshot import StartSnapshot

PARENT = "product-sdk-parent"


def _spec(name, effect="read_only", confirm=False):
    return SimpleNamespace(
        name=name, effect_class=effect, confirm_only=confirm, schema_hash=f"h-{name}",
        schema={"name": name, "description": f"{name} tool", "parameters": {"type": "object", "properties": {}}},
    )


SPECS = {
    "web_search": _spec("web_search"),
    "web_fetch": _spec("web_fetch"),
    "file_read": _spec("file_read"),
    "write_file": _spec("write_file", effect="reversible_local"),
    "shell_exec": _spec("shell_exec", effect="destructive", confirm=True),
    "agent": _spec("agent"),
    "await_subagents": _spec("await_subagents"),
    "tool_search": _spec("tool_search"),
    # 清单里标为只读，但会改主对话待办 / 写文件：不在子运行白名单里。
    "todo_write": _spec("todo_write"),
    "download_file": _spec("download_file"),
}


class Authorities:
    def __init__(self):
        self.prepared = {}
        self.terminal = []
        self.listeners = []
        work = SimpleNamespace(task_scope_id=None, workspace_root=None, binding_version=0)
        self.parent = SimpleNamespace(
            run_id=PARENT, session_id="s1", root_run_id="host-root-1", principal_id="sdk-runtime",
            specs=SPECS, dispatch_kinds={name: "standard" for name in SPECS},
            task_work_context=work, catalog_generation=3, catalog_fingerprint="f" * 64,
            workspace_resolution={"kind": "projectless", "effective_root": None, "binding_version": 0},
            run_start_record=lambda: {"inventory": [{"name": name, "effect_class": spec.effect_class}
                                                    for name, spec in SPECS.items()]},
        )

    def add_terminal_listener(self, listener):
        self.listeners.append(listener)

    def resolve(self, run_id):
        if run_id == PARENT:
            return self.parent
        return self.prepared[run_id]

    def prepare_run(self, **raw):
        record = SimpleNamespace(**raw, run_start_record=lambda: {"kind": "child", "run_id": raw["run_id"]},
                                 task_work_context=self.parent.task_work_context)
        self.prepared[raw["run_id"]] = record
        return record

    def mark_terminal(self, run_id, state):
        self.terminal.append((run_id, state))
        record = SimpleNamespace(run_id=run_id)
        for listener in self.listeners:
            listener(record)


class Bindings:
    def __init__(self):
        self.created = {}
        self.terminal = []
        parent = SimpleNamespace(
            session_id="s1", snapshot_id="snap-1", provider_id="p", provider_incarnation_id="i",
            provider_config_revision=1, binding_epoch=2, model_id="m", model_params={}, context_window=64000,
            catalog_generation=3, catalog_fingerprint="f" * 64,
        )
        self.registry = SimpleNamespace(resolve=lambda run_id: parent if run_id == PARENT else None)

    def create_binding(self, **raw):
        binding = SimpleNamespace(**raw, budget_fingerprint="b" * 64, to_record=lambda: dict(raw))
        self.created[raw["run_id"]] = binding
        return binding

    def mark_terminal(self, run_id, state):
        self.terminal.append((run_id, state))


class Uow:
    def __init__(self):
        self.runs = {PARENT: SimpleNamespace(run_id=PARENT, parent_run_id=None, state="running")}
        self.tickets = []
        self.snapshots = {PARENT: StartSnapshot(
            profile_key="agent.general", driver_kind="react", turn_id="t1", tool_catalog_generation=3,
            input={"context_metadata": {"session_id": "s1", "effective_ceiling": 50000}},
            policy_fingerprint="p" * 64, tool_catalog_fingerprint="f" * 64, provider_budget_fingerprint="x" * 64,
        ).to_json()}

    def read_run(self, run_id):
        return self.runs.get(run_id)

    def read_start_snapshot(self, run_id):
        return self.snapshots.get(run_id)

    def issue_profile_launch_ticket(self, ticket, *, now):
        self.tickets.append(ticket)
        return ticket

    unknown: list = []
    resolutions: list = []

    def list_unknown_effects_for_run(self, run_id):
        return [effect for effect in self.unknown if effect.run_id.value == run_id]

    def record_tool_reconciliation(self, record, *, outcome, result, evidence_ref, now):
        self.resolutions.append((record.call_id.value, outcome.value, result))
        self.unknown = [effect for effect in self.unknown if effect is not record]
        return record


class Runtime:
    def __init__(self, uow):
        self.uow = uow
        self.launches = []
        self.cancelled = []
        self.reconciled = 0
        runtime = self

        class Children:
            async def launch(self, request):
                runtime.launches.append(request)
                runtime.uow.runs[request.child_run_id] = SimpleNamespace(
                    run_id=request.child_run_id, parent_run_id=PARENT, state="running")
                runtime.uow.snapshots[request.child_run_id] = thaw_json(request.start_snapshot)

        class Client:
            async def cancel(self, run_id):
                runtime.cancelled.append(run_id.value)
                runtime.uow.runs[run_id.value].state = "cancelled"

        self.children = Children()
        self.client = Client()

    async def reconcile(self):
        self.reconciled += 1


class ContextPort:
    def __init__(self):
        self.answers = {}

    def load(self, run_id):
        text = self.answers.get(run_id.value)
        messages = [Message(MessageRole.USER, "task")]
        if text:
            messages += [Message(MessageRole.ASSISTANT, "draft"), Message(MessageRole.ASSISTANT, text)]
        return SimpleNamespace(messages=messages)


def _service(wait_seconds=5.0, stall_seconds=120.0):
    uow = Uow()
    runtime = Runtime(uow)
    authorities, bindings, context = Authorities(), Bindings(), ContextPort()
    woken: list[str] = []
    service = ProductDelegationService(
        uow_getter=lambda: uow, runtime_getter=lambda: runtime, tool_authorities=authorities,
        binding_resolver=bindings, context_port_getter=lambda: context, wait_seconds=wait_seconds,
        on_parent_woken=woken.append, stall_seconds=stall_seconds,
    )
    return SimpleNamespace(service=service, uow=uow, runtime=runtime, authorities=authorities,
                           bindings=bindings, context=context, woken=woken)


def _ctx(call_id="call-1", run_id=PARENT):
    return SimpleNamespace(run_id=run_id, call_id=call_id)


async def _finish_later(env, answers, delay=0.05):
    await asyncio.sleep(delay)
    for run_id, text in answers.items():
        env.context.answers[run_id] = text
        env.uow.runs[run_id].state = "completed"


def test_task_shapes_and_limits():
    assert [t.prompt for t in delegated_tasks("agent", {"description": "d", "prompt": "查一下"})] == ["查一下"]
    assert len(delegated_tasks("spawn_team", {"task_descriptions": ["a", "b"]})) == 2
    assert [t.task_id for t in delegated_tasks("spawn_subagents", {"subagents": [{"prompt": "x", "task_id": "k"}]})] == ["k"]
    with pytest.raises(DelegationRefused):
        delegated_tasks("agent_parallel", {"subagents": [{"prompt": "x"}] * (MAX_CHILDREN_PER_PARENT + 1)})
    with pytest.raises(DelegationRefused):
        delegated_tasks("agent", {"description": "d", "prompt": "  "})


def test_child_tools_are_the_read_only_subset_without_delegation():
    parent = Authorities().parent
    assert child_tool_names(parent, None) == ("file_read", "web_fetch", "web_search")
    assert child_tool_names(parent, ["web_search"]) == ("web_search",)
    for forbidden in ("agent", "await_subagents", "write_file", "shell_exec", "tool_search", "todo_write",
                      "download_file", "no_such"):
        with pytest.raises(DelegationRefused) as refused:
            child_tool_names(parent, [forbidden])
        assert refused.value.code == "delegation_tool_not_available"


@pytest.mark.asyncio
async def test_agent_launches_detached_child_and_returns_its_last_answer():
    env = _service()
    child_id = delegated_child_run_id(PARENT, "call-1", 0)
    finisher = asyncio.create_task(_finish_later(env, {child_id: "结论：三条"}))
    result = await env.service.delegate("agent", {"description": "d", "prompt": "查三条", "tools": ["web_search"]},
                                        execution_context=_ctx())
    await finisher
    assert result == {"ok": True, "results": [{"task_id": "d", "run_id": child_id, "status": "completed",
                                               "value": "结论：三条", "error": None}]}
    [request] = env.runtime.launches
    assert request.attachment_policy.value == "detached"
    assert [t.ticket_id for t in env.uow.tickets] == [f"{child_id}:ticket"]
    snapshot = StartSnapshot.from_json(env.uow.snapshots[child_id])
    assert snapshot.conversation is None and snapshot.prepared_context is None
    assert snapshot.provider_budget_fingerprint == "b" * 64
    assert snapshot.tool_catalog_fingerprint == "f" * 64
    assert list(snapshot.input["capability_snapshot"]["tools"]) == ["web_search"]
    metadata = snapshot.input["context_metadata"]
    assert metadata["run_kind"] == DELEGATED_CHILD_RUN_KIND and metadata["parent_run_id"] == PARENT
    assert metadata["root_run_id"] == "host-root-1" and metadata["effective_ceiling"] == 0
    assert is_delegated_child_start(env.uow.snapshots[child_id])
    assert not is_delegated_child_start(env.uow.snapshots[PARENT])
    assert [spec["name"] for spec in env.authorities.prepared[child_id].catalog["specs"]] == ["web_search"]
    await asyncio.sleep(0.3)
    # 监视器在子运行结束后释放子运行的授权与模型绑定。
    assert (child_id, "completed") in env.authorities.terminal
    assert (child_id, "completed") in env.bindings.terminal


@pytest.mark.asyncio
async def test_replay_of_the_same_call_does_not_launch_again():
    env = _service()
    child_id = delegated_child_run_id(PARENT, "call-1", 0)
    finisher = asyncio.create_task(_finish_later(env, {child_id: "ok"}))
    first = await env.service.delegate("agent", {"description": "d", "prompt": "p"}, execution_context=_ctx())
    await finisher
    second = await env.service.delegate("agent", {"description": "d", "prompt": "p"}, execution_context=_ctx())
    assert len(env.runtime.launches) == 1 and len(env.uow.tickets) == 1
    assert first["results"][0]["value"] == second["results"][0]["value"] == "ok"


@pytest.mark.asyncio
async def test_spawn_then_await_collects_results_and_foreign_children_are_refused():
    env = _service()
    spawned = await env.service.delegate(
        "spawn_subagents", {"subagents": [{"prompt": "a"}, {"prompt": "b"}]}, execution_context=_ctx())
    ids = spawned["run_ids"]
    assert len(ids) == 2 and len(env.runtime.launches) == 2
    await _finish_later(env, {ids[0]: "A", ids[1]: "B"}, delay=0)
    joined = await env.service.await_subagents(arguments={}, context=_ctx())
    assert [r["value"] for r in joined["results"]] == ["A", "B"]
    env.uow.runs["other-child"] = SimpleNamespace(run_id="other-child", parent_run_id="other-parent", state="completed")
    refused = await env.service.await_subagents(arguments={"run_ids": ["other-child"]}, context=_ctx())
    assert refused["ok"] is False and refused["error_code"] == "delegation_child_not_owned"


@pytest.mark.asyncio
async def test_wait_timeout_cancels_unfinished_children():
    env = _service(wait_seconds=0.3)
    result = await env.service.delegate("agent", {"description": "d", "prompt": "p"}, execution_context=_ctx())
    child_id = delegated_child_run_id(PARENT, "call-1", 0)
    assert result["results"][0]["status"] == "running" and result["results"][0]["error"]
    assert env.runtime.cancelled == [child_id]


@pytest.mark.asyncio
async def test_parent_end_cancels_unfinished_spawned_children():
    env = _service()
    spawned = await env.service.delegate("spawn_subagents", {"subagents": [{"prompt": "a"}]}, execution_context=_ctx())
    env.uow.runs[PARENT].state = "completed"
    env.authorities.mark_terminal(PARENT, "completed")
    await asyncio.sleep(0.05)
    assert env.runtime.cancelled == spawned["run_ids"]


@pytest.mark.asyncio
async def test_app_shutdown_keeps_children_running():
    """关闭应用：Host 释放父运行授权、正在等待的调用被中断，但父运行没有结束 → 子运行不取消。"""

    env = _service()
    await env.service.delegate("spawn_subagents", {"subagents": [{"prompt": "a"}]}, execution_context=_ctx("c1"))
    env.authorities.mark_terminal(PARENT, "failed")
    call = asyncio.create_task(
        env.service.delegate("agent", {"description": "d", "prompt": "p"}, execution_context=_ctx("c2")))
    await asyncio.sleep(0.1)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert env.runtime.cancelled == []


@pytest.mark.asyncio
async def test_user_stop_cancels_the_children_of_the_interrupted_call():
    env = _service()
    call = asyncio.create_task(
        env.service.delegate("agent", {"description": "d", "prompt": "p"}, execution_context=_ctx("c2")))
    await asyncio.sleep(0.1)
    env.uow.runs[PARENT].state = "cancel_requested"
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert env.runtime.cancelled == [delegated_child_run_id(PARENT, "c2", 0)]


@pytest.mark.asyncio
async def test_refusals_launch_nothing():
    env = _service()
    refused = await env.service.delegate("agent", {"description": "d", "prompt": "p", "tools": ["write_file"]},
                                         execution_context=_ctx())
    assert refused["ok"] is False and refused["error_code"] == "delegation_tool_not_available"
    assert env.runtime.launches == [] and env.bindings.created == {}


@pytest.mark.asyncio
async def test_child_limit_per_parent():
    env = _service()
    await env.service.delegate("spawn_subagents", {"subagents": [{"prompt": str(i)} for i in range(6)]},
                               execution_context=_ctx("call-1"))
    refused = await env.service.delegate("spawn_subagents", {"subagents": [{"prompt": "x"}] * 3},
                                         execution_context=_ctx("call-2"))
    assert refused["error_code"] == "delegation_child_limit" and len(env.runtime.launches) == 6


@pytest.mark.asyncio
async def test_reconciliation_derives_children_from_the_call():
    from simple_harness.tools.reconciliation import ReconciliationState

    env = _service()
    from simple_harness import CallId, RunId

    effect = SimpleNamespace(tool_name="agent", run_id=RunId(PARENT), call_id=CallId("call-9"),
                             arguments={"description": "d", "prompt": "p"})
    assert (await env.service.observe(SimpleNamespace(tool_name="web_search"))) is None
    # 子运行还没启动就被打断：判"已完成、失败"，让模型重新调用（从不判"确认未开始"——
    # 重启后重新授权会撞 Host 授权记录的身份冲突）。
    absent = await env.service.observe(effect)
    assert absent.state is ReconciliationState.COMPLETED
    assert absent.result.error_code == "delegation_interrupted"
    child_id = delegated_child_run_id(PARENT, "call-9", 0)
    env.uow.runs[child_id] = SimpleNamespace(run_id=child_id, parent_run_id=PARENT, state="running")
    # 子运行还在跑：仍不明（父运行挂起），同时接管监视，子运行一结束就替父运行结算。
    running = await env.service.observe(effect)
    assert running.state is ReconciliationState.STILL_UNKNOWN
    assert child_id in env.service._children[PARENT]
    env.uow.runs[child_id].state = "completed"
    env.context.answers[child_id] = "答"
    settled = await env.service.observe(effect)
    assert settled.state is ReconciliationState.COMPLETED
    assert settled.result.value["results"][0]["value"] == "答"


def test_child_usage_is_recorded_but_never_counts_as_chat_context_usage():
    from deskpet.sdk_adapters.provider_projection import ProviderProjectionEnvelopeV1
    from deskpet.sdk_adapters.provider_projection_pump import ProviderProjectionContextV1

    async def launch():
        env = _service(wait_seconds=0.1)
        await env.service.delegate("spawn_subagents", {"subagents": [{"prompt": "a"}]}, execution_context=_ctx())
        return env

    env = asyncio.run(launch())
    [child_id] = [run_id for run_id in env.uow.snapshots if run_id != PARENT]
    context = ProviderProjectionContextV1.from_value(env.uow.snapshots[child_id]["input"]["context_metadata"])
    envelope = ProviderProjectionEnvelopeV1.from_value({
        "invocation_id": "inv-1", "settlement_version": 1, "settled_at": 1.0, "state": "succeeded",
        "provider_id": "p", "model_id": "m", "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        **{key: getattr(context, key) for key in ("session_id", "root_run_id", "request_id", "snapshot_id",
                                                  "binding_epoch", "context_window", "effective_ceiling")},
    })
    assert envelope.to_record()["usage"]["total_tokens"] == 12
    assert envelope.has_trusted_usage is False


@pytest.mark.asyncio
async def test_interrupted_await_settles_from_the_named_children():
    from simple_harness import CallId, RunId
    from simple_harness.tools.reconciliation import ReconciliationState

    env = _service()
    child_id = "delegate-x"
    env.uow.runs[child_id] = SimpleNamespace(run_id=child_id, parent_run_id=PARENT, state="running")
    effect = SimpleNamespace(tool_name="await_subagents", run_id=RunId(PARENT), call_id=CallId("w1"),
                             arguments={"run_ids": [child_id]})
    assert (await env.service.observe(effect)).state is ReconciliationState.STILL_UNKNOWN
    env.uow.runs[child_id].state = "completed"
    env.context.answers[child_id] = "收回"
    settled = await env.service.observe(effect)
    assert settled.state is ReconciliationState.COMPLETED
    assert settled.result.value["results"][0]["value"] == "收回"


@pytest.mark.asyncio
async def test_whole_batch_is_checked_before_any_child_starts():
    env = _service()
    refused = await env.service.delegate(
        "agent_parallel", {"subagents": [{"prompt": "a"}, {"prompt": "b", "tools": ["todo_write"]}]},
        execution_context=_ctx())
    assert refused["error_code"] == "delegation_tool_not_available"
    assert env.runtime.launches == [] and env.uow.tickets == []


@pytest.mark.asyncio
async def test_watcher_retries_a_failed_read_and_still_releases_the_child():
    env = _service()
    await env.service.delegate("spawn_subagents", {"subagents": [{"prompt": "a"}]}, execution_context=_ctx())
    child_id = delegated_child_run_id(PARENT, "call-1", 0)
    real_read, failures = env.uow.read_run, []

    def flaky(run_id):
        if run_id == child_id and not failures:
            failures.append(run_id)
            raise RuntimeError("database is locked")
        return real_read(run_id)

    env.uow.read_run = flaky
    env.uow.runs[child_id].state = "completed"
    await asyncio.sleep(0.8)
    assert failures == [child_id]
    assert (child_id, "completed") in env.authorities.terminal
    assert (child_id, "completed") in env.bindings.terminal


@pytest.mark.asyncio
async def test_after_restart_replaying_the_call_reattaches_and_waits():
    """重启后：恢复阶段接管还在跑的子运行，SDK 重新执行这次 agent 调用 → 不再启动，等到结论。"""

    env = _service()
    child_id = delegated_child_run_id(PARENT, "call-7", 0)
    env.uow.runs[child_id] = SimpleNamespace(run_id=child_id, parent_run_id=PARENT, state="running")
    env.service.adopt_recovered(child_id, PARENT)
    finisher = asyncio.create_task(_finish_later(env, {child_id: "重启后的结论"}, delay=0.2))
    result = await env.service.delegate("agent", {"description": "d", "prompt": "p"},
                                        execution_context=_ctx("call-7"))
    await finisher
    assert env.runtime.launches == [] and env.uow.tickets == []
    assert result["results"][0]["value"] == "重启后的结论"


def _unknown_agent_call(call_id):
    from simple_harness import CallId, RunId

    return SimpleNamespace(tool_name="agent", run_id=RunId(PARENT), call_id=CallId(call_id),
                           arguments={"description": "d", "prompt": "p"})


@pytest.mark.asyncio
async def test_waiting_parent_is_settled_and_woken_when_its_child_ends():
    """重启前停在 agent 调用上的父运行（等待中、调用结果不明）：子运行结束 → 写对账结论 → 唤醒。"""

    env = _service()
    child_id = delegated_child_run_id(PARENT, "call-3", 0)
    env.uow.runs[child_id] = SimpleNamespace(run_id=child_id, parent_run_id=PARENT, state="running")
    env.uow.runs[PARENT].state = "waiting"
    env.uow.unknown = [_unknown_agent_call("call-3")]
    env.uow.resolutions = []
    env.service.adopt_recovered(child_id, PARENT)
    await _finish_later(env, {child_id: "答案"}, delay=0.05)
    await asyncio.sleep(0.5)
    [(call_id, outcome, result)] = env.uow.resolutions
    assert (call_id, outcome) == ("call-3", "completed")
    assert result.value["results"][0]["value"] == "答案"
    assert env.runtime.reconciled == 1
    # 唤醒后重新挂上主对话的恢复展示，结论才能写回会话。
    assert env.woken == [PARENT]


@pytest.mark.asyncio
async def test_startup_settle_waits_for_running_children_then_completes():
    env = _service()
    child_id = delegated_child_run_id(PARENT, "call-4", 0)
    env.uow.runs[child_id] = SimpleNamespace(run_id=child_id, parent_run_id=PARENT, state="running")
    env.uow.runs[PARENT].state = "waiting"
    env.uow.unknown = [_unknown_agent_call("call-4")]
    env.uow.resolutions = []
    # 子运行还在跑：不结算（也不重放调用），由接管的监视器在子运行结束时结算。
    assert await env.service.settle_waiting_parent(PARENT) is False
    assert env.uow.resolutions == [] and env.runtime.reconciled == 0
    await _finish_later(env, {child_id: "晚到的结论"}, delay=0)
    await asyncio.sleep(0.6)
    [(call_id, outcome, result)] = env.uow.resolutions
    assert (call_id, outcome) == ("call-4", "completed")
    assert result.value["results"][0]["value"] == "晚到的结论"
    assert env.runtime.reconciled == 1
    # 父运行不在等待（正常路径：它自己正在等）→ 什么都不做。
    env.uow.runs[PARENT].state = "running"
    env.uow.unknown = [_unknown_agent_call("call-5")]
    assert await env.service.settle_waiting_parent(PARENT) is False


def test_waiting_parents_held_by_a_delegate_call_join_the_restart_recovery_list():
    import sqlite3

    from deskpet.sdk_adapters.delegation import waiting_runs_blocked_on_delegation

    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE runs(run_id TEXT, state TEXT);
        CREATE TABLE execution_effects(run_id TEXT, tool_name TEXT, state TEXT);
        INSERT INTO runs VALUES ('held', 'waiting'), ('running', 'running'), ('other', 'waiting'),
                                ('done', 'waiting');
        INSERT INTO execution_effects VALUES ('held', 'agent', 'unknown'), ('running', 'agent', 'unknown'),
                                             ('other', 'web_fetch', 'unknown'), ('done', 'agent', 'succeeded');
    """)
    uow = SimpleNamespace(database=SimpleNamespace(connection=connection),
                          read_run=lambda run_id: SimpleNamespace(run_id=run_id))
    assert [r.run_id for r in waiting_runs_blocked_on_delegation(uow)] == ["held"]
    assert waiting_runs_blocked_on_delegation(uow, exclude={"held"}) == ()


@pytest.mark.asyncio
async def test_a_child_stuck_waiting_is_reconciled_once_then_cancelled():
    """子运行的模型调用结果不明而挂在等待：先请内核对账一次，仍不动就取消，父运行拿到"已取消"的结果。"""

    env = _service(wait_seconds=10.0, stall_seconds=0.3)
    child_id = delegated_child_run_id(PARENT, "call-1", 0)

    async def park():
        await asyncio.sleep(0.05)
        env.uow.runs[child_id].state = "waiting"

    parker = asyncio.create_task(park())
    result = await env.service.delegate("agent", {"description": "d", "prompt": "p"}, execution_context=_ctx())
    await parker
    assert env.runtime.reconciled == 1
    assert env.runtime.cancelled == [child_id]
    assert result["results"][0]["status"] == "cancelled" and result["results"][0]["error"]
