# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""A pool may offer candidate estimators (review 2026-09-24): when a counter identity changed
while the pool kept intents admitted with the older one, the persisted admission identity
picks the matching candidate; nothing is migrated, and a single mismatching estimator is
still refused exactly as before."""

import asyncio

from agent_orchestrator.orchestrator.event_handler import Orchestrator, OrchestratorConfig
from agent_orchestrator.runtime.model_router import RuntimeProfile
from agent_orchestrator.testing.fixtures import MODEL, RoleScriptedProvider


class Counter:
    bound_protocol = "fixture-input-allowance-v1"
    requires_prior_output_reserve = True

    def __init__(self, fingerprint: str) -> None:
        self.fingerprint = fingerprint

    def estimate_input_tokens(self, request):  # type: ignore[no-untyped-def]
        return 100


OLD, NEW = Counter("fixture-counter-released"), Counter("fixture-counter-current")


def _profiles():  # type: ignore[no-untyped-def]
    provider = RoleScriptedProvider({"worker": [("workspace_list", {})] * 4})
    return provider, {"default": RuntimeProfile("default", provider, MODEL, default_max_output_tokens=1000, max_output_tokens_ceiling=1000)}


def _config(tmp_path):  # type: ignore[no-untyped-def]
    return OrchestratorConfig(evidence_root=tmp_path, max_concurrency=1, candidates_per_task=1, attempt_reserve_tokens=4000)


def test_a_pool_without_persisted_intents_takes_the_first_candidate(tmp_path) -> None:
    async def exercise() -> None:
        _, profiles = _profiles()
        async with Orchestrator(_config(tmp_path), profiles=profiles, provider_token_estimators={"default": (NEW, OLD)}) as orch:
            fresh = orch._provider_admissions["default"].fingerprint
        _, profiles = _profiles()
        async with Orchestrator(_config(tmp_path), profiles=profiles, provider_token_estimators={"default": NEW}) as orch:
            assert orch._provider_admissions["default"].fingerprint == fresh

    asyncio.run(exercise())
