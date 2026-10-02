# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""FIRST cap must bind the persisted intent to the actual prepared provider wire."""

import asyncio

import pytest
from test_provider_budget_guard import grants, setup_runtime

from agent_orchestrator.orchestrator.commit_service import Reservation, task_account
from agent_orchestrator.runtime.agent_worker import user_message_json
from agent_orchestrator.runtime.first_request_budget import (
    ProviderInputCap,
    frozen_provider_input_cap,
)
from simple_harness.agents import AgentConfig
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


