# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""FIRST cap must bind the persisted intent to the actual prepared provider wire."""

import asyncio
import json

import pytest
from fixtures_provider import (
    RoleScriptedProvider,
    critic_step,
    envelope_step,
    graph_proposal_step,
    package_of,
)
from graph_helpers7 import node, spec
from test_provider_budget_guard import Counter, grants, setup_runtime

from agent_orchestrator.contracts import Budget, MissionStatus
from agent_orchestrator.orchestrator.commit_service import Reservation, task_account
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.agent_worker import user_message_json
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.runtime.first_request_budget import (
    ProviderInputCap,
    frozen_provider_input_cap,
)
from agent_orchestrator.runtime.model_router import RuntimeProfile
from simple_harness.agents import AgentConfig
from simple_harness.agents.context.budget import ContextPolicy
from simple_harness.contracts import RunId


@pytest.mark.parametrize("drift_cap", [False, True])
def test_first_cap_rejects_actual_final_wire_before_admission_or_handoff(tmp_path, drift_cap):
    async def exercise():
        async with setup_runtime(tmp_path, estimate=101) as (
            commit,
            mission,
            task,
            guard,
            provider,
            runtime,
        ):
            agent = await runtime.create(
                AgentConfig(
                    name="first-cap",
                    instructions="Answer briefly.",
                    model_profile_ref="agent.general",
                ),
                creation_key="first-cap",
            )
            context = {
                "schema": 1,
                "fingerprint": "frozen-context",
                "policy": {
                    "max_input_tokens": 100,
                    "max_total_tokens": None,
                    "output_reserve": 4096,
                    "safety_margin": 256,
                    "max_tool_result_tokens": 2048,
                    "tool_result_preview_chars": 1024,
                    "render_slack_tokens": 64,
                },
            }
            cap = frozen_provider_input_cap(
                profile_id="agent.general",
                model="agent-model",
                runtime_context=context,
                estimator_fingerprint=guard.estimator.fingerprint,
            )
            assert isinstance(cap, ProviderInputCap)
            worker, _ = commit.create_attempt(
                task.id,
                role="worker",
                model="agent-model",
                prompt_version="worker-v2",
                context_version="ctx",
                reservation=Reservation(4000, 0),
                critic_tail=Reservation(1100, 0),
                intent_config={
                    "first_critic_budget": {
                        "provider_input_cap": cap.to_json(),
                        "output_ceiling": 1000,
                        "minimum_tokens": 1100,
                        "cost_micros": 0,
                    }
                },
                input_hash="worker-input",
            )
            subject = f"{worker.id}:critic:1"
            intent = commit.create_service_intent(
                kind="critic",
                subject_id=subject,
                mission_id=mission.id,
                account_id=task_account(task.id),
                creation_key=subject,
                input_id="attempt-input",
                input_hash="first-cap-request",
                config={
                    "agent_config": agent.config.to_json(),
                    "message": user_message_json("request"),
                    "provider_admission_fingerprint": guard.fingerprint,
                    "runtime_profile_id": "agent.general",
                    "model": "agent-model",
                    "runtime_context": context,
                    "provider_input_cap": {**cap.to_json(), "max_input_tokens": 101}
                    if drift_cap
                    else cap.to_json(),
                    "provider_output_ceiling": 1000,
                    "provider_first_cost_micros": 0,
                    "attempt_id": worker.id,
                },
                reservation=Reservation(1100, 0),
                task_id=task.id,
                attempt_id=worker.id,
            )
            commit.claim_intent(intent.intent_id, owner="test-owner", lease_seconds=60)
            commit.record_agent_created(
                intent.intent_id,
                agent_id=agent.agent_id,
                expected_turn_id=agent.turn_id_for(intent.input_id),
            )
            result = await agent.ask("request", input_id=intent.input_id, timeout=5)
            assert str(result.state) == "failed" and provider.calls == 0
            assert result.error["detail"]["reason_code"] == (
                "input_cap_identity" if drift_cap else "provider_input_cap_exceeded"
            )
            assert grants(commit) == []
            records = runtime.uow.list_provider_invocations(RunId(agent.run_id))
            assert len(records) == 1 and records[0].handoff_attempt == 0

    asyncio.run(exercise())


def _production_first_provider(*, worker_done=lambda: None, attempts=3, task_tokens=120_000):
    report = "# Finding\nThe fixture report is complete.\n"
    finish = envelope_step(summary="wrote a.md", artifacts=["a.md"], claims=["finding"])

    def finished(request):
        worker_done()
        return finish(request)

    return RoleScriptedProvider(
        {
            "planner": [
                graph_proposal_step(
                    [
                        node(
                            "A",
                            tokens=task_tokens,
                            budget={"max_tokens": task_tokens, "max_attempts": attempts},
                            verification_policy=["format_check", "rule_check", "critic_review"],
                        )
                    ]
                )
            ],
            "worker": [("workspace_write_file", {"path": "a.md", "content": report}), finished],
            "critic": [
                ("workspace_read_file", {"path": "a.md"}),
                critic_step(verdict="PASS", criteria_met=True),
            ]
            * 2,
        }
    )


