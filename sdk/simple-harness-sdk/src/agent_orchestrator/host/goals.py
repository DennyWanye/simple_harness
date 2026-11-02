# SPDX-License-Identifier: Apache-2.0
"""可信宿主登记的有界目标；收口只核结构事实，不替审阅员判断结果质量。"""

from __future__ import annotations

import re
import time
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
    criteria = body.get("completion_criteria")
    if not isinstance(criteria, (list, tuple)) or not 1 <= len(criteria) <= 128:
        raise SessionError("invalid_argument", "completion_criteria must be a bounded list")
    result["completion_criteria"] = normalize_criteria(criteria)
    conditions = body.get("stop_conditions", [])
    if not isinstance(conditions, (list, tuple)) or len(conditions) > 128:
        raise SessionError("invalid_argument", "stop_conditions must be a bounded list")
    result["stop_conditions"] = [_bounded_text(value, "stop_conditions") for value in conditions]
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


def normalize_criteria(values: Any) -> list[dict[str, Any]]:
    """完成条件各有稳定身份：宿主自带 ``criterion_id``，或 SDK 按位次派 ``c1…cN``。

    身份绑定 goal_id + goal_version：宿主改条件就是新版本，旧版本的验收记录对新版本无效。
    """
    result: list[dict[str, Any]] = []
    for position, value in enumerate(values, start=1):
        if isinstance(value, str):
            item = {"criterion_id": f"c{position}", "text": value, "required": True}
        elif isinstance(value, Mapping):
            if set(value) - {"criterion_id", "text", "required"}:
                raise SessionError("invalid_argument", "unknown completion criterion field")
            criterion_id = value.get("criterion_id", f"c{position}")
            if not isinstance(criterion_id, str) or not _CRITERION_ID.fullmatch(criterion_id):
                raise SessionError("invalid_argument", "criterion_id must be a safe identifier")
            required = value.get("required", True)
            if type(required) is not bool:
                raise SessionError("invalid_argument", "required must be a bool")
            item = {"criterion_id": criterion_id, "text": value.get("text"), "required": required}
        else:
            raise SessionError("invalid_argument", "completion criterion must be text or mapping")
        item["text"] = _bounded_text(item["text"], "completion_criteria")
        result.append(item)
    if len({item["criterion_id"] for item in result}) != len(result):
        raise SessionError("invalid_argument", "criterion_id must be unique within a goal")
    return result


_CRITERION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_HASH = re.compile(r"[0-9a-f]{64}")
VERDICTS = frozenset({"pass", "fail", "unknown"})
RECORD_FIELDS = frozenset(
    {
        "criterion_id",
        "verdict",
        "validator_id",
        "validator_version",
        "method",
        "evidence",
        "note",
        "checked_at",
    }
)


