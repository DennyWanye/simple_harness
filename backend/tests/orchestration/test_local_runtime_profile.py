"""Local shared-window profiles, admission identity and source-only routing."""
import json

import pytest

from deskpet.orchestration.local_profile import LOCAL_PROFILE_ID
from deskpet.orchestration.provider import ProviderSnapshot
from deskpet.orchestration.runtime_profile import source_runtime_options
from deskpet.orchestration.service import OrchestrationService
from deskpet.orchestration.settings import OrchestrationSettings, load_settings


class Counter:
    fingerprint = "fixture-local-template-v1"
    tool_schema_mode = "legacy"
    requires_prior_output_reserve = False
    bound_protocol = "hf-chat-template-v1"

    def __init__(self, *args, **kwargs):
        pass

    def count_text(self, text):
        return len(text)

    def estimate_input_tokens(self, request):
        return 100


class Provider:
    async def invoke(self, request, *, cancel):
        raise AssertionError("configuration must never call a model")


@pytest.fixture
def local_config(tmp_path, monkeypatch):
    import agent_orchestrator.runtime.hf_chat_tokens as counting
    from agent_orchestrator.runtime.assembly import OrchestratorConfig

    monkeypatch.setenv("DESKPET_SDK_RUNTIME_MODE", "editable-source")
    monkeypatch.setattr(counting, "HFChatTokenEstimator", Counter)
    path = tmp_path / "local.json"
    path.write_text(json.dumps({
        "base_url": "http://127.0.0.1:11434/v1", "model": "qwen38-flash-next",
        "max_total_tokens": 262144, "tokenizer_path": str(tmp_path),
        "tokenizer_files": {},
    }))
    snapshot = ProviderSnapshot("local", "http://127.0.0.1:11434/v1",
                                "qwen38-flash-next", "qwen38-flash-next", "fixture")
    return OrchestratorConfig(evidence_root=tmp_path / "store", model=snapshot.requested_model), snapshot, path


def test_shared_window_and_status_default(local_config):
    config, snapshot, path = local_config
    options = source_runtime_options(config, Provider(), snapshot, local_profile_path=str(path))
    assert set(options["profiles"]) == {"default", LOCAL_PROFILE_ID}
    p = options["profiles"][LOCAL_PROFILE_ID]
    assert p.context_policy.max_total_tokens == 262144
    assert p.context_policy.input_budget() == 228352
    assert p.context_policy.input_budget() + p.max_output_tokens_ceiling + p.context_policy.safety_margin == 262144
    assert p.tokenizer is options["provider_token_estimators"][LOCAL_PROFILE_ID]
    assert p.max_concurrent_model_calls == 1
    service = OrchestrationService(config.evidence_root, OrchestrationSettings(), principal=None)
    service._runtime_options = options
    assert service._context_default() == LOCAL_PROFILE_ID
    assert service._context_profiles()[0]["max_total_tokens"] == 262144
    assert service._mission_token_default() == 20_000_000
    assert not config.evidence_root.exists()


@pytest.mark.parametrize("change", [{"model": "deepseek-flash"}, {"base_url": "https://api.deepseek.com"}, {"max_total_tokens": 524288}, {"max_total_tokens": True}])
def test_mismatched_deployment_refused_without_model_call(local_config, change):
    config, snapshot, path = local_config
    raw = json.loads(path.read_text());raw.update(change);path.write_text(json.dumps(raw))
    with pytest.raises(RuntimeError, match="不会回退"):
        source_runtime_options(config, Provider(), snapshot, local_profile_path=str(path))
    assert not config.evidence_root.exists()


def test_settings_preserve_explicit_local_profile_path(tmp_path):
    path = str(tmp_path / "local.json")
    assert load_settings({"local_model_profile": path}).local_model_profile == path
    assert load_settings({"local_model_profile": 22}).local_model_profile == ""
    assert load_settings(None).context_input_tokens == 262144
