# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Attempt collection, verification and carried reviews."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from simple_harness.agents import AgentTurnState

from ...artifacts.bound_workspace import UnifiedDiffApplyError, bound_artifacts_named_in_envelope
from ...artifacts.store import ArtifactStoreError, read_verified
from ...artifacts.versioning import ArtifactConflict, next_versions
from ...assurance.codec import AssuranceError
from ...contracts import (
    TERMINAL_ATTEMPT,
    Artifact,
    Attempt,
    AttemptStatus,
    ContractError,
    Mission,
    MissionStopReason,
    ResultEnvelope,
    ResultOutcome,
    Task,
    TaskStatus,
    ids,
)
from ...contracts.models import jsonable, sha256_hex
from ...contracts.state_machines import IllegalTransition
from ...deployment.root import current_statements
from ...governance.budgets import BudgetExhausted
from ...graph.projection_validation import GraphIntegrityError
from ...memory.verified_knowledge import KnowledgeIndex
from ...runtime.model_router import RoutingUnavailable, classify_turn_error
from ...runtime.output_blocks import (
    BlockError,
    PortClaim,
    extract_block,
    outside_text,
    parse_port_claims,
)
from ...runtime.role_templates import RESULT_ENVELOPE_TAG
from ...storage import htn_store
from ...storage.store import DispatchIntent, StoreError
from ...verification.critics import CriticVerdict
from ...verification.deterministic_checks import LayerResult
from ...verification.human_review import (
    NEEDS_HUMAN,
    arbitration_request_id,
    judgment_conflict,
    reusable_layers,
    review_request_id,
)
from ...verification.verifier_router import VERIFIER_VERSION
from .. import resource_requests
from ..action_commits import ActionCommitError
from ..assurance_review_import import carried_review_alive
from ..budget_accounts import task_account
from ..carried_review import CARRIED_RESULT_REJECTED, MAX_CARRIED_REVIEW_ERRORS, source_key
from ..commit_service import CommitRejected
from ..completion_inputs import frozen_requirements_revision
from ..occurrence_tasks import read_only_leaf, read_only_rewrites
from ..operation_completion import OperationCompletionError
from ..planning_repair_requests import record_request
from ..resolution_commits import ResolutionCommitRejected
from ..review_adjudication import adjudication_of
from .core import OrchestratorCore
from .support import _under_source_root, logger, sha256_hex_text

if TYPE_CHECKING:
    from ...contracts.htn import TaskSemanticBindingV1


class _AcceptedSiblingSupersededVerification(Exception):
    """Only the recorder's rejected lease may signal this obsolete verifier."""


class _CriticAdmissionFailure(ContractError):
    """An SDK admission failure, not a malformed completed Critic verdict."""

    def __init__(self, error):
        super().__init__("critic provider admission denied")
        self.error = jsonable(error)


