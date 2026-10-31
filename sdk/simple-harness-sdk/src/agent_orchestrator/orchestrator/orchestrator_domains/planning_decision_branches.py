# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Planning decision branches: repairs, routed requests, evidence answers and refusals."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from ...assurance.codec import AssuranceError
from ...assurance.refs import Pin as AssurancePin
from ...contracts import ContractError
from ...contracts.error_table import SharingRefused
from ...contracts.planning_decisions import (
    PlanningDecisionRejectionCode,
    PlanningDecisionStatus,
    PlanningDecisionType,
    PlanningRefKind,
    ProposeMethodDecision,
    ReadMethodLibraryDecision,
    RepairRetrySameMethodDecision,
    RepairRuntimeBlockedDecision,
    RequestEvidenceDecision,
    RequestHumanDecision,
)
from ...governance.planning_authorization import SourceUnavailable as AuthoritySourceUnavailable
from ...planning import plan_preview
from ...planning.decision_admission import (
    NoMutationDecision,
    PreAdmittedPlanningDecision,
    pre_admit_planning_decision,
)
from ...planning.decision_codec import PlanningDecisionCodecError
from ...planning.decision_feedback import refusal_text
from ...planning.htn.planner_package import attempt_is_indexed
from ...runtime.planning_operations import SourceUnavailable as RetrySourceUnavailable
from ...storage.store import StoreError
from ..hierarchical_dispatch import append_hierarchical_event
from ..planning_evidence import observe_questions, persist_questions, prepare_questions
from ..planning_method_proposal import (
    MethodProposalRefused,
    decode_proposal,
    persist_method,
    prepare_method,
    proposal_origin,
    read_library,
)
from ..planning_runtime_block import register_block
from .core import OrchestratorCore
from .planning_decisions import (
    PlanningReply,
    _AssuranceReviewUnavailable,
    _commit_refusal_outcome,
    _commit_rejection_code,
)

if TYPE_CHECKING:
    from ...contracts.planning_decisions import PlanningDecisionEnvelopeV1


