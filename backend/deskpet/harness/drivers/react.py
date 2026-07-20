"""Test-only ReAct driver readiness adapter.

The production ``AgentLoop`` remains untouched.  ``LegacyAgentLoopCollaborator``
reuses its token/provider/final core for read-only turns, while prepared tool
execution uses an explicit collaborator emission so the driver can persist a
command boundary before any recoverable effect.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass, replace
from typing import Any, AsyncIterator, Mapping, Protocol

from deskpet.harness.continuations import (
    ReactCommandBoundary,
    ReactCommandBoundaryStore,
)
from deskpet.harness.ports import (
    CancelAcknowledgedCandidate,
    ChildAcceptedCandidate,
    ChildAcceptedSignal,
    ChildTerminalSignal,
    DecisionSignal,
    DelegateRun,
    DriverCandidate,
    DriverSignal,
    DriverStart,
    DriverTerminalCandidate,
    ExecuteTools,
    JoinPolicy,
    OpenDecision,
    ProviderFallbackCandidate,
    TokenCandidate,
    ToolGrantRef,
    ToolOutcomesSignal,
)
from deskpet.harness.tool_executor import (
    PreparedExecutionCall,
    ToolOutcome,
    ToolOutcomeStatus,
)
from deskpet.tools.capabilities import ToolExecutionContext


@dataclass(frozen=True)
class ReactToken:
    content: str
    kind: str = "content"


@dataclass(frozen=True)
class ReactFallback:
    from_provider: str
    to_provider: str
    reason: str


@dataclass(frozen=True)
class ReactToolBatch:
    command_id: str
    calls: tuple[PreparedExecutionCall, ...]
    contexts: tuple[ToolExecutionContext, ...]
    canonical_messages: tuple[Mapping[str, Any], ...] = ()
    iteration: int = 0

    def __post_init__(self) -> None:
        if not self.command_id:
            raise ValueError("tool batch command_id is required")
        if not self.calls or len(self.calls) != len(self.contexts):
            raise ValueError("tool batch calls and contexts must be non-empty and align")


@dataclass(frozen=True)
class ReactDecisionRequest:
    decision: OpenDecision


@dataclass(frozen=True)
class ReactDelegateRequest:
    command: DelegateRun


@dataclass(frozen=True)
class ReactFinal:
    content: str


@dataclass(frozen=True)
class ReactFailure:
    error: str


ReactEmission = (
    ReactToken
    | ReactFallback
    | ReactToolBatch
    | ReactDecisionRequest
    | ReactDelegateRequest
    | ReactFinal
    | ReactFailure
)


class ReActCollaborator(Protocol):
    def start(self, request: DriverStart) -> AsyncIterator[ReactEmission]: ...

    def resume(
        self,
        boundary: ReactCommandBoundary,
        response: Mapping[str, Any],
    ) -> AsyncIterator[ReactEmission]: ...

    async def cancel(self, run_id: str, reason: str) -> None: ...
    async def close(self) -> None: ...


class EffectOutcomeReader(Protocol):
    async def get_outcome(self, effect_id: str) -> ToolOutcome | None: ...


class EffectReconciler(Protocol):
    async def reconcile(
        self,
        call: PreparedExecutionCall,
        context: ToolExecutionContext,
        outcome: ToolOutcome,
    ) -> ToolOutcome: ...


class EffectContinuationCommitter(Protocol):
    """Future WI-1 storage seam for one effect+continuation transaction.

    The current execution unit-of-work cannot yet link an effect-journal
    outcome and continuation progress atomically.  Test-only wiring may omit
    this port and persist the already-journaled outcome in the boundary only;
    production activation must provide it.
    """

    async def commit_outcomes(
        self,
        boundary: ReactCommandBoundary,
        updates: Mapping[int, ToolOutcome],
    ) -> ReactCommandBoundary: ...


class LegacyAgentLoopToolInterceptionError(RuntimeError):
    pass


class LegacyAgentLoopCollaborator:
    """Narrow reuse seam for the existing LLM/token/fallback core.

    It fails closed when the legacy loop reaches its internal tool dispatcher;
    tool-capable test wiring must supply a collaborator that emits a prepared
    ``ReactToolBatch`` before execution.
    """

    def __init__(
        self,
        loop: Any,
        *,
        call_factory: Any | None = None,
    ) -> None:
        self._loop = loop
        self._call_factory = call_factory
        self._active: dict[str, AsyncIterator[Any]] = {}

    async def _map(
        self,
        request: DriverStart,
        iterator: AsyncIterator[Any],
    ) -> AsyncIterator[ReactEmission]:
        from agent.agent_loop import (
            AssistantDeltaEvent,
            AsyncHandoffEvent,
            ErrorEvent,
            FinalEvent,
            ProviderChainFallbackEvent,
            ToolCallEvent,
            ToolBatchEvent,
            ToolResultEvent,
        )

        run_id = request.run_id
        self._active[run_id] = iterator
        try:
            async for event in iterator:
                if isinstance(event, AssistantDeltaEvent):
                    yield ReactToken(event.content, event.kind)
                elif isinstance(event, ProviderChainFallbackEvent):
                    yield ReactFallback(event.from_, event.to, event.reason)
                elif isinstance(event, FinalEvent):
                    yield ReactFinal(event.content)
                elif isinstance(event, ErrorEvent):
                    yield ReactFailure(event.detail or event.reason)
                elif isinstance(event, ToolBatchEvent):
                    if self._call_factory is None or request.run_context is None:
                        raise LegacyAgentLoopToolInterceptionError(
                            "external tool batch requires a prepared-call factory and run context"
                        )
                    from deskpet.harness.context import HostContextFactory

                    context_factory = HostContextFactory()
                    calls = []
                    contexts = []
                    for tool_call in event.tool_calls:
                        effect_id = hashlib.sha256(
                            f"effect|{run_id}|{tool_call.id}".encode("utf-8")
                        ).hexdigest()
                        context = context_factory.create_tool_context(
                            request.run_context,
                            run_id=run_id,
                            call_id=tool_call.id,
                            effect_id=effect_id,
                        )
                        calls.append(
                            self._call_factory.prepare_execution_call(
                                tool_call.name,
                                tool_call.arguments,
                                call_id=tool_call.id,
                                effect_id=effect_id,
                                context=context,
                            )
                        )
                        contexts.append(context)
                    command_id = hashlib.sha256(
                        (
                            f"tool-batch|{run_id}|{event.iteration}|"
                            + "|".join(call.call_id for call in calls)
                        ).encode("utf-8")
                    ).hexdigest()
                    yield ReactToolBatch(
                        command_id,
                        tuple(calls),
                        tuple(contexts),
                        tuple(event.canonical_messages),
                        int(event.iteration),
                    )
                    return
                elif isinstance(event, (ToolCallEvent, ToolResultEvent, AsyncHandoffEvent)):
                    raise LegacyAgentLoopToolInterceptionError(
                        "legacy AgentLoop tool dispatch is unavailable in ReActDriver test wiring"
                    )
        finally:
            self._active.pop(run_id, None)

    async def start(self, request: DriverStart) -> AsyncIterator[ReactEmission]:
        iterator = self._loop.run(
            [copy.deepcopy(dict(item)) for item in request.canonical_messages],
            task_id=request.run_id,
            session_id=request.session_id,
            stream=True,
        )
        async for emission in self._map(request, iterator):
            yield emission

    async def resume(
        self,
        boundary: ReactCommandBoundary,
        response: Mapping[str, Any],
    ) -> AsyncIterator[ReactEmission]:
        request = DriverStart(
            run_id=boundary.run_id,
            session_id=boundary.session_id,
            canonical_messages=boundary.canonical_messages,
            session_projection_cursor=boundary.session_projection_cursor,
            prepared_context_ref=boundary.prepared_context_ref,
            tool_set_snapshot_ref=boundary.tool_set_snapshot_ref,
            provider_state=boundary.provider_state,
            iteration=boundary.iteration,
            completion_state=boundary.completion_state,
        )
        async for emission in self.start(request):
            yield emission

    async def cancel(self, run_id: str, reason: str) -> None:
        iterator = self._active.get(run_id)
        close = getattr(iterator, "aclose", None)
        if callable(close):
            await close()

    async def close(self) -> None:
        for run_id in tuple(self._active):
            await self.cancel(run_id, "driver_close")


class ReActDriver:
    def __init__(
        self,
        collaborator: ReActCollaborator,
        boundary_store: ReactCommandBoundaryStore,
        effect_reader: EffectOutcomeReader,
        *,
        reconciler: EffectReconciler | None = None,
        outcome_committer: EffectContinuationCommitter | None = None,
    ) -> None:
        self._collaborator = collaborator
        self._boundaries = boundary_store
        self._effects = effect_reader
        self._reconciler = reconciler
        self._outcome_committer = outcome_committer
        self._requests: dict[str, DriverStart] = {}
        self._volatile: dict[str, ReactCommandBoundary] = {}

    @staticmethod
    def _requires_boundary(batch: ReactToolBatch) -> bool:
        return any(
            call.recoverable_effect or call.requires_authorization
            for call in batch.calls
        )

    @staticmethod
    def _permission_decision(
        boundary: ReactCommandBoundary,
        index: int,
    ) -> OpenDecision:
        call = boundary.pending_calls[index]
        identity = hashlib.sha256(
            f"permission|{boundary.run_id}|{call.effect_id}".encode("utf-8")
        ).hexdigest()
        nonce = hashlib.sha256(f"nonce|{identity}".encode("utf-8")).hexdigest()
        return OpenDecision(
            run_id=boundary.run_id,
            command_id=boundary.command_id,
            decision_id=identity,
            nonce=nonce,
            kind="permission",
            prompt={
                "tool_name": call.tool_name,
                "call_id": call.call_id,
                "reason": "tool_requires_authorization",
            },
            expires_at=time.time() + 300.0,
            call_id=call.call_id,
            effect_id=call.effect_id,
            tool_name=call.tool_name,
            args_hash=call.args_hash,
            capability_hash=call.capability_hash,
            scope_hash=call.scope_hash,
        )

    @staticmethod
    def _grant_state(boundary: ReactCommandBoundary) -> dict[str, Mapping[str, Any]]:
        raw = boundary.completion_state.get("authorization_refs", {})
        return {
            str(call_id): dict(value)
            for call_id, value in dict(raw).items()
            if isinstance(value, Mapping)
        }

    @classmethod
    def _next_permission_index(
        cls, boundary: ReactCommandBoundary
    ) -> int | None:
        grants = cls._grant_state(boundary)
        for index in boundary.pending_indexes:
            call = boundary.pending_calls[index]
            if call.requires_authorization and call.call_id not in grants:
                return index
        return None

    @staticmethod
    def _boundary_for_batch(request: DriverStart, batch: ReactToolBatch) -> ReactCommandBoundary:
        return ReactCommandBoundary(
            run_id=request.run_id,
            session_id=request.session_id,
            command_id=batch.command_id,
            command_kind="execute_tools",
            canonical_messages=batch.canonical_messages or request.canonical_messages,
            session_projection_cursor=request.session_projection_cursor,
            prepared_context_ref=request.prepared_context_ref,
            tool_set_snapshot_ref=request.tool_set_snapshot_ref,
            pending_calls=batch.calls,
            tool_contexts=batch.contexts,
            outcomes=(None,) * len(batch.calls),
            provider_state=request.provider_state,
            iteration=max(request.iteration, batch.iteration),
            completion_state=request.completion_state,
        )

    @staticmethod
    def _tool_messages(boundary: ReactCommandBoundary) -> tuple[Mapping[str, Any], ...]:
        messages = [copy.deepcopy(dict(item)) for item in boundary.canonical_messages]
        for call, outcome in zip(boundary.pending_calls, boundary.outcomes):
            assert outcome is not None
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.call_id,
                    "name": call.tool_name,
                    "content": json.dumps(
                        {
                            "status": outcome.status.value,
                            "value": outcome.value,
                            "error": outcome.error,
                            "effect_id": outcome.effect_id,
                            "receipt_ref": outcome.receipt_ref,
                            "artifact_refs": list(outcome.artifact_refs),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                }
            )
        return tuple(messages)

    async def _emit(
        self,
        request: DriverStart,
        emissions: AsyncIterator[ReactEmission],
    ) -> AsyncIterator[DriverCandidate]:
        async for emission in emissions:
            if isinstance(emission, ReactToken):
                yield TokenCandidate(request.run_id, emission.content, emission.kind)
            elif isinstance(emission, ReactFallback):
                yield ProviderFallbackCandidate(
                    request.run_id,
                    emission.from_provider,
                    emission.to_provider,
                    emission.reason,
                )
            elif isinstance(emission, ReactToolBatch):
                boundary = self._boundary_for_batch(request, emission)
                if self._requires_boundary(emission):
                    permission_index = self._next_permission_index(boundary)
                    if permission_index is not None:
                        decision = self._permission_decision(boundary, permission_index)
                        boundary = replace(boundary, pending_decision=decision)
                        await self._boundaries.put(boundary, open_decision=decision)
                        yield decision
                        return
                    await self._boundaries.put(boundary)
                else:
                    self._volatile[request.run_id] = boundary
                yield self._execute_command(boundary)
                return
            elif isinstance(emission, ReactDecisionRequest):
                decision = emission.decision
                if decision.run_id != request.run_id:
                    raise ValueError("decision run binding mismatch")
                boundary = ReactCommandBoundary(
                    run_id=request.run_id,
                    session_id=request.session_id,
                    command_id=decision.command_id,
                    command_kind="open_decision",
                    canonical_messages=request.canonical_messages,
                    session_projection_cursor=request.session_projection_cursor,
                    prepared_context_ref=request.prepared_context_ref,
                    tool_set_snapshot_ref=request.tool_set_snapshot_ref,
                    pending_calls=(),
                    tool_contexts=(),
                    outcomes=(),
                    provider_state=request.provider_state,
                    iteration=request.iteration,
                    completion_state=request.completion_state,
                    pending_decision=decision,
                )
                await self._boundaries.put(boundary, open_decision=decision)
                yield decision
                return
            elif isinstance(emission, ReactDelegateRequest):
                command = emission.command
                if command.run_id != request.run_id:
                    raise ValueError("delegate run binding mismatch")
                await self._boundaries.put(
                    ReactCommandBoundary(
                        run_id=request.run_id,
                        session_id=request.session_id,
                        command_id=command.command_id,
                        command_kind="delegate",
                        canonical_messages=request.canonical_messages,
                        session_projection_cursor=request.session_projection_cursor,
                        prepared_context_ref=request.prepared_context_ref,
                        tool_set_snapshot_ref=request.tool_set_snapshot_ref,
                        pending_calls=(),
                        tool_contexts=(),
                        outcomes=(),
                        provider_state=request.provider_state,
                        iteration=request.iteration,
                        completion_state=request.completion_state,
                        pending_delegate=command,
                    )
                )
                yield command
                return
            elif isinstance(emission, ReactFinal):
                yield DriverTerminalCandidate(request.run_id, "completed", emission.content)
                return
            elif isinstance(emission, ReactFailure):
                yield DriverTerminalCandidate(
                    request.run_id, "failed", error=emission.error
                )
                return

    @staticmethod
    def _execute_command(boundary: ReactCommandBoundary) -> ExecuteTools:
        indexes = boundary.pending_indexes
        grants = ReActDriver._grant_state(boundary)
        grant_refs = []
        for index in indexes:
            call = boundary.pending_calls[index]
            value = grants.get(call.call_id)
            grant_refs.append(
                None
                if value is None
                else ToolGrantRef(
                    grant_id=str(value["grant_id"]),
                    decision_id=str(value["decision_id"]),
                    decision_nonce=str(value["decision_nonce"]),
                    version=int(value.get("version", 0)),
                )
            )
        return ExecuteTools(
            run_id=boundary.run_id,
            command_id=boundary.command_id,
            calls=tuple(boundary.pending_calls[index] for index in indexes),
            contexts=tuple(boundary.tool_contexts[index] for index in indexes),
            original_indexes=indexes,
            grant_refs=(
                tuple(grant_refs) if any(item is not None for item in grant_refs) else ()
            ),
        )

    def start(self, request: DriverStart) -> AsyncIterator[DriverCandidate]:
        async def iterator() -> AsyncIterator[DriverCandidate]:
            self._requests[request.run_id] = request
            async for candidate in self._emit(request, self._collaborator.start(request)):
                yield candidate

        return iterator()

    async def _load_boundary(self, run_id: str) -> ReactCommandBoundary:
        boundary = await self._boundaries.load(run_id)
        if boundary is None:
            boundary = self._volatile.get(run_id)
        if boundary is None:
            raise ValueError("react command boundary not found")
        return boundary

    async def _save_progress(self, boundary: ReactCommandBoundary) -> None:
        if boundary.run_id in self._volatile:
            self._volatile[boundary.run_id] = boundary
        else:
            await self._boundaries.put(boundary)

    async def _record_outcomes(
        self,
        boundary: ReactCommandBoundary,
        updates: Mapping[int, ToolOutcome],
    ) -> ReactCommandBoundary:
        if self._outcome_committer is not None:
            return await self._outcome_committer.commit_outcomes(boundary, updates)
        boundary = boundary.with_outcomes(updates)
        await self._save_progress(boundary)
        return boundary

    @staticmethod
    def _request_from_boundary(boundary: ReactCommandBoundary) -> DriverStart:
        return DriverStart(
            run_id=boundary.run_id,
            session_id=boundary.session_id,
            canonical_messages=boundary.canonical_messages,
            session_projection_cursor=boundary.session_projection_cursor,
            prepared_context_ref=boundary.prepared_context_ref,
            tool_set_snapshot_ref=boundary.tool_set_snapshot_ref,
            provider_state=boundary.provider_state,
            iteration=boundary.iteration,
            completion_state=boundary.completion_state,
        )

    async def _resume_completed(
        self, boundary: ReactCommandBoundary
    ) -> AsyncIterator[DriverCandidate]:
        if not bool(boundary.completion_state.get("model_backfilled")):
            boundary = boundary.with_backfilled_messages(self._tool_messages(boundary))
            await self._save_progress(boundary)
        # Always refresh from the persisted boundary.  Reusing the original
        # request here would let a second command omit the just-backfilled tool
        # or decision messages after recovery.
        request = self._request_from_boundary(boundary)
        self._requests[boundary.run_id] = request
        async for candidate in self._emit(
            request,
            self._collaborator.resume(
                boundary,
                {"type": "tool_outcomes", "command_id": boundary.command_id},
            ),
        ):
            yield candidate

    def signal(self, signal: DriverSignal) -> AsyncIterator[DriverCandidate]:
        async def iterator() -> AsyncIterator[DriverCandidate]:
            if isinstance(signal, ToolOutcomesSignal):
                boundary = await self._load_boundary(signal.run_id)
                if boundary.command_id != signal.command_id:
                    raise ValueError("tool outcome command binding mismatch")
                indexes = {call.call_id: index for index, call in enumerate(boundary.pending_calls)}
                updates: dict[int, ToolOutcome] = {}
                for outcome in signal.outcomes:
                    if outcome.call_id not in indexes:
                        raise ValueError("tool outcome call not present in boundary")
                    updates[indexes[outcome.call_id]] = outcome
                boundary = await self._record_outcomes(boundary, updates)
                if boundary.pending_indexes:
                    yield self._execute_command(boundary)
                    return
                async for candidate in self._resume_completed(boundary):
                    yield candidate
                return
            if isinstance(signal, DecisionSignal):
                boundary = await self._load_boundary(signal.run_id)
                await self._boundaries.confirm_resolved_decision(
                    boundary,
                    decision_id=signal.decision_id,
                )
                pending = boundary.pending_decision
                assert pending is not None
                if boundary.pending_calls and pending.call_id is not None:
                    indexes = {
                        call.call_id: index
                        for index, call in enumerate(boundary.pending_calls)
                    }
                    index = indexes[pending.call_id]
                    if bool(signal.response.get("allow", signal.response.get("approved", True))):
                        grant_id = str(signal.response.get("grant_id") or "").strip()
                        if not grant_id:
                            raise ValueError("allowed permission response is missing grant_id")
                        state = copy.deepcopy(dict(boundary.completion_state))
                        refs = dict(state.get("authorization_refs") or {})
                        refs[pending.call_id] = {
                            "grant_id": grant_id,
                            "decision_id": pending.decision_id,
                            "decision_nonce": pending.nonce,
                            "version": int(signal.response.get("grant_version") or 0),
                        }
                        state["authorization_refs"] = refs
                        boundary = replace(
                            boundary,
                            completion_state=state,
                            pending_decision=None,
                            version=boundary.version + 1,
                        )
                    else:
                        boundary = boundary.with_outcomes(
                            {
                                index: ToolOutcome.failed(
                                    boundary.pending_calls[index],
                                    "authorization_denied",
                                )
                            }
                        )
                        boundary = replace(boundary, pending_decision=None)
                    next_index = self._next_permission_index(boundary)
                    if next_index is not None:
                        decision = self._permission_decision(boundary, next_index)
                        boundary = replace(boundary, pending_decision=decision)
                        await self._boundaries.put(boundary, open_decision=decision)
                        yield decision
                        return
                    await self._boundaries.put(boundary)
                    if boundary.pending_indexes:
                        yield self._execute_command(boundary)
                    else:
                        async for candidate in self._resume_completed(boundary):
                            yield candidate
                    return
                messages = tuple(boundary.canonical_messages) + (
                    {
                        "role": "system",
                        "content": json.dumps(
                            {
                                "decision_id": signal.decision_id,
                                "response": dict(signal.response),
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    },
                )
                state = copy.deepcopy(dict(boundary.completion_state))
                state["model_backfilled"] = True
                boundary = replace(
                    boundary,
                    canonical_messages=messages,
                    completion_state=state,
                    pending_decision=None,
                    version=boundary.version + 1,
                )
                await self._boundaries.put(boundary)
                request = self._request_from_boundary(boundary)
                self._requests[signal.run_id] = request
                async for candidate in self._emit(
                    request,
                    self._collaborator.resume(
                        boundary,
                        {"type": "decision", "response": dict(signal.response)},
                    ),
                ):
                    yield candidate
                return
            if isinstance(signal, ChildAcceptedSignal):
                boundary = await self._load_boundary(signal.run_id)
                command = boundary.pending_delegate
                if command is None or command.command_id != signal.command_id:
                    raise ValueError("delegate command not found")
                yield ChildAcceptedCandidate(
                    signal.run_id,
                    signal.command_id,
                    signal.child_run_id,
                    command.join_policy,
                )
                if command.join_policy is JoinPolicy.DETACHED:
                    request = self._request_from_boundary(boundary)
                    async for candidate in self._emit(
                        request,
                        self._collaborator.resume(
                            boundary,
                            {"type": "child_accepted", "child_run_id": signal.child_run_id},
                        ),
                    ):
                        yield candidate
                return
            if isinstance(signal, ChildTerminalSignal):
                boundary = await self._load_boundary(signal.run_id)
                command = boundary.pending_delegate
                if command is None or command.command_id != signal.command_id:
                    raise ValueError("delegate command not found")
                if command.join_policy is JoinPolicy.ROOT_TERMINAL_CHILD:
                    yield DriverTerminalCandidate(
                        signal.run_id,
                        "completed" if signal.status == "completed" else "failed",
                        content=str(signal.value or ""),
                        error=(
                            None
                            if signal.status == "completed"
                            else str(signal.value or signal.status)
                        ),
                        correlation={"child_run_id": signal.child_run_id},
                    )
                    return
                if command.join_policy is JoinPolicy.JOIN_BEFORE_FINAL:
                    request = self._request_from_boundary(boundary)
                    async for candidate in self._emit(
                        request,
                        self._collaborator.resume(
                            boundary,
                            {
                                "type": "child_terminal",
                                "child_run_id": signal.child_run_id,
                                "status": signal.status,
                                "value": signal.value,
                            },
                        ),
                    ):
                        yield candidate

        return iterator()

    def recover(self, run_id: str) -> AsyncIterator[DriverCandidate]:
        async def iterator() -> AsyncIterator[DriverCandidate]:
            boundary = await self._load_boundary(run_id)
            if boundary.pending_decision is not None:
                yield boundary.pending_decision
                return
            if boundary.pending_delegate is not None:
                yield boundary.pending_delegate
                return
            updates: dict[int, ToolOutcome] = {}
            for index in boundary.pending_indexes:
                call = boundary.pending_calls[index]
                outcome = await self._effects.get_outcome(call.effect_id)
                if outcome is None:
                    continue
                if outcome.status is ToolOutcomeStatus.UNKNOWN and self._reconciler is not None:
                    outcome = await self._reconciler.reconcile(
                        call, boundary.tool_contexts[index], outcome
                    )
                updates[index] = outcome
            if updates:
                boundary = await self._record_outcomes(boundary, updates)
            if boundary.pending_indexes:
                yield self._execute_command(boundary)
                return
            async for candidate in self._resume_completed(boundary):
                yield candidate

        return iterator()

    def cancel(self, run_id: str, reason: str) -> AsyncIterator[DriverCandidate]:
        async def iterator() -> AsyncIterator[DriverCandidate]:
            await self._collaborator.cancel(run_id, reason)
            yield CancelAcknowledgedCandidate(run_id, reason)

        return iterator()

    async def close(self) -> None:
        await self._collaborator.close()


__all__ = [
    "EffectOutcomeReader",
    "EffectReconciler",
    "EffectContinuationCommitter",
    "LegacyAgentLoopCollaborator",
    "LegacyAgentLoopToolInterceptionError",
    "ReActCollaborator",
    "ReActDriver",
    "ReactDecisionRequest",
    "ReactDelegateRequest",
    "ReactEmission",
    "ReactFailure",
    "ReactFallback",
    "ReactFinal",
    "ReactToken",
    "ReactToolBatch",
]
