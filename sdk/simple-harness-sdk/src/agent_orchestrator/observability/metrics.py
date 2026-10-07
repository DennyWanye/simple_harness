# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Metrics (§23.2, theory 12-3 / 12-14, plan D6-10 / D6-11').

What can be filled from this build's records: system health (Attempt outcomes,
timeouts, backpressure transitions and the peak queue observations), cost (tokens by
role and by runtime profile; orchestration records no money), knowledge reuse, verification pass rate, Mission
duration, and the role distribution against §29.2's starting mix (all eight §9.2 roles,
``null`` where the original gives no share).

推后第 3 批 U05（§23.2 表的其余各项）：一律按事件或表行计数，不读任何正文。

* 新思路数 / 剪枝率：各次 ``PlanRevisionCommitted`` 里采用 / 退掉的做法实例个数（分支 = 做法实例）。
* 重复率：H06 的结果内容哈希计数（``stop_conditions.result_hashes`` / ``duplicate_count``）。
* 知识污染率：已验证知识被新结论顶出争议（``ClaimDisputed`` 且对方当时为 ``VERIFIED``）的不同条目数
  ÷ ``KnowledgeCommitted`` 数。
* 误报率：根终审打回（``HierarchicalRootReviewRejected``）÷ 根终审送审（``HierarchicalRootReviewCut``）。
  根终审只在全部叶子验收通过后才送审；它打回，是"下层都判通过、独立上层判不通过"的事件事实。
* 审阅积压：本任务现在待审的结果数、部署待审峰值、待审维升起次数、积压应对变化次数（H12）。
* 任务成功率 / 每成功任务成本：单个任务给结局与 token；全库见 :func:`deployment_outcomes`。"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..storage.store import Store

METRICS_VERSION = "metrics-v2"
#: 任务结局事件 → 结局名（全库成功率按这三类事件计数）。
OUTCOME_EVENTS = {"MissionCompleted": "completed", "MissionFailed": "failed", "MissionCancelled": "cancelled"}


