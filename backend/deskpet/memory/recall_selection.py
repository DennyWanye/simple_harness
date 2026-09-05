"""Model type selection is a request, never a disclosure or budget grant."""

REQUESTABLE_MEMORY_TYPES = ("semantic", "episode", "procedure", "prospective")
HOST_DEFAULT_MEMORY_TYPES = ("semantic", "episode", "procedure")


def parse_memory_types(value) -> tuple[str, ...]:
    if value is None:
        raise ValueError("context_route_memory_types_required")
    if (
        type(value) not in (list, tuple)
        or not 1 <= len(value) <= len(REQUESTABLE_MEMORY_TYPES)
        or any(type(item) is not str or item not in REQUESTABLE_MEMORY_TYPES for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("context_route_memory_types_invalid")
    return tuple(value)
