# SPDX-License-Identifier: Apache-2.0
"""可信宿主登记的有界目标；收口只核结构事实，不替审阅员判断结果质量。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from .delegation import TERMINAL
from .session_ledger import SessionError, digest
from .session_tree import _bounded_text

GOAL_TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


@dataclass(frozen=True)
class GoalSpec:
    objective: str
    completion_criteria: tuple[str, ...]
    budget: Mapping[str, int]
    goal_id: str | None = None
    stop_conditions: tuple[str, ...] = ()
    grant_ref: str | None = None
    policy_revision: str | None = None

    def public(self) -> dict[str, Any]:
        return asdict(self)


def normalize_goal(spec: GoalSpec | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(spec, GoalSpec):
        body = spec.public()
    elif isinstance(spec, Mapping):
        body = dict(spec)
    else:
        raise SessionError("invalid_argument", "spec must be a GoalSpec or mapping")
    if set(body) - {
        "goal_id",
        "objective",
        "completion_criteria",
        "stop_conditions",
        "budget",
        "grant_ref",
        "policy_revision",
    }:
        raise SessionError("invalid_argument", "goal identity is supplied by the SDK")
    goal_id = body.get("goal_id")
    if goal_id is not None and (
        not isinstance(goal_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", goal_id)
    ):
        raise SessionError("invalid_argument", "goal_id must be a safe identifier")
    result = {"goal_id": goal_id, "objective": _bounded_text(body.get("objective"), "objective")}
    for name, minimum in (("completion_criteria", 1), ("stop_conditions", 0)):
        values = body.get(name, [] if minimum == 0 else None)
        if not isinstance(values, (list, tuple)) or not minimum <= len(values) <= 128:
            raise SessionError("invalid_argument", f"{name} must be a bounded list")
        result[name] = [_bounded_text(value, name) for value in values]
    budget = body.get("budget")
    if not isinstance(budget, Mapping) or not budget or len(budget) > 32:
        raise SessionError("invalid_argument", "budget must contain positive integer limits")
    if any(
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 128
        or type(value) is not int
        or value <= 0
        for name, value in budget.items()
    ):
        raise SessionError("invalid_argument", "budget must contain positive integer limits")
    result["budget"] = dict(budget)
    for name in ("grant_ref", "policy_revision"):
        value = body.get(name)
        result[name] = None if value is None else _bounded_text(value, name, limit=2000)
    return result


def goal_matches(row: Mapping[str, Any], goal_id: str) -> bool:
    return (row.get("goal_ref") or {}).get("goal_id") == goal_id


def pending_request(row: Mapping[str, Any]) -> bool:
    return str(row.get("state", row.get("status", "PENDING"))).upper() in {
        "PENDING",
        "AUTHORIZATION_REQUIRED",
        "NEEDS_APPROVAL",
    }


class _UnfinishedGoal(Exception):
    def __init__(self, goal_id: str, reasons: list[str]) -> None:
        self.goal_id, self.reasons = goal_id, reasons


class Goals:
    def __init__(self, session: Any) -> None:
        self.session, self.store = session, session._store

    def _require_host(self) -> None:
        self.session._check_open()
        identity = self.store.get("meta", "identity")
        host = self.session._host
        if (
            self.session.parent is not None
            or host.principal.kind != "human"
            or host.principal.principal_id != identity["principal_id"]
            or host.tenant_id != identity["tenant_id"]
        ):
            raise SessionError("not_found_or_forbidden", "only the trusted root host manages goals")

    def rows(self) -> list[dict[str, Any]]:
        return self.store.rows("goals")

    def find(self, goal_id: str) -> dict[str, Any]:
        row = self.store.get("goals", str(goal_id))
        if row is None:
            raise SessionError("not_found_or_forbidden", "no such goal in this root session")
        return row

    def current_ref(self) -> dict[str, Any] | None:
        root = self.session._root_session()
        for row in root._store.rows("goals"):
            if row["state"] not in GOAL_TERMINAL:
                return {"goal_id": row["goal_id"], "goal_version": row["goal_version"]}
        return None

    @staticmethod
    def _receipt(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "goal_id": row["goal_id"],
            "goal_version": row["goal_version"],
            "goal_ref": {"goal_id": row["goal_id"], "goal_version": row["goal_version"]},
            "version": row["version"],
            "state": row["state"],
        }

    def put(
        self, spec: GoalSpec | Mapping[str, Any], *, command_id: str, expected_version: int
    ) -> dict[str, Any]:
        self._require_host()
        body = normalize_goal(spec)
        if type(expected_version) is not int or expected_version < 0:
            raise SessionError("invalid_argument", "expected_version must be nonnegative")
        goal_id = body["goal_id"] or "goal-" + digest([self.session.session_id, command_id])[:32]

        def apply() -> dict[str, Any]:
            previous = self.store.get("goals", goal_id)
            if previous is None:
                if expected_version != 0:
                    raise SessionError("stale_version")
                if any(row["state"] not in GOAL_TERMINAL for row in self.rows()):
                    raise SessionError("active_work", "one unfinished goal per root")
            elif previous["state"] in GOAL_TERMINAL or previous["version"] != expected_version:
                raise SessionError("stale_version")
            version = expected_version + 1
            row = {
                **body,
                "goal_id": goal_id,
                "root_session_id": self.session.session_id,
                "goal_version": version,
                "version": version,
                "state": "active",
                "waiting_reasons": [],
            }
            self.store.put("goals", goal_id, row)
            self.store.put("goal_history", f"{goal_id}:{version}", row)
            self.store.event(
                "goal_updated" if previous else "goal_created",
                key=f"goal:{goal_id}:{version}",
                goal_id=goal_id,
                goal_version=version,
            )
            return self._receipt(row)

        return self.session._commands.run(
            command_id, "put_goal", {"spec": body, "expected_version": expected_version}, apply
        )

    def structural_reasons(self, goal_id: str) -> list[str]:
        reasons: set[str] = set()
        root = self.session._root_session()
        sessions = root._tree_sessions()
        # 父镜像是消费权威，不能因子账本保留原始通知而把已应用结果再次判成未消费。
        notices = {row["event_id"]: row for row in root._store.rows("inbox")}
        for owner in sessions:
            for row in owner._store.rows("inbox"):
                notices.setdefault(row["event_id"], row)
        for event_id, row in notices.items():
            if (
                goal_matches(row, goal_id)
                and row.get("applied_turn_id") is None
                and not (root._store.get("result_exclusions", event_id) or row.get("excluded"))
            ):
                reasons.add(f"unapplied_result:{event_id}")
        for owner in sessions:
            for row in owner._store.rows("delegations"):
                if not goal_matches(row, goal_id) or row.get("error"):
                    continue
                ref = row["delegation_id"]
                mid = row.get("mission_id")
                if mid is None:
                    if (
                        row.get("cancel_effect_state") == "unknown"
                        or row.get("dispatch_state") == "claimed"
                    ):
                        reasons.add(f"unknown_effect:{ref}")
                    if row.get("terminal_status") not in TERMINAL:
                        reasons.add(f"active_delegation:{ref}")
                    continue
                snapshot = owner._host.snapshot(mid)["snapshot"]
                if snapshot["mission"]["status"] not in TERMINAL:
                    reasons.add(f"active_delegation:{ref}")
                for request in (
                    list(snapshot.get("planning_questions", []))
                    + list(snapshot.get("planning_authorization_requests", []))
                    + owner._host.approvals(mid)
                ):
                    if pending_request(request):
                        request_id = (
                            request.get("request_id")
                            or request.get("decision_id")
                            or request.get("question_id")
                            or ref
                        )
                        reasons.add(f"pending_request:{request_id}")
                for effect in snapshot.get("actions", []):
                    if str(effect.get("state", "")).upper() in {"UNKNOWN", "HANDED_OFF"}:
                        reasons.add(f"unknown_effect:{effect.get('action_key', ref)}")
            for row in owner._store.rows("effects"):
                if goal_matches(row, goal_id) and row["state"] in {"claimed", "pending", "unknown"}:
                    reasons.add(f"unknown_effect:{row['operation_key']}")
            for row in owner._store.rows("authorization_requests"):
                if goal_matches(row, goal_id) and pending_request(row):
                    reasons.add(f"pending_request:{row['request_id']}")
            for row in owner._store.rows("requests"):
                # 任务请求的投影不随终态删除，带 mission_id 的必须以上面的实时快照为准。
                if (
                    not row.get("mission_id")
                    and goal_matches(row, goal_id)
                    and pending_request(row)
                ):
                    reasons.add(f"pending_request:{row['request_id']}")
            for row in owner._store.rows("queued_inputs"):
                if goal_matches(row, goal_id) and row["state"] in {"queued", "running"}:
                    reasons.add(f"active_input:{row['input_id']}")
            for row in owner._store.rows("voice_deliveries"):
                queued = owner._store.get("queued_inputs", row["input_id"]) or {}
                if goal_matches(row, goal_id) or goal_matches(queued, goal_id):
                    if row["state"] == "submitted":
                        reasons.add(f"unknown_effect:{row['input_id']}")
        return sorted(reasons)

    def finish(
        self,
        goal_id: str,
        outcome: str,
        *,
        command_id: str,
        expected_version: int,
        basis: str = "",
    ) -> dict[str, Any]:
        self._require_host()
        if outcome not in GOAL_TERMINAL:
            raise SessionError("invalid_argument", "outcome must be succeeded, failed or cancelled")
        basis = _bounded_text(basis, "basis", required=outcome == "succeeded")
        if type(expected_version) is not int or expected_version < 1:
            raise SessionError("invalid_argument", "expected_version must be positive")
        self.find(goal_id)

        def apply() -> dict[str, Any]:
            row = self.find(goal_id)
            if row["version"] != expected_version or row["state"] in GOAL_TERMINAL:
                raise SessionError("stale_version")
            reasons = self.structural_reasons(goal_id)
            if reasons:
                raise _UnfinishedGoal(goal_id, reasons)
            row.update(state=outcome, basis=basis, waiting_reasons=[])
            self.store.put("goals", goal_id, row)
            self.store.event(
                "goal_finished",
                key=f"goal-finished:{goal_id}:{expected_version}",
                goal_id=goal_id,
                goal_version=expected_version,
                outcome=outcome,
            )
            return self._receipt(row)

        try:
            return self.session._commands.run(
                command_id,
                "finish_goal",
                {
                    "goal_id": goal_id,
                    "outcome": outcome,
                    "expected_version": expected_version,
                    "basis": basis,
                },
                apply,
            )
        except _UnfinishedGoal as blocked:
            if any(reason.startswith("unknown_effect:") for reason in blocked.reasons):
                # 先提交等待事实，再报告拒绝；异常不能把 unknown 的可见状态一起回滚。
                with self.store.transaction():
                    row = self.find(goal_id)
                    row.update(state="waiting", waiting_reasons=blocked.reasons)
                    self.store.put("goals", goal_id, row)
                    self.store.event(
                        "goal_waiting",
                        key=f"goal-waiting:{goal_id}:{digest(blocked.reasons)}",
                        goal_id=goal_id,
                        goal_version=row["goal_version"],
                        reasons=blocked.reasons,
                    )
            raise SessionError(
                "active_work", "goal still has unresolved structural facts"
            ) from None

    def exclude(self, event_id: str, reason: str, *, command_id: str) -> dict[str, Any]:
        self._require_host()
        reason = _bounded_text(reason, "reason")
        if not isinstance(event_id, str) or not event_id:
            raise SessionError("invalid_argument")
        root = self.session._root_session()
        notice = root._store.get("inbox", event_id)
        if notice is None:
            raise SessionError("not_found_or_forbidden", "result does not belong to this root")

        def apply() -> dict[str, Any]:
            exclusion = {
                "event_id": event_id,
                "reason": reason,
                "principal_id": self.session._host.principal.principal_id,
            }
            self.store.put("result_exclusions", event_id, exclusion)
            continuation = self.store.get("continuation_results", event_id)
            if continuation is not None and continuation["state"] != "applied":
                continuation.update(state="excluded", reason=reason)
                self.store.put("continuation_results", event_id, continuation)
            for queued in self.store.rows("queued_inputs"):
                if (
                    queued["kind"] != "result_event"
                    or queued["state"] != "queued"
                    or event_id not in queued["payload"]
                ):
                    continue
                resolved = all(
                    self.store.get("result_exclusions", item)
                    or (self.store.get("inbox", item) or {}).get("applied_turn_id") is not None
                    for item in queued["payload"]
                )
                if not resolved:
                    continue
                # 还没交给执行者的空批次应退出队列；混合批次保留未排除项的消费责任。
                queued.update(
                    state="cancelled",
                    reason="results_excluded",
                    receipt={
                        "state": "failed",
                        "text": "",
                        "turn_id": queued["turn_id"],
                        "error": {"code": "cancelled", "reason": "results_excluded"},
                    },
                )
                self.store.put("queued_inputs", queued["input_id"], queued)
                self.store.event(
                    "cancelled",
                    key=f"excluded-input:{queued['input_id']}",
                    input_id=queued["input_id"],
                    reason="results_excluded",
                )
            self.store.event("result_excluded", key=f"excluded:{event_id}", payload=exclusion)
            return {"event_id": event_id, "excluded": True, "reason": reason}

        receipt = self.session._commands.run(
            command_id, "exclude_result", {"event_id": event_id, "reason": reason}, apply
        )
        root._converge_cancellations()
        return receipt


__all__ = ("GOAL_TERMINAL", "GoalSpec", "Goals")
