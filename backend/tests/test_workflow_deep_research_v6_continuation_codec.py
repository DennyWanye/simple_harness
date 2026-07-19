from __future__ import annotations

import hashlib

import pytest

from deskpet.workflows.contracts import canonical_json
from deskpet.workflows.definitions.v6.deep_research import (
    build_continuation_start_payload,
    decode_continuation_snapshot,
    initial_state,
)


def _ref(seed: int) -> str:
    return "sha256:" + f"{seed:064x}"


def _snapshot() -> dict:
    policies = {
        name: _ref(index + 20)
        for index, name in enumerate((
            "compiler", "route", "extraction", "llm_extract", "llm_repair",
            "llm_inference", "admission", "inference", "assessment", "claim", "quality",
        ))
    }
    base = {
        "schema_version": 1,
        "run_id": "parent-run",
        "workflow_name": "deep_research",
        "workflow_version": "v6",
        "spec_ref": _ref(1),
        "spec_hash": "1" * 64,
        "fact_batch_refs": [_ref(2), _ref(3)],
        "evidence_head_hash": "2" * 64,
        "assessment_ref": _ref(4),
        "assessment_hash": "3" * 64,
        "assessment_input_hash": "4" * 64,
        "claim_batch_ref": _ref(5),
        "provenance_refs": [_ref(6), _ref(7)],
        "policy_refs": policies,
    }
    base["closure_refs"] = sorted({
        base["spec_ref"], base["assessment_ref"], base["claim_batch_ref"],
        *base["fact_batch_refs"], *base["provenance_refs"], *policies.values(),
    })
    semantic_hash = hashlib.sha256(canonical_json(base).encode("utf-8")).hexdigest()
    return {
        **base,
        "snapshot_id": "rcs_" + semantic_hash[:24],
        "snapshot_hash": semantic_hash,
    }


def test_v6_continuation_codec_hydrates_exact_frontier_and_closure() -> None:
    snapshot = _snapshot()
    decoded = decode_continuation_snapshot(snapshot)
    state = initial_state(
        topic="persisted normalized question",
        run_id="child-run",
        parent_run_id="parent-run",
        source_snapshot_hash=snapshot["snapshot_hash"],
        schema_version=1,
        continuation_snapshot=decoded,
    )
    values = state["values"]
    assert values["spec_ref"] == snapshot["spec_ref"]
    assert values["fact_batch_refs"] == snapshot["fact_batch_refs"]
    assert values["evidence_head_hash"] == snapshot["evidence_head_hash"]
    assert values["assessment_ref"] == snapshot["assessment_ref"]
    assert values["claim_batch_ref"] == snapshot["claim_batch_ref"]
    assert values["policy_refs"] == snapshot["policy_refs"]
    assert values["inherited_fact_batch_count"] == 2
    assert {"sha256:" + item["sha256"] for item in state["blob_refs"]} == set(
        snapshot["closure_refs"]
    )


def test_v6_continuation_codec_rejects_scope_widening_and_hash_change() -> None:
    snapshot = _snapshot()
    snapshot["closure_refs"].append(_ref(99))
    with pytest.raises(ValueError, match="closure"):
        decode_continuation_snapshot(snapshot)
    assert build_continuation_start_payload(
        parent_run_id="parent-run", source_snapshot_hash="a" * 64
    ) == {
        "schema_version": 1,
        "parent_run_id": "parent-run",
        "source_snapshot_hash": "a" * 64,
    }
