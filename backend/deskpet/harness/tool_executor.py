"""Prepared-only tool execution contracts and batch ordering.

This module is deliberately independent from ``deskpet.workflows``.  It is
the generic seam shared by future ReAct and Workflow drivers after WI-12.
"""

from __future__ import annotations

import asyncio
import contextvars
import copy
import inspect
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping, Optional, Protocol, Sequence

from deskpet.execution.contracts import DecisionAuthorization
from deskpet.tools.capabilities import ToolExecutionContext, canonical_hash
from deskpet.tools.context_adapter import (
    RESERVED_MODEL_FIELDS,
    ReservedModelFieldError,
    reject_reserved_model_fields,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedExecutionCall:
    tool_name: str
    model_args: Mapping[str, Any]
    call_id: str
    effect_id: str
    capability_hash: str
    scope_hash: str
    requires_authorization: bool = False
    recoverable_effect: bool = False
    tool_spec_version: str = ""
    schema_hash: str = ""
    permission_policy_version: str = ""
    args_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not all((self.tool_name, self.call_id, self.capability_hash, self.scope_hash)):
            raise ValueError(
                "tool_name, call_id, capability_hash and scope_hash are required"
            )
        args = copy.deepcopy(dict(self.model_args))
        reject_reserved_model_fields(args)
        object.__setattr__(self, "model_args", MappingProxyType(args))
        object.__setattr__(self, "args_hash", canonical_hash(args))

    def args_copy(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self.model_args))


class ToolOutcomeStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ACCEPTED = "accepted"
    WAITING = "waiting"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ToolOutcome:
    call_id: str
    effect_id: str
    status: ToolOutcomeStatus
    value: Any = None
    error: Optional[str] = None
    receipt_ref: Optional[str] = None
    artifact_refs: tuple[str, ...] = ()
    retryable: bool = False
    reconciliation: Optional[str] = None

    @classmethod
    def failed(cls, call: PreparedExecutionCall, error: str) -> "ToolOutcome":
        return cls(call.call_id, call.effect_id, ToolOutcomeStatus.FAILED, error=error)

    @classmethod
    def unknown(cls, call: PreparedExecutionCall, error: str) -> "ToolOutcome":
        return cls(
            call.call_id,
            call.effect_id,
            ToolOutcomeStatus.UNKNOWN,
            error=error,
            retryable=False,
            reconciliation="required",
        )


class EffectJournal(Protocol):
    async def prepare_effect(
        self,
        call: PreparedExecutionCall,
        context: ToolExecutionContext,
        authorization: Optional[DecisionAuthorization],
    ) -> Optional[ToolOutcome]: ...

    async def mark_unknown(
        self,
        call: PreparedExecutionCall,
        context: ToolExecutionContext,
        reason: str,
    ) -> None: ...

    async def finalize_effect(
        self,
        call: PreparedExecutionCall,
        context: ToolExecutionContext,
        outcome: ToolOutcome,
        *,
        late: bool,
    ) -> None: ...


class PreparedCallRegistry(Protocol):
    def is_concurrency_safe(self, tool_name: str) -> bool: ...

    async def execute_call(
        self,
        call: PreparedExecutionCall,
        context: ToolExecutionContext,
        *,
        authorization: Optional[DecisionAuthorization] = None,
        journal: Optional[EffectJournal] = None,
        late_supervisor: Optional["LateEffectSupervisor"] = None,
    ) -> ToolOutcome: ...


