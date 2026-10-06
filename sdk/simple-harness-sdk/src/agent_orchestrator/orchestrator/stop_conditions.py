# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""任务自带的两种计数型停止条件（原计划 §5 ``stop_conditions``、§19.1；第 2 批车道 H，H06）。

此前 ``Mission.stop_conditions`` 只存不读。这里是它的读方——只认两个名字，Harness 只做计数与上限，
不判语义：

* ``no_new_knowledge``：连续多少个**规划轮**之间知识库（``KnowledgeCommitted``）与验收记录
  （``AcceptanceCommitted``）都没有新增。一个规划轮以一次提交成功的规划决定
  （``PlanningDecisionEvaluated`` / ``COMMITTED``）为界；还没关上的那一轮不算。
* ``result_duplication``：结果**内容哈希**的重复率——有产物就按产物内容哈希的集合算，没有产物
  按结果里的原话算；结果总数不足部署政策的最少结果数时不算。

两条都是用户建任务时可配的：条件名后带 ``=值`` 覆盖阈值，不带就用部署政策的默认值。达到上限
不直接停：主循环把事实交给规划器一次（现有停滞路径同一条通道），同一版计划规划器了结了请求又没
改动，才按 :class:`MissionStopReason` 里对应的名字停。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..contracts.models import ContractError
from ..contracts.state_machines import MissionStopReason

NO_NEW_KNOWLEDGE = "no_new_knowledge"
RESULT_DUPLICATION = "result_duplication"
NAMES = (NO_NEW_KNOWLEDGE, RESULT_DUPLICATION)
#: 规划轮的界：一次提交成功的规划决定。
ROUND_EVENT = "PlanningDecisionEvaluated"
#: 算"新知识"的事件：知识库新增、验收记录新增。
NEW_KNOWLEDGE_EVENTS = frozenset({"KnowledgeCommitted", "AcceptanceCommitted"})
#: 请求幂等键前缀：``stop-condition:<条件>:<任务>:<计划版本>``，同一版计划每个条件只问一次。
STOP_PREFIX = "stop-condition:"

_REASONS = {
    NO_NEW_KNOWLEDGE: MissionStopReason.NO_NEW_KNOWLEDGE,
    RESULT_DUPLICATION: MissionStopReason.RESULT_DUPLICATION,
}


def reason_for(name: str) -> MissionStopReason:
    return _REASONS[name]


def parse_stop_conditions(conditions: Iterable[str], deployment: Any) -> dict[str, int | float]:
    """任务配的两种计数型停止条件 → 条件名 → 阈值。

    别的条件名（``verification_passed`` 等自由文本）原样放过；这两个名字后面的 ``=值`` 必须是
    合法阈值，否则 :class:`ContractError`。``deployment`` 为 None 时只校验格式，不带默认值的条目
    阈值为 None（建任务时的请求校验用）。
    """
    parsed: dict[str, Any] = {}
    for item in conditions:
        name, sep, raw = str(item).partition("=")
        name = name.strip()
        if name not in NAMES:
            continue
        if not sep:
            if deployment is None:
                parsed[name] = None
            elif name == NO_NEW_KNOWLEDGE:
                parsed[name] = int(deployment.no_new_knowledge_rounds)
            else:
                parsed[name] = float(deployment.result_duplication_rate)
            continue
        raw = raw.strip()
        try:
            if name == NO_NEW_KNOWLEDGE:
                value: int | float = int(raw)
                if value < 1:
                    raise ValueError(raw)
            else:
                value = float(raw)
                if not 0 < value <= 1:
                    raise ValueError(raw)
        except ValueError as error:
            raise ContractError(
                f"stop condition {name!r} has an invalid threshold {raw!r}"
                + (" (an integer >= 1)" if name == NO_NEW_KNOWLEDGE else " (a rate within (0, 1])")
            ) from error
        parsed[name] = value
    return parsed


#: 系统自己对"非模型原因失败"的原地重试（2026-09-28 用户决定，不计次数）：它不是规划器的一轮。
SYSTEM_RETRY_ORIGIN = "system_infrastructure_retry"


def knowledge_streak(events: Iterable[Any]) -> int:
    """连续多少个已关上的规划轮没有新知识、没有新验收。只数事件，不读内容。

    系统自己做的原地重试决定（``decision_origin == SYSTEM_RETRY_ORIGIN``）不算一轮：既不往上数，
    也不清零——执行者连续结果不明只走"非模型原因失败到上限"那条路。
    """
    streak = 0
    open_round = False
    fresh = 0
    for event in events:
        kind = event.type
        if (kind == ROUND_EVENT and event.payload.get("status") == "COMMITTED"
                and event.payload.get("decision_origin") != SYSTEM_RETRY_ORIGIN):
            if open_round:
                streak = 0 if fresh else streak + 1
            open_round, fresh = True, 0
        elif kind in NEW_KNOWLEDGE_EVENTS:
            fresh += 1
    return streak


def result_content_hash(artifact_hashes: Sequence[str], summary: str) -> str:
    """一份结果的内容哈希：有产物看产物内容（与顺序无关），没有产物看原话。"""
    if artifact_hashes:
        body = "artifacts:" + ",".join(sorted(str(item) for item in artifact_hashes))
    else:
        body = "summary:" + str(summary or "")
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


