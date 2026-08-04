from __future__ import annotations

import inspect
import json
from dataclasses import replace

import pytest

from deskpet.companion.evaluation_read_tools import (
    DEFAULT_EVALUATION_READ_ADAPTER_BUILD_FINGERPRINT,
    EvaluationMemoryFixtureStoreV1,
    EvaluationMemoryRecordV1,
    EvaluationReadAuthorizationV1,
    EvaluationReadToolAdapterV1,
    EvaluationReadToolBindingV1,
    EvaluationReadToolError,
    ProductionReadToolIdentityV1,
)

_A_HASH = "a" * 64
_B_HASH = "b" * 64
_C_HASH = "c" * 64
_D_HASH = "d" * 64


def _production_identity() -> ProductionReadToolIdentityV1:
    return ProductionReadToolIdentityV1(
        tool_name="memory_recall",
        tool_spec_ref="deskpet.tools.memory:memory_recall",
        tool_spec_hash=_A_HASH,
        input_schema_hash=_B_HASH,
        execution_build_fingerprint=_C_HASH,
        effect_policy_ref="deskpet.tools.effect-policy:memory_recall",
        effect_policy_hash=_D_HASH,
    )


def _fixture() -> EvaluationMemoryFixtureStoreV1:
    return EvaluationMemoryFixtureStoreV1.freeze(
        fixture_id="case-memory-v1",
        records=[
            EvaluationMemoryRecordV1(
                record_id="record-b",
                text="The project theme is midnight blue.",
                metadata={"kind": "preference"},
            ),
            EvaluationMemoryRecordV1(
                record_id="record-a",
                text="The project uses blue icons.",
                metadata={"kind": "fact"},
            ),
        ],
        sanitized=True,
    )


def _adapter() -> tuple[
    EvaluationReadToolAdapterV1,
    EvaluationReadToolBindingV1,
    EvaluationMemoryFixtureStoreV1,
]:
    fixture = _fixture()
    binding = EvaluationReadToolBindingV1.bind(
        production_identity=_production_identity(),
        fixture=fixture,
    )
    return (
        EvaluationReadToolAdapterV1(binding=binding, fixture=fixture),
        binding,
        fixture,
    )


def test_fixture_is_canonical_and_old_candidate_share_exact_input() -> None:
    fixture = _fixture()
    reverse_fixture = EvaluationMemoryFixtureStoreV1.freeze(
        fixture_id=fixture.fixture_id,
        records=reversed(fixture.records),
        sanitized=True,
    )
    binding = EvaluationReadToolBindingV1.bind(
        production_identity=_production_identity(),
        fixture=fixture,
    )
    authorization = EvaluationReadAuthorizationV1.for_binding(binding)
    old_adapter = EvaluationReadToolAdapterV1(binding=binding, fixture=fixture)
    candidate_adapter = EvaluationReadToolAdapterV1(
        binding=binding,
        fixture=reverse_fixture,
    )

    assert reverse_fixture.fixture_ref == fixture.fixture_ref
    assert reverse_fixture.fixture_hash == fixture.fixture_hash
    old_result = old_adapter.execute(
        {"query": "project blue", "limit": 2},
        authorization=authorization,
    )
    candidate_result = candidate_adapter.execute(
        {"query": "project blue", "limit": 2},
        authorization=authorization,
    )
    assert old_result == candidate_result
    assert [match["record_id"] for match in old_result["matches"]] == [
        "record-a",
        "record-b",
    ]
    assert json.loads(json.dumps(old_result, sort_keys=True)) == old_result


def test_adapter_only_accepts_production_memory_recall_schema() -> None:
    adapter, binding, _ = _adapter()
    authorization = EvaluationReadAuthorizationV1.for_binding(binding)

    with pytest.raises(EvaluationReadToolError) as missing:
        adapter.execute({"query": "blue"}, authorization=authorization)
    with pytest.raises(EvaluationReadToolError) as extra:
        adapter.execute(
            {"query": "blue", "limit": 2, "owner_id": "live-owner"},
            authorization=authorization,
        )
    with pytest.raises(EvaluationReadToolError) as invalid_limit:
        adapter.execute(
            {"query": "blue", "limit": True},
            authorization=authorization,
        )

    assert missing.value.code == "evaluation_read_schema_mismatch"
    assert extra.value.code == "evaluation_read_schema_mismatch"
    assert invalid_limit.value.code == "evaluation_read_schema_mismatch"


