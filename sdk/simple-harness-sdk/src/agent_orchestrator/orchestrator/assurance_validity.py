# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Current use certificates for the Assurance 1.1 profile (BW08 / BW10).

``prepare_accept_use`` reads one consistent Store snapshot, re-prepares every
check the official review consumed, re-decides the review under the *current*
typed check results, selects fresh anchors, computes the bounded supported and
clean closures and assembles a candidate certificate outside any write lock.
``commit_use_locked`` repeats the bounded identity/epoch/authority/expiry
checks inside the original consumer's write transaction, consumes the prepared
checks and records the certificate together with the consumer's own effect.

Neither step trusts a cached VERIFIED state, a legacy ValidityWitness, a Host
boolean or a model's prose. A missing candidate, an unavailable source or a
changed epoch refuses the use; nothing here fabricates a USABLE decision.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..assurance.certificates import UseCertificate, UseIdentity, check_certificate_binding
from ..assurance.check_bindings import CheckBinding
from ..assurance.checks import (
    Assessment,
    CheckResult,
    CriterionPolicy,
    Finding,
    Formula,
    ReviewReply,
    decide_review,
    grade,
)
from ..assurance.codec import AssuranceError, canonical, decode, fingerprint, integer, text
from ..assurance.evidence import ReadItem
from ..assurance.refs import AssuranceRef, Pin
from ..assurance.root_gate import CurrentReadPermission
from ..contracts import Artifact
from ..contracts.evidence_state import TruthValue
from ..contracts.resolution import ReviewRecord
from ..contracts.semantic_base import content_hash_of
from ..knowledge.assurance_sources import (
    CheckAnchorInput,
    ReviewAnchorInput,
    SupportEvaluation,
    evaluate_acceptance_support,
    support_fingerprint,
)
from ..storage.assurance_reads import (
    AssuranceReader,
    CompleteRead,
    EpochSnapshot,
    ExactMetadata,
    read_complete_evidence_snapshot,
    read_epochs_locked,
    require_epochs_locked,
)
from ..storage.assurance_store import AssuranceStore
from ..storage.htn_store import HtnStore
from ..storage.assurance_work import atomic
from .assurance_check_use import (
    CurrentAuthority,
    PreparedCheckUse,
    _merge_reads,
    _permission,
    _require_same_permission,
    prepare_local_check_use,
)
from .assurance_review_import import (
    read_imported_review_locked,
    read_official_review_binding_locked,
)

USE_CERTIFIED_KIND = "AssuranceUseCertified"
ACCEPTANCE_CONSUMER = "ACCEPTANCE"
ROOT_RESOLUTION_CONSUMER = "ROOT_RESOLUTION"
COMPOUND_RESOLUTION_CONSUMER = "COMPOUND_RESOLUTION"
MAXIMUM_CANDIDATES = 256
#: Official record purposes a use certificate can be prepared from, with the
#: kind of the review subject's target (approved contract SUBJECT_TARGET_KINDS).
USE_TARGET_KINDS = {
    "TASK_CONTENT": "result",
    "MISSION_FINAL": "task",
    "OPERATION_OUTCOME": "operation",
    # 2026-10-01（第 3 项）：中间目标的组合审阅，目标是该复合任务
    "COMPOSITION": "task",
}


def acceptance_id_for(task_id: str, result_id: str, requirements_revision: int) -> str:
    """The leaf acceptance identity of one result under one requirements revision; kept in
    one place for every writer and reader.  A result kept across an amendment and reviewed
    again under the new revision gets its own acceptance (TaskGraph 补全第四批)."""
    return "acc-" + content_hash_of({"task": str(task_id), "result": str(result_id),
                                     "requirements_revision": int(requirements_revision)})[:32]


def result_acceptance_ids(store: Any, mission_id: str, task_id: str, result_id: str) -> tuple[str, ...]:
    """Every leaf acceptance of one result, oldest requirements revision first."""
    from ..storage.htn_store import HtnStore

    found = sorted(
        (int(item.requirements_revision), str(item.acceptance_id))
        for item in HtnStore(store).list_acceptances(mission_id)
        if str(item.task_id) == str(task_id)
        and str(item.acceptance_id) == acceptance_id_for(task_id, result_id, int(item.requirements_revision)))
    return tuple(acceptance for _, acceptance in found)


