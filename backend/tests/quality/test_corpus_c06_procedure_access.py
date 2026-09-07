"""C06 gold caliber: procedure is scored as a discovery access, not a recall type.

A revision=1 Procedure keeps an UNBOUND applicability fingerprint until its first
real use and `memory_standalone` supplies no current fingerprints, so the SDK
type-authority gate withholds it from typed recall by design. The cross-scope
class therefore declares `required_procedure_access` beside `required_types`.
No Provider, model or scoring session runs here.
"""
import json
from pathlib import Path

import pytest

from deskpet.quality.corpus_scoring import review_packet, save

SDK = Path(__file__).resolve().parents[3].parent / "simple-harness-memory-sdk"
CORPUS = SDK / ("plans/2026-08-29-human-memory-digital-twin/quality/"
                "recall-corpus-candidate/review-zh/successor-12x20")
SEEDED = "cognitive-memory-" + "8" * 8
OTHER = "cognitive-memory-" + "9" * 8


def _discover_call(call_id="call-1"):
    return {"call_id": call_id, "name": "procedure_discover", "arguments": {"query": "排日程"}}


def _route_call(call_id="call-0", types=("semantic",)):
    return {"call_id": call_id, "name": "context_route",
            "arguments": {"route": "memory_standalone", "memory_types": list(types),
                          "include_short_horizon": False}}


def _trace(tool_calls):
    return {"providers": [{"invocation_id": "inv-1", "handed_off_at": 1.0,
                           "response_json": {"tool_calls": list(tool_calls)}}],
            "provider_observation_complete": True, "trace_status": "COMPLETE",
            "observation_errors": {}}


def _tool_message(candidates, *, outcome="succeeded", call_id="call-1"):
    value = {"kind": "procedure_draft_preview", "execution_authorized": False,
             "candidates": [{"candidate": {"memory_id": memory_id, "revision": 1},
                             "history_binding": {"memory_id": memory_id, "revision": 1,
                                                 "candidate_hash": "h"}}
                            for memory_id in candidates],
             "next_after": None, "omitted_oversize": 0}
    return {"role": "tool", "name": "procedure_discover", "call_id": call_id,
            "content": json.dumps({"error_code": None, "outcome": outcome,
                                   "public_message": None, "value": value})}


def _packet(tmp_path, name, *, tool_calls, messages, labels, seeded=(SEEDED,)):
    directory = tmp_path / name
    directory.mkdir()
    save(directory / "case.json", {"case_id": "control-only"})
    save(directory / "oracle.json", {"labels": labels, "oracle_source_text": "control oracle"})
    save(directory / "execution.json", {
        "execution_status": "COMPLETED", "trace": _trace(tool_calls), "transcript": messages,
        "setup_receipt": {"labels": {
            **{f"P{i}": {"memory_id": memory_id, "memory_type": "procedure", "revision": 1}
               for i, memory_id in enumerate(seeded)},
            "S": {"memory_id": "cognitive-memory-" + "a" * 8, "memory_type": "semantic",
                  "revision": 1}}}})
    return review_packet(directory, 0)


C06_LABELS = {"required_types": ["semantic"], "required_procedure_access": "procedure_discover"}


@pytest.mark.skipif(not CORPUS.is_dir(), reason="Memory SDK corpus source not present")
def test_compiled_c06_oracle_moves_procedure_out_of_required_types():
    import sys
    sys.path.insert(0, str(SDK / "scripts"))
    from corpus_runtime_input.compiler import compile_source
    artifacts = compile_source(CORPUS)
    catalog = [json.loads(line) for line in artifacts["audit/catalog.jsonl"].splitlines()]
    oracles = [json.loads(line) for line in artifacts["oracle/cases.jsonl"].splitlines()]
    by_id = {row["case_id"]: oracle["labels"] for row, oracle in zip(catalog, oracles)}
    assert len(by_id) == 240
    for index in range(1, 21):
        labels = by_id[f"C06-{index:02d}"]
        assert labels["required_types"] == ["semantic"]
        assert labels["required_procedure_access"] == "procedure_discover"
    # Every other class keeps its original contract and measures no tool access.
    assert by_id["C01-01"]["required_types"] == ["semantic"]
    assert by_id["C04-03"]["required_types"] == ["episode", "prospective"]
    assert {case_id for case_id, labels in by_id.items()
            if labels["required_procedure_access"] is not None} == {
                f"C06-{index:02d}" for index in range(1, 21)}


def test_real_discovery_of_the_seeded_procedure_is_a_hit(tmp_path):
    packet = _packet(tmp_path, "hit", tool_calls=[_route_call(), _discover_call()],
                     messages=[_tool_message([SEEDED])], labels=C06_LABELS)
    access = packet["required_procedure_access"]
    assert access["status"] == "SATISFIED"
    assert access["required_access"] == "procedure_discover"
    assert access["observed_calls"] == [{"invocation_id": "inv-1", "call_id": "call-1"}]
    assert access["observed_results"] == [
        {"call_id": "call-1", "outcome": "succeeded", "candidate_count": 1}]
    assert access["matched_memory_ids"] == [SEEDED]
    # The typed-recall metric now measures only what typed recall can return.
    assert packet["original_metric_components"]["required_type_denominator"] == 1
    assert packet["original_metric_components"]["proposed_required_matches"] == 1
    assert packet["predicted_types"] == ["semantic"]


def test_typed_recall_alone_is_not_procedure_access(tmp_path):
    packet = _packet(tmp_path, "no-call", tool_calls=[_route_call(types=("semantic", "procedure"))],
                     messages=[], labels=C06_LABELS)
    access = packet["required_procedure_access"]
    assert access["status"] == "NOT_SATISFIED"
    assert access["observed_calls"] == [] and access["candidate_memory_ids"] == []
    # Requesting the type is still recorded as an extra proposal, not as access.
    assert packet["original_metric_components"]["extra_proposed_types"] == 1


@pytest.mark.parametrize("candidates,outcome", [((), "succeeded"), ((SEEDED,), "failed"),
                                                ((OTHER,), "succeeded")])
def test_empty_failed_or_unrelated_candidates_are_not_a_hit(tmp_path, candidates, outcome):
    packet = _packet(tmp_path, "miss", tool_calls=[_discover_call()],
                     messages=[_tool_message(candidates, outcome=outcome)], labels=C06_LABELS)
    assert packet["required_procedure_access"]["status"] == "NOT_SATISFIED"


def test_missing_transcript_is_unknown_not_a_failed_access(tmp_path):
    directory = tmp_path / "unknown"
    directory.mkdir()
    save(directory / "case.json", {"case_id": "control-only"})
    save(directory / "oracle.json", {"labels": C06_LABELS, "oracle_source_text": "control"})
    save(directory / "execution.json", {"execution_status": "OBSERVATION_FAILED",
                                        "trace": _trace([_discover_call()])})
    assert review_packet(directory, 0)["required_procedure_access"]["status"] == "UNKNOWN"


def test_a_class_without_the_label_is_unaffected(tmp_path):
    packet = _packet(tmp_path, "plain", tool_calls=[_route_call()], messages=[],
                     labels={"required_types": ["semantic"]}, seeded=())
    assert packet["required_procedure_access"] is None
    assert "procedure" not in "".join(packet["review_requirements"])
