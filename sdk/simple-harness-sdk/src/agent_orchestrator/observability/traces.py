# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Contribution attribution (original §23.3 "需要记录哪些知识位于最终成功路径上";
theory 12 §16 / 13 §17 Credit Assignment; ORCH §10.2 ``observability/traces.py``; plan
D8-4 / D8-4').

From the Mission's final products — what the root ``GoalResolution`` was formed out of:
its contribution list (the CURRENT acceptances of the adopted plan) and, recursively, the
contribution lists of its child resolutions — to the Task, Attempt, Agent, role, model and
prompt version that produced them, the verification layers that let them through, the
knowledge path (``lineage``) and the actions and people on the way.  Everything that did
not reach the success path is listed as exploration spending with its reason, and the
Mission's imported usage is split row by row into path / exploration / services with an
"unclassified" bucket that must stay empty.  A record that is missing is reported as a
break — never bridged.

第 2 批车道 H（T10，原计划 §18.4 / §23.3 / §30.5；v1.4 R51）：分层计划下 ``Task.dependency_ids``
恒空，成功路径不再沿平面依赖链合并（被换掉做法下完成的步骤会被算进去；先后两步写同一文件会报
"集成树冲突"断点），改为以根结论的贡献清单为起点递归取贡献链。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .lineage import lineage
from .metrics import _service_role

if TYPE_CHECKING:
    from ..storage.store import Store

ATTRIBUTION_VERSION = "attribution-v2"
#: 根结论与子结论的提交事件（``resolution_commits``）：贡献清单就记在它的载荷里。
GOAL_RESOLUTION_COMMITTED = "GoalResolutionCommitted"


@dataclass(frozen=True, slots=True)
class ContributionChain:
    """根结论往下递归读出的贡献链：验收记录编号（根先、子后，去重）、每级目标结论、断点。"""

    acceptance_ids: tuple[str, ...]
    goal_chain: tuple[dict[str, Any], ...]
    breaks: tuple[dict[str, Any], ...]


def contribution_chain(resolutions: Mapping[str, Mapping[str, Any]]) -> ContributionChain:
    """``resolution_id → GoalResolutionCommitted 载荷`` → 贡献链。

    起点是 ``is_mission_root`` 的那条（最后提交的一条）；没有就是 ``root_resolution`` 断点。每条结论
    的 ``contributing_acceptances``（出现 → 验收记录编号）进链，再沿 ``child_resolution_ids`` 递归；
    载荷里没有的子结论是 ``resolution_commit`` 断点，不补。贡献清单之外的结论与验收不在链上。
    """
    roots = [key for key, payload in resolutions.items() if payload.get("is_mission_root")]
    if not roots:
        return ContributionChain((), (), ({"missing": "root_resolution"},))
    acceptances: list[str] = []
    chain: list[dict[str, Any]] = []
    breaks: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(resolution_id: str) -> None:
        if resolution_id in seen:
            return
        seen.add(resolution_id)
        payload = resolutions.get(resolution_id)
        if payload is None:
            breaks.append({"missing": "resolution_commit", "id": resolution_id})
            return
        contributions = payload.get("contributing_acceptances") or {}
        listed = {str(occurrence): [str(item) for item in contributions[occurrence]]
                  for occurrence in sorted(contributions)}
        chain.append({
            "resolution_id": resolution_id,
            "goal_task_id": payload.get("goal_task_id"),
            "obligation_id": payload.get("obligation_id"),
            "is_mission_root": bool(payload.get("is_mission_root")),
            "child_resolution_ids": [str(item) for item in payload.get("child_resolution_ids") or ()],
            "contributing_acceptances": listed,
        })
        for ids in listed.values():
            for acceptance_id in ids:
                if acceptance_id not in acceptances:
                    acceptances.append(acceptance_id)
        for child in payload.get("child_resolution_ids") or ():
            visit(str(child))

    visit(roots[-1])
    return ContributionChain(tuple(acceptances), tuple(chain), tuple(breaks))


def read_contribution_chain(store: Store, mission_id: str) -> ContributionChain:
    """从任务的事件里读根结论及其子结论的提交载荷，再算贡献链。"""
    resolutions = {
        str(event.payload["resolution_id"]): dict(event.payload)
        for event in store.iter_events(mission_id)
        if event.type == GOAL_RESOLUTION_COMMITTED and event.payload.get("resolution_id")
    }
    return contribution_chain(resolutions)


def _bucket() -> dict[str, Any]:
    return {"tokens": 0, "rows": 0}


def _add(bucket: dict[str, Any], tokens: int) -> None:
    bucket["tokens"] += int(tokens)
    bucket["rows"] += 1


