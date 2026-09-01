"""Deterministic assessment for every frozen v6 requirement shape.

This module is deliberately a read-only projection over admitted bindings and
registered claims.  It does not persist facts or introduce another ledger.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cmp_to_key

from ..contracts import JsonValue, canonical_json, validate_json_value
from .deep_research_v6_contracts import ResearchSpecV1, parse_blob_ref
from .deep_research_v6_evidence import (
    Q1_ASSESSMENT_POLICY_HASH,
    AdmittedResearchFactV1,
    AnswerAssessmentV1,
    RegisteredInferenceV1,
)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _canonical_strings(values: Sequence[str], name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must be an array")
    result = tuple(sorted(set(values)))
    if any(not isinstance(item, str) or not item for item in result):
        raise ValueError(f"{name} must contain non-empty strings")
    return result


@dataclass(frozen=True, slots=True)
class GenericAdmittedFactV1:
    """Small immutable assessment DTO, never a persistence owner.

    ``entity_values`` identifies a discovered collection item, while
    ``axis_member_ids`` identifies a matrix cell.  Claim-set inputs carry an
    actual ``item_or_cell_id`` and ``claim_kind``.  A registered inference may
    have no binding ids, but must have a content-addressed ``inference_ref``.
    """

    requirement_id: str
    binding_ids: tuple[str, ...]
    item_or_cell_id: str | None = None
    axis_member_ids: tuple[str, ...] = ()
    entity_values: tuple[tuple[str, JsonValue], ...] = ()
    field_key: str | None = None
    value: JsonValue = None
    claim_kind: str | None = None
    facet_ids: tuple[str, ...] = ()
    inference_ref: str | None = None
    as_of: str | None = None
    conflicted: bool = False

    def __post_init__(self) -> None:
        _required_text(self.requirement_id, "requirement_id")
        object.__setattr__(self, "binding_ids", _canonical_strings(self.binding_ids, "binding_ids"))
        object.__setattr__(self, "axis_member_ids", _canonical_strings(self.axis_member_ids, "axis_member_ids"))
        object.__setattr__(self, "facet_ids", _canonical_strings(self.facet_ids, "facet_ids"))
        if self.item_or_cell_id is not None:
            _required_text(self.item_or_cell_id, "item_or_cell_id")
        if self.field_key is not None:
            _required_text(self.field_key, "field_key")
        if self.claim_kind is not None:
            _required_text(self.claim_kind, "claim_kind")
        if self.as_of is not None:
            _required_text(self.as_of, "as_of")
        if not isinstance(self.conflicted, bool):
            raise ValueError("conflicted must be boolean")
        validate_json_value(self.value)
        entity_values = tuple(sorted(self.entity_values, key=lambda item: item[0]))
        if any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not item[0]
            for item in entity_values
        ):
            raise ValueError("entity_values must contain (field_key, JSON value) pairs")
        if len({item[0] for item in entity_values}) != len(entity_values):
            raise ValueError("entity_values field keys must be unique")
        for _, item_value in entity_values:
            validate_json_value(item_value)
        object.__setattr__(self, "entity_values", copy.deepcopy(entity_values))
        if self.inference_ref is not None:
            parse_blob_ref(self.inference_ref)
        if not self.binding_ids and self.inference_ref is None:
            raise ValueError("an admitted fact requires binding_ids or inference_ref")

    @classmethod
    def create(
        cls,
        *,
        requirement_id: str,
        binding_ids: Sequence[str] = (),
        item_or_cell_id: str | None = None,
        axis_member_ids: Sequence[str] = (),
        entity_values: Mapping[str, JsonValue] | None = None,
        field_key: str | None = None,
        value: JsonValue = None,
        claim_kind: str | None = None,
        facet_ids: Sequence[str] = (),
        inference_ref: str | None = None,
        as_of: str | None = None,
        conflicted: bool = False,
    ) -> GenericAdmittedFactV1:
        return cls(
            requirement_id=requirement_id,
            binding_ids=tuple(binding_ids),
            item_or_cell_id=item_or_cell_id,
            axis_member_ids=tuple(axis_member_ids),
            entity_values=tuple((key, copy.deepcopy(value)) for key, value in (entity_values or {}).items()),
            field_key=field_key,
            value=copy.deepcopy(value),
            claim_kind=claim_kind,
            facet_ids=tuple(facet_ids),
            inference_ref=inference_ref,
            as_of=as_of,
            conflicted=conflicted,
        )


@dataclass(frozen=True, slots=True)
class GenericAdmittedInferenceV1:
    """Non-persistent assessment projection of one registered inference."""

    requirement_id: str
    item_or_cell_id: str | None
    assessment_claim_kind: str
    facet_ids: tuple[str, ...]
    premise_binding_ids: tuple[str, ...]
    inference_ref: str
    support_status: str

    def __post_init__(self) -> None:
        _required_text(self.requirement_id, "requirement_id")
        if self.item_or_cell_id is not None:
            _required_text(self.item_or_cell_id, "item_or_cell_id")
        _required_text(self.assessment_claim_kind, "assessment_claim_kind")
        object.__setattr__(self, "facet_ids", _canonical_strings(self.facet_ids, "facet_ids"))
        object.__setattr__(self, "premise_binding_ids", _canonical_strings(self.premise_binding_ids, "premise_binding_ids"))
        parse_blob_ref(self.inference_ref)
        if self.support_status != "supported":
            raise ValueError("only registered supported inferences enter assessment")


def _requirement_index(spec: ResearchSpecV1) -> dict[str, Mapping[str, JsonValue]]:
    return {str(item["requirement_id"]): item for item in spec.requirements}


def decode_admitted_fact(
    fact: AdmittedResearchFactV1,
    *,
    spec: ResearchSpecV1,
) -> GenericAdmittedFactV1:
    """Decode the persisted tagged fact without consulting its candidate."""

    requirements = _requirement_index(spec)
    requirement = requirements.get(fact.requirement_id)
    if requirement is None:
        raise ValueError("admitted fact references unknown requirement")
    payload = fact.semantic_payload
    common = {
        "requirement_id": fact.requirement_id,
        "binding_ids": (fact.binding_id,),
        "item_or_cell_id": fact.item_or_cell_id,
        "value": copy.deepcopy(payload["value"]),
        "conflicted": fact.status == "conflicted",
    }
    if fact.target_kind == "scalar":
        common["item_or_cell_id"] = None
    elif fact.target_kind == "matrix_cell":
        common.update(
            axis_member_ids=tuple(str(item) for item in payload["axis_member_ids"]),
            claim_kind=str(payload["claim_kind"]),
        )
    elif fact.target_kind == "collection_field":
        schema = requirement.get("item_schema")
        if not isinstance(schema, Mapping) or not isinstance(schema.get("unique_key"), list):
            raise ValueError("collection fact cannot resolve spec unique keys")
        unique_keys = [str(item) for item in schema["unique_key"]]
        unique_values = payload["unique_key_values"]
        if not isinstance(unique_values, list) or len(unique_keys) != len(unique_values):
            raise ValueError("collection unique key values differ from spec")
        common.update(
            entity_values=dict(zip(unique_keys, copy.deepcopy(unique_values), strict=True)),
            field_key=str(payload["field_key"]),
            as_of=payload["as_of"],
        )
    elif fact.target_kind == "claim_fact":
        common.update(
            claim_kind=str(payload["claim_kind"]),
            facet_ids=tuple(str(item) for item in payload["facet_ids"]),
        )
    else:  # pragma: no cover - persisted decoder already rejects unknown tags
        raise ValueError("unsupported admitted fact target")
    return GenericAdmittedFactV1.create(**common)


_INFERENCE_ASSESSMENT_KIND = {
    "impact": "impact_inference",
    "comparison": "inference",
    "preference": "preference",
    "conclusion": "conclusion",
    "limitation": "limitation",
    "counterevidence": "counterevidence",
    "uncertainty": "uncertainty",
}


def decode_registered_inference(
    inference: RegisteredInferenceV1,
    *,
    inference_ref: str,
) -> GenericAdmittedInferenceV1 | None:
    """Decode only registered records; rejected audit records remain excluded."""

    if inference.status == "rejected":
        return None
    return GenericAdmittedInferenceV1(
        requirement_id=inference.requirement_id,
        item_or_cell_id=inference.item_or_cell_id,
        assessment_claim_kind=_INFERENCE_ASSESSMENT_KIND[inference.inference_kind],
        facet_ids=inference.facet_ids,
        premise_binding_ids=inference.premise_binding_ids,
        inference_ref=inference_ref,
        support_status="supported",
    )


def decode_assessment_inputs(
    ordered_fact_refs: Sequence[str],
    ordered_inference_refs: Sequence[str],
    spec: ResearchSpecV1,
    *,
    registered_objects: Mapping[str, Mapping[str, JsonValue]],
) -> tuple[tuple[GenericAdmittedFactV1, ...], tuple[GenericAdmittedInferenceV1, ...]]:
    """Resolve ordered registered refs into deterministic in-memory DTOs."""

    facts: list[GenericAdmittedFactV1] = []
    inferences: list[GenericAdmittedInferenceV1] = []
    for ref in ordered_fact_refs:
        parse_blob_ref(ref)
        try:
            value = registered_objects[ref]
        except KeyError as exc:
            raise ValueError("assessment fact ref missing") from exc
        facts.append(decode_admitted_fact(AdmittedResearchFactV1.from_json(value), spec=spec))
    for ref in ordered_inference_refs:
        parse_blob_ref(ref)
        try:
            value = registered_objects[ref]
        except KeyError as exc:
            raise ValueError("assessment inference ref missing") from exc
        decoded = decode_registered_inference(RegisteredInferenceV1.from_json(value), inference_ref=ref)
        if decoded is not None:
            inferences.append(decoded)
    return tuple(facts), tuple(inferences)


def _derived_id(prefix: str, requirement_id: str, values: Sequence[str]) -> str:
    payload: JsonValue = [requirement_id, *values]
    return prefix + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:24]


def derive_matrix_cell_id(requirement_id: str, member_ids: Sequence[str]) -> str:
    return _derived_id("cell_", requirement_id, member_ids)


def _normalize_unique_value(value: JsonValue) -> str:
    if isinstance(value, str):
        return unicodedata.normalize("NFKC", value).casefold().strip()
    return canonical_json(value)


def derive_collection_item_id(requirement_id: str, unique_values: Sequence[JsonValue]) -> str:
    return _derived_id(
        "item_", requirement_id, [_normalize_unique_value(value) for value in unique_values]
    )


def _result(
    requirement_id: str,
    item_id: str,
    status: str,
    binding_ids: Sequence[str],
    reasons: Sequence[str],
) -> dict[str, JsonValue]:
    return {
        "requirement_id": requirement_id,
        "item_or_cell_id": item_id,
        "support_status": status,
        "binding_ids": sorted(set(binding_ids)),
        "reason_codes": sorted(set(reasons)),
    }


def _assess_scalar(requirement: Mapping[str, JsonValue], facts: Sequence[GenericAdmittedFactV1]):
    requirement_id = str(requirement["requirement_id"])
    bindings = sorted({binding for fact in facts for binding in fact.binding_ids})
    conflicted = any(fact.conflicted for fact in facts)
    if conflicted:
        result = _result(requirement_id, requirement_id, "conflicted", bindings, ["admitted_conflict"])
    elif bindings:
        result = _result(requirement_id, requirement_id, "supported", bindings, ["binding_admitted"])
    else:
        result = _result(requirement_id, requirement_id, "missing", (), ["no_admitted_binding"])
    return [result], bool(bindings) and not conflicted, bool(bindings), conflicted


def _assess_matrix(requirement: Mapping[str, JsonValue], facts: Sequence[GenericAdmittedFactV1]):
    requirement_id = str(requirement["requirement_id"])
    axes = requirement["axes"]
    assert isinstance(axes, list)
    member_lists = [[str(member["member_id"]) for member in axis["members"]] for axis in axes]
    excluded = {tuple(str(value) for value in item) for item in requirement["required_cells"]["excluded"]}
    expected = [members for members in itertools.product(*member_lists) if members not in excluded]
    by_members: dict[tuple[str, ...], list[GenericAdmittedFactV1]] = {}
    for fact in facts:
        if fact.claim_kind not in {None, "fact", "inference", "preference"}:
            raise ValueError(f"matrix fact has invalid claim kind for {requirement_id}")
        members = tuple(
            next(member for member in member_list if member in fact.axis_member_ids)
            for member_list in member_lists
            if any(member in fact.axis_member_ids for member in member_list)
        )
        if len(members) != len(member_lists) or len(fact.axis_member_ids) != len(member_lists):
            raise ValueError(f"matrix fact has invalid axis identity for {requirement_id}")
        by_members.setdefault(members, []).append(fact)
    minimum = int(requirement["cell_policy"]["minimum_admitted_bindings"])
    results: list[dict[str, JsonValue]] = []
    supported = 0
    has_conflict = False
    for members in expected:
        cell_facts = by_members.get(members, [])
        # Inference and preference claims may be rendered for the cell, but
        # cannot impersonate the factual bindings required by cell_policy.
        evidence_facts = [fact for fact in cell_facts if fact.claim_kind in {None, "fact"}]
        bindings = sorted({binding for fact in evidence_facts for binding in fact.binding_ids})
        conflicted = any(fact.conflicted for fact in evidence_facts)
        cell_id = derive_matrix_cell_id(requirement_id, members)
        if conflicted:
            has_conflict = True
            results.append(_result(requirement_id, cell_id, "conflicted", bindings, ["cell_conflicted"]))
        elif len(bindings) >= minimum:
            supported += 1
            results.append(_result(requirement_id, cell_id, "supported", bindings, ["cell_minimum_met"]))
        else:
            results.append(_result(requirement_id, cell_id, "missing", bindings, ["cell_minimum_not_met"]))
    ratio = 1_000_000 if not expected else supported * 1_000_000 // len(expected)
    minimum_ratio = int(requirement["coverage"]["minimum_ratio_ppm"])
    # The coverage ratio is the requirement's completion threshold.  Any
    # supported cell is still useful partial output, matching collection's
    # honest N-1-of-N behavior.
    minimum_useful = supported > 0
    return results, ratio >= minimum_ratio and not has_conflict, minimum_useful, has_conflict


def _compare_json(left: JsonValue, right: JsonValue) -> int:
    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
        return (left > right) - (left < right)
    left_key, right_key = canonical_json(left), canonical_json(right)
    return (left_key > right_key) - (left_key < right_key)


def _assess_collection(requirement: Mapping[str, JsonValue], facts: Sequence[GenericAdmittedFactV1]):
    requirement_id = str(requirement["requirement_id"])
    schema = requirement["item_schema"]
    selection = requirement["selection"]
    unique_keys = [str(value) for value in schema["unique_key"]]
    fields = {str(field["field_key"]): field for field in schema["fields"]}
    groups: dict[tuple[str, ...], list[GenericAdmittedFactV1]] = {}
    raw_unique: dict[tuple[str, ...], tuple[JsonValue, ...]] = {}
    for fact in facts:
        entity = dict(fact.entity_values)
        if set(entity) != set(unique_keys):
            raise ValueError(f"collection fact unique key differs for {requirement_id}")
        if fact.field_key not in fields:
            raise ValueError(f"collection fact has unknown field {fact.field_key!r}")
        values = tuple(entity[key] for key in unique_keys)
        normalized = tuple(_normalize_unique_value(value) for value in values)
        groups.setdefault(normalized, []).append(fact)
        raw_unique.setdefault(normalized, values)

    candidates: list[dict[str, object]] = []
    for normalized, item_facts in groups.items():
        item_id = derive_collection_item_id(requirement_id, raw_unique[normalized])
        by_field: dict[str, list[GenericAdmittedFactV1]] = {}
        for fact in item_facts:
            assert fact.field_key is not None
            by_field.setdefault(fact.field_key, []).append(fact)
        bindings = sorted({binding for fact in item_facts for binding in fact.binding_ids})
        conflicts = any(fact.conflicted for fact in item_facts)
        values: dict[str, JsonValue] = {}
        missing_fields: list[str] = []
        for field_key, field in fields.items():
            field_facts = sorted(
                by_field.get(field_key, []), key=lambda fact: canonical_json(fact.value)
            )
            distinct = {
                _normalize_unique_value(fact.value)
                if field_key in unique_keys else canonical_json(fact.value)
                for fact in field_facts
            }
            if len(distinct) > 1:
                conflicts = True
            if field_facts:
                values[field_key] = field_facts[0].value
            field_bindings = {binding for fact in field_facts for binding in fact.binding_ids}
            if bool(field["required"]) and len(field_bindings) < int(field["minimum_admitted_bindings"]):
                missing_fields.append(field_key)
        as_of = selection["as_of"]
        if as_of is not None and any(fact.as_of != as_of for fact in item_facts):
            missing_fields.append("as_of")
        candidates.append({
            "item_id": item_id, "bindings": bindings, "conflicts": conflicts,
            "values": values, "missing": sorted(set(missing_fields)),
        })

    ranking = selection["ranking_rule"]
    eligible = [item for item in candidates if not item["conflicts"] and not item["missing"]]
    if ranking is not None:
        metric_key = str(ranking["metric_key"])
        if ranking["missing_metric"] == "ineligible":
            eligible = [item for item in eligible if metric_key in item["values"]]

        def compare(left: dict[str, object], right: dict[str, object]) -> int:
            left_values, right_values = left["values"], right["values"]
            assert isinstance(left_values, dict) and isinstance(right_values, dict)
            left_missing, right_missing = metric_key not in left_values, metric_key not in right_values
            if left_missing != right_missing:
                return 1 if left_missing else -1
            if not left_missing:
                compared = _compare_json(left_values[metric_key], right_values[metric_key])
                if ranking["direction"] == "descending":
                    compared = -compared
                if compared:
                    return compared
            for key in ranking["tie_breakers"]:
                compared = _compare_json(left_values.get(key), right_values.get(key))
                if compared:
                    return compared
            return (left["item_id"] > right["item_id"]) - (left["item_id"] < right["item_id"])

        eligible.sort(key=cmp_to_key(compare))
    else:
        eligible.sort(key=lambda item: str(item["item_id"]))
    selected = eligible[: int(selection["maximum_items"])]
    results = [
        _result(requirement_id, str(item["item_id"]), "supported", item["bindings"], ["collection_item_eligible"])
        for item in selected
    ]
    for item in candidates:
        if item in selected:
            continue
        if item["conflicts"]:
            results.append(_result(requirement_id, str(item["item_id"]), "conflicted", item["bindings"], ["unique_key_collision_conflict"]))
        elif item["missing"]:
            reasons = ["required_field_missing" if value != "as_of" else "as_of_mismatch" for value in item["missing"]]
            results.append(_result(requirement_id, str(item["item_id"]), "missing", item["bindings"], reasons))
    count = len(selected)
    minimum = int(selection["minimum_items"])
    has_conflict = any(bool(item["conflicts"]) for item in candidates)
    return results, count >= minimum and not has_conflict, count > 0, has_conflict


def rank_collection_item_ids(
    requirement: Mapping[str, JsonValue],
    facts: Sequence[GenericAdmittedFactV1],
) -> tuple[str, ...]:
    """Return the selected collection item ids in canonical ranking order.

    The assessment contract intentionally stores results in composite-identity
    order.  Renderers call this projection instead of reimplementing dedupe,
    eligibility, as-of, tie-break, and Top-N selection semantics.
    """

    if requirement.get("kind") != "collection":
        raise ValueError("rank_collection_item_ids requires a collection requirement")
    results, _, _, _ = _assess_collection(requirement, facts)
    return tuple(
        str(item["item_or_cell_id"])
        for item in results
        if item["support_status"] == "supported"
    )


def _assess_claim_set(requirement: Mapping[str, JsonValue], facts: Sequence[GenericAdmittedFactV1]):
    requirement_id = str(requirement["requirement_id"])
    rules = {str(rule["claim_kind"]): rule for rule in requirement["claim_kinds"]}
    by_claim: dict[str, list[GenericAdmittedFactV1]] = {}
    for fact in facts:
        # Persisted claim facts are always tagged ``policy_fact``.  For open
        # research they are premise evidence for conclusion/limitation/etc.
        # and do not themselves satisfy a requested semantic claim kind.
        if (
            fact.claim_kind not in rules
            and fact.claim_kind != "policy_fact"
        ) or fact.item_or_cell_id is None:
            raise ValueError(f"claim-set fact has invalid kind/identity for {requirement_id}")
        by_claim.setdefault(fact.item_or_cell_id, []).append(fact)
    results: list[dict[str, JsonValue]] = []
    supported_by_kind = {kind: 0 for kind in rules}
    supported_by_facet: dict[str, int] = {}
    has_conflict = False
    for claim_id, claim_facts in by_claim.items():
        kinds = {fact.claim_kind for fact in claim_facts}
        facets = {facet for fact in claim_facts for facet in fact.facet_ids}
        bindings = sorted({binding for fact in claim_facts for binding in fact.binding_ids})
        conflicted = len(kinds) != 1 or any(fact.conflicted for fact in claim_facts)
        kind = next(iter(kinds))
        assert kind is not None
        rule = rules.get(kind)
        inference = any(fact.inference_ref is not None for fact in claim_facts)
        minimum_bindings = 1 if rule is None else int(rule["minimum_admitted_bindings"])
        supported = len(bindings) >= minimum_bindings
        if rule is not None and rule["support_rule"] == "admitted_binding_or_registered_inference":
            supported = supported or inference
        if conflicted:
            has_conflict = True
            results.append(_result(requirement_id, claim_id, "conflicted", bindings, ["claim_conflicted"]))
        elif supported:
            if kind in supported_by_kind:
                supported_by_kind[kind] += 1
            for facet in facets:
                supported_by_facet[facet] = supported_by_facet.get(facet, 0) + 1
            reason = "registered_inference" if inference and not bindings else "binding_admitted"
            results.append(_result(requirement_id, claim_id, "supported", bindings, [reason]))
        else:
            results.append(_result(requirement_id, claim_id, "missing", bindings, ["claim_support_minimum_not_met"]))
    kinds_met = all(supported_by_kind[kind] >= int(rule["minimum_claims"]) for kind, rule in rules.items())
    facets_met = all(
        supported_by_facet.get(str(facet["facet_id"]), 0) >= int(facet["minimum_claims"])
        for facet in requirement["topic_facets"]
    )
    blocking_conflict = has_conflict and requirement["contradiction_policy"] == "block_completed"
    supported_count = sum(supported_by_kind.values())
    return results, kinds_met and facets_met and not blocking_conflict, supported_count > 0, blocking_conflict


def assess_requirements(
    *,
    spec: ResearchSpecV1,
    evidence_head_hash: str,
    facts: Sequence[GenericAdmittedFactV1],
    inferences: Sequence[GenericAdmittedInferenceV1] = (),
    ordered_fact_refs: Sequence[str] = (),
    ordered_inference_refs: Sequence[str] = (),
    policy_hash: str = Q1_ASSESSMENT_POLICY_HASH,
) -> AnswerAssessmentV1:
    """Recompute one canonical assessment from immutable inputs."""

    spec_value = spec.to_json()
    requirements = spec_value["requirements"]
    assert isinstance(requirements, list)
    known_ids = {str(requirement["requirement_id"]) for requirement in requirements}
    if {fact.requirement_id for fact in facts} - known_ids:
        raise ValueError("assessment facts reference unknown requirements")
    by_requirement: dict[str, list[GenericAdmittedFactV1]] = {}
    for fact in facts:
        by_requirement.setdefault(fact.requirement_id, []).append(fact)
    if {item.requirement_id for item in inferences} - known_ids:
        raise ValueError("assessment inferences reference unknown requirements")
    # Existing assessors consume one deliberately non-persistent projection.
    # Inferences are represented without inventing page-local evidence and are
    # useful only to claim-set/integrity semantics; matrix factual coverage
    # continues to ignore comparison/preference records.
    for item in inferences:
        if item.assessment_claim_kind in {"inference", "preference"}:
            # Comparison/preference records are claim/integrity inputs, never
            # matrix factual coverage inputs.
            continue
        by_requirement.setdefault(item.requirement_id, []).append(
            GenericAdmittedFactV1.create(
                requirement_id=item.requirement_id,
                item_or_cell_id=item.item_or_cell_id,
                binding_ids=item.premise_binding_ids,
                claim_kind=item.assessment_claim_kind,
                facet_ids=item.facet_ids,
                inference_ref=item.inference_ref,
            )
        )

    results: list[dict[str, JsonValue]] = []
    missing: list[str] = []
    required_summaries: list[tuple[bool, bool, bool]] = []
    for requirement in requirements:
        requirement_id = str(requirement["requirement_id"])
        assessor = {
            "scalar": _assess_scalar,
            "matrix": _assess_matrix,
            "collection": _assess_collection,
            "claim_set": _assess_claim_set,
        }[str(requirement["kind"])]
        requirement_results, complete, minimum_useful, conflict = assessor(
            requirement, by_requirement.get(requirement_id, ())
        )
        results.extend(requirement_results)
        if requirement["importance"] == "required":
            required_summaries.append((complete, minimum_useful, conflict))
            if not complete:
                missing.append(requirement_id)

    results.sort(key=lambda item: (str(item["requirement_id"]), str(item["item_or_cell_id"])))
    required_complete = all(item[0] for item in required_summaries)
    # Partial means the run has at least one useful required result while one
    # or more other required results remain missing. Requiring every required
    # item to be minimally useful collapses honest N-1-of-N scalar answers to
    # insufficient and contradicts the matrix/collection partial contract.
    minimum_useful = bool(required_summaries) and any(item[1] for item in required_summaries)
    has_conflict = any(item[2] for item in required_summaries)
    if has_conflict:
        status, reasons = "needs_evidence", ["blocking_evidence_conflict"]
    elif required_complete:
        status, reasons = "completed_candidate", ["all_required_supported"]
    elif minimum_useful:
        status, reasons = "partial_candidate", ["minimum_useful_met", "required_evidence_missing"]
    else:
        status, reasons = "insufficient", ["minimum_useful_not_met"]
    return AnswerAssessmentV1.create(
        spec_hash=str(spec_value["spec_hash"]),
        evidence_head_hash=evidence_head_hash,
        ordered_fact_refs=ordered_fact_refs,
        ordered_inference_refs=ordered_inference_refs,
        policy_hash=policy_hash,
        requirement_results=results,
        missing_requirement_ids=sorted(set(missing)),
        minimum_useful=minimum_useful,
        status=status,
        reason_codes=reasons,
    )


__all__ = [
    "GenericAdmittedFactV1",
    "GenericAdmittedInferenceV1",
    "assess_requirements",
    "decode_admitted_fact",
    "decode_assessment_inputs",
    "decode_registered_inference",
    "derive_collection_item_id",
    "derive_matrix_cell_id",
    "rank_collection_item_ids",
]
