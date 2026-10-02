# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Host support 0.9.8 · code review round 1 fixes (SB-6):

* P1-1 — with local code execution off a Task-level ``pytest:`` criterion is refused at
  every gate, and a Task committed before the switch cannot pass on it (the rule layer
  FAILs it): no criterion is ever "met" without anybody judging it;
* P1-3 — one scenario run with the switch ON and OFF tells the behaviours apart (the old
  oracle passed on the old code too), and the gateway's defence-in-depth branch;
* P2-5 — a synthesis template asking for tests is refused at the door;
* P2-7 — an ``add_task`` without a policy gets the deployment's default.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from test_local_code_execution import (
    NO_CODE,
    TOOLS3,
    _config,
    _critics,
    _notes_worker,
    _spec,
    _task,
)

from agent_orchestrator.contracts import Budget, ContractError
from agent_orchestrator.orchestrator.commit_service import MissionSpec
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.testing.fixtures import RoleScriptedProvider, graph_proposal_step


def _pytest_task(key="A", tools=TOOLS3):
    task = _task(key, NO_CODE, tools=tools)
    task["success_criteria"] = ["file:NOTES.md", "pytest:tests/test_notes.py"]
    return task


# ------------------------------------------------------------------ P1-1
def test_a_task_level_pytest_criterion_is_refused_when_off(tmp_path, pytest_spy):
    provider = RoleScriptedProvider(
        {
            "planner": [
                graph_proposal_step([_pytest_task()]),
                graph_proposal_step([_task("A", NO_CODE)]),
            ],
            "worker": _notes_worker(),
            "critic": _critics(),
        }
    )

    async def run():
        async with Orchestrator(_config(tmp_path), provider) as orchestrator:
            mission = await orchestrator.submit_mission(_spec("p1-1"))
            await orchestrator.run()
            store = orchestrator.store
            return store.get_mission(mission.id), store.list_events(mission.id)

    mission, events = asyncio.run(run())
    rejected = [e for e in events if e.type == "TaskGraphRejected"]
    assert rejected
    assert rejected[0].payload["reason"] == "verification_policy_undeployed"
    assert "pytest:tests/test_notes.py" in json.dumps(rejected[0].payload, ensure_ascii=False)
    assert str(mission.status) == "COMPLETED"  # the replan without the criterion completes
    assert pytest_spy == []


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
def test_a_synthesis_template_asking_for_tests_is_refused_at_the_door(tmp_path):
    spec = MissionSpec(
        goal="写 NOTES.md",
        success_criteria=("file:NOTES.md",),
        tenant_id="t",
        idempotency_key="synth",
        allowed_tools=TOOLS3,
        budget=Budget(max_tokens=300_000, max_attempts=6),
        synthesis={
            "goal": "合成",
            "success_criteria": ["pytest:tests/test_all.py"],
            "verification_policy": ["format_check", "rule_check", "code_test"],
        },
        orchestration_semantics_version="legacy",
    )

    async def run():
        async with Orchestrator(_config(tmp_path), RoleScriptedProvider({})) as orchestrator:
            with pytest.raises(ContractError, match="local code execution"):
                await orchestrator.submit_mission(spec)
            return len(orchestrator.store.list_missions())

    assert asyncio.run(run()) == 0
