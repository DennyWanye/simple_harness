"""Real schema/compiler mismatch and persisted-v4 recovery on the new default."""
from copy import deepcopy
from dataclasses import replace

import pytest
from simple_harness import thaw_json

from deskpet.memory import analysis_proposal as v3, analysis_proposal_v4 as v4
from deskpet.memory import analysis_proposal_v5 as v5, analysis_protocol
from tests.memory.test_procedure_adoption import (
    compilation, ADOPTION, REPORTED, UNCERTAIN,
    test_public_materialization_and_response_only_reopen_keep_persisted_protocol as replay,
)


def _request(case, protocol):
    return replace(case.request, prompt_version=protocol.PROMPT_VERSION,
        result_schema_version=protocol.RESULT_SCHEMA_VERSION, policy_version=protocol.POLICY_VERSION)


def test_wire_schema_excludes_other_bodies_and_keeps_old_protocols():
    schema = thaw_json(v5.proposal_tool_spec().parameters)
    branches = schema["properties"]["operations"]["items"]["anyOf"]
    assert len(branches) == 4
    assert {b["properties"]["memory_type"]["enum"][0] for b in branches} == {
        "semantic", "episode", "procedure", "prospective"}
    for branch in branches:
        kind = branch["properties"]["memory_type"]["enum"][0]
        assert {"semantic", "episode", "procedure", "prospective"}.intersection(branch["properties"]) == {kind}
        assert kind in branch["required"] and branch["additionalProperties"] is False
    case = compilation()
    for protocol in (v3, v4, v5):
        assert analysis_protocol.protocol_for_request(_request(case, protocol)) is protocol
    assert analysis_protocol.PROMPT_VERSION == v5.PROMPT_VERSION
    assert "anyOf" not in v4.PROPOSAL_TOOL_SCHEMA["properties"]["operations"]["items"]
    assert "intent_kind" not in v3.PROPOSAL_TOOL_SCHEMA["properties"]["operations"]["items"]["properties"]["procedure"]["properties"]


def test_discriminator_rejects_observed_cross_body_shape_and_retains_source_checks():
    for text, intent, expected in ((ADOPTION, "adoption", "active"),
                                   (REPORTED, "reported_steps", "draft"),
                                   (UNCERTAIN, "uncertain", "draft")):
        case = compilation(text, intent=intent)
        request = _request(case, v5)
        def compile(raw):
            return v5.compile_proposal(raw, request=request, items=case.items,
                base_revision=1, plan_id="v5-control", now=1)
        good = compile(case.proposal)
        assert not good.rejected and good.plan.operations[0].lifecycle_state.value == expected
        bad = deepcopy(case.proposal)
        bad["operations"][0]["semantic"] = {"subject_entity": "user", "predicate": "x", "object_value": "y"}
        rejected = compile(bad)
        assert rejected.plan is None and rejected.rejected[0].detail["reason"] == "operation_discriminator_mismatch"
        bad = deepcopy(case.proposal)
        bad["operations"][0]["procedure"]["steps"][0] = "从未给出的步骤"
        assert compile(bad).plan is None


@pytest.mark.asyncio
async def test_persisted_v4_response_recovers_under_v5_without_another_provider(tmp_path, monkeypatch):
    await replay(tmp_path, monkeypatch, version=4, recover=True, recovery_version=5)
