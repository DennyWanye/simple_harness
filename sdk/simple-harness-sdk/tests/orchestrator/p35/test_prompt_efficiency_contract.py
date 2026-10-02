# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Frozen history and actual code-domain requests.

The scripted transports establish assembly and verification contracts, not that a
real model follows the efficiency instructions or that P34 fits its fixed budget.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from graph_helpers7 import node, spec

from agent_orchestrator.contracts import MissionStatus
from agent_orchestrator.governance import domains
from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.runtime.role_templates import WORKER, template_for_domain
from agent_orchestrator.testing.fixtures import (
    RoleScriptedProvider,
    critic_step,
    envelope_step,
    graph_proposal_step,
    package_of,
    role_of,
)
from simple_harness.contracts import canonical_json

FILE_TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list")


def _digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _assert_request_binding(orch, provider, role, expected_tools):
    template = template_for_domain(
        {"worker": WORKER}[role], domains.CODE_PROFILE, {}
    )
    assert template.prompt_version == f"{role}-code-observation-v3"
    assert "不能推出任意业务性质或其他版本仍然正确" in template.instructions
    assert {"knowledge_list", "knowledge_read"} <= set(template.tool_names)
    requests = [request for request in provider.requests if role_of(request) == role]
    assert requests
    for request in requests:
        package = package_of(request)
        intent = orch.store.get_intent_for_subject(package["attempt"]["attempt_id"])
        assert intent.config["prompt_version"] == template.prompt_version
        assert intent.config["agent_config"]["instructions"] == template.instructions
        assert [m.content for m in request.messages if str(m.role) == "system"] == [
            template.instructions
        ]
        assert {tool.name for tool in request.tools} == set(expected_tools)
        assert set(intent.config["agent_config"]["tool_names"]) == set(expected_tools)
    return requests


@pytest.mark.parametrize("deployment_narrows", [False, True])
def test_new_worker_reaches_actual_code_request_with_only_exposed_tools(
    tmp_path, deployment_narrows
):
    # In the second case the Task still permits run_tests: actual schemas must
    # narrow that upper bound, without changing the Task or executing pytest.
    task_tools = (*FILE_TOOLS, "run_tests") if deployment_narrows else FILE_TOOLS
    deployment = (
        DeploymentPolicy(allowed_tools=FILE_TOOLS, local_code_execution=False)
        if deployment_narrows else DeploymentPolicy()
    )
    provider = RoleScriptedProvider({
        "planner": [graph_proposal_step([node(
            "A", tokens=60_000, allowed_tools=list(task_tools),
            verification_policy=["format_check", "rule_check", "critic_review"],
        )])],
        "worker": [
            ("workspace_write_file", {"path": "a.md", "content": "fixture artifact\n"}),
            envelope_step(
                summary="artifact written", artifacts=["a.md"], claims=["artifact exists"]
            ),
        ],
        "critic": [critic_step(verdict="PASS", criteria_met=True)],
    })

    async def exercise():
        async with Orchestrator(OrchestratorConfig(
            evidence_root=tmp_path, max_concurrency=1, deployment_policy=deployment,
        ), provider) as orch:
            mission = await orch.submit_mission(spec(success_criteria=("file:a.md",)))
            await asyncio.wait_for(orch.run(), 30)
            assert orch.commit.domain_for(mission.id).id == "code-v1"
            assert orch.store.get_mission(mission.id).status is MissionStatus.COMPLETED
            requests = _assert_request_binding(orch, provider, "worker", FILE_TOOLS)
            assert len(requests) == 2
            for request in requests:
                package = package_of(request)
                assert package["task_contract"]["success_criteria"] == ["file:a.md"]
                assert set(package["tools_and_permissions"]["allowed_tools"]) == set(task_tools)
            [task] = orch.store.list_tasks(mission.id)
            rows = orch.store.list_verifications(task.accepted_result_id)
            assert any(row["layer"] == "critic_review" and row["status"] == "PASS" for row in rows)

    asyncio.run(exercise())

