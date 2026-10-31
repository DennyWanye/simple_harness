# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Collection of hierarchical planning decisions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, NamedTuple

from simple_harness.agents import AgentTurnState

from ...contracts import ContractError, Mission
from ...contracts.error_table import SharingRefused
from ...contracts.htn import (
    CancelBranchOperation,
    GraphStructureBudget,
    PlanProposal,
    ProposeSuccessorOperation,
    RebindInputOperation,
)
from ...contracts.models import sha256_hex
from ...contracts.planning_decisions import (
    PlanningDecisionRejectionCode,
    PlanningDecisionStatus,
    PlanningDecisionType,
    RepairRetrySameMethodDecision,
    RepairRuntimeBlockedDecision,
)
from ...governance.planning_authorization import SourceUnavailable as AuthoritySourceUnavailable
from ...graph.execution_contracts import PreviewBindingV1
from ...graph.planning_scope import planning_convergence_scope
from ...graph.projection_validation import GraphIntegrityError
from ...orchestrator.planning_admission_commits import PlanningCommitAdmission
from ...planning import plan_preview
from ...planning.decision_adapter import AdapterContext, adapt_admitted_decision, adapt_for_preview
from ...planning.decision_admission import (
    AdmittedPlanningDecision,
    DecisionAdmissionContext,
    NoMutationDecision,
    PlanShapeView,
    PreAdmittedPlanningDecision,
    _enablement_key,
    admit_planning_decision,
    pre_admit_planning_decision,
)
from ...planning.decision_codec import (
    PlanningDecisionCodecError,
    allocate_decision_id,
    canonical_decision_hash,
    canonical_decision_json,
    hash_raw_output,
    parse_planning_decision,
)
from ...planning.decision_feedback import package_filler, refusal_text
from ...planning.htn.backend_port import BackendStatus, PlanningBackendBridge, PlanningBackendResult
from ...runtime.planning_operations import SourceUnavailable as RuntimeSourceUnavailable
from ...runtime.planning_operations import StoreOperationReader, read_running_work
from ...storage import planning_decision_store
from ...storage.store import DispatchIntent, StoreConflict
from ...storage.taskgraph_binding import require_bound
from .. import planning_backend_runtime
from ..failure_classes import output_exhausted, output_exhausted_fact
from ..hierarchical_dispatch import (
    HierarchicalDispatch,
    PlanRoundOutcome,
    append_hierarchical_event,
)
from ..plan_commits import PlanCommitRejected, PlanPrincipal
from ..planning_backend_runtime import bind_deployment
from ..planning_protocol_binding import current_planning_protocol
from ..planning_repair_requests import address_requests
from ..planning_runtime_block import resolve_after_decision
from ..planning_selection import SYSTEM_RETRY_ORIGIN
from ..repair_impact import read_repair_impact_indexes
from ..taskgraph_policy import read_installed_graph_policy
from .core import OrchestratorCore

if TYPE_CHECKING:
    from ...contracts.planning_decisions import PlanningDecisionEnvelopeV1
    from ...planning.decision_adapter import AdaptedPlanningOutcome
    from ...runtime.planning_operations import RuntimeWorkSnapshot
    from ..plan_commits import CommitPlanCommand
    from ..taskgraph_preview import PlanMutationSources


def _sharing_refusal_codes(error: BaseException) -> list[str] | None:
    """共享核对的拒绝换成给规划器的码；只认 :class:`SharingRefused` 这个类型，不读异常文字。

    别的异常（哪怕消息以 ``TASKGRAPH_SHARED_`` 开头）返回 ``None``——那是库故障，不归规划器。
    """

    if not isinstance(error, SharingRefused):
        return None
    return [str(error.planner_code)]


class _AssuranceReviewUnavailable(ContractError):
    """An admitted planning subject could not open its independent Assurance review."""

    def __init__(self, purpose: str, error: Any) -> None:
        self.purpose = purpose
        self.code = str(getattr(error, "code", error))
        super().__init__(f"Assurance {purpose} review unavailable: {error}")


def _stale_commit_problems(reason: object) -> dict[str, Any]:
    """A commit refused because the world moved between preview and commit (the read set or
    the plan revision went stale) is the same fact as a stale request: named so on the
    refusal, it is not counted as the Planner answering wrongly (阶段 D)."""
    if str(reason) in {"READ_SET_STALE", "PLAN_REVISION_STALE"}:
        return {"problems": [{"code": "REQUEST_BINDING_STALE", "detail": str(reason)}]}
    return {}


