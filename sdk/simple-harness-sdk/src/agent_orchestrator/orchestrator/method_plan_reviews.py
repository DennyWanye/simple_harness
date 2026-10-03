# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""新做法审阅的结论：谁能被采用、什么时候叫醒规划器（HTN 精简 片 A 第 6、7 项，2026-10-01）。

规划器为目标提出的做法在登记的同一事务里开一份独立的新做法审阅（METHOD_PLAN）。
这个模块只回答秩序问题，不判断做法好不好：

* :func:`unreviewed_adopted_methods` —— 计划提交事务里的闸门：这次要采用的做法里，哪些是
  规划器在本任务里提出、却还没有通过（或人裁决通过）的正式审阅记录。
* :func:`advance` —— 每轮循环调用：提案的审阅有了结论（通过 / 打回 / 人已裁决 / 给不出
  结论）就记一条 ``PlanningMethodReviewed`` 事件，那条事件才叫醒规划器，事件里是审阅员的
  原话；两位审阅员都判不下来则问人裁决，裁决前不记结论；裁决题没等到回答就过期，按
  "没有结论"上报，不让提案永远停在"在等"。
* :func:`awaiting` —— 还有没有提案在等结论（空闲判定据此算"在等"）。
* :func:`reviews_by_method` —— 每个做法最近一次审阅的结论，给规划包里的做法库用。

正式记录是唯一依据：闸门直接读审阅包与正式记录表，不读事件。
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..contracts.resolution import ReviewPurpose, ReviewVerdict
from ..storage.htn_store import HtnStore
from .review_adjudication import adjudication_of

PROPOSED = "PlanningMethodProposed"
REVIEWED = "PlanningMethodReviewed"
#: 计划提交闸门的拒绝原因。规划决定的拒绝码清单是封闭的（§33），这里用其中现成的一项。
REVIEW_REQUIRED = "METHOD_NOT_AUTHORIZED"

PASSED = "PASSED"
REJECTED = "REJECTED"
NO_VERDICT = "NO_VERDICT"
PENDING = "PENDING"
AWAITING_PERSON = "AWAITING_PERSON"


@dataclass(frozen=True, slots=True)
class MethodReview:
    """Where the review of one method stands.  ``record`` is the official record."""

    state: str
    package_id: str | None = None
    record: Any = None
    ruling: Mapping[str, Any] | None = None

    @property
    def passed(self) -> bool:
        return self.state == PASSED


def _same_method(subject: Any, method_ref: Any) -> bool:
    return (str(subject.id) == str(method_ref.method_id)
            and int(subject.revision) == int(method_ref.version)
            and str(subject.content_hash) == str(method_ref.content_hash))


def review_of(store: Any, mission_id: str, method_ref: Any) -> MethodReview:
    """The latest METHOD_PLAN review this Mission opened for exactly this method."""

    htn = HtnStore(store)
    packages = [package for package in htn.list_review_packages(mission_id, purpose=ReviewPurpose.METHOD_PLAN)
                if _same_method(package.binding.subject_ref, method_ref)]
    if not packages:
        return MethodReview("NONE")
    package = packages[-1]
    record = htn.official_review_record(str(package.package_id))
    if record is None:
        return MethodReview(PENDING, str(package.package_id))
    if record.verdict is ReviewVerdict.ACCEPT:
        return MethodReview(PASSED, str(package.package_id), record)
    if record.verdict is ReviewVerdict.INCONCLUSIVE:
        ruling = adjudication_of(store, str(record.record_id))
        if ruling is None:
            return MethodReview(AWAITING_PERSON, str(package.package_id), record)
        state = PASSED if ruling.get("decision") == "pass" else REJECTED
        return MethodReview(state, str(package.package_id), record, ruling)
    return MethodReview(REJECTED, str(package.package_id), record)


def findings_of(record: Any) -> list[dict[str, Any]]:
    """Every criterion the reviewer did not pass, in the reviewer's own words."""

    return [
        {"criterion_id": str(item.criterion_id), "verdict": str(item.verdict),
         "limitations": list(item.limitations)}
        for item in record.criteria if str(item.verdict) != "PASS"
    ][:16]


