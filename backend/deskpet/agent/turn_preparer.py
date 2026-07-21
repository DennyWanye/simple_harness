
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Product-owned preparation stages used before either execution driver.
The legacy text ingress remains the production owner during R2.  This module
only moves its product policy into three explicit stages so the later Kernel
cutover can consume the same, parity-locked result without learning DeskPet
prompt, pipeline, or plan semantics.
"""
from __future__ import annotations
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
import structlog
from agent.nudge_queue import format_hints_for_injection
from agent.plan import maybe_extract_plan, plan_to_system_message
from deskpet.agent.attachment_budget import append_user_attachment_blocks
logger = structlog.get_logger(__name__)
Venue = Literal['text', 'code', 'voice', 'background']

@dataclass(frozen=True, slots=True)
class TurnInput:
    """Complete product input; adapters are not allowed to drop fields."""
    text: str
    session_id: str
    request_id: str | None = None
    turn_id: str | None = None
    venue: Venue = 'text'
    mode: str = 'companion'
    memory_policy: Mapping[str, Any] | None = None
    explicit_new: bool = False
    attachment_blocks: tuple[Mapping[str, Any], ...] = ()
    provider_ref: str | None = None
    capability_ref: str | None = None
    workspace_ref: str | None = None

    @property
    def is_sentinel(self) -> bool:
        return self.text.startswith('<<') and self.text.endswith('>>')

@dataclass(slots=True)
class PreparedTurnContext:
    turn: TurnInput
    messages: list[dict[str, Any]]
    bundle: Any | None
    assembler: Any | None

@dataclass(slots=True)
class RoutedTurnIntent:
    prepared: PreparedTurnContext
    pre_loop: Any | None = None
    problem_type: str | None = None
    attack_order: Any | None = None
    contradiction_descs: dict[str, str] | None = None
    commands: tuple['ProductDomainCommand', ...] = ()
    continue_turn: bool = True

@dataclass(slots=True)
class PlannedTurnDecision:
    routed: RoutedTurnIntent
    plan: Any | None = None
    auto_confirmed: bool = False
    commands: tuple['ProductDomainCommand', ...] = ()

@dataclass(frozen=True, slots=True)
class ProductDomainCommand:
    """Venue-neutral product interaction interpreted by RunPresenter."""
    kind: Literal['pipeline_event', 'clarification', 'plan_proposed', 'plan_confirmation']
    payload: Mapping[str, Any]

class ProductTurnPreparer:
    """Compose existing product components through three typed stages."""

    async def prepare_context(self, turn: TurnInput, *, services: Mapping[str, Any], config: Any, local_llm: Any, tool_registry: Any, current_message_id: int | None, summary_user_is_confused: Callable[[str], bool], summary_latest_task_snapshot: Callable[[Sequence[Any]], str | None], summary_build_reinject_msg: Callable[[str], dict[str, Any]]) -> PreparedTurnContext:
        bundle = None
        assembler = services.get('context_assembler')
        if assembler is not None and getattr(assembler, 'enabled', True):
            try:
                code_mode = services.get('code_mode')
                code_config = {'enabled': False, 'project_root': ''}
                persona_model = getattr(local_llm, 'model', 'unknown')
                persona_base = getattr(local_llm, 'base_url', '')
                if code_mode and code_mode.is_enabled(turn.session_id):
                    state = code_mode.get(turn.session_id)
                    if state and state.project_root:
                        code_config = {'enabled': True, 'project_root': str(state.project_root)}
                    try:
                        session_db = services.get('session_db')
                        binding = (await session_db.get_code_session_provider_binding(turn.session_id) if session_db is not None else {}) or {}
                        preferred_model = binding.get('preferred_model')
                        if preferred_model:
                            persona_model = preferred_model
                        else:
                            agent_config = (config.raw.get('agent') if hasattr(config, 'raw') else None) or {}
                            code_model = str(agent_config.get('code_model') or '').strip()
                            if code_model:
                                persona_model = code_model
                    except Exception as exc:
                        logger.debug('persona_model_resolve_skipped', sid=turn.session_id, error=str(exc))
                skill_config: dict[str, Any] = {}
                try:
                    disclosure = config.skills.auto_disclosure
                    skill_config = {'auto_disclosure': {'enabled': bool(disclosure.enabled), 'strong_threshold': float(disclosure.strong_threshold), 'budget_tokens': int(disclosure.budget_tokens), 'per_skill_max_tokens': int(disclosure.per_skill_max_tokens)}}
                except Exception:
                    pass
                bundle = await assembler.assemble(user_message=turn.text, memory_manager=services.get('memory_manager'), tool_registry=tool_registry, skill_registry=services.get('skill_loader'), mcp_manager=services.get('mcp_manager'), session_id=turn.session_id, current_message_id=current_message_id, task_type_override='code' if code_config['enabled'] else None, memory_policy_override=turn.memory_policy, config={'llm': {'model': persona_model, 'base_url': persona_base}, 'code_mode': code_config, 'skills': skill_config, 'features': {'context_os_v1': bool(getattr(getattr(config, 'features', None), 'context_os_v1', False))}})
                if bundle is not None and bundle.decisions is not None:
                    bundle.decisions.timestamp = time.time()
                    bundle.decisions.session_id = turn.session_id
            except Exception as exc:
                logger.warning('p4_assembler_failed', error=str(exc), error_type=type(exc).__name__)
                bundle = None
        if bundle is not None:
            messages = bundle.build_messages(user_message=turn.text, history=bundle.history, late_system_nudge=bundle.late_system_nudge)
        else:
            messages = [{'role': 'user', 'content': turn.text}]
        if turn.attachment_blocks:
            messages = append_user_attachment_blocks(messages, turn.attachment_blocks)
        if turn.is_sentinel:
            directive = '\uff08\u7cfb\u7edf\u81ea\u52a8\u7eed\u8dd1\uff1a\u4f60\u4e0a\u4e00\u8f6e\u8fd8\u6ca1\u628a\u7528\u6237\u8bf7\u6c42\u7684\u4efb\u52a1\u505a\u5b8c\u5c31\u8fbe\u5230\u4e86\u8fed\u4ee3\u4e0a\u9650\u3002**\u4e0d\u8981\u91cd\u65b0\u81ea\u6211\u4ecb\u7ecd\u3001\u4e0d\u8981\u53cd\u95ee\u7528\u6237\u60f3\u505a\u4ec0\u4e48**\u2014\u2014\u56de\u987e\u4e0a\u9762\u7684\u5bf9\u8bdd\u5386\u53f2\u4e0e\u5de5\u5177\u7ed3\u679c\uff0c\u627e\u51fa\u7528\u6237\u6700\u521d\u8bf7\u6c42\u7684\u90a3\u4e2a\u4efb\u52a1\u8fd8\u5dee\u54ea\u4e9b\u6b65\u9aa4\uff0c\u76f4\u63a5\u7ee7\u7eed\u628a\u5b83\u505a\u5b8c\u3002\uff09'
            for index in range(len(messages) - 1, -1, -1):
                if messages[index].get('role') == 'user':
                    messages[index] = {**messages[index], 'content': directive}
                    break
            else:
                messages.append({'role': 'user', 'content': directive})
        try:
            nudge_queue = services.get('nudge_queue')
            if nudge_queue is not None:
                hints = await nudge_queue.pop_all(turn.session_id)
                hint_text = format_hints_for_injection(hints) if hints else ''
                if hint_text:
                    self._insert_after_system(messages, {'role': 'system', 'content': hint_text, '_is_supervisor_hint': True})
                    session_db = services.get('session_db')
                    if session_db is not None:
                        for hint in hints:
                            try:
                                await session_db.append_supervisor_hint(session_id=turn.session_id, alert_id=hint.alert_id or '', hint_text=hint.text, action='dispatched', severity=hint.severity)
                            except Exception as exc:
                                logger.debug('supervisor_dispatched_audit_failed', error=str(exc))
        except Exception as exc:
            logger.debug('supervisor_hint_inject_failed', error=str(exc))
        summary_enabled = bool(getattr(getattr(config, 'features', None), 'summary_quality_loop', False))
        if summary_enabled and (not turn.is_sentinel) and summary_user_is_confused(turn.text):
            try:
                file_memory = services.get('file_memory')
                if file_memory is not None:
                    latest = summary_latest_task_snapshot(await file_memory.list_entries('memory'))
                    if latest:
                        self._insert_after_system(messages, summary_build_reinject_msg(latest))
            except Exception as exc:
                logger.debug('wi1b4_summary_loop_skipped', sid=turn.session_id, error=str(exc))
        return PreparedTurnContext(turn=turn, messages=messages, bundle=bundle, assembler=assembler)

    async def route_intent(self, prepared: PreparedTurnContext, *, services: Mapping[str, Any]) -> RoutedTurnIntent:
        result = RoutedTurnIntent(prepared=prepared)
        pipeline = services.get('problem_pipeline')
        if pipeline is None or not getattr(pipeline, 'enabled', False) or prepared.turn.is_sentinel:
            return result
        try:
            pre_loop = await pipeline.run_pre_loop(prepared.turn.text, prior_task_type=getattr(prepared.bundle, 'task_type', None) if prepared.bundle is not None else None)
            result.pre_loop = pre_loop
            commands = [ProductDomainCommand('pipeline_event', {'type': event['type'], 'payload': {'session_id': prepared.turn.session_id, **event['payload']}}) for event in pre_loop.events]
            if pre_loop.short_circuit:
                result.commands = tuple(commands)
                return result
            if pre_loop.needs_clarification and pre_loop.intent:
                commands.append(ProductDomainCommand('clarification', {'session_id': prepared.turn.session_id, 'text': '\n'.join(pre_loop.intent.clarifying_questions)}))
                result.commands = tuple(commands)
                result.continue_turn = False
                return result
            for injection in pre_loop.system_injections:
                self._insert_after_system(prepared.messages, {'role': 'system', 'content': injection})
            result.problem_type = pre_loop.intent.problem_type if pre_loop.intent else None
            if pre_loop.contradiction is not None:
                result.attack_order = pre_loop.contradiction.attack_order
                result.contradiction_descs = {item.id: item.desc for item in pre_loop.contradiction.contradictions}
            result.commands = tuple(commands)
        except Exception as exc:
            logger.warning('pipeline_pre_loop_failed', sid=prepared.turn.session_id, error=str(exc))
        return result

    async def plan_decision(self, routed: RoutedTurnIntent, *, services: Mapping[str, Any], config: Any, provider: Any, code_mode: Any, in_code_mode: bool) -> PlannedTurnDecision:
        result = PlannedTurnDecision(routed=routed)
        if not routed.continue_turn:
            return result
        turn = routed.prepared.turn
        gate_on = bool(getattr(config.features, 'plan_confirm_gate', False)) and in_code_mode
        try:
            pipeline_config = getattr(getattr(config, 'features', None), 'problem_pipeline', None)
            plan = await maybe_extract_plan(provider, turn.text, str(code_mode.project_root(turn.session_id)) if in_code_mode and code_mode else None, in_code_mode=in_code_mode, companion_enabled=bool(pipeline_config and pipeline_config.plan_companion_enabled), problem_type=routed.problem_type, attack_order=routed.attack_order, contradiction_descs=routed.contradiction_descs)
            result.plan = plan
            if plan is None:
                return result
            if gate_on:
                preference = services.get('preference_memory')
                if preference is not None:
                    try:
                        result.auto_confirmed = await preference.match(turn.text, 'plan') is not None
                    except Exception as exc:
                        logger.debug('pref_match_failed', error=str(exc))
            steps = [{'title': step.title, 'detail': step.detail} for step in plan.steps]
            awaiting = gate_on and (not result.auto_confirmed)
            self._insert_after_system(routed.prepared.messages, {'role': 'system', 'content': plan_to_system_message(plan)})
            commands = [ProductDomainCommand('plan_proposed', {'session_id': turn.session_id, 'rationale': plan.rationale, 'steps': steps, 'awaiting_confirm': awaiting, 'auto_confirmed': result.auto_confirmed})]
            if awaiting:
                commands.append(ProductDomainCommand('plan_confirmation', {'session_id': turn.session_id, 'text': turn.text, 'timeout_seconds': 900.0, 'read_only': bool(getattr(config.features, 'plan_read_only', False)), 'in_code_mode': in_code_mode}))
            result.commands = tuple(commands)
        except Exception as exc:
            logger.debug('p4s25_plan_skipped', error=str(exc))
        return result

    @staticmethod
    def _insert_after_system(messages: list[dict[str, Any]], message: dict[str, Any]) -> None:
        index = 0
        while index < len(messages) and messages[index].get('role') == 'system':
            index += 1
        messages.insert(index, message)
__all__ = ['PlannedTurnDecision', 'PreparedTurnContext', 'ProductDomainCommand', 'ProductTurnPreparer', 'RoutedTurnIntent', 'TurnInput']
