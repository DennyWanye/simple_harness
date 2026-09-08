"""v8（事件 L）：指代不能当事实存；关系端点可以是已有记忆。

turn 15「记住：秋分资料整理这套校对流程，就按我前面说的 Python 环境执行。」在 v7 下只能被
表达成一条 `object_value="前面说的 Python 环境"` 的语义声明——那是悬空指针，既召不回也无法被
以后的纠正覆盖。v8 一端拒绝指代值，另一端给出两条合法出路：把指代解析成候选的原值，或提出
applies_to 关系（端点可以是已有的 semantic / procedure / prospective 记忆）。
"""
from dataclasses import replace

import pytest

from deskpet.memory import analysis_proposal as v3
from deskpet.memory import analysis_proposal_v4 as v4
from deskpet.memory import analysis_proposal_v5 as v5
from deskpet.memory import analysis_proposal_v5_1 as v5_1
from deskpet.memory import analysis_proposal_v6 as v6
from deskpet.memory import analysis_proposal_v7 as v7
from deskpet.memory import analysis_proposal_v8 as v8
from deskpet.memory import analysis_protocol
from deskpet.memory.semantic_correction import anaphoric_reference_marker
from tests.memory.test_analysis_proposal_v6 import _request
from tests.memory.test_procedure_adoption import compilation

# The exact HM-TO-A6 turn-15 sentence, recovered from
# .local-test-evidence/2026-09-08/native-a6-run4/.../analysis-attempt-input-07f9af61…
TURN15 = "记住：秋分资料整理这套校对流程，就按我前面说的 Python 环境执行。"
QUOTE = "秋分资料整理这套校对流程，就按我前面说的 Python 环境执行"

PYTHON_CANDIDATE = {
    "candidate_key": "semantic-candidate-a13c722f",
    "memory_id": "cognitive-memory-python",
    "revision": 1,
    "privacy_class": "personal",
    "information_attributes": ["preference"],
    "payload": {"subject_entity": "user:self", "predicate": "proofreading_python_version",
                "object_value": "Python 3.12", "qualifiers": ["做资料校对时"]},
}
FLOW_CANDIDATE = {
    "candidate_key": "relation-candidate-flow",
    "memory_id": "cognitive-memory-flow",
    "revision": 2,
    "memory_type": "procedure",
    "privacy_class": "personal",
    "information_attributes": [],
    "payload": {"name": "秋分资料整理校对流程", "steps": ["先列清单"], "applicability": ["general"]},
}


def _compile(proposal, *, case, candidates=(), relation_candidates=()):
    return v8.compile_proposal(proposal, request=_request(case, v8), items=case.items,
        base_revision=1, plan_id="host-v8-unit", now=1.0,
        candidates=candidates, relation_candidates=relation_candidates)


def _claim(*, value, candidate_key=None, action="create", quote=QUOTE, op_id="op-1",
           subject="秋分资料整理校对流程", predicate="execution_environment"):
    op = {"operation_id": op_id, "memory_type": "semantic", "action": action, "candidate_key": "",
          "evidence_item_id": None, "exact_quote": quote, "reason_code": "explicit_user_statement",
          "semantic": {"subject_entity": subject, "predicate": predicate, "object_value": value}}
    if candidate_key is not None:
        op[v8.OBJECT_VALUE_CANDIDATE_KEY] = candidate_key
    return op


def _relation(*, source_op="", source_key="", target_op="", target_key="", quote=QUOTE, op_id="rel-1"):
    body = {"relation_kind": "applies_to"}
    if source_op:
        body["source_operation_id"] = source_op
    if source_key:
        body["source_candidate_key"] = source_key
    if target_op:
        body["target_operation_id"] = target_op
    if target_key:
        body["target_candidate_key"] = target_key
    return {"operation_id": op_id, "memory_type": "semantic_relation", "action": "create",
            "evidence_item_id": None, "exact_quote": quote,
            "reason_code": "explicit_user_statement", "semantic_relation": body}


