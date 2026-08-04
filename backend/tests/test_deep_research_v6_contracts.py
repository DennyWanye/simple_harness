from __future__ import annotations

import copy

import pytest

from deskpet.workflows.definitions.deep_research_v6_compiler import compile_official_exact_fact
from deskpet.workflows.definitions.deep_research_v6_contracts import (
    ResearchSpecV1,
    ResearchSpecValidationError,
    build_requirement,
    format_blob_ref,
    parse_blob_ref,
    requirement_from_json,
)


QUESTION = "深入调研：2024年中国总人口和出生人口分别是多少？优先国家统计局。"


def _common(kind: str, key: str) -> dict:
    return {
        "kind": kind,
        "key": key,
        "label": key,
        "importance": "optional",
        "subject_ids": ["geo.cn"],
        "scope": {"schema_version": 1, "jurisdiction": "CN", "geography_ids": ["CN"], "population_definition": None, "qualifiers": []},
        "time_scope": {"schema_version": 1, "kind": "timeless", "start": None, "end": None, "as_of": None, "label": None},
        "source_constraint": {"schema_version": 1, "first_party": "not_required", "authority_roles": [], "preferred_authority_ids": [], "eligible_source_types": ["official_statistic"], "minimum_source_families": 1, "secondary_evidence": "context_only"},
    }


def test_spec_roundtrip_is_exact_and_defensive() -> None:
    compiled = compile_official_exact_fact(QUESTION)
    serialized = compiled.to_json()
    restored = ResearchSpecV1.from_json(serialized)
    assert restored.to_json() == serialized
    serialized["normalized_question"] = "mutated"
    assert restored.normalized_question == compiled.normalized_question


def test_unknown_key_fails_closed() -> None:
    serialized = compile_official_exact_fact(QUESTION).to_json()
    serialized["compat"] = True
    with pytest.raises(ResearchSpecValidationError) as error:
        ResearchSpecV1.from_json(serialized)
    assert error.value.code == "spec_keys_differ"


def test_blob_ref_roundtrip_is_strict() -> None:
    digest = "a" * 64
    assert parse_blob_ref(format_blob_ref(digest)) == digest
    for invalid in (digest, "SHA256:" + digest, "sha256:" + "A" * 64, "sha256:abc"):
        with pytest.raises(ResearchSpecValidationError):
            parse_blob_ref(invalid)


def test_all_requirement_union_members_roundtrip() -> None:
    matrix = _common("matrix", "matrix.example") | {
        "axes": [{"axis_id": "subject", "role": "subject", "label": "Subject", "members": [{"member_id": "cn", "label": "China", "subject_id": "geo.cn", "value_schema": None}]}],
        "cell_policy": {"minimum_admitted_bindings": 1, "allow_inference": False},
        "required_cells": {"mode": "cartesian_product", "excluded": []},
        "coverage": {"minimum_ratio_ppm": 1_000_000},
    }
    collection = _common("collection", "collection.example") | {
        "item_schema": {"entity_type": "country", "unique_key": ["name"], "fields": [{"field_key": "name", "label": "Name", "value_type": "string", "required": True, "unit": None, "minimum_admitted_bindings": 1}]},
        "selection": {"mode": "top_n", "minimum_items": 3, "maximum_items": 3, "as_of": "2024-12-31", "ranking_rule": {"metric_key": "name", "direction": "ascending", "tie_breakers": ["name"], "missing_metric": "ineligible"}},
        "dedupe": {"normalizer": "nfkc_casefold_v1", "collision_policy": "merge_equal_identity_else_conflict"},
    }
    claim_set = _common("claim_set", "claims.example") | {
        "claim_kinds": [{"claim_kind": "conclusion", "minimum_claims": 1, "maximum_claims": 3, "minimum_admitted_bindings": 1, "support_rule": "admitted_binding"}],
        "topic_facets": [{"facet_id": "main", "label": "Main", "minimum_claims": 1}],
        "coverage_mode": "all_minima",
        "contradiction_policy": "surface",
    }
    for ordinal, payload in enumerate((matrix, collection, claim_set)):
        requirement = build_requirement(payload, ordinal)
        assert requirement_from_json(requirement.to_json()).to_json() == requirement.to_json()


def test_requirement_identity_excludes_only_id_and_ordinal() -> None:
    spec = compile_official_exact_fact(QUESTION).to_json()
    requirement = spec["requirements"][0]
    moved = copy.deepcopy(requirement)
    moved["ordinal"] = 99
    assert requirement_from_json(moved).requirement_id == requirement["requirement_id"]
    changed = copy.deepcopy(requirement)
    changed["label"] += " changed"
    with pytest.raises(ResearchSpecValidationError) as error:
        requirement_from_json(changed)
    assert error.value.code == "requirement_id_mismatch"
