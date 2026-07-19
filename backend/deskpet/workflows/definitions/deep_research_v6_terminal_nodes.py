"""Generic terminal nodes for the final DeepResearch v6 evidence chain.

The three handlers in this module are deliberately ref-only.  They reload the
registered fact/inference/assessment frontier, deterministically derive claims
and rendered bytes, recompute the generic hard gate, and finally persist the
exact continuation snapshot before the terminal manifest.  No Q1-only helper
is called from this module.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import aiosqlite

from ..contracts import (
    JsonValue,
    NodeExecutionIdentity,
    StatePatch,
    WorkflowContext,
    WorkflowState,
    canonical_json,
    validate_json_value,
)
from ..store import RegisteredBlobStore
from .deep_research_v6_assessment import (
    GenericAdmittedFactV1,
    GenericAdmittedInferenceV1,
    assess_requirements,
    decode_assessment_inputs,
)
from .deep_research_v6_contracts import ResearchSpecV1, format_blob_ref, parse_blob_ref, sha256_json
from .deep_research_v6_control import consume_v6_control
from .deep_research_v6_delivery import TerminalDeliveryManifestV1, build_intent_specs
from .deep_research_v6_evidence import (
    AdmittedResearchFactV1,
    AnswerAssessmentV1,
    EvidenceFactBatchV1,
    GENESIS_EVIDENCE_HEAD,
    RegisteredInferenceV1,
    derive_assessment_input_hash,
)
from .deep_research_v6_evidence_contracts import V6FetchedPageRefPayloadV1
from .deep_research_v6_retrieval_contracts import PageExtractionResultV1
from .deep_research_v6_integrity import ClaimBatchV1, ClaimRecordV1, QualityAuditV1
from .deep_research_v6_report import CitationView, TypedReportResult, render_typed_report


CLAIM_POLICY_V1: dict[str, JsonValue] = {
    "schema_version": 1,
    "policy_id": "deep-research-v6-claim-v1",
    "derivation": "registered-fact-and-inference-only",
}
QUALITY_POLICY_V1: dict[str, JsonValue] = {
    "schema_version": 1,
    "policy_id": "deep-research-v6-quality-v1",
    "hard_gate": "generic-registered-closure-v1",
}

_POLICY_KEYS = {
    "compiler", "route", "extraction", "llm_extract", "llm_repair",
    "llm_inference", "admission", "inference", "assessment", "claim", "quality",
}
_RENDER_KEYS = {
    "schema_version", "bundle_id", "run_id", "spec_hash", "evidence_head_hash",
    "assessment_ref", "assessment_hash", "ordered_fact_refs", "ordered_inference_refs",
    "answer_status", "final_assistant_ref", "report_ref", "claims",
}
_SNAPSHOT_KEYS = {
    "schema_version", "snapshot_id", "snapshot_hash", "run_id", "workflow_name",
    "workflow_version", "spec_ref", "spec_hash", "fact_batch_refs", "evidence_head_hash",
    "assessment_ref", "assessment_hash", "assessment_input_hash", "claim_batch_ref",
    "provenance_refs", "policy_refs", "closure_refs",
}
_HEX = frozenset("0123456789abcdef")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be a canonical non-empty string")
    return value


def _digest(value: object, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(char not in _HEX for char in value):
        raise ValueError(f"{name} must be lowercase sha256")
    return value


def _wire_ref(value: object, name: str) -> str:
    value = _text(value, name)
    parse_blob_ref(value)
    return value


def _values(state: WorkflowState) -> dict[str, JsonValue]:
    values = state.get("values")
    if not isinstance(values, Mapping):
        raise ValueError("workflow values must be an object")
    return copy.deepcopy(dict(values))


def _runtime(
    state: WorkflowState, context: WorkflowContext,
) -> tuple[RegisteredBlobStore, NodeExecutionIdentity]:
    blobs = context.ports.get("blob")
    identity = context.identity
    if not isinstance(blobs, RegisteredBlobStore):
        raise ValueError("deep_research v6 terminal nodes require RegisteredBlobStore")
    if identity is None or identity.run_id != state.get("run_id"):
        raise ValueError("terminal node identity belongs to another run")
    if state.get("workflow_name") != "deep_research" or state.get("workflow_version") != "v6":
        raise ValueError("terminal nodes require deep_research/v6 state")
    return blobs, identity


async def _assert_owner(blobs: RegisteredBlobStore, digest: str, run_id: str) -> None:
    db = await aiosqlite.connect(blobs.database)
    try:
        row = await (await db.execute(
            """SELECT 1 FROM workflow_blob_refs r WHERE r.sha256=? AND (
              (r.owner_kind='run_staging' AND r.owner_id=?) OR
              (r.owner_kind='pending_task' AND r.owner_id LIKE ?) OR
              (r.owner_kind='checkpoint' AND EXISTS(
                SELECT 1 FROM workflow_checkpoint_owners c
                WHERE c.checkpoint_id=r.owner_id AND c.run_id=?)) OR
              (r.owner_kind='effect' AND EXISTS(
                SELECT 1 FROM workflow_effects e
                WHERE e.effect_id=r.owner_id AND e.run_id=?))) LIMIT 1""",
            (digest, run_id, f"{run_id}:%", run_id, run_id),
        )).fetchone()
    finally:
        await db.close()
    if row is None:
        raise ValueError(f"registered blob {digest} has no current-run owner")


async def _read_bytes(
    blobs: RegisteredBlobStore, identity: NodeExecutionIdentity, wire_ref: str,
) -> bytes:
    digest = parse_blob_ref(wire_ref)
    await _assert_owner(blobs, digest, identity.run_id)
    data = await blobs.get(digest)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("registered blob digest mismatch")
    return data


async def _read_json(
    blobs: RegisteredBlobStore, identity: NodeExecutionIdentity, wire_ref: str,
) -> dict[str, Any]:
    data = await _read_bytes(blobs, identity, wire_ref)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("registered blob is not canonical UTF-8 JSON") from exc
    if not isinstance(value, dict) or canonical_json(value).encode("utf-8") != data:
        raise ValueError("registered blob is not canonical JSON")
    return value


async def _put_json(
    blobs: RegisteredBlobStore,
    identity: NodeExecutionIdentity,
    value: Mapping[str, JsonValue],
    *,
    media_type: str,
) -> str:
    validate_json_value(dict(value))
    ref = await blobs.put(canonical_json(dict(value)).encode("utf-8"), identity, media_type=media_type)
    return format_blob_ref(ref.sha256)


async def _put_text(
    blobs: RegisteredBlobStore, identity: NodeExecutionIdentity, value: str,
) -> str:
    value = _text(value, "rendered text")
    ref = await blobs.put(value.encode("utf-8"), identity, media_type="text/markdown; charset=utf-8")
    return format_blob_ref(ref.sha256)


def _state_refs(state: WorkflowState) -> set[str]:
    raw = state.get("blob_refs", [])
    if not isinstance(raw, list):
        raise ValueError("blob_refs must be an array")
    refs: set[str] = set()
    for item in raw:
        digest = str(item.get("sha256") or item.get("id") or "") if isinstance(item, Mapping) else str(item)
        refs.add(format_blob_ref(_digest(digest, "blob_refs digest")))
    return refs


def _blob_items(refs: Iterable[str]) -> list[dict[str, JsonValue]]:
    return [
        {"id": digest, "sha256": digest}
        for digest in sorted({parse_blob_ref(ref) for ref in refs})
    ]


def _ref_list(value: object, name: str, *, ordered: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be an array of wire refs")
    result = tuple(str(item) for item in value)
    for ref in result:
        parse_blob_ref(ref)
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must not contain duplicates")
    if not ordered and result != tuple(sorted(result)):
        raise ValueError(f"{name} must be canonically sorted")
    return result


@dataclass(frozen=True, slots=True)
class _TerminalInputs:
    spec: ResearchSpecV1
    assessment: AnswerAssessmentV1
    batches: tuple[EvidenceFactBatchV1, ...]
    batch_refs: tuple[str, ...]
    fact_refs: tuple[str, ...]
    inference_refs: tuple[str, ...]
    facts: tuple[GenericAdmittedFactV1, ...]
    inferences: tuple[GenericAdmittedInferenceV1, ...]
    registered: dict[str, dict[str, JsonValue]]
    policy_refs: dict[str, str]


async def _load_terminal_inputs(
    values: Mapping[str, JsonValue],
    blobs: RegisteredBlobStore,
    identity: NodeExecutionIdentity,
) -> _TerminalInputs:
    spec_ref = _wire_ref(values.get("spec_ref"), "spec_ref")
    spec = ResearchSpecV1.from_json(await _read_json(blobs, identity, spec_ref))
    if spec.spec_hash != values.get("spec_hash"):
        raise ValueError("spec ref/hash checkpoint mismatch")

    batch_refs = _ref_list(values.get("fact_batch_refs"), "fact_batch_refs", ordered=True)
    batches: list[EvidenceFactBatchV1] = []
    fact_refs: list[str] = []
    inference_refs: list[str] = []
    previous = GENESIS_EVIDENCE_HEAD
    policy_refs: dict[str, str] | None = None
    inherited_count_raw = values.get("inherited_fact_batch_count", 0)
    if (
        isinstance(inherited_count_raw, bool)
        or not isinstance(inherited_count_raw, int)
        or inherited_count_raw < 0
        or inherited_count_raw > len(batch_refs)
    ):
        raise ValueError("inherited fact batch count is invalid")
    inherited_count = inherited_count_raw
    parent_run_id = values.get("continuation_parent_run_id")
    if inherited_count and (not isinstance(parent_run_id, str) or not parent_run_id):
        raise ValueError("inherited fact batches require a continuation parent")
    for ordinal, ref in enumerate(batch_refs):
        batch = EvidenceFactBatchV1.from_json(await _read_json(blobs, identity, ref))
        if (
            (ordinal >= inherited_count and batch.run_id != identity.run_id)
            or batch.spec_hash != spec.spec_hash
            or batch.ordinal != ordinal
            or batch.previous_head_hash != previous
        ):
            raise ValueError("fact/inference batch chain identity mismatch")
        if policy_refs is None:
            policy_refs = dict(batch.policy_refs)
        elif dict(batch.policy_refs) != policy_refs:
            raise ValueError("batch policy refs changed within one evidence chain")
        fact_refs.extend(batch.admitted_fact_refs)
        inference_refs.extend(batch.registered_inference_refs)
        previous = batch.head_hash
        batches.append(batch)
    if not batches or previous != values.get("evidence_head_hash"):
        raise ValueError("terminal evidence head is absent or differs from checkpoint")
    if policy_refs is None or set(policy_refs) != _POLICY_KEYS:
        raise ValueError("terminal policy refs are incomplete")
    checkpoint_policy_refs = values.get("policy_refs")
    if checkpoint_policy_refs is not None and checkpoint_policy_refs != policy_refs:
        raise ValueError("checkpoint policy refs differ from batch policy refs")

    registered: dict[str, dict[str, JsonValue]] = {}
    for ref in (*fact_refs, *inference_refs):
        if ref in registered:
            raise ValueError("ordered semantic refs contain a duplicate")
        registered[ref] = await _read_json(blobs, identity, ref)
    for ref in fact_refs:
        fact = AdmittedResearchFactV1.from_json(registered[ref])
        if fact.admission_policy_hash != parse_blob_ref(policy_refs["admission"]):
            raise ValueError("admitted fact policy hash differs from registered policy ref")
    for ref in inference_refs:
        inference = RegisteredInferenceV1.from_json(registered[ref])
        if inference.inference_policy_hash != parse_blob_ref(policy_refs["inference"]):
            raise ValueError("registered inference policy hash differs from registered policy ref")
        if inference.model_policy_ref not in {
            policy_refs["llm_inference"], policy_refs["llm_repair"],
        }:
            raise ValueError("registered inference model profile is absent from policy closure")
    facts, inferences = decode_assessment_inputs(
        fact_refs, inference_refs, spec, registered_objects=registered,
    )
    assessment_ref = _wire_ref(values.get("assessment_ref"), "assessment_ref")
    assessment = AnswerAssessmentV1.from_json(await _read_json(blobs, identity, assessment_ref))
    if (
        assessment.assessment_hash != values.get("assessment_hash")
        or assessment.spec_hash != spec.spec_hash
        or assessment.evidence_head_hash != previous
    ):
        raise ValueError("assessment ref/hash/frontier mismatch")
    if assessment.policy_hash != parse_blob_ref(policy_refs["assessment"]):
        raise ValueError("assessment policy hash differs from registered policy ref")
    expected_input_hash = derive_assessment_input_hash(
        spec_hash=spec.spec_hash,
        evidence_head_hash=previous,
        ordered_fact_refs=fact_refs,
        ordered_inference_refs=inference_refs,
        assessment_policy_hash=assessment.policy_hash,
    )
    if assessment.assessment_input_hash != expected_input_hash:
        raise ValueError("assessment input hash differs from ordered semantic refs")
    recomputed = assess_requirements(
        spec=spec,
        evidence_head_hash=previous,
        facts=facts,
        inferences=inferences,
        ordered_fact_refs=fact_refs,
        ordered_inference_refs=inference_refs,
        policy_hash=assessment.policy_hash,
    )
    if recomputed.to_json() != assessment.to_json():
        raise ValueError("persisted assessment differs from deterministic recomputation")
    return _TerminalInputs(
        spec, assessment, tuple(batches), batch_refs, tuple(fact_refs), tuple(inference_refs),
        facts, inferences, registered, policy_refs,
    )


def _fact_proposition(fact: AdmittedResearchFactV1) -> str:
    payload = fact.semantic_payload
    key = fact.field_or_facet_key or payload.get("definition") or fact.requirement_id
    return f"{key}: {canonical_json(payload['value'])}"


_ZH_EXACT_FACT_LABELS = {
    "year_end_total_population": "年末总人口",
    "births_during_period": "全年出生人口",
}


def _zh_exact_time_label(value: object) -> str:
    if isinstance(value, Mapping):
        kind = value.get("kind")
        if kind == "instant":
            as_of = value.get("as_of")
            if isinstance(as_of, str) and len(as_of) >= 4 and as_of[:4].isdigit():
                return f"{as_of[:4]}年末"
            return "已声明时点"
        if kind == "period":
            start = value.get("start")
            end = value.get("end")
            if (
                isinstance(start, str)
                and isinstance(end, str)
                and len(start) >= 4
                and start[:4].isdigit()
                and end.startswith(start[:4])
            ):
                return f"{start[:4]}年全年"
            return "已声明期间"
        value = value.get("label")
    if isinstance(value, str):
        if value.endswith(" year end") and value[:-9].isdigit():
            return f"{value[:-9]}年末"
        if value.endswith((" calendar year", " full year")):
            year = value.split(" ", 1)[0]
            if year.isdigit():
                return f"{year}年全年"
    return "已声明时间"


def _zh_exact_scope_label(value: object) -> str:
    if isinstance(value, Mapping):
        jurisdiction = value.get("jurisdiction")
        geography_ids = value.get("geography_ids")
        if jurisdiction == "CN" or (
            isinstance(geography_ids, list) and "CN" in geography_ids
        ):
            return "全国"
    if isinstance(value, str) and value in {"China", "CN", "national"}:
        return "全国"
    return "指定范围"


def _zh_exact_population_value(payload: Mapping[str, JsonValue]) -> str:
    value = payload.get("value")
    unit = payload.get("canonical_unit")
    if isinstance(value, int) and not isinstance(value, bool):
        if unit == "person":
            if value % 10_000 == 0:
                return f"{value // 10_000} 万人"
            return f"{value} 人"
        if unit in {"ten_thousand_person", "ten_thousand_persons"}:
            return f"{value} 万人"
    return "数值不可用"


def _zh_exact_fact_proposition(fact: AdmittedResearchFactV1) -> str | None:
    payload = fact.semantic_payload
    label = _ZH_EXACT_FACT_LABELS.get(str(payload.get("definition")))
    if label is None:
        return None
    return (
        f"{label}：{_zh_exact_population_value(payload)}"
        f"（时间：{_zh_exact_time_label(payload.get('time_scope'))}；"
        f"范围：{_zh_exact_scope_label(payload.get('scope'))}）"
    )


def derive_registered_claims(inputs: _TerminalInputs) -> tuple[ClaimRecordV1, ...]:
    """Derive the only admissible claim set from registered semantic owners."""

    results = {
        (str(item["requirement_id"]), str(item["item_or_cell_id"])): item
        for item in inputs.assessment.requirement_results
    }
    records: list[ClaimRecordV1] = []
    for ref in inputs.fact_refs:
        fact = AdmittedResearchFactV1.from_json(inputs.registered[ref])
        item_id = fact.requirement_id if fact.target_kind == "scalar" else fact.item_or_cell_id
        result = results.get((fact.requirement_id, str(item_id)))
        supported = (
            fact.status == "admitted"
            and result is not None
            and result["support_status"] == "supported"
            and fact.binding_id in result["binding_ids"]
        )
        support_only = (
            inputs.spec.intent_type == "open_research"
            and fact.target_kind == "claim_fact"
            and fact.semantic_payload.get("claim_kind") == "policy_fact"
        )
        records.append(ClaimRecordV1.create(
            requirement_id=fact.requirement_id,
            item_or_cell_id=item_id,
            claim_kind="fact",
            normalized_proposition=_fact_proposition(fact),
            binding_ids=(fact.binding_id,),
            inference_ref=None,
            support_status="supported" if supported else ("conflicted" if fact.status == "conflicted" else "unsupported"),
            visibility="user" if supported and not support_only else "audit",
        ))
    inference_kind = {
        "impact": "inference", "comparison": "inference", "preference": "preference",
        "conclusion": "inference", "limitation": "limitation",
        "counterevidence": "counterevidence", "uncertainty": "uncertainty",
    }
    for ref in inputs.inference_refs:
        inference = RegisteredInferenceV1.from_json(inputs.registered[ref])
        if inference.status != "registered":
            continue
        result = results.get((inference.requirement_id, str(inference.item_or_cell_id)))
        supported = (
            result is not None
            and result["support_status"] == "supported"
            and set(inference.premise_binding_ids).issubset(set(result["binding_ids"]))
        )
        records.append(ClaimRecordV1.create(
            requirement_id=inference.requirement_id,
            item_or_cell_id=inference.item_or_cell_id,
            claim_kind=inference_kind[inference.inference_kind],
            normalized_proposition=inference.normalized_proposition,
            binding_ids=inference.premise_binding_ids,
            inference_ref=ref,
            support_status="supported" if supported else "unsupported",
            visibility="user" if supported else "audit",
        ))
    return tuple(sorted(records, key=lambda item: item.claim_id))


def _renderer_facts(inputs: _TerminalInputs) -> tuple[GenericAdmittedFactV1, ...]:
    # Open-research persisted policy facts are premise evidence only.  Its
    # renderer owns the typed conclusion/limitation/counterevidence/
    # uncertainty surface and must not treat support-only facts as claims.
    projected = [] if inputs.spec.intent_type == "open_research" else list(inputs.facts)
    requirement_kinds = {
        str(item["requirement_id"]): str(item["kind"])
        for item in inputs.spec.requirements
    }
    for item in inputs.inferences:
        # Collection ranking has a single fact/binding owner.  A preference
        # inference is a claim over the ranked result, not another collection
        # field and therefore must not enter rank_collection_item_ids().
        if requirement_kinds[item.requirement_id] == "collection":
            continue
        projected.append(GenericAdmittedFactV1.create(
            requirement_id=item.requirement_id,
            item_or_cell_id=item.item_or_cell_id,
            binding_ids=item.premise_binding_ids,
            claim_kind=item.assessment_claim_kind,
            facet_ids=item.facet_ids,
            inference_ref=item.inference_ref,
        ))
    return tuple(projected)


def _render_exact(
    inputs: _TerminalInputs,
    claims: Sequence[ClaimRecordV1],
    citations: Mapping[str, CitationView],
) -> TypedReportResult:
    requirement_order = {
        str(item["requirement_id"]): int(item["ordinal"])
        for item in inputs.spec.requirements
    }
    visible = tuple(
        claim for claim in claims
        if claim.visibility == "user" and claim.support_status == "supported"
    )
    display_claims = tuple(sorted(
        visible,
        key=lambda claim: (requirement_order[claim.requirement_id], claim.claim_id),
    ))
    status = {
        "completed_candidate": "completed",
        "partial_candidate": "partial",
        "insufficient": "insufficient_evidence",
        "needs_evidence": "insufficient_evidence",
    }[inputs.assessment.status]
    if status == "insufficient_evidence":
        summary = "没有足够的已准入证据来形成可靠答案。" if inputs.spec.answer_locale.startswith("zh") else "There is not enough admitted evidence to provide a reliable answer."
        return TypedReportResult(status, summary, None, (), inputs.assessment.missing_requirement_ids)
    lines = []
    localize_exact = inputs.spec.answer_locale.startswith("zh")
    for claim in display_claims:
        fact = None
        for ref in inputs.fact_refs:
            candidate_fact = AdmittedResearchFactV1.from_json(inputs.registered[ref])
            if candidate_fact.binding_id in claim.binding_ids:
                fact = candidate_fact
                break
        qualifiers = ""
        if fact is not None:
            payload = fact.semantic_payload
            time_scope = payload.get("time_scope")
            time_label = (
                time_scope.get("label")
                if isinstance(time_scope, Mapping)
                else time_scope
            )
            qualifiers = (
                f" [unit={payload.get('canonical_unit')}; "
                f"time={time_label}; scope={canonical_json(payload.get('scope'))}; "
                f"definition={payload.get('definition')}]"
            )
        citation = citations.get(claim.claim_id)
        suffix = (
            f" ([{citation.title}]({citation.url}))" if citation is not None else ""
        )
        localized = (
            _zh_exact_fact_proposition(fact)
            if localize_exact and fact is not None
            else None
        )
        if localized is not None:
            lines.append(f"- {localized}{suffix}")
        else:
            lines.append(f"- {claim.normalized_proposition}{qualifiers}{suffix}")
    heading = "结论" if localize_exact else "Answer"
    report_heading = "调研报告" if localize_exact else "Research report"
    final = f"## {heading}\n\n" + "\n".join(lines)
    return TypedReportResult(status, final, f"# {report_heading}\n\n{final}", visible, inputs.assessment.missing_requirement_ids)


def _render(
    inputs: _TerminalInputs,
    claims: Sequence[ClaimRecordV1],
    citations: Mapping[str, CitationView] | None = None,
) -> TypedReportResult:
    citations = citations or {}
    if inputs.spec.intent_type == "official_exact_fact":
        return _render_exact(inputs, claims, citations)
    return render_typed_report(
        spec=inputs.spec,
        assessment=inputs.assessment,
        admitted_facts=_renderer_facts(inputs),
        claims=claims,
        citations=citations,
    )


async def _citation_views(
    inputs: _TerminalInputs,
    claims: Sequence[ClaimRecordV1],
    blobs: RegisteredBlobStore,
    identity: NodeExecutionIdentity,
) -> dict[str, CitationView]:
    page_sources: dict[str, CitationView] = {}
    for batch in inputs.batches:
        for page_ref in batch.page_result_refs:
            result = await _read_json(blobs, identity, page_ref)
            if "result_id" in result:
                typed = PageExtractionResultV1.from_json(result)
                if typed.outcome != "succeeded" or typed.page_record is None:
                    continue
                page_id = str(typed.page_record["page_id"])
                locator_ref = typed.source_locator_ref
                authority_id = str(typed.page_record["authority_id"])
            else:
                page_value = result.get("page_ref")
                page = V6FetchedPageRefPayloadV1.from_json(
                    page_value if isinstance(page_value, Mapping) else result
                )
                page_id = page.page_id
                locator_ref = page.source_locator_ref
                authority_id = page.authority_id
            locator = await _read_json(blobs, identity, locator_ref)
            url = locator.get("canonical_url")
            if not isinstance(url, str) or not url.startswith(("https://", "http://")):
                raise ValueError("citation locator has no canonical HTTP(S) URL")
            title = locator.get("title")
            if not isinstance(title, str) or not title.strip():
                title = str(locator.get("authority_id") or authority_id)
            if inputs.spec.answer_locale.startswith("zh") and title.strip() == "cn.nbs":
                title = "国家统计局"
            page_sources[page_id] = CitationView(title=title, url=url)

    page_by_binding: dict[str, str] = {}
    for ref in inputs.fact_refs:
        fact = AdmittedResearchFactV1.from_json(inputs.registered[ref])
        page_by_binding[fact.binding_id] = fact.page_id
    views: dict[str, CitationView] = {}
    for claim in claims:
        for binding_id in sorted(claim.binding_ids):
            page_id = page_by_binding.get(binding_id)
            if page_id in page_sources:
                views[claim.claim_id] = page_sources[page_id]
                break
    return views


def _render_base(
    *, inputs: _TerminalInputs, final_ref: str, report_ref: str | None,
    result: TypedReportResult,
) -> dict[str, JsonValue]:
    return {
        "schema_version": 1,
        "run_id": inputs.batches[0].run_id,
        "spec_hash": inputs.spec.spec_hash,
        "evidence_head_hash": inputs.assessment.evidence_head_hash,
        "assessment_ref": "",  # filled by handler from checkpoint owner
        "assessment_hash": inputs.assessment.assessment_hash,
        "ordered_fact_refs": list(inputs.fact_refs),
        "ordered_inference_refs": list(inputs.inference_refs),
        "answer_status": result.answer_status,
        "final_assistant_ref": final_ref,
        "report_ref": report_ref,
        "claims": [claim.to_json() for claim in result.claims],
    }


def _render_bundle(value: Mapping[str, Any]) -> dict[str, JsonValue]:
    if set(value) != _RENDER_KEYS or value.get("schema_version") != 1:
        raise ValueError("RenderedClaimsV1 keys/version differ")
    raw = copy.deepcopy(dict(value))
    identity = raw.pop("bundle_id")
    expected = "rcl_" + sha256_json(raw)[:24]
    if identity != expected:
        raise ValueError("RenderedClaimsV1 bundle_id mismatch")
    _wire_ref(raw["assessment_ref"], "render assessment_ref")
    _wire_ref(raw["final_assistant_ref"], "render final_assistant_ref")
    if raw["report_ref"] is not None:
        _wire_ref(raw["report_ref"], "render report_ref")
    _ref_list(raw["ordered_fact_refs"], "render ordered_fact_refs", ordered=True)
    _ref_list(raw["ordered_inference_refs"], "render ordered_inference_refs", ordered=True)
    if not isinstance(raw["claims"], list):
        raise ValueError("render claims must be an array")
    claims = [ClaimRecordV1.from_json(item) for item in raw["claims"]]
    if [item.claim_id for item in claims] != sorted({item.claim_id for item in claims}):
        raise ValueError("render claims must be unique and sorted")
    return copy.deepcopy(dict(value))


async def render_claims_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    blobs, identity = _runtime(state, context)
    inputs = await _load_terminal_inputs(values, blobs, identity)
    claims = derive_registered_claims(inputs)
    citations = await _citation_views(inputs, claims, blobs, identity)
    result = _render(inputs, claims, citations)
    final_ref = await _put_text(blobs, identity, result.final_assistant)
    report_ref = (
        await _put_text(blobs, identity, result.report_markdown)
        if result.report_markdown is not None else None
    )
    base = _render_base(inputs=inputs, final_ref=final_ref, report_ref=report_ref, result=result)
    base["assessment_ref"] = _wire_ref(values.get("assessment_ref"), "assessment_ref")
    bundle = {**base, "bundle_id": "rcl_" + sha256_json(base)[:24]}
    _render_bundle(bundle)
    bundle_ref = await _put_json(
        blobs, identity, bundle,
        media_type="application/vnd.deskpet.deepresearch-v6-rendered-claims+json",
    )
    values["render_bundle_ref"] = bundle_ref
    values["answer_status"] = result.answer_status
    values["stage"] = "claims_rendered"
    closure = _state_refs(state) | {bundle_ref, final_ref}
    if report_ref is not None:
        closure.add(report_ref)
    return StatePatch({"values": values, "blob_refs": _blob_items(closure)})


def _policy_hash(value: Mapping[str, JsonValue]) -> str:
    return hashlib.sha256(canonical_json(dict(value)).encode("utf-8")).hexdigest()


def _generic_integrity(
    *, run_id: str, inputs: _TerminalInputs, render: Mapping[str, JsonValue],
) -> tuple[ClaimBatchV1, QualityAuditV1]:
    claims = derive_registered_claims(inputs)
    rerendered = _render(inputs, claims)
    if (
        render["ordered_fact_refs"] != list(inputs.fact_refs)
        or render["ordered_inference_refs"] != list(inputs.inference_refs)
        or render["answer_status"] != rerendered.answer_status
        or render["assessment_hash"] != inputs.assessment.assessment_hash
        or render["spec_hash"] != inputs.spec.spec_hash
        or render["evidence_head_hash"] != inputs.assessment.evidence_head_hash
        or render["claims"] != [item.to_json() for item in rerendered.claims]
    ):
        raise ValueError("render bundle differs from deterministic semantic replay")
    claim_policy_hash = _policy_hash(CLAIM_POLICY_V1)
    quality_policy_hash = _policy_hash(QUALITY_POLICY_V1)
    claim_batch = ClaimBatchV1.create(
        run_id=run_id,
        spec_hash=inputs.spec.spec_hash,
        evidence_head_hash=inputs.assessment.evidence_head_hash,
        assessment_hash=inputs.assessment.assessment_hash,
        claim_policy_hash=claim_policy_hash,
        claims=claims,
        status="valid",
    )
    claim_batch_ref = format_blob_ref(sha256_json(claim_batch.to_json()))
    audit = QualityAuditV1._create_derived(
        run_id=run_id,
        spec_hash=inputs.spec.spec_hash,
        assessment_hash=inputs.assessment.assessment_hash,
        claim_batch_ref=claim_batch_ref,
        quality_policy_hash=quality_policy_hash,
        hard_failure_codes=(),
        soft_scores={
            "readability": 1_000_000,
            "source_diversity": 0,
            "analysis_depth": 0,
            "counterevidence": 0,
            "uncertainty": 0,
        },
        repair_count=0,
        answer_status=rerendered.answer_status,
    )
    return claim_batch, audit


async def _assert_policy_objects(
    inputs: _TerminalInputs, blobs: RegisteredBlobStore, identity: NodeExecutionIdentity,
) -> None:
    claim = await _read_json(blobs, identity, inputs.policy_refs["claim"])
    quality = await _read_json(blobs, identity, inputs.policy_refs["quality"])
    if claim != CLAIM_POLICY_V1 or quality != QUALITY_POLICY_V1:
        raise ValueError("terminal claim/quality policy bytes differ from release policy")


async def integrity_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    blobs, identity = _runtime(state, context)
    inputs = await _load_terminal_inputs(values, blobs, identity)
    await _assert_policy_objects(inputs, blobs, identity)
    render_ref = _wire_ref(values.get("render_bundle_ref"), "render_bundle_ref")
    render = _render_bundle(await _read_json(blobs, identity, render_ref))
    final_bytes = await _read_bytes(blobs, identity, str(render["final_assistant_ref"]))
    report_bytes = (
        await _read_bytes(blobs, identity, str(render["report_ref"]))
        if render["report_ref"] is not None else None
    )
    claims = derive_registered_claims(inputs)
    citations = await _citation_views(inputs, claims, blobs, identity)
    result = _render(inputs, claims, citations)
    if final_bytes != result.final_assistant.encode("utf-8") or (
        report_bytes != (result.report_markdown.encode("utf-8") if result.report_markdown is not None else None)
    ):
        raise ValueError("rendered content bytes differ from deterministic replay")
    claim_batch, audit = _generic_integrity(
        run_id=identity.run_id, inputs=inputs, render=render
    )
    claim_ref = await _put_json(
        blobs, identity, claim_batch.to_json(),
        media_type="application/vnd.deskpet.claim-batch.v1+json",
    )
    if claim_ref != audit.claim_batch_ref:
        raise ValueError("quality audit claim ref does not address persisted claim batch")
    audit_ref = await _put_json(
        blobs, identity, audit.to_json(),
        media_type="application/vnd.deskpet.quality-audit.v1+json",
    )
    values["claim_batch_ref"] = claim_ref
    values["quality_audit_ref"] = audit_ref
    values["stage"] = "integrity_passed"
    closure = _state_refs(state) | {claim_ref, audit_ref}
    return StatePatch({"values": values, "blob_refs": _blob_items(closure)})


def _walk_wire_refs(value: object) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "proposed_premise_fact_refs":
                continue
            if key.endswith("_ref") and isinstance(item, str) and item.startswith("sha256:"):
                refs.add(item)
            elif key.endswith("_refs") and isinstance(item, list):
                refs.update(str(ref) for ref in item if isinstance(ref, str) and ref.startswith("sha256:"))
            else:
                refs.update(_walk_wire_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_walk_wire_refs(item))
    return refs


async def _expand_provenance(
    *, inputs: _TerminalInputs, blobs: RegisteredBlobStore, identity: NodeExecutionIdentity,
) -> tuple[str, ...]:
    policy_values = set(inputs.policy_refs.values())
    roots: set[str] = set()
    declared: set[str] = set()
    for batch in inputs.batches:
        declared.update(batch.provenance_refs)
        roots.update(batch.page_result_refs)
        roots.update(batch.admitted_fact_refs)
        roots.update(batch.registered_inference_refs)
        for slot in batch.candidate_slot_results:
            roots.add(str(slot["producer_outcome_ref"]))
            if slot["bundle_ref"] is not None:
                roots.add(str(slot["bundle_ref"]))
        for slot in batch.inference_slot_results:
            roots.add(str(slot["effect_outcome_ref"]))
            if slot["proposal_bundle_ref"] is not None:
                roots.add(str(slot["proposal_bundle_ref"]))
    roots -= policy_values
    seen: set[str] = set()
    pending = sorted(roots)
    while pending:
        ref = pending.pop(0)
        if ref in seen or ref in policy_values:
            continue
        data = await _read_bytes(blobs, identity, ref)
        seen.add(ref)
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Page bodies are provenance leaves and intentionally are not JSON.
            # Their digest and same-run ownership were already checked by
            # _read_bytes; only canonical JSON objects may contribute edges.
            continue
        if not isinstance(value, dict) or canonical_json(value).encode("utf-8") != data:
            raise ValueError("registered provenance JSON is not canonical")
        for child in sorted(_walk_wire_refs(value) - policy_values - seen):
            parse_blob_ref(child)
            pending.append(child)
    if seen != declared:
        raise ValueError(
            f"batch provenance is not the exact transitive closure: missing={sorted(seen-declared)}, extra={sorted(declared-seen)}"
        )
    return tuple(sorted(seen))


def build_continuation_snapshot(
    *, run_id: str, spec_ref: str, assessment_ref: str, inputs: _TerminalInputs,
    claim_batch_ref: str, provenance_refs: Sequence[str],
) -> dict[str, JsonValue]:
    policy_refs: dict[str, JsonValue] = {
        key: inputs.policy_refs[key] for key in sorted(_POLICY_KEYS)
    }
    closure_refs = sorted({
        spec_ref, *inputs.batch_refs, assessment_ref, claim_batch_ref,
        *provenance_refs, *[str(ref) for ref in policy_refs.values()],
    })
    base: dict[str, JsonValue] = {
        "schema_version": 1,
        "run_id": run_id,
        "workflow_name": "deep_research",
        "workflow_version": "v6",
        "spec_ref": spec_ref,
        "spec_hash": inputs.spec.spec_hash,
        "fact_batch_refs": list(inputs.batch_refs),
        "evidence_head_hash": inputs.assessment.evidence_head_hash,
        "assessment_ref": assessment_ref,
        "assessment_hash": inputs.assessment.assessment_hash,
        "assessment_input_hash": inputs.assessment.assessment_input_hash,
        "claim_batch_ref": claim_batch_ref,
        "provenance_refs": list(provenance_refs),
        "policy_refs": policy_refs,
        "closure_refs": closure_refs,
    }
    snapshot_hash = sha256_json(base)
    value = {**base, "snapshot_id": "rcs_" + snapshot_hash[:24], "snapshot_hash": snapshot_hash}
    if set(value) != _SNAPSHOT_KEYS:
        raise ValueError("ResearchContinuationSnapshotV1 keys differ")
    return value


async def persist_manifest_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    blobs, identity = _runtime(state, context)
    inputs = await _load_terminal_inputs(values, blobs, identity)
    await _assert_policy_objects(inputs, blobs, identity)
    render_ref = _wire_ref(values.get("render_bundle_ref"), "render_bundle_ref")
    render = _render_bundle(await _read_json(blobs, identity, render_ref))
    claim_ref = _wire_ref(values.get("claim_batch_ref"), "claim_batch_ref")
    quality_ref = _wire_ref(values.get("quality_audit_ref"), "quality_audit_ref")
    claim = ClaimBatchV1.from_json(await _read_json(blobs, identity, claim_ref))
    quality = QualityAuditV1.from_json(await _read_json(blobs, identity, quality_ref))
    expected_claim, expected_quality = _generic_integrity(
        run_id=identity.run_id, inputs=inputs, render=render
    )
    if claim.to_json() != expected_claim.to_json() or quality.to_json() != expected_quality.to_json():
        raise ValueError("persisted integrity objects differ from deterministic terminal replay")
    if (
        claim.status != "valid"
        or quality.hard_gate_status != "passed"
        or quality.claim_batch_ref != claim_ref
        or quality.answer_status != render["answer_status"]
        or claim.assessment_hash != inputs.assessment.assessment_hash
    ):
        raise ValueError("generic integrity objects do not admit terminal persistence")
    provenance = await _expand_provenance(inputs=inputs, blobs=blobs, identity=identity)
    spec_ref = _wire_ref(values.get("spec_ref"), "spec_ref")
    assessment_ref = _wire_ref(values.get("assessment_ref"), "assessment_ref")
    snapshot = build_continuation_snapshot(
        run_id=identity.run_id,
        spec_ref=spec_ref,
        assessment_ref=assessment_ref,
        inputs=inputs,
        claim_batch_ref=claim_ref,
        provenance_refs=provenance,
    )
    snapshot_ref = await _put_json(
        blobs, identity, snapshot,
        media_type="application/vnd.deskpet.research-continuation-snapshot.v1+json",
    )
    answer_status = str(render["answer_status"])
    final_ref = _wire_ref(render["final_assistant_ref"], "final_assistant_ref")
    report_ref = render["report_ref"]
    artifact = answer_status != "insufficient_evidence"
    content_refs: dict[str, JsonValue] = {
        "final_assistant_ref": final_ref,
        "report_ref": report_ref,
        "safe_summary_ref": final_ref if not artifact else None,
        "claim_batch_ref": claim_ref,
        "quality_audit_ref": quality_ref,
    }
    cardinality: dict[str, JsonValue] = {
        "final_assistant": 1,
        "workflow_final_status": 1,
        "report": 1 if artifact else 0,
        "artifact": 1 if artifact else 0,
        "run_terminal": 1,
    }
    manifest = TerminalDeliveryManifestV1.create(
        workflow_name="deep_research",
        workflow_version="v6",
        run_id=identity.run_id,
        answer_status=answer_status,
        spec_hash=inputs.spec.spec_hash,
        assessment_hash=inputs.assessment.assessment_hash,
        claim_policy_hash=_policy_hash(CLAIM_POLICY_V1),
        quality_policy_hash=_policy_hash(QUALITY_POLICY_V1),
        continuation_snapshot_ref=snapshot_ref,
        continuation_snapshot_hash=str(snapshot["snapshot_hash"]),
        content_refs=content_refs,
        intent_specs=build_intent_specs(
            run_id=identity.run_id,
            final_assistant_ref=final_ref,
            report_ref=str(report_ref) if report_ref is not None else None,
            artifact_required=artifact,
        ),
        cardinality=cardinality,
        engine_terminal={"status": "completed", "error_code": None, "recovery_action": None},
    )
    manifest_ref = await _put_json(
        blobs, identity, manifest.to_json(),
        media_type="application/vnd.deskpet.terminal-delivery-manifest.v1+json",
    )
    if manifest_ref != manifest.manifest_ref:
        raise ValueError("terminal manifest registered digest differs from semantic hash")
    values["continuation_snapshot_ref"] = snapshot_ref
    values["continuation_snapshot_hash"] = snapshot["snapshot_hash"]
    values["terminal_manifest_ref"] = manifest_ref
    values["terminal_manifest_hash"] = manifest.manifest_hash
    values["answer_status"] = answer_status
    await consume_v6_control(
        context,
        run_id=identity.run_id,
        result={"delivery_status": answer_status, "manifest_ref": manifest_ref},
    )
    values["stage"] = "terminal_persisted"
    closure = _state_refs(state) | {
        snapshot_ref, manifest_ref, render_ref, claim_ref, quality_ref, final_ref,
        *snapshot["closure_refs"],
    }
    if report_ref is not None:
        closure.add(str(report_ref))
    return StatePatch({"values": values, "blob_refs": _blob_items(closure)})


__all__ = [
    "CLAIM_POLICY_V1",
    "QUALITY_POLICY_V1",
    "build_continuation_snapshot",
    "derive_registered_claims",
    "integrity_handler",
    "persist_manifest_handler",
    "render_claims_handler",
]
