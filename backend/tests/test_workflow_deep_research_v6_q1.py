"""Compatibility assertions for the retired v6 Q1 prototype.

The old four-node test fixture accepted prefetched pages and exercised the
Q1-only persistence path. Production v6 now proves exact facts through the
same eleven-node registered-evidence graph as every other intent; end-to-end
coverage lives in test_deep_research_v6_production_runner.py.
"""

from __future__ import annotations

import inspect

import pytest

from deskpet.workflows.definitions import deep_research_v6_nodes as legacy_q1
from deskpet.workflows.definitions.v6.deep_research import NODE_IDS, initial_state


def test_q1_helpers_remain_importable_for_isolated_legacy_recovery() -> None:
    # Historical readers may still import these names. They are deliberately
    # absent from the production graph and must not be removed until old-run
    # recovery policy explicitly retires them.
    assert callable(legacy_q1.compile_spec_handler)
    assert callable(legacy_q1.collect_pages_handler)
    assert callable(legacy_q1.extract_facts_handler)
    assert callable(legacy_q1.assess_render_handler)


def test_production_v6_never_routes_through_q1_only_nodes() -> None:
    assert NODE_IDS == (
        "compile_spec", "plan_route", "load_pages", "extract_candidate_bundles",
        "admit_facts", "synthesize_inferences", "register_inferences",
        "assess_answer", "render_claims", "integrity", "persist_manifest",
    )
    assert {"collect_pages", "extract_facts", "assess_render"}.isdisjoint(NODE_IDS)


def test_q1_prefetched_ingress_is_rejected_by_signature() -> None:
    signature = inspect.signature(initial_state)
    assert "fetched_pages" not in signature.parameters
    assert "compiler_candidates" not in signature.parameters
    with pytest.raises(TypeError):
        initial_state(
            topic="2024年中国总人口和出生人口",
            run_id="run-q1-no-prefetch",
            fetched_pages=[],  # type: ignore[call-arg]
        )
