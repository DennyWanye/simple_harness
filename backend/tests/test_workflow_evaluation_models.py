from __future__ import annotations

import pytest

from deskpet.workflows.evaluation import (
    EvaluationOutcome,
    EvaluationVerdict,
    PairwiseEvaluationRecord,
    adapt_legacy_result,
)


def test_evaluation_outcome_is_framework_neutral_and_validates_score():
    outcome = EvaluationOutcome(
        evaluator_name="code-rule",
        evaluator_version="sha256:abc",
        evaluator_type="code_rule",
        verdict="PASS",
        score=0.75,
        labels=["tests", "file-hash"],
        evidence_refs=["receipt:1"],
    )

    assert outcome.verdict == EvaluationVerdict.PASS
    assert outcome.labels == ("tests", "file-hash")
    assert outcome.passed is True
    with pytest.raises(ValueError, match="finite"):
        EvaluationOutcome("rule", "1", "code_rule", "pass", score=float("nan"))


@pytest.mark.parametrize(
    ("legacy", "expected_verdict", "expected_score"),
    [
        (True, "pass", 1.0),
        (False, "fail", 0.0),
        (0.8, "pass", 0.8),
        ({"quality_score": 5, "issues": ["missing citation"]}, "fail", 5.0),
        ({"verdict": "revise", "quality_score": 4}, "revise", 4.0),
    ],
)
def test_legacy_adapters_cover_bool_score_and_dict(legacy, expected_verdict, expected_score):
    outcome = adapt_legacy_result(
        legacy,
        evaluator_name="legacy-gate",
        evaluator_version="v2",
    )

    assert outcome.verdict == expected_verdict
    assert outcome.score == expected_score


def test_malformed_legacy_mapping_fails_conservatively():
    outcome = adapt_legacy_result(
        {"unexpected": "shape"},
        evaluator_name="llm-judge",
        evaluator_version="prompt-hash",
    )

    assert outcome.verdict == "fail"
    assert outcome.degraded is True
    assert "malformed_legacy_result" in outcome.labels

    invalid_verdict = adapt_legacy_result(
        {"verdict": "definitely", "quality_score": 10},
        evaluator_name="llm-judge",
        evaluator_version="prompt-hash",
    )
    assert invalid_verdict.verdict == "fail"
    assert invalid_verdict.degraded is True


def test_pairwise_record_rejects_unknown_winner():
    with pytest.raises(ValueError, match="winner"):
        PairwiseEvaluationRecord(
            trace_id="trace",
            evaluator_name="reviewer",
            evaluator_version="1",
            left_version_key="workflow:a",
            right_version_key="workflow:b",
            winner="maybe",
        )
