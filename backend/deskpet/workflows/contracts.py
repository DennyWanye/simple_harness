# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""JSON 值校验与规范化、工具效果策略与工具清单条目（工具层与执行账本共用的类型）。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

from .errors import InvalidStatePatch, WorkflowDefinitionError

JsonPrimitive: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]


def validate_json_value(value: object, *, path: str = "$") -> None:
    """Reject values that cannot be represented by strict JSON."""

    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidStatePatch("non_finite_number", f"{path} must contain a finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidStatePatch("non_string_key", f"{path} contains a non-string key")
            validate_json_value(item, path=f"{path}.{key}")
        return
    raise InvalidStatePatch(
        "non_json_value",
        f"{path} contains unsupported value type {type(value).__name__}",
    )


def canonical_json(value: JsonValue) -> str:
    validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class EffectKind(StrEnum):
    IDEMPOTENT_READ = "idempotent_read"
    DETERMINISTIC_REUSABLE = "deterministic_reusable"
    STAGED_FILE = "staged_file"
    OPAQUE_MANUAL = "opaque_manual"


@dataclass(frozen=True)
class EffectPolicy:
    policy_id: str
    version: str
    kind: EffectKind | str
    max_attempts: int = 1
    reusable_across_branches: bool = False

    def __post_init__(self) -> None:
        if not self.policy_id or not self.version:
            raise WorkflowDefinitionError(
                "invalid_effect_policy", "Effect policy id and version are required"
            )
        try:
            kind = EffectKind(self.kind)
        except ValueError as exc:
            raise WorkflowDefinitionError(
                "unknown_effect_policy", f"Unknown effect policy: {self.kind}"
            ) from exc
        object.__setattr__(self, "kind", kind)
        if self.max_attempts < 1:
            raise WorkflowDefinitionError(
                "invalid_effect_policy", "Effect max_attempts must be at least 1"
            )
        if kind is EffectKind.OPAQUE_MANUAL and self.max_attempts != 1:
            raise WorkflowDefinitionError(
                "unsafe_opaque_retry", "Opaque effects may be attempted only once"
            )
        if self.reusable_across_branches and kind is not EffectKind.DETERMINISTIC_REUSABLE:
            raise WorkflowDefinitionError(
                "unsafe_branch_reuse",
                "Only deterministic reusable effects may cross branches",
            )


class ToolAccess(StrEnum):
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True)
class ToolInventoryEntry:
    name: str
    access: ToolAccess | str
    spec_version: str
    schema_hash: str
    effect_policy: EffectPolicy | None = None
    outcome_parser_id: str | None = None
    outcome_parser_version: str | None = None
    outcome_parser_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.spec_version or not self.schema_hash:
            raise WorkflowDefinitionError(
                "invalid_tool_inventory", "Tool name/spec version/schema hash are required"
            )
        try:
            access = ToolAccess(self.access)
        except ValueError as exc:
            raise WorkflowDefinitionError(
                "invalid_tool_inventory", f"Unknown tool access: {self.access}"
            ) from exc
        object.__setattr__(self, "access", access)
        parser_fields = (
            self.outcome_parser_id,
            self.outcome_parser_version,
            self.outcome_parser_hash,
        )
        if any(parser_fields) and not all(parser_fields):
            raise WorkflowDefinitionError(
                "incomplete_outcome_parser",
                "Outcome parser id, version and hash must be supplied together",
            )


__all__ = [
    "EffectKind",
    "EffectPolicy",
    "JsonPrimitive",
    "JsonValue",
    "ToolAccess",
    "ToolInventoryEntry",
    "canonical_json",
    "validate_json_value",
]
