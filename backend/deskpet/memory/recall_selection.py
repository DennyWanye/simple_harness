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
    "habit/format/unit/agreement\", including when worded \"as I said before\". "
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


def selection_policy_departures(memory_types) -> tuple[str, ...]:
    """Deterministic, gold-free advisory codes for an already-parsed selection.

    Observability only: no caller may gate, reject or rewrite a recall on these.
    The full policy needs the request's meaning, so only the type-intrinsic rules
    are decidable here - `procedure` cannot be served from typed recall at all,
    and a selection covering every type is a safety net by construction.
    """
    codes = []
    if "procedure" in memory_types:
        codes.append("procedure_not_served_by_typed_recall")
    if set(memory_types) == set(REQUESTABLE_MEMORY_TYPES):
        codes.append("all_types_requested")
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
