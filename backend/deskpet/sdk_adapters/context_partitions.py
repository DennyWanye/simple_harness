# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Frozen partition-budget assembler (S5a, V0 SPIKE-CONTEXT-DOC oracle).

Constants mirror ``testcase/human-memory-program/fixtures/metric-formulas.json``
(``context_budget``) — a test pins byte equality so the oracle cannot drift.
Trim order is the frozen V0 choice: tools_skills_attachments → long_term down
to one item → short_horizon.  Protected rules, the current query, and the open
causal group are never trimmed.  Token underestimates are forbidden: when the
estimate still exceeds the effective budget after every allowed trim, assembly
fails closed instead of shipping an oversized payload.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from deskpet.sdk_adapters.causal_groups import CausalGroup

GENERATION_RESERVE: dict[int, int] = {4096: 1024, 8192: 2048, 32768: 4096}

PARTITION_CAPS: dict[int, dict[str, dict[str, int]]] = {
    4096: {
        "protected": {"items_max": 32, "bytes_max": 65536},
        "recent_causal_groups": {"groups_max": 10, "items_max": 64, "bytes_max": 131072},
        "task_scope_current": {"items_max": 24, "bytes_max": 65536},
        "short_horizon": {"items_max": 8, "bytes_max": 32768},
        "long_term": {"items_max": 12, "bytes_max": 65536},
        "tools_skills_attachments": {"items_max": 32, "bytes_max": 131072},
    },
    8192: {
        "protected": {"items_max": 48, "bytes_max": 98304},
        "recent_causal_groups": {"groups_max": 10, "items_max": 80, "bytes_max": 196608},
        "task_scope_current": {"items_max": 40, "bytes_max": 98304},
        "short_horizon": {"items_max": 16, "bytes_max": 65536},
        "long_term": {"items_max": 24, "bytes_max": 98304},
        "tools_skills_attachments": {"items_max": 48, "bytes_max": 262144},
    },
    32768: {
        "protected": {"items_max": 64, "bytes_max": 131072},
        "recent_causal_groups": {"groups_max": 10, "items_max": 120, "bytes_max": 393216},
        "task_scope_current": {"items_max": 64, "bytes_max": 196608},
        "short_horizon": {"items_max": 32, "bytes_max": 131072},
        "long_term": {"items_max": 48, "bytes_max": 196608},
        "tools_skills_attachments": {"items_max": 96, "bytes_max": 524288},
    },
}

TRIM_ORDER: tuple[str, ...] = (
    "tools_skills_attachments",
    "long_term",
    "short_horizon",
)

_SUPPORTED_WINDOWS = tuple(sorted(PARTITION_CAPS))


class ContextBudgetExceeded(RuntimeError):
    code = "sdk_context_budget_exceeded"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


