"""Strict canonical retrieval-result contracts for DeepResearch v6.

These objects are the only wire shapes accepted by the v6 read-effect owner.
They deliberately keep provider payloads and raw URLs out of the effect row.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..contracts import JsonValue, canonical_json, validate_json_value
from .deep_research_v6_contracts import parse_blob_ref


def _exact(value: Mapping[str, Any], keys: set[str], name: str) -> None:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{name} keys differ")
    if value.get("schema_version") != 1:
        raise ValueError(f"{name}.schema_version must equal 1")


def _text(value: object, name: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _digest(value: object, name: str) -> str:
    text = _text(value, name)
    assert isinstance(text, str)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{name} must be 64 lowercase hex characters")
    return text


def _ref(value: object, name: str, *, nullable: bool = False) -> str | None:
    text = _text(value, name, nullable=nullable)
    if text is not None:
        parse_blob_ref(text)
    return text


def _strings(value: object, name: str, *, sorted_unique: bool = True) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    items = tuple(_text(item, name) for item in value)
    if sorted_unique and items != tuple(sorted(set(items))):
        raise ValueError(f"{name} must be unique and canonically sorted")
    return items  # type: ignore[return-value]


def _identity(prefix: str, payload: Mapping[str, JsonValue]) -> str:
    def jsonable(value: Any) -> JsonValue:
        if isinstance(value, tuple):
            return [jsonable(item) for item in value]
        if isinstance(value, list):
            return [jsonable(item) for item in value]
        if isinstance(value, dict):
            return {str(key): jsonable(item) for key, item in value.items()}
        return value

    normalized = jsonable(dict(payload))
    assert isinstance(normalized, dict)
    return prefix + hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class SourceLocatorV1:
    locator_id: str
    canonical_url: str
    final_url: str
    canonical_url_hash: str
    final_url_hash: str
    authority_id: str
    verification_status: str
    verification_policy_hash: str
    redirect_chain_hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("canonical_url", "final_url", "authority_id"):
            _text(getattr(self, name), name)
        if not self.canonical_url.startswith(("http://", "https://")):
            raise ValueError("canonical_url must be HTTP(S)")
        if not self.final_url.startswith(("http://", "https://")):
            raise ValueError("final_url must be HTTP(S)")
        canonical_hash = _digest(self.canonical_url_hash, "canonical_url_hash")
        final_hash = _digest(self.final_url_hash, "final_url_hash")
        if canonical_hash != hashlib.sha256(self.canonical_url.encode("utf-8")).hexdigest():
            raise ValueError("canonical_url_hash mismatch")
        if final_hash != hashlib.sha256(self.final_url.encode("utf-8")).hexdigest():
            raise ValueError("final_url_hash mismatch")
        _digest(self.verification_policy_hash, "verification_policy_hash")
        if self.verification_status not in {"verified", "rejected", "unverified"}:
            raise ValueError("verification_status is invalid")
        for item in self.redirect_chain_hashes:
            _digest(item, "redirect_chain_hashes")
        if self.verification_status == "unverified" and (
            self.final_url != self.canonical_url
            or final_hash != canonical_hash
            or self.redirect_chain_hashes
        ):
            raise ValueError("unverified locator must retain the canonical URL")
        if self.locator_id != _identity("locator_", self._payload()):
            raise ValueError("locator_id mismatch")

    def _payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1, "canonical_url": self.canonical_url,
            "final_url": self.final_url, "canonical_url_hash": self.canonical_url_hash,
            "final_url_hash": self.final_url_hash, "authority_id": self.authority_id,
            "verification_status": self.verification_status,
            "verification_policy_hash": self.verification_policy_hash,
            "redirect_chain_hashes": list(self.redirect_chain_hashes),
        }

    def to_json(self) -> dict[str, JsonValue]:
        value = {**self._payload(), "locator_id": self.locator_id}
        validate_json_value(value)
        return value

    @classmethod
    def create(cls, **kwargs: Any) -> "SourceLocatorV1":
        payload: dict[str, JsonValue] = {"schema_version": 1, **kwargs}
        return cls(locator_id=_identity("locator_", payload), **kwargs)

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "SourceLocatorV1":
        keys = {"schema_version", "locator_id", "canonical_url", "final_url", "canonical_url_hash", "final_url_hash", "authority_id", "verification_status", "verification_policy_hash", "redirect_chain_hashes"}
        _exact(value, keys, "SourceLocatorV1")
        hashes = _strings(value["redirect_chain_hashes"], "redirect_chain_hashes", sorted_unique=False)
        fields = keys - {"schema_version", "redirect_chain_hashes"}
        if any(not isinstance(value[key], str) for key in fields):
            raise ValueError("SourceLocatorV1 text fields must be strings")
        return cls(redirect_chain_hashes=hashes, **{key: value[key] for key in fields})  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class SearchCandidateV1:
    candidate_id: str
    ordinal: int
    source_locator_ref: str
    title_hash: str
    snippet_hash: str
    authority_match: str
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _integer(self.ordinal, "ordinal")
        _ref(self.source_locator_ref, "source_locator_ref")
        _digest(self.title_hash, "title_hash")
        _digest(self.snippet_hash, "snippet_hash")
        if self.authority_match not in {"verified", "rejected", "unverified"}:
            raise ValueError("authority_match is invalid")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("reason_codes must be unique and canonically sorted")
        if self.candidate_id != _identity("candidate_", self._payload()):
            raise ValueError("candidate_id mismatch")

    def _payload(self) -> dict[str, JsonValue]:
        return {"schema_version": 1, "ordinal": self.ordinal, "source_locator_ref": self.source_locator_ref, "title_hash": self.title_hash, "snippet_hash": self.snippet_hash, "authority_match": self.authority_match, "reason_codes": list(self.reason_codes)}

    def to_json(self) -> dict[str, JsonValue]:
        return {**self._payload(), "candidate_id": self.candidate_id}

    @classmethod
    def create(cls, **kwargs: Any) -> "SearchCandidateV1":
        payload: dict[str, JsonValue] = {"schema_version": 1, **kwargs}
        return cls(candidate_id=_identity("candidate_", payload), **kwargs)

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "SearchCandidateV1":
        keys = {"schema_version", "candidate_id", "ordinal", "source_locator_ref", "title_hash", "snippet_hash", "authority_match", "reason_codes"}
        _exact(value, keys, "SearchCandidateV1")
        return cls(candidate_id=_text(value["candidate_id"], "candidate_id"), ordinal=_integer(value["ordinal"], "ordinal"), source_locator_ref=_ref(value["source_locator_ref"], "source_locator_ref"), title_hash=_digest(value["title_hash"], "title_hash"), snippet_hash=_digest(value["snippet_hash"], "snippet_hash"), authority_match=_text(value["authority_match"], "authority_match"), reason_codes=_strings(value["reason_codes"], "reason_codes"))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class OfficialSearchResultV1:
    result_id: str
    logical_effect_id: str
    attempt_no: int
    request_id: str
    query_hash: str
    target_id: str
    candidates: tuple[SearchCandidateV1, ...]
    outcome: str
    error_code: str | None
    deadline_id: str

    def __post_init__(self) -> None:
        for name in ("logical_effect_id", "request_id", "target_id", "deadline_id"):
            _text(getattr(self, name), name)
        _integer(self.attempt_no, "attempt_no", minimum=1)
        _digest(self.query_hash, "query_hash")
        if self.outcome not in {"succeeded", "empty", "timeout", "cancelled_business", "failed"}:
            raise ValueError("official search outcome is invalid")
        if self.candidates != tuple(sorted(self.candidates, key=lambda item: item.ordinal)):
            raise ValueError("search candidates must be sorted by ordinal")
        if tuple(item.ordinal for item in self.candidates) != tuple(range(len(self.candidates))):
            raise ValueError("search candidate ordinals must be contiguous")
        if self.outcome != "succeeded" and self.candidates:
            raise ValueError("non-succeeded search must have no candidates")
        if self.outcome == "succeeded" and not self.candidates:
            raise ValueError("empty search must use the empty outcome")
        if self.result_id != _identity("osr_", self._payload()):
            raise ValueError("result_id mismatch")

    def _payload(self) -> dict[str, JsonValue]:
        return {"schema_version": 1, "logical_effect_id": self.logical_effect_id, "attempt_no": self.attempt_no, "request_id": self.request_id, "query_hash": self.query_hash, "target_id": self.target_id, "candidates": [item.to_json() for item in self.candidates], "outcome": self.outcome, "error_code": self.error_code, "deadline_id": self.deadline_id}

    def to_json(self) -> dict[str, JsonValue]:
        return {**self._payload(), "result_id": self.result_id}

    @classmethod
    def create(cls, **kwargs: Any) -> "OfficialSearchResultV1":
        payload: dict[str, JsonValue] = {"schema_version": 1, **kwargs, "candidates": [item.to_json() for item in kwargs["candidates"]]}
        return cls(result_id=_identity("osr_", payload), **kwargs)

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "OfficialSearchResultV1":
        keys = {"schema_version", "result_id", "logical_effect_id", "attempt_no", "request_id", "query_hash", "target_id", "candidates", "outcome", "error_code", "deadline_id"}
        _exact(value, keys, "OfficialSearchResultV1")
        raw = value["candidates"]
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or any(not isinstance(item, Mapping) for item in raw):
            raise ValueError("candidates must be an object array")
        return cls(result_id=_text(value["result_id"], "result_id"), logical_effect_id=_text(value["logical_effect_id"], "logical_effect_id"), attempt_no=_integer(value["attempt_no"], "attempt_no", minimum=1), request_id=_text(value["request_id"], "request_id"), query_hash=_digest(value["query_hash"], "query_hash"), target_id=_text(value["target_id"], "target_id"), candidates=tuple(SearchCandidateV1.from_json(item) for item in raw), outcome=_text(value["outcome"], "outcome"), error_code=_text(value["error_code"], "error_code", nullable=True), deadline_id=_text(value["deadline_id"], "deadline_id"))  # type: ignore[arg-type]

    def dependency_refs(self) -> tuple[str, ...]:
        return tuple(sorted({item.source_locator_ref for item in self.candidates}))


_PAGE_RECORD_KEYS = {"schema_version", "page_id", "source_locator_ref", "canonical_url_hash", "final_url_hash", "authority_id", "source_family_id", "source_tier", "body_ref", "body_hash", "fetched_at", "media_type", "admission_status", "reason_codes"}


def _validate_page_record(value: Mapping[str, Any]) -> dict[str, JsonValue]:
    _exact(value, _PAGE_RECORD_KEYS, "PageRecordV1")
    for key in _PAGE_RECORD_KEYS - {"schema_version", "reason_codes"}:
        _text(value[key], key)
    for key in ("canonical_url_hash", "final_url_hash", "body_hash"):
        _digest(value[key], key)
    _ref(value["source_locator_ref"], "source_locator_ref")
    body_ref = _ref(value["body_ref"], "body_ref")
    if parse_blob_ref(body_ref) != value["body_hash"]:  # type: ignore[arg-type]
        raise ValueError("body_ref/body_hash mismatch")
    expected = "page_" + hashlib.sha256((str(value["final_url_hash"]) + str(value["body_hash"])).encode("ascii")).hexdigest()[:24]
    if value["page_id"] != expected:
        raise ValueError("page_id mismatch")
    reasons = _strings(value["reason_codes"], "reason_codes")
    result = dict(value)
    result["reason_codes"] = list(reasons)
    validate_json_value(result)
    return result


_SPAN_KEYS = {"schema_version", "span_id", "page_id", "body_ref", "start_byte", "end_byte", "excerpt_hash", "page_part", "region_kind"}
_BINDING_KEYS = {"schema_version", "binding_id", "requirement_id", "target_kind", "item_or_cell_id", "field_or_facet_key", "span_id", "parsed_value", "normalized_value", "canonical_unit", "time_scope", "scope", "definition", "source_family_id", "source_tier", "validator_policy_hash", "status", "reason_codes"}


def _validate_span(value: Mapping[str, Any]) -> dict[str, JsonValue]:
    _exact(value, _SPAN_KEYS, "EvidenceSpanV1")
    for key in _SPAN_KEYS - {"schema_version", "start_byte", "end_byte"}:
        _text(value[key], key)
    _ref(value["body_ref"], "body_ref")
    start = _integer(value["start_byte"], "start_byte")
    end = _integer(value["end_byte"], "end_byte")
    if end <= start:
        raise ValueError("evidence span must be non-empty")
    _digest(value["excerpt_hash"], "excerpt_hash")
    payload = {key: value[key] for key in _SPAN_KEYS if key != "span_id"}
    if value["span_id"] != _identity("span_", payload):
        raise ValueError("span_id mismatch")
    result = dict(value); validate_json_value(result); return result


def _validate_binding(value: Mapping[str, Any]) -> dict[str, JsonValue]:
    _exact(value, _BINDING_KEYS, "EvidenceBindingV1")
    for key in ("binding_id", "requirement_id", "target_kind", "span_id", "source_family_id", "validator_policy_hash", "status"):
        _text(value[key], key)
    if value["target_kind"] not in {"scalar", "matrix_cell", "collection_field", "claim_fact"}:
        raise ValueError("binding target_kind is invalid")
    if value["status"] not in {"admitted", "rejected", "conflicted"}:
        raise ValueError("binding status is invalid")
    _digest(value["validator_policy_hash"], "validator_policy_hash")
    _text(value["source_tier"], "source_tier")
    reasons = _strings(value["reason_codes"], "reason_codes")
    payload = {key: value[key] for key in _BINDING_KEYS if key != "binding_id"}
    if value["binding_id"] != _identity("binding_", payload):
        raise ValueError("binding_id mismatch")
    result = dict(value); result["reason_codes"] = list(reasons); validate_json_value(result); return result


@dataclass(frozen=True, slots=True)
class PageExtractionResultV1:
    result_id: str
    logical_page_id: str
    logical_effect_id: str
    attempt_no: int
    source_locator_ref: str
    page_record: dict[str, JsonValue] | None
    spans: tuple[dict[str, JsonValue], ...]
    bindings: tuple[dict[str, JsonValue], ...]
    outcome: str
    error_code: str | None
    deadline_id: str
    control_command_id: str | None

    def __post_init__(self) -> None:
        for name in ("logical_page_id", "logical_effect_id", "deadline_id"):
            _text(getattr(self, name), name)
        _integer(self.attempt_no, "attempt_no", minimum=1)
        _ref(self.source_locator_ref, "source_locator_ref")
        if self.outcome not in {"succeeded", "empty", "blocked", "timeout", "cancelled_business", "failed"}:
            raise ValueError("page extraction outcome is invalid")
        page = _validate_page_record(self.page_record) if self.page_record is not None else None
        spans = tuple(_validate_span(item) for item in self.spans)
        bindings = tuple(_validate_binding(item) for item in self.bindings)
        if spans != tuple(sorted(spans, key=lambda item: str(item["span_id"]))) or bindings != tuple(sorted(bindings, key=lambda item: str(item["binding_id"]))):
            raise ValueError("spans/bindings must be sorted by ID")
        if self.outcome == "succeeded" and page is None:
            raise ValueError("succeeded page extraction requires page_record")
        if self.outcome != "succeeded" and (page is not None or spans or bindings):
            raise ValueError("non-succeeded page extraction cannot carry evidence")
        if self.result_id != _identity("pxr_", self._payload()):
            raise ValueError("result_id mismatch")

    def _payload(self) -> dict[str, JsonValue]:
        return {"schema_version": 1, "logical_page_id": self.logical_page_id, "logical_effect_id": self.logical_effect_id, "attempt_no": self.attempt_no, "source_locator_ref": self.source_locator_ref, "page_record": self.page_record, "spans": list(self.spans), "bindings": list(self.bindings), "outcome": self.outcome, "error_code": self.error_code, "deadline_id": self.deadline_id, "control_command_id": self.control_command_id}

    def to_json(self) -> dict[str, JsonValue]:
        return {**self._payload(), "result_id": self.result_id}

    @classmethod
    def create(cls, **kwargs: Any) -> "PageExtractionResultV1":
        payload: dict[str, JsonValue] = {"schema_version": 1, **kwargs, "spans": list(kwargs["spans"]), "bindings": list(kwargs["bindings"])}
        return cls(result_id=_identity("pxr_", payload), **kwargs)

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "PageExtractionResultV1":
        keys = {"schema_version", "result_id", "logical_page_id", "logical_effect_id", "attempt_no", "source_locator_ref", "page_record", "spans", "bindings", "outcome", "error_code", "deadline_id", "control_command_id"}
        _exact(value, keys, "PageExtractionResultV1")
        page = value["page_record"]
        if page is not None and not isinstance(page, Mapping): raise ValueError("page_record must be object or null")
        for name in ("spans", "bindings"):
            raw = value[name]
            if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or any(not isinstance(item, Mapping) for item in raw): raise ValueError(f"{name} must be an object array")
        return cls(result_id=_text(value["result_id"], "result_id"), logical_page_id=_text(value["logical_page_id"], "logical_page_id"), logical_effect_id=_text(value["logical_effect_id"], "logical_effect_id"), attempt_no=_integer(value["attempt_no"], "attempt_no", minimum=1), source_locator_ref=_ref(value["source_locator_ref"], "source_locator_ref"), page_record=(dict(page) if page is not None else None), spans=tuple(dict(item) for item in value["spans"]), bindings=tuple(dict(item) for item in value["bindings"]), outcome=_text(value["outcome"], "outcome"), error_code=_text(value["error_code"], "error_code", nullable=True), deadline_id=_text(value["deadline_id"], "deadline_id"), control_command_id=_text(value["control_command_id"], "control_command_id", nullable=True))  # type: ignore[arg-type]

    def dependency_refs(self) -> tuple[str, ...]:
        refs = {self.source_locator_ref}
        if self.page_record is not None:
            refs.add(str(self.page_record["source_locator_ref"])); refs.add(str(self.page_record["body_ref"]))
        refs.update(str(span["body_ref"]) for span in self.spans)
        return tuple(sorted(refs))


@dataclass(frozen=True, slots=True)
class PageAttemptOutcomeV1:
    outcome_id: str
    logical_page_id: str
    ordinal: int
    logical_effect_id: str | None
    attempt_no: int | None
    canonical_effect_id: str | None
    result_ref: str | None
    status: str
    deadline_id: str
    control_command_id: str | None

    def __post_init__(self) -> None:
        _text(self.logical_page_id, "logical_page_id"); _integer(self.ordinal, "ordinal"); _text(self.deadline_id, "deadline_id")
        if self.status not in {"committed", "not_started"}: raise ValueError("page attempt status is invalid")
        fields = (self.logical_effect_id, self.attempt_no, self.canonical_effect_id, self.result_ref)
        if self.status == "committed":
            _text(self.logical_effect_id, "logical_effect_id"); _integer(self.attempt_no, "attempt_no", minimum=1); _text(self.canonical_effect_id, "canonical_effect_id"); _ref(self.result_ref, "result_ref")
        elif any(value is not None for value in fields):
            raise ValueError("not_started attempt/effect/result fields must be null")
        if self.outcome_id != _identity("pao_", self._payload()): raise ValueError("outcome_id mismatch")

    def _payload(self) -> dict[str, JsonValue]:
        return {"schema_version": 1, "logical_page_id": self.logical_page_id, "ordinal": self.ordinal, "logical_effect_id": self.logical_effect_id, "attempt_no": self.attempt_no, "canonical_effect_id": self.canonical_effect_id, "result_ref": self.result_ref, "status": self.status, "deadline_id": self.deadline_id, "control_command_id": self.control_command_id}

    def to_json(self) -> dict[str, JsonValue]: return {**self._payload(), "outcome_id": self.outcome_id}

    @classmethod
    def create(cls, **kwargs: Any) -> "PageAttemptOutcomeV1":
        payload: dict[str, JsonValue] = {"schema_version": 1, **kwargs}; return cls(outcome_id=_identity("pao_", payload), **kwargs)

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "PageAttemptOutcomeV1":
        keys = {"schema_version", "outcome_id", "logical_page_id", "ordinal", "logical_effect_id", "attempt_no", "canonical_effect_id", "result_ref", "status", "deadline_id", "control_command_id"}; _exact(value, keys, "PageAttemptOutcomeV1")
        attempt = value["attempt_no"]
        if attempt is not None: attempt = _integer(attempt, "attempt_no", minimum=1)
        return cls(outcome_id=_text(value["outcome_id"], "outcome_id"), logical_page_id=_text(value["logical_page_id"], "logical_page_id"), ordinal=_integer(value["ordinal"], "ordinal"), logical_effect_id=_text(value["logical_effect_id"], "logical_effect_id", nullable=True), attempt_no=attempt, canonical_effect_id=_text(value["canonical_effect_id"], "canonical_effect_id", nullable=True), result_ref=_ref(value["result_ref"], "result_ref", nullable=True), status=_text(value["status"], "status"), deadline_id=_text(value["deadline_id"], "deadline_id"), control_command_id=_text(value["control_command_id"], "control_command_id", nullable=True))  # type: ignore[arg-type]


__all__ = ["OfficialSearchResultV1", "PageAttemptOutcomeV1", "PageExtractionResultV1", "SearchCandidateV1", "SourceLocatorV1"]
