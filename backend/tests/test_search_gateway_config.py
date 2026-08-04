from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from config import ConfigError, load_config, _reset_load_config_cache


@pytest.fixture(autouse=True)
def reset_config_cache():
    _reset_load_config_cache()
    yield
    _reset_load_config_cache()


def _load(tmp_path: Path, body: str):
    path = tmp_path / "config.toml"
    path.write_text(dedent(body), encoding="utf-8")
    return load_config(path)


def test_search_gateway_factory_defaults_on_without_external_service(tmp_path):
    cfg = _load(tmp_path, "")
    assert cfg.search_gateway.enabled is True
    assert cfg.search_gateway.providers == ["baidu", "duckduckgo", "google-cdp", "bing-cdp"]
    assert cfg.search_gateway.searxng_url == ""
    assert cfg.search_gateway.circuit_blocked_threshold == 1
    assert cfg.search_gateway.circuit_captcha_open_s == 600.0
    assert cfg.search_gateway.circuit_rate_limit_retry_after_max_s == 300.0
    assert cfg.search_gateway.provider_max_concurrency == 1
    assert cfg.search_gateway.provider_queue_max_wait_s == 8.0
    assert cfg.search_gateway.empty_rescue_enabled is True
    assert cfg.search_gateway.empty_rescue_max_per_request == 1
    assert cfg.workflows.deep_research_version == "v7"
    assert cfg.search_gateway.playwright_renderer_enabled is True


def test_research_v5_runtime_budgets_are_clamped_without_exposing_policy(tmp_path):
    cfg = _load(tmp_path, """
        [research_v5]
        soft_checkpoint_seconds = 1
        lease_seconds = 9999
        auto_cap_seconds = 2
        plateau_rounds = 99
        llm_input_token_budget = 1
        llm_output_token_budget = 9999999
        llm_cost_budget_micros = -5
        io_operation_budget = 1
    """)
    assert cfg.research_v5.soft_checkpoint_seconds == 30
    assert cfg.research_v5.lease_seconds == 600
    assert cfg.research_v5.auto_cap_seconds == 600
    assert cfg.research_v5.plateau_rounds == 10
    assert cfg.research_v5.llm_input_token_budget == 1000
    assert cfg.research_v5.llm_output_token_budget == 500000
    assert cfg.research_v5.llm_cost_budget_micros == 0
    assert cfg.research_v5.io_operation_budget == 8
    assert not hasattr(cfg.research_v5, "evidence_threshold")


def test_legacy_alias_only_overrides_when_explicit(tmp_path):
    cfg = _load(tmp_path, """
        [research]
        search_engines = ["duckduckgo"]
        searxng_url = "http://localhost:8080/search"
    """)
    assert cfg.search_gateway.providers == ["searxng", "duckduckgo"]
    cfg = _load(tmp_path, """
        [research]
        search_engines = ["duckduckgo"]
        [search_gateway]
        providers = ["baidu"]
    """)
    assert cfg.search_gateway.providers == ["baidu"]


@pytest.mark.parametrize("body,code", [
    ("[search_gateway]\nquick_total_timeout_s=-1", "SG-CONFIG-3"),
    ("[search_gateway]\nmax_concurrency=7", "SG-CONFIG-4"),
    ("[search_gateway]\nprovider_max_concurrency=0", "SG-CONFIG-8"),
    ("[search_gateway]\nprovider_max_concurrency=7", "SG-CONFIG-8"),
    ("[search_gateway]\nprovider_queue_max_wait_s=0", "SG-CONFIG-9"),
    ("[search_gateway]\nempty_rescue_max_per_request=2", "SG-CONFIG-10"),
    ('[search_gateway]\nsearxng_url="https://search.test/"', "SG-CONFIG-5"),
    ("[search_gateway]\ncircuit_blocked_threshold=0", "SG-CONFIG-6"),
    (
        "[search_gateway]\ncircuit_timeout_probe_base_s=10\n"
        "circuit_timeout_probe_max_s=2",
        "SG-CONFIG-7",
    ),
])
def test_invalid_search_gateway_config_fails_stably(tmp_path, body, code):
    with pytest.raises(ConfigError, match=code):
        _load(tmp_path, body)
