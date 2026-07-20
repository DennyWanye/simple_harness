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
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, AsyncIterator, Mapping, Protocol
from deskpet.execution.contracts import DecisionOpen as DurableDecisionOpen, OutcomeStatus, PersistenceLevel, RecoveryLease, RunEventCandidate
from deskpet.execution.ports import ExecutionUnitOfWork
from deskpet.harness.ports import AttachmentPolicy, CancelAcknowledgedCandidate, ChildAcceptedCandidate, ChildAcceptedSignal, ChildTerminalSignal, DecisionSignal, DelegateRun, DriverEvent, DriverSignal, DriverStart, DriverTerminalCandidate, ExecuteTools, JoinPolicy, OpenDecision, PersistedEventCandidate, ProviderFallbackCandidate, TokenCandidate, ToolGrantRef, ToolOutcomesSignal
from deskpet.harness.tool_executor import PreparedExecutionCall, ToolOutcome, ToolOutcomeStatus
from deskpet.tools.capabilities import ToolExecutionContext

def _call_payload(call: PreparedExecutionCall) -> dict[str, Any]:
    return {'tool_name': call.tool_name, 'model_args': call.args_copy(), 'call_id': call.call_id, 'effect_id': call.effect_id, 'capability_hash': call.capability_hash, 'scope_hash': call.scope_hash, 'requires_authorization': call.requires_authorization, 'recoverable_effect': call.recoverable_effect, 'tool_spec_version': call.tool_spec_version, 'schema_hash': call.schema_hash, 'permission_policy_version': call.permission_policy_version, 'args_hash': call.args_hash}

def _load_call(value: Mapping[str, Any]) -> PreparedExecutionCall:
    call = PreparedExecutionCall(tool_name=str(value['tool_name']), model_args=dict(value['model_args']), call_id=str(value['call_id']), effect_id=str(value['effect_id']), capability_hash=str(value['capability_hash']), scope_hash=str(value['scope_hash']), requires_authorization=bool(value.get('requires_authorization', False)), recoverable_effect=bool(value.get('recoverable_effect', False)), tool_spec_version=str(value.get('tool_spec_version', '')), schema_hash=str(value.get('schema_hash', '')), permission_policy_version=str(value.get('permission_policy_version', '')))
    if value.get('args_hash') != call.args_hash:
        raise ValueError('persisted prepared call args hash mismatch')
    return call

def _context_payload(context: ToolExecutionContext) -> dict[str, Any]:
    return {'scope_id': context.scope_id, 'session_id': context.session_id, 'request_id': context.request_id, 'origin': context.origin, 'root_run_id': context.root_run_id, 'parent_run_id': context.parent_run_id, 'turn_id': context.turn_id, 'venue': context.venue, 'workspace': context.workspace, 'write_scope_root': context.write_scope_root, 'capability_hash': context.capability_hash, 'scope_hash': context.scope_hash, 'provider_plan': list(context.provider_plan), 'run_id': context.run_id, 'call_id': context.call_id, 'effect_id': context.effect_id, 'trace_id': context.trace_id}

def _load_context(value: Mapping[str, Any]) -> ToolExecutionContext:
    data = dict(value)
    data['provider_plan'] = tuple((str(item) for item in data.get('provider_plan', ())))
    return ToolExecutionContext(**data)

def _outcome_payload(outcome: ToolOutcome) -> dict[str, Any]:
    return {'call_id': outcome.call_id, 'effect_id': outcome.effect_id, 'status': outcome.status.value, 'value': copy.deepcopy(outcome.value), 'error': outcome.error, 'receipt_ref': outcome.receipt_ref, 'artifact_refs': list(outcome.artifact_refs), 'retryable': outcome.retryable, 'reconciliation': outcome.reconciliation}

def _load_outcome(value: Mapping[str, Any]) -> ToolOutcome:
    return ToolOutcome(call_id=str(value['call_id']), effect_id=str(value['effect_id']), status=ToolOutcomeStatus(str(value['status'])), value=copy.deepcopy(value.get('value')), error=value.get('error'), receipt_ref=value.get('receipt_ref'), artifact_refs=tuple((str(item) for item in value.get('artifact_refs', ()))), retryable=bool(value.get('retryable', False)), reconciliation=value.get('reconciliation'))