def adoption_refusal(store: Any, mission_id: str, reference: Any,
                     seeded: set[tuple[str, int, str]]) -> dict[str, Any] | None:
    """Whether adopting this one method now would be refused: ``None`` when it would not, else
    the refusal row.  The one rule the plan-commit gate and the planner's candidate list share
    (HTN 补齐 F1 偏差单 2): a seed of the deployment, or a method this Mission's own method
    review passed (or the person passed), is adoptable; anything else is not."""

    if (reference.method_id, int(reference.version), reference.content_hash) in seeded:
        return None
    review = review_of(store, mission_id, reference)
    if review.passed:
        return None
    row: dict[str, Any] = {"method_ref": reference.to_json(), "review": review.state}
    if review.record is not None:
        row["findings"] = findings_of(review.record)
    if review.ruling is not None:
        row["human_ruling"] = {"decision": review.ruling.get("decision")}
    return row


def seed_keys(seeds: Iterable[Any]) -> set[tuple[str, int, str]]:
    return {(item.method_id, int(item.version), item.content_hash) for item in seeds}


def unreviewed_adopted_methods(store: Any, mission_id: str, drafts: Iterable[Any],
                               seeds: Iterable[Any] = ()) -> list[dict[str, Any]]:
    """Of the method instances a plan commit adopts: the ones the gate refuses.

    A method is adopted in a Mission only on a passed (or person-passed) official
    METHOD_PLAN record **of this Mission** — whoever wrote it and wherever it came from
    (阶段 C3：复用也要审).  The only methods let through without one are the deployment's
    own seed methods, which the planning world installed and names in ``seeds``; a method
    of another Mission, or one that reached the registry any other way, has no review
    here and is refused with ``review: NONE``.
    """

    seeded = seed_keys(seeds)
    return [row for draft in drafts
            if (row := adoption_refusal(store, mission_id, draft.method_ref, seeded)) is not None]


def _proposals(store: Any, mission_id: str) -> tuple[list[Any], dict[str, Any]]:
    proposed: list[Any] = []
    reviewed: dict[str, Any] = {}
    for event in store.iter_events(mission_id):
        if event.type == PROPOSED and event.payload.get("assurance_review_key"):
            proposed.append(event)
        elif event.type == REVIEWED:
            reviewed[str(event.payload.get("decision_id"))] = event
    return proposed, reviewed


def open_proposals(store: Any, mission_id: str) -> list[Any]:
    """The proposals whose review has not been concluded into an event yet."""

    proposed, reviewed = _proposals(store, mission_id)
    return [event for event in proposed if str(event.payload.get("decision_id")) not in reviewed]


def awaiting(store: Any, mission_id: str) -> bool:
    """A proposed method is out for review and nothing has been concluded about it."""

    if store.count_events(mission_id, PROPOSED) == 0:
        return False
    return bool(open_proposals(store, mission_id))


def reviews_by_method(store: Any, mission_id: str) -> dict[tuple[str, int, str], dict[str, Any]]:
    """Per method: the latest thing the Planner is told about its review."""

    if store.count_events(mission_id, PROPOSED) == 0:
        return {}
    proposed, reviewed = _proposals(store, mission_id)
    out: dict[tuple[str, int, str], dict[str, Any]] = {}
    for event in proposed:
        ref = event.payload.get("method_ref") or {}
        key = (str(ref.get("method_id")), int(ref.get("version", 0)), str(ref.get("content_hash")))
        conclusion = reviewed.get(str(event.payload.get("decision_id")))
        if conclusion is None:
            out[key] = {"outcome": PENDING}
            continue
        payload = conclusion.payload
        out[key] = {name: payload[name] for name in ("outcome", "verdict", "findings", "human_ruling", "reason")
                    if payload.get(name) not in (None, [], "")}
    return out


def _no_verdict_reason(orch: Any, mission_id: str, review_key: str) -> str | None:
    """Why no official record will come for this review, or None while one still may.

    Only the review *call* not coming back ends here (阶段 C 第 3 条): a reply that came
    back and could not be used is on record as inconclusive after its one repair, and
    goes to the person like any other inconclusive review."""

    for item in orch._exhausted_reviews(mission_id, "assurance-method-plan:"):
        if item["review_key"] == review_key:
            if item["interrupted"]:
                # 阶段 B 裁决第 6 类：如实说出审阅调用没回来，不说成审阅员判不了
                calls = orch.store.connection.execute(
                    "SELECT COUNT(*) FROM assurance_review_invocations WHERE mission_id=? AND review_key=?",
                    (mission_id, review_key)).fetchone()[0]
                return (f"the review call got no reply {int(calls)} time(s) (interrupted by a restart, "
                        "or it never came back before its deadline); no verdict was given")
            return item["reason"] or "the review's retries ran out without a readable verdict"
    rows = orch.store.connection.execute(
        "SELECT dispatch_intent_id FROM assurance_review_invocations WHERE mission_id=? AND review_key=?",
        (mission_id, review_key)).fetchall()
    intents = [orch.store.get_intent(str(row[0])) for row in rows]
    if not intents or any(intent is None or intent.state not in {"SETTLED", "FAILED"} for intent in intents):
        return None
    if orch._has_pending_assurance_work(mission_id):
        return None
    return "the review ended without an official record"


