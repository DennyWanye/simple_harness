"""Explicit, test-only seams between workflows and the generic execution ledger.

The production launcher still uses the legacy workflow-owned lifecycle.  These
ports make the alternative path injectable without teaching the native
scheduler about execution-ledger SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

import aiosqlite

from deskpet.execution.ports import ExecutionUnitOfWork


@runtime_checkable
class CheckpointExecutionAdapter(Protocol):
    """Execution-ledger writes that must join the checkpointer transaction."""

    async def consume_decisions(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        decisions: Sequence[str | Mapping[str, Any]],
        checkpoint_id: str,
        now: float,
    ) -> list[str]: ...

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
    ) -> Mapping[str, Any]: ...

    async def materialize_intent(
        self,
        db: aiosqlite.Connection,
        *,
        run: Mapping[str, Any],
        intent: Mapping[str, Any],
        now: float,
    ) -> str: ...

    async def link_effects(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        links: Sequence[Mapping[str, Any]],
        now: float,
    ) -> None: ...

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
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class WorkflowExecutionPorts:
    """All generic execution dependencies required by the opt-in workflow path."""

    unit_of_work: ExecutionUnitOfWork
    checkpoint: CheckpointExecutionAdapter


__all__ = ["CheckpointExecutionAdapter", "WorkflowExecutionPorts"]
