"""Canonical terminal manifest and async commit projection for v6.

The projector emits identity payloads only.  User-visible bytes remain in the
registered blob store and are resolved by delivery handlers in a later slice.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import aiosqlite

from ..contracts import (
    JsonValue,
    NodeExecutionIdentity,
    WorkflowContext,
    canonical_json,
    validate_json_value,
)
from ..errors import InvalidStatePatch
from ..store import RegisteredBlobStore
from .deep_research_v6_contracts import format_blob_ref, parse_blob_ref, sha256_json
from .deep_research_v6_integrity import (
    ClaimRecordV1,
    build_q1_exact_scalar_integrity,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_KEYS = {
    "schema_version", "manifest_id", "workflow_name", "workflow_version", "run_id",
    "answer_status", "spec_hash", "assessment_hash", "claim_policy_hash",
    "quality_policy_hash", "continuation_snapshot_ref", "continuation_snapshot_hash",
    "content_refs", "intent_specs", "cardinality", "engine_terminal",
}
_CONTENT_KEYS = {
    "final_assistant_ref", "report_ref", "safe_summary_ref", "claim_batch_ref",
    "quality_audit_ref",
}
_CARDINALITY_KEYS = {
    "final_assistant", "workflow_final_status", "report", "artifact", "run_terminal",
}
_ENGINE_KEYS = {"status", "error_code", "recovery_action"}
_INTENT_KEYS = {"intent_id", "event_key", "event_type", "content_ref", "delivery_specs"}
_DELIVERY_KEYS = {
    "delivery_spec_id", "channel", "target_role", "required_durable",
    "projection_kind", "context_visibility",
}
_REQUEST_KEYS = {
    "schema_version", "workflow_name", "workflow_version", "run_id", "manifest_ref",
    "manifest_hash", "engine_status", "engine_error_code", "recovery_action",
}
_PROJECTION_KEYS = {"schema_version", "manifest_ref", "manifest_hash", "answer_status", "blob_refs", "intents"}


def _invalid(code: str, message: str) -> NoReturn:
    raise InvalidStatePatch(code, message)


def _exact(value: object, keys: set[str], name: str, *, versioned: bool = False) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        _invalid("invalid_v6_terminal_manifest", f"{name} keys differ")
    raw = copy.deepcopy(dict(value))
    if versioned and raw.get("schema_version") != 1:
        _invalid("invalid_v6_terminal_manifest", f"{name}.schema_version must equal 1")
    validate_json_value(raw, path=f"$.{name}")
    return raw


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid("invalid_v6_terminal_manifest", f"{name} must be non-empty")
    return value


def _hash(value: object, name: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        _invalid("invalid_v6_terminal_manifest", f"{name} must be lowercase sha256")
    return value


def _nullable_ref(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _invalid("invalid_v6_terminal_manifest", f"{name} must be a wire ref or null")
    parse_blob_ref(value)
    return value


def _intent_id(run_id: str, event_key: str, event_type: str, content_ref: str | None) -> str:
    return hashlib.sha256(f"{run_id}|{event_key}|{event_type}|{content_ref or ''}".encode()).hexdigest()


def _delivery_spec(intent_id: str, **values: JsonValue) -> dict[str, JsonValue]:
    payload = copy.deepcopy(values)
    digest = hashlib.sha256(
        (intent_id + "|" + canonical_json(payload)).encode("utf-8")
    ).hexdigest()
    return {"delivery_spec_id": digest, **payload}


def _physical_specs(intent_id: str, event_key: str) -> list[dict[str, JsonValue]]:
    if event_key == "answer:final":
        rows = (
            {"channel": "session_message", "target_role": "original_session", "required_durable": True, "projection_kind": "final_assistant", "context_visibility": "conversation"},
            {"channel": "websocket", "target_role": "current_epoch", "required_durable": False, "projection_kind": "final_assistant", "context_visibility": "conversation"},
        )
    elif event_key == "run:terminal":
        rows = (
            {"channel": "session_message", "target_role": "original_session", "required_durable": True, "projection_kind": "workflow_final_status", "context_visibility": "exclude"},
            {"channel": "websocket", "target_role": "current_epoch", "required_durable": False, "projection_kind": "workflow_final_status", "context_visibility": "exclude"},
        )
    else:
        rows = (
            {"channel": "artifact", "target_role": "original_session", "required_durable": True, "projection_kind": "artifact_card", "context_visibility": "exclude"},
            {"channel": "websocket", "target_role": "current_epoch", "required_durable": False, "projection_kind": "artifact_card", "context_visibility": "exclude"},
        )
    return [_delivery_spec(intent_id, **row) for row in rows]


def build_intent_specs(
    *, run_id: str, final_assistant_ref: str, report_ref: str | None, artifact_required: bool,
) -> list[dict[str, JsonValue]]:
    rows: list[tuple[str, str, str | None]] = [
        ("answer:final", "workflow.final_assistant", final_assistant_ref),
        ("run:terminal", "workflow.final", None),
    ]
    if artifact_required:
        if report_ref is None:
            _invalid("invalid_v6_terminal_manifest", "artifact requires report_ref")
        rows.append(("artifact:report", "workflow.artifact_card", report_ref))
    result = []
    for event_key, event_type, content_ref in sorted(rows):
        intent_id = _intent_id(run_id, event_key, event_type, content_ref)
        result.append({
            "intent_id": intent_id,
            "event_key": event_key,
            "event_type": event_type,
            "content_ref": content_ref,
            "delivery_specs": _physical_specs(intent_id, event_key),
        })
    return result


def manifest_identity_seed(value: Mapping[str, Any]) -> str:
    return sha256_json({
        "workflow_name": value["workflow_name"],
        "workflow_version": value["workflow_version"],
        "run_id": value["run_id"],
        "answer_status": value["answer_status"],
        "spec_hash": value["spec_hash"],
        "assessment_hash": value["assessment_hash"],
    })


@dataclass(frozen=True, slots=True)
class TerminalDeliveryManifestV1:
    value: dict[str, JsonValue]

    def __post_init__(self) -> None:
        raw = _exact(self.value, _MANIFEST_KEYS, "TerminalDeliveryManifestV1", versioned=True)
        if raw["workflow_name"] != "deep_research" or raw["workflow_version"] != "v6":
            _invalid("invalid_v6_terminal_manifest", "manifest workflow identity must be deep_research/v6")
        run_id = _text(raw["run_id"], "run_id")
        status = raw["answer_status"]
        if status not in {"completed", "partial", "insufficient_evidence"}:
            _invalid("invalid_v6_terminal_manifest", "answer_status is invalid")
        for name in ("spec_hash", "assessment_hash", "claim_policy_hash", "quality_policy_hash", "continuation_snapshot_hash"):
            _hash(raw[name], name)
        parse_blob_ref(_text(raw["continuation_snapshot_ref"], "continuation_snapshot_ref"))
        content = _exact(raw["content_refs"], _CONTENT_KEYS, "content_refs")
        final_ref = _nullable_ref(content["final_assistant_ref"], "final_assistant_ref")
        report_ref = _nullable_ref(content["report_ref"], "report_ref")
        safe_ref = _nullable_ref(content["safe_summary_ref"], "safe_summary_ref")
        claim_ref = _nullable_ref(content["claim_batch_ref"], "claim_batch_ref")
        quality_ref = _nullable_ref(content["quality_audit_ref"], "quality_audit_ref")
        if final_ref is None or claim_ref is None or quality_ref is None:
            _invalid("invalid_v6_terminal_manifest", "final/claim/quality refs are required")
        cardinality = _exact(raw["cardinality"], _CARDINALITY_KEYS, "cardinality")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in cardinality.values()):
            _invalid("invalid_v6_terminal_manifest", "cardinality values must be non-negative integers")
        if status == "insufficient_evidence":
            expected = {"final_assistant": 1, "workflow_final_status": 1, "report": 0, "artifact": 0, "run_terminal": 1}
            if report_ref is not None or safe_ref != final_ref or cardinality != expected:
                _invalid("invalid_v6_terminal_manifest", "insufficient cardinality/content mismatch")
        else:
            if report_ref is None or safe_ref is not None:
                _invalid("invalid_v6_terminal_manifest", "completed/partial report content mismatch")
            expected_base = {"final_assistant": 1, "workflow_final_status": 1, "report": 1, "run_terminal": 1}
            if any(cardinality[key] != value for key, value in expected_base.items()) or cardinality["artifact"] not in {0, 1}:
                _invalid("invalid_v6_terminal_manifest", "completed/partial cardinality mismatch")
        engine = _exact(raw["engine_terminal"], _ENGINE_KEYS, "engine_terminal")
        if engine != {"status": "completed", "error_code": None, "recovery_action": None}:
            _invalid("invalid_v6_terminal_manifest", "answer manifest requires completed engine tuple")
        intents_raw = raw["intent_specs"]
        if not isinstance(intents_raw, list):
            _invalid("invalid_v6_terminal_manifest", "intent_specs must be an array")
        intents = [_exact(item, _INTENT_KEYS, "intent_spec") for item in intents_raw]
        if [item["event_key"] for item in intents] != sorted(item["event_key"] for item in intents):
            _invalid("invalid_v6_terminal_manifest", "intent_specs must be sorted by event_key")
        expected_bindings = {
            "answer:final": ("workflow.final_assistant", final_ref),
            "run:terminal": ("workflow.final", None),
        }
        if cardinality["artifact"] == 1:
            expected_bindings["artifact:report"] = ("workflow.artifact_card", report_ref)
        if set(item["event_key"] for item in intents) != set(expected_bindings):
            _invalid("invalid_v6_terminal_manifest", "intent cardinality differs from manifest")
        for item in intents:
            event_key = str(item["event_key"])
            event_type, content_ref = expected_bindings[event_key]
            if item["event_type"] != event_type or item["content_ref"] != content_ref:
                _invalid("invalid_v6_terminal_manifest", "intent content binding differs")
            if item["intent_id"] != _intent_id(run_id, event_key, event_type, content_ref):
                _invalid("invalid_v6_terminal_manifest", "intent_id mismatch")
            specs = item["delivery_specs"]
            if not isinstance(specs, list) or len(specs) != 2:
                _invalid("invalid_v6_terminal_manifest", "each v6 intent requires two delivery specs")
            for spec in specs:
                parsed = _exact(spec, _DELIVERY_KEYS, "delivery_spec")
                spec_id = parsed.pop("delivery_spec_id")
                if spec_id != _delivery_spec(str(item["intent_id"]), **parsed)["delivery_spec_id"]:
                    _invalid("invalid_v6_terminal_manifest", "delivery_spec_id mismatch")
        expected_id = "tdm_" + manifest_identity_seed(raw)[:24]
        if raw["manifest_id"] != expected_id:
            _invalid("invalid_v6_terminal_manifest", "manifest_id mismatch")
        object.__setattr__(self, "value", raw)

    @classmethod
    def create(cls, **values: JsonValue) -> TerminalDeliveryManifestV1:
        raw = copy.deepcopy(values)
        raw["schema_version"] = 1
        raw.pop("manifest_id", None)
        raw["manifest_id"] = "tdm_" + manifest_identity_seed(raw)[:24]
        return cls(raw)

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> TerminalDeliveryManifestV1:
        return cls(copy.deepcopy(dict(value)))

    def to_json(self) -> dict[str, JsonValue]:
        return copy.deepcopy(self.value)

    @property
    def manifest_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.value).encode("utf-8")).hexdigest()

    @property
    def manifest_ref(self) -> str:
        return format_blob_ref(self.manifest_hash)


@dataclass(frozen=True, slots=True)
class TerminalCommitRequestV1:
    value: dict[str, JsonValue]

    def __post_init__(self) -> None:
        raw = _exact(self.value, _REQUEST_KEYS, "TerminalCommitRequestV1", versioned=True)
        if raw["workflow_name"] != "deep_research" or raw["workflow_version"] != "v6":
            _invalid("invalid_v6_terminal_commit", "request workflow identity is invalid")
        _text(raw["run_id"], "run_id")
        digest = _hash(raw["manifest_hash"], "manifest_hash")
        if parse_blob_ref(_text(raw["manifest_ref"], "manifest_ref")) != digest:
            _invalid("invalid_v6_terminal_commit", "manifest ref/hash mismatch")
        if raw["engine_status"] != "completed" or raw["engine_error_code"] is not None or raw["recovery_action"] is not None:
            _invalid("invalid_v6_terminal_commit", "answer commit requires completed engine tuple")
        object.__setattr__(self, "value", raw)

    @classmethod
    def create(cls, **values: JsonValue) -> TerminalCommitRequestV1:
        return cls({"schema_version": 1, **copy.deepcopy(values)})

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> TerminalCommitRequestV1:
        return cls(copy.deepcopy(dict(value)))

    def to_json(self) -> dict[str, JsonValue]:
        return copy.deepcopy(self.value)


@dataclass(frozen=True, slots=True)
class PersistedTerminalBundle:
    manifest_ref: str
    manifest_hash: str
    blob_refs: tuple[str, ...]


async def _put_json(blobs: RegisteredBlobStore, identity: NodeExecutionIdentity, value: JsonValue) -> str:
    data = canonical_json(value).encode("utf-8")
    ref = await blobs.put(data, identity, media_type="application/json")
    return format_blob_ref(ref.sha256)


async def _put_text(blobs: RegisteredBlobStore, identity: NodeExecutionIdentity, text: str) -> str:
    data = text.encode("utf-8")
    if data.decode("utf-8").encode("utf-8") != data:
        _invalid("invalid_v6_terminal_content", "text is not strict UTF-8")
    ref = await blobs.put(data, identity, media_type="text/markdown; charset=utf-8")
    return format_blob_ref(ref.sha256)


async def persist_q1_terminal_bundle(
    *,
    state: Mapping[str, JsonValue],
    blobs: RegisteredBlobStore,
    identity: NodeExecutionIdentity,
    artifact_required: bool = True,
) -> PersistedTerminalBundle:
    """Persist a Q1 answer closure and return the two checkpoint pointers."""

    values = state.get("values")
    if not isinstance(values, Mapping):
        _invalid("invalid_v6_terminal_content", "state.values is required")
    if state.get("workflow_name") != "deep_research" or state.get("workflow_version") != "v6":
        _invalid("invalid_v6_terminal_content", "state workflow identity must be deep_research/v6")
    if identity.run_id != state.get("run_id"):
        _invalid("invalid_v6_terminal_content", "blob identity belongs to another run")
    answer_status = values.get("answer_status")
    if answer_status not in {"completed", "partial", "insufficient_evidence"}:
        _invalid("invalid_v6_terminal_content", "answer_status is invalid")
    final_assistant = _text(values.get("final_assistant"), "final_assistant")
    report = values.get("report_markdown")
    if answer_status == "insufficient_evidence":
        if report is not None:
            _invalid("invalid_v6_terminal_content", "insufficient answer cannot persist a report")
    elif not isinstance(report, str) or not report.strip():
        _invalid("invalid_v6_terminal_content", "completed/partial answer requires report")
    spec = values.get("research_spec")
    spec_hash = _hash(values.get("spec_hash"), "spec_hash")
    if not isinstance(spec, Mapping) or spec.get("spec_hash") != spec_hash:
        _invalid("invalid_v6_terminal_content", "research_spec/spec_hash mismatch")
    answer_result = values.get("answer_result")
    if not isinstance(answer_result, Mapping) or answer_result.get("answer_status") != answer_status:
        _invalid("invalid_v6_terminal_content", "answer_result/status mismatch")

    spec_ref = _text(values.get("spec_ref"), "spec_ref")
    if parse_blob_ref(spec_ref) == spec_hash:
        _invalid("invalid_v6_terminal_content", "spec semantic hash cannot replace blob ref")
    stored_spec = await _read_json(blobs, spec_ref)
    await _assert_current_run_owner(blobs, parse_blob_ref(spec_ref), identity.run_id)
    if stored_spec != dict(spec):
        _invalid("invalid_v6_terminal_content", "spec ref content differs from research_spec")
    final_ref = await _put_text(blobs, identity, final_assistant)
    report_ref = await _put_text(blobs, identity, str(report)) if report is not None else None
    raw_batch_refs = values.get("fact_batch_refs")
    if not isinstance(raw_batch_refs, list) or any(not isinstance(ref, str) for ref in raw_batch_refs):
        _invalid("invalid_v6_terminal_content", "fact_batch_refs must be an array of refs")
    fact_batch_refs = [str(ref) for ref in raw_batch_refs]
    for ref in fact_batch_refs:
        await _assert_current_run_owner(blobs, parse_blob_ref(ref), identity.run_id)
    evidence_head_hash = _hash(values.get("evidence_head_hash"), "evidence_head_hash")
    assessment_ref = _text(values.get("assessment_ref"), "assessment_ref")
    assessment_hash = _hash(values.get("assessment_hash"), "assessment_hash")
    assessment_value = await _read_json(blobs, assessment_ref)
    await _assert_current_run_owner(blobs, parse_blob_ref(assessment_ref), identity.run_id)
    if (
        assessment_value.get("assessment_hash") != assessment_hash
        or assessment_value.get("spec_hash") != spec_hash
        or assessment_value.get("evidence_head_hash") != evidence_head_hash
    ):
        _invalid("invalid_v6_terminal_content", "assessment ref/hash/evidence identity mismatch")
    raw_provenance = values.get("provenance_refs")
    if not isinstance(raw_provenance, list) or any(not isinstance(ref, str) for ref in raw_provenance):
        _invalid("invalid_v6_terminal_content", "provenance_refs must be an array of refs")
    provenance_refs = sorted(set(str(ref) for ref in raw_provenance))
    if provenance_refs != list(raw_provenance):
        _invalid("invalid_v6_terminal_content", "provenance_refs must be sorted unique")
    for ref in provenance_refs:
        await _assert_current_run_owner(blobs, parse_blob_ref(ref), identity.run_id)

    claim_policy: dict[str, JsonValue] = {"schema_version": 1, "policy_id": "deep-research-v6-claim-q1-v1"}
    quality_policy: dict[str, JsonValue] = {"schema_version": 1, "policy_id": "deep-research-v6-quality-q1-v1"}
    claim_policy_hash = sha256_json(claim_policy)
    quality_policy_hash = sha256_json(quality_policy)
    claim_policy_ref = await _put_json(blobs, identity, claim_policy)
    quality_policy_ref = await _put_json(blobs, identity, quality_policy)
    evidence_policy_refs = values.get("evidence_policy_refs")
    if not isinstance(evidence_policy_refs, Mapping) or set(evidence_policy_refs) != {
        "compiler", "route", "admission", "assessment"
    }:
        _invalid("invalid_v6_terminal_content", "evidence policy refs are incomplete")
    for ref in evidence_policy_refs.values():
        await _assert_current_run_owner(blobs, parse_blob_ref(str(ref)), identity.run_id)

    fact_batches = [await _read_json(blobs, ref) for ref in fact_batch_refs]
    raw_requirements = spec.get("requirements")
    if not isinstance(raw_requirements, list) or any(
        not isinstance(item, Mapping) or not isinstance(item.get("requirement_id"), str)
        for item in raw_requirements
    ):
        _invalid("invalid_v6_terminal_content", "spec requirements are invalid")
    raw_claims = answer_result.get("claims")
    if not isinstance(raw_claims, list):
        _invalid("invalid_v6_terminal_content", "answer_result.claims must be an array")
    integrity_claims = []
    for index, claim in enumerate(raw_claims):
        if not isinstance(claim, Mapping):
            _invalid("invalid_v6_terminal_content", f"claim {index} must be an object")
        integrity_claims.append(
            ClaimRecordV1.create(
                requirement_id=_text(claim.get("requirement_id"), f"claims[{index}].requirement_id"),
                item_or_cell_id=_text(
                    claim.get("requirement_id"), f"claims[{index}].requirement_id"
                ),
                claim_kind="fact",
                normalized_proposition=_text(
                    claim.get("normalized_proposition"),
                    f"claims[{index}].normalized_proposition",
                ),
                binding_ids=(
                    _text(claim.get("binding_id"), f"claims[{index}].binding_id"),
                ),
                inference_ref=None,
                support_status=_text(
                    claim.get("support_status"), f"claims[{index}].support_status"
                ),
                visibility="user",
            )
        )
    integrity = build_q1_exact_scalar_integrity(
        run_id=str(state["run_id"]),
        spec_hash=spec_hash,
        required_requirement_ids=[str(item["requirement_id"]) for item in raw_requirements],
        answer_assessment=assessment_value,
        evidence_fact_batches=fact_batches,
        claims=integrity_claims,
        requested_answer_status=str(answer_status),
        claim_policy_hash=claim_policy_hash,
        quality_policy_hash=quality_policy_hash,
        soft_scores={
            "readability": 1_000_000,
            "source_diversity": 0,
            "analysis_depth": 0,
            "counterevidence": 0,
            "uncertainty": 0,
        },
    )
    if integrity.answer_status != answer_status:
        _invalid(
            "invalid_v6_terminal_content",
            "hard integrity gate answer status differs from rendered content",
        )
    claim_batch = integrity.claim_batch.to_json()
    claim_batch_ref = await _put_json(blobs, identity, claim_batch)
    quality_audit = integrity.quality_audit.to_json()
    if quality_audit["claim_batch_ref"] != claim_batch_ref:
        _invalid("invalid_v6_terminal_content", "quality audit claim ref mismatch")
    quality_audit_ref = await _put_json(blobs, identity, quality_audit)

    policy_refs: dict[str, JsonValue] = {
        "compiler": str(evidence_policy_refs["compiler"]),
        "route": str(evidence_policy_refs["route"]),
        "admission": str(evidence_policy_refs["admission"]),
        "assessment": str(evidence_policy_refs["assessment"]),
        "claim": claim_policy_ref,
        "quality": quality_policy_ref,
    }
    closure_refs = sorted({
        spec_ref, *fact_batch_refs, assessment_ref, claim_batch_ref, *provenance_refs,
        *[str(value) for value in policy_refs.values()],
    })
    snapshot_base: dict[str, JsonValue] = {
        "schema_version": 1, "run_id": str(state["run_id"]),
        "workflow_name": "deep_research", "workflow_version": "v6",
        "spec_ref": spec_ref, "spec_hash": spec_hash, "fact_batch_refs": fact_batch_refs,
        "evidence_head_hash": evidence_head_hash, "assessment_ref": assessment_ref,
        "assessment_hash": assessment_hash, "claim_batch_ref": claim_batch_ref,
        "provenance_refs": provenance_refs, "policy_refs": policy_refs,
        "closure_refs": closure_refs,
    }
    snapshot_hash = sha256_json(snapshot_base)
    snapshot = {
        **snapshot_base,
        "snapshot_id": "rcs_" + snapshot_hash[:24],
        "snapshot_hash": snapshot_hash,
    }
    snapshot_ref = await _put_json(blobs, identity, snapshot)

    content_refs: dict[str, JsonValue] = {
        "final_assistant_ref": final_ref,
        "report_ref": report_ref,
        "safe_summary_ref": final_ref if answer_status == "insufficient_evidence" else None,
        "claim_batch_ref": claim_batch_ref,
        "quality_audit_ref": quality_audit_ref,
    }
    artifact = bool(artifact_required and answer_status != "insufficient_evidence")
    cardinality: dict[str, JsonValue] = {
        "final_assistant": 1, "workflow_final_status": 1,
        "report": 0 if answer_status == "insufficient_evidence" else 1,
        "artifact": 1 if artifact else 0, "run_terminal": 1,
    }
    manifest = TerminalDeliveryManifestV1.create(
        workflow_name="deep_research", workflow_version="v6", run_id=str(state["run_id"]),
        answer_status=str(answer_status), spec_hash=spec_hash, assessment_hash=assessment_hash,
        claim_policy_hash=claim_policy_hash, quality_policy_hash=quality_policy_hash,
        continuation_snapshot_ref=snapshot_ref, continuation_snapshot_hash=snapshot_hash,
        content_refs=content_refs,
        intent_specs=build_intent_specs(
            run_id=str(state["run_id"]), final_assistant_ref=final_ref,
            report_ref=report_ref, artifact_required=artifact,
        ),
        cardinality=cardinality,
        engine_terminal={"status": "completed", "error_code": None, "recovery_action": None},
    )
    manifest_ref = await _put_json(blobs, identity, manifest.to_json())
    if manifest_ref != manifest.manifest_ref:
        _invalid("invalid_v6_terminal_manifest", "registered manifest digest mismatch")
    all_refs = sorted({manifest_ref, snapshot_ref, *closure_refs, *[str(value) for value in content_refs.values() if value is not None]})
    return PersistedTerminalBundle(manifest_ref, manifest.manifest_hash, tuple(all_refs))


def build_terminal_commit_request(
    *, state: Mapping[str, JsonValue], engine_status: str, engine_error_code: str | None,
    recovery_action: str | None,
) -> TerminalCommitRequestV1:
    values = state.get("values")
    if not isinstance(values, Mapping):
        _invalid("invalid_v6_terminal_commit", "state.values is missing")
    manifest_ref = values.get("terminal_manifest_ref")
    manifest_hash = values.get("terminal_manifest_hash")
    return TerminalCommitRequestV1.create(
        workflow_name=str(state.get("workflow_name") or ""),
        workflow_version=str(state.get("workflow_version") or ""),
        run_id=str(state.get("run_id") or ""),
        manifest_ref=manifest_ref,
        manifest_hash=manifest_hash,
        engine_status=engine_status,
        engine_error_code=engine_error_code,
        recovery_action=recovery_action,
    )


async def _read_json(blobs: RegisteredBlobStore, wire_ref: str) -> dict[str, Any]:
    digest = parse_blob_ref(wire_ref)
    data = await blobs.get(digest)
    if hashlib.sha256(data).hexdigest() != digest:
        _invalid("invalid_v6_terminal_commit", "blob digest mismatch")
    try:
        decoded = data.decode("utf-8")
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _invalid("invalid_v6_terminal_commit", "canonical JSON blob is unreadable")
    if not isinstance(value, dict) or canonical_json(value).encode("utf-8") != data:
        _invalid("invalid_v6_terminal_commit", "blob is not canonical JSON")
    return value


async def _assert_current_run_owner(blobs: RegisteredBlobStore, digest: str, run_id: str) -> None:
    db = await aiosqlite.connect(blobs.database)
    try:
        row = await (await db.execute(
            """SELECT 1 FROM workflow_blob_refs r
            WHERE r.sha256=? AND (
              (r.owner_kind='run_staging' AND r.owner_id=?) OR
              (r.owner_kind='pending_task' AND r.owner_id LIKE ?) OR
              (r.owner_kind='checkpoint' AND EXISTS(
                SELECT 1 FROM workflow_checkpoint_owners c
                WHERE c.checkpoint_id=r.owner_id AND c.run_id=?
              )) OR
              (r.owner_kind='effect' AND EXISTS(
                SELECT 1 FROM workflow_effects e
                WHERE e.effect_id=r.owner_id AND e.run_id=?
              ))
            ) LIMIT 1""",
            (digest, run_id, f"{run_id}:%", run_id, run_id),
        )).fetchone()
    finally:
        await db.close()
    if row is None:
        _invalid("invalid_v6_terminal_commit", f"blob {digest} has no current-run owner")


def _terminal_public(answer_status: str, manifest_ref: str) -> dict[str, JsonValue]:
    actions: list[dict[str, JsonValue]] = []
    if answer_status in {"partial", "insufficient_evidence"}:
        actions.append({"action_id": "continue_research", "enabled": True, "reason_code": "evidence_gap"})
    value: dict[str, JsonValue] = {
        "schema_version": 1, "answer_status": answer_status,
        "manifest_ref": manifest_ref, "action_matrix": actions,
    }
    if len(canonical_json(value).encode("utf-8")) > 4096:
        _invalid("invalid_v6_terminal_projection", "terminal public exceeds 4096 bytes")
    return value


async def project_v6_terminal_commit(
    request_value: Mapping[str, JsonValue], context: WorkflowContext,
) -> dict[str, JsonValue]:
    request = TerminalCommitRequestV1.from_json(request_value)
    raw = request.to_json()
    blobs = context.ports.get("blob")
    if not isinstance(blobs, RegisteredBlobStore):
        _invalid("invalid_v6_terminal_commit", "v6 terminal commit requires RegisteredBlobStore")
    manifest_value = await _read_json(blobs, str(raw["manifest_ref"]))
    manifest = TerminalDeliveryManifestV1.from_json(manifest_value)
    if manifest.manifest_hash != raw["manifest_hash"] or manifest.manifest_ref != raw["manifest_ref"]:
        _invalid("invalid_v6_terminal_commit", "request does not identify loaded manifest")
    value = manifest.to_json()
    if value["run_id"] != raw["run_id"] or value["engine_terminal"] != {
        "status": raw["engine_status"],
        "error_code": raw["engine_error_code"],
        "recovery_action": raw["recovery_action"],
    }:
        _invalid("invalid_v6_terminal_commit", "manifest run/engine tuple mismatch")

    content = value["content_refs"]
    assert isinstance(content, dict)
    claim_batch = await _read_json(blobs, str(content["claim_batch_ref"]))
    quality = await _read_json(blobs, str(content["quality_audit_ref"]))
    if claim_batch.get("status") != "valid":
        _invalid("invalid_v6_terminal_commit", "claim batch is not valid")
    if quality.get("hard_gate_status") != "passed" or quality.get("answer_status") != value["answer_status"]:
        _invalid("invalid_v6_terminal_commit", "quality audit does not admit answer status")
    snapshot = await _read_json(blobs, str(value["continuation_snapshot_ref"]))
    snapshot_without_identity = dict(snapshot)
    snapshot_id = snapshot_without_identity.pop("snapshot_id", None)
    snapshot_hash = snapshot_without_identity.pop("snapshot_hash", None)
    expected_snapshot_hash = sha256_json(snapshot_without_identity)
    if snapshot_hash != expected_snapshot_hash or snapshot_id != "rcs_" + expected_snapshot_hash[:24] or snapshot_hash != value["continuation_snapshot_hash"]:
        _invalid("invalid_v6_terminal_commit", "continuation snapshot identity mismatch")
    closure = snapshot.get("closure_refs")
    if not isinstance(closure, list) or closure != sorted(set(closure)):
        _invalid("invalid_v6_terminal_commit", "snapshot closure_refs are not canonical")
    expected_closure = {
        str(snapshot.get("spec_ref")), str(snapshot.get("assessment_ref")),
        str(snapshot.get("claim_batch_ref")),
        *[str(item) for item in snapshot.get("fact_batch_refs", [])],
        *[str(item) for item in snapshot.get("provenance_refs", [])],
        *[str(item) for item in dict(snapshot.get("policy_refs") or {}).values()],
    }
    if set(closure) != expected_closure:
        _invalid("invalid_v6_terminal_commit", "snapshot closure is not exact")
    blob_refs = sorted({
        str(raw["manifest_ref"]), str(value["continuation_snapshot_ref"]),
        *[str(item) for item in closure],
        *[str(item) for item in content.values() if item is not None],
    })
    for wire_ref in blob_refs:
        digest = parse_blob_ref(wire_ref)
        await _assert_current_run_owner(blobs, digest, str(raw["run_id"]))
        await blobs.get(digest)

    public = _terminal_public(str(value["answer_status"]), str(raw["manifest_ref"]))
    intents: list[dict[str, JsonValue]] = []
    for intent in value["intent_specs"]:
        assert isinstance(intent, dict)
        if intent["event_key"] == "run:terminal":
            payload: dict[str, JsonValue] = {
                "schema_version": 1, "kind": "final", "status": "completed",
                "error": None, "recovery_action": None,
                "manifest_ref": str(raw["manifest_ref"]),
                "answer_status": str(value["answer_status"]), "public": public,
                "card": {
                    "run_id": str(raw["run_id"]), "status": "completed", "error": None,
                    "recovery_action": None, "manifest_ref": str(raw["manifest_ref"]),
                    "answer_status": str(value["answer_status"]),
                },
            }
            if len(canonical_json(payload).encode("utf-8")) > 8192:
                _invalid("invalid_v6_terminal_projection", "workflow final payload exceeds 8192 bytes")
        else:
            payload = {
                "schema_version": 1, "manifest_ref": str(raw["manifest_ref"]),
                "intent_id": str(intent["intent_id"]), "content_ref": str(intent["content_ref"]),
            }
        intents.append({
            "intent_id": str(intent["intent_id"]), "event_key": str(intent["event_key"]),
            "event_type": str(intent["event_type"]), "content_ref": intent["content_ref"],
            "delivery_specs": copy.deepcopy(intent["delivery_specs"]), "payload": payload,
        })
    projection: dict[str, JsonValue] = {
        "schema_version": 1, "manifest_ref": str(raw["manifest_ref"]),
        "manifest_hash": str(raw["manifest_hash"]), "answer_status": str(value["answer_status"]),
        "blob_refs": blob_refs, "intents": intents,
    }
    _exact(projection, _PROJECTION_KEYS, "TerminalCommitProjectionV1", versioned=True)
    validate_json_value(projection)
    return projection


__all__ = [
    "PersistedTerminalBundle", "TerminalCommitRequestV1", "TerminalDeliveryManifestV1",
    "build_intent_specs", "build_terminal_commit_request", "persist_q1_terminal_bundle",
    "project_v6_terminal_commit",
]