def _usage(bucket: dict[str, Any]) -> dict[str, Any]:
    return {"tokens": bucket["tokens"], "rows": bucket["rows"]}


def attribution(store: Store, mission_id: str) -> dict[str, Any]:  # noqa: C901 - one report
    mission = store.get_mission(mission_id)
    if mission is None:
        return {
            "version": ATTRIBUTION_VERSION,
            "mission_id": mission_id,
            "breaks": [{"missing": "mission"}],
        }
    tasks = store.list_tasks(mission_id)
    by_id = {task.id: task for task in tasks}
    breaks: list[dict[str, Any]] = []
    success = str(mission.status) == "COMPLETED"

    def accepted_attempt(task: Any) -> str | None:
        if not task.accepted_result_id:
            return None
        stored = store.get_result(task.accepted_result_id)
        if stored is None:
            breaks.append({"missing": "result", "id": task.accepted_result_id, "task_id": task.id})
            return None
        return str(stored.envelope.attempt_id)

    # -- the final products and the success path: the root resolution's contribution chain
    products: list[dict[str, Any]] = []
    path_tasks: set[str] = set()
    goal_chain: list[dict[str, Any]] = []
    final_ids: set[str] = set()
    if success:
        from ..storage.htn_store import HtnStore
        from ..storage.store import StoreError

        chain = read_contribution_chain(store, mission_id)
        goal_chain = [dict(row) for row in chain.goal_chain]
        breaks.extend(dict(row) for row in chain.breaks)
        semantics = HtnStore(store)
        # 验收记录 → 产出它的步骤；一个步骤几条验收记录只算一次，顺序按贡献链
        producers: list[tuple[str, str]] = []
        for acceptance_id in chain.acceptance_ids:
            try:
                acceptance = semantics.get_acceptance(acceptance_id)
            except StoreError:
                breaks.append({"missing": "acceptance", "id": acceptance_id})
                continue
            task_id = str(acceptance.task_id)
            if task_id not in by_id:
                breaks.append({"missing": "task", "id": task_id, "acceptance_id": acceptance_id})
                continue
            path_tasks.add(task_id)
            if task_id not in {item for item, _ in producers}:
                producers.append((task_id, acceptance_id))
        for task_id, acceptance_id in producers:
            producer = by_id[task_id]
            attempt_id = accepted_attempt(producer)
            attempt = None if attempt_id is None else store.get_attempt(attempt_id)
            layers = (
                []
                if not producer.accepted_result_id
                else [
                    {
                        "layer": v["layer"],
                        "status": v["status"],
                        "verifier_version": (v.get("detail") or {}).get("verifier_version"),
                    }
                    for v in store.list_verifications(producer.accepted_result_id, requirements_revision=None)
                    if v["status"] in {"PASS", "NEEDS_HUMAN"}
                ]
            )
            for artifact_id in producer.accepted_artifacts:
                artifact = store.get_artifact(artifact_id)
                if artifact is None:
                    breaks.append({"missing": "artifact", "id": artifact_id, "task_id": task_id})
                    continue
                final_ids.add(artifact.id)
                products.append(
                    {
                        "path": artifact.path,
                        "content_hash": artifact.content_hash,
                        "artifact_id": artifact.id,
                        "task_id": task_id,
                        "acceptance_id": acceptance_id,
                        "result_id": producer.accepted_result_id,
                        "attempt_id": attempt_id,
                        "agent_id": None if attempt is None else attempt.agent_id,
                        "role": None if attempt is None else attempt.role,
                        "model": None if attempt is None else attempt.model,
                        "runtime_profile_id": None if attempt is None else attempt.runtime_profile_id,
                        "prompt_version": None if attempt is None else attempt.prompt_version,
                        "verified_by": layers,
                    }
                )
    path_task_view = [
        {
            "task_id": tid,
            "kind": by_id[tid].kind,
            "accepted_result_id": by_id[tid].accepted_result_id,
            "artifacts": [
                {
                    "artifact_id": aid,
                    "in_final_products": aid in final_ids,
                }  # overridden upstream work stays on the path
                for aid in by_id[tid].accepted_artifacts
            ],
        }
        for tid in sorted(path_tasks)
    ]

    # -- the knowledge path
    knowledge = (
        lineage(store, mission_id)
        if success
        else {"knowledge": [], "attempts": [], "edges": []}
    )
    path_attempts = {aid for tid in path_tasks if (aid := accepted_attempt(by_id[tid]))}
    path_attempts.update(str(a["attempt_id"]) for a in knowledge.get("attempts", []))

    # -- usage, row by row (plan D8-4')
    attempts = {a.id: a for t in tasks for a in store.list_attempts(t.id)}
    per_attempt: dict[str, dict[str, Any]] = defaultdict(_bucket)
    per_attempt_verification: dict[str, dict[str, Any]] = defaultdict(_bucket)
    services: dict[str, dict[str, Any]] = defaultdict(_bucket)
    unclassified = _bucket()
    unclassified_subjects: list[str] = []
    total = _bucket()
    unknown_rows = 0
    usage_by_subject: dict[str, int] = defaultdict(int)
    rows = store.connection.execute(
        "SELECT subject_id, input_tokens + output_tokens, unknown"
        " FROM imported_usage WHERE mission_id = ?",
        (mission_id,),
    ).fetchall()
    for subject, tokens, unknown in rows:
        subject = str(subject)
        tokens = int(tokens or 0)
        _add(total, tokens)
        usage_by_subject[subject] += tokens
        unknown_rows += int(bool(unknown))
        owner = subject.split(":critic:")[0] if ":critic:" in subject else None
        if subject in attempts:
            _add(per_attempt[subject], tokens)
        elif owner is not None and owner in attempts:
            _add(per_attempt_verification[owner], tokens)
        else:
            role = _service_role(subject)
            if role == "planner":
                _add(services[role], tokens)
            elif role == "critic":  # the Mission-level judgment Critic
                _add(services["judge"], tokens)
            else:
                _add(unclassified, tokens)
                unclassified_subjects.append(subject)
    tool_calls: dict[str, int] = {}
    action_reservations = []
    for subject, settled_calls, state in store.connection.execute(
        "SELECT subject_id, settled_tool_calls, state FROM budget_reservations"
        " WHERE mission_id = ?",
        (mission_id,),
    ).fetchall():
        if str(subject).startswith("action:"):
            action_reservations.append(
                {"subject_id": subject, "settled_tool_calls": settled_calls, "state": state}
            )
        else:
            tool_calls[str(subject)] = int(settled_calls or 0)

    path_bucket, exploration_bucket = _bucket(), _bucket()
    broken_tasks = {b["task_id"] for b in breaks if b.get("missing") == "result"}
    attempt_view = []
    for attempt_id, attempt in attempts.items():
        owner_task = by_id.get(attempt.task_id)
        on_path = attempt_id in path_attempts
        reason = None
        if not on_path:
            status = str(attempt.status)
            if owner_task is not None and str(owner_task.status) == "CANCELLED":
                reason = "task_superseded_or_cancelled"
            elif status == "SUPERSEDED":
                reason = "candidate_superseded"
            elif status in {"RETRY_WAIT", "LOST", "TIMED_OUT", "CANCELLED"}:
                reason = f"attempt_{status.lower()}"
            elif attempt.task_id in broken_tasks:  # review P2-6: the record is missing, say so
                reason = "record_missing"
            elif not success:
                reason = "mission_not_completed"
            else:
                reason = "not_used_by_final_products"
        work, check = (
            per_attempt.get(attempt_id, _bucket()),
            per_attempt_verification.get(attempt_id, _bucket()),
        )
        target = path_bucket if on_path else exploration_bucket
        for bucket in (work, check):
            target["tokens"] += bucket["tokens"]
            target["rows"] += bucket["rows"]
        attempt_view.append(
            {
                "attempt_id": attempt_id,
                "task_id": attempt.task_id,
                "agent_id": attempt.agent_id,
                "role": attempt.role,
                "model": attempt.model,
                "runtime_profile_id": attempt.runtime_profile_id,
                "prompt_version": attempt.prompt_version,
                "status": str(attempt.status),
                "on_success_path": on_path,
                "exploration_reason": reason,
                "work": _usage(work),
                "verification": _usage(check),
                "tool_calls": tool_calls.get(attempt_id, 0),
            }
        )
    service_total = sum(b["tokens"] for b in services.values())
    # review P1-4: reconciled = nothing unclassified, the buckets add up, the budget ledger
    # (what each settled reservation was charged) agrees subject by subject, and a
    # finished Mission has no usage left unsettled.  The ledger was summed from the same
    # usage rows at settlement, so it catches usage imported afterwards or altered — it
    # is not an independent meter (re-review)
    settled = {
        str(subject): int(tokens or 0)
        for subject, tokens in store.connection.execute(
            "SELECT subject_id, settled_tokens FROM budget_reservations"
            " WHERE mission_id = ? AND state = 'SETTLED' AND subject_id NOT LIKE 'action:%'",
            (mission_id,),
        ).fetchall()
    }
    ledger = {
        "settled_tokens": sum(settled.values()),
        "usage_of_settled_subjects": sum(usage_by_subject.get(s, 0) for s in settled),
        "unsettled_usage_tokens": sum(t for s, t in usage_by_subject.items() if s not in settled),
        "mismatched_subjects": sorted(
            s for s, t in settled.items() if usage_by_subject.get(s, 0) != t
        ),
    }
    reconciled = (
        unclassified["rows"] == 0
        and path_bucket["tokens"] + exploration_bucket["tokens"] + service_total == total["tokens"]
        and not ledger["mismatched_subjects"]
        and (
            str(mission.status) not in {"COMPLETED", "FAILED", "CANCELLED"}
            or ledger["unsettled_usage_tokens"] == 0
        )
    )

    # -- actions and people on the way (step 7)
    actions = []
    if store.has_table("actions"):
        for action in store.list_actions(mission_id, "SUCCEEDED"):
            request_id = action.get("approval_request_id")
            approvers = (
                []
                if not request_id
                else [
                    d["principal_id"]
                    for d in store.list_decisions(str(request_id))
                    if d["decision"] == "grant"
                ]
            )
            actions.append(
                {
                    "action_key": action["action_key"],
                    "target": action.get("target"),
                    "receipt_hash": (action.get("receipt") or {}).get("receipt_hash"),
                    "decision_receipts": list(action.get("decision_receipts") or []),
                    "approved_by": approvers,
                }
            )
    human = {
        "wait_seconds": round(store.human_wait_seconds(mission_id, store.now), 3)
        if store.has_table("approvals")
        else 0.0,
        "decisions": sum(
            len(store.list_decisions(r["request_id"])) for r in store.list_approvals(mission_id)
        )
        if store.has_table("approvals")
        else 0,
        "overrides": len(store.list_overrides(mission_id))
        if store.has_table("human_overrides")
        else 0,
    }
    return {
        "version": ATTRIBUTION_VERSION,
        "mission_id": mission_id,
        "mission_status": str(mission.status),
        "success_path": success,
        "final_products": products,
        "goal_chain": goal_chain,
        "path_tasks": path_task_view,
        "knowledge_path": {
            "knowledge": [k.get("id") for k in knowledge.get("knowledge", [])],
            "edges": knowledge.get("edges", []),
        },
        "attempts": attempt_view,
        "actions": actions,
        "action_reservations": action_reservations,
        "human": human,
        "cost": {
            "success_path": _usage(path_bucket),
            "exploration": _usage(exploration_bucket),
            "services": {role: _usage(b) for role, b in sorted(services.items())},
            "unclassified": {**_usage(unclassified), "subjects": unclassified_subjects},
            "total": _usage(total),
            "unknown_usage_rows": unknown_rows,
            "ledger": ledger,
            "reconciled": reconciled,
        },
        "breaks": [b for n, b in enumerate(breaks) if b not in breaks[:n]],  # review P2-6
    }


