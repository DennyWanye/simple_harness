
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Legacy AgentEvent presentation extracted without changing its owner.
R2 keeps ``main.py::_run_chat`` and ``AgentLoop`` in charge.  This presenter
only owns the product projection already performed by their event bridge.
Handlers are explicitly registered in live, durable, and domain groups so a
later Driver can reuse the same mapping without growing another switch.
"""
from __future__ import annotations
import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol
import structlog
from agent.agent_loop import AgentEvent, AssistantDeltaEvent, AssistantMessageEvent, AsyncHandoffEvent, ContextCompactedEvent, ErrorEvent, FinalEvent, PipelineEvent, ProviderChainFallbackEvent, ToolCallEvent, ToolResultEvent
from agent.auto_resume import is_auto_resume_trigger
from deskpet.execution.contracts import OutcomeStatus, RunEvent, thaw_json
from deskpet.agent.assembler.components.tool import has_image_completion_claim
from deskpet.agent.product_domain_sink import ProductDomainSink
from deskpet.agent.turn_preparer import ProductDomainCommand
from deskpet.tools.public_projection import project_public_tool_arguments, project_public_tool_result
from llm.types import ToolCall
logger = structlog.get_logger(__name__)
HandlerDomain = Literal['live', 'durable', 'domain']
Handler = Callable[[AgentEvent, 'RunPresentationContext', 'PresentationState'], Awaitable[None]]
DomainHandler = Callable[[ProductDomainCommand, ProductDomainSink], Awaitable[bool]]

class RunEventPresentationAdapter(Protocol):
    """One venue-neutral typed RunEvent boundary shared by Text and Voice."""

    def to_presentation_events(self, event: RunEvent) -> tuple[AgentEvent, ...]:
        ...


class CanonicalRunEventPresentationAdapter:
    """Translate canonical RunEvents without owning product side effects."""

    def to_presentation_events(self, event: RunEvent) -> tuple[AgentEvent, ...]:
        candidate = event.candidate
        payload = thaw_json(candidate.payload)
        correlation = thaw_json(candidate.correlation)
        error = None if candidate.error is None else thaw_json(candidate.error)
        assert isinstance(payload, dict)
        assert isinstance(correlation, dict)
        assert error is None or isinstance(error, dict)
        iteration = int(correlation.get('iteration') or event.durable_seq or (
            event.live_cursor.live_seq if event.live_cursor is not None else 0
        ))

        if candidate.kind == 'transcript':
            return (AssistantDeltaEvent(
                content=str(payload.get('text') or ''),
                kind=str(payload.get('token_kind') or 'content'),
                iteration=iteration,
            ),)
        if candidate.kind == 'tool_requested':
            calls = payload.get('calls')
            if not isinstance(calls, list):
                calls = [
                    {'id': f"{correlation.get('command_id', event.event_id)}:{index}",
                     'name': name, 'arguments': {}}
                    for index, name in enumerate(payload.get('tools') or ())
                ]
            result: list[AgentEvent] = []
            for raw in calls:
                if not isinstance(raw, Mapping):
                    continue
                arguments = raw.get('arguments')
                result.append(ToolCallEvent(
                    tool_call=ToolCall(
                        id=str(raw.get('id') or event.event_id),
                        name=str(raw.get('name') or ''),
                        arguments=dict(arguments) if isinstance(arguments, Mapping) else {},
                    ),
                    iteration=iteration,
                ))
            return tuple(result)
        if candidate.kind == 'tool.outcome':
            outcome = payload.get('outcome')
            normalized = dict(outcome) if isinstance(outcome, Mapping) else {'value': outcome}
            tool_name = str(payload.get('tool_name') or correlation.get('tool_name') or '')
            return (ToolResultEvent(
                tool_call_id=str(correlation.get('call_id') or ''),
                tool_name=tool_name,
                result=json.dumps(normalized, ensure_ascii=False),
                outcome_status=candidate.status.value,
                outcome_error=error,
                iteration=iteration,
            ),)
        if candidate.kind == 'provider_fallback':
            return (ProviderChainFallbackEvent(
                session_id=event.session_id,
                from_=str(payload.get('from_provider') or ''),
                to=str(payload.get('to_provider') or ''),
                reason=str(payload.get('reason') or ''),
                iteration=iteration,
            ),)
        if candidate.kind == 'admission.waiting':
            return (PipelineEvent(type='chat_v2_plan', payload=payload),)
        if candidate.kind in {'final', 'run.final'}:
            if candidate.status is OutcomeStatus.SUCCEEDED:
                return (FinalEvent(
                    content=str(payload.get('text') or ''),
                    reasoning_content=str(payload.get('reasoning') or ''),
                    iteration=iteration,
                ),)
            detail = str((error or {}).get('message') or payload.get('text') or candidate.status.value)
            return (ErrorEvent(
                reason=candidate.status.value,
                detail=detail,
                error_class=str((error or {}).get('code') or 'run_failed'),
                iteration=iteration,
            ),)

        return (PipelineEvent(
            type='run_event',
            payload={
                'run_id': event.run_id,
                'kind': candidate.kind,
                'status': candidate.status.value,
                'driver_kind': candidate.driver_kind,
                'correlation': correlation,
                'payload': payload,
                'error': error,
                'artifact_refs': list(candidate.artifact_refs),
            },
            iteration=iteration,
        ),)

@dataclass(slots=True)
class PresentationState:
    final_text: str = ''
    final_reasoning: str = ''
    had_tool_call: bool = False

@dataclass(slots=True)
class RunPresentationContext:
    session_id: str
    text: str
    websocket: Any
    services: Mapping[str, Any]
    config: Any
    messages: list[dict[str, Any]]
    session_db: Any | None
    vector_worker: Any | None
    activity_store: Any | None
    provider_chain: Any | None
    fallback_provider: Any | None
    request_id: str | None
    max_iterations: int
    in_code_mode: bool
    is_sentinel: bool
    buffer_short_followup_stream: bool
    skill_candidate_waiters: MutableMapping[str, Any]
    broadcast: Callable[[Any, dict[str, Any]], Awaitable[None]]
    send_final: Callable[..., Awaitable[None]]
    emit_context_usage: Callable[..., Awaitable[None]]
    codify_skill: Callable[..., Awaitable[None]]
    intent_label_from_turn: Callable[[bool], str]
    assembler: Any | None = None
    bundle: Any | None = None
    billing_ledger: Any | None = None
    provider: Any | None = None

class RunPresenter:
    """Translate AgentEvents through one categorized handler registry."""

    def __init__(self) -> None:
        self._handlers: dict[type[AgentEvent], tuple[HandlerDomain, Handler]] = {}
        self._domain_handlers: dict[str, DomainHandler] = {}

    def register(self, domain: HandlerDomain, event_type: type[AgentEvent], handler: Handler) -> None:
        if event_type in self._handlers:
            raise ValueError(f'presenter handler already registered: {event_type.__name__}')
        self._handlers[event_type] = (domain, handler)

    @property
    def registrations(self) -> tuple[tuple[str, str], ...]:
        return tuple(((event_type.__name__, domain) for event_type, (domain, _handler) in self._handlers.items()))

    def register_domain(self, kind: str, handler: DomainHandler) -> None:
        if kind in self._domain_handlers:
            raise ValueError(f'domain handler already registered: {kind}')
        self._domain_handlers[kind] = handler

    async def present_domain(self, command: ProductDomainCommand, sink: ProductDomainSink) -> bool:
        try:
            handler = self._domain_handlers[command.kind]
        except KeyError as exc:
            raise ValueError(f'domain handler not registered: {command.kind}') from exc
        return await handler(command, sink)

    async def present(self, event: AgentEvent, context: RunPresentationContext, state: PresentationState) -> None:
        if isinstance(event, ToolCallEvent) and event.tool_call:
            state.had_tool_call = True
        await self._record_activity(event, context)
        for event_type, (_domain, handler) in self._handlers.items():
            if isinstance(event, event_type):
                await handler(event, context, state)
                return

    async def present_run_event(
        self,
        event: RunEvent,
        adapter: RunEventPresentationAdapter,
        context: RunPresentationContext,
        state: PresentationState,
    ) -> None:
        for presentation_event in adapter.to_presentation_events(event):
            await self.present(presentation_event, context, state)

    async def finish_turn(self, context: RunPresentationContext, state: PresentationState) -> None:
        if context.bundle is not None and context.assembler is not None:
            try:
                context.assembler.feedback(context.bundle, final_response=state.final_text)
            except Exception as exc:
                logger.warning('p4_assembler_feedback_failed', error=str(exc))
        usage = getattr(context.provider, 'last_usage', None)
        if usage and context.billing_ledger is not None:
            try:
                base_url = getattr(context.provider, 'base_url', '') or ''
                await context.billing_ledger.record(provider='local' if 'localhost' in base_url or '127.0.0.1' in base_url else 'cloud', model=getattr(context.provider, 'model', 'unknown'), prompt_tokens=int(usage.get('prompt_tokens', 0)), completion_tokens=int(usage.get('completion_tokens', 0)))
            except Exception as exc:
                logger.warning('billing_record_failed', error=str(exc))
            context.provider.last_usage = None

    async def _record_activity(self, event: AgentEvent, context: RunPresentationContext) -> None:
        store = context.activity_store
        if store is None:
            return
        try:
            common = {'iteration': event.iteration, 'max_iterations': context.max_iterations}
            if isinstance(event, ToolCallEvent) and event.tool_call:
                await store.bump(context.session_id, event_type='tool_call', name=event.tool_call.name, args=event.tool_call.arguments, **common)
            elif isinstance(event, ToolResultEvent):
                await store.bump(context.session_id, event_type='tool_result', name=event.tool_name, ok=event.outcome_status == 'succeeded', snippet=(event.result or '')[:80], **common)
            elif isinstance(event, AsyncHandoffEvent):
                await store.bump(context.session_id, event_type='async_handoff', name=event.tool_name, **common)
                await store.set_status(context.session_id, 'idle')
            elif isinstance(event, AssistantMessageEvent):
                await store.bump(context.session_id, event_type='assistant_message', **common)
            elif isinstance(event, FinalEvent):
                await store.bump(context.session_id, event_type='final', **common)
                await store.set_status(context.session_id, 'idle')
            elif isinstance(event, ErrorEvent):
                await store.bump(context.session_id, event_type='error', snippet=(event.detail or event.reason or '')[:80], **common)
                await store.mark_error_pending(context.session_id)
        except Exception as exc:
            logger.debug('session_activity_bump_failed', error=str(exc))

def build_legacy_run_presenter() -> RunPresenter:
    presenter = RunPresenter()
    presenter.register('live', AssistantDeltaEvent, _present_delta)
    presenter.register('live', AssistantMessageEvent, _present_assistant)
    presenter.register('durable', ToolCallEvent, _present_tool_call)
    presenter.register('durable', ToolResultEvent, _present_tool_result)
    presenter.register("domain", AsyncHandoffEvent, _present_handoff)
    presenter.register('durable', FinalEvent, _present_final)
    presenter.register('durable', ErrorEvent, _present_error)
    presenter.register('domain', ContextCompactedEvent, _present_compacted)
    presenter.register('domain', PipelineEvent, _present_pipeline)
    presenter.register('domain', ProviderChainFallbackEvent, _present_provider_fallback)
    presenter.register_domain('pipeline_event', _domain_pipeline)
    presenter.register_domain('clarification', _domain_clarification)
    presenter.register_domain('plan_proposed', _domain_plan_proposed)
    presenter.register_domain('plan_confirmation', _domain_plan_confirmation)
    return presenter

async def _domain_pipeline(command: ProductDomainCommand, sink: ProductDomainSink) -> bool:
    await sink.emit(command.payload)
    return True

async def _domain_clarification(command: ProductDomainCommand, sink: ProductDomainSink) -> bool:
    payload = dict(command.payload)
    sid, text = (str(payload['session_id']), str(payload['text']))
    await sink.emit({'type': 'chat_v2_final', 'payload': {'session_id': sid, 'text': text}})
    await sink.set_idle(sid)
    await sink.persist_assistant(sid, text or '')
    return False

async def _domain_plan_proposed(command: ProductDomainCommand, sink: ProductDomainSink) -> bool:
    payload = dict(command.payload)
    frame = {'type': 'chat_v2_plan', 'payload': payload}
    await sink.emit(frame)
    await sink.store_plan(payload)
    return True

async def _domain_plan_confirmation(command: ProductDomainCommand, sink: ProductDomainSink) -> bool:
    payload = dict(command.payload)
    sid = str(payload['session_id'])
    if await sink.await_plan(payload):
        return True
    await sink.emit({'type': 'chat_v2_plan_cancelled', 'payload': {'session_id': sid}})
    if payload.get('in_code_mode'):
        await sink.set_idle(sid)
    return False

async def _send_both(context: RunPresentationContext, frame: dict[str, Any]) -> None:
    await context.websocket.send_json(frame)
    await context.broadcast(context.websocket, frame)

async def _present_delta(event: AgentEvent, context: RunPresentationContext, state: PresentationState) -> None:
    assert isinstance(event, AssistantDeltaEvent)
    if context.buffer_short_followup_stream:
        return
    await _send_both(context, {'type': 'chat_v2_delta', 'payload': {'session_id': context.session_id, 'kind': event.kind, 'content': event.content, 'iteration': event.iteration}})

async def _present_assistant(event: AgentEvent, context: RunPresentationContext, state: PresentationState) -> None:
    assert isinstance(event, AssistantMessageEvent)
    if event.content and event.tool_calls:
        await context.websocket.send_json({'type': 'chat_response', 'payload': {'text': event.content, 'provider': 'v2', 'session_id': context.session_id}})

async def _present_tool_call(event: AgentEvent, context: RunPresentationContext, state: PresentationState) -> None:
    assert isinstance(event, ToolCallEvent)
    if event.tool_call is None:
        return
    public_arguments = project_public_tool_arguments(event.tool_call.name, event.tool_call.arguments)
    await _send_both(context, {'type': 'tool_use_event', 'payload': {'kind': 'request', 'tool_name': event.tool_call.name, 'params': public_arguments, 'turn': event.iteration, 'session_id': context.session_id}})
    await _send_both(context, {'type': 'tool_call', 'payload': {'name': event.tool_call.name, 'arguments': public_arguments, 'turn': event.iteration, 'session_id': context.session_id}})
    if context.session_db is not None:
        try:
            await context.session_db.append_message(session_id=context.session_id, role='assistant', content='', tool_calls=[{'id': event.tool_call.id, 'type': 'function', 'function': {'name': event.tool_call.name, 'arguments': json.dumps(event.tool_call.arguments, ensure_ascii=False)}}])
        except Exception as exc:
            logger.warning('chat_persist_tool_call_failed', error=str(exc))

async def _present_tool_result(event: AgentEvent, context: RunPresentationContext, state: PresentationState) -> None:
    assert isinstance(event, ToolResultEvent)
    try:
        parsed = json.loads(event.result)
    except Exception:
        parsed = event.result
    public_result = project_public_tool_result(event.tool_name, parsed)
    public_text = public_result if isinstance(public_result, str) else json.dumps(public_result, ensure_ascii=False)
    await _send_both(context, {'type': 'tool_use_event', 'payload': {'kind': 'result', 'tool_name': event.tool_name, 'result': public_text, 'turn': event.iteration, 'session_id': context.session_id}})
    ok = event.outcome_status == 'succeeded'
    result_frame = {'type': 'tool_result', 'payload': {'tool': event.tool_name, 'ok': ok, 'result': public_text, 'turn': event.iteration, 'session_id': context.session_id}}
    if not ok:
        result_frame['payload']['status'] = event.outcome_status
    if event.outcome_error is not None:
        result_frame['payload']['error'] = event.outcome_error
    await _send_both(context, result_frame)
    if context.session_db is not None:
        try:
            await context.session_db.append_message(session_id=context.session_id, role='tool', content=event.result if isinstance(event.result, str) else json.dumps(event.result, ensure_ascii=False), tool_call_id=event.tool_call_id or '')
        except Exception as exc:
            logger.warning('chat_persist_tool_result_failed', error=str(exc))

async def _present_handoff(event: AgentEvent, context: RunPresentationContext, state: PresentationState) -> None:
    assert isinstance(event, AsyncHandoffEvent)
    await _send_both(context, {"type": "chat_v2_final", 'payload': {'text': '', 'iterations': event.iteration, 'session_id': context.session_id, "handoff_run_id": event.run_id}})

async def _present_final(event: AgentEvent, context: RunPresentationContext, state: PresentationState) -> None:
    assert isinstance(event, FinalEvent)
    state.final_text = event.content
    state.final_reasoning = event.reasoning_content
    if context.buffer_short_followup_stream and has_image_completion_claim(state.final_text):
        state.final_text = '\u6211\u521a\u624d\u6ca1\u6709\u8c03\u7528\u56fe\u7247\u751f\u6210\u5de5\u5177\uff0c\u4e5f\u6ca1\u6709\u751f\u6210\u56fe\u7247\u3002\u8fd9\u662f\u4e00\u6761\u4f9d\u8d56\u4e0a\u6587\u7684\u8865\u5145\u8bf4\u660e\u3002\u6211\u4f1a\u56de\u5230\u524d\u6587\u8bed\u5883\u7ee7\u7eed\u56de\u7b54\uff0c\u800c\u4e0d\u662f\u628a\u5b83\u5f53\u4f5c\u751f\u56fe\u8bf7\u6c42\u3002'
    if context.session_db is not None:
        try:
            message_id = await context.session_db.append_message(session_id=context.session_id, role='assistant', content=state.final_text or '', reasoning_content=state.final_reasoning or None)
            if context.vector_worker is not None and message_id is not None and state.final_text:
                await context.vector_worker.enqueue(message_id, state.final_text)
        except Exception as exc:
            logger.warning('chat_persist_assistant_failed', error=str(exc))
    await context.send_final(context.websocket, {'type': 'chat_v2_final', 'payload': {'text': state.final_text, 'iterations': event.iteration, 'session_id': context.session_id}}, session_id=context.session_id, request_id=context.request_id)
    if context.in_code_mode and (not context.is_sentinel):
        preference = context.services.get('preference_memory')
        if preference is not None and context.text:
            asyncio.create_task(preference.record(context.text, context.intent_label_from_turn(state.had_tool_call), 'intent'))
    await context.emit_context_usage(context.websocket, context.session_id, provider_chain=context.provider_chain, fallback_provider=context.fallback_provider)
    try:
        activity = context.services.get('session_activity')
        if activity is not None:
            snapshot = await activity.get(context.session_id)
            if snapshot is not None and snapshot.auto_resume_attempts > 0:
                await context.websocket.send_json({'type': 'auto_resume_succeeded', 'payload': {'session_id': context.session_id, 'attempts': snapshot.auto_resume_attempts}})
    except Exception as exc:
        logger.debug('auto_resume_success_emit_failed', sid=context.session_id, error=str(exc))
    await context.codify_skill(context.services, context.config, context.session_id, context.websocket, context.skill_candidate_waiters)

async def _present_error(event: AgentEvent, context: RunPresentationContext, state: PresentationState) -> None:
    assert isinstance(event, ErrorEvent)
    handled = False
    try:
        if is_auto_resume_trigger(event.reason or ''):
            orchestrator = context.services.get('auto_resume')
            if orchestrator is not None:
                result = await orchestrator.handle_failure(context.session_id, event.reason or '', {'session_id': context.session_id, 'reason': event.reason, 'detail': event.detail, 'iteration': event.iteration}, context.messages)
                handled = result.action in {'spawned', 'exhausted'}
    except Exception as exc:
        logger.debug('auto_resume_handle_failed', sid=context.session_id, error=str(exc))
    if not handled:
        await context.websocket.send_json({'type': 'chat_v2_error', 'payload': {'reason': event.reason, 'detail': event.detail, 'session_id': context.session_id, 'error_class': event.error_class or ''}})
    await context.codify_skill(context.services, context.config, context.session_id, context.websocket, context.skill_candidate_waiters)

async def _present_compacted(event: AgentEvent, context: RunPresentationContext, state: PresentationState) -> None:
    assert isinstance(event, ContextCompactedEvent)
    try:
        await context.websocket.send_json({'type': 'context_compacted', 'payload': {'reduction': event.reduction, 'tokens_in': event.tokens_in, 'tokens_out': event.tokens_out, 'model': event.model or '', 'session_id': context.session_id}})
    except Exception as exc:
        logger.debug('context_compacted_ws_failed', sid=context.session_id, error=str(exc))

async def _present_pipeline(event: AgentEvent, context: RunPresentationContext, state: PresentationState) -> None:
    assert isinstance(event, PipelineEvent)
    frame = {'type': event.type, 'payload': {'session_id': context.session_id, **event.payload}}
    try:
        await _send_both(context, frame)
    except Exception as exc:
        logger.debug('pipeline_event_ws_failed', sid=context.session_id, error=str(exc))

async def _present_provider_fallback(event: AgentEvent, context: RunPresentationContext, state: PresentationState) -> None:
    assert isinstance(event, ProviderChainFallbackEvent)
    await _send_both(context, {'type': 'provider_chain_fallback', 'payload': {'session_id': context.session_id, 'from': event.from_, 'to': event.to, 'reason': event.reason}})

__all__ = ['CanonicalRunEventPresentationAdapter', 'PresentationState', 'RunEventPresentationAdapter', 'RunPresentationContext', 'RunPresenter', 'build_legacy_run_presenter']
