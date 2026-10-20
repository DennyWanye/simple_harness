# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Planner intents, planning rounds and hierarchical admission context."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from simple_harness.agents import AgentConfig, AgentLimits

from ...context.context_builder import ContextRejected, TaskPackage, _seal
from ...contracts import TERMINAL_MISSION, ContractError, Mission, MissionStatus, MissionStopReason
from ...contracts.htn import MethodRef, MethodRegistryStatus, ObligationId
from ...contracts.models import sha256_hex
from ...contracts.planning_decisions import (
    ENABLED_DECISIONS,
    PlanningDecisionStatus,
    PlanningRefKind,
    PlanningRefV1,
    PlanningRequestBinding,
    PlanningRetryBudgetView,
    UnsupportedPlanningPackage,
    internal_enablement_keys,
)
from ...governance import planning_authorization
from ...governance.budgets import BudgetExhausted
from ...governance.planning_authorization import SourceUnavailable as AuthoritySourceUnavailable
from ...planning.decision_admission import (
    AdmissionContext,
    AuthorizationView,
    BudgetView,
    CapabilityView,
    MethodInstanceView,
    MethodView,
    OperationStateView,
    PlanShapeView,
)
from ...planning.decision_codec import allocate_decision_id, hash_raw_output
from ...planning.decision_feedback import feedback_from_decision
from ...planning.htn.planner_package import package_hash, subject_bindings_hash, visible_refs_digest
from ...planning.htn.registry import iter_predicates
from ...runtime import planning_operations, role_templates
from ...runtime.agent_worker import user_message_json
from ...runtime.first_request_budget import FirstRequestBudget
from ...runtime.model_router import RoutingUnavailable
from ...runtime.planning_operations import SourceUnavailable as OperationSourceUnavailable
from ...runtime.role_templates import (
    PLANNER_HIERARCHICAL,
    hierarchical_worker_for_domain,
    hierarchical_worker_versions,
)
from ...storage import planning_decision_store
from ...storage.obligation_store import ObligationStore
from ...storage.planning_admission_store import PlanningAdmissionStore
from ...storage.store import DispatchIntent, StoreConflict, StoreError
from .. import step_actions
from ..assurance_point_use import (
    PLANNING_REQUEST_CONSUMER,
    PointUseRefused,
    knowledge_handover,
    planning_claims,
    planning_evidence_stale,
    require_point_use_locked,
)
from ..budget_accounts import Reservation, is_global_account, mission_account
from ..hierarchical_dispatch import HierarchicalDispatch
from ..plan_commits import PlanPrincipal
from ..planner_views import read_planner_package
from ..planning_backend_runtime import bind_deployment
from ..planning_evidence import evidence_authority
from ..planning_protocol_binding import current_planning_protocol
from ..planning_runtime_block import planner_binding, resume_source_current
from ..planning_selection import infrastructure_retry
from ..taskgraph_epochs import planning_scope_digest
from .core import OrchestratorCore

if TYPE_CHECKING:
    from ...graph.task_network import TaskNetworkSnapshot
    from ..hierarchical_dispatch import PlanningWorld


