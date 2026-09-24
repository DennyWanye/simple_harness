# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Source-only Host wiring: run against the attested editable SDK environment."""

import asyncio
import os
from pathlib import Path

import pytest
from deskpet.orchestration.provider import ProviderSnapshot
from deskpet.orchestration.runtime_profile import source_runtime_options


class Provider:
    async def invoke(self, request, *, cancel):
        raise AssertionError("profile selection must not call the provider")


def test_wheel_runtime_does_not_load_new_source_ports(monkeypatch):
    monkeypatch.delenv("DESKPET_SDK_RUNTIME_MODE", raising=False)
    assert source_runtime_options(object(), Provider(), None) == {}


@pytest.fixture
def source_config(tmp_path, monkeypatch):
    from agent_orchestrator.runtime import assembly

    if not hasattr(assembly, "resolve_profile_context_policy"):
        pytest.skip(
            "requires the new editable SDK; installed candidate remains separate"
        )
    monkeypatch.setenv("DESKPET_SDK_RUNTIME_MODE", "editable-source")
    return assembly.OrchestratorConfig(
        evidence_root=tmp_path / "evidence", model="deepseek-flash"
    )


def test_source_official_profile_requires_pinned_tokenizer(source_config, monkeypatch, tmp_path):
    monkeypatch.delenv("DESKPET_ORCH_TOKENIZER_PATH", raising=False)
    # ARP: without the variable the deployment's model directory is the fallback; an
    # empty one means no pinned tokenizer anywhere, which must still be refused.
    import paths

    monkeypatch.setattr(paths, "user_models_dir", lambda: str(tmp_path / "no-models"))
    snapshot = ProviderSnapshot(
        "deepseek",
        "https://api.deepseek.com",
        "deepseek-flash",
        "deepseek-flash",
        "unused-local",
    )
    with pytest.raises(RuntimeError, match="DESKPET_ORCH_TOKENIZER_PATH"):
        source_runtime_options(source_config, Provider(), snapshot)
    assert not source_config.evidence_root.exists()


def test_source_uses_same_counter_for_context_and_budget_and_keeps_legacy(
    source_config, monkeypatch
):
    path = os.environ.get("DEEPSEEK_TOKENIZER_PATH")
    if not path:
        pytest.skip("optional pinned tokenizer is not supplied")
    monkeypatch.setenv("DESKPET_ORCH_TOKENIZER_PATH", str(Path(path).resolve()))
    snapshot = ProviderSnapshot(
        "deepseek",
        "https://api.deepseek.com",
        "deepseek-flash",
        "deepseek-flash",
        "unused-local",
    )
    options = source_runtime_options(source_config, Provider(), snapshot)
    profile = options["profiles"]["default"]
    assert profile.tokenizer is options["provider_token_estimators"]["default"]
    assert profile.context_policy.max_input_tokens == 32768
    assert profile.context_policy.max_tool_result_tokens == 16384
    assert not source_config.evidence_root.exists()  # resolver is read-only
    from agent_orchestrator.orchestrator.event_handler import Orchestrator

    async def create_precontext_library():
        # Genuine legacy runtime from its first open, with no context sidecar.
        async with Orchestrator(source_config, Provider()):
            pass

    asyncio.run(create_precontext_library())
    old = source_runtime_options(source_config, Provider(), snapshot)
    assert old["profiles"]["default"].context_policy is None
    assert old["profiles"]["default"].tokenizer is None
    assert old["provider_token_estimators"]["default"] is None
    assert set(old["profiles"]) == {
        "default", "deepseek-context-256k-v1", "deepseek-context-512k-v1",
    }
    assert all(old["provider_token_estimators"][key] is old["profiles"][key].tokenizer
               for key in old["profiles"] if key != "default")


def test_another_endpoint_cannot_acquire_official_deepseek_counter(
    source_config, monkeypatch
):
    monkeypatch.delenv("DESKPET_ORCH_TOKENIZER_PATH", raising=False)
    snapshot = ProviderSnapshot(
        "another",
        "https://example.com",
        "deepseek-flash",
        "deepseek-flash",
        "unused-local",
    )
    options = source_runtime_options(source_config, Provider(), snapshot)
    assert "provider_token_estimator" not in options
    assert (
        options["profiles"]["default"].tokenizer.fingerprint
        == "upper-bound-utf8-bytes-div-2:v1"
    )


@pytest.mark.parametrize("guarded", [False, True])
def test_genuine_precontext_library_distinguishes_frozen_admission_and_adds_long_pools(
    source_config, monkeypatch, guarded,
):
    from agent_orchestrator.orchestrator.commit_service import MissionSpec
    from agent_orchestrator.orchestrator.event_handler import Orchestrator
    from agent_orchestrator.runtime import deepseek_tokens
    from simple_harness.agents.context.tokenizer import UpperBoundTokenizer

    class FixtureCounter(UpperBoundTokenizer):
        fingerprint = "host-lc2-fixture-v1"
        bound_protocol = "host-lc2-fixture-text-v1"
        requires_prior_output_reserve = False

        def estimate_input_tokens(self, request):
            raise AssertionError("Host configuration must never estimate a Provider request")

    counter = FixtureCounter()
    # Import the meter before the estimator is replaced: its counters subclass it.
    from agent_orchestrator.runtime import deepseek_meter  # noqa: F401

    monkeypatch.setattr(deepseek_tokens, "DeepSeekV41TokenEstimator", lambda *a, **kw: counter)
    # ARP (2026-09-24): pools are metered by the certified / relay counters, and a legacy
    # identity pool by the legacy counter — every constructor yields the fixture here.
    from deskpet.orchestration import runtime_profile

    monkeypatch.setattr(runtime_profile, "deepseek_counter_for", lambda *a, **kw: counter)
    monkeypatch.setattr(runtime_profile, "legacy_counter_for", lambda *a, **kw: counter)
    monkeypatch.setenv("DESKPET_ORCH_TOKENIZER_PATH", str(source_config.evidence_root / "fixture.json"))

    async def old_runtime():
        kwargs = {"provider_token_estimators": {"default": counter}} if guarded else {}
        async with Orchestrator(source_config, Provider(), **kwargs) as orch:
            mission = await orch.submit_mission(MissionSpec(
                goal="Write NOTES.md", success_criteria=("file:NOTES.md",),
                tenant_id="lc2", idempotency_key="original",
            ))
            orch.commit.begin_planning(mission.id)
            intent = await orch._create_planner_intent(mission.id, ordinal=1)
            assert intent.config.get("runtime_context") is None
            assert (intent.config.get("provider_admission_fingerprint") is not None) is guarded

    asyncio.run(old_runtime())
    # Hash the closed database: source_runtime_options must remain read-only.
    before = source_config.orchestrator_db.read_bytes()
    snapshot = ProviderSnapshot("deepseek", "https://api.deepseek.com", "deepseek-flash",
                                "deepseek-flash", "unused-local")
    options = source_runtime_options(source_config, Provider(), snapshot)
    assert options["profiles"]["default"].context_policy is None
    assert options["profiles"]["default"].tokenizer is None
    assert options["provider_token_estimators"]["default"] is (counter if guarded else None)
    assert options["profiles"]["deepseek-context-256k-v1"].context_policy.max_input_tokens == 262144
    assert options["profiles"]["deepseek-context-512k-v1"].context_policy.max_input_tokens == 524288
    assert source_config.orchestrator_db.read_bytes() == before