def _bind(case, operations):
    item_id = case.items[0].item_id
    for op in operations:
        op["evidence_item_id"] = item_id
    return {"outcome": "mutate", "operations": operations}


# ------------------------------------------------------------------ wire / versioning
def test_v8_is_current_and_every_persisted_protocol_still_resolves():
    case = compilation()
    assert analysis_protocol.PROMPT_VERSION == v8.PROMPT_VERSION
    assert analysis_protocol.RESULT_SCHEMA_VERSION == v8.RESULT_SCHEMA_VERSION
    assert analysis_protocol.POLICY_VERSION == v8.POLICY_VERSION
    assert analysis_protocol.VALIDATOR_VERSION == v8.VALIDATOR_VERSION != v7.VALIDATOR_VERSION
    for protocol in (v3, v4, v5, v5_1, v6, v7, v8):
        assert analysis_protocol.protocol_for_request(_request(case, protocol)) is protocol


def test_v8_prompt_and_wire_extend_v7_without_touching_it():
    assert v8.PROMPT_VERSION in v8.ANALYSIS_SYSTEM_INSTRUCTION
    assert v7.PROMPT_VERSION not in v8.ANALYSIS_SYSTEM_INSTRUCTION
    assert "contest_semantic" in v8.ANALYSIS_SYSTEM_INSTRUCTION  # v7 rules survive
    assert "回指规则" in v8.ANALYSIS_SYSTEM_INSTRUCTION and "不能是指代短语" in v8.ANALYSIS_SYSTEM_INSTRUCTION
    assert "source_candidate_key" in v8.ANALYSIS_SYSTEM_INSTRUCTION
    branches = v8.PROPOSAL_TOOL_SCHEMA["properties"]["operations"]["items"]["anyOf"]
    semantic = next(b for b in branches if b["properties"]["memory_type"]["enum"] == ["semantic"])
    relation = next(b for b in branches if b["properties"]["memory_type"]["enum"] == ["semantic_relation"])
    assert v8.OBJECT_VALUE_CANDIDATE_KEY in semantic["properties"]
    assert relation["properties"]["semantic_relation"]["required"] == ["relation_kind"]
    assert {"source_candidate_key", "target_candidate_key"} <= set(
        relation["properties"]["semantic_relation"]["properties"])
    # v6/v7 wires are untouched: no new keys, endpoints still mandatory operation ids.
    v7_relation = next(b for b in v7.PROPOSAL_TOOL_SCHEMA["properties"]["operations"]["items"]["anyOf"]
                       if b["properties"]["memory_type"]["enum"] == ["semantic_relation"])
    assert v7_relation["properties"]["semantic_relation"]["required"] == [
        "relation_kind", "source_operation_id", "target_operation_id"]
    assert v8.OBJECT_VALUE_CANDIDATE_KEY not in next(
        b for b in v7.PROPOSAL_TOOL_SCHEMA["properties"]["operations"]["items"]["anyOf"]
        if b["properties"]["memory_type"]["enum"] == ["semantic"])["properties"]


def test_v8_compiler_refuses_another_version():
    case = compilation()
    with pytest.raises(v3.AnalysisProposalRejected) as exc:
        v8.compile_proposal({"outcome": "no_mutation", "operations": []},
                            request=_request(case, v7), items=case.items,
                            base_revision=1, plan_id="v8-guard", now=1.0)
    assert exc.value.code == "analysis_protocol_unsupported"


# ------------------------------------------------------------------ anaphora grammar
@pytest.mark.parametrize("value", [
    "前面说的 Python 环境", "我前面说的 Python 环境", "上面提到的目录", "之前说的那台机器",
    "上述环境", "同上", "as mentioned above", "the one I mentioned",
])
def test_anaphoric_values_are_recognised(value):
    assert anaphoric_reference_marker(value) is not None


