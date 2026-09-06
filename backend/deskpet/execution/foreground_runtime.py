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
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
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
    EffectBoundary,
    ForegroundQueueError,
    ForegroundQueueStore,
    ForegroundRunSnapshot,
    PreparationCandidate,
    RunState,
)
from deskpet.sdk_adapters.ingress import SdkRuntimeIngress
from deskpet.task_scope.protocol import canonical_hash

# S5b Task 6 (review F-9): the only shape a FAILED terminal's public
# ``error_code`` may take; everything else degrades to the SDK public code.
TERMINAL_ERROR_CODE_TOKEN = re.compile(r"[a-z][a-z0-9_]{2,63}")
TERMINAL_ERROR_CODE_FALLBACK = "driver_failed"
# Task 4 review F-5: outbox dead-letter reason when the durable Run binding
# cannot be read at Host terminal time.
RUN_BINDING_UNAVAILABLE_REASON = "run_binding_unavailable"


class ForegroundRuntimeError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class FrozenProviderAuthority:
    host_run_id: str
    sdk_run_id: str
    owner_id: str
    generation: int
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
    host_run_id: str
    sdk_run_id: str
    owner_id: str
    generation: int
    authority_ref: str
    authority_hash: str
    catalog: Mapping[str, object]
    run_start_record: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class FrozenContextAuthority:
    host_run_id: str
    sdk_run_id: str
    owner_id: str
    generation: int
    authority_ref: str
    authority_hash: str
    snapshot_id: str
    provider_messages: tuple[Mapping[str, object], ...]
    current_text: str
    resume_refs: tuple[str, ...]
    visibility_dependencies: Mapping[str, object] | None = None
    scope_disclosure: Mapping[str, object] | None = None

    def __post_init__(self):
        if self.scope_disclosure is not None:
            from simple_harness import freeze_json, thaw_json
            object.__setattr__(self, "scope_disclosure", freeze_json(thaw_json(self.scope_disclosure)))
        if self.visibility_dependencies is not None:
            from simple_harness import freeze_json
            from deskpet.execution.primary_dependencies import parse_dependencies
            object.__setattr__(self, "visibility_dependencies", freeze_json(parse_dependencies(self.visibility_dependencies)))


@dataclass(frozen=True, slots=True)
class BoundProviderAuthority:
    host_run_id: str
    sdk_run_id: str
    owner_id: str
    generation: int
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
    def record(self, event: str, payload: Mapping[str, object]) -> None: ...


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


class ForegroundEffectAdmissionGate:
    """Final Tool-effect admission consulted by the physical effect executor.

    The foreground runtime registers the exact ``(host_run_id, sdk_run_id,
    owner_id, generation)`` lease of every Run it starts.  The product effect
    executor calls :meth:`authorize` immediately before dispatching a physical
    Tool effect; a reclaimed lease makes the durable admission fail, so a stale
    worker's Run can no longer produce external Tool side effects.  Runs never
    registered here (legacy chat ingress) are outside the foreground lease and
    pass through unchanged.
    """

    def __init__(self) -> None:
        self._bindings: dict[str, _ForegroundEffectBinding] = {}

    def register(
        self,
        *,
        store: ForegroundQueueStore,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        start_ready: asyncio.Event | None = None,
    ) -> None:
        self._bindings[sdk_run_id] = _ForegroundEffectBinding(
            store, host_run_id, sdk_run_id, owner_id, generation, start_ready
        )

    def release(self, sdk_run_id: str) -> None:
        self._bindings.pop(sdk_run_id, None)

    async def authorize(self, sdk_run_id: str) -> _ForegroundEffectBinding | None:
        binding = self._bindings.get(sdk_run_id)
        if binding is None:
            return
        # SDK.start schedules its driver before the Host start observation is
        # durable. Wait only for this captured invocation, then re-authorize the
        # unchanged owner/generation against the ordinary RUNNING-state fence.
        if binding.start_ready is not None:
            try:
                await asyncio.wait_for(binding.start_ready.wait(), timeout=5.0)
            except TimeoutError as exc:
                raise ForegroundRuntimeError("foreground_tool_start_barrier_timeout") from exc
        await binding.store.authorize_effect(
            host_run_id=binding.host_run_id,
            sdk_run_id=binding.sdk_run_id,
            owner_id=binding.owner_id,
            generation=binding.generation,
            boundary=EffectBoundary.TOOL,
        )
        return binding


@dataclass(frozen=True, slots=True)
class _ForegroundEffectBinding:
    store: ForegroundQueueStore
    host_run_id: str
    sdk_run_id: str
    owner_id: str
    generation: int
    start_ready: asyncio.Event | None = None


