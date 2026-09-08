"""v7 wire: ``contest_semantic`` is added without touching any persisted protocol."""
from deskpet.memory import analysis_proposal as v3
from deskpet.memory import analysis_proposal_v4 as v4
from deskpet.memory import analysis_proposal_v5 as v5
from deskpet.memory import analysis_proposal_v5_1 as v5_1
from deskpet.memory import analysis_proposal_v6 as v6
from deskpet.memory import analysis_proposal_v7 as v7
from deskpet.memory import analysis_protocol
from tests.memory.test_analysis_proposal_v6 import _request
from tests.memory.test_procedure_adoption import compilation


def _semantic_branch(schema):
    branches = schema["properties"]["operations"]["items"]["anyOf"]
    return next(b for b in branches if b["properties"]["memory_type"]["enum"] == ["semantic"])


def test_v7_is_still_resolvable_after_the_v8_bump():
    # v8 (Incident L) is the current protocol; every persisted wire must keep replaying.
    case = compilation()
    for protocol in (v3, v4, v5, v5_1, v6, v7):
        assert analysis_protocol.protocol_for_request(_request(case, protocol)) is protocol
    assert analysis_protocol.PROMPT_VERSION != v7.PROMPT_VERSION


def test_contest_action_is_only_on_the_v7_semantic_branch():
    assert _semantic_branch(v7.PROPOSAL_TOOL_SCHEMA)["properties"]["action"]["enum"] == [
        "create", "revise_semantic", "contest_semantic"]
    # v6 and v5.1 wires are untouched.
    assert _semantic_branch(v6.PROPOSAL_TOOL_SCHEMA)["properties"]["action"]["enum"] == [
        "create", "revise_semantic"]
    assert _semantic_branch(v5_1.PROPOSAL_TOOL_SCHEMA)["properties"]["action"]["enum"] == [
        "create", "revise_semantic"]
    # The relation branch v6 introduced survives the copy.
    kinds = [b["properties"]["memory_type"]["enum"]
             for b in v7.PROPOSAL_TOOL_SCHEMA["properties"]["operations"]["items"]["anyOf"]]
    assert ["semantic_relation"] in kinds and len(kinds) == 5
    assert "contest_semantic" in v7.ANALYSIS_SYSTEM_INSTRUCTION
    assert v7.PROMPT_VERSION in v7.ANALYSIS_SYSTEM_INSTRUCTION
    assert v6.PROMPT_VERSION not in v7.ANALYSIS_SYSTEM_INSTRUCTION


def test_v7_compiler_refuses_a_request_of_another_version():
    case = compilation()
    try:
        v7.compile_proposal({"outcome": "no_mutation", "operations": []},
                            request=_request(case, v6), items=case.items,
                            base_revision=1, plan_id="v7-guard", now=1.0)
    except v3.AnalysisProposalRejected as exc:
        assert exc.code == "analysis_protocol_unsupported"
    else:  # pragma: no cover - guard must fire
        raise AssertionError("v7 compiled a v6 request")
