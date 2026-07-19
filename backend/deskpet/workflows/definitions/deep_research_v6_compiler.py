"""Deterministic semantic compiler for the first DeepResearch v6 scenario."""

from __future__ import annotations

import copy
import hashlib
import re
import unicodedata
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from .deep_research_v6_contracts import (
    ClaimSetRequirement,
    ResearchSpecV1,
    ResearchSpecValidationError,
    RequirementV1,
    build_requirement,
    canonical_set,
    format_blob_ref,
    sha256_json,
)


COMPILER_POLICY = {
    "schema_version": 1,
    "policy_id": "deep-research-v6-deterministic-compiler-v1",
    "base_requirements_immutable": True,
    "llm_new_requirements_importance": "optional",
}
COMPLETION_POLICY = {
    "schema_version": 1,
    "policy_id": "deep-research-v6-completion-v1",
    "required_requirements_must_be_satisfied": True,
}
RENDER_PROFILE = {
    "schema_version": 1,
    "profile_id": "deep-research-v6-report-v1",
    "artifact_required": True,
}
COMPILER_POLICY_HASH = sha256_json(COMPILER_POLICY)
COMPLETION_POLICY_HASH = sha256_json(COMPLETION_POLICY)
RENDER_PROFILE_HASH = sha256_json(RENDER_PROFILE)


def normalize_question(question: str) -> str:
    if not isinstance(question, str) or not question.strip():
        raise ResearchSpecValidationError("spec_keys_differ", "$.question", "must be non-empty")
    return " ".join(unicodedata.normalize("NFKC", question).split())


def _extract_year(question: str) -> int:
    matches = sorted({int(value) for value in re.findall(r"(?<!\d)(20\d{2})(?!\d)", question)})
    if len(matches) != 1:
        raise ResearchSpecValidationError(
            "spec_merge_conflict", "$.normalized_question", "official exact fact requires one explicit year"
        )
    return matches[0]


def _source_constraint(preferred: bool) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "first_party": "preferred" if preferred else "not_required",
        "authority_roles": ["national_statistics_office"],
        "preferred_authority_ids": ["cn.nbs"] if preferred else [],
        "eligible_source_types": ["official_statistic"],
        "minimum_source_families": 1,
        "secondary_evidence": "context_only",
    }


def _scope() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "jurisdiction": "CN",
        "geography_ids": ["CN"],
        "population_definition": "national_population",
        "qualifiers": [],
    }


def _time_scope(year: int, kind: str) -> dict[str, Any]:
    if kind == "instant":
        return {
            "schema_version": 1, "kind": "instant", "start": None, "end": None,
            "as_of": f"{year}-12-31", "label": f"{year} year end",
        }
    return {
        "schema_version": 1, "kind": "period", "start": f"{year}-01-01",
        "end": f"{year}-12-31", "as_of": None, "label": f"{year} calendar year",
    }


def _value_schema(definition: str) -> dict[str, Any]:
    person = {
        "unit_id": "person", "symbol": "person",
        "to_canonical_numerator": 1, "to_canonical_denominator": 1,
    }
    ten_thousand = {
        "unit_id": "ten_thousand_person", "symbol": "10k person",
        "to_canonical_numerator": 10_000, "to_canonical_denominator": 1,
    }
    return {
        "schema_version": 1,
        "value_type": "integer",
        "quantity_kind": "population_count",
        "canonical_unit": person,
        "accepted_units": [person, ten_thousand],
        "definition": definition,
        "tolerance": {"mode": "exact", "numerator": 0, "denominator": 1},
    }


def _scalar_payload(
    *, key: str, label: str, definition: str, year: int, time_kind: str,
    source_constraint: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "kind": "scalar",
        "key": key,
        "label": label,
        "importance": "required",
        "subject_ids": ["geo.cn"],
        "scope": _scope(),
        "time_scope": _time_scope(year, time_kind),
        "source_constraint": copy.deepcopy(dict(source_constraint)),
        "value_schema": _value_schema(definition),
        "cardinality": {"minimum": 1, "maximum": 1},
    }


