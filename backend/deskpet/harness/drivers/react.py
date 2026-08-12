"""ReAct driver with one host-created AgentLoop collaborator per Run."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import time
import unicodedata
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace
from typing import Any, AsyncIterator, Mapping

from deskpet.capabilities.brokered_planner import BrokeredEffectPlanner
from deskpet.capabilities.effect_plan import (
    BrokeredCommandBoundary,
    EffectPlanValidationError,
)
from deskpet.capabilities.failure_receipts import (
    parse_capability_registry_source,
)
from deskpet.capabilities.store import CapabilityStoreError
from deskpet.capabilities.tool_proxy import BrokeredPlanEnvelope
from deskpet.companion.skills import instruction_content_hash
from deskpet.types.task_work_context import (
    ConversationBoundary,
    TaskRunProjection,
    TaskWorkContext,
)
from deskpet.execution.contracts import (
    ActorContext,
    AdmissionLaunchClaim,
    AttemptFailureSet as DurableAttemptFailureSet,
    AttemptRecord as DurableAttemptRecord,
    DecisionOpen as DurableDecisionOpen,
    DecisionSignal as DurableDecisionSignal,
    ExternalWaitKind,
    OutcomeStatus,
    PersistenceLevel,
    PlanVersionRecord as DurablePlanVersionRecord,
    ProviderActionAdmissionState,
    ProviderActionBatch as DurableProviderActionBatch,
    ProviderActionCall as DurableProviderActionCall,
    ProviderTurnFence,
    RecoveryLease,
    RunEventCandidate,
    TaskExternalWait,
    TaskFailureReport as DurableTaskFailureReport,
    TaskGoalRecord as DurableTaskGoalRecord,
    fingerprint_json,
    stable_decision_grant_id,
    thaw_json,
)
from deskpet.execution.evidence import EvidenceContext, EvidenceSelection, UNKNOWN_EVIDENCE
from deskpet.execution.failure_reports import FailureReportIssuer, TaskFailureReport
from deskpet.harness.attempts import AttemptFailureSet, AttemptLoopGuard, AttemptRecord
from deskpet.execution.uow_ports import ReActUnitOfWork
from deskpet.permissions.runtime import (
    PreparedAuthorizationPlan,
    PreparedAuthorizationRuntime,
)
from deskpet.harness.live_index import BoundedLiveIndex
from deskpet.harness.ports import CancelAcknowledgedCandidate, ChildAcceptedCandidate, DecisionSignal, DriverEvent, DriverSignal, DriverStart, DriverTerminalCandidate, ExecuteTools, JoinPolicy, OpenDecision, PersistedEventCandidate, ProviderFallbackCandidate, TokenCandidate, ToolGrantRef
from deskpet.workflows.effects import (
    NormalizedToolOutcome,
    PreparedToolCall,
    ToolOutcomeState,
)
from deskpet.tools.capabilities import (
    PreparedToolCapability,
    ToolExecutionContext,
    canonical_deferred_capability_id,
)
from deskpet.tools.orchestration_controls import (
    EXTERNAL_ACTION_WAIT,
    PROJECT_DIRECTORY_SELECT,
)

logger = logging.getLogger(__name__)

from .react_boundary import (
    ReactCommandBoundary,
    _call_payload,
    _context_payload,
    _load_call,
    _load_context,
    _skill_tool_intersection,
)
from .react_artifact_completion import (
    remember_artifact_completion,
    replay_artifact_completions,
)
from .react_loop import (
    AgentLoopCollaborator,
    AgentLoopToolInterceptionError,
    ReactControlBatch,
    ReactEmission,
    ReactFailure,
    ReactFallback,
    ReactFinal,
    ReactToken,
    ReactToolBatch,
)


class ReActDriver:

    _DELEGATE_OBJECTIVE_HISTORY_LIMIT = 8
    _DELEGATE_CONVERGENCE_REJECTION_LIMIT = 2
    _DELEGATE_OBJECTIVE_STOPWORDS = frozenset(
        {
            "a",
            "an",
            "and",
            "child",
            "delegate",
            "durable",
            "please",
            "task",
            "the",
            "to",
            "use",
            "workflow",
        }
    )

    def __init__(
        self,
        collaborator: Any,
        uow: ReActUnitOfWork,
        tool_registry: Any,
        *,
        auto_mode_check: Any | None = None,
        authorization_runtime: PreparedAuthorizationRuntime | None = None,
        capability_refresh_staging: Any | None = None,
        capability_refresh_service: Any | None = None,
        capability_scope_store: Any | None = None,
        brokered_planner: BrokeredEffectPlanner | None = None,
        capability_builder_host: Any | None = None,
    ) -> None:
        self._collaborator = collaborator
        self._uow = uow
        self._tool_registry = tool_registry
        self._auto_mode_check = auto_mode_check
        self._authorization_runtime = authorization_runtime
        self._capability_refresh_staging = capability_refresh_staging
        self._capability_refresh_service = capability_refresh_service
        self._capability_scope_store = capability_scope_store
        self._brokered_planner = brokered_planner
        self._capability_builder_host = capability_builder_host
        self._live: BoundedLiveIndex | None = None

    def _auto_mode_enabled(self) -> bool:
        check = self._auto_mode_check
        if not callable(check):
            return False
        try:
            return bool(check())
        except Exception:  # noqa: BLE001 - policy lookup fails closed
            return False

    @staticmethod
    def _decision_actor(boundary: ReactCommandBoundary) -> ActorContext:
        context = boundary.run_context
        return ActorContext(
            principal_id=(
                context.principal_id
                if context is not None and context.principal_id
                else f"local:{boundary.session_id}"
            ),
            session_id=boundary.session_id,
            auth_epoch=0 if context is None else context.auth_epoch,
            root_run_id=(
                boundary.run_id if context is None else context.root_run_id
            ),
            capability_hash=(
                None if context is None else context.capability_hash
            ),
        )

    def _auto_allow_permission(
        self,
        boundary: ReactCommandBoundary,
        decision: DriverEvent,
    ) -> AsyncIterator[DriverEvent]:
        async def iterator() -> AsyncIterator[DriverEvent]:
            if decision.kind != "open_decision" or decision.decision_kind != "permission":
                yield decision
                return
            authorization_plan: PreparedAuthorizationPlan | None = None
            if self._authorization_runtime is not None:
                authorization_plan = await self._plan_permission(
                    boundary,
                    decision,
                    confirmed=False,
                )
                if not authorization_plan.resolves_without_ui:
                    yield decision
                    return
                grant = authorization_plan.committed_task_grant
                assert grant is not None
                response = {
                    "allow": True,
                    "source": grant.source,
                    "task_grant_id": grant.task_grant_id,
                    "policy_generation": authorization_plan.policy_state.generation,
                }
            else:
                response = {"allow": True, "source": "auto-mode"}
            durable = DurableDecisionSignal(
                decision_id=decision.decision_id,
                run_id=decision.run_id,
                expected_session_id=boundary.session_id,
                nonce=decision.nonce,
                expected_version=0,
                allow=True,
                response_schema_version=1,
                response=response,
                domain_kind=decision.domain_kind,
                domain_id=decision.domain_id,
                call_id=decision.call_id,
                effect_id=decision.effect_id,
                tool_name=decision.tool_name,
                args_hash=decision.args_hash,
                capability_hash=decision.capability_hash,
                scope_hash=decision.scope_hash,
            )
            signal = DecisionSignal(
                decision.run_id,
                decision.decision_id,
                response,
                nonce=decision.nonce,
                version=0,
            )
            async for candidate in self.signal_decision_atomically(
                signal,
                durable,
                self._decision_actor(boundary),
                authorization_plan=authorization_plan,
            ):
                if (
                    candidate.kind == "open_decision"
                    and candidate.decision_kind == "permission"
                    and (
                        self._authorization_runtime is not None
                        or self._auto_mode_enabled()
                    )
                ):
                    current = await self._load_boundary(boundary.run_id)
                    async for nested in self._auto_allow_permission(
                        current, candidate
                    ):
                        yield nested
                    return
                yield candidate

        return iterator()

    async def _plan_permission(
        self,
        boundary: ReactCommandBoundary,
        decision: DriverEvent,
        *,
        confirmed: bool,
    ) -> PreparedAuthorizationPlan:
        runtime = self._authorization_runtime
        if runtime is None:
            raise RuntimeError("prepared authorization runtime is not configured")
        indexes = {
            call.stable_call_id: index
            for index, call in enumerate(boundary.pending_calls)
        }
        if decision.call_id in indexes:
            index = indexes[str(decision.call_id)]
            call = boundary.pending_calls[index]
            context = boundary.tool_contexts[index]
        else:
            brokered = next(
                (
                    inner
                    for command in boundary.active_brokered_commands
                    if (inner := self._brokered_inner(command)) is not None
                    and inner[0].stable_call_id == decision.call_id
                ),
                None,
            )
            if brokered is None:
                raise ValueError("permission decision call is not pending")
            call, context = brokered
        actor = self._decision_actor(boundary)
        task_grant_id = boundary.completion_state.get("task_grant_id")
        explicit_only = call.tool_name in boundary.confirm_only_names
        return await runtime.plan_prepared_call(
            call=call,
            context=context,
            permission_category=self._permission_category(call),
            task_grant_id=(
                str(task_grant_id) if task_grant_id is not None else None
            ),
            principal_id=actor.principal_id,
            confirmed=confirmed,
            decision_expires_at=decision.expires_at,
            explicit_only=explicit_only,
            decision_id=decision.decision_id if explicit_only else None,
            decision_nonce=decision.nonce if explicit_only else None,
            confirm_only_snapshot_ref=(
                boundary.confirm_only_snapshot_ref if explicit_only else None
            ),
            confirm_only_snapshot_hash=(
                boundary.confirm_only_snapshot_hash if explicit_only else None
            ),
        )

    def _apply_permission_policy(
        self,
        boundary: ReactCommandBoundary,
        decision: DriverEvent,
    ) -> AsyncIterator[DriverEvent]:
        if self._authorization_runtime is not None or self._auto_mode_enabled():
            return self._auto_allow_permission(boundary, decision)

        async def iterator() -> AsyncIterator[DriverEvent]:
            yield decision

        return iterator()

    def bind_live_index(self, live: BoundedLiveIndex) -> None:
        if self._live is not None and self._live is not live:
            raise RuntimeError('ReAct driver is already bound to another live index')
        self._live = live
        bind_live_index = getattr(self._collaborator, 'bind_live_index', None)
        if callable(bind_live_index):
            bind_live_index(live)

    def _bound_live(self) -> BoundedLiveIndex:
        if self._live is None:
            raise RuntimeError('ReAct driver requires the Kernel live index')
        return self._live

    def _live_boundary(self, run_id: str) -> ReactCommandBoundary | None:
        active = self._bound_live().get(run_id)
        state = None if active is None else active.driver_state
        return state if isinstance(state, ReactCommandBoundary) else None

    @staticmethod
    def _durable_decision(decision: DriverEvent | None) -> DurableDecisionOpen | None:
        if decision is None:
            return None
        return DurableDecisionOpen(decision_id=decision.decision_id, run_id=decision.run_id, nonce=decision.nonce, kind=decision.decision_kind, prompt_schema_version=decision.prompt_schema_version, prompt=thaw_json(decision.prompt), expires_at=decision.expires_at, domain_kind=decision.domain_kind, domain_id=decision.domain_id, call_id=decision.call_id, effect_id=decision.effect_id, tool_name=decision.tool_name, args_hash=decision.args_hash, capability_hash=decision.capability_hash, scope_hash=decision.scope_hash)

    async def _save_durable_boundary(self, boundary: ReactCommandBoundary, *, decision: DriverEvent | None=None, recovery_lease: RecoveryLease | None=None, admission_launch: AdmissionLaunchClaim | None=None) -> ReactCommandBoundary:
        expected = max(0, boundary.version - 1)
        saved = await self._uow.persist_react_boundary(boundary.run_id, expected, boundary.to_payload(), self._durable_decision(decision), recovery_lease=recovery_lease, admission_launch=admission_launch)
        return replace(boundary, version=int(saved.version))

    async def _persist_boundary(self, request: DriverStart, boundary: ReactCommandBoundary, *, continuation_version: int=0, decision: DriverEvent | None=None, recovery_lease: RecoveryLease | None=None, admission_launch: AdmissionLaunchClaim | None=None) -> ReactCommandBoundary:
        volatile = self._live_boundary(request.run_id)
        if continuation_version and volatile is None:
            return await self._save_durable_boundary(replace(boundary, version=continuation_version + 1), decision=decision, recovery_lease=recovery_lease, admission_launch=admission_launch)
        spec = request.run_spec
        if spec is None:
            raise RuntimeError('durable ReAct boundary requires its immutable RunCreate')
        waiting = RunEventCandidate(event_key=f'boundary:{boundary.command_id}', kind='run.waiting', status=OutcomeStatus.WAITING, driver_kind=spec.driver_kind, correlation={'command_id': boundary.command_id})
        _, saved = await self._uow.persist_react_boundary(replace(spec, persistence_level=PersistenceLevel.DURABLE), expected_run_version=0, expected_continuation_version=0, payload=boundary.to_payload(), decision=self._durable_decision(decision), waiting_event=waiting)
        if volatile is not None:
            active = self._bound_live().get(request.run_id)
            if active is not None and active.driver_state is volatile:
                active.driver_state = None
        return replace(boundary, version=int(saved.version))

    def _permission_category(self, call: PreparedToolCall) -> str:
        spec = self._tool_registry.resolve_prepared_spec(call)
        return str(spec.permission_category)

    def _permission_decision(
        self, boundary: ReactCommandBoundary, index: int
    ) -> DriverEvent:
        call = boundary.pending_calls[index]
        context = boundary.tool_contexts[index]
        return self._permission_decision_for_call(
            boundary, call, context
        )

    def _permission_decision_for_call(
        self,
        boundary: ReactCommandBoundary,
        call: PreparedToolCall,
        context: ToolExecutionContext,
    ) -> DriverEvent:
        identity = hashlib.sha256(f'permission|{boundary.run_id}|{context.effect_id}'.encode('utf-8')).hexdigest()
        nonce = hashlib.sha256(f'nonce|{identity}'.encode('utf-8')).hexdigest()
        expires_at = time.time() + 300.0
        prompt: dict[str, Any] = {
            'tool_name': call.tool_name,
            'call_id': call.stable_call_id,
            'reason': 'tool_requires_authorization',
            # Permission UI must show the exact prepared inputs that will be
            # executed.  Fingerprints/selectors prove identity but are not
            # enough for a human to judge an opaque command safely.
            'arguments': call.arguments_json(),
        }
        explicit_only = call.tool_name in boundary.confirm_only_names
        if explicit_only:
            prompt.update(
                {
                    "reason": "tool_requires_explicit_action_confirmation",
                    "authorization_origin": "explicit_decision",
                    "confirm_only_snapshot_ref": (
                        boundary.confirm_only_snapshot_ref
                    ),
                    "confirm_only_snapshot_hash": (
                        boundary.confirm_only_snapshot_hash
                    ),
                }
            )
        domain_kind: str | None = None
        domain_id: str | None = None
        if self._authorization_runtime is not None:
            permission_category = self._permission_category(call)
            exact = self._authorization_runtime.build_exact_request(
                call=call,
                context=context,
                permission_category=permission_category,
                decision_expires_at=expires_at,
            )
            prompt.update(
                authorization_intent_fingerprint=exact.fingerprint,
                permission_category=permission_category,
                effect_kind=call.effect_type,
                schema_hash=call.schema_hash,
                resource_selectors=[
                    selector.to_dict() for selector in call.resource_selectors
                ],
            )
            domain_kind = "react"
            domain_id = "prepared_tool"
        return OpenDecision(run_id=boundary.run_id, command_id=boundary.command_id, decision_id=identity, nonce=nonce, kind='permission', prompt=prompt, expires_at=expires_at, domain_kind=domain_kind, domain_id=domain_id, call_id=call.stable_call_id, effect_id=context.effect_id, tool_name=call.tool_name, args_hash=call.args_hash, capability_hash=context.capability_hash, scope_hash=context.scope_hash)

    @staticmethod
    def _grant_state(boundary: ReactCommandBoundary) -> dict[str, Mapping[str, Any]]:
        raw = boundary.completion_state.get('authorization_refs', {})
        return {str(call_id): dict(value) for call_id, value in dict(raw).items() if isinstance(value, Mapping)}

    @classmethod
    def _next_permission_index(cls, boundary: ReactCommandBoundary) -> int | None:
        grants = cls._grant_state(boundary)
        for index in boundary.provider_execution_indexes:
            call = boundary.pending_calls[index]
            if index in boundary.authorization_indexes and call.stable_call_id not in grants:
                return index
        return None

    @staticmethod
    def _prepared_action_fingerprint(call: PreparedToolCall) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "tool_name": call.tool_name,
                    "args_hash": call.args_hash,
                    "effect_type": call.effect_type,
                    "tool_spec_fingerprint": call.tool_spec_fingerprint,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _delegate_objective_signature(
        cls,
        call: PreparedToolCall,
        *,
        route_hint: str | None = None,
    ) -> dict[str, Any] | None:
        """Return a privacy-preserving signature for normal workflow delegates.

        Provider call ids and exact argument hashes are intentionally unsuitable
        here: a fallback provider can paraphrase the same failed objective and
        thereby create another child.  We retain only hashes of normalized words
        and CJK bigrams, bounded in the continuation state, so convergence can be
        checked without persisting another copy of the prompt text.
        """

        if call.tool_name != "workflow_spawn":
            return None
        args = call.arguments_json()
        objective = str(args.get("objective") or "").strip()
        profile_key = str(
            route_hint or args.get("profile_key") or ""
        ).strip()
        if not objective or not profile_key:
            return None
        normalized = unicodedata.normalize("NFKC", objective).casefold()
        words = {
            word
            for word in re.findall(r"[a-z0-9]+", normalized)
            if word not in cls._DELEGATE_OBJECTIVE_STOPWORDS
        }
        cjk_tokens: set[str] = set()
        for sequence in re.findall(r"[\u3400-\u9fff]+", normalized):
            cleaned = sequence
            for boilerplate in (
                "请",
                "使用",
                "派生",
                "一个",
                "持久子任务",
                "耐久子任务",
                "子工作流",
                "工作流",
            ):
                cleaned = cleaned.replace(boilerplate, "")
            if len(cleaned) == 1:
                cjk_tokens.add(cleaned)
            else:
                cjk_tokens.update(
                    cleaned[index : index + 2]
                    for index in range(len(cleaned) - 1)
                )
        tokens = {f"w:{item}" for item in words} | {
            f"c:{item}" for item in cjk_tokens
        }
        if not tokens:
            tokens = {"n:" + re.sub(r"\s+", " ", normalized).strip()}
        token_hashes = tuple(
            sorted(
                hashlib.sha256(token.encode("utf-8")).hexdigest()
                for token in tokens
            )
        )
        normalized_text = re.sub(r"\s+", " ", normalized).strip()
        return {
            "profile_key": profile_key,
            "objective_hash": hashlib.sha256(
                normalized_text.encode("utf-8")
            ).hexdigest(),
            "token_hashes": list(token_hashes),
        }

    @staticmethod
    def _delegate_objectives_match(
        current: Mapping[str, Any], previous: Mapping[str, Any]
    ) -> bool:
        if str(current.get("profile_key") or "") != str(
            previous.get("profile_key") or ""
        ):
            return False
        if current.get("objective_hash") == previous.get("objective_hash"):
            return True
        current_tokens = {
            str(item) for item in current.get("token_hashes", ()) if str(item)
        }
        previous_tokens = {
            str(item) for item in previous.get("token_hashes", ()) if str(item)
        }
        if not current_tokens or not previous_tokens:
            return False
        shared = len(current_tokens & previous_tokens)
        union = len(current_tokens | previous_tokens)
        smaller = min(len(current_tokens), len(previous_tokens))
        larger = max(len(current_tokens), len(previous_tokens))
        return (
            shared / union >= 0.72
            or (
                shared >= 2
                and shared / smaller >= 0.85
                and shared / larger >= 0.55
            )
        )

    @classmethod
    def _matching_failed_delegate(
        cls, boundary: ReactCommandBoundary
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if len(boundary.pending_calls) != 1:
            return None
        current = cls._delegate_objective_signature(boundary.pending_calls[0])
        if current is None:
            return None
        for raw in reversed(
            tuple(
                boundary.completion_state.get(
                    "failed_delegate_objectives", ()
                )
            )
        ):
            if not isinstance(raw, Mapping):
                continue
            previous = dict(raw)
            if cls._delegate_objectives_match(current, previous):
                return current, previous
        return None

    @staticmethod
    def _provider_call_record_id(run_id: str, provider_call_id: str) -> str:
        return "provider-call:" + hashlib.sha256(
            f"{run_id}|{provider_call_id}".encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _command_boundary_ref(boundary: ReactCommandBoundary) -> str:
        return f"react-command:{boundary.run_id}:{boundary.command_id}"

    @staticmethod
    def _prepared_call_ref(call: PreparedToolCall) -> str:
        return "prepared-call:" + fingerprint_json(call.to_dict())

    @staticmethod
    def _prepared_arguments_hash(call: PreparedToolCall) -> str:
        return fingerprint_json(call.arguments_json())

    def _successful_attempt_outcome_refs(
        self, boundary: ReactCommandBoundary
    ) -> Mapping[str, str] | None:
        current = dict(boundary.completion_state.get("current_attempt") or {})
        if (
            current.get("status") != "succeeded"
            or not current.get("attempt_id")
            or len(boundary.outcomes) != len(boundary.pending_calls)
            or len(boundary.outcome_statuses) != len(boundary.pending_calls)
        ):
            return None
        refs: dict[str, str] = {}
        for call, outcome, status in zip(
            boundary.pending_calls,
            boundary.outcomes,
            boundary.outcome_statuses,
        ):
            if (
                outcome is None
                or outcome.state is not ToolOutcomeState.SUCCESS
                or status is not OutcomeStatus.SUCCEEDED
            ):
                return None
            call_record_id = self._provider_call_record_id(
                boundary.run_id, call.stable_call_id
            )
            refs[call_record_id] = "provider-outcome:" + fingerprint_json(
                {
                    "command_id": boundary.command_id,
                    "call_record_id": call_record_id,
                    "outcome": outcome.to_dict(),
                }
            )
        return refs or None

    @staticmethod
    def _external_wait_state(
        boundary: ReactCommandBoundary,
    ) -> Mapping[str, Any] | None:
        value = boundary.completion_state.get("external_wait")
        return value if isinstance(value, Mapping) else None

    @staticmethod
    def _external_wait_index(boundary: ReactCommandBoundary) -> int | None:
        return next(
            (
                index
                for index in boundary.provider_execution_indexes
                if boundary.pending_calls[index].tool_name
                in {EXTERNAL_ACTION_WAIT, PROJECT_DIRECTORY_SELECT}
            ),
            None,
        )

    def _build_external_wait(
        self,
        boundary: ReactCommandBoundary,
        index: int,
    ) -> tuple[TaskExternalWait, DriverEvent]:
        call = boundary.pending_calls[index]
        context = boundary.tool_contexts[index]
        if call.tool_name not in {
            EXTERNAL_ACTION_WAIT,
            PROJECT_DIRECTORY_SELECT,
        }:
            raise ValueError("external wait index does not reference its host tool")
        current = dict(boundary.completion_state.get("current_attempt") or {})
        attempt_id = str(current.get("attempt_id") or "")
        if not attempt_id:
            raise RuntimeError("external wait requires the current Attempt identity")
        is_project_directory = call.tool_name == PROJECT_DIRECTORY_SELECT
        project_directory_mode = str(
            call.final_params.get("directory_mode") or "create_new"
        ).strip()
        is_existing_project = (
            is_project_directory and project_directory_mode == "use_existing"
        )
        wait_kind = (
            ExternalWaitKind.USER_CONTENT
            if is_project_directory
            else ExternalWaitKind(str(call.final_params.get("wait_kind") or ""))
        )
        required_action = (
            "请选择要使用的现有项目文件夹"
            if is_existing_project
            else "请选择项目保存到哪个文件夹下面"
            if is_project_directory
            else str(call.final_params.get("required_action") or "").strip()
        )
        if not required_action:
            raise ValueError("external wait requires a concrete user action")
        evidence_refs = tuple(
            dict.fromkeys(
                str(item).strip()
                for item in call.final_params.get("evidence_refs", ())
                if str(item).strip()
            )
        )
        root_run_id = (
            boundary.run_context.root_run_id
            if boundary.run_context is not None
            else boundary.run_id
        )
        call_record_id = self._provider_call_record_id(
            boundary.run_id, call.stable_call_id
        )
        wait_ref = "external-wait:" + fingerprint_json(
            {
                "root_run_id": root_run_id,
                "attempt_id": attempt_id,
                "call_record_id": call_record_id,
                "provider_call_id": call.stable_call_id,
                "wait_kind": wait_kind.value,
                "required_action": required_action,
            }
        )
        wait = TaskExternalWait(
            wait_ref=wait_ref,
            root_run_id=root_run_id,
            attempt_id=attempt_id,
            call_record_id=call_record_id,
            provider_call_id=call.stable_call_id,
            command_boundary_ref=self._command_boundary_ref(boundary),
            effect_id=context.effect_id,
            wait_kind=wait_kind,
            required_action_ref=(
                "external-action:"
                + fingerprint_json({"required_action": required_action})
            ),
            checkpoint_ref=boundary.prepared_context_ref,
            resume_admission_state=ProviderActionAdmissionState.PREPARED,
            evidence_refs=evidence_refs,
        )
        decision_id = "external-decision:" + fingerprint_json(
            {"wait_ref": wait_ref, "run_id": boundary.run_id}
        )
        nonce = fingerprint_json(
            {"decision_id": decision_id, "wait_ref": wait_ref}
        )
        decision = OpenDecision(
            run_id=boundary.run_id,
            command_id=boundary.command_id,
            decision_id=decision_id,
            nonce=nonce,
            kind="workflow_hitl",
            prompt={
                "title": (
                    "选择现有项目目录"
                    if is_existing_project
                    else "选择项目保存位置"
                    if is_project_directory
                    else "请完成 Windows 系统确认"
                    if wait_kind is ExternalWaitKind.UAC
                    else "需要你完成一个外部步骤"
                ),
                "required_action": required_action,
                "wait_kind": wait_kind.value,
                "wait_ref": wait_ref,
                "evidence_refs": list(evidence_refs),
                "tool_name": call.tool_name,
                **(
                    {
                        "project_name": str(
                            call.final_params.get("project_name") or ""
                        ).strip(),
                        "folder_name": str(
                            call.final_params.get("folder_name") or ""
                        ).strip(),
                        "project_kind": str(
                            call.final_params.get("project_kind") or ""
                        ).strip(),
                        "directory_mode": project_directory_mode,
                    }
                    if is_project_directory
                    else {}
                ),
            },
            domain_kind="external",
            domain_id=(
                "project_directory"
                if is_project_directory
                else "windows_uac"
                if wait_kind is ExternalWaitKind.UAC
                else "external_action"
            ),
            call_id=call.stable_call_id,
            effect_id=context.effect_id,
            tool_name=call.tool_name,
            args_hash=call.args_hash,
            capability_hash=context.capability_hash,
            scope_hash=context.scope_hash,
        )
        return wait, decision

    async def _stage_external_wait_boundary(
        self,
        boundary: ReactCommandBoundary,
        index: int,
        *,
        recovery_lease: RecoveryLease | None = None,
    ) -> tuple[ReactCommandBoundary, DriverEvent]:
        if boundary.pending_decision is not None:
            raise ValueError("external wait boundary already has a decision")
        wait, decision = self._build_external_wait(boundary, index)
        state = copy.deepcopy(dict(boundary.completion_state))
        current = dict(state.get("current_attempt") or {})
        current.update(status="waiting_external", budget_eligible=False)
        state["current_attempt"] = current
        state["attempt_status"] = "waiting_external"
        state["external_wait"] = {
            "record": wait.to_dict(),
            "index": index,
            "decision_id": decision.decision_id,
        }
        updated = replace(
            boundary,
            completion_state=state,
            pending_decision=decision,
            version=boundary.version + 1,
        )
        # Persist the replayable continuation and fenced UI decision first.
        # If the process exits before the second transaction, recover() stages
        # this same immutable wait before it re-emits the decision.
        updated = await self._save_durable_boundary(
            updated,
            decision=decision,
            recovery_lease=recovery_lease,
        )
        await self._uow.stage_external_wait(wait)
        return updated, decision

    async def _ensure_external_wait_staged(
        self, boundary: ReactCommandBoundary
    ) -> TaskExternalWait:
        state = self._external_wait_state(boundary)
        if state is None or not isinstance(state.get("record"), Mapping):
            raise RuntimeError("external wait continuation record is unavailable")
        wait = TaskExternalWait.from_dict(dict(state["record"]))
        return await self._uow.stage_external_wait(wait)

    @staticmethod
    def _with_project_workspace(
        boundary: ReactCommandBoundary,
        project_root: str,
    ) -> ReactCommandBoundary:
        if boundary.run_context is None:
            raise RuntimeError("project directory selection lost RunContext")
        workspace = {
            "root": project_root,
            "write_scope_root": project_root,
            "scope_hash": fingerprint_json(
                {
                    "session_id": boundary.run_context.session_id,
                    "workspace": project_root,
                    "write_scope_root": project_root,
                    "venue": boundary.run_context.venue,
                }
            ),
        }
        # RunContext freezes JSON arrays as tuples.  ``dataclasses.replace``
        # re-runs its validator, so hand the validator a thawed JSON value
        # instead of the already-frozen provider plan.  Exact provider/model
        # bindings must survive a user-confirmed workspace rebind unchanged.
        run_context = replace(
            boundary.run_context,
            workspace=workspace,
            provider_plan=thaw_json(boundary.run_context.provider_plan),
        )
        run_spec = (
            None
            if boundary.run_spec is None
            else replace(boundary.run_spec, context=run_context)
        )
        return replace(
            boundary,
            run_context=run_context,
            run_spec=run_spec,
        )

    @staticmethod
    def _replace_brokered_command(
        boundary: ReactCommandBoundary,
        updated: BrokeredCommandBoundary,
    ) -> ReactCommandBoundary:
        commands = list(boundary.brokered_commands)
        for index, current in enumerate(commands):
            if current.parent_index == updated.parent_index:
                commands[index] = updated
                break
        else:
            commands.append(updated)
            commands.sort(key=lambda item: item.parent_index)
        return replace(boundary, brokered_commands=tuple(commands))

    @staticmethod
    def _brokered_for_parent(
        boundary: ReactCommandBoundary, parent_index: int
    ) -> BrokeredCommandBoundary | None:
        return next(
            (
                item
                for item in boundary.brokered_commands
                if item.parent_index == parent_index
            ),
            None,
        )

    @staticmethod
    def _brokered_inner(
        command: BrokeredCommandBoundary,
    ) -> tuple[PreparedToolCall, ToolExecutionContext] | None:
        if (
            command.status != "running_actions"
            or command.current_inner_call is None
            or command.current_inner_context is None
        ):
            return None
        return (
            _load_call(command.current_inner_call),
            _load_context(command.current_inner_context),
        )

    def _is_brokered_call(self, call: PreparedToolCall) -> bool:
        dispatch = getattr(self._tool_registry, "dispatch_kind", None)
        if not callable(dispatch):
            return False
        try:
            return dispatch(call.tool_name) == "brokered_effect"
        except KeyError:
            return False

    @staticmethod
    def _set_parent_outcome(
        boundary: ReactCommandBoundary,
        *,
        parent_index: int,
        outcome: NormalizedToolOutcome,
        status: OutcomeStatus,
        metadata: Mapping[str, Any],
    ) -> ReactCommandBoundary:
        outcomes = list(boundary.outcomes)
        statuses = list(boundary.outcome_statuses)
        all_metadata = list(boundary.outcome_metadata)
        outcomes[parent_index] = outcome
        statuses[parent_index] = status
        all_metadata[parent_index] = MappingProxyType(
            copy.deepcopy(dict(metadata))
        )
        return replace(
            boundary,
            outcomes=tuple(outcomes),
            outcome_statuses=tuple(statuses),
            outcome_metadata=tuple(all_metadata),
        )

    def _accept_brokered_parent(
        self,
        boundary: ReactCommandBoundary,
        *,
        parent_index: int,
        outcome: NormalizedToolOutcome,
        metadata: Mapping[str, Any],
    ) -> tuple[ReactCommandBoundary, bool]:
        call = boundary.pending_calls[parent_index]
        if outcome.state is not ToolOutcomeState.SUCCESS or not self._is_brokered_call(call):
            return boundary, False
        try:
            envelope = BrokeredPlanEnvelope.from_durable_value(outcome.value)
        except (EffectPlanValidationError, TypeError, ValueError) as exc:
            failed = NormalizedToolOutcome.failure(
                "brokered_envelope_invalid", str(exc)
            )
            return (
                self._set_parent_outcome(
                    boundary,
                    parent_index=parent_index,
                    outcome=failed,
                    status=OutcomeStatus.FAILED,
                    metadata=metadata,
                ),
                True,
            )
        if envelope is None:
            failed = NormalizedToolOutcome.failure(
                "brokered_envelope_missing",
                "brokered tool did not return its durable plan envelope",
            )
            return (
                self._set_parent_outcome(
                    boundary,
                    parent_index=parent_index,
                    outcome=failed,
                    status=OutcomeStatus.FAILED,
                    metadata=metadata,
                ),
                True,
            )
        context = boundary.tool_contexts[parent_index]
        record = envelope.plan_record
        bindings_match = (
            record.root_run_id == context.root_run_id
            and record.parent_call_id == call.stable_call_id
            and record.provider_call_id == call.stable_call_id
            and record.tool_spec_fingerprint == call.tool_spec_fingerprint
            and envelope.input_snapshot.root_run_id == context.root_run_id
        )
        if not bindings_match:
            failed = NormalizedToolOutcome.failure(
                "brokered_envelope_binding_mismatch",
                "validated plan does not belong to the prepared provider call",
            )
            return (
                self._set_parent_outcome(
                    boundary,
                    parent_index=parent_index,
                    outcome=failed,
                    status=OutcomeStatus.FAILED,
                    metadata=metadata,
                ),
                True,
            )
        if self._brokered_for_parent(boundary, parent_index) is not None:
            raise ValueError("brokered parent outcome was already accepted")
        aggregate_ref = "brokered-aggregate:" + fingerprint_json(
            {
                "plan_ref": record.plan_ref,
                "parent_call_id": call.stable_call_id,
            }
        )
        command = BrokeredCommandBoundary(
            parent_index=parent_index,
            parent_prepared_call_ref=self._prepared_call_ref(call),
            provider_call_id=call.stable_call_id,
            plan_ref=record.plan_ref,
            current_inner_call_ref=None,
            aggregate_value_ref=aggregate_ref,
            status="deferred_pending",
            input_snapshot=envelope.input_snapshot,
            plan_record=record,
            value=copy.deepcopy(envelope.value),
            artifacts=tuple(copy.deepcopy(envelope.artifacts)),
            observations=tuple(copy.deepcopy(envelope.observations)),
            parent_outcome_metadata=dict(metadata),
        )
        return self._replace_brokered_command(boundary, command), True

    @staticmethod
    def _brokered_result_payload(
        command: BrokeredCommandBoundary,
    ) -> dict[str, Any]:
        return {
            "value": copy.deepcopy(command.value),
            "artifacts": copy.deepcopy(list(command.artifacts)),
            "observations": copy.deepcopy(list(command.observations)),
            "effect_plan": {
                "plan_ref": command.plan_record.plan_ref,
                "plan_hash": command.plan_record.plan_hash,
                "status": command.plan_record.status,
                "action_receipt_refs": list(
                    command.plan_record.action_receipt_refs
                ),
            },
        }

    def _terminalize_brokered(
        self,
        boundary: ReactCommandBoundary,
        command: BrokeredCommandBoundary,
        *,
        outcome: NormalizedToolOutcome,
        status: OutcomeStatus,
        plan_status: str | None = None,
        extra_metadata: Mapping[str, Any] = MappingProxyType({}),
    ) -> ReactCommandBoundary:
        record = command.plan_record
        if plan_status is not None and record.status not in {
            "succeeded",
            "failed",
            "cancelled",
        }:
            record = replace(record, status=plan_status)
        terminal = replace(
            command,
            status="terminal",
            plan_record=record,
            current_inner_call_ref=None,
            current_inner_call=None,
            current_inner_context=None,
        )
        boundary = self._replace_brokered_command(boundary, terminal)
        metadata = {
            **dict(command.parent_outcome_metadata),
            "brokered_plan_ref": record.plan_ref,
            "brokered_action_receipt_refs": list(record.action_receipt_refs),
            **dict(extra_metadata),
        }
        return self._set_parent_outcome(
            boundary,
            parent_index=command.parent_index,
            outcome=outcome,
            status=status,
            metadata=metadata,
        )

    def _prepare_brokered_action(
        self,
        boundary: ReactCommandBoundary,
        command: BrokeredCommandBoundary,
    ) -> ReactCommandBoundary:
        planner = self._brokered_planner
        if planner is None:
            failed = NormalizedToolOutcome.failure(
                "brokered_dispatch_unavailable",
                "durable brokered effect planner is not configured",
                value=self._brokered_result_payload(command),
            )
            return self._terminalize_brokered(
                boundary,
                command,
                outcome=failed,
                status=OutcomeStatus.FAILED,
                plan_status="failed",
            )
        try:
            action = planner.next_action(
                command.plan_record,
                snapshot=command.input_snapshot,
            )
            if action is None:
                if command.plan_record.status != "succeeded":
                    raise EffectPlanValidationError(
                        "brokered plan ended without a successful terminal record"
                    )
                return self._terminalize_brokered(
                    boundary,
                    command,
                    outcome=NormalizedToolOutcome.success(
                        self._brokered_result_payload(command)
                    ),
                    status=OutcomeStatus.SUCCEEDED,
                )
            if action.provider_backfill:
                raise EffectPlanValidationError(
                    "brokered inner actions may not request provider backfill"
                )
            parent_context = boundary.tool_contexts[command.parent_index]
            effect_id = hashlib.sha256(
                (
                    f"brokered-effect|{boundary.run_id}|"
                    f"{action.stable_call_id}"
                ).encode("utf-8")
            ).hexdigest()
            inner_context = replace(
                parent_context,
                origin="brokered_effect",
                command_id=boundary.command_id,
                call_id=action.stable_call_id,
                effect_id=effect_id,
            )
            prepared = self._tool_registry.prepare_call(
                action.tool_name,
                action.args,
                boundary.session_id,
                action.stable_call_id,
                execution_context=inner_context,
            )
            if (
                prepared.stable_call_id != action.stable_call_id
                or prepared.tool_name != action.tool_name
            ):
                raise ValueError(
                    "generic ToolSpec returned a mismatched brokered action"
                )
            if prepared.catalog_snapshot_ref not in {
                "",
                inner_context.capability_snapshot_ref,
            }:
                raise ValueError(
                    "brokered action capability snapshot ref drifted"
                )
            prepared = replace(
                prepared,
                catalog_snapshot_ref=(
                    inner_context.capability_snapshot_ref
                ),
            )
        except (EffectPlanValidationError, KeyError, OSError, TypeError, ValueError) as exc:
            failed = NormalizedToolOutcome.failure(
                "brokered_action_prepare_failed",
                f"{type(exc).__name__}: {exc}",
                value=self._brokered_result_payload(command),
            )
            return self._terminalize_brokered(
                boundary,
                command,
                outcome=failed,
                status=OutcomeStatus.FAILED,
                plan_status="failed",
            )
        return self._replace_brokered_command(
            boundary,
            replace(
                command,
                status="running_actions",
                current_inner_call_ref=prepared.stable_call_id,
                current_inner_call=_call_payload(prepared),
                current_inner_context=_context_payload(inner_context),
            ),
        )

    def _advance_brokered_actions(
        self, boundary: ReactCommandBoundary
    ) -> ReactCommandBoundary:
        if boundary.provider_execution_indexes:
            return boundary
        updated = boundary
        for command in boundary.active_brokered_commands:
            current = self._brokered_for_parent(updated, command.parent_index)
            if current is not None and current.status == "deferred_pending":
                updated = self._prepare_brokered_action(updated, current)
        return updated

    def _apply_brokered_action_outcome(
        self,
        boundary: ReactCommandBoundary,
        command: BrokeredCommandBoundary,
        *,
        outcome: NormalizedToolOutcome,
        status: OutcomeStatus,
        metadata: Mapping[str, Any],
    ) -> ReactCommandBoundary:
        inner = self._brokered_inner(command)
        if inner is None:
            raise ValueError("brokered action outcome has no current inner call")
        call, context = inner
        receipt_ref = str(metadata.get("receipt_ref") or "").strip()
        succeeded = (
            outcome.state is ToolOutcomeState.SUCCESS
            and status is OutcomeStatus.SUCCEEDED
            and bool(receipt_ref)
        )
        record = command.plan_record
        if receipt_ref:
            record = self._brokered_planner.record_receipt(  # type: ignore[union-attr]
                record,
                receipt_ref=receipt_ref,
                succeeded=succeeded,
            )
        elif record.status not in {"failed", "cancelled", "succeeded"}:
            record = replace(record, status="failed")
        cleared = replace(
            command,
            status=(
                "terminal"
                if record.status in {"succeeded", "failed", "cancelled"}
                else "deferred_pending"
            ),
            plan_record=record,
            current_inner_call_ref=None,
            current_inner_call=None,
            current_inner_context=None,
        )
        if succeeded and record.status == "succeeded":
            return self._terminalize_brokered(
                boundary,
                cleared,
                outcome=NormalizedToolOutcome.success(
                    self._brokered_result_payload(cleared)
                ),
                status=OutcomeStatus.SUCCEEDED,
                extra_metadata={
                    "brokered_last_inner_effect_id": context.effect_id,
                },
            )
        if succeeded:
            return self._replace_brokered_command(boundary, cleared)
        code = (
            "brokered_action_receipt_missing"
            if outcome.state is ToolOutcomeState.SUCCESS
            and status is OutcomeStatus.SUCCEEDED
            else "brokered_action_failed"
        )
        message = (
            "generic action completed without a durable receipt"
            if code == "brokered_action_receipt_missing"
            else str(
                (dict(outcome.error or {})).get("message")
                or f"generic action ended with {status.value}"
            )
        )
        failed = NormalizedToolOutcome.failure(
            code,
            message,
            value={
                **self._brokered_result_payload(cleared),
                "failed_inner_call_id": call.stable_call_id,
                "inner_outcome": outcome.to_dict(),
            },
        )
        return self._terminalize_brokered(
            boundary,
            cleared,
            outcome=failed,
            status=(
                OutcomeStatus.CANCELLED
                if status is OutcomeStatus.CANCELLED
                else OutcomeStatus.FAILED
            ),
            plan_status=(
                "cancelled"
                if status is OutcomeStatus.CANCELLED
                else "failed"
            ),
            extra_metadata={
                "brokered_failed_inner_effect_id": context.effect_id,
            },
        )

    def _next_brokered_permission(
        self, boundary: ReactCommandBoundary
    ) -> tuple[PreparedToolCall, ToolExecutionContext] | None:
        grants = self._grant_state(boundary)
        for command in boundary.active_brokered_commands:
            inner = self._brokered_inner(command)
            if inner is None:
                continue
            call, context = inner
            requires_authorization, _effectful = (
                self._tool_registry.prepared_execution_policy(call)
            )
            if (
                requires_authorization
                or call.tool_name in boundary.confirm_only_names
            ) and call.stable_call_id not in grants:
                return call, context
        return None

    def _next_permission_after_progress(
        self, boundary: ReactCommandBoundary
    ) -> DriverEvent | None:
        index = self._next_permission_index(boundary)
        if index is not None:
            return self._permission_decision(boundary, index)
        if boundary.provider_execution_indexes:
            return None
        brokered = self._next_brokered_permission(boundary)
        if brokered is None:
            return None
        return self._permission_decision_for_call(
            boundary, brokered[0], brokered[1]
        )

    async def _admit_provider_boundary(
        self, boundary: ReactCommandBoundary
    ) -> None:
        """Project the compatible JSON boundary into the authoritative v10 tables."""

        if boundary.command_kind not in {"execute_tools", "control_delegate"}:
            return
        current = dict(boundary.completion_state.get("current_attempt") or {})
        if not current.get("attempt_id"):
            raise RuntimeError("provider boundary is missing its attempt identity")
        root_run_id = (
            boundary.run_context.root_run_id
            if boundary.run_context is not None
            else boundary.run_id
        )
        task_scope_id = str(
            boundary.request_payload.get("task_scope_id")
            or boundary.request_payload.get("root_run_id")
            or root_run_id
        )
        goal = await self._uow.get_task_goal(root_run_id)
        if goal is None:
            objective_ref = str(
                boundary.request_payload.get("objective_ref")
                or boundary.prepared_context_ref
                or (
                    "request:"
                    + fingerprint_json(
                        {
                            "root_run_id": root_run_id,
                            "request_id": (
                                boundary.run_context.request_id
                                if boundary.run_context is not None
                                else boundary.run_id
                            ),
                        }
                    )
                )
            )
            await self._uow.create_task_goal(
                DurableTaskGoalRecord(
                    goal_id="task-goal:"
                    + hashlib.sha256(root_run_id.encode("utf-8")).hexdigest(),
                    root_run_id=root_run_id,
                    task_scope_id=task_scope_id,
                    objective_ref=objective_ref,
                ),
                DurablePlanVersionRecord(
                    root_run_id=root_run_id,
                    plan_version=1,
                ),
            )
        elif goal.task_scope_id != task_scope_id:
            raise RuntimeError("provider boundary task scope differs from its root goal")

        plan_version = max(1, int(current.get("plan_version") or 1))
        trigger_failure_set_id = (
            (
                str(current["trigger_failure_set_id"])
                if current.get("trigger_failure_set_id")
                else None
            )
            if "trigger_failure_set_id" in current
            else (
                str(boundary.completion_state["latest_failure_set_id"])
                if boundary.completion_state.get("latest_failure_set_id")
                else None
            )
        )
        if plan_version > 1:
            await self._uow.append_plan_version(
                DurablePlanVersionRecord(
                    root_run_id=root_run_id,
                    plan_version=plan_version,
                    trigger_failure_set_id=trigger_failure_set_id,
                )
            )

        prepared_by_id = {
            call.stable_call_id: call for call in boundary.pending_calls
        }
        raw_by_id = {
            str(item["provider_call_id"]): dict(item)
            for item in boundary.raw_failures
        }
        durable_calls: list[DurableProviderActionCall] = []
        batch_fingerprint_payload = []
        for call_order, provider_call_id in enumerate(boundary.provider_call_order):
            call_record_id = self._provider_call_record_id(
                boundary.run_id, provider_call_id
            )
            prepared = prepared_by_id.get(provider_call_id)
            if prepared is not None:
                arguments_hash = self._prepared_arguments_hash(prepared)
                durable_calls.append(
                    DurableProviderActionCall(
                        call_record_id=call_record_id,
                        root_run_id=root_run_id,
                        provider_batch_id=boundary.command_id,
                        call_order=call_order,
                        provider_call_id=provider_call_id,
                        raw_tool_name=prepared.tool_name,
                        raw_arguments_ref=f"inline:{arguments_hash}",
                        raw_arguments_hash=arguments_hash,
                    )
                )
                batch_fingerprint_payload.append(
                    {
                        "provider_call_id": provider_call_id,
                        "tool_name": prepared.tool_name,
                        "arguments_hash": arguments_hash,
                    }
                )
                continue
            failure = raw_by_id[provider_call_id]
            outcome_ref = "admission-error:" + fingerprint_json(
                {
                    "provider_call_id": provider_call_id,
                    "error_code": str(failure["error_code"]),
                    "raw_arguments_hash": str(failure["raw_arguments_hash"]),
                }
            )
            durable_calls.append(
                DurableProviderActionCall(
                    call_record_id=call_record_id,
                    root_run_id=root_run_id,
                    provider_batch_id=boundary.command_id,
                    call_order=call_order,
                    provider_call_id=provider_call_id,
                    raw_tool_name=str(failure["raw_tool_name"]),
                    raw_arguments_ref=str(failure["raw_arguments_ref"]),
                    raw_arguments_hash=str(failure["raw_arguments_hash"]),
                    parsed_arguments_hash=(
                        str(failure["parsed_arguments_hash"])
                        if failure.get("parsed_arguments_hash")
                        else None
                    ),
                    admission_state=ProviderActionAdmissionState.REJECTED,
                    terminal_outcome_ref=outcome_ref,
                )
            )
            batch_fingerprint_payload.append(
                {
                    "provider_call_id": provider_call_id,
                    "tool_name": str(failure["raw_tool_name"]),
                    "arguments_hash": str(failure["raw_arguments_hash"]),
                    "admission_error": str(failure["error_code"]),
                }
            )

        batch_fingerprint = str(
            current.get("provider_batch_fingerprint")
            or fingerprint_json(batch_fingerprint_payload)
        )
        await self._uow.open_provider_turn_fence(
            ProviderTurnFence(
                provider_turn_id=boundary.command_id,
                root_run_id=root_run_id,
                idempotency_key=(
                    f"provider-turn:{boundary.run_id}:{boundary.command_id}"
                ),
                request_hash=batch_fingerprint,
            )
        )
        await self._uow.accept_provider_batch_and_create_attempt(
            DurableProviderActionBatch(
                provider_batch_id=boundary.command_id,
                root_run_id=root_run_id,
                provider_turn_id=boundary.command_id,
                canonical_assistant_batch_ref=str(
                    current.get("canonical_assistant_batch_ref")
                    or (
                        "assistant-batch:"
                        + fingerprint_json(
                            [dict(item) for item in boundary.canonical_messages]
                        )
                    )
                ),
                batch_fingerprint=batch_fingerprint,
                pending_call_count=len(durable_calls),
            ),
            DurableAttemptRecord(
                attempt_id=str(current["attempt_id"]),
                root_run_id=root_run_id,
                run_id=boundary.run_id,
                provider_turn_id=boundary.command_id,
                provider_batch_id=boundary.command_id,
                plan_version=plan_version,
                trigger_failure_set_id=trigger_failure_set_id,
                supersedes_attempt_id=(
                    str(current["supersedes_attempt_id"])
                    if current.get("supersedes_attempt_id")
                    else None
                ),
                strategy_fingerprint=str(current["strategy_fingerprint"]),
                planned_call_refs=tuple(
                    call.call_record_id for call in durable_calls
                ),
                checkpoint_ref=boundary.prepared_context_ref,
            ),
            durable_calls,
        )
        command_boundary_ref = self._command_boundary_ref(boundary)
        for prepared in boundary.pending_calls:
            await self._uow.mark_provider_action_prepared(
                self._provider_call_record_id(
                    boundary.run_id, prepared.stable_call_id
                ),
                expected_version=0,
                parsed_arguments_hash=self._prepared_arguments_hash(prepared),
                prepared_call_ref=self._prepared_call_ref(prepared),
                command_boundary_ref=command_boundary_ref,
            )

    def _captured_confirm_only_policy(
        self,
        request: DriverStart,
        batch: ReactToolBatch,
    ) -> tuple[tuple[str, ...], str | None, str | None]:
        if not batch.contexts:
            return (), None, None
        scope_ids = {context.scope_id for context in batch.contexts}
        if len(scope_ids) != 1:
            raise ValueError("one provider batch cannot mix tool scope snapshots")
        store = self._capability_scope_store or getattr(
            self._tool_registry, "capability_scope_store", None
        )
        if store is None:
            return (), None, None
        first = batch.contexts[0]
        record = store.get(
            first.scope_id,
            session_id=first.session_id,
            request_id=first.request_id,
        )
        if record is None:
            return (), None, None
        prepared = record.prepared
        names = tuple(prepared.confirm_only_names)
        if not names:
            return (), None, None
        snapshot_ref = (
            request.tool_set_snapshot_ref
            or f"prepared-tool-set:{prepared.scope_id}:{prepared.revision}"
        )
        snapshot_hash = fingerprint_json(
            {
                "snapshot_ref": snapshot_ref,
                "scope_id": prepared.scope_id,
                "revision": prepared.revision,
                "schema_fingerprint": prepared.schema_fingerprint,
                "effect_policy_version": prepared.effect_policy_version,
                "effect_policy_hash": prepared.effect_policy_hash,
                "confirm_only_names": list(names),
            }
        )
        return names, snapshot_ref, snapshot_hash

    def _boundary_for_batch(self, request: DriverStart, batch: ReactToolBatch, version: int=0) -> ReactCommandBoundary:
        policies = tuple(self._tool_registry.prepared_execution_policy(call) for call in batch.calls)
        confirm_only_names, confirm_only_ref, confirm_only_hash = (
            self._captured_confirm_only_policy(request, batch)
        )
        confirm_only = frozenset(confirm_only_names)
        authorization = tuple(
            index
            for index, (call, policy) in enumerate(zip(batch.calls, policies))
            if policy[0] or call.tool_name in confirm_only
        )
        durable = tuple(index for index, policy in enumerate(policies) if policy[1])
        state = copy.deepcopy(dict(request.completion_state))
        batch_feedback = copy.deepcopy(dict(batch.feedback_state))
        host_retry = batch_feedback.pop("_host_capability_retry", None)
        state['agent_loop_feedback'] = {**dict(state.get('agent_loop_feedback') or {}), **batch_feedback}
        retry_calls = tuple(
            call for call in batch.calls if call.retry_of_effect_id
        )
        if retry_calls:
            if len(retry_calls) != 1:
                raise ValueError(
                    "one provider batch cannot retry multiple repaired effects"
                )
            pending_retry = state.pop("pending_capability_retry", None)
            state["capability_retry_inflight"] = {
                "provider_call_id": retry_calls[0].stable_call_id,
                "retry_of_effect_id": retry_calls[0].retry_of_effect_id,
                "failure_receipt_ref": (
                    str(pending_retry.get("failure_receipt_ref") or "")
                    if isinstance(pending_retry, Mapping)
                    else ""
                ),
            }
        elif isinstance(host_retry, Mapping):
            pending_retry = state.pop("pending_capability_retry", None)
            state["capability_retry_prepare_failure"] = {
                "failure_receipt_ref": str(
                    host_retry.get("failure_receipt_ref") or ""
                ),
                "retry_of_effect_id": str(
                    (
                        pending_retry.get("retry_of_effect_id")
                        if isinstance(pending_retry, Mapping)
                        else host_retry.get("retry_of_effect_id")
                    )
                    or ""
                ),
            }
        previous_plan = max(1, int(state.get("plan_version", 1)))
        plan_version = (
            previous_plan + 1
            if state.get("latest_failure_set_id")
            and state.get("attempt_status") in {"failed", "rejected"}
            else previous_plan
        )
        prepared_by_id = {
            call.stable_call_id: {
                "tool_name": call.tool_name,
                "args_hash": call.args_hash,
                "effect_type": call.effect_type,
                "tool_spec_fingerprint": call.tool_spec_fingerprint,
            }
            for call in batch.calls
        }
        raw_by_id = {
            str(item["provider_call_id"]): {
                "tool_name": str(item["raw_tool_name"]),
                "args_hash": str(item["raw_arguments_hash"]),
                "admission_error": str(item["error_code"]),
            }
            for item in batch.raw_failures
        }
        strategy_payload = [
            (
                prepared_by_id[call_id]
                if call_id in prepared_by_id
                else raw_by_id[call_id]
            )
            for call_id in batch.provider_call_order
        ]
        strategy_fingerprint = hashlib.sha256(
            json.dumps(
                strategy_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        canonical_assistant_batch_ref = (
            "assistant-batch:"
            + fingerprint_json(
                [
                    dict(item)
                    for item in (
                        batch.canonical_messages or request.canonical_messages
                    )
                ]
            )
        )
        provider_batch_fingerprint = fingerprint_json(strategy_payload)
        attempt_id = "attempt:" + hashlib.sha256(
            f"{request.run_id}|{batch.command_id}|{strategy_fingerprint}".encode(
                "utf-8"
            )
        ).hexdigest()
        prior_reports = tuple(
            TaskFailureReport.from_dict(item)
            for item in state.get("failure_reports", ())
            if isinstance(item, Mapping)
        )
        current_action_fingerprints = (
            *(
                self._prepared_action_fingerprint(call)
                for call in batch.calls
            ),
            *(
                str(item["action_fingerprint"])
                for item in batch.raw_failures
            ),
        )
        loop_decision = "allow"
        guard = AttemptLoopGuard()
        for action_fingerprint in current_action_fingerprints:
            matching = tuple(
                report
                for report in prior_reports
                if report.action_fingerprint == action_fingerprint
            )
            if not matching:
                continue
            decision = guard.evaluate(
                action_fingerprint=action_fingerprint,
                error_fingerprint=matching[-1].error_fingerprint,
                strategy_fingerprint=strategy_fingerprint,
                prior_reports=matching,
            )
            if decision == "block_budget":
                loop_decision = decision
                break
            if decision == "reject_same_strategy":
                loop_decision = decision
        state.update(
            {
                "model_backfilled": False,
                "plan_version": plan_version,
                "current_attempt": {
                    "attempt_id": attempt_id,
                    "provider_turn_id": batch.command_id,
                    "provider_batch_id": batch.command_id,
                    "plan_version": plan_version,
                    "strategy_fingerprint": strategy_fingerprint,
                    "canonical_assistant_batch_ref": (
                        canonical_assistant_batch_ref
                    ),
                    "provider_batch_fingerprint": provider_batch_fingerprint,
                    "planned_call_refs": [
                        self._provider_call_record_id(
                            request.run_id, provider_call_id
                        )
                        for provider_call_id in batch.provider_call_order
                    ],
                    "trigger_failure_set_id": (
                        str(state["latest_failure_set_id"])
                        if state.get("latest_failure_set_id")
                        else None
                    ),
                    "supersedes_attempt_id": (
                        str(state["latest_attempt_id"])
                        if state.get("latest_attempt_id")
                        else None
                    ),
                    "status": "running",
                    "budget_eligible": True,
                },
                "attempt_status": "running",
                "loop_guard_decision": loop_decision,
            }
        )
        outcomes, outcome_statuses, outcome_metadata = (
            replay_artifact_completions(
                session_id=request.session_id,
                calls=batch.calls,
                completion_state=state,
            )
        )
        raw_failures = tuple(dict(item) for item in batch.raw_failures)
        if loop_decision != "allow":
            blocked = loop_decision == "block_budget"
            error_code = (
                "attempt_budget_exhausted" if blocked else "replan_required"
            )
            message = (
                "model replan budget exhausted for the same failure cause"
                if blocked
                else "the proposed action is unchanged after the same failure; "
                "choose a materially different strategy"
            )
            outcomes = tuple(
                NormalizedToolOutcome.failure(error_code, message)
                for _call in batch.calls
            )
            outcome_statuses = (OutcomeStatus.FAILED,) * len(batch.calls)
            raw_failures = tuple(
                {
                    **item,
                    "source_kind": "strategy_rejected",
                    "source_identity": "attempt_loop_guard",
                    "error_code": error_code,
                    "message": message,
                }
                for item in raw_failures
            )
            attempt_status = "blocked" if blocked else "rejected"
            state["attempt_status"] = attempt_status
            state["current_attempt"] = {
                **dict(state["current_attempt"]),
                "status": attempt_status,
            }
            if blocked:
                state["agent_loop_terminal_error"] = json.dumps(
                    {
                        "code": error_code,
                        "message": message,
                        "failure_refs": [
                            str(item.get("report_ref") or "")
                            for item in state.get("failure_reports", ())
                        ],
                        "tried_strategies": list(
                            state.get("prior_strategy_fingerprints", ())
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
        return ReactCommandBoundary(run_id=request.run_id, session_id=request.session_id, command_id=batch.command_id, command_kind='execute_tools', canonical_messages=batch.canonical_messages or request.canonical_messages, session_projection_cursor=request.session_projection_cursor, prepared_context_ref=request.prepared_context_ref, tool_set_snapshot_ref=request.tool_set_snapshot_ref, pending_calls=batch.calls, tool_contexts=batch.contexts, outcomes=outcomes, provider_state=request.provider_state, iteration=max(request.iteration, batch.iteration), completion_state=state, capability_snapshot=request.capability_snapshot, request_payload=request.request_payload, run_context=request.run_context, run_spec=request.run_spec, version=version, outcome_statuses=outcome_statuses, outcome_metadata=outcome_metadata, authorization_indexes=authorization, durable_indexes=durable, confirm_only_names=confirm_only_names, confirm_only_snapshot_ref=confirm_only_ref, confirm_only_snapshot_hash=confirm_only_hash, raw_failures=raw_failures, provider_call_order=batch.provider_call_order, active_skill_scope_ids=request.active_skill_scope_ids, activated_skill_scopes=request.activated_skill_scopes)

    def _boundary_for_control(
        self, request: DriverStart, batch: ReactControlBatch, version: int = 0
    ) -> ReactCommandBoundary:
        tool_batch = ReactToolBatch(
            command_id=batch.command_id,
            calls=(batch.call,),
            contexts=(batch.context,),
            canonical_messages=batch.canonical_messages,
            iteration=batch.iteration,
            feedback_state=batch.feedback_state,
        )
        return replace(
            self._boundary_for_batch(request, tool_batch, version),
            command_kind="control_delegate",
        )

    async def _prepare_control_event(
        self,
        boundary: ReactCommandBoundary,
        *,
        recovery_lease: RecoveryLease | None = None,
    ) -> tuple[ReactCommandBoundary, DriverEvent | None]:
        if boundary.command_kind != "control_delegate" or len(boundary.pending_calls) != 1:
            raise ValueError("control delegate boundary is malformed")
        if boundary.pending_delegate is not None:
            return boundary, boundary.pending_delegate
        if boundary.pending_decision is not None:
            return boundary, boundary.pending_decision
        if boundary.outcomes[0] is not None:
            # A resolved permission denial (or another authoritative control
            # outcome) must never be re-prepared as a delegate command.
            return boundary, None
        duplicate = self._matching_failed_delegate(boundary)
        if duplicate is not None:
            current, previous = duplicate
            failed = NormalizedToolOutcome.failure(
                "duplicate_failed_delegation",
                (
                    "a child for a materially equivalent objective already "
                    "failed; do not spawn another child for the same work. "
                    "Use the existing failure evidence, choose a materially "
                    "different objective, or finish with an honest partial/"
                    "failure response"
                ),
                value={
                    "matched_failed_child_run_id": str(
                        previous.get("child_run_id") or ""
                    ),
                    "profile_key": str(current["profile_key"]),
                },
            )
            updated = boundary.with_outcomes(
                {0: failed},
                {0: OutcomeStatus.FAILED},
                {
                    0: {
                        "error_code": "duplicate_failed_delegation",
                        "retryable": False,
                        "matched_failed_child_run_id": str(
                            previous.get("child_run_id") or ""
                        ),
                    }
                },
            )
            state = copy.deepcopy(dict(updated.completion_state))
            rejections = int(
                state.get("delegate_convergence_rejections") or 0
            ) + 1
            state["delegate_convergence_rejections"] = rejections
            state["last_delegate_convergence_rejection"] = {
                "profile_key": str(current["profile_key"]),
                "objective_hash": str(current["objective_hash"]),
                "matched_failed_child_run_id": str(
                    previous.get("child_run_id") or ""
                ),
            }
            if rejections >= self._DELEGATE_CONVERGENCE_REJECTION_LIMIT:
                state["agent_loop_terminal_error"] = json.dumps(
                    {
                        "code": "delegate_convergence_exhausted",
                        "message": (
                            "provider repeatedly proposed a materially "
                            "equivalent delegate after its child failed"
                        ),
                        "matched_failed_child_run_id": str(
                            previous.get("child_run_id") or ""
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            updated = replace(
                updated,
                completion_state=state,
                version=boundary.version + 1,
            )
            await self._save_progress(
                updated, recovery_lease=recovery_lease
            )
            return updated, None
        try:
            control = await self._collaborator.prepare_control(
                boundary.to_start(), boundary.pending_calls[0]
            )
        except ValueError as exc:
            message = str(exc)
            if "project_workspace_selection_required" not in message:
                raise
            failed = NormalizedToolOutcome.failure(
                "project_workspace_selection_required",
                (
                    "Select the project directory with "
                    "project_directory_select, wait for the user confirmation, "
                    "then retry this same workflow_spawn."
                ),
            )
            updated = boundary.with_outcomes(
                {0: failed},
                {0: OutcomeStatus.FAILED},
                {
                    0: {
                        "error_code": "project_workspace_selection_required",
                        "retryable": True,
                        "required_tool": "project_directory_select",
                    }
                },
            )
            state = copy.deepcopy(dict(updated.completion_state))
            state["last_control_precondition"] = {
                "code": "project_workspace_selection_required",
                "retryable": True,
            }
            updated = replace(
                updated,
                completion_state=state,
                version=boundary.version + 1,
            )
            await self._save_progress(
                updated, recovery_lease=recovery_lease
            )
            return updated, None
        if control.run_id != boundary.run_id:
            raise ValueError("control event run binding mismatch")
        updated = replace(
            boundary,
            pending_delegate=(
                control if control.kind == "delegate_run" else None
            ),
            pending_decision=(
                control if control.kind == "open_decision" else None
            ),
            version=boundary.version + 1,
        )
        if control.kind == "open_decision":
            updated = await self._save_durable_boundary(
                updated,
                decision=control,
                recovery_lease=recovery_lease,
            )
        else:
            await self._save_progress(updated, recovery_lease=recovery_lease)
        return updated, control

    @staticmethod
    def _tool_messages(boundary: ReactCommandBoundary) -> tuple[Mapping[str, Any], ...]:
        messages = [copy.deepcopy(dict(item)) for item in boundary.canonical_messages]
        prepared = {
            call.stable_call_id: (index, call)
            for index, call in enumerate(boundary.pending_calls)
        }
        raw = {
            str(item["provider_call_id"]): dict(item)
            for item in boundary.raw_failures
        }
        for provider_call_id in boundary.provider_call_order:
            if provider_call_id in prepared:
                index, call = prepared[provider_call_id]
                outcome = boundary.outcomes[index]
                assert outcome is not None
                context = boundary.tool_contexts[index]
                metadata = boundary.outcome_metadata[index]
                status = boundary.outcome_statuses[index]
                messages.append({'role': 'tool', 'tool_call_id': call.stable_call_id, 'name': call.tool_name, 'content': json.dumps({'status': status.value if status is not None else OutcomeStatus.UNKNOWN.value, 'outcome': outcome.to_dict(), 'effect_id': context.effect_id, **dict(metadata)}, ensure_ascii=False, sort_keys=True, default=str)})
                continue
            failure = raw[provider_call_id]
            payload = {
                "status": OutcomeStatus.FAILED.value,
                "outcome": NormalizedToolOutcome.failure(
                    str(failure["error_code"]),
                    str(failure["message"]),
                ).to_dict(),
                "admission": {
                    "source_kind": failure["source_kind"],
                    "call_order": failure["call_order"],
                    "raw_arguments": failure.get("raw_arguments"),
                    "raw_arguments_hash": failure["raw_arguments_hash"],
                    "parsed_arguments_hash": failure.get(
                        "parsed_arguments_hash"
                    ),
                },
            }
            for name in ("retriable", "replan"):
                if failure.get(name) is not None:
                    payload["admission"][name] = bool(failure[name])
            for name in (
                "failure_report_ref",
                "failure_report",
                "failure_set_id",
            ):
                if failure.get(name) is not None:
                    payload[name] = failure[name]
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": provider_call_id,
                    "name": str(failure["raw_tool_name"]),
                    "content": json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                }
            )
        return tuple(messages)

    async def _emit(self, request: DriverStart, emissions: AsyncIterator[ReactEmission], *, continuation_version: int=0, recovery_lease: RecoveryLease | None=None) -> AsyncIterator[DriverEvent]:
        launch_pending = request.launch_operation_id is not None
        async for emission in emissions:
            if launch_pending:
                state = dict(request.completion_state)
                state.update(launch_operation_id=request.launch_operation_id, provider_launch_snapshot=request.provider_launch_snapshot.to_dict(), admission_boundary_version=max(continuation_version, request.admission_launch.boundary.boundary_version if request.admission_launch is not None else 0))
                launch_boundary = ReactCommandBoundary(run_id=request.run_id, session_id=request.session_id, command_id=request.launch_operation_id or '', command_kind='provider_launch', canonical_messages=request.canonical_messages, session_projection_cursor=request.session_projection_cursor, prepared_context_ref=request.prepared_context_ref, tool_set_snapshot_ref=request.tool_set_snapshot_ref, pending_calls=(), tool_contexts=(), outcomes=(), provider_state=request.provider_state, iteration=request.iteration, completion_state=state, capability_snapshot=request.capability_snapshot, request_payload=request.request_payload, run_context=request.run_context, run_spec=request.run_spec, version=continuation_version, active_skill_scope_ids=request.active_skill_scope_ids, activated_skill_scopes=request.activated_skill_scopes)
                launch_boundary = await self._persist_boundary(request, launch_boundary, continuation_version=max(continuation_version, int(state['admission_boundary_version'])), recovery_lease=recovery_lease, admission_launch=request.admission_launch)
                continuation_version, launch_pending = launch_boundary.version, False
            if isinstance(emission, ReactToken):
                yield TokenCandidate(
                    request.run_id,
                    emission.content,
                    emission.kind,
                    invocation_id=emission.invocation_id,
                    stream_epoch=emission.stream_epoch,
                    provisional=emission.provisional,
                    retract_provisional=emission.retract_provisional,
                )
            elif isinstance(emission, ReactFallback):
                yield ProviderFallbackCandidate(request.run_id, emission.from_provider, emission.to_provider, emission.reason)
            elif isinstance(emission, DriverEvent) and emission.kind == "context_compacted":
                from deskpet.agent.context_usage import (
                    context_usage_sample_id,
                )
                request_payload = copy.deepcopy(
                    dict(request.request_payload)
                )
                request_payload["context_usage_basis_sample_id"] = (
                    context_usage_sample_id(
                        request.session_id,
                        emission.source_event_id,
                    )
                )
                request = replace(
                    request,
                    request_payload=request_payload,
                )
                yield emission
            elif isinstance(emission, ReactControlBatch):
                boundary = self._boundary_for_control(
                    request, emission, continuation_version
                )
                permission_index = self._next_permission_index(boundary)
                if permission_index is not None:
                    decision = self._permission_decision(boundary, permission_index)
                    boundary = replace(boundary, pending_decision=decision)
                    boundary = await self._persist_boundary(
                        request,
                        boundary,
                        continuation_version=continuation_version,
                        decision=decision,
                        recovery_lease=recovery_lease,
                    )
                    await self._admit_provider_boundary(boundary)
                    async for candidate in self._apply_permission_policy(
                        boundary, decision
                    ):
                        yield candidate
                    return
                boundary = await self._persist_boundary(
                    request,
                    boundary,
                    continuation_version=continuation_version,
                    recovery_lease=recovery_lease,
                )
                await self._admit_provider_boundary(boundary)
                if not boundary.pending_indexes:
                    async for candidate in self._resume_completed(
                        boundary, recovery_lease=recovery_lease
                    ):
                        yield candidate
                    return
                boundary, delegate = await self._prepare_control_event(
                    boundary, recovery_lease=recovery_lease
                )
                if delegate is None:
                    async for candidate in self._resume_completed(
                        boundary, recovery_lease=recovery_lease
                    ):
                        yield candidate
                    return
                yield delegate
                return
            elif isinstance(emission, ReactToolBatch):
                boundary = self._boundary_for_batch(request, emission, continuation_version)
                permission_index = self._next_permission_index(boundary)
                if permission_index is not None:
                    decision = self._permission_decision(boundary, permission_index)
                    boundary = replace(boundary, pending_decision=decision)
                    boundary = await self._persist_boundary(
                        request,
                        boundary,
                        continuation_version=continuation_version,
                        decision=decision,
                        recovery_lease=recovery_lease,
                    )
                    await self._admit_provider_boundary(boundary)
                    async for candidate in self._apply_permission_policy(
                        boundary, decision
                    ):
                        yield candidate
                    return
                boundary = await self._persist_boundary(
                    request,
                    boundary,
                    continuation_version=continuation_version,
                    recovery_lease=recovery_lease,
                )
                await self._admit_provider_boundary(boundary)
                if not boundary.pending_indexes:
                    async for candidate in self._resume_completed(
                        boundary, recovery_lease=recovery_lease
                    ):
                        yield candidate
                    return
                async for candidate in self._continue_after_tool_progress(
                    boundary, recovery_lease=recovery_lease
                ):
                    yield candidate
                return
            elif isinstance(emission, DriverEvent) and emission.kind == 'open_decision':
                decision = emission
                if decision.run_id != request.run_id:
                    raise ValueError('decision run binding mismatch')
                boundary = ReactCommandBoundary(run_id=request.run_id, session_id=request.session_id, command_id=decision.command_id, command_kind='open_decision', canonical_messages=request.canonical_messages, session_projection_cursor=request.session_projection_cursor, prepared_context_ref=request.prepared_context_ref, tool_set_snapshot_ref=request.tool_set_snapshot_ref, pending_calls=(), tool_contexts=(), outcomes=(), provider_state=request.provider_state, iteration=request.iteration, completion_state=request.completion_state, capability_snapshot=request.capability_snapshot, request_payload=request.request_payload, run_context=request.run_context, run_spec=request.run_spec, pending_decision=decision, version=continuation_version, active_skill_scope_ids=request.active_skill_scope_ids, activated_skill_scopes=request.activated_skill_scopes)
                boundary = await self._persist_boundary(request, boundary, continuation_version=continuation_version, decision=decision, recovery_lease=recovery_lease)
                yield decision
                return
            elif isinstance(emission, DriverEvent) and emission.kind == 'delegate_run':
                command = emission
                if command.run_id != request.run_id:
                    raise ValueError('delegate run binding mismatch')
                boundary = await self._persist_boundary(request, ReactCommandBoundary(run_id=request.run_id, session_id=request.session_id, command_id=command.command_id, command_kind='delegate', canonical_messages=request.canonical_messages, session_projection_cursor=request.session_projection_cursor, prepared_context_ref=request.prepared_context_ref, tool_set_snapshot_ref=request.tool_set_snapshot_ref, pending_calls=(), tool_contexts=(), outcomes=(), provider_state=request.provider_state, iteration=request.iteration, completion_state=request.completion_state, capability_snapshot=request.capability_snapshot, request_payload=request.request_payload, run_context=request.run_context, run_spec=request.run_spec, pending_delegate=command, version=continuation_version, active_skill_scope_ids=request.active_skill_scope_ids, activated_skill_scopes=request.activated_skill_scopes), continuation_version=continuation_version, recovery_lease=recovery_lease)
                yield command
                return
            elif isinstance(emission, ReactFinal):
                yield DriverTerminalCandidate(request.run_id, 'completed', emission.content)
                return
            elif isinstance(emission, ReactFailure):
                yield DriverTerminalCandidate(
                    request.run_id,
                    'failed',
                    error=emission.error,
                    correlation={
                        "failure_layer": emission.source_layer,
                        "failure_code": emission.error_code,
                    },
                )
                return

    def _execute_command(
        self,
        boundary: ReactCommandBoundary,
        *,
        selected_indexes: tuple[int, ...] | None = None,
    ) -> DriverEvent:
        indexes = (
            boundary.provider_execution_indexes
            if selected_indexes is None
            else selected_indexes
        )
        if indexes:
            calls = tuple(boundary.pending_calls[index] for index in indexes)
            contexts = tuple(boundary.tool_contexts[index] for index in indexes)
            effectful = tuple(
                index in boundary.durable_indexes for index in indexes
            )
        else:
            brokered = tuple(
                (command.parent_index, inner)
                for command in boundary.active_brokered_commands
                if (inner := self._brokered_inner(command)) is not None
            )
            indexes = tuple(item[0] for item in brokered)
            calls = tuple(item[1][0] for item in brokered)
            contexts = tuple(item[1][1] for item in brokered)
            effectful = tuple(
                self._tool_registry.prepared_execution_policy(call)[1]
                for call in calls
            )
        if not calls:
            raise ValueError("react boundary has no executable tool calls")
        grants = ReActDriver._grant_state(boundary)
        grant_refs = []
        for call in calls:
            value = grants.get(call.stable_call_id)
            grant_refs.append(None if value is None else ToolGrantRef(grant_id=str(value['grant_id']), decision_id=str(value['decision_id']), decision_nonce=str(value['decision_nonce']), version=int(value.get('version', 0))))
        confirm_only = tuple(
            call.tool_name in boundary.confirm_only_names for call in calls
        )
        return ExecuteTools(run_id=boundary.run_id, command_id=boundary.command_id, calls=calls, contexts=contexts, original_indexes=indexes, grant_refs=tuple(grant_refs) if any((item is not None for item in grant_refs)) else (), effectful=effectful, confirm_only=confirm_only, confirm_only_snapshot_ref=boundary.confirm_only_snapshot_ref, confirm_only_snapshot_hash=boundary.confirm_only_snapshot_hash)

    async def _continue_after_tool_progress(
        self,
        boundary: ReactCommandBoundary,
        *,
        recovery_lease: RecoveryLease | None = None,
    ) -> AsyncIterator[DriverEvent]:
        if boundary.provider_execution_indexes:
            external_wait_index = self._external_wait_index(boundary)
            executable_indexes = tuple(
                index
                for index in boundary.provider_execution_indexes
                if boundary.pending_calls[index].tool_name
                not in {EXTERNAL_ACTION_WAIT, PROJECT_DIRECTORY_SELECT}
            )
            if executable_indexes:
                yield self._execute_command(
                    boundary, selected_indexes=executable_indexes
                )
                return
            if external_wait_index is not None:
                _, decision = await self._stage_external_wait_boundary(
                    boundary,
                    external_wait_index,
                    recovery_lease=recovery_lease,
                )
                yield decision
                return
            yield self._execute_command(boundary)
            return
        advanced = self._advance_brokered_actions(boundary)
        if advanced != boundary:
            advanced = replace(advanced, version=boundary.version + 1)
            await self._save_progress(
                advanced, recovery_lease=recovery_lease
            )
            boundary = advanced
        decision = self._next_permission_after_progress(boundary)
        if decision is not None:
            updated = replace(
                boundary,
                pending_decision=decision,
                version=boundary.version + 1,
            )
            boundary = await self._save_durable_boundary(
                updated,
                decision=decision,
                recovery_lease=recovery_lease,
            )
            if self._auto_mode_enabled():
                async for candidate in self._auto_allow_permission(
                    boundary, decision
                ):
                    yield candidate
                return
            yield decision
            return
        if boundary.active_brokered_commands:
            yield self._execute_command(boundary)
            return
        async for candidate in self._resume_completed(
            boundary, recovery_lease=recovery_lease
        ):
            yield candidate

    async def _bootstrap_root_task_context(
        self, request: DriverStart
    ) -> int:
        """Commit root-local identity before the first provider call."""

        payload = request.request_payload
        work_raw = payload.get("task_work_context")
        conversation_raw = payload.get("conversation_boundary")
        projection_raw = payload.get("task_run_projection")
        supplied = tuple(
            value is not None
            for value in (work_raw, conversation_raw, projection_raw)
        )
        if not any(supplied):
            return 0
        if not all(supplied) or not all(
            isinstance(value, Mapping)
            for value in (work_raw, conversation_raw, projection_raw)
        ):
            raise RuntimeError("root task context contract is incomplete")
        assert isinstance(work_raw, Mapping)
        assert isinstance(conversation_raw, Mapping)
        assert isinstance(projection_raw, Mapping)
        work_context = TaskWorkContext(
            session_id=str(work_raw.get("session_id") or ""),
            root_run_id=str(work_raw.get("root_run_id") or ""),
            task_scope_id=str(work_raw.get("task_scope_id") or ""),
            workspace_root=(
                str(work_raw["workspace_root"])
                if work_raw.get("workspace_root") is not None
                else None
            ),
            workspace_source=str(
                work_raw.get("workspace_source") or "none"
            ),
            binding_version=int(work_raw.get("binding_version") or 1),
        )
        conversation = ConversationBoundary(
            boundary_ref=str(conversation_raw.get("boundary_ref") or ""),
            session_id=str(conversation_raw.get("session_id") or ""),
            root_run_id=str(conversation_raw.get("root_run_id") or ""),
            task_scope_id=str(conversation_raw.get("task_scope_id") or ""),
            seed_message_refs=tuple(
                str(item)
                for item in conversation_raw.get("seed_message_refs", ())
            ),
            continuation_message_refs=tuple(
                str(item)
                for item in conversation_raw.get(
                    "continuation_message_refs", ()
                )
            ),
            version=int(conversation_raw.get("version") or 1),
        )
        projection = TaskRunProjection(
            projection_id=str(projection_raw.get("projection_id") or ""),
            session_id=str(projection_raw.get("session_id") or ""),
            root_run_id=str(projection_raw.get("root_run_id") or ""),
            task_scope_id=str(projection_raw.get("task_scope_id") or ""),
            ui_state=str(projection_raw.get("ui_state") or "open"),
            version=int(projection_raw.get("version") or 0),
        )
        await self._uow.create_task_context(
            work_context, conversation, projection
        )
        existing = await self._uow.load_continuation(request.run_id)
        if existing is not None:
            return int(existing.version)
        initial = ReactCommandBoundary(
            run_id=request.run_id,
            session_id=request.session_id,
            command_id=f"root-start:{request.run_id}",
            command_kind="provider_start",
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
            capability_snapshot=request.capability_snapshot,
            request_payload=request.request_payload,
            run_context=request.run_context,
            run_spec=request.run_spec,
            version=0,
            active_skill_scope_ids=request.active_skill_scope_ids,
            activated_skill_scopes=request.activated_skill_scopes,
        )
        saved = await self._persist_boundary(request, initial)
        return saved.version

    def start(self, request: DriverStart) -> AsyncIterator[DriverEvent]:

        async def iterator() -> AsyncIterator[DriverEvent]:
            continuation_version = await self._bootstrap_root_task_context(
                request
            )
            async for candidate in self._emit(
                request,
                self._collaborator.start(request),
                continuation_version=continuation_version,
            ):
                yield candidate
        return iterator()

    async def _load_boundary(self, run_id: str) -> ReactCommandBoundary:
        record = await self._uow.load_continuation(run_id)
        boundary = None if record is None else ReactCommandBoundary.from_record(record)
        if boundary is None:
            boundary = self._live_boundary(run_id)
        if boundary is None:
            raise ValueError('react command boundary not found')
        if boundary.capability_snapshot_ref is not None:
            start_snapshot = await self._uow.read_run_start_snapshot(run_id)
            if start_snapshot is None:
                raise ValueError(
                    "react boundary has no trusted RunStart snapshot"
                )
            start_capabilities = json.loads(
                start_snapshot.capability_snapshot_json
            )
            if (
                fingerprint_json(start_capabilities)
                != start_snapshot.capability_snapshot_hash
            ):
                raise ValueError(
                    "react boundary capability snapshot drifted from RunStart"
                )
            if start_capabilities != dict(boundary.capability_snapshot):
                self._verify_tool_activation_snapshot_evolution(
                    start_snapshot=start_snapshot,
                    start_capabilities=start_capabilities,
                    boundary=boundary,
                )
            if any(
                context.capability_snapshot_ref
                != boundary.capability_snapshot_ref
                for context in boundary.tool_contexts
            ):
                raise ValueError(
                    "tool context capability snapshot ref drifted"
                )
        await self._verify_skill_scope_activation_receipts(boundary)
        return boundary

    @staticmethod
    def _verify_tool_activation_snapshot_evolution(
        *,
        start_snapshot: Any,
        start_capabilities: Mapping[str, Any],
        boundary: ReactCommandBoundary,
    ) -> None:
        """Accept only receipt-proven Context OS activation after RunStart.

        The Run catalog and its lease remain immutable.  ``tool_activate`` may
        only advance the request-local PreparedToolSet, so every changed
        presentation field must be derivable by replaying the durable
        activation receipts from the RunStart Context OS snapshot.
        """

        current_capabilities = dict(boundary.capability_snapshot)
        dynamic_keys = frozenset(
            {"prepared_tool_set_ref", "capabilities", "tools"}
        )
        immutable_start = {
            key: value
            for key, value in start_capabilities.items()
            if key not in dynamic_keys
        }
        immutable_current = {
            key: value
            for key, value in current_capabilities.items()
            if key not in dynamic_keys
        }
        if immutable_start != immutable_current:
            raise ValueError(
                "react boundary capability snapshot drifted from RunStart"
            )

        try:
            sanitized = json.loads(start_snapshot.sanitized_request_json)
            start_payload = sanitized["payload"]
            start_context_os = start_payload["context_os"]
            current_context_os = boundary.request_payload["context_os"]
            from deskpet.capabilities.refresh import context_os_snapshot_ref
            from deskpet.tools.capabilities import PreparedToolCapability
            from deskpet.tools.prepared_snapshot import (
                dump_context_os_snapshot,
                load_context_os_snapshot,
            )

            expected, expected_eligibility = load_context_os_snapshot(
                start_context_os
            )
            current, current_eligibility = load_context_os_snapshot(
                current_context_os
            )
            if expected_eligibility != current_eligibility:
                raise ValueError("tool activation eligibility drifted")
            start_ref = context_os_snapshot_ref(
                expected, expected_eligibility
            )
            if (
                str(start_capabilities.get("prepared_tool_set_ref") or "")
                != start_ref
                or str(start_payload.get("tool_set_snapshot_ref") or "")
                != start_ref
            ):
                raise ValueError("RunStart Context OS snapshot ref mismatch")

            raw_receipts = boundary.completion_state.get(
                "tool_activation_receipts"
            )
            if not isinstance(raw_receipts, Mapping) or not raw_receipts:
                raise ValueError("tool activation receipts are unavailable")
            receipts = sorted(
                (dict(item) for item in raw_receipts.values()),
                key=lambda item: int(item["base_scope_revision"]),
            )
            for receipt in receipts:
                base_revision = int(receipt["base_scope_revision"])
                scope_revision = int(receipt["scope_revision"])
                if (
                    base_revision != expected.revision
                    or scope_revision != base_revision + 1
                    or str(receipt["scope_id"]) != expected.scope_id
                ):
                    raise ValueError("tool activation revision chain drifted")
                capability_id = str(receipt["capability_id"])
                tool_name = str(receipt["tool_name"])
                schema_hash = str(receipt["schema_hash"])
                deferred = next(
                    (
                        item
                        for item in expected.deferred
                        if item.capability_id == capability_id
                        and item.name == tool_name
                        and item.schema_hash == schema_hash
                    ),
                    None,
                )
                materialized = next(
                    (
                        item
                        for item in (*current.direct, *current.activated)
                        if item.ref.capability_id == capability_id
                        and item.ref.name == tool_name
                        and item.ref.schema_hash == schema_hash
                    ),
                    None,
                )
                if deferred is None or materialized is None:
                    raise ValueError("tool activation receipt target drifted")
                expected = expected.activate(
                    PreparedToolCapability(
                        deferred, materialized.schema_copy()
                    )
                )
                receipt_ref = context_os_snapshot_ref(
                    expected, expected_eligibility
                )
                if str(receipt["tool_set_snapshot_ref"]) != receipt_ref:
                    raise ValueError("tool activation receipt ref drifted")

            final_context_os = dump_context_os_snapshot(
                expected, expected_eligibility
            )
            final_ref = context_os_snapshot_ref(
                expected, expected_eligibility
            )
            if (
                final_context_os != current_context_os
                or expected != current
                or str(boundary.tool_set_snapshot_ref or "") != final_ref
                or str(
                    boundary.request_payload.get("tool_set_snapshot_ref")
                    or ""
                )
                != final_ref
                or str(
                    current_capabilities.get("prepared_tool_set_ref") or ""
                )
                != final_ref
            ):
                raise ValueError("tool activation final snapshot drifted")
            visible_names = sorted(
                item.ref.name
                for item in (*expected.direct, *expected.activated)
            )
            for key in ("capabilities", "tools"):
                if key in current_capabilities and sorted(
                    str(item) for item in current_capabilities[key]
                ) != visible_names:
                    raise ValueError(
                        "tool activation visible ToolSet drifted"
                    )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "react boundary capability snapshot drifted from RunStart"
            ) from exc

    @staticmethod
    def _activation_receipt_hash(
        values: Mapping[str, Any],
    ) -> str:
        return fingerprint_json(
            {
                "schema": "skill-scope-activation-receipt-v1",
                **{
                    key: copy.deepcopy(value)
                    for key, value in values.items()
                    if key != "receipt_hash"
                },
            }
        )

    @staticmethod
    def _activation_scope_from_outcome(
        boundary: ReactCommandBoundary,
        outcome: NormalizedToolOutcome,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
        if outcome.state is not ToolOutcomeState.SUCCESS:
            return None
        value = outcome.value
        if not isinstance(value, Mapping) or value.get("ok") is not True:
            return None
        activation = value.get("scope_activation")
        if not isinstance(activation, Mapping):
            raise RuntimeError(
                "successful skill_invoke lacks a scope activation receipt"
            )
        required_scope = (
            "owner_key",
            "pack_id",
            "skill_id",
            "version",
            "manifest_hash",
            "content_hash",
            "instruction",
            "allowed_tools",
            "scope_id",
            "scope_hash",
            "allowed_tool_refs",
        )
        if any(name not in value for name in required_scope):
            raise RuntimeError(
                "successful skill_invoke scope identity is incomplete"
            )
        allowed_tools = value["allowed_tools"]
        allowed_refs = value["allowed_tool_refs"]
        if (
            isinstance(allowed_tools, (str, bytes))
            or not isinstance(allowed_tools, (list, tuple))
            or any(not isinstance(item, str) for item in allowed_tools)
            or isinstance(allowed_refs, (str, bytes))
            or not isinstance(allowed_refs, (list, tuple))
            or any(not isinstance(item, Mapping) for item in allowed_refs)
        ):
            raise RuntimeError(
                "successful skill_invoke exact ToolRefs are malformed"
            )
        scope = {
            name: str(value[name])
            for name in (
                "owner_key",
                "pack_id",
                "skill_id",
                "version",
                "manifest_hash",
                "content_hash",
                "scope_id",
                "scope_hash",
            )
        }
        scope.update(
            {
                "allowed_tools": sorted(set(allowed_tools)),
                "allowed_tool_refs": sorted(
                    (
                        dict(thaw_json(item))
                        for item in allowed_refs
                    ),
                    key=lambda item: json.dumps(
                        item,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
                "activation_id": str(
                    activation.get("activation_id") or ""
                ),
                "instruction_content_hash": instruction_content_hash(
                    str(value["instruction"])
                ),
            }
        )
        snapshot_ref = str(boundary.capability_snapshot_ref or "")
        content_stamp = str(
            boundary.capability_snapshot.get(
                "run_catalog_content_stamp"
            )
            or ""
        )
        root_run_id = (
            boundary.run_context.root_run_id
            if boundary.run_context is not None
            else boundary.run_id
        )
        expected_activation = {
            "activation_id": scope["activation_id"],
            "run_id": boundary.run_id,
            "root_run_id": root_run_id,
            "scope_id": scope["scope_id"],
            "scope_hash": scope["scope_hash"],
            "capability_snapshot_ref": snapshot_ref,
            "run_catalog_content_stamp": content_stamp,
            "allowed_tool_names": sorted(set(allowed_tools)),
            "allowed_tool_refs": sorted(
                (
                    dict(thaw_json(item))
                    for item in allowed_refs
                ),
                key=lambda item: json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
            "effective_tool_ref_hashes": sorted(
                set(
                    str(item)
                    for item in activation.get(
                        "effective_tool_ref_hashes", ()
                    )
                )
            ),
            "effective_tool_refs_hash": str(
                activation.get("effective_tool_refs_hash") or ""
            ),
            "instruction_content_hash": scope[
                "instruction_content_hash"
            ],
        }
        for name, expected in expected_activation.items():
            actual = activation.get(name)
            if name in {
                "allowed_tool_names",
                "allowed_tool_refs",
                "effective_tool_ref_hashes",
            }:
                actual = thaw_json(actual)
            if actual != expected:
                raise RuntimeError(
                    f"skill scope activation field drifted: {name}"
                )
        intersection = _skill_tool_intersection(
            boundary.capability_snapshot,
            boundary.request_payload,
            active_scope_ids=(str(scope["scope_id"]),),
            activated_scopes=(scope,),
        )
        if intersection is None:
            raise RuntimeError("skill scope activation has no ToolRef boundary")
        if (
            tuple(expected_activation["allowed_tool_names"])
            != intersection.allowed_tool_names
            or tuple(expected_activation["effective_tool_ref_hashes"])
            != tuple(sorted(intersection.effective_tool_ref_hashes))
            or expected_activation["effective_tool_refs_hash"]
            != intersection.effective_tool_refs_hash
        ):
            raise RuntimeError(
                "skill scope activation differs from captured ToolRefs"
            )
        activation_identity = {
            "schema": "skill-scope-activation/v1",
            "run_id": boundary.run_id,
            "scope_id": scope["scope_id"],
            "scope_hash": scope["scope_hash"],
            "capability_snapshot_ref": snapshot_ref,
            "instruction_content_hash": scope[
                "instruction_content_hash"
            ],
            "effective_tool_refs_hash": intersection.effective_tool_refs_hash,
        }
        if scope["activation_id"] != (
            "skill-scope-activation:"
            + fingerprint_json(activation_identity)
        ):
            raise RuntimeError("skill scope activation id drifted")
        return MappingProxyType(scope), MappingProxyType(
            expected_activation
        )

    async def _verify_skill_scope_activation_receipts(
        self, boundary: ReactCommandBoundary
    ) -> None:
        for scope in boundary.activated_skill_scopes:
            activation_id = str(scope.get("activation_id") or "")
            if not activation_id:
                raise ValueError(
                    "activated Skill scope has no durable activation id"
                )
            row = await self._uow.get_skill_scope_activation(
                activation_id
            )
            if row is None:
                raise ValueError(
                    "activated Skill scope has no committed receipt"
                )
            allowed_refs = json.loads(
                str(row["allowed_tool_refs_json"])
            )
            ref_hashes = json.loads(
                str(row["effective_tool_ref_hashes_json"])
            )
            allowed_names = json.loads(
                str(row["allowed_tool_names_json"])
            )
            expected = {
                "activation_id": activation_id,
                "run_id": boundary.run_id,
                "scope_id": str(scope.get("scope_id") or ""),
                "scope_hash": str(scope.get("scope_hash") or ""),
                "capability_snapshot_ref": str(
                    boundary.capability_snapshot_ref or ""
                ),
                "run_catalog_content_stamp": str(
                    boundary.capability_snapshot.get(
                        "run_catalog_content_stamp"
                    )
                    or ""
                ),
                "instruction_content_hash": str(
                    scope.get("instruction_content_hash") or ""
                ),
                "status": "committed",
            }
            if any(str(row[name]) != value for name, value in expected.items()):
                raise ValueError(
                    "activated Skill scope receipt identity drifted"
                )
            if (
                allowed_refs
                != [
                    copy.deepcopy(dict(item))
                    for item in scope.get("allowed_tool_refs", ())
                ]
                or allowed_names
                != list(scope.get("allowed_tools", ()))
                or int(row["continuation_version"]) > boundary.version
            ):
                raise ValueError(
                    "activated Skill scope receipt payload drifted"
                )
            intersection = _skill_tool_intersection(
                boundary.capability_snapshot,
                boundary.request_payload,
                active_scope_ids=(expected["scope_id"],),
                activated_scopes=(scope,),
            )
            if (
                intersection is None
                or ref_hashes
                != sorted(intersection.effective_tool_ref_hashes)
                or str(row["effective_tool_refs_hash"])
                != intersection.effective_tool_refs_hash
            ):
                raise ValueError(
                    "activated Skill scope receipt ToolRefs drifted"
                )
            receipt_values = {
                name: row[name]
                for name in (
                    "activation_id",
                    "run_id",
                    "root_run_id",
                    "scope_id",
                    "scope_hash",
                    "capability_snapshot_ref",
                    "run_catalog_content_stamp",
                    "effective_tool_refs_hash",
                    "instruction_content_hash",
                    "boundary_ref",
                    "boundary_hash",
                    "status",
                    "receipt_ref",
                    "receipt_hash",
                )
            }
            receipt_values.update(
                {
                    "allowed_tool_names": allowed_names,
                    "allowed_tool_refs": allowed_refs,
                    "effective_tool_ref_hashes": ref_hashes,
                    "continuation_version": int(
                        row["continuation_version"]
                    ),
                }
            )
            if str(row["receipt_hash"]) != self._activation_receipt_hash(
                receipt_values
            ):
                raise ValueError(
                    "activated Skill scope receipt hash drifted"
                )

    async def _commit_skill_scope_activation(
        self,
        boundary: ReactCommandBoundary,
        *,
        expected_continuation_version: int,
        scope: Mapping[str, Any],
        activation: Mapping[str, Any],
    ) -> ReactCommandBoundary:
        scope_id = str(scope["scope_id"])
        existing = {
            str(item.get("scope_id") or ""): item
            for item in boundary.activated_skill_scopes
        }
        if scope_id in existing and dict(existing[scope_id]) != dict(scope):
            raise RuntimeError("dynamic Skill scope identity conflicts")
        activated = tuple(
            (
                *boundary.activated_skill_scopes,
                *(() if scope_id in existing else (scope,)),
            )
        )
        active = tuple(
            sorted(
                dict.fromkeys(
                    (*boundary.active_skill_scope_ids, scope_id)
                )
            )
        )
        proposed = replace(
            boundary,
            active_skill_scope_ids=active,
            activated_skill_scopes=activated,
            effective_skill_tool_refs_hash=None,
            version=expected_continuation_version + 1,
        )
        boundary_ref = (
            f"react-continuation:{boundary.run_id}:"
            f"{expected_continuation_version + 1}"
        )
        values = {
            **copy.deepcopy(dict(activation)),
            "continuation_version": expected_continuation_version + 1,
            "boundary_ref": boundary_ref,
            "boundary_hash": fingerprint_json(proposed.to_payload()),
            "status": "committed",
            "receipt_ref": (
                f"{activation['activation_id']}:receipt"
            ),
        }
        values["receipt_hash"] = self._activation_receipt_hash(values)
        try:
            _receipt, saved = (
                await self._uow.commit_skill_scope_activation(
                    boundary.run_id,
                    expected_continuation_version=(
                        expected_continuation_version
                    ),
                    continuation_payload=proposed.to_payload(),
                    activation_values=values,
                )
            )
            return replace(proposed, version=int(saved.version))
        except Exception:
            committed = await self._uow.get_skill_scope_activation(
                str(activation["activation_id"])
            )
            if committed is None:
                raise
            record = await self._uow.load_continuation(boundary.run_id)
            if (
                record is None
                or int(record.version)
                < expected_continuation_version + 1
            ):
                raise
            recovered = ReactCommandBoundary.from_record(record)
            if not any(
                str(item.get("activation_id") or "")
                == str(activation["activation_id"])
                for item in recovered.activated_skill_scopes
            ):
                raise
            await self._verify_skill_scope_activation_receipts(recovered)
            return recovered

    async def _save_progress(self, boundary: ReactCommandBoundary, *, recovery_lease: RecoveryLease | None=None) -> None:
        volatile = self._live_boundary(boundary.run_id)
        if volatile is not None:
            active = self._bound_live().get(boundary.run_id)
            if active is None or active.driver_state is not volatile:
                raise RuntimeError('ReAct live boundary changed while saving progress')
            active.driver_state = boundary
        else:
            await self._save_durable_boundary(boundary, recovery_lease=recovery_lease)

    async def _completion_evidence(self, boundary: ReactCommandBoundary) -> EvidenceSelection:
        if boundary.raw_failures or len(boundary.outcomes) != 1 or boundary.outcomes[0] is None or boundary.outcome_statuses[0] is not OutcomeStatus.SUCCEEDED:
            return UNKNOWN_EVIDENCE
        call, context, outcome = boundary.pending_calls[0], boundary.tool_contexts[0], boundary.outcomes[0]
        artifacts = tuple(str(item) for item in boundary.outcome_metadata[0].get('artifact_refs', ()))
        identity = EvidenceContext(run_id=boundary.run_id, turn_id=context.turn_id, call_id=call.stable_call_id, effect_id=context.effect_id, artifact_ref=artifacts[0] if artifacts else None)
        return await self._uow.lookup_completion_evidence(identity)

    @staticmethod
    def _stage_failure_reports(
        boundary: ReactCommandBoundary,
        tool_registry: Any | None = None,
    ) -> ReactCommandBoundary:
        failed_prepared = tuple(
            index
            for index, outcome in enumerate(boundary.outcomes)
            if outcome is not None
            and outcome.state is not ToolOutcomeState.SUCCESS
        )
        raw_failed = tuple(dict(item) for item in boundary.raw_failures)
        if not failed_prepared and not raw_failed:
            state = copy.deepcopy(dict(boundary.completion_state))
            state.pop("capability_retry_inflight", None)
            if state.get("current_attempt"):
                state["current_attempt"] = {
                    **dict(state["current_attempt"]),
                    "status": "succeeded",
                }
                state["attempt_status"] = "succeeded"
                return replace(boundary, completion_state=state)
            return boundary
        state = copy.deepcopy(dict(boundary.completion_state))
        current = dict(state.get("current_attempt") or {})
        if (
            state.get("failure_report_command_id") == boundary.command_id
            or not current.get("attempt_id")
        ):
            return boundary
        plan_version = max(1, int(current.get("plan_version") or 1))
        strategy = str(current.get("strategy_fingerprint") or "")
        prior = tuple(
            str(item)
            for item in state.get("prior_strategy_fingerprints", ())
            if str(item)
        )
        strategy_history = tuple(dict.fromkeys((*prior, strategy)))
        failed_attempt_status = str(current.get("status") or "failed")
        if failed_attempt_status not in {"failed", "rejected", "blocked"}:
            failed_attempt_status = "failed"
        attempt = AttemptRecord(
            attempt_id=str(current["attempt_id"]),
            root_run_id=(
                boundary.run_context.root_run_id
                if boundary.run_context is not None
                else boundary.run_id
            ),
            run_id=boundary.run_id,
            provider_turn_id=str(
                current.get("provider_turn_id") or boundary.command_id
            ),
            provider_batch_id=str(
                current.get("provider_batch_id") or boundary.command_id
            ),
            plan_version=plan_version,
            trigger_failure_set_id=(
                str(state["latest_failure_set_id"])
                if state.get("latest_failure_set_id")
                else None
            ),
            supersedes_attempt_id=(
                str(state["latest_attempt_id"])
                if state.get("latest_attempt_id")
                else None
            ),
            strategy_fingerprint=strategy,
            planned_call_refs=tuple(
                str(item) for item in current.get("planned_call_refs", ())
            ),
            checkpoint_ref=boundary.prepared_context_ref,
            status=failed_attempt_status,  # type: ignore[arg-type]
            budget_eligible=bool(current.get("budget_eligible", True)),
        )
        reports = []
        metadata = [dict(item) for item in boundary.outcome_metadata]
        task_scope_id = str(
            boundary.request_payload.get("task_scope_id")
            or boundary.request_payload.get("root_run_id")
            or attempt.root_run_id
        )
        for index in failed_prepared:
            call = boundary.pending_calls[index]
            context = boundary.tool_contexts[index]
            outcome = boundary.outcomes[index]
            assert outcome is not None and outcome.error is not None
            error = dict(outcome.error)
            error_code = str(error.get("code") or "tool_failed")
            try:
                if tool_registry is None:
                    raise KeyError("tool registry unavailable")
                prepared_spec = tool_registry.resolve_prepared_spec(call)
                capability_source = parse_capability_registry_source(
                    str(prepared_spec.source)
                )
            except Exception:  # noqa: BLE001 - stale provenance fails closed below
                capability_source = None
            source_kind = (
                "child_terminal"
                if metadata[index].get("child_run_id")
                else "tool_authorization"
                if error_code == "authorization_denied"
                else "strategy_rejected"
                if error_code == "replan_required"
                else "tool_executor"
            )
            action_fingerprint = ReActDriver._prepared_action_fingerprint(call)
            source_identity = (
                f"capability:{capability_source[0]}:{call.tool_name}"
                if capability_source is not None
                else f"{call.tool_name}@{call.tool_spec_version}"
            )
            report = FailureReportIssuer.issue(
                root_run_id=attempt.root_run_id,
                run_id=boundary.run_id,
                task_scope_id=task_scope_id,
                attempt_id=attempt.attempt_id,
                plan_version=plan_version,
                call_record_id=(
                    "provider-call:"
                    + hashlib.sha256(
                        f"{boundary.run_id}|{call.stable_call_id}".encode()
                    ).hexdigest()
                ),
                source_kind=source_kind,
                source_identity=source_identity,
                provider_call_id=call.stable_call_id,
                child_run_id=(
                    str(metadata[index]["child_run_id"])
                    if metadata[index].get("child_run_id")
                    else None
                ),
                failed_call_id=call.stable_call_id,
                failed_effect_id=context.effect_id,
                failed_step=call.tool_name,
                error_code=error_code,
                error_class=(
                    "capability" if capability_source is not None else None
                ),
                action_fingerprint=action_fingerprint,
                artifact_refs=tuple(
                    str(item)
                    for item in metadata[index].get("artifact_refs", ())
                ),
                checkpoint_ref=boundary.prepared_context_ref,
                prior_strategy_fingerprints=strategy_history,
            )
            reports.append(report)
            if capability_source is not None:
                metadata[index]["capability_failure_provenance"] = {
                    "capability_id": capability_source[0],
                    "pack_version": capability_source[1],
                    "manifest_hash": capability_source[2],
                    "tool_spec_fingerprint": call.tool_spec_fingerprint,
                }
        for failure in raw_failed:
            report = FailureReportIssuer.issue(
                root_run_id=attempt.root_run_id,
                run_id=boundary.run_id,
                task_scope_id=task_scope_id,
                attempt_id=attempt.attempt_id,
                plan_version=plan_version,
                call_record_id=str(failure["call_record_id"]),
                source_kind=str(failure["source_kind"]),  # type: ignore[arg-type]
                source_identity=str(failure["source_identity"]),
                provider_call_id=str(failure["provider_call_id"]),
                failed_call_id=str(failure["provider_call_id"]),
                failed_step=str(failure["failed_step"]),
                error_code=str(failure["error_code"]),
                action_fingerprint=str(failure["action_fingerprint"]),
                checkpoint_ref=boundary.prepared_context_ref,
                prior_strategy_fingerprints=strategy_history,
            )
            reports.append(report)
        failure_set = AttemptFailureSet.from_reports(attempt, reports)
        by_ref = {report.report_ref: report for report in reports}
        for index in failed_prepared:
            call_id = boundary.pending_calls[index].stable_call_id
            report = next(
                report
                for report in reports
                if report.provider_call_id == call_id
            )
            metadata[index].update(
                {
                    "failure_report_ref": report.report_ref,
                    "failure_report": report.to_dict(),
                    "failure_set_id": failure_set.failure_set_id,
                }
            )
        reports_by_call = {
            str(report.provider_call_id): report
            for report in reports
            if report.provider_call_id is not None
        }
        enriched_raw = []
        for failure in raw_failed:
            report = reports_by_call[str(failure["provider_call_id"])]
            failure.update(
                {
                    "failure_report_ref": report.report_ref,
                    "failure_report": report.to_dict(),
                    "failure_set_id": failure_set.failure_set_id,
                }
            )
            enriched_raw.append(failure)
        state.update(
            {
                "failure_report_command_id": boundary.command_id,
                "failure_reports": [
                    by_ref[ref].to_dict() for ref in failure_set.report_refs
                ],
                "latest_failure_set_id": failure_set.failure_set_id,
                "latest_attempt_id": attempt.attempt_id,
                "attempt_status": failed_attempt_status,
                "current_attempt": {
                    **current,
                    "status": failed_attempt_status,
                    "failure_set_id": failure_set.failure_set_id,
                },
                "prior_strategy_fingerprints": list(
                    strategy_history
                )[-3:],
            }
        )
        return replace(
            boundary,
            completion_state=state,
            outcome_metadata=tuple(metadata),
            raw_failures=tuple(enriched_raw),
        )

    async def _persist_failure_set(
        self, boundary: ReactCommandBoundary
    ) -> ReactCommandBoundary:
        """Commit trusted failure facts before any provider-visible backfill."""

        failure_report_command_id = str(
            boundary.completion_state.get("failure_report_command_id") or ""
        )
        if failure_report_command_id != boundary.command_id:
            return boundary
        failure_set_id = str(
            boundary.completion_state.get("latest_failure_set_id") or ""
        )
        if not failure_set_id:
            return boundary
        reports = tuple(
            TaskFailureReport.from_dict(item)
            for item in boundary.completion_state.get("failure_reports", ())
            if isinstance(item, Mapping)
        )
        if not reports:
            raise RuntimeError("failure set identity exists without failure reports")
        reports_by_call = {
            str(report.provider_call_id): report
            for report in reports
            if report.provider_call_id is not None
        }
        ordered = tuple(
            reports_by_call[provider_call_id]
            for provider_call_id in boundary.provider_call_order
            if provider_call_id in reports_by_call
        )
        if len(ordered) != len(reports):
            raise RuntimeError("failure reports do not match the provider call order")
        durable_reports = tuple(
            DurableTaskFailureReport(**report.to_dict()) for report in ordered
        )
        await self._uow.stage_attempt_failure_set(
            DurableAttemptFailureSet(
                failure_set_id=failure_set_id,
                root_run_id=durable_reports[0].root_run_id,
                failed_attempt_id=durable_reports[0].attempt_id,
                report_refs=tuple(
                    report.report_ref for report in durable_reports
                ),
                primary_report_ref=durable_reports[0].report_ref,
                backfill_state="ready",
                provider_resume_state="pending",
            ),
            durable_reports,
        )
        builder_host = self._capability_builder_host
        capability_store = (
            None if builder_host is None else getattr(builder_host, "store", None)
        )
        if capability_store is None:
            return boundary
        metadata = [dict(item) for item in boundary.outcome_metadata]
        state = copy.deepcopy(dict(boundary.completion_state))
        receipt_refs: list[str] = []
        for index, outcome in enumerate(boundary.outcomes):
            if (
                outcome is None
                or outcome.state is ToolOutcomeState.SUCCESS
                or not isinstance(
                    metadata[index].get("capability_failure_provenance"),
                    Mapping,
                )
            ):
                continue
            provenance = dict(
                metadata[index]["capability_failure_provenance"]
            )
            call = boundary.pending_calls[index]
            context = boundary.tool_contexts[index]
            report = reports_by_call.get(call.stable_call_id)
            if report is None:
                raise RuntimeError(
                    "capability failure has no trusted task failure report"
                )
            error = dict(outcome.error or {})
            try:
                receipt = await capability_store.issue_failure_receipt(
                    root_run_id=report.root_run_id,
                    run_id=boundary.run_id,
                    attempt_id=report.attempt_id,
                    failure_report_ref=report.report_ref,
                    provider_call_id=call.stable_call_id,
                    effect_id=context.effect_id,
                    capability_id=str(provenance["capability_id"]),
                    pack_version=str(provenance["pack_version"]),
                    manifest_hash=str(provenance["manifest_hash"]),
                    tool_name=call.tool_name,
                    tool_spec_fingerprint=call.tool_spec_fingerprint,
                    canonical_args=call.arguments_json(),
                    error_code=str(error.get("code") or "tool_failed"),
                    error_fingerprint=report.error_fingerprint,
                    evidence_refs=(
                        report.report_ref,
                        *report.artifact_refs,
                    ),
                )
            except CapabilityStoreError as exc:
                metadata[index]["capability_failure_receipt_error"] = {
                    "code": exc.code,
                    "message": str(exc),
                }
                continue
            metadata[index].update(
                {
                    "capability_failure_receipt_ref": receipt.receipt_ref,
                    "capability_failure_receipt": receipt.to_dict(),
                }
            )
            receipt_refs.append(receipt.receipt_ref)
        if not receipt_refs and metadata == [
            dict(item) for item in boundary.outcome_metadata
        ]:
            return boundary
        if receipt_refs:
            state["capability_failure_receipt_refs"] = list(
                dict.fromkeys(
                    (
                        *state.get("capability_failure_receipt_refs", ()),
                        *receipt_refs,
                    )
                )
            )
        return replace(
            boundary,
            completion_state=state,
            outcome_metadata=tuple(metadata),
        )

    @staticmethod
    def _capability_operation_receipt(
        outcome: NormalizedToolOutcome,
    ) -> Any | None:
        if outcome.state is not ToolOutcomeState.SUCCESS:
            return None
        value = thaw_json(outcome.value)
        if not isinstance(value, Mapping):
            return None
        raw = value.get("operation_receipt")
        if not isinstance(raw, Mapping):
            return None
        from deskpet.capabilities.refresh_contracts import (
            CapabilityOperationReceipt,
        )

        return CapabilityOperationReceipt.from_dict(raw)

    @staticmethod
    def _tool_activation_proposal(
        boundary: ReactCommandBoundary,
    ) -> tuple[int, Mapping[str, Any]] | None:
        """Return one trusted bridge proposal that still needs host commit."""

        if len(boundary.pending_calls) != 1 or len(boundary.outcomes) != 1:
            return None
        call = boundary.pending_calls[0]
        outcome = boundary.outcomes[0]
        if (
            call.tool_name != "tool_activate"
            or outcome is None
            or outcome.state is not ToolOutcomeState.SUCCESS
        ):
            return None
        value = thaw_json(outcome.value)
        if not isinstance(value, Mapping):
            return None
        if str(value.get("status") or "") == "activated":
            return None
        control = value.get("__deskpet_control")
        if not isinstance(control, Mapping):
            return None
        if str(control.get("kind") or "") != "tool_activation":
            return None
        return 0, control

    @staticmethod
    def _tool_activation_failure_boundary(
        boundary: ReactCommandBoundary,
        *,
        index: int,
        code: str,
        message: str,
    ) -> ReactCommandBoundary:
        outcomes = list(boundary.outcomes)
        statuses = list(boundary.outcome_statuses)
        metadata = [dict(item) for item in boundary.outcome_metadata]
        outcomes[index] = NormalizedToolOutcome.failure(
            code,
            message,
            value={
                "status": "activation_failed",
                "error": {"code": code, "message": message},
            },
        )
        statuses[index] = OutcomeStatus.FAILED
        metadata[index]["tool_activation_error"] = {
            "code": code,
            "message": message,
        }
        return replace(
            boundary,
            outcomes=tuple(outcomes),
            outcome_statuses=tuple(statuses),
            outcome_metadata=tuple(metadata),
            version=boundary.version + 1,
        )

    async def _consume_pending_tool_activation(
        self,
        boundary: ReactCommandBoundary,
        *,
        recovery_lease: RecoveryLease | None = None,
    ) -> ReactCommandBoundary:
        """Materialize ``tool_activate`` into this root Run's next tool set.

        The bridge handler can only validate and propose an activation.  The
        Driver owns the durable continuation, so it is the only layer allowed
        to replace ``request_payload.context_os`` and the exposed capability
        snapshot before the next provider turn.
        """

        pending = self._tool_activation_proposal(boundary)
        if pending is None:
            return boundary
        index, control = pending
        call = boundary.pending_calls[index]

        async def fail(code: str, message: str) -> ReactCommandBoundary:
            failed = self._tool_activation_failure_boundary(
                boundary,
                index=index,
                code=code,
                message=message,
            )
            await self._save_progress(
                failed, recovery_lease=recovery_lease
            )
            return failed

        scopes = self._capability_scope_store
        if scopes is None:
            return await fail(
                "tool_activation_runtime_unavailable",
                "capability scope store is unavailable",
            )
        refresh_service = self._capability_refresh_service
        snapshots = getattr(refresh_service, "snapshots", None)
        put_context_os = getattr(snapshots, "put_context_os", None)
        if not callable(put_context_os):
            return await fail(
                "tool_activation_snapshot_unavailable",
                "durable Context OS snapshot repository is unavailable",
            )
        raw_context = boundary.request_payload.get("context_os")
        if not isinstance(raw_context, Mapping):
            return await fail(
                "tool_activation_context_unavailable",
                "durable Context OS request snapshot is unavailable",
            )

        try:
            from deskpet.tools.prepared_snapshot import (
                dump_context_os_snapshot,
                load_context_os_snapshot,
            )

            durable_tool_set, eligibility = load_context_os_snapshot(
                raw_context
            )
            request_id = (
                boundary.run_context.request_id
                if boundary.run_context is not None
                else ""
            )
            if (
                eligibility.session_id != boundary.session_id
                or not request_id
                or eligibility.request_id != request_id
            ):
                raise RuntimeError("tool_activation_identity_mismatch")

            base_revision = int(control["base_scope_revision"])
            capability_id = str(control["capability_id"])
            schema_hash = str(control["schema_hash"])
            activation_nonce = str(control["nonce"])
            schema = control.get("schema")
            if not isinstance(schema, Mapping):
                raise RuntimeError("tool_activation_schema_malformed")
            if durable_tool_set.revision != base_revision:
                raise RuntimeError("tool_activation_revision_conflict")
            final_params = call.arguments_json()
            try:
                bound_capability_id = canonical_deferred_capability_id(
                    str(final_params.get("capability_id") or ""),
                    durable_tool_set.deferred,
                )
            except RuntimeError as exc:
                raise RuntimeError(
                    "tool_activation_call_binding_mismatch"
                ) from exc
            if (
                bound_capability_id != capability_id
                or str(final_params.get("schema_hash") or "") != schema_hash
                or str(final_params.get("describe_nonce") or "")
                != activation_nonce
            ):
                raise RuntimeError("tool_activation_call_binding_mismatch")
            ref = next(
                (
                    item
                    for item in durable_tool_set.deferred
                    if item.capability_id == capability_id
                ),
                None,
            )
            if ref is None or ref.schema_hash != schema_hash:
                raise RuntimeError("tool_activation_capability_stale")
            candidate = durable_tool_set.activate(
                PreparedToolCapability(ref, dict(schema))
            )
            validate = getattr(
                self._tool_registry, "validate_prepared_tool_set", None
            )
            if not callable(validate):
                raise RuntimeError("tool_activation_registry_unavailable")
            validate(candidate, eligibility=eligibility)
        except Exception as exc:  # noqa: BLE001 - returned to the same model
            return await fail(
                str(exc) or "tool_activation_invalid",
                f"{type(exc).__name__}: {exc}",
            )

        existing = scopes.get(
            candidate.scope_id,
            session_id=eligibility.session_id,
            request_id=eligibility.request_id,
        )
        if existing is None:
            try:
                scopes.open(durable_tool_set, eligibility)
            except ValueError:
                pass
        try:
            scope_lock = scopes.lock_for(candidate.scope_id)
        except KeyError:
            return await fail(
                "tool_activation_scope_unavailable",
                "request capability scope is unavailable",
            )

        async with scope_lock:
            current = scopes.get(
                candidate.scope_id,
                session_id=eligibility.session_id,
                request_id=eligibility.request_id,
            )
            if current is None:
                return await fail(
                    "tool_activation_scope_expired",
                    "request capability scope expired before activation",
                )
            if current.prepared.revision == candidate.revision:
                active = current.prepared.capability(ref.name)
                if (
                    active is None
                    or active.ref.capability_id != capability_id
                    or active.ref.schema_hash != schema_hash
                ):
                    return await fail(
                        "tool_activation_scope_conflict",
                        "capability scope advanced to a different tool set",
                    )
            elif current.prepared.revision != durable_tool_set.revision:
                return await fail(
                    "tool_activation_scope_conflict",
                    "capability scope changed before activation commit",
                )

            context_os = dump_context_os_snapshot(candidate, eligibility)
            snapshot_ref = fingerprint_json(context_os)
            try:
                persisted_ref = await put_context_os(
                    snapshot_ref, context_os
                )
            except Exception as exc:  # noqa: BLE001 - same-model failure
                return await fail(
                    "tool_activation_snapshot_failed",
                    f"{type(exc).__name__}: {exc}",
                )
            if persisted_ref != snapshot_ref:
                return await fail(
                    "tool_activation_snapshot_mismatch",
                    "snapshot repository returned a non-content-addressed reference",
                )

            visible_names = sorted(
                capability.ref.name
                for capability in (*candidate.direct, *candidate.activated)
            )
            capability_snapshot = copy.deepcopy(
                dict(boundary.capability_snapshot)
            )
            if "tools" in capability_snapshot:
                capability_snapshot["tools"] = visible_names
            capability_snapshot["capabilities"] = visible_names
            capability_snapshot["prepared_tool_set_ref"] = snapshot_ref

            request_payload = copy.deepcopy(dict(boundary.request_payload))
            request_payload["context_os"] = context_os
            request_payload["tool_set_snapshot_ref"] = snapshot_ref
            refresh = request_payload.get("capability_refresh")
            if isinstance(refresh, Mapping):
                refresh_payload = copy.deepcopy(dict(refresh))
                refresh_payload["tool_set_snapshot_ref"] = snapshot_ref
                request_payload["capability_refresh"] = refresh_payload

            receipt = {
                "schema_version": 1,
                "scope_id": candidate.scope_id,
                "base_scope_revision": durable_tool_set.revision,
                "scope_revision": candidate.revision,
                "capability_id": capability_id,
                "tool_name": ref.name,
                "schema_hash": schema_hash,
                "tool_set_snapshot_ref": snapshot_ref,
                "call_id": call.stable_call_id,
            }
            outcomes = list(boundary.outcomes)
            statuses = list(boundary.outcome_statuses)
            metadata = [dict(item) for item in boundary.outcome_metadata]
            outcomes[index] = NormalizedToolOutcome.success(
                {
                    "status": "activated",
                    "capability_id": capability_id,
                    "tool_name": ref.name,
                    "scope_revision": candidate.revision,
                    "activation_receipt": receipt,
                }
            )
            statuses[index] = OutcomeStatus.SUCCEEDED
            metadata[index]["tool_activation_receipt"] = receipt
            state = copy.deepcopy(dict(boundary.completion_state))
            applied = copy.deepcopy(
                dict(state.get("tool_activation_receipts") or {})
            )
            applied[call.stable_call_id] = receipt
            state["tool_activation_receipts"] = applied
            activated = replace(
                boundary,
                outcomes=tuple(outcomes),
                outcome_statuses=tuple(statuses),
                outcome_metadata=tuple(metadata),
                completion_state=state,
                capability_snapshot=capability_snapshot,
                request_payload=request_payload,
                tool_set_snapshot_ref=snapshot_ref,
                version=boundary.version + 1,
            )

            reset = getattr(self._collaborator, "reset_runtime", None)
            if not callable(reset):
                return await fail(
                    "tool_activation_runtime_reset_unavailable",
                    "AgentLoop runtime cannot be reset after tool activation",
                )
            try:
                await reset(boundary.run_id)
                await self._save_progress(
                    activated, recovery_lease=recovery_lease
                )
            except Exception:
                # The snapshot is content-addressed and harmless if the CAS
                # never commits.  A reset runtime is rebuilt from the still
                # authoritative durable boundary on retry/recovery.
                raise
            scopes.commit_prevalidated(candidate)
            return activated

    @staticmethod
    def _capability_refresh_metadata(
        boundary: ReactCommandBoundary,
    ) -> dict[str, Any]:
        raw = boundary.request_payload.get("capability_refresh")
        if not isinstance(raw, Mapping):
            raise RuntimeError(
                "capability mutation requires a durable refresh snapshot"
            )
        return copy.deepcopy(dict(raw))

    def _capability_refresh_intent(
        self,
        boundary: ReactCommandBoundary,
        *,
        index: int,
        outcome: NormalizedToolOutcome,
        expected_continuation_version: int,
        source_kind: str = "tool_effect",
    ) -> Any | None:
        receipt = self._capability_operation_receipt(outcome)
        if receipt is None:
            return None
        context = boundary.tool_contexts[index]
        if (
            receipt.root_run_id
            != (
                boundary.run_context.root_run_id
                if boundary.run_context is not None
                else boundary.run_id
            )
            or receipt.parent_command_id != boundary.command_id
            or receipt.parent_effect_id != context.effect_id
        ):
            raise RuntimeError(
                "capability operation receipt does not belong to this effect"
            )
        metadata = self._capability_refresh_metadata(boundary)
        stamp_raw = metadata.get("catalog_stamp")
        if not isinstance(stamp_raw, Mapping):
            raise RuntimeError("capability refresh catalog stamp is unavailable")
        from deskpet.capabilities.contracts import CatalogStamp
        from deskpet.capabilities.refresh_contracts import CapabilityRefreshIntent

        stamp = CatalogStamp(
            catalog_generation=int(stamp_raw["catalog_generation"]),
            registry_revision=int(stamp_raw["registry_revision"]),
            binding_generation=int(stamp_raw["binding_generation"]),
            skill_revision=int(stamp_raw["skill_revision"]),
            mcp_revision=int(stamp_raw["mcp_revision"]),
            fingerprint=str(stamp_raw.get("fingerprint") or ""),
        )
        if stamp.fingerprint != receipt.old_stamp.fingerprint:
            raise RuntimeError(
                "capability operation started from a different catalog stamp"
            )
        intent_id = hashlib.sha256(
            (
                "capability-refresh|"
                f"{receipt.operation_id}|{receipt.refresh_nonce}|"
                f"{boundary.run_id}"
            ).encode("utf-8")
        ).hexdigest()
        return CapabilityRefreshIntent(
            intent_id=intent_id,
            root_run_id=receipt.root_run_id,
            run_id=boundary.run_id,
            operation_id=receipt.operation_id,
            source_kind=source_kind,  # type: ignore[arg-type]
            source_command_id=boundary.command_id,
            source_effect_id=context.effect_id,
            refresh_nonce=receipt.refresh_nonce,
            expected_continuation_version=expected_continuation_version,
            old_stamp=stamp,
            old_catalog_snapshot_ref=str(metadata["catalog_snapshot_ref"]),
            old_tool_set_snapshot_ref=str(
                metadata["tool_set_snapshot_ref"]
            ),
            exposure_intent_ref=str(metadata["exposure_intent_ref"]),
        )

    @staticmethod
    def _with_refresh_pending(
        boundary: ReactCommandBoundary, intent_id: str
    ) -> ReactCommandBoundary:
        payload = copy.deepcopy(dict(boundary.request_payload))
        refresh = copy.deepcopy(dict(payload.get("capability_refresh") or {}))
        current = refresh.get("refresh_pending")
        if current not in (None, intent_id):
            raise RuntimeError("another capability refresh is already pending")
        refresh["refresh_pending"] = intent_id
        refresh["provider_backfill_blocked"] = True
        payload["capability_refresh"] = refresh
        return replace(boundary, request_payload=payload)

    def _pending_capability_retry_batch(
        self, boundary: ReactCommandBoundary
    ) -> ReactToolBatch | None:
        raw_pending = boundary.completion_state.get("pending_capability_retry")
        if not isinstance(raw_pending, Mapping):
            return None
        pending = copy.deepcopy(dict(raw_pending))
        receipt_ref = str(pending.get("failure_receipt_ref") or "")
        tool_name = str(pending.get("tool_name") or "")
        retry_of_effect_id = str(pending.get("retry_of_effect_id") or "")
        canonical_args = pending.get("canonical_args")
        if (
            not receipt_ref
            or not tool_name
            or not retry_of_effect_id
            or not isinstance(canonical_args, Mapping)
        ):
            raise RuntimeError("pending capability retry contract is malformed")

        identity_seed = (
            f"{boundary.run_id}|{receipt_ref}|{retry_of_effect_id}|{tool_name}"
        )
        provider_call_id = hashlib.sha256(
            f"capability-retry-call|{identity_seed}".encode("utf-8")
        ).hexdigest()
        command_id = hashlib.sha256(
            f"capability-retry-batch|{identity_seed}".encode("utf-8")
        ).hexdigest()
        effect_id = hashlib.sha256(
            f"effect|{boundary.run_id}|{provider_call_id}".encode("utf-8")
        ).hexdigest()
        arguments = copy.deepcopy(dict(canonical_args))
        assistant_message = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": provider_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(
                            arguments,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                }
            ],
        }
        canonical_messages = (
            *boundary.canonical_messages,
            assistant_message,
        )
        feedback = {
            "_host_capability_retry": {
                "failure_receipt_ref": receipt_ref,
                "retry_of_effect_id": retry_of_effect_id,
            }
        }
        synthetic_call = SimpleNamespace(
            id=provider_call_id,
            name=tool_name,
            arguments=arguments,
            args_raw=None,
            args_parse_error=None,
        )

        error_code = ""
        error_message = ""
        prepared: PreparedToolCall | None = None
        context: ToolExecutionContext | None = None
        try:
            if boundary.run_context is None:
                raise RuntimeError(
                    "host retry requires the immutable root RunContext"
                )
            allowed = AgentLoopCollaborator._allowed_tool_names(
                boundary.to_start()
            )
            if allowed is not None and tool_name not in allowed:
                error_code = "capability_retry_tool_not_exposed"
                raise RuntimeError(
                    "repaired tool is absent from the refreshed capability snapshot"
                )
            from deskpet.harness.context import HostContextFactory

            context = HostContextFactory().create_tool_context(
                boundary.run_context,
                run_id=boundary.run_id,
                command_id=command_id,
                call_id=provider_call_id,
                effect_id=effect_id,
                scope_id=AgentLoopCollaborator._capability_scope_id(
                    boundary.to_start()
                ),
                capability_snapshot_ref=(
                    AgentLoopCollaborator._capability_snapshot_ref(
                        boundary.to_start()
                    )
                ),
                active_skill_scope_ids=boundary.active_skill_scope_ids,
                effective_skill_tool_ref_hashes=(
                    ()
                    if not boundary.active_skill_scope_ids
                    else _skill_tool_intersection(
                        boundary.capability_snapshot,
                        boundary.request_payload,
                        active_scope_ids=boundary.active_skill_scope_ids,
                        activated_scopes=(
                            boundary.activated_skill_scopes
                        ),
                    ).effective_tool_ref_hashes
                ),
                effective_skill_tool_refs_hash=(
                    boundary.effective_skill_tool_refs_hash or ""
                ),
            )
            prepare_call = getattr(self._tool_registry, "prepare_call", None)
            if not callable(prepare_call):
                error_code = "capability_retry_registry_unavailable"
                raise RuntimeError(
                    "tool registry cannot prepare the repaired invocation"
                )
            prepared = prepare_call(
                tool_name,
                arguments,
                boundary.session_id,
                provider_call_id,
                execution_context=context,
            )
            if prepared.catalog_snapshot_ref not in {
                "",
                context.capability_snapshot_ref,
            }:
                error_code = "capability_retry_snapshot_drift"
                raise RuntimeError(
                    "repaired tool capability snapshot ref drifted"
                )
            prepared = replace(
                prepared,
                catalog_snapshot_ref=context.capability_snapshot_ref,
            )
            if (
                fingerprint_json(prepared.arguments_json())
                != str(pending.get("args_hash") or "")
            ):
                error_code = "capability_retry_args_changed"
                raise RuntimeError(
                    "re-preparing the repaired tool changed its canonical arguments"
                )
            if prepared.tool_spec_fingerprint == str(
                pending.get("failed_tool_spec_fingerprint") or ""
            ):
                error_code = "capability_retry_spec_unchanged"
                raise RuntimeError(
                    "capability repair did not activate a new immutable ToolSpec"
                )
            prepared = replace(
                prepared, retry_of_effect_id=retry_of_effect_id
            )
        except KeyError as exc:
            error_code = error_code or "capability_retry_tool_not_found"
            error_message = str(exc)
        except Exception as exc:  # noqa: BLE001 - converted to trusted failure
            error_code = error_code or "capability_retry_prepare_failed"
            error_message = f"{type(exc).__name__}: {exc}"

        if prepared is not None and context is not None:
            return ReactToolBatch(
                command_id=command_id,
                calls=(prepared,),
                contexts=(context,),
                canonical_messages=canonical_messages,
                iteration=boundary.iteration + 1,
                feedback_state=feedback,
                provider_call_order=(provider_call_id,),
            )

        raw_failure = dict(
            AgentLoopCollaborator._raw_failure(
                run_id=boundary.run_id,
                tool_call=synthetic_call,
                call_order=0,
                source_kind="tool_prepare",
                error_code=error_code,
                message=error_message,
            )
        )
        raw_failure["source_identity"] = (
            f"capability_repair_retry:{receipt_ref}"
        )
        return ReactToolBatch(
            command_id=command_id,
            calls=(),
            contexts=(),
            canonical_messages=canonical_messages,
            iteration=boundary.iteration + 1,
            feedback_state=feedback,
            raw_failures=(raw_failure,),
            provider_call_order=(provider_call_id,),
        )

    @staticmethod
    def _refresh_failure_boundary(
        boundary: ReactCommandBoundary,
        *,
        intent_id: str,
        source_effect_id: str | None,
        code: str,
        message: str,
    ) -> ReactCommandBoundary:
        matching = tuple(
            index
            for index, context in enumerate(boundary.tool_contexts)
            if source_effect_id and context.effect_id == source_effect_id
        )
        if not matching and len(boundary.pending_calls) == 1:
            matching = (0,)
        if len(matching) != 1:
            raise RuntimeError(
                "capability refresh failure cannot resolve its provider call"
            )
        index = matching[0]
        outcomes = list(boundary.outcomes)
        statuses = list(boundary.outcome_statuses)
        metadata = [dict(item) for item in boundary.outcome_metadata]
        original = outcomes[index]
        outcomes[index] = NormalizedToolOutcome.failure(
            code,
            message,
            value={
                "refresh_intent_id": intent_id,
                "operation_outcome": (
                    None if original is None else original.to_dict()
                ),
            },
        )
        statuses[index] = OutcomeStatus.FAILED
        metadata[index].update(
            {
                "capability_refresh_intent_id": intent_id,
                "capability_refresh_error": {
                    "code": code,
                    "message": message,
                },
            }
        )
        payload = copy.deepcopy(dict(boundary.request_payload))
        refresh = copy.deepcopy(dict(payload.get("capability_refresh") or {}))
        refresh.update(
            {
                "refresh_pending": None,
                "provider_backfill_blocked": False,
                "last_refresh_failure": {
                    "intent_id": intent_id,
                    "code": code,
                    "message": message,
                },
            }
        )
        payload["capability_refresh"] = refresh
        state = copy.deepcopy(dict(boundary.completion_state))
        state["model_backfilled"] = False
        state["capability_refresh_failure"] = {
            "intent_id": intent_id,
            "code": code,
            "message": message,
        }
        state.pop("pending_capability_retry", None)
        state.pop("capability_retry_inflight", None)
        return replace(
            boundary,
            outcomes=tuple(outcomes),
            outcome_statuses=tuple(statuses),
            outcome_metadata=tuple(metadata),
            completion_state=state,
            request_payload=payload,
            version=boundary.version + 1,
        )

    async def _persist_refresh_failure(
        self,
        boundary: ReactCommandBoundary,
        *,
        intent_id: str,
        error: Exception,
    ) -> ReactCommandBoundary:
        service = self._capability_refresh_service
        capability_store = (
            getattr(service, "store", None) if service is not None else None
        )
        if capability_store is None:
            capability_store = getattr(
                self._capability_refresh_staging, "store", None
            )
        current_intent = (
            await capability_store.get_refresh_intent(intent_id)
            if capability_store is not None
            else None
        )
        if current_intent is not None and current_intent.status == "committed":
            record = await self._uow.load_continuation(boundary.run_id)
            if record is None:
                raise RuntimeError(
                    "committed capability refresh lost its continuation"
                )
            return ReactCommandBoundary.from_record(record)

        code = str(
            getattr(error, "code", None) or "capability_refresh_failed"
        )
        message = f"{type(error).__name__}: {error}"
        updated = self._refresh_failure_boundary(
            boundary,
            intent_id=intent_id,
            source_effect_id=(
                None
                if current_intent is None
                else current_intent.source_effect_id
            ),
            code=code,
            message=message,
        )
        live = self._live_boundary(boundary.run_id)
        bind_execution = getattr(self._uow, "bind", None)
        if (
            live is None
            and capability_store is not None
            and current_intent is not None
            and current_intent.status == "pending"
            and callable(bind_execution)
        ):
            async with capability_store.write_transaction() as db:
                await capability_store.bind(db).fail_refresh_intent(
                    intent_id,
                    error={"code": code, "message": message},
                )
                saved = await bind_execution(db).save_continuation_payload(
                    run_id=boundary.run_id,
                    expected_version=boundary.version,
                    payload=updated.to_payload(),
                )
            return replace(updated, version=int(saved.version))

        if (
            capability_store is not None
            and current_intent is not None
            and current_intent.status == "pending"
        ):
            async with capability_store.write_transaction() as db:
                await capability_store.bind(db).fail_refresh_intent(
                    intent_id,
                    error={"code": code, "message": message},
                )
        if live is not None:
            active = self._bound_live().get(boundary.run_id)
            if active is None or active.driver_state is not live:
                raise RuntimeError(
                    "ReAct live boundary changed during refresh failure"
                )
            active.driver_state = updated
            return updated
        return await self._save_durable_boundary(updated)

    async def _perform_pending_capability_refresh(
        self,
        boundary: ReactCommandBoundary,
    ) -> ReactCommandBoundary:
        metadata = boundary.request_payload.get("capability_refresh")
        if not isinstance(metadata, Mapping):
            return boundary
        intent_id = str(metadata.get("refresh_pending") or "")
        if not intent_id:
            return boundary
        service = self._capability_refresh_service
        if service is None:
            raise RuntimeError("capability refresh service is unavailable")

        from deskpet.capabilities.contracts import (
            CapabilityScope,
            fingerprint_json,
        )
        from deskpet.capabilities.refresh import (
            CapabilityContinuationCommitEvidence,
        )

        scope_raw = metadata.get("scope")
        if not isinstance(scope_raw, Mapping):
            raise RuntimeError("capability refresh scope is unavailable")
        scope = CapabilityScope(
            run_key=(
                None
                if scope_raw.get("run_key") is None
                else str(scope_raw["run_key"])
            ),
            project_key=(
                None
                if scope_raw.get("project_key") is None
                else str(scope_raw["project_key"])
            ),
            user_key=str(scope_raw.get("user_key") or "default"),
            builtin_key=str(scope_raw.get("builtin_key") or "builtin"),
        )
        updated_boundary: ReactCommandBoundary | None = None

        async def commit_continuation(db: Any, prepared: Any) -> Any:
            nonlocal updated_boundary
            commit = prepared.commit
            payload = copy.deepcopy(dict(boundary.request_payload))
            refresh = copy.deepcopy(
                dict(payload.get("capability_refresh") or {})
            )
            refresh.update(
                {
                    "catalog_snapshot_ref": commit.new_catalog_snapshot_ref,
                    "catalog_stamp": commit.new_stamp.to_dict(),
                    "tool_set_snapshot_ref": commit.new_tool_set_snapshot_ref,
                    "refresh_pending": None,
                    "provider_backfill_blocked": False,
                    "last_refresh_commit": commit.fingerprint,
                }
            )
            payload["capability_refresh"] = refresh
            payload["context_os"] = thaw_json(commit.new_context_os)
            payload["tool_set_snapshot_ref"] = (
                commit.new_tool_set_snapshot_ref
            )
            visible_names = sorted(
                capability.ref.name
                for capability in (
                    *prepared.prepared_tool_set.direct,
                    *prepared.prepared_tool_set.activated,
                )
            )
            capability_snapshot = copy.deepcopy(
                dict(boundary.capability_snapshot)
            )
            capability_snapshot["capabilities"] = visible_names
            capability_snapshot["catalog_stamp"] = commit.new_stamp.to_dict()
            # The Context OS payload and this trusted ref are one atomic
            # continuation snapshot.  Leaving the start-time ref in place
            # makes the next Driver recovery correctly reject the refreshed
            # payload as "new bytes + old hash".
            capability_snapshot["prepared_tool_set_ref"] = (
                commit.new_tool_set_snapshot_ref
            )
            candidate = replace(
                boundary,
                request_payload=payload,
                capability_snapshot=capability_snapshot,
                tool_set_snapshot_ref=commit.new_tool_set_snapshot_ref,
                version=boundary.version + 1,
            )
            bind = getattr(self._uow, "bind", None)
            if not callable(bind):
                raise RuntimeError(
                    "execution UoW cannot join capability refresh transaction"
                )
            saved = await bind(db).save_continuation_payload(
                run_id=boundary.run_id,
                expected_version=boundary.version,
                payload=candidate.to_payload(),
            )
            reset = getattr(self._collaborator, "reset_runtime", None)
            if not callable(reset):
                raise RuntimeError(
                    "AgentLoop runtime cannot be reset after catalog refresh"
                )
            await reset(boundary.run_id)
            updated_boundary = replace(candidate, version=int(saved.version))
            return CapabilityContinuationCommitEvidence(
                root_run_id=commit.root_run_id,
                run_id=commit.run_id,
                previous_version=commit.expected_continuation_version,
                new_version=commit.expected_continuation_version + 1,
                refresh_pending_cleared=True,
                new_catalog_snapshot_ref=commit.new_catalog_snapshot_ref,
                new_tool_set_snapshot_ref=commit.new_tool_set_snapshot_ref,
                new_stamp_fingerprint=commit.new_stamp.fingerprint,
                context_os_fingerprint=fingerprint_json(
                    commit.to_dict()["new_context_os"]
                ),
                driver_runtime_cleared=True,
            )

        prepared = await service.refresh(
            intent_id,
            scope=scope,
            commit_continuation=commit_continuation,
        )
        if updated_boundary is None:
            raise RuntimeError("capability refresh did not commit a continuation")
        scopes = self._capability_scope_store
        if scopes is not None:
            eligibility = prepared.eligibility
            existing = scopes.get(
                prepared.prepared_tool_set.scope_id,
                session_id=eligibility.session_id,
                request_id=eligibility.request_id,
            )
            if existing is None:
                scopes.open(prepared.prepared_tool_set, eligibility)
            else:
                scopes.commit_prevalidated(prepared.prepared_tool_set)
        return updated_boundary

    async def _consume_pending_capability_refresh(
        self,
        boundary: ReactCommandBoundary,
    ) -> ReactCommandBoundary:
        metadata = boundary.request_payload.get("capability_refresh")
        if not isinstance(metadata, Mapping):
            return boundary
        intent_id = str(metadata.get("refresh_pending") or "")
        if not intent_id:
            return boundary
        try:
            return await self._perform_pending_capability_refresh(boundary)
        except Exception as exc:  # noqa: BLE001 - failure returns to same model
            return await self._persist_refresh_failure(
                boundary,
                intent_id=intent_id,
                error=exc,
            )

    async def _resume_completed(self, boundary: ReactCommandBoundary, *, recovery_lease: RecoveryLease | None=None) -> AsyncIterator[DriverEvent]:
        boundary = await self._consume_pending_tool_activation(
            boundary, recovery_lease=recovery_lease
        )
        boundary = await self._consume_pending_capability_refresh(boundary)
        boundary = self._stage_failure_reports(
            boundary, self._tool_registry
        )
        boundary = await self._persist_failure_set(boundary)
        if (
            outcome_refs := self._successful_attempt_outcome_refs(boundary)
        ) is not None:
            current = dict(
                boundary.completion_state.get("current_attempt") or {}
            )
            await self._uow.settle_provider_action_success(
                str(current["attempt_id"]),
                outcome_refs,
            )
        if not bool(boundary.completion_state.get('model_backfilled')):
            state = copy.deepcopy(dict(boundary.completion_state))
            state['model_backfilled'] = True
            messages = self._tool_messages(boundary)
            prepare_feedback = getattr(self._collaborator, 'prepare_tool_feedback', None)
            if callable(prepare_feedback):
                state, messages = await prepare_feedback(boundary, state, messages)
            boundary = replace(boundary, canonical_messages=messages, completion_state=state, version=boundary.version + 1)
            await self._save_progress(boundary, recovery_lease=recovery_lease)
        retry_batch = self._pending_capability_retry_batch(boundary)
        if retry_batch is not None:
            async def retry_emission() -> AsyncIterator[ReactEmission]:
                yield retry_batch

            async for candidate in self._emit(
                boundary.to_start(),
                retry_emission(),
                continuation_version=boundary.version,
                recovery_lease=recovery_lease,
            ):
                yield candidate
            return
        terminal_error = str(boundary.completion_state.get('agent_loop_terminal_error') or '')
        if terminal_error:
            yield DriverTerminalCandidate(boundary.run_id, 'failed', error=terminal_error)
            return
        evidence = await self._completion_evidence(boundary)
        async for candidate in self._emit(boundary.to_start(), self._collaborator.resume(boundary, {'type': 'tool_outcomes', 'command_id': boundary.command_id, 'scoped_evidence': evidence}), continuation_version=boundary.version, recovery_lease=recovery_lease):
            yield candidate

    async def _apply_child_inbox(self, boundary: ReactCommandBoundary, signal: DriverSignal, *, recovery_lease: RecoveryLease | None) -> tuple[ReactCommandBoundary, DriverEvent | None]:
        signal_id = str(getattr(signal, 'signal_id', '') or '').strip()
        if not signal_id:
            return boundary, None
        command = boundary.pending_delegate
        root_terminal = signal.kind == 'child_terminal' and boundary.pending_delegate is not None and boundary.pending_delegate.join_policy is JoinPolicy.ROOT_TERMINAL_CHILD
        clear_delegate = not root_terminal and (signal.kind == 'child_terminal' or boundary.pending_delegate is not None and boundary.pending_delegate.join_policy is JoinPolicy.DETACHED)
        value = thaw_json(getattr(signal, 'value', None))
        state = copy.deepcopy(dict(boundary.completion_state))
        detached = dict(state.get('detached_children') or {})
        if signal.kind == 'child_accepted' and boundary.pending_delegate is not None and boundary.pending_delegate.join_policy is JoinPolicy.DETACHED:
            detached[signal.command_id] = signal.child_run_id
        elif signal.kind == 'child_terminal':
            detached.pop(signal.command_id, None)
        state['detached_children'] = detached
        terminal_status = str(getattr(signal, 'status', 'accepted'))
        if (
            signal.kind == "child_terminal"
            and terminal_status == "failed"
            and command is not None
            and boundary.pending_calls
        ):
            signature = self._delegate_objective_signature(
                boundary.pending_calls[0], route_hint=command.route_hint
            )
            if signature is not None:
                history = [
                    dict(item)
                    for item in state.get("failed_delegate_objectives", ())
                    if isinstance(item, Mapping)
                ]
                signature.update(
                    child_run_id=signal.child_run_id,
                    command_id=signal.command_id,
                )
                history.append(signature)
                state["failed_delegate_objectives"] = history[
                    -self._DELEGATE_OBJECTIVE_HISTORY_LIMIT :
                ]
        capability_retry = None
        if (
            signal.kind == "child_terminal"
            and terminal_status == "completed"
            and command is not None
            and command.route_hint == "workflow.capability_build"
        ):
            host = self._capability_builder_host
            try:
                if host is None:
                    raise RuntimeError(
                        "capability builder host is unavailable"
                    )
                raw_launch = command.child_request.get(
                    "capability_builder"
                )
                if not isinstance(raw_launch, Mapping):
                    raise RuntimeError(
                        "capability builder launch contract is missing"
                    )
                if len(boundary.tool_contexts) != 1:
                    raise RuntimeError(
                        "capability builder parent context is malformed"
                    )
                from deskpet.capabilities.builder import (
                    CapabilityBuildLaunch,
                )

                launch = CapabilityBuildLaunch.from_dict(raw_launch)
                value = thaw_json(
                    await host.finalize_child_completion(
                        launch,
                        value,
                        context=boundary.tool_contexts[0],
                    )
                )
                if launch.lineage.operation_kind == "repair":
                    receipt = await host.store.get_failure_receipt(
                        str(launch.lineage.failure_receipt_ref or "")
                    )
                    if receipt is None:
                        raise RuntimeError(
                            "repair failure receipt disappeared before refresh"
                        )
                    capability_retry = {
                        "failure_receipt_ref": receipt.receipt_ref,
                        "tool_name": receipt.tool_name,
                        "canonical_args": dict(receipt.canonical_args),
                        "args_hash": receipt.args_hash,
                        "retry_of_effect_id": receipt.effect_id,
                        "failed_tool_spec_fingerprint": (
                            receipt.tool_spec_fingerprint
                        ),
                    }
            except Exception as exc:  # noqa: BLE001 - normalized for the model
                terminal_status = "failed"
                value = {
                    "code": str(
                        getattr(
                            exc,
                            "code",
                            "capability_builder_finalize_failed",
                        )
                    ),
                    "message": str(exc),
                    "phase": "host_validate_publish",
                }
        event_status = (
            OutcomeStatus.SUCCEEDED
            if signal.kind == 'child_terminal' and terminal_status == 'completed'
            else OutcomeStatus.FAILED
            if signal.kind == 'child_terminal'
            else OutcomeStatus.ACCEPTED
        )
        transaction_hook = None
        if (
            boundary.command_kind == "control_delegate"
            and signal.kind == "child_terminal"
        ):
            if terminal_status == "completed":
                if capability_retry is not None:
                    state["pending_capability_retry"] = copy.deepcopy(
                        capability_retry
                    )
                success_value = (
                    {
                        **dict(value),
                        "status": terminal_status,
                        "child_run_id": signal.child_run_id,
                    }
                    if (
                        command is not None
                        and command.route_hint
                        == "workflow.capability_build"
                        and isinstance(value, Mapping)
                    )
                    else {
                        "status": terminal_status,
                        "child_run_id": signal.child_run_id,
                        "value": value,
                    }
                )
                outcome = NormalizedToolOutcome.success(
                    success_value
                )
            else:
                outcome = NormalizedToolOutcome.failure(
                    "child_workflow_failed",
                    str(value or terminal_status),
                    value={
                        "status": terminal_status,
                        "child_run_id": signal.child_run_id,
                        "value": value,
                    },
                )
            existing_outcome = boundary.outcomes[0]
            if existing_outcome is not None and existing_outcome != outcome:
                ignored = list(state.get("ignored_child_terminals") or ())
                ignored.append(
                    {
                        "signal_id": signal_id,
                        "command_id": signal.command_id,
                        "child_run_id": signal.child_run_id,
                        "status": terminal_status,
                        "reason": "authoritative_outcome_already_recorded",
                    }
                )
                state["ignored_child_terminals"] = ignored[-16:]
                updated = replace(
                    boundary,
                    completion_state=state,
                    pending_delegate=None,
                    version=boundary.version + 1,
                )
                event = RunEventCandidate(
                    event_key=f"child-signal:{signal_id}",
                    kind=signal.kind,
                    status=event_status,
                    driver_kind="react",
                    correlation={
                        "command_id": signal.command_id,
                        "child_run_id": signal.child_run_id,
                        "signal_id": signal_id,
                    },
                    payload={
                        "status": terminal_status,
                        "value": value,
                        "ignored": True,
                        "reason": "authoritative_outcome_already_recorded",
                    },
                )
                _, saved, stored_event = await self._uow.ack_child_signal(
                    signal_id,
                    expected_continuation_version=boundary.version,
                    continuation_payload=updated.to_payload(),
                    event=event,
                    recovery_lease=recovery_lease,
                )
                return (
                    replace(updated, version=int(saved.version)),
                    PersistedEventCandidate(stored_event),
                )
            updated = boundary.with_outcomes(
                {0: outcome},
                {0: event_status},
                {
                    0: {
                        "child_run_id": signal.child_run_id,
                        "child_signal_id": signal_id,
                        **(
                            {
                                "capability_retry_required": copy.deepcopy(
                                    capability_retry
                                )
                            }
                            if capability_retry is not None
                            else {}
                        ),
                    }
                },
            )
            updated = replace(
                updated,
                completion_state=state,
                pending_delegate=None,
                version=boundary.version + 1,
            )
            refresh_intent = self._capability_refresh_intent(
                updated,
                index=0,
                outcome=outcome,
                expected_continuation_version=boundary.version + 1,
                source_kind="child_terminal",
            )
            if refresh_intent is not None:
                staging = self._capability_refresh_staging
                if staging is None:
                    raise RuntimeError(
                        "capability refresh staging is unavailable"
                    )
                updated = self._with_refresh_pending(
                    updated, refresh_intent.intent_id
                )

                async def stage_child_refresh(
                    db: Any,
                    continuation: Any,
                    *,
                    intent: Any = refresh_intent,
                    stage_service: Any = staging,
                ) -> None:
                    from deskpet.capabilities.refresh import (
                        CapabilityRefreshStageEvidence,
                    )

                    await stage_service.stage_in_transaction(
                        db,
                        intent,
                        CapabilityRefreshStageEvidence(
                            root_run_id=intent.root_run_id,
                            run_id=intent.run_id,
                            source_kind="child_terminal",
                            source_command_id=intent.source_command_id,
                            source_effect_id=intent.source_effect_id,
                            previous_version=boundary.version,
                            new_version=int(continuation.version),
                            refresh_pending_intent_id=intent.intent_id,
                            outer_effect_state="deferred_pending",
                            provider_outcome_staged=False,
                            child_terminal_acked=True,
                            provider_backfill_blocked=True,
                        ),
                    )

                transaction_hook = stage_child_refresh
        elif boundary.command_kind == "control_delegate":
            updated = replace(
                boundary,
                completion_state=state,
                version=boundary.version + 1,
            )
        else:
            child_response = {'type': 'host_child_response', 'signal': signal.kind, 'signal_id': signal_id, 'child_run_id': signal.child_run_id, 'status': terminal_status, 'value': value}
            messages = boundary.canonical_messages + ({'role': 'system', 'content': json.dumps(child_response, ensure_ascii=False, sort_keys=True, default=str)},)
            updated = replace(boundary, canonical_messages=messages, completion_state=state, pending_delegate=None if clear_delegate else boundary.pending_delegate, version=boundary.version + 1)
        event = RunEventCandidate(event_key=f'child-signal:{signal_id}', kind=signal.kind, status=event_status, driver_kind='react', correlation={'command_id': signal.command_id, 'child_run_id': signal.child_run_id, 'signal_id': signal_id}, payload={'status': terminal_status, 'value': value})
        _, saved, stored_event = await self._uow.ack_child_signal(signal_id, expected_continuation_version=boundary.version, continuation_payload=updated.to_payload(), event=event, recovery_lease=recovery_lease, transaction_hook=transaction_hook)
        return replace(updated, version=int(saved.version)), PersistedEventCandidate(stored_event)

    async def _apply_tool_outcomes(
        self,
        signal: DriverSignal,
        *,
        recovery_lease: RecoveryLease | None,
    ) -> AsyncIterator[DriverEvent]:
        boundary = await self._load_boundary(signal.run_id)
        if boundary.command_id != signal.command_id:
            raise ValueError('tool outcome command binding mismatch')
        updates = dict(zip(signal.original_indexes, signal.outcomes))
        statuses = dict(zip(signal.original_indexes, signal.statuses))
        metadata = dict(
            zip(
                signal.original_indexes,
                signal.metadata or ({},) * len(signal.outcomes),
            )
        )
        dirty = False
        persisted_version = boundary.version
        for index in signal.original_indexes:
            command = self._brokered_for_parent(boundary, index)
            inner = None if command is None else self._brokered_inner(command)
            if inner is None and index not in boundary.provider_execution_indexes:
                raise ValueError('tool outcome index not pending in boundary')
            current_call, current_context = (
                inner
                if inner is not None
                else (
                    boundary.pending_calls[index],
                    boundary.tool_contexts[index],
                )
            )
            raw_outcome = updates[index]
            raw_status = statuses[index]
            if command is not None and inner is not None:
                boundary = self._apply_brokered_action_outcome(
                    boundary,
                    command,
                    outcome=raw_outcome,
                    status=raw_status,
                    metadata=metadata[index],
                )
            else:
                boundary, handled = self._accept_brokered_parent(
                    boundary,
                    parent_index=index,
                    outcome=raw_outcome,
                    metadata=metadata[index],
                )
                if not handled:
                    boundary = boundary.with_outcomes(
                        {index: raw_outcome},
                        {index: raw_status},
                        {index: metadata[index]},
                    )
            boundary = remember_artifact_completion(
                boundary,
                call=current_call,
                outcome=raw_outcome,
                status=raw_status,
                metadata=metadata[index],
            )
            boundary = replace(boundary, version=persisted_version + 1)
            claim = dict(metadata[index].get('effect_claim') or {})
            if (
                current_call.tool_name == "skill_invoke"
                and not claim
            ):
                prepared_activation = (
                    self._activation_scope_from_outcome(
                        boundary, raw_outcome
                    )
                )
                if prepared_activation is not None:
                    scope, activation = prepared_activation
                    boundary = (
                        await self._commit_skill_scope_activation(
                            boundary,
                            expected_continuation_version=(
                                persisted_version
                            ),
                            scope=scope,
                            activation=activation,
                        )
                    )
                    persisted_version = boundary.version
                    dirty = False
                    continue
            if not claim:
                if self._capability_operation_receipt(raw_outcome) is not None:
                    raise RuntimeError(
                        "capability mutation outcome lacks a durable "
                        "effect settlement claim"
                    )
                dirty = True
                continue
            refresh_intent = self._capability_refresh_intent(
                boundary,
                index=index,
                outcome=raw_outcome,
                expected_continuation_version=persisted_version + 1,
            )
            transaction_hook = None
            if refresh_intent is not None:
                staging = self._capability_refresh_staging
                if staging is None:
                    raise RuntimeError(
                        "capability refresh staging is unavailable"
                    )
                boundary = self._with_refresh_pending(
                    boundary, refresh_intent.intent_id
                )

                async def stage_refresh(
                    db: Any,
                    settlement: Any,
                    *,
                    intent: Any = refresh_intent,
                    stage_service: Any = staging,
                ) -> None:
                    from deskpet.capabilities.refresh import (
                        CapabilityRefreshStageEvidence,
                    )

                    await stage_service.stage_in_transaction(
                        db,
                        intent,
                        CapabilityRefreshStageEvidence(
                            root_run_id=intent.root_run_id,
                            run_id=intent.run_id,
                            source_kind="tool_effect",
                            source_command_id=intent.source_command_id,
                            source_effect_id=intent.source_effect_id,
                            previous_version=(
                                intent.expected_continuation_version - 1
                            ),
                            new_version=int(settlement.continuation.version),
                            refresh_pending_intent_id=intent.intent_id,
                            outer_effect_state="settled",
                            provider_outcome_staged=True,
                            child_terminal_acked=False,
                            provider_backfill_blocked=True,
                        ),
                    )

                transaction_hook = stage_refresh
            effect_status = {
                OutcomeStatus.FAILED: 'failed',
                OutcomeStatus.UNKNOWN: 'unknown',
                OutcomeStatus.CANCELLED: 'cancelled',
                OutcomeStatus.ACCEPTED: 'accepted',
            }.get(raw_status, 'succeeded')
            event = RunEventCandidate(
                event_key=f'effect:{current_context.effect_id}:settled',
                kind='tool.outcome',
                status=raw_status,
                driver_kind='react',
                correlation={
                    'command_id': boundary.command_id,
                    'call_id': current_context.call_id,
                    'effect_id': current_context.effect_id,
                },
                payload={
                    'tool_name': current_call.tool_name,
                    'outcome': raw_outcome.to_dict(),
                    'receipt_ref': metadata[index].get('receipt_ref'),
                    'evidence_verified': bool(
                        metadata[index].get('evidence_verified')
                    ),
                    'capability_audit_ref': metadata[index].get(
                        'capability_audit_ref'
                    ),
                },
                error=raw_outcome.error,
                artifact_refs=tuple(
                    str(item)
                    for item in metadata[index].get('artifact_refs', ())
                ),
            )
            settlement = await self._uow.settle_effect(
                current_context.effect_id,
                expected_effect_version=int(claim['effect_version']),
                attempt_no=int(claim['attempt_no']),
                worker_owner=str(claim['worker_owner']),
                worker_epoch=int(claim['worker_epoch']),
                status=effect_status,
                outcome=raw_outcome.to_dict(),
                receipt_ref=metadata[index].get('receipt_ref'),
                artifact_refs=event.artifact_refs,
                node_execution_id=(
                    f'react:{boundary.command_id}:'
                    f'{current_call.stable_call_id}'
                ),
                checkpoint_ns='react',
                checkpoint_id=boundary.command_id,
                expected_continuation_version=persisted_version,
                continuation_payload=boundary.to_payload(),
                event=event,
                recovery_lease=recovery_lease,
                reconciliation=bool(metadata[index].get('reconciliation')),
                evidence_verified=bool(metadata[index].get('evidence_verified')),
                transaction_hook=transaction_hook,
            )
            boundary = replace(
                boundary, version=int(settlement.continuation.version)
            )
            persisted_version = boundary.version
            dirty = False
            yield PersistedEventCandidate(settlement.event)
        if dirty:
            await self._save_progress(
                boundary, recovery_lease=recovery_lease
            )
        async for candidate in self._continue_after_tool_progress(
            boundary, recovery_lease=recovery_lease
        ):
            yield candidate

    def signal(self, signal: DriverSignal, recovery_lease: RecoveryLease | None = None) -> AsyncIterator[DriverEvent]:

        async def iterator() -> AsyncIterator[DriverEvent]:
            if signal.kind == "user_continuation":
                boundary = await self._load_boundary(signal.run_id)
                message_ref = str(signal.message_ref)
                existing_refs = {
                    str(item.get("_deskpet_message_ref"))
                    for item in boundary.canonical_messages
                    if isinstance(item, Mapping)
                    and item.get("_deskpet_message_ref")
                }
                already_applied = message_ref in existing_refs
                if already_applied:
                    updated = boundary
                else:
                    resume_signal = {
                        "type": "user_continuation",
                        "message_ref": message_ref,
                    }
                    messages = boundary.canonical_messages + (
                        {
                            "role": "user",
                            "content": str(signal.content),
                            "_deskpet_message_ref": message_ref,
                        },
                    )
                    state = copy.deepcopy(dict(boundary.completion_state))
                    refs = list(state.get("user_continuation_refs") or ())
                    refs.append(message_ref)
                    state["user_continuation_refs"] = list(
                        dict.fromkeys(str(ref) for ref in refs if ref)
                    )
                    if (
                        boundary.pending_decision is None
                        and boundary.pending_delegate is None
                        and not boundary.pending_calls
                    ):
                        state["pending_resume_signal"] = resume_signal
                    updated = replace(
                        boundary,
                        canonical_messages=messages,
                        completion_state=state,
                        version=boundary.version + 1,
                    )
                event = RunEventCandidate(
                    event_key=f"user-continuation:{message_ref}",
                    kind="run.user_continuation",
                    status=OutcomeStatus.ACCEPTED,
                    driver_kind="react",
                    correlation={"message_ref": message_ref},
                    payload={
                        "task_scope_id": str(signal.task_scope_id),
                        "message_ref": message_ref,
                    },
                )
                if bool(getattr(signal, "queued", False)):
                    _conversation, saved, stored_event = (
                        await self._uow.commit_queued_user_continuation(
                            signal.run_id,
                            message_ref,
                            task_scope_id=str(signal.task_scope_id),
                            expected_continuation_version=boundary.version,
                            continuation_payload=updated.to_payload(),
                            event=event,
                        )
                    )
                else:
                    _conversation, saved, stored_event = (
                        await self._uow.commit_user_continuation(
                            signal.run_id,
                            message_ref,
                            task_scope_id=str(signal.task_scope_id),
                            expected_boundary_version=int(
                                signal.expected_boundary_version
                            ),
                            expected_continuation_version=boundary.version,
                            continuation_payload=updated.to_payload(),
                            event=event,
                        )
                    )
                updated = replace(updated, version=int(saved.version))
                yield PersistedEventCandidate(stored_event)
                if already_applied:
                    return
                if (
                    updated.pending_decision is not None
                    or updated.pending_delegate is not None
                    or bool(updated.pending_calls)
                ):
                    return
                clean_state = copy.deepcopy(dict(updated.completion_state))
                clean_state.pop("pending_resume_signal", None)
                resumed = replace(updated, completion_state=clean_state)
                async for candidate in self._emit(
                    resumed.to_start(),
                    self._collaborator.resume(
                        resumed,
                        resume_signal,
                    ),
                    continuation_version=updated.version,
                    recovery_lease=recovery_lease,
                ):
                    yield candidate
                return
            if signal.kind == "tool_outcomes":
                async for candidate in self._apply_tool_outcomes(
                    signal, recovery_lease=recovery_lease
                ):
                    yield candidate
                return
            if signal.kind == "decision":
                raise RuntimeError('decision signals require the atomic Kernel path')
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
                    async for candidate in self._emit(boundary.to_start(), self._collaborator.resume(boundary, {'type': 'child_accepted', 'child_run_id': signal.child_run_id}), continuation_version=boundary.version, recovery_lease=recovery_lease):
                        yield candidate
                return
            if signal.kind == "child_terminal":
                boundary = await self._load_boundary(signal.run_id)
                command = boundary.pending_delegate
                detached = dict(boundary.completion_state.get('detached_children') or {})
                if command is None and detached.get(signal.command_id) == signal.child_run_id:
                    boundary, persisted = await self._apply_child_inbox(boundary, signal, recovery_lease=recovery_lease)
                    if persisted is not None:
                        yield persisted
                    return
                if command is None or command.command_id != signal.command_id:
                    raise ValueError('delegate command not found')
                boundary, persisted = await self._apply_child_inbox(boundary, signal, recovery_lease=recovery_lease)
                if persisted is not None:
                    yield persisted
                if command.join_policy is JoinPolicy.ROOT_TERMINAL_CHILD:
                    yield DriverTerminalCandidate(signal.run_id, 'completed' if signal.status == 'completed' else 'failed', content=str(signal.value or ''), error=None if signal.status == 'completed' else str(signal.value or signal.status), correlation={'child_run_id': signal.child_run_id})
                    return
                if command.join_policy is JoinPolicy.JOIN_BEFORE_FINAL:
                    if boundary.command_kind == "control_delegate":
                        async for candidate in self._resume_completed(
                            boundary, recovery_lease=recovery_lease
                        ):
                            yield candidate
                        return
                    async for candidate in self._emit(boundary.to_start(), self._collaborator.resume(boundary, {'type': 'child_terminal', 'child_run_id': signal.child_run_id, 'status': signal.status, 'value': signal.value}), continuation_version=boundary.version, recovery_lease=recovery_lease):
                        yield candidate
        return iterator()

    def signal_decision_atomically(
        self,
        signal: DriverSignal,
        durable_signal: DurableDecisionSignal,
        actor: ActorContext,
        *,
        authorization_plan: PreparedAuthorizationPlan | None = None,
    ) -> AsyncIterator[DriverEvent]:
        async def iterator() -> AsyncIterator[DriverEvent]:
            planned_authorization = authorization_plan
            boundary = await self._load_boundary(signal.run_id)
            if boundary.pending_decision is None or boundary.pending_decision.decision_id != signal.decision_id:
                raise ValueError('decision does not match pending boundary')
            pending = boundary.pending_decision
            response = dict(signal.response)
            response['decision_status'] = 'allowed' if durable_signal.allow else 'denied'
            next_decision: DriverEvent | None = None
            resume_signal: Mapping[str, Any] | None = None
            permission_decision = False
            external_wait_decision = False
            if pending.domain_kind == "external":
                if not durable_signal.allow:
                    raise ValueError(
                        "external wait must be completed or the Run must be cancelled"
                    )
                external_state = self._external_wait_state(boundary)
                if (
                    external_state is None
                    or not isinstance(external_state.get("record"), Mapping)
                ):
                    raise ValueError(
                        "external wait decision lost its durable wait record"
                    )
                wait = TaskExternalWait.from_dict(
                    dict(external_state["record"])
                )
                selected_project_root: str | None = None
                if pending.domain_id == "project_directory":
                    selected_project_root = str(
                        durable_signal.response.get("project_root") or ""
                    ).strip()
                    if not selected_project_root:
                        raise ValueError(
                            "project directory response requires project_root"
                        )
                    rebound = (
                        await self._uow.rebind_task_workspace_for_project(
                            boundary.run_context.root_run_id
                            if boundary.run_context is not None
                            else boundary.run_id,
                            selected_project_root,
                            expected_binding_version=1,
                        )
                    )
                    if rebound.workspace_root != selected_project_root:
                        raise RuntimeError(
                            "project workspace rebind returned a different path"
                        )
                    boundary = self._with_project_workspace(
                        boundary,
                        selected_project_root,
                    )
                index = int(external_state.get("index", -1))
                if (
                    index < 0
                    or index >= len(boundary.pending_calls)
                    or boundary.pending_calls[index].stable_call_id
                    != wait.provider_call_id
                ):
                    raise ValueError(
                        "external wait decision call is absent from the boundary"
                    )
                response_ref = "external-response:" + fingerprint_json(
                    {
                        "wait_ref": wait.wait_ref,
                        "decision_id": signal.decision_id,
                        "nonce": signal.nonce,
                        "version": signal.version,
                        "response": dict(durable_signal.response),
                    }
                )
                resumed_wait = await self._uow.resume_external_wait(
                    wait.wait_ref,
                    response_ref,
                    expected_version=wait.version,
                )
                response.update(
                    completed=True,
                    wait_ref=wait.wait_ref,
                    response_ref=response_ref,
                )
                boundary = boundary.with_outcomes(
                    {
                        index: NormalizedToolOutcome.success(
                            {
                                "completed": True,
                                "external_action_handled": True,
                                "verification_required": True,
                                "wait_ref": wait.wait_ref,
                                "wait_kind": wait.wait_kind.value,
                                "response_ref": response_ref,
                                **(
                                    {
                                        "project_root": selected_project_root,
                                        "project_directory_selected": True,
                                    }
                                    if selected_project_root is not None
                                    else {}
                                ),
                            }
                        )
                    },
                    {index: OutcomeStatus.SUCCEEDED},
                    {
                        index: {
                            "external_wait_ref": wait.wait_ref,
                            "external_wait_response_ref": response_ref,
                            "external_wait_version": resumed_wait.version,
                        }
                    },
                )
                state = copy.deepcopy(dict(boundary.completion_state))
                current = dict(state.get("current_attempt") or {})
                current.update(status="running", budget_eligible=True)
                state["current_attempt"] = current
                state["attempt_status"] = "running"
                state["last_external_wait"] = {
                    **dict(external_state),
                    "response_ref": response_ref,
                    "state": "satisfied",
                }
                state.pop("external_wait", None)
                boundary = replace(
                    boundary,
                    completion_state=state,
                    pending_decision=None,
                )
                external_wait_decision = True
            elif (
                pending.decision_kind == "permission"
                and pending.call_id is not None
            ):
                permission_decision = True
                indexes = {
                    call.stable_call_id: index
                    for index, call in enumerate(boundary.pending_calls)
                }
                index = indexes.get(pending.call_id)
                brokered_command = next(
                    (
                        command
                        for command in boundary.active_brokered_commands
                        if command.current_inner_call_ref == pending.call_id
                    ),
                    None,
                )
                if index is None and brokered_command is None:
                    raise ValueError(
                        "permission decision call is absent from the boundary"
                    )
                if durable_signal.allow:
                    if self._authorization_runtime is not None:
                        if planned_authorization is None:
                            planned_authorization = await self._plan_permission(
                                boundary,
                                pending,
                                confirmed=True,
                            )
                        if not planned_authorization.resolves_without_ui:
                            raise ValueError(
                                "permission allow is not covered by authorization policy: "
                                f"{planned_authorization.reason}"
                            )
                        task_grant = planned_authorization.committed_task_grant
                        assert task_grant is not None
                        response.update(
                            task_grant_id=task_grant.task_grant_id,
                            task_grant_version=task_grant.version,
                            authorization_source=task_grant.source,
                            policy_generation=(
                                planned_authorization.policy_state.generation
                            ),
                        )
                    grant_id = stable_decision_grant_id(pending.decision_id)
                    response['grant_id'] = grant_id
                    response['grant_version'] = 0
                    state = copy.deepcopy(dict(boundary.completion_state))
                    refs = dict(state.get('authorization_refs') or {})
                    refs[pending.call_id] = {'grant_id': grant_id, 'decision_id': pending.decision_id, 'decision_nonce': pending.nonce, 'version': 0}
                    state['authorization_refs'] = refs
                    if self._authorization_runtime is not None:
                        assert planned_authorization is not None
                        task_grant = planned_authorization.committed_task_grant
                        assert task_grant is not None
                        state['task_grant_id'] = task_grant.task_grant_id
                        state['authorization_policy_generation'] = (
                            planned_authorization.policy_state.generation
                        )
                    boundary = replace(boundary, completion_state=state, pending_decision=None, version=boundary.version + 1)
                else:
                    denied = NormalizedToolOutcome.failure(
                        'authorization_denied', 'authorization denied'
                    )
                    if brokered_command is not None:
                        boundary = self._apply_brokered_action_outcome(
                            boundary,
                            brokered_command,
                            outcome=denied,
                            status=OutcomeStatus.FAILED,
                            metadata={},
                        )
                        boundary = replace(
                            boundary,
                            pending_decision=None,
                            version=boundary.version + 1,
                        )
                    else:
                        assert index is not None
                        boundary = boundary.with_outcomes(
                            {index: denied}, {index: OutcomeStatus.FAILED}
                        )
                        boundary = replace(boundary, pending_decision=None)
                next_decision = self._next_permission_after_progress(boundary)
                if next_decision is not None:
                    boundary = replace(boundary, pending_decision=next_decision)
            elif (
                boundary.command_kind == "control_delegate"
                and len(boundary.pending_calls) == 1
            ):
                boundary = boundary.with_outcomes(
                    {
                        0: NormalizedToolOutcome.success(
                            {
                                "decision_id": signal.decision_id,
                                "response": response,
                            }
                        )
                    },
                    {0: OutcomeStatus.SUCCEEDED},
                    {
                        0: {
                            "decision_id": signal.decision_id,
                            "decision_kind": pending.decision_kind,
                        }
                    },
                )
                boundary = replace(boundary, pending_decision=None)
            else:
                messages = tuple(boundary.canonical_messages) + ({'role': 'system', 'content': json.dumps({'decision_id': signal.decision_id, 'response': response}, ensure_ascii=False, sort_keys=True)},)
                state = copy.deepcopy(dict(boundary.completion_state))
                state['model_backfilled'] = True
                resume_signal = {'type': 'decision', 'response': response}
                state['pending_resume_signal'] = resume_signal
                boundary = replace(boundary, canonical_messages=messages, completion_state=state, pending_decision=None, version=boundary.version + 1)
            resumed_event = RunEventCandidate(
                event_key=f'decision:{signal.decision_id}:resumed',
                kind='run.resumed', status=OutcomeStatus.ACCEPTED, driver_kind='react',
                correlation={'decision_id': signal.decision_id},
                payload={'decision_status': response['decision_status']},
            )
            _, authorization, saved, event = await self._uow.commit_decision(
                durable_signal, actor,
                expected_continuation_version=boundary.version - 1,
                continuation_payload=boundary.to_payload(),
                resumed_event=resumed_event,
                next_decision=self._durable_decision(next_decision),
                authorization_commit=(
                    planned_authorization.to_commit()
                    if planned_authorization is not None and durable_signal.allow
                    else None
                ),
            )
            if authorization is not None and authorization.grant_id != response.get('grant_id'):
                raise RuntimeError('atomic decision grant identity mismatch')
            boundary = replace(boundary, version=int(saved.version))
            yield PersistedEventCandidate(event)
            if next_decision is not None:
                if self._authorization_runtime is not None:
                    async for candidate in self._apply_permission_policy(
                        boundary, next_decision
                    ):
                        yield candidate
                else:
                    yield next_decision
                return
            if permission_decision:
                if boundary.command_kind == "control_delegate":
                    if durable_signal.allow:
                        boundary, delegate = await self._prepare_control_event(
                            boundary
                        )
                        if delegate is not None:
                            yield delegate
                            return
                    async for candidate in self._resume_completed(boundary):
                        yield candidate
                    return
                async for candidate in self._continue_after_tool_progress(
                    boundary
                ):
                    yield candidate
                return
            if external_wait_decision:
                async for candidate in self._continue_after_tool_progress(
                    boundary
                ):
                    yield candidate
                return
            if boundary.pending_calls:
                async for candidate in self._resume_completed(boundary):
                    yield candidate
                return
            if resume_signal is not None:
                clean_state = copy.deepcopy(dict(boundary.completion_state))
                clean_state.pop('pending_resume_signal', None)
                resumed_boundary = replace(boundary, completion_state=clean_state)
                async for candidate in self._emit(resumed_boundary.to_start(), self._collaborator.resume(resumed_boundary, resume_signal), continuation_version=boundary.version):
                    yield candidate
        return iterator()

    def recover(self, run_id: str, recovery_lease: RecoveryLease) -> AsyncIterator[DriverEvent]:

        async def iterator() -> AsyncIterator[DriverEvent]:
            boundary = await self._load_boundary(run_id)
            external_wait = self._external_wait_state(boundary)
            if (
                external_wait is not None
                and boundary.pending_decision is not None
                and boundary.pending_decision.domain_kind == "external"
            ):
                await self._ensure_external_wait_staged(boundary)
            else:
                await self._admit_provider_boundary(boundary)
            last_message = boundary.canonical_messages[-1] if boundary.canonical_messages else {}
            root_terminal = json.loads(str(last_message.get('content', '{}'))) if boundary.pending_delegate is not None and boundary.pending_delegate.join_policy is JoinPolicy.ROOT_TERMINAL_CHILD and last_message.get('role') == 'system' else {}
            if root_terminal.get('signal') == 'child_terminal':
                status, value = str(root_terminal.get('status')), thaw_json(root_terminal.get('value'))
                yield DriverTerminalCandidate(run_id, 'completed' if status == 'completed' else 'failed', content=str(value or ''), error=None if status == 'completed' else str(value or status), correlation={'child_run_id': str(root_terminal.get('child_run_id'))})
                return
            if boundary.pending_decision is not None:
                if boundary.pending_decision.decision_kind == "permission":
                    async for candidate in self._apply_permission_policy(
                        boundary, boundary.pending_decision
                    ):
                        yield candidate
                    return
                yield boundary.pending_decision
                return
            if boundary.pending_delegate is not None:
                yield boundary.pending_delegate
                return
            pending_resume = boundary.completion_state.get('pending_resume_signal')
            if isinstance(pending_resume, Mapping):
                state = copy.deepcopy(dict(boundary.completion_state))
                state.pop('pending_resume_signal', None)
                resumed_boundary = replace(boundary, completion_state=state)
                async for candidate in self._emit(resumed_boundary.to_start(), self._collaborator.resume(resumed_boundary, dict(pending_resume)), continuation_version=boundary.version, recovery_lease=recovery_lease):
                    yield candidate
                return
            original_version = boundary.version
            changed = False
            for index in boundary.provider_execution_indexes:
                call, context = boundary.pending_calls[index], boundary.tool_contexts[index]
                effect = await self._uow.read_effect_outcome(
                    run_id=boundary.run_id,
                    call_id=call.stable_call_id,
                    effect_id=context.effect_id,
                    args_hash=call.args_hash,
                    capability_hash=context.capability_hash,
                    scope_hash=context.scope_hash,
                )
                if effect is None:
                    continue
                status, payload, receipt_ref, artifact_refs = effect
                if status == 'unknown' and payload.get('reconciliation_pending'):
                    continue
                outcome = NormalizedToolOutcome.from_dict(payload)
                outcome_status = OutcomeStatus(status)
                outcome_metadata = {
                    'receipt_ref': receipt_ref,
                    'artifact_refs': list(artifact_refs),
                }
                boundary, handled = self._accept_brokered_parent(
                    boundary,
                    parent_index=index,
                    outcome=outcome,
                    metadata=outcome_metadata,
                )
                if not handled:
                    boundary = boundary.with_outcomes(
                        {index: outcome},
                        {index: outcome_status},
                        {index: outcome_metadata},
                    )
                changed = True
            for command in tuple(boundary.active_brokered_commands):
                inner = self._brokered_inner(command)
                if inner is None:
                    continue
                call, context = inner
                effect = await self._uow.read_effect_outcome(
                    run_id=boundary.run_id,
                    call_id=call.stable_call_id,
                    effect_id=context.effect_id,
                    args_hash=call.args_hash,
                    capability_hash=context.capability_hash,
                    scope_hash=context.scope_hash,
                )
                if effect is None:
                    continue
                status, payload, receipt_ref, artifact_refs = effect
                if status == 'unknown' and payload.get('reconciliation_pending'):
                    continue
                boundary = self._apply_brokered_action_outcome(
                    boundary,
                    command,
                    outcome=NormalizedToolOutcome.from_dict(payload),
                    status=OutcomeStatus(status),
                    metadata={
                        'receipt_ref': receipt_ref,
                        'artifact_refs': list(artifact_refs),
                        'recovered': True,
                    },
                )
                changed = True
            if changed:
                boundary = replace(
                    boundary,
                    version=original_version + 1,
                )
                await self._save_progress(
                    boundary, recovery_lease=recovery_lease
                )
            if (
                boundary.command_kind == "control_delegate"
                and boundary.provider_execution_indexes
            ):
                boundary, delegate = await self._prepare_control_event(
                    boundary, recovery_lease=recovery_lease
                )
                if delegate is None:
                    async for candidate in self._resume_completed(
                        boundary, recovery_lease=recovery_lease
                    ):
                        yield candidate
                    return
                yield delegate
                return
            async for candidate in self._continue_after_tool_progress(
                boundary, recovery_lease=recovery_lease
            ):
                yield candidate
        return iterator()

    async def prepare_recovery(self, run_id: str, recovery_lease: RecoveryLease) -> None:
        boundary = await self._load_boundary(run_id)
        prepare = getattr(self._collaborator, 'prepare_recovery', None)
        if callable(prepare):
            await prepare(boundary)
    def cancel(self, run_id: str, reason: str) -> AsyncIterator[DriverEvent]:

        async def iterator() -> AsyncIterator[DriverEvent]:
            try:
                boundary = await self._load_boundary(run_id)
            except ValueError:
                boundary = None
            if boundary is not None and boundary.active_brokered_commands:
                original_version = boundary.version
                for command in tuple(boundary.active_brokered_commands):
                    cancelled_record = replace(
                        command.plan_record, status="cancelled"
                    )
                    cancelled = replace(
                        command,
                        plan_record=cancelled_record,
                        status="terminal",
                        current_inner_call_ref=None,
                        current_inner_call=None,
                        current_inner_context=None,
                    )
                    boundary = self._terminalize_brokered(
                        boundary,
                        cancelled,
                        outcome=NormalizedToolOutcome.failure(
                            "brokered_effect_cancelled",
                            reason,
                            value=self._brokered_result_payload(cancelled),
                        ),
                        status=OutcomeStatus.CANCELLED,
                        extra_metadata={"cancel_reason": reason},
                    )
                boundary = replace(
                    boundary,
                    pending_decision=None,
                    version=original_version + 1,
                )
                await self._save_progress(boundary)
            await self._collaborator.cancel(run_id, reason)
            volatile = self._live_boundary(run_id)
            if volatile is not None:
                active = self._bound_live().get(run_id)
                if active is not None and active.driver_state is volatile:
                    active.driver_state = None
            yield CancelAcknowledgedCandidate(run_id, reason)
        return iterator()

    async def close(self) -> None:
        await self._collaborator.close()
        if self._live is not None:
            for active in self._live.values():
                if isinstance(active.driver_state, ReactCommandBoundary):
                    active.driver_state = None
__all__ = [
    "AgentLoopCollaborator",
    "AgentLoopToolInterceptionError",
    "ReActDriver",
    "ReactCommandBoundary",
    "ReactControlBatch",
    "ReactEmission",
    "ReactFailure",
    "ReactFallback",
    "ReactFinal",
    "ReactToken",
    "ReactToolBatch",
]
