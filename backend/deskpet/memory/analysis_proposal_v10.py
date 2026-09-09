# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""host-analysis-prompt/v10 —— 模糊的将来愿望是 Semantic，不是被调度的 Prospective（事件 AE）。

HM-TO-A6 第 11 次 T14 是负控 NC-3：「以后有机会我想学画画。」**不该** 产生任何 pending
Prospective。v9 的分析车道给出了两条 operation：

* ``op_sem_learning_draw`` —— ``user:self · interest_learn · "画画"``，**这条是对的**；
* ``op_prosp_learning_draw`` —— ``prospective``，action「有机会时开始学画画」，
  ``trigger={"trigger_kind":"time","trigger_at":1788920760.0,"timezone":"Asia/Shanghai"}``。

**这个数值不是随手估的：它逐字等于请求体里的 ``now_iso``。** 该 batch 的
attempt-input（``analysis-attempt-input-0bf36e50…``，state.db）第二条消息写着
``"now_iso": "2026-09-09T10:26:00+08:00"``，其 epoch 正是 ``1788920760.0``。schema 把
``trigger_at_iso`` 列为 required，句子里又没有任何时间——模型于是把 Host 递给它的「现在」
原样填了回来。所以触发在注册后 ~3 秒就到期并触发（``occurred_at=1788920763.3``），
它从一开始就是一个「已经到期」的提醒。

Host 收下了第二条，注册成两条 ``prospective_scheduler_registrations``，并在旅程内真的
触发（``prospective_trigger_events`` 一条 ``time_due/matched``，revision 2
``lifecycle=triggered``）。第 8–10 次「通过」只是因为 s5c cursor-version 回归
（``48617f73`` 已修）让每一次注册都失败，所以这是第一次诚实的测量。

**根因不在模型，在契约。** v3 起 prospective 的 payload 是
``{action, trigger_at_iso, timezone}``，编译器（``analysis_proposal.compile_operation``
的 else 分支）对它只做一件事：``datetime.fromisoformat`` 并要求带时区偏移。逐字引文规则
（``derive_span``）约束的是 ``exact_quote``，从不约束 ``trigger_at_iso``。于是整条分析
车道里，「首次到期时间」是 **唯一一个模型可以凭空写、Host 完全不核对** 的字段：模型引了
一句完全没有时间的话，写了一个「几分钟后」的时间戳，每一道 Host 检查都放行。

而且 v3..v9 的 schema **只有** 时间触发一种（``required: [action, trigger_at_iso,
timezone]``），没有事件触发分支。所以一个「等有机会」的意图，模型要么放弃 prospective，
要么编一个时间——v9 的策略文本从没说过前者是正确出路。这是本次要补的第二半。

v10 因此做两件事：

1. **策略正面陈述**（提示文本，见 ``_PROSPECTIVE_INSTRUCTION``）：有明确时间表达/明确触发
   条件才提 prospective，且 ``trigger_at_iso`` 必须就是那句话里的时间；只是模糊的将来愿望
   （「以后有机会」「将来」「哪天」「有空」）就记成 semantic 的 interest/goal（必要时再加
   一条 episode），**不提 prospective**。
2. **硬校验**（``host-analysis-validator/v6``，``prospective_trigger_grounding``）：
   ``trigger_kind=time`` 的 create 必须能在自己引用的证据跨度里找到一个解析得到该
   ``trigger_at`` 的时间表达（有界容差），否则
   ``analysis_prospective_trigger_not_grounded``；引文只有模糊将来标记而没有任何时间表达
   时，``analysis_prospective_vague_wish``。两者都是 **单条 operation** 的拒收——同一批
   里的 semantic/episode 照常落库，这正是 NC-3 期望的结局（只留 interest_learn 那条）。

线格式、schema 与编译器仍是 v8 的同一个对象；v10 只是策略版本 + 一条新准入规则。它必须是
新的协议 id 而不是改 v9：提示体进 ``bind_attempt`` 的哈希，已持久化的 v9 请求必须继续渲染
v9 的原文，否则重放报 ``analysis_attempt_input_conflict``。v3..v9 全部保持可解析且一字未改
（``analysis_protocol.protocol_for_request``）。

