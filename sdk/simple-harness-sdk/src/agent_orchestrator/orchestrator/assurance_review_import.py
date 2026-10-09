# SPDX-License-Identifier: Apache-2.0
"""Original official review writer, fed only by authenticated runtime imports.

A review is a historical judgement, never an acceptance/use licence. Current
acceptance still requires its own complete validity certificate and OCC checks.
All preparation runs outside the write lock; the original writer repeats the
bounded identity/authority/epoch checks in the effect transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..assurance.certificates import UseIdentity
from ..assurance.checks import (
    Assessment,
    CheckResult,
    CriterionPolicy,
    Formula,
    Grade,
    ReviewReply,
    decode_review_reply,
    decide_review,
    evaluate_check_gate,
    global_blocker_targets,
)
from ..assurance.codec import MAX_RECORD_BYTES, AssuranceError, canonical, decode, fingerprint
from ..assurance.disclosure import DisclosureBatch, disclosed_to_turn
from ..assurance.evidence import CatalogueEntry, ReadItem, resolve_evidence_ids
from ..assurance.refs import AssuranceRef, Pin
from ..assurance.reviews import (
    REVIEW_CODEC_VERSION,
    AssuranceReviewBinding,
    ReviewInvocation,
    ReviewRecordBinding,
)
from ..assurance.root_gate import CurrentReadPermission
from ..contracts import TERMINAL_ATTEMPT, TERMINAL_MISSION, TERMINAL_TASK
from ..contracts.resolution import (
    CheckExecution,
    CriterionOutcome,
    CriterionVerdict,
    ReviewRecord,
    ReviewVerdict,
)
from ..contracts.state_machines import TaskStatus
from ..storage.assurance_blobs import PreparedBlob, read_pinned_blob
from ..storage.assurance_reads import (
    AssuranceReader,
    CompleteRead,
    EpochSnapshot,
    ResolvedRef,
    read_complete_evidence_snapshot,
    read_epochs_locked,
    require_epochs_locked,
)
from ..storage.assurance_store import AssuranceStore
from ..storage.assurance_work import atomic
from ..storage.htn_store import HtnStore
from ..storage.assurance_reads import CurrentAuthority, _permission, _require_same_permission
from .assurance_check_use import PreparedCheckUse, _merge_reads
from .assurance_review_transport import read_review_invocation_locked


@dataclass(frozen=True, slots=True)
class ImportedReview:
    invocation: ReviewInvocation
    binding: AssuranceReviewBinding
    classification: ResolvedRef
    turn: ResolvedRef
    #: None for the "no reply" shape: the second call's turn failed through the reviewer's own
    #: doing (2026-10-09 四项修复第 1 条); nothing was exposed to a reply that never came.
    provider_manifest: ResolvedRef | None
    disclosures: tuple[DisclosureBatch, ...]
    disclosure_metadata: tuple[ResolvedRef, ...]
    catalogue: tuple[CatalogueEntry, ...]
    exposed: frozenset[str]
    #: The frozen review package text when it cannot be read from a provider manifest.
    package_json: str | None = None


PRE_SCOPE_ID = "mission"


def review_scope_id(body: Mapping[str, Any]) -> str:
    """The use-identity scope of a review binding.

    Only METHOD_PLAN may be bound before a completion Scope exists (subject
    shape rule); its use scope is the Mission itself, exactly as the purpose
    builder froze it (``PurposeSubject.scope_id == "mission"``). Every other
    purpose without a Scope is refused rather than given a guessed scope.
    """
    subject = body["subject"]
    scope = subject["completion_scope_ref"]
    if scope is not None:
        return str(scope["id"])
    if subject["purpose"] != "METHOD_PLAN":
        raise AssuranceError("REVIEW_SCOPE_UNAVAILABLE")
    return PRE_SCOPE_ID


def review_subject_stopped(store: Any, binding: AssuranceReviewBinding) -> bool:
    """Whether the reviewed subject can no longer use an answer; purpose-aware.

    A stopped Mission stops every review. A TASK_CONTENT review dies with its
    Attempt or Task. A MISSION_FINAL review is asked *after* the root Task's own
    work is accepted, so the root being DONE is its normal state; it stops when
    the root duty is already resolved or the cut package was superseded. The
    other purposes are bound to a live owner Task.
    """
    body = binding.to_json()
    mission = store.get_mission(body["mission_id"])
    task = store.get_task(body["subject"]["owner_task_ref"]["id"])
    if (
        mission is None
        or mission.status in TERMINAL_MISSION
        or task is None
        or task.mission_id != mission.id
    ):
        return True
    purpose = body["subject"]["purpose"]
    if purpose == "MISSION_FINAL":
        if task.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return True
        from ..storage.htn_store import HtnStore
        from ..storage.store import StoreError
        from .hierarchical_dispatch import ROOT_REVIEW_SUPERSEDED

        package_id = body["package_ref"]["id"]
        try:
            package = HtnStore(store).get_review_package(package_id)
        except StoreError:
            return True
        if HtnStore(store).adopted_goal_resolution(mission.id, str(package.binding.obligation_id)):
            return True
        return any(
            event.type == ROOT_REVIEW_SUPERSEDED
            and str((event.payload or {}).get("package_id", "")) == str(package_id)
            for event in store.list_events(mission.id)
        )
    if purpose == "TASK_CONTENT":
        result = store.get_result(body["subject"]["target"]["pin"]["id"])
        attempt = None if result is None else store.get_attempt(result.envelope.attempt_id)
        if attempt is None:
            return True
        if carried_review_binding(store, body, result):
            # TaskGraph 补全第四批：重审的对象是已验收的结果，任务与原尝试早已结束是它的常态；
            # 它活着 = 任务没结束、这一步还在现行计划里
            return not carried_review_alive(store, mission.id, task.id)
        return task.status in TERMINAL_TASK or attempt.status in TERMINAL_ATTEMPT
    return task.status in TERMINAL_TASK


def carried_review_binding(store: Any, body: Any, result: Any) -> bool:
    """Whether a TASK_CONTENT review binding judges an accepted result again under
    requirements newer than the ones it was produced under (TaskGraph 补全第四批)."""
    from .completion_inputs import frozen_requirements_revision
    from .operation_completion import OperationCompletionError

    try:
        return int(body["requirements_ref"]["revision"]) > frozen_requirements_revision(store, result)
    except OperationCompletionError:
        return False


def carried_review_alive(store: Any, mission_id: str, task_id: str) -> bool:
    """Whether a re-review of a kept, accepted result still has a subject: its Mission has
    not ended and the step is in the current plan.  The one reading every carried-review
    liveness check uses — the Attempt ended long ago and says nothing."""
    from ..storage.htn_store import HtnStore

    mission = store.get_mission(mission_id)
    if mission is None or mission.status in TERMINAL_MISSION:
        return False
    htn = HtnStore(store)
    active = htn.active_plan_revision(mission_id)
    return active is not None and any(
        str(item.task_id) == str(task_id) for item in htn.list_plan_memberships(mission_id, active.revision))


def read_imported_review_locked(
    commit: Any, reader: AssuranceReader, classification_ref: AssuranceRef
) -> ImportedReview:
    """Authenticate the source receipt and exact final request's exposure chain."""
    if not reader.store.connection.in_transaction:
        raise AssuranceError("READ_TRANSACTION_REQUIRED")
    if classification_ref.kind != "commit_receipt":
        raise AssuranceError("REVIEW_CLASSIFICATION_SOURCE_INVALID")
    classified = reader.read_exact_metadata(classification_ref)
    source, meta = decode(classified.body_json), decode(classified.state_witness_json)
    intent_id = source.get("intent_id")
    invocation, binding = read_review_invocation_locked(commit, reader, intent_id)
    value, bound = invocation.to_json(), binding.to_json()
    # Two shapes are importable (阶段 C 第 3 条): a reply that decoded, and the *second*
    # call's reply that still could not be decoded — that one is imported as "no usable
    # reply" (an INCONCLUSIVE record, see ``_interpret``), never as the reviewer's verdict.
    readable = (meta["kind"] == "AssuranceReviewClassified"
                and source.get("classification") == "READY_FOR_CURRENT_REVIEW"
                and source.get("error_code") is None)
    unusable = (meta["kind"] == "AssuranceReviewFormatRejected"
                and source.get("classification") == "FORMAT_INVALID"
                and isinstance(source.get("error_code"), str) and value["ordinal"] == 2)
    # 2026-10-09 四项修复第 1 条：第 2 次调用的回合失败、又不是被打断（调用上限、工具用错、
    # 输出上限用完……审阅员自己的事）——同样导入为"没有可采用的回复"的正式记录，交人裁决。
    # 被打断的第 2 次（有被打断回执）不在此列：仍是"被打断用完"，终审重切、步骤重做。
    failed_second = (meta["kind"] == "AssuranceReviewClassified"
                     and source.get("classification") == "TURN_FAILED"
                     and isinstance(source.get("error_code"), str) and value["ordinal"] == 2
                     and reader.store.get_receipt("assurance-review-interrupted:" + str(intent_id)) is None)
    if (
        not (readable or unusable or failed_second)
        or meta["subject_id"] != intent_id
        or meta["base_version"] != 0
        or meta["proposal_hash"] != fingerprint(source)
        or source.get("mission_id") != reader.mission_id
        or source.get("review_key") != bound["review_key"]
        or source.get("invocation_ordinal") != value["ordinal"]
    ):
        raise AssuranceError("REVIEW_CLASSIFICATION_SOURCE_INVALID")
    turn = reader.read_exact_metadata(
        AssuranceRef.from_json(source.get("turn_ref"), kinds={"agent_turn_receipt"})
    )
    payload = decode(turn.body_json)["payload"]
    intent = reader.store.get_intent(intent_id)
    if (
        payload.get("intent_id") != intent_id
        or payload.get("state") != ("FAILED" if failed_second else "COMMITTED")
        or payload.get("review_key") != bound["review_key"]
        or payload.get("invocation_ordinal") != value["ordinal"]
        or payload.get("turn_id") != intent.expected_turn_id
        or payload.get("agent_id") != intent.agent_id
        or payload.get("raw_output_hash") != source.get("raw_output_hash")
        or payload.get("exposure_error") != ("REVIEW_TURN_NOT_COMMITTED" if failed_second else None)
    ):
        raise AssuranceError("REVIEW_TURN_SOURCE_MISMATCH")
    # A classification cannot transform an untrusted source row into a runtime
    # receipt. Authenticate the original importer receipt as well as the Event.
    imported_id = "assurance-review-turn:" + fingerprint(
        {"intent": intent_id, "turn": intent.expected_turn_id}
    )
    imported = reader.store.connection.execute(
        "SELECT * FROM commit_receipts WHERE commit_id=?", (imported_id,)
    ).fetchone()
    expected = {
        "mission_id": reader.mission_id,
        "intent_id": intent_id,
        "source_hash": fingerprint(payload),
        "event_ref": turn.ref.to_json(),
    }
    if (
        imported is None
        or imported["kind"] != "AssuranceReviewTurnImported"
        or imported["subject_id"] != intent_id
        or imported["base_version"] != 0
        or imported["proposal_hash"] != fingerprint(payload)
        or decode(imported["receipt_json"]) != expected
    ):
        raise AssuranceError("REVIEW_TURN_SOURCE_MISMATCH")
    if intent.agent_id in bound["producer_agent_ids"]:
        raise AssuranceError("REVIEW_INDEPENDENCE_REQUIRED")
    catalogue = {
        item["label"]: CatalogueEntry(item["label"], AssuranceRef.from_json(item["ref"]))
        for item in bound["evidence_catalogue"]
    }
    if failed_second:
        if payload.get("provider_manifest_ref") is not None:
            raise AssuranceError("REVIEW_TURN_SOURCE_MISMATCH")
        # 没有回复就没有曝光链；审查包从冻结的包记录读（不是从提供方清单里的消息）
        package = reader.read_exact_metadata(
            AssuranceRef("review_package", Pin.from_json(bound["package_ref"]))
        )
        return ImportedReview(
            invocation, binding, classified, turn, None, (), (),
            tuple(catalogue[k] for k in sorted(catalogue)), frozenset(),
            canonical(decode(package.body_json, limit=MAX_RECORD_BYTES)),
        )
    manifest = reader.read_exact_metadata(
        AssuranceRef.from_json(payload.get("provider_manifest_ref"), kinds={"input_manifest"})
    )
    provider = decode(manifest.body_json, limit=MAX_RECORD_BYTES)
    batches = AssuranceStore(reader.store).disclosure_chain(reader.mission_id, bound["review_key"])
    exposed = disclosed_to_turn(
        batches,
        mission_id=reader.mission_id,
        review_key=bound["review_key"],
        agent_id=intent.agent_id,
        turn_receipt_ref=turn.ref,
        provider_input_hash=provider["provider_input_hash"],
    )
    disclosure_metadata = []
    selected = []
    for batch in batches:
        event = reader.read_exact_metadata(batch.delivery_receipt_ref)
        disclosure_metadata.append(event)
        expected_event = {
            "review_key": batch.review_key,
            "batch_no": batch.batch_no,
            "previous_batch_hash": batch.previous_batch_hash,
            "delta_hash": fingerprint([item.to_json() for item in batch.entries]),
            "reviewer_agent_id": batch.reviewer_agent_id,
            "turn_receipt_ref": batch.turn_receipt_ref.to_json(),
            "provider_input_hash": batch.provider_input_hash,
            "visible_message_ids": list(batch.visible_message_ids),
        }
        if decode(event.body_json)["payload"] != expected_event:
            raise AssuranceError("DISCLOSURE_INPUT_BINDING")
        if (
            batch.turn_receipt_ref == turn.ref
            and batch.reviewer_agent_id == intent.agent_id
            and batch.provider_input_hash == provider["provider_input_hash"]
        ):
            if not set(batch.visible_message_ids) <= set(provider["selected_message_ids"]):
                raise AssuranceError("UNEXPOSED_EVIDENCE")
            selected.append(batch)
            for item in batch.entries:
                if catalogue.setdefault(item.label, item) != item:
                    raise AssuranceError("LABEL_COLLISION")
    return ImportedReview(
        invocation,
        binding,
        classified,
        turn,
        manifest,
        tuple(selected),
        tuple(disclosure_metadata),
        tuple(catalogue[k] for k in sorted(catalogue)),
        exposed,
    )


