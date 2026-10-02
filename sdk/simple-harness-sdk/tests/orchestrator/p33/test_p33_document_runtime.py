"""C01/C02/C03/C08: actual SDK dispatch → file tools → record → verify → accept.

Uses a deterministic Provider, real SQLite and CAS. It does not claim real-model
quality, a finished Mission report or native Host UI acceptance (slice G).
"""

import asyncio

import pytest
from doc5_helpers import node
from fixtures_provider import RoleScriptedProvider
from graph_helpers7 import spec

from agent_orchestrator.governance.domains import DOC_DOMAIN
from agent_orchestrator.graph.task_graph import TaskGraphProposal
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig


@pytest.mark.parametrize("target,expected_ok", [(None, False), ("b", False), ("a", True)])
def test_doc_action_criterion_requires_a_checked_matching_candidate(tmp_path, target, expected_ok):
    import json

    from agent_orchestrator.governance.policies import DeploymentPolicy
    from agent_orchestrator.runtime.connectors import TestConfigService

    async def case():
        config = OrchestratorConfig(
            evidence_root=tmp_path / "run",
            deployment_policy=DeploymentPolicy(enabled_connectors=("test_config",)),
        )
        connector = TestConfigService(tmp_path / "service.json")
        async with Orchestrator(
            config, RoleScriptedProvider({}), connectors={"test_config": connector}
        ) as orch:
            mission = await orch.submit_mission(
                spec(
                    domain=DOC_DOMAIN,
                    success_criteria=("action:test_config.set:a", "action:test_config.set:b"),
                )
            )
            planning = orch.commit.begin_planning(mission.id)
            tasks, _ = orch.commit.commit_task_graph(
                mission.id,
                TaskGraphProposal.from_json(
                    {
                        "tasks": [
                            node(
                                "A",
                                outputs=["actions/change.json"],
                                success_criteria=["action:test_config.set:a"],
                            )
                        ]
                    }
                ),
                base_version=planning.version,
                source={"planner": "fixture"},
            )
            workspace = orch.assembled.workspaces.create("fixture-attempt", seed={})
            if target is not None:
                workspace.write_text(
                    "actions/change.json",
                    json.dumps(
                        {
                            "connector": "test_config",
                            "operation": "set",
                            "target": target,
                            "params": {"value": "on"},
                            "reason": "record",
                        }
                    ),
                )
            artifacts = workspace.snapshot(
                mission_id=mission.id, task_id=tasks[0].id, produced_by="worker"
            )
            problems = orch._action_problems(mission, tasks[0], artifacts, workspace)
            assert problems is not None  # absence of a candidate is not a checked PASS
            assert (problems == []) is expected_ok

    asyncio.run(case())
