"""v9（事件 T）：本句自己点名的流程要在同一 plan 里建成节点，关系指向它。

事件 L 的 v8 让「按我前面说的 Python 环境」不再被抄成字面值，但它给出的两条出路里，
关系那一条（分支①）要求被回指的流程**已经**是 `procedure_candidates` 的一项——而 F-L1
决定了那几乎不可能：SDK 只让被真实用过三次、适用性指纹命中的 Procedure 露面。于是
HM-TO-A6 turn 15 只能落到分支②，变成一条语义声明，`cognitive_relations` 恒为 0。

v9 只加一条分支：**本句自己点名了一个可复用流程、并且就是在决定今后照此执行**时，
把这个流程在本轮建成 procedure 节点，再从被回指的事实候选连一条 `applies_to` 过去。
线格式、schema、编译器全部沿用 v8（`_compile_validated_proposal`），v9 是**策略版本**——
提示体进 `bind_attempt` 的哈希，改词就必须换协议 id，不能就地改 v8。

本文件钉三件事：协议身份（v3..v8 一条不丢）、v9 提示词的有序判定文本、
以及 turn-15 目标形状在 Host 编译器下真的成立（依赖、端点方向、排序）。
"""
import hashlib
import json
from dataclasses import replace

import pytest

from deskpet.memory import analysis_proposal as v3
from deskpet.memory import analysis_proposal_v4 as v4
from deskpet.memory import analysis_proposal_v5 as v5
from deskpet.memory import analysis_proposal_v5_1 as v5_1
from deskpet.memory import analysis_proposal_v6 as v6
from deskpet.memory import analysis_proposal_v7 as v7
from deskpet.memory import analysis_proposal_v8 as v8
from deskpet.memory import analysis_proposal_v9 as v9
from deskpet.memory import analysis_protocol
from tests.memory.test_analysis_proposal_v6 import _request
from tests.memory.test_analysis_proposal_v8 import PYTHON_CANDIDATE, TURN15
from tests.memory.test_procedure_adoption import compilation

QUOTE = "秋分资料整理这套校对流程，就按我前面说的 Python 环境执行"
FLOW_NAME = "秋分资料整理校对流程"
STEP = "就按我前面说的 Python 环境执行"


def _compile(proposal, *, case, candidates=(), relation_candidates=()):
    return v9.compile_proposal(proposal, request=_request(case, v9), items=case.items,
        base_revision=1, plan_id="host-v9-unit", now=1.0,
        candidates=candidates, relation_candidates=relation_candidates)


def _procedure(*, op_id="op-flow", intent="adoption", steps=(STEP,), name=FLOW_NAME):
    return {"operation_id": op_id, "memory_type": "procedure",
            "evidence_item_id": None, "exact_quote": TURN15,
            "reason_code": "explicit_user_statement",
            "procedure": {"name": name, "steps": list(steps), "intent_kind": intent,
                          "adoption_quote": STEP if intent == "adoption" else ""}}


def _relation(*, source_key="", source_op="", target_op="", target_key="", op_id="op-rel"):
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
            "evidence_item_id": None, "exact_quote": QUOTE,
            "reason_code": "explicit_user_statement", "semantic_relation": body}


def _bind(case, operations):
    for op in operations:
        op["evidence_item_id"] = case.items[0].item_id
    return {"outcome": "mutate", "operations": operations}


# ------------------------------------------------------------------ protocol identity
def test_v9_is_current_and_every_persisted_protocol_still_resolves():
    case = compilation()
    assert analysis_protocol.PROMPT_VERSION == v9.PROMPT_VERSION == "host-analysis-prompt/v9"
    assert analysis_protocol.RESULT_SCHEMA_VERSION == v9.RESULT_SCHEMA_VERSION
    assert analysis_protocol.POLICY_VERSION == v9.POLICY_VERSION
    # v9 adds one Host admission rule, so the validator id moves with it.
    assert analysis_protocol.VALIDATOR_VERSION == v9.VALIDATOR_VERSION == "host-analysis-validator/v5"
    assert v9.VALIDATOR_VERSION != v8.VALIDATOR_VERSION
    for protocol in (v3, v4, v5, v5_1, v6, v7, v8, v9):
        assert analysis_protocol.protocol_for_request(_request(case, protocol)) is protocol


