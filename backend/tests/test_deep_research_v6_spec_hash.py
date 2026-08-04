from __future__ import annotations

import copy

import pytest

from deskpet.workflows.definitions.deep_research_v6_compiler import compile_official_exact_fact
from deskpet.workflows.definitions.deep_research_v6_contracts import (
    ResearchSpecV1,
    ResearchSpecValidationError,
    derive_spec_hash,
    validate_continuation_spec_hash,
)


QUESTION = "深入调研：2024年中国总人口和出生人口分别是多少？优先国家统计局。"


def test_spec_hash_and_id_are_recomputed_on_load() -> None:
    value = compile_official_exact_fact(QUESTION).to_json()
    assert derive_spec_hash(value) == value["spec_hash"]
    assert value["spec_id"] == "rs_" + value["spec_hash"][:24]
    value["answer_locale"] = "en-US"
    with pytest.raises(ResearchSpecValidationError) as error:
        ResearchSpecV1.from_json(value)
    assert error.value.code == "spec_hash_mismatch"


def test_hash_is_independent_of_mapping_insertion_order() -> None:
    original = compile_official_exact_fact(QUESTION).to_json()
    reversed_keys = dict(reversed(list(original.items())))
    assert ResearchSpecV1.from_json(reversed_keys).spec_hash == original["spec_hash"]


def test_requirement_order_is_semantic() -> None:
    original = compile_official_exact_fact(QUESTION).to_json()
    changed = copy.deepcopy(original)
    changed["requirements"].reverse()
    with pytest.raises(ResearchSpecValidationError) as error:
        ResearchSpecV1.from_json(changed)
    assert error.value.code in {"ordinal_invalid", "spec_hash_mismatch"}


def test_continuation_cannot_replace_frozen_spec() -> None:
    spec_hash = compile_official_exact_fact(QUESTION).spec_hash
    validate_continuation_spec_hash(spec_hash, spec_hash)
    with pytest.raises(ResearchSpecValidationError) as error:
        validate_continuation_spec_hash(spec_hash, "0" * 64)
    assert error.value.code == "continuation_spec_mismatch"