def _rate(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(part / whole, 4)


def _service_role(subject_id: str) -> str:
    for marker, role in ((":planner:", "planner"), (":critic", "critic")):
        if marker in subject_id:
            return role
    if "-judge-" in subject_id or ":judge" in subject_id:  # the Mission-level judgment Critic
        return "critic"
    return "service"


def metrics(store: Store, mission_id: str) -> dict[str, Any]:
    attempts = [a for t in store.list_tasks(mission_id) for a in store.list_attempts(t.id)]
    by_id = {a.id: a for a in attempts}
    statuses = Counter(str(a.status) for a in attempts)
    intents = {
        i.subject_id: i
        for i in store.list_intents(
            "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED", "SETTLED", "FAILED"
        )
        if i.mission_id == mission_id
    }
    tokens_by_role: Counter[str] = Counter()
    tokens_by_profile: Counter[str] = Counter()
    rows = store.connection.execute(
        "SELECT subject_id, input_tokens + output_tokens FROM imported_usage"
        " WHERE mission_id = ?",
        (mission_id,),
    ).fetchall()
    for subject_id, tokens in rows:
        attempt = by_id.get(subject_id)
        if attempt is not None:
            role, profile = attempt.role, attempt.runtime_profile_id
        else:
            intent = intents.get(subject_id)
            role = _service_role(subject_id)
            profile = str((intent.config if intent else {}).get("runtime_profile_id", "default"))
        tokens_by_role[role] += int(tokens)
        tokens_by_profile[profile] += int(tokens)
    passed = store.count_events(mission_id, "VerificationPassed")
    failed = store.count_events(mission_id, "VerificationFailed")
    # 先取成元组：下面要按不同条件读好几遍（此前是迭代器，读完结束时刻后剩下的事件已被消费掉）
    events = tuple(store.iter_events(mission_id))
    created = next((e.created_at for e in events if e.type == "MissionCreated"), None)
    ended = next(
        (
            e.created_at
            for e in events
            if e.type in {"MissionCompleted", "MissionFailed", "MissionCancelled"}
        ),
        None,
    )
    backpressure = store.get_scheduler_state("backpressure") or {}
    peaks = {str(k): int(v) for k, v in dict(backpressure.get("peaks") or {}).items()}
    critic_turns = sum(
        1 for s, i in intents.items() if _service_role(s) == "critic" and i.state == "SETTLED"
    )
    role_counts = Counter(a.role for a in attempts)
    role_counts["critic"] += critic_turns
    total_roles = sum(role_counts.values()) or 1
    role_mix = {
        role: {
            "observed": count,
            "observed_share": round(count / total_roles, 4),
        }
        for role, count in sorted(role_counts.items())
    }
    approvals = store.list_approvals(mission_id)
    actions = store.list_actions(mission_id)
    from ..orchestrator.stop_conditions import duplicate_count, result_hashes

    adopted = sum(len(e.payload.get("adopted_method_instances") or ())
                  for e in events if e.type == "PlanRevisionCommitted")
    retired = sum(len(e.payload.get("retired_method_instances") or ())
                  for e in events if e.type == "PlanRevisionCommitted")
    duplicates, results = duplicate_count(result_hashes(store, mission_id, events=events))
    knowledge_committed = sum(1 for e in events if e.type == "KnowledgeCommitted")
    polluted = len({str(e.payload.get("contradicts")) for e in events
                    if e.type == "ClaimDisputed" and e.payload.get("other_status") == "VERIFIED"})
    root_cuts = sum(1 for e in events if e.type == "HierarchicalRootReviewCut")
    root_rejections = sum(1 for e in events if e.type == "HierarchicalRootReviewRejected")
    pending_now = sum(1 for stored in store.list_results_by_verification("PENDING", "RUNNING")
                      if stored.envelope.mission_id == mission_id)
    mission_row = store.get_mission(mission_id)
    status = None if mission_row is None else str(mission_row.status)
    succeeded = {"COMPLETED": True, "FAILED": False, "CANCELLED": False}.get(str(status))
    total_tokens = sum(int(tokens) for _, tokens in rows)
    return {
        "version": METRICS_VERSION,
        "mission_id": mission_id,
        "human": {  # step 7 (D7-11): where people came in, and how long they took
            "requests": dict(Counter(f"{r['kind']}:{r['state']}" for r in approvals)),
            "decisions": sum(len(store.list_decisions(r["request_id"])) for r in approvals),
            "human_escalations": sum(
                1 for r in approvals if r["kind"] == "review" and r.get("reason") == "needs_human"
            ),
            "overrides": len(store.list_overrides(mission_id)),
            "comments": store.count_events(mission_id, "HumanCommentAdded"),
            "human_wait_seconds": round(store.human_wait_seconds(mission_id, store.now), 3),
        },
        "actions": {
            "by_state": dict(Counter(str(a["state"]) for a in actions)),
            "handoffs": sum(int(a.get("handoffs") or 0) for a in actions),
        },
        "health": {
            "attempts": len(attempts),
            "attempts_by_status": dict(statuses),
            "timed_out": statuses.get("TIMED_OUT", 0),
            "lost": statuses.get("LOST", 0),
            "tool_calls_rejected": store.count_events(mission_id, "ToolCallRejected"),
            "backpressure_transitions": store.count_events(mission_id, "BackpressureRaised")
            + store.count_events(mission_id, "BackpressureCleared"),
            "peak_observed": peaks,
            "profile_unavailable_events": store.count_events(
                mission_id, "RuntimeProfileUnavailable"
            ),
        },
        "cost": {
            "tokens_by_role": dict(tokens_by_role),
            "tokens_by_profile": dict(tokens_by_profile),
        },
        "verification": {
            "passed": passed,
            "failed": failed,
            "pass_rate": None if passed + failed == 0 else round(passed / (passed + failed), 4),
            "root_reviews": root_cuts,
            "root_review_rejections": root_rejections,
            "false_positive_rate": _rate(root_rejections, root_cuts),
            "backlog": {
                "pending_now": pending_now,
                "deployment_peak": peaks.get("pending_verifications", 0),
                "raised": sum(1 for e in events if e.type == "BackpressureRaised"
                              and e.payload.get("dimension") == "pending_verifications"),
                "responses": sum(1 for e in events if e.type == "BacklogResponseChanged"),
            },
        },
        "search": {
            "new_approaches": adopted,
            "retired_approaches": retired,
            "prune_rate": _rate(retired, adopted),
            "duplicate_results": duplicates,
            "results": results,
            "duplicate_rate": _rate(duplicates, results),
        },
        "knowledge": {
            "reuse_events": store.count_events(mission_id, "KnowledgeUsed"),
            "committed": knowledge_committed,
            "polluted": polluted,
            "pollution_rate": _rate(polluted, knowledge_committed),
        },
        "outcome": {
            "status": status,
            "succeeded": succeeded,
            "tokens": total_tokens,
            "cost_per_success": total_tokens if succeeded else None,
        },
        "mission": {
            "duration_seconds": None
            if created is None or ended is None
            else round(ended - created, 3),
            "escalations": sum(
                1 for e in events if e.type == "ModelRouted" and e.payload.get("escalated_from")
            ),
        },
        "role_mix": role_mix,
    }


def deployment_outcomes(store: Store) -> dict[str, Any]:
    """全库的任务成功率与每成功任务成本（§23.2"Mission 成功率""每成功任务成本"）。

    按结局事件计数（每个任务至多一种结局）；成本 = 已结束任务的 token 合计 ÷ 成功任务数（失败任务的
    花费也摊进去）。只有计数与合计，不含任何任务的内容。编排只记 token，不记金额。"""

    names = sorted(OUTCOME_EVENTS)
    rows = store.connection.execute(
        "SELECT type, COUNT(DISTINCT mission_id) FROM events"
        f" WHERE type IN ({','.join('?' for _ in names)}) GROUP BY type",
        names,
    ).fetchall()
    counts = {OUTCOME_EVENTS[str(kind)]: int(count) for kind, count in rows}
    ended = sum(counts.values())
    completed = counts.get("completed", 0)
    tokens = int(store.connection.execute(
        "SELECT COALESCE(SUM(u.input_tokens + u.output_tokens), 0) FROM imported_usage u"
        " WHERE u.mission_id IN (SELECT DISTINCT mission_id FROM events"
        f" WHERE type IN ({','.join('?' for _ in names)}))",
        names,
    ).fetchone()[0])
    return {
        "missions_ended": ended,
        "completed": completed,
        "failed": counts.get("failed", 0),
        "cancelled": counts.get("cancelled", 0),
        "success_rate": _rate(completed, ended),
        "tokens_of_ended": tokens,
        "cost_per_success": None if completed == 0 else round(tokens / completed, 4),
    }


__all__ = ("METRICS_VERSION", "OUTCOME_EVENTS", "deployment_outcomes", "metrics")
