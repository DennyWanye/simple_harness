"""Pure DeepResearch v6 claim and report-integrity contracts.

This module is intentionally independent from the workflow graph, persistence,
delivery, and LLM layers.  It owns the frozen T7/T8 JSON contracts and the
bounded Q1 exact-scalar integrity decision.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..contracts import JsonValue, canonical_json, validate_json_value
from .deep_research_v6_contracts import format_blob_ref, parse_blob_ref, sha256_json

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CLAIM_KINDS = {
    "fact",
    "inference",
    "preference",
    "limitation",
    "counterevidence",
    "uncertainty",
}
_CLAIM_SUPPORT = {"supported", "unsupported", "conflicted"}
_ASSESSMENT_SUPPORT = {"supported", "unsupported", "conflicted", "missing"}
_VISIBILITIES = {"user", "audit"}
_ANSWER_STATUSES = {"completed", "partial", "insufficient_evidence"}
_ASSESSMENT_STATUSES = {
    "completed_candidate",
    "partial_candidate",
    "insufficient",
    "needs_evidence",
}
_SOFT_SCORE_KEYS = {
    "readability",
    "source_diversity",
    "analysis_depth",
    "counterevidence",
    "uncertainty",
}
_CLAIM_KEYS = {
    "schema_version",
    "claim_id",
    "requirement_id",
    "item_or_cell_id",
    "claim_kind",
    "normalized_proposition",
    "binding_ids",
    "inference_ref",
    "support_status",
    "visibility",
}
_CLAIM_BATCH_KEYS = {
    "schema_version",
    "batch_id",
    "run_id",
    "spec_hash",
    "evidence_head_hash",
    "assessment_hash",
    "claim_policy_hash",
    "claims",
    "visible_claim_ids",
    "status",
}
_QUALITY_AUDIT_KEYS = {
    "schema_version",
    "audit_id",
    "run_id",
    "spec_hash",
    "assessment_hash",
    "claim_batch_ref",
    "quality_policy_hash",
    "hard_gate_status",
    "hard_failure_codes",
    "soft_scores",
    "repair_count",
    "answer_status",
}
_ASSESSMENT_KEYS = {
    "schema_version",
    "assessment_id",
    "assessment_hash",
    "spec_hash",
    "evidence_head_hash",
    "policy_hash",
    "requirement_results",
    "conflicts",
    "missing_requirement_ids",
    "minimum_useful",
    "status",
    "reason_codes",
}
_REQUIREMENT_RESULT_KEYS = {
    "requirement_id",
    "item_or_cell_id",
    "support_status",
    "binding_ids",
    "reason_codes",
}
_FACT_BATCH_KEYS = {
    "schema_version",
    "batch_id",
    "run_id",
    "spec_hash",
    "previous_head_hash",
    "ordinal",
    "page_result_ref",
    "admitted_binding_ids",
    "rejected_binding_ids",
    "conflict_ids",
    "provenance_refs",
    "policy_hash",
    "head_hash",
}


class IntegrityValidationError(ValueError):
    """A frozen integrity contract failed closed."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _fail(code: str, path: str, message: str) -> NoReturn:
    raise IntegrityValidationError(code, path, message)


