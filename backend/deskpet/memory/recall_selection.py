"""Model type selection is a request, never a disclosure or budget grant."""

REQUESTABLE_MEMORY_TYPES = ("semantic", "episode", "procedure", "prospective")
HOST_DEFAULT_MEMORY_TYPES = ("semantic", "episode", "procedure")


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