def _commit_refusal_outcome(reason: object) -> tuple[str, dict[str, Any]]:
    """提交被拒时，规划拒绝事件的原因与计次用的码。

    只有原因就是内部契约错误（存储故障由调用方折成它）才记"来源不可用"、不扣规划次数。
    计划本身过不了提交检查——没有具名原因的契约拒绝，或原因不在规划拒绝码表里（如完成范围
    冻结不了）——仍是规划器答错：记 proposal_not_grounded，事件里不放兜底的内部码，照常计次。"""

    raw = str(reason or "")
    extra: dict[str, Any] = dict(_stale_commit_problems(raw))
    code = _commit_rejection_code(raw)
    if code != raw:
        return "proposal_not_grounded", extra
    extra["rejection_codes"] = [code]
    if code == PlanningDecisionRejectionCode.INTERNAL_CONTRACT_ERROR:
        return "planning_source_unavailable", extra
    return "proposal_not_grounded", extra


@dataclass(frozen=True, slots=True)
class PlanningReply:
    """One Planner reply being evaluated: the values fixed before decoding it."""

    intent: DispatchIntent
    mission: Mission
    text: str
    new_mode: HierarchicalDispatch
    store: planning_decision_store.PlanningDecisionStore
    request_id: str
    attempt_ordinal: int
    raw_hash: str
    decision_id: str
    existing: dict[str, Any] | None
    raw_artifact_ref: str


def _commit_rejection_code(value: object) -> str:
    """Map a commit refusal to the closed planning rejection enum.

    ``COMMIT_REJECTED`` is a lifecycle status, not a rejection code.  Keep the
    concrete guard reason when it is one of the H1 codes; otherwise record the
    stable internal fallback without making the durable row invalid.
    """

    try:
        return str(PlanningDecisionRejectionCode(str(value)))
    except ValueError:
        return str(PlanningDecisionRejectionCode.INTERNAL_CONTRACT_ERROR)


class _PlanningPreview(NamedTuple):
    """What a successful preview hands to the commit step."""

    context: Any
    planning_commit_admission: PlanningCommitAdmission
    preview_candidate: plan_preview.CandidatePreview
    preview_command: CommitPlanCommand
    source_principal: PlanPrincipal


