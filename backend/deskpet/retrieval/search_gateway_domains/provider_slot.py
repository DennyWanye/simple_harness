"""Calling one search provider inside a concurrency slot and settling every outcome (rows, timeout, provider error, cancel)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace

from ..budget import QueryBudgetLease, SearchBudgetAllocator
from ..cache import CircuitPermit
from ..contracts import (
    AttemptStatus,
    ProviderAttempt,
    PublicErrorCode,
    RetrievalCandidate,
    SearchBudget,
    SearchRequest,
)
from ..providers.base import ProviderFailure

logger = logging.getLogger(__name__)


_FAILURE_STATUS = {
    PublicErrorCode.TIMEOUT: AttemptStatus.TIMEOUT,
    PublicErrorCode.BLOCKED: AttemptStatus.BLOCKED,
    PublicErrorCode.CAPTCHA: AttemptStatus.CAPTCHA,
    PublicErrorCode.RATE_LIMIT: AttemptStatus.RATE_LIMIT,
    PublicErrorCode.COOLDOWN: AttemptStatus.COOLDOWN,
    PublicErrorCode.HALF_OPEN_BUSY: AttemptStatus.HALF_OPEN_BUSY,
    PublicErrorCode.UNAVAILABLE: AttemptStatus.UNAVAILABLE,
    PublicErrorCode.BUDGET_EXHAUSTED: AttemptStatus.BUDGET_EXHAUSTED,
    PublicErrorCode.QUEUE_TIMEOUT: AttemptStatus.QUEUE_TIMEOUT,
}

_FAILURE_CLASS = {
    PublicErrorCode.TIMEOUT: "timeout",
    PublicErrorCode.BLOCKED: "blocked",
    PublicErrorCode.CAPTCHA: "captcha",
    PublicErrorCode.RATE_LIMIT: "rate_limit",
    PublicErrorCode.HTTP_ERROR: "http",
    PublicErrorCode.PARSE_ERROR: "invalid_response",
    PublicErrorCode.INVALID_RESPONSE: "invalid_response",
}


class ProviderSlotMixin:
    """Calling one search provider inside a concurrency slot and settling every outcome (rows, timeout, provider error, cancel)."""

    __slots__ = ()

    async def _run_provider_in_slot(
        self,
        name: str,
        request: SearchRequest,
        budget: SearchBudget,
        budget_lease: QueryBudgetLease,
        allocator: SearchBudgetAllocator,
        *,
        available: bool,
        allow_probe: bool,
        is_rescue: bool,
        expected_generation: int | None,
        started: float,
    ):
        provider = self.providers[name]
        health = self._health_for(request)
        if available:
            try:
                available = bool(await provider.is_available())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("run_provider_in_slot_bool_failed", exc_info=True)
                available = False
        if not available:
            permit = CircuitPermit("closed", 0)
            self._attempt_metric(
                request,
                provider=name,
                status="unavailable",
                elapsed_ms=0,
                permit=permit,
                error_code="unavailable",
                is_rescue=is_rescue,
            )
            return [], ProviderAttempt(
                name,
                AttemptStatus.UNAVAILABLE,
                0,
                public_error_code=PublicErrorCode.UNAVAILABLE,
                upstream_called=False,
                is_rescue=is_rescue,
            )

        before_permit = await health.snapshot(name)
        if expected_generation is not None and (
            before_permit.generation != expected_generation or before_permit.state == "closed"
        ):
            return self._reject_half_open_busy(
                before_permit, is_rescue=is_rescue, name=name, request=request, started=started
            )
        permit = await health.acquire_permit(name, allow_probe=allow_probe)
        if permit.kind == "open" and allow_probe and permit.wait_s > 0:
            if permit.wait_s < budget.remaining_s:
                margin = min(
                    0.001,
                    max(0.0, (budget.remaining_s - permit.wait_s) / 2),
                )
                await asyncio.sleep(permit.wait_s + margin)
                permit = await health.acquire_permit(name, allow_probe=True)
        if permit.kind != "closed":
            await self._circuit_metric(
                request,
                provider=name,
                permit=permit,
                before_state=before_permit.state,
                status=permit.kind,
                failure_class=permit.failure_class,
            )
        if permit.kind in {"open", "half_open_busy"}:
            busy = permit.kind == "half_open_busy"
            status = AttemptStatus.HALF_OPEN_BUSY if busy else AttemptStatus.COOLDOWN
            code = PublicErrorCode.HALF_OPEN_BUSY if busy else PublicErrorCode.COOLDOWN
            self._attempt_metric(
                request,
                provider=name,
                status=status.value,
                elapsed_ms=0,
                permit=permit,
                error_code=code.value,
                failure_class=permit.failure_class,
                is_rescue=is_rescue,
            )
            return [], ProviderAttempt(
                name,
                status,
                0,
                public_error_code=code,
                permit=permit.kind,
                circuit_generation=permit.generation,
                upstream_called=False,
                is_rescue=is_rescue,
            )
        return await self._call_provider_in_slot(
            allocator,
            budget=budget,
            budget_lease=budget_lease,
            health=health,
            is_rescue=is_rescue,
            name=name,
            permit=permit,
            provider=provider,
            request=request,
            started=started,
        )

    async def _call_provider_in_slot(
        self,
        allocator: SearchBudgetAllocator,
        *,
        budget: SearchBudget,
        budget_lease: QueryBudgetLease,
        health,
        is_rescue: bool,
        name: str,
        permit,
        provider,
        request: SearchRequest,
        started: float,
    ):
        timeout = min(budget.per_provider_timeout_s, budget.remaining_s)
        if timeout <= 0:
            return await self._finish_slot_cancelled(
                health, is_rescue=is_rescue, name=name, permit=permit, request=request
            )
        budget_attempt = await allocator.claim_provider_attempt(
            budget_lease,
            provider=name,
            generation=permit.generation,
            is_rescue=is_rescue,
        )
        if budget_attempt is None:
            if permit.probe_owner:
                await health.complete_probe(name, permit, "cancel")
            return [], ProviderAttempt(
                name,
                AttemptStatus.BUDGET_EXHAUSTED,
                int((time.monotonic() - started) * 1000),
                public_error_code=PublicErrorCode.BUDGET_EXHAUSTED,
                permit=permit.kind,
                circuit_generation=permit.generation,
                upstream_called=False,
                is_rescue=is_rescue,
            )

        async def commit_transport(
            rows: list[RetrievalCandidate], attempt: ProviderAttempt
        ) -> tuple[list[RetrievalCandidate], ProviderAttempt]:
            accepted = await allocator.commit_provider_attempt(
                budget_lease,
                budget_attempt.attempt_id,
                outcome=attempt.status.value,
                result_count=len(rows),
            )
            return rows[:accepted], replace(
                attempt,
                result_count=accepted,
                upstream_called=True,
                budget_attempt_id=budget_attempt.attempt_id,
            )

        try:
            rows = await asyncio.wait_for(
                provider.search(request, budget, self.client), timeout=timeout
            )
            return await self._finish_slot_rows(
                commit_transport,
                health=health,
                is_rescue=is_rescue,
                name=name,
                permit=permit,
                request=request,
                rows=rows,
                started=started,
            )
        except asyncio.CancelledError:
            if permit.probe_owner:
                await health.complete_probe(name, permit, "cancel")
                after_state = await self._circuit_metric(
                    request,
                    provider=name,
                    permit=permit,
                    before_state="half_open",
                    status="cancelled",
                    failure_class=permit.failure_class,
                    probe_outcome="cancel",
                )
                self._attempt_metric(
                    request,
                    provider=name,
                    status="cancelled",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    permit=permit,
                    probe_outcome="cancel",
                    failure_class=permit.failure_class,
                    transition=f"half_open_to_{after_state}",
                    upstream_called=True,
                    is_rescue=is_rescue,
                )
            else:
                self._attempt_metric(
                    request,
                    provider=name,
                    status="cancelled",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    permit=permit,
                    upstream_called=True,
                    is_rescue=is_rescue,
                )
            await asyncio.shield(
                allocator.commit_provider_attempt(
                    budget_lease,
                    budget_attempt.attempt_id,
                    outcome="cancelled",
                    result_count=0,
                )
            )
            raise
        except TimeoutError:
            return await self._finish_slot_timeout(
                commit_transport,
                health=health,
                is_rescue=is_rescue,
                name=name,
                permit=permit,
                request=request,
                started=started,
            )
        except ProviderFailure as exc:
            return await self._finish_slot_provider_error(
                commit_transport,
                exc=exc,
                health=health,
                is_rescue=is_rescue,
                name=name,
                permit=permit,
                request=request,
                started=started,
            )
        except Exception:
            logger.debug("call_provider_in_slot_wait_for_failed", exc_info=True)
            return await self._finish_slot_unexpected_error(
                commit_transport,
                health=health,
                is_rescue=is_rescue,
                name=name,
                permit=permit,
                request=request,
                started=started,
            )

    def _reject_half_open_busy(
        self, before_permit, *, is_rescue: bool, name: str, request: SearchRequest, started: float
    ):
        busy = before_permit.state == "half_open"
        status = AttemptStatus.HALF_OPEN_BUSY if busy else AttemptStatus.COOLDOWN
        code = PublicErrorCode.HALF_OPEN_BUSY if busy else PublicErrorCode.COOLDOWN
        permit = CircuitPermit(
            "half_open_busy" if busy else "open",
            before_permit.generation,
            failure_class=before_permit.failure_class,
        )
        self._attempt_metric(
            request,
            provider=name,
            status=status.value,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            permit=permit,
            error_code=code.value,
            failure_class=before_permit.failure_class,
            is_rescue=is_rescue,
        )
        return [], ProviderAttempt(
            name,
            status,
            int((time.monotonic() - started) * 1000),
            public_error_code=code,
            permit=permit.kind,
            circuit_generation=permit.generation,
            upstream_called=False,
            is_rescue=is_rescue,
        )

    async def _finish_slot_cancelled(
        self, health, *, is_rescue: bool, name: str, permit, request: SearchRequest
    ):
        if permit.probe_owner:
            await health.complete_probe(name, permit, "cancel")
            transition = await self._circuit_metric(
                request,
                provider=name,
                permit=permit,
                before_state="half_open",
                status="budget_exhausted",
                failure_class=permit.failure_class,
                probe_outcome="cancel",
            )
            self._attempt_metric(
                request,
                provider=name,
                status="budget_exhausted",
                elapsed_ms=0,
                permit=permit,
                error_code="budget_exhausted",
                probe_outcome="cancel",
                failure_class=permit.failure_class,
                transition=f"half_open_to_{transition}",
                is_rescue=is_rescue,
            )
        return [], ProviderAttempt(
            name,
            AttemptStatus.BUDGET_EXHAUSTED,
            0,
            public_error_code=PublicErrorCode.BUDGET_EXHAUSTED,
            permit=permit.kind,
            circuit_generation=permit.generation,
            upstream_called=False,
            is_rescue=is_rescue,
        )

    async def _finish_slot_rows(
        self,
        commit_transport,
        *,
        health,
        is_rescue: bool,
        name: str,
        permit,
        request: SearchRequest,
        rows,
        started: float,
    ):
        elapsed = int((time.monotonic() - started) * 1000)
        if rows:
            if permit.probe_owner:
                await health.complete_probe(name, permit, "hit")
                after_state = await self._circuit_metric(
                    request,
                    provider=name,
                    permit=permit,
                    before_state="half_open",
                    status="hit",
                    failure_class=permit.failure_class,
                    probe_outcome="hit",
                )
            else:
                await health.record_success(name, permit=permit)
                after_state = "closed"
            self._attempt_metric(
                request,
                provider=name,
                status="hit",
                elapsed_ms=elapsed,
                count=len(rows),
                permit=permit,
                probe_outcome="hit" if permit.probe_owner else None,
                failure_class=permit.failure_class,
                transition=f"half_open_to_{after_state}" if permit.probe_owner else None,
                upstream_called=True,
                is_rescue=is_rescue,
            )
            return await commit_transport(
                rows,
                ProviderAttempt(
                    name,
                    AttemptStatus.HIT,
                    elapsed,
                    len(rows),
                    permit=permit.kind,
                    circuit_generation=permit.generation,
                    probe_outcome="hit" if permit.probe_owner else None,
                    upstream_called=True,
                    is_rescue=is_rescue,
                ),
            )
        if permit.probe_owner:
            await health.complete_probe(name, permit, "empty")
            after_state = await self._circuit_metric(
                request,
                provider=name,
                permit=permit,
                before_state="half_open",
                status="empty",
                failure_class=permit.failure_class,
                probe_outcome="empty",
            )
        else:
            await health.record_success(name, permit=permit)
            after_state = "closed"
        self._attempt_metric(
            request,
            provider=name,
            status="empty",
            elapsed_ms=elapsed,
            permit=permit,
            probe_outcome="empty" if permit.probe_owner else None,
            failure_class=permit.failure_class,
            transition=f"half_open_to_{after_state}" if permit.probe_owner else None,
            upstream_called=True,
            is_rescue=is_rescue,
        )
        return await commit_transport(
            [],
            ProviderAttempt(
                name,
                AttemptStatus.EMPTY,
                elapsed,
                permit=permit.kind,
                circuit_generation=permit.generation,
                probe_outcome="empty" if permit.probe_owner else None,
                upstream_called=True,
                is_rescue=is_rescue,
            ),
        )

    async def _finish_slot_timeout(
        self,
        commit_transport,
        *,
        health,
        is_rescue: bool,
        name: str,
        permit,
        request: SearchRequest,
        started: float,
    ):
        if permit.probe_owner:
            await health.complete_probe(
                name,
                permit,
                "failure",
                failure_class="timeout",
            )
            after_state = await self._circuit_metric(
                request,
                provider=name,
                permit=permit,
                before_state="half_open",
                status="timeout",
                failure_class="timeout",
                probe_outcome="failure",
            )
        else:
            await health.record_failure(name, "timeout", permit=permit)
            after_state = await self._circuit_metric(
                request,
                provider=name,
                permit=permit,
                before_state="closed",
                status="timeout",
                failure_class="timeout",
            )
        elapsed = int((time.monotonic() - started) * 1000)
        self._attempt_metric(
            request,
            provider=name,
            status="timeout",
            elapsed_ms=elapsed,
            permit=permit,
            error_code="timeout",
            probe_outcome="failure" if permit.probe_owner else None,
            failure_class="timeout",
            transition=f"{'half_open' if permit.probe_owner else 'closed'}_to_{after_state}",
            upstream_called=True,
            is_rescue=is_rescue,
        )
        return await commit_transport(
            [],
            ProviderAttempt(
                name,
                AttemptStatus.TIMEOUT,
                elapsed,
                public_error_code=PublicErrorCode.TIMEOUT,
                permit=permit.kind,
                circuit_generation=permit.generation,
                probe_outcome="failure" if permit.probe_owner else None,
                upstream_called=True,
                is_rescue=is_rescue,
            ),
        )

    async def _finish_slot_provider_error(
        self,
        commit_transport,
        *,
        exc,
        health,
        is_rescue: bool,
        name: str,
        permit,
        request: SearchRequest,
        started: float,
    ):
        failure_class = _FAILURE_CLASS.get(exc.code)
        if failure_class is not None:
            if permit.probe_owner:
                await health.complete_probe(
                    name,
                    permit,
                    "failure",
                    failure_class=failure_class,
                    retry_after_s=exc.retry_after_s,
                )
                before_state = "half_open"
            else:
                await health.record_failure(
                    name,
                    failure_class,
                    retry_after_s=exc.retry_after_s,
                    permit=permit,
                )
                before_state = "closed"
            after_state = await self._circuit_metric(
                request,
                provider=name,
                permit=permit,
                before_state=before_state,
                status=_FAILURE_STATUS.get(exc.code, AttemptStatus.ERROR).value,
                failure_class=failure_class,
                probe_outcome="failure" if permit.probe_owner else None,
            )
        elif permit.probe_owner:
            await health.complete_probe(name, permit, "cancel")
            before_state = "half_open"
            after_state = await self._circuit_metric(
                request,
                provider=name,
                permit=permit,
                before_state=before_state,
                status=_FAILURE_STATUS.get(exc.code, AttemptStatus.ERROR).value,
                failure_class=permit.failure_class,
                probe_outcome="cancel",
            )
        else:
            before_state = "closed"
            after_state = "closed"
        elapsed = int((time.monotonic() - started) * 1000)
        status = _FAILURE_STATUS.get(exc.code, AttemptStatus.ERROR)
        upstream_called = True
        self._attempt_metric(
            request,
            provider=name,
            status=status.value,
            elapsed_ms=elapsed,
            permit=permit,
            error_code=exc.code.value,
            probe_outcome="failure" if permit.probe_owner and failure_class else None,
            failure_class=failure_class or permit.failure_class,
            transition=f"{before_state}_to_{after_state}"
            if failure_class or permit.probe_owner
            else None,
            upstream_called=upstream_called,
            is_rescue=is_rescue,
        )
        return await commit_transport(
            [],
            ProviderAttempt(
                name,
                status,
                elapsed,
                public_error_code=exc.code,
                permit=permit.kind,
                circuit_generation=permit.generation,
                probe_outcome="failure" if permit.probe_owner and failure_class else None,
                upstream_called=upstream_called,
                is_rescue=is_rescue,
            ),
        )

    async def _finish_slot_unexpected_error(
        self,
        commit_transport,
        *,
        health,
        is_rescue: bool,
        name: str,
        permit,
        request: SearchRequest,
        started: float,
    ):
        if permit.probe_owner:
            await health.complete_probe(
                name,
                permit,
                "failure",
                failure_class="http",
            )
            before_state = "half_open"
        else:
            await health.record_failure(name, "http", permit=permit)
            before_state = "closed"
        after_state = await self._circuit_metric(
            request,
            provider=name,
            permit=permit,
            before_state=before_state,
            status="error",
            failure_class="http",
            probe_outcome="failure" if permit.probe_owner else None,
        )
        elapsed = int((time.monotonic() - started) * 1000)
        self._attempt_metric(
            request,
            provider=name,
            status="error",
            elapsed_ms=elapsed,
            permit=permit,
            error_code="http_error",
            probe_outcome="failure" if permit.probe_owner else None,
            failure_class="http",
            transition=f"{before_state}_to_{after_state}",
            upstream_called=True,
            is_rescue=is_rescue,
        )
        return await commit_transport(
            [],
            ProviderAttempt(
                name,
                AttemptStatus.ERROR,
                elapsed,
                public_error_code=PublicErrorCode.HTTP_ERROR,
                permit=permit.kind,
                circuit_generation=permit.generation,
                probe_outcome="failure" if permit.probe_owner else None,
                upstream_called=True,
                is_rescue=is_rescue,
            ),
        )