@dataclass(frozen=True)
class ReactCommandBoundary:
    run_id: str
    session_id: str
    command_id: str
    command_kind: str
    canonical_messages: tuple[Mapping[str, Any], ...]
    session_projection_cursor: int
    prepared_context_ref: str | None
    tool_set_snapshot_ref: str | None
    pending_calls: tuple[PreparedExecutionCall, ...]
    tool_contexts: tuple[ToolExecutionContext, ...]
    outcomes: tuple[ToolOutcome | None, ...]
    provider_state: Mapping[str, Any]
    iteration: int
    completion_state: Mapping[str, Any]
    capability_snapshot: Mapping[str, Any] = field(default_factory=dict)
    pending_decision: DriverEvent | None = None
    pending_delegate: DriverEvent | None = None
    version: int = 0

    def __post_init__(self) -> None:
        if len(self.pending_calls) != len(self.tool_contexts) or len(self.pending_calls) != len(self.outcomes):
            raise ValueError('pending calls, contexts and outcomes must align')
        object.__setattr__(self, 'canonical_messages', tuple((MappingProxyType(copy.deepcopy(dict(item))) for item in self.canonical_messages)))
        for name in ('provider_state', 'completion_state', 'capability_snapshot'):
            object.__setattr__(self, name, MappingProxyType(copy.deepcopy(dict(getattr(self, name)))))

    @property
    def pending_indexes(self) -> tuple[int, ...]:
        return tuple((index for index, outcome in enumerate(self.outcomes) if outcome is None))

    def with_outcomes(self, updates: Mapping[int, ToolOutcome]) -> 'ReactCommandBoundary':
        outcomes = list(self.outcomes)
        for index, outcome in updates.items():
            call = self.pending_calls[index]
            if (outcome.call_id, outcome.effect_id) != (call.call_id, call.effect_id):
                raise ValueError('outcome binding mismatch')
            if outcomes[index] is not None and outcomes[index] != outcome:
                raise ValueError('outcome already recorded with different value')
            outcomes[index] = outcome
        return replace(self, outcomes=tuple(outcomes), version=self.version + 1)

    def with_backfilled_messages(self, messages: tuple[Mapping[str, Any], ...]) -> 'ReactCommandBoundary':
        state = copy.deepcopy(dict(self.completion_state))
        state['model_backfilled'] = True
        return replace(self, canonical_messages=tuple((copy.deepcopy(dict(item)) for item in messages)), completion_state=state, version=self.version + 1)

    def to_payload(self) -> dict[str, Any]:
        delegate = self.pending_delegate
        decision = self.pending_decision
        return {'session_id': self.session_id, 'command_id': self.command_id, 'command_kind': self.command_kind, 'canonical_messages': [dict(item) for item in self.canonical_messages], 'session_projection_cursor': self.session_projection_cursor, 'prepared_context_ref': self.prepared_context_ref, 'tool_set_snapshot_ref': self.tool_set_snapshot_ref, 'calls': [_call_payload(call) for call in self.pending_calls], 'contexts': [_context_payload(context) for context in self.tool_contexts], 'outcomes': [_outcome_payload(outcome) if outcome is not None else None for outcome in self.outcomes], 'provider_state': dict(self.provider_state), 'iteration': self.iteration, 'completion_state': dict(self.completion_state), 'capability_snapshot': dict(self.capability_snapshot), 'decision': None if decision is None else {'run_id': decision.run_id, 'command_id': decision.command_id, 'decision_id': decision.decision_id, 'nonce': decision.nonce, 'kind': decision.decision_kind, 'prompt': dict(decision.prompt), 'prompt_schema_version': decision.prompt_schema_version, 'expires_at': decision.expires_at, 'call_id': decision.call_id, 'effect_id': decision.effect_id, 'tool_name': decision.tool_name, 'args_hash': decision.args_hash, 'capability_hash': decision.capability_hash, 'scope_hash': decision.scope_hash}, 'delegate': None if delegate is None else {'run_id': delegate.run_id, 'command_id': delegate.command_id, 'child_request': dict(delegate.child_request), 'route_hint': delegate.route_hint, 'capability_subset': list(delegate.capability_subset), 'attachment_policy': delegate.attachment_policy.value, 'join_policy': delegate.join_policy.value}}

    @classmethod
    def from_record(cls, record: Any) -> 'ReactCommandBoundary':
        value = dict(record.payload)
        decision = value.get('decision')
        delegate = value.get('delegate')
        return cls(run_id=record.run_id, session_id=str(value['session_id']), command_id=str(value['command_id']), command_kind=str(value['command_kind']), canonical_messages=tuple(value['canonical_messages']), session_projection_cursor=int(value['session_projection_cursor']), prepared_context_ref=value.get('prepared_context_ref'), tool_set_snapshot_ref=value.get('tool_set_snapshot_ref'), pending_calls=tuple((_load_call(item) for item in value['calls'])), tool_contexts=tuple((_load_context(item) for item in value['contexts'])), outcomes=tuple((_load_outcome(item) if item is not None else None for item in value['outcomes'])), provider_state=dict(value['provider_state']), iteration=int(value['iteration']), completion_state=dict(value['completion_state']), capability_snapshot=dict(value.get('capability_snapshot') or {}), pending_decision=OpenDecision(**decision) if decision is not None else None, pending_delegate=DelegateRun(run_id=str(delegate['run_id']), command_id=str(delegate['command_id']), child_request=dict(delegate['child_request']), route_hint=str(delegate['route_hint']), capability_subset=tuple(delegate['capability_subset']), attachment_policy=AttachmentPolicy(str(delegate['attachment_policy'])), join_policy=JoinPolicy(str(delegate['join_policy']))) if delegate is not None else None, version=int(record.version))

