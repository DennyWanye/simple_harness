
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Product-owned preparation stages used before either execution driver.

Companion preferences and owner-scoped selections enter only through the typed
``CompanionTurnAuthority``.  Historical callback composition was removed at
the Task 13 cutover so a caller cannot recreate a second preference writer.
"""
from __future__ import annotations
import inspect
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal
import structlog
from agent.nudge_queue import format_hints_for_injection
from agent.plan import maybe_extract_general_plan, plan_to_system_message
from deskpet.agent.attachment_budget import append_user_attachment_blocks
from deskpet.tools.capabilities import ToolExposureIntent
from deskpet.execution.provider_workloads import workload_context
from deskpet.execution.run_block_signals import (
    PreflightBlocked,
    RootBlockReasonV1,
)
logger = structlog.get_logger(__name__)
Venue = Literal['text', 'voice', 'background']

@dataclass(frozen=True, slots=True)
class TurnInput:
    """Complete product input; adapters are not allowed to drop fields."""
    text: str
    session_id: str
    request_id: str | None = None
    turn_id: str | None = None
    venue: Venue = 'text'
    memory_policy: Mapping[str, Any] | None = None
    explicit_new: bool = False
    attachment_blocks: tuple[Mapping[str, Any], ...] = ()
    provider_ref: str | None = None
    capability_ref: str | None = None
    workspace_ref: str | None = None
    root_run_id: str | None = None
    task_scope_id: str | None = None
    target_root_run_id: str | None = None
    active_execution_budget_seconds: float | None = None
    context_usage_basis_sample_id: str | None = None

    def __post_init__(self) -> None:
        if self.venue not in {"text", "voice", "background"}:
            raise ValueError(f"unsupported product venue: {self.venue}")
        if (
            self.active_execution_budget_seconds is not None
            and (
                not math.isfinite(self.active_execution_budget_seconds)
                or self.active_execution_budget_seconds <= 0
            )
        ):
            raise ValueError(
                "active execution budget must be positive and finite"
            )

    @property
    def is_sentinel(self) -> bool:
        return self.text.startswith('<<') and self.text.endswith('>>')

@dataclass(slots=True)
class PreparedTurnContext:
    turn: TurnInput
    messages: list[dict[str, Any]]
    bundle: Any | None
    assembler: Any | None
    prepared_context: Any | None = None
    eligibility: Any | None = None
    # Host-owned authority port. Task 1 exposes it to later preference
    # assembly without reading a feature flag or selecting a second writer.
    growth_preference_reader: Any | None = None
    preference_resolution: Any | None = None
    owner_memory_read_scope: Any | None = None
    growth_dependencies: tuple[Any, ...] = ()
    run_prepared_context: Any | None = None
    # Host-only phase-1 authority result. ProductVenue must finalize this
    # value with its one PreparedRunCatalogLease capture before Kernel start.
    companion_authority_state: Any | None = None
    companion_turn_finalizer: Any | None = None
    # The message/outbox identity is captured before model triage.  The model
    # later settles its semantic growth classification against this exact
    # durable message; no regex or Driver routing participates.
    current_message_id: int | None = None
    companion_ingress_owner: Any | None = None

@dataclass(slots=True)
class RoutedTurnIntent:
    prepared: PreparedTurnContext
    pre_loop: Any | None = None
    problem_type: str | None = None
    requires_action_plan: bool | None = None
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

PRODUCT_ROUTE_OWNED_TOOL_NAMES: tuple[str, ...] = (
    "deepresearch",
    "ppt_create",
    "ppt_pro",
)

def _needs_process_start_direct(text: str) -> bool:
    """Expose the long-lived launcher only for an explicit launch/control ask."""

    normalized = str(text or "").casefold()
    if re.search(r"\b(?:what|which)\b.+\bcan you\b", normalized):
        return False
    if re.search(
        r"(?:^|[\s`])(?:echo|dir|ls|pwd|cd|whoami|grep|findstr|type|cat|curl|where|which)(?:\s|$|`)",
        normalized,
    ) or re.search(
        r"\b(?:powershell(?:\.exe)?\s+.*-command|cmd(?:\.exe)?\s+/c)\b",
        normalized,
    ):
        return False
    chinese_targets = (
        "godot",
        "游戏",
        "应用",
        "软件",
        "程序",
        "编辑器",
        "浏览器",
        "窗口",
        "服务器",
    )
    english_target = re.search(
        r"\b(?:godot|gui|app|application|game|editor|browser|server|service|window)\b",
        normalized,
    )
    if not any(term in normalized for term in chinese_targets) and not english_target:
        return False

    clauses = re.split(
        r"[，。！？；,!?;]|\b(?:but|and then|then)\b|(?:但是|不过|然后|接着)",
        normalized,
    )
    description_only = any(
        marker in normalized
        for marker in (
            "只介绍",
            "只说明",
            "只告诉",
            "just describe",
            "only describe",
            "describe capabilities",
        )
    )
    chinese_action = r"(?:启动|打开|运行|操作|玩)"
    english_action = r"(?:launch|start|open|run|play)"
    for raw_clause in clauses:
        clause = raw_clause.strip()
        if not clause:
            continue
        if re.search(
            rf"(?:不要|别|禁止|无需|不需要)\s*(?:实际)?\s*{chinese_action}",
            clause,
        ) or re.search(
            rf"\b(?:do not|don't|never)\s+(?:actually\s+)?{english_action}\b",
            clause,
        ):
            continue
        if any(
            marker in clause
            for marker in (
                "只介绍",
                "只说明",
                "只告诉",
                "告诉我命令",
                "有哪些",
                "怎么",
                "如何",
                "为什么",
                "原因",
                "状态",
                "方法",
                "教程",
                "能力",
                "just describe",
                "only describe",
                "how do ",
                "how to ",
                "why ",
                "runtime status",
                "architecture",
                "open source",
                "capabilit",
                "tutorial",
            )
        ):
            continue
        if re.search(
            rf"^\s*(?:(?:请你|请|麻烦|帮我|现在|马上|立即|直接|再)\s*)*{chinese_action}",
            clause,
        ) or re.search(
            rf"(?:请你|请|麻烦|帮我|现在|马上|立即|直接|并)\s*{chinese_action}",
            clause,
        ):
            return True
        if re.search(
            rf"^\s*(?:(?:please|now)\s+|go ahead(?:\s+and)?\s+)*{english_action}\b",
            clause,
        ) or (
            not description_only
            and re.search(
            rf"\b(?:can|could|would)\s+you\s+(?:please\s+)?{english_action}\b",
            clause,
            )
        ) or re.search(
            rf"\b(?:please|go ahead and|i want you to)\s+{english_action}\b",
            clause,
        ):
            return True
    return False


class ProductTurnPreparer:
    """Assemble the one authoritative Context OS input for a product Run.

    ``route_intent`` and ``plan_decision`` remain as compatibility helpers for
    callers outside the production Text/Voice ingress.  The production venue
    uses ``prepare_direct_run`` so no context-poor model can answer, clarify,
    or plan before the main Agent sees the assembled Session context.
    """

    def __init__(
        self,
        profile_registry: Any | None = None,
        *,
        companion_turn_authority: Any | None = None,
    ) -> None:
        self._profile_registry = profile_registry
        self._companion_turn_authority = companion_turn_authority

    @staticmethod
    def _target_directory(turn: TurnInput, config: Any) -> str | None:
        if turn.workspace_ref:
            return turn.workspace_ref
        raw = getattr(config, "raw", None)
        companion = (
            raw.get("companion", {})
            if isinstance(raw, Mapping)
            else {}
        )
        if not bool(companion.get("write_scope_enforced", True)):
            return None
        from agent.write_scope import resolve_workspace_root

        return str(
            resolve_workspace_root(
                configured=str(companion.get("workspace_root", ""))
            )
        )

    @staticmethod
    def _has_active_skill_scope(prepared: PreparedTurnContext) -> bool:
        """Return whether semantic discovery already froze an active Skill.

        Intent triage only receives the raw user text.  Once the assembler and
        Companion authority have selected an exact invocation scope, that
        selection resolves the task shape and must not be replaced by an
        unrelated pre-run clarification or chitchat short circuit.
        """
        authority = prepared.companion_authority_state
        if authority is not None and tuple(
            getattr(authority, "active_skill_scope_ids", ()) or ()
        ):
            return True
        bundle = prepared.bundle
        decisions = getattr(bundle, "decisions", None)
        components = getattr(decisions, "components", None)
        if not isinstance(components, Mapping):
            return False
        trace = components.get("skill")
        if trace is None or not bool(getattr(trace, "included", False)):
            return False
        meta = getattr(trace, "meta", None)
        return bool(
            tuple(meta.get("active_skill_scope_ids", ()) or ())
            if isinstance(meta, Mapping)
            else ()
        )

    @staticmethod
    def _active_skill_required_tools(bundle: Any | None) -> tuple[str, ...]:
        """Read exact allowed tool names from assembler-frozen active scopes."""
        decisions = getattr(bundle, "decisions", None)
        components = getattr(decisions, "components", None)
        if not isinstance(components, Mapping):
            return ()
        trace = components.get("skill")
        if trace is None or not bool(getattr(trace, "included", False)):
            return ()
        meta = getattr(trace, "meta", None)
        if not isinstance(meta, Mapping):
            return ()
        scopes = meta.get("skill_invocation_scopes", ())
        active_ids = meta.get("active_skill_scope_ids", ())
        if (
            isinstance(scopes, (str, bytes))
            or not isinstance(scopes, (list, tuple))
            or isinstance(active_ids, (str, bytes))
            or not isinstance(active_ids, (list, tuple))
            or any(not isinstance(scope, Mapping) for scope in scopes)
        ):
            raise RuntimeError("assembled_skill_scope_shape_invalid")
        active = {str(item) for item in active_ids}
        names: list[str] = []
        for scope in scopes:
            if str(scope.get("scope_id") or "") not in active:
                continue
            allowed = scope.get("allowed_tools", ())
            if (
                isinstance(allowed, (str, bytes))
                or not isinstance(allowed, (list, tuple))
            ):
                raise RuntimeError("assembled_skill_allowed_tools_invalid")
            names.extend(str(item) for item in allowed if str(item))
        return tuple(dict.fromkeys(names))

    @staticmethod
    def prepare_workflow_request_payload(
        routed: Any, turn: TurnInput, config: Any,
    ) -> dict[str, Any]:
        """Keep product loop/research policy outside the generic Harness."""
        problem = getattr(getattr(config, 'features', None), 'problem_pipeline', None)
        intent = getattr(routed.pre_loop, 'intent', None)
        response_quality = None
        preference_resolution = getattr(
            getattr(routed, "prepared", None),
            "preference_resolution",
            None,
        )
        for item in getattr(preference_resolution, "items", ()) or ():
            if (
                getattr(item, "preference_key", None) == "response.detail"
                and getattr(item, "value", None) == "brief"
            ):
                response_quality = {
                    "schema_version": 1,
                    "mode": "semantic_completeness",
                    "preference_key": "response.detail",
                    "preference_value": "brief",
                    "snapshot_hash": str(
                        getattr(preference_resolution, "snapshot_hash", "")
                        or ""
                    ),
                }
                break
        loop = {
            'user_request': None if turn.is_sentinel else turn.text,
            'is_sentinel_run': turn.is_sentinel,
            'pipeline_problem_type': routed.problem_type,
            'pipeline_needs_investigation': bool(getattr(intent, 'needs_investigation', False)),
            'pipeline_observability': bool(getattr(problem, 'observability_events', False)),
            'convergence_report_on_stop': bool(
                routed.pre_loop and not routed.pre_loop.short_circuit
                and getattr(problem, 'convergence_report_on_stop', False)
            ),
            'response_quality': response_quality,
        }
        raw = getattr(config, 'raw', None)
        research = raw.get('research', {}) if isinstance(raw, dict) else {}
        return {
            'provider_ref': turn.provider_ref, 'capability_ref': turn.capability_ref,
            'workspace_ref': turn.workspace_ref, 'loop': loop,
            'context_usage_basis_sample_id': (
                turn.context_usage_basis_sample_id
            ),
            'research_config': {
                key: bool(research[key])
                for key in ('direct_sources', 'source_packs') if key in research
            } if isinstance(research, dict) else {},
        }

    async def prepare_context(self, turn: TurnInput, *, services: Mapping[str, Any], config: Any, local_llm: Any, tool_registry: Any, current_message_id: int | None, summary_user_is_confused: Callable[[str], bool], summary_latest_task_snapshot: Callable[[Sequence[Any]], str | None], summary_build_reinject_msg: Callable[[str], dict[str, Any]], provider: Any = None, companion_ingress_owner: Any = None) -> PreparedTurnContext:
        projection_gate = services.get(
            "session_terminal_projection_gate"
        )
        if projection_gate is not None:
            # Product context must never be assembled from a view known to be
            # stale.  Projection failures propagate and stop the turn.
            await projection_gate.ensure_current(turn.session_id)
        bundle = None
        assembler = services.get('context_assembler')
        if assembler is not None and getattr(assembler, 'enabled', True):
            try:
                # ``provider`` is the already resolved and Session-bound
                # provider for this exact product turn. ``local_llm`` is only
                # a global fallback and can name a different default model.
                persona_provider = provider or local_llm
                persona_model = getattr(persona_provider, 'model', 'unknown')
                persona_base = getattr(persona_provider, 'base_url', '')
                skill_config: dict[str, Any] = {}
                try:
                    disclosure = config.skills.auto_disclosure
                    skill_config = {'auto_disclosure': {'enabled': bool(disclosure.enabled), 'strong_threshold': float(disclosure.strong_threshold), 'budget_tokens': int(disclosure.budget_tokens), 'per_skill_max_tokens': int(disclosure.per_skill_max_tokens)}}
                except Exception:
                    pass
                workspace_config = (
                    {
                        'verified': True,
                        'root': turn.workspace_ref,
                        'active_path': turn.workspace_ref,
                    }
                    if turn.workspace_ref
                    else {}
                )
                provider_workload_context = None
                if turn.request_id and turn.root_run_id:
                    provider_workload_context = workload_context(
                        "memory.query_rewrite",
                        request_id=str(turn.request_id),
                        session_id=turn.session_id,
                        root_run_id=str(turn.root_run_id),
                        task_scope_id=turn.task_scope_id,
                    )
                bundle = await assembler.assemble(
                    user_message=turn.text,
                    memory_manager=services.get('memory_manager'),
                    tool_registry=tool_registry,
                    skill_registry=(
                        services.get('managed_skill_discovery_projection')
                        or services.get('skill_loader')
                    ),
                    mcp_manager=services.get('mcp_manager'),
                    session_id=turn.session_id,
                    current_message_id=current_message_id,
                    # The product root is always the same general agent.  Legacy
                    # classifier labels may rank retrieval candidates elsewhere,
                    # but they cannot select a persona, policy or tool surface.
                    task_type_override='chat',
                    memory_policy_override=turn.memory_policy,
                    config={
                        'llm': {'model': persona_model, 'base_url': persona_base},
                        'workspace_context': workspace_config,
                        'task_conversation': {
                            'root_run_id': turn.root_run_id,
                            'task_scope_id': turn.task_scope_id,
                        },
                        'provider_workload_context': provider_workload_context,
                        'skills': skill_config,
                        'features': {
                            'context_os_v1': bool(
                                getattr(
                                    getattr(config, 'features', None),
                                    'context_os_v1',
                                    False,
                                )
                            )
                        },
                    },
                )
                if bundle is not None and bundle.decisions is not None:
                    bundle.decisions.timestamp = time.time()
                    bundle.decisions.session_id = turn.session_id
                if bundle is not None:
                    intent = bundle.tool_exposure_intent or ToolExposureIntent()
                    core_names = tuple(
                        name
                        for name in (
                            "capability_search",
                            "workspace_prepare",
                            "external_action_wait",
                            "project_directory_select",
                        )
                        if tool_registry.has(name)
                    )
                    if (
                        _needs_process_start_direct(turn.text)
                        and tool_registry.has("process_start")
                    ):
                        # A GUI/server launch must return a durable process
                        # lease immediately.  Do not expose this dangerous
                        # launcher to unrelated artifact turns: the model can
                        # otherwise misuse it for short shell/path queries.
                        core_names = (*core_names, "process_start")
                    active_skill_names = self._active_skill_required_tools(bundle)
                    bundle.tool_exposure_intent = replace(
                        intent,
                        # Durable product workflows are no longer model-spawnable
                        # (the spawn tool was removed on 2026-09-09); their
                        # legacy/direct tools stay hidden pending followup F-WF-1.
                        # Keeping their legacy/direct tools discoverable creates
                        # a split-brain surface: the model can bypass the
                        # WorkflowDriver and call a compatibility handler whose
                        # process-local starter is intentionally absent.
                        deny_selectors=tuple(
                            dict.fromkeys(
                                (
                                    *intent.deny_selectors,
                                    *PRODUCT_ROUTE_OWNED_TOOL_NAMES,
                                )
                            )
                        ),
                        required_direct_names=tuple(
                            dict.fromkeys(
                                (
                                    *intent.required_direct_names,
                                    *core_names,
                                    *active_skill_names,
                                )
                            )
                        ),
                    )
            except Exception as exc:
                logger.warning('p4_assembler_failed', error=str(exc), error_type=type(exc).__name__)
                bundle = None
        if bundle is not None:
            messages = bundle.build_messages(user_message=turn.text, history=bundle.history, late_system_nudge=bundle.late_system_nudge)
        else:
            messages = [{'role': 'user', 'content': turn.text}]
        if turn.attachment_blocks:
            messages = append_user_attachment_blocks(messages, turn.attachment_blocks)
        prepared_context = None
        eligibility = None
        if bool(getattr(getattr(config, 'features', None), 'context_os_v1', False)):
            if bundle is None:
                raise PreflightBlocked(
                    RootBlockReasonV1.CAPABILITY_UNAVAILABLE,
                    ('capability-context:bundle-unavailable',),
                )
            planner = services.get('context_request_planner')
            scope_store = services.get('tool_capability_scope_store')
            if planner is None or scope_store is None:
                raise PreflightBlocked(
                    RootBlockReasonV1.CAPABILITY_UNAVAILABLE,
                    ('capability-context:tool-runtime-unavailable',),
                )
            from deskpet.agent.attachment_budget import collect_attachment_budget
            from deskpet.tools.capabilities import ToolEligibilityContext
            from llm.model_info import resolve as resolve_model_info
            eligibility = ToolEligibilityContext(
                session_id=turn.session_id,
                request_id=str(turn.request_id or ''),
                task_type='chat',
                mode='general',
            )
            if not eligibility.request_id:
                raise PreflightBlocked(
                    RootBlockReasonV1.CAPABILITY_UNAVAILABLE,
                    ('capability-context:request-identity-unavailable',),
                )
            selected_provider = provider or local_llm
            model_info = resolve_model_info(
                str(getattr(selected_provider, 'model', '') or turn.provider_ref or '_default')
            )
            active_snapshot = None
            project_snapshot = services.get('project_initial_context_snapshot')
            if callable(project_snapshot):
                active_snapshot = await project_snapshot(
                    session_id=turn.session_id,
                    request_id=eligibility.request_id,
                    task_scope_id=str(turn.task_scope_id or ""),
                    user_text=turn.text,
                    explicit_new=turn.explicit_new,
                )
            attach_snapshot = services.get('attach_task_snapshot_to_request')
            if callable(attach_snapshot):
                attach_snapshot(bundle, messages, active_snapshot)
            attachment_refs, attachment_tokens = collect_attachment_budget(messages)
            planned_request = await planner.prepare_initial(
                bundle,
                base_system='',
                history=bundle.history,
                user_message=turn.text,
                eligibility=eligibility,
                context_window=model_info.context_window,
                effective_pct=model_info.effective_pct,
                generation_reserve=min(8192, max(512, int(model_info.context_window) // 8)),
                active_snapshot=active_snapshot,
                prebuilt_messages=messages,
                current_message_id=current_message_id,
                attachment_refs=attachment_refs,
                attachment_tokens=attachment_tokens,
            )
            prepared_context = planned_request.prepared_context
            prepared_context.request_budget = planned_request.budget
            scope_store.open(
                prepared_context.tool_set,
                eligibility,
                snapshot_handle=prepared_context.active_snapshot_handle,
            )
            messages = prepared_context.messages
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
        preference_resolution = None
        owner_memory_read_scope = None
        growth_dependencies: tuple[Any, ...] = ()
        run_prepared_context = None
        companion_authority_state = None
        if self._companion_turn_authority is not None:
            from deskpet.companion.turn_authority import (
                CompanionTurnPreparationRequestV1,
                PreparedCompanionTurnV1,
            )

            auto_scopes: list[dict[str, Any]] = []
            auto_scope_ids: list[str] = []
            if bundle is not None and bundle.decisions is not None:
                skill_trace = bundle.decisions.components.get("skill")
                skill_meta = (
                    {}
                    if skill_trace is None or not skill_trace.included
                    else dict(skill_trace.meta)
                )
                raw_auto_scopes = skill_meta.get(
                    "skill_invocation_scopes", ()
                )
                raw_auto_ids = skill_meta.get(
                    "active_skill_scope_ids", ()
                )
                if any(
                    isinstance(value, (str, bytes))
                    or not isinstance(value, (list, tuple))
                    for value in (raw_auto_scopes, raw_auto_ids)
                ) or any(
                    not isinstance(item, Mapping)
                    for item in raw_auto_scopes
                ):
                    raise RuntimeError(
                        "assembled_skill_scope_shape_invalid"
                    )
                auto_scopes = [dict(item) for item in raw_auto_scopes]
                required_scope_fields = {
                    "owner_key",
                    "pack_id",
                    "skill_id",
                    "version",
                    "manifest_hash",
                    "content_hash",
                    "allowed_tools",
                    "scope_id",
                    "scope_hash",
                }
                if any(
                    not required_scope_fields.issubset(scope)
                    for scope in auto_scopes
                ):
                    raise RuntimeError(
                        "assembled_skill_scope_identity_incomplete"
                    )
                auto_scope_ids = [str(item) for item in raw_auto_ids]
                known_auto_ids = {
                    str(item.get("scope_id") or "")
                    for item in auto_scopes
                }
                if "" in known_auto_ids or not set(
                    auto_scope_ids
                ).issubset(known_auto_ids):
                    raise RuntimeError(
                        "assembled_active_skill_scope_invalid"
                    )
            active_ids = set(auto_scope_ids)
            auto_instruction_refs = [
                {
                    key: scope[key]
                    for key in (
                        "owner_key",
                        "pack_id",
                        "skill_id",
                        "version",
                        "manifest_hash",
                        "content_hash",
                        "scope_id",
                        "scope_hash",
                    )
                    if key in scope
                }
                for scope in auto_scopes
                if str(scope.get("scope_id") or "") in active_ids
            ]
            authority_result = (
                self._companion_turn_authority.prepare_turn(
                    CompanionTurnPreparationRequestV1(
                        turn=turn,
                        services=services,
                        current_message_id=current_message_id,
                        ingress_owner=companion_ingress_owner,
                        selected_instruction_refs=tuple(
                            auto_instruction_refs
                        ),
                        skill_invocation_scopes=tuple(auto_scopes),
                        active_skill_scope_ids=tuple(auto_scope_ids),
                    )
                )
            )
            if inspect.isawaitable(authority_result):
                authority_result = await authority_result
            if not isinstance(
                authority_result, PreparedCompanionTurnV1
            ):
                raise RuntimeError(
                    "companion_turn_authority_prepare_invalid"
                )
            companion_authority_state = authority_result
            preference_resolution = (
                authority_result.preference_resolution
            )
            growth_dependencies = (
                *preference_resolution.dependencies,
                *authority_result.base_dependencies,
            )
            owner_memory_read_scope = (
                authority_result.owner_memory_read_scopes[0]
                if authority_result.owner_memory_read_scopes
                else None
            )
            if authority_result.preference_prompt:
                self._insert_after_system(
                    messages,
                    {
                        "role": "system",
                        "content": authority_result.preference_prompt,
                        "_is_companion_preference_snapshot": True,
                    },
                )
            if authority_result.route_prompt:
                self._insert_after_system(
                    messages,
                    {
                        "role": "system",
                        "content": authority_result.route_prompt,
                        "_is_companion_workflow_selection_hint": True,
                    },
                )
        return PreparedTurnContext(
            turn=turn,
            messages=messages,
            bundle=bundle,
            assembler=assembler,
            prepared_context=prepared_context,
            eligibility=eligibility,
            growth_preference_reader=services.get("growth_authority_router"),
            preference_resolution=preference_resolution,
            owner_memory_read_scope=owner_memory_read_scope,
            growth_dependencies=growth_dependencies,
            run_prepared_context=run_prepared_context,
            companion_authority_state=companion_authority_state,
            companion_turn_finalizer=(
                self._companion_turn_authority
                if companion_authority_state is not None
                else None
            ),
            current_message_id=current_message_id,
            companion_ingress_owner=companion_ingress_owner,
        )

    async def prepare_direct_run(
        self,
        prepared: PreparedTurnContext,
        *,
        services: Mapping[str, Any],
    ) -> RoutedTurnIntent:
        """Create the compatibility request view without running IntentTriage."""

        await self._settle_growth_intent(
            prepared,
            services=services,
            growth_signal_kind="none",
        )
        return RoutedTurnIntent(prepared=prepared)

    async def route_intent(self, prepared: PreparedTurnContext, *, services: Mapping[str, Any]) -> RoutedTurnIntent:
        result = RoutedTurnIntent(prepared=prepared)
        pipeline = services.get('problem_pipeline')
        if pipeline is None or not getattr(pipeline, 'enabled', False) or prepared.turn.is_sentinel:
            await self._settle_growth_intent(
                prepared,
                services=services,
                growth_signal_kind="none",
            )
            return result
        growth_signal_kind = "none"
        try:
            turn = prepared.turn
            if not turn.request_id or not turn.root_run_id:
                raise RuntimeError(
                    "problem pre-analysis requires frozen request/root identity"
                )
            pre_loop = await pipeline.run_pre_loop(
                turn.text,
                prior_task_type=(
                    getattr(prepared.bundle, 'task_type', None)
                    if prepared.bundle is not None else None
                ),
                workload_context=workload_context(
                    "agent.problem_preanalysis",
                    request_id=str(turn.request_id),
                    session_id=turn.session_id,
                    root_run_id=str(turn.root_run_id),
                    task_scope_id=turn.task_scope_id,
                ),
            )
            result.pre_loop = pre_loop
            commands = [ProductDomainCommand('pipeline_event', {'type': event['type'], 'payload': {'session_id': prepared.turn.session_id, **event['payload']}}) for event in pre_loop.events]
            result.problem_type = (
                getattr(pre_loop.intent, "problem_type", None)
                if pre_loop.intent
                else None
            )
            result.requires_action_plan = (
                getattr(pre_loop.intent, "requires_action_plan", None)
                if pre_loop.intent
                else None
            )
            growth_signal_kind = (
                getattr(pre_loop.intent, "growth_signal_kind", "none")
                if pre_loop.intent
                else "none"
            )
            active_skill_scope = self._has_active_skill_scope(prepared)
            if pre_loop.short_circuit and not active_skill_scope:
                result.commands = tuple(commands)
                return result
            if (
                pre_loop.needs_clarification
                and pre_loop.intent
                and not active_skill_scope
            ):
                commands.append(ProductDomainCommand('clarification', {'session_id': prepared.turn.session_id, 'text': '\n'.join(pre_loop.intent.clarifying_questions)}))
                result.commands = tuple(commands)
                result.continue_turn = False
                return result
            if active_skill_scope and (
                pre_loop.short_circuit or pre_loop.needs_clarification
            ):
                logger.info(
                    "intent_triage_deferred_to_active_skill",
                    sid=prepared.turn.session_id,
                    short_circuit=bool(pre_loop.short_circuit),
                    needs_clarification=bool(pre_loop.needs_clarification),
                )
            for injection in pre_loop.system_injections:
                self._insert_after_system(prepared.messages, {'role': 'system', 'content': injection})
            if pre_loop.contradiction is not None:
                result.attack_order = pre_loop.contradiction.attack_order
                result.contradiction_descs = {item.id: item.desc for item in pre_loop.contradiction.contradictions}
            result.commands = tuple(commands)
        except Exception as exc:
            logger.warning('pipeline_pre_loop_failed', sid=prepared.turn.session_id, error=str(exc))
        finally:
            await self._settle_growth_intent(
                prepared,
                services=services,
                growth_signal_kind=growth_signal_kind,
            )
        return result

    @staticmethod
    async def _settle_growth_intent(
        prepared: PreparedTurnContext,
        *,
        services: Mapping[str, Any],
        growth_signal_kind: str,
    ) -> None:
        dispatcher = services.get("companion_ingress_dispatcher")
        if (
            dispatcher is None
            or prepared.current_message_id is None
            or prepared.companion_ingress_owner is None
        ):
            return
        try:
            await dispatcher.settle_semantic_intent(
                session_id=prepared.turn.session_id,
                message_id=prepared.current_message_id,
                request_id=prepared.turn.request_id,
                turn_id=prepared.turn.turn_id,
                owner=prepared.companion_ingress_owner,
                growth_signal_kind=growth_signal_kind,
            )
        except Exception as exc:
            logger.warning(
                "companion_growth_intent_settle_failed",
                sid=prepared.turn.session_id,
                message_id=prepared.current_message_id,
                growth_signal_kind=growth_signal_kind,
                error=str(exc),
            )

    async def plan_decision(
        self,
        routed: RoutedTurnIntent,
        *,
        services: Mapping[str, Any],
        config: Any,
        provider: Any,
    ) -> PlannedTurnDecision:
        result = PlannedTurnDecision(routed=routed)
        if not routed.continue_turn:
            return result
        turn = routed.prepared.turn
        target_directory = self._target_directory(turn, config)
        policy_store = services.get("capability_store")
        policy_mode = "manual"
        if policy_store is not None:
            try:
                policy_mode = str((await policy_store.get_policy_state()).mode)
            except Exception as exc:
                logger.warning(
                    "authorization_policy_read_failed",
                    sid=turn.session_id,
                    error=str(exc),
                )
        try:
            pipeline_config = getattr(getattr(config, 'features', None), 'problem_pipeline', None)
            authorization_required = (
                policy_mode != "auto"
                # Unknown fails closed to a model-generated plan in manual
                # mode. It never falls back to regex/classifier semantics.
                and routed.requires_action_plan is not False
            )
            plan = await maybe_extract_general_plan(
                provider,
                turn.text,
                target_directory,
                planning_enabled=bool(
                    pipeline_config and pipeline_config.plan_companion_enabled
                ) or authorization_required,
                authorization_required=authorization_required,
                problem_type=routed.problem_type,
                attack_order=routed.attack_order,
                contradiction_descs=routed.contradiction_descs,
            )
            result.plan = plan
            if plan is None:
                return result
            action_categories = tuple(
                sorted(
                    {
                        str(item)
                        for step in plan.steps
                        for item in getattr(step, "action_categories", ())
                    }
                )
            )
            gate_on = policy_mode != "auto" and any(
                item != "filesystem_read" for item in action_categories
            )
            # Only the explicit Auto policy may skip this approval.  A stored
            # preference is not a substitute for the current task's scoped
            # TaskGrant.
            result.auto_confirmed = policy_mode == "auto"
            steps = [{'title': step.title, 'detail': step.detail} for step in plan.steps]
            awaiting = gate_on and (not result.auto_confirmed)
            self._insert_after_system(routed.prepared.messages, {'role': 'system', 'content': plan_to_system_message(plan)})
            presentation = {
                'session_id': turn.session_id,
                'rationale': plan.rationale,
                'steps': steps,
                'target_directory': target_directory,
                'action_categories': list(action_categories),
                'awaiting_confirm': awaiting,
                'auto_confirmed': result.auto_confirmed,
            }
            commands = [ProductDomainCommand('plan_proposed', presentation)]
            if awaiting:
                commands.append(ProductDomainCommand('plan_confirmation', {
                    'session_id': turn.session_id,
                    'text': turn.text,
                    'target_directory': target_directory,
                    'action_categories': list(action_categories),
                    'timeout_seconds': 900.0,
                    'read_only': bool(getattr(config.features, 'plan_read_only', False)),
                }))
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