#: A reply the reviewer committed that decodes but cannot be imported as given —
#: its own mistake, not a missing source.  POLICY_CATALOGUE_MISMATCH is the
#: package's, not the reply's, and stays final.
REPAIRABLE_INTERPRETATION_ERRORS = frozenset(
    {"UNEXPOSED_EVIDENCE", "DUPLICATE_CRITERION", "FINDING_SCOPE", "MANDATORY_CRITERIA_INVALID",
     "DUPLICATE_CLAIM", "CLAIM_SCOPE", "DUPLICATE_METHOD", "METHOD_SCOPE", "SUMMARY_SCOPE"}
)
NO_USABLE_REPLY = "NO_USABLE_REPLY"


def _no_usable_reply(bound: Mapping[str, Any], code: str) -> ReviewReply:
    """What the record says when the reviewer's last reply could not be used: nothing was
    judged.  Every criterion is UNKNOWN with the reason named; the overall word is
    INCONCLUSIVE — the same exit as a reviewer that says it cannot tell (a person rules)."""
    marker = "REVIEW_NO_USABLE_REPLY:" + code
    return ReviewReply(
        "INCONCLUSIVE",
        tuple(Assessment(row["criterion_id"], Grade.UNKNOWN, (), marker, (marker,))
              for row in sorted(bound["check_requirements"], key=lambda row: row["criterion_id"])),
        (),
    )