@pytest.mark.parametrize("value", [
    "Python 3.12", "外接硬盘 / 校对归档", "以主清单 A 为准", "", "   ", None, 3.12,
])
def test_plain_values_are_not_anaphoric(value):
    assert anaphoric_reference_marker(value) is None


def test_incident_l_literal_claim_is_rejected_with_a_stable_reason():
    """The exact attempt-4 operation: the anaphor may never masquerade as a fact."""
    case = compilation(TURN15)
    proposal = _bind(case, [_claim(value="前面说的 Python 环境")])
    compiled = _compile(proposal, case=case, candidates=[PYTHON_CANDIDATE])
    assert compiled.outcome == "no_mutation"
    assert [(r.operation_id, r.code) for r in compiled.rejected] == [
        ("op-1", v8.ANAPHORIC_OBJECT_VALUE)]
    assert compiled.rejected[0].detail["marker"] == "前面说的"


def test_anaphoric_value_is_rejected_for_revise_and_contest_too():
    case = compilation(TURN15)
    for action in ("revise_semantic", "contest_semantic"):
        proposal = _bind(case, [_claim(value="之前说的 Python 环境", action=action)])
        proposal["operations"][0]["candidate_key"] = PYTHON_CANDIDATE["candidate_key"]
        compiled = _compile(proposal, case=case, candidates=[PYTHON_CANDIDATE])
        assert [r.code for r in compiled.rejected] == [v8.ANAPHORIC_OBJECT_VALUE]


# ------------------------------------------------------------------ reference resolution
def test_reference_resolves_to_the_candidates_exact_value():
    case = compilation(TURN15)
    proposal = _bind(case, [_claim(value="Python 3.12",
                                   candidate_key=PYTHON_CANDIDATE["candidate_key"])])
    compiled = _compile(proposal, case=case, candidates=[PYTHON_CANDIDATE])
    assert compiled.outcome == "mutate" and compiled.rejected == ()
    op = compiled.plan.operations[0]
    assert op.payload.object_value == "Python 3.12"
    assert op.payload.subject_entity == "秋分资料整理校对流程"
    assert op.kind.value == "create" and op.target is None


@pytest.mark.parametrize("value,candidate_key,code", [
    ("Python 3.13", PYTHON_CANDIDATE["candidate_key"], v8.REFERENCE_VALUE_MISMATCH),
    ("Python 3.12", "semantic-candidate-invented", v8.REFERENCE_CANDIDATE_UNKNOWN),
])
def test_reference_resolution_cannot_invent_a_value_or_a_key(value, candidate_key, code):
    case = compilation(TURN15)
    proposal = _bind(case, [_claim(value=value, candidate_key=candidate_key)])
    compiled = _compile(proposal, case=case, candidates=[PYTHON_CANDIDATE])
    assert [(r.operation_id, r.code) for r in compiled.rejected] == [("op-1", code)]


def test_reference_resolution_requires_a_referring_sentence():
    """Without an anaphor the model would just be importing an unrelated fact."""
    plain = "记住：秋分资料整理这套校对流程用 Python 跑。"
    case = compilation(plain)
    proposal = _bind(case, [_claim(value="Python 3.12", quote="秋分资料整理这套校对流程用 Python 跑",
                                   candidate_key=PYTHON_CANDIDATE["candidate_key"])])
    compiled = _compile(proposal, case=case, candidates=[PYTHON_CANDIDATE])
    assert [r.code for r in compiled.rejected] == [v8.REFERENCE_NOT_ANAPHORIC]


def test_reference_resolution_is_create_only():
    case = compilation(TURN15)
    proposal = _bind(case, [_claim(value="Python 3.12", action="revise_semantic",
                                   candidate_key=PYTHON_CANDIDATE["candidate_key"])])
    compiled = _compile(proposal, case=case, candidates=[PYTHON_CANDIDATE])
    assert [r.code for r in compiled.rejected] == [v8.REFERENCE_ACTION_INVALID]


