# SPDX-License-Identifier: Apache-2.0
"""Original executor identity of a dispatch intent, without ID guessing."""
from __future__ import annotations

from typing import Any

from simple_harness.agents.runtime import agent_id_for

from ..storage.store import DispatchIntent, Store
from .planning_operations import SourceUnavailable


def read_dispatch_history(store: Store, runtime: Any, intent: DispatchIntent) -> tuple[DispatchIntent, ...]:
    """The executors this dispatch intent ever had, oldest first; the last is current.

    2026-10-03: a service intent is never handed to a second executor any more (its
    only writer, the service re-handoff, was removed with the legacy review paths), so
    the history is the intent itself — checked against the Store and its own Agent id.
    """
    with store.read_view():
        current = store.get_intent(intent.intent_id)
        if current != intent:
            raise SourceUnavailable("dispatch_history_intent_changed")
        expected_agent = agent_id_for(runtime.owner_scope, intent.creation_key)
        if intent.agent_id not in (None, expected_agent):
            raise SourceUnavailable("dispatch_history_current_agent_invalid")
        return (intent,)