def _interpret(
    imported: ImportedReview, raw: bytes, checks: Mapping[AssuranceRef, CheckResult]
) -> tuple[ReviewRecord, dict]:
    """The official reading of one committed review turn.

    Deterministic in its inputs, so every reader (the importer, its final-lock recheck,
    a UseCertificate) reads the same record.  The reviewer's first unusable reply is
    asked again in a fresh session by the caller; its *second* one is read here as "no
    usable reply": the reply could not be decoded (classified FORMAT_INVALID), or it
    decoded but cannot be imported as given (a repairable interpretation error)."""
    bound = imported.binding.to_json()
    source = decode(imported.classification.body_json)
    if source.get("classification") in {"FORMAT_INVALID", "TURN_FAILED"}:
        # 不合法 JSON，或这一轮根本没交出回复（2026-10-09）：都是"没有可采用的回复"
        code = str(source["error_code"])
        return _interpret_reply(imported, _no_usable_reply(bound, code), checks, code)
    try:
        return _interpret_reply(imported, decode_review_reply(raw), checks, None)
    except AssuranceError as error:
        if (error.code not in REPAIRABLE_INTERPRETATION_ERRORS
                or imported.invocation.to_json()["ordinal"] != 2):
            raise
        return _interpret_reply(imported, _no_usable_reply(bound, error.code), checks, error.code)


