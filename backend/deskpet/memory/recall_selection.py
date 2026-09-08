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
