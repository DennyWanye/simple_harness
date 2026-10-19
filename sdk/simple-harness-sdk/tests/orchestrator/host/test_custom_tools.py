# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""应用经宿主入口注册自己的只读工具（AgentOS / AIPhone 交接，2026-10-20）：

- 注册时就拒：有副作用的、与内置同名的、重名的；
- 脚本模型真的调用到 handler，拿到 SDK 填的可信上下文（任务号、调用号、当事人、租户）；
- 同步与 async 两种 handler 都行；
- 每次调用在任务事件流里有开始/成功事件，带结构化结果；
- 同一数据目录换了工具集合：宿主照常启动；在途任务再调已删的工具被拒并记事件，任务不崩。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from agent_orchestrator.host import (
    DomainTool,
    HostSettings,
    OrchestrationHost,
    Principal,
    ToolCallContext,
)
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider
from agent_orchestrator.testing.scripted_replies import worker_reply as default_worker
from agent_orchestrator.testing.word_counter import FixtureWordCounter

STATUS_SCHEMA = {
    "type": "object",
    "description": "读一台设备的当前状态（电量、网络）",
    "properties": {"device": {"type": "string", "description": "设备名"}},
    "required": ["device"],
    "additionalProperties": False,
}
APPS_SCHEMA = {
    "type": "object",
    "description": "列出设备上允许使用的应用",
    "properties": {},
    "additionalProperties": False,
}


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _worker_using(tool_name: str, arguments: dict):
    """脚本执行者：先调一次应用的工具，再按默认脚本写文件、交结果。"""

    def worker(request):
        tool_replies = [m for m in request.messages if "tool" in str(m.role).lower()]
        if not tool_replies:
            return (tool_name, arguments)
        # 默认脚本按"工具回复条数"数已写文件数；去掉第一条（我们那次调用）再交给它
        return default_worker(
            replace(request, messages=[m for m in request.messages if m is not tool_replies[0]])
        )

    return worker


def _host(root: Path, *, tools, worker=None, provider=None) -> OrchestrationHost:
    return OrchestrationHost(
        root,
        principal=Principal("host-test-user", "测试用户"),
        provider=provider or LayeredScriptedProvider(worker=worker or default_worker),
        counter=FixtureWordCounter(),
        settings=HostSettings(lease_seconds=5.0),
        require_acceptance=False,
        tools=tools,
    )


def test_registration_refuses_side_effects_collisions_and_duplicates(tmp_path) -> None:
    ok = DomainTool(name="device_status", schema=STATUS_SCHEMA, invoke=lambda a, c: {})
    with pytest.raises(ValueError, match="side effects"):
        _host(
            tmp_path,
            tools=(
                DomainTool(
                    name="send_sms", schema=APPS_SCHEMA, invoke=lambda a, c: {}, read_only=False
                ),
            ),
        )
    with pytest.raises(ValueError, match="built-in"):
        _host(
            tmp_path,
            tools=(DomainTool(name="workspace_list", schema=APPS_SCHEMA, invoke=lambda a, c: {}),),
        )
    with pytest.raises(ValueError, match="twice"):
        _host(tmp_path, tools=(ok, ok))
    with pytest.raises(ValueError, match="DomainTool"):
        _host(tmp_path, tools=(object(),))  # type: ignore[arg-type]


def _run_mission(host: OrchestrationHost, key: str):
    created = host.create_mission(
        {
            "goal": "写一份 NOTES.md，记录设备状态",
            "success_criteria": ["file:NOTES.md"],
            "idempotency_key": key,
        }
    )
    return created["mission_id"]


def test_a_sync_tool_is_called_with_trusted_context_and_shows_up_in_events(tmp_path) -> None:
    seen: list[tuple[dict, ToolCallContext]] = []

    def device_status(arguments, context):
        seen.append((dict(arguments), context))
        return {"device": arguments["device"], "battery": 87, "online": True}

    tool = DomainTool(name="device_status", schema=STATUS_SCHEMA, invoke=device_status)

    async def case():
        host = _host(
            tmp_path / "root",
            tools=(tool,),
            worker=_worker_using("device_status", {"device": "phone-1"}),
        )
        await host.start()
        try:
            assert host.state == "available", host.reason
            assert "device_status" in host.status()["allowed_tools"]
            mission_id = _run_mission(host, "tools-sync-1")
            final = await host.run_until_settled(mission_id, timeout=60.0)
            assert str(final["snapshot"]["mission"]["status"]) == "COMPLETED", final["snapshot"][
                "mission"
            ]
            assert len(seen) == 1
            arguments, context = seen[0]
            assert arguments == {"device": "phone-1"}
            assert context.mission_id == mission_id
            assert context.call_id.count(":") >= 1 and context.attempt_id
            assert context.principal_id == "host-test-user" and context.tenant_id == "local-host"
            rows = host.events(mission_id, limit=200)["events"]
            kinds = [r["type"] for r in rows]
            assert "ExternalToolCallStarted" in kinds and "ExternalToolCallSucceeded" in kinds
            done = next(r for r in rows if r["type"] == "ExternalToolCallSucceeded")
            payload = done["payload"]
            assert payload["tool"] == "device_status" and payload["call_key"] == context.call_id
            assert payload["result"] == {"device": "phone-1", "battery": 87, "online": True}
            assert payload["truncated"] is False and payload["argument_keys"] == ["device"]
        finally:
            await host.close()

    asyncio.run(case())


