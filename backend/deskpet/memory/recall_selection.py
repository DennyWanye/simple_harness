"""Model type selection is a request, never a disclosure or budget grant."""

REQUESTABLE_MEMORY_TYPES = ("semantic", "episode", "procedure", "prospective")
HOST_DEFAULT_MEMORY_TYPES = ("semantic", "episode", "procedure")

# Model-facing selection policy (HM-AC-8 extra-type rate). Canonical text lives
# here beside the parser so the schema description and the tests read the same
# words. It states what each type can actually contain and what the Host will
# withhold; it never encodes a per-case expected answer, and the Host neither
# rejects nor rewrites a selection that departs from it.
MEMORY_TYPE_SELECTION_POLICY = (
    "Request only types that can hold the answer; extras return nothing and spend the budget. "
    "semantic: facts, preferences, standing agreements - use alone for \"what is my "
    "habit/format/unit/agreement\", including when worded \"as I said before\"; if the "
    "request names no such standing value, omit it - what happened plus which reminder you "
    "set names none. "
    "episode: only when the answer needs the past occurrence itself - what happened, when, with "
    "whom, how it ended; a past reference alone is not such a question. "
    "prospective: only for a future intention, reminder, deadline or trigger; scheduling words "
    "in a preference question are not one. "
    "procedure: typed recall returns only an already-bound Procedure, never a workflow saved but "
    "not yet used - call procedure_discover instead when the request mentions steps or a checklist."
)


# A request that reads as "how is this done?". Pure text markers only: no gold,
# no per-case answer, no model call. Used to decide whether the Host points at
# `procedure_discover` when typed recall returned no Procedure — which must not
# depend on the model having requested the `procedure` type, because rule R4 of
# the policy above tells it not to. (F-ETR-5: after R4 landed, C06's
# `procedure_discover` call rate fell 18/19 → 14/19 purely because the hint had
# been keyed on `memory_types`.)
WORKFLOW_REQUEST_MARKERS = (
    # Chinese, simplified and traditional.
    "流程", "步骤", "步驟", "怎么做", "怎麼做", "怎样做", "怎樣做",
    "如何做", "如何操作", "操作方法", "做法", "工序", "checklist",
    # English, matched on lowercased text.
    "workflow", "procedure", "runbook", "playbook", "sop",
    "step by step", "steps", "how do i", "how to",
)


def indicates_workflow_request(text) -> bool:
    """Deterministic "this asks how something is done" signal for one query.

    Advisory only, exactly like :func:`selection_policy_departures`: no caller
    may gate, reject or rewrite a recall on it. It decides only whether a
    result that returned no Procedure also points at the discovery surface.
    """

    if type(text) is not str:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in WORKFLOW_REQUEST_MARKERS)


