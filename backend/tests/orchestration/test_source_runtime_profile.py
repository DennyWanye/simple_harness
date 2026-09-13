# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Source-only Host wiring: run against the attested editable SDK environment."""

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


def test_source_official_profile_requires_pinned_tokenizer(source_config, monkeypatch):
    monkeypatch.delenv("DESKPET_ORCH_TOKENIZER_PATH", raising=False)
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
    source_config.evidence_root.mkdir()
    source_config.execution_db.touch()  # a pre-identity execution pool stays legacy
    old = source_runtime_options(source_config, Provider(), snapshot)
    assert old["profiles"]["default"].context_policy is None
    assert old["profiles"]["default"].tokenizer is None
    assert "provider_token_estimators" not in old
    assert set(old["profiles"]) == {"default"}


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
