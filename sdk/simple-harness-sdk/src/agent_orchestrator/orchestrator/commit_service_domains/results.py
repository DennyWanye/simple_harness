# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (long event / receipt literals)

"""Results, verification layers and acceptance."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ...artifacts.versioning import next_versions
from ...assurance.codec import AssuranceError
from ...contracts import (
    TERMINAL_TASK,
    Artifact,
    Attempt,
    AttemptStatus,
    Claim,
    ClaimStatus,
    Event,
    Mission,
    ResultEnvelope,
    ResultOutcome,
    Task,
    TaskStatus,
    ids,
)
from ...contracts.models import jsonable, sha256_hex
from ...governance.domains import CODE_DOMAIN, DomainProfileV1, check_against_domain
from ...governance.policies import DeploymentPolicy
from ...memory.verified_knowledge import KnowledgeIndex
from ...scheduling.allocator import OPEN_ATTEMPT_STATES
from ...storage.operation_completion_store import OperationCompletionStore
from ...storage.store import StoredResult
from ..assurance_validity import ACCEPTANCE_CONSUMER, acceptance_id_for
from ..completion_inputs import (
    frozen_requirements_revision,
    load_completion_result_inputs,
    validate_result_port_claims,
)
from ..leaf_acceptance import LeafAcceptanceAssembly
from ..resolution_commits import ResolutionCommitRejected
from ..resource_requests import REQUESTED
from ..state_machine import next_attempt, next_claim, next_task
from .core import CommitServiceCore
from .support import ARTIFACT_MERGE_NOT_APPLICABLE, SUBMITTED_STATES, CommitRejected


class ResultCommitsMixin(CommitServiceCore):
    """Results, verification layers and acceptance."""

    __slots__ = ()

    if TYPE_CHECKING:

        def _close_attempt(
            self, attempt: Attempt, target: AttemptStatus, *, reason: str
        ) -> Attempt: ...
        def _grade_and_project(
            self,
            mission: Mission,
            task: Task,
            attempt: Attempt,
            stored: StoredResult,
            review: Any = None,
            certificate: Any = None,
        ) -> list[dict[str, Any]]: ...
        def _release_attempt_charge(self, attempt: Attempt) -> bool: ...
        def propose_step_action(
            self,
            request: Any,
            *,
            mission_id: str,
            task_id: str,
            attempt_id: str,
            result_id: str,
            prepared_params: Mapping[str, Any] | None,
            prepare_refusal: str | None,
            connectors: Mapping[str, Any],
            deployment: Any,
        ) -> tuple[dict[str, Any] | None, str | None]: ...
        def _settle_subject(
            self,
            subject_id: str,
            mission_id: str,
            *,
            task_id: str | None,
            tool_calls: int | None = None,
        ) -> Mapping[str, Any]: ...
        def domain_for(self, mission_id: str) -> DomainProfileV1: ...

        def refuse_policy_files(
            self, stored: Any, *, mission_id: str, task_id: str, result_id: str
        ) -> None: ...

    def check_result_evidence(self, mission_id: str, envelope: ResultEnvelope) -> None:
        """P33-08/09: refuse disallowed evidence kinds before result admission.

        This is a domain vocabulary gate, not proof that a reference resolves. Code
        evidence retains its legacy semantics (D2); source/knowledge validation is
        still performed by the verifier. Inspect all claims and the envelope, even
        when claim-local evidence would otherwise override the envelope's evidence.
        """

        domain = self.domain_for(mission_id)
        if domain.id == CODE_DOMAIN:
            return
        references = [
            *envelope.evidence,
            *(ref for claim in envelope.claims for ref in claim.evidence),
        ]
        kinds = tuple(
            sorted({ref.partition(":")[0] if ":" in ref else "file" for ref in references})
        )
        problems = check_against_domain(
            domain, key="result evidence", success_criteria=(), evidence_kinds=kinds
        )
        if problems:
            raise CommitRejected("result_evidence_kind_not_allowed: " + "; ".join(problems))

    def record_result(
        self,
        attempt_id: str,
        *,
        envelope: ResultEnvelope,
        turn_id: str,
        artifacts: Sequence[Artifact],
        usage_refs: Sequence[str],
        port_claims: Sequence[Any] = (),
    ) -> StoredResult:
        """ResultSubmitted (§16.2): Attempt RUNNING → SUBMITTED, Task ACTIVE → VERIFYING.

        Idempotent on ``(attempt_id, turn_id)``: a duplicate delivery returns the
        stored result and appends nothing (§17.4).
        """

        with self._store.transaction():
            attempt = self._require_attempt(attempt_id)
            existing = self._store.find_result_for_attempt(attempt_id)
            if existing is not None and existing.turn_id == turn_id:
                frozen = load_completion_result_inputs(self._store, existing)
                if tuple(port_claims) != tuple(frozen.port_claims):
                    raise CommitRejected("result replay changed its frozen output claims")
                return existing
            if attempt.status is not AttemptStatus.RUNNING:
                raise CommitRejected(
                    f"attempt {attempt_id} is {attempt.status}; cannot accept a result"
                )
            if envelope.attempt_id != attempt_id or envelope.task_id != attempt.task_id:
                raise CommitRejected("result identity does not match the Attempt")
            if attempt.turn_id != turn_id:
                raise CommitRejected("result turn differs from the Attempt's bound turn")
            self.check_result_evidence(attempt.mission_id, envelope)
            stored = StoredResult(
                envelope=envelope,
                turn_id=turn_id,
                verification_state="PENDING",
                verdict=None,
                received_at=self._store.now,
                artifacts=tuple(artifact.id for artifact in artifacts),
                usage_refs=tuple(usage_refs),
            )
            # D4-15 (L3-2): the version is assigned here, inside the transaction, along the
            # (mission, path) lineage — two candidates snapshotting concurrently never collide
            lineage = next_versions(self._store.list_mission_artifacts(attempt.mission_id))
            registered: list[Artifact] = []
            for artifact in artifacts:
                if self._store.get_artifact(artifact.id) is None:
                    version = lineage.get(artifact.path, 0) + 1
                    lineage[artifact.path] = version
                    artifact = replace(artifact, version=version)
                self._store.upsert_artifact(artifact)
                registered.append(artifact)
            stored = replace(stored, artifacts=tuple(artifact.id for artifact in registered))

            completion_claims = validate_result_port_claims(
                self._store,
                commit=self,
                mission=self._require_mission(attempt.mission_id),
                task=self._require_task(attempt.task_id),
                result=stored,
                artifacts=registered,
                port_claims=port_claims,
            )
            self._store.insert_result(stored)
            self._store.fault("mid_commit", "attempt")
            for index, proposal in enumerate(envelope.claims, start=1):
                self._store.upsert_claim(
                    Claim(
                        id=ids.claim_id(envelope.id, index),
                        content=proposal.content,
                        type=proposal.type,
                        status=ClaimStatus.PROPOSED,
                        source_task=attempt.task_id,
                        source_attempt=attempt_id,
                        evidence=proposal.evidence or envelope.evidence,
                        dependencies=envelope.used_knowledge,
                        verifier_results=(),
                        confidence_metadata={
                            "self_reported_confidence": proposal.confidence,
                            "proposed_supersedes": proposal.supersedes,
                        },
                        supersedes=None,
                        mission_id=attempt.mission_id,
                        result_id=envelope.id,
                        key=proposal.key,
                        stance=proposal.stance,
                        proposed_by=attempt.agent_id or "",
                        contradicts=proposal.contradicts,
                    )
                )
            self._store.update_attempt(
                next_attempt(attempt, AttemptStatus.SUBMITTED, result_id=envelope.id),
                expected_version=attempt.version,
            )
            task = self._require_task(attempt.task_id)
            if (
                task.status is TaskStatus.ACTIVE
            ):  # D3-5': another candidate may already be VERIFYING
                self._store.update_task(
                    next_task(task, TaskStatus.VERIFYING), expected_version=task.version
                )
            self._emit(
                "ResultSubmitted",
                attempt.mission_id,
                key=envelope.id,
                task_id=attempt.task_id,
                attempt_id=attempt_id,
                payload={
                    "result_id": envelope.id,
                    "outcome": str(envelope.outcome),
                    "artifacts": list(stored.artifacts),
                    "claims": len(envelope.claims),
                    **(
                        {"completion_port_claims": list(completion_claims)}
                        if completion_claims is not None
                        else {}
                    ),
                },
                actor_type="agent",
                actor_id=attempt.agent_id or attempt_id,
            )
            return stored

    def record_outcome_result(
        self,
        attempt_id: str,
        *,
        envelope: ResultEnvelope,
        turn_id: str,
        usage_refs: Sequence[str],
        resource_request: Mapping[str, Any] | None = None,
        action_request: Mapping[str, Any] | None = None,
    ) -> StoredResult:
        """A non-candidate Result Envelope (§13 blocked / failure / no_progress /
        proposed_subtasks; D5-5): kept as history (never verified), the Attempt ends in
        RETRY_WAIT with the outcome as its failure and the Task stays ACTIVE: it is tried
        again within its attempt allowance.  Idempotent on (attempt, turn).

        Settlement is the caller's next step, after it closes the dispatch intent
        (2026-09-25): settling in here ran before the intent was closed, which the
        Assurance lane refuses -- every loop round then failed on the same row."""

        with self._store.transaction():
            attempt = self._require_attempt(attempt_id)
            existing = self._store.find_result_for_attempt(attempt_id)
            if existing is not None and existing.turn_id == turn_id:
                return existing
            if attempt.status is not AttemptStatus.RUNNING:
                raise CommitRejected(
                    f"attempt {attempt_id} is {attempt.status}; cannot accept a result"
                )
            if envelope.attempt_id != attempt_id or envelope.task_id != attempt.task_id:
                raise CommitRejected("result identity does not match the Attempt")
            if envelope.outcome is ResultOutcome.CANDIDATE:
                raise CommitRejected("a candidate result goes through record_result / verification")
            stored = StoredResult(
                envelope=envelope,
                turn_id=turn_id,
                verification_state="REJECTED",
                verdict=f"outcome:{envelope.outcome}",
                received_at=self._store.now,
                artifacts=(),
                usage_refs=tuple(usage_refs),
            )
            self._store.insert_result(stored)
            for index, proposal in enumerate(envelope.claims, start=1):
                claim = Claim(
                    id=ids.claim_id(envelope.id, index),
                    content=proposal.content,
                    type=proposal.type,
                    status=ClaimStatus.PROPOSED,
                    source_task=attempt.task_id,
                    source_attempt=attempt_id,
                    evidence=proposal.evidence or envelope.evidence,
                    dependencies=envelope.used_knowledge,
                    verifier_results=(),
                    confidence_metadata={
                        "self_reported_confidence": proposal.confidence,
                        "grade": "not_verified_non_candidate",
                    },
                    supersedes=None,
                    mission_id=attempt.mission_id,
                    result_id=envelope.id,
                    key=proposal.key,
                    stance=proposal.stance,
                    proposed_by=attempt.agent_id or "",
                )
                self._store.upsert_claim(claim)
                self._store.upsert_claim(
                    next_claim(next_claim(claim, ClaimStatus.UNDER_REVIEW), ClaimStatus.REJECTED)
                )
            failure: dict[str, Any] = {
                "reason": f"outcome_{envelope.outcome}",
                "summary": envelope.summary,
                "proposed_tasks": [dict(item) for item in envelope.proposed_tasks],
                "risks": list(envelope.risks),
                "result_id": envelope.id,
            }
            step_action_key: str | None = None
            if action_request is not None:
                # 中途提议的动作：和这次结果同一个事务进账本；记在失败里，续做时按它找结果
                opened, refusal = self.propose_step_action(
                    action_request["request"],
                    mission_id=attempt.mission_id,
                    task_id=attempt.task_id,
                    attempt_id=attempt_id,
                    result_id=envelope.id,
                    prepared_params=action_request.get("prepared_params"),
                    prepare_refusal=action_request.get("prepare_refusal"),
                    connectors=action_request["connectors"],
                    deployment=action_request["deployment"],
                )
                # 进了账本记动作号；门口拒了记原因——两种都是"为提议动作而停"，续做不问规划器
                step_action_key = (
                    str(opened["action_key"]) if opened is not None else f"refused:{refusal}"
                )
                failure["action_request"] = step_action_key
            self._store.update_attempt(
                next_attempt(
                    attempt, AttemptStatus.RETRY_WAIT, result_id=envelope.id, failure=failure
                ),
                expected_version=attempt.version,
            )
            self._emit(
                "ResultSubmitted",
                attempt.mission_id,
                key=envelope.id,
                task_id=attempt.task_id,
                attempt_id=attempt_id,
                payload={
                    "result_id": envelope.id,
                    "outcome": str(envelope.outcome),
                    "artifacts": [],
                    "claims": len(envelope.claims),
                    "proposed_tasks": [dict(item) for item in envelope.proposed_tasks],
                },
                actor_type="agent",
                actor_id=attempt.agent_id or attempt_id,
            )
            self._emit(
                "OutcomeRecorded",
                attempt.mission_id,
                key=envelope.id,
                task_id=attempt.task_id,
                attempt_id=attempt_id,
                payload={
                    "outcome": str(envelope.outcome),
                    "summary": envelope.summary[:400],
                    **({"action_request": step_action_key} if step_action_key else {}),
                },
            )
            if resource_request is not None:
                # 申请与核的结果，和这次结果同一个事务

                self._emit(
                    REQUESTED,
                    attempt.mission_id,
                    key=envelope.id,
                    task_id=attempt.task_id,
                    attempt_id=attempt_id,
                    payload={**dict(resource_request), "result_id": envelope.id},
                    actor_type="agent",
                    actor_id=attempt.agent_id or attempt_id,
                )
            return stored

    def record_artifact_merge_not_applicable(
        self,
        mission_id: str,
        *,
        subject: str,
        contributions: int,
        artifacts: int,
        superseded: Sequence[Mapping[str, Any]],
    ) -> Event:
        """P2.3k / defect N3: the legacy artifact merge a hierarchical Mission does not run.

        Keyed by the subject so the durable record is written once per Mission judgment
        tree rather than once per cycle.
        It carries the same reason code the mode gate would have raised
        (``SEMANTICS_IS_HIERARCHICAL``) and, so the reader can see what the tree holds
        instead, every path more than one contribution wrote — with the writer that was
        kept and why (``acceptance_order`` or ``criterion_link``).
        """

        return self._emit(
            ARTIFACT_MERGE_NOT_APPLICABLE,
            mission_id,
            key=subject,
            payload={
                "reason": "SEMANTICS_IS_HIERARCHICAL",
                "subject": subject,
                "redirect": "root_resolution_contributions",
                "contributions": int(contributions),
                "artifacts": int(artifacts),
                "superseded": [dict(item) for item in superseded],
                "detail": (
                    "this Mission runs under the hierarchical semantics; an all-ancestors "
                    "merge would read 'independent branches' off Task.dependency_ids, "
                    "which the materialised occurrences leave empty by design, so two leaves "
                    "writing one path would be an artifact_conflict overturning a root "
                    "GoalResolution that already stands. The judgment tree is built from "
                    "the resolution's CURRENT contributions instead: later acceptances "
                    "override earlier ones and a criterion-linked step's accepted output "
                    "overrides the rest (§9.1, §21.5)."
                ),
            },
        )

    def record_late_result(
        self, attempt_id: str, *, turn_id: str, summary: str, artifacts: Sequence[str]
    ) -> Attempt:
        """A result that arrived after its Attempt was superseded / cancelled (D3-6'):
        history only — ``ResultRejected(reason=superseded)``, no state transition."""

        with self._store.transaction():
            attempt = self._require_attempt(attempt_id)
            if attempt.status not in {AttemptStatus.SUPERSEDED, AttemptStatus.CANCELLED}:
                raise CommitRejected(f"attempt {attempt_id} is {attempt.status}; not a late result")
            self._emit(
                "ResultRejected",
                attempt.mission_id,
                key=f"{attempt_id}:{turn_id}",
                task_id=attempt.task_id,
                attempt_id=attempt_id,
                payload={
                    "reason": "superseded",
                    "detail": {"summary": summary, "artifacts": list(artifacts)},
                    "turn_id": turn_id,
                },
            )
            return attempt

    def reject_result(
        self, attempt_id: str, *, turn_id: str, reason: str, detail: Mapping[str, Any]
    ) -> Attempt:
        """An invalid / forged submission: ResultRejected, Attempt → RETRY_WAIT, Task stays ACTIVE (S2-07)."""

        detail = jsonable(detail)
        with self._store.transaction():
            attempt = self._require_attempt(attempt_id)
            if attempt.status is AttemptStatus.RETRY_WAIT:
                return attempt
            if attempt.status is not AttemptStatus.RUNNING:
                raise CommitRejected(f"attempt {attempt_id} is {attempt.status}; nothing to reject")
            updated = next_attempt(
                attempt, AttemptStatus.RETRY_WAIT, failure={"reason": reason, **dict(detail)}
            )
            self._store.update_attempt(updated, expected_version=attempt.version)
            self._release_attempt_charge(updated)
            self._emit(
                "ResultRejected",
                attempt.mission_id,
                key=f"{attempt_id}:{turn_id}",
                task_id=attempt.task_id,
                attempt_id=attempt_id,
                payload={"reason": reason, "detail": dict(detail), "turn_id": turn_id},
            )
            return updated

    def wait_for_admission_usage(self, attempt_id: str) -> Task:
        """Persist an accounting wait after a real, already rejected SDK denial.

        Pausing reuses TaskPaused; it neither settles an UNKNOWN reservation nor
        changes the original Task contract. Only reconciled, settled usage can
        clear this system pause.
        """
        with self._store.transaction():
            attempt = self._require_attempt(attempt_id)
            failure = attempt.failure or {}
            error = failure.get("error", {})
            detail = error.get("detail", {}) if isinstance(error, Mapping) else {}
            if (
                attempt.status is not AttemptStatus.RETRY_WAIT
                or failure.get("reason") != "provider_admission_denied"
                or not isinstance(detail, Mapping)
                or detail.get("reason_code") != "usage_unresolved"
            ):
                raise CommitRejected("no rejected admission usage wait to persist")
            task = self._require_task(attempt.task_id)
            reason = "provider_admission:usage_unresolved"
            if task.paused and task.pause_reason == reason:
                return task
            if task.status in TERMINAL_TASK:
                return task
            updated = next_task(task, paused=True, pause_reason=reason)
            self._store.update_task(updated, expected_version=task.version)
            self._emit(
                "TaskPaused",
                task.mission_id,
                key=f"{attempt_id}:admission_usage",
                task_id=task.id,
                attempt_id=attempt_id,
                payload={"reason": reason, "admission": dict(detail)},
            )
            return updated

    def resume_admission_usage(self, task_id: str) -> bool:
        """Clear only our accounting pause, after all real reservations settle."""
        with self._store.transaction():
            task = self._require_task(task_id)
            if (
                not task.paused
                or task.pause_reason != "provider_admission:usage_unresolved"
                or task.status in TERMINAL_TASK
            ):
                return False
            attempts = self._store.list_attempts(task_id)
            for attempt in attempts:
                reservation = self._ledger.reservation(attempt.id)
                if self._ledger.has_unknown_usage(attempt.id) or (
                    reservation is not None and reservation["state"] != "SETTLED"
                ):
                    return False
            updated = next_task(task, paused=False, pause_reason=None)
            self._store.update_task(updated, expected_version=task.version)
            self._emit(
                "TaskResumed",
                task.mission_id,
                key=f"{task_id}:admission_usage:{task.version}",
                task_id=task.id,
                payload={"reason": "provider_usage_reconciled"},
            )
            return True

    def start_verification(self, result_id: str) -> StoredResult:
        with self._store.transaction():
            stored = self._require_result(result_id)
            attempt = self._require_attempt(stored.envelope.attempt_id)
            if stored.verification_state == "PENDING":
                self._store.set_result_verification(result_id, state="RUNNING", verdict=None)
                self._store.update_attempt(
                    next_attempt(attempt, AttemptStatus.VERIFYING), expected_version=attempt.version
                )
                for claim in self._store.list_claims(result_id):
                    self._store.upsert_claim(next_claim(claim, ClaimStatus.UNDER_REVIEW))
                self._emit(
                    "VerificationStarted",
                    attempt.mission_id,
                    key=result_id,
                    task_id=attempt.task_id,
                    attempt_id=attempt.id,
                    payload={"result_id": result_id},
                )
            return self._require_result(result_id)

    def record_verification_layer(
        self,
        result_id: str,
        *,
        layer: str,
        status: str,
        detail: Mapping[str, Any],
        requirements_revision: int | None = None,
    ) -> None:
        """Record one layer under the requirements revision it was judged against: the
        revision the result was produced under (``None``), or — for an accepted result
        reviewed again under amended requirements (TaskGraph 补全第四批) — the newer one.
        A revision under which the result is already accepted is immutable."""

        with self._store.transaction():
            stored = self._require_result(result_id)
            frozen = frozen_requirements_revision(self._store, stored)
            revision = frozen if requirements_revision is None else int(requirements_revision)

            accepted = (
                stored.verification_state == "DONE"
                and stored.verdict == "PASS"
                and revision == frozen
            ) or OperationCompletionStore(self._store).get_acceptance_scope_exact(
                stored.envelope.mission_id,
                acceptance_id_for(stored.envelope.task_id, result_id, revision),
            ) is not None
            if accepted:
                known = next(
                    (
                        row
                        for row in self._store.list_verifications(
                            result_id, requirements_revision=revision
                        )
                        if row["layer"] == layer
                    ),
                    None,
                )
                if (
                    known is not None
                    and known["status"] == status
                    and sha256_hex(known["detail"]) == sha256_hex(dict(detail))
                ):
                    return
                raise CommitRejected("accepted result verification history is immutable")
            self._store.upsert_verification(
                result_id=result_id,
                attempt_id=stored.envelope.attempt_id,
                requirements_revision=revision,
                layer=layer,
                status=status,
                detail=detail,
            )
            self._emit(
                "VerificationLayerRecorded",
                stored.envelope.mission_id,
                # 同一结果同一层再验一次、结论或明细变了，要有自己的事件（阶段 G）；一模一样的重放
                # 仍是同一条。键里带要求版本：改要求后按新版重审同一份结果，某一层的结论与明细可能
                # 和旧版一字不差，那也是新的一行，要有自己的事件（联测真机库重建核对发现的静默改动）
                key=f"{result_id}:{layer}:r{revision}:{sha256_hex([status, dict(detail)])[:16]}",
                task_id=stored.envelope.task_id,
                attempt_id=stored.envelope.attempt_id,
                payload={
                    "layer": layer,
                    "status": status,
                    "summary": detail.get("summary"),
                    "verifier_version": detail.get("verifier_version"),
                    "requirements_revision": revision,
                },
            )

    def _acceptance_materials(
        self,
        stored: StoredResult,
        task: Task,
        attempt: Attempt,
        mission: Mission,
        *,
        verifier_results: Sequence[Mapping[str, Any]],
        owner: str | None,
        connectors: Mapping[str, Any] | None,
        deployment: DeploymentPolicy | None,
    ) -> dict[str, Any] | Task:
        """Shared nonpublishing eligibility; failures retain their actual failure path.

        A valid return has not accepted the result, graded a claim, published an
        action, or completed/superseded an Attempt.
        """
        result_id = stored.envelope.id
        stale = KnowledgeIndex.load(self._store, mission.id).check(stored.envelope.used_knowledge)
        if stale:  # D4-4': the reference check is repeated inside the Commit (TOCTOU)
            return self.fail_result(
                result_id,
                failures=[
                    {
                        "layer": "rule_check",
                        "status": "FAIL",
                        "summary": "used_knowledge_stale: " + "; ".join(stale),
                        "detail": {"problems": stale, "reason": "used_knowledge_stale"},
                    }
                ],
                owner=owner,
            )
        # 2026-09-29（plans/2026-09-28-system-operations）：操作申请单由系统按已批准效果
        # 生成，步骤结果里的 actions/*.json 只是普通文件，不检查也不退回。
        return {"verifier_results": verifier_results}

    def accept_result(
        self,
        result_id: str,
        *,
        verifier_results: Sequence[Mapping[str, Any]],
        owner: str | None = None,
        connectors: Mapping[str, Any] | None = None,
        deployment: DeploymentPolicy | None = None,
    ) -> Task:

        prepared = self._prepare_assured_acceptance(result_id)
        for retry in (False, True):
            try:
                with self._store.transaction():
                    completed = self._accept_result(
                        result_id,
                        verifier_results=verifier_results,
                        owner=owner,
                        connectors=connectors,
                        deployment=deployment,
                    )
            except ResolutionCommitRejected as error:
                # A source moved between the fresh preparation and BEGIN IMMEDIATE.
                # Exactly one bounded re-preparation; never a licence by retry.
                if error.reason != "RECHECK_REQUIRED" or retry or prepared is None:
                    raise
                prepared = self._prepare_assured_acceptance(result_id)
                continue
            break
        if prepared is not None:
            self._assurance_validity.forget(
                prepared.identity.mission_id, str(prepared.record.record_id)
            )
        return completed

    def accept_carried_result(self, result_id: str, *, requirements_revision: int) -> Any:
        """TaskGraph 补全第四批：一份按旧版要求通过、被新计划沿用的结果，审阅员按新版要求重审
        通过后，写一条按新版要求的验收（同一份结果与产物、带要求版本的新验收编号）。
        不动尝试 / 任务 / 产物状态、不结清预算——那些在它当初通过时都已落定。"""

        def prepare() -> Any:
            with self._store.read_view():
                stored = self._require_result(result_id)
                mission_id = stored.envelope.mission_id
            validity = getattr(self, "_assurance_validity", None)
            if validity is None:
                return None
            try:
                return validity.prepare_accept_use_for_result(
                    mission_id, result_id, requirements_revision
                )
            except AssuranceError as error:
                raise ResolutionCommitRejected(
                    error.code, "the current use certificate could not be prepared"
                ) from error

        prepared = prepare()
        for retry in (False, True):
            try:
                with self._store.transaction():
                    receipt = self._accept_carried(result_id, int(requirements_revision))
            except ResolutionCommitRejected as error:
                if error.reason != "RECHECK_REQUIRED" or retry or prepared is None:
                    raise
                prepared = prepare()
                continue
            break
        if prepared is not None:
            self._assurance_validity.forget(
                prepared.identity.mission_id, str(prepared.record.record_id)
            )
        return receipt

    def _accept_carried(self, result_id: str, requirements_revision: int) -> Any:

        stored = self._require_result(result_id)
        if stored.verification_state != "DONE" or stored.verdict != "PASS":
            raise CommitRejected(
                "only an accepted result is reviewed again under newer requirements"
            )
        attempt = self._require_attempt(stored.envelope.attempt_id)
        mission_id, task_id = stored.envelope.mission_id, stored.envelope.task_id
        frozen = load_completion_result_inputs(
            self._store, stored, requirements_revision=requirements_revision
        )
        self._lock_assured_acceptance(mission_id, task_id, result_id, requirements_revision)
        receipt = LeafAcceptanceAssembly(self._store, self).accept(
            mission_id,
            task_id,
            result_id=result_id,
            layers=self._store.list_verifications(
                result_id, requirements_revision=requirements_revision
            ),
            artifacts=tuple(self._store.get_artifact(key) for key in stored.artifacts),
            reviewer_agent_id=f"critic:{attempt.id}",
            now_ms=int(self._store.now * 1000),
            input_manifest_hash=frozen.frozen.manifest_hash,
            port_claims=frozen.port_claims,
        )
        self._emit(
            "CarriedResultAccepted",
            mission_id,
            key=f"{result_id}:r{requirements_revision}",
            task_id=task_id,
            attempt_id=attempt.id,
            payload={
                "result_id": result_id,
                "requirements_revision": int(requirements_revision),
                "acceptance_id": str(receipt.acceptance_id),
            },
        )
        return receipt

    def _prepare_assured_acceptance(self, result_id: str) -> Any:
        """Assurance 1.1: compute the current ACCEPT use right before the acceptance UoW.

        Every layer the router recorded (an inventoried source) has already moved
        the mission epoch by now, so a candidate prepared earlier would be stale.
        Outside the write transaction, read only; the UoW then locks it first."""

        with self._store.read_view():
            stored = self._store.get_result(result_id)
            if stored is None:
                return None
            mission_id = stored.envelope.mission_id
            if stored.verification_state == "DONE" and stored.verdict == "PASS":
                return None  # replay; the committed certificate licenses it
        validity = getattr(self, "_assurance_validity", None)
        if validity is None:
            return None  # accept_review refuses USE_CERTIFICATE_REQUIRED; never a fallback
        try:
            return validity.prepare_accept_use_for_result(mission_id, result_id)
        except AssuranceError as error:
            raise ResolutionCommitRejected(
                error.code, "the current use certificate could not be prepared"
            ) from error

    def _lock_assured_acceptance(
        self,
        mission_id: str,
        task_id: str,
        result_id: str,
        requirements_revision: int | None = None,
    ) -> Any:
        """Assurance 1.1: the prepared ACCEPT use is locked before this UoW's own writes.

        The freshness/authority/root gates must see the world as it was when the
        transaction began; the result, artifact and acceptance rows this UoW then
        moves are its intended effect, not foreign changes. The certificate itself
        is committed later by ``accept_review`` in this same generation. A missing
        candidate is not licensed here; ``accept_review`` refuses it.  Returns the locked
        candidate (its official review record is what claim confirmation reads), or None."""

        validity = getattr(self, "_assurance_validity", None)
        if validity is None:
            return None

        revision = (
            frozen_requirements_revision(self._store, self._require_result(result_id))
            if requirements_revision is None
            else int(requirements_revision)
        )
        candidate = validity.candidate_for_consumer(
            mission_id, ACCEPTANCE_CONSUMER, acceptance_id_for(task_id, result_id, revision)
        )
        if candidate is None:
            return None
        try:
            validity.lock_use_locked(candidate, now_ms=int(self._store.now * 1000))
        except AssuranceError as error:
            raise ResolutionCommitRejected(
                error.code, "the current use certificate refused this acceptance"
            ) from error
        return candidate

    def _accept_result(
        self,
        result_id: str,
        *,
        verifier_results: Sequence[Mapping[str, Any]],
        owner: str | None = None,
        connectors: Mapping[str, Any] | None = None,
        deployment: DeploymentPolicy | None = None,
    ) -> Task:
        """PASS (§24 step 11) in one transaction (D3-6'): claims → VERIFIED, Attempt →
        COMPLETED, Task → COMPLETED (via VERIFYING when a sibling candidate had not
        moved it yet), the losing candidates → SUPERSEDED with their results kept as
        history, and every dependent whose dependencies are now all COMPLETED → READY."""

        with self._store.transaction():
            stored = self._require_result(result_id)
            if stored.verification_state == "DONE" and stored.verdict == "PASS":
                acceptance_id = acceptance_id_for(
                    stored.envelope.task_id,
                    result_id,
                    frozen_requirements_revision(self._store, stored),
                )
                if (
                    OperationCompletionStore(self._store).get_acceptance_scope_exact(
                        stored.envelope.mission_id, acceptance_id
                    )
                    is None
                ):
                    raise CommitRejected("verified result has no atomic scoped Acceptance")
                return self._require_task(stored.envelope.task_id)
            licensed = self._lock_assured_acceptance(
                stored.envelope.mission_id, stored.envelope.task_id, result_id
            )
            attempt = self._require_attempt(stored.envelope.attempt_id)
            self._require_lease(attempt, owner)
            task = self._require_task(stored.envelope.task_id)
            mission = self._require_mission(stored.envelope.mission_id)

            frozen_completion = load_completion_result_inputs(self._store, stored)
            materials = self._acceptance_materials(
                stored,
                task,
                attempt,
                mission,
                verifier_results=verifier_results,
                owner=owner,
                connectors=connectors,
                deployment=deployment,
            )
            if isinstance(materials, Task):
                return materials
            verifier_results = materials["verifier_results"]
            if task.status is TaskStatus.ACTIVE:
                verifying = next_task(task, TaskStatus.VERIFYING)
                self._store.update_task(verifying, expected_version=task.version)
                task = verifying
            self._store.set_result_verification(result_id, state="DONE", verdict="PASS")
            grading = self._grade_and_project(
                mission,
                task,
                attempt,
                stored,
                review=None if licensed is None else licensed.record,
                certificate=None if licensed is None else licensed.certificate,
            )
            self._store.update_attempt(
                next_attempt(attempt, AttemptStatus.COMPLETED), expected_version=attempt.version
            )
            completed = next_task(
                task,
                (
                    TaskStatus.VERIFYING
                    if frozen_completion.scope.required_effect_keys
                    else TaskStatus.COMPLETED
                ),
                accepted_result_id=result_id,
                accepted_artifacts=stored.artifacts,
            )
            self._store.update_task(completed, expected_version=task.version)
            for artifact_id in stored.artifacts:  # P3.1 fix F-ORCH-3: in this transaction
                self._store.update_artifact_verification(artifact_id, "VERIFIED")
            # step 9 (plan D9-10'): an Agent's file that tries to set policy is refused on
            # record in the same transaction; it never reaches the registry
            self.refuse_policy_files(
                stored, mission_id=mission.id, task_id=task.id, result_id=result_id
            )

            # Preparation, its accepted bytes and contribution share the result
            # transaction. No effect proposal is inferred from a Worker file.
            LeafAcceptanceAssembly(self._store, self).accept(
                mission.id,
                task.id,
                result_id=result_id,
                layers=self._store.list_verifications(
                    result_id,
                    requirements_revision=frozen_requirements_revision(self._store, stored),
                ),
                artifacts=tuple(self._store.get_artifact(key) for key in stored.artifacts),
                reviewer_agent_id=f"critic:{attempt.id}",
                now_ms=int(self._store.now * 1000),
                input_manifest_hash=frozen_completion.frozen.manifest_hash,
                port_claims=frozen_completion.port_claims,
            )
            if not self._ledger.has_unknown_usage(attempt.id):  # ORCH §12.2 (P2-12)
                self._settle_subject(attempt.id, mission.id, task_id=task.id)
            self._emit(
                "VerificationPassed",
                mission.id,
                key=result_id,
                task_id=task.id,
                attempt_id=attempt.id,
                payload={
                    "result_id": result_id,
                    "layers": [dict(item) for item in verifier_results],
                    "claims": grading,
                },
            )
            self._store.fault("after_accept_before_supersede", "attempt")
            superseded = []
            for other in self._store.list_attempts(task.id):
                if other.id != attempt.id and other.status in OPEN_ATTEMPT_STATES:
                    self._close_attempt(other, AttemptStatus.SUPERSEDED, reason="sibling_accepted")
                    superseded.append(other.id)
            self._emit(
                "TaskCompleted"
                if completed.status is TaskStatus.COMPLETED
                else "PreparationAccepted",
                mission.id,
                key=task.id,
                task_id=task.id,
                payload={
                    "result_id": result_id,
                    "artifacts": list(stored.artifacts),
                    "superseded": superseded,
                },
            )
            return completed

    def fail_result(
        self,
        result_id: str,
        *,
        failures: Sequence[Mapping[str, Any]],
        owner: str | None = None,
    ) -> Task:
        """FAIL: the result's claims are rejected. Retry is separate."""

        with self._store.transaction():
            stored = self._require_result(result_id)
            if stored.verification_state == "DONE" and stored.verdict == "FAIL":
                return self._require_task(stored.envelope.task_id)
            attempt = self._require_attempt(stored.envelope.attempt_id)
            self._require_lease(attempt, owner)
            task = self._require_task(stored.envelope.task_id)
            failure_reason = "verification_failed"
            self._store.set_result_verification(result_id, state="DONE", verdict="FAIL")
            for artifact_id in stored.artifacts:  # P3.1 fix F-ORCH-3: judged, and not accepted
                self._store.update_artifact_verification(artifact_id, "REJECTED")
            for claim in self._store.list_claims(result_id):
                self._store.upsert_claim(
                    next_claim(
                        claim,
                        ClaimStatus.REJECTED,
                        verifier_results=tuple(dict(item) for item in failures),
                    )
                )
            self._store.update_attempt(
                next_attempt(
                    attempt,
                    AttemptStatus.RETRY_WAIT,
                    failure={
                        "reason": failure_reason,
                        "failures": [dict(item) for item in failures],
                    },
                ),
                expected_version=attempt.version,
            )
            others_submitted = any(
                other.id != attempt.id and other.status in SUBMITTED_STATES
                for other in self._store.list_attempts(task.id)
            )
            active = task
            if task.status is TaskStatus.VERIFYING and not others_submitted:
                active = next_task(task, TaskStatus.ACTIVE)
                self._store.update_task(active, expected_version=task.version)
            self._settle_subject(attempt.id, attempt.mission_id, task_id=task.id)
            if self._release_attempt_charge(self._require_attempt(attempt.id)):
                active = self._require_task(task.id)
            self._emit(
                "VerificationFailed",
                attempt.mission_id,
                key=result_id,
                task_id=task.id,
                attempt_id=attempt.id,
                payload={"result_id": result_id, "failures": [dict(item) for item in failures]},
            )
            return active