# -- R5 (semantic narrowing) --------------------------------------------------
# F-ETR-7: P4 - the bare `semantic` safety net - was the one shape in
# DECISION-EXTRA-TYPE-RATE.md 3.1 with no decidable rule, only the general
# "smallest set that can hold the answer". It then fired on 12 of the 20 C04
# turns, none of which returned a single semantic fragment, and on neither side
# of the same sentence shape (C04-08 did not, C04-13 did) - jitter, not
# judgement. R5 gives that shape a rule stated in the vocabulary the policy
# already uses: a request that asks what happened AND what reminder the user
# already set, while naming no standing value, has nothing for `semantic` to
# hold, because a standing value is the only thing that type stores.
#
# Marker sets are pure text, exactly like WORKFLOW_REQUEST_MARKERS: no gold, no
# per-case answer, no model call. Measured over the 240-case corpus they select
# the 20 C04 turns and nothing else; in particular no request whose gold
# requires `semantic` (C01/C02/C03/C06) matches, because every one of those
# names a standing value.
#
# F-ETR-8 (R5b, RUN-C04-RERUN-2-REVIEW 6.4): R5's model-visible half was the one
# clause in this policy stated as a forbidden *motive* ("never a safety net"),
# while every other clause is a decidable gate ("only when..."). Its 20/20
# request-shape coverage was already complete, yet 6 of 20 C04 turns still added
# `semantic` - and 3 of those put it first or second in the list, which is not
# how a fallback is written. The Host judgement was never the gap; a rule the
# model cannot self-check against was. R5b keeps the identical scope and states
# it as the same kind of gate as R1-R4: name a standing value, or omit the type.
# The marker sets below are unchanged - the rule they encode did not move.
OCCURRENCE_REQUEST_MARKERS = (
    "回顾", "上次", "那次", "昨天", "上月", "上个", "最近一次", "结果", "为什么",
    "问题", "发现", "缺", "漏", "完成", "怎么样", "出了", "延期", "取消", "返工",
    "what happened", "last time", "yesterday", "why did", "how did it go",
)
REMINDER_REQUEST_MARKERS = (
    "提醒", "待办", "截止", "到期", "定下", "定过", "设了", "设下", "已定", "记下",
    "留了", "排程",
    "reminder", "deadline", "due date", "follow-up i set", "todo",
)
# The R1 vocabulary: what `semantic` actually holds. One of these in the request
# means a standing value *is* being asked for, so R5 must stay silent.
STANDING_VALUE_MARKERS = (
    "习惯", "偏好", "格式", "单位", "约定", "一贯", "平时", "通常", "规则", "要求",
    "沿用", "默认", "惯例", "方式", "风格", "口径", "模板", "标准",
    "habit", "format", "unit", "agreement", "prefer", "usually", "always",
    "convention", "style", "standing", "as i said before",
)


def indicates_reminder_lifecycle_request(text) -> bool:
    """Deterministic "what happened, and what reminder did I set?" signal.

    Advisory only, exactly like :func:`indicates_workflow_request`: no caller
    may gate, reject or rewrite a recall on it. It only lets the Host record
    that a `semantic` request on this shape departs from rule R5.
    """

    if type(text) is not str:
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in STANDING_VALUE_MARKERS):
        return False
    return (any(marker in lowered for marker in REMINDER_REQUEST_MARKERS)
            and any(marker in lowered for marker in OCCURRENCE_REQUEST_MARKERS))


def selection_policy_departures(memory_types, request=None) -> tuple[str, ...]:
    """Deterministic, gold-free advisory codes for an already-parsed selection.

    Observability only: no caller may gate, reject or rewrite a recall on these.
    The rest of the policy needs the request's meaning, so only rules that are
    decidable from the types themselves - `procedure` cannot be served from
    typed recall at all, and a selection covering every type is a safety net by
    construction - plus R5, which the caller may make decidable by passing the
    request text, produce a code here.
    """
    codes = []
    if "procedure" in memory_types:
        codes.append("procedure_not_served_by_typed_recall")
    if set(memory_types) == set(REQUESTABLE_MEMORY_TYPES):
        codes.append("all_types_requested")
    if "semantic" in memory_types and indicates_reminder_lifecycle_request(request):
        codes.append("semantic_fallback_on_reminder_lifecycle_request")
    return tuple(codes)


def parse_memory_types(value, *, allow_empty: bool = False) -> tuple[str, ...]:
    if value is None:
        raise ValueError("context_route_memory_types_required")
    if (
        type(value) not in (list, tuple)
        or not (0 if allow_empty else 1) <= len(value) <= len(REQUESTABLE_MEMORY_TYPES)
        or any(type(item) is not str or item not in REQUESTABLE_MEMORY_TYPES for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("context_route_memory_types_invalid")
    return tuple(value)


def parse_recall_selection(memory_types, include_short_horizon=False):
    if type(include_short_horizon) is not bool:
        raise ValueError("context_route_short_horizon_invalid")
    return parse_memory_types(memory_types, allow_empty=include_short_horizon), include_short_horizon
