"""Database-registered content-addressed blobs with provisional ownership."""

from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import aiosqlite

from ..contracts import NodeExecutionIdentity
from .blob_store import BlobRef, BlobStore
from .schema import initialize_workflow_db


class RegisteredBlobStore:
    """Make the file durable first, then register it with a run-staging owner."""

    def __init__(
        self,
        root: str | Path,
        database: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        fault_injector: Callable[[str], None | Awaitable[None]] | None = None,
    ) -> None:
        self.root = Path(root)
        self.database = Path(database)
        self._files = BlobStore(self.root)
        self._clock = clock
        self._fault_injector = fault_injector

    async def _fault(self, stage: str) -> None:
        if self._fault_injector is None:
            return
        result = self._fault_injector(stage)
        if inspect.isawaitable(result):
            await result

    async def put(
        self,
        data: bytes,
        execution_identity: NodeExecutionIdentity,
        *,
        media_type: str = "application/octet-stream",
    ) -> BlobRef:
        ref = self._files.put(data, media_type=media_type)
        await self._fault("blob.after_file_before_register")
        await initialize_workflow_db(self.database)
        db = await aiosqlite.connect(self.database)
        db.row_factory = aiosqlite.Row
        try:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            relative_path = self._files.path_for(ref.sha256).relative_to(self.root).as_posix()
            existing = await (
                await db.execute("SELECT * FROM workflow_blobs WHERE sha256=?", (ref.sha256,))
            ).fetchone()
            if existing is not None and (
                int(existing["size_bytes"]) != ref.size_bytes
                or str(existing["media_type"]) != ref.media_type
                or str(existing["relative_path"]) != relative_path
            ):
                raise ValueError(f"workflow blob metadata conflict: {ref.sha256}")
            await db.execute(
                """INSERT OR IGNORE INTO workflow_blobs(
                sha256,size_bytes,media_type,relative_path,created_at) VALUES(?,?,?,?,?)""",
                (ref.sha256, ref.size_bytes, ref.media_type, relative_path, self._clock()),
            )
            await db.execute(
                """INSERT OR IGNORE INTO workflow_blob_refs(
                sha256,owner_kind,owner_id,created_at) VALUES(?,'run_staging',?,?)""",
                (ref.sha256, execution_identity.run_id, self._clock()),
            )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()
        await self._fault("blob.after_register_before_return")
        return ref

    async def get(self, ref: BlobRef | str) -> bytes:
        digest = ref.sha256 if isinstance(ref, BlobRef) else str(ref)
        await initialize_workflow_db(self.database)
        db = await aiosqlite.connect(self.database)
        db.row_factory = aiosqlite.Row
        try:
            row = await (
                await db.execute("SELECT * FROM workflow_blobs WHERE sha256=?", (digest,))
            ).fetchone()
        finally:
            await db.close()
        if row is None:
            raise FileNotFoundError(f"workflow blob is not registered: {digest}")
        data = self._files.get(digest)
        if len(data) != int(row["size_bytes"]):
            raise ValueError(f"workflow blob size mismatch: {digest}")
        return data


__all__ = ["RegisteredBlobStore"]