class LateEffectSupervisor:
    """Own still-running sync futures after a write timeout.

    A timeout produces ``unknown`` immediately.  The same future is observed
    to completion in-process and handed to the journal once; it is never
    invoked a second time.  Restart reconciliation remains the journal's
    responsibility and must keep unreconcilable effects unknown.
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[Any]] = {}

    @property
    def pending_effect_ids(self) -> tuple[str, ...]:
        return tuple(self._pending)

    async def run_sync(
        self,
        handler: Callable[[], Any],
        *,
        timeout_seconds: float,
        call: PreparedExecutionCall,
        context: ToolExecutionContext,
        journal: EffectJournal,
        normalize: Callable[[Any], ToolOutcome],
    ) -> ToolOutcome:
        loop = asyncio.get_running_loop()
        copied = contextvars.copy_context()
        future = loop.run_in_executor(None, copied.run, handler)
        try:
            raw = await asyncio.wait_for(asyncio.shield(future), timeout_seconds)
        except asyncio.TimeoutError:
            self._pending[call.effect_id] = future
            await journal.mark_unknown(call, context, "sync_handler_timeout")

            def finish_late(completed: asyncio.Future[Any]) -> None:
                self._pending.pop(call.effect_id, None)
                try:
                    outcome = normalize(completed.result())
                except BaseException as exc:  # noqa: BLE001
                    outcome = ToolOutcome.failed(call, f"tool_handler_error:{type(exc).__name__}")

                def schedule() -> None:
                    task = asyncio.create_task(
                        journal.finalize_effect(call, context, outcome, late=True)
                    )
                    task.add_done_callback(_consume_task)

                if not loop.is_closed():
                    loop.call_soon_threadsafe(schedule)

            future.add_done_callback(finish_late)
            return ToolOutcome.unknown(call, "tool_timeout")
        except BaseException as exc:  # noqa: BLE001
            return ToolOutcome.failed(call, f"tool_handler_error:{type(exc).__name__}: {exc}")
        return normalize(raw)


def _consume_task(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    try:
        task.result()
    except BaseException:  # durable journal remains the source of truth
        logger.exception("late effect finalization failed")


class UnifiedToolExecutor:
    """Single prepared-call boundary with contiguous-safe batching."""

    def __init__(
        self,
        registry: PreparedCallRegistry,
        *,
        journal: Optional[EffectJournal] = None,
        late_supervisor: Optional[LateEffectSupervisor] = None,
    ) -> None:
        self._registry = registry
        self._journal = journal
        self._late_supervisor = late_supervisor or LateEffectSupervisor()

    async def execute_one(
        self,
        call: PreparedExecutionCall,
        context: ToolExecutionContext,
        *,
        authorization: Optional[DecisionAuthorization] = None,
    ) -> ToolOutcome:
        execute_call = getattr(self._registry, "execute_call", None)
        if callable(execute_call):
            return await execute_call(
                call,
                context,
                authorization=authorization,
                journal=self._journal,
                late_supervisor=self._late_supervisor,
            )

        # Transitional bridge only: ToolRegistry V2 owns preparation and
        # execution.  The old harness envelope remains until the Driver/UoW
        # owner-collapse slice deletes it; registry.py never imports it back.
        prepare_call = getattr(self._registry, "prepare_call", None)
        execute_prepared = getattr(self._registry, "execute_prepared", None)
        if not callable(prepare_call) or not callable(execute_prepared):
            raise TypeError("registry does not expose a prepared execution kernel")
        prepared = prepare_call(
            call.tool_name,
            call.args_copy(),
            context.session_id,
            call.call_id,
            execution_context=context,
        )
        canonical_authorization: object | None = authorization
        if isinstance(authorization, DecisionAuthorization):
            # Compatibility for the pre-cutover decision schema: the durable
            # prepared hash includes tool identity while the old grant stored
            # only model-args hash. The grant's immutable run/call/effect and
            # capability bindings are retained and revalidated by V2.
            canonical_authorization = {
                "grant_id": authorization.grant_id,
                "decision_id": authorization.decision_id,
                "run_id": authorization.run_id,
                "session_id": context.session_id,
                "call_id": authorization.call_id,
                "effect_id": authorization.effect_id,
                "tool_name": authorization.tool_name,
                "args_hash": prepared.args_hash,
                "capability_hash": authorization.capability_hash,
                "scope_hash": authorization.scope_hash,
                "permission_policy_version": prepared.permission_policy_version,
                "expires_at": authorization.expires_at,
            }
        effectful = call.recoverable_effect and self._journal is not None
        if effectful:
            replayed = await self._journal.prepare_effect(
                call, context, authorization
            )
            if replayed is not None:
                return replayed
        normalized = await execute_prepared(
            prepared,
            effect_id=context.effect_id,
            authorization=canonical_authorization,
            execution_context=context,
        )
        state = str(getattr(getattr(normalized, "state", None), "value", ""))
        payload = normalized.to_dict()
        if state == "success":
            spec = getattr(self._registry, "get", lambda _name: None)(call.tool_name)
            status = (
                ToolOutcomeStatus.ACCEPTED
                if getattr(spec, "completion_semantics", "") == "accepted_async"
                else ToolOutcomeStatus.SUCCEEDED
            )
            outcome = ToolOutcome(
                call.call_id,
                call.effect_id,
                status,
                value=payload.get("value"),
            )
        else:
            error = payload.get("error") or {}
            outcome = ToolOutcome.failed(
                call, str(error.get("code") or error.get("message") or "tool_failed")
            )
        if effectful:
            await self._journal.finalize_effect(
                call, context, outcome, late=False
            )
        return outcome

    async def execute_batch(
        self,
        calls: Sequence[PreparedExecutionCall],
        contexts: Sequence[ToolExecutionContext],
        *,
        authorizations: Optional[Sequence[Optional[DecisionAuthorization]]] = None,
    ) -> list[ToolOutcome]:
        if len(calls) != len(contexts):
            raise ValueError("calls and contexts must have the same length")
        grants = list(authorizations or [None] * len(calls))
        if len(grants) != len(calls):
            raise ValueError("authorizations and calls must have the same length")
        results: list[Optional[ToolOutcome]] = [None] * len(calls)

        async def execute_at(index: int) -> None:
            try:
                results[index] = await self.execute_one(
                    calls[index], contexts[index], authorization=grants[index]
                )
            except Exception as exc:  # sibling calls must not be cancelled
                results[index] = ToolOutcome.failed(
                    calls[index], f"executor_error:{type(exc).__name__}: {exc}"
                )

        index = 0
        while index < len(calls):
            if not self._registry.is_concurrency_safe(calls[index].tool_name):
                await execute_at(index)
                index += 1
                continue
            end = index + 1
            while end < len(calls) and self._registry.is_concurrency_safe(
                calls[end].tool_name
            ):
                end += 1
            await asyncio.gather(*(execute_at(pos) for pos in range(index, end)))
            index = end

        return [outcome for outcome in results if outcome is not None]


class LegacyPreparedCallAdapter:
    """Read-only conversion for trusted persisted legacy call records.

    Callers must pass host identifiers separately.  Reserved values found in
    the historical parameter blob are discarded and can never override those
    trusted references.
    """

    @staticmethod
    def from_persisted(
        persisted: object,
        *,
        trusted_call_id: str,
        trusted_effect_id: str,
        trusted_capability_hash: str,
        trusted_scope_hash: str,
    ) -> PreparedExecutionCall:
        if isinstance(persisted, Mapping):
            tool_name = str(persisted.get("tool_name") or "")
            raw_args = persisted.get("final_params", persisted.get("model_args", {}))
            kwargs = persisted
        else:
            tool_name = str(getattr(persisted, "tool_name", ""))
            to_dict = getattr(persisted, "to_dict", None)
            kwargs = to_dict() if callable(to_dict) else {}
            raw_args = kwargs.get("final_params", getattr(persisted, "final_params", {}))
        clean_args = {
            str(key): copy.deepcopy(value)
            for key, value in dict(raw_args or {}).items()
            if str(key) not in RESERVED_MODEL_FIELDS
        }
        return PreparedExecutionCall(
            tool_name=tool_name,
            model_args=clean_args,
            call_id=trusted_call_id,
            effect_id=trusted_effect_id,
            capability_hash=trusted_capability_hash,
            scope_hash=trusted_scope_hash,
            requires_authorization=bool(kwargs.get("requires_authorization", False)),
            recoverable_effect=bool(kwargs.get("recoverable_effect", True)),
            tool_spec_version=str(kwargs.get("tool_spec_version", "")),
            schema_hash=str(kwargs.get("schema_hash", "")),
            permission_policy_version=str(kwargs.get("permission_policy_version", "")),
        )


__all__ = [
    "DecisionAuthorization",
    "EffectJournal",
    "LateEffectSupervisor",
    "LegacyPreparedCallAdapter",
    "PreparedExecutionCall",
    "RESERVED_MODEL_FIELDS",
    "ReservedModelFieldError",
    "ToolOutcome",
    "ToolOutcomeStatus",
    "UnifiedToolExecutor",
    "reject_reserved_model_fields",
]
