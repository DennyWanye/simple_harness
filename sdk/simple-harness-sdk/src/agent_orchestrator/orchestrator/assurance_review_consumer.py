# SPDX-License-Identifier: Apache-2.0
"""Durable REVIEW import on the original tick, without creating an executor."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..assurance.certificates import UseIdentity
from ..assurance.check_bindings import CheckBinding
from ..assurance.codec import AssuranceError, decode, fingerprint, text
from ..assurance.refs import AssuranceRef, Pin
from ..contracts import Artifact, Event
from ..contracts.resolution import ReviewVerdict
from ..storage.assurance_reads import AssuranceReader, CurrentAuthority
from ..storage.assurance_work import CHECK_PENDING, WorkClaim, WorkTarget, budget_wait_delay_ms
from ..storage.htn_store import HtnStore
from ..storage.store import _event_from_row
from .assurance_check_use import prepare_local_check_use
from .assurance_review_import import (
    prepare_official_review,
    REPAIRABLE_INTERPRETATION_ERRORS,
    read_imported_review_locked,
    read_official_review_binding_locked,
    review_scope_id,
    review_subject_stopped,
)
from .assurance_review_pins import ensure_review_blob_pins
from .assurance_tick import AssuranceWait, PreparedAssuranceWork


#: 一次审阅作废的原因：它开始之后，证据里的一份资料换了版本或被撤销（2026-10-05 真机）。
REVIEW_SOURCE_REPLACED = "REVIEW_SOURCE_REPLACED"
#: 一次终审作废的原因：回复到达时，它审的那个 ``MISSION_FINAL`` 包已不再描述世界（纪元动了、
#: 贡献集动了、资料版本集动了……）。过不过期只由根终审协调器的 ``stale_reasons`` 说了算——同一个
#: 判定，协调器据它重切或判"切包用完"，导入据它作废这份迟到回复；具体的过期码记在回执
#: ``stale_reasons`` 里（第 1 批车道 F，2026-10-06）。
REVIEW_PACKAGE_STALE = "REVIEW_PACKAGE_STALE"

# 审阅这一侧登记的唤醒事件（事件消费表的类别登记，按类型列出，不按前缀猜；事件名不带来权限——
# 唤醒以后准备照常重核来源、授权、纪元、钉住对象与预算）。三类都只唤醒还没做完的同一项导入工作
# （工作键不变，目标升到这条事件），不另起一项、不重解已做完的导入（推后第 1 批 A25）。
#: SOURCE_OR_AUTHORITY_CHANGED：资料换版 / 撤销。
EVIDENCE_CHANGED_EVENT = "AssuranceEvidenceChanged"
#: ACTUAL_CHECK_AVAILABLE：受信检查导入方把一次真实检查绑定到完成范围。
CHECK_BOUND_EVENT = "AssuranceCheckBound"
#: BUSINESS_OR_RUNTIME_SETTLED：调用与预算结清（与收尾消费者登记的同一组名字）；只唤醒卡在预算上的导入。
SETTLED_EVENTS = frozenset({"IntentSettled", "BudgetReleased", "BudgetTailReleased"})
WAKE_EVENTS = frozenset({EVIDENCE_CHANGED_EVENT, CHECK_BOUND_EVENT}) | SETTLED_EVENTS

_UNFINISHED_IMPORTS = (
    "SELECT w.work_key,c.commit_id,c.receipt_json,b.subject_hash,b.binding_json "
    "FROM assurance_pending_work w "
    "JOIN assurance_review_invocations i ON i.mission_id=w.mission_id "
    "AND w.work_key='review-import:'||i.review_key||':'||i.ordinal "
    "JOIN assurance_review_bindings b ON b.mission_id=i.mission_id AND b.review_key=i.review_key "
    "JOIN commit_receipts c ON c.subject_id=i.dispatch_intent_id "
    "AND c.kind IN ('AssuranceReviewClassified','AssuranceReviewFormatRejected') "
    "JOIN events e ON e.mission_id=i.mission_id AND e.type=c.kind AND json_extract(e.payload_json,'$.classification_receipt_ref.pin.id')=c.commit_id "
    "WHERE w.mission_id=? AND w.consumer='REVIEW' AND w.state IN ('PENDING','RUNNING','WAITING') "
    "AND e.seq<? {extra} ORDER BY w.work_key LIMIT 257"
)


def checks_pending(body: Mapping[str, Any], bound: Any) -> tuple[str, ...]:
    """这份内容审阅还在等哪几条必检准则的检查（每个检查组都还缺绑定）。

    只有对象是一份结果的审阅才会等：结果有受信检查写方（本地检查 / 执行器检查导入），检查绑定迟早
    写进库；终审、组合等对象没有检查写方，缺的检查照真值表算 UNKNOWN，不等。这是"绑定在不在"的
    秩序事实，不是内容判断。"""
    if body["subject"]["target"]["kind"] != "result" or body["subject"]["completion_scope_ref"] is None:
        return ()
    return tuple(
        str(policy["criterion_id"])
        for policy in body["check_requirements"]
        if policy["mode"] == "CHECKED"
        and not any(all(AssuranceRef.from_json(ref) in bound for ref in group)
                    for group in policy["any_check_sets"])
    )


def replaced_review_source(store: Any, binding: Any) -> str | None:
    """The path of a source in this review's evidence that is no longer the one in force
    (superseded or revoked since the review was cut), or None.  A fact read from the source
    rows: the reviewer judged against text that is not the current reference material."""
    body = binding.to_json()
    for item in body["evidence_catalogue"]:
        ref = AssuranceRef.from_json(item["ref"])
        if ref.kind != "source":
            continue
        row = store.connection.execute(
            "SELECT superseded_by, revoked FROM sources WHERE mission_id=? AND path=? AND version_hash=?",
            (body["mission_id"], ref.pin.id, ref.pin.content_hash)).fetchone()
        if row is None or row["superseded_by"] is not None or row["revoked"]:
            return str(ref.pin.id)
    return None


class AssuranceReviewConsumer:
    def __init__(
        self,
        commit: Any,
        *,
        tenant_id: str,
        principal_id: str,
        authority: CurrentAuthority,
        cas: Any,
        check_adapter: Any,
        root_review: Callable[[str], Any] | None = None,
    ) -> None:
        self.commit = commit
        self.store = commit.store
        self.tenant_id = text(tenant_id)
        self.principal_id = text(principal_id)
        self.authority = authority
        self.cas = cas
        self.check_adapter = check_adapter
        #: 部署自己的根终审协调器（按 mission 建，与主循环 ``_advance_root_review`` 用的是同一个
        #: 构造）；终审回复导入前问它这个包过没过期，不另抄一份判定。
        self.root_review = root_review

    def stale_root_package(self, body: Mapping[str, Any]) -> tuple[str, ...]:
        """这份审阅绑定指向的 ``MISSION_FINAL`` 包为什么已不再描述世界；非终审或仍新鲜为空。

        答案来自 :meth:`RootReviewCoordinator.stale_reasons`，与协调器决定重切 / 切包用完所依据
        的是同一个函数。没有协调器可问的部署不会切出终审包；真遇到就是组装错了，报出来。
        """
        if body["subject"]["purpose"] != "MISSION_FINAL":
            return ()
        coordinator = None if self.root_review is None else self.root_review(body["mission_id"])
        if coordinator is None:
            raise AssuranceError("REVIEW_ROOT_COORDINATOR_UNAVAILABLE")
        package = HtnStore(self.store).get_review_package(body["package_ref"]["id"])
        return tuple(str(item) for item in coordinator.stale_reasons(body["mission_id"], package))

    def _woken(self, event: Event) -> list[Any]:
        """这条唤醒事件唤醒哪几项还没做完的导入工作（工作箱里同一项，按原键）。"""
        if event.type == EVIDENCE_CHANGED_EVENT:
            # Wake the same unfinished logical work, including a capped wait.
            # Its first-start time/tries remain owned by WorkStore. Completed
            # historical imports are not reinterpreted when a source changes.
            extra, args = "", ()
        elif event.type == CHECK_BOUND_EVENT:
            # 检查晚到：只唤醒对象、完成范围都对得上、且检查要求里有这项检查的导入。
            binding = self.store.connection.execute(
                "SELECT binding_json FROM assurance_check_bindings WHERE mission_id=? AND check_binding_id=?",
                (event.mission_id, AssuranceRef.from_json(
                    event.payload.get("check_binding_ref"), kinds={"check_binding"}).pin.id),
            ).fetchone()
            if binding is None:
                raise AssuranceError("REVIEW_WORK_SOURCE_MISSING", "check binding")
            check = CheckBinding.from_json(decode(binding[0]))
            extra = ("AND b.subject_hash=? "
                     "AND json_extract(b.binding_json,'$.subject.completion_scope_ref.content_hash')=?")
            args = (check.subject_hash, check.scope_hash)
        else:
            # 结清：只唤醒卡在预算上的导入；已被这条事件唤醒、正在准备的那一项也算（准备时重核身份）。
            extra = "AND (w.wait_reason='BUDGET_WAIT' OR w.trigger_event_id=?)"
            args = (event.id,)
        rows = self.store.connection.execute(
            _UNFINISHED_IMPORTS.format(extra=extra), (event.mission_id, event.seq, *args)
        ).fetchall()
        if len(rows) > 256 or len({row["work_key"] for row in rows}) != len(rows):
            raise AssuranceError("REVIEW_WAKEUP_INVENTORY_INCOMPLETE")
        if event.type == CHECK_BOUND_EVENT:
            rows = [row for row in rows if check.check_spec_ref in {
                AssuranceRef.from_json(ref)
                for policy in decode(row["binding_json"])["check_requirements"]
                for group in policy["any_check_sets"] for ref in group}]
        return rows

    def classify(self, event: Event) -> tuple[WorkTarget, ...]:
        if event.type in WAKE_EVENTS:
            rows = self._woken(event)
            return tuple(
                WorkTarget(
                    row["work_key"],
                    fingerprint(
                        {
                            "classification_ref": AssuranceRef(
                                "commit_receipt",
                                Pin(row["commit_id"], 0, fingerprint(decode(row["receipt_json"]))),
                            ).to_json(),
                            "change_event": fingerprint(event.to_json()),
                        }
                    ),
                )
                for row in rows
            )
        expected = {
            "AssuranceReviewClassified": {"READY_FOR_CURRENT_REVIEW", "TURN_FAILED"},
            "AssuranceReviewFormatRejected": {"FORMAT_INVALID"},
        }
        if (
            event.type not in expected
            or event.payload.get("classification") not in expected[event.type]
        ):
            return ()
        ref = AssuranceRef.from_json(
            event.payload.get("classification_receipt_ref"), kinds={"commit_receipt"}
        )
        key = (
            "review-import:"
            + text(event.payload.get("review_key"))
            + ":"
            + str(event.payload.get("invocation_ordinal"))
        )
        return (WorkTarget(key, ref.key),)

    async def prepare(self, claim: WorkClaim) -> PreparedAssuranceWork | AssuranceWait:
        """No Provider call: source, pins, checks, interpretation, final commit."""
        if claim.consumer != "REVIEW":
            raise AssuranceError("REVIEW_WORK_IDENTITY")
        reader = AssuranceReader(self.store, tenant_id=self.tenant_id, mission_id=claim.mission_id)
        with self.store.read_view():
            row = self.store.connection.execute(
                "SELECT * FROM events WHERE event_id=? AND mission_id=?",
                (claim.trigger_event_id, claim.mission_id),
            ).fetchone()
            if row is None:
                raise AssuranceError("REVIEW_WORK_SOURCE_MISSING")
            event = _event_from_row(row)
            if (
                WorkTarget(claim.work_key, claim.target_fingerprint) not in self.classify(event)
                or event.seq != claim.target_epoch
            ):
                raise AssuranceError("REVIEW_WORK_IDENTITY")
            if event.type in WAKE_EVENTS:
                sources = self.store.connection.execute(
                    "SELECT e.* FROM assurance_review_invocations i "
                    "JOIN commit_receipts c ON c.subject_id=i.dispatch_intent_id "
                    "AND c.kind IN ('AssuranceReviewClassified','AssuranceReviewFormatRejected') "
                    "JOIN events e ON e.mission_id=i.mission_id AND e.type=c.kind AND json_extract(e.payload_json,'$.classification_receipt_ref.pin.id')=c.commit_id "
                    "WHERE i.mission_id=? AND 'review-import:'||i.review_key||':'||i.ordinal=?",
                    (claim.mission_id, claim.work_key),
                ).fetchall()
                if len(sources) != 1:
                    raise AssuranceError("REVIEW_WORK_SOURCE_MISSING")
                event = _event_from_row(sources[0])
            ref = AssuranceRef.from_json(event.payload["classification_receipt_ref"])
            unusable_second = (event.type == "AssuranceReviewFormatRejected"
                               and event.payload.get("invocation_ordinal") == 2)
            if (event.type == "AssuranceReviewFormatRejected"
                    or event.payload.get("classification") == "TURN_FAILED") and not unusable_second:
                # First unusable reply: asked again in a fresh session.  A call that never
                # came back (TURN_FAILED) stays an infrastructure matter on both calls.
                return self._prepare_format_repair(reader, ref)
            # The second call's reply still cannot be decoded: imported below as "no usable
            # reply" — an INCONCLUSIVE record, the same exit as "cannot tell" (阶段 C 第 3 条).
            imported = read_imported_review_locked(self.commit, reader, ref)
            body = imported.binding.to_json()
            existing = HtnStore(self.store).official_review_record(body["package_ref"]["id"])
            if existing is not None:
                # Recovery after effect commit/before ACK reads the original
                # immutable receipt. Do not recompute a historical judgement.
                source = read_official_review_binding_locked(self.commit, self.tenant_id, existing)
                if decode(source.body_json)["classification_ref"] != ref.to_json():
                    return self._prepare_late(
                        reader, imported, "OTHER_TURN_ALREADY_OFFICIAL", source
                    )

                def replay() -> AssuranceRef:
                    self.commit._assurance_root_gate.require_execution()
                    actual = read_official_review_binding_locked(
                        self.commit, self.tenant_id, existing
                    )
                    if actual != source:
                        raise AssuranceError("RECHECK_REQUIRED")
                    return actual.ref

                return PreparedAssuranceWork(replay)
            if review_subject_stopped(self.store, imported.binding):
                return self._prepare_late(reader, imported, "REVIEW_SUBJECT_STOPPED")
            if replaced_review_source(self.store, imported.binding) is not None:
                # 审阅在途时它证据里的资料换了版本 / 被撤销：这次审阅看的不是现行资料，作废。
                return self._prepare_late(reader, imported, REVIEW_SOURCE_REPLACED)
            if self.stale_root_package(body):
                # 终审在途时世界动了（协调器正要重切或已判切包用完）：这份回复说的是一个已经
                # 不存在的世界，作废；不写正式记录、不形成根结论，费用照记。随后由协调器按现状
                # 重切（新包的回复才算数）或停在"切包用完"。
                return self._prepare_late(reader, imported, REVIEW_PACKAGE_STALE)
            scope = body["subject"]["completion_scope_ref"]
            identity = UseIdentity(
                claim.mission_id,
                "REVIEW",
                body["review_key"],
                review_scope_id(body),
                self.principal_id,
                "ACCEPT",
                self.commit._assurance_root_gate.require_execution().root_incarnation_id,
            )
            required = {
                AssuranceRef.from_json(ref)
                for policy in body["check_requirements"]
                for group in policy["any_check_sets"]
                for ref in group
            }
            # A pre-Scope METHOD_PLAN review has no Scope-bound check bindings:
            # its policy domain is the planning subject and only SEMANTIC checks
            # apply, so no executor/local check is selected for it.
            candidates = (
                []
                if scope is None
                else self.store.connection.execute(
                    "SELECT * FROM assurance_check_bindings WHERE mission_id=? AND subject_hash=? "
                    "AND json_extract(binding_json,'$.scope_hash')=? "
                    "ORDER BY created_at_ms,check_binding_id LIMIT 257",
                    (
                        claim.mission_id,
                        body["subject"]["target"]["pin"]["content_hash"],
                        scope["content_hash"],
                    ),
                ).fetchall()
            )
            if len(candidates) > 256:
                return AssuranceWait("RECHECK_REQUIRED", int(self.store.now * 1000))
            selected = {}
            for row in candidates:
                check = CheckBinding.from_json(decode(row["binding_json"]))
                if check.check_spec_ref in required:
                    if row["binding_hash"] != fingerprint(check.to_json()):
                        raise AssuranceError("CHECK_BINDING_SOURCE_MISMATCH")
                    # Deterministic latest actual run for this exact Spec/subject;
                    # a newer ERROR never falls back to an older PASS.
                    selected[check.check_spec_ref] = (
                        AssuranceRef(
                            "check_binding", Pin(row["check_binding_id"], 0, row["binding_hash"])
                        ),
                        check,
                    )
            if self.check_adapter is not None and checks_pending(body, selected):
                # 必检的检查还没绑定进库：等它，不拿缺检查的 UNKNOWN 导入（那会判不下来、再调一次
                # 模型）。检查绑定事件唤醒这同一项工作，回复照旧是这一份（推后第 1 批 A25）。
                now_ms = int(self.store.now * 1000)
                return AssuranceWait(CHECK_PENDING, now_ms + budget_wait_delay_ms(claim.tries))
            raw_ref = AssuranceRef.from_json(
                decode(imported.turn.body_json)["payload"]["raw_output_ref"]
            )
            blobs = {raw_ref} | {
                item.ref for item in imported.catalogue if item.ref.kind in {"artifact", "source"}
            }
            for _, check in selected.values():
                blobs.update(check.evidence_refs)
                if check.execution_ref.kind not in {"local_check_receipt", "execution_receipt"}:
                    continue
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
                            "artifact", Pin(artifact.id, artifact.version, artifact.content_hash)
                        )
                    )
        pins = ensure_review_blob_pins(
            self.commit, tenant_id=self.tenant_id, binding=imported.binding, refs=blobs
        )
        checks = []
        try:
            for ref, check in selected.values():
                if (
                    check.execution_ref.kind not in {"local_check_receipt", "execution_receipt"}
                    or self.check_adapter is None
                ):
                    # Unsupported sources remain UNKNOWN; no synthesized successful
                    # check or retry of a potentially effectful tool.  An executor
                    # check (code_test) is used from its recorded receipt, never re-run.
                    continue
                try:
                    prepared = prepare_local_check_use(
                        self.check_adapter,
                        binding_ref=ref,
                        result_ref=AssuranceRef.from_json(body["subject"]["target"]),
                        completion_scope=AssuranceRef("completion_scope", Pin.from_json(scope)),
                        identity=identity,
                        review_key=body["review_key"],
                        pins=pins,
                        authority=self.authority,
                        maximum_blob_bytes=32 * 1024 * 1024,
                    )
                except AssuranceError as error:
                    if error.code == "CHECK_USE_EXPIRED":
                        continue
                    raise
                checks.append(prepared)
            prepared = prepare_official_review(
                self.commit,
                tenant_id=self.tenant_id,
                classification_ref=ref_from_event(event),
                identity=identity,
                authority=self.authority,
                cas=self.cas,
                pins=pins,
                check_uses=tuple(checks),
                check_adapter=self.check_adapter,
            )
            if (
                prepared.record.verdict is ReviewVerdict.INCONCLUSIVE
                and imported.invocation.to_json()["ordinal"] == 1
            ):
                # 2026-09-30：第一次就"判不下来"不当正式结论，换一个新会话独立复审一次。
                return self._prepare_second_opinion(reader, imported, event)
            return PreparedAssuranceWork(prepared.import_locked)
        except AssuranceError as error:
            if error.code not in REPAIRABLE_INTERPRETATION_ERRORS | {"POLICY_CATALOGUE_MISMATCH"}:
                # Missing/temporarily denied evidence is not an immutable model
                # error. The tick retains it under the same persistent retry cap.
                raise
            # Immutable interpretation failure is an explicit original receipt.  (A
            # repairable one on the second call never reaches here: ``_interpret`` reads
            # it as "no usable reply" and the record is INCONCLUSIVE.)
            error_code = error.code
            if (
                error_code in REPAIRABLE_INTERPRETATION_ERRORS
                and imported.invocation.to_json()["ordinal"] == 1
                and _format_retries(self.store, claim.mission_id) == 1
            ):
                # 2026-09-26 Host run: the root's final review answered ACCEPT but
                # cited one label it had only seen in a listing.  Rejecting that as
                # final left a Mission whose six steps were all accepted with no
                # review and nothing to do.  It is the reviewer's mistake in the
                # reply, so it gets the one second invocation a malformed reply gets,
                # told exactly what was wrong; the second reply is judged as strictly.
                return self._prepare_interpretation_repair(reader, imported, event, error_code)

            def rejected() -> AssuranceRef:
                self.commit._assurance_root_gate.require_execution()
                if (
                    read_imported_review_locked(self.commit, reader, ref_from_event(event))
                    != imported
                ):
                    raise AssuranceError("RECHECK_REQUIRED")
                receipt = {
                    "mission_id": claim.mission_id,
                    "review_key": body["review_key"],
                    "classification_ref": ref_from_event(event).to_json(),
                    "reason": error_code,
                }
                receipt_id = "assurance-review-rejected:" + fingerprint(receipt)
                self.store.insert_receipt(
                    commit_id=receipt_id,
                    kind="AssuranceReviewImportRejected",
                    subject_id=body["review_key"],
                    base_version=0,
                    proposal_hash=fingerprint(receipt),
                    receipt=receipt,
                )
                self.commit._emit(
                    "AssuranceReviewImportRejected",
                    claim.mission_id,
                    key=receipt_id,
                    payload=receipt,
                )
                return AssuranceRef("commit_receipt", Pin(receipt_id, 0, fingerprint(receipt)))

            return PreparedAssuranceWork(rejected, rejected=True)

    def _prepare_late(self, reader, imported, reason, official=None) -> PreparedAssuranceWork:
        def late() -> AssuranceRef:
            self.commit._assurance_root_gate.require_execution()
            if (
                read_imported_review_locked(self.commit, reader, imported.classification.ref)
                != imported
            ):
                raise AssuranceError("RECHECK_REQUIRED")
            body = imported.binding.to_json()
            stale: tuple[str, ...] = ()
            if official is not None:
                record = HtnStore(self.store).official_review_record(body["package_ref"]["id"])
                if (
                    record is None
                    or read_official_review_binding_locked(self.commit, self.tenant_id, record)
                    != official
                ):
                    raise AssuranceError("RECHECK_REQUIRED")
            elif reason == REVIEW_SOURCE_REPLACED:
                if replaced_review_source(self.store, imported.binding) is None:
                    raise AssuranceError("RECHECK_REQUIRED")
            elif reason == REVIEW_PACKAGE_STALE:
                stale = self.stale_root_package(body)
                if not stale:
                    raise AssuranceError("RECHECK_REQUIRED")
            elif not review_subject_stopped(self.store, imported.binding):
                raise AssuranceError("RECHECK_REQUIRED")
            receipt = {
                "mission_id": reader.mission_id,
                "review_key": body["review_key"],
                "classification_ref": imported.classification.ref.to_json(),
                "turn_ref": imported.turn.ref.to_json(),
                "reason": reason,
                "existing_official_receipt_ref": None
                if official is None
                else official.ref.to_json(),
                **({"stale_reasons": list(stale)} if reason == REVIEW_PACKAGE_STALE else {}),
            }
            digest = fingerprint(receipt)
            receipt_id = "assurance-review-late:" + digest
            if self.store.get_receipt(receipt_id) is None:
                self.store.insert_receipt(
                    commit_id=receipt_id,
                    kind="AssuranceReviewLateTurn",
                    subject_id=body["review_key"],
                    base_version=0,
                    proposal_hash=digest,
                    receipt=receipt,
                )
                self.commit._emit(
                    "AssuranceReviewLateTurn", reader.mission_id, key=receipt_id, payload=receipt
                )
            return AssuranceRef("commit_receipt", Pin(receipt_id, 0, digest))

        return PreparedAssuranceWork(late)

    def _prepare_second_opinion(
        self, reader: AssuranceReader, imported: Any, event: Event
    ) -> PreparedAssuranceWork:
        """The first reviewer could not decide: ask a second one, in a fresh session.

        The committed INCONCLUSIVE reply is not imported as the official record;
        the second invocation resends the frozen request unchanged (no verdict of
        the first reviewer leaks in) and its reply is imported as any first reply
        would be — ACCEPT, REWORK or INCONCLUSIVE again, which then goes to a person.
        """
        invocation = imported.invocation.to_json()
        body = {
            "mission_id": reader.mission_id,
            "review_key": invocation["review_key"],
            "intent_id": invocation["dispatch_intent_id"],
            "invocation_ordinal": 1,
            "classification": "SECOND_OPINION",
            "error_code": None,
            "classification_ref": ref_from_event(event).to_json(),
            "turn_ref": imported.turn.ref.to_json(),
        }
        digest = fingerprint(body)
        receipt_id = "assurance-review-second-opinion:" + body["intent_id"]

        def second_opinion() -> AssuranceRef:
            self.commit._assurance_root_gate.require_execution()
            if read_imported_review_locked(self.commit, reader, ref_from_event(event)) != imported:
                raise AssuranceError("RECHECK_REQUIRED")
            old = self.store.get_receipt(receipt_id)
            if old is not None and dict(old) != body:
                raise AssuranceError("IMMUTABLE_IDENTITY_CONFLICT")
            if old is None:
                self.store.insert_receipt(
                    commit_id=receipt_id,
                    kind=SECOND_OPINION_REQUESTED,
                    subject_id=body["intent_id"],
                    base_version=0,
                    proposal_hash=digest,
                    receipt=body,
                )
                self.commit._emit(SECOND_OPINION_REQUESTED, reader.mission_id, key=receipt_id,
                                  payload=body)
            second = self.commit.ensure_assurance_format_repair(
                tenant_id=self.tenant_id,
                prior_failure=AssuranceRef("commit_receipt", Pin(receipt_id, 0, digest)),
            )
            return AssuranceRef.from_json(
                second.to_json()["source_receipt_ref"], kinds={"commit_receipt"}
            )

        return PreparedAssuranceWork(second_opinion)

    def _prepare_interpretation_repair(
        self, reader: AssuranceReader, imported: Any, event: Event, error_code: str
    ) -> PreparedAssuranceWork:
        invocation = imported.invocation.to_json()
        body = {
            "mission_id": reader.mission_id,
            "review_key": invocation["review_key"],
            "intent_id": invocation["dispatch_intent_id"],
            "invocation_ordinal": 1,
            "classification": "INTERPRETATION_INVALID",
            "error_code": error_code,
            "classification_ref": ref_from_event(event).to_json(),
            "turn_ref": imported.turn.ref.to_json(),
        }
        digest = fingerprint(body)
        receipt_id = "assurance-review-interpretation:" + digest

        def repair() -> AssuranceRef:
            self.commit._assurance_root_gate.require_execution()
            if read_imported_review_locked(self.commit, reader, ref_from_event(event)) != imported:
                raise AssuranceError("RECHECK_REQUIRED")
            old = self.store.get_receipt(receipt_id)
            if old is not None and dict(old) != body:
                raise AssuranceError("IMMUTABLE_IDENTITY_CONFLICT")
            if old is None:
                self.store.insert_receipt(
                    commit_id=receipt_id,
                    kind=INTERPRETATION_REJECTED,
                    subject_id=body["intent_id"],
                    base_version=0,
                    proposal_hash=digest,
                    receipt=body,
                )
                self.commit._emit(INTERPRETATION_REJECTED, reader.mission_id, key=receipt_id,
                                  payload=body)
            repaired = self.commit.ensure_assurance_format_repair(
                tenant_id=self.tenant_id,
                prior_failure=AssuranceRef("commit_receipt", Pin(receipt_id, 0, digest)),
            )
            return AssuranceRef.from_json(
                repaired.to_json()["source_receipt_ref"], kinds={"commit_receipt"}
            )

        return PreparedAssuranceWork(repair)

    def _prepare_format_repair(
        self, reader: AssuranceReader, ref: AssuranceRef
    ) -> PreparedAssuranceWork:
        from .assurance_review_transport import read_review_invocation_locked

        metadata = reader.read_exact_metadata(ref)
        source, row = decode(metadata.body_json), decode(metadata.state_witness_json)
        invocation, binding = read_review_invocation_locked(
            self.commit, reader, source.get("intent_id")
        )
        value = invocation.to_json()
        second_sources = {"FORMAT_INVALID": "AssuranceReviewFormatRejected",
                          "TURN_FAILED": "AssuranceReviewClassified"}
        if (
            second_sources.get(str(source.get("classification"))) != row["kind"]
            or row["subject_id"] != source.get("intent_id")
            or row["base_version"] != 0
            or row["proposal_hash"] != fingerprint(source)
            or source.get("mission_id") != reader.mission_id
            or source.get("review_key") != value["review_key"]
            or source.get("invocation_ordinal") != value["ordinal"]
        ):
            raise AssuranceError("REVIEW_REPAIR_SOURCE_INVALID")
        if value["ordinal"] == 1:

            def repair() -> AssuranceRef:
                repaired = self.commit.ensure_assurance_format_repair(
                    tenant_id=self.tenant_id, prior_failure=ref
                )
                return AssuranceRef.from_json(
                    repaired.to_json()["source_receipt_ref"], kinds={"commit_receipt"}
                )

            return PreparedAssuranceWork(repair)

        def exhausted() -> AssuranceRef:
            self.commit._assurance_root_gate.require_execution()
            if reader.read_exact_metadata(ref) != metadata:
                raise AssuranceError("RECHECK_REQUIRED")
            body = {
                "mission_id": reader.mission_id,
                "review_key": value["review_key"],
                "classification_ref": ref.to_json(),
                "reason": ("REVIEW_FORMAT_REPAIR_EXHAUSTED"
                           if source.get("classification") == "FORMAT_INVALID"
                           else "REVIEW_TURN_RETRY_EXHAUSTED"),
            }
            receipt_id = "assurance-review-format-exhausted:" + binding.to_json()["review_key"]
            old = self.store.get_receipt(receipt_id)
            if old is not None and dict(old) != body:
                raise AssuranceError("IMMUTABLE_IDENTITY_CONFLICT")
            if old is None:
                self.store.insert_receipt(
                    commit_id=receipt_id,
                    kind="AssuranceReviewFormatExhausted",
                    subject_id=value["review_key"],
                    base_version=0,
                    proposal_hash=fingerprint(body),
                    receipt=body,
                )
                self.commit._emit(
                    "AssuranceReviewFormatExhausted",
                    reader.mission_id,
                    key=receipt_id,
                    payload=body,
                )
            return AssuranceRef("commit_receipt", Pin(receipt_id, 0, fingerprint(body)))

        return PreparedAssuranceWork(exhausted, rejected=True)


INTERPRETATION_REJECTED = "AssuranceReviewInterpretationRejected"
SECOND_OPINION_REQUESTED = "AssuranceReviewSecondOpinionRequested"


def _format_retries(store: Any, mission_id: str) -> int | None:
    from ..assurance.policy import AssurancePolicy

    row = store.connection.execute(
        "SELECT policy_json FROM assurance_mission_bindings WHERE mission_id=?", (mission_id,)
    ).fetchone()
    return None if row is None else AssurancePolicy.from_json(decode(row[0])).format_retries


def ref_from_event(event: Event) -> AssuranceRef:
    return AssuranceRef.from_json(
        event.payload["classification_receipt_ref"], kinds={"commit_receipt"}
    )
