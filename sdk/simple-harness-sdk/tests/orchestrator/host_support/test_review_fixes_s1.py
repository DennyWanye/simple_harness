# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Host support 0.9.8 · code review round 1 fixes (SB-6): with local code execution off the
gateway refuses ``run_tests`` even when a binding lists it (defence in depth).

删旧平面模式 第三刀：P1-1"平面规划器提出带 ``pytest:`` 判据的任务被拒、重规划后完成"那条
平面整圈测试随平面删。
"""

from __future__ import annotations

import asyncio


# ------------------------------------------------------------------ P1-3
def test_the_gateway_refuses_run_tests_even_when_a_binding_lists_it(tmp_path, pytest_spy):
    """Defence in depth: the permission intersection normally removes run_tests first."""

    from agent_orchestrator.artifacts.workspace import WorkspaceManager
    from agent_orchestrator.runtime.tool_gateway import WorkspaceBinding, WorkspaceToolGateway
    from simple_harness.contracts.identity import CallId
    from simple_harness.tools.contracts import ToolCall

    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("attempt-1", seed={"test_ok.py": "def test_ok():\n    assert True\n"})
    gateway = WorkspaceToolGateway(workspaces, local_code_execution=False)
    gateway.bind(
        "run-1",
        WorkspaceBinding(
            attempt_id="attempt-1", view="work", writable=True, allowed_tools=("run_tests",)
        ),
    )
    asyncio.run(gateway.execute(ToolCall(CallId("call-1"), "run_tests", {}), {"run_id": "run-1"}))
    record = gateway.calls[-1]
    assert record["error_code"] == "local_code_execution_disabled"
    assert record["outcome"] == "rejected:policy"
    assert pytest_spy == []


# ------------------------------------------------------------------ P2-5