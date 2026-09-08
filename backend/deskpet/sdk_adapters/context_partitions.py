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

import math
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


# ── Wire-shaped token accounting (Incident N, 2026-09-08) ────────────────────
#
# HM-TO-A6 attempt 4 measured 306 (request_json, usage_json) pairs against the
# real provider tokenizer.  The pre-fix estimator counted *only* message text at
# ``non_cjk/4`` and reached a median 1.98× under-count, peaking at 4.88×.  The
# decomposition (see plans/2026-09-08-hm-to-a6/DECISION-TOKEN-ESTIMATOR.md):
#
#   * message text density is provider-specific: JSON tool results tokenize at
#     ~3.1 non-CJK chars/token on this evidence while the fixed persona and tool
#     payload sit at ~4.0.  The shared ``/4`` constant is deliberately left
#     alone — tightening it globally fails closed on Runs that really did fit,
#     and a tokenizer's density belongs with the rest of the per-model
#     correction.  → ProviderTokenCalibration, not NON_CJK_CHARS_PER_TOKEN.
#   * tool schemas were missing entirely from ``_plan_turn_messages``
#     ``protected_tokens`` (~3.5 K tokens on the evidence).  → tool_schema_tokens.
#   * per-message wire framing and the assistant ``tool_calls`` array the
#     adapter synthesises for every tool result are chat-template shaped, so
#     they too ride the per-model calibration rather than a global constant.
#   * the relay re-injects each assistant turn's ``reasoning_content`` into the
#     prompt; that text never reaches the Host, so no text-derived estimate can
#     see it.  → ProviderTokenCalibration, a per-model ratio that grows with the
#     provider turn ordinal (the number of hidden reasoning blocks carried).
# Non-CJK characters per token.  Unchanged from the frozen V0 formula on
# purpose (see the note above); the per-model calibration carries the density.
NON_CJK_CHARS_PER_TOKEN = 4
# The ``tools`` array's own JSON envelope per entry
# (``{"type":"function","function":{...}}``): structurally certain, the Host
# writes it itself.  Per-*message* framing is deliberately not a constant here —
# it is chat-template shaped, so it belongs to ProviderTokenCalibration together
# with the tokenizer density and the synthesised ``tool_calls`` array.
WIRE_TOOL_SPEC_OVERHEAD_TOKENS = 8


def text_tokens(value: object) -> int:
    """Shared CJK-aware token estimator (the chat lane formula, verbatim).

    CJK counts one token per character, the rest rounds up at
    ``NON_CJK_CHARS_PER_TOKEN``.  ``token_underestimate_allowed`` is ``false``
    in the frozen oracle, so the rounding direction is always up — and what a
    text-shaped figure structurally cannot see (tool schemas, wire framing,
    provider-injected content) is added by the callers rather than smuggled into
    this constant.
    """

    text = str(value or "")
    cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff")
    non_cjk = max(0, len(text) - cjk)
    return cjk + (non_cjk + NON_CJK_CHARS_PER_TOKEN - 1) // NON_CJK_CHARS_PER_TOKEN


@dataclass(frozen=True, slots=True)
class ProviderTokenCalibration:
    """Per-model uplift for prompt content the Host cannot see or measure.

    ``ratio(ordinal) = min(base_ratio + per_turn_ratio * ordinal, max_ratio)``.
    The default is the identity (1.0 / 0.0 / 1.0): an uncalibrated model keeps
    exactly the wire-shaped estimate, so nothing fails closed that did not
    already.  A model whose provider re-injects hidden per-turn content (a
    thinking model behind a relay) carries a ratio > 1 that grows with the
    provider turn ordinal, because the hidden mass grows one reasoning block
    per turn.
    """

    base_ratio: float = 1.0
    per_turn_ratio: float = 0.0
    max_ratio: float = 1.0

    def ratio(self, provider_turn_ordinal: int = 0) -> float:
        ordinal = max(0, int(provider_turn_ordinal))
        value = float(self.base_ratio) + float(self.per_turn_ratio) * ordinal
        return max(1.0, min(value, max(1.0, float(self.max_ratio))))

    def apply(self, tokens: int, *, provider_turn_ordinal: int = 0) -> int:
        return int(math.ceil(max(0, int(tokens)) * self.ratio(provider_turn_ordinal)))


