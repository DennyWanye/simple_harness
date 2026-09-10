"""Runtime codec/freeze contract, not a claim of registered short production."""
from copy import deepcopy
import pytest
from simple_harness import thaw_json
from deskpet.execution.primary_dependencies import dependencies, parse_dependencies
from deskpet.execution.foreground_runtime import FrozenContextAuthority

SHORT = {"audit_id":"actual-audit-id", "chunk_ref":"actual-chunk-ref", "content_hash":"a"*64}


def test_v1_preserved_v2_exact_short_survives_deep_freeze_without_aliasing():
    old = {"schema_version":1,"evidence":[],"recall":[]}
    assert parse_dependencies(old) == old
    proof = dependencies(short_horizon=[SHORT])
    expected = deepcopy(proof)
    context = FrozenContextAuthority("host", "sdk", "owner", 1, "ref", "hash", "snapshot", (), "USER", (), visibility_dependencies=proof)
    proof["short_horizon"][0]["audit_id"] = "mutated-caller"
    assert thaw_json(context.visibility_dependencies) == expected
    empty_v2 = {**old, "schema_version":2, "short_horizon":[]}
    assert parse_dependencies(empty_v2) == empty_v2  # no silent v1 fallback


@pytest.mark.parametrize("fault", ["missing", "v1_extra", "typed", "hash", "version"])
def test_closed_v2_never_discards_an_unrecognized_carrier(fault):
    proof = dependencies(short_horizon=[SHORT])
    if fault == "missing":
        proof.pop("short_horizon")
    elif fault == "v1_extra":
        proof["schema_version"] = 1
    elif fault == "typed":
        proof["short_horizon"] = [{"result_id":"x","result_hash":"a"*64,"item_id":"i","item_hash":"b"*64}]
    elif fault == "hash":
        proof["short_horizon"][0]["content_hash"] = "bad"
    else:
        proof["schema_version"] = True
    with pytest.raises(ValueError):
        parse_dependencies(proof)


# 2026-09-10 removed with the Memory SDK: test_short_projection_keeps_sdk_dto_identity_bytes_and_host_source_copy
