# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Private validation helpers shared by several memory protocol families."""

from __future__ import annotations

from enum import StrEnum

from ..disclosure_protocol import _bounded_text, _identifier


def _trusted_current_time(value: object) -> float:
    current_time = _optional_timestamp(value, "current_time")
    if current_time is None:
        raise ValueError("current_time is required")
    return current_time


def _bounded_identifiers(
    value: object,
    name: str,
    *,
    max_items: int = 128,
    max_length: int = 1024,
) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"{name} must contain strings")
    result = tuple(_identifier(item, f"{name} item", max_length=max_length) for item in value)
    if len(result) > max_items:
        raise ValueError(f"{name} exceeds the item limit")
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must be unique")
    return result


def _bounded_enum_tuple(
    value: object,
    name: str,
    enum_type: type[StrEnum],
    *,
    required: bool = False,
    max_items: int = 32,
) -> tuple[StrEnum, ...]:
    if not isinstance(value, (tuple, list)):
        raise TypeError(f"{name} must be an array")
    try:
        result = tuple(enum_type(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} contains an unknown value") from exc
    if required and not result:
        raise ValueError(f"{name} must be non-empty")
    if len(result) > max_items or len(set(result)) != len(result):
        raise ValueError(f"{name} must be unique and bounded")
    return result


def _optional_timestamp(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric or null")
    parsed = float(value)
    if parsed < 0 or parsed == float("inf") or parsed != parsed:
        raise ValueError(f"{name} must be a finite non-negative timestamp or null")
    return parsed


def _bounded_tuple(
    value: object,
    name: str,
    *,
    max_items: int = 64,
    max_item_bytes: int = 2048,
    required: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"{name} must contain strings")
    result = tuple(_bounded_text(item, f"{name} item", max_bytes=max_item_bytes) for item in value)
    if required and not result:
        raise ValueError(f"{name} must be non-empty")
    if len(result) > max_items:
        raise ValueError(f"{name} exceeds the item limit")
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must be unique")
    return result
