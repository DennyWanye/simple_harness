# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (long event / receipt literals)

"""Grading results into knowledge: claims, confirmations, disputes and supersession."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ...assurance.codec import AssuranceError, decode
from ...assurance.refs import AssuranceRef, Pin
from ...contracts import Attempt, AttemptStatus, Claim, ClaimStatus, Mission, Task, TaskStatus
from ...contracts.models import jsonable
from ...contracts.semantic_base import content_hash_of
from ...governance.domains import DomainProfileV1
from ...memory.claims import grade_claim
from ...memory.code_observations import scoped_test_observations
from ...memory.knowledge_standing import CURRENT as KNOWLEDGE_CURRENT
from ...memory.knowledge_standing import knowledge_standing
from ...memory.verified_knowledge import KnowledgeRecord, modified, parse_knowledge_ref
from ...storage.assurance_reads import AssuranceReader
from ...storage.assurance_store import AssuranceStore
from ...storage.store import DispatchIntent, StoredResult
from ...verification.conflicts import Contradiction, find_contradiction
from ..assurance_validity import acceptance_id_for
from ..completion_inputs import frozen_requirements_revision
from ..review_adjudication import accepted_or_adjudicated, adjudication_of
from ..state_machine import next_attempt, next_claim, next_task
from .core import CommitServiceCore
from .support import SUBMITTED_STATES, CommitRejected


class GradingCommitsMixin(CommitServiceCore):
    """Grading results into knowledge: claims, confirmations, disputes and supersession."""

    __slots__ = ()

    if TYPE_CHECKING:

        def _release_attempt_charge(self, attempt: Attempt) -> bool: ...
        def _settle_intent(self, intent: DispatchIntent, state: str) -> DispatchIntent: ...
        def domain_for(self, mission_id: str) -> DomainProfileV1: ...

        def _cancel_review_request(self, result_id: str, *, reason: str) -> None: ...

    def _close_attempt(self, attempt: Attempt, target: AttemptStatus, *, reason: str) -> Attempt:
        """Attempt → SUPERSEDED / CANCELLED with its result (if any) rejected as history,
        its intent closed and its reservation settled unless an UNKNOWN charge holds it."""

        updated = next_attempt(attempt, target, failure={"reason": reason})
        self._store.update_attempt(updated, expected_version=attempt.version)
        stored = self._store.find_result_for_attempt(attempt.id)
        if stored is not None and stored.verification_state in {"PENDING", "RUNNING", "SUSPENDED"}:
            self._store.set_result_verification(
                stored.envelope.id, state="REJECTED", verdict="superseded"
            )
            if stored.verification_state == "SUSPENDED":  # D7-8': the review goes with it
                self._cancel_review_request(stored.envelope.id, reason=reason)
            self._reject_open_claims(stored.envelope.id)
        intent = self._store.get_intent_for_subject(attempt.id)
        if intent is not None and intent.state in {"PENDING", "CLAIMED", "AGENT_CREATED"}:
            self._settle_intent(intent, "FAILED")
        # A SUBMITTED intent stays open: its SDK turn is still running and the loop
        # collects it later (usage imported, late result kept as history, D3-6'); the
        # late-accounting scanner settles the reservation once the turn's cost is known.
        self._emit(
            "AttemptSuperseded" if target is AttemptStatus.SUPERSEDED else "AttemptCancelled",
            attempt.mission_id,
            key=attempt.id,
            task_id=attempt.task_id,
            attempt_id=attempt.id,
            payload={"reason": reason, "from": str(attempt.status)},
        )
        return updated

    def _reject_open_claims(self, result_id: str) -> None:
        for claim in self._store.list_claims(result_id):
            if claim.status in {ClaimStatus.PROPOSED, ClaimStatus.UNDER_REVIEW}:
                target_claim = (
                    ClaimStatus.UNDER_REVIEW
                    if claim.status is ClaimStatus.PROPOSED
                    else claim.status
                )
                moved = claim if target_claim is claim.status else next_claim(claim, target_claim)
                self._store.upsert_claim(next_claim(moved, ClaimStatus.REJECTED))

    def set_aside_result(self, result_id: str, *, detail: Mapping[str, Any]) -> Attempt | None:
        """A stored result whose completion scope went stale before it was verified (the user
        amended the requirements, or a new plan revision was adopted): archived as superseded —
        not the worker's fault, no repair request, no review package — and its Attempt goes to
        RETRY_WAIT, exactly as a result collected while the requirements are amended is (阶段 E).
        Whether the step is done again is the planner's call.  ``None`` when the result or
        Attempt has already moved on."""

        detail = {**jsonable(detail), "result_id": result_id}
        with self._store.transaction():
            stored = self._store.get_result(result_id)
            if stored is None or stored.verification_state not in {"PENDING", "RUNNING"}:
                return None
            attempt = self._require_attempt(stored.envelope.attempt_id)
            if attempt.status not in {
                AttemptStatus.RUNNING,
                AttemptStatus.SUBMITTED,
                AttemptStatus.VERIFYING,
            }:
                return None
            task = self._require_task(stored.envelope.task_id)
            self._store.set_result_verification(result_id, state="REJECTED", verdict="superseded")
            self._reject_open_claims(result_id)
            self._store.update_attempt(
                next_attempt(
                    attempt, AttemptStatus.RETRY_WAIT, failure={"reason": "superseded", **detail}
                ),
                expected_version=attempt.version,
            )
            others_submitted = any(
                other.id != attempt.id and other.status in SUBMITTED_STATES
                for other in self._store.list_attempts(task.id)
            )
            if task.status is TaskStatus.VERIFYING and not others_submitted:
                self._store.update_task(
                    next_task(task, TaskStatus.ACTIVE), expected_version=task.version
                )
            self._release_attempt_charge(self._require_attempt(attempt.id))
            self._emit(
                "ResultRejected",
                attempt.mission_id,
                key=f"{attempt.id}:{stored.turn_id}",
                task_id=task.id,
                attempt_id=attempt.id,
                payload={"reason": "superseded", "detail": detail, "turn_id": stored.turn_id},
            )
            return self._require_attempt(attempt.id)

    # ---------------------------------------------------------- knowledge (step 4)
    def _grade_and_project(
        self,
        mission: Mission,
        task: Task,
        attempt: Attempt,
        stored: StoredResult,
        review: Any = None,
        certificate: Any = None,
    ) -> list[dict[str, Any]]:
        """D4-2/D4-3/D4-4/D4-5 inside the accept transaction: grade every claim of the
        accepted result from the verification that ran, project the VERIFIED ones into
        the Verified Knowledge store with their provenance, record the reuse chain of
        ``used_knowledge`` and apply an explicit, legal supersession.

        阶段 C：一条结论成为"已验证"有两个来源——系统自己跑过的测试观察，或独立审阅员在
        正式审阅记录（``review``）里逐条确认。每条入库的知识自带它的依据（``support``：
        来源验收、确认时引用的证据产物、本步用过的知识），之后是否仍然当前只读它。

        第 2 批 K02：审阅确认入库的知识另带三个登记项（有效期、允许用途、保障等级），从这次验收
        用的使用证书（``certificate``）与任务的保证通道取；取不到就是 None。"""

        envelope = stored.envelope
        confirmed = self._review_confirmations(mission, envelope.id, review)
        registration = self._knowledge_registration(mission, certificate) if confirmed else {}
        used = {
            knowledge_id: version
            for knowledge_id, version in map(parse_knowledge_ref, envelope.used_knowledge)
        }
        domain = self.domain_for(mission.id)
        untrusted = [str(p) for p in (mission.final_report or {}).get("untrusted_sources", [])]
        artifact_paths = [
            artifact.path
            for artifact_id in stored.artifacts
            for artifact in [self._store.get_artifact(artifact_id)]
            if artifact is not None
        ]
        # Only runtime-recorded verification is evidence; caller-supplied acceptance
        # summaries are not execution receipts.

        layers = [
            dict(item)
            for item in self._store.list_verifications(
                envelope.id, requirements_revision=frozen_requirements_revision(self._store, stored)
            )
        ]

        artifacts = [self._store.get_artifact(item) for item in stored.artifacts]
        observations = scoped_test_observations(
            envelope, [a for a in artifacts if a is not None], layers, self._store.now, task
        )
        grading_layers = [
            {
                "layer": "code_test",
                "detail": {
                    "runs": [
                        {"target": record.verifier["target"], "passed": True}
                        for _, record in observations
                    ]
                },
            }
        ]
        resolved_refs: set[str] = set()
        for proposal in envelope.claims:
            for ref in proposal.evidence or envelope.evidence:
                if ref.startswith("knowledge:"):
                    record = self._store.get_knowledge(ref.removeprefix("knowledge:"))
                    if (
                        record is not None
                        and used.get(record.id) == record.version
                        and record.mission_id == mission.id
                        and knowledge_standing(self._store, record) == KNOWLEDGE_CURRENT
                        and self._accepted_result(record.source_result)
                    ):
                        resolved_refs.add(ref)
                elif ref.startswith("tool-run:"):
                    run = self._store.get_tool_call(ref.removeprefix("tool-run:"))
                    if (
                        run is not None
                        and run["mission_id"] == mission.id
                        and run["subject_id"] == attempt.id
                        and run["outcome"] == "succeeded"
                    ):
                        resolved_refs.add(ref)
        report: list[dict[str, Any]] = []
        existing_claims = [
            other
            for other in self._store.list_mission_claims(mission.id)
            if other.result_id != envelope.id and self._accepted_result(other.result_id)
        ]
        for claim in self._store.list_claims(envelope.id):
            grade = grade_claim(
                claim.id,
                claim.evidence,
                verifier_results=grading_layers,
                artifact_paths=artifact_paths,
                untrusted_prefixes=untrusted,
                domain=domain,
                resolved_refs=frozenset(resolved_refs),
            )
            metadata = {
                **dict(claim.confidence_metadata),
                "grade": str(grade.status).lower()
                if grade.status is not ClaimStatus.UNDER_REVIEW
                else "unsupported",
                "basis": dict(grade.basis),
                "evidence_trust": list(grade.evidence_trust),
            }
            supersedes: str | None = None
            proposed = claim.confidence_metadata.get("proposed_supersedes")
            if proposed is not None:
                target = self._store.get_knowledge(str(proposed))
                if grade.status is not ClaimStatus.VERIFIED:
                    metadata["supersedes_rejected"] = (
                        f"only a VERIFIED claim may supersede knowledge (graded {grade.status})"
                    )
                elif (
                    target is None or target.mission_id != mission.id or target.status != "VERIFIED"
                ):
                    metadata["supersedes_rejected"] = (
                        f"{proposed!r} is not VERIFIED knowledge of this Mission"
                    )
                elif claim.key is None or claim.key != target.key:
                    metadata["supersedes_rejected"] = "supersession requires the same key"
                else:
                    supersedes = target.id
            results = (
                *claim.verifier_results,
                *layers,
                dict(grade.basis, layer=grade.basis.get("layer", "grading")),
            )
            target_status = grade.status
            verifier = dict(grade.basis)
            confirmation = confirmed.get(claim.id)
            if (
                confirmation is not None
                and target_status is not ClaimStatus.VERIFIED
                and confirmation["content_sha256"]
                == hashlib.sha256(claim.content.encode("utf-8")).hexdigest()
            ):
                # The independent reviewer confirmed this claim against evidence other
                # than the reviewed result itself: that is what makes it knowledge.
                target_status = ClaimStatus.VERIFIED
                verifier = {
                    key: value for key, value in confirmation.items() if key != "content_sha256"
                }
                metadata["grade"] = "verified"
                metadata["basis"] = {
                    "layer": "review",
                    "reason": "confirmed by the independent review",
                }
            # D4-6': conflict precedes grading — a contested claim is capped at DISPUTED
            # and never projected, whatever its own evidence says
            contradiction = find_contradiction(claim, existing_claims)
            if contradiction is not None:
                target_status = ClaimStatus.DISPUTED
                metadata["grade_before_dispute"] = metadata["grade"]
                metadata["grade"] = "disputed"
                supersedes = None
            updated = next_claim(
                claim,
                target_status if target_status is not claim.status else None,
                verifier_results=tuple(results),
                confidence_metadata=metadata,
                supersedes=supersedes,
            )
            self._store.upsert_claim(updated)
            report.append({"claim_id": claim.id, "status": str(updated.status), "key": claim.key})
            if contradiction is not None:
                self._dispute(mission, updated, contradiction)
                continue
            if updated.status is ClaimStatus.VERIFIED:
                record = KnowledgeRecord(
                    id=updated.id,
                    mission_id=mission.id,
                    claim_id=updated.id,
                    content=updated.content,
                    type=updated.type,
                    status="VERIFIED",
                    version=1,
                    key=updated.key,
                    stance=updated.stance,
                    proposed_by=updated.proposed_by,
                    source_task=task.id,
                    source_attempt=attempt.id,
                    source_result=envelope.id,
                    evidence=updated.evidence,
                    verifier=verifier,
                    created_at=self._store.now,
                    supersedes=supersedes,
                    evidence_trust=grade.evidence_trust,
                    support=self._knowledge_support(
                        mission, task, stored, updated.id, updated.evidence, confirmation
                    ),
                    **(registration if confirmation is not None else {}),
                )
                self._store.upsert_knowledge(record)
                self._emit(
                    "KnowledgeCommitted",
                    mission.id,
                    key=record.id,
                    task_id=task.id,
                    attempt_id=attempt.id,
                    payload={
                        "knowledge_id": record.id,
                        "key": record.key,
                        "stance": record.stance,
                        "verifier": dict(record.verifier),
                        "supersedes": supersedes,
                        "basis": record.verifier.get("basis", "test_observation"),
                        "support": dict(record.support),
                    },
                )
                if supersedes is not None:
                    self._supersede_knowledge(supersedes, by=record)
        for observation, record in observations:
            record = replace(
                record,
                verifier={**dict(record.verifier), "basis": "test_observation"},
                support=self._knowledge_support(
                    mission, task, stored, record.id, record.evidence, None
                ),
            )
            self._store.upsert_claim(observation)
            self._store.upsert_knowledge(record)
            report.append({"claim_id": observation.id, "status": "VERIFIED", "key": None})
            self._emit(
                "KnowledgeCommitted",
                mission.id,
                key=record.id,
                task_id=task.id,
                attempt_id=attempt.id,
                payload={
                    "knowledge_id": record.id,
                    "verifier": dict(record.verifier),
                    "system_observation": True,
                    "basis": "test_observation",
                    "support": dict(record.support),
                },
            )
        for knowledge_id in used:
            source = self._store.get_knowledge(knowledge_id)
            if source is None or source.mission_id != mission.id or source.status != "VERIFIED":
                continue
            if task.id not in source.used_by:
                self._store.upsert_knowledge(replace(source, used_by=(*source.used_by, task.id)))
            self._emit(
                "KnowledgeUsed",
                mission.id,
                key=f"{envelope.id}:{source.id}",
                task_id=task.id,
                attempt_id=attempt.id,
                payload={
                    "knowledge_id": source.id,
                    "version": source.version,
                    "result_id": envelope.id,
                    "source_task": source.source_task,
                    "source_attempt": source.source_attempt,
                },
            )
        return report

    def _review_confirmations(
        self, mission: Mission, result_id: str, review: Any
    ) -> dict[str, dict[str, Any]]:
        """The claims the official content review confirmed, by claim id (阶段 C).

        Read from the record's authenticated manifest.  A confirmation counts only when
        the review as a whole stands (ACCEPT, or INCONCLUSIVE and the person passed it)
        and it cites at least one piece of evidence that is not the reviewed result
        itself — a result cannot be the proof of its own claims."""
        validity = getattr(self, "_assurance_validity", None)
        if review is None or validity is None or not accepted_or_adjudicated(self._store, review):
            return {}

        reader = AssuranceReader(self._store, tenant_id=validity.tenant_id, mission_id=mission.id)
        manifest = decode(
            reader.read_exact_metadata(
                AssuranceRef(
                    "input_manifest",
                    Pin(review.evidence_manifest_hash, 0, review.evidence_manifest_hash),
                )
            ).body_json
        )
        ruling = adjudication_of(self._store, str(review.record_id))
        found: dict[str, dict[str, Any]] = {}
        for row in manifest.get("claims", ()):
            independent = [
                ref
                for ref in row["evidence_refs"]
                if not (ref["kind"] == "result" and ref["pin"]["id"] == result_id)
            ]
            if not row["confirmed"] or not independent:
                continue
            found[row["claim_id"]] = {
                "basis": "review_confirmed",
                "record_id": str(review.record_id),
                "review_key": manifest["review_key"],
                "reviewer_agent_id": str(review.reviewer_agent_id),
                "manifest_hash": review.evidence_manifest_hash,
                "reason": row["reason"],
                "evidence_refs": row["evidence_refs"],
                "adjudication": ruling,
                "content_sha256": row["content_sha256"],
            }
        return found

    def _knowledge_registration(self, mission: Mission, certificate: Any) -> dict[str, Any]:
        """第 2 批 K02：审阅确认入库的知识的三个登记项，只从已有记录取——有效期取这次验收用的
        使用证书的签发/失效时刻与任务纪元；保障等级取任务的保证通道；允许用途没有任何记录写它
        （审阅回复与证书都没有这一项），一律 None。取不到的就是 None，不猜。"""

        out: dict[str, Any] = {
            "validity_interval": None,
            "permitted_uses": None,
            "assurance_level": None,
        }
        if certificate is not None:
            out["validity_interval"] = {
                "valid_from_ms": int(certificate.issued_at_ms),
                "valid_until_ms": None
                if certificate.not_after_ms is None
                else int(certificate.not_after_ms),
                "mission_epoch": int(certificate.mission_epoch),
            }
        try:
            out["assurance_level"] = AssuranceStore(self._store).lane(mission.id)
        except AssuranceError:
            out["assurance_level"] = None
        return out

    def _knowledge_support(
        self,
        mission: Mission,
        task: Task,
        stored: StoredResult,
        knowledge_id: str,
        evidence: Sequence[str],
        confirmation: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """What a piece of knowledge rests on, computed here and stored on its record: the
        acceptance of its source step, the evidence artifacts (the ones the reviewer cited
        or the claim names; all of the result's when neither says), and the knowledge the
        step used.  Nothing is written by this — the record carries it."""

        envelope = stored.envelope
        cited = {
            ref["pin"]["id"]
            for ref in (confirmation or {}).get("evidence_refs", ())
            if ref["kind"] == "artifact"
        }
        artifacts = [a for a in map(self._store.get_artifact, stored.artifacts) if a is not None]
        named = [a for a in artifacts if a.id in cited or a.path in evidence]
        knowledge = []
        for source_id, _ in map(parse_knowledge_ref, envelope.used_knowledge):
            source = self._store.get_knowledge(source_id)
            if source is None or source.mission_id != mission.id or source.id == knowledge_id:
                continue
            knowledge.append(
                {
                    "id": source.id,
                    "version": int(source.version),
                    "content_hash": content_hash_of(source.content),
                }
            )
        return {
            "acceptance_id": acceptance_id_for(
                task.id, envelope.id, frozen_requirements_revision(self._store, stored)
            ),
            "artifacts": [
                {"id": a.id, "version": int(a.version), "content_hash": a.content_hash}
                for a in (named or artifacts)
            ],
            "knowledge": knowledge,
        }

    def _accepted_result(self, result_id: str) -> bool:
        stored = self._store.get_result(result_id)
        return (
            stored is not None and stored.verification_state == "DONE" and stored.verdict == "PASS"
        )

    def _dispute(self, mission: Mission, claim: Claim, contradiction: Contradiction) -> None:
        """§14.4 保留双方、都标"有争议"、互记对方；后来的这条不进知识库 (D4-6')."""

        # re-read: an earlier claim of the same result may already have marked it
        other = self._store.get_claim(contradiction.other.id) or contradiction.other
        if other.status is ClaimStatus.VERIFIED:
            # VERIFIED has no edge to DISPUTED (§25.3): the knowledge stays formal and is
            # marked as contested on both the claim and its projection
            if claim.id not in other.disputed_by:
                self._store.upsert_claim(
                    next_claim(other, disputed_by=(*other.disputed_by, claim.id))
                )
            record = self._store.get_knowledge(other.id)
            if record is not None and claim.id not in record.disputed_by:
                # K07：谁改了它，与这次改动同一次写入
                self._store.upsert_knowledge(
                    modified(
                        replace(record, disputed_by=(*record.disputed_by, claim.id)),
                        change="DISPUTED",
                        by=claim.id,
                        task_id=claim.source_task,
                        attempt_id=claim.source_attempt,
                        proposed_by=claim.proposed_by,
                        at=self._store.now,
                    )
                )
        elif other.status is not ClaimStatus.DISPUTED:
            self._store.upsert_claim(
                next_claim(other, ClaimStatus.DISPUTED, disputed_by=(*other.disputed_by, claim.id))
            )
        elif claim.id not in other.disputed_by:
            self._store.upsert_claim(next_claim(other, disputed_by=(*other.disputed_by, claim.id)))
        if other.id not in claim.disputed_by:
            # both sides name each other: "who disagrees" is read from the claim itself
            self._store.upsert_claim(next_claim(claim, disputed_by=(*claim.disputed_by, other.id)))
        self._emit(
            "ClaimDisputed",
            mission.id,
            key=f"{claim.id}:{other.id}",
            task_id=claim.source_task,
            attempt_id=claim.source_attempt,
            payload={
                "claim_id": claim.id,
                "contradicts": other.id,
                "key": contradiction.key,
                "reason": contradiction.reason,
                "other_status": str(other.status),
            },
        )

    def _require_claim(self, claim_id: str) -> Claim:
        claim = self._store.get_claim(claim_id)
        if claim is None:
            raise CommitRejected(f"unknown claim {claim_id}")
        return claim

    def record_retrieval_unavailable(self, task_id: str, *, reason: str, policy: str) -> int:
        """S4-07 / D4-11': one durable event per failed retrieval; returns the count so
        far for this Task (it survives restarts — it is derived from the events)."""

        with self._store.transaction():
            task = self._require_task(task_id)
            previous = sum(
                1
                for event in self._store.list_events(task.mission_id)
                if event.type == "RetrievalUnavailable" and event.task_id == task_id
            )
            count = previous + 1
            self._emit(
                "RetrievalUnavailable",
                task.mission_id,
                key=f"{task_id}:retrieval:{count}",
                task_id=task_id,
                payload={"reason": reason, "policy": policy, "count": count},
            )
            return count

    def _supersede_knowledge(self, knowledge_id: str, *, by: KnowledgeRecord) -> None:
        """VERIFIED → SUPERSEDED (§25.3, the one legal edge out of VERIFIED) on both the
        claim and its knowledge projection, pointing at the newer version ``by`` (whose
        Task / Attempt / proposer are noted as the modifier, K07)."""

        record = self._store.get_knowledge(knowledge_id)
        if record is None or record.status != "VERIFIED":
            return
        self._store.upsert_knowledge(
            modified(
                replace(
                    record, status="SUPERSEDED", superseded_by=by.id
                ),  # P2-13: version = identity
                change="SUPERSEDED",
                by=by.id,
                task_id=by.source_task,
                attempt_id=by.source_attempt,
                proposed_by=by.proposed_by,
                at=self._store.now,
            )
        )
        claim = self._store.get_claim(record.claim_id)
        if claim is not None and claim.status is ClaimStatus.VERIFIED:
            self._store.upsert_claim(next_claim(claim, ClaimStatus.SUPERSEDED, superseded_by=by.id))
        self._emit(
            "KnowledgeSuperseded",
            record.mission_id,
            key=f"{knowledge_id}:{by.id}",
            task_id=record.source_task,
            payload={"knowledge_id": knowledge_id, "superseded_by": by.id, "key": record.key},
        )
