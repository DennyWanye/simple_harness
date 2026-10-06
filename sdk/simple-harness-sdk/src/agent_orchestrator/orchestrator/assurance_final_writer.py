# SPDX-License-Identifier: Apache-2.0
"""The unique final writer of an assured Mission (spec §7.1, handoff item 7).

An assured Mission is never completed by ``judge_mission``'s success tail. The
Mission judge records that the success criteria are met and *requests* a
closeout; the CLOSEOUT consumer re-derives readiness (adopted root
GoalResolution, current evidence, root Scope met, effects known, no open
intents / reservations / unknown usage, success judgment recorded) and, when
READY, calls :func:`finalize_assured_mission` inside its own commit
transaction. That call is the only place an assured Mission becomes
COMPLETED: it moves the original missions row, releases the terminal pools,
emits the original ``MissionCompleted``, moves the closeout row READY →
FINALIZED and requests the NOTIFY transport with the final event identity.

Every terminal write on the assured lane — this one and the original failure /
cancellation writers — ends with :func:`request_assured_notification`, so the
Host push chain always learns the final state from a durable event.
Nothing here fabricates readiness: a closeout that is not READY, a Mission
that is not ACTIVE, a judgment that is missing or a row that moved refuse.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..assurance.codec import AssuranceError, canonical, fields, fingerprint, integer, text
from ..assurance.refs import AssuranceRef, Pin
from ..contracts import Event, Mission, MissionStatus
from ..contracts.state_machines import MissionStopReason
from ..storage.assurance_store import AssuranceStore
from ..storage.assurance_work import AssuranceWorkStore, WorkTarget
from ..storage.htn_store import HtnStore
from .state_machine import next_mission

FINALIZED_KIND = "AssuranceMissionFinalized"
NOTIFICATION_EVENT = "AssuranceStatusNotificationRequested"
CLOSEOUT_REQUESTED_EVENT = "AssuranceCloseoutRequested"
JUDGMENT_KEY = "assurance_judgment"
CLOSEOUT_KEY = "assurance_closeout"
#: closeout-v1（``plans/Assurance/specs/1.1/contracts/closeout-v1.schema.json``）的字段；收尾记录、
#: 定稿回执、任务最终报告里的收尾记录都按它写（第 1 批 A17）。``read_set`` 不写：收尾没有自己的读集，
#: 它依赖的证书各自带读集。
CLOSEOUT_V1_FIELDS = (
    "schema_version", "mission_id", "root_resolution_ref", "requirements_ref", "completion_spec_hash",
    "state", "pending_effect_keys", "unsettled_operation_refs", "accounting_pending_refs",
    "dangerous_work_refs", "report_ref", "as_of_ms", "reasons",
)


def closeout_document(evaluation: Mapping[str, Any], **overrides: Any) -> dict[str, Any]:
    """收尾评估正文里的 closeout-v1 文档部分（内部核对字段不带）。"""
    document = {name: evaluation.get(name) for name in CLOSEOUT_V1_FIELDS}
    document.update(overrides)
    return document


ASSURED = "ASSURED"
NOT_ASSURED = "NOT_ASSURED"
READ_FAILED = "READ_FAILED"


def assurance_lane_status(store: Any, mission_id: str) -> tuple[str, str | None]:
    """保证通道状态：``ASSURED`` / ``NOT_ASSURED``（没有建任务合同，或合同不是保证通道）/
    ``READ_FAILED``（合同在、绑定却读不回来等），连同出错的码。

    第 1 批 A06（2026-10-06）：读错和"不是保证通道"是两回事——前者不能让终态通知静默不发。
    """
    try:
        lane = AssuranceStore(store).lane(mission_id)
    except AssuranceError as error:
        if error.code == "CREATION_CONTRACT_UNRESOLVED":
            return NOT_ASSURED, error.code
        return READ_FAILED, error.code
    return (ASSURED if lane == "ASSURANCE_1_1" else NOT_ASSURED), None


def is_assured(store: Any, mission_id: str) -> bool:
    """Whether this Mission runs on the assured lane; a missing or unreadable contract is not."""
    return assurance_lane_status(store, mission_id)[0] == ASSURED


def recorded_judgment(mission: Mission) -> Mapping[str, Any] | None:
    """The Mission judge's recorded success judgment awaiting closeout, if any."""
    report = mission.final_report or {}
    judgment = report.get(JUDGMENT_KEY)
    if not isinstance(judgment, Mapping) or judgment.get("met") is not True:
        return None
    return judgment


def assured_closeout_pending(store: Any, mission: Mission | None) -> bool:
    """An ACTIVE assured Mission whose success is judged and whose closeout is not final.

    The orchestrator loop neither re-judges nor stalls such a Mission: DRAINING /
    BLOCKED_UNKNOWN keep it ACTIVE until the original responsibilities converge
    (handoff item 7: no no-progress pseudo failure).
    """
    if mission is None or mission.status is not MissionStatus.ACTIVE:
        return False
    if recorded_judgment(mission) is None:
        return False
    return is_assured(store, mission.id)