@dataclass(frozen=True, slots=True)
class CandidateUseCertificate:
    """A computed, not yet committed, use certificate and everything it read."""

    certificate_id: str
    certificate: UseCertificate
    identity: UseIdentity
    record: ReviewRecord
    subject_hash: str
    epochs: EpochSnapshot
    metadata: tuple[ExactMetadata, ...]
    permissions: tuple[tuple[AssuranceRef, CurrentReadPermission], ...]
    check_uses: tuple[PreparedCheckUse, ...]
    check_grades: tuple[tuple[AssuranceRef, str], ...]
    complete: tuple[CompleteRead, ...]
    evaluation: SupportEvaluation
    acceptable: bool
    #: Per-criterion effective grades and check-gate reasons re-decided under
    #: the current typed check results (not the imported projection).
    grades: tuple[tuple[str, str], ...]
    gates: tuple[tuple[str, str], ...]
    prepared_at_ms: int
    #: The person's recorded ruling this certificate rests on (INCONCLUSIVE review,
    #: human pass), or None.
    adjudication_ref: str | None = None

    @property
    def usable(self) -> bool:
        return self.certificate.decision == "USABLE"

    @property
    def effective_grades(self) -> dict[str, str]:
        return dict(self.grades)

    @property
    def gate_reasons(self) -> dict[str, str]:
        return dict(self.gates)


