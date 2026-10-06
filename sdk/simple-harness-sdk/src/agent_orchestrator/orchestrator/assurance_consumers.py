# SPDX-License-Identifier: Apache-2.0
"""VALIDITY / CLOSEOUT / NOTIFY consumers on the original Assurance tick.

Each adapter classifies original durable Events into stable work keys and
prepares a bounded, transaction-free computation whose ``commit`` re-reads the
same facts under the final lock and writes one original receipt. None of them
runs a model, opens a pool, releases a hold or changes a Mission status: the
unique final writer (spec §7.1, handoff item 7) is installed separately and
consumes a READY closeout; NOTIFY only transports ``{mission_id, event_id,
state_version}`` (spec §7.3), never a report body.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..assurance.codec import AssuranceError, canonical, decode, fields, fingerprint, integer, text
from .assurance_recheck import (
    EVIDENCE_STALE,
    certificate_expired,
    changed_items,
    live_usable_certificates,
    stale_certificates,
)
from ..assurance.expiry import EXPIRY_EVENT, classify_expiry, validity_work_key
from ..assurance.refs import AssuranceRef, Pin
from ..contracts import Event
from ..contracts.semantic_base import content_hash_of
from ..storage.assurance_reads import clock_discontinuous, read_epochs_locked
from ..storage.assurance_store import AssuranceStore
from ..storage.assurance_work import WorkClaim, WorkTarget, atomic
from ..storage.htn_store import HtnStore
from ..storage.store import _event_from_row
from .assurance_final_writer import notification_body, notification_work_target, recorded_judgment
from .assurance_tick import AssuranceWait, PreparedAssuranceWork

#: 收尾原因：有资料变更还没问完规划器。
SOURCE_CHANGE_OPEN = "SOURCE_CHANGE_OPEN"
NOTIFICATION_EVENT = "AssuranceStatusNotificationRequested"
VALIDITY_CHECKED_EVENT = "AssuranceUseValidityChecked"
CLOSEOUT_EVENT = "AssuranceCloseoutEvaluated"
UPPER_BOUND_EVENT = "ReservationCountedAtUpperBound"  # 2026-09-26 user decision
NOTIFIED_EVENT = "AssuranceStatusNotified"
ACTIVATION_EVENT = "AssuranceProfileActivated"

# Events whose appearance can change the closeout answer (event-consumer-map
# classes SOURCE_OR_AUTHORITY_CHANGED / BUSINESS_OR_RUNTIME_SETTLED). Listing
# them here is the AS-0 adapter registration; nothing is inferred from a prefix.
CLOSEOUT_SOURCE_EVENTS = frozenset(
    {
        "AssuranceEvidenceChanged",
        "GoalResolutionCommitted",
        "AcceptanceCommitted",
        "OperationOutcomeAccepted",
        "IntentSettled",
        "BudgetReleased",
        "BudgetTailReleased",
        "AssuranceUseCertified",
        "AssuranceLocalCheckImported",
        "AssuranceExecutorCheckImported",
        "MissionCriteriaJudged",
        "MissionSuccessJudged",
        "AssuranceCloseoutRequested",
        "MissionCompleted",
        "MissionFailed",
        "MissionCancelled",
        "ActionFailed",
        "ActionCancelled",
        "AttemptCancelled",
        "TaskCompleted",
        "TaskFailed",
        "TaskCancelled",
        # Resumed under new code (plan D9-4'): readiness is derived, so it is
        # recomputed once by the running interpreter — a Mission left DRAINING by
        # older code must not wait for an event that will never come.
        "PolicyInterpreterDrift",
    }
)
VALIDITY_SOURCE_EVENTS = frozenset(
    {"AssuranceEvidenceChanged", "AssuranceLocalCheckImported", "AssuranceExecutorCheckImported"}
)
# Own outputs: DIAGNOSTIC_ONLY for every consumer, never re-ingested as work.
DIAGNOSTIC_EVENTS = frozenset({VALIDITY_CHECKED_EVENT, CLOSEOUT_EVENT, NOTIFIED_EVENT})

MAX_WAKE_ROWS = 256


def _receipt_ref(
    store: Any, receipt_id: str, kind: str, subject_id: str, body: dict
) -> AssuranceRef:
    digest = fingerprint(body)
    existing = store.get_receipt(receipt_id)
    if existing is not None:
        if dict(existing) != body:
            raise AssuranceError("IMMUTABLE_IDENTITY_CONFLICT", receipt_id)
        return AssuranceRef("commit_receipt", Pin(receipt_id, 0, digest))
    store.insert_receipt(
        commit_id=receipt_id,
        kind=kind,
        subject_id=subject_id,
        base_version=0,
        proposal_hash=digest,
        receipt=body,
    )
    return AssuranceRef("commit_receipt", Pin(receipt_id, 0, digest))


def _trigger(store: Any, claim: WorkClaim) -> Event:
    row = store.connection.execute(
        "SELECT * FROM events WHERE event_id=? AND mission_id=?",
        (claim.trigger_event_id, claim.mission_id),
    ).fetchone()
    if row is None:
        raise AssuranceError("SOURCE_UNAVAILABLE", "work trigger event")
    event = _event_from_row(row)
    if event.seq != claim.target_epoch:
        raise AssuranceError("WORK_TRIGGER_IDENTITY")
    return event


def _trigger_json(event: Event) -> dict:
    return {"event_id": event.id, "seq": event.seq, "hash": fingerprint(event.to_json())}


class _ConsumerBase:
    def __init__(self, commit: Any, *, tenant_id: str, consumer: str) -> None:
        self.commit = commit
        self.store = commit.store
        self.tenant_id = text(tenant_id)
        self.consumer = consumer

    def _root(self) -> str:
        gate = self.commit._assurance_root_gate
        if gate is None:
            raise AssuranceError("ROOT_AUTHORITY_UNAVAILABLE")
        return gate.require_execution().root_incarnation_id

    def _require_mission(self, mission_id: str) -> Any:
        mission = self.store.get_mission(mission_id)
        if mission is None or mission.tenant_id != self.tenant_id:
            raise AssuranceError("ASSURANCE_TENANT_MISMATCH")
        if AssuranceStore(self.store).lane(mission_id) != "ASSURANCE_1_1":
            raise AssuranceError("ASSURANCE_PROFILE_REQUIRED")
        return mission

    def _check_claim(self, claim: WorkClaim, event: Event, *, exact: bool = True) -> None:
        if claim.consumer != self.consumer:
            raise AssuranceError(self.consumer + "_WORK_IDENTITY")
        targets = self.classify(event)
        if exact:
            found = WorkTarget(claim.work_key, claim.target_fingerprint) in targets
        else:
            found = claim.work_key in {target.work_key for target in targets}
        if not found:
            raise AssuranceError(self.consumer + "_WORK_IDENTITY")

    def classify(self, event: Event) -> tuple[WorkTarget, ...]:  # pragma: no cover - abstract
        raise NotImplementedError


# ------------------------------------------------------------------ VALIDITY
class AssuranceValidityConsumer(_ConsumerBase):
    """Timing owner for issued use certificates (spec §8.4).

    ``AssuranceExpiry.emit_due`` (inside the tick) turns each USABLE certificate
    boundary into one ``AssuranceUseExpiryDue`` event; a source/authority change
    wakes every live certificate consumer of the Mission. The work re-reads the
    exact certificate and records whether it is still CURRENT or STALE (with
    the reasons EXPIRED / SOURCE_CHANGED / ROOT_CHANGED / SUPERSEDED /
    TIME_DISCONTINUITY). STALE is an observation, not a revocation: a consumed
    certificate is history, and any new use recomputes its own candidate. The
    worker never extends a deadline, never re-issues a certificate and never
    repairs a consumer's stored use.
    """

    def __init__(self, commit: Any, *, tenant_id: str) -> None:
        super().__init__(commit, tenant_id=tenant_id, consumer="VALIDITY")

    def _live_certificates(self, mission_id: str) -> list[Any]:
        # Latest inserted certificate per exact consumer identity; only USABLE
        # ones carry a validity to observe.
        return live_usable_certificates(self.store.connection, mission_id, limit=MAX_WAKE_ROWS + 1)

    def classify(self, event: Event) -> tuple[WorkTarget, ...]:
        if event.type == EXPIRY_EVENT:
            return classify_expiry(event, "VALIDITY")
        if event.type not in VALIDITY_SOURCE_EVENTS:
            return ()
        rows = self._live_certificates(event.mission_id)
        if len(rows) > MAX_WAKE_ROWS:
            raise AssuranceError("VALIDITY_WAKEUP_INVENTORY_INCOMPLETE")
        change = fingerprint(event.to_json())
        targets = {}
        for row in rows:
            key = validity_work_key(decode(row["certificate_json"]))
            targets[key] = WorkTarget(
                key,
                fingerprint(
                    {
                        "certificate_id": row["certificate_id"],
                        "certificate_hash": row["certificate_hash"],
                        "change_event": change,
                    }
                ),
            )
        return tuple(targets[key] for key in sorted(targets))

    def _subject(self, claim: WorkClaim, event: Event) -> Any:
        """The exact certificate row this work observes, from the trigger itself."""
        if event.type == EXPIRY_EVENT:
            body = dict(event.payload)
            row = self.store.connection.execute(
                "SELECT * FROM assurance_use_certificates WHERE mission_id=? AND certificate_id=?",
                (claim.mission_id, text(body.get("certificate_id"))),
            ).fetchone()
            if row is None or row["certificate_hash"] != body.get("certificate_hash"):
                raise AssuranceError("SOURCE_UNAVAILABLE", "expired certificate")
            return row
        for row in self._live_certificates(claim.mission_id):
            if validity_work_key(decode(row["certificate_json"])) == claim.work_key:
                return row
        raise AssuranceError("SOURCE_UNAVAILABLE", "live certificate for work key")

    def _observe_locked(self, row: Any, *, now_ms: int) -> dict:
        certificate = decode(row["certificate_json"])
        if fingerprint(certificate) != row["certificate_hash"]:
            raise AssuranceError("CERTIFICATE_HASH_MISMATCH")
        epochs = read_epochs_locked(self.store.connection, row["mission_id"])
        root = self._root()
        reasons = []
        if certificate["root_incarnation_id"] != root:
            reasons.append("ROOT_CHANGED")
        # 2026-10-01（第 4 项）：来源是否变了按读集逐项重读，不再比整任务时钟——时钟一动
        # 就判"来源已变"让这个观察从没接到任何决定上。时钟移动只是说明（notes）。
        changed = changed_items(self.store, tenant_id=self.tenant_id, certificate=certificate)
        if changed:
            reasons.append("SOURCE_CHANGED")
        notes = []
        if (
            certificate["mission_epoch"],
            certificate["environment_epoch"],
            certificate["clock_generation"],
        ) != (epochs.mission, epochs.environment, epochs.clock_generation):
            notes.append("EPOCH_MOVED")
        if epochs.clock_state != "STABLE":
            reasons.append("TIME_DISCONTINUITY")
        if certificate_expired(row, now_ms=now_ms):
            reasons.append("EXPIRED")
        superseded = self.store.connection.execute(
            "SELECT 1 FROM assurance_use_certificates newer WHERE newer.mission_id=? "
            "AND newer.consumer_kind=? AND newer.consumer_id=? AND newer.purpose=? "
            "AND newer.scope_id=? AND newer.rowid>(SELECT rowid FROM assurance_use_certificates "
            "WHERE certificate_id=?) LIMIT 1",
            (
                row["mission_id"],
                row["consumer_kind"],
                row["consumer_id"],
                row["purpose"],
                row["scope_id"],
                row["certificate_id"],
            ),
        ).fetchone()
        if superseded is not None:
            reasons.append("SUPERSEDED")
        return {
            "mission_id": row["mission_id"],
            "certificate_id": row["certificate_id"],
            "certificate_hash": row["certificate_hash"],
            "consumer_kind": row["consumer_kind"],
            "consumer_id": row["consumer_id"],
            "purpose": row["purpose"],
            "scope_id": row["scope_id"],
            "not_after_ms": row["not_after_ms"],
            "validity": "CURRENT" if not reasons else "STALE",
            "reasons": reasons,
            "changed_items": changed,
            "notes": notes,
            "observed_at_ms": now_ms,
            "epochs": epochs.to_json(),
        }

    async def prepare(self, claim: WorkClaim) -> PreparedAssuranceWork | AssuranceWait:
        if self.store.connection.in_transaction:
            raise AssuranceError("ASSURANCE_PREPARATION_INSIDE_TRANSACTION")
        with self.store.read_view():
            self._require_mission(claim.mission_id)
            event = _trigger(self.store, claim)
            self._check_claim(claim, event, exact=event.type == EXPIRY_EVENT)
            row = self._subject(claim, event)
            preview = self._observe_locked(row, now_ms=int(self.store.now * 1000))
        trigger = _trigger_json(event)
        certificate_id, certificate_hash = row["certificate_id"], row["certificate_hash"]

        def commit() -> AssuranceRef:
            with atomic(self.store):
                self._require_mission(claim.mission_id)
                current = self.store.connection.execute(
                    "SELECT * FROM assurance_use_certificates WHERE mission_id=? "
                    "AND certificate_id=?",
                    (claim.mission_id, certificate_id),
                ).fetchone()
                if current is None or current["certificate_hash"] != certificate_hash:
                    raise AssuranceError("RECHECK_REQUIRED")
                observed = self._observe_locked(current, now_ms=int(self.store.now * 1000))
                if (observed["validity"], observed["reasons"], observed["changed_items"]) != (
                    preview["validity"],
                    preview["reasons"],
                    preview["changed_items"],
                ):
                    raise AssuranceError("RECHECK_REQUIRED")
                body = {
                    **observed,
                    "trigger": trigger,
                    "work_key": claim.work_key,
                    "target_epoch": claim.target_epoch,
                }
                receipt_id = "assurance-validity:" + fingerprint(
                    {
                        "certificate_id": certificate_id,
                        "certificate_hash": certificate_hash,
                        "trigger": trigger,
                    }
                )
                ref = _receipt_ref(
                    self.store, receipt_id, VALIDITY_CHECKED_EVENT, certificate_id, body
                )
                self.commit._emit(
                    VALIDITY_CHECKED_EVENT,
                    claim.mission_id,
                    key=receipt_id,
                    payload={
                        key: body[key]
                        for key in (
                            "certificate_id",
                            "certificate_hash",
                            "consumer_kind",
                            "consumer_id",
                            "purpose",
                            "scope_id",
                            "validity",
                            "reasons",
                            "observed_at_ms",
                        )
                    },
                )
                return ref

        return PreparedAssuranceWork(commit)


# ------------------------------------------------------------------ CLOSEOUT
def unmet_only_by_unknown_effects(status: Any, effect_states: Mapping[str, str]) -> bool:
    """A root scope that is not complete only because a required effect's result is unknown
    (第 1 批偏差 2，B 口径；原计划 §7.2 "危险/必需效果 UNKNOWN → BLOCKED_UNKNOWN").

    True when the root's content is ready, it has required effects, at least one is
    ``RECONCILIATION_REQUIRED`` and every one is either ``ACCEPTED`` or
    ``RECONCILIATION_REQUIRED``.  Anything else that keeps the scope open (content not
    ready, an effect still awaiting its intent / review) is ``ROOT_SCOPE_UNMET`` as before.
    """
    if not bool(getattr(status, "content_ready", False)) or not effect_states:
        return False
    states = set(effect_states.values())
    return "RECONCILIATION_REQUIRED" in states and states <= {"ACCEPTED", "RECONCILIATION_REQUIRED"}


class AssuranceCloseoutConsumer(_ConsumerBase):
    """Derived closeout readiness of an assured Mission (spec §7.2).

    The original missions row stays the final-status authority. This consumer
    only projects, into ``assurance_closeouts``, whether the root business
    requirement is met AND every original responsibility has converged:
    NOT_READY (no adopted root resolution / current evidence invalid / root
    Scope unmet), BLOCKED_UNKNOWN (an approved effect is in
    RECONCILIATION_REQUIRED), DRAINING (open intents, unsettled reservations,
    unknown usage), READY (all converged: also the Mission judge's recorded
    success judgment on an ACTIVE Mission). READY→FINALIZED belongs to the unique
    final writer ``assurance_final_writer.finalize_assured_mission`` (item 7),
    installed as ``finalizer(mission_id, check)`` and called inside the same
    transaction when the re-evaluation lands READY.

    第 1 批（2026-10-06）：评估正文按 closeout-v1（``contracts/closeout-v1.schema.json``）的字段写——
    ``root_resolution_ref`` / ``requirements_ref``（钉住要求版本）/ ``completion_spec_hash`` /
    ``pending_effect_keys`` / ``unsettled_operation_refs`` / ``accounting_pending_refs`` /
    ``dangerous_work_refs`` / ``report_ref`` / ``as_of_ms``，不再写裸 id 列表（A17）；未收敛判定把任务
    下所有 HELD 的审阅员尾部预留和所有结果不明（UNKNOWN）的已交出动作算进去，不限根范围（A07）；
    定稿那一次事务重读纪元与时钟、证书到期走 EVIDENCE_STALE（A01，原计划 §7.2）。
    """

    VOLATILE = frozenset({"as_of_ms", "epochs"})

    def __init__(
        self,
        commit: Any,
        *,
        tenant_id: str,
        finalizer: Callable[[str, Mapping[str, Any]], AssuranceRef | None] | None = None,
    ) -> None:
        super().__init__(commit, tenant_id=tenant_id, consumer="CLOSEOUT")
        self.finalizer = finalizer

    @staticmethod
    def work_key(mission_id: str) -> str:
        return "closeout:" + text(mission_id)

    def classify(self, event: Event) -> tuple[WorkTarget, ...]:
        if event.type == EXPIRY_EVENT:
            return classify_expiry(event, "CLOSEOUT")
        if event.type not in CLOSEOUT_SOURCE_EVENTS:
            return ()
        return (
            WorkTarget(
                self.work_key(event.mission_id),
                fingerprint({"type": event.type, "change_event": fingerprint(event.to_json())}),
            ),
        )

    # -------------------------------------------------------------- readers
    def _root_resolution(
        self, mission: Any
    ) -> tuple[Any | None, list[str], list[str], str | None]:
        """Adopted root GoalResolution for every required root duty, or what is missing.

        A Mission whose plan does not read back (no root binding yet, or a damaged
        plan the original judge refuses) has no root to resolve: reported as
        ``ROOT_NETWORK_UNAVAILABLE`` and NOT_READY, never repaired here.
        """
        from .assurance_tick import MISSION_DATA_ERRORS
        from .commit_service import CommitRejected

        try:
            network = self.commit._judgment_network(mission)
        except (CommitRejected, *MISSION_DATA_ERRORS) as error:
            # 读不回来有两种：原判定拒绝，或这个任务的执行图文档 / 计划本身读不了（比如按
            # 旧编解码清单存的旧任务）。都是"根网络不可用"，如实记下，不往外抛。
            return None, [], [], "ROOT_NETWORK_UNAVAILABLE: " + str(error)[:200]
        semantics = HtnStore(self.store)
        resolutions, missing = [], []
        for duty in dict.fromkeys(network.required_obligations):
            adopted = semantics.adopted_goal_resolution(mission.id, str(duty))
            if adopted is None:
                missing.append(str(duty))
            else:
                resolutions.append(adopted)
        roots = [str(root) for root in network.root_occurrence_ids]
        if missing or not resolutions:
            return None, missing, roots, None
        return resolutions[0], missing, roots, None

    def _evaluate_locked(self, mission: Any, *, now_ms: int) -> dict:
        integer(now_ms)
        from .completion_status import read_current_effect, read_occurrence_completion
        from .requirements_amendment import requirements_ref

        epochs = read_epochs_locked(self.store.connection, mission.id)
        body: dict[str, Any] = {
            "schema_version": 1,
            "mission_id": mission.id,
            "mission_version": mission.version,
            "mission_status": str(mission.status),
            "as_of_ms": now_ms,
            "root_incarnation_id": self._root(),
            "epochs": epochs.to_json(),
            "root_resolution_ref": None,
            "requirements_ref": None,
            "completion_spec_hash": None,
            "pending_effect_keys": [],
            "unsettled_operation_refs": [],
            "accounting_pending_refs": [],
            "dangerous_work_refs": [],
            "report_ref": None,
        }
        reasons: list[str] = []
        if str(mission.status) != "ACTIVE":
            reasons.append("MISSION_NOT_ACTIVE")
        # A01：时钟回拨 / 不稳时不定稿——与证书最终锁同一个判定（``require_epochs_locked``）。
        if clock_discontinuous(epochs, now_ms=now_ms):
            reasons.append("TIME_DISCONTINUITY")
        resolution, missing, roots, unavailable = self._root_resolution(mission)
        body["root_occurrences"] = roots
        body["missing_root_duties"] = missing
        body["root_network"] = unavailable
        if resolution is None:
            reasons.append(
                "ROOT_RESOLUTION_MISSING" if unavailable is None else "ROOT_NETWORK_UNAVAILABLE"
            )
            body.update(state="NOT_READY", reasons=reasons)
            return body
        body["root_resolution_ref"] = AssuranceRef(
            "resolution", Pin(str(resolution.resolution_id), 0, content_hash_of(resolution.to_json()))
        ).to_json()
        # 钉住根结论是按哪一版要求形成的（与改要求同一种引用：revision + content_hash）。
        body["requirements_ref"] = requirements_ref(
            HtnStore(self.store).get_requirements_revision(mission.id, int(resolution.requirements_version))
        )
        # The Mission judge's own verdict on ``Mission.success_criteria`` is part of
        # the root business requirement; the unique final writer completes from it.
        judgment = recorded_judgment(mission)
        body["success_judgment"] = None if judgment is None else dict(judgment)
        if judgment is None:
            reasons.append("MISSION_JUDGMENT_MISSING")
        if str(resolution.verdict) != "ACCEPT":
            reasons.append("ROOT_RESOLUTION_NOT_ACCEPT")
        if str(resolution.validity) != "CURRENT":
            reasons.append("ROOT_RESOLUTION_NOT_CURRENT")
        # 2026-10-01（第 4 项）：收尾前复查"当初通过的依据现在还成立吗"——根结论、中间目标
        # 结论、每条贡献验收的证书逐项重读；真变了（或到期了，A01）就不收尾，由规划器按修复请求
        # 决定重做。这里只拦收尾，不写 goal_resolutions.validity（用户红线）。
        stale = stale_certificates(self.store, tenant_id=self.tenant_id, mission_id=mission.id, now_ms=now_ms)
        body["stale_certificates"] = stale
        if stale:
            reasons.append(EVIDENCE_STALE)
        # 2026-10-05（原计划 §7.2"最终事务重读"）：资料不在证书钉住的对象里，换版本 / 撤销由
        # 规划器判现有成果还作不作数。终审等这件事问完才开；终审之后、完成之前才换的，同一个
        # 判断在这里再拦一次——问完之前不收尾。
        from .planning_repair_requests import source_change_open

        if source_change_open(self.store, mission.id):
            reasons.append(SOURCE_CHANGE_OPEN)
        pending_effects: list[str] = []
        unmet: list[str] = []
        blocked: list[str] = []
        spec_hashes: list[str] = []
        for root in roots:
            status = read_occurrence_completion(self.store, mission.id, root)
            spec_hashes.append(str(status.scope.spec_hash))
            states = {
                str(effect_key): str(read_current_effect(
                    self.store, mission.id, status.scope.spec_hash, str(effect_key))["state"])
                for effect_key in status.scope.required_effect_keys
            }
            pending_effects.extend(k for k, state in states.items() if state == "RECONCILIATION_REQUIRED")
            if status.complete:
                continue
            # 第 1 批偏差 2（B 口径）：根范围只差结果不明的效果时不是"范围未满足"，是"效果不明"——
            # 走下面的 BLOCKED_UNKNOWN（原计划 §7.2），不落 ROOT_SCOPE_UNMET。
            (blocked if unmet_only_by_unknown_effects(status, states) else unmet).append(root)
        body["completion_spec_hash"] = spec_hashes[0] if spec_hashes else None
        body["unmet_root_occurrences"] = unmet
        body["blocked_root_occurrences"] = blocked
        body["pending_effect_keys"] = pending_effects
        if unmet:
            reasons.append("ROOT_SCOPE_UNMET")
        # A07：已交出的动作，不限根范围——结果不明（UNKNOWN）的是危险工作，交出去还没结算的也记下。
        unknown_actions = self.store.list_actions(mission.id, "UNKNOWN")
        handed_off = self.store.list_actions(mission.id, "HANDED_OFF")
        body["dangerous_work_refs"] = [self._action_ref(action) for action in unknown_actions]
        body["unsettled_operation_refs"] = [
            self._action_ref(action) for action in (*unknown_actions, *handed_off)
        ]
        open_intents = sorted(
            intent.intent_id
            for intent in self.store.list_intents(
                "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED"
            )
            if intent.mission_id == mission.id
        )
        # A07：未结算的预留——预留表里 RESERVED 的，加上尾部预留表里仍 HELD 的（含首个审阅员的尾部
        # 预留；两张表不一致时以预留本身为准）。
        open_reservations, accounting = self._open_accounting(mission.id)
        body["accounting_pending_refs"] = accounting
        usage = self.commit._ledger.usage_flags(mission.id)
        # NEXT-TG-1.0 §12 E5 (real forced exit, 2026-09-28): a review turn whose original
        # provider call was cut off by a crash waits only for that call's reconciliation,
        # which a relay that reports nothing can never answer.  Its charge is UNKNOWN and
        # is exactly what the upper-bound rule below counts; it does not hold the judged
        # Mission open for ever.  The intent itself is left to original recovery.
        body.update(
            open_intents=open_intents[:256],
            usage_fully_known=bool(usage["usage_fully_known"]),
            budget_conserved=bool(usage["budget_conserved"]),
        )
        body.update(self._drain_decision(
            mission.id, reasons=reasons,
            unknown=[*pending_effects, *(ref["pin"]["id"] for ref in body["dangerous_work_refs"])],
            open_intents=open_intents, open_reservations=open_reservations,
            usage_fully_known=bool(usage["usage_fully_known"]),
        ))
        return body

    @staticmethod
    def _action_ref(action: Mapping[str, Any]) -> dict[str, Any]:
        """一条动作记录的引用：按动作键与版本钉住，正文指纹是整条记录。"""
        return AssuranceRef(
            "operation",
            Pin(str(action["action_key"]), int(action.get("version") or 0), fingerprint(dict(action))),
        ).to_json()

    def _open_accounting(self, mission_id: str) -> tuple[list[str], list[dict[str, Any]]]:
        """未结算的记账主体：预留表里 RESERVED 的行，加上尾部预留表里仍 HELD 的（A07）。

        返回 (主体编号列表, closeout-v1 的 ``accounting_pending_refs``)。
        """
        connection = self.store.connection
        rows = connection.execute(
            "SELECT subject_id, state, reserved_tokens FROM budget_reservations WHERE mission_id=? "
            "AND state='RESERVED' ORDER BY subject_id LIMIT 257",
            (mission_id,),
        ).fetchall()
        facts: dict[str, dict[str, Any]] = {
            str(row["subject_id"]): {"source": "budget_reservations", "state": str(row["state"]),
                                     "reserved_tokens": int(row["reserved_tokens"])}
            for row in rows
        }
        if self.store.has_table("budget_tail_holds"):
            for row in connection.execute(
                "SELECT hold_id, subject_id, purpose, remaining_attempts FROM budget_tail_holds "
                "WHERE mission_id=? AND state='HELD' ORDER BY subject_id LIMIT 257",
                (mission_id,),
            ).fetchall():
                facts.setdefault(str(row["subject_id"]), {
                    "source": "budget_tail_holds", "hold_id": str(row["hold_id"]), "purpose": str(row["purpose"]),
                    "state": "HELD", "remaining_attempts": int(row["remaining_attempts"])})
        subjects = sorted(facts)[:256]
        refs = [
            AssuranceRef("reservation_fact", Pin(subject, 0, fingerprint(facts[subject]))).to_json()
            for subject in subjects
        ]
        return subjects, refs

    def _drain_decision(
        self, mission_id: str, *, reasons: list[str], unknown: list[str], open_intents: list[str],
        open_reservations: list[str], usage_fully_known: bool,
    ) -> dict[str, Any]:
        """The closeout state once the root facts are read (NOT_READY / BLOCKED_UNKNOWN /
        DRAINING / READY) and, when READY by the upper-bound rule, what it counts.

        ``unknown``: the required effect keys in RECONCILIATION_REQUIRED plus the handed-off
        actions whose outcome is UNKNOWN anywhere in the Mission (A07) — the spec's
        "危险/必需效果 UNKNOWN → BLOCKED_UNKNOWN".

        User decision 2026-09-26: a judged Mission whose only remaining drain is the
        UNKNOWN charge of work that will never run again closes with that charge counted
        at its upper bound (overcount, never undercount, never freeze).
        NEXT-TG-1.0 §12 E5 (real forced exit, 2026-09-28): a review turn whose original
        provider call was cut off waits only for that call's reconciliation, which a
        relay that reports nothing can never answer; such an intent does not hold the
        judged Mission open — its UNKNOWN charge is exactly what the rule counts.  The
        intent itself is left to original recovery.
        """
        awaiting = self._awaiting_original_reconciliation(open_intents)
        draining_intents = [i for i in open_intents if i not in awaiting]
        out: dict[str, Any] = {}
        upper = self._upper_bound_plan(mission_id, open_reservations) if (
            not reasons and not unknown and not draining_intents
            and (open_reservations or not usage_fully_known)
        ) else None
        if upper is not None:
            out["usage_counted_at_upper_bound"] = upper
        if reasons:
            out.update(state="NOT_READY", reasons=reasons)
        elif unknown:
            out.update(state="BLOCKED_UNKNOWN", reasons=["EFFECT_UNKNOWN"])
        elif upper is not None:
            out.update(state="READY", reasons=[])
        elif draining_intents or open_reservations or not usage_fully_known:
            draining = []
            if draining_intents:
                draining.append("OPEN_INTENTS")
            if open_reservations:
                draining.append("OPEN_RESERVATIONS")
            if not usage_fully_known:
                draining.append("USAGE_UNKNOWN")
            out.update(state="DRAINING", reasons=draining)
        else:
            out.update(state="READY", reasons=[])
        return out

    @staticmethod
    def require_final_consistency(preview: Mapping[str, Any], evaluation: Mapping[str, Any]) -> None:
        """A01（原计划 §7.2"最终事务重读当前 epoch…才 READY→FINALIZED"）：要定稿（READY）的那次评估，
        任务 / 环境纪元与时钟代次必须等于收尾依据读取时的，现在不能早于读取时刻，时钟要 STABLE；
        任一不成立 → RECHECK_REQUIRED，退回重算，不定稿。没到 READY 的评估只是投影，纪元照常在动，不比。"""
        if evaluation.get("state") != "READY":
            return
        before, now = preview["epochs"], evaluation["epochs"]
        if any(now[key] != before[key] for key in ("mission", "environment", "clock_generation")):
            raise AssuranceError("RECHECK_REQUIRED", "closeout epochs moved")
        if now["clock_state"] != "STABLE" or int(evaluation["as_of_ms"]) < int(preview["as_of_ms"]):
            raise AssuranceError("RECHECK_REQUIRED", "closeout clock")

    def _awaiting_original_reconciliation(self, open_intents: list[str]) -> set[str]:
        """Open review intents whose only wait is an unanswerable original provider call:
        still SUBMITTED, recorded as ``AssuranceProviderReconciliationRequired`` (the turn
        was stopped, nothing is reissued), and their subject's charge is UNKNOWN."""
        awaiting: set[str] = set()
        for intent_id in open_intents:
            intent = self.store.get_intent(intent_id)
            # only Assurance review turns (content reviews run as critic intents, the
            # purpose reviews as plan intents) ever record this wait
            if intent is None or intent.state != "SUBMITTED" or intent.kind not in ("critic", "plan"):
                continue
            recorded = self.store.connection.execute(
                "SELECT 1 FROM commit_receipts WHERE kind='AssuranceProviderReconciliationRequired' "
                "AND subject_id=? LIMIT 1", (intent_id,)).fetchone()
            if recorded is not None and self.commit._ledger.has_unknown_usage(intent.subject_id):
                awaiting.add(intent_id)
        return awaiting

    def _upper_bound_plan(self, mission_id: str, open_reservations: list[str]) -> dict | None:
        """The subjects to count at their upper bound, or None when anything else drains.

        Every open reservation must be held by an UNKNOWN charge, and every unknown
        usage fact / unsettled grant of the Mission must belong to a subject that is
        either counted now or was counted by an earlier closeout.
        """
        ledger = self.commit._ledger
        if any(not ledger.has_unknown_usage(subject) for subject in open_reservations):
            return None
        counted = {
            str(row[0]) for row in self.store.connection.execute(
                "SELECT json_extract(payload_json,'$.subject_id') FROM events "
                "WHERE mission_id=? AND type=?", (mission_id, UPPER_BOUND_EVENT)).fetchall()
        }
        pending = set(open_reservations)
        unknown = {
            str(row[0]) for row in self.store.connection.execute(
                "SELECT subject_id FROM imported_usage WHERE mission_id=? AND unknown=1",
                (mission_id,)).fetchall()
        }
        if self.store.has_table("provider_token_grants"):
            unknown |= {
                str(row[0]) for row in self.store.connection.execute(
                    "SELECT subject_id FROM provider_token_grants WHERE mission_id=? "
                    "AND state IN ('RESERVED','HANDED_OFF','UNKNOWN')", (mission_id,)).fetchall()
            }
        if not unknown <= pending | counted:
            return None
        return {"subjects": sorted(pending), "already_counted": sorted(counted & unknown)}

    def _stable(self, evaluation: Mapping[str, Any]) -> dict:
        return {key: value for key, value in evaluation.items() if key not in self.VOLATILE}

    # -------------------------------------------------------------- writer
    def _write_locked(self, evaluation: dict, *, trigger: dict, claim: WorkClaim) -> AssuranceRef:
        mission_id = evaluation["mission_id"]
        connection = self.store.connection
        row = connection.execute(
            "SELECT * FROM assurance_closeouts WHERE mission_id=?", (mission_id,)
        ).fetchone()
        finalized = row is not None and row["state"] == "FINALIZED"
        if finalized:
            # History never rolls back. Re-evaluations after finalisation are
            # recorded as receipts only; the row keeps its final identity.
            evaluation = {**evaluation, "state": "FINALIZED", "reasons": ["ALREADY_FINALIZED"]}
        check_hash = fingerprint(evaluation)
        check_json = canonical(evaluation)
        now_ms = evaluation["as_of_ms"]
        state = evaluation["state"]
        resolution_ref = evaluation.get("root_resolution_ref")
        resolution_id = None if resolution_ref is None else str(resolution_ref["pin"]["id"])
        writes_row = resolution_id is not None and not finalized
        row_version = None if row is None else row["row_version"]
        changed = False
        if writes_row:
            if row is None:
                row_version, changed = 1, state != "NOT_READY"
            else:
                changed = (
                    row["state"] != state
                    or row["check_body_hash"] != check_hash
                    or row["resolution_id"] != resolution_id
                )
            if changed:
                row_version += 1
        receipt = {
            **evaluation,
            "trigger": trigger,
            "work_key": claim.work_key,
            "target_epoch": claim.target_epoch,
            "row_version": row_version,
            "check_body_hash": check_hash,
        }
        receipt_id = "assurance-closeout:" + fingerprint(
            {"mission_id": mission_id, "trigger": trigger, "check_body_hash": check_hash}
        )
        ref = _receipt_ref(self.store, receipt_id, CLOSEOUT_EVENT, mission_id, receipt)
        if writes_row:
            if row is None:
                # First insert is always NOT_READY / version 1 (spec §7.2); the
                # computed state lands as the immediate re-evaluation below.
                connection.execute(
                    "INSERT INTO assurance_closeouts VALUES(?,?,?,?,?,?,?,?)",
                    (
                        mission_id,
                        resolution_id,
                        "NOT_READY",
                        1,
                        check_hash,
                        check_json,
                        receipt_id,
                        now_ms,
                    ),
                )
            if changed:
                connection.execute(
                    "UPDATE assurance_closeouts SET resolution_id=?,state=?,row_version=?,"
                    "check_body_hash=?,check_body_json=?,last_receipt_id=?,updated_at_ms=? "
                    "WHERE mission_id=?",
                    (
                        resolution_id,
                        state,
                        row_version,
                        check_hash,
                        check_json,
                        receipt_id,
                        now_ms,
                        mission_id,
                    ),
                )
        self.commit._emit(
            CLOSEOUT_EVENT,
            mission_id,
            key=receipt_id,
            payload={
                "state": state,
                "reasons": evaluation["reasons"],
                "root_resolution_ref": resolution_ref,
                "check_body_hash": check_hash,
                "receipt_ref": ref.to_json(),
            },
        )
        upper = evaluation.get("usage_counted_at_upper_bound")
        if state == "READY" and upper and not finalized:
            for subject in upper["subjects"]:
                settled = self.commit._ledger.settle_at_upper_bound(subject_id=subject)
                self.commit._emit(UPPER_BOUND_EVENT, mission_id, key="upper-bound:" + subject, payload={
                    "subject_id": subject, "reason": "unknown_usage",
                    "counted_tokens": settled["settled_tokens"],
                    "closeout_receipt_ref": ref.to_json()})
        if state == "READY" and self.finalizer is not None:
            final = self.finalizer(mission_id, evaluation)
            if final is not None and not isinstance(final, AssuranceRef):
                raise AssuranceError("WORK_COMMIT_RECEIPT_REQUIRED")
        return ref

    async def prepare(self, claim: WorkClaim) -> PreparedAssuranceWork | AssuranceWait:
        if self.store.connection.in_transaction:
            raise AssuranceError("ASSURANCE_PREPARATION_INSIDE_TRANSACTION")
        with self.store.read_view():
            mission = self._require_mission(claim.mission_id)
            event = _trigger(self.store, claim)
            self._check_claim(claim, event)
            basis = self._evaluate_locked(mission, now_ms=int(self.store.now * 1000))
            preview = self._stable(basis)
        trigger = _trigger_json(event)

        def commit() -> AssuranceRef:
            with atomic(self.store):
                current = self._require_mission(claim.mission_id)
                evaluation = self._evaluate_locked(current, now_ms=int(self.store.now * 1000))
                if self._stable(evaluation) != preview:
                    raise AssuranceError("RECHECK_REQUIRED")
                # A01：定稿那一次事务重读纪元与时钟（依据读取时 vs 现在）。
                self.require_final_consistency(basis, evaluation)
                return self._write_locked(evaluation, trigger=trigger, claim=claim)

        return PreparedAssuranceWork(commit)


