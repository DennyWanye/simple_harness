"""Shared durable global/profile slots measured at actual SDK provider boundaries."""

from __future__ import annotations

import asyncio
import json

import pytest
from graph_helpers7 import node
from test_provider_budget_guard import Counter, grants

from agent_orchestrator.runtime.model_router import RuntimeProfile
from agent_orchestrator.testing.fixtures import (
    RoleScriptedProvider,
    critic_step,
    envelope_step,
    package_of,
    role_of,
)
from simple_harness.contracts import RunId


class Load:
    def __init__(self):
        self.active = {"workers": 0, "critics": 0}
        self.peak = 0
        self.profile_peak = {"workers": 0, "critics": 0}
        self.calls = []
        self.gates = {key: asyncio.Event() for key in ("A", "B", "D")}
        self.scripts = {}
        for label in ("A", "B", "C", "D", "E"):
            content = f"# Actual report {label}\n"

            def assessed(request, expected=content):
                reads = [json.loads(m.content) for m in request.messages if str(m.role) == "tool"]
                met = reads[-1]["value"]["content"] == expected
                return critic_step(verdict="PASS" if met else "FAIL", criteria_met=met)(request)

            self.scripts[label] = RoleScriptedProvider(
                {
                    "worker": [
                        ("workspace_write_file", {"path": "a.md", "content": content}),
                        envelope_step(summary=content, artifacts=["a.md"], claims=[content]),
                    ],
                    "critic": [("workspace_read_file", {"path": "a.md"}), assessed],
                }
            )

    def provider(self, profile):
        load = self
        scripts = {
            label: RoleScriptedProvider(
                {role: list(steps) for role, steps in scripted.scripts.items()},
                model="worker-model" if profile == "workers" else "critic-model",
            )
            for label, scripted in self.scripts.items()
        }

        class Provider:
            async def invoke(self, request, *, cancel):
                label = package_of(request)["mission_root_goal"]
                role = role_of(request)
                load.active[profile] += 1
                load.peak = max(load.peak, sum(load.active.values()))
                load.profile_peak[profile] = max(load.profile_peak[profile], load.active[profile])
                load.calls.append((profile, label, role, str(request.request_id)))
                try:
                    if (role == "critic" and label in ("A", "B")) or (
                        role == "worker" and label == "D"
                    ):
                        await load.gates[label].wait()
                    return await scripts[label].invoke(request, cancel=cancel)
                finally:
                    load.active[profile] -= 1

        return Provider()


@pytest.mark.parametrize("limit", [True, 0, -1, 1.5])
def test_invalid_profile_slot_limit_is_rejected(limit):
    with pytest.raises(ValueError, match="positive integer"):
        RuntimeProfile("p", Load().provider("workers"), "model", max_concurrent_model_calls=limit)


