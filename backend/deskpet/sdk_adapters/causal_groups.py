# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Recent causal turn-group planner (S5a, TC-HM-11 step 4 oracle).

Replaces the 10,000-row greedy token tail: history is grouped into causal
units — a group starts at a USER message, ends at the assistant message that
no tool result follows (assistant terminal), and tool call/result pairs never
separate from their group.  The newest group may be unterminated (the Run in
flight); it is marked open and is never trimmed.  Large tool results keep a
typed summary plus an exact page ref; raw bytes stay in evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_ASSISTANT_ROLES = {"assistant"}
_USER_ROLES = {"user"}
_TOOL_ROLES = {"tool"}

DEFAULT_LARGE_RESULT_BYTES = 16_384
_SUMMARY_BYTES = 1_024


@dataclass(frozen=True, slots=True)
class CausalItem:
    role: str
    content: str
    projection_kind: str
    bytes_len: int
    page_ref: str | None = None
    summarized: bool = False

    def to_history_row(self) -> dict[str, object]:
        return {
            "role": self.role,
            "content": self.content,
            "projection_kind": self.projection_kind,
            "context_visibility": "conversation",
        }


@dataclass(frozen=True, slots=True)
class CausalGroup:
    items: tuple[CausalItem, ...]
    complete: bool
    open_run: bool = False

    @property
    def bytes_len(self) -> int:
        return sum(item.bytes_len for item in self.items)

    @property
    def item_count(self) -> int:
        return len(self.items)


@dataclass(frozen=True, slots=True)
class CausalGroupPlan:
    groups: tuple[CausalGroup, ...]
    dropped_group_count: int


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _page_ref(index: int, content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"page:causal:{index}:{digest}"


def _item(
    raw: Mapping[str, object], index: int, large_result_bytes: int
) -> CausalItem:
    role = str(raw.get("role") or "user")
    content = _text(raw.get("content"))
    projection = str(raw.get("projection_kind") or f"{role}_message")
    size = len(content.encode("utf-8"))
    if role in _TOOL_ROLES and size > large_result_bytes:
        # Large results never travel raw: typed summary + exact ref; the raw
        # payload remains durable and can be paged back in by this ref.
        ref = _page_ref(index, content)
        summary = json.dumps(
            {
                "kind": "typed_tool_result_summary",
                "bytes": size,
                "excerpt": content[:_SUMMARY_BYTES],
                "page_ref": ref,
            },
            ensure_ascii=False,
        )
        return CausalItem(
            role=role,
            content=summary,
            projection_kind=projection,
            bytes_len=len(summary.encode("utf-8")),
            page_ref=ref,
            summarized=True,
        )
    return CausalItem(
        role=role, content=content, projection_kind=projection, bytes_len=size
    )


def plan_recent_causal_groups(
    history: Sequence[Mapping[str, object]],
    *,
    groups_max: int = 10,
    large_result_bytes: int = DEFAULT_LARGE_RESULT_BYTES,
) -> CausalGroupPlan:
    """Group history into causal units and keep the most recent complete ones.

    The trailing unterminated group (if any) is kept in addition to
    ``groups_max`` complete groups and marked ``open_run``; tool causality is
    never cut inside a group.
    """

    if groups_max < 1:
        raise ValueError("groups_max must be positive")
    items = [_item(raw, index, large_result_bytes) for index, raw in enumerate(history)]

    groups: list[list[CausalItem]] = []
    for item in items:
        if item.role in _USER_ROLES or not groups:
            groups.append([item])
        else:
            groups[-1].append(item)

    built: list[CausalGroup] = []
    for ordinal, bucket in enumerate(groups):
        is_last = ordinal == len(groups) - 1
        # Complete = user-opened and assistant-terminated with no dangling
        # tool item after the final assistant message.
        opened = bucket[0].role in _USER_ROLES
        terminated = bool(bucket) and bucket[-1].role in _ASSISTANT_ROLES
        complete = opened and terminated
        built.append(
            CausalGroup(
                items=tuple(bucket),
                complete=complete,
                open_run=is_last and not complete,
            )
        )

    open_tail = built[-1] if built and built[-1].open_run else None
    complete_groups = [group for group in built if not group.open_run]
    kept = complete_groups[-groups_max:]
    dropped = len(complete_groups) - len(kept)
    if open_tail is not None:
        kept = [*kept, open_tail]
    return CausalGroupPlan(groups=tuple(kept), dropped_group_count=dropped)


__all__ = [
    "DEFAULT_LARGE_RESULT_BYTES",
    "CausalGroup",
    "CausalGroupPlan",
    "CausalItem",
    "plan_recent_causal_groups",
]