#: 用户改要求落地的事件；它的 ``requirements_revision`` 与时刻决定每份结果交到哪一版要求之下。
AMENDMENT_EVENT = "RequirementsAmended"


def result_hashes(store: Any, mission_id: str, *, events: Iterable[Any] = ()) -> list[tuple[int, str]]:
    """任务里每份已提交结果的 ``(交到时生效的要求版本, 内容哈希)``，按收到的先后。

    要求版本只按顺序算（第 1 版起，每个改要求事件之后换成它写的版本号），不读内容：
    原计划 §18 / §19.1 说的"结果重复率"是搜索原地打转；用户改要求后被重做的步骤交出同样的
    内容，是在回答新问题，不与旧版要求下的结果比。
    """
    landed = sorted((float(event.created_at), int(event.payload.get("requirements_revision") or 0))
                    for event in events if event.type == AMENDMENT_EVENT)

    def revision_at(moment: float) -> int:
        current = 1
        for at, revision in landed:
            if at <= moment and revision > current:
                current = revision
        return current

    rows: list[tuple[float, int, str]] = []
    for task in store.list_tasks(mission_id):
        for attempt in store.list_attempts(task.id):
            stored = store.find_result_for_attempt(attempt.id)
            if stored is None:
                continue
            hashes = []
            for artifact_id in stored.envelope.artifacts:
                artifact = store.get_artifact(artifact_id)
                if artifact is not None:
                    hashes.append(artifact.content_hash)
            received = float(stored.received_at)
            rows.append((received, revision_at(received), result_content_hash(hashes, stored.envelope.summary)))
    return [(revision, digest) for _, revision, digest in sorted(rows, key=lambda item: item[0])]


def duplicate_count(hashes: Sequence[Any]) -> tuple[int, int]:
    """(与同一版要求下更早结果内容相同的结果数, 结果总数)。元素是 ``(要求版本, 哈希)``；
    只给哈希时当作同一版。"""
    seen: set[tuple[int, str]] = set()
    duplicates = 0
    for item in hashes:
        key = (int(item[0]), str(item[1])) if isinstance(item, tuple) else (0, str(item))
        if key in seen:
            duplicates += 1
        seen.add(key)
    return duplicates, len(hashes)


def reached_stop_conditions(mission: Any, deployment: Any, *, streak: int,
                            hashes: Sequence[str]) -> list[dict[str, Any]]:
    """达到或超过上限的条件，每条带它的计数与上限（给规划器与停机报告的事实）。"""
    limits = parse_stop_conditions(mission.stop_conditions, deployment)
    reached: list[dict[str, Any]] = []
    rounds = limits.get(NO_NEW_KNOWLEDGE)
    if rounds is not None and streak >= rounds:
        reached.append({"condition": NO_NEW_KNOWLEDGE, "rounds_without_new_knowledge": int(streak),
                        "limit": int(rounds)})
    rate = limits.get(RESULT_DUPLICATION)
    if rate is not None:
        duplicates, total = duplicate_count(hashes)
        if total >= int(deployment.result_duplication_min_results) and duplicates / total >= rate:
            reached.append({"condition": RESULT_DUPLICATION, "duplicate_results": duplicates,
                            "results": total, "rate": round(duplicates / total, 4), "limit": float(rate)})
    return reached


def stop_condition_key(mission_id: str, name: str, plan_revision: int) -> str:
    return f"{STOP_PREFIX}{name}:{mission_id}:{int(plan_revision)}"


def stop_condition_asks(events: Iterable[Any], name: str) -> int:
    """这个任务为这个条件问过规划器几次（跨计划版本累计；只数事件）。"""
    prefix = f"{STOP_PREFIX}{name}:"
    return sum(1 for event in events if event.type == "PlanningRepairRequested"
               and str(event.payload.get("source_key", "")).startswith(prefix))


def stop_request_state(events: Iterable[Any], source_key: str) -> Mapping[str, Any] | None:
    """这一版计划为这个条件记的请求：请求编号、规划器了结了没有。没记过是 None。"""
    request_id = None
    handled: set[str] = set()
    for event in events:
        if event.type == "PlanningRepairRequested" and event.payload.get("source_key") == source_key:
            request_id = str(event.payload["request_id"])
        elif event.type == "PlanningRepairAddressed":
            handled.update(str(item) for item in event.payload.get("repair_request_ids", ()))
    if request_id is None:
        return None
    return {"request_id": request_id, "addressed": request_id in handled}


__all__ = (
    "NAMES",
    "NEW_KNOWLEDGE_EVENTS",
    "NO_NEW_KNOWLEDGE",
    "RESULT_DUPLICATION",
    "ROUND_EVENT",
    "AMENDMENT_EVENT",
    "STOP_PREFIX",
    "SYSTEM_RETRY_ORIGIN",
    "duplicate_count",
    "knowledge_streak",
    "parse_stop_conditions",
    "reached_stop_conditions",
    "reason_for",
    "result_content_hash",
    "result_hashes",
    "stop_condition_asks",
    "stop_condition_key",
    "stop_request_state",
)