关于 s5c 注册侧要不要再加一道「``trigger_at`` 距提案时刻不足 N 秒就拒绝注册」的纵深防御：
**不加**，理由见 ``plans/2026-09-08-hm-to-a6/DECISION-AE-VAGUE-WISH-PROSPECTIVE.md`` §4。
一句话：``prospective_registration_source`` 是传输授权车道，看不见证据，「五分钟后提醒我」
是完全合法的产品行为；在那里拒绝会让 Memory 侧留下一条永远 pending、调度器却不知道的记忆，
比响一次更糟。接地判定放在唯一看得见证据的地方——分析校验器。
"""
from __future__ import annotations

from deskpet.memory import analysis_proposal as legacy
from deskpet.memory import analysis_proposal_v8 as v8
from deskpet.memory import analysis_proposal_v9 as v9
from deskpet.memory import prospective_trigger_grounding as grounding

PROMPT_VERSION = "host-analysis-prompt/v10"
RESULT_SCHEMA_VERSION = "memory-analysis-proposal/v10"
POLICY_VERSION = "host-analysis-policy/v10"
# v10 keeps every v9 admission rule and adds exactly one: a ``time`` trigger must be grounded
# in a time expression of the operation's own cited evidence span.
VALIDATOR_VERSION = "host-analysis-validator/v6"

SUPPORTS_RELATION_CANDIDATES = True

RELATION_TYPE = v9.RELATION_TYPE
RELATION_KINDS = v9.RELATION_KINDS
RELATION_ENDPOINT_UNKNOWN = v9.RELATION_ENDPOINT_UNKNOWN
RELATION_SELF_LOOP = v9.RELATION_SELF_LOOP
RELATION_ENDPOINT_AMBIGUOUS = v9.RELATION_ENDPOINT_AMBIGUOUS
RELATION_ENDPOINT_TYPE_INVALID = v9.RELATION_ENDPOINT_TYPE_INVALID
CONTEST_ACTION = v9.CONTEST_ACTION
ANAPHORIC_OBJECT_VALUE = v9.ANAPHORIC_OBJECT_VALUE
REFERENCE_ACTION_INVALID = v9.REFERENCE_ACTION_INVALID
REFERENCE_CANDIDATE_UNKNOWN = v9.REFERENCE_CANDIDATE_UNKNOWN
REFERENCE_NOT_ANAPHORIC = v9.REFERENCE_NOT_ANAPHORIC
REFERENCE_VALUE_MISMATCH = v9.REFERENCE_VALUE_MISMATCH
OBJECT_VALUE_CANDIDATE_KEY = v9.OBJECT_VALUE_CANDIDATE_KEY

# The two codes event AE adds. Both are per-operation refusals.
PROSPECTIVE_TRIGGER_NOT_GROUNDED = grounding.TRIGGER_NOT_GROUNDED
PROSPECTIVE_VAGUE_WISH = grounding.VAGUE_WISH

# ---------------------------------------------------------------- wire
# The same shared *mutable* dict v8 and v9 already alias — v10 is a policy version and the
# prospective payload shape is unchanged, so copying it would be a silent protocol change.
# Never mutate it in place: the prompt body + tool schema are hashed into ``bind_attempt``,
# so an edit would break the replay of every persisted v8/v9 request.
# ``test_analysis_proposal_v10.py`` re-checks v8's and v9's wire goldens after importing v10.
PROPOSAL_TOOL_SCHEMA = v9.PROPOSAL_TOOL_SCHEMA

# 正面陈述，不是禁令。事件 AE 的教训是模型没有「不提 prospective」这条出路：schema 里
# prospective 只有时间触发一种形状（``required: [action, trigger_at_iso, timezone]``），
# 而 v3..v9 的策略文本把「未来意图/提醒」整类都指向 prospective，却从没说过一个没有时间的
# 愿望应该落到哪里。于是模型在「丢掉这句话」和「编一个时间」之间选了后者。
#
# 三个子句各自有用，删任何一条都会把这个出路重新关上：
#   * 「先判定再产出」——和 v8/v9 的有序分支同一个句法，模型已经在这个提示里被训练成先判定；
#   * 「trigger_at_iso 必须就是那句话里的时间……Host 会……逐一比对，对不上整条拒收」——
#     把硬校验的存在写进提示，模型才知道估一个时间不是「尽力而为」而是整条丢失；「不要把
#     now_iso 抄成 trigger_at_iso」是 T14 实测的那一步，必须点名；
#   * 「改记一条 semantic……predicate 用 interest_/goal_ 这类」——给出确切的替代形状。
#     T14 的模型自己已经同时提了 ``user:self · interest_learn · "画画"``，这句只是把那条
#     从「附带」提升为「唯一正确产出」。
# 任何改动都要重跑 DECISION-AE-VAGUE-WISH-PROSPECTIVE.md §5 的真实模型复算。
_PROSPECTIVE_INSTRUCTION = (
    "未来意图先判定再产出：①用户给了明确的时间表达（“明天九点半”“下周三”“9月8日10:00”"
    "“两小时后”）或明确的触发条件，才用 prospective；trigger_at_iso 必须就是本句引文里那个"
    "时间解析出来的结果，不能自己估、不能取“现在往后一点”，尤其不要把上面给你的 now_iso "
    "抄成 trigger_at_iso（那不是提醒，那是立刻就到期）——Host 会把引文里的时间表达重新"
    "解析并逐一比对，对不上就整条拒收（analysis_prospective_trigger_not_grounded）。"
    "②只是模糊的将来愿望（“以后有机会”“将来”“哪天”“有空”“找时间”）、句中没有具体时间也"
    "没有具体条件：不要提 prospective，改记一条 semantic 表达这个兴趣/目标"
    "（subject_entity=user:self，predicate 用 interest_… 或 goal_… 这类槽位，"
    "object_value 取本句里那件事的逐字原文），需要时再加一条 episode。"
    "没有时间表达就没有提醒：编一个时间等于凭空给用户排了一个真的会响的日程"
    "（analysis_prospective_vague_wish）。"
)

ANALYSIS_SYSTEM_INSTRUCTION = v9.ANALYSIS_SYSTEM_INSTRUCTION.replace(
    v9.PROMPT_VERSION, PROMPT_VERSION
) + _PROSPECTIVE_INSTRUCTION
PROPOSAL_TOOL_DESCRIPTION = v9.PROPOSAL_TOOL_DESCRIPTION + (
    " prospective is ONLY for a future intention the sentence itself times or conditions "
    "(\"tomorrow 9:30\", \"next Wednesday\", \"Sept 8 10:00\", \"in two hours\"); trigger_at_iso must "
    "be that stated time, resolved — the Host re-parses the quote's own time expressions and "
    "refuses the operation when none of them resolves to it. A vague future wish (\"someday\", "
    "\"when I get the chance\", \"eventually\") with no stated time or condition is NOT a "
    "prospective: record it as a semantic interest/goal (subject_entity=user:self, an "
    "interest_/goal_ predicate, object_value copied from the sentence), plus an episode if useful. "
    "Never invent a due time."
)


def proposal_tool_spec():
    from simple_harness.providers import ProviderToolSpec

    return ProviderToolSpec(legacy.PROPOSAL_TOOL_NAME, PROPOSAL_TOOL_DESCRIPTION, PROPOSAL_TOOL_SCHEMA)


# ---------------------------------------------------------------- validation
# Re-exported so a caller reaching for a v9 rule gets the *same function object*.
_validate_operation = v9._validate_operation
_check_object_value = v9._check_object_value
_endpoint = v9._endpoint
_compile_relation = v9._compile_relation


def _trigger_reference(item, now):
    """The instant a relative expression («两小时后») in this evidence is anchored to.

    The evidence's own admission time, not the analysis clock: analysis is a post-turn job
    that can run seconds or (after a crash/restart) minutes later, and anchoring on ``now``
    would slide the admissible window away from what the user actually said. ``now`` is the
    fallback only when the item carries no usable admission time.
    """
    occurred = getattr(item, "occurred_at", None)
    if isinstance(occurred, (int, float)) and not isinstance(occurred, bool) and occurred > 0:
        return float(occurred)
    return float(now)


def _ground_prospective(raw, span, *, item, now):
    """``host-analysis-validator/v6``: a ``time`` trigger must be grounded in the quote.

    The check reads the operation's own cited span, which ``derive_span`` has already proved
    byte-for-byte to be a substring of the admitted evidence text — so «is there a time
    expression here» is a fact the Host can recompute from durable rows alone, never a
    second model judgement.
    """
    from datetime import datetime

    body = raw.get("prospective")
    if not isinstance(body, dict):
        raise legacy.AnalysisProposalRejected(legacy.PAYLOAD_INVALID, reason="payload_missing")
    try:
        at = datetime.fromisoformat(str(body.get("trigger_at_iso") or ""))
    except (TypeError, ValueError):
        # The legacy compiler owns this diagnosis; let it produce its own code unchanged.
        return
    if at.tzinfo is None:
        return
    quote = getattr(span, "exact_quote", None)
    grounding.check_time_trigger(
        quote=quote if isinstance(quote, str) else "",
        trigger_at=at.timestamp(),
        timezone=str(body.get("timezone") or ""),
        reference=_trigger_reference(item, now),
    )


def compile_proposal(proposal, *, request, items, base_revision, plan_id, now,
                     candidates=(), relation_candidates=()):
    if (request.prompt_version, request.result_schema_version, request.policy_version) != (
        PROMPT_VERSION, RESULT_SCHEMA_VERSION, POLICY_VERSION
    ):
        raise legacy.AnalysisProposalRejected("analysis_protocol_unsupported")

    def prospective_grounding(raw, span, *, item):
        _ground_prospective(raw, span, item=item, now=now)

    return v8._compile_validated_proposal(
        proposal,
        request=request,
        items=items,
        base_revision=base_revision,
        plan_id=plan_id,
        now=now,
        candidates=candidates,
        relation_candidates=relation_candidates,
        created_endpoint_must_survive=True,
        prospective_grounding=prospective_grounding,
    )
