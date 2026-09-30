# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Source-only Host wiring: run against the attested editable SDK environment."""

import pytest
from deskpet.orchestration.provider import ProviderSnapshot
from deskpet.orchestration.runtime_profile import source_runtime_options


class Provider:
    async def invoke(self, request, *, cancel):
        raise AssertionError("profile selection must not call the provider")


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
    # 2026-09-30: no legacy pools any more.  An endpoint that is not DeepSeek (nor a
    # declared-compatible relay) gets no certified counter, so no pool at all; the
    # service then refuses with ONLY_DEEPSEEK_REASON.
    options = source_runtime_options(source_config, Provider(), snapshot, native=object())
    assert options == {"profiles": {}}
    assert not source_config.evidence_root.exists()