def _metric_payloads(question: str, year: int, source: Mapping[str, Any]) -> list[dict[str, Any]]:
    folded = question.casefold()
    wants_total = any(token in folded for token in ("总人口", "人口总数", "total population", "population total"))
    wants_births = any(token in folded for token in ("出生人口", "出生人数", "birth population", "number of births", "births"))
    result: list[dict[str, Any]] = []
    # Contract order is semantic and deliberately independent of phrase order.
    if wants_total:
        result.append(_scalar_payload(
            key="population.total.year_end", label=f"{year} year-end total population",
            definition="year_end_total_population", year=year, time_kind="instant",
            source_constraint=source,
        ))
    if wants_births:
        result.append(_scalar_payload(
            key="population.births.period", label=f"{year} births during year",
            definition="births_during_period", year=year, time_kind="period",
            source_constraint=source,
        ))
    if not result:
        raise ResearchSpecValidationError(
            "spec_merge_conflict", "$.normalized_question", "no supported official exact metric found"
        )
    return result


_IMMUTABLE_FIELDS = {
    "kind", "subject_ids", "scope", "time_scope", "value_schema", "axes",
    "item_schema", "selection",
}


def merge_requirement_candidates(
    deterministic: Sequence[Mapping[str, Any]],
    candidates: object | None,
) -> list[dict[str, Any]]:
    """Merge a validated supplement without allowing base deletion/downgrade.

    An invalid candidate bundle is discarded as a whole.  Semantic conflicts
    are fail-closed because silently accepting them would change the question.
    """

    base = [copy.deepcopy(dict(item)) for item in deterministic]
    if candidates is None:
        return base
    if not isinstance(candidates, list) or any(not isinstance(item, Mapping) for item in candidates):
        return base
    by_key = {str(item["key"]): item for item in base}
    additions: list[dict[str, Any]] = []
    for candidate_value in candidates:
        candidate = copy.deepcopy(dict(candidate_value))
        key = candidate.get("key")
        if not isinstance(key, str) or not key:
            return base
        current = by_key.get(key)
        if current is None:
            candidate.pop("requirement_id", None)
            candidate.pop("ordinal", None)
            candidate.pop("schema_version", None)
            candidate["importance"] = "optional"
            additions.append(candidate)
            by_key[key] = candidate
            continue
        for field in _IMMUTABLE_FIELDS:
            if field in candidate and candidate[field] != current.get(field):
                raise ResearchSpecValidationError(
                    "spec_merge_conflict", f"$.requirements[{key}].{field}", "candidate conflicts with deterministic base"
                )
        if candidate.get("importance") == "required":
            current["importance"] = "required"
        for nested in ("source_constraint",):
            if nested in candidate:
                candidate_source = candidate[nested]
                if not isinstance(candidate_source, Mapping):
                    return base
                current_source = current[nested]
                rank = {"not_required": 0, "preferred": 1, "required": 2}
                first_party = max(
                    (current_source["first_party"], candidate_source.get("first_party", "not_required")),
                    key=lambda value: rank[value],
                )
                eligible = sorted(set(current_source["eligible_source_types"]) & set(candidate_source.get("eligible_source_types", current_source["eligible_source_types"])))
                if not eligible:
                    raise ResearchSpecValidationError("spec_merge_conflict", f"$.requirements[{key}].source_constraint", "eligible source intersection is empty")
                current_source["first_party"] = first_party
                current_source["eligible_source_types"] = eligible
                current_source["preferred_authority_ids"] = canonical_set(
                    [*current_source["preferred_authority_ids"], *candidate_source.get("preferred_authority_ids", [])]
                )
    return base + sorted(additions, key=lambda value: str(value["key"]))


def _build_spec(
    question: str,
    *,
    answer_locale: str,
    year: int,
    intent_type: str,
    payloads: Sequence[Mapping[str, Any]],
) -> ResearchSpecV1:
    requirements: list[RequirementV1] = [build_requirement(payload, index) for index, payload in enumerate(payloads)]
    dimensions = []
    for index, requirement in enumerate(requirements):
        value = requirement.to_json()
        concepts = {
            "population.total.year_end": [
                "population",
                "year-end total population",
                "全国人口",
                "年末人口",
            ],
            "population.births.period": [
                "births",
                "annual births",
                "出生人口",
            ],
        }.get(str(value["key"]), [str(value["key"])])
        dimensions.append({
            "dimension_id": f"dim_{index:02d}",
            "ordinal": index,
            "requirement_ids": [value["requirement_id"]],
            "search_concepts": sorted(concepts),
            "time_scope": copy.deepcopy(value["time_scope"]),
            "source_constraint": copy.deepcopy(value["source_constraint"]),
        })
    preferred = any("cn.nbs" in requirement.to_json()["source_constraint"]["preferred_authority_ids"] for requirement in requirements)
    return ResearchSpecV1.create(
        normalized_question=question,
        intent_type=intent_type,
        answer_locale=answer_locale,
        subjects=[{
            "subject_id": "geo.cn", "label": "China",
            "aliases": ["China", "中国"], "entity_type": "country",
        }],
        user_constraints={
            "schema_version": 1,
            "as_of_date": f"{year}-12-31",
            "jurisdictions": ["CN"],
            "preferred_authority_ids": ["cn.nbs"] if preferred else [],
            "explicit_exclusions": [],
        },
        work_dimensions=dimensions,
        requirements=[requirement.to_json() for requirement in requirements],
        completion_policy_ref=format_blob_ref(COMPLETION_POLICY_HASH),
        completion_policy_hash=COMPLETION_POLICY_HASH,
        render_profile_ref=format_blob_ref(RENDER_PROFILE_HASH),
        render_profile_hash=RENDER_PROFILE_HASH,
        compiler_policy_hash=COMPILER_POLICY_HASH,
    )


