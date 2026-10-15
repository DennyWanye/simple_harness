# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Planner rounds waiting for their pool, persisted in the scheduler state."""

from __future__ import annotations

from typing import Any


class DeferredPlanning(dict):
    """mission id → (since, ordinal) of a Planner round waiting for its pool.

    NEXT-TG-1.0 §3.6: it lived only in memory, so a restart while the planner pool
    cooled down forgot the round — counted as used and never asked. The map is
    now kept in ``scheduler_state`` and reloaded by :meth:`Orchestrator.recover`;
    ``since`` survives too, so the wait bound is not reset by a restart.
    """

    KEY = "deferred_planning"

    def __init__(self) -> None:
        super().__init__()
        self._store: Any = None

    def bind(self, store: Any) -> None:
        self._store = store
        saved = store.get_scheduler_state(self.KEY) or {}
        for mission_id, (since, ordinal) in dict(saved.get("missions") or {}).items():
            super().setdefault(str(mission_id), (float(since), int(ordinal)))

    def _save(self) -> None:
        if self._store is not None:
            self._store.put_scheduler_state(
                self.KEY,
                {"missions": {key: [since, ordinal] for key, (since, ordinal) in self.items()}},
            )

    def __setitem__(self, key: str, value: tuple[float, int]) -> None:
        if self.get(key) == value:
            return
        super().__setitem__(key, value)
        self._save()

    def pop(self, key: str, *default: Any) -> Any:
        present = key in self
        value = super().pop(key, *default)
        if present:
            self._save()
        return value