def request_assured_closeout(
    commit: Any, mission: Mission, *, report: Mapping[str, Any], judged: Event
) -> Mission:
    """``judge_mission``'s assured success tail: record, request, stay ACTIVE.

    Called inside the judge's own transaction after ``MissionSuccessJudged``. The
    report the legacy tail would have completed the Mission with is kept on the
    row (the final writer completes from it), and the request is a durable
    event the CLOSEOUT consumer ingests.
    """
    store = commit._store
    if not store.connection.in_transaction:
        raise AssuranceError("FINAL_WRITER_TRANSACTION_REQUIRED")
    if mission.status is not MissionStatus.ACTIVE:
        raise AssuranceError("MISSION_NOT_ACTIVE")
    judgment = {
        "met": True,
        "judged_event_id": judged.id,
        "judged_at_version": int(mission.version),
    }
    pending = next_mission(mission, final_report={**dict(report), JUDGMENT_KEY: judgment})
    store.update_mission(pending, expected_version=mission.version)
    commit._emit(
        CLOSEOUT_REQUESTED_EVENT,
        mission.id,
        key=f"{mission.id}:{judged.id}",
        payload={**judgment, "mission_version": pending.version},
    )
    return pending


def notification_body(event: Event) -> dict[str, Any]:
    """通知请求事件的正文：``note`` 是终态写入时保证状态读取失败的如实说明（A06）；送往 Host 的仍只有
    ``{mission_id, event_id, state_version}``。"""
    return fields(dict(event.payload), {"final_event_id", "state_version"}, {"final_event_type", "note"})


def notification_work_target(event: Event) -> WorkTarget:
    """这条通知请求对应的 NOTIFY 待办目标：终态写入（同事务建）与游标摄取（重放）算出同一个。"""
    body = notification_body(event)
    return WorkTarget(
        "notify:" + event.mission_id + ":" + text(body["final_event_id"]),
        fingerprint({"type": event.type, "payload": body}),
    )


def request_assured_notification(
    commit: Any, mission_id: str, final: Event, *, state_version: int
) -> Event | None:
    """Emit the NOTIFY request for a terminal event of an assured Mission.

    Idempotent per final event (the event key); a Mission that is not assured gets
    nothing.  A Mission whose assurance state cannot be read (A06) still gets its
    terminal notice — the request says so in ``note`` instead of staying silent.

    第 2 批 A20（原计划 §7.3）：NOTIFY 待办与终态事件在同一事务里建，不等下一轮游标摄取；之后游标
    摄取到同一事件按同目标（同 seq、同指纹）合并。读取失败的任务没有可用的绑定行（待办表外键指它），
    只发事件，待办仍由游标摄取建。
    """
    store = commit._store
    status, code = assurance_lane_status(store, mission_id)
    if status == NOT_ASSURED:
        return None
    payload: dict[str, Any] = {
        "final_event_id": final.id,
        "state_version": integer(state_version),
        "final_event_type": final.type,
    }
    if status == READ_FAILED:
        payload["note"] = f"保证状态读取失败：{code}"
    event = commit._emit(
        NOTIFICATION_EVENT,
        mission_id,
        key=f"{mission_id}:{final.id}",
        payload=payload,
    )
    if status == ASSURED:
        AssuranceWorkStore(store).seed(
            mission_id, "NOTIFY", event, (notification_work_target(event),), now_ms=int(store.now * 1000)
        )
    return event


