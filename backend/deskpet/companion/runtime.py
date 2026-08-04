"""Single-scheduler lifecycle for durable Companion jobs."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

from .clock import ClockPort, SystemClock
from .contracts import LeaseClaim, OwnerRef
from .identity_gate import CompanionIdentityNotReady, IdentityReadyGate


logger = logging.getLogger(__name__)


class ForegroundExecutionStatePort(Protocol):
    async def list_recoverable(
        self, *, limit: int = 10_000, run_ids: Sequence[str] = ()
    ) -> Sequence[Any]: ...


@dataclass(frozen=True, slots=True)
class ForegroundActivity:
    busy: bool
    active_runs: int
    idle_for_seconds: float
    reason_code: str


class ForegroundActivityGate:
    """Recompute foreground busy state from the durable execution ledger.

    Task 5 deliberately uses a consistent read before every claim rather than
    pretending an event subscription exists.  Read errors fail closed.
    """

    def __init__(
        self,
        execution_state: ForegroundExecutionStatePort | None,
        *,
        clock: ClockPort | None = None,
    ) -> None:
        self._execution_state = execution_state
        self._clock = clock or SystemClock()
        self._last_busy = self._clock.monotonic()

    async def snapshot(self) -> ForegroundActivity:
        if self._execution_state is None:
            return ForegroundActivity(True, 0, 0.0, "execution_cursor_unavailable")
        try:
            records = await self._execution_state.list_recoverable()
        except Exception:
            return ForegroundActivity(True, 0, 0.0, "execution_state_unavailable")
        foreground_roots: set[str] = set()
        for record in records:
            spec = getattr(record, "spec", None)
            context = getattr(spec, "context", None)
            venue = str(getattr(context, "venue", "") or "")
            root_run_id = str(getattr(context, "root_run_id", "") or "")
            if not venue or not root_run_id:
                return ForegroundActivity(
                    True, 0, 0.0, "execution_projection_incomplete"
                )
            if venue != "background":
                foreground_roots.add(root_run_id)
        foreground = len(foreground_roots)
        now = self._clock.monotonic()
        if foreground:
            self._last_busy = now
            return ForegroundActivity(True, foreground, 0.0, "foreground_run_active")
        return ForegroundActivity(
            False,
            0,
            max(0.0, now - self._last_busy),
            "foreground_idle",
        )


@dataclass(frozen=True, slots=True)
class CompanionJobResult:
    status: str = "succeeded"
    result_ref: str | None = None
    result_hash: str | None = None
    reason_code: str = "job_completed"
    budget_actual_tokens: int = 0
    budget_actual_ms: int = 0
    failure_context: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "failed", "waiting_decision"}:
            raise ValueError("companion_job_result_status_invalid")
        if min(self.budget_actual_tokens, self.budget_actual_ms) < 0:
            raise ValueError("companion_job_result_budget_invalid")
        if self.failure_context is not None:
            if self.status != "failed":
                raise ValueError(
                    "companion_job_failure_context_requires_failed_status"
                )
            if not isinstance(self.failure_context, Mapping):
                raise ValueError("companion_job_failure_context_invalid")


class CompanionJobHandler(Protocol):
    def __call__(
        self, owner: OwnerRef, claim: LeaseClaim
    ) -> Awaitable[CompanionJobResult]: ...


@dataclass(frozen=True, slots=True)
class CompanionRuntimePolicy:
    enabled: bool = True
    paused: bool = False
    poll_interval_seconds: float = 1.0
    lease_seconds: float = 30.0
    shutdown_timeout_seconds: float = 5.0
    idle_window_seconds: float = 30.0
    retry_delay_seconds: float = 5.0
    max_children: int = 2
    max_retries: int = 3
    max_job_tokens: int = 32_000
    max_job_ms: int = 900_000
    daily_token_budget: int = 64_000
    daily_time_budget_ms: int = 1_800_000
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "08:00"
    timezone: str = "Asia/Shanghai"
    claim_kinds: tuple[str, ...] = ("reflection",)
    retryable_kinds: tuple[str, ...] = ("reflection",)
    expire_on_recovery_kinds: tuple[str, ...] = (
        "reminder",
        "reminder_occurrence",
    )

    def __post_init__(self) -> None:
        if (
            self.poll_interval_seconds <= 0
            or self.lease_seconds <= 0
            or self.shutdown_timeout_seconds <= 0
            or self.idle_window_seconds < 0
            or self.retry_delay_seconds < 0
            or self.max_children < 1
            or self.max_retries < 1
            or self.max_job_tokens < 0
            or self.max_job_ms < 0
            or self.daily_token_budget < 0
            or self.daily_time_budget_ms < 0
        ):
            raise ValueError("companion_runtime_policy_invalid")
        _parse_local_time(self.quiet_hours_start)
        _parse_local_time(self.quiet_hours_end)
        ZoneInfo(self.timezone)


def _parse_local_time(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("companion_quiet_hour_invalid") from exc
    if parsed.tzinfo is not None:
        raise ValueError("companion_quiet_hour_must_be_local")
    return parsed


class CompanionRuntime:
    """Idempotent runtime with one scheduler and bounded job children."""

    def __init__(
        self,
        *,
        store: Any,
        identity_gate: IdentityReadyGate,
        foreground_gate: ForegroundActivityGate,
        handler: CompanionJobHandler,
        clock: ClockPort | None = None,
        policy: CompanionRuntimePolicy | None = None,
        authority_active: Callable[[], bool] | None = None,
        claim_owner: str | None = None,
        scheduled_work_hook: Callable[
            [OwnerRef], Awaitable[None] | None
        ]
        | None = None,
    ) -> None:
        self.store = store
        self.identity_gate = identity_gate
        self.foreground_gate = foreground_gate
        self.handler = handler
        self.clock = clock or SystemClock()
        self.policy = policy or CompanionRuntimePolicy()
        self.authority_active = authority_active or (lambda: False)
        self.claim_owner = claim_owner or f"companion:{os.getpid()}:{id(self):x}"
        self.scheduled_work_hook = scheduled_work_hook
        self._scheduler: asyncio.Task[None] | None = None
        self._children: set[asyncio.Task[None]] = set()
        self._child_claims: dict[asyncio.Task[None], tuple[OwnerRef, LeaseClaim]] = {}
        self._owner: OwnerRef | None = None
        self._recovered: set[OwnerRef] = set()
        self._accepting = False
        self._closed = False
        self._lifecycle_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._last_recovery: Mapping[str, int] = {
            "recovered": 0,
            "expired": 0,
            "failed": 0,
        }

    def __deepcopy__(self, _memo):
        return self

    async def recover(self, owner: OwnerRef) -> Mapping[str, int]:
        async with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("companion_runtime_closed")
            result = self.store.recover_expired_job_leases(
                owner,
                max_attempts=self.policy.max_retries,
                retryable_kinds=self.policy.retryable_kinds,
                expire_kinds=self.policy.expire_on_recovery_kinds,
            )
            self._recovered.add(owner)
            self._last_recovery = dict(result)
            return dict(result)

    async def start(self, owner: OwnerRef) -> bool:
        return await self._start(owner, binding_epoch=None)

    async def start_prebound(
        self,
        owner: OwnerRef,
        *,
        binding_epoch: int,
    ) -> bool:
        """Start for the exact durable binding before IdentityReadyGate opens.

        ``ProfileBindingCoordinator`` has already projected the owner and
        written the binding at this point.  The scheduler may start, but chat
        ingress stays closed until the coordinator marks the binding ready and
        binds ``IdentityReadyGate``.
        """

        return await self._start(owner, binding_epoch=binding_epoch)

    async def _start(
        self,
        owner: OwnerRef,
        *,
        binding_epoch: int | None,
    ) -> bool:
        async with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("companion_runtime_closed")
            if not self.policy.enabled or self.policy.paused or not self.authority_active():
                return False
            if binding_epoch is None:
                current = self.identity_gate.freeze()
                if current.owner != owner:
                    raise CompanionIdentityNotReady(
                        "companion_identity_not_ready"
                    )
            else:
                binding = self.store.get_profile_binding(
                    device_scope="desktop"
                )
                if (
                    binding is None
                    or str(binding["profile_id"]) != owner.profile_id
                    or int(binding["profile_generation"])
                    != owner.profile_generation
                    or int(binding["binding_epoch"]) != binding_epoch
                    or str(binding["status"]) not in {"unready", "ready"}
                ):
                    raise CompanionIdentityNotReady(
                        "companion_identity_not_ready"
                    )
            if owner not in self._recovered:
                result = self.store.recover_expired_job_leases(
                    owner,
                    max_attempts=self.policy.max_retries,
                    retryable_kinds=self.policy.retryable_kinds,
                    expire_kinds=self.policy.expire_on_recovery_kinds,
                )
                self._recovered.add(owner)
                self._last_recovery = dict(result)
            if self._scheduler is not None and not self._scheduler.done():
                if self._owner != owner:
                    raise RuntimeError("companion_runtime_owner_conflict")
                return True
            self._owner = owner
            self._accepting = True
            self._wake.clear()
            self._scheduler = asyncio.create_task(
                self._scheduler_loop(owner),
                name=f"companion-scheduler:{owner.profile_id}:{owner.profile_generation}",
            )
            return True

    async def switch(self, owner: OwnerRef, *, timeout: float | None = None) -> bool:
        await self.pause(timeout=timeout)
        await self.recover(owner)
        return await self.start(owner)

    async def pause(self, *, timeout: float | None = None) -> None:
        async with self._lifecycle_lock:
            self._accepting = False
            self._wake.set()
            scheduler = self._scheduler
            self._scheduler = None
            if scheduler is not None and not scheduler.done():
                scheduler.cancel()
        if scheduler is not None:
            await asyncio.gather(scheduler, return_exceptions=True)
        children = tuple(self._children)
        if children:
            limit = self.policy.shutdown_timeout_seconds if timeout is None else timeout
            done, pending = await asyncio.wait(children, timeout=max(0.0, limit))
            del done
            for child in pending:
                child.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self._owner = None
        if self._children:
            raise RuntimeError("companion_runtime_children_survived_pause")

    async def close(self, *, timeout: float | None = None) -> None:
        if self._closed:
            return
        await self.pause(timeout=timeout)
        self._closed = True

    def wake(self) -> None:
        self._wake.set()

    def diagnostics(self) -> Mapping[str, Any]:
        scheduler_count = int(
            self._scheduler is not None and not self._scheduler.done()
        )
        return {
            "closed": self._closed,
            "accepting": self._accepting,
            "owner": (
                None
                if self._owner is None
                else {
                    "profile_id": self._owner.profile_id,
                    "profile_generation": self._owner.profile_generation,
                }
            ),
            "scheduler_count": scheduler_count,
            "child_count": len(self._children),
            "last_recovery": dict(self._last_recovery),
        }

    async def _scheduler_loop(self, owner: OwnerRef) -> None:
        try:
            while self._accepting and self._owner == owner:
                self._wake.clear()
                await self._schedule_once(owner)
                sleep_task = asyncio.create_task(
                    self.clock.sleep(self.policy.poll_interval_seconds),
                    name="companion-scheduler-sleep",
                )
                wake_task = asyncio.create_task(
                    self._wake.wait(),
                    name="companion-scheduler-wake",
                )
                try:
                    _, pending = await asyncio.wait(
                        (sleep_task, wake_task),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    pending = {
                        task
                        for task in (sleep_task, wake_task)
                        if not task.done()
                    }
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
        except asyncio.CancelledError:
            raise

    async def _schedule_once(self, owner: OwnerRef) -> None:
        if len(self._children) >= self.policy.max_children:
            return
        try:
            current = self.identity_gate.freeze()
        except CompanionIdentityNotReady:
            return
        if (
            current.owner != owner
            or not self.authority_active()
            or self.policy.paused
        ):
            return
        if self.scheduled_work_hook is not None:
            try:
                hooked = self.scheduled_work_hook(owner)
                if inspect.isawaitable(hooked):
                    await hooked
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "companion_scheduled_work_failed owner=%s:%d error=%s",
                    owner.profile_id,
                    owner.profile_generation,
                    type(exc).__name__,
                )
        activity = await self.foreground_gate.snapshot()
        if activity.busy or activity.idle_for_seconds < self.policy.idle_window_seconds:
            return
        day_start = self._local_day_start_utc()
        claim = self.store.claim_next_job_with_budget(
            owner,
            claim_owner=self.claim_owner,
            lease_seconds=self.policy.lease_seconds,
            budget_window_start=day_start,
            token_budget=self.policy.daily_token_budget,
            time_budget_ms=self.policy.daily_time_budget_ms,
            kinds=self.policy.claim_kinds,
            max_attempts=self.policy.max_retries,
            max_job_tokens=self.policy.max_job_tokens,
            max_job_ms=self.policy.max_job_ms,
        )
        if claim is None:
            return
        payload = dict(claim.payload)
        if self._in_quiet_hours() and not bool(
            payload.get("allow_during_quiet_hours", False)
        ):
            self.store.defer_job(
                owner,
                job_id=claim.item_id,
                claim_owner=claim.claim_owner,
                claim_epoch=claim.claim_epoch,
                retry_at=self._next_quiet_end_utc(),
                reason_code="job_deferred_quiet_hours",
            )
            return
        if bool(payload.get("requires_idle", True)):
            activity = await self.foreground_gate.snapshot()
            if (
                activity.busy
                or activity.idle_for_seconds < self.policy.idle_window_seconds
            ):
                self.store.defer_job(
                    owner,
                    job_id=claim.item_id,
                    claim_owner=claim.claim_owner,
                    claim_epoch=claim.claim_epoch,
                    retry_at=self.clock.now_utc()
                    + timedelta(seconds=self.policy.poll_interval_seconds),
                    reason_code="job_deferred_foreground_busy",
                )
                return
        child = asyncio.create_task(
            self._run_claim(owner, claim),
            name=f"companion-job:{claim.item_id}:{claim.claim_epoch}",
        )
        self._children.add(child)
        self._child_claims[child] = (owner, claim)
        child.add_done_callback(self._child_done)

    def _child_done(self, task: asyncio.Task[None]) -> None:
        self._children.discard(task)
        self._child_claims.pop(task, None)
        self._wake.set()

    async def _run_claim(self, owner: OwnerRef, claim: LeaseClaim) -> None:
        started = self.clock.monotonic()
        try:
            result = self.handler(owner, claim)
            if inspect.isawaitable(result):
                row = self.store.get_job(owner, job_id=claim.item_id)
                reserved_ms = int(row["budget_reserved_ms"])
                timeout_seconds = (
                    reserved_ms if reserved_ms > 0 else self.policy.max_job_ms
                ) / 1000.0
                result = await asyncio.wait_for(result, timeout=timeout_seconds)
            if not isinstance(result, CompanionJobResult):
                raise TypeError("companion_job_handler_result_invalid")
            if result.status == "waiting_decision":
                row = self.store.get_job(owner, job_id=claim.item_id)
                if (
                    row["status"] != "waiting_decision"
                    or row["claim_owner"] is not None
                    or row["lease_expires_at"] is not None
                ):
                    raise RuntimeError(
                        "companion_waiting_decision_not_durable"
                    )
                return
            if (
                result.status == "failed"
                and claim.attempt < self.policy.max_retries
            ):
                self.store.defer_job_for_replan(
                    owner,
                    job_id=claim.item_id,
                    claim_owner=claim.claim_owner,
                    claim_epoch=claim.claim_epoch,
                    # A model-declared terminal failure already has complete
                    # durable evidence. Replanning can start on the next
                    # scheduler pass; this also keeps the immutable DEV clock
                    # seam from stranding the retry in its own future.
                    retry_at=self.clock.now_utc(),
                    failure_context=(
                        dict(result.failure_context)
                        if result.failure_context is not None
                        else {
                            "reason_code": result.reason_code,
                            "result_ref": result.result_ref,
                            "result_hash": result.result_hash,
                        }
                    ),
                    reason_code="job_replan_after_model_failure",
                )
                return
            self.store.settle_job(
                owner,
                job_id=claim.item_id,
                claim_owner=claim.claim_owner,
                claim_epoch=claim.claim_epoch,
                status=result.status,
                result_ref=result.result_ref,
                result_hash=result.result_hash,
                reason_code=result.reason_code,
                budget_actual_tokens=result.budget_actual_tokens,
                budget_actual_ms=max(
                    result.budget_actual_ms,
                    int((self.clock.monotonic() - started) * 1000),
                ),
            )
        except asyncio.CancelledError:
            row = self.store.get_job(owner, job_id=claim.item_id)
            if row["status"] != "waiting_decision":
                self._defer_or_cancel(owner, claim, reason_code="runtime_paused")
            raise
        except Exception as exc:
            logger.exception(
                "companion_job_attempt_failed job_id=%s kind=%s attempt=%d "
                "claim_epoch=%d error_type=%s",
                claim.item_id,
                str(claim.payload.get("purpose") or ""),
                claim.attempt,
                claim.claim_epoch,
                type(exc).__name__,
            )
            if claim.attempt < self.policy.max_retries:
                error_message = str(exc).strip()
                self.store.defer_job_for_replan(
                    owner,
                    job_id=claim.item_id,
                    claim_owner=claim.claim_owner,
                    claim_epoch=claim.claim_epoch,
                    # Failure evidence is durable model input, so a retry is
                    # a fresh model plan rather than a blind delayed replay.
                    # Retrying at "now" is also required by immutable DEV
                    # clocks used for deterministic recovery tests.
                    retry_at=self.clock.now_utc(),
                    failure_context={
                        "error_type": type(exc).__name__,
                        "error_message": (
                            error_message[:2000]
                            if error_message
                            else type(exc).__name__
                        ),
                        "job_kind": str(
                            claim.payload.get("purpose") or ""
                        ),
                        "reason_code": "background_run_exception",
                    },
                    reason_code="job_replan_after_runtime_failure",
                )
            else:
                self.store.settle_job(
                    owner,
                    job_id=claim.item_id,
                    claim_owner=claim.claim_owner,
                    claim_epoch=claim.claim_epoch,
                    status="failed",
                    result_ref=None,
                    result_hash=None,
                    reason_code=f"job_failed:{type(exc).__name__}",
                    budget_actual_ms=max(
                        0, int((self.clock.monotonic() - started) * 1000)
                    ),
                )

    def _defer_or_cancel(
        self, owner: OwnerRef, claim: LeaseClaim, *, reason_code: str
    ) -> None:
        if claim.attempt < self.policy.max_retries:
            self.store.defer_job(
                owner,
                job_id=claim.item_id,
                claim_owner=claim.claim_owner,
                claim_epoch=claim.claim_epoch,
                retry_at=self.clock.now_utc()
                + timedelta(seconds=self.policy.retry_delay_seconds),
                reason_code=reason_code,
            )
        else:
            self.store.cancel_job(
                owner,
                job_id=claim.item_id,
                claim_owner=claim.claim_owner,
                claim_epoch=claim.claim_epoch,
                reason_code=reason_code,
            )

    def _local_now(self) -> datetime:
        return self.clock.now_utc().astimezone(ZoneInfo(self.policy.timezone))

    def _in_quiet_hours(self) -> bool:
        local_now = self._local_now().timetz().replace(tzinfo=None)
        start = _parse_local_time(self.policy.quiet_hours_start)
        end = _parse_local_time(self.policy.quiet_hours_end)
        if start == end:
            return False
        if start < end:
            return start <= local_now < end
        return local_now >= start or local_now < end

    def _next_quiet_end_utc(self) -> datetime:
        local_now = self._local_now()
        end = _parse_local_time(self.policy.quiet_hours_end)
        candidate = datetime.combine(local_now.date(), end, local_now.tzinfo)
        if candidate <= local_now:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)

    def _local_day_start_utc(self) -> str:
        local_now = self._local_now()
        start = datetime.combine(local_now.date(), time.min, local_now.tzinfo)
        return start.astimezone(UTC).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")


__all__ = [
    "CompanionJobHandler",
    "CompanionJobResult",
    "CompanionRuntime",
    "CompanionRuntimePolicy",
    "ForegroundActivity",
    "ForegroundActivityGate",
    "ForegroundExecutionStatePort",
]