def test_a_persisted_v8_request_still_renders_v8s_own_prompt():
    """``bind_attempt`` hashes the prompt body and the system text — v8 must not drift."""
    case = compilation()
    assert analysis_protocol.protocol_for_request(_request(case, v8)) is v8
    assert v8.PROMPT_VERSION in v8.ANALYSIS_SYSTEM_INSTRUCTION
    assert v9.PROMPT_VERSION not in v8.ANALYSIS_SYSTEM_INSTRUCTION
    assert "②否则，本句自己点名" not in v8.ANALYSIS_SYSTEM_INSTRUCTION
    with pytest.raises(v3.AnalysisProposalRejected) as exc:
        v9.compile_proposal({"outcome": "no_mutation", "operations": []},
                            request=_request(case, v8), items=case.items,
                            base_revision=1, plan_id="v9-guard", now=1.0)
    assert exc.value.code == "analysis_protocol_unsupported"


def test_v9_reuses_v8s_wire_and_admission_rules_object_for_object():
    # A copied schema could drift silently; v9 is a *policy* version, so it must be the
    # same object, and every rejection code must be v8's own constant.
    assert v9.PROPOSAL_TOOL_SCHEMA is v8.PROPOSAL_TOOL_SCHEMA
    assert v9.SUPPORTS_RELATION_CANDIDATES is True
    for name in ("ANAPHORIC_OBJECT_VALUE", "REFERENCE_ACTION_INVALID", "REFERENCE_CANDIDATE_UNKNOWN",
                 "REFERENCE_NOT_ANAPHORIC", "REFERENCE_VALUE_MISMATCH", "RELATION_ENDPOINT_UNKNOWN",
                 "RELATION_ENDPOINT_AMBIGUOUS", "RELATION_ENDPOINT_TYPE_INVALID", "RELATION_SELF_LOOP",
                 "OBJECT_VALUE_CANDIDATE_KEY"):
        assert getattr(v9, name) == getattr(v8, name)


# ------------------------------------------------------------------ the policy text
def test_v9_policy_is_an_ordered_four_branch_decision_ending_in_a_named_exit():
    text = v9.ANALYSIS_SYSTEM_INSTRUCTION
    assert v9.PROMPT_VERSION in text and v7.PROMPT_VERSION not in text
    assert "contest_semantic" in text  # every inherited v7 rule survives
    # ordered, judged once — the wording that took finish_reason=length back to baseline.
    assert "按顺序只走第一条成立的分支，判断一次即可" in text
    for marker in ("①", "②", "③", "④"):
        assert marker in text
    assert text.index("①") < text.index("②") < text.index("③") < text.index("④")
    # ④ still names the exit the model is allowed to take (DECISION-RELATION-EXTRACTION §5.1).
    assert "只记 episode（或 no_mutation）" in text
    # ② carries its own precondition and neutralises the inherited "不能补造" rule.
    assert "本句自己点名了一套可复用流程" in text
    assert "就是在决定今后照此执行" in text
    # the branch terminates the comparison instead of re-deriving it (§3.1 of the memo:
    # without this clause the first draft went 4/4 finish=length on flash; with it, 1/9).
    assert "这一条成立就直接照下面产出，不用再和③比较" in text
    # …and it names the precedence over the inherited conservative Procedure rule.
    assert "本分支不适用上面“没有可直接绑定的实际步骤就不提 Procedure”那一条" in text
    assert "这些字全部来自本句，不是补造" in text
    assert "target_operation_id=前面那条 procedure，排在它后面" in text
    # the incident-L guarantee is untouched.
    assert "任何分支下 object_value 都不能是指代短语本身" in text
    # v3..v8 never said what a reason_code looks like; the SDK bounds it at 256 UTF-8 bytes
    # and DeepSeek writes whole sentences (the measured cause of attempt 9's turn-15 loss).
    assert "reason_code 只写简短的英文小写标识符" in text
    assert "超过 256 字节 Host 会整条拒收" in text
    # tool description tells the same four-branch story.
    assert "names a reusable workflow and adopts it" in v9.PROPOSAL_TOOL_DESCRIPTION