class PlanningDecisionMixin(OrchestratorCore):
    """Collection of hierarchical planning decisions: reply intake, preview and commit."""

    __slots__ = ()

    async def _collect_plan_decision(
        self,
        intent: DispatchIntent,
        result: Any,
        mission: Mission,
        text: str,
        new_mode: HierarchicalDispatch,
    ) -> None:
        """Evaluate one new-protocol reply and bridge executable decisions to HTN."""

        # 规划器这一轮因输出上限停止（没有正文）：评估的是一条空回复，事实替代"没找到决定块"
        # （系统自己发起的原样重做没有模型回合，result 为 None）
        # 只读回合结果上真有的两个字段：没有 ``state``/``error`` 的结果（没有模型回合的替身）不是写满
        turn_error = getattr(result, "error", None)
        exhausted_fact = (
            output_exhausted_fact(turn_error)
            if getattr(result, "state", None) is not None
            and result.state is not AgentTurnState.COMMITTED
            and output_exhausted(turn_error)
            else None
        )
        del result

        reply = self._open_planning_reply(intent, mission, text, new_mode)
        if reply is None:
            return
        canonical_hash = None
        detail: dict[str, Any]
        resuming_preview = reply.existing is not None and reply.existing["status"] in {
            str(PlanningDecisionStatus.ADMITTED),
            str(PlanningDecisionStatus.COMPILED),
        }
        refusal_status = (
            PlanningDecisionStatus.COMMIT_REJECTED
            if resuming_preview
            else PlanningDecisionStatus.REJECTED
        )

        decoded_subject_key = ""
        # 2026-09-30：唯一的无损补齐（goal_type_ref 只缺一个字段且能唯一对上）；补了什么记进评估事件。
        autofilled: list[str] = []

        def evaluated(status: PlanningDecisionStatus, **payload: Any) -> None:
            # 按调用时的值转交（解码过程中这三项会更新）
            self._record_decision_evaluated(
                reply, status, canonical_hash, decoded_subject_key, autofilled, **payload
            )

        try:
            decision = parse_planning_decision(
                reply.text,
                request_id=reply.request_id,
                attempt_ordinal=reply.attempt_ordinal,
                raw_output_hash=reply.raw_hash,
                fill=package_filler(reply.intent.config.get("planning_package"), autofilled),
            )
            canonical_json = canonical_decision_json(decision)
            canonical_hash = canonical_decision_hash(decision)
            decoded_subject_key = decision.subject_key
            phase_key = (
                f"REPAIR/{getattr(decision.payload, 'repair_kind', '')}"
                if decision.decision_type is PlanningDecisionType.REPAIR
                else str(decision.decision_type)
            )
            context = self._hierarchical_admission_context(
                intent=reply.intent,
                mission=reply.mission,
                new_mode=reply.new_mode,
                raw_text=reply.text,
                include_plan_sources=phase_key != "REPAIR/DECLARE_RUNTIME_BLOCKED"
                and decision.decision_type
                not in {
                    PlanningDecisionType.WAIT,
                    PlanningDecisionType.NO_CHANGE,
                    PlanningDecisionType.REQUEST_EVIDENCE,
                    PlanningDecisionType.REQUEST_HUMAN,
                    PlanningDecisionType.PROPOSE_METHOD,
                    PlanningDecisionType.READ_METHOD_LIBRARY,
                },
            )
        except PlanningDecisionCodecError as error:
            await self._reject_undecodable_reply(reply, error, exhausted_fact, evaluated)
            return
        except ContractError as error:
            # A producer contract failure cannot be repaired by asking the model
            # to rewrite an already decoded reply, especially after COMPILED.
            status = (
                refusal_status if canonical_hash is not None else PlanningDecisionStatus.UNREADABLE
            )
            detail = {"error": refusal_text(error), "internal_contract_error": True}
            with self.store.transaction():
                reply.store.record_planning_decision(
                    request_id=reply.request_id,
                    attempt_ordinal=reply.attempt_ordinal,
                    raw_output_hash=reply.raw_hash,
                    raw_artifact_ref=reply.raw_artifact_ref,
                    decision_id=reply.decision_id,
                    status=status,
                    rejection_codes=("INTERNAL_CONTRACT_ERROR",),
                    detail=detail,
                    canonical_json=canonical_json if canonical_hash is not None else None,
                    canonical_hash=canonical_hash,
                    decision_type=str(decision.decision_type)
                    if canonical_hash is not None
                    else None,
                )
                evaluated(
                    status,
                    rejection_codes=["INTERNAL_CONTRACT_ERROR"],
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

        if isinstance(context, AuthoritySourceUnavailable):
            # The model supplied a readable decision. A missing/unreadable authority
            # producer is not a formatting defect and another model turn cannot fix
            # it. Preserve the closed wire enum while retaining the typed source
            # diagnostic in the durable detail, without opening a format retry.
            self._reject_unavailable_authority(
                reply,
                decision=decision,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                context=context,
                refusal_status=refusal_status,
                evaluated=evaluated,
            )
            return

        self._record_decision_progress(
            reply,
            PlanningDecisionStatus.DECODED,
            decision=decision,
            canonical_json=canonical_json,
            canonical_hash=canonical_hash,
        )
        pre_admitted = pre_admit_planning_decision(
            decision,
            context=DecisionAdmissionContext(
                context, allow_convergence_preview=reply.new_mode._taskgraph_preview is not None
            ),
            for_repair_preview=True,
        )

        if isinstance(pre_admitted, PreAdmittedPlanningDecision) and isinstance(
            decision.payload, RepairRuntimeBlockedDecision
        ):
            await self._repair_runtime_blocked(
                reply,
                decision=decision,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                evaluated=evaluated,
            )
            return

        if isinstance(pre_admitted, PreAdmittedPlanningDecision) and isinstance(
            decision.payload, RepairRetrySameMethodDecision
        ):
            await self._repair_retry_same_method(
                reply,
                decision=decision,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                evaluated=evaluated,
            )
            return
        if isinstance(pre_admitted, PreAdmittedPlanningDecision) and decision.decision_type in {
            PlanningDecisionType.REQUEST_HUMAN,
            PlanningDecisionType.PROPOSE_METHOD,
            PlanningDecisionType.READ_METHOD_LIBRARY,
        }:
            await self._route_method_or_human_request(
                reply,
                decision=decision,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                evaluated=evaluated,
            )
            return
        if (
            isinstance(pre_admitted, PreAdmittedPlanningDecision)
            and decision.decision_type is PlanningDecisionType.REQUEST_EVIDENCE
        ):
            await self._answer_evidence_request(
                reply,
                decision=decision,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                context=context,
                evaluated=evaluated,
            )
            return
        if isinstance(pre_admitted, NoMutationDecision):
            await self._settle_no_mutation_decision(
                reply,
                decision=decision,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                pre_admitted=pre_admitted,
                evaluated=evaluated,
            )
            return
        admitted: AdmittedPlanningDecision | Any
        preview_candidate: plan_preview.CandidatePreview | None = None
        planning_commit_admission: PlanningCommitAdmission | None = None
        preview_command: CommitPlanCommand | None = None
        source_principal: PlanPrincipal | None = None
        if isinstance(pre_admitted, PreAdmittedPlanningDecision):
            previewed = await self._preview_planning_decision(
                reply,
                decision=decision,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                pre_admitted=pre_admitted,
                context=context,
                refusal_status=refusal_status,
                evaluated=evaluated,
            )
            if previewed is None:
                return
            (
                context,
                planning_commit_admission,
                preview_candidate,
                preview_command,
                source_principal,
            ) = previewed
        admitted = admit_planning_decision(decision, context=context)
        if not isinstance(admitted, AdmittedPlanningDecision):
            await self._reject_unadmitted_decision(
                reply,
                decision=decision,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                admitted=admitted,
                refusal_status=refusal_status,
                evaluated=evaluated,
            )
            return

        outcome = adapt_admitted_decision(admitted, context=context)
        if outcome.durable_only is not None:
            self._settle_durable_only_decision(
                reply,
                decision=decision,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                evaluated=evaluated,
            )
            return

        await self._commit_planning_decision(
            reply,
            decision=decision,
            canonical_json=canonical_json,
            canonical_hash=canonical_hash,
            context=context,
            outcome=outcome,
            planning_commit_admission=planning_commit_admission,
            preview_candidate=preview_candidate,
            preview_command=preview_command,
            source_principal=source_principal,
            evaluated=evaluated,
        )
        return

    def _settle_durable_only_decision(
        self,
        reply: PlanningReply,
        *,
        decision: PlanningDecisionEnvelopeV1,
        canonical_json: str,
        canonical_hash: str,
        evaluated: Callable[..., None],
    ) -> None:
        reply.store.record_planning_decision(
            request_id=reply.request_id,
            attempt_ordinal=reply.attempt_ordinal,
            raw_output_hash=reply.raw_hash,
            raw_artifact_ref=reply.raw_artifact_ref,
            decision_id=reply.decision_id,
            status=PlanningDecisionStatus.NO_STATE_CHANGE,
            rejection_codes=(),
            detail={"decision_type": str(decision.decision_type)},
            canonical_json=canonical_json,
            canonical_hash=canonical_hash,
            decision_type=str(decision.decision_type),
        )
        evaluated(
            PlanningDecisionStatus.NO_STATE_CHANGE,
            decision_type=str(decision.decision_type),
        )
        self._settle_intent(reply.intent, "SETTLED")
        self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
        return

    def _open_planning_reply(
        self,
        intent: DispatchIntent,
        mission: Mission,
        text: str,
        new_mode: HierarchicalDispatch,
    ) -> PlanningReply | None:
        """Bind the reply to its request; None when it needs no evaluation now (already recorded, or waiting for convergence)."""

        store = planning_decision_store.PlanningDecisionStore(self.store)

        current_planning_protocol(self.store, mission.id)
        binding = store.get_planning_request_for_intent(intent.intent_id)
        if binding is None:
            raise ContractError(
                f"planning request for answering intent {intent.intent_id!r} is not persisted"
            )
        request_id = binding.request_id
        if binding.mission_id != mission.id or intent.mission_id != mission.id:
            raise StoreConflict("planning reply does not belong to this Mission")
        attempt_ordinal = self._planning_decision_attempt_ordinal(intent)
        raw_hash = hash_raw_output(text.encode("utf-8"))
        decision_id = allocate_decision_id(
            request_id=request_id,
            attempt_ordinal=attempt_ordinal,
            raw_output_hash=raw_hash,
        )

        existing = store.get_planning_decision_by_attempt(request_id, attempt_ordinal)
        if existing is not None:
            store._check_replay(existing, raw_hash=raw_hash, decision_id=decision_id)
            if existing["status"] in {
                str(PlanningDecisionStatus.UNREADABLE),
                str(PlanningDecisionStatus.REJECTED),
                str(PlanningDecisionStatus.COMMIT_REJECTED),
                str(PlanningDecisionStatus.NO_STATE_CHANGE),
                str(PlanningDecisionStatus.COMMITTED),
            }:
                # Internal collector replay consumes the already recorded outcome;
                # it neither grants fresh authority nor returns data to a new caller.
                # Public Commit receipt reads retain their own principal checks.
                # Re-admission would downgrade a committed result after revocation.
                return None

        if existing is not None and existing["status"] == str(PlanningDecisionStatus.COMPILED):
            held = PreviewBindingV1.from_json(existing.get("detail", {}).get("taskgraph_preview"))
            if held.required_convergence_ids:
                if new_mode._taskgraph_preview is None or len(held.required_convergence_ids) != 1:
                    raise ContractError(
                        "SOURCE_UNAVAILABLE: TaskGraph continuation is not installed"
                    )
                row = self.store.connection.execute(
                    "SELECT state FROM taskgraph_convergence_jobs WHERE mission_id=? AND job_id=?",
                    (mission.id, held.required_convergence_ids[0]),
                ).fetchone()
                if row is not None and row["state"] in {"FENCED", "WAITING"}:
                    # The original reply is durable. Waiting resumes that reply
                    # from the convergence consumer, never by calling Planner.
                    self._settle_intent(intent, "SETTLED")
                    self._settle_service_if_known(intent.subject_id, mission.id)
                    return None

        # Preserve the exact reply before decoding it. A content-addressed write
        # precedes every new row; failure must not leave a fabricated/null raw ref.
        # Existing terminal receipts above remain immutable (no history backfill).
        raw_artifact_ref = self.assembled.workspaces.artifact_store.put_bytes(text.encode("utf-8"))
        return PlanningReply(
            intent=intent,
            mission=mission,
            text=text,
            new_mode=new_mode,
            store=store,
            request_id=request_id,
            attempt_ordinal=attempt_ordinal,
            raw_hash=raw_hash,
            decision_id=decision_id,
            existing=existing,
            raw_artifact_ref=raw_artifact_ref,
        )

    async def _preview_planning_decision(
        self,
        reply: PlanningReply,
        *,
        decision: PlanningDecisionEnvelopeV1,
        canonical_json: str,
        canonical_hash: str,
        pre_admitted: PreAdmittedPlanningDecision,
        context: Any,
        refusal_status: PlanningDecisionStatus,
        evaluated: Callable[..., None],
    ) -> _PlanningPreview | None:
        """Preview the admitted decision; None when the reply was refused (already recorded)."""
        adapter_context = AdapterContext.from_admission_context(context)
        proposal = adapt_for_preview(pre_admitted, context=adapter_context)
        if not isinstance(proposal, PlanProposal):
            raise ContractError("SOURCE_UNAVAILABLE: preview adapter returned no proposal")
        retiring_instance_ids = tuple(
            str(operation.method_instance_id)
            for operation in proposal.operations
            if getattr(operation, "method_instance_id", None) is not None
        )
        context, frozen_inputs, runtime_work, source_principal, taskgraph_sources = (
            self._read_preview_sources(
                reply,
                canonical_hash=canonical_hash,
                proposal=proposal,
                retiring_instance_ids=retiring_instance_ids,
            )
        )
        preview = reply.new_mode.preview_plan_proposal(proposal, inputs=frozen_inputs)
        if isinstance(preview, plan_preview.PreviewUnavailable):
            await self._reject_unavailable_preview(
                reply,
                decision=decision,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                preview=preview,
                refusal_status=refusal_status,
                evaluated=evaluated,
            )
            return None
        if not isinstance(preview, plan_preview.CandidatePreview):
            raise ContractError("SOURCE_UNAVAILABLE: preview did not return a typed candidate")
        preview_candidate = preview
        if context.authorization.planning_snapshot is None:
            raise ContractError("SOURCE_UNAVAILABLE: preview has no planning authority snapshot")
        if context.operations.snapshot is None:
            raise ContractError("SOURCE_UNAVAILABLE: preview has no operation snapshot")

        decision_key = _enablement_key(decision)
        planning_commit_admission = PlanningCommitAdmission(
            request_id=reply.request_id,
            decision_hash=canonical_hash,
            decision_key=decision_key,
            authority=context.authorization.planning_snapshot,
            operations=context.operations.snapshot,
            runtime_work=runtime_work,
            preview_request_id=preview.request_id,
            preview_decision_hash=preview.decision_hash,
            preview_compilation_hash=preview.compilation_hash,
            preview_read_set_hash=sha256_hex(preview.compilation.delta.read_set.to_json()),
        )
        preview_command = reply.new_mode.build_command(
            reply.mission.id,
            proposal,
            preview.compilation,
            principal=source_principal,
            command_id=f"plan:{reply.intent.intent_id}",
            source={
                "intent_id": reply.intent.intent_id,
                "agent_id": reply.intent.agent_id,
                "preview_compilation_hash": preview.compilation_hash,
                "preview_source_snapshot_hash": preview.source_snapshot_hash,
            },
        )
        try:
            frozen_graph = reply.new_mode._taskgraph_preview.freeze(
                preview_command, preview, taskgraph_sources
            )
        except SharingRefused as error:
            # 共用的秩序核对没过（规划器提示词里说的"TaskGraph 开头的原因代码"）：是这份决定的
            # 问题，退回规划器；不是库故障，原地重试多少次结果都一样。
            # 按异常类型码判（错误码表 SharingRefusalCode → 规划器码），不读异常文字；其它
            # ContractError 不在这里接，照旧往上抛当库故障原地重试。
            codes = _sharing_refusal_codes(error)
            if codes is None:
                raise
            await self._reject_shared_refusal(
                reply,
                decision=decision,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                error=error,
                codes=codes,
                refusal_status=refusal_status,
                evaluated=evaluated,
            )
            return None
        planning_commit_admission = replace(
            planning_commit_admission, taskgraph_candidate=frozen_graph
        )

        context = replace(
            context,
            taskgraph_scope=planning_convergence_scope(
                taskgraph_sources.before,
                frozen_graph.document,
                context.operations.snapshot,
                taskgraph_sources.operation_producers,
            ),
        )
        context = replace(
            context,
            plan_shape=PlanShapeView(
                mapped_problems=tuple(
                    PlanningDecisionRejectionCode(code) for code in preview.mapped_problems
                )
            ),
        )
        return _PlanningPreview(
            context, planning_commit_admission, preview_candidate, preview_command, source_principal
        )

    def _record_decision_evaluated(
        self,
        reply: PlanningReply,
        status: PlanningDecisionStatus,
        canonical_hash: str | None,
        decoded_subject_key: str,
        autofilled: list[str],
        /,
        **payload: Any,
    ) -> None:
        address_requests(
            self.store,
            reply.mission.id,
            package=reply.intent.config.get("planning_package"),
            decision_id=reply.decision_id,
            decision_type=str(payload.get("decision_type") or ""),
            status=str(status),
            subject_key=decoded_subject_key,
        )

        resolve_after_decision(
            self,
            reply.intent,
            status=str(status),
            decision_type=str(payload.get("decision_type") or ""),
            decision_id=reply.decision_id,
        )

        payload.setdefault(
            "decision_origin",
            SYSTEM_RETRY_ORIGIN
            if reply.intent.config.get("native_planning_decision") is not None
            else "planner_reply",
        )
        payload.setdefault("decision_type", None)
        payload.setdefault("rejection_codes", [])
        payload.setdefault("canonical_hash", canonical_hash)
        if autofilled:
            payload.setdefault("autofilled", list(autofilled))
        append_hierarchical_event(
            self.store,
            "PlanningDecisionEvaluated",
            reply.mission.id,
            # 同一决定再评一次（另一次尝试或另一个结论）也要有自己的事件，不被同键吞掉（阶段 G）
            key=f"{reply.decision_id}:{reply.attempt_ordinal}:{status!s}",
            payload={
                "request_id": reply.request_id,
                "decision_id": reply.decision_id,
                "attempt_ordinal": reply.attempt_ordinal,
                "raw_output_hash": reply.raw_hash,
                "status": str(status),
                **payload,
            },
        )

    def _record_decision_progress(
        self,
        reply: PlanningReply,
        status: PlanningDecisionStatus,
        *,
        decision: PlanningDecisionEnvelopeV1,
        canonical_json: str,
        canonical_hash: str,
        planning_commit_admission: PlanningCommitAdmission | None = None,
        preview_command: CommitPlanCommand | None = None,
        source_principal: PlanPrincipal | None = None,
    ) -> None:
        # 只在 COMPILED 阶段读预览三项；其余阶段调用时不必传
        stages = (
            PlanningDecisionStatus.DECODED,
            PlanningDecisionStatus.ADMITTED,
            PlanningDecisionStatus.COMPILED,
        )
        progress_detail: dict[str, Any] = {}
        if status is PlanningDecisionStatus.COMPILED and planning_commit_admission is not None:
            frozen = planning_commit_admission.taskgraph_candidate
            if frozen is not None:
                progress_detail["taskgraph_preview"] = frozen.preview.to_json()
                if (
                    reply.existing is not None
                    and reply.existing["status"] == str(PlanningDecisionStatus.COMPILED)
                    and reply.existing.get("detail", {}).get("taskgraph_preview")
                    != frozen.preview.to_json()
                ):
                    if reply.new_mode._taskgraph_preview is None:
                        raise ContractError("SOURCE_UNAVAILABLE: TaskGraph preview service missing")
                    reply.new_mode._taskgraph_preview.resume_preview(
                        reply.existing.get("detail", {}).get("taskgraph_preview"),
                        frozen,
                        preview_command,
                        source_principal,
                    )
        if reply.existing is not None and stages.index(
            PlanningDecisionStatus(reply.existing["status"])
        ) >= stages.index(status):
            return
        reply.store.record_planning_decision(
            request_id=reply.request_id,
            attempt_ordinal=reply.attempt_ordinal,
            raw_output_hash=reply.raw_hash,
            raw_artifact_ref=reply.raw_artifact_ref,
            decision_id=reply.decision_id,
            status=status,
            rejection_codes=(),
            detail=progress_detail,
            canonical_json=canonical_json,
            canonical_hash=canonical_hash,
            decision_type=str(decision.decision_type),
        )

    def _read_preview_sources(
        self,
        reply: PlanningReply,
        *,
        canonical_hash: str,
        proposal: PlanProposal,
        retiring_instance_ids: tuple[str, ...],
    ) -> tuple[
        Any, plan_preview.PreviewInputs, RuntimeWorkSnapshot, PlanPrincipal, PlanMutationSources
    ]:
        with self.store.read_view():
            context = self._hierarchical_admission_context(
                intent=reply.intent,
                mission=reply.mission,
                new_mode=reply.new_mode,
                raw_text=reply.text,
                include_plan_sources=True,
            )
            if isinstance(context, AuthoritySourceUnavailable):
                raise ContractError("SOURCE_UNAVAILABLE: H1 plan source snapshot is unavailable")
            try:
                runtime_work = read_running_work(
                    reply.mission.id,
                    retiring_instance_ids,
                    # The planner intent being collected is itself SUBMITTED while
                    # this decision is previewed.  It is the request under commit,
                    # not sibling runtime work that needs convergence.
                    reader=StoreOperationReader(
                        self.store, ignore_intent_ids=(reply.intent.intent_id,)
                    ),
                )
            except RuntimeSourceUnavailable as error:
                raise ContractError(f"SOURCE_UNAVAILABLE: {error}") from error
            world = reply.new_mode.require_planning_world()
            network = reply.new_mode.network(reply.mission.id)

            require_bound(self.store, reply.mission.id)
            preview_budget = GraphStructureBudget.from_json(
                read_installed_graph_policy(self.store, reply.mission.id).to_json()[
                    "graph_structure_budget"
                ]
            )
            if (
                reply.new_mode._taskgraph_preview is None
                or context.authorization.planning_snapshot is None
            ):
                raise ContractError("SOURCE_UNAVAILABLE: TaskGraph preview assembly is missing")
            source_principal = PlanPrincipal(
                principal_id=context.authorization.planning_snapshot.planner_principal_id,
                scope_id="mission",
            )
            taskgraph_sources = reply.new_mode._taskgraph_preview.capture(
                reply.request_id, reply.decision_id, source_principal
            )

            graph_mutation = any(
                isinstance(
                    op, (CancelBranchOperation, ProposeSuccessorOperation, RebindInputOperation)
                )
                for op in proposal.operations
            )
            frozen_inputs = plan_preview.PreviewInputs(
                decision_id=reply.decision_id,
                decision_hash=canonical_hash,
                request_id=reply.request_id,
                source_plan_revision=int(network.plan_revision),
                source_network_hash=sha256_hex(plan_preview._source_snapshot_payload(network)),
                proposal=proposal,
                network=network,
                registry=world.registry,
                catalog=world.catalog,
                schemas=world.schemas,
                evidence=world.snapshot(),
                predicates=world.predicates,
                requirements_revision=context.requirements_revision,
                budget=preview_budget,
                taskgraph_contract=True,
                sharing_entries=taskgraph_sources.sharing.entries,
                system_identity_seed=self._owner,
                now_ms=int(self.store.now * 1000),
                capabilities=world.capabilities(),
                runtime_work=runtime_work,
                repair_impact=read_repair_impact_indexes(self.store, network, reply.mission.id)
                if graph_mutation
                else None,
                **self._preview_read_facts(reply.mission.id),
            )
        return context, frozen_inputs, runtime_work, source_principal, taskgraph_sources

    async def _commit_planning_decision(
        self,
        reply: PlanningReply,
        *,
        decision: PlanningDecisionEnvelopeV1,
        canonical_json: str,
        canonical_hash: str,
        context: Any,
        outcome: AdaptedPlanningOutcome,
        planning_commit_admission: PlanningCommitAdmission | None,
        preview_candidate: plan_preview.CandidatePreview | None,
        preview_command: CommitPlanCommand | None,
        source_principal: PlanPrincipal | None,
        evaluated: Callable[..., None],
    ) -> None:
        assert outcome.proposal is not None
        self._record_decision_progress(
            reply,
            PlanningDecisionStatus.ADMITTED,
            decision=decision,
            canonical_json=canonical_json,
            canonical_hash=canonical_hash,
            planning_commit_admission=planning_commit_admission,
            preview_command=preview_command,
            source_principal=source_principal,
        )
        try:
            self._record_decision_progress(
                reply,
                PlanningDecisionStatus.COMPILED,
                decision=decision,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                planning_commit_admission=planning_commit_admission,
                preview_command=preview_command,
                source_principal=source_principal,
            )
            authority_snapshot = context.authorization.planning_snapshot
            principal = PlanPrincipal(
                # Commit must use the same durable authority identity that Admission
                # checked.  A retry's executor lease may have a different agent id;
                # using it here would turn an admitted decision into a false
                # ``caller is not the bound planner principal`` refusal.
                principal_id=(
                    authority_snapshot.planner_principal_id
                    if authority_snapshot is not None
                    else (reply.intent.agent_id or self._owner)
                ),
                scope_id="mission",
            )
            if preview_candidate is None:
                # This branch is retained for durable-only/legacy adapters.  New
                # executable decisions must carry the exact preview compilation into
                # commit; silently recompiling would admit a different candidate.
                raise ContractError("SOURCE_UNAVAILABLE: final commit has no candidate preview")
            if planning_commit_admission is None:
                raise ContractError("SOURCE_UNAVAILABLE: final commit has no H1-H admission")
            if planning_commit_admission.taskgraph_candidate is not None:
                if reply.new_mode._taskgraph_preview is None:
                    raise ContractError("SOURCE_UNAVAILABLE: TaskGraph convergence service missing")
                with self.store.transaction():
                    job = reply.new_mode._taskgraph_preview.ensure_convergence(
                        preview_command, principal, planning_commit_admission.taskgraph_candidate
                    )
                    if job is not None and job.state != "READY":
                        self._settle_intent(reply.intent, "SETTLED")
                        self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
                        return

            bind_deployment(self, reply.mission.id)
            solver_lane, solver_snapshot, solver_result = await self._solve_with_planning_backend(
                reply,
                outcome=outcome,
                preview_candidate=preview_candidate,
                planning_commit_admission=planning_commit_admission,
                principal=principal,
            )
            with self.store.transaction():
                if solver_lane is not None:
                    assert solver_result is not None and solver_snapshot is not None
                    receipt = PlanningBackendBridge(solver_lane).commit(
                        solver_result, snapshot=solver_snapshot
                    )
                    plan_outcome = PlanRoundOutcome(
                        proposal_id=outcome.proposal.proposal_id, receipt=receipt
                    )
                else:
                    plan_outcome = reply.new_mode.commit_preview_plan_proposal(
                        reply.mission.id,
                        outcome.proposal,
                        preview=preview_candidate,
                        admission=planning_commit_admission,
                        principal=principal,
                        command_id=f"plan:{reply.intent.intent_id}",
                        source={
                            "intent_id": reply.intent.intent_id,
                            "agent_id": reply.intent.agent_id,
                        },
                        owner=self._owner,
                        proposal_text=reply.text,
                    )
                if plan_outcome.committed:
                    assert plan_outcome.receipt is not None
                    reply.store.record_planning_decision(
                        request_id=reply.request_id,
                        attempt_ordinal=reply.attempt_ordinal,
                        raw_output_hash=reply.raw_hash,
                        raw_artifact_ref=reply.raw_artifact_ref,
                        decision_id=reply.decision_id,
                        status=PlanningDecisionStatus.COMMITTED,
                        rejection_codes=(),
                        detail={"plan_revision": plan_outcome.receipt.new_plan_revision},
                        canonical_json=canonical_json,
                        canonical_hash=canonical_hash,
                        decision_type=str(decision.decision_type),
                    )
                    evaluated(
                        PlanningDecisionStatus.COMMITTED,
                        decision_type=str(decision.decision_type),
                        plan_revision=plan_outcome.receipt.new_plan_revision,
                    )
                    self._blame_replaced_method(reply.mission.id, decision, reply.decision_id)
                    reply.new_mode.advance_compound_phases(reply.mission.id)
                    self._settle_intent(reply.intent, "SETTLED")
                    self._settle_service_if_known(reply.intent.subject_id, reply.mission.id)
                    return
        except (GraphIntegrityError, ContractError, StoreConflict, PlanCommitRejected) as error:
            await self._reject_failed_commit(
                reply,
                decision=decision,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                error=error,
                evaluated=evaluated,
            )
            return

        await self._reject_refused_plan(
            reply,
            decision=decision,
            canonical_json=canonical_json,
            canonical_hash=canonical_hash,
            plan_outcome=plan_outcome,
            evaluated=evaluated,
        )

    async def _solve_with_planning_backend(
        self,
        reply: PlanningReply,
        *,
        outcome: AdaptedPlanningOutcome,
        preview_candidate: plan_preview.CandidatePreview,
        planning_commit_admission: PlanningCommitAdmission,
        principal: PlanPrincipal,
    ) -> tuple[Any, Any, PlanningBackendResult | None]:
        assert outcome.proposal is not None  # 调用方已断言
        solver_lane = None
        solver_snapshot = None
        solver_result = None
        if self._config.planning_backend is not None:
            solver_lane = reply.new_mode.solver_preview_lane(
                reply.mission.id,
                outcome.proposal,
                preview=preview_candidate,
                admission=planning_commit_admission,
                principal=principal,
                command_id=(
                    f"plan:{reply.intent.intent_id}"
                    if planning_commit_admission.taskgraph_candidate is not None
                    else f"plan:{reply.intent.intent_id}:solver"
                ),
                source={"intent_id": reply.intent.intent_id, "agent_id": reply.intent.agent_id},
            )
            solver_snapshot, solver_result = await planning_backend_runtime.solve_decision(
                self,
                mission_id=reply.mission.id,
                decision_id=reply.decision_id,
                lane=solver_lane,
            )
            if solver_result.status is not BackendStatus.SOLVED:
                raise ContractError(
                    f"planning backend refused: {solver_result.status}: {solver_result.detail}"
                )
        return solver_lane, solver_snapshot, solver_result