def resolve_host_terminal(
    raw_sdk_state: str, host_head_state: str | None
) -> RunState | None:
    """Resolve the Host terminal state from authenticated SDK evidence.

    SDK 0.7 reports ``completed``/``failed``/``cancelled`` only.  The Host
    keeps STOP and CANCEL as distinct terminal semantics: a Run whose durable
    head carries the STOP intent resolves SDK ``cancelled`` evidence to
    ``STOPPED``; a CANCEL intent (or no surviving intent) resolves it to
    ``CANCELLED``.  COMPLETED/FAILED always follow the SDK evidence — the Host
    never fabricates a cancellation terminal over a Run that actually
    finished.
    """

    mapped = {
        "completed": RunState.COMPLETED,
        "failed": RunState.FAILED,
        "cancelled": RunState.CANCELLED,
    }.get(raw_sdk_state)
    if mapped is RunState.CANCELLED and host_head_state == (
        RunState.STOP_REQUESTED.value
    ):
        return RunState.STOPPED
    return mapped


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
        effect_gate: ForegroundEffectAdmissionGate | None = None,
        lease_seconds: float = 300.0,
        closure_fallback: object | None = None,
        run_binding_reader: Callable[[str], object] | None = None,
        endpoint_identity_resolver: Callable[[Mapping[str, object]], str | None] | None = None,
        conversation_entrypoint: Callable[..., Awaitable[object]] | None = None,
        state_changed: Callable[[], Awaitable[None]] | None = None,
        terminal_audit_wake: Callable[[], None] | None = None,
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
        # 冻结 SDK ``runtime/kernel.py:729-733``：启用 Agent Memory 时 ``start()`` 必须带
        # ``conversation``，否则 ``conversation_entrypoint_required``。此前前台链调
        # ``ingress.start`` 时不传它，于是**生产上从未真正启动过一个前台 SDK Run**
        # （实测证据 ``.local-test-evidence/real-ui-channel/20260903T1640-wsentry``）。
        # pytest 车道用基座自建 runtime，绕过了这里，所以一直是绿的。
        self._conversation_entrypoint = conversation_entrypoint
        self._audit = audit_sink
        self._effect_gate = effect_gate
        # S5b Task 3: semantic-closure fallback between the SDK terminal
        # observation and the Host terminal commit (lease-fenced, replayable).
        self._closure_fallback = closure_fallback
        # S5b Task 4: the durable Run binding (``SdkRunBindingV1`` record) and the
        # Provider endpoint identity feed the terminal-commit Memory ingestion
        # outbox row (same transaction as the Host terminal).
        self._run_binding_reader = run_binding_reader
        self._endpoint_identity_resolver = endpoint_identity_resolver
        self._lease_seconds = float(lease_seconds)
        self._driver: asyncio.Task[None] | None = None
        # 驱动仍在运行时到达的唤醒：不能新建任务，但必须留痕，否则驱动一旦
        # 因「无进展」退出，这次唤醒就被永久丢弃（见 _run_driver 的退出复查）。
        self._rewake_pending = False
        # 「从未起过驱动」与「驱动跑完后置空引用」都会让 _driver 为 None，
        # 但 close() 对两者的处理不同：前者无租约可清，后者必须清。
        self._driver_started = False
        self._driver_lock = asyncio.Lock()
        self._control_wake = asyncio.Event()
        self._lease_task: asyncio.Task[None] | None = None
        self._lease_identity: tuple[str, int] | None = None
        self._closed = False
        self._last_error: Exception | None = None
        self._state_changed = state_changed
        self._terminal_audit_wake = terminal_audit_wake
        self._notification_task: asyncio.Task[None] | None = None
        self._notification_pending = False

    def _notify_state_changed(self) -> None:
        """Best-effort invalidation after commit; coalesce without delaying the driver."""
        if self._state_changed is None or self._closed:
            return
        self._notification_pending = True
        if self._notification_task is None or self._notification_task.done():
            self._notification_task = asyncio.create_task(
                self._flush_state_changes(), name="foreground-state-invalidation"
            )

    async def _flush_state_changes(self) -> None:
        while self._notification_pending and not self._closed:
            self._notification_pending = False
            try:
                async with asyncio.timeout(0.5):
                    await self._state_changed()
            except Exception:
                # Reconnection reads SQLite; this hint is neither ledger nor ACK.
                # Never publish callback errors, which may contain private data.
                pass

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
                self._rewake_pending = False
                self._driver_started = True
                self._driver = asyncio.create_task(
                    self._run_driver(),
                    name=f"foreground-runtime:{hashlib.sha256(subject.encode()).hexdigest()[:12]}",
                )
            else:
                # 驱动仍在跑 → 不新建任务。但这次唤醒必须留痕：驱动可能正处在
                # 本轮 _drive_once 的末尾、马上要因「无进展」退出，退出后就再没有
                # 东西去观察 SDK 终态。实测该丢失会让回合永停 CLAIMED、终态受理
                # 为空、Memory 摄入永不发生
                # （.local-test-evidence/real-ui-channel/20260904T120431）。
                self._rewake_pending = True

    async def after_control(self, *, subject: str) -> None:
        """Wake the active Run's control pump after a durable control commit.

        The durable control intent, reduced desired state, and signal outbox
        are already committed by the caller; this wake only shortens delivery
        latency for the in-process Runtime.  SQLite remains the authority — a
        missed wake is recovered by the pump's poll fallback and by restart
        reconciliation.
        """

        if subject != self._subject:
            raise ForegroundRuntimeError("foreground_runtime_subject_mismatch")
        if self._closed:
            raise ForegroundRuntimeError("foreground_runtime_closed")
        self._control_wake.set()
        self._notify_state_changed()
        await self.after_enqueue(subject=subject)

    async def drain(self) -> None:
        """Wait for the current process helper; tests and shutdown only."""

        task = self._driver
        if task is not None:
            await asyncio.shield(task)

    async def close(self, *, timeout: float = 5.0) -> None:
        self._closed = True
        if self._notification_task is not None:
            self._notification_task.cancel()
            await asyncio.gather(self._notification_task, return_exceptions=True)
        task = self._driver
        # ``_driver`` 为 None 有两种情形，处理不同：
        #  · 从未起过驱动 → 没有租约可清，照旧提前返回；
        #  · 驱动已正常退出并在退出时置空引用（见 _run_driver）→ **必须**继续
        #    走下面的租约清理，否则驱动跑完后租约永不关闭。
        if task is None and not self._driver_started and self._lease_task is None:
            return
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await self._stop_lease_keeper()
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

    def _record_audit(self, event: str, **payload: object) -> None:
        if self._audit is not None:
            self._audit.record(event, payload)

    def _terminal_binding(self, sdk_run_id: str) -> tuple[Mapping[str, object] | None, str | None]:
        """Durable ``SdkRunBindingV1`` record (+ endpoint identity) for the terminal outbox row."""

        reader = self._run_binding_reader
        if reader is None:
            return None, None
        try:
            facts = reader(sdk_run_id)
            record = getattr(facts, "binding_record", facts)
        except Exception as exc:  # noqa: BLE001 - the terminal must not stall on the Memory side
            self._record_audit(
                "foreground.runtime.run_binding_unavailable",
                sdk_run_id=sdk_run_id,
                error_code=str(getattr(exc, "code", type(exc).__name__)),
            )
            return None, None
        if not isinstance(record, Mapping) or not record:
            # Task 4 review F-5: a missing durable binding is a Memory-ingestion
            # problem, not a Host-terminal problem.  The terminal still commits;
            # the turn's outbox row is dead-lettered with this reason.
            self._record_audit(
                "foreground.runtime.run_binding_unavailable",
                sdk_run_id=sdk_run_id,
                error_code=RUN_BINDING_UNAVAILABLE_REASON,
            )
            return None, None
        endpoint = None
        if self._endpoint_identity_resolver is not None:
            endpoint = self._endpoint_identity_resolver(record)
        return dict(record), endpoint

    @staticmethod
    def _assert_authority_identity(
        authority: object,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
    ) -> None:
        observed = (
            str(getattr(authority, "host_run_id", "")),
            str(getattr(authority, "sdk_run_id", "")),
            str(getattr(authority, "owner_id", "")),
            int(getattr(authority, "generation", 0)),
        )
        if observed != (host_run_id, sdk_run_id, owner_id, generation):
            raise ForegroundRuntimeError(
                "foreground_runtime_authority_identity_drift"
            )

    async def _run_driver(self) -> None:
        self._last_error = None
        try:
            while not self._closed:
                progressed = await self._drive_once()
                if progressed:
                    continue
                # 无进展就该退出——但退出前必须在锁内复查唤醒标记，并把 _driver
                # 置空。置空是为了消除最后一点窗口：只复查标记的话，标记可能在
                # 「复查为假」与「任务真正 done()」之间被设上，而那一瞬 after_enqueue
                # 看到的 done() 仍是 False，于是既不新建任务、留下的痕迹也没人再看。
                # 置空后 after_enqueue 判 `is None` 成立，必定新建。
                async with self._driver_lock:
                    if self._rewake_pending:
                        self._rewake_pending = False
                        continue
                    self._driver = None
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - durable state remains recoverable
            self._last_error = exc
            self._notify_state_changed()
            # 只记 error_code 时，SQLite IntegrityError 这类异常会退化成一个无从下手的
            # 类名（实测 20260903T1700-wsentry 卡住时只看到 "IntegrityError"）。
            # 追加异常类型与消息：这条日志只进 Host 日志、不对模型可见，且内容是
            # 我们自己的约束名/表名，不含凭据。
            self._record_audit(
                "foreground.runtime.failed",
                error_code=str(getattr(exc, "code", type(exc).__name__)),
                error_type=type(exc).__name__,
                error_detail=str(exc)[:500],
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
            from deskpet.memory.trusted_disclosure import TrustedDisclosureError
            try:
                lineage = await self._context.draft_lineage(candidate)
            except TrustedDisclosureError as exc:
                if exc.code not in {"host_disclosure_binding_stale", "host_disclosure_legacy_policy_changed"}:
                    raise
                from deskpet.execution.admission_rejection import reject_stale_candidate
                await reject_stale_candidate(self._store, candidate)
                self._notify_state_changed()
                return True
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
            self._notify_state_changed()
            snapshot = await self._store.current_snapshot(self._subject)
            if snapshot is None:
                raise ForegroundRuntimeError("foreground_runtime_claim_disappeared")
        else:
            # The store's clock/fence is authoritative, including a still-live
            # Runtime waking after its own lease expired. Never infer freshness
            # from owner equality or compare a fixture clock to wall time.
            reclaim = snapshot.owner_id != self._owner_id
            if not reclaim:
                try:
                    await self._store.read_claimed_execution(
                        host_run_id=snapshot.host_run_id, owner_id=self._owner_id,
                        generation=snapshot.generation,
                    )
                except ForegroundQueueError as exc:
                    if exc.code != "foreground_lease_expired":
                        raise
                    reclaim = True
            if reclaim:
                try:
                    await self._store.reclaim_expired(
                        host_run_id=snapshot.host_run_id,
                        new_owner_id=self._owner_id,
                        expected_generation=snapshot.generation,
                        lease_seconds=self._lease_seconds,
                        idempotency_key=f"runtime-reclaim:{snapshot.host_run_id}:g{snapshot.generation + 1}",
                    )
                except ForegroundQueueError as exc:
                    if exc.code != "foreground_lease_not_expired":
                        raise
                    await asyncio.sleep(min(1.0, self._lease_seconds / 3))
                    return True
                snapshot = await self._store.current_snapshot(self._subject)
                if snapshot is None:
                    raise ForegroundRuntimeError("foreground_runtime_reclaim_disappeared")

        await self._drive_with_lease(snapshot)
        current = await self._store.current_snapshot(self._subject)
        return current is None or current.state.value in {
            "COMPLETED",
            "FAILED",
            "STOPPED",
            "CANCELLED",
        }

    async def _stop_lease_keeper(self) -> None:
        task, self._lease_task = self._lease_task, None
        self._lease_identity = None
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _drive_with_lease(self, snapshot: ForegroundRunSnapshot) -> None:
        """Maintain ownership while the driver is idle at permission WAITING.

        The keeper is Runtime-owned and joined on terminal/close/lease loss.
        Its random incarnation is only an idempotency namespace, never authority.
        """
        identity = (snapshot.host_run_id, snapshot.generation)
        if self._lease_identity != identity or self._lease_task is None or self._lease_task.done():
            await self._stop_lease_keeper()
            self._lease_identity = identity
            self._lease_task = asyncio.create_task(self._maintain_lease(snapshot), name="foreground-lease-keeper")
            # Retrieve idle failures too; the next driver still observes them.
            self._lease_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        keeper = self._lease_task
        work = asyncio.create_task(self._drive_claimed(snapshot.host_run_id, snapshot.sdk_run_id))
        succeeded = False
        try:
            done, _ = await asyncio.wait((work, keeper), return_when=asyncio.FIRST_COMPLETED)
            if keeper in done:
                await keeper  # stop this owner's work immediately on lost lease
            await work
            succeeded = True
        finally:
            if not work.done():
                work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            current = await self._store.current_snapshot(self._subject)
            if not succeeded or current is None or (current.host_run_id, current.generation) != identity:
                await self._stop_lease_keeper()

    async def _maintain_lease(self, snapshot: ForegroundRunSnapshot) -> None:
        incarnation, ordinal = uuid.uuid4().hex, 0
        interval = self._lease_seconds / 3
        renew_at = asyncio.get_running_loop().time() + interval
        try:
            while True:
                await asyncio.sleep(min(1.0, interval))
                current = await self._store.current_snapshot(self._subject)
                if current is None:
                    return  # the authenticated Host terminal has committed
                if (current.host_run_id, current.owner_id, current.generation) != (
                    snapshot.host_run_id, self._owner_id, snapshot.generation
                ):
                    raise ForegroundQueueError("foreground_generation_stale")
                if asyncio.get_running_loop().time() >= renew_at:
                    ordinal += 1
                    await self._store.heartbeat(
                        host_run_id=snapshot.host_run_id, owner_id=self._owner_id,
                        generation=snapshot.generation, lease_seconds=self._lease_seconds,
                        idempotency_key=f"runtime-heartbeat:{snapshot.host_run_id}:g{snapshot.generation}:{incarnation}:{ordinal}",
                    )
                    renew_at = asyncio.get_running_loop().time() + interval
                # Durable controls can be committed by another connection.
                # Waiting alone must not busy-reprepare the original history.
                if not self._closed and current.sdk_run_id is not None and (self._driver is None or self._driver.done()):
                    record = self._ingress.query(current.sdk_run_id)
                    state = str(getattr(getattr(record, "state", None), "value", ""))
                    if state in {"completed", "failed", "cancelled"} or (
                        current.desired_control is not None and await self._store.pending_signals(current.host_run_id)
                    ):
                        await self.after_enqueue(subject=self._subject)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = exc
            self._notify_state_changed()
            self._record_audit("foreground.runtime.lease_keeper_failed",
                host_run_id=snapshot.host_run_id, generation=snapshot.generation,
                error_code=str(getattr(exc, "code", type(exc).__name__)))
            raise

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

        # Recovery consumes the actual durable Run, not a newly prepared copy
        # of its original context. In particular, forgetting old sources must
        # not prevent STOP or the recording of an already-failed SDK terminal.
        record = None if previously_bound_sdk_run_id is None else self._ingress.query(sdk_run_id)
        if record is not None:
            if (getattr(record, "run_id", None), getattr(record, "execution_session_id", None),
                getattr(record, "request_id", None)) != (sdk_run_id, execution_session_id, request_id):
                raise ForegroundRuntimeError("foreground_runtime_sdk_binding_drift")
            await self._store.bind_sdk_run(
                host_run_id=host_run_id, sdk_run_id=sdk_run_id, owner_id=self._owner_id,
                generation=claimed.generation, idempotency_key=f"runtime-sdk-bind:{host_run_id}",
            )
            await self._store.record_start_observation(
                host_run_id=host_run_id, sdk_run_id=sdk_run_id, owner_id=self._owner_id,
                generation=claimed.generation, outcome="QUERY_FOUND",
                result_ref=f"sdk-run:{sdk_run_id}:v{getattr(record, 'version', 0)}",
                result_hash=canonical_hash({"run_id": sdk_run_id,
                    "state": str(getattr(getattr(record, "state", None), "value", "")),
                    "version": int(getattr(record, "version", 0))}),
                idempotency_key=f"runtime-recovery-query:{host_run_id}:g{claimed.generation}:v{getattr(record, 'version', 0)}",
            )
            if snapshot.state is RunState.CLAIMED:
                await self._store.record_sdk_started(
                    host_run_id=host_run_id, sdk_run_id=sdk_run_id, owner_id=self._owner_id,
                    generation=claimed.generation,
                    sdk_event_id=f"sdk-query:{sdk_run_id}:v{getattr(record, 'version', 0)}",
                    idempotency_key=f"runtime-recovered-running:{host_run_id}:g{claimed.generation}",
                )
            if self._effect_gate is not None:
                self._effect_gate.register(store=self._store, host_run_id=host_run_id,
                    sdk_run_id=sdk_run_id, owner_id=self._owner_id, generation=claimed.generation)
            await self._finish_bound(claimed, sdk_run_id)
            return

        def _check_identity(authority: object) -> None:
            self._assert_authority_identity(
                authority,
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                owner_id=self._owner_id,
                generation=claimed.generation,
            )
        provider = await self._provider.freeze(
            claimed=claimed,
            execution_session_id=execution_session_id,
            request_id=request_id,
            sdk_run_id=sdk_run_id,
        )
        _check_identity(provider)
        tools = await self._tools.freeze(
            claimed=claimed,
            execution_session_id=execution_session_id,
            request_id=request_id,
            sdk_run_id=sdk_run_id,
        )
        _check_identity(tools)
        from deskpet.execution.preparation_rejection import PreparationDisclosureRejected

        try:
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
        except PreparationDisclosureRejected as exc:
            rejection = exc.rejection
            candidate = claimed.candidate
            if (
                rejection.host_run_id != host_run_id
                or rejection.subject != candidate.subject
                or rejection.turn_id != candidate.turn_id
                or rejection.turn_hash != candidate.turn_hash
                or rejection.candidate_hash != candidate.candidate_hash
                or rejection.evidence_id != candidate.evidence_id
                or rejection.evidence_hash != candidate.evidence_hash
            ):
                raise ForegroundRuntimeError("foreground_preparation_rejection_identity_mismatch") from exc
            await self._store.settle_preparation_rejection(
                rejection=exc.rejection, owner_id=claimed.owner_id, generation=claimed.generation,
            )
            self._notify_state_changed()
            return
        _check_identity(context)
        bound_provider = await self._provider.bind(
            frozen=provider,
            context=context,
            tools=tools,
            execution_session_id=execution_session_id,
            request_id=request_id,
            sdk_run_id=sdk_run_id,
        )
        _check_identity(bound_provider)
        candidate = claimed.candidate
        if candidate.task_scope_id is not None and (
            candidate.binding_set_revision < 1
            or candidate.binding_set_receipt_id is None
            or candidate.binding_set_receipt_hash is None
        ):
            raise ForegroundRuntimeError("foreground_runtime_route_authority_missing")
        if not claimed.admission_receipt_id or not claimed.admission_receipt_hash:
            raise ForegroundRuntimeError(
                "foreground_runtime_admission_receipt_missing"
            )
        route = None
        if candidate.task_scope_id is not None:
            host_ref = claimed.admission_receipt_id
            host_hash = claimed.admission_receipt_hash
            route_receipt_id = _uuid(
                f"foreground-initial-route:{host_run_id}:{host_ref}:{host_hash}"
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
        from simple_harness import thaw_json
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
                "visibility_dependencies": thaw_json(context.visibility_dependencies),
                "primary_effect_index_version": 1,
                "scope_disclosure": thaw_json(context.scope_disclosure),
                "context_authority_ref": context.authority_ref,
                "context_authority_hash": context.authority_hash,
                "provider_authority_ref": bound_provider.authority_ref,
                "provider_authority_hash": bound_provider.authority_hash,
                "tool_authority_ref": tools.authority_ref,
                "tool_authority_hash": tools.authority_hash,
                "snapshot_id": context.snapshot_id,
                "run_binding": run_binding.to_record(),
                "tool_authority": dict(tools.run_start_record),
                "initial_route_receipt_id": None if route is None else route.receipt_id,
                "initial_route_receipt_hash": None if route is None else route.receipt_hash,
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
                "route_receipt_hash": None if route is None else route.receipt_hash,
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
        self._notify_state_changed()
        tool_start_ready = asyncio.Event()
        if self._effect_gate is not None:
            self._effect_gate.register(
                store=self._store,
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                owner_id=self._owner_id,
                generation=claimed.generation,
                start_ready=tool_start_ready,
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
                        idempotency_key=(
                            f"runtime-reconcile:{host_run_id}:g{claimed.generation}:missing"
                        ),
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
                    idempotency_key=(
                        f"runtime-reconcile:{host_run_id}:g{claimed.generation}:pre-start"
                    ),
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
                    # 同上：驱动重入时 record.version 会变，键必须含版本，
                    # 否则同键写不同 result_hash 触发
                    # ``foreground_execution_start_observation_idempotency_conflict``
                    # （实测 .local-test-evidence/real-ui-channel/verify-03）。
                    idempotency_key=(
                        f"runtime-restart-query:{host_run_id}:found:"
                        f"v{getattr(record, 'version', 0)}"
                    ),
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
            await self._store.authorize_effect(
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                owner_id=self._owner_id,
                generation=claimed.generation,
                boundary=EffectBoundary.SDK_START,
            )
            start_returned = True
            try:
                conversation = None
                if self._conversation_entrypoint is not None:
                    conversation = await self._conversation_entrypoint(
                        session_id=execution_session_id,
                        sdk_run_id=sdk_run_id,
                        text=context.current_text,
                        context_snapshot_id=context.snapshot_id,
                        provider_messages=context.provider_messages,
                    )
                receipt = await self._ingress.start(
                    session_id=execution_session_id,
                    request_id=request_id,
                    turn_id=candidate.turn_id,
                    payload=start_payload,
                    session_generation=catalog_generation,
                    tool_catalog_fingerprint=catalog_fingerprint,
                    provider_budget_fingerprint=budget_fingerprint,
                    conversation=conversation,
                    initial_route_receipt=route,
                    initial_route_receipt_hash=None if route is None else route.receipt_hash,
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
                        idempotency_key=(
                            f"runtime-reconcile:{host_run_id}:g{claimed.generation}:start-missing"
                        ),
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
                    # 幂等键必须含 Run 版本：驱动可能被多次唤醒（授权决策后
                    # `after_control` 会重入），每次 query 到的 record.version 不同，
                    # 用同一把键写不同内容会撞
                    # ``foreground_execution_start_observation_idempotency_conflict``
                    # （实测 .local-test-evidence/real-ui-channel/final-ui）。
                    idempotency_key=(
                        f"runtime-start-query:{host_run_id}:found:"
                        f"v{getattr(discovered, 'version', 0)}"
                    ),
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
        tool_start_ready.set()
        self._notify_state_changed()
        self._record_audit(
            "foreground.runtime.bound",
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            generation=claimed.generation,
            route_receipt_id=None if route is None else route.receipt_id,
            route_receipt_hash=None if route is None else route.receipt_hash,
        )
        await self._finish_bound(claimed, sdk_run_id)

    async def _finish_bound(self, claimed: ClaimedExecution, sdk_run_id: str) -> None:
        host_run_id = claimed.host_run_id
        record = self._ingress.query(sdk_run_id)
        state = str(getattr(getattr(record, "state", None), "value", ""))
        if resolve_host_terminal(state, None) is None:
            await self._deliver_controls(host_run_id=host_run_id, sdk_run_id=sdk_run_id, generation=claimed.generation)
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
                idempotency_key=(
                    f"runtime-reconcile:{host_run_id}:g{claimed.generation}:"
                    f"{state_value or 'running'}"
                ),
            )
            if observed == "BOUND_WAITING":
                self._notify_state_changed()
            return
        await self._store.record_reconciliation(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=self._owner_id,
            generation=claimed.generation,
            observed_state="BOUND_TERMINAL",
            evidence_ref=terminal.sdk_event_id,
            evidence_hash=terminal.sdk_event_hash,
            idempotency_key=(
                f"runtime-reconcile:{host_run_id}:g{claimed.generation}:terminal"
            ),
        )
        if self._closure_fallback is not None:
            # SDK terminal observed (final answer already delivered by the SDK
            # pump) → drain done by the observer → semantic closure fallback →
            # Host terminal.  Only the current lease owner may call the
            # Provider; a lost lease / crash is reconciled by the next owner.
            settlement = await self._closure_fallback.settle(
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                owner_id=self._owner_id,
                generation=claimed.generation,
                terminal_state=terminal.terminal_state,
            )
            self._record_audit(
                "foreground.runtime.closure_settled",
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                status=settlement.status,
                reason_code=settlement.reason_code,
                provider_calls=settlement.provider_calls,
            )
            if settlement.status == "lease_lost":
                raise ForegroundRuntimeError("foreground_runtime_lease_lost_during_closure")
        run_binding, endpoint_identity = self._terminal_binding(sdk_run_id)
        await self._store.record_sdk_terminal(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=self._owner_id,
            generation=claimed.generation,
            terminal_state=terminal.terminal_state,
            sdk_event_id=terminal.sdk_event_id,
            sdk_event_hash=terminal.sdk_event_hash,
            idempotency_key=f"runtime-terminal:{host_run_id}",
            run_binding=run_binding,
            endpoint_identity=endpoint_identity,
            outbox_dead_letter_reason=(
                RUN_BINDING_UNAVAILABLE_REASON
                if run_binding is None and self._run_binding_reader is not None
                else None
            ),
        )
        if self._terminal_audit_wake is not None:
            try:
                self._terminal_audit_wake()
            except Exception:
                # Wake is only an optimization. Durable terminal discovery recovers
                # missed notifications without changing the business result.
                pass
        self._notify_state_changed()
        self._provider.mark_terminal(sdk_run_id, terminal.terminal_state.value.lower())
        self._tools.mark_terminal(sdk_run_id, terminal.terminal_state.value.lower())
        if self._effect_gate is not None:
            self._effect_gate.release(sdk_run_id)

    async def _deliver_controls(
        self, *, host_run_id: str, sdk_run_id: str, generation: int
    ) -> None:
        for signal in await self._store.pending_signals(host_run_id):
            if signal.generation != generation or signal.sdk_run_id != sdk_run_id:
                raise ForegroundRuntimeError("foreground_runtime_signal_generation_drift")
            # Final current-generation admission immediately before the
            # externally visible SDK control side effect.  A lease reclaimed
            # between the durable signal read and this send fails here, so a
            # stale worker never cancels or signals the SDK Run.
            await self._store.authorize_effect(
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                owner_id=self._owner_id,
                generation=generation,
                boundary=EffectBoundary.SDK_CONTROL,
            )
            if signal.control_kind is ControlKind.CANCEL:
                await self._ingress.cancel(sdk_run_id)
                sdk_signal_id = f"sdk-cancel:{signal.signal_id}"
            elif signal.control_kind is ControlKind.STOP:
                # STOP keeps its own signal identity and terminal semantics.
                # SDK 0.7 halts a Run only through cancellation; the durable
                # STOP_REQUESTED head plus this distinct ack resolve the SDK
                # cancelled evidence to the Host STOPPED terminal state.
                await self._ingress.cancel(sdk_run_id)
                sdk_signal_id = f"sdk-stop:{signal.signal_id}"
            else:
                delivered = await self._ingress.signal(
                    run_id=sdk_run_id,
                    signal_id=signal.signal_id,
                    payload={"kind": signal.control_kind.value},
                )
                sdk_signal_id = delivered.delivery_id
            try:
                await self._store.acknowledge_signal(
                    signal_id=signal.signal_id,
                    sdk_run_id=sdk_run_id,
                    owner_id=self._owner_id,
                    generation=generation,
                    sdk_signal_id=sdk_signal_id,
                )
            except ForegroundQueueError as exc:
                if exc.code == "foreground_signal_superseded":
                    # A higher-priority control committed between the durable
                    # signal read and this ack.  Supersession invalidates only
                    # THIS signal — this worker still holds the lease and the
                    # superseding signal is pending, so skip and let the next
                    # delivery pass (pump wake or poll) send it.
                    continue
                raise
            if signal.control_kind is ControlKind.PAUSE:
                try:
                    await self._store.record_pause_outcome(
                        host_run_id=host_run_id,
                        sdk_run_id=sdk_run_id,
                        owner_id=self._owner_id,
                        generation=generation,
                        sdk_event_id=sdk_signal_id,
                        paused=True,
                        idempotency_key=f"runtime-pause:{signal.signal_id}",
                    )
                except ForegroundQueueError as exc:
                    if exc.code == "foreground_state_transition_invalid":
                        # A superseding STOP/CANCEL moved the head off
                        # PAUSE_REQUESTED after the ack; the pause outcome is
                        # moot and the superseding signal delivers next pass.
                        continue
                    raise
                self._notify_state_changed()
                self._record_audit(
                    "foreground.runtime.paused",
                    host_run_id=host_run_id,
                    sdk_run_id=sdk_run_id,
                    generation=generation,
                    signal_id=signal.signal_id,
                )

    async def _pump_controls(
        self, *, host_run_id: str, sdk_run_id: str, generation: int
    ) -> None:
        """Deliver durably committed controls to the active Run immediately.

        Runs alongside terminal observation.  ``after_control`` wakes the pump
        as soon as a control commits; a short poll fallback covers commits from
        other processes.  Stale-generation and terminal rejections end the pump
        because this worker may no longer signal the Run.
        """

        poll_interval = min(1.0, self._lease_seconds / 3)
        while True:
            try:
                await asyncio.wait_for(
                    self._control_wake.wait(), timeout=poll_interval
                )
            except TimeoutError:
                pass
            self._control_wake.clear()
            try:
                await self._deliver_controls(
                    host_run_id=host_run_id,
                    sdk_run_id=sdk_run_id,
                    generation=generation,
                )
            except ForegroundQueueError as exc:
                # Only lease-loss/terminal rejections end the pump — this
                # worker may no longer signal the Run.  Supersession races are
                # handled per-signal inside _deliver_controls and must NOT end
                # delivery: the superseding higher-priority control is still
                # pending and this worker still owns the lease.
                if exc.code in {
                    "foreground_generation_stale",
                    "foreground_lease_expired",
                    "foreground_run_already_terminal",
                }:
                    return
                raise

    async def _observe_with_heartbeats(
        self, *, host_run_id: str, sdk_run_id: str, generation: int
    ) -> AuthenticatedTerminalObservation | None:
        record = self._ingress.query(sdk_run_id)
        state = str(getattr(getattr(record, "state", None), "value", ""))
        if resolve_host_terminal(state, None) is not None:
            return await self._terminal.observe(host_run_id=host_run_id, sdk_run_id=sdk_run_id,
                subject=self._subject, owner_id=self._owner_id, generation=generation)
        pump_task = asyncio.create_task(
            self._pump_controls(
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                generation=generation,
            )
        )
        try:
            return await self._terminal.observe(
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                subject=self._subject,
                owner_id=self._owner_id,
                generation=generation,
            )
        finally:
            pump_task.cancel()
            results = await asyncio.gather(
                pump_task, return_exceptions=True
            )
            for result in results:
                if isinstance(result, Exception) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    # Terminal observation already succeeded; the durable
                    # ledger stays authoritative, so surface the helper
                    # failure without discarding the authenticated terminal.
                    self._record_audit(
                        "foreground.runtime.helper_failed",
                        host_run_id=host_run_id,
                        error_code=str(
                            getattr(result, "code", type(result).__name__)
                        ),
                    )


class SqliteSdkTerminalObserver:
    """Join SDK terminal state to an already-ingested authenticated S1 fact.

    S5b Task 1: on durable FAILED the ``run_terminal`` evidence carries a
    stable ``public_payload.error_code`` — the Host whole-Run fault code from
    ``run_fault_memo`` (``sdk_task_execution_route_authority_missing``,
    ``sdk_task_execution_root_authority_ambiguous|missing``,
    ``catalog_execution_policy_unavailable``) when the Host raised it, else the
    SDK public code (``driver_failed`` …).  No new host.* event kind is added.
    """

    def __init__(
        self,
        db_path: str,
        ingress: SdkRuntimeIngress,
        runtime_stack: object,
        *,
        run_fault_memo: object | None = None,
        fault_inject: Callable[[str], None] | None = None,
    ) -> None:
        self._db_path = db_path
        self._ingress = ingress
        self._runtime_stack = runtime_stack
        self._run_fault_memo = run_fault_memo
        self._fault_inject = fault_inject

    def _terminal_error_code(self, sdk_run_id: str, sdk_evidence: object) -> str | None:
        """Stable ``error_code`` of a FAILED terminal: Host memo first, else SDK public code.

        S5b Task 6 (review F-9): the value lands in ``run_terminal.public_payload``
        (durable, disclosable), so only a stable token
        (``^[a-z][a-z0-9_]{2,63}$``) is accepted; anything else — a path, an
        exception message, a tampered payload — degrades to ``driver_failed``.
        """

        memo = self._run_fault_memo
        read = getattr(memo, "read", None)
        code = read(sdk_run_id) if callable(read) else None
        if not code:
            code = getattr(sdk_evidence, "error_code", None)
        code = str(code or "").strip()
        if not code:
            return None
        if TERMINAL_ERROR_CODE_TOKEN.fullmatch(code) is None:
            return TERMINAL_ERROR_CODE_FALLBACK
        return code

    async def observe(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        subject: str,
        owner_id: str,
        generation: int,
    ) -> AuthenticatedTerminalObservation | None:
        await self._ingress.wait_idle(sdk_run_id)
        record = self._ingress.query(sdk_run_id)
        raw_state = str(
            getattr(getattr(record, "state", None), "value", "")
        ).lower()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT current_state FROM foreground_run_heads WHERE host_run_id=?",
                (host_run_id,),
            )
            head = await cursor.fetchone()
            await cursor.close()
        terminal = resolve_host_terminal(
            raw_state, None if head is None else str(head[0])
        )
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
        from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress
        evidence_ingress = ExecutionEvidenceIngress(self._db_path, fault_inject=self._fault_inject)
        effective_scope = await evidence_ingress.resolve_run_scope(sdk_run_id)
        read_messages = getattr(self._runtime_stack, "read_primary_run_messages", None)
        if callable(read_messages):
            async with aiosqlite.connect(self._db_path) as source_db:
                cursor = await source_db.execute(
                    "SELECT t.turn_json FROM foreground_runs r "
                    "JOIN foreground_turns t ON t.turn_id=r.turn_id "
                    "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id "
                    "WHERE r.host_run_id=? AND b.sdk_run_id=? AND r.subject=?",
                    (host_run_id, sdk_run_id, subject),
                )
                turn = await cursor.fetchone()
            if turn is None:
                raise ForegroundRuntimeError("foreground_terminal_run_binding_missing")
            text = json.loads(turn[0])["payload"]["text"]
            messages = read_messages(sdk_run_id, current_text=text)
            from deskpet.execution.primary_dependencies import read_run_dependencies
            proof = None
            if callable(getattr(self._runtime_stack, "read_primary_dependency_facts", None)):
                async with aiosqlite.connect(self._db_path) as dependency_db:
                    dependency_db.row_factory = aiosqlite.Row
                    try:
                        source = await read_run_dependencies(db=dependency_db,
                            stack=self._runtime_stack, sdk_run_id=sdk_run_id)
                        proof = None if source is None else source[1]
                    except (ValueError, TypeError, KeyError):
                        # Unverifiable/old source stays archived; never fabricate
                        # an empty complete proof to make generated history visible.
                        proof = None
            from deskpet.execution.primary_history import record_terminal_observation
            tool_sources = None
            read_tool_sources = getattr(self._runtime_stack, "read_primary_tool_causal_sources", None)
            if terminal is RunState.COMPLETED and any(message.get("role") == "tool" for message in messages):
                from deskpet.memory.primary_tool_causality import PrimaryToolCausalityUnavailable
                if callable(read_tool_sources):
                    try:
                        tool_sources = await read_tool_sources(db_path=self._db_path,
                            host_run_id=host_run_id, run_id=sdk_run_id, subject=subject,
                            current_text=text, messages=messages)
                    except PrimaryToolCausalityUnavailable:
                        # Archive the complete original terminal. A missing or
                        # incomplete tool source never permits partial indexing.
                        tool_sources = None
            primary_event_id, primary_event_hash = await record_terminal_observation(
                self._db_path, host_run_id=host_run_id, sdk_run_id=sdk_run_id, subject=subject,
                owner_id=owner_id, generation=generation, terminal=terminal,
                sdk_evidence=sdk_evidence, messages=messages, visibility_dependencies=proof,
                tool_causal_sources=tool_sources,
                error_code=self._terminal_error_code(sdk_run_id, sdk_evidence) if terminal is RunState.FAILED else None,
            )
            if effective_scope is None:
                if self._fault_inject is not None:
                    self._fault_inject("primary-terminal-observed")
                return AuthenticatedTerminalObservation(terminal, primary_event_id, primary_event_hash)
        elif effective_scope is None:
            raise ForegroundRuntimeError("foreground_primary_message_reader_unavailable")
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            # S5b Task 6 (review F-6): the durable run_terminal row is looked up
            # WITHOUT requiring the gate receipt, so a retry after a crash
            # between ingest and authorize_terminal never rebuilds the
            # evidence (the memo may be gone; a rebuilt payload would conflict)
            # — it only re-runs the idempotent authorize step.
            cursor = await db.execute(
                "SELECT r.source_event_id,r.evidence_hash,e.payload_json "
                "FROM task_scope_execution_ingest_receipts r "
                "JOIN task_scope_events e ON e.event_id=r.event_id "
                "WHERE r.run_id=? AND r.evidence_kind='run_terminal' "
                "ORDER BY r.source_sequence DESC LIMIT 1",
                (sdk_run_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            gate_cursor = await db.execute(
                "SELECT 1 FROM task_scope_terminal_gate_receipts WHERE run_id=?",
                (sdk_run_id,),
            )
            gate_receipt = await gate_cursor.fetchone()
            await gate_cursor.close()
            if row is None:
                authority_cursor = await db.execute(
                    "SELECT r.task_scope_id,t.evidence_id,t.evidence_hash "
                    "FROM foreground_runs r "
                    "JOIN foreground_turns t ON t.turn_id=r.turn_id "
                    "WHERE r.host_run_id=?",
                    (host_run_id,),
                )
                authority = await authority_cursor.fetchone()
                await authority_cursor.close()
            else:
                authority = None
        from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress

        evidence_ingress = ExecutionEvidenceIngress(
            self._db_path, fault_inject=self._fault_inject
        )
        if row is None:
            if authority is None or effective_scope is None:
                raise ForegroundRuntimeError(
                    "foreground_terminal_task_scope_authority_missing"
                )
            # S5b Task 2 (design-freeze §3): the terminal observer is the
            # single drainer.  Every still-reserved Harness row is resolved
            # first — ingested from the SDK ledger when its fact is readable
            # (objective event included), otherwise a same-kind tombstone —
            # and only then is run_terminal allocated at
            # MAX(reservations ∪ receipts) + 1.  Replayed by the next owner
            # after a crash (idempotent on source_event_id).
            fact_reader = (
                self._runtime_stack
                if callable(getattr(self._runtime_stack, "read_reserved_fact", None))
                else None
            )
            await evidence_ingress.drain_reservations(sdk_run_id, fact_reader=fact_reader)
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
            public_payload: dict[str, object] = {
                "generation": generation,
                "terminal_state": terminal.value,
                "sdk_terminal_event_hash": str(sdk_evidence.event_hash),
            }
            if terminal is RunState.FAILED:
                error_code = self._terminal_error_code(sdk_run_id, sdk_evidence)
                if error_code is not None:
                    public_payload["error_code"] = error_code
            evidence = ExecutionEvidence(
                event_id=str(sdk_evidence.event_id),
                run_id=sdk_run_id,
                subject=subject,
                kind=ExecutionEvidenceKind.RUN_TERMINAL,
                public_payload=public_payload,
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
            # S5b Task 6 (Task 2 review F-6): run_terminal is reserved
            # (``terminal:{sdk_run_id}``) and ingested in ONE transaction —
            # the sequence is allocated under the write lock, never read
            # outside a transaction.
            await evidence_ingress.ingest_terminal(
                task_scope_id=effective_scope.task_scope_id,
                evidence=evidence,
            )
            # The stable code is now durable in the run_terminal row: release
            # the process-local memo here, before any later step can raise
            # (review F-6) — a retry reads the durable row, not the memo.
            release = getattr(self._run_fault_memo, "release", None)
            if callable(release):
                release(sdk_run_id)
            if self._fault_inject is not None:
                self._fault_inject("terminal-ingested")
            gate_receipt = None
        if gate_receipt is None:
            # Idempotent: returns the existing gate receipt on replay.
            await evidence_ingress.authorize_terminal(sdk_run_id)
            release = getattr(self._run_fault_memo, "release", None)
            if callable(release):
                release(sdk_run_id)
        if row is None:
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
            or isinstance(public.get("generation"), bool)
            or not isinstance(public.get("generation"), int)
            or int(public["generation"]) < 1
            or int(public["generation"]) > generation
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
    "ForegroundEffectAdmissionGate",
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
    "resolve_host_terminal",
)
