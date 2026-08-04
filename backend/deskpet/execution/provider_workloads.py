"""Explicit routing and failure isolation for non-root provider workloads.

This module deliberately does not resolve Session bindings.  The provider
routing authority lives in :mod:`llm.resolution`; this layer consumes one
already-frozen route, applies the workload failure policy, and records a
privacy-safe audit row.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, TypeVar


log = logging.getLogger(__name__)


class WorkloadClass(StrEnum):
    MAIN = "main"
    SESSION_AUXILIARY = "session-auxiliary"
    SYSTEM_MAINTENANCE = "system-maintenance"
    EXPLICIT_INDEPENDENT = "explicit-independent"


class OwnerPolicy(StrEnum):
    IN_TURN = "in-turn"
    POST_TURN_MEMORY = "post-turn-memory"
    DETACHED_MAINTENANCE = "detached-maintenance"


class ResultSink(StrEnum):
    EXECUTION = "execution"
    MEMORY = "memory"
    MAINTENANCE = "maintenance"


class WorkloadFailurePolicy(StrEnum):
    REQUIRED = "required"
    DEGRADE = "degrade"
    MAINTENANCE_ONLY = "maintenance-only"


@dataclass(frozen=True, slots=True)
class ProviderWorkloadContext:
    """Immutable identity envelope created before any background task starts."""

    workload_class: WorkloadClass
    callsite_id: str
    purpose: str
    request_id: str
    owner_policy: OwnerPolicy
    result_sink: ResultSink
    failure_policy: WorkloadFailurePolicy
    call_id: str
    session_id: str | None = None
    root_run_id: str | None = None
    task_scope_id: str | None = None
    detached: bool = False

    def __post_init__(self) -> None:
        for name in ("callsite_id", "purpose", "request_id", "call_id"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        if self.workload_class is WorkloadClass.SESSION_AUXILIARY:
            if self.detached or not self.session_id or not self.root_run_id:
                raise ValueError(
                    "session-auxiliary requires session_id/root_run_id and detached=false"
                )
            if self.owner_policy is OwnerPolicy.DETACHED_MAINTENANCE:
                raise ValueError("session-auxiliary cannot use detached maintenance owner")
        elif self.workload_class is WorkloadClass.SYSTEM_MAINTENANCE:
            if not self.detached or self.session_id is not None or self.root_run_id is not None:
                raise ValueError(
                    "system-maintenance must be detached and cannot claim Session/Root identity"
                )
            if self.owner_policy is not OwnerPolicy.DETACHED_MAINTENANCE:
                raise ValueError("system-maintenance requires detached maintenance owner")


@dataclass(frozen=True, slots=True)
class ProviderWorkloadCallsite:
    callsite_id: str
    workload_class: WorkloadClass
    purpose: str
    owner_policy: OwnerPolicy
    result_sink: ResultSink
    failure_policy: WorkloadFailurePolicy


# This is the production allow-list.  A callsite not represented here is not
# allowed to start through ProviderWorkloadRouter.
PROVIDER_WORKLOAD_CALLSITES: Mapping[str, ProviderWorkloadCallsite] = {
    item.callsite_id: item
    for item in (
        ProviderWorkloadCallsite("agent.root_turn", WorkloadClass.MAIN, "main", OwnerPolicy.IN_TURN, ResultSink.EXECUTION, WorkloadFailurePolicy.REQUIRED),
        ProviderWorkloadCallsite("memory.fact_extract", WorkloadClass.SESSION_AUXILIARY, "memory_summarizer", OwnerPolicy.POST_TURN_MEMORY, ResultSink.MEMORY, WorkloadFailurePolicy.DEGRADE),
        ProviderWorkloadCallsite("memory.fact_merge", WorkloadClass.SESSION_AUXILIARY, "memory_summarizer", OwnerPolicy.POST_TURN_MEMORY, ResultSink.MEMORY, WorkloadFailurePolicy.DEGRADE),
        ProviderWorkloadCallsite("memory.cross_key_merge", WorkloadClass.SESSION_AUXILIARY, "memory_summarizer", OwnerPolicy.POST_TURN_MEMORY, ResultSink.MEMORY, WorkloadFailurePolicy.DEGRADE),
        ProviderWorkloadCallsite("memory.query_rewrite", WorkloadClass.SESSION_AUXILIARY, "classifier", OwnerPolicy.IN_TURN, ResultSink.EXECUTION, WorkloadFailurePolicy.DEGRADE),
        ProviderWorkloadCallsite("memory.entity_extract", WorkloadClass.SESSION_AUXILIARY, "classifier", OwnerPolicy.IN_TURN, ResultSink.EXECUTION, WorkloadFailurePolicy.DEGRADE),
        ProviderWorkloadCallsite("agent.goal_check", WorkloadClass.SESSION_AUXILIARY, "supervisor", OwnerPolicy.IN_TURN, ResultSink.EXECUTION, WorkloadFailurePolicy.DEGRADE),
        ProviderWorkloadCallsite("agent.problem_preanalysis", WorkloadClass.SESSION_AUXILIARY, "classifier", OwnerPolicy.IN_TURN, ResultSink.EXECUTION, WorkloadFailurePolicy.DEGRADE),
        ProviderWorkloadCallsite("agent.context_compaction", WorkloadClass.SESSION_AUXILIARY, "compressor", OwnerPolicy.IN_TURN, ResultSink.EXECUTION, WorkloadFailurePolicy.REQUIRED),
        ProviderWorkloadCallsite("agent.verify_ephemeral", WorkloadClass.SESSION_AUXILIARY, "supervisor", OwnerPolicy.IN_TURN, ResultSink.EXECUTION, WorkloadFailurePolicy.DEGRADE),
        ProviderWorkloadCallsite("agent.external_evaluator", WorkloadClass.SESSION_AUXILIARY, "supervisor", OwnerPolicy.IN_TURN, ResultSink.EXECUTION, WorkloadFailurePolicy.DEGRADE),
        ProviderWorkloadCallsite("memory.curation", WorkloadClass.SESSION_AUXILIARY, "memory_summarizer", OwnerPolicy.IN_TURN, ResultSink.MEMORY, WorkloadFailurePolicy.DEGRADE),
        ProviderWorkloadCallsite("memory.forget_confirm", WorkloadClass.SESSION_AUXILIARY, "memory_summarizer", OwnerPolicy.IN_TURN, ResultSink.MEMORY, WorkloadFailurePolicy.REQUIRED),
        ProviderWorkloadCallsite("companion.preference_interpret", WorkloadClass.SESSION_AUXILIARY, "classifier", OwnerPolicy.IN_TURN, ResultSink.EXECUTION, WorkloadFailurePolicy.DEGRADE),
        ProviderWorkloadCallsite("companion.personal_workflow_match", WorkloadClass.SESSION_AUXILIARY, "classifier", OwnerPolicy.IN_TURN, ResultSink.EXECUTION, WorkloadFailurePolicy.DEGRADE),
        ProviderWorkloadCallsite("memory.reflection", WorkloadClass.SYSTEM_MAINTENANCE, "memory_summarizer", OwnerPolicy.DETACHED_MAINTENANCE, ResultSink.MAINTENANCE, WorkloadFailurePolicy.MAINTENANCE_ONLY),
        ProviderWorkloadCallsite("memory.cross_session_summary", WorkloadClass.SYSTEM_MAINTENANCE, "memory_summarizer", OwnerPolicy.DETACHED_MAINTENANCE, ResultSink.MAINTENANCE, WorkloadFailurePolicy.MAINTENANCE_ONLY),
        ProviderWorkloadCallsite("provider.audit_retention", WorkloadClass.SYSTEM_MAINTENANCE, "auxiliary_unknown", OwnerPolicy.DETACHED_MAINTENANCE, ResultSink.MAINTENANCE, WorkloadFailurePolicy.MAINTENANCE_ONLY),
        ProviderWorkloadCallsite("provider.maintenance_probe", WorkloadClass.SYSTEM_MAINTENANCE, "auxiliary_unknown", OwnerPolicy.DETACHED_MAINTENANCE, ResultSink.MAINTENANCE, WorkloadFailurePolicy.MAINTENANCE_ONLY),
        ProviderWorkloadCallsite("provider.connection_test", WorkloadClass.EXPLICIT_INDEPENDENT, "provider_test", OwnerPolicy.IN_TURN, ResultSink.EXECUTION, WorkloadFailurePolicy.REQUIRED),
    )
}


def workload_context(
    callsite_id: str,
    *,
    request_id: str,
    session_id: str | None = None,
    root_run_id: str | None = None,
    task_scope_id: str | None = None,
    call_id: str | None = None,
) -> ProviderWorkloadContext:
    """Build a context only from the reviewed callsite inventory."""

    try:
        spec = PROVIDER_WORKLOAD_CALLSITES[callsite_id]
    except KeyError as exc:
        raise ValueError(f"undeclared provider workload callsite:{callsite_id}") from exc
    detached = spec.workload_class is WorkloadClass.SYSTEM_MAINTENANCE
    return ProviderWorkloadContext(
        workload_class=spec.workload_class,
        callsite_id=spec.callsite_id,
        purpose=spec.purpose,
        request_id=request_id,
        owner_policy=spec.owner_policy,
        result_sink=spec.result_sink,
        failure_policy=spec.failure_policy,
        call_id=call_id or uuid.uuid4().hex,
        session_id=session_id,
        root_run_id=root_run_id,
        task_scope_id=task_scope_id,
        detached=detached,
    )


def derive_workload_context(
    base: ProviderWorkloadContext,
    callsite_id: str,
    *,
    call_id: str | None = None,
) -> ProviderWorkloadContext:
    """Create a new occurrence while preserving frozen Session/Root identity."""

    return workload_context(
        callsite_id,
        request_id=base.request_id,
        session_id=base.session_id,
        root_run_id=base.root_run_id,
        task_scope_id=base.task_scope_id,
        call_id=call_id,
    )


@dataclass(frozen=True, slots=True)
class ResolvedProviderWorkloadTarget:
    """Physical target returned by the Task-1 routing authority adapter."""

    provider: Any
    provider_id: str
    model: str
    incarnation_id: str
    config_revision: int
    endpoint: str
    account_ref: str = ""

    def __post_init__(self) -> None:
        for name in ("provider_id", "model", "incarnation_id", "endpoint"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"resolved target {name} is required")
        if self.config_revision < 1:
            raise ValueError("resolved target config_revision must be positive")


class ProviderWorkloadTargetResolver(Protocol):
    async def __call__(
        self, context: ProviderWorkloadContext
    ) -> ResolvedProviderWorkloadTarget: ...


class ProviderFaultMatchProtocol(Protocol):
    injection_ref: str
    bound_root_id: str | None
    bound_correlation_id: str

    def to_exception(self) -> BaseException: ...


class ProviderFaultScriptProtocol(Protocol):
    async def consume(
        self, context: ProviderWorkloadContext
    ) -> ProviderFaultMatchProtocol | None: ...


class SessionProviderWorkloadTargetResolver:
    """Thin adapter over Task-1 ``resolve_session_provider_route``.

    It never inspects bindings or chooses a fallback itself. Route identity,
    readiness, and frozen Root snapshot semantics remain owned by
    :mod:`llm.resolution`.
    """

    def __init__(
        self,
        *,
        registry: Any,
        session_db: Any,
        readiness: Any,
        provider_factory: Callable[[Any, str], Any],
        start_snapshot_reader: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._registry = registry
        self._session_db = session_db
        self._readiness = readiness
        self._provider_factory = provider_factory
        self._start_snapshot_reader = start_snapshot_reader

    async def __call__(
        self, context: ProviderWorkloadContext
    ) -> ResolvedProviderWorkloadTarget:
        if context.workload_class is WorkloadClass.SYSTEM_MAINTENANCE:
            raise RuntimeError(
                "system-maintenance requires an explicit BackgroundModelPolicy target"
            )
        if context.workload_class is not WorkloadClass.SESSION_AUXILIARY:
            raise RuntimeError(
                f"provider workload router cannot execute:{context.workload_class.value}"
            )
        from llm.resolution import resolve_session_provider_route

        route = await resolve_session_provider_route(
            str(context.session_id),
            registry=self._registry,
            session_db=self._session_db,
            readiness=self._readiness,
            root_run_id=context.root_run_id,
            start_snapshot_reader=self._start_snapshot_reader,
        )
        entries = tuple(getattr(route, "entries", ()) or ())
        if not entries:
            raise RuntimeError("resolved provider route has no entries")
        entry = entries[0]
        model = str(getattr(route, "model", "") or getattr(entry, "model", ""))
        provider = self._provider_factory(entry, model)
        return ResolvedProviderWorkloadTarget(
            provider=provider,
            provider_id=str(getattr(route, "provider_id", "") or getattr(entry, "id", "")),
            model=model,
            incarnation_id=str(
                getattr(route, "incarnation_id", "")
                or getattr(entry, "incarnation_id", "")
            ),
            config_revision=int(
                getattr(route, "config_revision", 0)
                or getattr(entry, "config_revision", 0)
            ),
            endpoint=str(getattr(entry, "base_url", "") or ""),
            account_ref=str(getattr(entry, "account_ref", "") or ""),
        )


class ProviderWorkloadAuditSink(Protocol):
    async def record(self, event: Mapping[str, Any]) -> None: ...


class NullProviderWorkloadAudit:
    async def record(self, event: Mapping[str, Any]) -> None:
        del event


class FailureKind(StrEnum):
    CREDENTIAL = "credential"
    ACCOUNT_QUOTA = "account_quota"
    MODEL_NOT_FOUND = "model_not_found"
    RATE_LIMIT = "rate_limit"
    ENDPOINT_TRANSIENT = "endpoint_transient"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ProviderFailureDecision:
    kind: FailureKind
    scope_key: tuple[str, ...]
    retry_after_s: float | None = None
    opens_breaker: bool = False


class ProviderFailurePolicyV1:
    """Map structured provider failures to the narrowest stable scope."""

    version = 1

    def classify(
        self,
        exc: BaseException,
        *,
        target: ResolvedProviderWorkloadTarget,
        context: ProviderWorkloadContext,
    ) -> ProviderFailureDecision:
        status = getattr(exc, "status_code", None)
        error_class = str(getattr(exc, "error_class", "") or "").lower()
        message = str(exc).lower()
        retry_after = getattr(exc, "retry_after", None)
        try:
            retry_after_s = None if retry_after is None else max(0.0, float(retry_after))
        except (TypeError, ValueError):
            retry_after_s = None

        provider_revision = (
            target.provider_id,
            target.incarnation_id,
            str(target.config_revision),
        )
        if status in {401, 403} and not (
            status == 403 and ("quota" in error_class or "balance" in error_class)
        ):
            return ProviderFailureDecision(
                FailureKind.CREDENTIAL,
                ("credential", *provider_revision),
                opens_breaker=True,
            )
        if status == 402 or "insufficient_balance" in error_class or "quota" in error_class:
            model_scoped = bool(getattr(exc, "model_quota", False))
            return ProviderFailureDecision(
                FailureKind.ACCOUNT_QUOTA,
                (
                    "account_quota",
                    target.provider_id,
                    target.incarnation_id,
                    target.account_ref or target.provider_id,
                    *((target.model,) if model_scoped else ()),
                ),
                retry_after_s=retry_after_s,
                opens_breaker=True,
            )
        if status == 404 or "model_not_found" in error_class or "model not found" in message:
            return ProviderFailureDecision(
                FailureKind.MODEL_NOT_FOUND,
                ("model", *provider_revision, target.model),
                opens_breaker=True,
            )
        if status == 429:
            return ProviderFailureDecision(
                FailureKind.RATE_LIMIT,
                (
                    "rate_limit",
                    *provider_revision,
                    target.model,
                    context.workload_class.value,
                ),
                retry_after_s=retry_after_s,
                opens_breaker=True,
            )
        if (
            isinstance(status, int) and 500 <= status <= 599
        ) or error_class in {"timeout", "transport", "connect_error"}:
            return ProviderFailureDecision(
                FailureKind.ENDPOINT_TRANSIENT,
                ("endpoint", target.endpoint),
                retry_after_s=retry_after_s,
                opens_breaker=True,
            )
        return ProviderFailureDecision(
            FailureKind.OTHER,
            ("other", *provider_revision, target.model, context.callsite_id),
        )


class BreakerStateName(StrEnum):
    UNPROVEN = "unproven"
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


@dataclass(slots=True)
class _BreakerState:
    state: BreakerStateName = BreakerStateName.UNPROVEN
    opened_at: float | None = None
    open_until: float | None = None
    failure_count: int = 0
    reset_generation: int = 0
    leader_future: asyncio.Future[None] | None = None
    stable_error: BaseException | None = None
    decision: ProviderFailureDecision | None = None


class ProviderCircuitOpen(RuntimeError):
    def __init__(
        self,
        decision: ProviderFailureDecision,
        *,
        open_until: float | None,
        reset_generation: int,
        cause: BaseException | None = None,
    ) -> None:
        self.decision = decision
        self.open_until = open_until
        self.reset_generation = reset_generation
        self.cause = cause
        super().__init__(f"provider_workload_circuit_open:{decision.kind.value}")


class ProviderProbeCancelled(RuntimeError):
    pass


T = TypeVar("T")


class ProviderWorkloadBreaker:
    """In-process per-scope breaker with cold/half-open single-flight probes."""

    def __init__(
        self,
        *,
        policy: ProviderFailurePolicyV1 | None = None,
        clock: Callable[[], float] = time.monotonic,
        quota_cooldown_s: float = 30.0,
        max_cooldown_s: float = 900.0,
        transient_cooldown_s: float = 1.0,
    ) -> None:
        self._policy = policy or ProviderFailurePolicyV1()
        self._clock = clock
        self._quota_cooldown_s = float(quota_cooldown_s)
        self._max_cooldown_s = float(max_cooldown_s)
        self._transient_cooldown_s = float(transient_cooldown_s)
        self._states: dict[tuple[str, ...], _BreakerState] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _provisional_key(
        target: ResolvedProviderWorkloadTarget,
    ) -> tuple[str, ...]:
        # A new provider revision must prove itself once before unconstrained
        # concurrency. This is deliberately broader than callsite/purpose.
        return (
            "revision",
            target.provider_id,
            target.incarnation_id,
            str(target.config_revision),
        )

    def _cooldown(
        self, decision: ProviderFailureDecision, failure_count: int
    ) -> float | None:
        if decision.kind is FailureKind.CREDENTIAL:
            return None
        base = (
            self._quota_cooldown_s
            if decision.kind is FailureKind.ACCOUNT_QUOTA
            else self._transient_cooldown_s
        )
        bounded = min(self._max_cooldown_s, base * (2 ** max(0, failure_count - 1)))
        return max(bounded, decision.retry_after_s or 0.0)

    def _known_scope_keys(
        self,
        target: ResolvedProviderWorkloadTarget,
        context: ProviderWorkloadContext,
    ) -> tuple[tuple[str, ...], ...]:
        revision = (
            target.provider_id,
            target.incarnation_id,
            str(target.config_revision),
        )
        return (
            ("credential", *revision),
            (
                "account_quota",
                target.provider_id,
                target.incarnation_id,
                target.account_ref or target.provider_id,
            ),
            (
                "account_quota",
                target.provider_id,
                target.incarnation_id,
                target.account_ref or target.provider_id,
                target.model,
            ),
            ("model", *revision, target.model),
            (
                "rate_limit",
                *revision,
                target.model,
                context.workload_class.value,
            ),
            ("endpoint", target.endpoint),
        )

    async def _claim_existing_scope_probe(
        self,
        target: ResolvedProviderWorkloadTarget,
        context: ProviderWorkloadContext,
    ) -> tuple[tuple[str, ...], bool, asyncio.Future[None]] | None:
        async with self._lock:
            now = self._clock()
            for key in self._known_scope_keys(target, context):
                state = self._states.get(key)
                if state is None or state.state in {
                    BreakerStateName.UNPROVEN,
                    BreakerStateName.CLOSED,
                }:
                    continue
                if state.state is BreakerStateName.OPEN:
                    if state.open_until is None or now < state.open_until:
                        decision = state.decision or ProviderFailureDecision(
                            FailureKind.OTHER, key, opens_breaker=True
                        )
                        raise ProviderCircuitOpen(
                            decision,
                            open_until=state.open_until,
                            reset_generation=state.reset_generation,
                            cause=state.stable_error,
                        )
                    state.state = BreakerStateName.HALF_OPEN
                    state.leader_future = None
                if state.leader_future is None:
                    state.leader_future = asyncio.get_running_loop().create_future()
                    return key, True, state.leader_future
                return key, False, state.leader_future
        return None

    async def _claim_probe(
        self, key: tuple[str, ...]
    ) -> tuple[bool, asyncio.Future[None], _BreakerState]:
        async with self._lock:
            now = self._clock()
            state = self._states.setdefault(key, _BreakerState())
            if state.state is BreakerStateName.CLOSED:
                loop = asyncio.get_running_loop()
                done = loop.create_future()
                done.set_result(None)
                return False, done, state
            if state.leader_future is None:
                state.leader_future = asyncio.get_running_loop().create_future()
                return True, state.leader_future, state
            return False, state.leader_future, state

    async def _open_scope(
        self,
        *,
        decision: ProviderFailureDecision,
        exc: BaseException,
        leader_future: asyncio.Future[None] | None,
        share_failure: bool = False,
    ) -> _BreakerState:
        async with self._lock:
            state = self._states.setdefault(decision.scope_key, _BreakerState())
            state.failure_count += 1
            state.state = BreakerStateName.OPEN
            state.opened_at = self._clock()
            cooldown = self._cooldown(decision, state.failure_count)
            state.open_until = None if cooldown is None else state.opened_at + cooldown
            state.stable_error = exc
            state.decision = decision
            state.leader_future = None
            if leader_future is not None and not leader_future.done():
                if share_failure:
                    leader_future.set_exception(
                        ProviderCircuitOpen(
                            decision,
                            open_until=state.open_until,
                            reset_generation=state.reset_generation,
                            cause=exc,
                        )
                    )
                    leader_future.exception()
                else:
                    leader_future.set_result(None)
            return state

    async def _close(
        self,
        key: tuple[str, ...],
        leader_future: asyncio.Future[None] | None,
    ) -> None:
        async with self._lock:
            state = self._states.setdefault(key, _BreakerState())
            state.state = BreakerStateName.CLOSED
            state.opened_at = None
            state.open_until = None
            state.failure_count = 0
            state.stable_error = None
            state.decision = None
            state.leader_future = None
            if leader_future is not None and not leader_future.done():
                leader_future.set_result(None)

    async def execute(
        self,
        *,
        target: ResolvedProviderWorkloadTarget,
        context: ProviderWorkloadContext,
        operation: Callable[[], Awaitable[T]],
    ) -> tuple[T, str | None]:
        scope_probe = await self._claim_existing_scope_probe(target, context)
        if scope_probe is not None:
            scope_key, leader, future = scope_probe
            if not leader:
                await asyncio.shield(future)
                return await self.execute(
                    target=target, context=context, operation=operation
                )
            try:
                result = await operation()
            except asyncio.CancelledError:
                await self._cancel_probe(scope_key, future)
                raise
            except BaseException as exc:
                decision = self._policy.classify(
                    exc, target=target, context=context
                )
                if decision.opens_breaker:
                    if decision.scope_key == scope_key:
                        await self._open_scope(
                            decision=decision,
                            exc=exc,
                            leader_future=future,
                            share_failure=True,
                        )
                    else:
                        # A half-open probe can discover a different authority
                        # failure (for example endpoint recovery followed by a
                        # credential rejection). Close/wake the old scope before
                        # opening the newly classified scope; otherwise the old
                        # leader future would remain stranded forever.
                        await self._close(scope_key, future)
                        await self._open_scope(
                            decision=decision,
                            exc=exc,
                            leader_future=None,
                        )
                else:
                    await self._close(scope_key, future)
                raise
            await self._close(scope_key, future)
            return result, "half_open_closed"

        provisional_key = self._provisional_key(target)
        leader, future, _state = await self._claim_probe(provisional_key)
        if not leader and not future.done():
            await asyncio.shield(future)
            # The leader proved the revision healthy. Followers execute their
            # own semantic request; only admission/probe outcome is shared.
            return await self.execute(
                target=target, context=context, operation=operation
            )
        if not leader:
            return await self._execute_closed(
                provisional_key, target=target, context=context, operation=operation
            )
        try:
            result = await operation()
        except asyncio.CancelledError:
            await self._cancel_probe(provisional_key, future)
            raise
        except BaseException as exc:
            decision = self._policy.classify(exc, target=target, context=context)
            if decision.opens_breaker:
                await self._open_scope(
                    decision=decision,
                    exc=exc,
                    leader_future=None,
                )
            stable_failure = decision.kind in {
                FailureKind.CREDENTIAL,
                FailureKind.ACCOUNT_QUOTA,
            }
            await self._finish_provisional_failure(
                provisional_key,
                future,
                decision=decision,
                exc=exc,
                share_failure=stable_failure,
            )
            raise
        await self._close(provisional_key, future)
        return result, "probe_closed"

    async def _cancel_probe(
        self, key: tuple[str, ...], future: asyncio.Future[None]
    ) -> None:
        async with self._lock:
            state = self._states.setdefault(key, _BreakerState())
            state.state = BreakerStateName.UNPROVEN
            state.leader_future = None
            if not future.done():
                future.set_exception(
                    ProviderProbeCancelled("provider probe leader cancelled")
                )
                future.exception()

    async def _finish_provisional_failure(
        self,
        key: tuple[str, ...],
        future: asyncio.Future[None],
        *,
        decision: ProviderFailureDecision,
        exc: BaseException,
        share_failure: bool,
    ) -> None:
        async with self._lock:
            state = self._states.setdefault(key, _BreakerState())
            state.state = BreakerStateName.CLOSED
            state.leader_future = None
            if not future.done():
                if share_failure:
                    scope = self._states[decision.scope_key]
                    future.set_exception(
                        ProviderCircuitOpen(
                            decision,
                            open_until=scope.open_until,
                            reset_generation=scope.reset_generation,
                            cause=exc,
                        )
                    )
                    future.exception()
                else:
                    future.set_result(None)

    async def _execute_closed(
        self,
        provisional_key: tuple[str, ...],
        *,
        target: ResolvedProviderWorkloadTarget,
        context: ProviderWorkloadContext,
        operation: Callable[[], Awaitable[T]],
    ) -> tuple[T, str | None]:
        try:
            return await operation(), None
        except BaseException as exc:
            decision = self._policy.classify(exc, target=target, context=context)
            if decision.opens_breaker:
                await self._open_scope(
                    decision=decision,
                    exc=exc,
                    leader_future=None,
                )
            raise

    async def reset(
        self,
        scope_key: tuple[str, ...],
        *,
        expected_provider_id: str,
    ) -> int:
        if expected_provider_id not in scope_key:
            raise ValueError("breaker reset scope does not belong to selected provider")
        async with self._lock:
            state = self._states.setdefault(scope_key, _BreakerState())
            state.reset_generation += 1
            state.state = BreakerStateName.UNPROVEN
            state.opened_at = None
            state.open_until = None
            state.stable_error = None
            state.decision = None
            state.leader_future = None
            return state.reset_generation

    async def reconcile_provider_revision(
        self, target: ResolvedProviderWorkloadTarget
    ) -> int:
        """Discard stale incarnation/revision state before admission.

        Endpoint scopes intentionally survive because they represent the
        physical endpoint rather than one registry incarnation.
        """

        removed = 0
        async with self._lock:
            for key in tuple(self._states):
                if len(key) < 3 or key[1] != target.provider_id:
                    continue
                stale = False
                if key[0] == "account_quota":
                    stale = key[2] != target.incarnation_id
                elif key[0] in {"revision", "credential", "model", "rate_limit"}:
                    stale = (
                        len(key) < 4
                        or key[2] != target.incarnation_id
                        or key[3] != str(target.config_revision)
                    )
                if stale:
                    state = self._states.pop(key)
                    if state.leader_future is not None and not state.leader_future.done():
                        state.leader_future.set_exception(
                            ProviderProbeCancelled(
                                "provider revision replaced during probe"
                            )
                        )
                        state.leader_future.exception()
                    removed += 1
        return removed

    async def state_snapshot(self) -> dict[tuple[str, ...], dict[str, Any]]:
        async with self._lock:
            return {
                key: {
                    "state": state.state.value,
                    "opened_at": state.opened_at,
                    "open_until": state.open_until,
                    "failure_count": state.failure_count,
                    "reset_generation": state.reset_generation,
                    "leader_active": state.leader_future is not None,
                }
                for key, state in self._states.items()
            }


def _stable_call_id(context: ProviderWorkloadContext) -> str:
    seed = "|".join(("provider-workload-v1", context.call_id))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


class RootOwnedWorkloadTasks:
    """Tracks in-turn tasks so user cancellation has one explicit seam."""

    def __init__(self) -> None:
        self._tasks: dict[str, set[asyncio.Task[Any]]] = {}
        self._lock = asyncio.Lock()

    def track(
        self, context: ProviderWorkloadContext, task: asyncio.Task[T]
    ) -> asyncio.Task[T]:
        if context.owner_policy is not OwnerPolicy.IN_TURN or not context.root_run_id:
            return task
        # Registration is synchronous and happens before schedule() returns;
        # event-loop task callbacks cannot interleave this mutation.
        self._tasks.setdefault(context.root_run_id, set()).add(task)

        def _discard(done: asyncio.Task[Any]) -> None:
            tasks = self._tasks.get(context.root_run_id or "")
            if tasks is not None:
                tasks.discard(done)
                if not tasks:
                    self._tasks.pop(context.root_run_id or "", None)

        task.add_done_callback(_discard)
        return task

    async def cancel_root(self, root_run_id: str) -> int:
        async with self._lock:
            tasks = tuple(self._tasks.pop(root_run_id, ()))
        for task in tasks:
            task.cancel()
        return len(tasks)


def _extract_provider_text(result: Any) -> str:
    """Normalize the provider protocol without assuming one response class."""

    if result is None:
        return ""
    if isinstance(result, Mapping):
        return str(result.get("content") or "")
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    if content is None:
        raise TypeError(
            f"provider response has no content field:{type(result).__name__}"
        )
    return str(content)


class ProviderWorkloadRouter:
    def __init__(
        self,
        resolver: ProviderWorkloadTargetResolver,
        *,
        breaker: ProviderWorkloadBreaker | None = None,
        audit: ProviderWorkloadAuditSink | None = None,
        tasks: RootOwnedWorkloadTasks | None = None,
        fault_script: ProviderFaultScriptProtocol | None = None,
    ) -> None:
        self._resolver = resolver
        self._breaker = breaker or ProviderWorkloadBreaker()
        self._audit = audit or NullProviderWorkloadAudit()
        self._tasks = tasks or RootOwnedWorkloadTasks()
        self._fault_script = fault_script

    async def invoke(
        self,
        prompt: str,
        *,
        workload_context: ProviderWorkloadContext,
        max_tokens: int = 512,
        response_format: Any = None,
    ) -> str:
        spec = PROVIDER_WORKLOAD_CALLSITES.get(workload_context.callsite_id)
        if spec is None or (
            spec.workload_class is not workload_context.workload_class
            or spec.owner_policy is not workload_context.owner_policy
            or spec.result_sink is not workload_context.result_sink
            or spec.failure_policy is not workload_context.failure_policy
        ):
            raise RuntimeError("provider workload context does not match inventory")
        target = await self._resolver(workload_context)
        started = time.monotonic()
        call_id = _stable_call_id(workload_context)
        transition = None
        status = "failed"
        error_class = None
        injection_ref = None
        injection_bound_root_id = None
        injection_bound_correlation_id = None
        try:
            async def _physical_call() -> str:
                nonlocal injection_ref, injection_bound_root_id
                nonlocal injection_bound_correlation_id
                if self._fault_script is not None:
                    fault = await self._fault_script.consume(workload_context)
                    if fault is not None:
                        injection_ref = fault.injection_ref
                        injection_bound_root_id = fault.bound_root_id
                        injection_bound_correlation_id = fault.bound_correlation_id
                        raise fault.to_exception()
                from agent.context_messages import (
                    ProviderAttemptOptions,
                    context_attempt_scope,
                    provider_purpose_scope,
                )

                options = ProviderAttemptOptions(
                    purpose=workload_context.purpose,
                    session_id=workload_context.session_id,
                    root_run_id=workload_context.root_run_id,
                    request_id=workload_context.request_id,
                    attempt_id=f"aux:{call_id}",
                    workload_class=workload_context.workload_class.value,
                    callsite_id=workload_context.callsite_id,
                    detached=workload_context.detached,
                )
                messages = [{"role": "user", "content": prompt}]
                with provider_purpose_scope(
                    workload_context.purpose,
                    session_id=workload_context.session_id,
                    request_id=workload_context.request_id,
                ):
                    with context_attempt_scope(options):
                        if response_format is not None and hasattr(
                            target.provider, "_legacy_chat_with_tools_nonstream"
                        ):
                            result = await target.provider._legacy_chat_with_tools_nonstream(
                                messages=messages,
                                max_tokens=max_tokens,
                                temperature=0.2,
                                response_format=response_format,
                            )
                        else:
                            result = await target.provider.chat_with_tools(
                                messages=messages,
                                max_tokens=max_tokens,
                                temperature=0.2,
                                **(
                                    {"response_format": response_format}
                                    if response_format is not None
                                    else {}
                                ),
                            )
                return _extract_provider_text(result)

            result, transition = await self._breaker.execute(
                target=target,
                context=workload_context,
                operation=_physical_call,
            )
            status = "completed"
            return result
        except BaseException as exc:
            error_class = type(exc).__name__
            raise
        finally:
            event = {
                "stable_call_id": call_id,
                "workload_class": workload_context.workload_class.value,
                "callsite_id": workload_context.callsite_id,
                "purpose": workload_context.purpose,
                "provider_id": target.provider_id,
                "provider_incarnation_id": target.incarnation_id,
                "model": target.model,
                "config_revision": target.config_revision,
                "session_id": workload_context.session_id,
                "root_run_id": workload_context.root_run_id,
                "detached": workload_context.detached,
                "owner_policy": workload_context.owner_policy.value,
                "status": status,
                "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
                "error_class": error_class,
                "breaker_transition": transition,
                "injection_ref": injection_ref,
                "injection_bound_root_id": injection_bound_root_id,
                "injection_bound_correlation_id": injection_bound_correlation_id,
            }
            try:
                await self._audit.record(event)
            except Exception as audit_exc:  # audit is observability, not call authority
                log.warning(
                    "provider_workload_audit_write_failed call_id=%s error=%s",
                    call_id,
                    type(audit_exc).__name__,
                )

    async def schedule(
        self,
        prompt: str,
        *,
        workload_context: ProviderWorkloadContext,
        max_tokens: int = 512,
        response_format: Any = None,
        name: str | None = None,
    ) -> asyncio.Task[str]:
        task = asyncio.create_task(
            self.invoke(
                prompt,
                workload_context=workload_context,
                max_tokens=max_tokens,
                response_format=response_format,
            ),
            name=name,
        )
        return self._tasks.track(workload_context, task)

    async def cancel_root(self, root_run_id: str) -> int:
        return await self._tasks.cancel_root(root_run_id)

    def track_task(
        self,
        workload_context: ProviderWorkloadContext,
        task: asyncio.Task[T],
    ) -> asyncio.Task[T]:
        return self._tasks.track(workload_context, task)

    async def reset_scope(
        self, scope_key: tuple[str, ...], *, expected_provider_id: str
    ) -> int:
        return await self._breaker.reset(
            scope_key, expected_provider_id=expected_provider_id
        )

    async def reconcile_provider_revision(
        self, target: ResolvedProviderWorkloadTarget
    ) -> int:
        """Registry lifecycle hook for revision/delete-and-readd cutovers."""

        return await self._breaker.reconcile_provider_revision(target)


class SessionAwareLLMCall:
    """The only production ``prompt -> text`` auxiliary provider protocol."""

    def __init__(
        self,
        router: ProviderWorkloadRouter | Callable[[], ProviderWorkloadRouter],
        *,
        max_tokens: int = 512,
        response_format: Any = None,
    ) -> None:
        self._router = router
        self._max_tokens = max_tokens
        self._response_format = response_format

    def _resolve_router(self) -> ProviderWorkloadRouter:
        router = self._router
        if isinstance(router, ProviderWorkloadRouter):
            return router
        resolved = router()
        if not isinstance(resolved, ProviderWorkloadRouter):
            raise RuntimeError("provider_workload_router is unavailable")
        return resolved

    async def __call__(
        self, prompt: str, *, workload_context: ProviderWorkloadContext
    ) -> str:
        return await self._resolve_router().invoke(
            prompt,
            workload_context=workload_context,
            max_tokens=self._max_tokens,
            response_format=self._response_format,
        )


class BackgroundModelPolicy:
    """Explicit provider authority for detached cross-Session maintenance."""

    def __init__(
        self,
        *,
        registry: Any | None = None,
        readiness: Any | None = None,
        provider_factory: Callable[[Any, str], Any] | None = None,
        provider_id: str | None = None,
        model: str | None = None,
    ) -> None:
        self._registry = registry
        self._readiness = readiness
        self._provider_factory = provider_factory
        self._provider_id = str(provider_id or "").strip() or None
        self._model = str(model or "").strip() or None

    @staticmethod
    def context(callsite_id: str, *, request_id: str | None = None) -> ProviderWorkloadContext:
        return workload_context(
            callsite_id,
            request_id=request_id or f"maintenance:{uuid.uuid4().hex}",
        )

    async def resolve(
        self, context: ProviderWorkloadContext
    ) -> ResolvedProviderWorkloadTarget:
        if context.workload_class is not WorkloadClass.SYSTEM_MAINTENANCE:
            raise RuntimeError("background model policy only owns system-maintenance")
        if self._registry is None or self._provider_factory is None:
            raise RuntimeError("background model policy is not configured")
        if self._readiness is not None:
            ready = self._readiness.require_ready()
            if inspect.isawaitable(ready):
                await ready
        entry = (
            self._registry.get_entry(self._provider_id)
            if self._provider_id is not None
            else None
        )
        if entry is None and self._provider_id is None:
            chain = self._registry.get_chain()
            chain_head = chain[0] if chain else None
            if isinstance(chain_head, Mapping):
                chain_provider_id = str(chain_head.get("id") or "").strip()
            else:
                chain_provider_id = str(
                    getattr(chain_head, "id", "") or ""
                ).strip()
            entry = (
                self._registry.get_entry(chain_provider_id)
                if chain_provider_id
                else None
            )
        if entry is None:
            raise RuntimeError("background provider policy target is unavailable")
        model = self._model or str(getattr(entry, "model", "") or "")
        return ResolvedProviderWorkloadTarget(
            provider=self._provider_factory(entry, model),
            provider_id=str(getattr(entry, "id", "") or ""),
            model=model,
            incarnation_id=str(getattr(entry, "incarnation_id", "") or ""),
            config_revision=int(getattr(entry, "config_revision", 0) or 0),
            endpoint=str(getattr(entry, "base_url", "") or ""),
            account_ref=str(getattr(entry, "account_ref", "") or ""),
        )


class ProviderWorkloadRoutePolicy:
    """Dispatch only by declared workload class, never by missing identity."""

    def __init__(
        self,
        *,
        session: SessionProviderWorkloadTargetResolver,
        background: BackgroundModelPolicy,
    ) -> None:
        self._session = session
        self._background = background

    async def __call__(
        self, context: ProviderWorkloadContext
    ) -> ResolvedProviderWorkloadTarget:
        if context.workload_class is WorkloadClass.SESSION_AUXILIARY:
            return await self._session(context)
        if context.workload_class is WorkloadClass.SYSTEM_MAINTENANCE:
            return await self._background.resolve(context)
        raise RuntimeError(
            f"provider workload route policy cannot execute:{context.workload_class.value}"
        )


async def invoke_explicit(
    llm_call: Callable[..., Awaitable[str]],
    prompt: str,
    *,
    workload_context: ProviderWorkloadContext,
) -> str:
    """Invoke the explicit protocol; legacy one-arg callables are test-only.

    Production composition always supplies :class:`SessionAwareLLMCall`.
    The signature check keeps existing pure unit-test fakes usable without
    creating a runtime provider fallback path.
    """

    if isinstance(llm_call, SessionAwareLLMCall):
        return await llm_call(prompt, workload_context=workload_context)
    parameters = inspect.signature(llm_call).parameters
    if "workload_context" in parameters or any(
        item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()
    ):
        return await llm_call(prompt, workload_context=workload_context)
    return await llm_call(prompt)


__all__ = [
    "BackgroundModelPolicy",
    "FailureKind",
    "OwnerPolicy",
    "PROVIDER_WORKLOAD_CALLSITES",
    "ProviderCircuitOpen",
    "ProviderFailurePolicyV1",
    "ProviderProbeCancelled",
    "ProviderWorkloadBreaker",
    "ProviderWorkloadCallsite",
    "ProviderWorkloadContext",
    "ProviderFaultMatchProtocol",
    "ProviderFaultScriptProtocol",
    "ProviderWorkloadRouter",
    "ProviderWorkloadRoutePolicy",
    "ResolvedProviderWorkloadTarget",
    "ResultSink",
    "RootOwnedWorkloadTasks",
    "SessionProviderWorkloadTargetResolver",
    "SessionAwareLLMCall",
    "WorkloadClass",
    "WorkloadFailurePolicy",
    "invoke_explicit",
    "derive_workload_context",
    "workload_context",
]