def test_an_async_tool_runs_on_the_loop_and_a_failure_is_a_failed_event(tmp_path) -> None:
    calls: list[str] = []

    async def app_list(arguments, context):
        await asyncio.sleep(0.01)
        calls.append(context.call_id)
        if len(calls) == 1:
            raise RuntimeError("token=abc-secret")  # 第一次坏掉：事件里只有类型名
        return {"apps": ["camera", "notes"]}

    tool = DomainTool(name="app_list", schema=APPS_SCHEMA, invoke=app_list)

    def worker(request):
        tool_replies = [m for m in request.messages if "tool" in str(m.role).lower()]
        if len(tool_replies) < 2:
            return ("app_list", {})
        return default_worker(
            replace(request, messages=[m for m in request.messages if m not in tool_replies[:2]])
        )

    async def case():
        host = _host(tmp_path / "root", tools=(tool,), worker=worker)
        await host.start()
        try:
            mission_id = _run_mission(host, "tools-async-1")
            final = await host.run_until_settled(mission_id, timeout=60.0)
            assert str(final["snapshot"]["mission"]["status"]) == "COMPLETED"
            assert len(calls) == 2
            rows = host.events(mission_id, limit=200)["events"]
            failed = [r for r in rows if r["type"] == "ExternalToolCallFailed"]
            assert len(failed) == 1
            assert failed[0]["payload"]["error_type"] == "RuntimeError"
            assert "abc-secret" not in json.dumps(failed[0]["payload"], ensure_ascii=False)
            assert any(r["type"] == "ExternalToolCallSucceeded" for r in rows)
        finally:
            await host.close()

    asyncio.run(case())


def test_changing_the_tool_set_of_an_existing_directory_still_starts(tmp_path) -> None:
    """同一目录：有工具 A 时建任务但不跑 → 换成没有 A 重开 → 宿主起来；那次调用被拒成事件，
    任务照旧按脚本写完文件。"""

    tool = DomainTool(name="device_status", schema=STATUS_SCHEMA, invoke=lambda a, c: {"ok": 1})
    worker = _worker_using("device_status", {"device": "phone-1"})

    async def case():
        first = _host(tmp_path / "root", tools=(tool,), worker=worker)
        await first.start()
        try:
            mission_id = _run_mission(first, "tools-change-1")
        finally:
            await first.close()
        second = _host(tmp_path / "root", tools=(), worker=worker)
        await second.start()
        try:
            assert second.state == "available", second.reason
            final = await second.run_until_settled(mission_id, timeout=60.0)
            rows = second.events(mission_id, limit=200)["events"]
            rejected = [r for r in rows if r["type"] == "ToolCallRejected"]
            assert rejected and rejected[0]["payload"]["tool"] == "device_status"
            assert str(final["snapshot"]["mission"]["status"]) in {"COMPLETED", "FAILED"}
        finally:
            await second.close()
        third = _host(tmp_path / "root", tools=(tool,), worker=worker)
        await third.start()
        try:
            assert third.state == "available", third.reason
        finally:
            await third.close()

    asyncio.run(case())


def test_a_read_only_timeout_is_a_failure_and_the_step_finishes(tmp_path) -> None:
    """只读工具超时：交回模型的是失败（tool_timeout），步骤不挂在对账上，任务照常完成。"""

    async def slow(arguments, context):
        await asyncio.sleep(5)
        return {}

    tool = DomainTool(name="app_list", schema=APPS_SCHEMA, invoke=slow, timeout_seconds=0.2)

    async def case():
        host = _host(tmp_path / "root", tools=(tool,), worker=_worker_using("app_list", {}))
        await host.start()
        try:
            mission_id = _run_mission(host, "tools-timeout-1")
            final = await host.run_until_settled(mission_id, timeout=60.0)
            assert str(final["snapshot"]["mission"]["status"]) == "COMPLETED"
            rows = host.events(mission_id, limit=200)["events"]
            failed = [r for r in rows if r["type"] == "ExternalToolCallFailed"]
            assert [r["payload"]["error_code"] for r in failed] == ["tool_timeout"]
        finally:
            await host.close()

    asyncio.run(case())


def test_an_interrupted_read_only_call_reconciles_to_a_failure() -> None:
    """重启后对账：只读工具的调用不留"不确定"，交回一个失败结果；别的工具照旧。"""
    from types import SimpleNamespace

    from agent_orchestrator.runtime.tool_gateway import WorkspaceToolGateway
    from simple_harness.contracts import CallId
    from simple_harness.tools.reconciliation import ReconciliationState

    tool = DomainTool(name="app_list", schema=APPS_SCHEMA, invoke=lambda a, c: {})
    gateway = WorkspaceToolGateway(SimpleNamespace(), domain_tools={"app_list": tool})

    def effect(name):
        return SimpleNamespace(
            tool_name=name,
            run_id=SimpleNamespace(value="run-1"),
            call_id=CallId("c-1"),
            arguments={},
        )

    audited: list[tuple[str, str, dict]] = []
    gateway.on_external_tool = lambda run_id, phase, record: audited.append(
        (run_id, phase, dict(record))
    )
    seen = asyncio.run(gateway.observe(effect("app_list")))
    assert seen.state is ReconciliationState.COMPLETED
    assert seen.result is not None and seen.result.error_code == "tool_interrupted"
    # 事件流给这次调用收尾：同一个 call_key，一条"失败"
    assert [(r, p, a["call_key"], a["error_code"]) for r, p, a in audited] == [
        ("run-1", "failed", "run-1:c-1", "tool_interrupted")
    ]
    other = asyncio.run(gateway.observe(effect("run_tests")))
    assert other.state is ReconciliationState.STILL_UNKNOWN
