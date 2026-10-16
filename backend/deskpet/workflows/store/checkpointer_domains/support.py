"""Shared helpers for the split class."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from ...errors import WorkflowContractError

NATIVE_ENGINE_KIND = "deskpet-native"


class NativeCheckpointError(WorkflowContractError):
    """Stable persistence failure raised by the native workflow engine."""


def _native_mapping(value: object, *, name: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return copy.deepcopy(dict(result))
    fields = getattr(value, "__dataclass_fields__", None)
    if fields:
        return {key: copy.deepcopy(getattr(value, key)) for key in fields}
    raise TypeError(f"{name} must be a mapping or expose to_dict()")
