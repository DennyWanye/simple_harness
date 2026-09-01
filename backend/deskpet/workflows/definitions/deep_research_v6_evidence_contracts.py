"""Checkpoint-safe evidence reference contracts for DeepResearch v6."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..contracts import JsonValue, validate_json_value
from .deep_research_v6_contracts import parse_blob_ref, sha256_json


def _digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{name} must be 64 lowercase hex characters")
    return value


@dataclass(frozen=True, slots=True)
class V6FetchedPageRefPayloadV1:
    """Checkpoint-safe page input; raw body and URLs live only in blobs."""

    page_id: str
    ordinal: int
    source_locator_ref: str
    body_ref: str
    body_hash: str
    canonical_url_hash: str
    final_url_hash: str
    authority_id: str
    title_hash: str
    media_type: str
    admission_status: str
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.page_id or isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValueError("page_id and a non-negative ordinal are required")
        parse_blob_ref(self.source_locator_ref)
        body_digest = parse_blob_ref(self.body_ref)
        body_hash = _digest(self.body_hash, "body_hash")
        if body_digest != body_hash:
            raise ValueError("body_ref digest must equal body_hash")
        final_hash = _digest(self.final_url_hash, "final_url_hash")
        _digest(self.canonical_url_hash, "canonical_url_hash")
        _digest(self.title_hash, "title_hash")
        expected_page_id = "page_" + hashlib.sha256(
            (final_hash + body_hash).encode("ascii")
        ).hexdigest()[:24]
        if self.page_id != expected_page_id:
            raise ValueError("page_id does not match final URL/body identity")
        if not self.authority_id.strip() or not self.media_type.strip():
            raise ValueError("authority_id and media_type are required")
        if self.admission_status != "admitted":
            raise ValueError("ref-only graph pages must already be admitted")
        if not self.reason_codes or any(not item.strip() for item in self.reason_codes):
            raise ValueError("reason_codes must contain non-empty values")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("reason_codes must be unique and canonically sorted")

    def to_json(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "schema_version": 1,
            "page_id": self.page_id,
            "ordinal": self.ordinal,
            "source_locator_ref": self.source_locator_ref,
            "body_ref": self.body_ref,
            "body_hash": self.body_hash,
            "canonical_url_hash": self.canonical_url_hash,
            "final_url_hash": self.final_url_hash,
            "authority_id": self.authority_id,
            "title_hash": self.title_hash,
            "media_type": self.media_type,
            "admission_status": self.admission_status,
            "reason_codes": list(self.reason_codes),
        }
        validate_json_value(value)
        return value

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> V6FetchedPageRefPayloadV1:
        keys = {
            "schema_version", "page_id", "ordinal", "source_locator_ref",
            "body_ref", "body_hash", "canonical_url_hash", "final_url_hash",
            "authority_id", "title_hash", "media_type", "admission_status",
            "reason_codes",
        }
        if not isinstance(value, Mapping) or set(value) != keys:
            raise ValueError("V6FetchedPageRefPayloadV1 keys differ")
        if value.get("schema_version") != 1:
            raise ValueError("V6FetchedPageRefPayloadV1.schema_version must equal 1")
        reason_codes = value["reason_codes"]
        if isinstance(reason_codes, (str, bytes)) or not isinstance(reason_codes, Sequence):
            raise ValueError("reason_codes must be an array")
        ordinal = value["ordinal"]
        if isinstance(ordinal, bool) or not isinstance(ordinal, int):
            raise ValueError("ordinal must be an integer")
        fields = (
            "page_id", "source_locator_ref", "body_ref", "body_hash",
            "canonical_url_hash", "final_url_hash", "authority_id", "title_hash",
            "media_type", "admission_status",
        )
        if any(not isinstance(value[field], str) for field in fields):
            raise ValueError("page ref text fields must be strings")
        if any(not isinstance(item, str) for item in reason_codes):
            raise ValueError("reason_codes values must be strings")
        return cls(
            page_id=value["page_id"], ordinal=ordinal,
            source_locator_ref=value["source_locator_ref"], body_ref=value["body_ref"],
            body_hash=value["body_hash"], canonical_url_hash=value["canonical_url_hash"],
            final_url_hash=value["final_url_hash"], authority_id=value["authority_id"],
            title_hash=value["title_hash"], media_type=value["media_type"],
            admission_status=value["admission_status"], reason_codes=tuple(reason_codes),
        )


@dataclass(frozen=True, slots=True)
class V6ExtractedEvidenceRefV1:
    requirement_id: str
    page_id: str
    evidence_ref: str
    source_locator_ref: str

    def __post_init__(self) -> None:
        if not self.requirement_id.strip() or not self.page_id.strip():
            raise ValueError("evidence reference requires requirement_id and page_id")
        parse_blob_ref(self.evidence_ref)
        parse_blob_ref(self.source_locator_ref)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "requirement_id": self.requirement_id,
            "page_id": self.page_id,
            "evidence_ref": self.evidence_ref,
            "source_locator_ref": self.source_locator_ref,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> V6ExtractedEvidenceRefV1:
        keys = {
            "schema_version", "requirement_id", "page_id", "evidence_ref",
            "source_locator_ref",
        }
        if not isinstance(value, Mapping) or set(value) != keys or value.get("schema_version") != 1:
            raise ValueError("V6ExtractedEvidenceRefV1 keys/version differ")
        if any(not isinstance(value[key], str) for key in keys - {"schema_version"}):
            raise ValueError("evidence ref fields must be strings")
        return cls(
            requirement_id=value["requirement_id"], page_id=value["page_id"],
            evidence_ref=value["evidence_ref"],
            source_locator_ref=value["source_locator_ref"],
        )


def _text(value: object, name: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _ref(value: object, name: str, *, nullable: bool = False) -> str | None:
    text = _text(value, name, nullable=nullable)
    if text is not None:
        parse_blob_ref(text)
    return text


def _string_array(value: object, name: str, *, ordered: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    items = tuple(_text(item, name) for item in value)
    if len(set(items)) != len(items):
        raise ValueError(f"{name} must not contain duplicates")
    if not ordered and items != tuple(sorted(items)):
        raise ValueError(f"{name} must be canonically sorted")
    return items  # type: ignore[return-value]


_CANDIDATE_KEYS = {
    "schema_version", "candidate_id", "candidate_kind", "work_group_id",
    "logical_page_id", "page_plan_ordinal", "candidate_ordinal",
    "requirement_id", "span_start_byte", "span_end_byte", "excerpt_hash",
    "normalized_proposition", "payload",
}
_PAYLOAD_KEYS = {
    "scalar": {
        "item_or_cell_id", "value", "canonical_unit", "time_scope", "scope",
        "definition",
    },
    "matrix_cell": {
        "cell_id", "axis_member_ids", "field_key", "value", "canonical_unit",
        "time_scope", "scope", "definition",
    },
    "collection_field": {
        "item_id", "unique_key_values", "field_key", "value", "canonical_unit",
        "as_of", "rank_inputs",
    },
    "claim_fact": {
        "claim_instance_id", "facet_key", "value", "canonical_unit", "time_scope",
        "scope", "definition",
    },
}


@dataclass(frozen=True, slots=True)
class EvidenceCandidateV1:
    """One page-local fact proposal with a verifiable UTF-8 byte span."""

    candidate_id: str
    candidate_kind: str
    work_group_id: str
    logical_page_id: str
    page_plan_ordinal: int
    candidate_ordinal: int
    requirement_id: str
    span_start_byte: int
    span_end_byte: int
    excerpt_hash: str
    normalized_proposition: str
    payload: dict[str, JsonValue]

    def __post_init__(self) -> None:
        if self.candidate_kind not in _PAYLOAD_KEYS:
            raise ValueError("candidate_kind is invalid")
        for name in ("work_group_id", "logical_page_id", "requirement_id", "normalized_proposition"):
            _text(getattr(self, name), name)
        for name in ("page_plan_ordinal", "candidate_ordinal", "span_start_byte", "span_end_byte"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.span_end_byte <= self.span_start_byte:
            raise ValueError("candidate span must be non-empty")
        _digest(self.excerpt_hash, "excerpt_hash")
        if set(self.payload) != _PAYLOAD_KEYS[self.candidate_kind]:
            raise ValueError("candidate payload keys differ")
        self._validate_payload()
        expected = "ecd_" + sha256_json(self._base_json())[:24]
        if self.candidate_id != expected:
            raise ValueError("candidate_id mismatch")

    def _validate_payload(self) -> None:
        payload = self.payload
        validate_json_value(payload)
        if self.candidate_kind == "scalar":
            if payload["item_or_cell_id"] is not None:
                raise ValueError("scalar item_or_cell_id must be null")
        elif self.candidate_kind == "matrix_cell":
            _text(payload["cell_id"], "cell_id")
            _text(payload["field_key"], "field_key")
            _string_array(payload["axis_member_ids"], "axis_member_ids", ordered=True)
        elif self.candidate_kind == "collection_field":
            _text(payload["item_id"], "item_id")
            _text(payload["field_key"], "field_key")
            if not isinstance(payload["unique_key_values"], list) or not payload["unique_key_values"]:
                raise ValueError("unique_key_values must be a non-empty array")
            rank_inputs = payload["rank_inputs"]
            if not isinstance(rank_inputs, list):
                raise ValueError("rank_inputs must be an array")
            for item in rank_inputs:
                if not isinstance(item, Mapping) or set(item) != {"field_key", "value", "missing"}:
                    raise ValueError("rank input keys differ")
                _text(item["field_key"], "rank input field_key")
                if not isinstance(item["missing"], bool) or (item["missing"] != (item["value"] is None)):
                    raise ValueError("rank input missing/value truth table differs")
        else:
            _text(payload["claim_instance_id"], "claim_instance_id")
            _text(payload["facet_key"], "facet_key")

    def _base_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "candidate_kind": self.candidate_kind,
            "work_group_id": self.work_group_id,
            "logical_page_id": self.logical_page_id,
            "page_plan_ordinal": self.page_plan_ordinal,
            "candidate_ordinal": self.candidate_ordinal,
            "requirement_id": self.requirement_id,
            "span_start_byte": self.span_start_byte,
            "span_end_byte": self.span_end_byte,
            "excerpt_hash": self.excerpt_hash,
            "normalized_proposition": self.normalized_proposition,
            "payload": self.payload,
        }

    def validate_body_span(self, body_bytes: bytes) -> None:
        if self.span_end_byte > len(body_bytes):
            raise ValueError("span_out_of_bounds")
        try:
            body_bytes[: self.span_start_byte].decode("utf-8")
            body_bytes[: self.span_end_byte].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("span_not_utf8_aligned") from exc
        excerpt = body_bytes[self.span_start_byte : self.span_end_byte]
        if hashlib.sha256(excerpt).hexdigest() != self.excerpt_hash:
            raise ValueError("excerpt_hash_mismatch")

    def to_json(self) -> dict[str, JsonValue]:
        return {**self._base_json(), "candidate_id": self.candidate_id}

    @classmethod
    def create(cls, *, body_bytes: bytes, **values: Any) -> EvidenceCandidateV1:
        start = values["span_start_byte"]
        end = values["span_end_byte"]
        if not isinstance(start, int) or not isinstance(end, int):
            raise ValueError("span offsets must be integers")
        values["excerpt_hash"] = hashlib.sha256(body_bytes[start:end]).hexdigest()
        base = {"schema_version": 1, **values}
        base["payload"] = dict(base["payload"])
        candidate_id = "ecd_" + sha256_json(base)[:24]
        return cls.from_json({**base, "candidate_id": candidate_id}, body_bytes=body_bytes)

    @classmethod
    def from_json(cls, value: Mapping[str, Any], *, body_bytes: bytes) -> EvidenceCandidateV1:
        if not isinstance(value, Mapping) or set(value) != _CANDIDATE_KEYS or value.get("schema_version") != 1:
            raise ValueError("EvidenceCandidateV1 keys/version differ")
        payload = value["payload"]
        if not isinstance(payload, Mapping):
            raise ValueError("candidate payload must be an object")
        candidate = cls(
            candidate_id=str(value["candidate_id"]), candidate_kind=str(value["candidate_kind"]),
            work_group_id=str(value["work_group_id"]), logical_page_id=str(value["logical_page_id"]),
            page_plan_ordinal=value["page_plan_ordinal"], candidate_ordinal=value["candidate_ordinal"],
            requirement_id=str(value["requirement_id"]), span_start_byte=value["span_start_byte"],
            span_end_byte=value["span_end_byte"], excerpt_hash=str(value["excerpt_hash"]),
            normalized_proposition=str(value["normalized_proposition"]), payload=dict(payload),
        )
        candidate.validate_body_span(body_bytes)
        return candidate


@dataclass(frozen=True, slots=True)
class EvidenceCandidateBundleV1:
    bundle_id: str
    run_id: str
    spec_hash: str
    work_group_id: str
    logical_page_id: str
    page_plan_ordinal: int
    page_result_ref: str
    route_decision_ref: str
    route_policy_ref: str
    extraction_policy_ref: str
    repair_round: int
    candidates: tuple[EvidenceCandidateV1, ...]
    bundle_reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.run_id, "run_id"); _digest(self.spec_hash, "spec_hash")
        _text(self.work_group_id, "work_group_id"); _text(self.logical_page_id, "logical_page_id")
        for name in ("page_result_ref", "route_decision_ref", "route_policy_ref", "extraction_policy_ref"):
            _ref(getattr(self, name), name)
        if self.repair_round not in {0, 1}:
            raise ValueError("repair_round must equal 0 or 1")
        if isinstance(self.page_plan_ordinal, bool) or not isinstance(self.page_plan_ordinal, int) or self.page_plan_ordinal < 0:
            raise ValueError("page_plan_ordinal must be non-negative")
        expected_order = tuple(sorted(self.candidates, key=lambda item: (item.candidate_ordinal, item.candidate_id)))
        if self.candidates != expected_order or tuple(item.candidate_ordinal for item in self.candidates) != tuple(range(len(self.candidates))):
            raise ValueError("candidates must be continuously ordered")
        if any(item.work_group_id != self.work_group_id or item.logical_page_id != self.logical_page_id or item.page_plan_ordinal != self.page_plan_ordinal for item in self.candidates):
            raise ValueError("candidate bundle identity differs from candidate")
        _string_array(self.bundle_reason_codes, "bundle_reason_codes")
        expected = "ecb_" + sha256_json(self._base_json())[:24]
        if self.bundle_id != expected:
            raise ValueError("bundle_id mismatch")

    def _base_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1, "run_id": self.run_id, "spec_hash": self.spec_hash,
            "work_group_id": self.work_group_id, "logical_page_id": self.logical_page_id,
            "page_plan_ordinal": self.page_plan_ordinal, "page_result_ref": self.page_result_ref,
            "route_decision_ref": self.route_decision_ref, "route_policy_ref": self.route_policy_ref,
            "extraction_policy_ref": self.extraction_policy_ref, "repair_round": self.repair_round,
            "candidates": [item.to_json() for item in self.candidates],
            "bundle_reason_codes": list(self.bundle_reason_codes),
        }

    def to_json(self) -> dict[str, JsonValue]:
        return {**self._base_json(), "bundle_id": self.bundle_id}

    @classmethod
    def create(cls, **values: Any) -> EvidenceCandidateBundleV1:
        values["candidates"] = tuple(values.get("candidates", ()))
        values["bundle_reason_codes"] = tuple(sorted(set(values.get("bundle_reason_codes", ()))))
        provisional = cls.__new__(cls)
        for key, value in values.items():
            object.__setattr__(provisional, key, value)
        base = provisional._base_json()
        return cls(bundle_id="ecb_" + sha256_json(base)[:24], **values)

    @classmethod
    def from_json(cls, value: Mapping[str, Any], *, body_bytes: bytes) -> EvidenceCandidateBundleV1:
        keys = {"schema_version", "bundle_id", "run_id", "spec_hash", "work_group_id", "logical_page_id", "page_plan_ordinal", "page_result_ref", "route_decision_ref", "route_policy_ref", "extraction_policy_ref", "repair_round", "candidates", "bundle_reason_codes"}
        if not isinstance(value, Mapping) or set(value) != keys or value.get("schema_version") != 1:
            raise ValueError("EvidenceCandidateBundleV1 keys/version differ")
        if not isinstance(value["candidates"], list) or not isinstance(value["bundle_reason_codes"], list):
            raise ValueError("bundle arrays differ")
        return cls(
            bundle_id=str(value["bundle_id"]), run_id=str(value["run_id"]), spec_hash=str(value["spec_hash"]),
            work_group_id=str(value["work_group_id"]), logical_page_id=str(value["logical_page_id"]),
            page_plan_ordinal=value["page_plan_ordinal"], page_result_ref=str(value["page_result_ref"]),
            route_decision_ref=str(value["route_decision_ref"]), route_policy_ref=str(value["route_policy_ref"]),
            extraction_policy_ref=str(value["extraction_policy_ref"]), repair_round=value["repair_round"],
            candidates=tuple(EvidenceCandidateV1.from_json(item, body_bytes=body_bytes) for item in value["candidates"]),
            bundle_reason_codes=tuple(str(item) for item in value["bundle_reason_codes"]),
        )


@dataclass(frozen=True, slots=True)
class CandidateProducerOutcomeV1:
    outcome_id: str
    origin: str
    status: str
    logical_page_id: str
    work_group_id: str
    bundle_ref: str | None
    llm_effect_outcome_ref: str | None
    policy_ref: str
    dependency_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.origin not in {"deterministic", "llm"} or self.status not in {"validated", "malformed", "opaque_uncertain", "deadline", "budget_denied"}:
            raise ValueError("candidate producer origin/status invalid")
        _text(self.logical_page_id, "logical_page_id"); _text(self.work_group_id, "work_group_id")
        _ref(self.bundle_ref, "bundle_ref", nullable=True); _ref(self.llm_effect_outcome_ref, "llm_effect_outcome_ref", nullable=True); _ref(self.policy_ref, "policy_ref")
        dependencies = _string_array(self.dependency_refs, "dependency_refs")
        required = {self.policy_ref}
        if self.bundle_ref is not None: required.add(self.bundle_ref)
        if self.llm_effect_outcome_ref is not None: required.add(self.llm_effect_outcome_ref)
        if not required.issubset(set(dependencies)):
            raise ValueError("producer dependencies omit direct refs")
        if self.origin == "deterministic" and (self.status != "validated" or self.bundle_ref is None or self.llm_effect_outcome_ref is not None):
            raise ValueError("deterministic producer truth table differs")
        if self.origin == "deterministic" and len(dependencies) != 3:
            raise ValueError("deterministic producer dependencies must be page, bundle, policy")
        if self.status == "validated" and self.bundle_ref is None:
            raise ValueError("validated producer requires bundle_ref")
        expected = "cpo_" + sha256_json(self._base_json())[:24]
        if self.outcome_id != expected:
            raise ValueError("producer outcome_id mismatch")

    def _base_json(self) -> dict[str, JsonValue]:
        return {"schema_version": 1, "origin": self.origin, "status": self.status, "logical_page_id": self.logical_page_id, "work_group_id": self.work_group_id, "bundle_ref": self.bundle_ref, "llm_effect_outcome_ref": self.llm_effect_outcome_ref, "policy_ref": self.policy_ref, "dependency_refs": list(self.dependency_refs)}

    def to_json(self) -> dict[str, JsonValue]: return {**self._base_json(), "outcome_id": self.outcome_id}

    @classmethod
    def create(cls, **values: Any) -> CandidateProducerOutcomeV1:
        values["dependency_refs"] = tuple(sorted(set(values["dependency_refs"])))
        provisional = cls.__new__(cls)
        for key, value in values.items(): object.__setattr__(provisional, key, value)
        return cls(outcome_id="cpo_" + sha256_json(provisional._base_json())[:24], **values)

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> CandidateProducerOutcomeV1:
        keys = {"schema_version", "outcome_id", "origin", "status", "logical_page_id", "work_group_id", "bundle_ref", "llm_effect_outcome_ref", "policy_ref", "dependency_refs"}
        if not isinstance(value, Mapping) or set(value) != keys or value.get("schema_version") != 1 or not isinstance(value["dependency_refs"], list):
            raise ValueError("CandidateProducerOutcomeV1 keys/version differ")
        return cls(outcome_id=str(value["outcome_id"]), origin=str(value["origin"]), status=str(value["status"]), logical_page_id=str(value["logical_page_id"]), work_group_id=str(value["work_group_id"]), bundle_ref=value["bundle_ref"], llm_effect_outcome_ref=value["llm_effect_outcome_ref"], policy_ref=str(value["policy_ref"]), dependency_refs=tuple(str(item) for item in value["dependency_refs"]))


@dataclass(frozen=True, slots=True)
class InferenceProposalV1:
    proposal_id: str
    requirement_id: str
    inference_kind: str
    item_or_cell_id: str | None
    facet_ids: tuple[str, ...]
    normalized_proposition: str
    premise_fact_refs: tuple[str, ...]
    model_id: str
    model_policy_ref: str

    def __post_init__(self) -> None:
        if self.inference_kind not in {"impact", "comparison", "preference", "conclusion", "limitation", "counterevidence", "uncertainty"}:
            raise ValueError("inference_kind invalid")
        _text(self.requirement_id, "requirement_id"); _text(self.item_or_cell_id, "item_or_cell_id", nullable=True); _text(self.normalized_proposition, "normalized_proposition"); _text(self.model_id, "model_id"); _ref(self.model_policy_ref, "model_policy_ref")
        _string_array(self.facet_ids, "facet_ids"); refs = _string_array(self.premise_fact_refs, "premise_fact_refs")
        if not refs: raise ValueError("premise_fact_refs must be non-empty")
        for ref in refs: parse_blob_ref(ref)
        expected = "inp_" + sha256_json(self._base_json())[:24]
        if self.proposal_id != expected: raise ValueError("proposal_id mismatch")

    def _base_json(self) -> dict[str, JsonValue]:
        return {"schema_version": 1, "requirement_id": self.requirement_id, "inference_kind": self.inference_kind, "item_or_cell_id": self.item_or_cell_id, "facet_ids": list(self.facet_ids), "normalized_proposition": self.normalized_proposition, "premise_fact_refs": list(self.premise_fact_refs), "model_id": self.model_id, "model_policy_ref": self.model_policy_ref}
    def to_json(self) -> dict[str, JsonValue]: return {**self._base_json(), "proposal_id": self.proposal_id}
    @classmethod
    def create(cls, **values: Any) -> InferenceProposalV1:
        values["facet_ids"] = tuple(sorted(set(values.get("facet_ids", ()))))
        values["premise_fact_refs"] = tuple(sorted(set(values["premise_fact_refs"])))
        provisional = cls.__new__(cls)
        for key, value in values.items(): object.__setattr__(provisional, key, value)
        return cls(proposal_id="inp_" + sha256_json(provisional._base_json())[:24], **values)
    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> InferenceProposalV1:
        keys={"schema_version","proposal_id","requirement_id","inference_kind","item_or_cell_id","facet_ids","normalized_proposition","premise_fact_refs","model_id","model_policy_ref"}
        if not isinstance(value,Mapping) or set(value)!=keys or value.get("schema_version")!=1 or not isinstance(value["facet_ids"],list) or not isinstance(value["premise_fact_refs"],list): raise ValueError("InferenceProposalV1 keys/version differ")
        return cls(proposal_id=str(value["proposal_id"]),requirement_id=str(value["requirement_id"]),inference_kind=str(value["inference_kind"]),item_or_cell_id=value["item_or_cell_id"],facet_ids=tuple(str(x) for x in value["facet_ids"]),normalized_proposition=str(value["normalized_proposition"]),premise_fact_refs=tuple(str(x) for x in value["premise_fact_refs"]),model_id=str(value["model_id"]),model_policy_ref=str(value["model_policy_ref"]))


@dataclass(frozen=True, slots=True)
class InferenceProposalBundleV1:
    bundle_id: str
    run_id: str
    spec_hash: str
    work_group_id: str
    input_evidence_head_hash: str
    ordinal: int
    profile_ref: str
    premise_fact_refs: tuple[str, ...]
    proposals: tuple[InferenceProposalV1, ...]

    def __post_init__(self) -> None:
        _text(self.run_id,"run_id"); _digest(self.spec_hash,"spec_hash"); _text(self.work_group_id,"work_group_id"); _digest(self.input_evidence_head_hash,"input_evidence_head_hash"); _ref(self.profile_ref,"profile_ref")
        if isinstance(self.ordinal,bool) or not isinstance(self.ordinal,int) or self.ordinal<0: raise ValueError("ordinal invalid")
        refs=_string_array(self.premise_fact_refs,"premise_fact_refs")
        for ref in refs: parse_blob_ref(ref)
        expected_union=tuple(sorted({ref for proposal in self.proposals for ref in proposal.premise_fact_refs}))
        if refs!=expected_union: raise ValueError("top-level premise refs differ from proposal union")
        if self.proposals!=tuple(sorted(self.proposals,key=lambda x:(self.work_group_id,x.inference_kind,x.proposal_id))): raise ValueError("proposals must be canonically sorted")
        expected="ipb_"+sha256_json(self._base_json())[:24]
        if self.bundle_id!=expected: raise ValueError("inference bundle_id mismatch")
    def _base_json(self)->dict[str,JsonValue]: return {"schema_version":1,"run_id":self.run_id,"spec_hash":self.spec_hash,"work_group_id":self.work_group_id,"input_evidence_head_hash":self.input_evidence_head_hash,"ordinal":self.ordinal,"profile_ref":self.profile_ref,"premise_fact_refs":list(self.premise_fact_refs),"proposals":[x.to_json() for x in self.proposals]}
    def to_json(self)->dict[str,JsonValue]: return {**self._base_json(),"bundle_id":self.bundle_id}
    @classmethod
    def create(cls,**values:Any)->InferenceProposalBundleV1:
        values["proposals"]=tuple(sorted(values.get("proposals",()),key=lambda x:(values["work_group_id"],x.inference_kind,x.proposal_id)))
        values["premise_fact_refs"]=tuple(sorted({ref for proposal in values["proposals"] for ref in proposal.premise_fact_refs}))
        provisional=cls.__new__(cls)
        for key,value in values.items(): object.__setattr__(provisional,key,value)
        return cls(bundle_id="ipb_"+sha256_json(provisional._base_json())[:24],**values)
    @classmethod
    def from_json(cls,value:Mapping[str,Any])->InferenceProposalBundleV1:
        keys={"schema_version","bundle_id","run_id","spec_hash","work_group_id","input_evidence_head_hash","ordinal","profile_ref","premise_fact_refs","proposals"}
        if not isinstance(value,Mapping) or set(value)!=keys or value.get("schema_version")!=1 or not isinstance(value["premise_fact_refs"],list) or not isinstance(value["proposals"],list): raise ValueError("InferenceProposalBundleV1 keys/version differ")
        return cls(bundle_id=str(value["bundle_id"]),run_id=str(value["run_id"]),spec_hash=str(value["spec_hash"]),work_group_id=str(value["work_group_id"]),input_evidence_head_hash=str(value["input_evidence_head_hash"]),ordinal=value["ordinal"],profile_ref=str(value["profile_ref"]),premise_fact_refs=tuple(str(x) for x in value["premise_fact_refs"]),proposals=tuple(InferenceProposalV1.from_json(x) for x in value["proposals"]))


_LLM_RESULT_KINDS = {"candidate_bundle", "inference_bundle"}
_LLM_OUTCOME_STATUSES = {
    "validated", "malformed", "opaque_uncertain", "deadline", "budget_denied",
}


@dataclass(frozen=True, slots=True)
class EvidenceRepairRequestV1:
    """Registered one-shot structured-repair request from §6.2."""

    repair_id: str
    result_kind: str
    original_profile_ref: str
    repair_profile_ref: str
    original_prompt_ref: str
    prior_outcome_ref: str
    raw_result_ref: str
    validation_reason_codes: tuple[str, ...]
    logical_page_id: str
    work_group_id: str
    repair_round: int

    def __post_init__(self) -> None:
        if self.result_kind not in _LLM_RESULT_KINDS:
            raise ValueError("repair result_kind invalid")
        for name in (
            "original_profile_ref", "repair_profile_ref", "original_prompt_ref",
            "prior_outcome_ref", "raw_result_ref",
        ):
            _ref(getattr(self, name), name)
        if self.original_profile_ref == self.repair_profile_ref:
            raise ValueError("repair profile must differ from original profile")
        _text(self.logical_page_id, "logical_page_id")
        _text(self.work_group_id, "work_group_id")
        reasons = _string_array(self.validation_reason_codes, "validation_reason_codes")
        if not reasons:
            raise ValueError("repair request requires validation_reason_codes")
        if isinstance(self.repair_round, bool) or self.repair_round != 1:
            raise ValueError("repair_round must equal 1")
        expected = "err_" + sha256_json(self._base_json())[:24]
        if self.repair_id != expected:
            raise ValueError("repair_id mismatch")

    def _base_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "result_kind": self.result_kind,
            "original_profile_ref": self.original_profile_ref,
            "repair_profile_ref": self.repair_profile_ref,
            "original_prompt_ref": self.original_prompt_ref,
            "prior_outcome_ref": self.prior_outcome_ref,
            "raw_result_ref": self.raw_result_ref,
            "validation_reason_codes": list(self.validation_reason_codes),
            "logical_page_id": self.logical_page_id,
            "work_group_id": self.work_group_id,
            "repair_round": self.repair_round,
        }

    def to_json(self) -> dict[str, JsonValue]:
        return {**self._base_json(), "repair_id": self.repair_id}

    @classmethod
    def create(cls, **values: Any) -> EvidenceRepairRequestV1:
        values["validation_reason_codes"] = tuple(
            sorted(set(values["validation_reason_codes"]))
        )
        provisional = cls.__new__(cls)
        for key, value in values.items():
            object.__setattr__(provisional, key, value)
        return cls(repair_id="err_" + sha256_json(provisional._base_json())[:24], **values)

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> EvidenceRepairRequestV1:
        keys = {
            "schema_version", "repair_id", "result_kind", "original_profile_ref",
            "repair_profile_ref", "original_prompt_ref", "prior_outcome_ref",
            "raw_result_ref", "validation_reason_codes", "logical_page_id",
            "work_group_id", "repair_round",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != keys
            or value.get("schema_version") != 1
            or not isinstance(value["validation_reason_codes"], list)
            or any(not isinstance(item, str) for item in value["validation_reason_codes"])
        ):
            raise ValueError("EvidenceRepairRequestV1 keys/version differ")
        text_fields = keys - {"schema_version", "repair_round", "validation_reason_codes"}
        if any(not isinstance(value[key], str) for key in text_fields):
            raise ValueError("EvidenceRepairRequestV1 text fields must be strings")
        if isinstance(value["repair_round"], bool) or not isinstance(value["repair_round"], int):
            raise ValueError("repair_round must be an integer")
        return cls(
            repair_id=str(value["repair_id"]),
            result_kind=str(value["result_kind"]),
            original_profile_ref=str(value["original_profile_ref"]),
            repair_profile_ref=str(value["repair_profile_ref"]),
            original_prompt_ref=str(value["original_prompt_ref"]),
            prior_outcome_ref=str(value["prior_outcome_ref"]),
            raw_result_ref=str(value["raw_result_ref"]),
            validation_reason_codes=tuple(value["validation_reason_codes"]),
            logical_page_id=str(value["logical_page_id"]),
            work_group_id=str(value["work_group_id"]),
            repair_round=value["repair_round"],
        )


@dataclass(frozen=True, slots=True)
class ResearchLLMEffectOutcomeV1:
    """Canonical result owner for one v6 extract/infer/repair logical effect."""

    outcome_id: str
    logical_effect_id: str
    effect_id: str
    status: str
    result_kind: str
    profile_ref: str
    prompt_ref: str
    raw_result_ref: str | None
    result_ref: str | None
    repair_request_ref: str | None
    prior_outcome_ref: str | None
    repair_round: int
    reason_codes: tuple[str, ...]
    dependency_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        logical = _text(self.logical_effect_id, "logical_effect_id")
        assert isinstance(logical, str)
        if self.result_kind not in _LLM_RESULT_KINDS:
            raise ValueError("LLM outcome result_kind invalid")
        if self.status not in _LLM_OUTCOME_STATUSES:
            raise ValueError("LLM outcome status invalid")
        if isinstance(self.repair_round, bool) or self.repair_round not in {0, 1}:
            raise ValueError("repair_round must be 0 or 1")
        if not logical.startswith("v6-") or not logical.endswith(f":r{self.repair_round}"):
            raise ValueError("logical_effect_id round/namespace mismatch")
        # ``effect_id`` is the real workflow_effects journal row identity;
        # ``logical_effect_id`` separately freezes the semantic call/round.
        _digest(self.effect_id, "effect_id")
        _ref(self.profile_ref, "profile_ref")
        _ref(self.prompt_ref, "prompt_ref")
        for name in (
            "raw_result_ref", "result_ref", "repair_request_ref", "prior_outcome_ref",
        ):
            _ref(getattr(self, name), name, nullable=True)
        _string_array(self.reason_codes, "reason_codes")
        dependencies = _string_array(self.dependency_refs, "dependency_refs")
        for ref in dependencies:
            parse_blob_ref(ref)

        if self.repair_round == 0:
            if self.repair_request_ref is not None or self.prior_outcome_ref is not None:
                raise ValueError("round-0 outcome cannot reference repair/prior outcome")
        elif self.repair_request_ref is None or self.prior_outcome_ref is None:
            raise ValueError("round-1 outcome requires repair request and prior outcome")

        if self.status == "validated":
            if self.raw_result_ref is None or self.result_ref is None:
                raise ValueError("validated outcome requires raw and canonical result refs")
        elif self.status == "malformed":
            if self.raw_result_ref is None or self.result_ref is not None:
                raise ValueError("malformed outcome requires raw and forbids canonical result")
        elif self.raw_result_ref is not None or self.result_ref is not None:
            raise ValueError("opaque/deadline/budget outcome forbids raw and result refs")

        expected_dependencies = tuple(sorted({
            ref for ref in (
                self.profile_ref, self.prompt_ref, self.raw_result_ref, self.result_ref,
                self.repair_request_ref, self.prior_outcome_ref,
            ) if ref is not None
        }))
        if dependencies != expected_dependencies:
            raise ValueError("LLM outcome dependency_refs are not the exact non-null ref set")
        expected = "rlo_" + sha256_json(self._base_json())[:24]
        if self.outcome_id != expected:
            raise ValueError("LLM outcome_id mismatch")

    def _base_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "logical_effect_id": self.logical_effect_id,
            "effect_id": self.effect_id,
            "status": self.status,
            "result_kind": self.result_kind,
            "profile_ref": self.profile_ref,
            "prompt_ref": self.prompt_ref,
            "raw_result_ref": self.raw_result_ref,
            "result_ref": self.result_ref,
            "repair_request_ref": self.repair_request_ref,
            "prior_outcome_ref": self.prior_outcome_ref,
            "repair_round": self.repair_round,
            "reason_codes": list(self.reason_codes),
            "dependency_refs": list(self.dependency_refs),
        }

    def to_json(self) -> dict[str, JsonValue]:
        return {**self._base_json(), "outcome_id": self.outcome_id}

    @classmethod
    def create(cls, **values: Any) -> ResearchLLMEffectOutcomeV1:
        values["reason_codes"] = tuple(sorted(set(values.get("reason_codes", ()))))
        values["dependency_refs"] = tuple(sorted({
            ref for ref in (
                values.get("profile_ref"), values.get("prompt_ref"),
                values.get("raw_result_ref"), values.get("result_ref"),
                values.get("repair_request_ref"), values.get("prior_outcome_ref"),
            ) if ref is not None
        }))
        provisional = cls.__new__(cls)
        for key, value in values.items():
            object.__setattr__(provisional, key, value)
        return cls(outcome_id="rlo_" + sha256_json(provisional._base_json())[:24], **values)

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ResearchLLMEffectOutcomeV1:
        keys = {
            "schema_version", "outcome_id", "logical_effect_id", "effect_id", "status",
            "result_kind", "profile_ref", "prompt_ref", "raw_result_ref", "result_ref",
            "repair_request_ref", "prior_outcome_ref", "repair_round", "reason_codes",
            "dependency_refs",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != keys
            or value.get("schema_version") != 1
            or not isinstance(value["reason_codes"], list)
            or not isinstance(value["dependency_refs"], list)
            or any(not isinstance(item, str) for item in value["reason_codes"])
            or any(not isinstance(item, str) for item in value["dependency_refs"])
        ):
            raise ValueError("ResearchLLMEffectOutcomeV1 keys/version differ")
        required_text = {
            "outcome_id", "logical_effect_id", "effect_id", "status", "result_kind",
            "profile_ref", "prompt_ref",
        }
        nullable_text = {"raw_result_ref", "result_ref", "repair_request_ref", "prior_outcome_ref"}
        if any(not isinstance(value[key], str) for key in required_text) or any(
            value[key] is not None and not isinstance(value[key], str) for key in nullable_text
        ):
            raise ValueError("ResearchLLMEffectOutcomeV1 ref/text fields differ")
        if isinstance(value["repair_round"], bool) or not isinstance(value["repair_round"], int):
            raise ValueError("repair_round must be an integer")
        return cls(
            outcome_id=str(value["outcome_id"]),
            logical_effect_id=str(value["logical_effect_id"]),
            effect_id=str(value["effect_id"]),
            status=str(value["status"]),
            result_kind=str(value["result_kind"]),
            profile_ref=str(value["profile_ref"]),
            prompt_ref=str(value["prompt_ref"]),
            raw_result_ref=value["raw_result_ref"],
            result_ref=value["result_ref"],
            repair_request_ref=value["repair_request_ref"],
            prior_outcome_ref=value["prior_outcome_ref"],
            repair_round=value["repair_round"],
            reason_codes=tuple(value["reason_codes"]),
            dependency_refs=tuple(value["dependency_refs"]),
        )


__all__ = [
    "CandidateProducerOutcomeV1", "EvidenceCandidateBundleV1", "EvidenceCandidateV1",
    "EvidenceRepairRequestV1", "InferenceProposalBundleV1", "InferenceProposalV1",
    "ResearchLLMEffectOutcomeV1", "V6ExtractedEvidenceRefV1", "V6FetchedPageRefPayloadV1",
]
