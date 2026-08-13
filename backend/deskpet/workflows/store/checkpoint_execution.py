"""Compatibility adapter for native checkpoints joining an execution UoW tx.

The adapter deliberately owns no SQL or transaction lifecycle.  It binds the
caller's already-open SQLite connection to the canonical execution UoW and
forwards the typed workflow/checkpoint operations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import aiosqlite

from .execution_uow import CheckpointExecutionError, SqliteExecutionUnitOfWork


class SqliteCheckpointExecutionAdapter:
    """Transition seam; all execution DML is owned by the bound UoW."""

    def __init__(self, unit_of_work: SqliteExecutionUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    @property
    def unit_of_work(self) -> SqliteExecutionUnitOfWork:
        return self._unit_of_work

    async def mark_running_on_claim(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        now: float,
    ) -> bool:
        return await self._unit_of_work.bind(db).mark_workflow_running_on_claim(
            run_id=run_id,
            now=now,
        )

    async def consume_decisions(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        decisions: Sequence[str | Mapping[str, Any]],
        checkpoint_id: str,
        now: float,
    ) -> list[str]:
        return await self._unit_of_work.bind(db).consume_workflow_decisions(
            run_id=run_id,
            decisions=decisions,
            checkpoint_id=checkpoint_id,
            now=now,
        )

    async def open_decision(
        self,
        db: aiosqlite.Connection,
        *,
        run: Mapping[str, Any],
        interrupt_id: str,
        checkpoint_id: str,
        task_id: str | None,
        kind: str,
        prompt: object,
        expires_at: float | None,
        now: float,
    ) -> Mapping[str, Any]:
        return await self._unit_of_work.bind(db).open_workflow_decision(
            run=run,
            interrupt_id=interrupt_id,
            checkpoint_id=checkpoint_id,
            task_id=task_id,
            kind=kind,
            prompt=prompt,
            expires_at=expires_at,
            now=now,
        )

    async def materialize_intent(
        self,
        db: aiosqlite.Connection,
        *,
        run: Mapping[str, Any],
        intent: Mapping[str, Any],
        now: float,
    ) -> str:
        return await self._unit_of_work.bind(db).append_workflow_event(
            run=run,
            intent=intent,
            now=now,
        )

    async def link_effects(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        links: Sequence[Mapping[str, Any]],
        now: float,
    ) -> None:
        await self._unit_of_work.bind(db).link_workflow_effects(
            run_id=run_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_id=checkpoint_id,
            links=links,
            now=now,
        )

    async def finalize_run(
        self,
        db: aiosqlite.Connection,
        *,
        run: Mapping[str, Any],
        terminal_status: str,
        terminal_error: Mapping[str, Any] | None,
        recovery_action: str | None,
        event_ids: Sequence[str],
        now: float,
    ) -> str:
        return await self._unit_of_work.bind(db).finalize_workflow_run(
            run=run,
            terminal_status=terminal_status,
            terminal_error=terminal_error,
            recovery_action=recovery_action,
            event_ids=event_ids,
            now=now,
        )


__all__ = ["CheckpointExecutionError", "SqliteCheckpointExecutionAdapter"]