class PlanningDecisionBranchMixin(OrchestratorCore):
    """Planning decision branches: repairs, routed requests, evidence answers and refusals."""

    __slots__ = ()

    async def _reject_shared_refusal(
        self,
        reply: PlanningReply,
        *,
        decision: PlanningDecisionEnvelopeV1,
        canonical_json: str,
        canonical_hash: str,
        error: SharingRefused,
        codes: Sequence[str],
        refusal_status: PlanningDecisionStatus,
        evaluated: Callable[..., None],
    ) -> None:
        detail = {
            "problems": [
                {
                    "code": codes[0],
                    "detail": str(error.code),
                    "field_path": "/payload",
                    "subject_ref": None,
                }
            ]
        }
        reply.store.record_planning_decision(
            request_id=reply.request_id,
            attempt_ordinal=reply.attempt_ordinal,
            raw_output_hash=reply.raw_hash,
            raw_artifact_ref=reply.raw_artifact_ref,
            decision_id=reply.decision_id,
            status=refusal_status,
            rejection_codes=codes,
            detail=detail,
            canonical_json=canonical_json,
            canonical_hash=canonical_hash,
            decision_type=str(decision.decision_type),
        )
        evaluated(
            refusal_status,
            decision_type=str(decision.decision_type),
            rejection_codes=codes,
            detail=detail,
        )
        self._settle_intent(reply.intent, "FAILED")
        self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
        await self._planning_rejected(
            reply.intent,
            reason="proposal_not_grounded",
            detail={"rejection_codes": codes, **detail},
        )
        return

    async def _reject_undecodable_reply(
        self,
        reply: PlanningReply,
        error: PlanningDecisionCodecError,
        exhausted_fact: str | None,
        evaluated: Callable[..., None],
    ) -> None:
        retry_remaining = self._planning_format_retry_remaining(
            intent=reply.intent, mission=reply.mission
        )
        error_text = exhausted_fact or error.detail or str(error)
        exhausted = {"output_exhausted": True} if exhausted_fact else {}
        reply.store.record_planning_decision(
            request_id=reply.request_id,
            attempt_ordinal=reply.attempt_ordinal,
            raw_output_hash=reply.raw_hash,
            raw_artifact_ref=reply.raw_artifact_ref,
            decision_id=reply.decision_id,
            status=PlanningDecisionStatus.UNREADABLE,
            rejection_codes=(str(error.code),),
            detail={"error": error_text, **exhausted},
        )
        evaluated(
            PlanningDecisionStatus.UNREADABLE,
            rejection_codes=[str(error.code)],
            detail=error_text,
            **exhausted,
        )
        self._settle_intent(reply.intent, "FAILED")
        self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
        await self._planning_rejected(
            reply.intent,
            reason="proposal_unreadable",
            detail={
                "error": error_text,
                "format_retry_remaining": retry_remaining,
                # 答错计数要知道是哪个码、本请求第几次回答（第 2 条：第一次读不懂免扣）
                "rejection_codes": [str(error.code)],
                "request_attempt_ordinal": int(reply.attempt_ordinal),
                **exhausted,
            },
        )
        return

    def _reject_unavailable_authority(
        self,
        reply: PlanningReply,
        *,
        decision: PlanningDecisionEnvelopeV1,
        canonical_json: str,
        canonical_hash: str,
        context: AuthoritySourceUnavailable,
        refusal_status: PlanningDecisionStatus,
        evaluated: Callable[..., None],
    ) -> None:
        code = _commit_rejection_code(context.reason_code)
        detail = {
            "reason": context.reason_code,
            "source": context.source,
            "error": context.detail,
        }
        with self.store.transaction():
            reply.store.record_planning_decision(
                request_id=reply.request_id,
                attempt_ordinal=reply.attempt_ordinal,
                raw_output_hash=reply.raw_hash,
                raw_artifact_ref=reply.raw_artifact_ref,
                decision_id=reply.decision_id,
                status=refusal_status,
                rejection_codes=(code,),
                detail=detail,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                decision_type=str(decision.decision_type),
            )
            evaluated(
                refusal_status,
                decision_type=str(decision.decision_type),
                rejection_codes=[code],
                detail=detail,
            )
            self._settle_intent(reply.intent, "FAILED")
            self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
            self.commit.record_planning_rejected(
                reply.mission.id,
                ordinal=int(reply.intent.config.get("ordinal", 1)),
                reason="planning_source_unavailable",
                detail=detail,
            )
        return

    async def _repair_runtime_blocked(
        self,
        reply: PlanningReply,
        *,
        decision: PlanningDecisionEnvelopeV1,
        canonical_json: str,
        canonical_hash: str,
        evaluated: Callable[..., None],
    ) -> None:
        assert isinstance(decision.payload, RepairRuntimeBlockedDecision)  # 调用方按这个类型分支
        try:
            with self.store.transaction():
                current = self._hierarchical_admission_context(
                    intent=reply.intent,
                    mission=reply.mission,
                    new_mode=reply.new_mode,
                    raw_text=reply.text,
                    include_plan_sources=False,
                )
                if isinstance(current, AuthoritySourceUnavailable):
                    raise ContractError("runtime-block authority is unavailable")
                checked = pre_admit_planning_decision(decision, context=current)
                if not isinstance(checked, PreAdmittedPlanningDecision):
                    raise ContractError("runtime-block request is no longer admitted")
                detail = register_block(
                    self,
                    reply.mission,
                    payload=decision.payload,
                    package=reply.intent.config.get("planning_package"),
                    decision_id=reply.decision_id,
                    canonical_hash=canonical_hash,
                )
                reply.store.record_planning_decision(
                    request_id=reply.request_id,
                    attempt_ordinal=reply.attempt_ordinal,
                    raw_output_hash=reply.raw_hash,
                    raw_artifact_ref=reply.raw_artifact_ref,
                    decision_id=reply.decision_id,
                    status=PlanningDecisionStatus.NO_STATE_CHANGE,
                    rejection_codes=(),
                    detail=detail,
                    canonical_json=canonical_json,
                    canonical_hash=canonical_hash,
                    decision_type=str(decision.decision_type),
                )
                evaluated(
                    PlanningDecisionStatus.NO_STATE_CHANGE,
                    decision_type=str(decision.decision_type),
                    detail=detail,
                )
                self._settle_intent(reply.intent, "SETTLED")
                self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
        except (ContractError, StoreError) as error:
            with self.store.transaction():
                detail = {"error": str(error), "repair_kind": "DECLARE_RUNTIME_BLOCKED"}
                reply.store.record_planning_decision(
                    request_id=reply.request_id,
                    attempt_ordinal=reply.attempt_ordinal,
                    raw_output_hash=reply.raw_hash,
                    raw_artifact_ref=reply.raw_artifact_ref,
                    decision_id=reply.decision_id,
                    status=PlanningDecisionStatus.REJECTED,
                    rejection_codes=("REPAIR_NOT_ALLOWED",),
                    detail=detail,
                    canonical_json=canonical_json,
                    canonical_hash=canonical_hash,
                    decision_type=str(decision.decision_type),
                )
                evaluated(
                    PlanningDecisionStatus.REJECTED,
                    decision_type=str(decision.decision_type),
                    rejection_codes=["REPAIR_NOT_ALLOWED"],
                    detail=detail,
                )
                self._settle_intent(reply.intent, "FAILED")
                self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
            await self._planning_rejected(
                reply.intent, reason="proposal_not_grounded", detail=detail
            )
        return

    async def _repair_retry_same_method(
        self,
        reply: PlanningReply,
        *,
        decision: PlanningDecisionEnvelopeV1,
        canonical_json: str,
        canonical_hash: str,
        evaluated: Callable[..., None],
    ) -> None:
        assert isinstance(decision.payload, RepairRetrySameMethodDecision)  # 调用方按这个类型分支
        try:
            with self.store.transaction():
                current = self._hierarchical_admission_context(
                    intent=reply.intent,
                    mission=reply.mission,
                    new_mode=reply.new_mode,
                    raw_text=reply.text,
                    include_plan_sources=False,
                )
                if isinstance(current, AuthoritySourceUnavailable):
                    raise ContractError("retry authority is unavailable")
                checked = pre_admit_planning_decision(decision, context=current)
                if not isinstance(checked, PreAdmittedPlanningDecision):
                    raise ContractError("retry decision is no longer admitted")

                if not attempt_is_indexed(
                    reply.intent.config.get("planning_package") or {},
                    decision.payload.failed_attempt_id,
                ):
                    raise ContractError("retry Attempt was not visible in this frozen request")
                detail = self.commit.authorize_planning_retry(
                    mission_id=reply.mission.id,
                    task_id=str(checked.subject["task_id"]),
                    payload=decision.payload,
                    expected_plan_revision=current.plan_revision,
                    decision_id=reply.decision_id,
                    canonical_hash=canonical_hash,
                    request_id=reply.request_id,
                )
                reply.store.record_planning_decision(
                    request_id=reply.request_id,
                    attempt_ordinal=reply.attempt_ordinal,
                    raw_output_hash=reply.raw_hash,
                    raw_artifact_ref=reply.raw_artifact_ref,
                    decision_id=reply.decision_id,
                    status=PlanningDecisionStatus.COMMITTED,
                    rejection_codes=(),
                    detail=detail,
                    canonical_json=canonical_json,
                    canonical_hash=canonical_hash,
                    decision_type=str(decision.decision_type),
                )
                evaluated(
                    PlanningDecisionStatus.COMMITTED,
                    decision_type=str(decision.decision_type),
                    detail=detail,
                )
                self._settle_intent(reply.intent, "SETTLED")
                self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
        except (ContractError, StoreError, RetrySourceUnavailable) as error:
            with self.store.transaction():
                detail = {"error": str(error), "repair_kind": "RETRY_SAME_METHOD"}
                reply.store.record_planning_decision(
                    request_id=reply.request_id,
                    attempt_ordinal=reply.attempt_ordinal,
                    raw_output_hash=reply.raw_hash,
                    raw_artifact_ref=reply.raw_artifact_ref,
                    decision_id=reply.decision_id,
                    status=PlanningDecisionStatus.REJECTED,
                    rejection_codes=("REPAIR_NOT_ALLOWED",),
                    detail=detail,
                    canonical_json=canonical_json,
                    canonical_hash=canonical_hash,
                    decision_type=str(decision.decision_type),
                )
                evaluated(
                    PlanningDecisionStatus.REJECTED,
                    decision_type=str(decision.decision_type),
                    rejection_codes=["REPAIR_NOT_ALLOWED"],
                    detail=detail,
                )
                self._settle_intent(reply.intent, "FAILED")
                self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
            await self._planning_rejected(
                reply.intent, reason="proposal_not_grounded", detail=detail
            )
        return

    async def _route_method_or_human_request(
        self,
        reply: PlanningReply,
        *,
        decision: PlanningDecisionEnvelopeV1,
        canonical_json: str,
        canonical_hash: str,
        evaluated: Callable[..., None],
    ) -> None:
        prepared_method = None
        try:
            with self.store.transaction():
                current = self._hierarchical_admission_context(
                    intent=reply.intent,
                    mission=reply.mission,
                    new_mode=reply.new_mode,
                    raw_text=reply.text,
                    include_plan_sources=False,
                )
                if isinstance(current, AuthoritySourceUnavailable):
                    raise ContractError("planning service authority is unavailable")
                checked = pre_admit_planning_decision(decision, context=current)
                if not isinstance(checked, PreAdmittedPlanningDecision):
                    raise ContractError("planning service request is no longer admitted")
                if isinstance(decision.payload, RequestHumanDecision):
                    question, service_detail = self._register_human_question(
                        reply.mission,
                        reply.new_mode,
                        decision_id=reply.decision_id,
                        subject_key=decision.subject_key,
                        payload=decision.payload,
                        current=current,
                        next_ordinal=int(reply.intent.config.get("ordinal", 1)) + 1,
                        repair_context=None,
                    )
                    event_type = "PlanningHumanRequested"
                elif isinstance(decision.payload, ReadMethodLibraryDecision):
                    # 只读（阶段 C3）：记一条读取事件，下一轮规划包带上原文
                    service_detail = read_library(reply.new_mode, reply.mission, decision.payload)
                    event_type = "PlanningLibraryRead"
                else:
                    assert isinstance(decision.payload, ProposeMethodDecision)
                    prepared_method = prepare_method(
                        reply.new_mode, reply.mission.id, decision.payload, checked.subject
                    )
                    # 每个目标最多提几次是按这条事件里的目标任务数的，所有通道都要带。
                    # 来源（阶段 C3）：写这个做法时的类型目录哈希、它参照的全库做法
                    service_detail = {
                        **persist_method(reply.new_mode, *prepared_method),
                        "subject_task_id": str(checked.subject["task_id"]),
                        **proposal_origin(
                            reply.new_mode, reply.mission.id, decode_proposal(decision.payload)
                        ),
                    }
                    event_type = "PlanningMethodProposed"
                    # BW03: the admitted draft gets its independent METHOD_PLAN
                    # review on the round transport, authored by this intent.

                    if self._wiring.assurance_reviews is None:
                        raise ContractError(
                            "Assurance review builder is not installed for METHOD_PLAN"
                        )
                    method_reference = prepared_method[1].method_ref()
                    # 审阅以被规划的目标为归属任务；根目标在第一份计划前还没有任务行。
                    subject_spec = next(
                        spec
                        for spec in reply.new_mode.network(reply.mission.id).occurrences
                        if str(spec.task_id) == str(checked.subject["task_id"])
                    )
                    self.commit.materialise_planning_subject(reply.mission.id, subject_spec)
                    try:
                        review = self._wiring.assurance_reviews.ensure_method_plan(
                            reply.mission,
                            task_id=str(checked.subject["task_id"]),
                            method_ref=AssurancePin(
                                method_reference.method_id,
                                int(method_reference.version),
                                method_reference.content_hash,
                            ),
                            producer_agent_ids=(str(reply.intent.agent_id or ""),),
                        )
                    except AssuranceError as error:
                        raise _AssuranceReviewUnavailable("METHOD_PLAN", error) from error
                    service_detail = {
                        **service_detail,
                        "assurance_review_key": review.to_json()["review_key"],
                    }
                reply.store.record_planning_decision(
                    request_id=reply.request_id,
                    attempt_ordinal=reply.attempt_ordinal,
                    raw_output_hash=reply.raw_hash,
                    raw_artifact_ref=reply.raw_artifact_ref,
                    decision_id=reply.decision_id,
                    status=PlanningDecisionStatus.NO_STATE_CHANGE,
                    rejection_codes=(),
                    detail=service_detail,
                    canonical_json=canonical_json,
                    canonical_hash=canonical_hash,
                    decision_type=str(decision.decision_type),
                )
                evaluated(
                    PlanningDecisionStatus.NO_STATE_CHANGE,
                    decision_type=str(decision.decision_type),
                    detail=service_detail,
                )
                append_hierarchical_event(
                    self.store,
                    event_type,
                    reply.mission.id,
                    key=reply.decision_id,
                    payload={
                        "decision_id": reply.decision_id,
                        "next_ordinal": int(reply.intent.config.get("ordinal", 1)) + 1,
                        **service_detail,
                    },
                )
                self._settle_intent(reply.intent, "SETTLED")
                self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
        except (ContractError, StoreError) as error:
            # An admitted proposal whose independent METHOD_PLAN review cannot be
            # opened (no approved check policy, review runtime unavailable) is not
            # a malformed proposal: it waits for authorization, and the record
            # says so instead of blaming the Planner.
            await self._reject_unroutable_request(
                reply,
                decision=decision,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                error=error,
                evaluated=evaluated,
            )
            return
        if prepared_method is not None:
            _, contract, registration = prepared_method
            reply.new_mode.require_planning_world().registry.restore(contract, registration)
        return

    async def _reject_unroutable_request(
        self,
        reply: PlanningReply,
        *,
        decision: PlanningDecisionEnvelopeV1,
        canonical_json: str,
        canonical_hash: str,
        error: ContractError | StoreError,
        evaluated: Callable[..., None],
    ) -> None:
        error_detail: dict[str, Any]
        code, reason, error_detail = (
            "PARAMETER_INVALID",
            "proposal_not_grounded",
            {"error": str(error)},
        )

        if isinstance(error, MethodProposalRefused):
            # 被拒的做法草案把每条可修正的问题原样列出。
            code = error.code
            error_detail = {
                "error": "method proposal refused",
                "problems": error.feedback(),
            }
        if isinstance(error, _AssuranceReviewUnavailable):
            code, reason = "AUTHORIZATION_REQUIRED", "assurance_review_unavailable"
            error_detail = {
                "error": str(error),
                "assurance_purpose": error.purpose,
                "assurance_error": error.code,
            }

            # 做法已被接受，只是开不了独立审阅（审阅线路不可用、时钟回拨……）：不是规划器答错，
            # 走服务故障宽限、不扣答错次数；宽限用完按运行环境不可用停（用户 2026-09-28）。
            error_detail["turn_failed"] = True
        with self.store.transaction():
            reply.store.record_planning_decision(
                request_id=reply.request_id,
                attempt_ordinal=reply.attempt_ordinal,
                raw_output_hash=reply.raw_hash,
                raw_artifact_ref=reply.raw_artifact_ref,
                decision_id=reply.decision_id,
                status=PlanningDecisionStatus.REJECTED,
                rejection_codes=(code,),
                detail=error_detail,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                decision_type=str(decision.decision_type),
            )
            evaluated(
                PlanningDecisionStatus.REJECTED,
                decision_type=str(decision.decision_type),
                rejection_codes=[code],
                detail=error_detail,
            )
            self._settle_intent(reply.intent, "FAILED")
            self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
        await self._planning_rejected(reply.intent, reason=reason, detail=error_detail)
        return

    async def _answer_evidence_request(
        self,
        reply: PlanningReply,
        *,
        decision: PlanningDecisionEnvelopeV1,
        canonical_json: str,
        canonical_hash: str,
        context: Any,
        evaluated: Callable[..., None],
    ) -> None:
        assert isinstance(decision.payload, RequestEvidenceDecision)
        try:
            world = reply.new_mode.require_planning_world()
            asks = prepare_questions(
                world, decision.payload, context.authorization.planning_snapshot
            )
            outcomes = observe_questions(world, asks, now_ms=int(self.store.now * 1000))
            with self.store.transaction():
                current = self._hierarchical_admission_context(
                    intent=reply.intent,
                    mission=reply.mission,
                    new_mode=reply.new_mode,
                    raw_text=reply.text,
                    include_plan_sources=False,
                )
                if isinstance(current, AuthoritySourceUnavailable):
                    raise ContractError("evidence authority is no longer available")
                checked = pre_admit_planning_decision(decision, context=current)
                if not isinstance(checked, PreAdmittedPlanningDecision):
                    raise ContractError("evidence request is no longer admitted")
                if (
                    prepare_questions(
                        world, decision.payload, current.authorization.planning_snapshot
                    )
                    != asks
                ):
                    raise ContractError(
                        "evidence predicate or authority changed during observation"
                    )
                evidence_result = persist_questions(
                    reply.new_mode.semantics(),
                    reply.mission.id,
                    asks,
                    outcomes,
                    scope_id=current.authorization.planning_snapshot.scope_id,
                )
                reply.store.record_planning_decision(
                    request_id=reply.request_id,
                    attempt_ordinal=reply.attempt_ordinal,
                    raw_output_hash=reply.raw_hash,
                    raw_artifact_ref=reply.raw_artifact_ref,
                    decision_id=reply.decision_id,
                    status=PlanningDecisionStatus.NO_STATE_CHANGE,
                    rejection_codes=(),
                    detail=evidence_result,
                    canonical_json=canonical_json,
                    canonical_hash=canonical_hash,
                    decision_type=str(decision.decision_type),
                )
                evaluated(
                    PlanningDecisionStatus.NO_STATE_CHANGE,
                    decision_type=str(decision.decision_type),
                    detail=evidence_result,
                )
                append_hierarchical_event(
                    self.store,
                    "PlanningEvidenceRecorded",
                    reply.mission.id,
                    key=reply.decision_id,
                    payload={
                        "decision_id": reply.decision_id,
                        "next_ordinal": int(reply.intent.config.get("ordinal", 1)) + 1,
                        **evidence_result,
                    },
                )
                self._settle_intent(reply.intent, "SETTLED")
                self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
        except (ContractError, StoreError) as error:
            with self.store.transaction():
                reply.store.record_planning_decision(
                    request_id=reply.request_id,
                    attempt_ordinal=reply.attempt_ordinal,
                    raw_output_hash=reply.raw_hash,
                    raw_artifact_ref=reply.raw_artifact_ref,
                    decision_id=reply.decision_id,
                    status=PlanningDecisionStatus.REJECTED,
                    rejection_codes=("EVIDENCE_REQUIRED",),
                    detail={"error": str(error)},
                    canonical_json=canonical_json,
                    canonical_hash=canonical_hash,
                    decision_type=str(decision.decision_type),
                )
                evaluated(
                    PlanningDecisionStatus.REJECTED,
                    decision_type=str(decision.decision_type),
                    rejection_codes=["EVIDENCE_REQUIRED"],
                    detail={"error": str(error)},
                )
                self._settle_intent(reply.intent, "FAILED")
                self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
            await self._planning_rejected(
                reply.intent, reason="proposal_not_grounded", detail={"error": str(error)}
            )
        return

    async def _settle_no_mutation_decision(
        self,
        reply: PlanningReply,
        *,
        decision: PlanningDecisionEnvelopeV1,
        canonical_json: str,
        canonical_hash: str,
        pre_admitted: NoMutationDecision,
        evaluated: Callable[..., None],
    ) -> None:
        with self.store.transaction():
            waited = (
                self._expand_planning_wait(reply.mission, tuple(pre_admitted.wait_for))
                if decision.decision_type is PlanningDecisionType.WAIT and pre_admitted.wait_for
                else None
            )
            invalid_wait = decision.decision_type is PlanningDecisionType.WAIT and not waited
            status = (
                PlanningDecisionStatus.REJECTED
                if invalid_wait
                else PlanningDecisionStatus.NO_STATE_CHANGE
            )
            codes = ("PARAMETER_INVALID",) if invalid_wait else ()
            detail = {"decision_type": str(decision.decision_type)}
            if invalid_wait:
                detail["reason"] = (
                    "WAIT target has no matching active producer or completed authoritative record"
                )
                if any(
                    ref.kind in {PlanningRefKind.METHOD_INSTANCE, PlanningRefKind.OBLIGATION}
                    for ref in pre_admitted.wait_for or ()
                ):
                    detail["reason"] += (
                        " (a method instance or duty may be waited on only while a step under"
                        " it is running; no step under it is running)"
                    )
            reply.store.record_planning_decision(
                request_id=reply.request_id,
                attempt_ordinal=reply.attempt_ordinal,
                raw_output_hash=reply.raw_hash,
                raw_artifact_ref=reply.raw_artifact_ref,
                decision_id=reply.decision_id,
                status=status,
                rejection_codes=codes,
                detail=detail,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                decision_type=str(decision.decision_type),
            )
            evaluated(
                status,
                decision_type=str(decision.decision_type),
                rejection_codes=list(codes),
                detail=detail,
                **(
                    {"wait_for": [ref.to_json() for ref in pre_admitted.wait_for]}
                    if decision.decision_type is PlanningDecisionType.WAIT
                    else {}
                ),
            )
            self._settle_intent(reply.intent, "FAILED" if invalid_wait else "SETTLED")
            self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
            if decision.decision_type is PlanningDecisionType.WAIT and not invalid_wait:
                append_hierarchical_event(
                    self.store,
                    "PlanningWaitRegistered",
                    reply.mission.id,
                    key=reply.decision_id,
                    payload={
                        "decision_id": reply.decision_id,
                        "request_id": reply.request_id,
                        "attempt_ordinal": reply.attempt_ordinal,
                        "canonical_hash": canonical_hash,
                        "wait_for": [ref.to_json() for ref in waited or ()],
                        **(
                            {"requested_wait_for": [ref.to_json() for ref in pre_admitted.wait_for]}
                            if tuple(waited or ()) != tuple(pre_admitted.wait_for)
                            else {}
                        ),
                        "reason": pre_admitted.reason,
                    },
                )
                self.store.fault("planning_wait_before_registration_commit")
        if invalid_wait:
            await self._planning_rejected(
                reply.intent,
                reason="proposal_not_grounded",
                detail={"rejection_codes": list(codes), **detail},
            )
            return
        return

    async def _reject_unavailable_preview(
        self,
        reply: PlanningReply,
        *,
        decision: PlanningDecisionEnvelopeV1,
        canonical_json: str,
        canonical_hash: str,
        preview: plan_preview.PreviewUnavailable,
        refusal_status: PlanningDecisionStatus,
        evaluated: Callable[..., None],
    ) -> None:
        try:
            preview_codes = tuple(
                str(PlanningDecisionRejectionCode(code)) for code in preview.mapped_problems
            ) or ("INTERNAL_CONTRACT_ERROR",)
        except (TypeError, ValueError):
            # A producer with an unknown reason cannot extend the wire
            # protocol through a durable decision or evaluation event.
            preview_codes = ("INTERNAL_CONTRACT_ERROR",)
        detail = {
            "reason": preview.reason,
            "detail": preview.detail,
            "mapped_problems": list(preview.mapped_problems),
        }
        if preview.problem_details:
            detail["problems"] = [item.to_json() for item in preview.problem_details]
        reply.store.record_planning_decision(
            request_id=reply.request_id,
            attempt_ordinal=reply.attempt_ordinal,
            raw_output_hash=reply.raw_hash,
            raw_artifact_ref=reply.raw_artifact_ref,
            decision_id=reply.decision_id,
            status=refusal_status,
            rejection_codes=preview_codes,
            detail=detail,
            canonical_json=canonical_json,
            canonical_hash=canonical_hash,
            decision_type=str(decision.decision_type),
        )
        evaluated(
            refusal_status,
            decision_type=str(decision.decision_type),
            rejection_codes=list(preview_codes),
            detail=detail,
        )
        self._settle_intent(reply.intent, "FAILED")
        self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
        await self._planning_rejected(
            reply.intent,
            reason="proposal_not_grounded",
            detail={"preview": detail},
        )
        return

    async def _reject_unadmitted_decision(
        self,
        reply: PlanningReply,
        *,
        decision: PlanningDecisionEnvelopeV1,
        canonical_json: str,
        canonical_hash: str,
        admitted: Any,
        refusal_status: PlanningDecisionStatus,
        evaluated: Callable[..., None],
    ) -> None:
        codes = [str(code) for code in admitted.rejection_codes]
        detail = {"problems": [item.to_json() for item in admitted.problems]}
        reply.store.record_planning_decision(
            request_id=reply.request_id,
            attempt_ordinal=reply.attempt_ordinal,
            raw_output_hash=reply.raw_hash,
            raw_artifact_ref=reply.raw_artifact_ref,
            decision_id=reply.decision_id,
            status=refusal_status,
            rejection_codes=codes,
            detail=detail,
            canonical_json=canonical_json,
            canonical_hash=canonical_hash,
            decision_type=str(decision.decision_type),
        )
        evaluated(
            refusal_status,
            decision_type=str(decision.decision_type),
            rejection_codes=codes,
            detail=detail,
        )
        self._settle_intent(reply.intent, "FAILED")
        self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
        await self._planning_rejected(
            reply.intent,
            reason="proposal_not_grounded",
            detail={"rejection_codes": codes, **detail},
        )
        return

    async def _reject_failed_commit(
        self,
        reply: PlanningReply,
        *,
        decision: PlanningDecisionEnvelopeV1,
        canonical_json: str,
        canonical_hash: str,
        error: Exception,
        evaluated: Callable[..., None],
    ) -> None:
        refusal_code = _commit_rejection_code(getattr(error, "reason", "INTERNAL_CONTRACT_ERROR"))
        reply.store.record_planning_decision(
            request_id=reply.request_id,
            attempt_ordinal=reply.attempt_ordinal,
            raw_output_hash=reply.raw_hash,
            raw_artifact_ref=reply.raw_artifact_ref,
            decision_id=reply.decision_id,
            status=PlanningDecisionStatus.COMMIT_REJECTED,
            rejection_codes=(refusal_code,),
            detail={"error": refusal_text(error)},
            canonical_json=canonical_json,
            canonical_hash=canonical_hash,
            decision_type=str(decision.decision_type),
        )
        evaluated(
            PlanningDecisionStatus.COMMIT_REJECTED,
            decision_type=str(decision.decision_type),
            rejection_codes=[refusal_code],
            detail=refusal_text(error),
        )
        self._release_refused_fence(reply.mission.id, reply.decision_id)
        self._settle_intent(reply.intent, "FAILED")
        self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
        named = getattr(error, "reason", None)
        if named is None and isinstance(error, StoreError):
            # 存储故障（如同号冲突）不是规划器答错
            named = PlanningDecisionRejectionCode.INTERNAL_CONTRACT_ERROR
        reason, extra = _commit_refusal_outcome(named)
        await self._planning_rejected(
            reply.intent,
            reason=reason,
            detail={"error": refusal_text(error), **extra},
        )
        return

    async def _reject_refused_plan(
        self,
        reply: PlanningReply,
        *,
        decision: PlanningDecisionEnvelopeV1,
        canonical_json: str,
        canonical_hash: str,
        plan_outcome: Any,
        evaluated: Callable[..., None],
    ) -> None:
        refusals = [item.to_json() for item in plan_outcome.refusals]
        refusal_code = _commit_rejection_code(
            plan_outcome.refusals[-1].reason if plan_outcome.refusals else "INTERNAL_CONTRACT_ERROR"
        )
        reply.store.record_planning_decision(
            request_id=reply.request_id,
            attempt_ordinal=reply.attempt_ordinal,
            raw_output_hash=reply.raw_hash,
            raw_artifact_ref=reply.raw_artifact_ref,
            decision_id=reply.decision_id,
            status=PlanningDecisionStatus.COMMIT_REJECTED,
            rejection_codes=(refusal_code,),
            detail={"refusals": refusals},
            canonical_json=canonical_json,
            canonical_hash=canonical_hash,
            decision_type=str(decision.decision_type),
        )
        evaluated(
            PlanningDecisionStatus.COMMIT_REJECTED,
            decision_type=str(decision.decision_type),
            rejection_codes=[refusal_code],
            detail={"refusals": refusals},
        )
        self._release_refused_fence(reply.mission.id, reply.decision_id)
        self._settle_intent(reply.intent, "FAILED")
        self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
        reason, extra = _commit_refusal_outcome(
            plan_outcome.refusals[-1].reason
            if plan_outcome.refusals
            else PlanningDecisionRejectionCode.INTERNAL_CONTRACT_ERROR
        )
        await self._planning_rejected(
            reply.intent,
            reason=reason,
            detail={"refusals": refusals, **extra},
        )
