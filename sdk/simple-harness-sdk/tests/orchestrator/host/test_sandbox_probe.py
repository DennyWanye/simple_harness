# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""沙箱探测的准入（原桌面端 ``test_frozen_sandbox_admission``）：冻结成单文件的宿主不是
Python 解释器，不拿它探测；源码模式照探测的结论放行或拒绝，并把结论落盘；驱动循环的退避有上限。"""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest
from agent_orchestrator.host import HostSettings, OrchestrationHost, Principal, SandboxOptions
from agent_orchestrator.host.host import backoff_delay
from agent_orchestrator.runtime import sandbox


def host_at(root) -> OrchestrationHost:
    host = OrchestrationHost(root, principal=Principal("p"), settings=HostSettings())
    host.executor = object()
    return host


def test_frozen_backend_never_enters_interpreter_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    def forbidden(_interpreter, **_options):
        pytest.fail("frozen application was treated as a Python CLI")

    monkeypatch.setattr(sandbox, "platform_executor", forbidden)
    host = host_at(tmp_path)
    result = asyncio.run(host._probe_sandbox())
    assert result == {"ok": False, "reason": "frozen_backend_is_not_a_python_interpreter"}
    assert host.executor is None
    assert list(tmp_path.iterdir()) == []


def test_source_mode_retains_interpreter_unavailable_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    seen = []

    def unavailable(interpreter, **_options):
        seen.append(interpreter)
        raise sandbox.SandboxUnavailable("unit interpreter unavailable")

    monkeypatch.setattr(sandbox, "platform_executor", unavailable)
    host = host_at(tmp_path)
    assert asyncio.run(host._probe_sandbox()) == {
        "ok": False,
        "reason": "unit interpreter unavailable",
    }
    assert seen == [sys.executable] and host.executor is None


@pytest.mark.parametrize("passed", [False, True])
def test_source_mode_still_uses_probe_verdict_and_records_it(tmp_path, monkeypatch, passed):
    # 只钉接线：这里不证明真的隔离住了
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    executor = object()
    seen = []

    def make(interpreter, **_options):
        seen.append(interpreter)
        return executor

    payload = {"ok": passed, "reason": "unit_probe_control"}

    async def probe(actual):
        assert actual is executor
        return SimpleNamespace(ok=passed, to_json=lambda: payload)

    monkeypatch.setattr(sandbox, "platform_executor", make)
    monkeypatch.setattr(sandbox, "probe_sandbox", probe)
    host = host_at(tmp_path)
    recorded = {**payload, "kind": None}  # 宿主补上执行器种类（这个替身没有）
    assert asyncio.run(host._probe_sandbox()) == recorded
    assert seen == [sys.executable]
    assert host.executor is (executor if passed else None)
    assert json.loads((tmp_path / "sandbox-probe.json").read_text()) == recorded


def test_the_backoff_delay_is_capped_and_never_overflows():
    assert backoff_delay(1, 60.0) == 2.0
    assert backoff_delay(3, 60.0) == 8.0
    assert backoff_delay(5000, 60.0) == 60.0  # 2.0 ** 5000 会抛 OverflowError
    assert backoff_delay(0, 60.0) == 1.0


def test_options_reach_the_platform_executor(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)  # 给了解释器就不按冻结包拒
    seen = []

    def make(interpreter, **options):
        seen.append((interpreter, options))
        raise sandbox.SandboxUnavailable("unit stop")

    monkeypatch.setattr(sandbox, "platform_executor", make)
    host = OrchestrationHost(
        tmp_path,
        principal=Principal("p"),
        settings=HostSettings(),
        sandbox=SandboxOptions(
            interpreter="/opt/py/bin/python3", extra_read_paths=("/opt/deps",), bwrap="/opt/bwrap"
        ),
    )
    assert asyncio.run(host._probe_sandbox()) == {"ok": False, "reason": "unit stop"}
    assert seen == [
        ("/opt/py/bin/python3", {"extra_read_paths": ("/opt/deps",), "bwrap": "/opt/bwrap"})
    ]


class _ClaimsIsolation(sandbox.ProcessOnlyExecutor):
    """应用自带、自称隔离、其实不隔离的执行器。"""

    kind = "app_claims"
    isolated = True


def test_an_application_executor_is_admitted_only_by_the_probe(tmp_path):
    """准入看行为不看自称：自称隔离的执行器过不了同一套检查，宿主不用它，原因写清楚。"""

    claimed = _ClaimsIsolation()
    host = OrchestrationHost(
        tmp_path,
        principal=Principal("p"),
        settings=HostSettings(),
        sandbox=SandboxOptions(executor=claimed),
    )
    result = asyncio.run(host._probe_sandbox())
    assert result["ok"] is False and result["kind"] == "app_claims"
    assert "read_home" in result["reason"] and "network" in result["reason"]
    assert host.executor is None and not sandbox.probe_passed(claimed)
    policy = SimpleNamespace(code_execution="sandboxed")
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.resolve_executor(policy, claimed)


def test_the_platform_sandbox_passes_and_is_admitted(tmp_path):
    """本机的隔离区（macOS Seatbelt / Linux bubblewrap）：真跑一遍检查，过了才准入。"""

    try:
        executor = sandbox.platform_executor()
    except sandbox.SandboxUnavailable as error:
        pytest.skip(f"no sandbox on this machine: {error}")
    policy = SimpleNamespace(code_execution="sandboxed")
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.resolve_executor(policy, executor)  # 还没检查
    report = asyncio.run(sandbox.probe_sandbox(executor))
    assert report.ok, report.to_json()
    assert sandbox.resolve_executor(policy, executor) is executor


@pytest.mark.parametrize(
    ("lan", "ok"),
    [
        ("ConnectionRefusedError", False),  # 自己的网络命名空间，但连得到宿主局域网地址
        ("TimeoutError", False),
        ("OSError", True),  # 没有路由
        ("PermissionError", True),  # Seatbelt 禁止
    ],
)
def test_a_reachable_lan_address_fails_the_network_check(lan, ok):
    """容器式执行器（有自己的回环、却能出网）连宿主回环会被拒，单凭这几项会误判"断网"；
    连本机局域网地址时网络通就是"连接被拒"或超时，判不通过。"""

    seen = {
        "network_tcp": "ConnectionRefusedError",
        "network_unix": "FileNotFoundError",
        "network_lan": lan,
    }
    items = sandbox._access_items(
        seen, ran=True, escaped=False, home_skeleton=set(), abstract=False, lan=True
    )
    network = next(item for item in items if item["name"] == "network")
    assert network["ok"] is ok