def _interpret_reply(
    imported: ImportedReview, reply: ReviewReply, checks: Mapping[AssuranceRef, CheckResult],
    no_usable_reply: str | None,
) -> tuple[ReviewRecord, dict]:
    bound = imported.binding.to_json()
    turn = decode(imported.turn.body_json)["payload"]
    policies = {
        row["criterion_id"]: CriterionPolicy.from_json(row) for row in bound["check_requirements"]
    }
    decision = decide_review(
        reply,
        Formula.from_json(bound["formula"], frozenset(policies)),
        tuple(bound["mandatory_ids"]),
        policies,
        checks,
    )
    # 全局问题（F04）：BLOCKER 的那几条挂到哪些准则上由 decide_review 同一个规则定；审阅员的原话
    # 写进这些准则的说明，随打回交到后面——不然被打回的一方只看到"不成立"，不知道为什么。
    blocked = set(global_blocker_targets(reply, tuple(bound["mandatory_ids"]), set(policies)))
    blocker_notes = [f"全局问题（BLOCKER）：{item.reason}" for item in reply.global_findings
                     if item.severity == "BLOCKER"]
    outcomes, details = [], []
    for assessment in sorted(reply.assessments, key=lambda item: item.criterion_id):
        name = assessment.criterion_id
        refs = resolve_evidence_ids(
            bound["review_key"], assessment.evidence_ids, imported.catalogue, imported.exposed
        )
        gate = evaluate_check_gate(policies[name], checks)
        effective = decision.effective_grades[name]
        execution = CheckExecution.NOT_RUN
        if gate.grade in {Grade.PASS, Grade.FAIL}:
            execution = CheckExecution.SUCCEEDED
        elif gate.grade is not None and gate.consumed:
            states = {
                row.execution_state for row in checks.values() if row.receipt in gate.consumed
            }
            execution = next(
                (
                    state
                    for state in (
                        CheckExecution.ERROR,
                        CheckExecution.CANCELLED,
                        CheckExecution.RUNNING,
                    )
                    if state.value in states
                ),
                CheckExecution.NOT_RUN,
            )
        # Public CriterionOutcome V1 cannot represent SEMANTIC PASS/NOT_RUN.
        # Keep its bytes/codec unchanged and project that combination as UNKNOWN;
        # the exact model/effective grades live in the authenticated manifest.
        # Assured acceptance must consume that manifest, not the legacy projection.
        projected = effective
        limitations = list(assessment.limitations)
        if name in blocked:
            limitations.extend(blocker_notes)
        if effective is Grade.PASS and execution is not CheckExecution.SUCCEEDED:
            projected = Grade.UNKNOWN
            limitations.append("ASSURANCE_SEMANTIC_GRADE_IN_BOUND_MANIFEST")
        outcomes.append(
            CriterionOutcome(
                name,
                CriterionVerdict(projected.value),
                execution,
                evidence_refs=(),
                limitations=tuple(limitations),
            )
        )
        details.append(
            {
                "criterion_id": name,
                "model_grade": assessment.verdict.value,
                "effective_grade": effective.value,
                "check_gate": None if gate.grade is None else gate.grade.value,
                "check_reason": gate.reason,
                "evidence_ids": list(assessment.evidence_ids),
                "evidence_refs": [ref.to_json() for ref in refs],
                "reason": assessment.reason,
                "limitations": list(assessment.limitations),
            }
        )
    # Package's original binding remains authoritative; no expanded TypedRef V1.
    from ..contracts.resolution import ReviewPackage

    package = ReviewPackage.from_json(decode(raw_package(imported)))
    # 逐条确认（阶段 C）：只能确认审查包"本步待确认结论"里的编号；证据照准则那样解析，
    # 没披露给它的标签一样拒。漏写的编号就是没确认，不拒收。
    listed = {row["claim_id"]: row for row in package.claims_to_confirm}
    confirmations = []
    for item in sorted(reply.claims, key=lambda item: item.claim_id):
        if item.claim_id not in listed:
            raise AssuranceError("CLAIM_SCOPE")
        refs = resolve_evidence_ids(
            bound["review_key"], item.evidence_ids, imported.catalogue, imported.exposed
        )
        confirmations.append({
            "claim_id": item.claim_id,
            "content_sha256": listed[item.claim_id]["content_sha256"],
            "confirmed": bool(item.confirmed),
            "evidence_refs": [ref.to_json() for ref in refs],
            "reason": item.reason,
        })
    # 做法与摘要（阶段 C3）：与逐条确认同一个规矩——只能说审查包里列出的；漏写就是没表态。
    judged = {row["method_ref"]: row for row in package.methods_to_judge}
    methods = []
    for item in sorted(reply.methods, key=lambda item: item.method_ref):
        if item.method_ref not in judged:
            raise AssuranceError("METHOD_SCOPE")
        methods.append({
            "method_ref": item.method_ref, "based_on": judged[item.method_ref]["based_on"],
            "reusable": bool(item.reusable), "purpose": item.purpose,
            "at_fault": bool(item.at_fault), "reason": item.reason,
        })
    summary = None
    if reply.summary is not None:
        if package.summary_to_confirm is None:
            raise AssuranceError("SUMMARY_SCOPE")
        summary = {
            "result_ref": package.summary_to_confirm["result_ref"],
            "summary_sha256": package.summary_to_confirm["summary_sha256"],
            "faithful": bool(reply.summary.faithful), "reason": reply.summary.reason,
        }
    verdict = reply.verdict
    if verdict == "ACCEPT" and not decision.acceptable:
        verdict = "REWORK" if Grade.FAIL in decision.effective_grades.values() else "INCONCLUSIVE"
    manifest = {
        "schema_version": 1,
        "codec_version": REVIEW_CODEC_VERSION,
        "review_key": bound["review_key"],
        "binding_hash": imported.binding.content_hash,
        "turn_ref": imported.turn.ref.to_json(),
        "raw_output_hash": turn["raw_output_hash"],
        "model_verdict": reply.verdict,
        "effective_verdict": verdict,
        "criteria": details,
        "findings": [
            {"criterion_id": item.criterion_id, "severity": item.severity, "reason": item.reason}
            for item in reply.findings
        ],
        "global_findings": [{"severity": item.severity, "reason": item.reason} for item in reply.global_findings],
        "claims": confirmations,
        "methods": methods,
        "summary": summary,
        "success_witness": sorted(decision.success_witness),
        "consumed_receipts": [ref.to_json() for ref in decision.consumed_receipts],
        "exposed_evidence_refs": [
            item.to_json() for item in imported.catalogue if item.label in imported.exposed
        ],
        "provider_manifest_ref": (None if imported.provider_manifest is None
                                  else imported.provider_manifest.ref.to_json()),
        "disclosure_refs": [batch.delivery_receipt_ref.to_json() for batch in imported.disclosures],
        **({} if no_usable_reply is None
           else {"interpretation": NO_USABLE_REPLY, "error_code": no_usable_reply}),
    }
    record = ReviewRecord(
        "assurance-review:"
        + fingerprint({"review_key": bound["review_key"], "turn_ref": imported.turn.ref.to_json()}),
        package.package_id,
        package.purpose,
        package.binding,
        turn["agent_id"],
        turn["turn_id"],
        fingerprint(manifest),
        tuple(outcomes),
        ReviewVerdict(verdict),
    )
    return record, manifest