# -------------------------------------------------------------------- NOTIFY
class AssuranceNotifyConsumer(_ConsumerBase):
    """At-least-once local status transport for the final writer's request.

    ``transport(payload)`` is the deployment's current Host push (the original
    tick→Host event chain); it receives ``{mission_id, event_id, state_version}``
    only. A raising transport keeps the work WAITING under the shared retry cap;
    a successful send records one original receipt so a replay is not re-sent.
    The execution root gate is checked before every send (spec §7.3).
    """

    def __init__(
        self,
        commit: Any,
        *,
        tenant_id: str,
        transport: Callable[[Mapping[str, Any]], None] | None,
    ) -> None:
        super().__init__(commit, tenant_id=tenant_id, consumer="NOTIFY")
        self.transport = transport

    @staticmethod
    def _body(event: Event) -> dict:
        return notification_body(event)

    def classify(self, event: Event) -> tuple[WorkTarget, ...]:
        if event.type != NOTIFICATION_EVENT:
            return ()
        # 与终态写入同事务建的那条待办是同一个目标（第 2 批 A20）：重放只合并，不冲突
        return (notification_work_target(event),)

    async def prepare(self, claim: WorkClaim) -> PreparedAssuranceWork | AssuranceWait:
        if self.store.connection.in_transaction:
            raise AssuranceError("ASSURANCE_PREPARATION_INSIDE_TRANSACTION")
        receipt_id = "assurance-notified:" + claim.work_key
        with self.store.read_view():
            mission = self._require_mission(claim.mission_id)
            event = _trigger(self.store, claim)
            self._check_claim(claim, event)
            body = self._body(event)
            final = self.store.connection.execute(
                "SELECT event_id FROM events WHERE event_id=? AND mission_id=?",
                (body["final_event_id"], mission.id),
            ).fetchone()
            if final is None:
                raise AssuranceError("SOURCE_UNAVAILABLE", "final event")
            self._root()
            already = self.store.get_receipt(receipt_id)
        payload = {
            "mission_id": mission.id,
            "event_id": body["final_event_id"],
            "state_version": integer(body["state_version"]),
        }
        if already is None:
            if self.transport is None:
                raise AssuranceError("NOTIFY_TRANSPORT_UNBOUND")
            # The send happens outside the Store lock; a crash between the send
            # and the receipt re-sends (at-least-once, spec §7.3). The Host
            # de-duplicates by event_id.
            self.transport(payload)
        receipt_body = {
            "mission_id": mission.id,
            "notification_event_id": event.id,
            "final_event_id": body["final_event_id"],
            "state_version": payload["state_version"],
            "delivered_payload": payload,
        }

        def commit() -> AssuranceRef:
            with atomic(self.store):
                self._require_mission(claim.mission_id)
                self._root()
                ref = _receipt_ref(self.store, receipt_id, NOTIFIED_EVENT, mission.id, receipt_body)
                self.commit._emit(
                    NOTIFIED_EVENT,
                    mission.id,
                    key=receipt_id,
                    payload={"receipt_ref": ref.to_json(), **payload},
                )
                return ref

        return PreparedAssuranceWork(commit)


__all__ = (
    "ACTIVATION_EVENT",
    "AssuranceCloseoutConsumer",
    "AssuranceNotifyConsumer",
    "AssuranceValidityConsumer",
    "CLOSEOUT_EVENT",
    "CLOSEOUT_SOURCE_EVENTS",
    "DIAGNOSTIC_EVENTS",
    "NOTIFICATION_EVENT",
    "NOTIFIED_EVENT",
    "VALIDITY_CHECKED_EVENT",
    "VALIDITY_SOURCE_EVENTS",
)
