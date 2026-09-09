# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""v10（事件 AE）：模糊的将来愿望不能变成被调度的 Prospective。

HM-TO-A6 第 11 次 T14 是负控 NC-3：「以后有机会我想学画画。」不该产生任何 pending
Prospective。实测（`.local-test-evidence/2026-09-09/native-a6-run11/.../human_memory_v7.db`）
v9 的分析车道提了两条 operation——一条正确的 `user:self · interest_learn · "画画"`，
一条 `prospective`，`trigger_at=1788920760.0`、`timezone=Asia/Shanghai`，引文却是整句
「以后有机会我想学画画。」，里面没有任何时间表达。Host 全程放行，注册两条，并在旅程内真的
触发（`prospective_trigger_events` 一条 `time_due/matched`）。

根因：v3 起 prospective 的 `trigger_at_iso` 是分析车道里**唯一一个模型可以凭空写、Host
完全不核对**的字段——逐字引文规则约束 `exact_quote`，编译器对触发时间只做
`datetime.fromisoformat` + 要求带时区偏移。

本文件钉四件事：
1. 协议身份：v10 是 current，v3..v9 一条不丢，且 v9 的线一字未变（否则持久化请求重放会
   报 `analysis_attempt_input_conflict`）；
2. T14 复现：同一份提案在 v9 下被接受（红），在 v10 下这条 prospective 单独被
   `analysis_prospective_vague_wish` 拒收（绿），而同一批的 semantic 照常落库；
