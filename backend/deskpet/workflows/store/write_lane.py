"""One serialized, long-lived SQLite writer for the execution database."""

from __future__ import annotations

import asyncio
import inspect
import os
import threading
import time
import uuid
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import aiosqlite


class ExecutionWriteLaneState(StrEnum):
    IDLE = "idle"
    BEGUN = "begun"
    COMMIT_STARTED = "commit_started"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    POISONED = "poisoned"
    CLOSED = "closed"


class ExecutionWriteLaneError(RuntimeError):
    def __init__(self, code: str, message: str, *, tx_id: str | None = None) -> None:
        self.code = code
        self.tx_id = tx_id
        super().__init__(message)


class ExecutionWriteCancelled(ExecutionWriteLaneError):
    def __init__(self, tx_id: str) -> None:
        super().__init__(
            "write_cancelled_before_commit",
            f"execution write {tx_id} was cancelled before commit",
            tx_id=tx_id,
        )


class ExecutionCommittedAfterCancel(ExecutionWriteLaneError):
    def __init__(self, tx_id: str, result_ref: str | None) -> None:
        self.result_ref = result_ref
        super().__init__(
            "committed_after_cancel",
            f"execution write {tx_id} committed after cancellation",
            tx_id=tx_id,
        )


class ExecutionWriteOutcomeUnknown(ExecutionWriteLaneError):
    def __init__(self, tx_id: str) -> None:
        super().__init__(
            "write_outcome_unknown",
            f"execution write {tx_id} has an unknown commit outcome",
            tx_id=tx_id,
        )


class ExecutionWriteLanePoisoned(ExecutionWriteLaneError):
    def __init__(self) -> None:
        super().__init__(
            "write_lane_poisoned",
            "execution write lane is poisoned and must be reconciled/reopened",
        )


@dataclass(frozen=True, slots=True)
class ExecutionWriteCommit:
    tx_id: str
    state: ExecutionWriteLaneState
    result_ref: str | None = None


_FaultInjector = Callable[[str], object | Awaitable[object]]
_PENDING_CLOSE_TASKS: set[asyncio.Task[None]] = set()


class _LaneConnection:
    """Connection façade that routes explicit commit/rollback through the lane."""

    def __init__(self, lane: "ExecutionWriteLane", db: aiosqlite.Connection) -> None:
        self._lane = lane
        self._db = db

    @property
    def in_transaction(self) -> bool:
        return self._db.in_transaction

    async def commit(self) -> ExecutionWriteCommit:
        return await self._lane._commit_current()

    async def rollback(self) -> None:
        await self._lane._rollback_current()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)