def raw_package(imported: ImportedReview) -> str:
    # The frozen initial message contains the exact package; its hash was
    # checked by the original invocation writer. No latest reconstruction.
    from ..assurance.review_input import read_initial_materials

    if imported.provider_manifest is None:
        if imported.package_json is None:
            raise AssuranceError("REVIEW_PACKAGE_SOURCE_UNAVAILABLE")
        return imported.package_json
    provider = decode(imported.provider_manifest.body_json, limit=MAX_RECORD_BYTES)
    matches = []
    for row in provider["messages"]:
        if row["kind"] != "user_input":
            continue
        try:
            read_initial_materials(row["message"], imported.binding)
        except AssuranceError:
            continue
        matches.append(decode(row["message"]["content"])["package"])
    if len(matches) != 1:
        raise AssuranceError("REVIEW_PACKAGE_SOURCE_UNAVAILABLE")
    return canonical(matches[0])


@dataclass(frozen=True, slots=True)
class PreparedOfficialReview:
    commit: Any
    tenant_id: str
    identity: UseIdentity
    authority: CurrentAuthority
    imported: ImportedReview
    epochs: EpochSnapshot
    metadata: tuple[ResolvedRef, ...]
    permissions: tuple[tuple[AssuranceRef, CurrentReadPermission], ...]
    blobs: tuple[PreparedBlob, ...]
    check_uses: tuple[PreparedCheckUse, ...]
    check_adapter: Any
    complete: tuple[CompleteRead, ...]
    read_set: tuple[ReadItem, ...]
    prepared_at_ms: int
    not_after_ms: int
    record: ReviewRecord
    manifest_json: str

    def _record_method_blame(self, store: Any) -> None:
        """根终审打回时审阅员写明"是做法本身的错"（阶段 C3）：对照全库先例写的做法，记一条归因。
        程序只记审阅员明确写出的；同一任务只算一次、两个任务即退役，在 method_library 里数。"""
        if str(self.record.purpose) != "MISSION_FINAL" or str(self.record.verdict) not in {"REWORK", "REJECTED"}:
            return
        from .method_library import record_attribution

        for row in decode(self.manifest_json).get("methods") or ():
            if row["at_fault"] and row["based_on"]:
                record_attribution(
                    store, mission_id=self.identity.mission_id, method_ref=row["method_ref"],
                    source_ref=str(self.record.record_id), source_kind="ROOT_REVIEW", reason=row["reason"])

    def require_locked(self, store: Any, record: ReviewRecord) -> None:
        if (
            store is not self.commit.store
            or not store.connection.in_transaction
            or record != self.record
        ):
            raise AssuranceError("REVIEW_IMPORT_TRANSACTION_REQUIRED")
        now_ms = int(store.now * 1000)
        if not self.prepared_at_ms <= now_ms < self.not_after_ms:
            raise AssuranceError("REVIEW_IMPORT_EXPIRED")
        gate = self.commit._assurance_root_gate
        if (
            gate is None
            or gate.require_execution().root_incarnation_id != self.identity.root_incarnation_id
        ):
            raise AssuranceError("ROOT_AUTHORITY_UNAVAILABLE")
        require_epochs_locked(
            store.connection, self.identity.mission_id, self.epochs, now_ms=now_ms
        )
        reader = AssuranceReader(
            store, tenant_id=self.tenant_id, mission_id=self.identity.mission_id
        )
        current = read_imported_review_locked(self.commit, reader, self.imported.classification.ref)
        if current != self.imported:
            raise AssuranceError("RECHECK_REQUIRED")
        if review_subject_stopped(store, current.binding):
            raise AssuranceError("REVIEW_SUBJECT_STOPPED")
        for item in self.metadata:
            if reader.read_exact_metadata(item.ref) != item:
                raise AssuranceError("RECHECK_REQUIRED")
        for ref, permission in self.permissions:
            _require_same_permission(self.authority, self.identity, ref, permission, now_ms)
        for blob in self.blobs:
            blob.require_current_locked(
                reader,
                now_ms=now_ms,
                authorize=lambda ref: (
                    _permission(self.authority, self.identity, ref, now_ms).access
                ),
            )
        checks = _consume_checks(
            self.check_uses, self.check_adapter, self.identity, self.authority, now_ms
        )
        raw = next(blob.data for blob in self.blobs if blob.metadata.ref == _raw_ref(self.imported))
        computed, manifest = _interpret(current, raw, checks)
        if computed != record or canonical(manifest) != self.manifest_json:
            raise AssuranceError("RECHECK_REQUIRED")

    def import_locked(self) -> AssuranceRef:
        store = self.commit.store
        with atomic(store):
            semantic = HtnStore(store)
            existing = semantic.official_review_record(self.record.package_id)
            if existing is not None:
                if existing != self.record:
                    raise AssuranceError("REVIEW_ALREADY_OFFICIAL")
                gate = self.commit._assurance_root_gate
                if (
                    gate is None
                    or gate.require_execution().root_incarnation_id
                    != self.identity.root_incarnation_id
                ):
                    raise AssuranceError("ROOT_AUTHORITY_UNAVAILABLE")
                _permission(
                    self.authority,
                    self.identity,
                    self.imported.classification.ref,
                    int(store.now * 1000),
                )
                # Replays are verified through the immutable import sidecar.
                return read_official_review_binding_locked(
                    self.commit, self.tenant_id, self.record
                ).ref
            self.require_locked(store, self.record)
            semantic.insert_review_record(self.record, official=True, assurance_import=self)
            owner = self.imported.binding.to_json()["subject"]["owner_task_ref"]["id"]
            manifest_hash = semantic.insert_input_manifest(
                self.identity.mission_id, owner, decode(self.manifest_json)
            )
            if manifest_hash != self.record.evidence_manifest_hash:
                raise AssuranceError("REVIEW_EVIDENCE_MANIFEST_MISMATCH")
            self._record_method_blame(store)
            consumed = {
                AssuranceRef.from_json(ref)
                for ref in decode(self.manifest_json)["consumed_receipts"]
            }
            bound = self.imported.binding.to_json()
            side = ReviewRecordBinding.from_json(
                {
                    "schema_version": 2,
                    "mission_id": self.identity.mission_id,
                    "review_key": bound["review_key"],
                    "package_ref": bound["package_ref"],
                    "record_ref": Pin(
                        str(self.record.record_id), 0, fingerprint(self.record.to_json())
                    ).to_json(),
                    "reviewer_agent_id": self.record.reviewer_agent_id,
                    "reviewer_turn_ref": self.imported.turn.ref.to_json(),
                    "raw_output_ref": _raw_ref(self.imported).to_json(),
                    "raw_output_hash": _raw_ref(self.imported).pin.content_hash,
                    "codec_version": REVIEW_CODEC_VERSION,
                    "consumed_check_refs": [
                        use.binding_ref.to_json()
                        for use in self.check_uses
                        if use.binding.execution_ref in consumed
                    ],
                    "exposed_evidence_refs": decode(self.manifest_json)["exposed_evidence_refs"],
                    "evidence_manifest_hash": manifest_hash,
                    "binding_read_set": [item.to_json() for item in self.read_set],
                    "disclosure_refs": decode(self.manifest_json)["disclosure_refs"],
                    "invocation_ordinal": self.imported.invocation.to_json()["ordinal"],
                }
            )
            receipt_id = "assurance-review-import:" + str(self.record.record_id)
            receipt = {
                "mission_id": self.identity.mission_id,
                "record_id": str(self.record.record_id),
                "binding_hash": side.content_hash,
                "classification_ref": self.imported.classification.ref.to_json(),
            }
            self.commit.store.insert_receipt(
                commit_id=receipt_id,
                kind="AssuranceReviewImported",
                subject_id=str(self.record.record_id),
                base_version=0,
                proposal_hash=side.content_hash,
                receipt=receipt,
            )
            AssuranceStore(store)._insert(
                store.connection,
                "assurance_review_record_bindings",
                {
                    "record_id": str(self.record.record_id),
                    "mission_id": self.identity.mission_id,
                    "review_key": bound["review_key"],
                    "source_turn_ref_hash": self.imported.turn.ref.key,
                    "raw_output_hash": _raw_ref(self.imported).pin.content_hash,
                    "evidence_manifest_hash": manifest_hash,
                    "binding_hash": side.content_hash,
                    "binding_json": side.body_json,
                    "import_receipt_id": receipt_id,
                    "created_at_ms": int(store.now * 1000),
                },
                identities=(("record_id",), ("mission_id", "review_key", "source_turn_ref_hash")),
            )
            self.commit._emit(
                "AssuranceReviewImported",
                self.identity.mission_id,
                key=str(self.record.record_id),
                payload=receipt,
            )
            return AssuranceRef("commit_receipt", Pin(receipt_id, 0, fingerprint(receipt)))