@dataclass(frozen=True)
class ReactToken:
    content: str
    kind: str = 'content'

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
            raise ValueError('tool batch command_id is required')
        if not self.calls or len(self.calls) != len(self.contexts):
            raise ValueError('tool batch calls and contexts must be non-empty and align')

@dataclass(frozen=True)
class ReactDecisionRequest:
    decision: DriverEvent

@dataclass(frozen=True)
class ReactDelegateRequest:
    command: DriverEvent

@dataclass(frozen=True)
class ReactFinal:
    content: str

@dataclass(frozen=True)
class ReactFailure:
    error: str
ReactEmission = ReactToken | ReactFallback | ReactToolBatch | ReactDecisionRequest | ReactDelegateRequest | ReactFinal | ReactFailure

class ReActCollaborator(Protocol):

    def start(self, request: DriverStart) -> AsyncIterator[ReactEmission]:
        ...

    def resume(self, boundary: ReactCommandBoundary, response: Mapping[str, Any]) -> AsyncIterator[ReactEmission]:
        ...

    async def cancel(self, run_id: str, reason: str) -> None:
        ...

    async def close(self) -> None:
        ...

class EffectOutcomeReader(Protocol):

    async def get_outcome(self, effect_id: str) -> ToolOutcome | None:
        ...

class EffectReconciler(Protocol):

    async def reconcile(self, call: PreparedExecutionCall, context: ToolExecutionContext, outcome: ToolOutcome) -> ToolOutcome:
        ...

class LegacyAgentLoopToolInterceptionError(RuntimeError):
    pass