3. 正控：明确时间表达（「明天九点半」「下周三」「9月7日09:00」「两小时后」）照常接受；
4. 接地本身：引文里有时间表达但对不上 `trigger_at` → `analysis_prospective_trigger_not_grounded`。
"""
import hashlib
import json
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from simple_harness.runtime import AnalysisBudget, EvidenceRef, MemoryAnalysisRequest

from deskpet.memory import analysis_proposal as v3
from deskpet.memory import analysis_proposal_v4 as v4
from deskpet.memory import analysis_proposal_v5 as v5
from deskpet.memory import analysis_proposal_v5_1 as v5_1
from deskpet.memory import analysis_proposal_v6 as v6
from deskpet.memory import analysis_proposal_v7 as v7
from deskpet.memory import analysis_proposal_v8 as v8
from deskpet.memory import analysis_proposal_v9 as v9
from deskpet.memory import analysis_proposal_v10 as v10
from deskpet.memory import analysis_protocol
from deskpet.memory import prospective_trigger_grounding as grounding
from deskpet.memory.human_memory_service import build_foreground_turn_evidence
from tests.memory.test_analysis_proposal_v9 import WIRE_GOLDENS, _wire_sha256
from tests.sdk_adapters import s5b_memory_harness as mh

# v9 的线在基线 `7d161678` 与本工作树上相同（v10 只新增模块，未改 v9 的三段文本）。
V9_WIRE_GOLDEN = "8895dc9bc0ada12c5cf1a178680d9bb4e4efd96afd857aeb33bb70d01e23c2bb"


ZONE = "Asia/Shanghai"
SHANGHAI = ZoneInfo(ZONE)

# T14 原文与实测触发值，逐字取自 attempt 11 的 accepted plan。
T14 = "以后有机会我想学画画。"
T14_TRIGGER_AT = 1788920760.0
# 该 batch 的证据采纳时刻（`analysis-batch-045e61fd…` 的 accepted plan 落库于
# 1788920762.9，触发值比它还早 ~3 秒——一个「已经过去」的提醒）。
T14_REFERENCE = 1788920745.0


def _iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, SHANGHAI).isoformat()


def _case(text, *, reference=T14_REFERENCE):
    """一条 USER 证据项 + 一个 v10 请求；`occurred_at` 固定，测试不读系统时钟。"""
    envelope, receipt = build_foreground_turn_evidence(
        subject="actor-1", authority_ref=mh.AUTHORITY_REF, delivery_key="ae-source", text=text)
    item = v3.admitted_item(envelope, receipt, occurred_at=reference)
    request = MemoryAnalysisRequest(
        job_id="ae-analysis", run_id=envelope.run_id, subject=envelope.subject,
        ordered_evidence_refs=(EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),),
        prompt_version=v10.PROMPT_VERSION, result_schema_version=v10.RESULT_SCHEMA_VERSION,
        policy_version=v10.POLICY_VERSION, provider_id="provider-1", model_id="model-1",
        model_config_hash="a" * 64, attempt=1, budget=AnalysisBudget(2048, 1024, 5000, 1000),
        disclosure_context=envelope.disclosure_context, idempotency_key="ae-analysis")
    return SimpleNamespace(items=[item], request=request, text=text)


def _request(case, protocol):
    return replace(case.request, prompt_version=protocol.PROMPT_VERSION,
                   result_schema_version=protocol.RESULT_SCHEMA_VERSION,
                   policy_version=protocol.POLICY_VERSION)


def _compile(case, operations, *, protocol=v10, now=T14_REFERENCE + 18.0):
    for op in operations:
        op["evidence_item_id"] = case.items[0].item_id
    return protocol.compile_proposal({"outcome": "mutate", "operations": operations},
        request=_request(case, protocol), items=case.items, base_revision=1,
        plan_id="host-v10-unit", now=now)


def _prospective(case, *, trigger_at, action="有机会时开始学画画", quote=None,
                 op_id="op_prosp_learning_draw", timezone=ZONE):
    return {"operation_id": op_id, "memory_type": "prospective", "action": "create",
            "candidate_key": "", "evidence_item_id": None,
            "exact_quote": case.text if quote is None else quote,
            "reason_code": "future_intention",
            "prospective": {"action": action, "trigger_at_iso": _iso(trigger_at),
                            "timezone": timezone}}


def _interest(case, *, value="画画", op_id="op_sem_learning_draw"):
    return {"operation_id": op_id, "memory_type": "semantic", "action": "create",
            "candidate_key": "", "evidence_item_id": None, "exact_quote": case.text,
            "reason_code": "explicit_user_statement",
            "semantic": {"subject_entity": "user:self", "predicate": "interest_learn",
                         "object_value": value}}


# ------------------------------------------------------------------ 协议身份
def test_v10_is_current_and_every_persisted_protocol_still_resolves():
    case = _case(T14)
    assert analysis_protocol.PROMPT_VERSION == v10.PROMPT_VERSION == "host-analysis-prompt/v10"
    assert analysis_protocol.RESULT_SCHEMA_VERSION == v10.RESULT_SCHEMA_VERSION
    assert analysis_protocol.POLICY_VERSION == v10.POLICY_VERSION
    # v10 加了一条 Host 准入规则，validator id 随之前进。
    assert analysis_protocol.VALIDATOR_VERSION == v10.VALIDATOR_VERSION == "host-analysis-validator/v6"
    assert v10.VALIDATOR_VERSION != v9.VALIDATOR_VERSION
    for protocol in (v3, v4, v5, v5_1, v6, v7, v8, v9, v10):
        assert analysis_protocol.protocol_for_request(_request(case, protocol)) is protocol


def test_a_persisted_v9_request_still_renders_v9s_own_prompt():
    """`bind_attempt` 把提示体哈希进 attempt——v9 的线不能因为 v10 出现而漂移。"""
    case = _case(T14)
    assert v9.PROMPT_VERSION in v9.ANALYSIS_SYSTEM_INSTRUCTION
    assert v10.PROMPT_VERSION not in v9.ANALYSIS_SYSTEM_INSTRUCTION
    assert "未来意图先判定再产出" not in v9.ANALYSIS_SYSTEM_INSTRUCTION
    with pytest.raises(v3.AnalysisProposalRejected) as exc:
        v10.compile_proposal({"outcome": "no_mutation", "operations": []},
                             request=_request(case, v9), items=case.items,
                             base_revision=1, plan_id="v10-guard", now=1.0)
    assert exc.value.code == "analysis_protocol_unsupported"


def test_v10_reuses_v9s_wire_object_and_leaves_every_golden_intact():
    assert v10.PROPOSAL_TOOL_SCHEMA is v9.PROPOSAL_TOOL_SCHEMA is v8.PROPOSAL_TOOL_SCHEMA
    # `ProviderToolSpec` 每次现包一个 mappingproxy，所以这里比内容——身份由上一行钉住。
    assert v10.proposal_tool_spec().parameters == v9.proposal_tool_spec().parameters
    for protocol in (v3, v4, v5, v5_1, v6, v7, v8):
        assert _wire_sha256(protocol) == WIRE_GOLDENS[protocol.PROMPT_VERSION]
    # v9 现在也是持久化协议了：它的线在本工作树上被钉死，任何改动都会让已存请求重放失败。
    assert _wire_sha256(v9) == V9_WIRE_GOLDEN
    assert _wire_sha256(v10) not in set(WIRE_GOLDENS.values()) | {V9_WIRE_GOLDEN}


# ------------------------------------------------------------------ 策略文本
def test_v10_states_the_rule_positively_and_names_both_codes():
    text = v10.ANALYSIS_SYSTEM_INSTRUCTION
    assert v10.PROMPT_VERSION in text and v9.PROMPT_VERSION not in text
    # v9 的每一条继承规则都还在。
    assert "按顺序只走第一条成立的分支，判断一次即可" in text
    assert "reason_code 只写简短的英文小写标识符" in text
    # ①：有时间/条件才提 prospective，且时间必须来自本句。
    assert "未来意图先判定再产出" in text
    assert "trigger_at_iso 必须就是本句引文里那个时间解析出来的结果" in text
    assert "Host 会把引文里的时间表达重新解析并逐一比对" in text
    # ②：正面给出替代形状，而不是只写禁令——这是事件 AE 的教训。
    assert "不要提 prospective，改记一条 semantic 表达这个兴趣/目标" in text
    assert "predicate 用 interest_… 或 goal_… 这类槽位" in text
    assert "没有时间表达就没有提醒" in text
    # 两个拒收码都在提示里出现，模型才知道代价是整条丢失。
    assert grounding.TRIGGER_NOT_GROUNDED in text and grounding.VAGUE_WISH in text
    assert "vague future wish" in v10.PROPOSAL_TOOL_DESCRIPTION
    assert "Never invent a due time." in v10.PROPOSAL_TOOL_DESCRIPTION


# ------------------------------------------------------------------ T14 复现
def test_t14_vague_wish_is_accepted_by_v9_and_refused_by_v10():
    """负控 NC-3 的红/绿对照，用 attempt 11 实测的那一组数值。"""
    case = _case(T14)
    operations = [_prospective(case, trigger_at=T14_TRIGGER_AT), _interest(case)]

    # 红：v9 收下这条凭空的时间触发，正是 attempt 11 落库的形状。
    under_v9 = v9.compile_proposal(
        {"outcome": "mutate", "operations": [dict(op, evidence_item_id=case.items[0].item_id)
                                             for op in operations]},
        request=_request(case, v9), items=case.items, base_revision=1,
        plan_id="host-v10-unit", now=T14_REFERENCE + 18.0)
    assert under_v9.rejected == ()
    assert [op.memory_type.value for op in under_v9.plan.operations] == ["prospective", "semantic"]
    assert under_v9.plan.operations[0].payload.trigger.trigger_at == T14_TRIGGER_AT

    # 绿：v10 只拒这一条，semantic 照常落库——这就是 NC-3 期望的结局。
    compiled = _compile(case, operations)
    assert [(r.operation_id, r.code) for r in compiled.rejected] == [
        ("op_prosp_learning_draw", grounding.VAGUE_WISH)]
    assert compiled.outcome == "mutate"
    assert [op.operation_id for op in compiled.plan.operations] == ["op_sem_learning_draw"]
    assert compiled.plan.operations[0].payload.predicate == "interest_learn"
    assert compiled.plan.operations[0].payload.object_value == "画画"


def test_the_fabricated_trigger_is_exactly_the_requests_own_now_iso():
    """T14 的触发值不是随手估的：它逐字等于请求体递给模型的 `now_iso`。

    `analysis-attempt-input-0bf36e50…`（run 11 的 state.db）第二条消息写着
    `"now_iso": "2026-09-09T10:26:00+08:00"`，其 epoch 正是 1788920760.0。schema 把
    `trigger_at_iso` 列为 required，句子里又没有任何时间，模型就把 Host 递给它的「现在」
    原样填了回来——所以这条提醒注册后 3 秒就到期。提示词因此点名禁止这一步。
    """
    assert datetime.fromisoformat("2026-09-09T10:26:00+08:00").timestamp() == T14_TRIGGER_AT
    assert "不要把上面给你的 now_iso " in v10.ANALYSIS_SYSTEM_INSTRUCTION
    assert "抄成 trigger_at_iso" in v10.ANALYSIS_SYSTEM_INSTRUCTION
    # 「现在」永远不可能被引文里的时间表达接地——除非用户自己说了那个时刻。
    case = _case(T14)
    assert [r.code for r in _compile(case, [_prospective(case, trigger_at=T14_TRIGGER_AT)]).rejected] \
        == [grounding.VAGUE_WISH]


def test_the_refusal_names_the_markers_it_saw():
    case = _case(T14)
    compiled = _compile(case, [_prospective(case, trigger_at=T14_TRIGGER_AT)])
    assert compiled.outcome == "no_mutation"
    rejection = compiled.rejected[0]
    assert rejection.code == grounding.VAGUE_WISH
    assert set(rejection.detail["markers"]) == {"有机会", "以后"}


@pytest.mark.parametrize("text", [
    "以后有机会我想学画画。", "将来有空了想学吉他。", "哪天有时间我想去趟敦煌。",
    "总有一天我要把这些照片整理完。", "改天再说吧，我想学做面包。",
])
def test_every_vague_wish_shape_is_refused_the_same_way(text):
    case = _case(text)
    compiled = _compile(case, [_prospective(case, trigger_at=T14_REFERENCE + 900.0,
                                            action="做这件事")])
    assert [r.code for r in compiled.rejected] == [grounding.VAGUE_WISH]


# ------------------------------------------------------------------ 正控
@pytest.mark.parametrize("text,local", [
    ("明天九点半提醒我把修正版交出去", "2026-09-10T09:30"),
    ("下周三提醒我复核封面", "2026-09-16T10:00"),          # 只有日期 → 整个本地日都算接地
    ("9月7日09:00提醒我索取修正版", "2026-09-07T09:00"),   # C04-01 的时间措辞
    ("2027年1月2日10:00提醒补拍年册素材", "2027-01-02T10:00"),  # C04-03
    ("9月8日00:30提醒我补附件", "2026-09-08T00:30"),        # C04-20，跨零点
    ("两小时后提醒我看一下打印结果", None),                  # 相对表达锚在证据采纳时刻
    ("下午三点提醒我去取校色样", "2026-09-09T15:00"),
])
def test_an_explicitly_timed_reminder_is_still_accepted(text, local):
    case = _case(text)
    at = (T14_REFERENCE + 7200.0 if local is None
          else datetime.fromisoformat(local).replace(tzinfo=SHANGHAI).timestamp())
    compiled = _compile(case, [_prospective(case, trigger_at=at, action="提醒")])
    assert compiled.rejected == (), compiled.rejected
    assert compiled.plan.operations[0].memory_type.value == "prospective"
    assert compiled.plan.operations[0].payload.trigger.trigger_at == at


def test_a_vague_marker_next_to_a_real_time_does_not_refuse_the_reminder():
    """「以后每周三上午十点提醒我」有具体时间——模糊标记规则不该吃掉它。"""
    case = _case("以后每周三上午十点提醒我核对清单")
    at = datetime.fromisoformat("2026-09-16T10:00").replace(tzinfo=SHANGHAI).timestamp()
    compiled = _compile(case, [_prospective(case, trigger_at=at, action="核对清单")])
    assert compiled.rejected == ()


# ------------------------------------------------------------------ 接地本身
def test_a_time_that_no_expression_in_the_quote_resolves_to_is_refused():
    case = _case("明天九点半提醒我把修正版交出去")
    at = datetime.fromisoformat("2026-09-12T16:00").replace(tzinfo=SHANGHAI).timestamp()
    compiled = _compile(case, [_prospective(case, trigger_at=at, action="交修正版")])
    rejection = compiled.rejected[0]
    assert rejection.code == grounding.TRIGGER_NOT_GROUNDED
    assert rejection.detail["trigger_at"] == at
    assert "明天九点半" in rejection.detail["expressions"]


def test_a_sentence_with_neither_a_time_nor_a_vague_marker_is_not_grounded():
    case = _case("把校对结果存到外接硬盘。")
    compiled = _compile(case, [_prospective(case, trigger_at=T14_REFERENCE + 3600.0,
                                            action="存结果")])
    assert [r.code for r in compiled.rejected] == [grounding.TRIGGER_NOT_GROUNDED]


def test_the_tolerance_is_bounded_and_symmetric():
    case = _case("明天九点半提醒我把修正版交出去")
    base = datetime.fromisoformat("2026-09-10T09:30").replace(tzinfo=SHANGHAI).timestamp()
    inside = grounding.CLOCK_TOLERANCE_SECONDS - 1.0
    for delta in (-inside, 0.0, inside):
        assert _compile(case, [_prospective(case, trigger_at=base + delta)]).rejected == ()
    # 超出容差但仍落在「明天」这一整天里 → 仍接地（只有日期精度的表达覆盖整日）。
    assert _compile(case, [_prospective(case, trigger_at=base + 7200.0)]).rejected == ()
    # 落到别的日子 → 拒。
    assert [r.code for r in _compile(
        case, [_prospective(case, trigger_at=base + 86400.0)]).rejected] == [
        grounding.TRIGGER_NOT_GROUNDED]


def test_the_quote_not_the_whole_message_is_what_grounds_the_trigger():
    """引 span 是 Host 已逐字节核对过的那一段；接地只看它。"""
    case = _case("明天九点半提醒我交表；另外以后有机会我想学画画。")
    at = datetime.fromisoformat("2026-09-10T09:30").replace(tzinfo=SHANGHAI).timestamp()
    assert _compile(case, [_prospective(case, trigger_at=at, quote="明天九点半提醒我交表")]).rejected == ()
    assert [r.code for r in _compile(case, [_prospective(
        case, trigger_at=at, quote="以后有机会我想学画画")]).rejected] == [grounding.VAGUE_WISH]


def test_a_relative_expression_is_anchored_on_the_evidence_not_the_analysis_clock():
    """分析是 post-turn job，可能晚很多才跑；「两小时后」必须锚在用户说话的时刻。"""
    case = _case("两小时后提醒我看一下打印结果")
    at = T14_REFERENCE + 7200.0
    # 分析时钟晚了一整个小时，接地结果不变。
    assert _compile(case, [_prospective(case, trigger_at=at)], now=T14_REFERENCE + 3600.0).rejected == ()


def test_an_unknown_timezone_fails_closed():
    case = _case("明天九点半提醒我把修正版交出去")
    at = datetime.fromisoformat("2026-09-10T09:30").replace(tzinfo=SHANGHAI).timestamp()
    operation = _prospective(case, trigger_at=at)
    operation["prospective"]["timezone"] = "Mars/Olympus"
    assert [r.code for r in _compile(case, [operation]).rejected] == [grounding.TRIGGER_NOT_GROUNDED]


def test_an_offsetless_trigger_still_gets_the_legacy_diagnosis():
    """v10 不抢 v3 已有的诊断：没有时区偏移仍是 `trigger_offset_missing`。"""
    case = _case("明天九点半提醒我把修正版交出去")
    operation = _prospective(case, trigger_at=T14_REFERENCE)
    operation["prospective"]["trigger_at_iso"] = "2026-09-10T09:30:00"
    compiled = _compile(case, [operation])
    assert [(r.code, r.detail.get("reason")) for r in compiled.rejected] == [
        (v3.PAYLOAD_INVALID, "trigger_offset_missing")]


# ------------------------------------------------------------------ 解析器单元
def test_the_parser_is_deterministic_and_reads_no_clock():
    text = "明天九点半提醒我"
    first = grounding.time_expressions(text, reference=T14_REFERENCE, timezone=ZONE)
    second = grounding.time_expressions(text, reference=T14_REFERENCE, timezone=ZONE)
    assert first == second and first


@pytest.mark.parametrize("token,value", [
    ("9", 9), ("23", 23), ("九", 9), ("十", 10), ("十一", 11), ("二十三", 23), ("两", 2),
])
def test_chinese_numerals_resolve(token, value):
    assert grounding._cn_number(token) == value


def test_a_bare_prospective_op_shape_is_unchanged_from_v9():
    """v10 是策略版本：schema 与编译产物一字未变，只是多了一道准入。"""
    case = _case("明天九点半提醒我把修正版交出去")
    at = datetime.fromisoformat("2026-09-10T09:30").replace(tzinfo=SHANGHAI).timestamp()
    operations = [_prospective(case, trigger_at=at)]
    ten = _compile(case, operations)
    nine = v9.compile_proposal(
        {"outcome": "mutate", "operations": [dict(op) for op in operations]},
        request=_request(case, v9), items=case.items, base_revision=1,
        plan_id="host-v10-unit", now=T14_REFERENCE + 18.0)
    assert json.loads(json.dumps(ten.structured_result)) == json.loads(json.dumps(nine.structured_result))


def test_v9_and_v10_hash_to_different_wires():
    assert hashlib.sha256(v9.ANALYSIS_SYSTEM_INSTRUCTION.encode()).hexdigest() != \
        hashlib.sha256(v10.ANALYSIS_SYSTEM_INSTRUCTION.encode()).hexdigest()
