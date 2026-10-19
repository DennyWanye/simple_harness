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

from agent_orchestrator.host import HostSettings, OrchestrationHost, Principal
from agent_orchestrator.host.host import backoff_delay
from agent_orchestrator.runtime import sandbox


def host_at(root) -> OrchestrationHost:
    host = OrchestrationHost(root, principal=Principal("p"), settings=HostSettings())
    host.executor = object()
    return host


def test_frozen_backend_never_enters_interpreter_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    def forbidden(_interpreter):
        pytest.fail("frozen application was treated as a Python CLI")

    monkeypatch.setattr(sandbox.SeatbeltExecutor, "for_interpreter", forbidden)
    host = host_at(tmp_path)
    result = asyncio.run(host._probe_sandbox())
    assert result == {"ok": False, "reason": "frozen_backend_is_not_a_python_interpreter"}
    assert host.executor is None
    assert list(tmp_path.iterdir()) == []


def test_source_mode_retains_interpreter_unavailable_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    seen = []

    def unavailable(interpreter):
        seen.append(interpreter)
        raise sandbox.SandboxUnavailable("unit interpreter unavailable")

    monkeypatch.setattr(sandbox.SeatbeltExecutor, "for_interpreter", unavailable)
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

    def make(interpreter):
        seen.append(interpreter)
        return executor

    payload = {"ok": passed, "reason": "unit_probe_control"}

    async def probe(actual):
        assert actual is executor
        return SimpleNamespace(ok=passed, to_json=lambda: payload)

    monkeypatch.setattr(sandbox.SeatbeltExecutor, "for_interpreter", make)
    monkeypatch.setattr(sandbox, "probe_sandbox", probe)
    host = host_at(tmp_path)
    assert asyncio.run(host._probe_sandbox()) == payload
    assert seen == [sys.executable]
    assert host.executor is (executor if passed else None)
    assert json.loads((tmp_path / "sandbox-probe.json").read_text()) == payload


def test_the_backoff_delay_is_capped_and_never_overflows():
    assert backoff_delay(1, 60.0) == 2.0
    assert backoff_delay(3, 60.0) == 8.0
    assert backoff_delay(5000, 60.0) == 60.0  # 2.0 ** 5000 会抛 OverflowError
    assert backoff_delay(0, 60.0) == 1.0