def _stable_token(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold().strip()
    ascii_token = re.sub(r"[^a-z0-9]+", ".", folded).strip(".")
    if ascii_token:
        return ascii_token[:32]
    return hashlib.sha256(folded.encode("utf-8")).hexdigest()[:16]


def _generic_scope() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "jurisdiction": None,
        "geography_ids": [],
        "population_definition": None,
        "qualifiers": [],
    }


def _latest_time_scope(as_of_date: str) -> dict[str, Any]:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of_date) is None:
        raise ResearchSpecValidationError(
            "time_scope_invalid", "$.user_constraints.as_of_date", "must be YYYY-MM-DD"
        )
    return {
        "schema_version": 1,
        "kind": "latest",
        "start": None,
        "end": None,
        "as_of": as_of_date,
        "label": f"latest as of {as_of_date}",
    }


def _generic_source_constraint(
    *,
    first_party: str = "not_required",
    authority_roles: Sequence[str] = (),
    source_types: Sequence[str] = ("article", "report"),
    minimum_source_families: int = 1,
    secondary_evidence: str = "support_allowed",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "first_party": first_party,
        "authority_roles": sorted(set(authority_roles)),
        "preferred_authority_ids": [],
        "eligible_source_types": sorted(set(source_types)),
        "minimum_source_families": minimum_source_families,
        "secondary_evidence": secondary_evidence,
    }


def _subject(label: str, *, entity_type: str) -> dict[str, Any]:
    return {
        "subject_id": f"subject.{_stable_token(label)}",
        "label": label.strip(),
        "aliases": [],
        "entity_type": entity_type,
    }


def _build_typed_spec(
    question: str,
    *,
    intent_type: str,
    answer_locale: str,
    as_of_date: str,
    subjects: Sequence[Mapping[str, Any]],
    deterministic_payloads: Sequence[Mapping[str, Any]],
    dimension_specs: Sequence[tuple[int, Sequence[str]]],
    llm_candidates: object | None,
) -> ResearchSpecV1:
    merged = merge_requirement_candidates(deterministic_payloads, llm_candidates)
    requirements = [build_requirement(payload, index) for index, payload in enumerate(merged)]
    covered = {requirement_index for requirement_index, _ in dimension_specs}
    if covered != set(range(len(deterministic_payloads))):
        raise ValueError("dimension specs must cover deterministic requirements")
    dimensions: list[dict[str, Any]] = []
    expanded_specs = list(dimension_specs)
    expanded_specs.extend(
        (index, (str(requirements[index].to_json()["key"]),))
        for index in range(len(deterministic_payloads), len(requirements))
    )
    for ordinal, (requirement_index, concepts) in enumerate(expanded_specs):
        if requirement_index < 0 or requirement_index >= len(requirements):
            raise ValueError("dimension requirement index is out of range")
        requirement = requirements[requirement_index]
        value = requirement.to_json()
        dimensions.append(
            {
                "dimension_id": f"dim_{ordinal:02d}",
                "ordinal": ordinal,
                "requirement_ids": [value["requirement_id"]],
                "search_concepts": sorted(set(concepts)),
                "time_scope": copy.deepcopy(value["time_scope"]),
                "source_constraint": copy.deepcopy(value["source_constraint"]),
            }
        )
    return ResearchSpecV1.create(
        normalized_question=question,
        intent_type=intent_type,
        answer_locale=answer_locale,
        subjects=[copy.deepcopy(dict(subject)) for subject in subjects],
        user_constraints={
            "schema_version": 1,
            "as_of_date": as_of_date,
            "jurisdictions": [],
            "preferred_authority_ids": [],
            "explicit_exclusions": [],
        },
        work_dimensions=dimensions,
        requirements=[requirement.to_json() for requirement in requirements],
        completion_policy_ref=format_blob_ref(COMPLETION_POLICY_HASH),
        completion_policy_hash=COMPLETION_POLICY_HASH,
        render_profile_ref=format_blob_ref(RENDER_PROFILE_HASH),
        render_profile_hash=RENDER_PROFILE_HASH,
        compiler_policy_hash=COMPILER_POLICY_HASH,
    )