TIMELINE_TYPES = frozenset(
    {
        "AttemptCreated",
        "AttemptStarted",
        "ResultSubmitted",
        "VerificationFailed",
        "VerificationSuspended",
        "AttemptLost",
        "AttemptTimedOut",
        "OutcomeRecorded",
        "TaskFailed",
        "TaskCancelled",
        "MissionFailed",
        "ActionFailed",
        "ActionOutcomeUnknown",
        "ApprovalRejected",
    }
)


def failure_timeline(store: Store, mission_id: str) -> list[dict[str, Any]]:
    """The key events of every Task that did not complete (plan D8-3'): read from the
    library's current state and the Mission's events (HTN 补齐阶段 G：v2 投影删了)."""

    mission = store.get_mission(mission_id)
    mission_failed = mission is not None and str(mission.status) == "FAILED"
    failed = {
        task.id for task in store.list_tasks(mission_id)
        if str(task.status) in {"FAILED", "CANCELLED"}
        or (mission_failed and str(task.status) not in {"COMPLETED", "FAILED", "CANCELLED"})
    }
    lines = []
    for event in store.iter_events(mission_id):
        kind = event.type
        relevant = event.task_id in failed or kind == "MissionFailed"
        if kind == "VerificationLayerRecorded":
            relevant = relevant and event.payload.get("status") in {"FAIL", "ERROR"}
        elif kind not in TIMELINE_TYPES:
            continue
        if not relevant:
            continue
        payload = dict(event.payload)
        payload.pop("final_report", None)
        lines.append({"seq": event.seq, "type": kind, "task_id": event.task_id,
                      "attempt_id": event.attempt_id, "detail": payload})
    return lines


__all__ = (
    "ATTRIBUTION_VERSION",
    "GOAL_RESOLUTION_COMMITTED",
    "TIMELINE_TYPES",
    "ContributionChain",
    "attribution",
    "contribution_chain",
    "failure_timeline",
    "read_contribution_chain",
)