# ------------------------------------------------------------------ the turn-15 shape
def test_turn15_shape_compiles_to_a_new_flow_node_plus_an_edge_from_the_existing_fact():
    case = compilation(TURN15)
    compiled = _compile(_bind(case, [_procedure(),
                                     _relation(source_key=PYTHON_CANDIDATE["candidate_key"],
                                               target_op="op-flow")]),
                        case=case, candidates=[PYTHON_CANDIDATE])
    assert compiled.outcome == "mutate" and compiled.rejected == ()
    flow, relation = compiled.plan.operations
    # The workflow really is a node of THIS plan, and ACTIVE — the SDK only accepts an
    # active/reinforced procedure as a relation endpoint.
    assert flow.memory_type.value == "procedure" and flow.lifecycle_state.value == "active"
    assert flow.payload.name == FLOW_NAME and flow.payload.steps == (STEP,)
    assert relation.payload.semantic_kind.value == "relation"
    assert relation.payload.relation_kind.value == "applies_to"
    # source = the fact the store already holds, at its exact revision; no duplicate slot.
    assert relation.payload.source_endpoint.memory_id == PYTHON_CANDIDATE["memory_id"]
    assert relation.payload.source_endpoint.revision == PYTHON_CANDIDATE["revision"]
    assert relation.payload.target_endpoint.operation_id == "op-flow"
    # Only the created endpoint is a dependency; an existing endpoint is not an operation.
    assert relation.depends_on_operation_ids == ("op-flow",)
    # …and it precedes the relation, as memory_protocol's INVALID_DEPENDENCY_ORDER requires.
    assert [op.operation_id for op in compiled.plan.operations] == ["op-flow", "op-rel"]
    # No second copy of "Python 3.12" was minted for this turn.
    assert [op.memory_type.value for op in compiled.plan.operations] == ["procedure", "semantic"]


def test_a_draft_flow_node_still_compiles_but_is_marked_draft():
    """v4's classification is untouched: only a real adoption becomes ACTIVE.

    The SDK rejects a DRAFT endpoint (`relation_endpoint_state_ineligible`), which is why
    branch ② is written with 「用户本句就是在决定今后照此执行」 as its precondition rather than
    telling the model to label every named workflow as an adoption.
    """
    case = compilation(TURN15)
    compiled = _compile(_bind(case, [_procedure(intent="uncertain")]), case=case)
    assert compiled.rejected == ()
    assert compiled.plan.operations[0].lifecycle_state.value == "draft"


@pytest.mark.parametrize("steps,code", [
    (["用 Python 3.12 跑脚本"], v3.QUOTE_NOT_FOUND),          # not in this sentence → fabrication
    ([], "analysis_procedure_binding_invalid"),               # no steps at all
])
def test_branch_two_grants_no_exemption_from_the_verbatim_step_rule(steps, code):
    case = compilation(TURN15)
    compiled = _compile(_bind(case, [_procedure(steps=steps)]), case=case)
    assert compiled.outcome == "no_mutation"
    assert [r.code for r in compiled.rejected] == [code]


def test_a_rejected_flow_node_takes_only_its_relation_down_not_the_whole_turn():
    """``host-analysis-validator/v5``: the one admission rule v9 adds.

    Measured on the replay: 1 of 8 flash samples wrote an over-long ``reason_code`` on the
    procedure, so the endpoint was rejected while the relation survived — and v6/v7/v8 then
    build a plan whose relation depends on an operation that is not in it.  The SDK refuses
    that plan wholesale (``operation has unknown dependencies``), which loses every memory of
    the turn.  v9 refuses the relation by name instead.
    """
    case = compilation(TURN15)
    operations = [_procedure(steps=["用 Python 3.12 跑脚本"]),
                  _relation(source_key=PYTHON_CANDIDATE["candidate_key"], target_op="op-flow"),
                  {"operation_id": "op-episode", "memory_type": "episode",
                   "evidence_item_id": None, "exact_quote": TURN15,
                   "reason_code": "explicit_user_statement",
                   "episode": {"title": "记住校对流程的执行环境", "actions": [], "results": []}}]
    compiled = _compile(_bind(case, operations), case=case, candidates=[PYTHON_CANDIDATE])
    assert [(r.operation_id, r.code) for r in compiled.rejected] == [
        ("op-flow", v3.QUOTE_NOT_FOUND), ("op-rel", v9.RELATION_ENDPOINT_UNKNOWN)]
    # …and the rest of the turn is still written.
    assert [op.operation_id for op in compiled.plan.operations] == ["op-episode"]

    # The same proposal under v8 is what the guard exists for: a plan-level admission error
    # escaping the compiler.  v8 is deliberately left as it was, so persisted replays are stable.
    with pytest.raises(Exception) as exc:
        v8.compile_proposal({"outcome": "mutate", "operations": operations},
                            request=_request(case, v8), items=case.items,
                            base_revision=1, plan_id="host-v9-unit", now=1.0,
                            candidates=[PYTHON_CANDIDATE])
    assert "unknown dependencies" in str(exc.value)


