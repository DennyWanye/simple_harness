# SPDX-License-Identifier: BUSL-1.1

"""Foreground Host scheduler composed around the sole SDK Runtime ingress.

The Host owns FIFO admission and durable lifecycle facts.  This module never
executes an Agent loop: it freezes the three Host authorities needed by one
Run, records the start boundary, and delegates the physical Agent execution to
``SdkRuntimeIngress``.  Process tasks are only wake-up helpers; SQLite remains
the authority after crashes and restarts.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import aiosqlite
from simple_harness import (
    ContextRouteOrigin,
    ContextRouteReceipt,
    TaskScopeRoute,
)

from deskpet.execution.foreground_queue import (
    ClaimedExecution,
    ContextLineage,
    ControlKind,
    ForegroundQueueError,
    ForegroundQueueStore,
    PreparationCandidate,
    RunState,
)
from deskpet.sdk_adapters.ingress import SdkRuntimeIngress
from deskpet.task_scope.protocol import canonical_hash


class ForegroundRuntimeError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class FrozenProviderAuthority:
    authority_ref: str
    authority_hash: str
    provider_id: str
    provider_incarnation_id: str
    provider_config_revision: int
    binding_epoch: int
    model_id: str
    model_params: Mapping[str, object]
    context_window: int


@dataclass(frozen=True, slots=True)
class FrozenToolAuthority:
    authority_ref: str
    authority_hash: str
    catalog: Mapping[str, object]
    run_start_record: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class FrozenContextAuthority:
    authority_ref: str
    authority_hash: str
    snapshot_id: str
    provider_messages: tuple[Mapping[str, object], ...]
    current_text: str
    resume_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BoundProviderAuthority:
    authority_ref: str
    authority_hash: str
    binding: ForegroundRunBinding


class ForegroundRunBinding(Protocol):
    catalog_generation: int
    catalog_fingerprint: str
    budget_fingerprint: str

    def to_record(self) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class AuthenticatedTerminalObservation:
    terminal_state: RunState
    sdk_event_id: str
    sdk_event_hash: str


class ForegroundContextPreparationPort(Protocol):
    async def draft_lineage(
        self, candidate: PreparationCandidate
    ) -> ContextLineage: ...

    async def prepare(
        self,
        *,
        claimed: ClaimedExecution,
        expected_context: ContextLineage,
        execution_session_id: str,
        request_id: str,
        sdk_run_id: str,
        provider: FrozenProviderAuthority,
        tools: FrozenToolAuthority,
    ) -> FrozenContextAuthority: ...

    async def verify_initial_route(
        self, receipt: ContextRouteReceipt
    ) -> None: ...


class ForegroundProviderAuthorityPort(Protocol):
    async def freeze(
        self,
        *,
        claimed: ClaimedExecution,
        execution_session_id: str,
        request_id: str,
        sdk_run_id: str,
    ) -> FrozenProviderAuthority: ...

    async def bind(
        self,
        *,
        frozen: FrozenProviderAuthority,
        context: FrozenContextAuthority,
        tools: FrozenToolAuthority,
        execution_session_id: str,
        request_id: str,
        sdk_run_id: str,
    ) -> BoundProviderAuthority: ...

    def mark_terminal(self, sdk_run_id: str, state: str) -> None: ...


class ForegroundToolAuthorityPort(Protocol):
    async def freeze(
        self,
        *,
        claimed: ClaimedExecution,
        execution_session_id: str,
        request_id: str,
        sdk_run_id: str,
    ) -> FrozenToolAuthority: ...

    def mark_terminal(self, sdk_run_id: str, state: str) -> None: ...


class ForegroundTerminalObserverPort(Protocol):
    async def observe(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        subject: str,
        owner_id: str,
        generation: int,
    ) -> AuthenticatedTerminalObservation | None: ...


class ForegroundRuntimeAuditSink(Protocol):
    def record(self, event: str, payload: Mapping[str, object]) -> object: ...


def _uuid(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"simple-harness:{label}"))


def _execution_session_id(host_run_id: str) -> str:
    digest = hashlib.sha256(host_run_id.encode("utf-8")).hexdigest()
    return f"foreground-execution-{digest}"


def _candidate_turn_payload(candidate: PreparationCandidate) -> dict[str, object]:
    try:
        raw = json.loads(candidate.candidate_json)
        turn = raw["turn"]
        payload = turn["payload"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ForegroundRuntimeError("foreground_claimed_turn_payload_invalid") from exc
    if not isinstance(payload, dict):
        raise ForegroundRuntimeError("foreground_claimed_turn_payload_invalid")
    return payload


async def _maybe_await(value: object) -> None:
    if inspect.isawaitable(value):
        await value


class ForegroundRuntimeExecutionAuthority:
    """One-subject foreground driver; the durable queue is the true lock."""

    def __init__(
        self,
        *,
        store: ForegroundQueueStore,
        subject: str,
        owner_id: str,
        ingress: SdkRuntimeIngress,
        context: ForegroundContextPreparationPort,
        provider: ForegroundProviderAuthorityPort,
        tools: ForegroundToolAuthorityPort,
        terminal_observer: ForegroundTerminalObserverPort,
        audit_sink: ForegroundRuntimeAuditSink | None = None,
        lease_seconds: float = 300.0,
    ) -> None:
        if not subject.strip() or not owner_id.strip():
            raise ValueError("subject and owner_id are required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self._store = store
        self._subject = subject
        self._owner_id = owner_id
        self._ingress = ingress
        self._context = context
        self._provider = provider
        self._tools = tools
        self._terminal = terminal_observer
        self._audit = audit_sink
        self._lease_seconds = float(lease_seconds)
        self._driver: asyncio.Task[None] | None = None
        self._driver_lock = asyncio.Lock()
        self._closed = False
        self._last_error: Exception | None = None

    @property
    def subject(self) -> str:
        return self._subject

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    async def after_enqueue(self, *, subject: str) -> None:
        """Wake the only process helper for the constructor-bound subject."""

        if subject != self._subject:
            raise ForegroundRuntimeError("foreground_runtime_subject_mismatch")
        if self._closed:
            raise ForegroundRuntimeError("foreground_runtime_closed")
        async with self._driver_lock:
            if self._driver is None or self._driver.done():
                self._driver = asyncio.create_task(
                    self._run_driver(),
                    name=f"foreground-runtime:{hashlib.sha256(subject.encode()).hexdigest()[:12]}",
                )

    async def drain(self) -> None:
        """Wait for the current process helper; tests and shutdown only."""

        task = self._driver
        if task is not None:
            await asyncio.shield(task)

    async def close(self, *, timeout: float = 5.0) -> None:
        self._closed = True
        task = self._driver
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        snapshot = await self._store.current_snapshot(self._subject)
        if snapshot is None or snapshot.owner_id != self._owner_id:
            return
        try:
            await self._store.close_current_lease(
                host_run_id=snapshot.host_run_id,
                owner_id=self._owner_id,
                generation=snapshot.generation,
                idempotency_key=(
                    f"runtime-shutdown:{snapshot.host_run_id}:g{snapshot.generation}"
                ),
            )
        except ForegroundQueueError as exc:
            if exc.code not in {
                "foreground_generation_stale",
                "foreground_lease_expired",
                "foreground_run_already_terminal",
            }:
                raise

    async def _record_audit(self, event: str, **payload: object) -> None:
        if self._audit is not None:
            await _maybe_await(self._audit.record(event, payload))

    async def _run_driver(self) -> None:
        self._last_error = None
        try:
            while not self._closed:
                progressed = await self._drive_once()
                if not progressed:
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - durable state remains recoverable
            self._last_error = exc
            await self._record_audit(
                "foreground.runtime.failed",
                error_code=str(getattr(exc, "code", type(exc).__name__)),
            )

    async def _drive_once(self) -> bool:
        snapshot = await self._store.current_snapshot(self._subject)
        if snapshot is None:
            candidate = await self._store.read_next_preparation_candidate(
                self._subject
            )
            if candidate is None:
                return False
            if candidate.subject != self._subject:
                raise ForegroundRuntimeError("foreground_runtime_candidate_subject_drift")
            lineage = await self._context.draft_lineage(candidate)
            draft = await self._store.prepare_candidate(
                subject=self._subject,
                expected_candidate_hash=candidate.candidate_hash,
                context=lineage,
                idempotency_key=f"runtime-draft:{candidate.turn_id}",
            )
            admission = await self._store.claim_next(
                subject=self._subject,
                owner_id=self._owner_id,
                claim_idempotency_key=f"runtime-claim:{candidate.turn_id}",
                preparation_draft_id=draft.draft_id,
                preparation_draft_hash=draft.draft_hash,
                lease_seconds=self._lease_seconds,
            )
            if admission is None:
                return False
            snapshot = await self._store.current_snapshot(self._subject)
            if snapshot is None:
                raise ForegroundRuntimeError("foreground_runtime_claim_disappeared")
        elif snapshot.owner_id != self._owner_id:
            delay = snapshot.lease_expires_at - time.time()
            if delay > 0:
                await asyncio.sleep(min(delay, 1.0))
                return True
            await self._store.reclaim_expired(
                host_run_id=snapshot.host_run_id,
                new_owner_id=self._owner_id,
                expected_generation=snapshot.generation,
                lease_seconds=self._lease_seconds,
                idempotency_key=(
                    f"runtime-reclaim:{snapshot.host_run_id}:g{snapshot.generation + 1}"
                ),
            )
            snapshot = await self._store.current_snapshot(self._subject)
            if snapshot is None:
                raise ForegroundRuntimeError("foreground_runtime_reclaim_disappeared")

        await self._drive_claimed(snapshot.host_run_id, snapshot.sdk_run_id)
        current = await self._store.current_snapshot(self._subject)
        return current is None or current.state.value in {
            "COMPLETED",
            "FAILED",
            "STOPPED",
            "CANCELLED",
        }

    async def _drive_claimed(
        self, host_run_id: str, previously_bound_sdk_run_id: str | None
    ) -> None:
        snapshot = await self._store.current_snapshot(self._subject)
        if snapshot is None or snapshot.host_run_id != host_run_id:
            raise ForegroundRuntimeError("foreground_runtime_snapshot_changed")
        claimed = await self._store.read_claimed_execution(
            host_run_id=host_run_id,
            owner_id=self._owner_id,
            generation=snapshot.generation,
        )
        execution_session_id = _execution_session_id(host_run_id)
        request_id = f"foreground-request-{claimed.candidate.turn_id}"
        sdk_run_id = SdkRuntimeIngress._compute_run_id(
            execution_session_id,
            request_id,
            claimed.candidate.turn_id,
        ).value
        if previously_bound_sdk_run_id not in (None, sdk_run_id):
            raise ForegroundRuntimeError("foreground_runtime_sdk_binding_drift")

        provider = await self._provider.freeze(
            claimed=claimed,
            execution_session_id=execution_session_id,
            request_id=request_id,
            sdk_run_id=sdk_run_id,
        )
        tools = await self._tools.freeze(
            claimed=claimed,
            execution_session_id=execution_session_id,
            request_id=request_id,
            sdk_run_id=sdk_run_id,
        )
        context = await self._context.prepare(
            claimed=claimed,
            expected_context=ContextLineage(
                snapshot.context_snapshot_id,
                snapshot.context_snapshot_revision,
                snapshot.context_snapshot_hash,
            ),
            execution_session_id=execution_session_id,
            request_id=request_id,
            sdk_run_id=sdk_run_id,
            provider=provider,
            tools=tools,
        )
        bound_provider = await self._provider.bind(
            frozen=provider,
            context=context,
            tools=tools,
            execution_session_id=execution_session_id,
            request_id=request_id,
            sdk_run_id=sdk_run_id,
        )
        candidate = claimed.candidate
        if (
            candidate.task_scope_id is None
            or candidate.binding_set_revision < 1
            or candidate.binding_set_receipt_id is None
            or candidate.binding_set_receipt_hash is None
        ):
            raise ForegroundRuntimeError("foreground_runtime_route_authority_missing")
        if claimed.admission_receipt_id and claimed.admission_receipt_hash:
            host_ref = claimed.admission_receipt_id
            host_hash = claimed.admission_receipt_hash
            route_receipt_id = _uuid(
                f"foreground-initial-route:{host_run_id}:{host_ref}:{host_hash}"
            )
        else:
            # Compatibility for injected test stores that predate the durable
            # admission receipt fields. Production queue reads always provide
            # the immutable admission authority above.
            host_ref = (
                f"foreground-claimed-execution:{host_run_id}:g{claimed.generation}"
            )
            host_hash = claimed.claimed_execution_hash
            route_receipt_id = _uuid(
                f"foreground-initial-route:{host_run_id}:g{claimed.generation}"
            )
        route = ContextRouteReceipt(
            route_receipt_id,
            sdk_run_id,
            None,
            None,
            TaskScopeRoute.RESUME_EXISTING,
            candidate.task_scope_id,
            candidate.binding_set_revision,
            context.resume_refs,
            schema_version=3,
            binding_set_receipt_id=candidate.binding_set_receipt_id,
            binding_set_receipt_hash=candidate.binding_set_receipt_hash,
            origin=ContextRouteOrigin.HOST_INITIAL,
            host_authority_ref=host_ref,
            host_authority_hash=host_hash,
        )
        await self._context.verify_initial_route(route)
        turn_payload = _candidate_turn_payload(candidate)
        run_binding = bound_provider.binding
        catalog_generation = int(run_binding.catalog_generation)
        catalog_fingerprint = str(run_binding.catalog_fingerprint)
        budget_fingerprint = str(run_binding.budget_fingerprint)
        tool_names = tools.catalog.get("tool_names")
        if not isinstance(tool_names, (list, tuple)):
            raise ForegroundRuntimeError("foreground_runtime_tool_catalog_invalid")
        start_payload = {
            "input": {"text": context.current_text},
            "messages": [dict(item) for item in context.provider_messages],
            "capability_snapshot": {
                "tools": [str(item) for item in tool_names]
            },
            "context_metadata": {
                "session_id": execution_session_id,
                "root_run_id": host_run_id,
                "request_id": request_id,
                "task_scope_id": candidate.task_scope_id,
                "context_authority_ref": context.authority_ref,
                "context_authority_hash": context.authority_hash,
                "provider_authority_ref": bound_provider.authority_ref,
                "provider_authority_hash": bound_provider.authority_hash,
                "tool_authority_ref": tools.authority_ref,
                "tool_authority_hash": tools.authority_hash,
                "snapshot_id": context.snapshot_id,
                "run_binding": run_binding.to_record(),
                "tool_authority": dict(tools.run_start_record),
                "initial_route_receipt_id": route.receipt_id,
                "initial_route_receipt_hash": route.receipt_hash,
            },
            "turn": turn_payload,
        }
        execution_request_hash = canonical_hash(start_payload)
        await self._store.record_execution_preparation(
            host_run_id=host_run_id,
            owner_id=self._owner_id,
            generation=claimed.generation,
            context_ref=context.authority_ref,
            context_hash=context.authority_hash,
            provider_ref=bound_provider.authority_ref,
            provider_hash=bound_provider.authority_hash,
            tool_ref=tools.authority_ref,
            tool_hash=tools.authority_hash,
            execution_request_hash=execution_request_hash,
            idempotency_key=f"runtime-preparation:{host_run_id}",
        )
        start_request_hash = canonical_hash(
            {
                "sdk_run_id": sdk_run_id,
                "execution_request_hash": execution_request_hash,
                "route_receipt_hash": route.receipt_hash,
            }
        )
        await self._store.record_start_intent(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=self._owner_id,
            generation=claimed.generation,
            start_request_hash=start_request_hash,
            idempotency_key=f"runtime-start-intent:{host_run_id}",
        )
        await self._store.bind_sdk_run(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=self._owner_id,
            generation=claimed.generation,
            idempotency_key=f"runtime-sdk-bind:{host_run_id}",
        )

        should_start = previously_bound_sdk_run_id is None
        if previously_bound_sdk_run_id is not None:
            record = self._ingress.query(sdk_run_id)
            if record is None:
                outcomes = await self._store.read_start_observation_outcomes(
                    host_run_id=host_run_id,
                    owner_id=self._owner_id,
                    generation=claimed.generation,
                )
                await self._store.record_start_observation(
                    host_run_id=host_run_id,
                    sdk_run_id=sdk_run_id,
                    owner_id=self._owner_id,
                    generation=claimed.generation,
                    outcome="QUERY_MISSING",
                    error_code="foreground_runtime_orphaned_start",
                    idempotency_key=f"runtime-restart-query:{host_run_id}:missing",
                )
                if {"RETURNED", "QUERY_FOUND"}.intersection(outcomes):
                    await self._store.record_reconciliation(
                        host_run_id=host_run_id,
                        sdk_run_id=sdk_run_id,
                        owner_id=self._owner_id,
                        generation=claimed.generation,
                        observed_state="FAILED_CLOSED",
                        idempotency_key=f"runtime-reconcile:{host_run_id}:missing",
                    )
                    raise ForegroundRuntimeError(
                        "foreground_runtime_orphaned_start"
                    )
                await self._store.record_reconciliation(
                    host_run_id=host_run_id,
                    sdk_run_id=sdk_run_id,
                    owner_id=self._owner_id,
                    generation=claimed.generation,
                    observed_state="UNBOUND_RETRY",
                    idempotency_key=f"runtime-reconcile:{host_run_id}:pre-start",
                )
                should_start = True
            else:
                await self._store.record_start_observation(
                    host_run_id=host_run_id,
                    sdk_run_id=sdk_run_id,
                    owner_id=self._owner_id,
                    generation=claimed.generation,
                    outcome="QUERY_FOUND",
                    result_ref=f"sdk-run:{sdk_run_id}:v{getattr(record, 'version', 0)}",
                    result_hash=canonical_hash(
                        {
                            "run_id": sdk_run_id,
                            "state": str(
                                getattr(
                                    getattr(record, "state", None), "value", ""
                                )
                            ),
                            "version": int(getattr(record, "version", 0)),
                        }
                    ),
                    idempotency_key=f"runtime-restart-query:{host_run_id}:found",
                )
                current = await self._store.current_snapshot(self._subject)
                if current is not None and current.state is RunState.CLAIMED:
                    await self._store.record_sdk_started(
                        host_run_id=host_run_id,
                        sdk_run_id=sdk_run_id,
                        owner_id=self._owner_id,
                        generation=claimed.generation,
                        sdk_event_id=(
                            f"sdk-query:{sdk_run_id}:v{getattr(record, 'version', 0)}"
                        ),
                        idempotency_key=f"runtime-running:{host_run_id}",
                    )
        if should_start:
            start_returned = True
            try:
                receipt = await self._ingress.start(
                    session_id=execution_session_id,
                    request_id=request_id,
                    turn_id=candidate.turn_id,
                    payload=start_payload,
                    session_generation=catalog_generation,
                    tool_catalog_fingerprint=catalog_fingerprint,
                    provider_budget_fingerprint=budget_fingerprint,
                    initial_route_receipt=route,
                    initial_route_receipt_hash=route.receipt_hash,
                )
            except Exception as exc:
                await self._store.record_start_observation(
                    host_run_id=host_run_id,
                    sdk_run_id=sdk_run_id,
                    owner_id=self._owner_id,
                    generation=claimed.generation,
                    outcome="RAISED",
                    error_code=str(getattr(exc, "code", type(exc).__name__)),
                    idempotency_key=f"runtime-start-raised:{host_run_id}",
                )
                discovered = self._ingress.query(sdk_run_id)
                if discovered is None:
                    await self._store.record_start_observation(
                        host_run_id=host_run_id,
                        sdk_run_id=sdk_run_id,
                        owner_id=self._owner_id,
                        generation=claimed.generation,
                        outcome="QUERY_MISSING",
                        error_code="foreground_runtime_start_not_committed",
                        idempotency_key=f"runtime-start-query:{host_run_id}:missing",
                    )
                    await self._store.record_reconciliation(
                        host_run_id=host_run_id,
                        sdk_run_id=sdk_run_id,
                        owner_id=self._owner_id,
                        generation=claimed.generation,
                        observed_state="UNBOUND_RETRY",
                        idempotency_key=f"runtime-reconcile:{host_run_id}:start-missing",
                    )
                    raise
                await self._store.record_start_observation(
                    host_run_id=host_run_id,
                    sdk_run_id=sdk_run_id,
                    owner_id=self._owner_id,
                    generation=claimed.generation,
                    outcome="QUERY_FOUND",
                    result_ref=(
                        f"sdk-run:{sdk_run_id}:v{getattr(discovered, 'version', 0)}"
                    ),
                    result_hash=canonical_hash(
                        {
                            "run_id": sdk_run_id,
                            "state": str(
                                getattr(
                                    getattr(discovered, "state", None), "value", ""
                                )
                            ),
                            "version": int(getattr(discovered, "version", 0)),
                        }
                    ),
                    idempotency_key=f"runtime-start-query:{host_run_id}:found",
                )
                start_returned = False
                result_ref = (
                    f"sdk-query:{sdk_run_id}:v{getattr(discovered, 'version', 0)}"
                )
            if start_returned:
                if receipt.run_id != sdk_run_id:
                    raise ForegroundRuntimeError(
                        "foreground_runtime_ingress_identity_drift"
                    )
                result_ref = f"sdk-start:{sdk_run_id}:g{receipt.generation}"
                result_hash = canonical_hash(
                    {
                        "run_id": receipt.run_id,
                        "generation": receipt.generation,
                        "session_id": receipt.session_id,
                        "request_id": receipt.request_id,
                    }
                )
                await self._store.record_start_observation(
                    host_run_id=host_run_id,
                    sdk_run_id=sdk_run_id,
                    owner_id=self._owner_id,
                    generation=claimed.generation,
                    outcome="RETURNED",
                    result_ref=result_ref,
                    result_hash=result_hash,
                    idempotency_key=f"runtime-start-returned:{host_run_id}",
                )
            await self._store.record_sdk_started(
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                owner_id=self._owner_id,
                generation=claimed.generation,
                sdk_event_id=result_ref,
                idempotency_key=f"runtime-running:{host_run_id}",
            )
        await self._record_audit(
            "foreground.runtime.bound",
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            generation=claimed.generation,
            route_receipt_id=route.receipt_id,
            route_receipt_hash=route.receipt_hash,
        )
        await self._deliver_controls(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            generation=claimed.generation,
        )
        terminal = await self._observe_with_heartbeats(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            generation=claimed.generation,
        )
        if terminal is None:
            state = self._ingress.query(sdk_run_id)
            state_value = str(
                getattr(getattr(state, "state", None), "value", "")
            ).lower()
            observed = (
                "BOUND_WAITING" if state_value == "waiting" else "BOUND_RUNNING"
            )
            await self._store.record_reconciliation(
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                owner_id=self._owner_id,
                generation=claimed.generation,
                observed_state=observed,
                idempotency_key=f"runtime-reconcile:{host_run_id}:{state_value or 'running'}",
            )
            return
        await self._store.record_reconciliation(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=self._owner_id,
            generation=claimed.generation,
            observed_state="BOUND_TERMINAL",
            evidence_ref=terminal.sdk_event_id,
            evidence_hash=terminal.sdk_event_hash,
            idempotency_key=f"runtime-reconcile:{host_run_id}:terminal",
        )
        await self._store.record_sdk_terminal(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=self._owner_id,
            generation=claimed.generation,
            terminal_state=terminal.terminal_state,
            sdk_event_id=terminal.sdk_event_id,
            sdk_event_hash=terminal.sdk_event_hash,
            idempotency_key=f"runtime-terminal:{host_run_id}",
        )
        self._provider.mark_terminal(sdk_run_id, terminal.terminal_state.value.lower())
        self._tools.mark_terminal(sdk_run_id, terminal.terminal_state.value.lower())

    async def _deliver_controls(
        self, *, host_run_id: str, sdk_run_id: str, generation: int
    ) -> None:
        for signal in await self._store.pending_signals(host_run_id):
            if signal.generation != generation or signal.sdk_run_id != sdk_run_id:
                raise ForegroundRuntimeError("foreground_runtime_signal_generation_drift")
            if signal.control_kind in {ControlKind.CANCEL, ControlKind.STOP}:
                await self._ingress.cancel(sdk_run_id)
                sdk_signal_id = f"sdk-cancel:{signal.signal_id}"
            else:
                delivered = await self._ingress.signal(
                    run_id=sdk_run_id,
                    signal_id=signal.signal_id,
                    payload={"kind": signal.control_kind.value},
                )
                sdk_signal_id = delivered.delivery_id
            await self._store.acknowledge_signal(
                signal_id=signal.signal_id,
                sdk_run_id=sdk_run_id,
                owner_id=self._owner_id,
                generation=generation,
                sdk_signal_id=sdk_signal_id,
            )

    async def _observe_with_heartbeats(
        self, *, host_run_id: str, sdk_run_id: str, generation: int
    ) -> AuthenticatedTerminalObservation | None:
        stop = asyncio.Event()

        async def heartbeat() -> None:
            ordinal = 0
            interval = max(0.1, self._lease_seconds / 3)
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                except TimeoutError:
                    ordinal += 1
                    await self._store.heartbeat(
                        host_run_id=host_run_id,
                        owner_id=self._owner_id,
                        generation=generation,
                        lease_seconds=self._lease_seconds,
                        idempotency_key=(
                            f"runtime-heartbeat:{host_run_id}:g{generation}:{ordinal}"
                        ),
                    )

        task = asyncio.create_task(heartbeat())
        try:
            return await self._terminal.observe(
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                subject=self._subject,
                owner_id=self._owner_id,
                generation=generation,
            )
        finally:
            stop.set()
            await asyncio.gather(task, return_exceptions=True)


class SqliteSdkTerminalObserver:
    """Join SDK terminal state to an already-ingested authenticated S1 fact."""

    def __init__(
        self, db_path: str, ingress: SdkRuntimeIngress, runtime_stack: object
    ) -> None:
        self._db_path = db_path
        self._ingress = ingress
        self._runtime_stack = runtime_stack

    async def observe(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        subject: str,
        owner_id: str,
        generation: int,
    ) -> AuthenticatedTerminalObservation | None:
        del owner_id
        await self._ingress.wait_idle(sdk_run_id)
        record = self._ingress.query(sdk_run_id)
        raw_state = str(
            getattr(getattr(record, "state", None), "value", "")
        ).lower()
        states = {
            "completed": RunState.COMPLETED,
            "failed": RunState.FAILED,
            "cancelled": RunState.CANCELLED,
        }
        terminal = states.get(raw_state)
        if terminal is None:
            return None
        read_terminal = getattr(
            self._runtime_stack, "read_run_terminal_evidence", None
        )
        if not callable(read_terminal):
            raise ForegroundRuntimeError(
                "foreground_sdk_terminal_evidence_reader_unavailable"
            )
        sdk_evidence = read_terminal(sdk_run_id)
        if sdk_evidence is None:
            return None
        if (
            str(getattr(sdk_evidence, "run_id", sdk_run_id)) != sdk_run_id
            or str(getattr(sdk_evidence, "state", raw_state)) != raw_state
            or not str(getattr(sdk_evidence, "event_id", "")).strip()
            or len(str(getattr(sdk_evidence, "event_hash", ""))) != 64
        ):
            raise ForegroundRuntimeError(
                "foreground_sdk_terminal_evidence_mismatch"
            )
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT r.source_event_id,r.evidence_hash,e.payload_json "
                "FROM task_scope_execution_ingest_receipts r "
                "JOIN task_scope_events e ON e.event_id=r.event_id "
                "JOIN task_scope_terminal_gate_receipts g ON g.run_id=r.run_id "
                "AND g.task_scope_id=r.task_scope_id "
                "WHERE r.run_id=? AND r.evidence_kind='run_terminal' "
                "ORDER BY r.source_sequence DESC LIMIT 1",
                (sdk_run_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                authority_cursor = await db.execute(
                    "SELECT r.task_scope_id,t.evidence_id,t.evidence_hash,"
                    "COALESCE(MAX(i.source_sequence),0)+1 AS next_sequence "
                    "FROM foreground_runs r "
                    "JOIN foreground_turns t ON t.turn_id=r.turn_id "
                    "LEFT JOIN task_scope_execution_ingest_receipts i "
                    "ON i.run_id=? WHERE r.host_run_id=? "
                    "GROUP BY r.task_scope_id,t.evidence_id,t.evidence_hash",
                    (sdk_run_id, host_run_id),
                )
                authority = await authority_cursor.fetchone()
                await authority_cursor.close()
            else:
                authority = None
        if row is None:
            if authority is None or authority["task_scope_id"] is None:
                raise ForegroundRuntimeError(
                    "foreground_terminal_task_scope_authority_missing"
                )
            from simple_harness import (
                DeliveryRecipient,
                DisclosureContext,
                DisclosureGeneration,
                DisclosurePurpose,
                DisclosureReasonCode,
                DisclosureSource,
                DisclosureTrust,
                EvidenceRef,
                ExecutionEvidence,
                ExecutionEvidenceKind,
                IntendedAudience,
            )

            from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress

            disclosure = DisclosureContext(
                sdk_run_id,
                subject,
                DeliveryRecipient.USER_SELF,
                subject,
                IntendedAudience.USER_SELF,
                DisclosurePurpose.TASK_EXECUTION,
                DisclosureSource.AUTHENTICATED_HOST,
                DisclosureTrust.TRUSTED_AUTHORITY,
                DisclosureGeneration.CURRENT,
                f"sdk-terminal:{sdk_evidence.event_id}:{sdk_evidence.event_hash}",
                (DisclosureReasonCode.MINIMUM_NECESSARY,),
            )
            evidence = ExecutionEvidence(
                event_id=str(sdk_evidence.event_id),
                run_id=sdk_run_id,
                subject=subject,
                kind=ExecutionEvidenceKind.RUN_TERMINAL,
                public_payload={
                    "generation": generation,
                    "terminal_state": terminal.value,
                    "sdk_terminal_event_hash": str(sdk_evidence.event_hash),
                },
                disclosure_context=disclosure,
                evidence_refs=(
                    EvidenceRef(
                        str(authority["evidence_id"]),
                        str(authority["evidence_hash"]),
                        1,
                    ),
                ),
                idempotency_key=f"foreground-terminal:{sdk_evidence.event_id}",
                occurred_at=float(sdk_evidence.occurred_at),
            )
            evidence_ingress = ExecutionEvidenceIngress(self._db_path)
            await evidence_ingress.ingest(
                task_scope_id=str(authority["task_scope_id"]),
                source_sequence=int(authority["next_sequence"]),
                evidence=evidence,
            )
            await evidence_ingress.authorize_terminal(sdk_run_id)
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT r.source_event_id,r.evidence_hash,e.payload_json "
                    "FROM task_scope_execution_ingest_receipts r "
                    "JOIN task_scope_events e ON e.event_id=r.event_id "
                    "WHERE r.run_id=? AND r.source_event_id=?",
                    (sdk_run_id, str(sdk_evidence.event_id)),
                )
                row = await cursor.fetchone()
                await cursor.close()
        if row is None:
            return None
        try:
            evidence = json.loads(str(row["payload_json"]))
            public = evidence["public_payload"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if (
            evidence.get("subject") != subject
            or int(public.get("generation", 0)) != generation
            or str(public.get("terminal_state", "")).upper() != terminal.value
        ):
            return None
        return AuthenticatedTerminalObservation(
            terminal,
            str(row["source_event_id"]),
            str(row["evidence_hash"]),
        )


__all__ = (
    "AuthenticatedTerminalObservation",
    "BoundProviderAuthority",
    "ForegroundContextPreparationPort",
    "ForegroundProviderAuthorityPort",
    "ForegroundRuntimeAuditSink",
    "ForegroundRuntimeError",
    "ForegroundRuntimeExecutionAuthority",
    "ForegroundTerminalObserverPort",
    "ForegroundToolAuthorityPort",
    "FrozenContextAuthority",
    "FrozenProviderAuthority",
    "FrozenToolAuthority",
    "SqliteSdkTerminalObserver",
)
