# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HM-AC-8 extra-type rate: the model-facing selection policy and its advisory.

The metric reads the model's own ``context_route`` arguments, so the only
Host-side lever is the text the provider sees before it calls. These tests pin
that the policy reaches the tool schema, that it names each requestable type
with the discriminator the 54 observed extra-type cases needed, and that the
deterministic advisory stays advisory - it never rejects, filters or rewrites a
selection, and it never encodes an expected per-case answer.
"""
import pytest

from deskpet.memory.recall_selection import (
    HOST_DEFAULT_MEMORY_TYPES,
    MEMORY_TYPE_SELECTION_POLICY,
    REQUESTABLE_MEMORY_TYPES,
    indicates_reminder_lifecycle_request,
    indicates_workflow_request,
    parse_memory_types,
    parse_recall_selection,
    selection_policy_departures,
)


def test_policy_reaches_the_provider_tool_schema_verbatim():
    from deskpet.sdk_adapters.context_route import CONTEXT_ROUTE_SCHEMA

    description = CONTEXT_ROUTE_SCHEMA["properties"]["memory_types"]["description"]
    assert MEMORY_TYPE_SELECTION_POLICY in description
    # The two invariants the schema kept before the policy was added.
    assert "include_short_horizon=true" in description
    assert "grants no permission to disclose or execute" in description


@pytest.mark.parametrize("memory_type", REQUESTABLE_MEMORY_TYPES)
def test_policy_states_a_rule_for_every_requestable_type(memory_type):
    assert f"{memory_type}: " in MEMORY_TYPE_SELECTION_POLICY


def test_policy_carries_the_four_observed_over_selection_discriminators():
    text = MEMORY_TYPE_SELECTION_POLICY
    # The safety-net rule, which is what a bare extra type always is.
    assert "extras return nothing and spend the budget" in text
    # C01/C02: a standing preference worded as a reference to the past is semantic.
    assert "as I said before" in text and "use alone for" in text
    # C03/C04: episode is for the occurrence itself, not for any past reference.
    assert "a past reference alone is not such a question" in text
    # C02/C03: everyday scheduling words are not a future intention.
    assert "scheduling words in a preference question are not one" in text
    # C06: a request naming steps/checklist must go to procedure_discover.
    assert "procedure_discover" in text
    assert "mentions steps or a checklist" in text


def test_policy_names_no_case_category_or_expected_answer():
    text = MEMORY_TYPE_SELECTION_POLICY.lower()
    for forbidden in ("c01", "c02", "c03", "c04", "c06", "gold", "required_types", "corpus"):
        assert forbidden not in text


class TestSelectionAdvisory:
    """Deterministic, gold-free observability codes; never a gate."""

    def test_minimal_semantic_only_selection_has_no_departure(self):
        assert selection_policy_departures(("semantic",)) == ()

    def test_episode_and_prospective_alone_are_not_departures(self):
        # Their correctness needs the request's meaning, which the Host does not
        # judge; only type-intrinsic rules are decidable here.
        assert selection_policy_departures(("episode", "semantic")) == ()
        assert selection_policy_departures(("episode", "prospective")) == ()

    def test_procedure_is_flagged_because_typed_recall_cannot_serve_it(self):
        assert selection_policy_departures(("semantic", "procedure")) == (
            "procedure_not_served_by_typed_recall",)

    def test_requesting_every_type_is_flagged_as_a_safety_net(self):
        assert selection_policy_departures(REQUESTABLE_MEMORY_TYPES) == (
            "procedure_not_served_by_typed_recall", "all_types_requested")

    def test_empty_selection_has_no_departure(self):
        assert selection_policy_departures(()) == ()

    def test_advisory_does_not_change_what_the_parser_accepts(self):
        # A flagged selection is still a fully valid request.
        assert parse_memory_types(["semantic", "procedure"]) == ("semantic", "procedure")
        assert parse_recall_selection(list(REQUESTABLE_MEMORY_TYPES)) == (
            REQUESTABLE_MEMORY_TYPES, False)

    def test_host_default_selection_is_unchanged_by_the_policy(self):
        # The Host fallback for an implicit selection is a separate contract and
        # the policy must not have narrowed it.
        assert HOST_DEFAULT_MEMORY_TYPES == ("semantic", "episode", "procedure")


def test_policy_keeps_the_route_schema_inside_its_measured_token_cost():
    """The description rides in every request's protected partition.

    Pinned because the 4096/8192-window paging guards sit close to that cap:
    growing this text further must be a deliberate, measured decision.
    """
    from deskpet.sdk_adapters.context_partitions import tool_schema_tokens
    from deskpet.sdk_adapters.context_route import CONTEXT_ROUTE_SCHEMA

    tokens = tool_schema_tokens([{"name": "context_route", "description": "Route context",
                                  "input_schema": CONTEXT_ROUTE_SCHEMA}])
    assert tokens <= 643, f"context_route schema grew to {tokens} wire tokens"


# -- F-ETR-5: the workflow-request signal that decouples the Procedure hint ----
# Rule R4 above tells the model not to request `procedure`, so the Host may not
# read that selection as "the user asked how something is done". This pure
# helper is the replacement signal; it stays advisory exactly like
# `selection_policy_departures` and never gates or rewrites a recall.


@pytest.mark.parametrize("query", [
    "发版流程是怎么走的",
    "上线的步骤有哪些",
    "这个报表怎么做",
    "备份的做法是什么",
    "SOP 在哪",
    "what is the deploy workflow",
    "How do I roll back a release",
    "give me the steps",
    "the release checklist",
])
def test_workflow_shaped_requests_are_recognised(query):
    assert indicates_workflow_request(query) is True


@pytest.mark.parametrize("query", [
    "我常用的日期格式",
    "上周做了什么",
    "下周三提醒我交周报",
    "我和小王约定的单位是什么",
    "who did I meet yesterday",
    "",
])
def test_ordinary_requests_are_not_workflow_shaped(query):
    assert indicates_workflow_request(query) is False


def test_non_text_is_never_a_workflow_request():
    for value in (None, 3, ["流程"], {"query": "流程"}):
        assert indicates_workflow_request(value) is False


def test_the_signal_is_advisory_and_selects_nothing():
    """It reports on the query only; it can neither add nor drop a type."""

    assert parse_recall_selection(["semantic"]) == (("semantic",), False)
    assert selection_policy_departures(("semantic",)) == ()
    assert indicates_workflow_request("发版流程") is True
    assert parse_recall_selection(["semantic"]) == (("semantic",), False)


# -- F-ETR-7: rule R5, semantic narrowing on a reminder-lifecycle turn --------
# P4 ("just add semantic as a safety net") was the one shape in
# DECISION-EXTRA-TYPE-RATE.md §3.1 without a decidable rule. On the C04 rerun it
# fired on 12 of 20 turns and returned zero semantic fragments on all of them,
# while the same sentence shape landed on both sides (C04-08 no, C04-13 yes).
# R5 states the rule in the policy's own vocabulary: a request that asks what
# happened *and* what reminder is already set, while naming no standing value,
# has nothing for `semantic` to hold.
#
# The 20 rows below are the C04 turn inputs of that run (`provider_input`, the
# text the model actually received - not gold, not setup), paired with the
# selection the model actually sent. 12 included `semantic`, 8 did not.

C04_TURNS = (
    ("C04-01", "周五验样结果如何？我还留了什么周一要做的提醒？现在只列，不执行。", False),
    ("C04-02", "上月底盘点发现什么，这个月对应的补办提醒是什么时间？", True),
    ("C04-03", "回顾上个跨年整理年册的问题，再列明年一月已经定下的相关提醒。", False),
    ("C04-04", "导出试用最后怎么样？未来已经记下哪项核查？不要现在去做。", True),
    ("C04-05", "昨天下午那次校色结果和后续取样提醒是什么？", True),
    ("C04-06", "访谈出了什么问题？补录提醒换成北京时间告诉我，先别通知任何人。", False),
    ("C04-07", "八月二十日那次返工因为什么，后续复核提醒定在何时？", False),
    ("C04-08", "昨天交接缺什么，明天一早我留了哪项提醒？", False),
    ("C04-09", "只回顾八月三十一日到九月六日这周，问题是什么，下周对应提醒是什么？", False),
    ("C04-10", "上次还书漏带了什么？我定过下次还完以后要提醒的哪件事？", True),
    ("C04-11", "昨天试印结果和我定下的后续提醒分别是什么？只回顾和列待办。", False),
    ("C04-12", "讨论为什么延期？现在有效的场地确认提醒是哪天？", True),
    ("C04-13", "申请上次查出缺什么，截止前我设了什么准备提醒？", True),
    ("C04-14", "第一阶段上次完成了什么，接第二阶段的已定提醒是什么？", True),
    ("C04-15", "三季度清点发现什么，下一季度首项提醒是什么？", True),
    ("C04-16", "最近一次试课出了什么问题，随后设下了什么提醒？", False),
    ("C04-17", "那次出游为什么取消？现在还有效的相关待办提醒是什么？", True),
    ("C04-18", "这里说的八月和九月都是2026年。八月十二日改封面发现什么，九月十二日有什么提醒？", True),
    ("C04-19", "周五测试最终结果是什么？我对后续跟进设了什么提醒？", True),
    ("C04-20", "夜间传稿漏了什么？把补交提醒同时按上海和UTC标注日期时间。", True),
)
OVER_PROPOSED = tuple(row for row in C04_TURNS if row[2])
DID_NOT = tuple(row for row in C04_TURNS if not row[2])

# Negative control: turns whose gold *requires* `semantic`. R5 must stay silent
# on every one of them - each names a standing value, which is exactly what the
# type holds. (C01/C02 required_types=[semantic]; C03 [episode, semantic];
# C06 [semantic] plus a separate procedure access.)
SEMANTIC_REQUIRED_TURNS = (
    ("C01-09", "我之前要求日常距离显示用什么单位？"),
    ("C01-01", "把这段改成给我的更新说明，篇幅照我之前定下的习惯：周三上线离线导出，旧格式仍可打开，升级前要备份。"),
    ("C02-12", "按我家里一贯的环境限制，今晚练鼓还是明天下午练更合适？"),
    ("C03-01", "只给我看的合作备忘：上次跟林岚卡在哪里，这次按我们定下的反馈方式怎么配合？"),
    ("C06-03", "给新社团做预算草表，按我自己的金额格式和检查习惯，先列规则。"),
)

R5_CODE = "semantic_fallback_on_reminder_lifecycle_request"


def test_policy_states_r5_for_the_semantic_safety_net():
    text = MEMORY_TYPE_SELECTION_POLICY
    assert "never a safety net on an occurrence-plus-reminder question" in text
    assert "asks for no standing value" in text


@pytest.mark.parametrize("case_id,request_text,_had_semantic", C04_TURNS)
def test_every_c04_turn_is_a_reminder_lifecycle_shape(case_id, request_text, _had_semantic):
    """R5 is decidable on all 20, which is what P4 lacked.

    The 12/8 split was jitter: the same shape sat on both sides of it, so the
    rule may not be built on whatever distinguished those two groups.
    """

    assert indicates_reminder_lifecycle_request(request_text) is True, case_id


@pytest.mark.parametrize("case_id,request_text,_had_semantic", OVER_PROPOSED)
def test_the_twelve_over_proposing_turns_now_depart_from_r5(case_id, request_text, _had_semantic):
    codes = selection_policy_departures(("episode", "prospective", "semantic"), request_text)
    assert R5_CODE in codes, case_id


@pytest.mark.parametrize("case_id,request_text,_had_semantic", DID_NOT)
def test_the_eight_minimal_turns_are_unchanged(case_id, request_text, _had_semantic):
    assert selection_policy_departures(("episode", "prospective"), request_text) == (), case_id


def test_r5_moves_twelve_of_the_twenty_and_touches_no_other_c04_turn():
    """The measurable claim: 12 flagged → 0 remaining, 8 untouched."""

    flagged = [case_id for case_id, text, had_semantic in C04_TURNS
               if had_semantic and R5_CODE in selection_policy_departures(
                   ("episode", "prospective", "semantic"), text)]
    unchanged = [case_id for case_id, text, had_semantic in C04_TURNS
                 if not had_semantic and selection_policy_departures(
                     ("episode", "prospective"), text) == ()]
    assert len(flagged) == 12 and len(unchanged) == 8
    # Nothing R5 says removes a required type: C04 gold is [episode, prospective].
    for _case_id, text, _had in C04_TURNS:
        assert "episode" not in selection_policy_departures(("episode", "prospective"), text)


@pytest.mark.parametrize("case_id,request_text", SEMANTIC_REQUIRED_TURNS)
def test_r5_never_fires_where_semantic_is_the_required_type(case_id, request_text):
    assert indicates_reminder_lifecycle_request(request_text) is False, case_id
    assert selection_policy_departures(("semantic",), request_text) == (), case_id
    assert selection_policy_departures(("episode", "semantic"), request_text) == (), case_id


def test_a_standing_value_question_wins_even_with_reminder_words():
    """"按我一贯的习惯" beats every occurrence/reminder marker in the sentence."""

    text = "上次交接缺什么，按我一贯的提醒习惯我定下了什么？"
    assert indicates_reminder_lifecycle_request(text) is False
    assert selection_policy_departures(("semantic", "episode"), text) == ()


def test_r5_needs_both_halves_of_the_shape():
    # Occurrence only, and reminder only, are ordinary requests.
    assert indicates_reminder_lifecycle_request("上次盘点发现什么问题？") is False
    assert indicates_reminder_lifecycle_request("我定下了哪些提醒？") is False


def test_r5_is_undecidable_without_the_request_text():
    """Existing callers pass types alone and must keep their old codes."""

    assert selection_policy_departures(("episode", "prospective", "semantic")) == ()
    assert selection_policy_departures(("episode", "prospective", "semantic"), None) == ()
    assert selection_policy_departures(("episode", "prospective", "semantic"), 17) == ()


def test_r5_is_advisory_and_selects_nothing():
    text = C04_TURNS[1][1]
    assert R5_CODE in selection_policy_departures(("episode", "prospective", "semantic"), text)
    # The same selection is still fully valid input to the parser.
    assert parse_recall_selection(["episode", "prospective", "semantic"]) == (
        ("episode", "prospective", "semantic"), False)


def test_r5_composes_with_the_existing_codes():
    text = C04_TURNS[1][1]
    assert selection_policy_departures(REQUESTABLE_MEMORY_TYPES, text) == (
        "procedure_not_served_by_typed_recall", "all_types_requested", R5_CODE)
