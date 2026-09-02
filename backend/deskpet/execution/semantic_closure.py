# SPDX-License-Identifier: BUSL-1.1

"""TaskScope semantic-closure dirty state (S5b Task 2, design-freeze §2).

``dirty_state(scope)`` = the set of *material* events of one TaskScope whose
``event_sequence`` is greater than the ``closure_watermark`` of the scope's
last closure receipt with ``outcome ∈ {mutate, no_mutation}`` (no receipt →
since 0).  A ``pending`` receipt never clears dirt.  Material / trivial is
decided by the frozen mapping table, never by keywords:

* ``host.file`` / ``host.test`` → material
* ``host.turn`` → trivial
* ``harness.tool_invocation`` whose ``public_payload.tool_name`` is a
  PROJECT_EFFECT tool (§1 list) → material, any other tool → trivial
* ``harness.provider_invocation`` / ``harness.context_snapshot`` /
  ``harness.route_decision`` / ``harness.run_terminal`` → trivial
* ``mutation.plan`` (the closure itself) → trivial

Task 3 adds the terminal closure gate and the fallback invoker on top of this.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from deskpet.task_scope.store import CanonicalTaskScopeStore

MATERIAL_HOST_EVENT_KINDS: frozenset[str] = frozenset({"host.file", "host.test"})
TRIVIAL_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "host.turn",
        "harness.provider_invocation",
        "harness.context_snapshot",
        "harness.route_decision",
        "harness.run_terminal",
        "mutation.plan",
    }
)
CLOSING_RECEIPT_OUTCOMES: frozenset[str] = frozenset({"mutate", "no_mutation"})


@dataclass(frozen=True, slots=True)
class MaterialEvent:
    event_id: str
    event_sequence: int
    event_kind: str
    source_event_id: str


@dataclass(frozen=True, slots=True)
class DirtyState:
    task_scope_id: str
    closure_watermark: int
    material_events: tuple[MaterialEvent, ...]

    @property
    def is_dirty(self) -> bool:
        return bool(self.material_events)

    @property
    def event_watermark(self) -> int:
        """Highest material sequence (== closure_watermark when clean)."""

        if not self.material_events:
            return self.closure_watermark
        return max(item.event_sequence for item in self.material_events)


def is_material_event(event_kind: str, payload: Mapping[str, object] | None) -> bool:
    """Deterministic §2 mapping over one archived event."""

    if event_kind in MATERIAL_HOST_EVENT_KINDS:
        return True
    if event_kind == "harness.tool_invocation":
        from deskpet.sdk_adapters.tool_authority import PROJECT_EFFECT_TOOL_NAMES

        public = payload.get("public_payload") if isinstance(payload, Mapping) else None
        tool_name = public.get("tool_name") if isinstance(public, Mapping) else None
        return isinstance(tool_name, str) and tool_name in PROJECT_EFFECT_TOOL_NAMES
    return False


async def dirty_state(store: CanonicalTaskScopeStore, task_scope_id: str) -> DirtyState:
    async with store._connection() as db:
        receipt = await store._fetchone(
            db,
            "SELECT closure_watermark FROM task_scope_closure_receipts "
            "WHERE task_scope_id=? AND outcome IN ('mutate','no_mutation') "
            "ORDER BY closure_watermark DESC, created_at DESC LIMIT 1",
            (task_scope_id,),
        )
        watermark = 0 if receipt is None else int(receipt["closure_watermark"])
        cursor = await db.execute(
            "SELECT event_id,event_sequence,event_kind,source_event_id,payload_json "
            "FROM task_scope_events WHERE task_scope_id=? AND event_sequence>? "
            "ORDER BY event_sequence",
            (task_scope_id, watermark),
        )
        rows = await cursor.fetchall()
        await cursor.close()
    material: list[MaterialEvent] = []
    for row in rows:
        kind = str(row["event_kind"])
        payload: Mapping[str, object] | None = None
        if kind == "harness.tool_invocation":
            try:
                loaded = json.loads(str(row["payload_json"]))
            except (TypeError, ValueError):
                loaded = None
            payload = loaded if isinstance(loaded, Mapping) else None
        if is_material_event(kind, payload):
            material.append(
                MaterialEvent(
                    str(row["event_id"]),
                    int(row["event_sequence"]),
                    kind,
                    str(row["source_event_id"]),
                )
            )
    return DirtyState(task_scope_id, watermark, tuple(material))


__all__ = [
    "CLOSING_RECEIPT_OUTCOMES",
    "MATERIAL_HOST_EVENT_KINDS",
    "TRIVIAL_EVENT_KINDS",
    "DirtyState",
    "MaterialEvent",
    "dirty_state",
    "is_material_event",
]
