# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 6 · S6-07 (D6-8): when one budget dimension — tokens, tool calls or wall-clock —
is exhausted, no new Attempt is allocated and the ledger keeps what was spent and what
is still reserved, without double counting."""

from __future__ import annotations

import asyncio

import pytest


# ------------------------------------------------------------------ tool calls


def test_s6_07_the_gateway_enforces_the_reserved_tool_call_cap_per_attempt(tmp_path):
    """§21.1 step 4 at the gateway itself: the SDK's per-turn limit is set to the same cap
    (so a compliant runtime never reaches it) — the gateway is the authority when it does."""

    from agent_orchestrator.artifacts.workspace import WorkspaceManager
    from agent_orchestrator.runtime.tool_gateway import WorkspaceBinding, WorkspaceToolGateway
    from simple_harness.contracts import CallId
    from simple_harness.tools import ToolCall

    workspaces = WorkspaceManager(tmp_path / "ws")
    workspaces.create("m:task-1:attempt-1", seed={"a.md": "x"})
    gateway = WorkspaceToolGateway(workspaces)
    gateway.bind(
        "run-1",
        WorkspaceBinding(
            "m:task-1:attempt-1",
            "work",
            True,
            ("workspace_list", "workspace_read_file"),
            max_tool_calls=2,
        ),
    )

    async def case():
        results = []
        for n in range(3):
            call = ToolCall(call_id=CallId(f"c{n}"), name="workspace_list", arguments={})
            results.append(await gateway.execute(call, {"run_id": "run-1"}))
        return results

    first, second, third = asyncio.run(case())
    assert first.error_code is None and second.error_code is None
    assert third.error_code == "tool_rate_limited"
    assert gateway.executed_calls("run-1") == 2
    assert [c["outcome"] for c in gateway.calls] == [
        "succeeded",
        "succeeded",
        "rejected:rate_limited",
    ]


# ------------------------------------------------------------------ S6-05 (D6-7)
def test_s6_05_the_permission_set_is_the_four_way_intersection_and_nobody_widens_it():
    from agent_orchestrator.governance.policies import DeploymentPolicy, effective_tools

    everything = ("workspace_read_file", "workspace_write_file", "workspace_list", "run_tests")
    worker = everything
    assert (
        effective_tools(
            mission_tools=everything,
            task_tools=everything,
            role_tools=worker,
            deployment=DeploymentPolicy(),
        )
        == everything
    )
    narrow = DeploymentPolicy(allowed_tools=("workspace_read_file", "workspace_list"))
    assert effective_tools(
        mission_tools=everything, task_tools=everything, role_tools=worker, deployment=narrow
    ) == ("workspace_read_file", "workspace_list")
    # a Task or Role naming more than the Mission allows gains nothing
    assert effective_tools(
        mission_tools=("workspace_list",),
        task_tools=everything,
        role_tools=worker,
        deployment=DeploymentPolicy(),
    ) == ("workspace_list",)
    assert effective_tools(
        mission_tools=everything,
        task_tools=everything,
        role_tools=("workspace_read_file",),
        deployment=DeploymentPolicy(),
    ) == ("workspace_read_file",)
    with pytest.raises(ValueError):
        DeploymentPolicy(allowed_tools=("shell",))


def test_s6_05_the_gateway_checks_in_the_21_1_order_and_reports_every_refusal(tmp_path):
    from agent_orchestrator.artifacts.workspace import WorkspaceManager
    from agent_orchestrator.runtime.tool_gateway import WorkspaceBinding, WorkspaceToolGateway
    from simple_harness.contracts import CallId
    from simple_harness.tools import ToolCall

    workspaces = WorkspaceManager(tmp_path / "ws")
    workspaces.create(
        "m:task-1:attempt-1", seed={"a.md": "x", "secrets/key.txt": "k", "up.md": "upstream"}
    )
    workspaces.create("m:task-2:attempt-1", seed={"other.md": "private"})
    gateway = WorkspaceToolGateway(workspaces)
    reported = []
    gateway.on_rejected = lambda run_id, record: reported.append(
        (run_id, record["stage"], record["error_code"])
    )
    gateway.bind(
        "run-1",
        WorkspaceBinding(
            "m:task-1:attempt-1",
            "work",
            True,
            ("workspace_read_file", "workspace_write_file", "workspace_list"),
            max_tool_calls=3,
            protected=("up.md",),
            denied_prefixes=("secrets/",),
        ),
    )

    async def call(name, arguments, run="run-1"):
        return await gateway.execute(
            ToolCall(call_id=CallId(f"c{len(gateway.calls)}"), name=name, arguments=arguments),
            {"run_id": run},
        )

    async def case():
        out = {}
        out["unbound"] = await call("workspace_list", {}, run="run-x")
        out["not_allowed"] = await call(
            "run_tests", {"bogus": 1}
        )  # permission is checked before the schema
        out["schema_missing"] = await call("workspace_read_file", {})
        out["schema_extra"] = await call("workspace_read_file", {"path": "a.md", "mode": "raw"})
        out["schema_type"] = await call("workspace_write_file", {"path": "a.md", "content": 7})
        out["escape"] = await call(
            "workspace_read_file", {"path": "../m:task-2:attempt-1/other.md"}
        )
        out["absolute"] = await call("workspace_read_file", {"path": "/etc/passwd"})
        out["denied"] = await call("workspace_read_file", {"path": "./secrets/key.txt"})
        out["protected"] = await call(
            "workspace_write_file", {"path": "up.md", "content": "rewritten"}
        )
        out["ok_read"] = await call("workspace_read_file", {"path": "up.md"})
        out["ok_list"] = await call("workspace_list", {})
        out["ok_write"] = await call("workspace_write_file", {"path": "new.md", "content": "n"})
        out["rate"] = await call("workspace_list", {})
        return out

    out = asyncio.run(case())
    codes = {k: v.error_code for k, v in out.items()}
    assert codes == {
        "unbound": "tool_not_bound",
        "not_allowed": "tool_not_allowed",
        "schema_missing": "invalid_arguments",
        "schema_extra": "invalid_arguments",
        "schema_type": "invalid_arguments",
        "escape": "workspace_error",
        "absolute": "workspace_error",
        "denied": "policy_denied",
        "protected": "protected_input",
        "ok_read": None,
        "ok_list": None,
        "ok_write": None,
        "rate": "tool_rate_limited",
    }
    listed = out["ok_list"].value["files"]
    assert "secrets/key.txt" not in listed and "a.md" in listed  # denied paths are not even listed
    stages = [stage for _run, stage, _code in reported]
    assert stages == [
        "identity",
        "permission",
        "schema",
        "schema",
        "schema",
        "policy",
        "policy",
        "policy",
        "policy",
        "rate",
    ]
    assert reported[0][0] == "run-x"  # an unbound run is reported too (review P2-5)
    assert all(run == "run-1" for run, _s, _c in reported[1:])
    assert (tmp_path / "ws" / "m:task-1:attempt-1" / "up.md").read_text() == "upstream"


def test_a_reviewer_at_the_cap_is_told_to_conclude_now(tmp_path):
    """2026-09-29 第六局：审阅员查满次数时，拒绝理由明确叫它别再查、马上按格式作答。"""

    from agent_orchestrator.artifacts.workspace import WorkspaceManager
    from agent_orchestrator.runtime.tool_gateway import WorkspaceBinding, WorkspaceToolGateway
    from simple_harness.contracts import CallId
    from simple_harness.tools import ToolCall

    workspaces = WorkspaceManager(tmp_path / "ws")
    workspaces.create("m:task-1:attempt-1", seed={"a.md": "x"})
    gateway = WorkspaceToolGateway(workspaces)
    gateway.bind("run-1", WorkspaceBinding("m:task-1:attempt-1", "work", True,
                                           ("workspace_list",), max_tool_calls=1, review_key="rk"))
    gateway.assurance_review_refusal = lambda _run, _binding: None  # 审阅权限在别处测

    async def case():
        return [await gateway.execute(ToolCall(call_id=CallId(f"c{n}"), name="workspace_list",
                                               arguments={}), {"run_id": "run-1"}) for n in range(2)]

    first, second = asyncio.run(case())
    assert first.error_code is None
    assert second.error_code == "tool_rate_limited"
    assert "立即根据已经看到的证据" in str(second.to_json() if hasattr(second, "to_json") else second)