# ------------------------------------------------------------------ relation endpoints
def test_relation_expected_case_existing_fact_applies_to_existing_flow():
    """A6-6 的 turn-15 形状：两端都已存在时，本轮 plan 只新建 relation memory 本身。"""
    case = compilation(TURN15)
    proposal = _bind(case, [_relation(source_key=PYTHON_CANDIDATE["candidate_key"],
                                      target_key=FLOW_CANDIDATE["candidate_key"])])
    compiled = _compile(proposal, case=case, candidates=[PYTHON_CANDIDATE],
                        relation_candidates=[FLOW_CANDIDATE])
    assert compiled.outcome == "mutate" and compiled.rejected == ()
    op = compiled.plan.operations[0]
    assert op.memory_type.value == "semantic" and op.payload.semantic_kind.value == "relation"
    assert op.payload.relation_kind.value == "applies_to"
    assert (op.payload.source_endpoint.memory_id, op.payload.source_endpoint.revision) == (
        PYTHON_CANDIDATE["memory_id"], 1)
    assert (op.payload.target_endpoint.memory_id, op.payload.target_endpoint.revision) == (
        FLOW_CANDIDATE["memory_id"], 2)
    # Existing endpoints are resolved by the SDK, so they carry no in-plan dependency.
    assert op.depends_on_operation_ids == ()
    assert op.evidence_spans[0].exact_quote == QUOTE


def test_relation_may_mix_a_new_claim_with_an_existing_flow():
    case = compilation(TURN15)
    proposal = _bind(case, [
        _claim(value="Python 3.12", candidate_key=PYTHON_CANDIDATE["candidate_key"]),
        _relation(source_op="op-1", target_key=FLOW_CANDIDATE["candidate_key"]),
    ])
    compiled = _compile(proposal, case=case, candidates=[PYTHON_CANDIDATE],
                        relation_candidates=[FLOW_CANDIDATE])
    assert compiled.rejected == ()
    rel = compiled.plan.operations[1]
    assert rel.payload.source_endpoint.operation_id == "op-1"
    assert rel.depends_on_operation_ids == ("op-1",)


@pytest.mark.parametrize("kwargs,code", [
    # both forms / neither form on one side
    (dict(source_op="op-1", source_key=PYTHON_CANDIDATE["candidate_key"],
          target_key=FLOW_CANDIDATE["candidate_key"]), v8.RELATION_ENDPOINT_AMBIGUOUS),
    (dict(source_key=PYTHON_CANDIDATE["candidate_key"]), v8.RELATION_ENDPOINT_AMBIGUOUS),
    # invented keys
    (dict(source_key="semantic-candidate-invented",
          target_key=FLOW_CANDIDATE["candidate_key"]), v8.RELATION_ENDPOINT_UNKNOWN),
    (dict(source_key=PYTHON_CANDIDATE["candidate_key"],
          target_key="relation-candidate-invented"), v8.RELATION_ENDPOINT_UNKNOWN),
    # v6 role rule kept: a Procedure can never be the source, a fact never the target
    (dict(source_key=FLOW_CANDIDATE["candidate_key"],
          target_key=FLOW_CANDIDATE["candidate_key"]), v8.RELATION_ENDPOINT_UNKNOWN),
    (dict(source_key=PYTHON_CANDIDATE["candidate_key"],
          target_key=PYTHON_CANDIDATE["candidate_key"]), v8.RELATION_ENDPOINT_UNKNOWN),
])
def test_existing_endpoint_rules(kwargs, code):
    case = compilation(TURN15)
    compiled = _compile(_bind(case, [_relation(**kwargs)]), case=case,
                        candidates=[PYTHON_CANDIDATE], relation_candidates=[FLOW_CANDIDATE])
    assert compiled.outcome == "no_mutation"
    assert [(r.operation_id, r.code) for r in compiled.rejected] == [("rel-1", code)]