def _context_profile(
    provider, *, profile_id="default", model="agent-model", input_cap=65_536, price_table=None
):
    return RuntimeProfile(
        profile_id,
        provider,
        model,
        context_policy=ContextPolicy(max_input_tokens=input_cap, render_slack_tokens=0),
        price_table=price_table,
    )


@pytest.mark.parametrize("allowance", [40_000, 100_000])
@pytest.mark.parametrize("worker_input", [1000, 20_000])
def test_system_hold_partitions_first_critic_before_synthesis_worker(
    tmp_path, allowance, worker_input
):
    async def exercise():
        provider = _production_first_provider(task_tokens=100_000)
        # Create real VERIFIED input via an executable assertion, rather than
        # mistaking an unsupported Critic-reviewed claim for reusable knowledge.
        provider.scripts["planner"] = [
            graph_proposal_step(
                [
                    node(
                        "A",
                        success_criteria=["file:a.md", "pytest:tests/test_input.py"],
                        verification_policy=["format_check", "rule_check", "code_test"],
                        budget={"max_tokens": 100_000, "max_attempts": 1},
                    )
                ]
            )
        ]
        provider.scripts["worker"] = [
            ("workspace_write_file", {"path": "a.md", "content": "input"}),
            envelope_step(
                summary="input",
                artifacts=["a.md"],
                claims=["input"],
                override=lambda body: {**body, "evidence": ["a.md", "pytest:tests/test_input.py"]},
            ),
        ]

        def finish_synthesis(request):
            knowledge_ids = [item["id"] for item in package_of(request)["verified_knowledge"]]
            assert knowledge_ids, "synthesis must consume the actual upstream verified result"
            return envelope_step(
                summary="combined verified finding",
                artifacts=["s.md"],
                claims=["synthesis"],
                override=lambda body: {**body, "used_knowledge": knowledge_ids},
            )(request)

        provider.extend(
            "synthesizer",
            [
                ("workspace_read_file", {"path": "a.md"}),
                ("workspace_write_file", {"path": "s.md", "content": "# synthesis\n"}),
                finish_synthesis,
            ],
        )
        provider.scripts["critic"] = [
            ("workspace_read_file", {"path": "s.md"}),
            critic_step(verdict="PASS", criteria_met=True),
        ]
        config = OrchestratorConfig(
            evidence_root=tmp_path / f"system-{allowance}",
            max_concurrency=1,
            candidates_per_task=1,
        )
        async with Orchestrator(
            config,
            profiles={"default": _context_profile(provider)},
            provider_token_estimator=Counter(worker_input),
        ) as orch:
            mission = await orch.submit_mission(
                spec(
                    success_criteria=("file:s.md",),
                    budget=Budget(max_tokens=300_000, max_attempts=12),
                    workspace_seed={
                        "tests/test_input.py": (
                            "from pathlib import Path\n\ndef test_actual_input():\n"
                            "    assert Path('a.md').read_text() == 'input'\n"
                        )
                    },
                    synthesis={
                        "goal": "Combine the actual file using pytest:tests/test_input.py",
                        "success_criteria": ["file:s.md"],
                        "outputs": ["s.md"],
                        "verification_policy": ["format_check", "rule_check", "critic_review"],
                        "budget": {"max_tokens": allowance, "max_attempts": 1},
                    },
                )
            )
            await asyncio.wait_for(orch.run(), 15)
            upstream = next(
                task for task in orch.store.list_tasks(mission.id) if task.kind != "synthesis"
            )
            assert orch.store.list_attempts(upstream.id)
            system = next(
                task for task in orch.store.list_tasks(mission.id) if task.kind == "synthesis"
            )
            hold = orch.commit.system_task_hold(system.id)
            assert hold is not None
            transfers = orch.store.connection.execute(
                "SELECT transfer_id,request_json FROM budget_tail_transfers WHERE hold_id=?",
                (hold["hold_id"],),
            ).fetchall()
            if allowance < 65_536 + 8192:
                assert provider.by_role.get("synthesizer", 0) == 0
                assert provider.by_role.get("critic", 0) == 0
                assert orch.store.list_attempts(system.id) == []
                assert transfers == []
                assert orch.store.get_mission(mission.id).status is not MissionStatus.COMPLETED
            else:
                assert orch.store.get_mission(mission.id).status is MissionStatus.COMPLETED
                [attempt] = orch.store.list_attempts(system.id)
                frozen = orch.store.get_intent_for_subject(attempt.id).config["first_critic_budget"]
                assert frozen["minimum_tokens"] == 65_536 + 8192
                assert len(transfers) == 2
                critic = next(row for row in transfers if ":critic:1" in row["transfer_id"])
                allocation = json.loads(critic["request_json"])["allocations"][0]
                assert allocation["tokens"] == frozen["minimum_tokens"]
                assert allocation["cost_micros"] == frozen["cost_micros"]
                assert provider.by_role["synthesizer"] >= 1
                assert provider.by_role["critic"] >= 1

    asyncio.run(exercise())
