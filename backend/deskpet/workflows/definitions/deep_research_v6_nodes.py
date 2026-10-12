"""Ref-only Q1 node pipeline for the DeepResearch v6 graph."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import asdict
from typing import Any, Protocol

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
from .deep_research_v6_compiler import COMPILER_POLICY, compile_official_exact_fact
from .deep_research_v6_contracts import ResearchSpecV1, format_blob_ref, parse_blob_ref
from .deep_research_v6_delivery import persist_q1_terminal_bundle
from .deep_research_v6_evidence import (
    GENESIS_EVIDENCE_HEAD,
    Q1_ADMISSION_POLICY,
    Q1_ASSESSMENT_POLICY,
    Q1_ROUTE_POLICY,
    AnswerAssessmentV1,
    EvidenceFactBatchV1,
    assess_q1_evidence,
)
from .deep_research_v6_evidence_contracts import (
    V6ExtractedEvidenceRefV1,
    V6FetchedPageRefPayloadV1,
)
from .deep_research_v6_exact_fact import (
    ExtractedScalarEvidence,
    ScalarEvidenceRequest,
    extract_scalar_evidence,
)
from .deep_research_v6_exact_report import (
    CitationView,
    ExactFactReportResult,
    assess_and_render_exact_facts,
)
from .deep_research_v6_integrity import (
    ClaimRecordV1,
    build_q1_exact_scalar_integrity,
)

_DEFINITION_ADAPTER = {
    "year_end_total_population": "year_end_total_population",
    "births_during_period": "births_during_period",
}
_LOCATOR_KEYS = {
    "schema_version", "locator_id", "canonical_url", "final_url",
    "canonical_url_hash", "final_url_hash", "authority_id",
    "verification_status", "verification_policy_hash", "redirect_chain_hashes",
}
_EVIDENCE_BLOB_KEYS = {
    "schema_version", "requirement_id", "page_id", "source_locator_ref",
    "page_ref", "evidence",
}
_PAGE_RESULT_KEYS = {
    "schema_version", "page_id", "page_ref", "page_ref_blob",
    "evidence_refs",
}


class V6FetchedPagePort(Protocol):
    def load_pages(
        self,
        *,
        spec: Mapping[str, JsonValue],
        identity: object | None,
    ) -> Sequence[V6FetchedPageRefPayloadV1 | Mapping[str, JsonValue]] | Awaitable[
        Sequence[V6FetchedPageRefPayloadV1 | Mapping[str, JsonValue]]
    ]: ...


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _values(state: WorkflowState) -> dict[str, JsonValue]:
    values = state.get("values")
    if not isinstance(values, Mapping):
        raise TypeError("workflow values must be an object")
    return copy.deepcopy(dict(values))


def _runtime(
    state: WorkflowState, context: WorkflowContext
) -> tuple[RegisteredBlobStore, NodeExecutionIdentity]:
    blobs = context.ports.get("blob")
    identity = context.identity
    if not isinstance(blobs, RegisteredBlobStore):
        raise ValueError("deep_research v6 requires RegisteredBlobStore")
    if identity is None:
        raise ValueError("deep_research v6 requires node execution identity")
    if identity.run_id != state.get("run_id"):
        raise ValueError("blob identity belongs to another workflow run")
    return blobs, identity


async def _assert_current_run_owner(
    blobs: RegisteredBlobStore, digest: str, run_id: str
) -> None:
    db = await aiosqlite.connect(blobs.database)
    try:
        row = await (
            await db.execute(
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
            )
        ).fetchone()
    finally:
        await db.close()
    if row is None:
        raise ValueError(f"blob {digest} has no current-run owner")


async def _read_blob(
    blobs: RegisteredBlobStore, identity: NodeExecutionIdentity, wire_ref: str
) -> bytes:
    digest = parse_blob_ref(wire_ref)
    await _assert_current_run_owner(blobs, digest, identity.run_id)
    data = await blobs.get(digest)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("registered blob digest mismatch")
    return data


async def _read_canonical_json(
    blobs: RegisteredBlobStore, identity: NodeExecutionIdentity, wire_ref: str
) -> dict[str, Any]:
    data = await _read_blob(blobs, identity, wire_ref)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("registered blob is not UTF-8 JSON") from exc
    if not isinstance(value, dict) or canonical_json(value).encode("utf-8") != data:
        raise ValueError("registered JSON blob is not canonical")
    return value


async def _put_json(
    blobs: RegisteredBlobStore,
    identity: NodeExecutionIdentity,
    value: Mapping[str, JsonValue],
    *,
    media_type: str,
) -> str:
    data = canonical_json(value).encode("utf-8")
    ref = await blobs.put(data, identity, media_type=media_type)
    return format_blob_ref(ref.sha256)


def _blob_items(wire_refs: Sequence[str]) -> list[dict[str, JsonValue]]:
    digests = sorted({parse_blob_ref(ref) for ref in wire_refs})
    return [{"id": digest, "sha256": digest} for digest in digests]


def _state_wire_refs(state: WorkflowState) -> set[str]:
    raw = state.get("blob_refs", [])
    if not isinstance(raw, list):
        raise ValueError("blob_refs must be an array")
    refs: set[str] = set()
    for item in raw:
        digest = (
            str(item.get("sha256") or item.get("id") or "")
            if isinstance(item, Mapping)
            else str(item)
        )
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("blob_refs contains an invalid digest")
        refs.add(format_blob_ref(digest))
    return refs


async def _load_spec(
    values: Mapping[str, JsonValue],
    blobs: RegisteredBlobStore,
    identity: NodeExecutionIdentity,
) -> ResearchSpecV1:
    spec_ref = _required_text(values.get("spec_ref"), "spec_ref")
    raw = await _read_canonical_json(blobs, identity, spec_ref)
    spec = ResearchSpecV1.from_json(raw)
    if spec.spec_hash != values.get("spec_hash"):
        raise ValueError("research spec hash differs from checkpoint")
    return spec


def _pages(value: object) -> tuple[V6FetchedPageRefPayloadV1, ...]:
    if not isinstance(value, list):
        raise TypeError("fetched_page_refs must be an array")
    pages = tuple(
        item
        if isinstance(item, V6FetchedPageRefPayloadV1)
        else V6FetchedPageRefPayloadV1.from_json(item)
        for item in value
    )
    if [page.ordinal for page in pages] != list(range(len(pages))):
        raise ValueError("fetched page ordinals must be contiguous from zero")
    if len({page.page_id for page in pages}) != len(pages):
        raise ValueError("fetched page ids must be unique")
    return pages


def _request_from_requirement(requirement: Mapping[str, Any]) -> ScalarEvidenceRequest:
    value_schema = requirement.get("value_schema")
    time_scope = requirement.get("time_scope")
    if not isinstance(value_schema, Mapping) or not isinstance(time_scope, Mapping):
        raise ValueError("scalar requirement is missing value/time schema")
    definition = value_schema.get("definition")
    extractor_definition = _DEFINITION_ADAPTER.get(str(definition))
    if extractor_definition is None:
        raise ValueError(f"unsupported exact-fact definition {definition!r}")
    canonical_unit = value_schema.get("canonical_unit")
    if not isinstance(canonical_unit, Mapping):
        raise ValueError("scalar requirement has no canonical unit")
    if time_scope.get("kind") == "instant":
        time_label = f"{str(time_scope.get('as_of', ''))[:4]}年末"
    elif time_scope.get("kind") == "period":
        time_label = f"{str(time_scope.get('start', ''))[:4]}年全年"
    else:
        raise ValueError("official exact fact requires instant or period time scope")
    return ScalarEvidenceRequest(
        requirement_id=_required_text(requirement.get("requirement_id"), "requirement_id"),
        definition=extractor_definition,
        canonical_unit=_required_text(
            canonical_unit.get("unit_id"), "canonical_unit.unit_id"
        ),
        time_label=time_label,
    )


def _required_requests(spec: ResearchSpecV1) -> tuple[ScalarEvidenceRequest, ...]:
    requests = tuple(
        _request_from_requirement(requirement)
        for requirement in spec.requirements
        if requirement["kind"] == "scalar" and requirement["importance"] == "required"
    )
    if len(requests) != sum(
        requirement["importance"] == "required" for requirement in spec.requirements
    ):
        raise ValueError("Q1 official_exact_fact pipeline only accepts scalar requirements")
    if not requests:
        raise ValueError("official_exact_fact spec must contain required scalar requirements")
    return requests


def _evidence_from_json(value: Mapping[str, Any]) -> ExtractedScalarEvidence:
    keys = set(ExtractedScalarEvidence.__dataclass_fields__)
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError("ExtractedScalarEvidence keys differ")
    return ExtractedScalarEvidence(**dict(value))


async def _load_locator(
    page: V6FetchedPageRefPayloadV1,
    blobs: RegisteredBlobStore,
    identity: NodeExecutionIdentity,
) -> dict[str, Any]:
    locator = await _read_canonical_json(blobs, identity, page.source_locator_ref)
    if set(locator) != _LOCATOR_KEYS or locator.get("schema_version") != 1:
        raise ValueError("source locator keys/version differ")
    canonical_url = _required_text(locator.get("canonical_url"), "canonical_url")
    final_url = _required_text(locator.get("final_url"), "final_url")
    canonical_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
    final_hash = hashlib.sha256(final_url.encode("utf-8")).hexdigest()
    if (
        canonical_hash != locator.get("canonical_url_hash")
        or final_hash != locator.get("final_url_hash")
        or canonical_hash != page.canonical_url_hash
        or final_hash != page.final_url_hash
        or locator.get("authority_id") != page.authority_id
        or locator.get("verification_status") != "verified"
    ):
        raise ValueError("source locator identity/authority mismatch")
    base = dict(locator)
    locator_id = base.pop("locator_id")
    expected_id = "locator_" + hashlib.sha256(
        canonical_json(base).encode("utf-8")
    ).hexdigest()[:24]
    if locator_id != expected_id:
        raise ValueError("source locator id is not canonical")
    return locator


async def _load_body(
    page: V6FetchedPageRefPayloadV1,
    blobs: RegisteredBlobStore,
    identity: NodeExecutionIdentity,
) -> str:
    data = await _read_blob(blobs, identity, page.body_ref)
    if hashlib.sha256(data).hexdigest() != page.body_hash:
        raise ValueError("page body hash mismatch")
    try:
        body = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("page body is not UTF-8") from exc
    if not body.strip():
        raise ValueError("page body is empty")
    return body


def _result_to_json(result: ExactFactReportResult) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {
        "answer_status": result.answer_status,
        "final_assistant": result.final_assistant,
        "report_markdown": result.report_markdown,
        "claims": [asdict(item) for item in result.claims],
        "missing_requirement_ids": list(result.missing_requirement_ids),
    }
    validate_json_value(value)
    return value


async def compile_spec_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    blobs, identity = _runtime(state, context)
    topic = _required_text(values.get("topic"), "topic")
    locale = values.get("answer_locale", "zh-CN")
    if not isinstance(locale, str):
        raise TypeError("answer_locale must be a string")
    spec = compile_official_exact_fact(
        topic,
        answer_locale=locale,
        llm_candidates=values.get("compiler_candidates"),
    )
    spec_ref = await _put_json(
        blobs,
        identity,
        spec.to_json(),
        media_type="application/json",
    )
    values["compiler_candidates"] = None
    values["spec_ref"] = spec_ref
    values["spec_hash"] = spec.spec_hash
    values["stage"] = "compiled"
    return StatePatch({"values": values, "blob_refs": _blob_items([spec_ref])})


async def collect_pages_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    blobs, identity = _runtime(state, context)
    spec = await _load_spec(values, blobs, identity)
    raw_pages = values.get("fetched_page_refs", [])
    if raw_pages:
        pages = _pages(raw_pages)
    else:
        port = context.ports.get("fetch")
        load_pages = getattr(port, "load_pages", None)
        if not callable(load_pages):
            pages = ()
        else:
            loaded = load_pages(spec=spec.to_json(), identity=identity)
            if inspect.isawaitable(loaded):
                loaded = await loaded
            if not isinstance(loaded, Sequence):
                raise TypeError("v6 fetch port load_pages() must return a sequence")
            pages = _pages(
                [
                    item.to_json()
                    if isinstance(item, V6FetchedPageRefPayloadV1)
                    else copy.deepcopy(dict(item))
                    for item in loaded
                ]
            )
    page_refs = {
        ref
        for page in pages
        for ref in (page.body_ref, page.source_locator_ref)
    }
    for ref in sorted(page_refs):
        await _read_blob(blobs, identity, ref)
    values["fetched_page_refs"] = [page.to_json() for page in pages]
    values["stage"] = "pages_collected"
    closure = _state_wire_refs(state) | page_refs | {str(values["spec_ref"])}
    return StatePatch({"values": values, "blob_refs": _blob_items(sorted(closure))})


async def extract_facts_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    blobs, identity = _runtime(state, context)
    spec = await _load_spec(values, blobs, identity)
    requests = _required_requests(spec)
    pages = _pages(values.get("fetched_page_refs", []))
    winners: dict[str, tuple[V6ExtractedEvidenceRefV1, ExtractedScalarEvidence]] = {}
    fact_batch_refs: list[str] = []
    evidence_head_hash = GENESIS_EVIDENCE_HEAD
    new_refs: set[str] = set()
    for page in pages:
        await _load_locator(page, blobs, identity)
        body = await _load_body(page, blobs, identity)
        page_ref_blob = await _put_json(
            blobs,
            identity,
            page.to_json(),
            media_type="application/vnd.deskpet.fetched-page-ref.v1+json",
        )
        page_evidence_refs: list[str] = []
        admitted_binding_ids: list[str] = []
        for evidence in extract_scalar_evidence(
            body=body,
            body_ref=page.body_ref,
            page_id=page.page_id,
            requests=requests,
        ):
            if evidence.requirement_id in winners:
                continue
            payload: dict[str, JsonValue] = {
                "schema_version": 1,
                "requirement_id": evidence.requirement_id,
                "page_id": page.page_id,
                "source_locator_ref": page.source_locator_ref,
                "page_ref": page.to_json(),
                "evidence": asdict(evidence),
            }
            evidence_ref = await _put_json(
                blobs,
                identity,
                payload,
                media_type="application/vnd.deskpet.scalar-evidence.v1+json",
            )
            ref = V6ExtractedEvidenceRefV1(
                requirement_id=evidence.requirement_id,
                page_id=page.page_id,
                evidence_ref=evidence_ref,
                source_locator_ref=page.source_locator_ref,
            )
            winners[evidence.requirement_id] = (ref, evidence)
            page_evidence_refs.append(evidence_ref)
            admitted_binding_ids.append(evidence.binding_id)
            new_refs.add(evidence_ref)
        page_result: dict[str, JsonValue] = {
            "schema_version": 1,
            "page_id": page.page_id,
            "page_ref": page.to_json(),
            "page_ref_blob": page_ref_blob,
            "evidence_refs": sorted(page_evidence_refs),
        }
        page_result_ref = await _put_json(
            blobs,
            identity,
            page_result,
            media_type="application/vnd.deskpet.q1-page-result.v1+json",
        )
        provenance_refs = sorted(
            {
                page.source_locator_ref,
                page.body_ref,
                page_ref_blob,
                page_result_ref,
                *page_evidence_refs,
            }
        )
        batch = EvidenceFactBatchV1.create(
            run_id=identity.run_id,
            spec_hash=spec.spec_hash,
            previous_head_hash=evidence_head_hash,
            ordinal=len(fact_batch_refs),
            page_result_ref=page_result_ref,
            admitted_binding_ids=admitted_binding_ids,
            provenance_refs=provenance_refs,
        )
        batch_ref = await _put_json(
            blobs,
            identity,
            batch.to_json(),
            media_type="application/vnd.deskpet.evidence-fact-batch.v1+json",
        )
        fact_batch_refs.append(batch_ref)
        evidence_head_hash = batch.head_hash
        new_refs.update({page_ref_blob, page_result_ref, batch_ref, *provenance_refs})
    assessment = assess_q1_evidence(
        spec_hash=spec.spec_hash,
        evidence_head_hash=evidence_head_hash,
        requests=requests,
        evidence=[
            winners[request.requirement_id][1]
            for request in requests
            if request.requirement_id in winners
        ],
    )
    assessment_ref = await _put_json(
        blobs,
        identity,
        assessment.to_json(),
        media_type="application/vnd.deskpet.answer-assessment.v1+json",
    )
    new_refs.add(assessment_ref)
    values.pop("scalar_evidence_refs", None)
    values["fact_batch_refs"] = fact_batch_refs
    values["evidence_head_hash"] = evidence_head_hash
    values["assessment_ref"] = assessment_ref
    values["assessment_hash"] = assessment.assessment_hash
    values["stage"] = "evidence_assessed"
    closure = _state_wire_refs(state) | new_refs
    return StatePatch({"values": values, "blob_refs": _blob_items(sorted(closure))})


async def assess_render_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    blobs, identity = _runtime(state, context)
    spec = await _load_spec(values, blobs, identity)
    requests = _required_requests(spec)
    raw_batch_refs = values.get("fact_batch_refs")
    if not isinstance(raw_batch_refs, list) or any(
        not isinstance(ref, str) for ref in raw_batch_refs
    ):
        raise ValueError("fact_batch_refs are required before rendering")
    expected_head = GENESIS_EVIDENCE_HEAD
    evidence: list[ExtractedScalarEvidence] = []
    fact_batch_values: list[dict[str, JsonValue]] = []
    citations: dict[str, CitationView] = {}
    provenance_refs: set[str] = set()
    admitted_binding_ids: set[str] = set()
    for ordinal, batch_ref in enumerate(raw_batch_refs):
        batch = EvidenceFactBatchV1.from_json(
            await _read_canonical_json(blobs, identity, batch_ref)
        )
        fact_batch_values.append(batch.to_json())
        if (
            batch.run_id != identity.run_id
            or batch.spec_hash != spec.spec_hash
            or batch.ordinal != ordinal
            or batch.previous_head_hash != expected_head
        ):
            raise ValueError("evidence fact batch chain identity mismatch")
        page_result = await _read_canonical_json(
            blobs, identity, batch.page_result_ref
        )
        if set(page_result) != _PAGE_RESULT_KEYS or page_result.get("schema_version") != 1:
            raise ValueError("Q1 page result keys/version differ")
        if not isinstance(page_result.get("page_ref"), Mapping) or not isinstance(
            page_result.get("evidence_refs"), list
        ):
            raise ValueError("Q1 page result payload is invalid")
        page = V6FetchedPageRefPayloadV1.from_json(page_result["page_ref"])
        if page.page_id != page_result.get("page_id"):
            raise ValueError("Q1 page result page identity mismatch")
        page_ref_blob = _required_text(page_result.get("page_ref_blob"), "page_ref_blob")
        persisted_page = await _read_canonical_json(blobs, identity, page_ref_blob)
        if persisted_page != page.to_json():
            raise ValueError("persisted page reference differs from page result")
        evidence_refs = page_result["evidence_refs"]
        if evidence_refs != sorted(set(evidence_refs)) or any(
            not isinstance(ref, str) for ref in evidence_refs
        ):
            raise ValueError("page result evidence refs must be sorted unique strings")
        expected_provenance = {
            page.source_locator_ref,
            page.body_ref,
            page_ref_blob,
            batch.page_result_ref,
            *evidence_refs,
        }
        if set(batch.provenance_refs) != expected_provenance:
            raise ValueError("fact batch provenance closure mismatch")
        locator_data = await _load_locator(page, blobs, identity)
        citation_url = _required_text(
            locator_data.get("canonical_url"), "locator.canonical_url"
        )
        authority_id = _required_text(locator_data.get("authority_id"), "locator.authority_id")
        citations[page.page_id] = CitationView(title=authority_id, url=citation_url)
        page_binding_ids: set[str] = set()
        for evidence_ref in evidence_refs:
            payload = await _read_canonical_json(blobs, identity, evidence_ref)
            if set(payload) != _EVIDENCE_BLOB_KEYS or payload.get("schema_version") != 1:
                raise ValueError("scalar evidence blob keys/version differ")
            if not isinstance(payload.get("evidence"), Mapping):
                raise ValueError("scalar evidence blob has no evidence object")
            item = _evidence_from_json(payload["evidence"])
            if (
                payload.get("page_id") != page.page_id
                or payload.get("source_locator_ref") != page.source_locator_ref
                or item.page_id != page.page_id
                or item.body_ref != page.body_ref
            ):
                raise ValueError("scalar evidence/page identity mismatch")
            evidence.append(item)
            page_binding_ids.add(item.binding_id)
        if set(batch.admitted_binding_ids) != page_binding_ids:
            raise ValueError("fact batch admitted bindings differ from evidence blobs")
        admitted_binding_ids.update(page_binding_ids)
        provenance_refs.update(batch.provenance_refs)
        expected_head = batch.head_hash
    if expected_head != values.get("evidence_head_hash"):
        raise ValueError("evidence head differs from checkpoint")
    assessment_ref = _required_text(values.get("assessment_ref"), "assessment_ref")
    assessment = AnswerAssessmentV1.from_json(
        await _read_canonical_json(blobs, identity, assessment_ref)
    )
    if (
        assessment.assessment_hash != values.get("assessment_hash")
        or assessment.spec_hash != spec.spec_hash
        or assessment.evidence_head_hash != expected_head
    ):
        raise ValueError("assessment identity differs from evidence checkpoint")
    assessment_binding_ids = {
        str(binding_id)
        for result_item in assessment.requirement_results
        for binding_id in result_item["binding_ids"]
    }
    if assessment_binding_ids != admitted_binding_ids:
        raise ValueError("assessment bindings differ from admitted evidence ledger")
    supported_ids = {
        str(result_item["requirement_id"])
        for result_item in assessment.requirement_results
        if result_item["support_status"] == "supported"
    }
    render_evidence = tuple(
        item for item in evidence if item.requirement_id in supported_ids
    )
    result = assess_and_render_exact_facts(
        requests=requests,
        evidence=render_evidence,
        citations=citations,
    )
    expected_answer_status = {
        "completed_candidate": "completed",
        "partial_candidate": "partial",
        "insufficient": "insufficient_evidence",
    }.get(assessment.status)
    if expected_answer_status is None or result.answer_status != expected_answer_status:
        raise ValueError("renderer result differs from persisted assessment")
    claim_policy = {"schema_version": 1, "policy_id": "deep-research-v6-claim-q1-v1"}
    quality_policy = {"schema_version": 1, "policy_id": "deep-research-v6-quality-q1-v1"}
    integrity_claims = [
        ClaimRecordV1.create(
            requirement_id=claim.requirement_id,
            item_or_cell_id=claim.requirement_id,
            claim_kind="fact",
            normalized_proposition=claim.normalized_proposition,
            binding_ids=(claim.binding_id,),
            inference_ref=None,
            support_status=claim.support_status,
            visibility="user",
        )
        for claim in result.claims
    ]
    integrity = build_q1_exact_scalar_integrity(
        run_id=identity.run_id,
        spec_hash=spec.spec_hash,
        required_requirement_ids=[request.requirement_id for request in requests],
        answer_assessment=assessment.to_json(),
        evidence_fact_batches=fact_batch_values,
        claims=integrity_claims,
        requested_answer_status=result.answer_status,
        claim_policy_hash=hashlib.sha256(canonical_json(claim_policy).encode("utf-8")).hexdigest(),
        quality_policy_hash=hashlib.sha256(canonical_json(quality_policy).encode("utf-8")).hexdigest(),
        soft_scores={
            "readability": 1_000_000,
            "source_diversity": 0,
            "analysis_depth": 0,
            "counterevidence": 0,
            "uncertainty": 0,
        },
    )
    if integrity.hard_failure_codes:
        # No visible factual bytes may survive a hard integrity failure.  The
        # terminal bundle still records the derived failed audit for diagnosis.
        result = ExactFactReportResult(
            answer_status="insufficient_evidence",
            final_assistant=(
                "证据完整性校验未通过，因此本次不能可靠给出这些数值。"
            ),
            report_markdown=None,
            claims=(),
            missing_requirement_ids=tuple(
                request.requirement_id for request in requests
            ),
        )
    policy_refs = {
        "compiler": await _put_json(
            blobs, identity, COMPILER_POLICY, media_type="application/json"
        ),
        "route": await _put_json(
            blobs, identity, Q1_ROUTE_POLICY, media_type="application/json"
        ),
        "admission": await _put_json(
            blobs, identity, Q1_ADMISSION_POLICY, media_type="application/json"
        ),
        "assessment": await _put_json(
            blobs, identity, Q1_ASSESSMENT_POLICY, media_type="application/json"
        ),
    }
    ephemeral_values = {
        **values,
        "research_spec": spec.to_json(),
        "answer_result": _result_to_json(result),
        "answer_status": result.answer_status,
        "final_assistant": result.final_assistant,
        "report_markdown": result.report_markdown,
        "provenance_refs": sorted(provenance_refs),
        "evidence_policy_refs": policy_refs,
        "integrity_fact_batches": fact_batch_values,
        "integrity_assessment": assessment.to_json(),
    }
    ephemeral_state = copy.deepcopy(dict(state))
    ephemeral_state["values"] = ephemeral_values
    bundle = await persist_q1_terminal_bundle(
        state=ephemeral_state,
        blobs=blobs,
        identity=identity,
        artifact_required=True,
    )
    if parse_blob_ref(bundle.manifest_ref) != bundle.manifest_hash:
        raise ValueError("terminal manifest ref/hash mismatch")
    values["answer_status"] = result.answer_status
    values["terminal_manifest_ref"] = bundle.manifest_ref
    values["terminal_manifest_hash"] = bundle.manifest_hash
    values["stage"] = "terminal_persisted"
    closure = _state_wire_refs(state) | provenance_refs | set(bundle.blob_refs)
    return StatePatch({"values": values, "blob_refs": _blob_items(sorted(closure))})


__all__ = [
    "V6FetchedPagePort", "assess_render_handler", "collect_pages_handler",
    "compile_spec_handler", "extract_facts_handler",
]