def normalize_record(record: Any) -> dict[str, Any]:
    """一条验收记录的形：谁（验证者）按什么方法对哪条条件给了什么结论，证据引用是什么。

    这里只核形与边界；证据能不能对上账、条件在不在，由 Goals 对着本树的事实核。
    """
    if not isinstance(record, Mapping):
        raise SessionError("invalid_argument", "acceptance record must be a mapping")
    if set(record) - RECORD_FIELDS:
        raise SessionError("invalid_argument", "record identity is supplied by the SDK")
    verdict = record.get("verdict")
    if verdict not in VERDICTS:
        raise SessionError("invalid_argument", "verdict must be pass, fail or unknown")
    criterion_id = record.get("criterion_id")
    if not isinstance(criterion_id, str) or not _CRITERION_ID.fullmatch(criterion_id):
        raise SessionError("invalid_argument", "criterion_id must be a safe identifier")
    evidence = record.get("evidence", [])
    if not isinstance(evidence, (list, tuple)) or len(evidence) > 64:
        raise SessionError("invalid_argument", "evidence must be a bounded list")
    if verdict == "pass" and not evidence:
        raise SessionError("invalid_argument", "a pass needs at least one evidence reference")
    items = []
    for item in evidence:
        if not isinstance(item, Mapping) or set(item) - {"ref", "content_hash", "receipt_ref"}:
            raise SessionError("invalid_argument", "evidence item must be ref + hash or receipt")
        ref = _bounded_text(item.get("ref"), "evidence.ref", limit=2000)
        content_hash, receipt_ref = item.get("content_hash"), item.get("receipt_ref")
        if content_hash is not None and (
            not isinstance(content_hash, str) or not _HASH.fullmatch(content_hash)
        ):
            raise SessionError("invalid_argument", "content_hash must be 64 hex characters")
        if receipt_ref is not None:
            receipt_ref = _bounded_text(receipt_ref, "evidence.receipt_ref", limit=2000)
        if content_hash is None and receipt_ref is None:
            raise SessionError("invalid_argument", "evidence needs content_hash or receipt_ref")
        items.append({"ref": ref, "content_hash": content_hash, "receipt_ref": receipt_ref})
    checked_at = record.get("checked_at")
    if checked_at is not None and (
        not isinstance(checked_at, (int, float)) or isinstance(checked_at, bool)
    ):
        raise SessionError("invalid_argument", "checked_at must be a timestamp")
    return {
        "criterion_id": criterion_id,
        "verdict": verdict,
        "validator_id": _bounded_text(record.get("validator_id"), "validator_id", limit=256),
        "validator_version": _bounded_text(
            record.get("validator_version"), "validator_version", limit=256, required=False
        ),
        "method": _bounded_text(record.get("method"), "method", limit=256),
        "evidence": items,
        "note": _bounded_text(record.get("note"), "note", limit=2000, required=False),
        "checked_at": checked_at,
    }


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


class _AcceptanceIncomplete(Exception):
    def __init__(self, goal_id: str, reasons: list[str]) -> None:
        self.goal_id, self.reasons = goal_id, reasons


def require_trusted_root(session: Any) -> None:
    """Goal、验收记录、重评这些命令只有可信的根宿主能发；模型与子会话没有这些命令。"""
    session._check_open()
    identity = session._store.get("meta", "identity")
    host = session._host
    if (
        session.parent is not None
        or host.principal.kind != "human"
        or host.principal.principal_id != identity["principal_id"]
        or host.tenant_id != identity["tenant_id"]
    ):
        raise SessionError("not_found_or_forbidden", "only the trusted root host manages goals")


