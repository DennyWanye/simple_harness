"""HTTP 传输超时必须大于任何车道的 deadline。

它是防挂死的安全网，不是语义闸门——语义切断由各车道自己的 deadline 负责。
比车道 deadline 短，就让后者永远够不着：A16 把 analysis 预算提到 6144 token /
deadline 180s，而 HTTP 层仍是 60s，于是模型一写长就在 60003ms 撞 transport_timeout
→ sent_unknown → 记忆零物化（.local-test-evidence/real-ui-channel/20260904T165832）。
"""

from __future__ import annotations

import inspect

from deskpet.memory.memory_ingestion_outbox import build_worker_config
from deskpet.sdk_adapters.provider import ProductProviderAdapter


def _http_timeout() -> float:
    return float(
        inspect.signature(ProductProviderAdapter.__init__).parameters["timeout"].default
    )


def _analysis_deadline_s() -> float:
    cfg = build_worker_config(
        provider_id="primary", model_id="m", model_config_hash="a" * 64
    )
    return cfg.analysis_budget.deadline_ms / 1000.0


def test_http_timeout_outlives_the_analysis_deadline() -> None:
    assert _http_timeout() > _analysis_deadline_s(), (
        f"HTTP 超时 {_http_timeout()}s 不大于 analysis deadline "
        f"{_analysis_deadline_s()}s —— 车道 deadline 永远够不着，"
        "长提案会撞 transport_timeout 而不是走正常的超时语义"
    )


def test_http_timeout_keeps_meaningful_headroom() -> None:
    """不是刚好大一点：安全网要留余量，否则临界值仍会被 HTTP 层截胡。"""
    assert _http_timeout() >= _analysis_deadline_s() * 1.2


def test_http_timeout_is_still_bounded() -> None:
    """安全网仍要有上限，不能变成无限等待。"""
    assert _http_timeout() <= _analysis_deadline_s() * 4