class AssuranceValidity:
    """Deployment-owned current use evaluator bound to one CommitService.

    Registered like the local-check adapter: a trusted deployment collaborator,
    never a Host- or model-selectable object.  The acceptance support is decided
    by the official review and the current check results alone.
    """

    def __init__(
        self,
        commit: Any,
        *,
        tenant_id: str,
        principal_id: str,
        cas: Any,
        check_adapter: Any,
        authority: CurrentAuthority,
        maximum_blob_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        self.commit = commit
        self.store = commit.store
        self.tenant_id = text(tenant_id)
        self.principal_id = text(principal_id)
        self.cas = cas
        self.check_adapter = check_adapter
        self.authority = authority
        self.maximum_blob_bytes = integer(maximum_blob_bytes, minimum=1)
        self._candidates: dict[tuple[str, str], CandidateUseCertificate] = {}
        # certificate_id -> Store transaction generation in which it was locked
        # before the consumer's own writes (see lock_use_locked).
        self._locked: dict[str, int] = {}
        existing = getattr(commit, "_assurance_validity", None)
        if existing is not None and existing is not self:
            raise AssuranceError("ASSURANCE_VALIDITY_ALREADY_BOUND")
        commit._assurance_validity = self

    # ------------------------------------------------------------- candidates
    def candidate_for(self, mission_id: str, record_id: str) -> CandidateUseCertificate | None:
        return self._candidates.get((text(mission_id), text(record_id)))

    def _remember(self, candidate: CandidateUseCertificate) -> None:
        key = (candidate.identity.mission_id, str(candidate.record.record_id))
        self._candidates.pop(key, None)
        while len(self._candidates) >= MAXIMUM_CANDIDATES:
            self._candidates.pop(next(iter(self._candidates)))
        self._candidates[key] = candidate

    def forget(self, mission_id: str, record_id: str) -> None:
        candidate = self._candidates.pop((text(mission_id), text(record_id)), None)
        if candidate is not None:
            self._locked.pop(candidate.certificate_id, None)

    def candidate_for_consumer(
        self, mission_id: str, consumer_kind: str, consumer_id: str
    ) -> CandidateUseCertificate | None:
        for candidate in self._candidates.values():
            identity = candidate.identity
            if (
                identity.mission_id == mission_id
                and identity.consumer_kind == consumer_kind
                and identity.consumer_id == consumer_id
            ):
                return candidate
        return None

    # ---------------------------------------------------------------- prepare
    def official_record_for_result(
        self, mission_id: str, result_id: str, requirements_revision: int | None = None
    ) -> ReviewRecord:
        """The exact official TASK_CONTENT record bound to this Result under one requirements
        revision (``None``: the one it was produced under); no latest fallback.  A result
        kept across an amendment has one record per revision (TaskGraph 补全第四批)."""
        from .completion_inputs import frozen_requirements_revision

        store = self.store
        with store.read_view():
            result = store.get_result(result_id)
            if result is None or result.envelope.mission_id != mission_id:
                raise AssuranceError("REVIEW_RESULT_SOURCE_MISSING")
            revision = (frozen_requirements_revision(store, result) if requirements_revision is None
                        else int(requirements_revision))
            rows = store.connection.execute(
                "SELECT package_id FROM assurance_review_bindings WHERE mission_id=? "
                "AND subject_hash=? AND requirements_revision=? "
                "AND json_extract(binding_json,'$.subject.purpose')='TASK_CONTENT'",
                (mission_id, fingerprint(result.envelope.to_json()), revision),
            ).fetchall()
            if len(rows) > 1:
                raise AssuranceError("REVIEW_RESULT_SOURCE_AMBIGUOUS")
            if not rows:
                raise AssuranceError("REVIEW_NOT_OFFICIAL")
            record = HtnStore(store).official_review_record(rows[0][0])
            if record is None:
                raise AssuranceError("REVIEW_NOT_OFFICIAL")
            read_official_review_binding_locked(self.commit, self.tenant_id, record)
        return record

    def prepare_accept_use_for_result(
        self, mission_id: str, result_id: str, requirements_revision: int | None = None
    ) -> CandidateUseCertificate:
        """Fresh preparation at the head of the acceptance path (outside its UoW)."""
        return self.prepare_accept_use(
            self.official_record_for_result(mission_id, result_id, requirements_revision))

    # ------------------------------------------------------------ diagnostics
    def diagnose_accept_use_for_result(self, mission_id: str, result_id: str) -> CandidateUseCertificate:
        """纯算（第 1 批 A04）：Host 诊断 ``use_check`` 用——同一套计算，但不记入共用候选缓存、不动
        锁表，所以一次诊断顶不掉、也清不掉正在进行的真实验收候选。"""
        return self.prepare_accept_use(
            self.official_record_for_result(mission_id, result_id), remember=False)

    def diagnose_root_use(self, record: ReviewRecord, *, resolution_id: str) -> CandidateUseCertificate:
        """纯算（第 1 批 A04）：根结论诊断用，同上。"""
        return self.prepare_root_use(record, resolution_id=resolution_id, remember=False)

    def prepare_accept_use(self, record: ReviewRecord, *, remember: bool = True) -> CandidateUseCertificate:
        """Bounded read/compute outside the write lock; nothing is written.

        The leaf ACCEPT use of an official ``TASK_CONTENT`` record: consumer is the
        original acceptance identity of the owner task and the reviewed Result.
        """

        def consumer(purpose: str, owner_task: str, target: AssuranceRef) -> tuple[str, str]:
            if purpose != "TASK_CONTENT":
                raise AssuranceError("USE_PURPOSE_UNSUPPORTED", purpose)
            return ACCEPTANCE_CONSUMER, acceptance_id_for(
                owner_task, target.pin.id, int(record.binding.requirements_revision))

        return self._prepare_use(record, consumer, remember=remember)

    def prepare_outcome_use(
        self, record: ReviewRecord, *, acceptance_id: str
    ) -> CandidateUseCertificate:
        """The ACCEPT use of an official ``OPERATION_OUTCOME`` record for its outcome
        acceptance (outside the write lock; nothing is written).

        NEXT-TG-1.0, 2026-09-27: the outcome acceptor still named the legacy
        self-issued witness, so every assured outcome was refused
        ``USE_CERTIFICATE_REQUIRED`` after an ACCEPTed review.
        """
        consumer_id = text(acceptance_id)

        def consumer(purpose: str, owner_task: str, target: AssuranceRef) -> tuple[str, str]:
            if purpose != "OPERATION_OUTCOME":
                raise AssuranceError("USE_PURPOSE_UNSUPPORTED", purpose)
            return ACCEPTANCE_CONSUMER, consumer_id

        return self._prepare_use(record, consumer)

    def prepare_root_use(
        self, record: ReviewRecord, *, resolution_id: str, remember: bool = True
    ) -> CandidateUseCertificate:
        """The ACCEPT use of an official ``MISSION_FINAL`` record for one root resolution.

        Handoff item 7: the assured root ``GoalResolution`` is licensed by the
        current re-decision of the bound review manifest, never by the legacy
        self-issued witness or by the public ``CriterionOutcome`` projection (which
        shows a SEMANTIC PASS as UNKNOWN). The consumer is the resolution the
        trigger proposes; ``commit_goal_resolution`` checks that identity again.
        """
        resolution = text(resolution_id)

        def consumer(purpose: str, owner_task: str, target: AssuranceRef) -> tuple[str, str]:
            if purpose != "MISSION_FINAL":
                raise AssuranceError("USE_PURPOSE_UNSUPPORTED", purpose)
            return ROOT_RESOLUTION_CONSUMER, resolution

        return self._prepare_use(record, consumer, remember=remember)

    def prepare_composition_use(
        self, record: ReviewRecord, *, resolution_id: str
    ) -> CandidateUseCertificate:
        """The ACCEPT use of an official ``COMPOSITION`` record for one compound resolution.

        2026-10-01（第 3 项）：中间目标的独立组合审阅此前只发起、不消费；它的结论由这张
        证书许可，与根终审同一套（当前重判、读集、根权威），消费方是要形成的目标结论。
        """
        resolution = text(resolution_id)

        def consumer(purpose: str, owner_task: str, target: AssuranceRef) -> tuple[str, str]:
            if purpose != "COMPOSITION":
                raise AssuranceError("USE_PURPOSE_UNSUPPORTED", purpose)
            return COMPOUND_RESOLUTION_CONSUMER, resolution

        return self._prepare_use(record, consumer)

    def _prepare_use(
        self,
        record: ReviewRecord,
        consumer: Callable[[str, str, AssuranceRef], tuple[str, str]],
        *,
        remember: bool = True,
    ) -> CandidateUseCertificate:
        store = self.store
        if store.connection.in_transaction:
            raise AssuranceError("USE_PREPARATION_INSIDE_TRANSACTION")
        if not isinstance(record, ReviewRecord):
            raise AssuranceError("USE_SUBJECT_INVALID")
        gate = self.commit._assurance_root_gate
        if gate is None:
            raise AssuranceError("ROOT_AUTHORITY_UNAVAILABLE")
        root = gate.require_execution()
        mission_id = record.binding.mission_id
        reader = AssuranceReader(store, tenant_id=self.tenant_id, mission_id=mission_id)
        # First pass: authenticate the official source and its consumed checks.
        with store.read_view():
            if AssuranceStore(store).lane(mission_id) != "ASSURANCE_1_1":
                raise AssuranceError("ASSURANCE_PROFILE_REQUIRED")
            import_receipt = read_official_review_binding_locked(
                self.commit, self.tenant_id, record
            )
            side_row = store.connection.execute(
                "SELECT * FROM assurance_review_record_bindings WHERE record_id=? AND mission_id=?",
                (str(record.record_id), mission_id),
            ).fetchone()
            if side_row is None:
                raise AssuranceError("REVIEW_RECORD_BINDING_REQUIRED")
            # 2026-09-30：审阅两次都判不下来后由人裁决；裁决回执进读集，"判不下来 + 人通过"算可用。
            adjudication_row = store.connection.execute(
                "SELECT commit_id, receipt_json FROM commit_receipts "
                "WHERE kind='AssuranceReviewAdjudicated' AND subject_id=?",
                (str(record.record_id),),
            ).fetchone()
            adjudication = None if adjudication_row is None else decode(adjudication_row[1])
            adjudication_ref = None if adjudication is None else AssuranceRef(
                "commit_receipt", Pin(adjudication_row[0], 0, fingerprint(adjudication))
            )
            side = decode(side_row["binding_json"])
            classification_ref = AssuranceRef.from_json(
                decode(import_receipt.body_json)["classification_ref"], kinds={"commit_receipt"}
            )
            imported = read_imported_review_locked(self.commit, reader, classification_ref)
            body = imported.binding.to_json()
            scope = body["subject"]["completion_scope_ref"]
            purpose = body["subject"]["purpose"]
            if purpose not in USE_TARGET_KINDS or scope is None:
                raise AssuranceError("USE_PURPOSE_UNSUPPORTED", purpose)
            target = AssuranceRef.from_json(
                body["subject"]["target"], kinds={USE_TARGET_KINDS[purpose]}
            )
            owner_task = body["subject"]["owner_task_ref"]["id"]
            consumer_kind, consumer_id = consumer(purpose, owner_task, target)
            identity = UseIdentity(
                mission_id,
                consumer_kind,
                consumer_id,
                scope["id"],
                self.principal_id,
                "ACCEPT",
                root.root_incarnation_id,
            )
            consumed_refs = tuple(
                AssuranceRef.from_json(item, kinds={"check_binding"})
                for item in side["consumed_check_refs"]
            )
            consumed_bindings = {}
            blobs: set[AssuranceRef] = set()
            for ref in consumed_refs:
                check = CheckBinding.from_json(decode(reader.read_exact_metadata(ref).body_json))
                consumed_bindings[ref] = check
                blobs.update(check.evidence_refs)
                if check.execution_ref.kind in {"local_check_receipt", "execution_receipt"}:
                    payload = decode(reader.read_exact_metadata(check.execution_ref).body_json)[
                        "payload"
                    ]
                    manifest = decode(
                        reader.read_exact_metadata(
                            AssuranceRef.from_json(payload["input_manifest_ref"])
                        ).body_json
                    )
                    for document in manifest["artifacts"]:
                        artifact = Artifact.from_json(document)
                        blobs.add(
                            AssuranceRef(
                                "artifact",
                                Pin(artifact.id, artifact.version, artifact.content_hash),
                            )
                        )
            pins = self._bound_pins(mission_id, body["review_key"], blobs)
        # Prepared checks read pinned CAS outside the lock (their own read views).
        check_uses = []
        for ref, check in sorted(consumed_bindings.items(), key=lambda item: item[0].key):
            if (
                check.execution_ref.kind not in {"local_check_receipt", "execution_receipt"}
                or self.check_adapter is None
                or target.kind != "result"
            ):
                # A check is bound to a Result (a MISSION_FINAL target is the root
                # Task): it stays UNKNOWN there rather than being replayed from its
                # historical PASS.  Executor checks are used from their recorded receipt.
                continue
            check_uses.append(
                prepare_local_check_use(
                    self.check_adapter,
                    binding_ref=ref,
                    result_ref=target,
                    completion_scope=AssuranceRef("completion_scope", Pin.from_json(scope)),
                    identity=identity,
                    review_key=body["review_key"],
                    pins=pins,
                    authority=self.authority,
                    maximum_blob_bytes=self.maximum_blob_bytes,
                )
            )
        # Second pass: one consistent snapshot for exact objects, complete sets,
        # current authority and the current normalized check grades.
        with store.read_view():
            now_ms = integer(int(store.now * 1000))
            epochs = read_epochs_locked(store.connection, mission_id)
            require_epochs_locked(store.connection, mission_id, epochs, now_ms=now_ms)
            if (
                read_official_review_binding_locked(self.commit, self.tenant_id, record)
                != import_receipt
                or read_imported_review_locked(self.commit, reader, classification_ref) != imported
            ):
                raise AssuranceError("RECHECK_REQUIRED")
            review_ref = AssuranceRef(
                "review", Pin(str(record.record_id), 0, fingerprint(record.to_json()))
            )
            manifest_ref = AssuranceRef(
                "input_manifest",
                Pin(record.evidence_manifest_hash, 0, record.evidence_manifest_hash),
            )
            required = {
                review_ref,
                import_receipt.ref,
                classification_ref,
                imported.turn.ref,
                manifest_ref,
                AssuranceRef("review_package", Pin.from_json(body["package_ref"])),
                AssuranceRef("requirements", Pin.from_json(body["requirements_ref"])),
                AssuranceRef("completion_scope", Pin.from_json(scope)),
                AssuranceRef.from_json(body["criterion_policy_ref"], kinds={"check_policy"}),
                AssuranceRef("task", Pin.from_json(body["subject"]["owner_task_ref"])),
                target,
                *consumed_refs,
            }
            if adjudication_ref is not None:
                required.add(adjudication_ref)
            metadata = tuple(
                reader.read_exact_metadata(ref) for ref in sorted(required, key=lambda r: r.key)
            )
            permissions = tuple(
                (row.ref, _permission(self.authority, identity, row.ref, now_ms))
                for row in metadata
            )
            checks: dict[AssuranceRef, CheckResult] = {}
            check_grades = []
            for use in check_uses:
                if use.binding.check_spec_ref in checks:
                    raise AssuranceError("AMBIGUOUS_REVIEW_CHECK")
                result = use.consume_locked(
                    self.check_adapter, identity=identity, authority=self.authority, now_ms=now_ms
                )
                checks[use.binding.check_spec_ref] = result
                check_grades.append((use.binding_ref, result.effective.value))
            manifest = decode(reader.read_exact_metadata(manifest_ref).body_json)
            acceptable, decision_reasons, effective, gates = self._redecide(body, manifest, checks)
            if (
                not acceptable
                and adjudication is not None
                and manifest["effective_verdict"] == "INCONCLUSIVE"
                and adjudication.get("decision") == "pass"
                and adjudication.get("mission_id") == mission_id
                and adjudication.get("record_id") == str(record.record_id)
                and adjudication.get("target_id") == target.pin.id
            ):
                acceptable = True
                decision_reasons.append("human_adjudication:" + adjudication_row[0])
                # The person passed the result as a whole: what the reviewers could
                # not grade counts as met; a FAIL would have made the review REWORK.
                effective = {
                    name: ("PASS" if value == "UNKNOWN" else value)
                    for name, value in effective.items()
                }
                adjudicated = adjudication_row[0]
            else:
                adjudicated = None
            complete = read_complete_evidence_snapshot(reader, scope_id=identity.scope_id)
            policy_row = store.connection.execute(
                "SELECT policy_hash FROM assurance_mission_bindings WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if policy_row is None:
                raise AssuranceError("ASSURANCE_PROFILE_UNBOUND")
        anchors = []
        for ref, check in consumed_bindings.items():
            current = next((value for use_ref, value in check_grades if use_ref == ref), None)
            anchors.append(
                CheckAnchorInput(
                    ref,
                    grade(current if current is not None else "UNKNOWN"),
                    check.observed_at_ms,
                    check.not_after_ms,
                )
            )
        evaluation = evaluate_acceptance_support(
            mission_id=mission_id,
            scope_id=identity.scope_id,
            purpose=identity.purpose,
            now_ms=now_ms,
            subject_hash=target.pin.content_hash,
            review=ReviewAnchorInput(review_ref, acceptable, side_row["created_at_ms"]),
            checks=tuple(anchors),
        )
        reads = [item.read_item for item in metadata] + [item.read_item for item in complete]
        for _, permission in permissions:
            reads.extend((permission.access, permission.policy))
        for use in check_uses:
            reads.extend(use.read_set)
        deadlines = [permission.not_after_ms for _, permission in permissions]
        deadlines.extend(use.not_after_ms for use in check_uses)
        if evaluation.earliest_expiry_ms is not None:
            deadlines.append(evaluation.earliest_expiry_ms)
        not_after_ms = min(deadlines)
        if evaluation.usable and acceptable:
            decision = "USABLE"
        elif evaluation.truth in {TruthValue.FALSE, TruthValue.CONFLICT}:
            decision = "BLOCKED"
        else:
            decision = "NEEDS_REVIEW"
        if not_after_ms <= now_ms and decision == "USABLE":
            decision = "NEEDS_REVIEW"
        reasons = [
            *evaluation.reasons,
            *decision_reasons,
            "support:" + support_fingerprint(evaluation),
        ]
        certificate = UseCertificate(
            mission_id=mission_id,
            consumer_kind=identity.consumer_kind,
            consumer_id=identity.consumer_id,
            scope_id=identity.scope_id,
            principal_id=identity.principal_id,
            purpose=identity.purpose,
            truth=str(evaluation.truth),
            freshness="CURRENT",
            availability="READABLE",
            decision=decision,
            coverage="COMPLETE",
            policy_ref=Pin("assurance-exec-v1.1", 1, policy_row["policy_hash"]),
            read_set=_merge_reads(reads),
            clean_support_refs=evaluation.clean_support_refs,
            issued_at_ms=now_ms,
            not_after_ms=not_after_ms,
            reasons=tuple(reasons[:64]),
            mission_epoch=epochs.mission,
            environment_epoch=epochs.environment,
            clock_generation=epochs.clock_generation,
            root_incarnation_id=root.root_incarnation_id,
        )
        certificate_id = "assurance-use:" + fingerprint(
            {
                "identity": _identity_json(identity),
                "record_id": str(record.record_id),
                "issued_at_ms": now_ms,
                "read_set_hash": fingerprint(certificate.to_json()["read_set"]),
            }
        )
        candidate = CandidateUseCertificate(
            certificate_id,
            certificate,
            identity,
            record,
            target.pin.content_hash,
            epochs,
            metadata,
            permissions,
            tuple(check_uses),
            tuple(check_grades),
            complete,
            evaluation,
            acceptable,
            tuple(sorted(effective.items())),
            tuple(sorted(gates.items())),
            now_ms,
            adjudicated,
        )
        with store.read_view():
            self.require_current_locked(candidate, now_ms=int(store.now * 1000))
        if remember:
            self._remember(candidate)
        return candidate

    def _bound_pins(
        self, mission_id: str, review_key: str, blobs: set[AssuranceRef]
    ) -> dict[AssuranceRef, str]:
        pins = {}
        for ref in sorted(blobs, key=lambda r: r.key):
            rows = self.store.connection.execute(
                "SELECT pin_id FROM assurance_blob_pins WHERE mission_id=? AND review_key=? "
                "AND object_ref_json=? AND state IN ('PREPARING','BOUND') ORDER BY pin_id LIMIT 2",
                (mission_id, review_key, canonical(ref.to_json())),
            ).fetchall()
            if len(rows) != 1:
                raise AssuranceError("LIVE_BLOB_PIN_REQUIRED", ref.pin.id)
            pins[ref] = rows[0][0]
        return pins

    @staticmethod
    def _redecide(
        body: Mapping[str, Any],
        manifest: Mapping[str, Any],
        checks: Mapping[AssuranceRef, CheckResult],
    ) -> tuple[bool, list[str], dict[str, str], dict[str, str]]:
        """The official model reply, re-decided under current typed check results."""
        policies = {
            row["criterion_id"]: CriterionPolicy.from_json(row)
            for row in body["check_requirements"]
        }
        reply = ReviewReply(
            manifest["model_verdict"],
            tuple(
                Assessment(
                    item["criterion_id"],
                    grade(item["model_grade"]),
                    tuple(item["evidence_ids"]),
                    item["reason"],
                    tuple(item["limitations"]),
                )
                for item in manifest["criteria"]
            ),
            tuple(
                Finding(item["criterion_id"], item["severity"], item["reason"])
                for item in manifest["findings"]
            ),
        )
        decision = decide_review(
            reply,
            Formula.from_json(body["formula"], frozenset(policies)),
            tuple(body["mandatory_ids"]),
            policies,
            checks,
        )
        recorded = {item["criterion_id"]: item["effective_grade"] for item in manifest["criteria"]}
        effective = {name: value.value for name, value in decision.effective_grades.items()}
        reasons = [
            "current_decision:" + ("ACCEPTABLE" if decision.acceptable else "NOT_ACCEPTABLE"),
            "recorded_verdict:" + str(manifest["effective_verdict"]),
        ]
        if effective != recorded:
            reasons.append("effective_grades:CHANGED_SINCE_IMPORT")
        gates = {}
        for item in decision.reasons:
            name, _, reason = item.partition(":")
            gates[name] = reason
        return decision.acceptable, reasons, effective, gates

    # ----------------------------------------------------------------- commit
    def require_current_locked(self, candidate: CandidateUseCertificate, *, now_ms: int) -> None:
        """Short final-lock revalidation; raises rather than repairing anything."""
        store = self.store
        if not store.connection.in_transaction:
            raise AssuranceError("READ_TRANSACTION_REQUIRED")
        integer(now_ms)
        gate = self.commit._assurance_root_gate
        if (
            gate is None
            or gate.require_execution().root_incarnation_id
            != candidate.identity.root_incarnation_id
        ):
            raise AssuranceError("ROOT_AUTHORITY_UNAVAILABLE")
        identity = candidate.identity
        if AssuranceStore(store).lane(identity.mission_id) != "ASSURANCE_1_1":
            raise AssuranceError("ASSURANCE_PROFILE_REQUIRED")
        require_epochs_locked(
            store.connection, identity.mission_id, candidate.epochs, now_ms=now_ms
        )
        reader = AssuranceReader(store, tenant_id=self.tenant_id, mission_id=identity.mission_id)
        for item in candidate.metadata:
            if reader.read_exact_metadata(item.ref) != item:
                raise AssuranceError("RECHECK_REQUIRED")
        for ref, captured in candidate.permissions:
            _require_same_permission(self.authority, identity, ref, captured, now_ms)
        for use in candidate.check_uses:
            result = use.consume_locked(
                self.check_adapter, identity=identity, authority=self.authority, now_ms=now_ms
            )
            recorded = next(
                (value for ref, value in candidate.check_grades if ref == use.binding_ref), None
            )
            if recorded != result.effective.value:
                raise AssuranceError("RECHECK_REQUIRED")
        # Complete sets are protected by the same-transaction source barriers:
        # any writer to an inventoried table moved the mission/global epoch.
        environment = store.connection.execute(
            "SELECT clock_state FROM assurance_environment_state WHERE singleton=1"
        ).fetchone()
        if environment is None:
            raise AssuranceError("ASSURANCE_ENVIRONMENT_UNINITIALIZED")
        if candidate.certificate.decision == "USABLE":
            access, policy = self._current_witness(candidate, now_ms)
            check_certificate_binding(
                candidate.certificate,
                identity=identity,
                mission_epoch=candidate.epochs.mission,
                environment_epoch=candidate.epochs.environment,
                clock_generation=candidate.epochs.clock_generation,
                clock_state=environment["clock_state"],
                now_ms=now_ms,
                current_access=access,
                current_policy=policy,
            )

    def _current_witness(
        self, candidate: CandidateUseCertificate, now_ms: int
    ) -> tuple[ReadItem, ReadItem]:
        review_ref = AssuranceRef(
            "review",
            Pin(str(candidate.record.record_id), 0, fingerprint(candidate.record.to_json())),
        )
        permission = _permission(self.authority, candidate.identity, review_ref, now_ms)
        return permission.access, permission.policy

    def lock_use_locked(self, candidate: CandidateUseCertificate, *, now_ms: int) -> None:
        """Run the final lock at the head of the consumer's own write transaction.

        A writer such as leaf acceptance moves inventoried rows (result, artifact,
        acceptance) as its intended effect, which advances the mission epoch
        through the same-transaction barriers before it can commit the use.
        Locking first, under BEGIN IMMEDIATE, proves that no foreign writer
        moved anything between preparation and this UoW; the later commit in the
        same generation trusts that proof instead of re-reading its own writes.
        Nothing is written here and the lock is forgotten with the candidate.
        """
        store = self.store
        if not store.connection.in_transaction or store.transaction_generation == 0:
            raise AssuranceError("USE_COMMIT_TRANSACTION_REQUIRED")
        self.require_current_locked(candidate, now_ms=now_ms)
        self._locked[candidate.certificate_id] = store.transaction_generation

    def commit_use_locked(self, candidate: CandidateUseCertificate, *, now_ms: int) -> AssuranceRef:
        """Record the certificate inside the consumer's own write transaction.

        The caller (the original acceptance/dispatch/handoff writer) owns the
        transaction and writes its effect beside this receipt. A candidate that
        is not USABLE is still refused here; diagnostics are not persisted as
        grants.
        """
        store = self.store
        if not store.connection.in_transaction:
            raise AssuranceError("USE_COMMIT_TRANSACTION_REQUIRED")
        if self._locked.get(candidate.certificate_id) != store.transaction_generation:
            self.require_current_locked(candidate, now_ms=now_ms)
        if candidate.certificate.decision != "USABLE":
            raise AssuranceError("CERTIFICATE_NOT_USABLE")
        certificate = candidate.certificate
        receipt_id = "assurance-use-certified:" + candidate.certificate_id
        receipt = {
            "mission_id": certificate.mission_id,
            "certificate_id": candidate.certificate_id,
            "certificate_hash": fingerprint(certificate.to_json()),
            "consumer_kind": certificate.consumer_kind,
            "consumer_id": certificate.consumer_id,
            "purpose": certificate.purpose,
            "scope_id": certificate.scope_id,
            "record_id": str(candidate.record.record_id),
            "subject_hash": candidate.subject_hash,
        }
        with atomic(store) as connection:
            existing = connection.execute(
                "SELECT * FROM commit_receipts WHERE commit_id=?", (receipt_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["kind"] != USE_CERTIFIED_KIND
                    or decode(existing["receipt_json"]) != receipt
                    or existing["proposal_hash"] != fingerprint(receipt)
                ):
                    raise AssuranceError("IMMUTABLE_IDENTITY_CONFLICT", "commit_receipts")
                AssuranceStore(store).record_certificate(candidate.certificate_id, certificate)
                return AssuranceRef("commit_receipt", Pin(receipt_id, 0, fingerprint(receipt)))
            if not AssuranceStore(store).record_certificate(candidate.certificate_id, certificate):
                raise AssuranceError("IMMUTABLE_IDENTITY_CONFLICT", "assurance_use_certificates")
            store.insert_receipt(
                commit_id=receipt_id,
                kind=USE_CERTIFIED_KIND,
                subject_id=candidate.certificate_id,
                base_version=0,
                proposal_hash=fingerprint(receipt),
                receipt=receipt,
            )
            self.commit._emit(
                USE_CERTIFIED_KIND,
                certificate.mission_id,
                key=candidate.certificate_id,
                payload=receipt,
            )
        return AssuranceRef("commit_receipt", Pin(receipt_id, 0, fingerprint(receipt)))


def _identity_json(identity: UseIdentity) -> dict[str, str]:
    return {
        "mission_id": identity.mission_id,
        "consumer_kind": identity.consumer_kind,
        "consumer_id": identity.consumer_id,
        "scope_id": identity.scope_id,
        "principal_id": identity.principal_id,
        "purpose": identity.purpose,
        "root_incarnation_id": identity.root_incarnation_id,
    }


__all__ = (
    "ACCEPTANCE_CONSUMER",
    "USE_CERTIFIED_KIND",
    "AssuranceValidity",
    "CandidateUseCertificate",
    "acceptance_id_for",
    "result_acceptance_ids",
)
