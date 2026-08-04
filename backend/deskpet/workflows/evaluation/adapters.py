"""Adapters from legacy gate return values to :class:`EvaluationOutcome`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any

from .models import EvaluationOutcome, EvaluationVerdict, EvaluatorType


_PASS_VALUES = {"pass", "passed", "ok", "success", "succeeded", "approve", "approved", "true"}
_FAIL_VALUES = {"fail", "failed", "failure", "reject", "rejected", "false"}
_REVISE_VALUES = {"revise", "retry", "needs_revision", "needs-revision"}


def _identity(
    evaluator_name: str,
    evaluator_version: str,
    evaluator_type: str,
) -> dict[str, str]:
    return {
        "evaluator_name": evaluator_name,
        "evaluator_version": evaluator_version,
        "evaluator_type": evaluator_type,
    }


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, Mapping):
        return tuple(str(item) for item in value.values() if str(item).strip())
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(str(item) for item in value if str(item).strip())
    return (str(value),)


def _normalize_verdict(value: Any) -> str | None:
    if isinstance(value, bool):
        return EvaluationVerdict.PASS if value else EvaluationVerdict.FAIL
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in _PASS_VALUES:
        return EvaluationVerdict.PASS
    if normalized in _FAIL_VALUES:
        return EvaluationVerdict.FAIL
    if normalized in _REVISE_VALUES:
        return EvaluationVerdict.REVISE
    if normalized in {item.value for item in EvaluationVerdict}:
        return normalized
    return None


def adapt_bool(
    value: bool,
    *,
    evaluator_name: str,
    evaluator_version: str = "1",
    evaluator_type: str = EvaluatorType.LEGACY,
    explanation: str | None = None,
) -> EvaluationOutcome:
    if not isinstance(value, bool):
        raise TypeError("adapt_bool requires bool")
    return EvaluationOutcome(
        **_identity(evaluator_name, evaluator_version, evaluator_type),
        verdict=EvaluationVerdict.PASS if value else EvaluationVerdict.FAIL,
        score=1.0 if value else 0.0,
        explanation=explanation,
    )


def adapt_score(
    value: Real,
    *,
    evaluator_name: str,
    evaluator_version: str = "1",
    evaluator_type: str = EvaluatorType.LEGACY,
    pass_threshold: float = 0.5,
    higher_is_better: bool = True,
    explanation: str | None = None,
) -> EvaluationOutcome:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError("adapt_score requires a real number")
    score = float(value)
    passed = score >= pass_threshold if higher_is_better else score <= pass_threshold
    return EvaluationOutcome(
        **_identity(evaluator_name, evaluator_version, evaluator_type),
        verdict=EvaluationVerdict.PASS if passed else EvaluationVerdict.FAIL,
        score=score,
        explanation=explanation,
    )


def adapt_mapping(
    value: Mapping[str, Any],
    *,
    evaluator_name: str,
    evaluator_version: str = "1",
    evaluator_type: str = EvaluatorType.LEGACY,
    pass_threshold: float | None = None,
    higher_is_better: bool = True,
    conservative: bool = True,
) -> EvaluationOutcome:
    score_key = next((key for key in ("score", "quality_score", "rating") if key in value), None)
    raw_score = value.get(score_key) if score_key else None
    score = None
    if raw_score is not None and not isinstance(raw_score, bool):
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            score = None

    explicit_verdict = "verdict" in value or "final_verdict" in value
    verdict = _normalize_verdict(value.get("verdict"))
    if verdict is None:
        verdict = _normalize_verdict(value.get("final_verdict"))
    invalid_explicit_verdict = explicit_verdict and verdict is None
    if verdict is None and not invalid_explicit_verdict:
        for key in ("passed", "pass", "ok", "success"):
            if key in value:
                verdict = _normalize_verdict(value[key])
                if verdict is not None:
                    break
    invalid_score = raw_score is not None and score is None
    if verdict is None and score is not None and not invalid_explicit_verdict:
        threshold = pass_threshold
        if threshold is None:
            threshold = 6.0 if score_key == "quality_score" else 0.5
        verdict = (
            EvaluationVerdict.PASS
            if (score >= threshold if higher_is_better else score <= threshold)
            else EvaluationVerdict.FAIL
        )

    malformed = verdict is None or invalid_score or invalid_explicit_verdict
    if malformed:
        verdict = EvaluationVerdict.FAIL if conservative else EvaluationVerdict.ABSTAIN

    explanation = value.get("explanation", value.get("reason", value.get("message")))
    if explanation is None and value.get("issues"):
        explanation = "; ".join(_strings(value.get("issues")))
    labels = _strings(value.get("labels", value.get("tags")))
    if malformed:
        labels = ("malformed_legacy_result", *labels)
    evidence_refs = _strings(
        value.get("evidence_refs", value.get("evidence", value.get("receipt_ids")))
    )
    return EvaluationOutcome(
        **_identity(evaluator_name, evaluator_version, evaluator_type),
        verdict=verdict,
        score=score,
        labels=labels,
        explanation=str(explanation) if explanation is not None else None,
        evidence_refs=evidence_refs,
        degraded=bool(value.get("degraded", False)) or malformed,
    )


def adapt_exception(
    error: BaseException,
    *,
    evaluator_name: str,
    evaluator_version: str = "1",
    evaluator_type: str = EvaluatorType.LEGACY,
) -> EvaluationOutcome:
    return EvaluationOutcome(
        **_identity(evaluator_name, evaluator_version, evaluator_type),
        verdict=EvaluationVerdict.ERROR,
        labels=("evaluation_error", type(error).__name__),
        explanation=str(error) or type(error).__name__,
        degraded=True,
    )


def adapt_legacy_result(
    value: Any,
    *,
    evaluator_name: str,
    evaluator_version: str = "1",
    evaluator_type: str = EvaluatorType.LEGACY,
    pass_threshold: float | None = None,
    higher_is_better: bool = True,
    conservative: bool = True,
) -> EvaluationOutcome:
    """Convert common bool, numeric, and mapping gate results conservatively."""

    if isinstance(value, EvaluationOutcome):
        return value
    if isinstance(value, bool):
        return adapt_bool(
            value,
            evaluator_name=evaluator_name,
            evaluator_version=evaluator_version,
            evaluator_type=evaluator_type,
        )
    if isinstance(value, Real):
        return adapt_score(
            value,
            evaluator_name=evaluator_name,
            evaluator_version=evaluator_version,
            evaluator_type=evaluator_type,
            pass_threshold=0.5 if pass_threshold is None else pass_threshold,
            higher_is_better=higher_is_better,
        )
    if isinstance(value, Mapping):
        return adapt_mapping(
            value,
            evaluator_name=evaluator_name,
            evaluator_version=evaluator_version,
            evaluator_type=evaluator_type,
            pass_threshold=pass_threshold,
            higher_is_better=higher_is_better,
            conservative=conservative,
        )
    return EvaluationOutcome(
        **_identity(evaluator_name, evaluator_version, evaluator_type),
        verdict=EvaluationVerdict.FAIL if conservative else EvaluationVerdict.ABSTAIN,
        labels=("unsupported_legacy_result", type(value).__name__),
        explanation=f"Unsupported evaluator result type: {type(value).__name__}",
        degraded=True,
    )