def _comparison_subjects(question: str) -> tuple[str, str]:
    patterns = (
        r"(?:比较|对比)\s*([^，,。；;]+?)\s*(?:和|与|跟|vs\.?|versus)\s*([^，,。；;]+?)(?=\s*[，,。；;]|\s*按|$)",
        r"compare\s+(.+?)\s+(?:and|with|vs\.?|versus)\s+(.+?)(?=\s+(?:across|on|by)|[,.?]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, question, flags=re.IGNORECASE)
        if match:
            labels = tuple(value.strip() for value in match.groups())
            if len(set(labels)) == 2 and all(labels):
                return labels  # type: ignore[return-value]
    raise ResearchSpecValidationError(
        "spec_merge_conflict", "$.subjects", "comparison requires two explicit subjects"
    )


def _comparison_axes(question: str) -> tuple[str, ...]:
    match = re.search(r"按\s*(.+?)\s*(?:六|6)个(?:轴|维度|方面)(?:进行)?(?:比较|对比)", question)
    if match:
        axes = tuple(
            value.strip()
            for value in re.split(r"[、,，/]", match.group(1))
            if value.strip()
        )
        if len(axes) == 6 and len(set(axes)) == 6:
            return axes
    return ("功能", "价格", "易用性", "性能", "安全性", "生态")


def _compile_comparison(
    question: str,
    *,
    answer_locale: str,
    as_of_date: str,
    llm_candidates: object | None,
) -> ResearchSpecV1:
    labels = _comparison_subjects(question)
    subjects = [_subject(label, entity_type="product") for label in labels]
    axes = _comparison_axes(question)
    time_scope = _latest_time_scope(as_of_date)
    source = _generic_source_constraint(
        first_party="preferred",
        source_types=("official_product_documentation", "independent_review"),
        minimum_source_families=2,
    )
    payload = {
        "kind": "matrix",
        "key": "comparison.product_matrix",
        "label": "Product comparison",
        "importance": "required",
        "subject_ids": [str(subject["subject_id"]) for subject in subjects],
        "scope": _generic_scope(),
        "time_scope": time_scope,
        "source_constraint": source,
        "axes": [
            {
                "axis_id": "axis.subject",
                "role": "subject",
                "label": "Product",
                "members": [
                    {
                        "member_id": f"member.{subject['subject_id']}",
                        "label": subject["label"],
                        "subject_id": subject["subject_id"],
                        "value_schema": None,
                    }
                    for subject in subjects
                ],
            },
            {
                "axis_id": "axis.criterion",
                "role": "criterion",
                "label": "Criterion",
                "members": [
                    {
                        "member_id": f"criterion.{_stable_token(axis)}",
                        "label": axis,
                        "subject_id": None,
                        "value_schema": None,
                    }
                    for axis in axes
                ],
            },
        ],
        "cell_policy": {"minimum_admitted_bindings": 1, "allow_inference": True},
        "required_cells": {"mode": "cartesian_product", "excluded": []},
        "coverage": {"minimum_ratio_ppm": 1_000_000},
    }
    return _build_typed_spec(
        question,
        intent_type="comparison",
        answer_locale=answer_locale,
        as_of_date=as_of_date,
        subjects=subjects,
        deterministic_payloads=[payload],
        dimension_specs=[(0, [str(axis)]) for axis in axes],
        llm_candidates=llm_candidates,
    )


def _top_n_count(question: str) -> int | None:
    patterns = (
        r"top\s*[- ]?(\d+)",
        r"(?:前|最值得关注的?)\s*(\d+)\s*个",
        r"(\d+)\s*个[^，,。]*(?:排名|排行|榜单|最值得关注)",
    )
    for pattern in patterns:
        match = re.search(pattern, question, flags=re.IGNORECASE)
        if match:
            value = int(match.group(1))
            if 1 <= value <= 100:
                return value
    return None


def _compile_top_n(
    question: str,
    *,
    count: int,
    answer_locale: str,
    as_of_date: str,
    llm_candidates: object | None,
) -> ResearchSpecV1:
    subject = _subject("AI products", entity_type="product_collection")
    source = _generic_source_constraint(
        source_types=("official_product_documentation", "independent_review", "industry_report"),
        minimum_source_families=2,
    )
    time_scope = _latest_time_scope(as_of_date)
    fields = [
        ("product_name", "Product", "string", True),
        ("attention_score", "Attention ranking metric", "number", True),
        ("advantages", "Advantages", "string_list", True),
        ("disadvantages", "Disadvantages", "string_list", True),
    ]
    payload = {
        "kind": "collection",
        "key": "ranked.ai_products",
        "label": f"Top {count} AI products",
        "importance": "required",
        "subject_ids": [subject["subject_id"]],
        "scope": _generic_scope(),
        "time_scope": time_scope,
        "source_constraint": source,
        "item_schema": {
            "entity_type": "ai_product",
            "unique_key": ["product_name"],
            "fields": [
                {
                    "field_key": key,
                    "label": label,
                    "value_type": value_type,
                    "required": required,
                    "unit": None,
                    "minimum_admitted_bindings": 1,
                }
                for key, label, value_type, required in fields
            ],
        },
        "selection": {
            "mode": "top_n",
            "minimum_items": count,
            "maximum_items": count,
            "as_of": as_of_date,
            "ranking_rule": {
                "metric_key": "attention_score",
                "direction": "descending",
                "tie_breakers": ["product_name"],
                "missing_metric": "ineligible",
            },
        },
        "dedupe": {
            "normalizer": "nfkc_casefold_v1",
            "collision_policy": "merge_equal_identity_else_conflict",
        },
    }
    return _build_typed_spec(
        question,
        intent_type="top_n",
        answer_locale=answer_locale,
        as_of_date=as_of_date,
        subjects=[subject],
        deterministic_payloads=[payload],
        dimension_specs=[
            (0, ["AI products", "advantages", "disadvantages", "attention ranking"])
        ],
        llm_candidates=llm_candidates,
    )


def _claim_rule(
    claim_kind: str,
    *,
    minimum: int = 1,
    maximum: int = 8,
    inference: bool = False,
) -> dict[str, Any]:
    return {
        "claim_kind": claim_kind,
        "minimum_claims": minimum,
        "maximum_claims": maximum,
        "minimum_admitted_bindings": 1,
        "support_rule": (
            "admitted_binding_or_registered_inference" if inference else "admitted_binding"
        ),
    }


def _compile_policy(
    question: str,
    *,
    answer_locale: str,
    as_of_date: str,
    llm_candidates: object | None,
) -> ResearchSpecV1:
    subject = _subject("latest policy", entity_type="policy_topic")
    source = _generic_source_constraint(
        first_party="required",
        authority_roles=("policy_issuer",),
        source_types=("official_policy", "official_guidance"),
    )
    payload = {
        "kind": "claim_set",
        "key": "policy.direction_and_impact",
        "label": "Policy direction and impact",
        "importance": "required",
        "subject_ids": [subject["subject_id"]],
        "scope": _generic_scope(),
        "time_scope": _latest_time_scope(as_of_date),
        "source_constraint": source,
        "claim_kinds": [
            _claim_rule("policy_fact", minimum=4, maximum=16),
            _claim_rule("impact_inference", minimum=1, maximum=8, inference=True),
        ],
        "topic_facets": [
            {"facet_id": key, "label": label, "minimum_claims": 1}
            for key, label in (
                ("issuer", "Issuer"),
                ("document", "Document"),
                ("date", "Date"),
                ("commitment", "Original commitment"),
                ("impact", "Impact"),
            )
        ],
        "coverage_mode": "all_minima",
        "contradiction_policy": "block_completed",
    }
    return _build_typed_spec(
        question,
        intent_type="policy",
        answer_locale=answer_locale,
        as_of_date=as_of_date,
        subjects=[subject],
        deterministic_payloads=[payload],
        dimension_specs=[
            (0, ["policy issuer"]),
            (0, ["policy document"]),
            (0, ["policy date"]),
            (0, ["commitment"]),
            (0, ["impact"]),
        ],
        llm_candidates=llm_candidates,
    )


def _compile_open_research(
    question: str,
    *,
    answer_locale: str,
    as_of_date: str,
    llm_candidates: object | None,
) -> ResearchSpecV1:
    source = _generic_source_constraint(
        source_types=("article", "paper", "report"), minimum_source_families=2
    )
    subject = _subject("research topic", entity_type="topic")
    payload = {
        "kind": "claim_set",
        "key": "open_research.claims",
        "label": "Research claims",
        "importance": "required",
        "subject_ids": [subject["subject_id"]],
        "scope": _generic_scope(),
        "time_scope": {
            "schema_version": 1,
            "kind": "timeless",
            "start": None,
            "end": None,
            "as_of": None,
            "label": None,
        },
        "source_constraint": source,
        "claim_kinds": [
            _claim_rule("conclusion"),
            _claim_rule("limitation"),
            _claim_rule("counterevidence"),
            _claim_rule("uncertainty"),
        ],
        "topic_facets": [
            {"facet_id": "topic", "label": "Topic", "minimum_claims": 1}
        ],
        "coverage_mode": "all_minima",
        "contradiction_policy": "surface",
    }
    return _build_typed_spec(
        question,
        intent_type="open_research",
        answer_locale=answer_locale,
        as_of_date=as_of_date,
        subjects=[subject],
        deterministic_payloads=[payload],
        dimension_specs=[
            (0, ["conclusion"]),
            (0, ["limitation"]),
            (0, ["counterevidence"]),
            (0, ["uncertainty"]),
        ],
        llm_candidates=llm_candidates,
    )


def compile_official_exact_fact(
    question: str,
    *,
    answer_locale: str = "zh-CN",
    llm_candidates: object | None = None,
) -> ResearchSpecV1:
    normalized = normalize_question(question)
    year = _extract_year(normalized)
    folded = normalized.casefold()
    if not any(token in folded for token in ("中国", "china")):
        raise ResearchSpecValidationError("spec_merge_conflict", "$.subjects", "phase-1 compiler requires China")
    preferred = any(token in folded for token in ("国家统计局", "national bureau of statistics", " nbs"))
    source = _source_constraint(preferred)
    deterministic = _metric_payloads(normalized, year, source)
    merged = merge_requirement_candidates(deterministic, llm_candidates)
    return _build_spec(normalized, answer_locale=answer_locale, year=year, intent_type="official_exact_fact", payloads=merged)


def compile_research_spec(
    question: str,
    *,
    answer_locale: str = "zh-CN",
    llm_candidates: object | None = None,
    as_of_date: str | None = None,
) -> ResearchSpecV1:
    """Compile a deterministic v6 intent without delegating scope ownership to an LLM."""

    normalized = normalize_question(question)
    folded = normalized.casefold()
    frozen_as_of = as_of_date or date.today().isoformat()
    if re.search(r"(?<!\d)20\d{2}(?!\d)", normalized) and any(
        token in folded for token in ("总人口", "人口总数", "total population", "出生人口", "births")
    ):
        return compile_official_exact_fact(normalized, answer_locale=answer_locale, llm_candidates=llm_candidates)
    if any(token in folded for token in ("比较", "对比", "compare", " versus ", " vs ")):
        return _compile_comparison(
            normalized,
            answer_locale=answer_locale,
            as_of_date=frozen_as_of,
            llm_candidates=llm_candidates,
        )
    top_n = _top_n_count(normalized)
    if top_n is not None and any(
        token in folded for token in ("最值得关注", "排名", "排行", "榜单", "top")
    ):
        return _compile_top_n(
            normalized,
            count=top_n,
            answer_locale=answer_locale,
            as_of_date=frozen_as_of,
            llm_candidates=llm_candidates,
        )
    if any(token in folded for token in ("政策", "policy")) and any(
        token in folded for token in ("影响", "方向", "impact", "direction")
    ):
        return _compile_policy(
            normalized,
            answer_locale=answer_locale,
            as_of_date=frozen_as_of,
            llm_candidates=llm_candidates,
        )
    return _compile_open_research(
        normalized,
        answer_locale=answer_locale,
        as_of_date=frozen_as_of,
        llm_candidates=llm_candidates,
    )


__all__ = [
    "COMPILER_POLICY_HASH", "COMPLETION_POLICY_HASH", "RENDER_PROFILE_HASH",
    "compile_official_exact_fact", "compile_research_spec",
    "merge_requirement_candidates", "normalize_question",
]