class VerificationMixin(OrchestratorCore):
    """Attempt collection, verification and carried reviews."""

    __slots__ = ()

    def _release_refused_fence(self, mission_id: str, decision_id: str) -> None:
        """A decision whose commit was rejected never applies: its fence ends with it
        (阶段 B 裁决第 5 类), so the old plan's fenced steps can run and publish again."""

        if self._wiring.taskgraph_notifications is None:
            return  # no convergence service installed: no fence was ever raised
        with self.store.transaction():
            self._wiring.taskgraph_notifications.convergence.jobs.release_for_decision(
                mission_id,
                decision_id,
                reason="decision_refused",
                now_ms=int(self.store.now * 1000),
            )

    async def _collect_attempt(self, intent: DispatchIntent, result) -> None:  # type: ignore[no-untyped-def]
        attempt = self.store.get_attempt(intent.subject_id)
        assert attempt is not None
        self._import_usage(intent)
        if result.state is AgentTurnState.COMMITTED:
            self._reset_after_handoff_unknown_streak(attempt.mission_id)
        if attempt.status is not AttemptStatus.RUNNING:
            closed = attempt.status in {AttemptStatus.SUPERSEDED, AttemptStatus.CANCELLED}
            if closed:
                # D3-6': a late result on a closed Attempt is history, never a transition
                self._record_late_result(attempt, result)
            self._settle_intent(intent, "SETTLED")
            if closed:
                self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            return
        echoed = self.bridge_for(intent).echoed_models(agent_id=intent.agent_id or "")
        if echoed and echoed != {self._expected_model(intent)}:
            # D10': the provider answered as a different model; charges are unknown and
            # the deployment binding is wrong.  Fail fast and visibly, hold the reservation.
            await self._reject_echoed_model_mismatch(intent, result, attempt, echoed)
            return
        if result.state is AgentTurnState.FAILED:
            await self._settle_failed_turn(intent, result, attempt)
            return
        text = "" if result.public_output is None else str(result.public_output.content)
        stale_plan = self._dispatch_for(attempt.mission_id)
        if stale_plan is not None and stale_plan.requirements_changed(attempt.mission_id):
            # 阶段 E：用户改了要求、按新版的计划还没提交。这份结果是按旧版要求的计划做出来的：
            # 归档为"被取代"，不当成执行者做错（不发修复请求、不为它切审查包）；
            # 新计划要不要再做这一步由规划器定。
            self.commit.reject_result(
                attempt.id,
                turn_id=result.turn_id,
                reason="superseded",
                detail={"error": "requirements_changed", "output_head": text[:400]},
            )
            self._settle_intent(intent, "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            self._note(f"attempt {attempt.id}: result set aside, the requirements were amended")
            return
        try:
            envelope, client_result_id = self._parse_envelope(text, attempt, turn_id=result.turn_id)
            self.commit.check_result_evidence(attempt.mission_id, envelope)
        except (ContractError, CommitRejected) as error:
            self.commit.reject_result(
                attempt.id,
                turn_id=result.turn_id,
                reason=(
                    "result_evidence_kind_not_allowed"
                    if isinstance(error, CommitRejected)
                    else "envelope_invalid"
                ),
                detail={"error": str(error), "output_head": text[:400]},
            )
            self._settle_intent(intent, "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            self._note(f"attempt {attempt.id}: envelope invalid → RETRY_WAIT ({error})")
            return
        mission = self.store.get_mission(attempt.mission_id)
        task = self.store.get_task(attempt.task_id)
        assert mission is not None and task is not None
        if envelope.outcome is not ResultOutcome.CANDIDATE:
            # D5-5: execution evidence, not a candidate — kept as history; the Attempt
            # ends in RETRY_WAIT and the Task is tried again within its allowance
            await self._settle_non_candidate_result(
                intent,
                result=result,
                attempt=attempt,
                mission=mission,
                task=task,
                envelope=envelope,
            )
            return
        workspace = self.assembled.workspaces.get(attempt.id)
        artifacts = workspace.snapshot(
            mission_id=attempt.mission_id,
            task_id=attempt.task_id,
            produced_by=intent.agent_id or attempt.id,
            versions=next_versions(self.store.list_mission_artifacts(attempt.mission_id)),
        )
        known = {artifact.path for artifact in artifacts}
        source_roots = tuple(self._frozen_source_binding(attempt).get("source_roots", ()))
        source_hashes = {
            path: sha256_hex_text(content) for path, content in self._source_files(attempt).items()
        }
        # Check the physical snapshot first: case aliases in a reported path must not
        # hide a changed source behind a generic missing-artifact diagnostic.
        source_rewritten = {
            artifact.path
            for artifact in artifacts
            if _under_source_root(artifact.path, source_roots)
            and artifact.content_hash != source_hashes.get(artifact.path)
        }
        missing = [path for path in envelope.artifacts if path not in known]
        if missing and not source_rewritten:
            self.commit.reject_result(
                attempt.id,
                turn_id=result.turn_id,
                reason="envelope_invalid",
                detail={"error": f"artifacts not in the workspace: {missing}"},
            )
            self._settle_intent(intent, "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            self._note(f"attempt {attempt.id}: artifacts missing → RETRY_WAIT")
            return
        # D3-7': the Attempt's artifact set = what it listed ∪ what it changed relative to
        # its initial inputs (seed + upstream); protected paths are never registered as
        # produced work (a rewrite there is tampering, reported by rule_check instead).
        # P2.3u: same function ``_read_only_initial`` the gateway snapshot uses.
        initial = self._read_only_initial(attempt)
        guarded = {
            path: sha256_hex_text(content)
            for path, content in self._protected_files(mission, task, attempt).items()
        }
        listed = set(envelope.artifacts)
        by_path = {artifact.path: artifact for artifact in artifacts}
        rewritten = sorted(
            source_rewritten
            | {
                path
                for path in listed & known
                if path in guarded and by_path[path].content_hash != guarded[path]
            }
        )
        if rewritten:  # P1-6: a protected path is never registered as produced work
            self.commit.reject_result(
                attempt.id,
                turn_id=result.turn_id,
                reason="protected_path_rewritten",
                detail={"paths": rewritten},
            )
            self._settle_intent(intent, "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            self._note(f"attempt {attempt.id}: rewrote protected {rewritten} → RETRY_WAIT")
            return
        # P2.3k verification P1-2: a hierarchical leaf whose type declares itself
        # read-only may not have changed a file it started from.  Grok C3's ``facts``
        # / ``reproduce`` leaves rewrote ``stats/window.py``; with ``code_test`` no
        # longer on such leaves, the declaration has to be enforced where the files
        # come in, not merely trusted.
        new_mode = self._new_mode(mission)
        if new_mode is None:
            raise ContractError(
                "hierarchical_assembly_missing: no result collection without the assembly"
            )
        try:
            current_task_ids = {
                str(spec.task_id) for spec in new_mode.network(mission.id).occurrences
            }
        except (GraphIntegrityError, ContractError, StoreError, KeyError):
            current_task_ids = set()
        accepted_hashes = self._accepted_path_hashes(mission.id, current_task_ids=current_task_ids)
        binding = new_mode.semantics().task_semantics_of(mission.id, task.id)
        rewrote = (
            []
            if binding is None
            else read_only_rewrites(
                binding,
                artifacts,
                initial,
                guarded=guarded,
                accepted=accepted_hashes,
            )
        )
        if rewrote:
            await self._reject_read_only_rewrite(
                intent, result=result, attempt=attempt, binding=binding, rewrote=rewrote
            )
            return
        # P2.3m: drop files whose bytes already belong to a completed leaf.
        # P2.3v: a *write* leaf that reproduces a retired method's accepted bytes
        # (Grok M2-r1's new apply leaf vs the retired patch) must still record
        # those paths — otherwise ``rule_check`` refuses the envelope.  Same-hash
        # drop stays on read-only leaves, where P2.3o re-adds bound inputs.
        drop_same_hash = binding is not None and read_only_leaf(binding)
        consistent = (
            set()
            if not drop_same_hash
            else {
                artifact.path
                for artifact in artifacts
                if artifact.path in initial
                and accepted_hashes.get(artifact.path) == artifact.content_hash
            }
        )
        referenced = [
            artifact
            for artifact in artifacts
            if artifact.path not in consistent
            and (
                (
                    artifact.path not in guarded
                    and (
                        artifact.path in listed
                        or initial.get(artifact.path) != artifact.content_hash
                    )
                )
                or (artifact.path in guarded and artifact.path in listed)
            )
        ]
        if binding is None or not read_only_leaf(binding):
            try:
                referenced = self._record_applied_diff_files(
                    referenced,
                    workspace=workspace,
                    attempt=attempt,
                    seed=dict((mission.final_report or {}).get("workspace_seed", {})),
                )
            except UnifiedDiffApplyError as error:
                self.commit.reject_result(
                    attempt.id,
                    turn_id=result.turn_id,
                    reason="unified_diff_apply_failed",
                    detail={"path": error.path, "reason": error.reason},
                )
                self._settle_intent(intent, "FAILED")
                self._settle_if_known(attempt)
                await self._release_attempt(attempt.id, cancel=False)
                self._note(
                    f"attempt {attempt.id}: unified diff {error.path} {error.reason} → RETRY_WAIT"
                )
                return

        try:
            self.commit.record_result(
                attempt.id,
                envelope=envelope,
                turn_id=result.turn_id,
                artifacts=referenced,
                usage_refs=tuple(result.usage_refs),
                port_claims=self._attempts.port_claims.get(envelope.id, ()),
            )
        except OperationCompletionError as error:
            if error.code != "OP_COMPLETION_PORT_CLAIMS_UNAVAILABLE":
                # Not the Worker's envelope but the Mission's own completion inputs:
                # refused collection, isolated by the caller and retried visibly.
                raise CommitRejected(f"{error.code}: {error}") from error
            # 2026-10-01: the envelope's port claims do not satisfy the frozen completion
            # inputs (a declared port left unclaimed, a claim that binds no artifact).
            # That is the model's mistake: a refused result, never an exception out of
            # the loop — it used to end ``run()`` for every Mission in the process.
            self.commit.reject_result(
                attempt.id,
                turn_id=result.turn_id,
                reason="completion_inputs_refused",
                detail={"code": error.code, "error": str(error)[:300]},
            )
            self._settle_intent(intent, "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            self._note(f"attempt {attempt.id}: port claims refused ({error}) → RETRY_WAIT")
            return
        self._fault("after_result_submitted", "attempt")
        self._settle_intent(intent, "SETTLED")
        await self._release_attempt(attempt.id, cancel=False)
        self._attempts.client_ids[envelope.id] = client_result_id
        self._note(f"attempt {attempt.id}: result {envelope.id} submitted")
        # P2.3b / TG §7: a child's result advances its parent compound's *typed* phase.
        # It never creates an Attempt for the compound and never writes its Task row —
        # the phase is a projection of typed state, recorded as an event.
        # (``new_mode`` was asked once, above, before the read-only check.)
        if new_mode is not None:
            new_mode.advance_compound_phases(mission.id)

    async def _reject_echoed_model_mismatch(
        self, intent: DispatchIntent, result: Any, attempt: Attempt, echoed: set[str]
    ) -> None:
        self.commit.reject_result(
            attempt.id,
            turn_id=result.turn_id,
            reason="model_echo_mismatch",
            detail={"expected": self._expected_model(intent), "echoed": sorted(echoed)},
        )
        self._settle_intent(intent, "FAILED")
        self._settle_if_known(attempt)
        await self._release_attempt(attempt.id, cancel=False)
        self._commit_stop_task(
            attempt.task_id,
            stop_reason=MissionStopReason.MODEL_ECHO_MISMATCH,
            detail={"expected": self._expected_model(intent), "echoed": sorted(echoed)},
        )
        await self._release_mission(attempt.mission_id)
        self._note(f"attempt {attempt.id}: model echo mismatch {sorted(echoed)} → stopped")
        return

    async def _settle_failed_turn(
        self, intent: DispatchIntent, result: Any, attempt: Attempt
    ) -> None:
        error = result.error
        context_limit = (
            isinstance(error, Mapping)
            and error.get("error_code") == "context_required_content_too_large"
        )
        admission = (
            error.get("detail")
            if isinstance(error, Mapping)
            and error.get("error_code") == "provider_admission_denied"
            and error.get("source_kind") == "provider_admission"
            and error.get("retryable") is False
            else None
        )
        if not (
            isinstance(admission, Mapping)
            and type(admission.get("schema_version")) is int
            and admission.get("schema_version") == 1
        ):
            admission = None
        mission_now = self.store.get_mission(attempt.mission_id)
        if mission_now is not None and self._definite_auth_failure(error):
            self.commit.reject_result(
                attempt.id,
                turn_id=result.turn_id,
                reason="turn_failed",
                detail={
                    "error": jsonable(result.error or {}),
                    "error_kind": "provider_unavailable",
                },
            )
            self._settle_intent(intent, "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            self._commit_stop_task(
                attempt.task_id,
                stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
                detail={
                    "source_kind": "provider",
                    "retryable": False,
                    "error": jsonable(error or {}),
                },
            )
            await self._release_mission(attempt.mission_id)
            self._note(f"attempt {attempt.id}: definite provider auth/payment failure → stopped")
            return
        self.commit.reject_result(
            attempt.id,
            turn_id=result.turn_id,
            reason=(
                "context_required_content_too_large"
                if context_limit
                else "provider_admission_denied"
                if admission is not None
                else "turn_failed"
            ),
            detail={
                "error": jsonable(result.error or {}),
                "error_kind": classify_turn_error(result.error),  # D6-4' classification
            },
        )
        self._settle_intent(intent, "FAILED")
        self._settle_if_known(attempt)
        await self._release_attempt(attempt.id, cancel=False)
        if context_limit:
            self._commit_stop_task(
                attempt.task_id,
                stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
                detail={
                    "source_kind": "runtime_context",
                    "retryable": False,
                    "error": jsonable(error),
                },
            )
            await self._release_mission(attempt.mission_id)
            self._note(f"attempt {attempt.id}: required context exceeds limit → stopped")
            return
        if admission is not None:
            reason = admission.get("reason_code")
            if reason == "usage_unresolved":
                self.commit.wait_for_admission_usage(attempt.id)
                self._note(f"attempt {attempt.id}: admission waits for usage; no new Attempt")
                return
            if reason == "lease_lost":
                # 2026-10-02 真机：强杀后重启，这一轮的执行权还记在旧进程名下。被打断，
                # 不是这一步做不了：原地重做（不扣次数，同一步合计有上限）。
                self._note(f"attempt {attempt.id}: executor lost its lease → RETRY_WAIT")
                return
            if reason == "cancelled":
                self._commit_cancel_mission(attempt.mission_id)
            elif reason == "deadline" and await self._runtime_exhausted(
                # attempts.mission_id 非空且外键引用 missions（库开了外键）
                cast("Mission", self.store.get_mission(attempt.mission_id)),
                (),
            ):
                # 2026-09-29：排队到点往往就是任务总时限到了：按任务总时限停（带用时明细），
                # 不再和主循环的时限检查抢先后。
                return
            else:
                # Runtime/deadline is the existing budget time-cap category.
                # Other admission failures retain their exact configuration /
                # authority cause; they never enter provider health fallback.
                stop = (
                    MissionStopReason.BUDGET_EXHAUSTED
                    if reason in {"budget_exhausted", "deadline"}
                    else MissionStopReason.RUNTIME_UNAVAILABLE
                )
                self._commit_stop_task(
                    attempt.task_id,
                    stop_reason=stop,
                    detail={
                        "source_kind": "provider_admission",
                        "retryable": False,
                        "admission": dict(admission),
                        **({"dimension": "runtime"} if reason == "deadline" else {}),
                    },
                )
            await self._release_mission(attempt.mission_id)
            self._note(f"attempt {attempt.id}: admission denied ({reason}) → stopped")
            return
        self._note(f"attempt {attempt.id}: SDK turn failed → RETRY_WAIT")
        return

    async def _settle_non_candidate_result(
        self,
        intent: DispatchIntent,
        *,
        result: Any,
        attempt: Attempt,
        mission: Mission,
        task: Task,
        envelope: ResultEnvelope,
    ) -> None:
        request = self._attempts.resource_requests.pop(envelope.id, None)
        self.commit.record_outcome_result(
            attempt.id,
            envelope=envelope,
            turn_id=result.turn_id,
            usage_refs=tuple(result.usage_refs),
            resource_request=None
            if request is None
            else self._check_resource_request(mission, task, request),
        )
        self._settle_intent(intent, "SETTLED")
        # Same order as every other outcome: close the intent, then settle (an
        # open Assurance responsibility keeps the reservation held, visibly).
        self._settle_if_known(self.store.get_attempt(attempt.id) or attempt)
        await self._release_attempt(attempt.id, cancel=False)
        self._note(f"attempt {attempt.id}: outcome {envelope.outcome} → RETRY_WAIT")
        return

    async def _reject_read_only_rewrite(
        self,
        intent: DispatchIntent,
        *,
        result: Any,
        attempt: Attempt,
        binding: TaskSemanticBindingV1 | None,
        rewrote: list[str],
    ) -> None:
        assert binding is not None
        self.commit.reject_result(
            attempt.id,
            turn_id=result.turn_id,
            reason="read_only_leaf_rewrote_workspace",
            detail={
                "paths": rewrote,
                "side_effect_kind": str(binding.side_effect_kind),
                "capabilities": list(binding.capability_requirements),
                "hint": (
                    "this leaf's task type is read-only: observe and report at "
                    "its declared output ports; do not change existing files"
                ),
            },
        )
        # The refusal is the permission rule and stays.  What happens next is
        # the Planner's call: the ``ResultRejected`` event becomes an ordinary
        # repair request carrying the paths and how many times it has happened.
        self._settle_intent(intent, "FAILED")
        self._settle_if_known(attempt)
        await self._release_attempt(attempt.id, cancel=False)
        self._note(f"attempt {attempt.id}: read-only leaf rewrote {rewrote} → RETRY_WAIT")
        return

    def _parse_envelope(
        self, text: str, attempt: Attempt, *, turn_id: str
    ) -> tuple[ResultEnvelope, str | None]:
        try:
            raw = extract_block(text, RESULT_ENVELOPE_TAG)
        except BlockError as error:
            raise ContractError(str(error)) from error
        # P2.3c part 2d, decision 4.  ``outputs`` is the hierarchical Worker's
        # "this file is what I produced at that port".  It is taken off the block
        # before ``ResultEnvelope.from_json`` because that contract refuses unknown
        # keys by design (§18.5: a model may not add fields to a system contract),
        # and it is checked *here* — against the ports this occurrence declares and
        # the files this Attempt actually wrote — so a bad claim is a bounded repair
        # on the same Attempt rather than a wrong artifact bound downstream.
        claims = self._port_claims_from(raw, attempt)

        resource_request = resource_requests.pop_request(raw)  # H10: shape errors are ContractError
        client_ids = {raw.get("id"), raw.get("result_id")} - {None}
        if len(client_ids) > 1:
            raise ContractError("result carries both id and result_id with different values")
        client_result_id = None if not client_ids else str(next(iter(client_ids)))
        raw.pop("id", None)
        raw.pop("result_id", None)
        raw.setdefault("mission_id", attempt.mission_id)
        provisional = ResultEnvelope.from_json({**raw, "id": "result-provisional"})
        if provisional.attempt_id != attempt.id or provisional.task_id != attempt.task_id:
            raise ContractError(
                "result identity does not match the Attempt / Task it was submitted for"
            )
        if provisional.mission_id != attempt.mission_id:
            raise ContractError("result mission_id does not match")
        body = provisional.to_json()
        body.pop("id")
        result_id = ids.result_id(attempt.id, turn_id, sha256_hex(body))
        envelope = ResultEnvelope.from_json({**body, "id": result_id})
        if outside_text(text, RESULT_ENVELOPE_TAG):
            logger.info("orchestrator.envelope_prose", extra={"attempt_id": attempt.id})
        if claims:
            self._attempts.port_claims[result_id] = claims
        if resource_request is not None:
            if envelope.outcome is ResultOutcome.CANDIDATE:
                raise ContractError(
                    "resource_request goes with a blocked / failure / no_progress result, not a candidate"
                )
            self._attempts.resource_requests[result_id] = resource_request
        return envelope, client_result_id

    def _port_claims_from(self, raw: dict[str, Any], attempt: Attempt) -> tuple[PortClaim, ...]:
        """Pop ``outputs`` off the block and check it, or refuse the block."""

        if "outputs" not in raw:
            return ()
        stated = raw.pop("outputs")
        mission = self.store.get_mission(attempt.mission_id)
        new_mode = None if mission is None else self._new_mode(mission)
        if new_mode is None:
            raise ContractError(
                "result has unknown fields: ['outputs']"  # the contract's own wording
            )
        declared = new_mode.declared_output_ports_for(attempt.mission_id, attempt.task_id)
        single = [item["port"] for item in declared if item.get("cardinality") == "single"]
        # The files this Attempt wrote are the ones this envelope *declares*: the
        # artifact rows are written from ``envelope.artifacts`` after the result is
        # accepted, so at parse time ``list_artifacts`` is empty and checking against
        # it refused every honest claim (found by the part 2d smoke, round 1).  Each of
        # those paths is separately checked against the real workspace before it
        # becomes an artifact, so a claim can never outlive a file that was not there.
        produced = [str(item) for item in (raw.get("artifacts") or []) if isinstance(item, str)]
        try:
            return parse_port_claims(
                stated,
                declared_ports=[item["port"] for item in declared],
                attempt_paths=produced,
                single_valued=single,
            )
        except BlockError as error:
            raise ContractError(str(error)) from error

    # ---------------------------------------------------------------- verify
    def _verification_superseded_by_accepted_sibling(self, result_id: str) -> bool:
        """Read the durable accept transaction; never settle or change a verdict."""
        losing = self.store.get_result(result_id)
        if losing is None:
            return False
        envelope = losing.envelope
        attempt = self.store.get_attempt(envelope.attempt_id)
        task = self.store.get_task(envelope.task_id)
        if (
            attempt is None
            or task is None
            or attempt.status is not AttemptStatus.SUPERSEDED
            or (attempt.failure or {}).get("reason") != "sibling_accepted"
            or attempt.task_id != task.id
            or attempt.mission_id != envelope.mission_id
            or task.mission_id != envelope.mission_id
            or task.status is not TaskStatus.COMPLETED
            or not task.accepted_result_id
            or task.accepted_result_id == result_id
            or losing.verification_state != "REJECTED"
            or losing.verdict != "superseded"
        ):
            return False
        accepted = self.store.get_result(task.accepted_result_id)
        if (
            accepted is None
            or accepted.verification_state != "DONE"
            or accepted.verdict != "PASS"
            or accepted.envelope.task_id != task.id
            or accepted.envelope.mission_id != task.mission_id
            or accepted.envelope.attempt_id == attempt.id
            or set(accepted.artifacts) != set(task.accepted_artifacts)
        ):
            return False
        winner = self.store.get_attempt(accepted.envelope.attempt_id)
        return (
            winner is not None
            and winner.status is AttemptStatus.COMPLETED
            and winner.task_id == task.id
            and winner.mission_id == task.mission_id
        )

    async def _verify(self, result_id: str) -> bool:
        stored = self.store.get_result(result_id)
        assert stored is not None
        attempt = self.store.get_attempt(stored.envelope.attempt_id)
        task = self.store.get_task(stored.envelope.task_id)
        mission = self.store.get_mission(stored.envelope.mission_id)
        assert attempt is not None and task is not None and mission is not None
        if attempt.status in TERMINAL_ATTEMPT:
            return False  # closed by a cascade; its result was rejected as history
        try:  # D3-10': verification is done by the Attempt's lease holder only
            attempt = self.commit.renew_lease(
                attempt.id,
                owner=self._owner,
                lease_seconds=self._config.lease_seconds,
                liveness={"progress": attempt.progress_marker, "phase": "verifying"},
            )
        except CommitRejected:
            return False
        stored = self.commit.start_verification(result_id)
        protected = self._protected_files(mission, task, attempt)
        tampered = self.assembled.workspaces.tampered_protected(attempt.id, protected)
        artifacts = [
            a for a in self.store.list_artifacts(attempt.id) if a.id in set(stored.artifacts)
        ]
        # P2.3o: a bound input the envelope named is a recorded workspace file.
        # Grok C3's verify leaf listed the patched ``stats/window.py`` the patch
        # leaf had already accepted; the collector dropped it (P2.3m same-hash)
        # and ``rule_check`` refused it as unrecorded.
        artifacts = bound_artifacts_named_in_envelope(
            stored.envelope.artifacts,
            artifacts,
            self._upstream_inputs(attempt),
            self.store.get_artifact,
        )
        # P3.2 review round 2 P1-3: rebuilt from the recorded bytes, never the live tree —
        # what is verified is what was recorded and what an approval will bind
        copy = self.assembled.workspaces.verification_copy(
            attempt.id,
            protected=protected,
            seed=dict((mission.final_report or {}).get("workspace_seed", {})),
            inputs=self._input_files(attempt),
            artifacts=artifacts,
        )
        self._register_copy(
            "verify",
            f"{attempt.id}-verify",
            mission_id=attempt.mission_id,
            attempt_id=attempt.id,
            detail={"artifacts": sorted(a.id for a in artifacts), "protected": sorted(protected)},
        )

        critic_admission_failure: _CriticAdmissionFailure | None = None

        async def recorder(layer: LayerResult) -> None:
            try:
                self._hold_lease(attempt.id)  # P1-3: a lost lease aborts verification
            except CommitRejected:
                if self._verification_superseded_by_accepted_sibling(result_id):
                    raise _AcceptedSiblingSupersededVerification from None
                raise
            detail = {"summary": layer.summary, **dict(layer.detail)}
            if layer.layer == "critic_review":
                if critic_admission_failure is not None:
                    detail["error"] = critic_admission_failure.error
                # A recovered intent may still contain an older prompt than the
                # domain now selects. Attribute only a completed Critic verdict,
                # including a reused layer, to its durable execution ordinal.
                detail.pop("critic_intent_id", None)
                detail["verifier_version"] = None
                if layer.status in {"PASS", "FAIL", NEEDS_HUMAN}:
                    detail.update(self._critic_provenance(mission.id, attempt.id))
            else:
                detail["verifier_version"] = VERIFIER_VERSION
            self.commit.record_verification_layer(
                result_id,
                layer=layer.layer,
                status=layer.status,
                detail=detail,
            )
            if layer.status == "PASS":
                self._fault("after_layer_pass", "attempt")

        async def run_critic(test_output: str | None) -> CriticVerdict:
            nonlocal critic_admission_failure
            try:
                return await self._run_critic(
                    mission,
                    task,
                    view_id=attempt.id,
                    subject_prefix=f"{attempt.id}:critic",
                    account_id=task_account(task.id),
                    artifacts=artifacts,
                    test_output=test_output,
                    attempt_id=attempt.id,
                )
            except _CriticAdmissionFailure as error:
                critic_admission_failure = error
                raise
            except BudgetExhausted as error:
                # a required layer that could not run is an ERROR, never a PASS (ORCH §12.4)
                raise ContractError(f"critic could not be funded: {error}") from error

        human, reuse, escalation_left = self._human_inputs(result_id, task)
        domain = self.commit.domain_for(mission.id)

        try:
            local_check_factory = None

            if self._wiring.assurance_local_checks is None:
                raise AssuranceError("ASSURANCE_LOCAL_CHECKS_UNBOUND")
            local_check_factory = self._wiring.assurance_local_checks.prepare
            verdict = await self._router.verify(
                mission=mission,
                task=task,
                envelope=stored.envelope,
                artifacts=artifacts,
                verification_copy=copy,
                client_result_id=self._attempts.client_ids.get(result_id),
                run_critic=run_critic,
                recorder=recorder,
                tampered=tampered,
                knowledge=KnowledgeIndex.load(self.store, mission.id),
                human=human,
                reuse=reuse,
                needs_human_allowed=escalation_left,
                domain=domain,
                local_check_recorder_factory=local_check_factory,
            )
        except _AcceptedSiblingSupersededVerification:
            self._note(f"result {result_id}: obsolete verifier after sibling acceptance")
            return True
        except (AssuranceError, OperationCompletionError) as error:
            # 这次验证依据的完成范围已经过期（用户改了要求，或计划换了一版）：结果归档为"被取代"，
            # 这一步要不要重做由规划器定（与收集处"按旧版要求做的结果归档"同一条规则）。验证开头
            # 读完成范围就会发现，检查跑到一半撞上也一样。别的错误照旧抛出（HTN 补齐 F1）
            if error.code not in {"CHECK_SCOPE_CHANGED", "OP_EFFECT_SCOPE_STALE"}:
                raise
            if self._scope_carried(result_id):
                return self._verdict_refused(result_id, error)
            return await self._set_aside_stale(result_id)
        if critic_admission_failure is not None:
            admission_detail_error = critic_admission_failure.error
            admission = admission_detail_error["detail"]
            reason = admission.get("reason_code")
            admission_failures = [
                {
                    **item,
                    "detail": {**dict(item.get("detail", {})), "error": admission_detail_error},
                }
                if item.get("layer") == "critic_review"
                else item
                for item in verdict.failures
            ]
            # Commit rejection and its terminal budget/configuration handling
            # atomically: recovery must never see a redo-eligible intermediate.
            with self.store.transaction():
                self.commit.fail_result(result_id, failures=admission_failures, owner=self._owner)
                if reason == "cancelled":
                    self._commit_cancel_mission(mission.id)
                else:
                    stop = (
                        MissionStopReason.BUDGET_EXHAUSTED
                        if reason in {"budget_exhausted", "deadline"}
                        else MissionStopReason.RUNTIME_UNAVAILABLE
                    )
                    self._commit_stop_task(
                        task.id,
                        stop_reason=stop,
                        detail={
                            "source_kind": "provider_admission",
                            "retryable": False,
                            "admission": admission,
                            "result_id": result_id,
                            **({"dimension": "runtime"} if reason == "deadline" else {}),
                        },
                    )
            await self._release_mission(mission.id)
            self._note(f"task {task.id}: Critic admission denied ({reason}) -> stopped")
            return True
        if verdict.critic is not None:
            self._reviews.critic_verdicts[result_id] = verdict.critic
        if verdict.suspended:  # D7-8': the sixth layer waits for a person
            reason = (
                "needs_human"
                if any(layer.status == NEEDS_HUMAN for layer in verdict.layers)
                else "policy"
            )
            try:
                self.commit.suspend_verification(
                    result_id,
                    owner=self._owner,
                    reason=reason,
                    layers=[layer.to_json() for layer in verdict.layers],
                )
            except (CommitRejected, IllegalTransition, ActionCommitError) as error:
                self._note(f"result {result_id}: suspension dropped ({error})")
                return True
            self._note(f"result {result_id} suspended: waiting for a person ({reason})")
            return True
        try:
            if verdict.passed:
                completed = self.commit.accept_result(
                    result_id,
                    verifier_results=[
                        layer.to_json() for layer in verdict.layers if layer.status == "PASS"
                    ],
                    owner=self._owner,
                    connectors=self._connectors,
                    deployment=self._config.deployment_policy,
                )
            else:
                self.commit.fail_result(result_id, failures=verdict.failures, owner=self._owner)
        except (AssuranceError, OperationCompletionError) as error:
            # 验完、提交结论前，用户改了要求或计划换了一版：同上，结果归档为"被取代"（阶段 G
            # 随机序列发现：这一处与验证开头是同一条规则的两个时刻）
            if error.code not in {"CHECK_SCOPE_CHANGED", "OP_EFFECT_SCOPE_STALE"}:
                raise
            if self._scope_carried(result_id):
                return self._verdict_refused(result_id, error)
            return await self._set_aside_stale(result_id)
        except (CommitRejected, IllegalTransition, ResolutionCommitRejected) as error:
            # the Attempt was closed / taken over while we verified (P1-4): the verdict is
            # dropped; the library's state is whatever the other Commit made it. An
            # assured acceptance refused by its current use certificate lands here too.
            self._note(f"result {result_id}: verdict dropped ({error})")
            # Visible in the Host log (progress notes are not): a refusal that repeats
            # every round is a stuck Mission, not progress.
            logger.warning(
                "orchestrator.verdict_dropped result=%s mission=%s error=%s: %s",
                result_id,
                mission.id,
                type(error).__name__,
                error,
            )
            return self._verdict_refused(result_id, error)
        self._reviews.verdict_refusals.pop(result_id, None)
        accepted = verdict.passed and (
            completed.status is TaskStatus.COMPLETED or completed.accepted_result_id == result_id
        )
        if accepted:
            self._fault("after_task_completed", "attempt")
            self._note(f"result {result_id} PASS → task {completed.id} {completed.status}")
            for sibling in self.store.list_attempts(task.id):
                if sibling.status is AttemptStatus.SUPERSEDED:
                    await self._release_attempt(sibling.id, cancel=True)
        else:
            self._note(f"result {result_id} FAIL at {verdict.short_circuited_at}")
            undeployed = [
                layer.layer
                for layer in verdict.layers
                if layer.status == "ERROR" and layer.detail.get("undeployed")
            ]
            if undeployed:
                # D6-9': a required verifier that is not deployed blocks — no retry can make
                # it appear, and a missing layer is never a PASS
                self._commit_stop_task(
                    task.id,
                    stop_reason=MissionStopReason.VERIFIER_UNAVAILABLE,
                    detail={"layers": undeployed, "result_id": result_id},
                )
                await self._release_mission(mission.id)
                self._note(f"task {task.id} stopped: verifier(s) {undeployed} not deployed")
                return True
        return True

    async def _carried_review_once(self, item: Any) -> bool:
        """TaskGraph 补全第四批：一份按旧版要求通过、被现行计划沿用的结果，按现行要求重审。

        与新结果同一条"本地检查 → 审阅员独立会话 → 写验收"的流程，只是驱动不同：由计划提交后
        的扫描驱动，不要尝试租约、不建尝试、不调执行者、不动尝试 / 任务 / 产物状态；验证记录与
        验收都按（结果, 要求版本）记。通过 → 按新版要求写一条验收；没过 → 修复请求交规划器
        （``CARRIED_RESULT_REJECTED``）；审阅员两次都判不下来 → 问用户裁决。"""

        revision = int(item.requirements_revision)
        stored = self.store.get_result(item.result_id)
        attempt = None if stored is None else self.store.get_attempt(stored.envelope.attempt_id)
        task = self.store.get_task(item.task_id)
        mission = self.store.get_mission(item.mission_id)
        if (
            stored is None
            or attempt is None
            or task is None
            or mission is None
            or self._wiring.assurance_reviews is None
            or self._wiring.assurance_local_checks is None
            or not carried_review_alive(self.store, mission.id, task.id)
        ):
            return False
        reviews = self._wiring.assurance_reviews
        artifacts = [
            a for a in self.store.list_artifacts(attempt.id) if a.id in set(stored.artifacts)
        ]
        artifacts = bound_artifacts_named_in_envelope(
            stored.envelope.artifacts,
            artifacts,
            self._upstream_inputs(attempt),
            self.store.get_artifact,
        )
        copy = self.assembled.workspaces.verification_copy(
            attempt.id,
            protected=self._protected_files(mission, task, attempt),
            seed=dict((mission.final_report or {}).get("workspace_seed", {})),
            inputs=self._input_files(attempt),
            artifacts=artifacts,
        )

        async def recorder(layer: LayerResult) -> None:
            detail = {"summary": layer.summary, **dict(layer.detail)}
            if layer.layer == "critic_review":
                detail.pop("critic_intent_id", None)
                detail["verifier_version"] = None
                if layer.status in {"PASS", "FAIL", NEEDS_HUMAN}:
                    detail.update(reviews.provenance(mission.id, attempt.id, revision))
            else:
                detail["verifier_version"] = VERIFIER_VERSION
            self.commit.record_verification_layer(
                item.result_id,
                layer=layer.layer,
                status=layer.status,
                detail=detail,
                requirements_revision=revision,
            )

        async def run_critic(test_output: str | None) -> CriticVerdict:

            try:
                return await reviews.run_task(
                    mission, task, attempt_id=attempt.id, requirements_revision=revision
                )
            except (AssuranceError, RoutingUnavailable, BudgetExhausted) as error:
                # 与新结果同一个口径：审阅员这一层跑不起来是"出错"，不是没过、更不是通过
                raise ContractError(f"Assurance review unavailable: {error}") from error

        with self.store.read_view():
            record = reviews.task_record(mission.id, attempt.id, revision)
        ruling = None if record is None else adjudication_of(self.store, str(record.record_id))
        if record is not None and ruling is None and str(record.verdict) == "INCONCLUSIVE":
            # 审阅员两次都判不下来、在等用户裁决：不重跑检查，只看问题答了没有
            return self._ask_carried_ruling(mission, task, record, item)
        human = (
            None
            if ruling is None
            else {
                "verdict": "PASS" if ruling.get("decision") == "pass" else "FAIL",
                "note": "",
                "principal": ruling.get("principal_id"),
                "request_id": "adjudicate-carried:" + str(record.record_id),
            }
        )
        verdict = await self._router.verify(
            mission=mission,
            task=task,
            envelope=stored.envelope,
            artifacts=artifacts,
            verification_copy=copy,
            client_result_id=None,
            run_critic=run_critic,
            recorder=recorder,
            tampered=(),
            knowledge=KnowledgeIndex.load(self.store, mission.id),
            human=human,
            reuse=None,
            needs_human_allowed=True,
            domain=self.commit.domain_for(mission.id),
            local_check_recorder_factory=functools.partial(
                self._wiring.assurance_local_checks.prepare, requirements_revision=revision
            ),
        )
        if verdict.passed:
            self.commit.accept_carried_result(item.result_id, requirements_revision=revision)
            self._note(
                f"result {item.result_id}: kept and passed again under requirements r{revision}"
            )
            return True
        errored = [layer for layer in verdict.layers if layer.status == "ERROR"]
        if errored:
            # 检查或审阅员这一层出错（不是谁的判断）：按次数上限重来，不当成没过
            raise ContractError("; ".join(f"{layer.layer}: {layer.summary}" for layer in errored))
        with self.store.read_view():
            record = reviews.task_record(mission.id, attempt.id, revision)
        if verdict.suspended and record is not None:
            return self._ask_carried_ruling(mission, task, record, item)
        return self._request_carried_repair(
            mission,
            task,
            item,
            record=record,
            failures=[dict(failure) for failure in verdict.failures],
        )

    def _ask_carried_ruling(self, mission: Mission, task: Task, record: Any, item: Any) -> bool:
        # 内容审阅判的是那份结果：裁决回执的对象写结果，按新版写验收时使用凭证才认它
        return self._ask_person_to_adjudicate(
            mission,
            record,
            target_id=item.result_id,
            subject_key=task.id,
            task_id=task.id,
            decision_id="adjudicate-carried:" + str(record.record_id),
            intro="改要求后沿用的一步（"
            + str(task.goal)[:60]
            + "）按新要求重审，两位审阅员都判不下来，"
            "需要你裁决它是否仍然合格。",
            extra={
                "result_id": item.result_id,
                "requirements_revision": int(item.requirements_revision),
            },
        )

    def _request_carried_repair(
        self,
        mission: Mission,
        task: Task,
        item: Any,
        *,
        record: Any = None,
        failures: Sequence[Mapping[str, Any]] = (),
        error: str | None = None,
    ) -> bool:
        """The one durable end of a re-review that did not accept: a repair request to the
        Planner (the scan stops on it).  Recorded once per (result, requirements revision)."""

        revision = int(item.requirements_revision)
        produced = record_request(
            self._new_mode(mission),
            mission.id,
            event_type="VerifierAcceptanceRejected",
            trigger_refs=(task.id,),
            source_key=source_key(item.result_id, revision),
            detail={
                "source": "carried_review",
                "reason_code": CARRIED_RESULT_REJECTED,
                "result_id": item.result_id,
                "requirements_revision": revision,
                "occurrence_id": item.occurrence_id,
                **(
                    {}
                    if record is None
                    else {
                        "record_id": str(record.record_id),
                        "findings": self._review_record_findings(record),
                    }
                ),
                **({} if error is None else {"review_error": error[:2000]}),
                "failures": [dict(failure) for failure in failures],
            },
        )
        if produced:
            self._note(
                f"result {item.result_id}: kept, not accepted under requirements r{revision}; repair requested"
            )
        return produced

    async def _carried_review(self, item: Any) -> bool:
        """One round of a kept result's review, inside its own fault boundary: nothing it
        raises reaches the main loop (a fault of one Mission stops only that step's review).
        A round that could not conclude — the plan moved, the reviewer or a checker was
        unavailable, anything unexpected — is retried, at most ``MAX_CARRIED_REVIEW_ERRORS``
        times; then the Planner is told the review could not be done and decides."""

        try:
            done = await self._carried_review_once(item)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - the boundary; the count below is the bound
            count = self._reviews.carried_errors.get(item.key, 0) + 1
            text = f"{type(error).__name__}: {error}"
            self._note(
                f"result {item.result_id}: re-review under r{item.requirements_revision} "
                f"could not conclude ({count}/{MAX_CARRIED_REVIEW_ERRORS}): {text}"
            )
            if count < MAX_CARRIED_REVIEW_ERRORS:
                self._reviews.carried_errors[item.key] = count
                return False
            self._reviews.carried_errors.pop(item.key, None)
            logger.warning(
                "orchestrator.carried_review_gave_up result=%s mission=%s error=%s",
                item.result_id,
                item.mission_id,
                text,
            )
            mission, task = (
                self.store.get_mission(item.mission_id),
                self.store.get_task(item.task_id),
            )
            if mission is None or task is None:
                return False
            try:
                return self._request_carried_repair(mission, task, item, error=text)
            except Exception as late:  # noqa: BLE001 - never out of the boundary
                self._note(f"result {item.result_id}: repair request not recorded ({late})")
                return False
        self._reviews.carried_errors.pop(item.key, None)
        return done

    def _critic_provenance(self, mission_id: str, attempt_id: str) -> dict[str, str]:
        """The one parsed Critic verdict's durable intent, not a current template.

        Invalid verdicts settle FAILED; the successful ordinal settles SETTLED
        before returning its verdict. This survives a crash before layer recording
        and also interprets legacy layers that did not record an intent id.
        Missing or ambiguous execution identity is never proof of a prompt version.
        """

        if self._wiring.assurance_reviews is None:
            return {}
        return self._wiring.assurance_reviews.provenance(mission_id, attempt_id)

    def _human_inputs(
        self, result_id: str, task: Task
    ) -> tuple[dict[str, Any] | None, dict[str, LayerResult] | None, bool]:
        """D7-8': a person's answer to this result's review (if any), the recorded layers
        a resumed verification may reuse, and whether this Task may still escalate."""

        request = self.store.get_approval(review_request_id(result_id))
        human: dict[str, Any] | None = None
        reuse: dict[str, LayerResult] | None = None
        if request is not None and request["state"] in {"GRANTED", "REJECTED"}:
            human = {
                "verdict": "PASS" if request["state"] == "GRANTED" else "FAIL",
                "note": request.get("note", ""),
                "principal": request.get("decided_by"),
                "request_id": request["request_id"],
            }
            stored = self.store.get_result(result_id)
            provenance = (
                self._critic_provenance(task.mission_id, stored.envelope.attempt_id)
                if stored is not None
                and stored.envelope.task_id == task.id
                and stored.envelope.mission_id == task.mission_id
                else {}
            )

            rows = []
            revision = None if stored is None else frozen_requirements_revision(self.store, stored)
            for row in (
                []
                if revision is None
                else self.store.list_verifications(result_id, requirements_revision=revision)
            ):
                if row["layer"] == "critic_review":
                    detail = row.get("detail") or {}
                    if (
                        not provenance
                        or detail.get("verifier_version") != provenance["verifier_version"]
                        or (
                            "critic_intent_id" in detail
                            and detail["critic_intent_id"] != provenance["critic_intent_id"]
                        )
                    ):
                        continue
                rows.append(row)
            reuse = reusable_layers(
                rows,
                versions={"critic_review": provenance["verifier_version"]} if provenance else {},
                default_version=VERIFIER_VERSION,
            )
        escalated_before = any(
            r["kind"] == "review"
            and r.get("reason") == "needs_human"
            and r.get("task_id") == task.id
            and r["subject_key"] != result_id
            for r in self.store.list_approvals(task.mission_id)
        )
        return human, reuse, not escalated_before

    def _arbitrated(
        self,
        mission: Mission,
        tasks: Sequence[Task],
        key: str,
        judgments: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]] | None, bool]:
        """D7-8' kind ②: when the independent judge Critic disagrees with the Tasks' own
        Critics on a criterion everything else says is met, a person rules.  Returns the
        judgments to use (``None`` while waiting) and whether a request was just opened."""

        opinions: dict[
            str, list[bool]
        ] = {}  # review P2-7: what each Task Critic said, per criterion
        for task in tasks:
            # 一份结果可能按几版要求审过，取最新一版的审阅意见
            layers = self.store.list_verifications(
                task.accepted_result_id or "", requirements_revision=None
            )
            newest = max((int(row["requirements_revision"]) for row in layers), default=0)
            for layer in (row for row in layers if int(row["requirements_revision"]) == newest):
                if layer["layer"] != "critic_review" or layer["status"] != "PASS":
                    continue
                for item in (layer.get("detail") or {}).get("mission_criteria", []):
                    opinions.setdefault(str(item.get("criterion")), []).append(
                        bool(item.get("met"))
                    )
        contested = judgment_conflict(judgments, task_opinions=opinions)
        if not contested:
            return [dict(j) for j in judgments], False
        subject = f"{mission.id}:judgment:{key}"
        request = self.store.get_approval(arbitration_request_id(subject))
        if request is None:
            self.commit.request_arbitration(
                mission.id,
                subject=subject,
                topic="judgment",
                options=("met", "unmet"),
                context={"criteria": contested, "judgments": [dict(j) for j in judgments]},
            )
            self._note(f"mission {mission.id}: Verifiers disagree on {contested} → arbitration")
            return None, True
        if request["state"] != "GRANTED":
            return None, False
        ruling = str(request.get("ruling"))
        return [
            {
                **dict(j),
                "met": ruling == "met",
                "judge": "human_arbitration",
                "reason": f"arbitrated by {request.get('decided_by')}: {request.get('basis')}",
                "override_id": request.get("override_id"),
            }
            if j.get("criterion") in contested
            else dict(j)
            for j in judgments
        ], False

    def _hold_lease(self, attempt_id: str) -> Attempt:
        """Renew this owner's lease during a long verification; ``CommitRejected`` when
        another owner took the Attempt over after a lapse (P1-3)."""

        attempt = self.store.get_attempt(attempt_id)
        assert attempt is not None
        return self.commit.renew_lease(
            attempt.id,
            owner=self._owner,
            lease_seconds=self._config.lease_seconds,
            liveness={"progress": attempt.progress_marker, "phase": "verifying"},
            minimum_remaining_seconds=self._config.lease_seconds / 2,
        )

    def _protected_seed(self, mission: Mission, task: Task) -> dict[str, str]:
        """Seed files the Worker may not rewrite: pytest targets and anything under tests/ (P0-1)."""

        seed = dict((mission.final_report or {}).get("workspace_seed", {}))
        targets = [
            c.removeprefix("pytest:").strip()
            for c in (*task.success_criteria, *current_statements(self.store, mission))
            if c.startswith("pytest:")
        ]
        protected = {}
        for path, content in seed.items():
            if path.startswith("tests/") or any(
                path == target or (target and path.startswith(target.rstrip("/") + "/"))
                for target in targets
            ):
                protected[path] = content
        return protected

    def _revises_its_inputs(self, mission_id: str, task_id: str) -> bool:
        """Every declared input port is also an output port of the same schema."""

        binding = htn_store.HtnStore(self.store).task_semantics_of(mission_id, task_id)
        if binding is None or not binding.input_ports:
            return False
        outputs = {(port.port_key, port.schema_ref) for port in binding.output_ports}
        return all((port.port_key, port.schema_ref) in outputs for port in binding.input_ports)

    def _protected_files(
        self, mission: Mission, task: Task, attempt: Attempt
    ) -> dict[str, str | bytes]:
        """Protected seed files plus every upstream input the Task did not declare as
        one of its ``outputs`` (D3-7': a downstream Worker may not silently rewrite what
        its dependencies delivered; rewriting an undeclared path needs a new Task)."""

        protected: dict[str, str | bytes] = dict(self._protected_seed(mission, task))
        declared = set(task.outputs)
        seed_paths = set((mission.final_report or {}).get("workspace_seed", {}))
        revises_inputs = self._revises_its_inputs(mission.id, task.id)
        for item in self._upstream_inputs(attempt):
            if item.path in declared:  # the Task declared it will rewrite this path
                continue
            if revises_inputs:
                # 2026-09-25 UI 全量点击: a revise-in-place Task (every input port is
                # also one of its output ports, e.g. desktop.continue-delivery) exists
                # to deliver the next version of what it received.  Protecting those
                # paths made it impossible — the document continuation could never
                # write summary.md and failed until its attempts ran out.  The upstream
                # artifact itself stays immutable (content-addressed).
                continue
            # P2.3o: a seed path overlaid from a bound producer is the consumer's
            # baseline (so ``code_test`` runs on the accepted patch), not a
            # protected port document.  Rewriting it to *new* bytes is still a
            # read-only rewrite (P2.3m); pinning it here would reclassify that
            # as ``protected_path_rewritten`` and skip the planning escalation.
            if item.path in seed_paths:
                continue
            artifact = self.store.get_artifact(item.artifact_id)
            try:  # P3.2 D3: the stored bytes, hash re-checked
                if artifact is None:
                    raise ArtifactStoreError("missing", item.path)
                raw = read_verified(artifact)
            except ArtifactStoreError as error:
                raise ArtifactConflict(
                    f"upstream artifact {item.artifact_id} ({item.path}) is missing"
                ) from error
            try:
                protected[item.path] = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ArtifactConflict(f"upstream artifact {item.path} is not text") from error
        # Source registration never grants a Worker permission to change source bytes,
        # even when it declares that path as an output.
        protected.update(self._source_files(attempt))
        return protected

    async def _run_critic(
        self,
        mission: Mission,
        task: Task | None,
        *,
        view_id: str,
        subject_prefix: str,
        account_id: str,
        artifacts: Sequence[Artifact],
        test_output: str | None,
        attempt_id: str | None = None,
    ) -> CriticVerdict:

        if task is None or attempt_id is None or self._wiring.assurance_reviews is None:
            raise ContractError("Assurance review builder is not installed for this purpose")
        try:
            return await self._wiring.assurance_reviews.run_task(
                mission, task, attempt_id=attempt_id
            )
        except (AssuranceError, RoutingUnavailable) as error:
            raise ContractError(f"Assurance review unavailable: {error}") from error