def _raw_ref(imported: ImportedReview) -> AssuranceRef:
    return AssuranceRef.from_json(
        decode(imported.turn.body_json)["payload"]["raw_output_ref"], kinds={"source"}
    )


def _consume_checks(
    uses: tuple[PreparedCheckUse, ...],
    adapter: Any,
    identity: UseIdentity,
    authority: CurrentAuthority,
    now_ms: int,
) -> dict[AssuranceRef, CheckResult]:
    results = {}
    for use in uses:
        if use.binding.check_spec_ref in results:
            raise AssuranceError("AMBIGUOUS_REVIEW_CHECK")
        results[use.binding.check_spec_ref] = use.consume_locked(
            adapter, identity=identity, authority=authority, now_ms=now_ms
        )
    return results


def prepare_official_review(
    commit: Any,
    *,
    tenant_id: str,
    classification_ref: AssuranceRef,
    identity: UseIdentity,
    authority: CurrentAuthority,
    cas: Any,
    pins: Mapping[AssuranceRef, str],
    check_uses: tuple[PreparedCheckUse, ...] = (),
    check_adapter: Any = None,
    maximum_blob_bytes: int = 32 * 1024 * 1024,
) -> PreparedOfficialReview:
    store = commit.store
    if store.connection.in_transaction:
        raise AssuranceError("REVIEW_PREPARATION_INSIDE_TRANSACTION")
    reader = AssuranceReader(store, tenant_id=tenant_id, mission_id=identity.mission_id)
    now_ms = int(store.now * 1000)
    with store.read_view():
        imported = read_imported_review_locked(commit, reader, classification_ref)
        body = imported.binding.to_json()
        scope = body["subject"]["completion_scope_ref"]
        if (
            identity.consumer_kind != "REVIEW"
            or identity.consumer_id != body["review_key"]
            or identity.purpose != "ACCEPT"
            or identity.scope_id != review_scope_id(body)
        ):
            raise AssuranceError("REVIEW_IMPORT_IDENTITY")
        if AssuranceStore(store).lane(identity.mission_id) != "ASSURANCE_1_1":
            raise AssuranceError("ASSURANCE_PROFILE_REQUIRED")
        epochs = read_epochs_locked(store.connection, identity.mission_id)
        refs = {
            classification_ref,
            imported.turn.ref,
            _raw_ref(imported),
            AssuranceRef("review_package", Pin.from_json(body["package_ref"])),
            AssuranceRef("requirements", Pin.from_json(body["requirements_ref"])),
            AssuranceRef.from_json(body["criterion_policy_ref"]),
            AssuranceRef.from_json(body["subject"]["target"]),
            AssuranceRef("task", Pin.from_json(body["subject"]["owner_task_ref"])),
        }
        if scope is not None:
            refs.add(AssuranceRef("completion_scope", Pin.from_json(scope)))
        if imported.provider_manifest is not None:
            refs.add(imported.provider_manifest.ref)
        refs.update(item.ref for item in imported.catalogue)
        refs.update(item.ref for item in imported.disclosure_metadata)
        # 解析时就核用途与访问（第 2 批 A10）：授权与身份进读取器，许可随解析结果回来。
        reader = AssuranceReader(
            store, tenant_id=reader.tenant_id, mission_id=reader.mission_id,
            authority=authority, identity=identity,
        )
        metadata = tuple(
            reader.read_exact_metadata(ref, now_ms=now_ms)
            for ref in sorted(refs, key=lambda ref: ref.key)
        )
        permissions = tuple((item.ref, item.permission) for item in metadata)
        complete = read_complete_evidence_snapshot(reader, scope_id=identity.scope_id)
        checks = _consume_checks(check_uses, check_adapter, identity, authority, now_ms)
        allowed = {
            AssuranceRef.from_json(ref)
            for policy in body["check_requirements"]
            for group in policy["any_check_sets"]
            for ref in group
        }
        if not set(checks) <= allowed:
            raise AssuranceError("REVIEW_CHECK_POLICY_MISMATCH")
    blobs, remaining = [], maximum_blob_bytes
    for ref in sorted(
        (ref for ref in refs if ref.kind in {"source", "artifact"}), key=lambda ref: ref.key
    ):
        if ref not in pins:
            raise AssuranceError("LIVE_BLOB_PIN_REQUIRED", ref.pin.id)
        blob = read_pinned_blob(
            reader,
            ref,
            pin_id=pins[ref],
            review_key=body["review_key"],
            cas=cas,
            maximum_bytes=remaining,
            authorize=lambda r: _permission(authority, identity, r, int(store.now * 1000)).access,
            now_ms=lambda: int(store.now * 1000),
        )
        blobs.append(blob)
        remaining -= len(blob.data)
    raw = next(blob.data for blob in blobs if blob.metadata.ref == _raw_ref(imported))
    record, manifest = _interpret(imported, raw, checks)
    reads = [item.read_item for item in metadata] + [item.read_item for item in complete]
    for _, permission in permissions:
        reads.extend((permission.access, permission.policy))
    for use in check_uses:
        reads.extend(use.read_set)
    deadlines = [permission.not_after_ms for _, permission in permissions] + [
        use.not_after_ms for use in check_uses
    ]
    prepared = PreparedOfficialReview(
        commit,
        tenant_id,
        identity,
        authority,
        imported,
        epochs,
        metadata,
        permissions,
        tuple(blobs),
        check_uses,
        check_adapter,
        complete,
        _merge_reads(reads),
        now_ms,
        min(deadlines),
        record,
        canonical(manifest),
    )
    with store.read_view():
        prepared.require_locked(store, record)
    return prepared


