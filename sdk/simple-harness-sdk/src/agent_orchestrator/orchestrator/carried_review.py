# SPDX-License-Identifier: Apache-2.0
"""改要求后沿用已验收的叶子：按新要求重审（TaskGraph 补全第四批）。

用户改了要求、新计划提交后，现行计划里的普通步骤若只按旧版要求通过、现行要求下不算数，
系统就请审阅员按新要求把**同一份结果**再审一次——不建尝试、不调执行者、不动尝试 / 任务 /
产物状态。触发只看事实：步骤留在现行计划里（原样留着，或换做法时被点名共用）。规划器不想
沿用，就照旧换掉这一步或换做法。

重审按数据先后：一步读的上游在现行要求下都算数后才审它；上游没过，下游不审，原样等规划器。
每一步每个要求版本只审一次——审阅绑定、验证记录、验收都以（结果, 要求版本）为键。

这里只做"找出该重审的步骤"这一件事（秩序）；审得过不过由审阅员定。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..contracts.htn import TaskForm
from ..graph.eligibility import OccurrenceOutcome
from ..storage.htn_store import HtnStore
from ..storage.store import Store

#: 修复请求里的原因：沿用的结果按新要求重审没过。
CARRIED_RESULT_REJECTED = "CARRIED_RESULT_REJECTED"
#: 派发处的细分原因：这一步的结果按旧版要求通过，正等审阅员按新要求重审。
CARRIED_REVIEW_PENDING = "CARRIED_REVIEW_PENDING"
#: 派发处的细分原因：这一步除了要求之外也变了（比如输入改接过），旧结果沿用不了，要规划器换掉它。
CARRIED_RESULT_NOT_KEPT = "CARRIED_RESULT_NOT_KEPT"
#: 重审连续出错（检查或审阅员这一层跑不起来）到这个次数，就按"没有结论"交给规划器，不再重来。
MAX_CARRIED_REVIEW_ERRORS = 3


def source_key(result_id: str, requirements_revision: int) -> str:
    """The one repair request a (result, requirements revision) review can end in."""
    return f"carried-review:{result_id}:r{int(requirements_revision)}"


@dataclass(frozen=True, slots=True)
class CarriedReview:
    """One kept step whose accepted result is to be reviewed under the current requirements."""

    mission_id: str
    task_id: str
    occurrence_id: str
    result_id: str
    requirements_revision: int

    @property
    def key(self) -> str:
        return f"carried:{self.result_id}:r{self.requirements_revision}"


def carried_reviews(store: Store, dispatch: Any, mission_id: str) -> list[CarriedReview]:
    """The kept steps due for a review under the current requirements, in data order."""

    from .assurance_validity import acceptance_id_for
    from .completion_inputs import frozen_requirements_revision
    from .operation_completion import OperationCompletionError
    from .taskgraph_outcomes import read_taskgraph_outcomes

    htn = HtnStore(store)
    latest = htn.latest_requirements_revision(mission_id)
    active = htn.active_plan_revision(mission_id)
    if latest is None or active is None or int(latest.revision) <= 1:
        return []
    revision = int(latest.revision)
    try:
        network = dispatch.network(mission_id)
        outcomes = read_taskgraph_outcomes(store, mission_id, network)
    except Exception:  # noqa: BLE001 - an unreadable plan is reported by this Mission's own round
        return []
    producers: dict[str, set[str]] = {}
    for item in network.data_requirements:
        producers.setdefault(str(item.consumer_occurrence), set()).add(str(item.producer_occurrence))
    due: list[CarriedReview] = []
    for member in htn.list_plan_memberships(mission_id, active.revision):
        occurrence = str(member.occurrence_id)
        if str(member.form) != str(TaskForm.PRIMITIVE) or outcomes.get(member.occurrence_id) is OccurrenceOutcome.ACCEPTED:
            continue
        task = store.get_task(str(member.task_id))
        stored = None if task is None or not task.accepted_result_id else store.get_result(task.accepted_result_id)
        if stored is None or stored.verification_state != "DONE" or stored.verdict != "PASS":
            continue
        try:
            if frozen_requirements_revision(store, stored) >= revision:
                continue
        except OperationCompletionError:
            continue
        result_id = str(stored.envelope.id)
        if store.connection.execute(
                "SELECT 1 FROM acceptances WHERE acceptance_id=?",
                (acceptance_id_for(task.id, result_id, revision),)).fetchone() is not None:
            continue
        if rejected(store, result_id, revision):
            continue  # 已经审过没过：原样等规划器处理
        upstream = producers.get(occurrence, set())
        if any(outcomes.get(producer) is not OccurrenceOutcome.ACCEPTED for producer in upstream):
            continue  # 上游在现行要求下还不算数：先审上游
        if not kept(store, result_id, revision):
            continue  # 这一步除要求外也变了：不是"沿用"，派发处如实报出来，由规划器定
        due.append(CarriedReview(mission_id, str(task.id), occurrence, result_id, revision))
    return due


def rejected(store: Store, result_id: str, requirements_revision: int) -> bool:
    """This result's review under this revision has ended without an acceptance: its one
    repair request was recorded (a check failed, the reviewer found it short, the person
    ruled against it, or the review kept erroring).  The durable end of the scan."""
    stored = store.get_result(result_id)
    if stored is None:
        return False
    return store.connection.execute(
        "SELECT 1 FROM events WHERE mission_id=? AND type='PlanningRepairRequested'"
        " AND json_extract(payload_json,'$.source_key')=? LIMIT 1",
        (stored.envelope.mission_id, source_key(result_id, requirements_revision))).fetchone() is not None


def kept(store: Store, result_id: str, requirements_revision: int) -> bool:
    """Whether this accepted result can be reviewed under this revision at all: only the
    requirements moved under its step (same Task contract, duty, effects and data edges)."""
    from .completion_inputs import load_completion_result_inputs
    from .operation_completion import OperationCompletionError

    stored = store.get_result(result_id)
    if stored is None:
        return False
    try:
        load_completion_result_inputs(store, stored, requirements_revision=requirements_revision)
    except OperationCompletionError:
        return False
    return True


__all__ = ("CARRIED_RESULT_NOT_KEPT", "CARRIED_RESULT_REJECTED", "CARRIED_REVIEW_PENDING",
           "MAX_CARRIED_REVIEW_ERRORS", "CarriedReview", "carried_reviews", "kept", "rejected", "source_key")