def test_legacy_unknown_without_profile_identity_blocks_new_profile_admission(tmp_path):
    from graph_helpers7 import graph_service
    from test_provider_budget_guard import ActualProvider, create_bound
    from test_provider_budget_recovery import until

    from agent_orchestrator.orchestrator.commit_service import Reservation
    from agent_orchestrator.runtime.agent_worker import user_message_json
    from agent_orchestrator.runtime.provider_budget_guard import ProviderBudgetGuard
    from simple_harness.agents import AgentConfig, AgentRuntimePorts, build_agent_runtime
    from simple_harness.agents.ports import AllowAllAuthorization
    from simple_harness.providers import (
        ProviderReconciliationObservation,
        ProviderReconciliationState,
    )
    from simple_harness.runtime.consumer_adapter import (
        ConsumerRuntimePolicies,
        _DefaultRuntimeReconciliation,
        _DefaultToolReconciliation,
    )

    class LostResponse(ActualProvider):
        async def invoke(self, request, *, cancel):
            await super().invoke(request, cancel=cancel)
            raise RuntimeError("actual response lost after handoff")

    class Unknown:
        async def observe(self, invocation):
            return ProviderReconciliationObservation(
                ProviderReconciliationState.STILL_UNKNOWN, "lost:" + invocation.invocation_id
            )

    async def exercise():
        commit, _, tasks = graph_service(tmp_path, nodes=[node("A"), node("B")])
        # 2026-09-24: an UNKNOWN call has ended on the wire and no longer holds a slot
        # (its allowance stays spent), so the unattributable *slot* this rule guards is a
        # legacy call still on the wire.
        old_provider, new_provider = ActualProvider(blocked=True), ActualProvider()
        old = ProviderBudgetGuard(commit, owner="test-owner", estimator=Counter(100), max_slots=2)
        new = ProviderBudgetGuard(
            commit,
            owner="test-owner",
            estimator=Counter(100),
            max_slots=2,
            profile_slots={"agent.general": 1},
        )
        policies = ConsumerRuntimePolicies(
            "unpriced_local",
            False,
            "consumer_reconciles",
            tool_reconciliation=_DefaultToolReconciliation(),
            provider_reconciliation=Unknown(),
            runtime_reconciliation=_DefaultRuntimeReconciliation(),
        )

        def ports(name, provider, guard):
            return AgentRuntimePorts(
                provider=provider,
                authorization=AllowAllAuthorization(),
                database_path=str(tmp_path / (name + ".db")),
                provider_admission=guard,
                policies=policies,
                default_max_output_tokens=1000,
            )

        try:
            async with build_agent_runtime(ports("old", old_provider, old)) as previous:
                agent, attempt, key = await create_bound(
                    commit, tasks["A"], old, previous, "legacy"
                )
                receipt = await agent.submit("request", input_id=key)
                intent = commit.store.get_intent_for_subject(attempt.id)
                commit.record_submitted(
                    intent.intent_id, receipt={"turn_id": receipt.turn_id, "seq": receipt.seq}
                )
                await until(
                    lambda: bool(grants(commit)) and grants(commit)[0]["state"] == "HANDED_OFF"
                )
                before = grants(commit)
                assert "runtime_profile_id" not in intent.config
                async with build_agent_runtime(ports("new", new_provider, new)) as current:
                    fresh = await current.create(
                        AgentConfig(
                            name="new",
                            instructions="Answer briefly.",
                            model_profile_ref="agent.general",
                        ),
                        creation_key="new",
                    )
                    _, incoming = commit.create_attempt(
                        tasks["B"].id,
                        role="worker",
                        model="agent-model",
                        prompt_version="worker-v2",
                        context_version="ctx",
                        reservation=Reservation(tokens=4000, cost_micros=0),
                        intent_config={
                            "agent_config": fresh.config.to_json(),
                            "runtime_profile_id": "agent.general",
                            "message": user_message_json("request"),
                            "provider_admission_fingerprint": new.fingerprint,
                        },
                        input_hash="h",
                        candidates_per_task=1,
                    )
                    commit.claim_intent(incoming.intent_id, owner="test-owner", lease_seconds=60)
                    commit.record_agent_created(
                        incoming.intent_id,
                        agent_id=fresh.agent_id,
                        expected_turn_id=fresh.turn_id_for(incoming.input_id),
                    )
                    result = await fresh.ask("request", input_id=incoming.input_id, timeout=5)
                    assert str(result.state) == "failed"
                    assert new_provider.calls == 0 and old_provider.calls == 1
                    records = current.uow.list_provider_invocations(RunId(fresh.run_id))
                    assert len(records) == 1 and records[0].handoff_attempt == 0
                    assert result.error["source_kind"] == "provider_admission"
                    assert result.error["detail"]["reason_code"] == "profile_identity_unknown"
                    assert (
                        grants(commit) == before
                    )  # original in-flight allowance and identity untouched
                    old_provider.allow.set()
        finally:
            commit.store.close()

    asyncio.run(exercise())