def safety_margin(window_tokens: int) -> int:
    return max(256, window_tokens // 10)


def budget_window(window_tokens: int) -> int:
    """Map an arbitrary provider window onto the frozen budget tier."""

    if window_tokens < _SUPPORTED_WINDOWS[0]:
        raise ContextBudgetExceeded("sdk_context_window_below_minimum")
    chosen = _SUPPORTED_WINDOWS[0]
    for tier in _SUPPORTED_WINDOWS:
        if window_tokens >= tier:
            chosen = tier
    return chosen


def effective_input_budget(window_tokens: int) -> int:
    tier = budget_window(window_tokens)
    return window_tokens - GENERATION_RESERVE[tier] - safety_margin(window_tokens)


@dataclass(frozen=True, slots=True)
class PartitionItem:
    content: str
    tokens: int
    bytes_len: int


@dataclass(frozen=True, slots=True)
class PartitionReport:
    item_count: int
    bytes_len: int
    tokens: int
    trimmed_items: int


@dataclass(frozen=True, slots=True)
class AssembledContext:
    window_tokens: int
    budget_tier: int
    effective_budget: int
    total_tokens: int
    partitions: Mapping[str, PartitionReport]
    kept: Mapping[str, tuple[PartitionItem, ...]]
    kept_groups: tuple[CausalGroup, ...]
    trimmed_groups: int


def _cap_items(
    items: Sequence[PartitionItem], caps: Mapping[str, int]
) -> tuple[list[PartitionItem], int]:
    kept = list(items)
    trimmed = 0
    items_max = int(caps["items_max"])
    bytes_max = int(caps["bytes_max"])
    while len(kept) > items_max:
        kept.pop(0)
        trimmed += 1
    while kept and sum(item.bytes_len for item in kept) > bytes_max:
        kept.pop(0)
        trimmed += 1
    return kept, trimmed


def assemble_partitions(
    *,
    window_tokens: int,
    protected: Sequence[PartitionItem],
    causal_groups: Sequence[CausalGroup],
    task_scope_current: Sequence[PartitionItem] = (),
    short_horizon: Sequence[PartitionItem] = (),
    long_term: Sequence[PartitionItem] = (),
    tools_skills_attachments: Sequence[PartitionItem] = (),
    token_estimator: Callable[[str], int],
) -> AssembledContext:
    """Apply frozen caps, then the frozen trim order, then fail closed."""

    tier = budget_window(window_tokens)
    caps = PARTITION_CAPS[tier]
    effective = effective_input_budget(window_tokens)

    def group_tokens(group: CausalGroup) -> int:
        return sum(token_estimator(item.content) for item in group.items)

    protected_items = list(protected)
    if len(protected_items) > caps["protected"]["items_max"] or sum(
        item.bytes_len for item in protected_items
    ) > caps["protected"]["bytes_max"]:
        raise ContextBudgetExceeded("sdk_context_protected_partition_over_cap")

    partitions: dict[str, list[PartitionItem]] = {
        "task_scope_current": list(task_scope_current),
        "short_horizon": list(short_horizon),
        "long_term": list(long_term),
        "tools_skills_attachments": list(tools_skills_attachments),
    }
    trimmed_counts: dict[str, int] = {}
    for name, items in partitions.items():
        kept, trimmed = _cap_items(items, caps[name])
        partitions[name] = kept
        trimmed_counts[name] = trimmed

    groups = list(causal_groups)
    group_caps = caps["recent_causal_groups"]
    trimmed_groups = 0

    def closed_groups() -> list[int]:
        return [
            index
            for index, group in enumerate(groups)
            if not group.open_run
        ]

    while len(closed_groups()) > int(group_caps["groups_max"]) or sum(
        group.item_count for group in groups
    ) > int(group_caps["items_max"]) or sum(
        group.bytes_len for group in groups
    ) > int(group_caps["bytes_max"]):
        candidates = closed_groups()
        if not candidates:
            raise ContextBudgetExceeded("sdk_context_causal_groups_over_cap")
        groups.pop(candidates[0])
        trimmed_groups += 1

    def total_tokens() -> int:
        total = sum(token_estimator(item.content) for item in protected_items)
        total += sum(group_tokens(group) for group in groups)
        for items in partitions.values():
            total += sum(item.tokens for item in items)
        return total

    # Frozen trim order: attachments -> long_term down to one item ->
    # short_horizon.  Groups: oldest complete groups may go, open group never.
    for name in TRIM_ORDER:
        while total_tokens() > effective and partitions[name]:
            if name == "long_term" and len(partitions[name]) <= 1:
                break
            partitions[name].pop(0)
            trimmed_counts[name] += 1
    while total_tokens() > effective:
        candidates = closed_groups()
        if len(candidates) <= 1:
            break
        groups.pop(candidates[0])
        trimmed_groups += 1
    final_total = total_tokens()
    if final_total > effective:
        raise ContextBudgetExceeded()

    reports = {
        "protected": PartitionReport(
            len(protected_items),
            sum(item.bytes_len for item in protected_items),
            sum(token_estimator(item.content) for item in protected_items),
            0,
        ),
        "recent_causal_groups": PartitionReport(
            sum(group.item_count for group in groups),
            sum(group.bytes_len for group in groups),
            sum(group_tokens(group) for group in groups),
            trimmed_groups,
        ),
    }
    for name, items in partitions.items():
        reports[name] = PartitionReport(
            len(items),
            sum(item.bytes_len for item in items),
            sum(item.tokens for item in items),
            trimmed_counts[name],
        )
    return AssembledContext(
        window_tokens=window_tokens,
        budget_tier=tier,
        effective_budget=effective,
        total_tokens=final_total,
        partitions=reports,
        kept={name: tuple(items) for name, items in partitions.items()},
        kept_groups=tuple(groups),
        trimmed_groups=trimmed_groups,
    )


__all__ = [
    "GENERATION_RESERVE",
    "PARTITION_CAPS",
    "TRIM_ORDER",
    "AssembledContext",
    "ContextBudgetExceeded",
    "PartitionItem",
    "PartitionReport",
    "assemble_partitions",
    "budget_window",
    "effective_input_budget",
    "safety_margin",
]