class LegacyAgentLoopCollaborator:
    """Narrow reuse seam for the existing LLM/token/fallback core.

    It fails closed when the legacy loop reaches its internal tool dispatcher;
    tool-capable test wiring must supply a collaborator that emits a prepared
    ``ReactToolBatch`` before execution.
    """

    def __init__(self, loop: Any, *, call_factory: Any | None=None) -> None:
        self._loop = loop
        self._call_factory = call_factory
        self._active: dict[str, AsyncIterator[Any]] = {}

    @staticmethod
    def _allowed_tool_names(request: DriverStart) -> tuple[str, ...] | None:
        snapshot = request.capability_snapshot
        if "tools" in snapshot:
            raw = snapshot["tools"]
        elif "capabilities" in snapshot:
            raw = snapshot["capabilities"]
        else:
            return None
        if not isinstance(raw, (list, tuple, set, frozenset)):
            raise ValueError("capability snapshot tools must be a sequence")
        return tuple(sorted({str(item) for item in raw if str(item).strip()}))

    async def _map(self, request: DriverStart, iterator: AsyncIterator[Any]) -> AsyncIterator[ReactEmission]:
        from agent.agent_loop import AssistantDeltaEvent, AsyncHandoffEvent, ErrorEvent, FinalEvent, ProviderChainFallbackEvent, ToolCallEvent, ToolBatchEvent, ToolResultEvent
        run_id = request.run_id
        allowed_tools = self._allowed_tool_names(request)
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
                        raise LegacyAgentLoopToolInterceptionError('external tool batch requires a prepared-call factory and run context')
                    from deskpet.harness.context import HostContextFactory
                    context_factory = HostContextFactory()
                    calls = []
                    contexts = []
                    for tool_call in event.tool_calls:
                        if allowed_tools is not None and tool_call.name not in allowed_tools:
                            raise LegacyAgentLoopToolInterceptionError(
                                f"tool is outside capability snapshot: {tool_call.name}"
                            )
                        effect_id = hashlib.sha256(f'effect|{run_id}|{tool_call.id}'.encode('utf-8')).hexdigest()
                        context = context_factory.create_tool_context(request.run_context, run_id=run_id, call_id=tool_call.id, effect_id=effect_id)
                        calls.append(self._call_factory.prepare_execution_call(tool_call.name, tool_call.arguments, call_id=tool_call.id, effect_id=effect_id, context=context))
                        contexts.append(context)
                    command_id = hashlib.sha256((f'tool-batch|{run_id}|{event.iteration}|' + '|'.join((call.call_id for call in calls))).encode('utf-8')).hexdigest()
                    yield ReactToolBatch(command_id, tuple(calls), tuple(contexts), tuple(event.canonical_messages), int(event.iteration))
                    return
                elif isinstance(event, (ToolCallEvent, ToolResultEvent, AsyncHandoffEvent)):
                    raise LegacyAgentLoopToolInterceptionError('legacy AgentLoop tool dispatch is unavailable in ReActDriver test wiring')
        finally:
            self._active.pop(run_id, None)

    async def start(self, request: DriverStart) -> AsyncIterator[ReactEmission]:
        kwargs: dict[str, Any] = {
            'task_id': request.run_id,
            'session_id': request.session_id,
            'stream': True,
        }
        allowed_tools = self._allowed_tool_names(request)
        if allowed_tools is not None:
            kwargs['tool_names_filter'] = list(allowed_tools)
        iterator = self._loop.run(
            [copy.deepcopy(dict(item)) for item in request.canonical_messages],
            **kwargs,
        )
        async for emission in self._map(request, iterator):
            yield emission

    async def resume(self, boundary: ReactCommandBoundary, response: Mapping[str, Any]) -> AsyncIterator[ReactEmission]:
        request = DriverStart(run_id=boundary.run_id, session_id=boundary.session_id, canonical_messages=boundary.canonical_messages, session_projection_cursor=boundary.session_projection_cursor, prepared_context_ref=boundary.prepared_context_ref, tool_set_snapshot_ref=boundary.tool_set_snapshot_ref, provider_state=boundary.provider_state, iteration=boundary.iteration, completion_state=boundary.completion_state)
        async for emission in self.start(request):
            yield emission

    async def cancel(self, run_id: str, reason: str) -> None:
        iterator = self._active.get(run_id)
        close = getattr(iterator, 'aclose', None)
        if callable(close):
            await close()

    async def close(self) -> None:
        for run_id in tuple(self._active):
            await self.cancel(run_id, 'driver_close')