def test_the_relation_alone_is_rejected_when_its_created_endpoint_is_missing():
    """A relation without its procedure loses only the relation, never the turn."""
    case = compilation(TURN15)
    compiled = _compile(_bind(case, [_procedure(),
                                     _relation(source_key=PYTHON_CANDIDATE["candidate_key"],
                                               target_op="op-absent")]),
                        case=case, candidates=[PYTHON_CANDIDATE])
    assert [op.operation_id for op in compiled.plan.operations] == ["op-flow"]
    assert [(r.operation_id, r.code) for r in compiled.rejected] == [
        ("op-rel", v9.RELATION_ENDPOINT_UNKNOWN)]


@pytest.mark.parametrize("kwargs,code", [
    ({"source_key": "semantic-candidate-unknown", "target_op": "op-flow"},
     v9.RELATION_ENDPOINT_UNKNOWN),
    ({"source_op": "op-flow", "target_op": "op-flow"}, v9.RELATION_SELF_LOOP),
    ({"source_key": PYTHON_CANDIDATE["candidate_key"], "source_op": "op-other",
      "target_op": "op-flow"}, v9.RELATION_ENDPOINT_AMBIGUOUS),
    ({"source_key": PYTHON_CANDIDATE["candidate_key"]}, v9.RELATION_ENDPOINT_AMBIGUOUS),
    ({"source_key": PYTHON_CANDIDATE["candidate_key"],
      "target_key": PYTHON_CANDIDATE["candidate_key"]}, v9.RELATION_ENDPOINT_TYPE_INVALID),
])
def test_v8_endpoint_rules_are_unchanged_under_v9(kwargs, code):
    case = compilation(TURN15)
    compiled = _compile(_bind(case, [_procedure(), _relation(**kwargs)]),
                        case=case, candidates=[PYTHON_CANDIDATE],
                        relation_candidates=[PYTHON_CANDIDATE])
    assert [(r.operation_id, r.code) for r in compiled.rejected] == [("op-rel", code)]


def test_the_incident_l_sentence_is_still_refused_as_a_literal_under_v9():
    case = compilation(TURN15)
    literal = {"operation_id": "op-1", "memory_type": "semantic", "action": "create",
               "candidate_key": "", "evidence_item_id": None, "exact_quote": QUOTE,
               "reason_code": "explicit_user_statement",
               "semantic": {"subject_entity": FLOW_NAME, "predicate": "execution_environment",
                            "object_value": "前面说的 Python 环境"}}
    compiled = _compile(_bind(case, [literal]), case=case, candidates=[PYTHON_CANDIDATE])
    assert compiled.outcome == "no_mutation"
    assert [r.code for r in compiled.rejected] == [v9.ANAPHORIC_OBJECT_VALUE]


def test_branch_three_reference_resolution_still_works_under_v9():
    case = compilation(TURN15)
    resolved = {"operation_id": "op-1", "memory_type": "semantic", "action": "create",
                "candidate_key": "", "evidence_item_id": None, "exact_quote": QUOTE,
                "reason_code": "explicit_user_statement",
                v9.OBJECT_VALUE_CANDIDATE_KEY: PYTHON_CANDIDATE["candidate_key"],
                "semantic": {"subject_entity": FLOW_NAME, "predicate": "execution_environment",
                             "object_value": PYTHON_CANDIDATE["payload"]["object_value"]}}
    compiled = _compile(_bind(case, [resolved]), case=case, candidates=[PYTHON_CANDIDATE])
    assert compiled.rejected == ()
    assert compiled.plan.operations[0].payload.object_value == "Python 3.12"


def test_v9_and_v8_compile_the_same_proposal_identically():
    """The only difference between the protocols is policy text, so prove it."""
    case = compilation(TURN15)
    operations = [_procedure(), _relation(source_key=PYTHON_CANDIDATE["candidate_key"],
                                          target_op="op-flow")]
    proposal = _bind(case, operations)
    nine = _compile(proposal, case=case, candidates=[PYTHON_CANDIDATE])
    eight = v8.compile_proposal(proposal, request=_request(case, v8), items=case.items,
        base_revision=1, plan_id="host-v9-unit", now=1.0, candidates=[PYTHON_CANDIDATE])
    assert nine.structured_result == eight.structured_result
    assert nine.rejected == eight.rejected == ()