class PlanningRoundMixin(OrchestratorCore):
    """Planner intents, planning rounds and hierarchical admission context."""

    __slots__ = ()

    def _requirements_unconfirmed(self, mission: Mission) -> bool:
        """The Mission's current requirements have no confirmed completion mapping yet (the
        Mission was just created, or the user just amended them): the Planner is not asked —
        a plan it made would be refused when its completion scopes are frozen.  The
        confirmation page shows "waiting for the completion requirements to be confirmed".
        Every path that opens a Planner round asks this one question (阶段 E)."""
        return (
            self._wiring.planning_start_gate is not None
            and not self._wiring.planning_start_gate(mission)
        )

    def _knowledge_handover(self, mission_id: str, reader: Mapping[str, Any]) -> Any:
        """``knowledge_read`` 交出正文前的那道门（裁决 2026-10-07 第 5 件）：同一个使用证书签发方，消费方
        是这次工具调用。执行者是装上下文（CONTEXT）；审阅员读黑板是披露（DISCLOSE，推后第 2 批 A03），
        钉住审阅所属的那一步。"""

        if reader.get("review_key") is not None:
            row = self.store.connection.execute(
                "SELECT mission_id, json_extract(binding_json,'$.subject.owner_task_ref.id') "
                "FROM assurance_review_bindings WHERE review_key=?",
                (str(reader["review_key"]),),
            ).fetchone()
            if row is None or row[0] != mission_id:
                return lambda claim: ("REVIEW_BINDING_SOURCE_MISMATCH",)
            return knowledge_handover(
                self.commit,
                mission_id=mission_id,
                consumer_id=str(reader["call_id"]),
                task_id=str(row[1]),
                purpose="DISCLOSE",
            )
        attempt = self.store.get_attempt(str(reader["attempt_id"]))
        if attempt is None or attempt.mission_id != mission_id:
            return lambda claim: ("TOOL_CALL_ATTEMPT_UNKNOWN",)
        return knowledge_handover(
            self.commit,
            mission_id=mission_id,
            consumer_id=str(reader["call_id"]),
            task_id=attempt.task_id,
        )

    def _planning_round_waits(self, mission_id: str, refused: Any, *, path: str) -> bool:
        """推后第 1 批 A26（裁决 2026-10-07 建议 1）：要当事实交给规划器的证据此刻签不出 PLAN 证书
        （证据刚过时、时钟回拨、授权变了）。三条开轮的路——普通开轮、服务恢复、等待唤醒——都走这里：
        这一轮不开（开轮事务已回滚），原因写进进度记录，不算一轮故障、不停任务；下一轮按当时的世界
        重新组包再签。"""
        now = self.store.now
        since, last = self._planning.planning_evidence_waits.get(mission_id, (now, now))
        if now - last > self._config.profile_wait_seconds:
            since = now  # 上一段等待早已断开（中间没再签不出）：重新计时，不把旧的开始时刻算进来
        self._planning.planning_evidence_waits[mission_id] = (since, now)
        refusals = list(refused.use.refusals)
        waited = now - since
        if waited >= self._config.profile_wait_seconds:
            # 原因一直不消失时不无声地等下去——与"规划池不可用"同一个上限，
            # 到点按名停下，明细写清哪条路、哪几条拒绝原因、等了多久。
            self._planning.planning_evidence_waits.pop(mission_id, None)
            self._stop_planning_round(
                mission_id,
                reason="planning_evidence_uncertifiable",
                detail={
                    "path": path,
                    "refusals": refusals[:16],
                    "waited_seconds": round(waited, 3),
                    "profile_wait_seconds": self._config.profile_wait_seconds,
                },
                stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
            )
            self._note(f"mission {mission_id}: planning evidence never certifiable → stopped")
            return False
        self._note(f"mission {mission_id}: planning round waits at {path} ({'; '.join(refusals)})")
        return False

    async def _try_planner_intent(self, mission_id: str, *, ordinal: int) -> bool:
        """Create the Planner intent, or — review P0-1 — wait (bounded) while its pool is
        cooling down; a package that would carry a credential stops planning visibly."""

        mission = self.store.get_mission(mission_id)
        if mission is not None and self._assembly_missing(mission, at="planner_retry"):
            return False
        if mission is not None and self._requirements_unconfirmed(mission):
            return False

        try:
            await self._create_planner_intent(mission_id, ordinal=ordinal)
        except PointUseRefused as refused:
            return self._planning_round_waits(mission_id, refused, path="ask")
        except UnsupportedPlanningPackage as error:
            # 2026-09-25: a Mission bound to a package this build no longer serves stops
            # here, by itself — it must not take the orchestrator loop (and every other
            # Mission in the library) down with it.
            self._planning.deferred_planning.pop(mission_id, None)
            self._stop_planning_round(
                mission_id,
                reason="unsupported_planning_package",
                detail={"error": str(error)[:300], "ordinal": ordinal},
                stop_reason=MissionStopReason.PLANNING_FAILED,
            )
            self._note(f"mission {mission_id}: {error} → stopped")
            return False
        except RoutingUnavailable as unavailable:
            since = self._planning.deferred_planning.get(mission_id, (self.store.now, ordinal))[0]
            self._planning.deferred_planning[mission_id] = (since, ordinal)
            if self.store.now - since >= self._config.profile_wait_seconds:
                self._planning.deferred_planning.pop(mission_id, None)
                self._stop_planning_round(
                    mission_id,
                    reason="runtime_unavailable",
                    detail={
                        "profile_id": unavailable.profile_id,
                        "waited_seconds": round(self.store.now - since, 3),
                        "profile_wait_seconds": self._config.profile_wait_seconds,
                        "ordinal": ordinal,
                    },
                    stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
                )
                self._note(
                    f"mission {mission_id}: planner pool {unavailable.profile_id!r} unavailable → stopped"
                )
            return False
        except ContextRejected as error:
            self._planning.deferred_planning.pop(mission_id, None)
            # Verification P2-F / mutation VO: ``ordinal`` is added only where it is
            # new information.  A Mission still in PLANNING writes exactly the payload
            # it wrote on main — the legacy ``MissionFailed.detail`` is a shipped shape
            # and this slice has no business widening it — while a Mission past PLANNING
            # is in a round that only P2.3d opens, where "which round" is the one thing
            # an operator cannot otherwise work out.
            rejected = self.store.get_mission(mission_id)
            detail: dict[str, Any] = {"error": str(error)[:300]}
            if rejected is not None and rejected.status is not MissionStatus.PLANNING:
                detail["ordinal"] = ordinal
            self._stop_planning_round(
                mission_id,
                reason="context_rejected",
                detail=detail,
                stop_reason=MissionStopReason.CONTEXT_REJECTED,
            )
            self._note(f"mission {mission_id}: planner package refused ({error})")
            return False
        self._planning.deferred_planning.pop(mission_id, None)
        return True

    def _stop_planning_round(
        self,
        mission_id: str,
        *,
        reason: str,
        detail: Mapping[str, Any],
        stop_reason: MissionStopReason,
    ) -> None:
        """End a Mission whose Planner round cannot be opened, in its own phase's terms.

        Review P0-1 / P2-4.  ``fail_planning`` files the stop as a *planning* failure and
        it was the only ending here, which was right while every Planner round belonged
        to the phase that produces the first plan.  P2.3d opens rounds on a Mission that
        already holds a committed plan, and calling ``fail_planning`` on one of those
        says something untrue about it (``final_report.planning_failure`` on a Mission
        that was planned) and skips the cascade a Mission-level stop owes its open work.

        So: a Mission still in PLANNING ends exactly as it did before — byte for byte,
        which is what the legacy goldens read — and a Mission past it ends through
        ``fail_mission``, whose report carries the same reason and the Task rows.
        """

        mission = self.store.get_mission(mission_id)
        if mission is None or mission.status in TERMINAL_MISSION:
            return
        if mission.status is MissionStatus.PLANNING:
            self._commit_fail_planning(
                mission_id, reason=reason, detail=detail, stop_reason=stop_reason
            )
            return
        self._commit_fail_mission(
            mission_id, stop_reason=stop_reason, detail={"reason": reason, **dict(detail)}
        )

    async def _planner_round_on_committed_plan(
        self, mission_id: str, *, ordinal: int, phase: str
    ) -> bool:
        """Open a Planner round for a Mission that already holds a plan (P2.3d).

        Review P0-1: ``_create_planner_intent`` reserves ``planner_reserve_tokens``
        against the Mission account and raises ``BudgetExhausted`` — a ``StoreError``
        that ``_cycle`` does not forgive and ``run()`` does not catch.  ``_start_planning``
        has caught it since step 6; D5-A's repair round and D5-B's refinement round had
        not, so a Mission that ran its account down — which is precisely the state the
        repair branch is reached in — took the whole process with it.

        One Mission's exhaustion is one Mission's stop (§24.1 decision 11).
        """

        try:
            return await self._try_planner_intent(mission_id, ordinal=ordinal)
        except BudgetExhausted as error:
            self._stop_planning_round(
                mission_id,
                reason="budget_exhausted",
                detail={
                    "dimension": error.dimension,
                    "requested": error.requested,
                    "remaining": error.remaining,
                    "account": error.account_id,
                    "phase": phase,
                    "ordinal": ordinal,
                    "scope": "global" if is_global_account(error.account_id) else "mission",
                },
                stop_reason=MissionStopReason.BUDGET_EXHAUSTED,
            )
            self._note(
                f"mission {mission_id} stopped in {phase}: budget_exhausted ({error.dimension})"
            )
            return False

    async def _retry_deferred_planning(self) -> bool:
        progressed = False
        self._prune_deferred()
        for mission_id, (_since, ordinal) in list(self._planning.deferred_planning.items()):
            mission = self.store.get_mission(mission_id)
            if mission is not None and mission.status is not MissionStatus.PLANNING:
                # Review P0-1: a deferred round belonging to a Mission that is already
                # ACTIVE is one of P2.3d's, and the retry must not carry its exhaustion
                # out of the loop either.
                step = lambda mission_id=mission_id, ordinal=ordinal: (
                    self._planner_round_on_committed_plan(
                        mission_id, ordinal=ordinal, phase="deferred_planning"
                    )
                )
            else:
                step = lambda mission_id=mission_id, ordinal=ordinal: self._try_planner_intent(  # noqa: E731
                    mission_id, ordinal=ordinal
                )
            if await self._mission_round(mission_id, "deferred_planning", step):
                progressed = True
        return progressed

    async def _start_planning(self, mission: Mission) -> bool:
        """``False`` when nothing could start (assembly missing, or the start gate is
        still waiting for a person).  2026-09-26 (Host 真机): the caller counted such a
        no-op as progress, and a progressing cycle does not sleep, so one Mission
        waiting for its completion mapping spun the loop at 100% CPU and starved the
        Host's event loop."""
        if self._assembly_missing(mission, at="start_planning"):
            return False
        if self._requirements_unconfirmed(mission):
            return False
        self.commit.begin_planning(mission.id)
        try:
            await self._try_planner_intent(mission.id, ordinal=1)
        except BudgetExhausted as error:
            # D6-8 / D3-12': a pool that cannot even fund the Planner is exhausted on that
            # dimension; the Mission stops visibly instead of the loop crashing
            detail = {
                "dimension": error.dimension,
                "requested": error.requested,
                "remaining": error.remaining,
                "account": error.account_id,
                "phase": "planning",
                # review P1-1: the Global pool is named as such — no Mission is to blame
                "scope": "global" if is_global_account(error.account_id) else "mission",
            }
            self._commit_fail_planning(
                mission.id,
                reason="budget_exhausted",
                detail=detail,
                stop_reason=MissionStopReason.BUDGET_EXHAUSTED,
            )
            self._note(
                f"mission {mission.id} stopped in planning: budget_exhausted ({error.dimension})"
            )
        return True

    def _planning_retry_budgets(self, mission: Mission, *, format_retries: int) -> Any:
        """The §39 budget view a feedback value reports (same count admission uses)."""

        allowance = max(0, int(self._config.max_planning_attempts))
        return PlanningRetryBudgetView(
            same_request_format_retries_remaining=max(0, int(format_retries)),
            planning_rounds_remaining=max(0, allowance - self._planning_attempts(mission.id)),
            root_review_repairs_remaining=max(0, int(self._config.max_root_review_repairs)),
            repeated_failure_before_escalation_remaining=None,
        )

    def _latest_planning_feedback(self, mission: Mission) -> Any:
        """2026-09-30：新请求的 ``previous_feedback``——本任务最近一条规划决定被拒时，
        告诉规划器拒在哪（字段路径）；最近一条没被拒就是 ``None``。"""

        row = planning_decision_store.PlanningDecisionStore(self.store).latest_planning_decision(
            mission.id
        )
        return feedback_from_decision(
            row, budgets=self._planning_retry_budgets(mission, format_retries=1)
        )

    def _hierarchical_planner_package(
        self,
        new_mode: HierarchicalDispatch,
        mission: Mission,
        *,
        ordinal: int,
        previous_feedback: Any = None,
    ) -> Any:
        """Seal the hierarchical Planner's package.

        The network is read through :meth:`HierarchicalDispatch.network`, the same
        call the plan compiler makes, so the package describes exactly the plan the
        commit will be checked against — and before the first revision exists that
        call already answers with the *seed* network, because the Planner's first job
        is to refine the root goal the Mission was opened for.  A damaged plan raises
        rather than producing a package about a plan that is not readable, and a
        deployment without a ``PlanningWorld`` raises rather than falling back to the
        other mode's package.
        """

        # ``_seal`` is private to the context builder and is reached anyway, on
        # purpose: it renders the package *and* derives the context hash, and a second
        # renderer here would be a second answer to "what did the model see".

        del ordinal  # the round number is not a fact the Planner reads
        current_planning_protocol(self.store, mission.id)
        world = new_mode.require_planning_world()
        network = new_mode.network(mission.id)
        # One assessment, rendered into the request *and* recorded.  Computing it twice
        # would let the record and the message disagree about a world that moved
        # between them, and the record exists precisely to say what the model was told.
        reports = new_mode.method_applicability(mission.id)
        new_mode.record_method_applicability(mission.id, reports=reports)
        usage = self.store.mission_budget_usage(mission.id)
        if usage is None:
            raise ContractError("planner budget ledger is unavailable")
        token_limit = mission.budget.max_tokens
        remaining_tokens = (
            self._config.planner_reserve_tokens
            if token_limit is None
            else max(
                0, int(token_limit) - int(usage["settled_tokens"]) - int(usage["reserved_tokens"])
            )
        )
        return _seal(
            read_planner_package(
                store=self.store,
                mission=mission,
                network=network,
                world=world,
                dispatch=new_mode,
                reports=reports,
                previous_feedback=previous_feedback,
                package_version=role_templates.PLANNING_DECISION_PACKAGE_VERSION,
                executor_capabilities=step_actions.executor_capabilities(
                    self._connectors,
                    self._config.deployment_policy,
                    self._config.domain_tools,
                    allowed=step_actions.scope_names(mission),
                ),
                budget={
                    "planning_remaining": max(
                        0, self._config.max_planning_attempts - self._planning_attempts(mission.id)
                    ),
                    "method_proposals_remaining": self._method_proposals_remaining(
                        mission, new_mode
                    ),
                    "root_repair_remaining": max(
                        0,
                        self._config.max_root_review_repairs
                        - self._root_review_repairs(mission.id),
                    ),
                    "max_method_candidates": 12,
                    "max_new_steps": int(getattr(world, "max_steps", 64)),
                    "max_repair_actions": 1,
                    "token_budget": remaining_tokens,
                },
            )
        )

    @staticmethod
    def _planning_decision_package_version(body: Mapping[str, Any]) -> int:
        """The package version a sealed request states, as the durable integer.

        Refuse malformed values and booleans instead of letting ``int()`` coerce or
        crash at the store boundary.
        """

        raw = body.get("package_version")
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise ContractError(f"unpairable planning package_version: {raw!r}")
        return raw

    def _bind_hierarchical_planning_request(
        self,
        *,
        intent: DispatchIntent,
        mission: Mission,
        new_mode: HierarchicalDispatch,
        package: Any,
        template: Any,
        request_id: str | None = None,
    ) -> None:
        """Persist the exact request facts a decision will be admitted against.

        The package is already sealed before the intent is created.  The intent id is
        therefore the durable request identity; replaying an idempotent intent can
        safely replay this insert as well.  Every hash is derived from the same
        package/template/world snapshot sent to the provider; no empty context is
        substituted for an unavailable field.
        """

        stored = current_planning_protocol(self.store, mission.id)
        body = package.package
        protocol = body.get("planning_protocol")
        if (
            not isinstance(protocol, Mapping)
            or protocol.get("protocol") != stored["protocol_version"]
        ):
            raise ContractError("planning package is missing its durable protocol binding")
        epochs = new_mode.scope_epochs(mission.id)
        latest = new_mode.semantics().latest_requirements_revision(mission.id)
        binding = PlanningRequestBinding(
            request_id=intent.intent_id if request_id is None else request_id,
            mission_id=mission.id,
            protocol_version=stored["protocol_version"],
            package_version=self._planning_decision_package_version(body),
            package_hash=package_hash(body),
            base_plan_revision=int(body["views"]["plans"][0]["plan_revision"]),
            requirements_revision=0 if latest is None else int(latest.revision),
            scope_epoch_digest=planning_scope_digest(epochs),
            subject_bindings_hash=subject_bindings_hash(body.get("planning_subjects", ())),
            visible_refs_digest=visible_refs_digest(body.get("visible_refs", ())),
            prompt_version=template.prompt_version,
            prompt_hash=sha256_hex(template.instructions),
            created_at=intent.created_at,
            intent_id=intent.intent_id,
        )
        decisions = planning_decision_store.PlanningDecisionStore(self.store)
        if request_id is None or request_id == intent.intent_id:
            decisions.insert_planning_request(binding)
            return
        existing = decisions.get_planning_request(request_id)
        if existing is None:
            raise ContractError(
                f"planning request {request_id!r} is not persisted; retry cannot rebind"
            )
        # The retry may only move the answering intent.  Every request fact is
        # frozen from the opener, including created_at and all package/prompt hashes.
        candidate = replace(
            binding,
            request_id=existing.request_id,
            intent_id=existing.intent_id,
            created_at=existing.created_at,
        )
        if candidate != existing:
            raise ContractError(f"planning request {request_id!r} changed while retry was pending")
        try:
            decisions.rebind_planning_request_intent(
                request_id=request_id,
                expected_opener_intent_id=existing.intent_id,
                retry_intent_id=intent.intent_id,
            )
        except StoreConflict as error:
            raise ContractError(str(error)) from error

    def _planning_request_retry_id(
        self, *, intent: DispatchIntent, mission: Mission, new_mode: HierarchicalDispatch
    ) -> str | None:
        """Find the opener request for the one permitted unreadable format retry."""

        return self._planning_request_retry_id_for_ordinal(
            mission=mission,
            new_mode=new_mode,
            ordinal=int(intent.config.get("ordinal", 1)),
        )

    def _planning_request_retry_id_for_ordinal(
        self, *, mission: Mission, new_mode: HierarchicalDispatch, ordinal: int
    ) -> str | None:
        """Find the opener request before constructing a retry package.

        A format retry answers the same durable request.  The package therefore has to
        be rendered with the opener's ordinal; rendering the new attempt ordinal would
        change ``planning_attempt`` (and the package hash) before the request can be
        rebound, which is precisely the contract error this gate is meant to prevent.
        """

        del new_mode

        existing = self.store.get_intent_for_subject(f"{mission.id}:planner:{ordinal}")
        if existing is not None and existing.config.get("planning_decision_attempt_ordinal") == 1:
            bound = planning_decision_store.PlanningDecisionStore(
                self.store
            ).get_planning_request_for_intent(existing.intent_id)
            if bound is None:
                raise ContractError("format retry lost its original planning request")
            return bound.request_id
        if ordinal < 2:
            return None
        opener = self.store.get_intent_for_subject(f"{mission.id}:planner:{ordinal - 1}")
        if opener is None or self._planning_decision_attempt_ordinal(opener) != 0:
            return None
        decisions = planning_decision_store.PlanningDecisionStore(self.store)
        binding = decisions.get_planning_request(opener.intent_id)
        if binding is None:
            return None
        row = decisions.get_planning_decision_by_attempt(binding.request_id, 0)
        if row is None or row["status"] != str(PlanningDecisionStatus.UNREADABLE):
            return None
        return binding.request_id

    @staticmethod
    def _planning_decision_attempt_ordinal(intent: DispatchIntent) -> int:
        # New requests have their own 0/1 syntax ladder. Historical intents retain
        # their original global ordinal so existing decision IDs/replays never move.
        value = intent.config.get("planning_decision_attempt_ordinal")
        if value is None:
            return max(0, int(intent.config.get("ordinal", 1)) - 1)
        if type(value) is not int or value not in (0, 1):
            raise ContractError("invalid request-local planning attempt ordinal")
        return value

    def _planning_format_retry_remaining(self, *, intent: DispatchIntent, mission: Mission) -> int:
        """The same-request format retries left (one per request)."""

        return max(0, 1 - self._planning_decision_attempt_ordinal(intent))

    def _format_retry_exhausted(
        self, *, intent: DispatchIntent, reason: str, mission: Mission
    ) -> bool:
        return (
            reason == "proposal_unreadable"
            and self._planning_format_retry_remaining(intent=intent, mission=mission) == 0
        )

    def _hierarchical_admission_context(
        self,
        *,
        intent: DispatchIntent,
        mission: Mission,
        new_mode: HierarchicalDispatch,
        raw_text: str,
        include_plan_sources: bool = True,
    ) -> Any:
        """Assemble admission from the request snapshot and the live HTN world."""

        body = intent.config.get("planning_package")
        if not isinstance(body, Mapping):
            raise ContractError("planning intent has no sealed planning package")
        decision_store = planning_decision_store.PlanningDecisionStore(self.store)
        request = decision_store.get_planning_request_for_intent(intent.intent_id)
        if request is None:
            raise ContractError(f"planning request {intent.intent_id!r} is not persisted")
        current_planning_protocol(self.store, mission.id)

        network = new_mode.network(mission.id)
        world = new_mode.require_planning_world()
        operation_snapshot = None
        # Authority and operation producers are read through one deferred view.  The
        # readers themselves also use ``read_view``; Store joins that nested view to
        # this connection, so the two snapshots cannot straddle a concurrent write.
        with self.store.read_view():
            if not resume_source_current(self, intent, mission):
                return AuthoritySourceUnavailable(
                    "runtime_resume_binding", "runtime wake or its authoritative sources changed"
                )
            try:
                request_authority_binding = PlanningAdmissionStore(self.store).get_request_binding(
                    request.request_id
                )
            except (sqlite3.Error, StoreError, json.JSONDecodeError):
                return AuthoritySourceUnavailable(
                    "request_authority_binding", "authoritative source could not be read"
                )
            bound_planner = (
                str(request_authority_binding.get("planner_principal_id"))
                if isinstance(request_authority_binding, Mapping)
                and request_authority_binding.get("planner_principal_id")
                else (intent.agent_id or self._owner)
            )
            authority = planning_authorization.build_planning_authorization(
                request.request_id,
                read=planning_authorization.StorePlanningAuthorityReader(
                    PlanningAdmissionStore(self.store), self.store
                ),
                caller=PlanPrincipal(
                    # The authority is bound to the durable planning request, not
                    # to the executor lease assigned to a particular retry intent.
                    # Reusing ``intent.agent_id`` here made a valid format retry fail
                    # closed as a different planner principal after a worker handoff.
                    principal_id=bound_planner,
                    scope_id="mission",
                ),
                policy=planning_authorization.planning_policy_for_mission(self.store, mission.id),
                now_ms=int(self.store.now * 1000),
            )
            if isinstance(authority, AuthoritySourceUnavailable):
                return authority
            if include_plan_sources:
                try:
                    operation_snapshot = planning_operations.build_operation_snapshot(
                        mission.id,
                        reader=planning_operations.StoreOperationReader(self.store),
                        now_ms=int(self.store.now * 1000),
                    )
                except OperationSourceUnavailable as error:
                    return AuthoritySourceUnavailable("operations", error.reason)
        subjects = tuple(dict(item) for item in body.get("planning_subjects", ()))
        visible_refs = tuple(PlanningRefV1.from_json(item) for item in body.get("visible_refs", ()))
        subject_by_occurrence = {
            str(item.get("occurrence_id")): str(item.get("subject_key")) for item in subjects
        }
        methods = self._method_views(
            mission,
            new_mode=new_mode,
            body=body,
            network=network,
            world=world,
            subject_by_occurrence=subject_by_occurrence,
        )

        active_instances = tuple(
            MethodInstanceView(
                subject_key=subject_by_occurrence.get(str(item.effective_goal_occurrence_id), ""),
                instance_id=str(item.instance_id),
                semantic_revision=max(1, int(item.plan_revision)),
                # A method_instance PlanningRef identifies the grounding, so its
                # content hash is the instance parameters digest, not the method
                # definition's content hash.
                content_hash=item.parameters_digest(),
            )
            for item in network.method_instances
            if item.instance_id in set(network.adopted_instance_ids)
            and subject_by_occurrence.get(str(item.effective_goal_occurrence_id))
        )
        capability_snapshot = world.capabilities()
        capability_records = tuple(getattr(capability_snapshot, "records", ()) or ())
        available_capabilities = frozenset(
            str(item.capability_id)
            for item in capability_records
            if bool(getattr(item, "available", False))
        )
        available_predicates = frozenset(
            str(item.predicate_ref.id) for item in world.predicates.signatures()
        )
        protocol = body.get("planning_protocol")
        # 2026-09-25: the package lists decision types and repair kinds separately;
        # admission keys rows as ``REPAIR/<kind>``, so translate back here (once).

        enabled = (
            internal_enablement_keys(
                protocol.get("enabled_decision_types", ()), protocol.get("enabled_repair_kinds", ())
            )
            if isinstance(protocol, Mapping)
            else frozenset()
        )
        enabled_effective = enabled or ENABLED_DECISIONS
        allowance = max(0, int(self._config.max_planning_attempts))
        remaining = max(0, allowance - self._planning_attempts(mission.id))
        epochs = new_mode.scope_epochs(mission.id)
        latest = new_mode.semantics().latest_requirements_revision(mission.id)
        current_package_hash = package_hash(body)
        prompt = self._hierarchical_planner_template(mission.id)

        evidence_observers, evidence_authorized = evidence_authority(world, authority)

        duties = ObligationStore(self.store)
        open_duties = {
            str(duty.obligation_id)
            for duty in duties.list_obligations(mission.id)
            if (
                account := duties.account(mission.id, ObligationId(str(duty.obligation_id)))
            ).has_admitted_demand
            and str(account.lifecycle) == "UNSATISFIED"
        }
        raw_hash = hash_raw_output(raw_text.encode("utf-8"))
        ordinal = self._planning_decision_attempt_ordinal(intent)
        return AdmissionContext(
            binding=request,
            decision_id=allocate_decision_id(
                request_id=request.request_id,
                attempt_ordinal=ordinal,
                raw_output_hash=raw_hash,
            ),
            planning_subjects=subjects,
            visible_refs=visible_refs,
            plan_revision=int(network.plan_revision),
            requirements_revision=0 if latest is None else int(latest.revision),
            scope_epoch_digest=planning_scope_digest(epochs),
            package_version=self._planning_decision_package_version(body),
            package_hash=current_package_hash,
            prompt_version=prompt.prompt_version,
            prompt_hash=sha256_hex(prompt.instructions),
            enabled_decision_types=enabled_effective,
            repair_allowed="REPAIR/REPLACE_METHOD" in enabled_effective,
            methods=tuple(methods),
            predicates=available_predicates,
            evidence_observers=evidence_observers,
            evidence_authorized_predicates=evidence_authorized,
            active_method_instances=active_instances,
            open_obligations=tuple(
                ref
                for ref in visible_refs
                if ref.kind is PlanningRefKind.OBLIGATION and ref.id in open_duties
            ),
            authorization=AuthorizationView(
                approval_granted=all(
                    item.authorization_granted for item in methods if item.requires_authorization
                ),
                planning_snapshot=authority,
            ),
            capabilities=CapabilityView(available=available_capabilities),
            budget=BudgetView(
                planning_rounds_remaining=remaining,
                bound_remaining=remaining,
                budget_available=mission.budget.max_tokens is None
                or int(mission.budget.max_tokens) > 0,
            ),
            operations=OperationStateView(
                unresolved_operations=()
                if operation_snapshot is None
                else operation_snapshot.unresolved,
                snapshot=operation_snapshot,
            ),
            plan_shape=PlanShapeView(),
            retry_budgets=PlanningRetryBudgetView(
                same_request_format_retries_remaining=self._planning_format_retry_remaining(
                    intent=intent, mission=mission
                ),
                planning_rounds_remaining=remaining,
                root_review_repairs_remaining=max(0, int(self._config.max_root_review_repairs)),
                repeated_failure_before_escalation_remaining=None,
            ),
            # 这个请求签过 PLAN 证书的证据，此刻还当前吗（只核不落库）
            planning_evidence_stale=planning_evidence_stale(
                self.commit, mission.id, request.request_id
            ),
        )

    def _method_views(
        self,
        mission: Mission,
        *,
        new_mode: HierarchicalDispatch,
        body: Mapping[Any, Any],
        network: TaskNetworkSnapshot,
        world: PlanningWorld,
        subject_by_occurrence: dict[str, str],
    ) -> list[MethodView]:
        signature_by_subject = {
            subject_by_occurrence[str(spec.occurrence_id)]: str(
                network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id
            )
            for spec in network.occurrences
            if str(spec.occurrence_id) in subject_by_occurrence
        }
        reports = new_mode.method_applicability(mission.id)
        report_by_method = {
            (
                str(item.goal_occurrence_id),
                str(getattr(item.method_ref, "method_id", "")),
                int(getattr(item.method_ref, "version", 0)),
                str(getattr(item.method_ref, "content_hash", "")),
            ): item.report
            for item in reports
        }
        methods: list[MethodView] = []
        for row in (body.get("views") or {}).get("methods", ()):
            if not isinstance(row, Mapping):
                continue
            raw_ref = row.get("method_ref")
            if not isinstance(raw_ref, Mapping):
                continue
            # the row quotes the method as a decision does: the reference quadruple
            ref = MethodRef(
                method_id=str(raw_ref["id"]),
                version=int(raw_ref["semantic_revision"]),
                content_hash=str(raw_ref["content_hash"]),
            )
            contract = world.registry.definition(ref)
            registration = world.registry.registration(ref)
            if contract is None or registration is None:
                raise ContractError(
                    f"planning package method {ref.method_id}@{ref.version} is missing "
                    "from the live registry"
                )
            applies_to = tuple(
                sorted(
                    subject
                    for subject, signature in signature_by_subject.items()
                    if signature == str(row.get("goal_signature_id", ""))
                )
            )
            schema = world.schemas.resolve(contract.parameter_schema_ref)
            if schema is None:
                raise ContractError(
                    f"planning package method {ref.method_id}@{ref.version} references "
                    f"an unavailable parameter schema {contract.parameter_schema_ref!s}"
                )
            required_parameters = tuple(
                sorted(str(field.name) for field in getattr(schema, "fields", ()))
            )
            predicates = tuple(
                sorted(
                    str(item.predicate_ref.id) for item in iter_predicates(contract.applicable_when)
                )
            )
            # several goals may share the method's type; the first one assessed against it
            # carries the report (a goal already refined is no longer assessed)
            report = next(
                (
                    found
                    for found in (
                        report_by_method.get(
                            (str(spec.occurrence_id), ref.method_id, ref.version, ref.content_hash)
                        )
                        for spec in network.occurrences
                        if str(spec.occurrence_id) in subject_by_occurrence
                        and subject_by_occurrence[str(spec.occurrence_id)] in applies_to
                    )
                    if found is not None
                ),
                None,
            )
            if report is None:
                raise ContractError(
                    f"planning package method {ref.method_id}@{ref.version} has no "
                    "authoritative applicability report"
                )
            truth = report.truth
            authorization = report.authorization
            if authorization is None:
                raise ContractError(
                    f"planning package method {ref.method_id}@{ref.version} has no "
                    "authoritative authorization report"
                )
            methods.append(
                MethodView(
                    method_id=ref.method_id,
                    version=ref.version,
                    content_hash=ref.content_hash,
                    status=registration.status
                    if isinstance(registration.status, MethodRegistryStatus)
                    else MethodRegistryStatus(str(registration.status)),
                    applies_to=applies_to,
                    required_parameters=required_parameters,
                    predicate_keys=predicates,
                    required_capabilities=tuple(
                        str(item) for item in contract.required_capabilities
                    ),
                    # Match grounding/refinement: an absent precondition needs
                    # no evidence gate. Its vacuous TRUE still grants no authority.
                    requires_authorization=bool(contract.applicable_when),
                    authorization_granted=bool(authorization.allowed),
                    precondition_truth=truth,
                )
            )
        return methods

    def _hierarchical_planner_template(self, mission_id: str) -> Any:
        """The hierarchical Planner's prompt: there is one.

        A deployment's frozen ``prompt_versions`` pins ``planner`` to a DAG-Planner
        version for the other mode; it says nothing here.  A Mission whose durable
        binding names a different package/prompt pair is refused by
        ``current_planning_protocol``, not served on other words.
        """

        current_planning_protocol(self.store, mission_id)
        return PLANNER_HIERARCHICAL

    def _hierarchical_worker_template(self, role: Any, mission_id: str) -> Any:
        """The Worker prompt that knows about output ports.

        A deployment's frozen ``prompt_versions`` pins ``worker`` to a DAG-mode version,
        and ``worker-v3`` never asks the model which port its files belong to — so the
        accept side would refuse every leaf for ``OUTPUT_PORT_UNCLAIMED``.  A role that
        already is a hierarchical Worker (a domain's own) is kept; anything else is a
        choice made for the other mode and is replaced by the domain's hierarchical
        Worker.
        """

        if role.name != "worker":
            return role
        if role.prompt_version in hierarchical_worker_versions():
            return role
        # P2.3d / defect D1: the fallback is the *domain's* hierarchical Worker, not a
        # constant.  Returning ``WORKER_HIERARCHICAL`` unconditionally threw away the
        # AppWorld template ``role_for_task`` had just selected and with it both
        # ``appworld_execute`` (``effective_tools`` walks ``role_tools``, so a tool the
        # role does not list is dropped however many other sets hold it) and the words
        # saying there is a simulated world — 20 of the Grok run's L1 episodes reported
        # ``tool_not_exposed`` and did nothing.  A domain that registers no hierarchical
        # Worker still falls back to the code-domain one.
        return hierarchical_worker_for_domain(self.commit.domain_for(mission_id))

    async def _create_planner_intent(self, mission_id: str, *, ordinal: int) -> DispatchIntent:
        # The synchronous core never yields. Keep semantic outcomes, task state,
        # package hashes, reservation and binding on one snapshot for normal asks
        # as well as WAIT wakeups (which already own an outer transaction).
        with self.store.transaction():
            return self._create_planner_intent_now(mission_id, ordinal=ordinal)

    def _create_planner_intent_now(self, mission_id: str, *, ordinal: int) -> DispatchIntent:
        """Build the package and persist its intent without an async suspension.

        WAIT wakeup joins these writes to its own transaction, including the request
        binding and budget reservation. No provider call is made here.
        """
        mission = self.store.get_mission(mission_id)
        assert mission is not None
        new_mode = self._new_mode(mission)
        if new_mode is None:
            raise ContractError("hierarchical_assembly_missing: no planner without the assembly")
        source_binding = self._active_source_binding(mission_id)
        retry_request_id = self._planning_request_retry_id_for_ordinal(
            mission=mission, new_mode=new_mode, ordinal=ordinal
        )
        retry_package_frozen = False

        bind_deployment(self, mission_id)
        # A format retry keeps the request identity and all request facts frozen;
        # use the opener's package ordinal so its package hash remains identical.
        original_intent = (
            None if retry_request_id is None else self.store.get_intent(retry_request_id)
        )
        if retry_request_id is not None and original_intent is None:
            raise ContractError("format retry opener is unavailable")
        package_ordinal = (
            ordinal if original_intent is None else int(original_intent.config["ordinal"])
        )
        package = self._hierarchical_planner_package(
            new_mode,
            mission,
            ordinal=package_ordinal,
            # A format retry answers the opener's frozen package (rehydrated below);
            # only a fresh request is told about the last refusal in its package.
            previous_feedback=(
                None if retry_request_id is not None else self._latest_planning_feedback(mission)
            ),
        )
        if retry_request_id is not None:
            # A format retry answers the opener's exact durable request.  The
            # rejection event and H3's durable selection ledger may change what a
            # fresh collector would render, so rehydrate the opener package and
            # provider message before rebinding the retry intent.

            opener_binding = planning_decision_store.PlanningDecisionStore(
                self.store
            ).get_planning_request(retry_request_id)
            opener = (
                None if opener_binding is None else self.store.get_intent(opener_binding.intent_id)
            )
            opener_package = None if opener is None else opener.config.get("planning_package")
            opener_message = None if opener is None else opener.config.get("message")
            if (
                opener is not None
                and isinstance(opener_package, Mapping)
                and isinstance(opener_message, Mapping)
                and isinstance(opener_message.get("content"), str)
            ):
                package = TaskPackage(
                    text=str(opener_message["content"]),
                    context_version=str(
                        opener.config.get("context_version", package.context_version)
                    ),
                    package=dict(opener_package),
                )
                retry_package_frozen = True
        template = self._hierarchical_planner_template(mission_id)
        decision = self._route_service("planner", mission_id)
        config = AgentConfig(
            name=f"planner-{ordinal}",
            instructions=template.instructions,
            model_profile_ref=decision.profile_id,
            tool_names=(),
            limits=AgentLimits(
                max_model_calls_per_turn=4,
                max_tool_calls_per_turn=1,
                turn_deadline_seconds=self._config.turn_deadline_seconds,
            ),
        )
        package_text = package.text
        if retry_package_frozen and retry_request_id is not None:
            # 2026-09-30 格式三件：同一请求的格式重试原来一字不差重发原消息，模型不知道错在
            # 哪、照样再错。包仍冻结不变（请求事实不动）；只在消息末尾附上上一次的字段路径反馈。

            feedback = feedback_from_decision(
                planning_decision_store.PlanningDecisionStore(
                    self.store
                ).get_planning_decision_by_attempt(retry_request_id, 0),
                budgets=self._planning_retry_budgets(mission, format_retries=0),
            )
            if feedback is not None:
                package_text += (
                    "\n\n上一次回复被拒（这是同一个请求的格式重试，也是最后一次）。previous_feedback: "
                    + json.dumps(feedback.to_json(), ensure_ascii=False, sort_keys=True)
                    + "\n按 problems 里的 field_path 改正，重新输出完整的 <planning_decision> 块。"
                )

        native_decision = infrastructure_retry(package.package)
        message = user_message_json(package_text)
        subject = f"{mission_id}:planner:{ordinal}"

        intent = self.commit.create_service_intent(
            kind="plan",
            subject_id=subject,
            mission_id=mission_id,
            account_id=mission_account(mission_id),
            creation_key=subject,
            input_id="attempt-input",
            input_hash=sha256_hex(message),
            config={
                "agent_config": config.to_json(),
                "message": message,
                "context_version": package.context_version,
                "prompt_version": template.prompt_version,
                "base_version": mission.version,
                "ordinal": ordinal,
                "planning_decision_attempt_ordinal": 1 if retry_request_id is not None else 0,
                **planner_binding(self.store, mission_id),
                **(
                    {"native_planning_decision": native_decision}
                    if native_decision is not None
                    else {}
                ),
                "planning_package": dict(package.package),
                **source_binding,
                **self._service_config(decision),
            },
            reservation=self._reservation(
                0 if native_decision is not None else self._config.planner_reserve_tokens
            ),
        )
        self._bind_hierarchical_planning_request(
            intent=intent,
            mission=mission,
            new_mode=new_mode,
            package=package,
            template=template,
            request_id=retry_request_id,
        )
        if retry_request_id is None:
            # 把知识与核对过的摘要当事实交给规划器，就是一次 PLAN 使用——同一事务里
            # 签证书；签不出（证据此刻不当前、时钟不可信、授权变了）这一轮不开，回滚后下一轮重来。
            # 格式重试沿用开头那次请求冻结的包，它的回复照样在准入时复核这张证书的证据。

            require_point_use_locked(
                self.commit,
                mission_id=mission_id,
                purpose="PLAN",
                consumer_kind=PLANNING_REQUEST_CONSUMER,
                consumer_id=intent.intent_id,
                claims=planning_claims(package.package),
                subject=("requirements", mission_id),
            )
        self._planning.planning_evidence_waits.pop(mission_id, None)
        return intent

    # -------------------------------------------------------------- dispatch
    def _reservation(self, tokens: int) -> Reservation:
        return Reservation(tokens=tokens)

    def _first_critic_reservation(self, budget: FirstRequestBudget) -> Reservation:
        return Reservation(tokens=budget.minimum_tokens)
