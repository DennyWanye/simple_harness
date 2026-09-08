# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Host-side memo of the ContextRouteState each provider turn was offered under.

Why this exists (HM-TO-A6 incident A, 2026-09-08 native run
``product-sdk-a551104a…`` turn 8): the per-turn provider snapshot already hides
PROJECT_EFFECT Tools while the Run is not ``routed_task``
(``ProductRunContextAuthority._visible_provider_specs``), but the *discovery*
surface (``tool_search`` / ``tool_describe`` / ``tool_activate``) had no idea
what the route was.  A real model therefore activated ``builtin:run_shell``
while UNROUTED, routed ``direct_standalone``, called it from the schema still
in its own history, and the frozen SDK escalated the Host authority refusal
into a whole-Run ``driver_failed`` (see
``DECISION-STANDALONE-ROUTE-TOOL-AUTHORITY.md``).

``ProductRunContextAuthority.prepare_snapshot`` writes the route state of the
turn it is preparing; the capability bridge reads it back inside the tool
handlers of that same turn, so the value is exactly "the route state the model
saw when it decided to call ``tool_activate``".

Process-local and advisory by design: this memo only shapes *disclosure and
activation ergonomics*.  Every authority check downstream (the SDK route
barrier, the Host TaskExecutionEnvelope authority, the EffectGate) is
unchanged and still fails closed on its own, so a missing memo entry degrades
to the previous behaviour rather than to a weaker check.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

DEFAULT_RUN_ROUTE_STATE_MEMO_CAPACITY = 4096
ROUTED_TASK_STATE = "routed_task"


class RunRouteStateMemo:
    """Latest ContextRouteState per SDK Run id (last writer wins, bounded)."""

    def __init__(
        self, *, capacity: int = DEFAULT_RUN_ROUTE_STATE_MEMO_CAPACITY
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("run route state memo capacity must be a positive integer")
        self._states: OrderedDict[str, str] = OrderedDict()
        self._capacity = capacity
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def record(self, run_id: object, route_state: object) -> None:
        key = _run_key(run_id)
        value = str(getattr(route_state, "value", route_state) or "").strip()
        if not key or not value:
            return
        with self._lock:
            # Last writer wins: the route can legitimately move forward inside
            # one Run (unrouted -> routed_standalone / routed_task).
            self._states.pop(key, None)
            self._states[key] = value
            while len(self._states) > self._capacity:
                self._states.popitem(last=False)

    def read(self, run_id: object) -> str | None:
        with self._lock:
            return self._states.get(_run_key(run_id))

    def release(self, run_id: object) -> None:
        with self._lock:
            self._states.pop(_run_key(run_id), None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._states)


def _run_key(run_id: object) -> str:
    value = getattr(run_id, "value", run_id)
    return str(value or "").strip()


__all__ = [
    "DEFAULT_RUN_ROUTE_STATE_MEMO_CAPACITY",
    "ROUTED_TASK_STATE",
    "RunRouteStateMemo",
]