def _object(value: object, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("integrity_keys_differ", path, "must be an object")
    raw = copy.deepcopy(dict(value))
    if set(raw) != keys:
        _fail(
            "integrity_keys_differ",
            path,
            f"missing={sorted(keys - set(raw))}, unknown={sorted(set(raw) - keys)}",
        )
    if raw.get("schema_version") != 1:
        _fail("integrity_schema_unsupported", f"{path}.schema_version", "must equal 1")
    try:
        validate_json_value(raw, path=path)
    except Exception as exc:  # pragma: no cover - defensive normalization boundary
        _fail("integrity_keys_differ", path, str(exc))
    return raw


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _fail("integrity_value_invalid", path, "must be a canonical non-empty string")
    return value


def _optional_text(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _text(value, path)


def _digest(value: object, path: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        _fail("integrity_hash_invalid", path, "must be lowercase sha256 hex")
    return value


def _enum(value: object, allowed: set[str], path: str) -> str:
    if value not in allowed:
        _fail("integrity_value_invalid", path, f"must be one of {sorted(allowed)}")
    return str(value)


def _integer(value: object, path: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail("integrity_value_invalid", path, f"must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        _fail("integrity_value_invalid", path, f"must be an integer <= {maximum}")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        _fail("integrity_value_invalid", path, "must be boolean")
    return value


def _canonical_strings(value: object, path: str, *, refs: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        _fail("integrity_value_invalid", path, "must be an array of non-empty strings")
    if value != sorted(set(value)):
        _fail("integrity_order_invalid", path, "must be sorted and duplicate-free")
    if refs:
        for index, item in enumerate(value):
            try:
                parse_blob_ref(item)
            except Exception as exc:
                _fail("integrity_reference_invalid", f"{path}[{index}]", str(exc))
    return tuple(value)


def _identity(prefix: str, value: Mapping[str, JsonValue], identity_key: str) -> str:
    payload = copy.deepcopy(dict(value))
    payload.pop(identity_key, None)
    return prefix + sha256_json(payload)[:24]


@dataclass(frozen=True, slots=True)
class ClaimRecordV1:
    _value: dict[str, JsonValue]

    def __post_init__(self) -> None:
        raw = _object(self._value, _CLAIM_KEYS, "$ClaimRecordV1")
        _text(raw["requirement_id"], "$.requirement_id")
        _optional_text(raw["item_or_cell_id"], "$.item_or_cell_id")
        _enum(raw["claim_kind"], _CLAIM_KINDS, "$.claim_kind")
        _text(raw["normalized_proposition"], "$.normalized_proposition")
        _canonical_strings(raw["binding_ids"], "$.binding_ids")
        inference_ref = raw["inference_ref"]
        if inference_ref is not None:
            try:
                parse_blob_ref(inference_ref)
            except Exception as exc:
                _fail("integrity_reference_invalid", "$.inference_ref", str(exc))
        _enum(raw["support_status"], _CLAIM_SUPPORT, "$.support_status")
        _enum(raw["visibility"], _VISIBILITIES, "$.visibility")
        expected = _identity("clm_", raw, "claim_id")
        if raw["claim_id"] != expected:
            _fail("claim_id_mismatch", "$.claim_id", f"expected {expected}")
        object.__setattr__(self, "_value", raw)

    @classmethod
    def create(
        cls,
        *,
        requirement_id: str,
        item_or_cell_id: str | None,
        claim_kind: str,
        normalized_proposition: str,
        binding_ids: Iterable[str],
        inference_ref: str | None,
        support_status: str,
        visibility: str,
    ) -> ClaimRecordV1:
        payload: dict[str, JsonValue] = {
            "schema_version": 1,
            "claim_id": "pending",
            "requirement_id": requirement_id,
            "item_or_cell_id": item_or_cell_id,
            "claim_kind": claim_kind,
            "normalized_proposition": normalized_proposition,
            "binding_ids": sorted(set(binding_ids)),
            "inference_ref": inference_ref,
            "support_status": support_status,
            "visibility": visibility,
        }
        payload["claim_id"] = _identity("clm_", payload, "claim_id")
        return cls.from_json(payload)

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ClaimRecordV1:
        return cls(copy.deepcopy(dict(value)))

    def to_json(self) -> dict[str, JsonValue]:
        return copy.deepcopy(self._value)

    def __getattr__(self, name: str) -> Any:
        try:
            return copy.deepcopy(self._value[name])
        except KeyError as exc:
            raise AttributeError(name) from exc


@dataclass(frozen=True, slots=True)
class ClaimBatchV1:
    _value: dict[str, JsonValue]

    def __post_init__(self) -> None:
        raw = _object(self._value, _CLAIM_BATCH_KEYS, "$ClaimBatchV1")
        _text(raw["run_id"], "$.run_id")
        for key in ("spec_hash", "evidence_head_hash", "assessment_hash", "claim_policy_hash"):
            _digest(raw[key], f"$.{key}")
        if not isinstance(raw["claims"], list):
            _fail("integrity_value_invalid", "$.claims", "must be an array")
        claims = [ClaimRecordV1.from_json(item) for item in raw["claims"]]
        claim_ids = [claim.claim_id for claim in claims]
        if claim_ids != sorted(set(claim_ids)):
            _fail("integrity_order_invalid", "$.claims", "claims must be sorted by unique claim_id")
        visible_ids = _canonical_strings(raw["visible_claim_ids"], "$.visible_claim_ids")
        expected_visible = tuple(
            claim.claim_id
            for claim in claims
            if claim.visibility == "user" and claim.support_status == "supported"
        )
        if visible_ids != expected_visible:
            _fail(
                "visible_claims_mismatch",
                "$.visible_claim_ids",
                "must exactly identify displayable user claims",
            )
        _enum(raw["status"], {"valid", "invalid"}, "$.status")
        expected = _identity("clb_", raw, "batch_id")
        if raw["batch_id"] != expected:
            _fail("claim_batch_id_mismatch", "$.batch_id", f"expected {expected}")
        object.__setattr__(self, "_value", raw)

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        spec_hash: str,
        evidence_head_hash: str,
        assessment_hash: str,
        claim_policy_hash: str,
        claims: Iterable[ClaimRecordV1 | Mapping[str, Any]],
        status: str,
    ) -> ClaimBatchV1:
        records = sorted(
            (
                item
                if isinstance(item, ClaimRecordV1)
                else ClaimRecordV1.from_json(item)
                for item in claims
            ),
            key=lambda item: item.claim_id,
        )
        payload: dict[str, JsonValue] = {
            "schema_version": 1,
            "batch_id": "pending",
            "run_id": run_id,
            "spec_hash": spec_hash,
            "evidence_head_hash": evidence_head_hash,
            "assessment_hash": assessment_hash,
            "claim_policy_hash": claim_policy_hash,
            "claims": [item.to_json() for item in records],
            "visible_claim_ids": [
                item.claim_id
                for item in records
                if item.visibility == "user" and item.support_status == "supported"
            ],
            "status": status,
        }
        payload["batch_id"] = _identity("clb_", payload, "batch_id")
        return cls.from_json(payload)

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ClaimBatchV1:
        return cls(copy.deepcopy(dict(value)))

    def to_json(self) -> dict[str, JsonValue]:
        return copy.deepcopy(self._value)

    def __getattr__(self, name: str) -> Any:
        try:
            return copy.deepcopy(self._value[name])
        except KeyError as exc:
            raise AttributeError(name) from exc


@dataclass(frozen=True, slots=True)
class QualityAuditV1:
    _value: dict[str, JsonValue]

    def __post_init__(self) -> None:
        raw = _object(self._value, _QUALITY_AUDIT_KEYS, "$QualityAuditV1")
        _text(raw["run_id"], "$.run_id")
        for key in ("spec_hash", "assessment_hash", "quality_policy_hash"):
            _digest(raw[key], f"$.{key}")
        try:
            parse_blob_ref(raw["claim_batch_ref"])
        except Exception as exc:
            _fail("integrity_reference_invalid", "$.claim_batch_ref", str(exc))
        hard_status = _enum(raw["hard_gate_status"], {"passed", "failed"}, "$.hard_gate_status")
        codes = _canonical_strings(raw["hard_failure_codes"], "$.hard_failure_codes")
        if (hard_status == "passed" and codes) or (hard_status == "failed" and not codes):
            _fail(
                "hard_gate_codes_mismatch",
                "$.hard_failure_codes",
                "passed requires no codes and failed requires at least one code",
            )
        scores = raw["soft_scores"]
        if not isinstance(scores, Mapping) or set(scores) != _SOFT_SCORE_KEYS:
            _fail("integrity_keys_differ", "$.soft_scores", "soft score keys differ")
        for key in sorted(_SOFT_SCORE_KEYS):
            _integer(scores[key], f"$.soft_scores.{key}", maximum=1_000_000)
        _integer(raw["repair_count"], "$.repair_count")
        answer_status = _enum(raw["answer_status"], _ANSWER_STATUSES, "$.answer_status")
        if hard_status == "failed" and answer_status == "completed":
            _fail("hard_gate_completed", "$.answer_status", "hard failure cannot be completed")
        expected = _identity("qau_", raw, "audit_id")
        if raw["audit_id"] != expected:
            _fail("quality_audit_id_mismatch", "$.audit_id", f"expected {expected}")
        object.__setattr__(self, "_value", raw)

    @classmethod
    def _create_derived(
        cls,
        *,
        run_id: str,
        spec_hash: str,
        assessment_hash: str,
        claim_batch_ref: str,
        quality_policy_hash: str,
        hard_failure_codes: Sequence[str],
        soft_scores: Mapping[str, int],
        repair_count: int,
        answer_status: str,
    ) -> QualityAuditV1:
        codes = sorted(set(hard_failure_codes))
        payload: dict[str, JsonValue] = {
            "schema_version": 1,
            "audit_id": "pending",
            "run_id": run_id,
            "spec_hash": spec_hash,
            "assessment_hash": assessment_hash,
            "claim_batch_ref": claim_batch_ref,
            "quality_policy_hash": quality_policy_hash,
            "hard_gate_status": "failed" if codes else "passed",
            "hard_failure_codes": codes,
            "soft_scores": {key: int(soft_scores[key]) for key in sorted(_SOFT_SCORE_KEYS)},
            "repair_count": repair_count,
            "answer_status": answer_status,
        }
        payload["audit_id"] = _identity("qau_", payload, "audit_id")
        return cls.from_json(payload)

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> QualityAuditV1:
        return cls(copy.deepcopy(dict(value)))

    def to_json(self) -> dict[str, JsonValue]:
        return copy.deepcopy(self._value)

    def __getattr__(self, name: str) -> Any:
        try:
            return copy.deepcopy(self._value[name])
        except KeyError as exc:
            raise AttributeError(name) from exc


@dataclass(frozen=True, slots=True)
class Q1IntegrityResult:
    claim_batch: ClaimBatchV1
    quality_audit: QualityAuditV1
    answer_status: str
    hard_failure_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _RequirementResult:
    requirement_id: str
    item_or_cell_id: str | None
    support_status: str
    binding_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Assessment:
    assessment_hash: str
    spec_hash: str
    evidence_head_hash: str
    status: str
    minimum_useful: bool
    missing_requirement_ids: tuple[str, ...]
    requirement_results: tuple[_RequirementResult, ...]


def _assessment(value: Mapping[str, Any]) -> _Assessment:
    raw = _object(value, _ASSESSMENT_KEYS, "$AnswerAssessmentV1")
    for key in ("assessment_hash", "spec_hash", "evidence_head_hash", "policy_hash"):
        _digest(raw[key], f"$.{key}")
    if not isinstance(raw["requirement_results"], list):
        _fail("integrity_value_invalid", "$.requirement_results", "must be an array")
    results: list[_RequirementResult] = []
    identities: list[tuple[str, str]] = []
    for index, item in enumerate(raw["requirement_results"]):
        path = f"$.requirement_results[{index}]"
        result = _object_unversioned(item, _REQUIREMENT_RESULT_KEYS, path)
        requirement_id = _text(result["requirement_id"], f"{path}.requirement_id")
        item_id = _optional_text(result["item_or_cell_id"], f"{path}.item_or_cell_id")
        support = _enum(result["support_status"], _ASSESSMENT_SUPPORT, f"{path}.support_status")
        bindings = _canonical_strings(result["binding_ids"], f"{path}.binding_ids")
        _canonical_strings(result["reason_codes"], f"{path}.reason_codes")
        if support == "supported" and not bindings:
            _fail("assessment_support_invalid", path, "supported result requires bindings")
        identities.append((requirement_id, item_id or ""))
        results.append(_RequirementResult(requirement_id, item_id, support, bindings))
    if identities != sorted(set(identities)):
        _fail(
            "integrity_order_invalid",
            "$.requirement_results",
            "must be sorted by unique requirement/item identity",
        )
    if not isinstance(raw["conflicts"], list):
        _fail("integrity_value_invalid", "$.conflicts", "must be an array")
    missing = _canonical_strings(raw["missing_requirement_ids"], "$.missing_requirement_ids")
    minimum_useful = _boolean(raw["minimum_useful"], "$.minimum_useful")
    status = _enum(raw["status"], _ASSESSMENT_STATUSES, "$.status")
    _canonical_strings(raw["reason_codes"], "$.reason_codes")
    identity_payload = copy.deepcopy(raw)
    identity_payload.pop("assessment_id")
    identity_payload.pop("assessment_hash")
    expected_hash = sha256_json(identity_payload)
    if raw["assessment_hash"] != expected_hash or raw["assessment_id"] != "asa_" + expected_hash[:24]:
        _fail("assessment_hash_mismatch", "$.assessment_hash", f"expected {expected_hash}")
    return _Assessment(
        assessment_hash=str(raw["assessment_hash"]),
        spec_hash=str(raw["spec_hash"]),
        evidence_head_hash=str(raw["evidence_head_hash"]),
        status=status,
        minimum_useful=minimum_useful,
        missing_requirement_ids=missing,
        requirement_results=tuple(results),
    )


def _object_unversioned(value: object, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("integrity_keys_differ", path, "must be an object")
    raw = copy.deepcopy(dict(value))
    if set(raw) != keys:
        _fail(
            "integrity_keys_differ",
            path,
            f"missing={sorted(keys - set(raw))}, unknown={sorted(set(raw) - keys)}",
        )
    try:
        validate_json_value(raw, path=path)
    except Exception as exc:  # pragma: no cover - defensive normalization boundary
        _fail("integrity_keys_differ", path, str(exc))
    return raw


def derive_fact_batch_head(value: Mapping[str, Any]) -> str:
    """Return the §6 chained head hash for an EvidenceFactBatchV1 payload."""

    raw = _object(value, _FACT_BATCH_KEYS, "$EvidenceFactBatchV1")
    previous = _digest(raw["previous_head_hash"], "$.previous_head_hash")
    payload = copy.deepcopy(raw)
    payload.pop("batch_id")
    payload.pop("head_hash")
    return hashlib.sha256((previous + canonical_json(payload)).encode("utf-8")).hexdigest()


def _admitted_bindings(
    values: Iterable[Mapping[str, Any]],
    *,
    run_id: str,
    spec_hash: str,
    expected_head_hash: str,
) -> set[str]:
    admitted: set[str] = set()
    previous = "0" * 64
    saw_batch = False
    for expected_ordinal, value in enumerate(values):
        path = f"$EvidenceFactBatchV1[{expected_ordinal}]"
        raw = _object(value, _FACT_BATCH_KEYS, path)
        _text(raw["batch_id"], f"{path}.batch_id")
        if raw["run_id"] != run_id or raw["spec_hash"] != spec_hash:
            _fail("fact_batch_identity_mismatch", path, "run/spec identity differs")
        if _integer(raw["ordinal"], f"{path}.ordinal") != expected_ordinal:
            _fail("integrity_order_invalid", f"{path}.ordinal", "must be contiguous from zero")
        if raw["previous_head_hash"] != previous:
            _fail("fact_batch_head_mismatch", f"{path}.previous_head_hash", "chain predecessor differs")
        try:
            parse_blob_ref(raw["page_result_ref"])
        except Exception as exc:
            _fail("integrity_reference_invalid", f"{path}.page_result_ref", str(exc))
        admitted_ids = _canonical_strings(raw["admitted_binding_ids"], f"{path}.admitted_binding_ids")
        rejected_ids = _canonical_strings(raw["rejected_binding_ids"], f"{path}.rejected_binding_ids")
        _canonical_strings(raw["conflict_ids"], f"{path}.conflict_ids")
        _canonical_strings(raw["provenance_refs"], f"{path}.provenance_refs", refs=True)
        _digest(raw["policy_hash"], f"{path}.policy_hash")
        if admitted.intersection(admitted_ids) or set(admitted_ids).intersection(rejected_ids):
            _fail("fact_batch_binding_conflict", path, "binding admission is not append-only/disjoint")
        expected_head = derive_fact_batch_head(raw)
        if raw["head_hash"] != expected_head:
            _fail("fact_batch_head_mismatch", f"{path}.head_hash", f"expected {expected_head}")
        admitted.update(admitted_ids)
        previous = expected_head
        saw_batch = True
    actual_head = previous if saw_batch else "0" * 64
    if actual_head != expected_head_hash:
        _fail("assessment_head_mismatch", "$.evidence_head_hash", "assessment does not reference final batch head")
    return admitted


def _normalize_soft_scores(value: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != _SOFT_SCORE_KEYS:
        _fail("integrity_keys_differ", "$.soft_scores", "soft score keys differ")
    result: dict[str, int] = {}
    for key in sorted(_SOFT_SCORE_KEYS):
        result[key] = _integer(value[key], f"$.soft_scores.{key}", maximum=1_000_000)
    return result


def _coverage_status(supported: int, total: int) -> tuple[str, str]:
    if total > 0 and supported == total:
        return "completed", "completed_candidate"
    if supported > 0:
        return "partial", "partial_candidate"
    return "insufficient_evidence", "insufficient"


def build_q1_exact_scalar_integrity(
    *,
    run_id: str,
    spec_hash: str,
    required_requirement_ids: Sequence[str],
    answer_assessment: Mapping[str, Any],
    evidence_fact_batches: Iterable[Mapping[str, Any]],
    claims: Iterable[ClaimRecordV1 | Mapping[str, Any]],
    requested_answer_status: str,
    claim_policy_hash: str,
    quality_policy_hash: str,
    soft_scores: Mapping[str, int],
    repair_count: int = 0,
) -> Q1IntegrityResult:
    """Build the bounded Q1 claim batch and a caller-proof integrity audit.

    The hard-gate decision is derived exclusively from the assessment, fact
    batches, and immutable claims.  The caller can request an answer status and
    supply soft scores, but cannot set or override ``hard_gate_status``.
    """

    run_id = _text(run_id, "$.run_id")
    spec_hash = _digest(spec_hash, "$.spec_hash")
    claim_policy_hash = _digest(claim_policy_hash, "$.claim_policy_hash")
    quality_policy_hash = _digest(quality_policy_hash, "$.quality_policy_hash")
    requested_answer_status = _enum(
        requested_answer_status, _ANSWER_STATUSES, "$.requested_answer_status"
    )
    repair_count = _integer(repair_count, "$.repair_count")
    normalized_scores = _normalize_soft_scores(soft_scores)
    required = tuple(required_requirement_ids)
    if (
        not required
        or any(not isinstance(item, str) or not item.strip() for item in required)
        or len(set(required)) != len(required)
    ):
        _fail(
            "required_requirements_invalid",
            "$.required_requirement_ids",
            "must be a non-empty duplicate-free sequence",
        )

    assessment = _assessment(answer_assessment)
    if assessment.spec_hash != spec_hash:
        _fail("assessment_spec_mismatch", "$.spec_hash", "assessment belongs to another spec")
    admitted = _admitted_bindings(
        evidence_fact_batches,
        run_id=run_id,
        spec_hash=spec_hash,
        expected_head_hash=assessment.evidence_head_hash,
    )

    records = tuple(
        item if isinstance(item, ClaimRecordV1) else ClaimRecordV1.from_json(item)
        for item in claims
    )
    if len({item.claim_id for item in records}) != len(records):
        _fail("duplicate_claim_id", "$.claims", "claim ids must be unique")

    hard_failures: set[str] = set()
    result_by_requirement: dict[str, _RequirementResult] = {}
    for result in assessment.requirement_results:
        if result.requirement_id in result_by_requirement:
            hard_failures.add("assessment_requirement_duplicate")
        result_by_requirement[result.requirement_id] = result
    if set(result_by_requirement) != set(required):
        hard_failures.add("assessment_requirement_set_mismatch")

    supported_requirements: set[str] = set()
    for requirement_id in required:
        result = result_by_requirement.get(requirement_id)
        if result is None:
            continue
        if result.support_status == "supported":
            supported_requirements.add(requirement_id)
            if not set(result.binding_ids).issubset(admitted):
                hard_failures.add("assessment_binding_not_admitted")
    expected_missing = set(required) - supported_requirements
    if set(assessment.missing_requirement_ids) != expected_missing:
        hard_failures.add("assessment_missing_set_mismatch")

    coverage_status, expected_assessment_status = _coverage_status(
        len(supported_requirements), len(required)
    )
    if assessment.status != expected_assessment_status:
        hard_failures.add("assessment_status_mismatch")
    if assessment.minimum_useful != (coverage_status != "insufficient_evidence"):
        hard_failures.add("assessment_minimum_useful_mismatch")
    if requested_answer_status != coverage_status:
        hard_failures.add("answer_status_mismatch")

    valid_visible_requirements: set[str] = set()
    visible_count_by_requirement: dict[str, int] = {}
    for claim in records:
        if claim.visibility != "user":
            continue
        result = result_by_requirement.get(claim.requirement_id)
        if result is None:
            hard_failures.add("claim_requirement_unknown")
            continue
        if claim.item_or_cell_id != result.item_or_cell_id:
            hard_failures.add("claim_item_mismatch")
        if claim.claim_kind != "fact":
            hard_failures.add("claim_kind_invalid_for_exact_scalar")
        if claim.inference_ref is not None:
            hard_failures.add("claim_inference_invalid_for_exact_scalar")
        if claim.support_status != "supported":
            hard_failures.add("claim_unsupported")
            continue
        if result.support_status != "supported":
            hard_failures.add("claim_requirement_unsupported")
        claim_bindings = set(claim.binding_ids)
        if not claim_bindings:
            hard_failures.add("claim_binding_missing")
        if not claim_bindings.issubset(admitted):
            hard_failures.add("claim_binding_dangling")
        if not claim_bindings.issubset(set(result.binding_ids)):
            hard_failures.add("claim_binding_mismatch")
        claim_valid = (
            claim.claim_kind == "fact"
            and claim.inference_ref is None
            and claim.support_status == "supported"
            and result.support_status == "supported"
            and bool(claim_bindings)
            and claim_bindings.issubset(admitted)
            and claim_bindings.issubset(set(result.binding_ids))
            and claim.item_or_cell_id == result.item_or_cell_id
        )
        if claim_valid:
            valid_visible_requirements.add(claim.requirement_id)
        visible_count_by_requirement[claim.requirement_id] = (
            visible_count_by_requirement.get(claim.requirement_id, 0) + 1
        )

    for requirement_id in supported_requirements:
        count = visible_count_by_requirement.get(requirement_id, 0)
        if count == 0:
            hard_failures.add("claim_omitted")
        elif count > 1:
            hard_failures.add("claim_duplicate_for_requirement")

    failures = tuple(sorted(hard_failures))
    claim_batch = ClaimBatchV1.create(
        run_id=run_id,
        spec_hash=spec_hash,
        evidence_head_hash=assessment.evidence_head_hash,
        assessment_hash=assessment.assessment_hash,
        claim_policy_hash=claim_policy_hash,
        claims=records,
        status="invalid" if failures else "valid",
    )

    answer_status = coverage_status
    if failures and answer_status == "completed":
        answer_status = "partial" if valid_visible_requirements else "insufficient_evidence"
    elif failures and answer_status == "partial" and not valid_visible_requirements:
        answer_status = "insufficient_evidence"
    effective_scores = (
        normalized_scores
        if not failures
        else {key: 0 for key in sorted(_SOFT_SCORE_KEYS)}
    )
    claim_batch_ref = format_blob_ref(sha256_json(claim_batch.to_json()))
    audit = QualityAuditV1._create_derived(
        run_id=run_id,
        spec_hash=spec_hash,
        assessment_hash=assessment.assessment_hash,
        claim_batch_ref=claim_batch_ref,
        quality_policy_hash=quality_policy_hash,
        hard_failure_codes=failures,
        soft_scores=effective_scores,
        repair_count=repair_count,
        answer_status=answer_status,
    )
    return Q1IntegrityResult(claim_batch, audit, answer_status, failures)


def validate_q1_exact_scalar_integrity(
    *,
    claim_batch: ClaimBatchV1 | Mapping[str, Any],
    quality_audit: QualityAuditV1 | Mapping[str, Any],
    **inputs: Any,
) -> Q1IntegrityResult:
    """Recompute a Q1 result and require byte-for-byte canonical equality."""

    loaded_batch = (
        claim_batch
        if isinstance(claim_batch, ClaimBatchV1)
        else ClaimBatchV1.from_json(claim_batch)
    )
    loaded_audit = (
        quality_audit
        if isinstance(quality_audit, QualityAuditV1)
        else QualityAuditV1.from_json(quality_audit)
    )
    rebuilt = build_q1_exact_scalar_integrity(**inputs)
    if loaded_batch.to_json() != rebuilt.claim_batch.to_json():
        _fail("claim_batch_semantic_mismatch", "$ClaimBatchV1", "recomputed batch differs")
    if loaded_audit.to_json() != rebuilt.quality_audit.to_json():
        _fail("quality_audit_semantic_mismatch", "$QualityAuditV1", "recomputed audit differs")
    return rebuilt


__all__ = [
    "ClaimBatchV1",
    "ClaimRecordV1",
    "IntegrityValidationError",
    "Q1IntegrityResult",
    "QualityAuditV1",
    "build_q1_exact_scalar_integrity",
    "derive_fact_batch_head",
    "validate_q1_exact_scalar_integrity",
]
