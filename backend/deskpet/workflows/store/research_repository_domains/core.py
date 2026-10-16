"""Connection and fault hooks."""

from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite

from ..schema import initialize_workflow_db


class ResearchRepositoryCore:
    """Connection and fault hooks."""

    if TYPE_CHECKING:

        async def _read_registered_bytes_v6(
            self, db: aiosqlite.Connection, wire_ref: object, *, field: str
        ) -> tuple[str, bytes]: ...
        async def _read_registered_json_v6(
            self, db: aiosqlite.Connection, wire_ref: object, *, field: str
        ) -> tuple[str, dict[str, Any]]: ...
        async def _v6_audit_result(
            self,
            db: aiosqlite.Connection,
            *,
            parent_run_id: str,
            caller_idempotency_key: str,
            child_run_id: str,
            child_operation_id: str,
            created: bool,
            now: float,
        ) -> tuple[str, bool]: ...
        async def _validate_v6_snapshot_graph(
            self, db: aiosqlite.Connection, value: Mapping[str, Any], *, parent_run_id: str
        ) -> None: ...

    def __init__(
        self,
        path: str | Path,
        *,
        clock=time.time,
        blob_root: str | Path | None = None,
        fault_injector: Callable[[str], None | Awaitable[None]] | None = None,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self.blob_root = Path(blob_root) if blob_root is not None else None
        self._fault_injector = fault_injector

    async def _fault(self, stage: str) -> None:
        if self._fault_injector is None:
            return
        result = self._fault_injector(stage)
        if inspect.isawaitable(result):
            await result

    async def initialize(self) -> None:
        await initialize_workflow_db(self.path)

    async def _connect(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        return db