def read_official_review_binding_locked(
    commit: Any, tenant_id: str, record: ReviewRecord
) -> ResolvedRef:
    """Authenticate historical transport and sidecar; this grants no current use."""
    reader = AssuranceReader(
        commit.store, tenant_id=tenant_id, mission_id=record.binding.mission_id
    )
    original = reader.read_exact_metadata(
        AssuranceRef("review", Pin(str(record.record_id), 0, fingerprint(record.to_json())))
    )
    if not decode(original.state_witness_json)["official"]:
        raise AssuranceError("REVIEW_NOT_OFFICIAL")
    row = commit.store.connection.execute(
        "SELECT * FROM assurance_review_record_bindings WHERE record_id=?", (str(record.record_id),)
    ).fetchone()
    if row is None:
        raise AssuranceError("REVIEW_RECORD_BINDING_REQUIRED")
    binding = ReviewRecordBinding(row["binding_json"])
    side = binding.to_json()
    receipt = commit.store.get_receipt(row["import_receipt_id"])
    if receipt is None:
        raise AssuranceError("REVIEW_IMPORT_RECEIPT_MISSING")
    receipt_ref = AssuranceRef(
        "commit_receipt", Pin(row["import_receipt_id"], 0, fingerprint(dict(receipt)))
    )
    # 回执的写者与对象由解析器核（第 2 批 A09）
    metadata = reader.read_exact_metadata(
        receipt_ref, receipt_kind="AssuranceReviewImported", receipt_subject=str(record.record_id)
    )
    lifecycle = decode(metadata.state_witness_json)
    imported = read_imported_review_locked(
        commit,
        reader,
        AssuranceRef.from_json(receipt.get("classification_ref"), kinds={"commit_receipt"}),
    )
    if (
        row["binding_hash"] != binding.content_hash
        or lifecycle["base_version"] != 0
        or lifecycle["proposal_hash"] != binding.content_hash
        or receipt.get("binding_hash") != binding.content_hash
        or receipt.get("record_id") != str(record.record_id)
        or receipt.get("mission_id") != reader.mission_id
        or side["record_ref"] != original.ref.pin.to_json()
        or side["mission_id"] != reader.mission_id
        or side["package_ref"] != imported.binding.to_json()["package_ref"]
        or side["review_key"] != imported.binding.to_json()["review_key"]
        or side["reviewer_turn_ref"] != imported.turn.ref.to_json()
        or side["reviewer_agent_id"] != record.reviewer_agent_id
        or side["raw_output_ref"] != _raw_ref(imported).to_json()
        or side["evidence_manifest_hash"] != record.evidence_manifest_hash
        or side["invocation_ordinal"] != imported.invocation.to_json()["ordinal"]
        or side["codec_version"] != REVIEW_CODEC_VERSION
        or side["raw_output_hash"] != _raw_ref(imported).pin.content_hash
        or row["mission_id"] != reader.mission_id
        or row["review_key"] != side["review_key"]
        or row["source_turn_ref_hash"] != imported.turn.ref.key
        or row["raw_output_hash"] != side["raw_output_hash"]
        or row["evidence_manifest_hash"] != side["evidence_manifest_hash"]
    ):
        raise AssuranceError("REVIEW_RECORD_BINDING_MISMATCH")
    evidence = reader.read_exact_metadata(
        AssuranceRef(
            "input_manifest", Pin(record.evidence_manifest_hash, 0, record.evidence_manifest_hash)
        )
    )
    manifest = decode(evidence.body_json)
    if (
        manifest.get("codec_version") != REVIEW_CODEC_VERSION
        or manifest.get("binding_hash") != imported.binding.content_hash
        or manifest.get("turn_ref") != imported.turn.ref.to_json()
        or manifest.get("raw_output_hash") != side["raw_output_hash"]
        or manifest.get("effective_verdict") != str(record.verdict)
        or sorted(
            manifest.get("disclosure_refs", []), key=lambda ref: AssuranceRef.from_json(ref).key
        )
        != side["disclosure_refs"]
        or manifest.get("exposed_evidence_refs") != side["exposed_evidence_refs"]
    ):
        raise AssuranceError("REVIEW_EVIDENCE_MANIFEST_MISMATCH")
    return metadata
