"""Fenced workflow leases and the durable run-state transition guard."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Iterable, TypeVar

from .contracts import TERMINAL_RUN_STATUSES, WorkflowRunStatus
from .errors import LeaseLostError
from .store import RunFence, StaleRunFence, WorkflowRunStore


HEARTBEAT_INTERVAL_SECONDS = 15.0
LEASE_TTL_SECONDS = 90.0

_T = TypeVar("_T")


LEGAL_RUN_TRANSITIONS: dict[WorkflowRunStatus, frozenset[WorkflowRunStatus]] = {
    WorkflowRunStatus.CREATED: frozenset(
        {WorkflowRunStatus.RUNNING, WorkflowRunStatus.CANCEL_REQUESTED, WorkflowRunStatus.BLOCKED}
    ),
    WorkflowRunStatus.RUNNING: frozenset(
        {
            WorkflowRunStatus.WAITING,
            WorkflowRunStatus.RETRYABLE,
            WorkflowRunStatus.CANCEL_REQUESTED,
            WorkflowRunStatus.CANCELLING,
            WorkflowRunStatus.BLOCKED,
            WorkflowRunStatus.COMPLETED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
        }
    ),
    WorkflowRunStatus.WAITING: frozenset(
        {WorkflowRunStatus.RETRYABLE, WorkflowRunStatus.CANCEL_REQUESTED, WorkflowRunStatus.BLOCKED}
    ),
    WorkflowRunStatus.RETRYABLE: frozenset(
        {WorkflowRunStatus.RUNNING, WorkflowRunStatus.CANCEL_REQUESTED, WorkflowRunStatus.BLOCKED}
    ),
    WorkflowRunStatus.CANCEL_REQUESTED: frozenset(
        {WorkflowRunStatus.CANCELLING, WorkflowRunStatus.BLOCKED, WorkflowRunStatus.CANCELLED}
    ),
    WorkflowRunStatus.CANCELLING: frozenset(
        {WorkflowRunStatus.BLOCKED, WorkflowRunStatus.CANCELLED}
    ),
    WorkflowRunStatus.BLOCKED: frozenset(
        {WorkflowRunStatus.RETRYABLE, WorkflowRunStatus.CANCEL_REQUESTED, WorkflowRunStatus.CANCELLED}
    ),
    WorkflowRunStatus.COMPLETED: frozenset(),
    WorkflowRunStatus.FAILED: frozenset(),
    WorkflowRunStatus.CANCELLED: frozenset(),
}


def validate_run_transition(source: str | WorkflowRunStatus, target: str | WorkflowRunStatus) -> None:
    source_status = WorkflowRunStatus(source)
    target_status = WorkflowRunStatus(target)
    if target_status not in LEGAL_RUN_TRANSITIONS[source_status]:
        raise ValueError(f"illegal workflow run transition: {source_status.value} -> {target_status.value}")


@dataclass(frozen=True, slots=True)
class ActiveLease:
    fence: RunFence
    heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS
    ttl_seconds: float = LEASE_TTL_SECONDS

    @property
    def configurable(self) -> dict[str, str | int]:
        return {
            "deskpet_lease_owner": self.fence.owner,
            "deskpet_lease_epoch": self.fence.lease_epoch,
            "deskpet_run_version": self.fence.run_version,
        }


class LeaseManager:
    """Claims leases and keeps them alive independently of graph execution."""

    def __init__(
        self,
        store: WorkflowRunStore,
        *,
        owner: str,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
        ttl_seconds: float = LEASE_TTL_SECONDS,
        sleep=asyncio.sleep,
    ) -> None:
        if heartbeat_interval <= 0 or ttl_seconds <= 0:
            raise ValueError("lease timing values must be positive")
        if heartbeat_interval >= ttl_seconds:
            raise ValueError("heartbeat interval must be shorter than the lease TTL")
        self.store = store
        self.owner = owner
        self.heartbeat_interval = heartbeat_interval
        self.ttl_seconds = ttl_seconds
        self._sleep = sleep

    async def claim(
        self,
        run_id: str,
        *,
        allowed_statuses: Iterable[str] = (
            WorkflowRunStatus.CREATED.value,
            WorkflowRunStatus.RETRYABLE.value,
            WorkflowRunStatus.RUNNING.value,
        ),
    ) -> ActiveLease:
        fence = await self.store.claim(
            run_id,
            self.owner,
            ttl_seconds=self.ttl_seconds,
            allowed_statuses=allowed_statuses,
        )
        return ActiveLease(fence, self.heartbeat_interval, self.ttl_seconds)

    async def run_with_heartbeat(self, lease: ActiveLease, awaitable: Awaitable[_T]) -> _T:
        work = asyncio.ensure_future(awaitable)
        heartbeat = asyncio.create_task(self._heartbeat_loop(lease), name=f"workflow-heartbeat:{lease.fence.run_id}")
        try:
            done, _ = await asyncio.wait({work, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                error = heartbeat.exception()
                if error is not None:
                    if not work.done():
                        work.cancel()
                    try:
                        await work
                    except asyncio.CancelledError:
                        pass
                    raise error
            if work in done:
                return await work
            if not work.done():
                work.cancel()
            try:
                await work
            except asyncio.CancelledError:
                pass
            raise LeaseLostError(f"workflow lease stopped: {lease.fence.run_id}")
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    async def _heartbeat_loop(self, lease: ActiveLease) -> None:
        transient_failures = 0
        while True:
            await self._sleep(lease.heartbeat_interval)
            try:
                await self.store.heartbeat(lease.fence, ttl_seconds=lease.ttl_seconds)
                transient_failures = 0
            except StaleRunFence as exc:
                raise LeaseLostError(f"workflow lease fence changed: {lease.fence.run_id}") from exc
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                transient_failures += 1
                if transient_failures >= 2:
                    raise LeaseLostError(
                        f"workflow lease heartbeat failed twice: {lease.fence.run_id}"
                    ) from exc


async def transition_run(
    store: WorkflowRunStore,
    run_id: str,
    target: str | WorkflowRunStatus,
    *,
    fence: RunFence | None = None,
    expected_version: int | None = None,
    allowed_statuses: Iterable[str | WorkflowRunStatus],
    error: dict[str, Any] | None = None,
    recovery_action: str | None = None,
    clock=time.time,
) -> dict[str, Any]:
    """Apply one legal state transition with either a live fence or a CAS version."""

    target_status = WorkflowRunStatus(target)
    statuses = tuple(WorkflowRunStatus(item) for item in allowed_statuses)
    if not statuses:
        raise ValueError("a transition requires at least one allowed source status")
    for source in statuses:
        validate_run_transition(source, target_status)
    if (fence is None) == (expected_version is None):
        raise ValueError("provide exactly one of fence or expected_version")

    now = clock()
    placeholders = ",".join("?" for _ in statuses)
    terminal = target_status in TERMINAL_RUN_STATUSES
    assignments = [
        "status=?",
        "lease_owner=NULL",
        "lease_expires_at=NULL",
        "heartbeat_at=NULL",
        "run_version=run_version+1",
        "error_json=?",
        "recovery_action=?",
        "updated_at=?",
    ]
    params: list[Any] = [
        target_status.value,
        json.dumps(error, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if error else None,
        recovery_action,
        now,
    ]
    if terminal:
        assignments.append("ended_at=COALESCE(ended_at,?)")
        params.append(now)
    where = [f"run_id=?", f"status IN ({placeholders})"]
    params.extend([run_id, *(status.value for status in statuses)])
    if fence is not None:
        where.extend(["lease_owner=?", "lease_epoch=?", "run_version=?"])
        params.extend([fence.owner, fence.lease_epoch, fence.run_version])
    else:
        where.append("run_version=?")
        params.append(expected_version)

    db = await store._connect()
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (
            await db.execute(
                f"UPDATE workflow_runs SET {','.join(assignments)} WHERE {' AND '.join(where)} RETURNING *",
                params,
            )
        ).fetchone()
        if row is None:
            await db.rollback()
            raise StaleRunFence(f"stale or illegal run transition: {run_id} -> {target_status.value}")
        await db.commit()
        return dict(row)
    except BaseException:
        if db.in_transaction:
            await db.rollback()
        raise
    finally:
        await db.close()


__all__ = [
    "ActiveLease",
    "HEARTBEAT_INTERVAL_SECONDS",
    "LEASE_TTL_SECONDS",
    "LEGAL_RUN_TRANSITIONS",
    "LeaseManager",
    "transition_run",
    "validate_run_transition",
]