def _ruling_question_stale(store: Any, record: Any) -> bool:
    """The person was asked to rule on this record and the question was retired unanswered."""

    from ..storage.planning_human_store import PlanningHumanStore

    row = PlanningHumanStore(store).get("adjudicate-method:" + str(record.record_id))
    return row is not None and row["state"] == "STALE"


def advance(orch: Any, mission: Any) -> bool:
    """Conclude every proposal whose review can be concluded; True when something moved.

    One ``PlanningMethodReviewed`` per proposal, keyed by the proposal's decision id.
    Harness only reports: the verdict, the reviewer's words, the person's ruling.
    Whether to revise the method, choose another or ask the person is the Planner's.
    """

    from ..contracts.htn import MethodRef
    from .hierarchical_dispatch import append_hierarchical_event

    progressed = False
    for event in open_proposals(orch.store, mission.id):
        payload = event.payload
        decision_id = str(payload["decision_id"])
        reference = MethodRef.from_json(payload["method_ref"])
        review = review_of(orch.store, mission.id, reference)
        if review.state == AWAITING_PERSON:
            subject_task = str(payload.get("subject_task_id") or "")
            if orch._ask_person_to_adjudicate(
                    mission, review.record, target_id=subject_task, subject_key=subject_task,
                    decision_id="adjudicate-method:" + str(review.record.record_id),
                    intro="新做法「" + str(reference.method_id) + "」的审阅两位审阅员都判不下来，"
                          "需要你裁决这个做法能不能用。",
                    extra={"package_id": review.package_id, "method_ref": reference.to_json()}):
                progressed = True
            review = review_of(orch.store, mission.id, reference)
            if review.state == AWAITING_PERSON and not _ruling_question_stale(orch.store, review.record):
                continue
        conclusion: dict[str, Any]
        if review.state == AWAITING_PERSON:
            # 裁决题在回答前过期（计划、要求或管理纪元变了）：这道题不会再有答案，如实报
            # "没有结论"，接下来怎么办由规划器定。不重发——那是替规划器决定"还要问"。
            conclusion = {"outcome": NO_VERDICT, "verdict": str(review.record.verdict),
                          "record_id": str(review.record.record_id),
                          "findings": findings_of(review.record),
                          "reason": "the question asking the person to rule on this review went stale "
                                    "before it was answered (the plan, the requirements or the "
                                    "management epoch changed); no ruling was given"}
        elif review.record is None:
            reason = _no_verdict_reason(orch, mission.id, str(payload["assurance_review_key"]))
            if reason is None:
                continue
            conclusion = {"outcome": NO_VERDICT, "verdict": None, "record_id": None,
                          "findings": [], "reason": reason}
        else:
            conclusion = {"outcome": review.state, "verdict": str(review.record.verdict),
                          "record_id": str(review.record.record_id),
                          "findings": [] if review.record.verdict is ReviewVerdict.ACCEPT
                          else findings_of(review.record)}
            if review.ruling is not None:
                conclusion["human_ruling"] = {"decision": review.ruling.get("decision"),
                                              "principal_id": review.ruling.get("principal_id")}
        with orch.store.transaction():
            append_hierarchical_event(
                orch.store, REVIEWED, mission.id, key=decision_id,
                payload={"decision_id": decision_id, "method_ref": reference.to_json(),
                         "subject_task_id": payload.get("subject_task_id"),
                         "review_key": payload["assurance_review_key"],
                         "package_id": review.package_id, **conclusion})
        orch._note(f"mission {mission.id}: method review of {reference.method_id}@{int(reference.version)} "
                   f"concluded {conclusion['outcome']}")
        progressed = True
    return progressed


__all__ = (
    "AWAITING_PERSON",
    "NO_VERDICT",
    "PASSED",
    "PENDING",
    "PROPOSED",
    "REJECTED",
    "REVIEWED",
    "REVIEW_REQUIRED",
    "MethodReview",
    "advance",
    "awaiting",
    "findings_of",
    "open_proposals",
    "review_of",
    "reviews_by_method",
    "adoption_refusal",
    "seed_keys",
    "unreviewed_adopted_methods",
)