class Goals:
    def __init__(self, session: Any) -> None:
        self.session, self.store = session, session._store

    def _require_host(self) -> None:
        require_trusted_root(self.session)

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
            if row["state"] not in GOAL_TERMINAL and row["state"] != "cancel_requested":
                cancelled = root._is_cancelled(
                    goal_ref={"goal_id": row["goal_id"], "goal_version": row["goal_version"]},
                    for_new_input=True,
                )
                if cancelled:
                    continue
                return {"goal_id": row["goal_id"], "goal_version": row["goal_version"]}
        return None

    @staticmethod
    def _receipt(row: Mapping[str, Any]) -> dict[str, Any]:
        receipt = {
            "goal_id": row["goal_id"],
            "goal_version": row["goal_version"],
            "goal_ref": {"goal_id": row["goal_id"], "goal_version": row["goal_version"]},
            "version": row["version"],
            "state": row["state"],
        }
        if row.get("accepted_records") is not None:
            receipt["accepted_records"] = row["accepted_records"]
        return receipt

    def read(self, goal_id: str) -> dict[str, Any]:
        """公开只读视图：规格、状态、结构等待原因、验收阻塞与采用的记录。不调模型。"""
        self.session._check_open()
        row = self.find(goal_id)
        return {
            **row,
            "waiting_reasons": list(row.get("waiting_reasons", [])),
            "acceptance_blockers": list(row.get("acceptance_blockers", [])),
            "accepted_records": row.get("accepted_records"),
        }

    # ---- 验收记录 ----------------------------------------------------------------

    def records(self, goal_id: str) -> list[dict[str, Any]]:
        self.session._check_open()
        self.find(goal_id)
        return [row for row in self.store.rows("acceptance_records") if row["goal_id"] == goal_id]

    def record(
        self, goal_id: str, record: Any, *, command_id: str, expected_version: int
    ) -> dict[str, Any]:
        """可信宿主登记一条验收结论。模型没有这个命令，正文里的"已验证"不会变成记录。"""
        self._require_host()
        body = normalize_record(record)
        if type(expected_version) is not int or expected_version < 1:
            raise SessionError("invalid_argument", "expected_version must be positive")
        goal = self.find(goal_id)
        if goal["state"] in GOAL_TERMINAL or goal["version"] != expected_version:
            raise SessionError("stale_version")
        if body["criterion_id"] not in {c["criterion_id"] for c in goal["completion_criteria"]}:
            raise SessionError("not_found_or_forbidden", "no such criterion in this goal version")
        for item in body["evidence"]:
            problem = self._evidence_problem(item)
            if problem == "foreign":
                raise SessionError("not_found_or_forbidden", "evidence is not in this tree")
            if problem is not None:
                raise SessionError("invalid_argument", f"evidence does not bind: {problem}")
        record_id = "acceptance-" + digest([self.session.session_id, goal_id, command_id])[:32]

        def apply() -> dict[str, Any]:
            now = time.time()
            row = {
                **body,
                "record_id": record_id,
                "goal_id": goal_id,
                "goal_version": expected_version,
                "source_principal_id": self.session._host.principal.principal_id,
                "recorded_at": now,
                "command_id": command_id,
                "current": True,
                "superseded_by": None,
            }
            # 同一条件、同一版本：后来的记录接替先前的；历史保留，不静默覆盖
            for previous in self.store.rows("acceptance_records"):
                if (
                    previous["goal_id"] == goal_id
                    and previous["goal_version"] == expected_version
                    and previous["criterion_id"] == body["criterion_id"]
                    and previous["current"]
                ):
                    previous.update(current=False, superseded_by=record_id)
                    self.store.put("acceptance_records", previous["record_id"], previous)
            self.store.put("acceptance_records", record_id, row)
            self.store.event(
                "acceptance_recorded",
                key=f"acceptance:{record_id}",
                goal_id=goal_id,
                goal_version=expected_version,
                payload={
                    "record_id": record_id,
                    "criterion_id": body["criterion_id"],
                    "verdict": body["verdict"],
                    "validator_id": body["validator_id"],
                    "evidence_refs": [item["ref"] for item in body["evidence"]],
                },
            )
            return {"record_id": record_id, "goal_version": expected_version, "current": True}

        return self.session._commands.run(
            command_id,
            "record_acceptance",
            {"goal_id": goal_id, "record": body, "expected_version": expected_version},
            apply,
        )

    def _evidence_problem(self, item: Mapping[str, Any]) -> str | None:
        """证据引用能不能对上本树的账。返回 None（能）、"foreign"（不属本树）或不一致的原因。

        任务产物、连接器效果、任务结果三类由 SDK 对账；其他前缀是业务回执，只要求带哈希或回执身份。
        日志与异常不带证据正文。
        """
        ref = item["ref"]
        kind, _, rest = ref.partition(":")
        root = self.session._root_session()
        if kind == "artifact":
            mission_id, _, artifact_id = rest.partition(":")
            owner = next(
                (
                    session
                    for session in root._tree_sessions()
                    for row in session._store.rows("delegations")
                    if row.get("mission_id") == mission_id
                ),
                None,
            )
            if owner is None or not artifact_id:
                return "foreign"
            artifacts = owner._host.snapshot(mission_id)["snapshot"].get("artifacts", [])
            found = next((a for a in artifacts if str(a.get("id")) == artifact_id), None)
            if found is None:
                return "artifact_missing"
            if (
                item["content_hash"] is not None
                and found.get("content_hash") != item["content_hash"]
            ):
                return "content_hash_mismatch"
            return None
        if kind == "effect":
            for session in root._tree_sessions():
                row = session._store.get("effects", rest)
                if row is not None:
                    if row["state"] != "committed":
                        return "effect_not_committed"
                    if (
                        item["receipt_ref"] is not None
                        and row.get("receipt_ref") != item["receipt_ref"]
                    ):
                        return "receipt_ref_mismatch"
                    return None
            return "foreign"
        if kind == "result":
            for session in root._tree_sessions():
                if session._store.get("inbox", rest) is not None:
                    return None
            return "foreign"
        return None

    def acceptance_reasons(self, goal: Mapping[str, Any]) -> list[str]:
        """成功收口前，每个必需条件都要有当前版本、结论为 pass、证据仍对得上的记录。"""
        reasons = []
        version = goal["goal_version"]
        records = [
            r for r in self.store.rows("acceptance_records") if r["goal_id"] == goal["goal_id"]
        ]
        for criterion in goal["completion_criteria"]:
            if not criterion["required"]:
                continue
            cid = criterion["criterion_id"]
            current = next(
                (
                    r
                    for r in records
                    if r["criterion_id"] == cid and r["goal_version"] == version and r["current"]
                ),
                None,
            )
            if current is None:
                older = any(r["criterion_id"] == cid for r in records)
                reasons.append(
                    f"criterion_unmet:{cid}:" + ("stale_version" if older else "missing")
                )
                continue
            if current["verdict"] != "pass":
                reasons.append(f"criterion_unmet:{cid}:{current['verdict']}")
                continue
            if any(self._evidence_problem(item) is not None for item in current["evidence"]):
                reasons.append(f"criterion_unmet:{cid}:evidence_invalid")
        return reasons

    def _accepted(self, goal: Mapping[str, Any]) -> dict[str, Any]:
        rows = sorted(
            (
                r
                for r in self.store.rows("acceptance_records")
                if r["goal_id"] == goal["goal_id"]
                and r["goal_version"] == goal["goal_version"]
                and r["current"]
                and r["verdict"] == "pass"
            ),
            key=lambda r: r["record_id"],
        )
        return {
            "record_ids": [r["record_id"] for r in rows],
            "digest": digest([[r["record_id"], r["criterion_id"], r["evidence"]] for r in rows]),
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
                "acceptance_blockers": [],
                "accepted_records": None,
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
                if row.get("terminal_status") in TERMINAL and not row.get(
                    "terminal_actions_unresolved"
                ):
                    # 终态任务没有在途工作、不会再有待答请求；记终态时核过快照里没有结果不明的
                    # 动作，不必再读快照。记了不明动作的终态任务（少数）照常读，这里只读不写。
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
                unresolved = [
                    effect
                    for effect in snapshot.get("actions", [])
                    if str(effect.get("state", "")).upper() in {"UNKNOWN", "HANDED_OFF"}
                ]
                for effect in unresolved:
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
            if outcome == "succeeded":
                blockers = self.acceptance_reasons(row)
                if blockers:
                    raise _AcceptanceIncomplete(goal_id, blockers)
                row["accepted_records"] = self._accepted(row)
            row.update(state=outcome, basis=basis, waiting_reasons=[], acceptance_blockers=[])
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
        except _AcceptanceIncomplete as blocked:
            # 阻塞原因要能查到：先提交，再报拒绝
            with self.store.transaction():
                row = self.find(goal_id)
                row["acceptance_blockers"] = blocked.reasons
                self.store.put("goals", goal_id, row)
                self.store.event(
                    "goal_acceptance_blocked",
                    key=f"goal-acceptance-blocked:{goal_id}:{digest(blocked.reasons)}",
                    goal_id=goal_id,
                    goal_version=row["goal_version"],
                    reasons=blocked.reasons,
                )
            raise SessionError(
                "acceptance_incomplete", "required criteria lack a current verified pass"
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


__all__ = ("GOAL_TERMINAL", "GoalSpec", "Goals", "normalize_criteria", "normalize_record")