DEFAULT_CALIBRATION = ProviderTokenCalibration()


def calibration_for_model(model_id: object) -> ProviderTokenCalibration:
    """Resolve the per-model calibration from ``llm.model_info`` (best effort).

    Goes through ``resolve()`` rather than ``BUILTIN`` so the global
    ``model_overrides.toml`` layer really reaches this lane — a relay's hidden
    injection differs per endpoint, so the user must be able to retune these
    three numbers against their own observed ``usage.input_tokens``.  The cost
    is bounded: this runs a few times per provider turn, not per message.
    Any failure degrades to the identity calibration rather than breaking
    context assembly, and a ratio below 1.0 can never shrink an estimate.
    """

    name = str(model_id or "").strip().lower()
    if not name:
        return DEFAULT_CALIBRATION
    if "/" in name:
        name = name.split("/", 1)[1]
    try:
        from llm.model_info import resolve

        info = resolve(name)
        return ProviderTokenCalibration(
            base_ratio=float(getattr(info, "input_estimate_ratio", 1.0) or 1.0),
            per_turn_ratio=float(getattr(info, "input_estimate_ratio_per_turn", 0.0) or 0.0),
            max_ratio=float(getattr(info, "input_estimate_ratio_max", 1.0) or 1.0),
        )
    except Exception:  # noqa: BLE001 — model metadata must never break budgeting
        return DEFAULT_CALIBRATION


def window_tokens_for(window_tokens: object, model_id: object = None) -> int:
    """The window to budget against: the bound one, else the model's own.

    A missing window used to drop straight to the smallest frozen tier (4096,
    i.e. 2663 input tokens).  Once the tool schemas are honestly charged that is
    no longer merely "over-trimming" — a real catalog does not fit at all, so a
    lane that simply failed to put the window in ``context_metadata`` could not
    start.  When ``llm.model_info`` actually knows the model we now fall back to
    its window instead.  An *unknown* model still falls back to the smallest
    tier: guessing large for a model we know nothing about is the one direction
    that can overflow rather than over-trim.
    """

    floor = min(PARTITION_CAPS)
    if window_tokens:
        return max(int(window_tokens), floor)
    name = str(model_id or "").strip().lower()
    if "/" in name:
        name = name.split("/", 1)[1]
    if not name:
        return floor
    try:
        from llm.model_info import BUILTIN, resolve

        if name not in BUILTIN:
            return floor
        return max(int(resolve(name).context_window), floor)
    except Exception:  # noqa: BLE001 — metadata must never break budgeting
        return floor


def tool_schema_tokens(specs: object) -> int:
    """Wire tokens of the ``tools`` array a request ships alongside its messages.

    Accepts both catalog rows (``{"name", "description", "input_schema"}``) and
    ``ProviderToolSpec`` objects (``.name`` / ``.description`` / ``.parameters``).
    The pre-fix catalog field ``schema_token_count`` used
    ``len(repr(input_schema)) // 4`` — it dropped the tool name and description
    entirely and used ``repr`` rather than the JSON actually sent, under-counting
    the evidence's tool payload by ~35%.
    """

    from simple_harness import thaw_json

    from deskpet.task_scope.protocol import canonical_json

    total = 0
    for spec in specs or ():
        if isinstance(spec, Mapping):
            name = spec.get("name")
            description = spec.get("description")
            schema = spec.get("input_schema")
            if schema is None:
                schema = spec.get("parameters")
        else:
            name = getattr(spec, "name", None)
            description = getattr(spec, "description", None)
            schema = getattr(spec, "parameters", None)
        rendered = str(name or "") + "\n" + str(description or "")
        # ``ProviderToolSpec`` freezes ``parameters`` into a recursive
        # MappingProxyType; canonical_json rejects it with a TaskScopeProtocolError,
        # which is a ValueError — so this silently fell back to repr(), whose
        # per-level ``mappingproxy(...)`` wrappers priced the *same* catalog 13%
        # higher here than on the primary lane (3867 vs 3423 tokens on the
        # incident's 12-tool catalog), i.e. the two lanes this fix exists to
        # unify would still have disagreed.  Thaw first.
        thawed = thaw_json(schema) if schema is not None else {}
        try:
            rendered += "\n" + canonical_json(thawed)
        except (TypeError, ValueError):
            # A schema this rejects cannot be serialised onto the wire either,
            # so the Run is already broken elsewhere; budgeting must not be the
            # thing that raises.  Render the *thawed* form so a frozen spec and
            # its plain catalog row still agree even on this degraded path.
            rendered += "\n" + repr(thawed)
        total += text_tokens(rendered) + WIRE_TOOL_SPEC_OVERHEAD_TOKENS
    return total