class ExecutionWriteLane:
    """Serialize all execution writes without merging transaction boundaries.

    A lane owns exactly one writer connection.  Every transaction still uses
    ``BEGIN IMMEDIATE`` and ``synchronous=FULL``.  Cancellation before commit
    rolls back; cancellation after commit starts cannot pretend the write did
    not happen.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        commit_deadline_seconds: float = 5.0,
        fault_injector: _FaultInjector | None = None,
    ) -> None:
        self.path = Path(path).resolve(strict=False)
        self._pid = os.getpid()
        self._commit_deadline_seconds = float(commit_deadline_seconds)
        if self._commit_deadline_seconds <= 0:
            raise ValueError("commit deadline must be positive")
        self._fault_injector = fault_injector
        self._lock = asyncio.Lock()
        self._open_lock = asyncio.Lock()
        self._db: aiosqlite.Connection | None = None
        self._state = ExecutionWriteLaneState.IDLE
        self._tx_id: str | None = None
        self._result_ref: str | None = None
        self._owner_count = 0

    @property
    def state(self) -> ExecutionWriteLaneState:
        return self._state

    @property
    def tx_id(self) -> str | None:
        return self._tx_id

    @property
    def poisoned(self) -> bool:
        return self._state is ExecutionWriteLaneState.POISONED

    def acquire_owner(self) -> None:
        """Keep the shared lane alive while one UoW can still use it."""

        self._owner_count += 1

    async def release_owner(self, *, timeout: float = 5.0) -> None:
        """Release one UoW owner and close only after the final owner leaves."""

        if self._owner_count <= 0:
            return
        self._owner_count -= 1
        if self._owner_count == 0:
            await self.close(timeout=timeout)

    def release_owner_nowait(self) -> None:
        """Best-effort interpreter/event-loop teardown fallback.

        UoWs should normally call ``close``.  A number of short-lived callers
        historically did not, so the final owner must still stop aiosqlite's
        worker before its event loop disappears.  ``Connection.stop`` is the
        library's own synchronous finalizer path; it does not pretend that a
        transaction committed and is used only when no UoW can still use this
        lane.
        """

        if self._owner_count <= 0:
            return
        self._owner_count -= 1
        if self._owner_count != 0:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            task = loop.create_task(self.close())
            _PENDING_CLOSE_TASKS.add(task)
            task.add_done_callback(_PENDING_CLOSE_TASKS.discard)
            return
        db, self._db = self._db, None
        if db is not None:
            # Interpreter teardown has no live loop to await. Run the library's
            # synchronous finalizer from a thread so it uses ``future is None``.
            stopper = threading.Thread(target=db.stop, daemon=True)
            stopper.start()
            stopper.join(timeout=1.0)
            worker = getattr(db, "_thread", None)
            if worker is not None:
                worker.join(timeout=1.0)
        if self._state is not ExecutionWriteLaneState.POISONED:
            self._state = ExecutionWriteLaneState.CLOSED

    async def _fault(self, point: str) -> None:
        if self._fault_injector is None:
            return
        result = self._fault_injector(point)
        if inspect.isawaitable(result):
            await result

    async def open(self) -> None:
        if os.getpid() != self._pid:
            raise ExecutionWriteLanePoisoned()
        if self._state is ExecutionWriteLaneState.POISONED:
            raise ExecutionWriteLanePoisoned()
        if self._db is not None:
            return
        async with self._open_lock:
            if self._db is not None:
                return
            db = await aiosqlite.connect(self.path)
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=FULL")
            await db.execute("PRAGMA busy_timeout=5000")
            self._db = db
            self._state = ExecutionWriteLaneState.IDLE

    @asynccontextmanager
    async def transaction(
        self, *, result_ref: str | None = None
    ) -> AsyncIterator[_LaneConnection]:
        await self.open()
        await self._lock.acquire()
        db = self._require_db()
        self._tx_id = uuid.uuid4().hex
        self._result_ref = result_ref
        try:
            await self._begin_current()
            proxy = _LaneConnection(self, db)
            try:
                yield proxy
            except asyncio.CancelledError as exc:
                if self._state is ExecutionWriteLaneState.BEGUN:
                    await self._rollback_current()
                    raise ExecutionWriteCancelled(self._require_tx_id()) from exc
                raise
            except BaseException:
                if self._state is ExecutionWriteLaneState.BEGUN:
                    await self._rollback_current()
                raise
            else:
                if self._state is ExecutionWriteLaneState.BEGUN:
                    await self._commit_current()
        finally:
            self._tx_id = None
            self._result_ref = None
            self._lock.release()

    async def _begin_current(self) -> None:
        """Begin or deterministically settle cancellation before yielding."""

        tx_id = self._require_tx_id()
        db = self._require_db()
        self._state = ExecutionWriteLaneState.IDLE

        async def begin_once() -> None:
            await self._fault("begin_started")
            await db.execute("BEGIN IMMEDIATE")
            await self._fault("begin_returned")

        task = asyncio.create_task(begin_once())
        try:
            async with asyncio.timeout(self._commit_deadline_seconds):
                await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            try:
                async with asyncio.timeout(self._commit_deadline_seconds):
                    await asyncio.shield(task)
            except (TimeoutError, asyncio.CancelledError) as wait_exc:
                await self._poison()
                raise ExecutionWriteOutcomeUnknown(tx_id) from wait_exc
            except BaseException:
                if db.in_transaction:
                    self._state = ExecutionWriteLaneState.BEGUN
                    await self._rollback_current()
                else:
                    self._state = ExecutionWriteLaneState.ROLLED_BACK
                raise ExecutionWriteCancelled(tx_id) from exc
            if db.in_transaction:
                self._state = ExecutionWriteLaneState.BEGUN
                await self._rollback_current()
            else:
                self._state = ExecutionWriteLaneState.ROLLED_BACK
            raise ExecutionWriteCancelled(tx_id) from exc
        except TimeoutError as exc:
            await self._poison()
            raise ExecutionWriteOutcomeUnknown(tx_id) from exc
        except BaseException:
            if db.in_transaction:
                self._state = ExecutionWriteLaneState.BEGUN
                await self._rollback_current()
            else:
                self._state = ExecutionWriteLaneState.IDLE
            raise
        self._state = ExecutionWriteLaneState.BEGUN

    async def _commit_current(self) -> ExecutionWriteCommit:
        tx_id = self._require_tx_id()
        if self._state is ExecutionWriteLaneState.COMMITTED:
            return ExecutionWriteCommit(
                tx_id, ExecutionWriteLaneState.COMMITTED, self._result_ref
            )
        if self._state is not ExecutionWriteLaneState.BEGUN:
            raise ExecutionWriteLaneError(
                "invalid_write_lane_state",
                f"cannot commit execution lane in {self._state.value}",
                tx_id=tx_id,
            )
        db = self._require_db()
        self._state = ExecutionWriteLaneState.COMMIT_STARTED

        async def commit_once() -> None:
            await self._fault("commit_started")
            await db.commit()
            await self._fault("commit_returned")

        task = asyncio.create_task(commit_once())
        try:
            async with asyncio.timeout(self._commit_deadline_seconds):
                await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            try:
                async with asyncio.timeout(self._commit_deadline_seconds):
                    await asyncio.shield(task)
            except (TimeoutError, asyncio.CancelledError) as wait_exc:
                await self._poison()
                raise ExecutionWriteOutcomeUnknown(tx_id) from wait_exc
            except BaseException as commit_exc:
                await self._settle_commit_failure(commit_exc)
                raise
            self._state = ExecutionWriteLaneState.COMMITTED
            raise ExecutionCommittedAfterCancel(tx_id, self._result_ref) from exc
        except TimeoutError as exc:
            await self._poison()
            raise ExecutionWriteOutcomeUnknown(tx_id) from exc
        except BaseException as exc:
            await self._settle_commit_failure(exc)
            raise
        self._state = ExecutionWriteLaneState.COMMITTED
        return ExecutionWriteCommit(
            tx_id, ExecutionWriteLaneState.COMMITTED, self._result_ref
        )

    async def _settle_commit_failure(self, exc: BaseException) -> None:
        db = self._db
        if db is not None and db.in_transaction:
            try:
                await db.rollback()
            except BaseException:
                await self._poison()
                raise ExecutionWriteOutcomeUnknown(self._require_tx_id()) from exc
            self._state = ExecutionWriteLaneState.ROLLED_BACK
            return
        await self._poison()
        raise ExecutionWriteOutcomeUnknown(self._require_tx_id()) from exc

    async def _rollback_current(self) -> None:
        if self._state is ExecutionWriteLaneState.ROLLED_BACK:
            return
        if self._state is not ExecutionWriteLaneState.BEGUN:
            raise ExecutionWriteLaneError(
                "invalid_write_lane_state",
                f"cannot rollback execution lane in {self._state.value}",
                tx_id=self._tx_id,
            )
        db = self._require_db()
        if db.in_transaction:
            await db.rollback()
        self._state = ExecutionWriteLaneState.ROLLED_BACK

    async def _poison(self) -> None:
        self._state = ExecutionWriteLaneState.POISONED
        db, self._db = self._db, None
        if db is not None:
            async def close_connection() -> None:
                try:
                    await db.close()
                except BaseException:
                    pass

            task = asyncio.create_task(close_connection())
            _PENDING_CLOSE_TASKS.add(task)
            task.add_done_callback(_PENDING_CLOSE_TASKS.discard)

    async def close(self, *, timeout: float = 5.0) -> None:
        try:
            async with asyncio.timeout(timeout):
                async with self._lock:
                    db, self._db = self._db, None
                    if db is not None:
                        if db.in_transaction:
                            await db.rollback()
                        await db.close()
                    if self._state is not ExecutionWriteLaneState.POISONED:
                        self._state = ExecutionWriteLaneState.CLOSED
        except TimeoutError:
            await self._poison()
            raise

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            if self._state is ExecutionWriteLaneState.POISONED:
                raise ExecutionWriteLanePoisoned()
            raise ExecutionWriteLaneError(
                "write_lane_closed", "execution write lane is not open"
            )
        return self._db

    def _require_tx_id(self) -> str:
        if self._tx_id is None:
            raise ExecutionWriteLaneError(
                "write_transaction_missing", "execution write transaction is not active"
            )
        return self._tx_id


_LANES: weakref.WeakValueDictionary[
    tuple[int, str], ExecutionWriteLane
] = weakref.WeakValueDictionary()


def get_execution_write_lane(path: str | Path) -> ExecutionWriteLane:
    """Return the process/path singleton used by Kernel, Driver and workers."""

    normalized = str(Path(path).resolve(strict=False)).casefold()
    key = (os.getpid(), normalized)
    lane = _LANES.get(key)
    if lane is None or lane.state in {
        ExecutionWriteLaneState.POISONED,
        ExecutionWriteLaneState.CLOSED,
    }:
        lane = ExecutionWriteLane(path)
        _LANES[key] = lane
    return lane


async def close_execution_write_lanes_for_tests() -> None:
    """Close process-global lanes after one isolated pytest case.

    Production owns and closes its UoW explicitly. Older unit tests often
    construct short-lived UoWs without a composition-root shutdown; keeping
    their resident aiosqlite workers past the test event loop causes leaked
    threads and order-dependent hangs. This test-only hook closes every lane
    while the fixture loop is still alive.
    """

    lanes = tuple(_LANES.values())
    _LANES.clear()
    for lane in lanes:
        lane._owner_count = 0
        await lane.close()
    pending = tuple(_PENDING_CLOSE_TASKS)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


__all__ = [
    "ExecutionCommittedAfterCancel",
    "ExecutionWriteCancelled",
    "ExecutionWriteCommit",
    "ExecutionWriteLane",
    "ExecutionWriteLaneError",
    "ExecutionWriteLanePoisoned",
    "ExecutionWriteLaneState",
    "ExecutionWriteOutcomeUnknown",
    "close_execution_write_lanes_for_tests",
    "get_execution_write_lane",
]
