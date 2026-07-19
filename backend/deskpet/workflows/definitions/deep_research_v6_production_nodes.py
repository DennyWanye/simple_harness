"""Registered-ref-only nodes 1-8 for the DeepResearch v6 production graph.

The module deliberately owns orchestration and the two single-writer evidence
frontiers. Retrieval and semantic providers may persist immutable result blobs,
but they never receive or mutate the workflow state.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from typing import Any, Mapping, Sequence

from ..contracts import (
    JsonValue,
    NodeExecutionIdentity,
    StatePatch,
    WorkflowContext,
    WorkflowState,
    canonical_json,
)
from ..store import RegisteredBlobStore
from ..adapters.deep_research_v6_semantic_runtime import INFERENCE_POLICY
from .deep_research_v6_assessment import (
    assess_requirements,
    decode_assessment_inputs,
    derive_collection_item_id,
    derive_matrix_cell_id,
)
from .deep_research_v6_compiler import (
    COMPILER_POLICY,
    COMPLETION_POLICY,
    RENDER_PROFILE,
    compile_research_spec,
)
from .deep_research_v6_contracts import (
    ResearchSpecV1,
    RouteDecisionV1,
    format_blob_ref,
    parse_blob_ref,
)
from .deep_research_v6_control import poll_v6_control, settle_v6_control
from .deep_research_v6_evidence import (
    AdmittedResearchFactV1,
    EvidenceFactBatchV1,
    GENESIS_EVIDENCE_HEAD,
    RegisteredInferenceV1,
)
from .deep_research_v6_evidence_contracts import (
    CandidateProducerOutcomeV1,
    EvidenceCandidateBundleV1,
    EvidenceCandidateV1,
    InferenceProposalBundleV1,
)
from .deep_research_v6_terminal_nodes import CLAIM_POLICY_V1, QUALITY_POLICY_V1


ADMISSION_POLICY_V1: dict[str, JsonValue] = {
    "schema_version": 1,
    "policy_id": "deep-research-v6-admission-v1",
    "validation_order": [
        "shape", "identity", "spec_target", "body_span", "source_policy",
        "type_semantics", "unit_time_definition", "duplicate", "conflict",
    ],
}
INFERENCE_POLICY_V1 = INFERENCE_POLICY
ASSESSMENT_POLICY_V1: dict[str, JsonValue] = {
    "schema_version": 1,
    "policy_id": "deep-research-v6-assessment-v1",
    "input_rule": "ordered-fact-and-registered-inference-refs",
}

_UPSTREAM_KEYS = (
    "fetched_page_refs", "page_result_refs", "compiler_candidates",
    "candidate_slot_results", "fact_batch_refs", "inference_slot_results",
    "admitted_fact_refs", "registered_inference_refs",
)
_POLICY_KEYS = {
    "compiler", "route", "extraction", "llm_extract", "llm_repair",
    "llm_inference", "admission", "inference", "assessment", "claim", "quality",
}

_CANDIDATE_KINDS = {
    "scalar": "scalar",
    "matrix": "matrix_cell",
    "collection": "collection_field",
    "claim_set": "claim_fact",
}


def validate_candidate_admission(
    candidate: object,
    *,
    requirement: Mapping[str, Any] | None,
    body_bytes: bytes,
    work_group_id: str,
    logical_page_id: str,
    page_plan_ordinal: int,
    source_family_id: str,
    source_tier: str,
    prior_semantic_payload: Mapping[str, Any] | None = None,
) -> str | None:
    """Return the first frozen v6 admission failure, or ``None``.

    The order is an externally frozen identity surface.  Keep each phase
    independent: a later semantic error must never mask an earlier identity,
    span, or source-policy failure.
    """

    # shape
    if not isinstance(candidate, EvidenceCandidateV1):
        return "candidate_shape_invalid"

    # identity
    raw = candidate.to_json()
    declared_id = str(raw.pop("candidate_id"))
    expected_id = "ecd_" + hashlib.sha256(
        canonical_json(raw).encode("utf-8")
    ).hexdigest()[:24]
    if (
        declared_id != expected_id
        or candidate.work_group_id != work_group_id
        or candidate.logical_page_id != logical_page_id
        or candidate.page_plan_ordinal != page_plan_ordinal
    ):
        return "candidate_id_mismatch"

    # spec target
    if requirement is None:
        return "requirement_missing"
    expected_kind = _CANDIDATE_KINDS.get(str(requirement.get("kind")))
    if expected_kind != candidate.candidate_kind:
        return "requirement_kind_mismatch"

    # body span
    if candidate.span_end_byte > len(body_bytes):
        return "span_out_of_bounds"
    try:
        body_bytes[: candidate.span_start_byte].decode("utf-8")
        body_bytes[: candidate.span_end_byte].decode("utf-8")
    except UnicodeDecodeError:
        return "span_not_utf8_aligned"
    excerpt = body_bytes[candidate.span_start_byte : candidate.span_end_byte]
    if hashlib.sha256(excerpt).hexdigest() != candidate.excerpt_hash:
        return "excerpt_hash_mismatch"

    # source policy
    source_policy = requirement.get("source_constraint")
    if not isinstance(source_policy, Mapping):
        return "source_policy_rejected"
    if source_policy.get("first_party") == "required" and source_tier != "first_party":
        return "source_policy_rejected"
    preferred = source_policy.get("preferred_authority_ids")
    if (
        source_policy.get("first_party") == "required"
        and isinstance(preferred, list)
        and preferred
        and source_family_id not in preferred
    ):
        return "source_policy_rejected"

    # type semantics
    payload = candidate.payload
    try:
        if candidate.candidate_kind == "scalar":
            if payload["item_or_cell_id"] is not None:
                return "payload_semantics_invalid"
        elif candidate.candidate_kind == "matrix_cell":
            axes = requirement["axes"]
            member_ids = list(payload["axis_member_ids"])
            allowed_by_axis = [
                {str(member["member_id"]) for member in axis["members"]}
                for axis in axes
            ]
            if (
                len(member_ids) != len(allowed_by_axis)
                or any(member_id not in allowed_by_axis[index] for index, member_id in enumerate(member_ids))
                or payload["cell_id"] != derive_matrix_cell_id(candidate.requirement_id, member_ids)
            ):
                return "payload_semantics_invalid"
        elif candidate.candidate_kind == "collection_field":
            schema = requirement["item_schema"]
            fields = {str(item["field_key"]): item for item in schema["fields"]}
            field_key = str(payload["field_key"])
            unique_values = list(payload["unique_key_values"])
            if (
                field_key not in fields
                or len(unique_values) != len(schema["unique_key"])
                or payload["item_id"] != derive_collection_item_id(candidate.requirement_id, unique_values)
            ):
                return "payload_semantics_invalid"
        elif candidate.candidate_kind == "claim_fact":
            allowed_facets = {str(item["facet_id"]) for item in requirement["topic_facets"]}
            if payload["facet_key"] not in allowed_facets:
                return "payload_semantics_invalid"
    except (KeyError, TypeError, ValueError):
        return "payload_semantics_invalid"

    # unit / time / definition (stable sub-order)
    if candidate.candidate_kind == "scalar":
        schema = requirement["value_schema"]
        if payload["canonical_unit"] != schema["canonical_unit"]["unit_id"]:
            return "unit_mismatch"
        if payload["time_scope"] != requirement["time_scope"] or payload["scope"] != requirement["scope"]:
            return "time_scope_mismatch"
        if payload["definition"] != schema["definition"]:
            return "definition_mismatch"
    elif candidate.candidate_kind == "matrix_cell":
        if payload["time_scope"] != requirement["time_scope"] or payload["scope"] != requirement["scope"]:
            return "time_scope_mismatch"
        if not isinstance(payload["definition"], str) or not payload["definition"].strip():
            return "definition_mismatch"
    elif candidate.candidate_kind == "collection_field":
        field = next(
            item for item in requirement["item_schema"]["fields"]
            if item["field_key"] == payload["field_key"]
        )
        expected_unit = None if field["unit"] is None else field["unit"]["unit_id"]
        if payload["canonical_unit"] != expected_unit:
            return "unit_mismatch"
        if payload["as_of"] != requirement["selection"]["as_of"]:
            return "time_scope_mismatch"
    else:
        if payload["time_scope"] != requirement["time_scope"] or payload["scope"] != requirement["scope"]:
            return "time_scope_mismatch"
        if not isinstance(payload["definition"], str) or not payload["definition"].strip():
            return "definition_mismatch"
    # duplicate -> conflict
    if prior_semantic_payload is not None:
        if canonical_json(prior_semantic_payload) == canonical_json(payload):
            return "duplicate_candidate"
        return "conflicting_candidate"
    return None


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _values(state: WorkflowState) -> dict[str, JsonValue]:
    value = state.get("values")
    if not isinstance(value, Mapping):
        raise TypeError("workflow values must be an object")
    return copy.deepcopy(dict(value))


def _runtime(
    state: WorkflowState, context: WorkflowContext
) -> tuple[RegisteredBlobStore, NodeExecutionIdentity]:
    blobs = context.ports.get("blob")
    identity = context.identity
    if not isinstance(blobs, RegisteredBlobStore):
        raise ValueError("deep_research v6 requires RegisteredBlobStore")
    if identity is None or identity.run_id != state.get("run_id"):
        raise ValueError("deep_research v6 requires current-run execution identity")
    return blobs, identity


async def _put_json(
    blobs: RegisteredBlobStore,
    identity: NodeExecutionIdentity,
    value: Mapping[str, JsonValue],
    *,
    media_type: str,
) -> str:
    ref = await blobs.put(
        canonical_json(value).encode("utf-8"), identity, media_type=media_type
    )
    return format_blob_ref(ref.sha256)


async def _read_json(blobs: RegisteredBlobStore, wire_ref: str) -> dict[str, Any]:
    digest = parse_blob_ref(wire_ref)
    raw = await blobs.get(digest)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("registered blob digest mismatch")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("registered blob is not UTF-8 JSON") from exc
    if not isinstance(value, dict) or canonical_json(value).encode("utf-8") != raw:
        raise ValueError("registered JSON blob is not canonical")
    return value


def _walk_registered_refs(value: object) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            # Rejected inference proposals retain unreachable audit strings;
            # they are not closure ownership edges.
            if key == "proposed_premise_fact_refs":
                continue
            if key.endswith("_ref") and isinstance(item, str) and item.startswith("sha256:"):
                refs.add(item)
            elif key.endswith("_refs") and isinstance(item, list):
                refs.update(
                    str(ref)
                    for ref in item
                    if isinstance(ref, str) and ref.startswith("sha256:")
                )
            else:
                refs.update(_walk_registered_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_walk_registered_refs(item))
    return refs


async def _exact_provenance_closure(
    blobs: RegisteredBlobStore,
    *,
    roots: Sequence[str] | set[str],
    policy_refs: Mapping[str, object],
) -> set[str]:
    """Expand only forward registered-ref edges, excluding frozen policies."""

    policies = {str(ref) for ref in policy_refs.values()}
    seen: set[str] = set()
    pending = sorted(set(roots) - policies)
    while pending:
        ref = pending.pop(0)
        if ref in seen or ref in policies:
            continue
        digest = parse_blob_ref(ref)
        raw = await blobs.get(digest)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("registered provenance digest mismatch")
        seen.add(ref)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or canonical_json(value).encode("utf-8") != raw:
            raise ValueError("registered provenance JSON is not canonical")
        for child in sorted(_walk_registered_refs(value) - policies - seen):
            parse_blob_ref(child)
            pending.append(child)
    return seen


def _blob_items(wire_refs: Sequence[str]) -> list[dict[str, JsonValue]]:
    return [
        {"id": digest, "sha256": digest}
        for digest in sorted({parse_blob_ref(ref) for ref in wire_refs})
    ]


def _state_refs(state: WorkflowState) -> set[str]:
    result: set[str] = set()
    raw = state.get("blob_refs", [])
    if not isinstance(raw, list):
        raise ValueError("blob_refs must be an array")
    for item in raw:
        digest = str(item.get("sha256") or item.get("id") or "") if isinstance(item, Mapping) else str(item)
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("blob_refs contains an invalid digest")
        result.add(format_blob_ref(digest))
    return result


def _wire_refs_from_items(value: object, name: str) -> set[str]:
    if not isinstance(value, list):
        raise TypeError(f"{name} must be an array")
    refs: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"id", "sha256"}:
            raise ValueError(f"{name} item keys differ")
        digest = str(item["sha256"])
        if item["id"] != digest:
            raise ValueError(f"{name} id/sha256 differ")
        refs.add(format_blob_ref(digest))
    return refs


async def _call(method: object, **kwargs: object) -> object:
    if not callable(method):
        raise ValueError("required workflow port method is unavailable")
    result = method(**kwargs)
    return await result if inspect.isawaitable(result) else result


async def _load_spec(
    values: Mapping[str, JsonValue], blobs: RegisteredBlobStore
) -> ResearchSpecV1:
    raw = await _read_json(blobs, _required_text(values.get("spec_ref"), "spec_ref"))
    spec = ResearchSpecV1.from_json(raw)
    if spec.spec_hash != values.get("spec_hash"):
        raise ValueError("research spec hash differs from checkpoint")
    return spec


async def _load_route(
    values: Mapping[str, JsonValue], blobs: RegisteredBlobStore, spec: ResearchSpecV1
) -> RouteDecisionV1:
    raw = await _read_json(
        blobs, _required_text(values.get("route_decision_ref"), "route_decision_ref")
    )
    decision = RouteDecisionV1.from_json(raw)
    decision_value = decision.to_json()
    if (
        decision_value["route_id"] != values.get("route_id")
        or decision_value["spec_hash"] != spec.spec_hash
        or decision_value["policy_ref"] != values.get("route_policy_ref")
        or decision_value["policy_hash"] != values.get("route_policy_hash")
    ):
        raise ValueError("route decision differs from frozen checkpoint")
    return decision


async def _register_policy(
    blobs: RegisteredBlobStore,
    identity: NodeExecutionIdentity,
    policy: Mapping[str, JsonValue],
    *,
    media_type: str = "application/vnd.deskpet.research-policy.v1+json",
) -> str:
    return await _put_json(blobs, identity, policy, media_type=media_type)


def _merge_policy_refs(values: dict[str, JsonValue], additions: Mapping[str, object]) -> None:
    current_raw = values.get("policy_refs", {})
    if not isinstance(current_raw, Mapping):
        raise ValueError("policy_refs must be an object")
    current = {str(key): str(value) for key, value in current_raw.items()}
    for key, raw_ref in additions.items():
        if key not in _POLICY_KEYS:
            raise ValueError(f"unknown policy ref {key!r}")
        ref = _required_text(raw_ref, f"policy_refs.{key}")
        parse_blob_ref(ref)
        if key in current and current[key] != ref:
            raise ValueError(f"frozen policy ref {key!r} changed")
        current[key] = ref
    values["policy_refs"] = current


async def compile_spec_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    blobs, identity = _runtime(state, context)
    deadline = context.ports.get("deadline")
    observe_automatic = getattr(deadline, "observe_automatic", None)
    if callable(observe_automatic):
        # The run-wide wall guard must exist before the compiled brief becomes
        # an actionable checkpoint.  Route planning is too late: the UI may
        # legitimately submit generate-now as soon as this node commits.
        await _call(observe_automatic, identity=identity)
    if values.get("source_snapshot_hash") is not None:
        if values.get("stage") != "continuation_hydrated":
            raise ValueError("v6 continuation state was not hydrated by its version codec")
        parent_run_id = _required_text(
            values.get("continuation_parent_run_id"), "continuation_parent_run_id"
        )
        if parent_run_id == state.get("run_id"):
            raise ValueError("v6 continuation parent and child run ids must differ")
        inherited_count = values.get("inherited_fact_batch_count")
        batch_refs = values.get("fact_batch_refs")
        if (
            isinstance(inherited_count, bool)
            or not isinstance(inherited_count, int)
            or inherited_count < 1
            or not isinstance(batch_refs, list)
            or inherited_count != len(batch_refs)
        ):
            raise ValueError("v6 continuation inherited batch frontier differs")
        spec = await _load_spec(values, blobs)
        if spec.spec_hash != values.get("spec_hash"):
            raise ValueError("v6 continuation spec hash differs")
        fact_refs, inference_refs = await _ordered_fact_refs(values, blobs)
        for ref in (
            *batch_refs,
            *fact_refs,
            *inference_refs,
            _required_text(values.get("assessment_ref"), "assessment_ref"),
            _required_text(values.get("claim_batch_ref"), "claim_batch_ref"),
        ):
            await _read_json(blobs, ref)
        policies = values.get("policy_refs")
        if not isinstance(policies, Mapping) or set(policies) != _POLICY_KEYS:
            raise ValueError("v6 continuation policy closure differs")
        for ref in policies.values():
            await _read_json(blobs, _required_text(ref, "policy_ref"))
        values["stage"] = "compiled"
        return StatePatch({"values": values, "blob_refs": copy.deepcopy(state["blob_refs"])})
    for key in _UPSTREAM_KEYS:
        if values.get(key) not in (None, [], {}):
            raise ValueError(f"pending v6 state may not inject {key}")
    topic = _required_text(values.get("topic"), "topic")
    locale = values.get("answer_locale", "zh-CN")
    if not isinstance(locale, str):
        raise TypeError("answer_locale must be a string")
    spec = compile_research_spec(topic, answer_locale=locale)
    spec_ref = await _put_json(
        blobs, identity, spec.to_json(), media_type="application/vnd.deskpet.research-spec.v1+json"
    )
    refs = {
        "compiler": await _register_policy(blobs, identity, COMPILER_POLICY),
        "admission": await _register_policy(blobs, identity, ADMISSION_POLICY_V1),
        "inference": await _register_policy(
            blobs,
            identity,
            INFERENCE_POLICY_V1,
            media_type="application/vnd.deskpet.deepresearch-v6-inference-policy+json",
        ),
        "assessment": await _register_policy(blobs, identity, ASSESSMENT_POLICY_V1),
        "claim": await _register_policy(blobs, identity, CLAIM_POLICY_V1),
        "quality": await _register_policy(blobs, identity, QUALITY_POLICY_V1),
    }
    completion_ref = await _register_policy(blobs, identity, COMPLETION_POLICY)
    render_ref = await _register_policy(blobs, identity, RENDER_PROFILE)
    if completion_ref != spec.completion_policy_ref or render_ref != spec.render_profile_ref:
        raise ValueError("compiled spec policy refs differ from registered bytes")
    values.update({"spec_ref": spec_ref, "spec_hash": spec.spec_hash, "stage": "compiled"})
    _merge_policy_refs(values, refs)
    closure = _state_refs(state) | {spec_ref, completion_ref, render_ref, *refs.values()}
    return StatePatch({"values": values, "blob_refs": _blob_items(sorted(closure))})


async def plan_route_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    blobs, identity = _runtime(state, context)
    spec = await _load_spec(values, blobs)
    retrieval = context.ports.get("retrieval")
    raw = await _call(getattr(retrieval, "plan_route", None), spec=spec.to_json(), identity=identity)
    keys = {"route_policy_ref", "route_policy_hash", "route_decision_ref", "route_id", "blob_refs"}
    if not isinstance(raw, Mapping) or set(raw) != keys:
        raise ValueError("retrieval plan_route response keys differ")
    policy_ref = _required_text(raw["route_policy_ref"], "route_policy_ref")
    policy_hash = _required_text(raw["route_policy_hash"], "route_policy_hash")
    decision_ref = _required_text(raw["route_decision_ref"], "route_decision_ref")
    if parse_blob_ref(policy_ref) != policy_hash:
        raise ValueError("route policy ref/hash differ")
    decision = RouteDecisionV1.from_json(await _read_json(blobs, decision_ref))
    decision_value = decision.to_json()
    if (
        decision_value["route_id"] != raw["route_id"]
        or decision_value["run_id"] != identity.run_id
        or decision_value["spec_hash"] != spec.spec_hash
        or decision_value["policy_ref"] != policy_ref
        or decision_value["policy_hash"] != policy_hash
    ):
        raise ValueError("retrieval returned a mismatched route decision")
    port_refs = _wire_refs_from_items(raw["blob_refs"], "plan_route.blob_refs")
    if not {policy_ref, decision_ref}.issubset(port_refs):
        raise ValueError("plan_route blob closure omits route policy/decision")
    values.update({
        "route_policy_ref": policy_ref,
        "route_policy_hash": policy_hash,
        "route_decision_ref": decision_ref,
        "route_id": decision_value["route_id"],
        "stage": "route_planned",
    })
    _merge_policy_refs(values, {"route": policy_ref})
    closure = _state_refs(state) | port_refs
    return StatePatch({"values": values, "blob_refs": _blob_items(sorted(closure))})


async def load_pages_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    blobs, identity = _runtime(state, context)
    spec = await _load_spec(values, blobs)
    decision = await _load_route(values, blobs, spec)
    # Inspect only.  The output frontier does not exist yet, so observing here
    # would attach the command to the older route checkpoint and violate the
    # durable ordering required by the v6 contract.
    command = await poll_v6_control(context, run_id=identity.run_id, observe=False)
    if (
        command is not None
        and command.status in {"accepted", "observed"}
        and command.action in {"generate_now", "cancel_settle"}
    ):
        raw: object = {"page_result_refs": [], "blob_refs": []}
    else:
        retrieval = context.ports.get("retrieval")
        raw = await _call(
            getattr(retrieval, "load_pages", None),
            spec=spec.to_json(), identity=identity, route_decision=decision.to_json(),
        )
    if not isinstance(raw, Mapping) or set(raw) != {"page_result_refs", "blob_refs"}:
        raise ValueError("retrieval load_pages response keys differ")
    page_refs = raw["page_result_refs"]
    if not isinstance(page_refs, list) or any(not isinstance(ref, str) for ref in page_refs):
        raise ValueError("page_result_refs must be an ordered ref array")
    if len(set(page_refs)) != len(page_refs):
        raise ValueError("page_result_refs must be unique")
    for ref in page_refs:
        parse_blob_ref(ref)
    port_refs = _wire_refs_from_items(raw["blob_refs"], "load_pages.blob_refs")
    if not set(page_refs).issubset(port_refs):
        raise ValueError("load_pages blob closure omits page results")
    values.update({"page_result_refs": copy.deepcopy(page_refs), "stage": "pages_loaded"})
    return StatePatch({"values": values, "blob_refs": _blob_items(sorted(_state_refs(state) | port_refs))})


async def extract_candidate_bundles_handler(
    state: WorkflowState, context: WorkflowContext
) -> StatePatch:
    values = _values(state)
    blobs, identity = _runtime(state, context)
    spec = await _load_spec(values, blobs)
    decision = await _load_route(values, blobs, spec)
    page_refs = values.get("page_result_refs")
    if not isinstance(page_refs, list) or any(not isinstance(ref, str) for ref in page_refs):
        raise ValueError("page_result_refs are required before candidate extraction")
    # load_pages' output is now the committed input frontier.  Observe a
    # generate-now command here, before semantic extraction can dispatch any
    # new LLM work.  The semantic runtime still validates and freezes its
    # policies, while preserving the canonical page outcomes for admission.
    command = await poll_v6_control(context, run_id=identity.run_id, observe=True)
    upstream_fenced = (
        command is not None
        and command.status in {"accepted", "observed", "settled"}
        and command.action in {"generate_now", "cancel_settle"}
    )
    page_slots = [
        {
            "page_result_ref": ref,
            "route_decision_ref": str(values["route_decision_ref"]),
            "route_policy_ref": str(values["route_policy_ref"]),
        }
        for ref in page_refs
    ]
    semantic = context.ports.get("semantic")
    candidate_kwargs: dict[str, object] = {
        "spec": spec.to_json(),
        "route_decision": decision.to_json(),
        "page_slots": page_slots,
        "identity": identity,
    }
    if upstream_fenced:
        candidate_kwargs["allow_upstream"] = False
    raw = await _call(
        getattr(semantic, "produce_candidate_slots", None),
        **candidate_kwargs,
    )
    if not isinstance(raw, Mapping) or set(raw) != {"candidate_slots", "policy_refs", "blob_refs"}:
        raise ValueError("semantic candidate response keys differ")
    slots = raw["candidate_slots"]
    if not isinstance(slots, list):
        raise ValueError("candidate_slots must be an array")
    normalized: list[dict[str, JsonValue]] = []
    for item in slots:
        keys = {"logical_page_id", "work_group_id", "producer_outcome_ref", "bundle_ref"}
        if not isinstance(item, Mapping) or set(item) != keys:
            raise ValueError("candidate slot keys differ")
        producer_ref = _required_text(item["producer_outcome_ref"], "producer_outcome_ref")
        bundle_ref = item["bundle_ref"]
        parse_blob_ref(producer_ref)
        if bundle_ref is not None:
            parse_blob_ref(str(bundle_ref))
        normalized.append({key: copy.deepcopy(item[key]) for key in keys})
    port_refs = _wire_refs_from_items(raw["blob_refs"], "candidate.blob_refs")
    direct = {str(item["producer_outcome_ref"]) for item in normalized} | {
        str(item["bundle_ref"]) for item in normalized if item["bundle_ref"] is not None
    }
    if not direct.issubset(port_refs):
        raise ValueError("candidate blob closure omits slot refs")
    policies = raw["policy_refs"]
    if not isinstance(policies, Mapping) or set(policies) != {
        "extraction", "llm_extract", "llm_repair", "llm_inference"
    }:
        raise ValueError("semantic candidate policy refs differ")
    _merge_policy_refs(values, policies)
    values.update({"candidate_slot_results": normalized, "stage": "candidates_extracted"})
    return StatePatch({"values": values, "blob_refs": _blob_items(sorted(_state_refs(state) | port_refs | {str(ref) for ref in policies.values()}))})


def _page_identity(page: Mapping[str, Any]) -> tuple[str, str, str, str]:
    nested = page.get("page_record") or page.get("page_ref")
    source = nested if isinstance(nested, Mapping) else page
    body_ref = _required_text(source.get("body_ref") or page.get("body_ref"), "page.body_ref")
    page_id = _required_text(source.get("page_id") or page.get("page_id"), "page.page_id")
    family = str(source.get("authority_id") or page.get("source_family_id") or page_id)
    tier = str(page.get("source_tier") or ("first_party" if source.get("authority_id") else "general"))
    return body_ref, page_id, family, tier


def _fact_from_candidate(
    candidate: Any, *, run_id: str, spec_hash: str, page_id: str,
    source_family_id: str, source_tier: str, admission_policy_hash: str,
    status: str = "admitted",
) -> AdmittedResearchFactV1:
    payload = copy.deepcopy(candidate.payload)
    kind = candidate.candidate_kind
    if kind == "scalar":
        payload.pop("item_or_cell_id", None)
        item_or_cell_id = field = None
    elif kind == "matrix_cell":
        payload.pop("field_key", None)
        payload["claim_kind"] = "fact"
        item_or_cell_id, field = payload["cell_id"], "value"
    elif kind == "collection_field":
        item_or_cell_id, field = payload["item_id"], payload["field_key"]
    elif kind == "claim_fact":
        facet = payload.pop("facet_key")
        payload["facet_ids"] = [facet]
        payload["claim_kind"] = "policy_fact"
        item_or_cell_id, field = payload["claim_instance_id"], facet
    else:  # pragma: no cover - contract decoder rejects first
        raise ValueError("candidate kind is not fact-bearing")
    span_payload: dict[str, JsonValue] = {
        "page_id": page_id,
        "start": candidate.span_start_byte,
        "end": candidate.span_end_byte,
        "excerpt_hash": candidate.excerpt_hash,
    }
    span_id = "span_" + hashlib.sha256(canonical_json(span_payload).encode("utf-8")).hexdigest()[:24]
    binding_id = "binding_" + hashlib.sha256(
        canonical_json({"candidate_id": candidate.candidate_id, "span_id": span_id}).encode("utf-8")
    ).hexdigest()[:24]
    return AdmittedResearchFactV1.create(
        run_id=run_id, spec_hash=spec_hash, requirement_id=candidate.requirement_id,
        target_kind=kind, item_or_cell_id=item_or_cell_id, field_or_facet_key=field,
        candidate_id=candidate.candidate_id, page_id=page_id, span_id=span_id,
        binding_id=binding_id, source_family_id=source_family_id, source_tier=source_tier,
        admission_policy_hash=admission_policy_hash, status=status, semantic_payload=payload,
    )


async def admit_facts_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    blobs, identity = _runtime(state, context)
    spec = await _load_spec(values, blobs)
    await _load_route(values, blobs, spec)
    policy_refs = values.get("policy_refs")
    if not isinstance(policy_refs, Mapping) or set(policy_refs) != _POLICY_KEYS:
        raise ValueError("all eleven policy refs must be frozen before fact admission")
    slots = values.get("candidate_slot_results")
    if not isinstance(slots, list):
        raise ValueError("candidate_slot_results are required")
    admitted_refs: list[str] = []
    rejected: list[str] = []
    conflicts: list[str] = []
    provenance: set[str] = set(str(ref) for ref in values.get("page_result_refs", []))
    seen: dict[tuple[str, object, object], tuple[str, str]] = {}
    ordered_candidates: list[
        tuple[str, int, int, str, Any, tuple[str, str, str, bytes]]
    ] = []
    for slot in slots:
        if not isinstance(slot, Mapping):
            raise ValueError("candidate slot must be an object")
        producer_ref = str(slot["producer_outcome_ref"])
        producer = CandidateProducerOutcomeV1.from_json(await _read_json(blobs, producer_ref))
        if producer.logical_page_id != slot["logical_page_id"] or producer.work_group_id != slot["work_group_id"] or producer.bundle_ref != slot["bundle_ref"]:
            raise ValueError("candidate producer differs from state slot")
        provenance.update(producer.dependency_refs)
        provenance.add(producer_ref)
        if producer.bundle_ref is None:
            continue
        bundle_raw = await _read_json(blobs, producer.bundle_ref)
        page_result_ref = str(bundle_raw.get("page_result_ref"))
        page = await _read_json(blobs, page_result_ref)
        body_ref, page_id, family, tier = _page_identity(page)
        body = await blobs.get(parse_blob_ref(body_ref))
        bundle = EvidenceCandidateBundleV1.from_json(bundle_raw, body_bytes=body)
        if bundle.run_id != identity.run_id or bundle.spec_hash != spec.spec_hash or bundle.bundle_id == "":
            raise ValueError("candidate bundle identity differs")
        provenance.update({producer.bundle_ref, page_result_ref, body_ref})
        for candidate in bundle.candidates:
            ordered_candidates.append((
                bundle.work_group_id, bundle.page_plan_ordinal,
                candidate.candidate_ordinal, candidate.candidate_id, candidate,
                (page_id, family, tier, body),
            ))
    ordered_candidates.sort(key=lambda item: item[:4])
    requirements = {
        str(item["requirement_id"]): item for item in spec.requirements
    }
    admission_hash = parse_blob_ref(str(policy_refs["admission"]))
    for group_id, page_ordinal, _, candidate_id, candidate, (page_id, family, tier, body) in ordered_candidates:
        requirement = requirements.get(candidate.requirement_id)
        failure = validate_candidate_admission(
            candidate,
            requirement=requirement,
            body_bytes=body,
            work_group_id=group_id,
            logical_page_id=page_id,
            page_plan_ordinal=page_ordinal,
            source_family_id=family,
            source_tier=tier,
        )
        if failure is not None:
            rejected.append(candidate_id)
            continue
        fact = _fact_from_candidate(
            candidate, run_id=identity.run_id, spec_hash=spec.spec_hash,
            page_id=page_id, source_family_id=family, source_tier=tier,
            admission_policy_hash=admission_hash,
        )
        target = (fact.requirement_id, fact.item_or_cell_id, fact.field_or_facet_key)
        # Duplicate/conflict identity is evaluated on the frozen candidate
        # payload before the tagged fact projection removes transport-only
        # fields such as matrix ``field_key``.
        semantic_key = canonical_json(candidate.payload)
        prior = seen.get(target)
        duplicate_or_conflict = validate_candidate_admission(
            candidate,
            requirement=requirement,
            body_bytes=body,
            work_group_id=group_id,
            logical_page_id=page_id,
            page_plan_ordinal=page_ordinal,
            source_family_id=family,
            source_tier=tier,
            prior_semantic_payload=(json.loads(prior[1]) if prior is not None else None),
        )
        if duplicate_or_conflict == "duplicate_candidate":
            rejected.append(candidate_id)
            continue
        if duplicate_or_conflict == "conflicting_candidate":
            fact = _fact_from_candidate(
                candidate, run_id=identity.run_id, spec_hash=spec.spec_hash,
                page_id=page_id, source_family_id=family, source_tier=tier,
                admission_policy_hash=admission_hash, status="conflicted",
            )
            conflict_id = "conflict_" + hashlib.sha256(
                canonical_json({"target": list(target), "facts": sorted([prior[0], fact.fact_id])}).encode("utf-8")
            ).hexdigest()[:24]
            conflicts.append(conflict_id)
        fact_ref = await _put_json(
            blobs, identity, fact.to_json(),
            media_type="application/vnd.deskpet.admitted-research-fact.v1+json",
        )
        admitted_refs.append(fact_ref)
        provenance.add(fact_ref)
        seen[target] = (fact.fact_id, semantic_key)
    previous_refs = values.get("fact_batch_refs", [])
    if not isinstance(previous_refs, list):
        raise ValueError("fact_batch_refs must be an array")
    head = str(values.get("evidence_head_hash", GENESIS_EVIDENCE_HEAD))
    candidate_roots = {
        str(slot["producer_outcome_ref"])
        for slot in slots
    } | {
        str(slot["bundle_ref"])
        for slot in slots
        if slot["bundle_ref"] is not None
    }
    provenance = await _exact_provenance_closure(
        blobs,
        roots={
            *[str(ref) for ref in values.get("page_result_refs", [])],
            *admitted_refs,
            *candidate_roots,
        },
        policy_refs=policy_refs,
    )
    batch = EvidenceFactBatchV1.create(
        batch_kind="facts", run_id=identity.run_id, spec_hash=spec.spec_hash,
        previous_head_hash=head, ordinal=len(previous_refs),
        page_result_refs=tuple(str(ref) for ref in values.get("page_result_refs", [])),
        candidate_slot_results=slots, inference_slot_results=(),
        admitted_fact_refs=admitted_refs, registered_inference_refs=(),
        rejected_candidate_ids=rejected, conflict_ids=conflicts,
        provenance_refs=provenance, policy_refs=dict(policy_refs),
    )
    batch_ref = await _put_json(blobs, identity, batch.to_json(), media_type="application/vnd.deskpet.evidence-fact-batch.v1+json")
    values.update({
        "fact_batch_refs": [*previous_refs, batch_ref],
        "evidence_head_hash": batch.head_hash,
        "stage": "facts_admitted",
    })
    # Candidate extraction observed at the committed load_pages frontier.  The
    # input here contains its typed partial result, so admission can now settle
    # the command against the exact page/candidate evidence frontier.
    command = await poll_v6_control(context, run_id=identity.run_id, observe=True)
    if command is not None and command.action in {"generate_now", "cancel_settle"}:
        await settle_v6_control(
            context,
            command,
            result={
                "route": "evidence_frontier",
                "evidence_head_hash": batch.head_hash,
                "page_result_refs": [
                    str(ref) for ref in values.get("page_result_refs", [])
                ],
            },
        )
    closure = _state_refs(state) | provenance | {batch_ref, *admitted_refs}
    return StatePatch({"values": values, "blob_refs": _blob_items(sorted(closure))})


async def _ordered_fact_refs(values: Mapping[str, JsonValue], blobs: RegisteredBlobStore) -> tuple[list[str], list[str]]:
    raw_refs = values.get("fact_batch_refs")
    if not isinstance(raw_refs, list) or any(not isinstance(ref, str) for ref in raw_refs):
        raise ValueError("fact_batch_refs must be an ordered ref array")
    expected = GENESIS_EVIDENCE_HEAD
    facts: list[str] = []
    inferences: list[str] = []
    for ordinal, ref in enumerate(raw_refs):
        batch = EvidenceFactBatchV1.from_json(await _read_json(blobs, ref))
        if batch.ordinal != ordinal or batch.previous_head_hash != expected:
            raise ValueError("evidence batch chain identity mismatch")
        facts.extend(batch.admitted_fact_refs)
        inferences.extend(batch.registered_inference_refs)
        expected = batch.head_hash
    if expected != values.get("evidence_head_hash"):
        raise ValueError("evidence head differs from batch chain")
    return facts, inferences


async def synthesize_inferences_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    blobs, identity = _runtime(state, context)
    spec = await _load_spec(values, blobs)
    decision = await _load_route(values, blobs, spec)
    facts, _ = await _ordered_fact_refs(values, blobs)
    semantic = context.ports.get("semantic")
    raw = await _call(
        getattr(semantic, "synthesize_inference_slots", None),
        spec=spec.to_json(), route_decision=decision.to_json(),
        evidence_head_hash=_required_text(values.get("evidence_head_hash"), "evidence_head_hash"),
        admitted_fact_refs=facts, identity=identity,
    )
    if not isinstance(raw, Mapping) or set(raw) != {"inference_slots", "policy_refs", "blob_refs"}:
        raise ValueError("semantic inference response keys differ")
    slots = raw["inference_slots"]
    if not isinstance(slots, list):
        raise ValueError("inference_slots must be an array")
    normalized: list[dict[str, JsonValue]] = []
    for item in slots:
        keys = {"work_group_id", "effect_outcome_ref", "proposal_bundle_ref"}
        if not isinstance(item, Mapping) or set(item) != keys:
            raise ValueError("inference slot keys differ")
        parse_blob_ref(_required_text(item["effect_outcome_ref"], "effect_outcome_ref"))
        if item["proposal_bundle_ref"] is not None:
            parse_blob_ref(str(item["proposal_bundle_ref"]))
        normalized.append({key: copy.deepcopy(item[key]) for key in keys})
    port_refs = _wire_refs_from_items(raw["blob_refs"], "inference.blob_refs")
    direct = {str(item["effect_outcome_ref"]) for item in normalized} | {
        str(item["proposal_bundle_ref"]) for item in normalized if item["proposal_bundle_ref"] is not None
    }
    if not direct.issubset(port_refs):
        raise ValueError("inference blob closure omits slot refs")
    policies = raw["policy_refs"]
    if not isinstance(policies, Mapping) or set(policies) != {"llm_inference", "inference"}:
        raise ValueError("semantic inference policy refs differ")
    _merge_policy_refs(values, policies)
    values.update({"inference_slot_results": normalized, "stage": "inferences_synthesized"})
    return StatePatch({"values": values, "blob_refs": _blob_items(sorted(_state_refs(state) | port_refs | {str(ref) for ref in policies.values()}))})


async def register_inferences_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    blobs, identity = _runtime(state, context)
    spec = await _load_spec(values, blobs)
    decision = await _load_route(values, blobs, spec)
    fact_refs, prior_inferences = await _ordered_fact_refs(values, blobs)
    if prior_inferences:
        raise ValueError("inference frontier may be appended only once")
    facts = {
        ref: AdmittedResearchFactV1.from_json(await _read_json(blobs, ref))
        for ref in fact_refs
    }
    slots = values.get("inference_slot_results")
    if not isinstance(slots, list):
        raise ValueError("inference_slot_results are required")
    groups = {
        str(item["work_group_id"]): set(str(ref) for ref in item["requirement_ids"])
        for item in decision.to_json()["work_groups"]
    }
    policy_refs = values.get("policy_refs")
    if not isinstance(policy_refs, Mapping) or set(policy_refs) != _POLICY_KEYS:
        raise ValueError("all policy refs must remain frozen")
    registered_refs: list[str] = []
    provenance: set[str] = set()
    for slot in slots:
        effect_ref = str(slot["effect_outcome_ref"])
        provenance.add(effect_ref)
        bundle_ref = slot["proposal_bundle_ref"]
        if bundle_ref is None:
            continue
        bundle = InferenceProposalBundleV1.from_json(await _read_json(blobs, str(bundle_ref)))
        if bundle.run_id != identity.run_id or bundle.spec_hash != spec.spec_hash or bundle.input_evidence_head_hash != values["evidence_head_hash"] or bundle.work_group_id != slot["work_group_id"]:
            raise ValueError("inference proposal bundle identity differs")
        provenance.add(str(bundle_ref))
        for proposal in bundle.proposals:
            proposed = tuple(proposal.premise_fact_refs)
            resolved = tuple(ref for ref in proposed if ref in facts)
            reasons: list[str] = []
            if len(resolved) != len(proposed):
                reasons.append("inference_premise_missing")
            if proposal.requirement_id not in groups.get(bundle.work_group_id, set()) or any(facts[ref].requirement_id != proposal.requirement_id for ref in resolved):
                reasons.append("inference_premise_requirement_mismatch")
            status = "rejected" if reasons else "registered"
            bindings = tuple(sorted({facts[ref].binding_id for ref in resolved}))
            inference = RegisteredInferenceV1.create(
                run_id=identity.run_id, spec_hash=spec.spec_hash,
                requirement_id=proposal.requirement_id,
                item_or_cell_id=proposal.item_or_cell_id,
                inference_kind=proposal.inference_kind, facet_ids=proposal.facet_ids,
                normalized_proposition=proposal.normalized_proposition,
                proposed_premise_fact_refs=proposed, premise_fact_refs=resolved,
                premise_binding_ids=bindings, model_id=proposal.model_id,
                model_policy_ref=proposal.model_policy_ref,
                inference_policy_hash=parse_blob_ref(str(policy_refs["inference"])),
                status=status, reason_codes=reasons,
            )
            inference_ref = await _put_json(blobs, identity, inference.to_json(), media_type="application/vnd.deskpet.registered-inference.v1+json")
            registered_refs.append(inference_ref)
            provenance.add(inference_ref)
            provenance.update(resolved)
    prior_batches = values["fact_batch_refs"]
    inference_roots = {
        str(slot["effect_outcome_ref"])
        for slot in slots
    } | {
        str(slot["proposal_bundle_ref"])
        for slot in slots
        if slot["proposal_bundle_ref"] is not None
    } | set(registered_refs)
    provenance = await _exact_provenance_closure(
        blobs,
        roots=inference_roots,
        policy_refs=policy_refs,
    )
    batch = EvidenceFactBatchV1.create(
        batch_kind="inferences", run_id=identity.run_id, spec_hash=spec.spec_hash,
        previous_head_hash=str(values["evidence_head_hash"]), ordinal=len(prior_batches),
        page_result_refs=(), candidate_slot_results=(), inference_slot_results=slots,
        admitted_fact_refs=(), registered_inference_refs=registered_refs,
        rejected_candidate_ids=(), conflict_ids=(), provenance_refs=provenance,
        policy_refs=dict(policy_refs),
    )
    batch_ref = await _put_json(blobs, identity, batch.to_json(), media_type="application/vnd.deskpet.evidence-fact-batch.v1+json")
    values.update({"fact_batch_refs": [*prior_batches, batch_ref], "evidence_head_hash": batch.head_hash, "stage": "inferences_registered"})
    command = await poll_v6_control(context, run_id=identity.run_id, observe=True)
    if command is not None and command.action in {"generate_now", "cancel_settle"}:
        await settle_v6_control(
            context,
            command,
            result={
                "route": "evidence_frontier",
                "evidence_head_hash": batch.head_hash,
                "inference_slot_count": len(slots),
            },
        )
    return StatePatch({"values": values, "blob_refs": _blob_items(sorted(_state_refs(state) | provenance | {batch_ref, *registered_refs}))})


async def assess_answer_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    blobs, identity = _runtime(state, context)
    spec = await _load_spec(values, blobs)
    fact_refs, inference_refs = await _ordered_fact_refs(values, blobs)
    objects: dict[str, Mapping[str, JsonValue]] = {}
    for ref in [*fact_refs, *inference_refs]:
        objects[ref] = await _read_json(blobs, ref)
    facts, inferences = decode_assessment_inputs(
        fact_refs, inference_refs, spec, registered_objects=objects
    )
    policy_refs = values.get("policy_refs")
    if not isinstance(policy_refs, Mapping) or set(policy_refs) != _POLICY_KEYS:
        raise ValueError("assessment requires the frozen policy set")
    assessment = assess_requirements(
        spec=spec, evidence_head_hash=str(values["evidence_head_hash"]),
        facts=facts, inferences=inferences, ordered_fact_refs=fact_refs,
        ordered_inference_refs=inference_refs,
        policy_hash=parse_blob_ref(str(policy_refs["assessment"])),
    )
    assessment_ref = await _put_json(blobs, identity, assessment.to_json(), media_type="application/vnd.deskpet.answer-assessment.v1+json")
    values.update({
        "assessment_ref": assessment_ref,
        "assessment_hash": assessment.assessment_hash,
        "assessment_input_hash": assessment.assessment_input_hash,
        "stage": "answer_assessed",
    })
    return StatePatch({"values": values, "blob_refs": _blob_items(sorted(_state_refs(state) | {assessment_ref}))})


__all__ = [
    "ADMISSION_POLICY_V1", "ASSESSMENT_POLICY_V1", "INFERENCE_POLICY_V1",
    "admit_facts_handler", "assess_answer_handler", "compile_spec_handler",
    "extract_candidate_bundles_handler", "load_pages_handler", "plan_route_handler",
    "register_inferences_handler", "synthesize_inferences_handler",
    "validate_candidate_admission",
]