class ReActDriver:

    def __init__(self, collaborator: ReActCollaborator, uow: ExecutionUnitOfWork, effect_reader: EffectOutcomeReader, *, reconciler: EffectReconciler | None=None) -> None:
        self._collaborator = collaborator
        self._uow = uow
        self._effects = effect_reader
        self._reconciler = reconciler
        self._requests: dict[str, DriverStart] = {}
        self._volatile: dict[str, ReactCommandBoundary] = {}

    @staticmethod
    def _durable_decision(decision: DriverEvent | None) -> DurableDecisionOpen | None:
        if decision is None:
            return None
        return DurableDecisionOpen(decision_id=decision.decision_id, run_id=decision.run_id, nonce=decision.nonce, kind=decision.decision_kind, prompt_schema_version=decision.prompt_schema_version, prompt=dict(decision.prompt), expires_at=decision.expires_at, call_id=decision.call_id, effect_id=decision.effect_id, tool_name=decision.tool_name, args_hash=decision.args_hash, capability_hash=decision.capability_hash, scope_hash=decision.scope_hash)

    async def _persist_initial_boundary(self, request: DriverStart, boundary: ReactCommandBoundary, *, decision: DriverEvent | None=None) -> ReactCommandBoundary:
        spec = request.run_spec
        if spec is None:
            raise RuntimeError('durable ReAct boundary requires its immutable RunCreate')
        durable_spec = replace(spec, persistence_level=PersistenceLevel.DURABLE)
        waiting = RunEventCandidate(event_key=f'boundary:{boundary.command_id}', kind='run.waiting', status=OutcomeStatus.WAITING, driver_kind=spec.driver_kind, correlation={'command_id': boundary.command_id})
        _, saved = await self._uow.promote_and_persist_batch_boundary(durable_spec, expected_run_version=0, expected_continuation_version=0, payload=boundary.to_payload(), decision=self._durable_decision(decision), waiting_event=waiting)
        return replace(boundary, version=int(saved.version))

    async def _save_durable_boundary(self, boundary: ReactCommandBoundary, *, decision: DriverEvent | None=None, recovery_lease: RecoveryLease | None=None) -> ReactCommandBoundary:
        expected = max(0, boundary.version - 1)
        saved = await self._uow.save_continuation(boundary.run_id, expected, boundary.to_payload(), self._durable_decision(decision), recovery_lease=recovery_lease)
        return replace(boundary, version=int(saved.version))

    @staticmethod
    def _requires_boundary(batch: ReactToolBatch) -> bool:
        return any((call.recoverable_effect or call.requires_authorization for call in batch.calls))

    @staticmethod
    def _permission_decision(boundary: ReactCommandBoundary, index: int) -> DriverEvent:
        call = boundary.pending_calls[index]
        identity = hashlib.sha256(f'permission|{boundary.run_id}|{call.effect_id}'.encode('utf-8')).hexdigest()
        nonce = hashlib.sha256(f'nonce|{identity}'.encode('utf-8')).hexdigest()
        return OpenDecision(run_id=boundary.run_id, command_id=boundary.command_id, decision_id=identity, nonce=nonce, kind='permission', prompt={'tool_name': call.tool_name, 'call_id': call.call_id, 'reason': 'tool_requires_authorization'}, expires_at=time.time() + 300.0, call_id=call.call_id, effect_id=call.effect_id, tool_name=call.tool_name, args_hash=call.args_hash, capability_hash=call.capability_hash, scope_hash=call.scope_hash)

    @staticmethod
    def _grant_state(boundary: ReactCommandBoundary) -> dict[str, Mapping[str, Any]]:
        raw = boundary.completion_state.get('authorization_refs', {})
        return {str(call_id): dict(value) for call_id, value in dict(raw).items() if isinstance(value, Mapping)}

    @classmethod
    def _next_permission_index(cls, boundary: ReactCommandBoundary) -> int | None:
        grants = cls._grant_state(boundary)
        for index in boundary.pending_indexes:
            call = boundary.pending_calls[index]
            if call.requires_authorization and call.call_id not in grants:
                return index
        return None

    @staticmethod
    def _boundary_for_batch(request: DriverStart, batch: ReactToolBatch) -> ReactCommandBoundary:
        return ReactCommandBoundary(run_id=request.run_id, session_id=request.session_id, command_id=batch.command_id, command_kind='execute_tools', canonical_messages=batch.canonical_messages or request.canonical_messages, session_projection_cursor=request.session_projection_cursor, prepared_context_ref=request.prepared_context_ref, tool_set_snapshot_ref=request.tool_set_snapshot_ref, pending_calls=batch.calls, tool_contexts=batch.contexts, outcomes=(None,) * len(batch.calls), provider_state=request.provider_state, iteration=max(request.iteration, batch.iteration), completion_state=request.completion_state, capability_snapshot=request.capability_snapshot)

    @staticmethod
    def _tool_messages(boundary: ReactCommandBoundary) -> tuple[Mapping[str, Any], ...]:
        messages = [copy.deepcopy(dict(item)) for item in boundary.canonical_messages]
        for call, outcome in zip(boundary.pending_calls, boundary.outcomes):
            assert outcome is not None
            messages.append({'role': 'tool', 'tool_call_id': call.call_id, 'name': call.tool_name, 'content': json.dumps({'status': outcome.status.value, 'value': outcome.value, 'error': outcome.error, 'effect_id': outcome.effect_id, 'receipt_ref': outcome.receipt_ref, 'artifact_refs': list(outcome.artifact_refs)}, ensure_ascii=False, sort_keys=True, default=str)})
        return tuple(messages)

    async def _emit(self, request: DriverStart, emissions: AsyncIterator[ReactEmission]) -> AsyncIterator[DriverEvent]:
        async for emission in emissions:
            if isinstance(emission, ReactToken):
                yield TokenCandidate(request.run_id, emission.content, emission.kind)
            elif isinstance(emission, ReactFallback):
                yield ProviderFallbackCandidate(request.run_id, emission.from_provider, emission.to_provider, emission.reason)
            elif isinstance(emission, ReactToolBatch):
                boundary = self._boundary_for_batch(request, emission)
                if self._requires_boundary(emission):
                    permission_index = self._next_permission_index(boundary)
                    if permission_index is not None:
                        decision = self._permission_decision(boundary, permission_index)
                        boundary = replace(boundary, pending_decision=decision)
                        boundary = await self._persist_initial_boundary(request, boundary, decision=decision)
                        yield decision
                        return
                    boundary = await self._persist_initial_boundary(request, boundary)
                else:
                    self._volatile[request.run_id] = boundary
                yield self._execute_command(boundary)
                return
            elif isinstance(emission, ReactDecisionRequest):
                decision = emission.decision
                if decision.run_id != request.run_id:
                    raise ValueError('decision run binding mismatch')
                boundary = ReactCommandBoundary(run_id=request.run_id, session_id=request.session_id, command_id=decision.command_id, command_kind='open_decision', canonical_messages=request.canonical_messages, session_projection_cursor=request.session_projection_cursor, prepared_context_ref=request.prepared_context_ref, tool_set_snapshot_ref=request.tool_set_snapshot_ref, pending_calls=(), tool_contexts=(), outcomes=(), provider_state=request.provider_state, iteration=request.iteration, completion_state=request.completion_state, capability_snapshot=request.capability_snapshot, pending_decision=decision)
                boundary = await self._persist_initial_boundary(request, boundary, decision=decision)
                yield decision
                return
            elif isinstance(emission, ReactDelegateRequest):
                command = emission.command
                if command.run_id != request.run_id:
                    raise ValueError('delegate run binding mismatch')
                boundary = await self._persist_initial_boundary(request, ReactCommandBoundary(run_id=request.run_id, session_id=request.session_id, command_id=command.command_id, command_kind='delegate', canonical_messages=request.canonical_messages, session_projection_cursor=request.session_projection_cursor, prepared_context_ref=request.prepared_context_ref, tool_set_snapshot_ref=request.tool_set_snapshot_ref, pending_calls=(), tool_contexts=(), outcomes=(), provider_state=request.provider_state, iteration=request.iteration, completion_state=request.completion_state, capability_snapshot=request.capability_snapshot, pending_delegate=command))
                yield command
                return
            elif isinstance(emission, ReactFinal):
                yield DriverTerminalCandidate(request.run_id, 'completed', emission.content)
                return
            elif isinstance(emission, ReactFailure):
                yield DriverTerminalCandidate(request.run_id, 'failed', error=emission.error)
                return

    @staticmethod
    def _execute_command(boundary: ReactCommandBoundary) -> DriverEvent:
        indexes = boundary.pending_indexes
        grants = ReActDriver._grant_state(boundary)
        grant_refs = []
        for index in indexes:
            call = boundary.pending_calls[index]
            value = grants.get(call.call_id)
            grant_refs.append(None if value is None else ToolGrantRef(grant_id=str(value['grant_id']), decision_id=str(value['decision_id']), decision_nonce=str(value['decision_nonce']), version=int(value.get('version', 0))))
        return ExecuteTools(run_id=boundary.run_id, command_id=boundary.command_id, calls=tuple((boundary.pending_calls[index] for index in indexes)), contexts=tuple((boundary.tool_contexts[index] for index in indexes)), original_indexes=indexes, grant_refs=tuple(grant_refs) if any((item is not None for item in grant_refs)) else ())

    def start(self, request: DriverStart) -> AsyncIterator[DriverEvent]:

        async def iterator() -> AsyncIterator[DriverEvent]:
            self._requests[request.run_id] = request
            async for candidate in self._emit(request, self._collaborator.start(request)):
                yield candidate
        return iterator()

    async def _load_boundary(self, run_id: str) -> ReactCommandBoundary:
        record = await self._uow.load_continuation(run_id)
        boundary = None if record is None else ReactCommandBoundary.from_record(record)
        if boundary is None:
            boundary = self._volatile.get(run_id)
        if boundary is None:
            raise ValueError('react command boundary not found')
        return boundary

    async def _save_progress(self, boundary: ReactCommandBoundary, *, recovery_lease: RecoveryLease | None=None) -> None:
        if boundary.run_id in self._volatile:
            self._volatile[boundary.run_id] = boundary
        else:
            await self._save_durable_boundary(boundary, recovery_lease=recovery_lease)

    async def _record_outcomes(self, boundary: ReactCommandBoundary, updates: Mapping[int, ToolOutcome], *, recovery_lease: RecoveryLease | None=None) -> ReactCommandBoundary:
        boundary = boundary.with_outcomes(updates)
        await self._save_progress(boundary, recovery_lease=recovery_lease)
        return boundary

    @staticmethod
    def _request_from_boundary(boundary: ReactCommandBoundary) -> DriverStart:
        return DriverStart(run_id=boundary.run_id, session_id=boundary.session_id, canonical_messages=boundary.canonical_messages, session_projection_cursor=boundary.session_projection_cursor, prepared_context_ref=boundary.prepared_context_ref, tool_set_snapshot_ref=boundary.tool_set_snapshot_ref, provider_state=boundary.provider_state, iteration=boundary.iteration, completion_state=boundary.completion_state, capability_snapshot=boundary.capability_snapshot)

    async def _resume_completed(self, boundary: ReactCommandBoundary, *, recovery_lease: RecoveryLease | None=None) -> AsyncIterator[DriverEvent]:
        if not bool(boundary.completion_state.get('model_backfilled')):
            boundary = boundary.with_backfilled_messages(self._tool_messages(boundary))
            await self._save_progress(boundary, recovery_lease=recovery_lease)
        request = self._request_from_boundary(boundary)
        self._requests[boundary.run_id] = request
        async for candidate in self._emit(request, self._collaborator.resume(boundary, {'type': 'tool_outcomes', 'command_id': boundary.command_id})):
            yield candidate

    async def _apply_child_inbox(self, boundary: ReactCommandBoundary, signal: DriverSignal, *, recovery_lease: RecoveryLease | None) -> tuple[ReactCommandBoundary, DriverEvent | None]:
        signal_id = str(getattr(signal, 'signal_id', '') or '').strip()
        if not signal_id:
            return boundary, None
        clear_delegate = signal.kind == 'child_terminal' or boundary.pending_delegate is not None and boundary.pending_delegate.join_policy is JoinPolicy.DETACHED
        updated = replace(boundary, pending_delegate=None if clear_delegate else boundary.pending_delegate, version=boundary.version + 1)
        event = RunEventCandidate(event_key=f'child-signal:{signal_id}', kind=signal.kind, status=OutcomeStatus.SUCCEEDED if signal.kind == 'child_terminal' and signal.status == 'completed' else OutcomeStatus.ACCEPTED, driver_kind='react', correlation={'command_id': signal.command_id, 'child_run_id': signal.child_run_id, 'signal_id': signal_id}, payload={'status': getattr(signal, 'status', 'accepted'), 'value': getattr(signal, 'value', None)})
        _, saved, stored_event = await self._uow.apply_child_signal_and_ack(signal_id, expected_continuation_version=boundary.version, continuation_payload=updated.to_payload(), event=event, recovery_lease=recovery_lease)
        return replace(updated, version=int(saved.version)), PersistedEventCandidate(stored_event)

    def signal(self, signal: DriverSignal, recovery_lease: RecoveryLease | None = None) -> AsyncIterator[DriverEvent]:

        async def iterator() -> AsyncIterator[DriverEvent]:
            if signal.kind == "tool_outcomes":
                boundary = await self._load_boundary(signal.run_id)
                if boundary.command_id != signal.command_id:
                    raise ValueError('tool outcome command binding mismatch')
                indexes = {call.call_id: index for index, call in enumerate(boundary.pending_calls)}
                updates: dict[int, ToolOutcome] = {}
                for outcome in signal.outcomes:
                    if outcome.call_id not in indexes:
                        raise ValueError('tool outcome call not present in boundary')
                    updates[indexes[outcome.call_id]] = outcome
                boundary = await self._record_outcomes(boundary, updates, recovery_lease=recovery_lease)
                if boundary.pending_indexes:
                    yield self._execute_command(boundary)
                    return
                async for candidate in self._resume_completed(boundary, recovery_lease=recovery_lease):
                    yield candidate
                return
            if signal.kind == "decision":
                boundary = await self._load_boundary(signal.run_id)
                if boundary.pending_decision is None or boundary.pending_decision.decision_id != signal.decision_id:
                    raise ValueError('decision does not match pending boundary')
                pending = boundary.pending_decision
                assert pending is not None
                if boundary.pending_calls and pending.call_id is not None:
                    indexes = {call.call_id: index for index, call in enumerate(boundary.pending_calls)}
                    index = indexes[pending.call_id]
                    if bool(signal.response.get('allow', signal.response.get('approved', True))):
                        grant_id = str(signal.response.get('grant_id') or '').strip()
                        if not grant_id:
                            raise ValueError('allowed permission response is missing grant_id')
                        state = copy.deepcopy(dict(boundary.completion_state))
                        refs = dict(state.get('authorization_refs') or {})
                        refs[pending.call_id] = {'grant_id': grant_id, 'decision_id': pending.decision_id, 'decision_nonce': pending.nonce, 'version': int(signal.response.get('grant_version') or 0)}
                        state['authorization_refs'] = refs
                        boundary = replace(boundary, completion_state=state, pending_decision=None, version=boundary.version + 1)
                    else:
                        boundary = boundary.with_outcomes({index: ToolOutcome.failed(boundary.pending_calls[index], 'authorization_denied')})
                        boundary = replace(boundary, pending_decision=None)
                    next_index = self._next_permission_index(boundary)
                    if next_index is not None:
                        decision = self._permission_decision(boundary, next_index)
                        boundary = replace(boundary, pending_decision=decision)
                        boundary = await self._save_durable_boundary(boundary, decision=decision, recovery_lease=recovery_lease)
                        yield decision
                        return
                    boundary = await self._save_durable_boundary(boundary, recovery_lease=recovery_lease)
                    if boundary.pending_indexes:
                        yield self._execute_command(boundary)
                    else:
                        async for candidate in self._resume_completed(boundary, recovery_lease=recovery_lease):
                            yield candidate
                    return
                messages = tuple(boundary.canonical_messages) + ({'role': 'system', 'content': json.dumps({'decision_id': signal.decision_id, 'response': dict(signal.response)}, ensure_ascii=False, sort_keys=True)},)
                state = copy.deepcopy(dict(boundary.completion_state))
                state['model_backfilled'] = True
                boundary = replace(boundary, canonical_messages=messages, completion_state=state, pending_decision=None, version=boundary.version + 1)
                boundary = await self._save_durable_boundary(boundary, recovery_lease=recovery_lease)
                request = self._request_from_boundary(boundary)
                self._requests[signal.run_id] = request
                async for candidate in self._emit(request, self._collaborator.resume(boundary, {'type': 'decision', 'response': dict(signal.response)})):
                    yield candidate
                return
            if signal.kind == "child_accepted":
                boundary = await self._load_boundary(signal.run_id)
                command = boundary.pending_delegate
                if command is None or command.command_id != signal.command_id:
                    raise ValueError('delegate command not found')
                boundary, persisted = await self._apply_child_inbox(boundary, signal, recovery_lease=recovery_lease)
                if persisted is not None:
                    yield persisted
                else:
                    yield ChildAcceptedCandidate(signal.run_id, signal.command_id, signal.child_run_id, command.join_policy)
                if command.join_policy is JoinPolicy.DETACHED:
                    request = self._request_from_boundary(boundary)
                    async for candidate in self._emit(request, self._collaborator.resume(boundary, {'type': 'child_accepted', 'child_run_id': signal.child_run_id})):
                        yield candidate
                return
            if signal.kind == "child_terminal":
                boundary = await self._load_boundary(signal.run_id)
                command = boundary.pending_delegate
                if command is None or command.command_id != signal.command_id:
                    raise ValueError('delegate command not found')
                boundary, persisted = await self._apply_child_inbox(boundary, signal, recovery_lease=recovery_lease)
                if persisted is not None:
                    yield persisted
                if command.join_policy is JoinPolicy.ROOT_TERMINAL_CHILD:
                    yield DriverTerminalCandidate(signal.run_id, 'completed' if signal.status == 'completed' else 'failed', content=str(signal.value or ''), error=None if signal.status == 'completed' else str(signal.value or signal.status), correlation={'child_run_id': signal.child_run_id})
                    return
                if command.join_policy is JoinPolicy.JOIN_BEFORE_FINAL:
                    request = self._request_from_boundary(boundary)
                    async for candidate in self._emit(request, self._collaborator.resume(boundary, {'type': 'child_terminal', 'child_run_id': signal.child_run_id, 'status': signal.status, 'value': signal.value})):
                        yield candidate
        return iterator()

    def recover(self, run_id: str, recovery_lease: RecoveryLease) -> AsyncIterator[DriverEvent]:

        async def iterator() -> AsyncIterator[DriverEvent]:
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
                    outcome = await self._reconciler.reconcile(call, boundary.tool_contexts[index], outcome)
                updates[index] = outcome
            if updates:
                boundary = await self._record_outcomes(boundary, updates, recovery_lease=recovery_lease)
            if boundary.pending_indexes:
                yield self._execute_command(boundary)
                return
            async for candidate in self._resume_completed(boundary, recovery_lease=recovery_lease):
                yield candidate
        return iterator()

    def cancel(self, run_id: str, reason: str) -> AsyncIterator[DriverEvent]:

        async def iterator() -> AsyncIterator[DriverEvent]:
            await self._collaborator.cancel(run_id, reason)
            yield CancelAcknowledgedCandidate(run_id, reason)
        return iterator()

    async def close(self) -> None:
        await self._collaborator.close()
__all__ = ['EffectOutcomeReader', 'EffectReconciler', 'LegacyAgentLoopCollaborator', 'LegacyAgentLoopToolInterceptionError', 'ReActCollaborator', 'ReActDriver', 'ReactDecisionRequest', 'ReactDelegateRequest', 'ReactEmission', 'ReactFailure', 'ReactFallback', 'ReactFinal', 'ReactToken', 'ReactToolBatch']