def test_existing_endpoint_of_the_wrong_memory_type_is_rejected():
    """A prospective candidate is a legal target; a semantic one issued there is not."""
    wrong = {**FLOW_CANDIDATE, "candidate_key": "relation-candidate-wrong", "memory_type": "semantic"}
    case = compilation(TURN15)
    compiled = _compile(
        _bind(case, [_relation(source_key=PYTHON_CANDIDATE["candidate_key"],
                               target_key=wrong["candidate_key"])]),
        case=case, candidates=[PYTHON_CANDIDATE], relation_candidates=[wrong])
    assert [(r.operation_id, r.code) for r in compiled.rejected] == [
        ("rel-1", v8.RELATION_ENDPOINT_TYPE_INVALID)]


def test_v6_in_plan_relation_rules_are_unchanged_under_v8():
    """The r8-proven shape (claim + procedure + relation, all in-plan) still compiles."""
    from tests.memory.test_analysis_proposal_v6 import TEXT, _proposal, _relation as v6_relation

    case = compilation(TEXT)
    item = case.items[0]
    proposal = _proposal(item.item_id, v6_relation(
        "rel-1", item.item_id, "整理文件时备份目录用外接硬盘", "claim-lang", "procedure-1"))
    compiled = _compile(proposal, case=case)
    assert compiled.outcome == "mutate" and compiled.rejected == ()
    rel = {op.operation_id: op for op in compiled.plan.operations}["rel-1"]
    assert rel.payload.source_endpoint.operation_id == "claim-lang"
    assert rel.payload.target_endpoint.operation_id == "procedure-1"
    assert set(rel.depends_on_operation_ids) == {"claim-lang", "procedure-1"}


@pytest.mark.parametrize("source,target,code", [
    ("claim-lang", "claim-lang", v8.RELATION_SELF_LOOP),
    ("claim-lang", "claim-missing", v8.RELATION_ENDPOINT_UNKNOWN),
    ("procedure-1", "claim-lang", v8.RELATION_ENDPOINT_UNKNOWN),
])
def test_v6_in_plan_endpoint_rejections_are_unchanged_under_v8(source, target, code):
    from tests.memory.test_analysis_proposal_v6 import TEXT, _proposal, _relation as v6_relation

    case = compilation(TEXT)
    item = case.items[0]
    proposal = _proposal(item.item_id, v6_relation("rel-1", item.item_id, "先列清单", source, target))
    compiled = _compile(proposal, case=case)
    assert [(r.operation_id, r.code) for r in compiled.rejected] == [("rel-1", code)]


def test_unknown_operation_keys_are_still_refused():
    case = compilation(TURN15)
    op = _claim(value="Python 3.12", candidate_key=PYTHON_CANDIDATE["candidate_key"])
    op["procedure"] = {"name": "x", "steps": ["y"]}
    compiled = _compile(_bind(case, [op]), case=case, candidates=[PYTHON_CANDIDATE])
    assert [r.code for r in compiled.rejected] == [v3.PAYLOAD_INVALID]
    assert compiled.rejected[0].detail["reason"] == "operation_discriminator_mismatch"


def test_persisted_v7_request_never_sees_the_v8_grammar():
    """A v7 replay must still reject the resolved-reference key it never had."""
    case = compilation(TURN15)
    op = _claim(value="Python 3.12", candidate_key=PYTHON_CANDIDATE["candidate_key"])
    compiled = v7.compile_proposal(_bind(case, [op]), request=_request(case, v7),
        items=case.items, base_revision=1, plan_id="v7-replay", now=1.0,
        candidates=[PYTHON_CANDIDATE])
    assert [r.code for r in compiled.rejected] == [v3.PAYLOAD_INVALID]
    # …and it still accepts the literal it accepted on 2026-09-08 (persisted replay).
    literal = v7.compile_proposal(_bind(case, [_claim(value="前面说的 Python 环境")]),
        request=_request(case, v7), items=case.items, base_revision=1,
        plan_id="v7-replay", now=1.0, candidates=[PYTHON_CANDIDATE])
    assert literal.outcome == "mutate" and literal.rejected == ()