@pytest.mark.parametrize(
    ("authorization_update", "expected_code"),
    [
        ({"origin": "normal"}, "evaluation_read_origin_forbidden"),
        ({"binding_hash": _B_HASH}, "evaluation_read_binding_drift"),
        (
            {"adapter_build_fingerprint": _B_HASH},
            "evaluation_read_adapter_build_drift",
        ),
        ({"fixture_hash": _B_HASH}, "evaluation_read_fixture_identity_drift"),
    ],
)
def test_adapter_fails_closed_on_origin_or_identity_drift(
    authorization_update: dict[str, object],
    expected_code: str,
) -> None:
    adapter, binding, _ = _adapter()
    authorization = replace(
        EvaluationReadAuthorizationV1.for_binding(binding),
        **authorization_update,
    )

    with pytest.raises(EvaluationReadToolError) as exc_info:
        adapter.execute(
            {"query": "blue", "limit": 2},
            authorization=authorization,
        )

    assert exc_info.value.code == expected_code
    assert exc_info.value.inconclusive is True


def test_adapter_fails_closed_on_production_spec_or_effect_drift() -> None:
    adapter, binding, _ = _adapter()
    drifted_identity = replace(
        binding.production_identity,
        effect_policy_hash=_A_HASH,
    )
    authorization = replace(
        EvaluationReadAuthorizationV1.for_binding(binding),
        production_identity=drifted_identity,
    )

    with pytest.raises(EvaluationReadToolError) as exc_info:
        adapter.execute(
            {"query": "blue", "limit": 2},
            authorization=authorization,
        )

    assert exc_info.value.code == "evaluation_read_production_identity_drift"
    assert exc_info.value.inconclusive is True


def test_fixture_restore_rejects_hash_mismatch() -> None:
    fixture = _fixture()
    payload = dict(fixture.frozen_payload())
    payload["records"][0]["text"] = "tampered"

    with pytest.raises(EvaluationReadToolError) as exc_info:
        EvaluationMemoryFixtureStoreV1.restore(
            payload,
            expected_fixture_ref=fixture.fixture_ref,
            expected_fixture_hash=fixture.fixture_hash,
        )

    assert exc_info.value.code == "evaluation_fixture_hash_mismatch"


def test_redacted_fixture_body_is_not_readable() -> None:
    fixture = _fixture().redact()
    binding = EvaluationReadToolBindingV1(
        production_identity=_production_identity(),
        adapter_id="evaluation.memory_recall",
        adapter_version="v1",
        adapter_build_fingerprint=DEFAULT_EVALUATION_READ_ADAPTER_BUILD_FINGERPRINT,
        fixture_ref=fixture.fixture_ref,
        fixture_hash=fixture.fixture_hash,
    )
    adapter = EvaluationReadToolAdapterV1(binding=binding, fixture=fixture)

    with pytest.raises(EvaluationReadToolError) as payload_error:
        fixture.frozen_payload()
    with pytest.raises(EvaluationReadToolError) as read_error:
        adapter.execute(
            {"query": "blue", "limit": 2},
            authorization=EvaluationReadAuthorizationV1.for_binding(binding),
        )

    assert payload_error.value.code == "evaluation_fixture_redacted"
    assert read_error.value.code == "evaluation_fixture_redacted"


def test_adapter_has_no_live_memory_dependency_or_fallback_parameter() -> None:
    parameters = inspect.signature(EvaluationReadToolAdapterV1).parameters

    assert set(parameters) == {"binding", "fixture"}
    assert not {
        "session_db",
        "retriever",
        "embedder",
        "owner_memory",
        "fallback",
    } & set(parameters)