def finalize_assured_mission(
    commit: Any, mission_id: str, evaluation: Mapping[str, Any]
) -> AssuranceRef:
    """READY closeout → COMPLETED Mission + FINALIZED row, in the caller's transaction.

    ``evaluation`` is the CLOSEOUT consumer's re-evaluation that just landed
    READY under this same lock. Everything it says is re-read here from the
    rows before anything is written; a disagreement refuses (the work is
    rechecked, never repaired).

    第 1 批 A17：定稿回执正文 = closeout-v1 文档（state FINALIZED）+ 判定 / 版本字段；收尾行与任务最终
    报告里的收尾记录是同一份文档，``report_ref`` 指向这张回执。
    """
    store = commit._store
    mission_id = text(mission_id)
    if not store.connection.in_transaction:
        raise AssuranceError("FINAL_WRITER_TRANSACTION_REQUIRED")
    if not is_assured(store, mission_id):
        raise AssuranceError("ASSURANCE_PROFILE_REQUIRED")
    if evaluation.get("mission_id") != mission_id or evaluation.get("state") != "READY":
        raise AssuranceError("CLOSEOUT_NOT_READY")
    connection = store.connection
    row = connection.execute(
        "SELECT * FROM assurance_closeouts WHERE mission_id=?", (mission_id,)
    ).fetchone()
    if row is None or row["state"] != "READY":
        raise AssuranceError("CLOSEOUT_NOT_READY")
    resolution_ref = evaluation.get("root_resolution_ref")
    if resolution_ref is None or row["resolution_id"] != resolution_ref["pin"]["id"]:
        raise AssuranceError("RECHECK_REQUIRED", "closeout resolution")
    mission = commit._require_mission(mission_id)
    if mission.status is not MissionStatus.ACTIVE:
        raise AssuranceError("MISSION_NOT_ACTIVE")
    if int(mission.version) != int(evaluation.get("mission_version", -1)):
        raise AssuranceError("RECHECK_REQUIRED", "mission version")
    judgment = recorded_judgment(mission)
    if judgment is None:
        raise AssuranceError("MISSION_JUDGMENT_MISSING")
    semantics = HtnStore(store)
    resolution = semantics.get_goal_resolution(row["resolution_id"])
    if resolution.mission_id != mission_id or str(resolution.verdict) != "ACCEPT":
        raise AssuranceError("RECHECK_REQUIRED", "root resolution")
    receipt_id = "assurance-finalized:" + mission_id
    if store.get_receipt(receipt_id) is not None:
        raise AssuranceError("IMMUTABLE_IDENTITY_CONFLICT", "commit_receipts")
    now_ms = int(store.now * 1000)
    row_version = int(row["row_version"]) + 1
    state_version = int(mission.version) + 1
    # 定稿回执正文：closeout-v1 文档（它自己就是收尾记录，report_ref 为空）+ 判定与版本字段。
    document = closeout_document(evaluation, state="FINALIZED", as_of_ms=now_ms, report_ref=None)
    body = {
        **document,
        "closeout_row_version": row_version,
        "previous_check_body_hash": row["check_body_hash"],
        "judged_event_id": judgment["judged_event_id"],
        "state_version": state_version,
    }
    digest = fingerprint(body)
    ref = AssuranceRef("commit_receipt", Pin(receipt_id, 0, digest))
    report = dict(mission.final_report or {})
    report[CLOSEOUT_KEY] = {**document, "report_ref": ref.to_json()}
    report.update(commit._ledger.usage_flags(mission_id))
    done = next_mission(
        mission,
        MissionStatus.COMPLETED,
        stop_reason=str(MissionStopReason.VERIFICATION_PASSED),
        final_report=report,
    )
    if int(done.version) != state_version:
        raise AssuranceError("RECHECK_REQUIRED", "mission version")
    store.update_mission(done, expected_version=mission.version)
    final = commit._emit(
        "MissionCompleted",
        mission_id,
        key=mission_id,
        payload={"stop_reason": done.stop_reason, "final_report": report},
    )
    _promote_methods(store, mission_id, resolution)
    store.insert_receipt(
        commit_id=receipt_id,
        kind=FINALIZED_KIND,
        subject_id=mission_id,
        base_version=int(row["row_version"]),
        proposal_hash=digest,
        receipt=body,
    )
    check = {**dict(evaluation), "state": "FINALIZED", "as_of_ms": now_ms, "report_ref": ref.to_json()}
    moved = connection.execute(
        "UPDATE assurance_closeouts SET state='FINALIZED',row_version=?,check_body_hash=?,"
        "check_body_json=?,last_receipt_id=?,updated_at_ms=? WHERE mission_id=? AND row_version=?",
        (
            row_version,
            fingerprint(check),
            canonical(check),
            receipt_id,
            now_ms,
            mission_id,
            int(row["row_version"]),
        ),
    ).rowcount
    if moved != 1:
        raise AssuranceError("RECHECK_REQUIRED", "closeout row")
    commit._emit(
        FINALIZED_KIND,
        mission_id,
        key=receipt_id,
        payload={**body, "final_event_id": final.id, "final_event_type": final.type,
                 "receipt_ref": ref.to_json()},
    )
    request_assured_notification(commit, mission_id, final, state_version=done.version)
    return ref



def _promote_methods(store: Any, mission_id: str, resolution: Any) -> None:
    """The one road into the method library (阶段 C3): delivered, the root's final review passed,
    and that review called the method reusable.  Same transaction as ``MissionCompleted``; a
    promotion that fails for any reason is undone and recorded — the Mission still completes."""
    from .method_library import PROMOTION_SKIPPED, promote_methods

    store.connection.execute("SAVEPOINT method_promotion")
    try:
        promote_methods(store, mission_id, resolution)
    except Exception as error:  # noqa: BLE001 - the library is a by-product; completion never fails on it
        store.connection.execute("ROLLBACK TO method_promotion")
        from .hierarchical_dispatch import append_hierarchical_event

        append_hierarchical_event(store, PROMOTION_SKIPPED, mission_id, key=f"{mission_id}:unreadable",
                                  payload={"reason": "unreadable", "error_type": type(error).__name__,
                                           "error": str(error)[:300]})
    store.connection.execute("RELEASE method_promotion")

__all__ = (
    "ASSURED",
    "CLOSEOUT_KEY",
    "CLOSEOUT_REQUESTED_EVENT",
    "CLOSEOUT_V1_FIELDS",
    "FINALIZED_KIND",
    "JUDGMENT_KEY",
    "NOTIFICATION_EVENT",
    "NOT_ASSURED",
    "READ_FAILED",
    "assurance_lane_status",
    "assured_closeout_pending",
    "closeout_document",
    "finalize_assured_mission",
    "is_assured",
    "notification_body",
    "notification_work_target",
    "recorded_judgment",
    "request_assured_closeout",
    "request_assured_notification",
)
