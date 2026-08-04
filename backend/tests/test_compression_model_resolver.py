from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.agent_loop import AgentLoop
from deskpet.agent.compression_model_resolver import (
    CompressionModelCandidate,
    CompressionModelResolver,
    CompressionModelUnavailable,
)


def _candidate(provider: str, model: str):
    return CompressionModelCandidate(provider, model, object())


def test_follow_session_preserves_ordered_chain() -> None:
    chain = (_candidate("a", "m1"), _candidate("b", "m2"))
    resolution = CompressionModelResolver().resolve(
        "follow_session", session_chain=chain
    )
    assert resolution.candidates == chain
    assert resolution.source == "follow_session"


def test_explicit_model_has_no_cross_model_fallback() -> None:
    resolution = CompressionModelResolver().resolve(
        "m2",
        session_chain=(_candidate("a", "m1"),),
        provider_catalog=(_candidate("b", "m2"), _candidate("c", "m3")),
    )
    assert [(c.provider_id, c.model_id) for c in resolution.candidates] == [
        ("b", "m2")
    ]


def test_unknown_explicit_model_fails_closed() -> None:
    with pytest.raises(CompressionModelUnavailable, match="unknown"):
        CompressionModelResolver().resolve(
            "unknown", session_chain=(_candidate("a", "m1"),)
        )


def test_agent_loop_reads_live_model_each_compaction_without_cross_model_fallback() -> None:
    selected = {"model": "m2"}
    providers = (
        SimpleNamespace(id="a", model="m1"),
        SimpleNamespace(id="b", model="m2"),
    )
    loop = AgentLoop(
        None,
        object(),
        compression_model_resolver=CompressionModelResolver(),
        compression_model="follow_session",
        compression_model_provider=lambda: selected["model"],
    )

    first = loop._resolve_compression_model(providers)
    assert first.source == "explicit"
    assert [(item.provider_id, item.model_id) for item in first.candidates] == [
        ("b", "m2")
    ]

    selected["model"] = "missing"
    with pytest.raises(CompressionModelUnavailable, match="missing"):
        loop._resolve_compression_model(providers)