# ------------------------------------------------------------------ authored ordering
def test_a_relation_listed_before_its_created_endpoint_is_refused_not_fatal():
    """`memory_protocol` 要求端点在 `operations` 里严格排在关系之前, v9 提早一步挡下来。

    提示词讲了顺序（「target_operation_id=前面那条 procedure，排在它后面」），但模型不保证照做。
    倒序时 v9 的 `survived` 集合里还没有 `op-flow`，关系按 `analysis_relation_endpoint_unknown`
    单独被拒，procedure 照常落库；v8 会把两条都放进 plan，然后由 SDK 的准入检查整轮抛掉
    （`created semantic relation endpoint must precede the relation`）——正是本轮要消灭的整轮丢失。
    """
    case = compilation(TURN15)
    operations = [_relation(source_key=PYTHON_CANDIDATE["candidate_key"], target_op="op-flow"),
                  _procedure()]
    compiled = _compile(_bind(case, operations), case=case, candidates=[PYTHON_CANDIDATE])
    assert [(r.operation_id, r.code) for r in compiled.rejected] == [
        ("op-rel", v9.RELATION_ENDPOINT_UNKNOWN)]
    assert [op.operation_id for op in compiled.plan.operations] == ["op-flow"]
    assert compiled.outcome == "mutate"

    with pytest.raises(Exception) as exc:
        v8.compile_proposal({"outcome": "mutate", "operations": operations},
                            request=_request(case, v8), items=case.items,
                            base_revision=1, plan_id="host-v9-unit", now=1.0,
                            candidates=[PYTHON_CANDIDATE])
    assert "must precede the relation" in str(exc.value)


# ------------------------------------------------------------------ the wire, byte for byte
# `PROPOSAL_TOOL_SCHEMA` 是 v8 与 v9 共用的**同一个可变 dict**；就地改它会同时改掉两个协议的
# 线格式，而提示体 + 工具 schema 会进 `bind_attempt` 的哈希 —— 已持久化的 v8 请求就会重放报
# `analysis_attempt_input_conflict`。下面两项是那条约束的绊线。
def _wire_sha256(protocol) -> str:
    """一个协议在 provider 上真正发出去的三段文本：system 指令 + 工具 description + schema。"""
    spec = protocol.proposal_tool_spec()
    body = "\x00".join([
        protocol.ANALYSIS_SYSTEM_INSTRUCTION,
        spec.description,
        json.dumps(protocol.PROPOSAL_TOOL_SCHEMA, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")),
    ])
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# 在基线 `eb9f4449` 与本工作树上逐条相同（v3..v7 的模块本轮一字未改，v8 的改动只在编译器内部，
# 不触及这三段文本）。任何一条变了，都意味着某个**已持久化**协议的重放会失败——那不是重跑测试
# 就能修的，必须新开一个协议版本。v9 这一行会随策略文本变化，改它之前先重跑
# `DECISION-T-RELATION-FORM.md` §3 的真实模型复算。
WIRE_GOLDENS = {
    "host-analysis-prompt/v3": "7125f98afa85597cb8c1405ca226c4d2fbe2472b7192683c894f6bad37301273",
    "host-analysis-prompt/v4": "9f8d29edaacefbaba6d2215850ff0d6aceb3f34008d5ca78938f8170011e4c4d",
    "host-analysis-prompt/v5": "458befedfff1dc7bb953bf039ea864b099cc160ace1c87ef4227cc9df852c1d1",
    "host-analysis-prompt/v5.1": "d90483a76a4b848ef97145ad2d046ded526283770301d49082d3a9e55082eb51",
    "host-analysis-prompt/v6": "97198b7126652d6f9329d7a431d69b03e71e2f13d7b11ba71e772579213feb42",
    "host-analysis-prompt/v7": "8f5537ebeb9a94989c07147a2c2c575d9e908589d883910c99b40507bfb6c2db",
    "host-analysis-prompt/v8": "80d33973e7e40de1ffe765a696977558c73cf93bcc413a98aaead82daafcf556",
}


@pytest.mark.parametrize("protocol", [v3, v4, v5, v5_1, v6, v7, v8])
def test_every_persisted_protocols_wire_text_is_unchanged(protocol):
    assert _wire_sha256(protocol) == WIRE_GOLDENS[protocol.PROMPT_VERSION]


def test_v9_is_a_new_wire_and_the_shared_schema_object_is_not_mutated():
    # v9 换了策略文本，所以线一定与 v8 不同；但 schema 必须还是 v8 那个对象、内容一字不差。
    assert _wire_sha256(v9) not in WIRE_GOLDENS.values()
    assert v9.PROPOSAL_TOOL_SCHEMA is v8.PROPOSAL_TOOL_SCHEMA
    # 导入 v9 之后 v8 的 schema 仍然渲染出 v8 的金值（即没人就地改过这个共享 dict）。
    assert _wire_sha256(v8) == WIRE_GOLDENS[v8.PROMPT_VERSION]
    # 每个协议的 spec 都必须交出同一个 schema 对象的内容，而不是各自的副本。
    assert v9.proposal_tool_spec().parameters == v8.proposal_tool_spec().parameters