def turn_token_estimator(
    calibration: ProviderTokenCalibration = DEFAULT_CALIBRATION,
    *,
    provider_turn_ordinal: int = 0,
) -> Callable[[object], int]:
    """Per-item token estimator: the text figure, uplifted by the calibration.

    Returned as a ``token_estimator`` so :func:`assemble_partitions` and
    :func:`trim_causal_groups` keep their existing shape — the calibration
    reaches every partition and every causal-group item identically, which is
    what keeps the trim decisions consistent with the fail-closed check.
    """

    ratio = calibration.ratio(provider_turn_ordinal)

    def estimate(value: object) -> int:
        return int(math.ceil(text_tokens(value) * ratio))

    return estimate


def trim_causal_groups(
    groups,
    *,
    window_tokens: int,
    protected_tokens: int,
    token_estimator=text_tokens,
):
    """Frozen-cap + budget trim over causal groups (production + oracle path).

    Applies the frozen recent_causal_groups caps for the window tier, then
    drops the oldest complete groups until the token budget fits.  The open
    group is never dropped.  Returns (kept_groups, trimmed_count).
    """

    tier = budget_window(window_tokens)
    caps = PARTITION_CAPS[tier]["recent_causal_groups"]
    effective = effective_input_budget(window_tokens)
    kept = list(groups)
    trimmed = 0

    def closed():
        return [i for i, g in enumerate(kept) if not g.open_run]

    def group_tokens(group):
        return sum(token_estimator(item.content) for item in group.items)

    while (
        len(closed()) > int(caps["groups_max"])
        or sum(g.item_count for g in kept) > int(caps["items_max"])
        or sum(g.bytes_len for g in kept) > int(caps["bytes_max"])
    ):
        candidates = closed()
        if not candidates:
            raise ContextBudgetExceeded("sdk_context_causal_groups_over_cap")
        kept.pop(candidates[0])
        trimmed += 1
    while protected_tokens + sum(group_tokens(g) for g in kept) > effective:
        candidates = closed()
        if not candidates or (len(candidates) <= 1 and kept[candidates[0]] is kept[-1]):
            break
        if len(candidates) <= 1:
            break
        kept.pop(candidates[0])
        trimmed += 1
    return tuple(kept), trimmed


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
    "DEFAULT_CALIBRATION",
    "GENERATION_RESERVE",
    "NON_CJK_CHARS_PER_TOKEN",
    "PARTITION_CAPS",
    "TRIM_ORDER",
    "WIRE_TOOL_SPEC_OVERHEAD_TOKENS",
    "AssembledContext",
    "ContextBudgetExceeded",
    "PartitionItem",
    "PartitionReport",
    "ProviderTokenCalibration",
    "assemble_partitions",
    "budget_window",
    "calibration_for_model",
    "effective_input_budget",
    "safety_margin",
    "text_tokens",
    "tool_schema_tokens",
    "turn_token_estimator",
    "window_tokens_for",
]
