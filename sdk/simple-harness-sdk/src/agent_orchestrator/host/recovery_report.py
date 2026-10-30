# SPDX-License-Identifier: Apache-2.0
"""从会话账本与任务快照读取恢复责任，不调用模型，也不把恢复当成业务成功。"""

from __future__ import annotations

from typing import Any, TypedDict

from .delegation import TERMINAL
from .goals import pending_request


class RecoveryReport(TypedDict):
    store_version: int
    owner_generation: int
    resumed_refs: list[str]
    waiting_approval_refs: list[str]
    held_refs: list[str]
    unknown_effect_refs: list[str]
    rejected_refs: list[str]
    reasons: dict[str, str]


def build_recovery_report(session: Any) -> RecoveryReport:
    report: RecoveryReport = {
        "store_version": int(session._store.get("meta", "configuration")["version"]),
        "owner_generation": int(getattr(session._host, "owner_generation", 0)),
        "resumed_refs": [],
        "waiting_approval_refs": [],
        "held_refs": [],
        "unknown_effect_refs": [],
        "rejected_refs": [],
        "reasons": {},
    }

    def add(category: str, ref: str, reason: str) -> None:
        refs = report[category]
        if ref not in refs:
            refs.append(ref)
        report["reasons"][ref] = reason

    for owner in session._tree_sessions():
        store = owner._store
        for row in store.rows("delegations"):
            ref = row["delegation_id"]
            if row.get("error"):
                add("rejected_refs", ref, row["error"])
                continue
            if row.get("held_reason"):
                add("held_refs", ref, row["held_reason"])
            mid = row.get("mission_id")
            if not mid:
                if (
                    row.get("cancel_effect_state") == "unknown"
                    or row.get("dispatch_state") == "claimed"
                ):
                    add("unknown_effect_refs", ref, "mission_handoff_unknown")
                    continue
                add("resumed_refs", ref, "accepted_not_dispatched")
                continue
            snapshot = owner._host.snapshot(mid)["snapshot"]
            status = snapshot["mission"]["status"]
            if status not in TERMINAL:
                add("resumed_refs", ref, str(status))
            for request in (
                list(snapshot.get("planning_questions", []))
                + list(snapshot.get("planning_authorization_requests", []))
                + owner._host.approvals(mid)
            ):
                if pending_request(request):
                    rid = (
                        request.get("request_id")
                        or request.get("decision_id")
                        or request.get("question_id")
                    )
                    if rid:
                        add("waiting_approval_refs", str(rid), "pending_request")
            for effect in snapshot.get("actions", []):
                if str(effect.get("state", "")).upper() in {"UNKNOWN", "HANDED_OFF"}:
                    add("unknown_effect_refs", str(effect["action_key"]), str(effect["state"]))
        for row in store.rows("queued_inputs"):
            if row["state"] in {"queued", "running"}:
                add("resumed_refs", row["input_id"], row["state"])
            elif row["state"] in {"failed", "cancelled"}:
                add(
                    "rejected_refs",
                    row["input_id"],
                    row.get("reason", row.get("error", row["state"])),
                )
        for row in store.rows("continuation_results"):
            if row["state"] in {"hold", "waiting"}:
                add("held_refs", row["event_id"], row.get("reason", row["state"]))
            elif row["state"] == "deny":
                add("rejected_refs", row["event_id"], row.get("reason", "deny"))
            elif row["state"] == "pending":
                add("resumed_refs", row["event_id"], "continuation_pending")
        for namespace in ("authorization_requests", "requests"):
            for row in store.rows(namespace):
                if pending_request(row) and (
                    namespace == "authorization_requests" or not row.get("mission_id")
                ):
                    add("waiting_approval_refs", row["request_id"], "authorization_required")
        for row in store.rows("effects"):
            if row["state"] in {"unknown", "pending", "claimed"}:
                add("unknown_effect_refs", row["operation_key"], row["state"])
        for row in store.rows("voice_deliveries"):
            binding = owner._voice
            if row["state"] == "submitted" and (
                binding is None or getattr(binding, "binding_id", None) != row.get("binding_id")
            ):
                add("unknown_effect_refs", row["input_id"], "voice_binding_lost_after_submission")
        for row in store.rows("cancellations"):
            if row["state"] == "cancel_requested":
                add("held_refs", row["target_id"], "cancel_requested")
        for row in store.rows("goals"):
            if row["state"] == "waiting":
                add(
                    "held_refs",
                    row["goal_id"],
                    ",".join(row.get("waiting_reasons", [])) or "waiting",
                )
    for key in (
        "resumed_refs",
        "waiting_approval_refs",
        "held_refs",
        "unknown_effect_refs",
        "rejected_refs",
    ):
        report[key].sort()
    return report


__all__ = ("RecoveryReport", "build_recovery_report")
