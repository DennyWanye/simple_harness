# SPDX-License-Identifier: Apache-2.0
"""Read-only Mission polling with atomic inbox, progress and request projection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .delegation import TERMINAL
from .session_ledger import SessionError, digest

TERMINAL_EVENTS = frozenset({"MissionCompleted", "MissionFailed", "MissionCancelled"})


def request_version(request: Mapping[str, Any], kind: str) -> int:
    return int(request["version"]) if kind == "planning" else int(digest(dict(request))[:15], 16)


def project_event(raw: Mapping[str, Any]) -> dict[str, Any]:
    projected = {
        k: raw.get(k) for k in ("seq", "type", "created_at", "task_id", "attempt_id", "actor_type")
    }
    if raw.get("type") == "HumanCommentAdded":
        projected["summary"] = str((raw.get("payload") or {}).get("text", ""))[:300]
    return projected


class SessionInbox:
    def __init__(self, session: Any) -> None:
        self.session = session
        self.host, self.store = session._host, session._store

    def catch_up(self) -> None:
        for row in self.store.rows("delegations"):
            mid = row.get("mission_id")
            if mid is None:
                continue
            cursor = int(self.store.get("cursors", mid) or 0)
            mission_store = getattr(self.host, "store", None)
            if mission_store is not None:
                with mission_store.read_view():
                    tail = mission_store.connection.execute(
                        "SELECT COALESCE(MAX(seq),0) FROM events WHERE mission_id=?", (mid,)
                    ).fetchone()[0]
                if int(tail) <= cursor:
                    continue
            while True:
                page = self.host.events(mid, after_seq=cursor, limit=100)
                events = page.get("events", [])
                snapshot = self.host.snapshot(mid)
                mission = snapshot["snapshot"]["mission"]
                added = False
                with self.store.transaction():
                    for event in events:
                        seq = int(event["seq"])
                        self.store.event(
                            "task_progress",
                            key=f"progress:{mid}:{seq}",
                            mission_id=mid,
                            delegation_id=row["delegation_id"],
                            payload=project_event(event),
                        )
                        if (
                            event["type"] in TERMINAL_EVENTS
                            or event["type"] == "AssuranceStatusNotified"
                        ):
                            payload = event.get("payload") or {}
                            final_id = str(
                                payload.get("event_id")
                                or event.get("id")
                                or event.get("event_id")
                                or f"{mid}:{seq}"
                            )
                            if self.store.get("inbox", final_id) is None:
                                notice = {
                                    "event_id": final_id,
                                    "mission_id": mid,
                                    "delegation_id": row["delegation_id"],
                                    "status": mission["status"],
                                    "state_version": snapshot.get("state_version"),
                                    "source": "task_result",
                                    "instruction": False,
                                    "acked": False,
                                    "applied_turn_id": None,
                                    "artifacts": [
                                        {
                                            k: a[k]
                                            for k in ("id", "content_hash", "logical_path")
                                            if k in a
                                        }
                                        for a in snapshot["snapshot"].get("artifacts", [])
                                    ],
                                }
                                self.store.put("inbox", final_id, notice)
                                self.store.event(
                                    "result_ready",
                                    key=f"result:{final_id}",
                                    mission_id=mid,
                                    delegation_id=row["delegation_id"],
                                    payload=notice,
                                )
                                added = True
                        cursor = max(cursor, seq)
                    self.store.put("cursors", mid, cursor)
                if added:
                    self.session._checkpoint("inbox_written")
                if not page.get("has_more"):
                    break
            if str(mission["status"]) not in TERMINAL:
                self._requests(mid, snapshot)
        self._mirror_children()

    def _mirror_children(self) -> None:
        """子会话的终态通知镜像进父 inbox：同一个 event_id，带 child_key。
        父下一次输入把它当资料附上；人只在父这里点已收到。"""

        for key, child in self.session._children.items():
            child._inbox.catch_up()
            for notice in child.inbox():
                if self.store.get("inbox", notice["event_id"]) is not None:
                    continue
                mirrored = {
                    **notice,
                    "child_key": key,
                    "child_session_id": child.session_id,
                    "source": "child_result",
                    "instruction": False,
                    "acked": False,
                    "applied_turn_id": None,
                }
                with self.store.transaction():
                    self.store.put("inbox", notice["event_id"], mirrored)
                    self.store.event(
                        "result_ready",
                        key=f"result:{notice['event_id']}",
                        mission_id=notice["mission_id"],
                        delegation_id=notice["delegation_id"],
                        child_key=key,
                        payload=mirrored,
                    )
                self.session._checkpoint("inbox_written")

    def _requests(self, mid: str, snapshot: Mapping[str, Any]) -> None:
        candidates = [("planning", r) for r in snapshot["snapshot"].get("planning_questions", [])]
        candidates += [("approval", r) for r in self.host.approvals(mid)]
        with self.store.transaction():
            for kind, request in candidates:
                if str(request.get("state", request.get("status", "PENDING"))) != "PENDING":
                    continue
                rid = (
                    request.get("decision_id")
                    or request.get("question_id")
                    or request.get("request_id")
                )
                if not rid:
                    continue
                version = request_version(request, kind)
                projected = {
                    "request_id": rid,
                    "mission_id": mid,
                    "version": version,
                    "request_kind": kind,
                }
                self.store.put("requests", str(rid), projected)
                detail = (
                    request.get("request", {}).get("payload", {}) if kind == "planning" else request
                )
                projected = {
                    **projected,
                    **{
                        name: detail[name]
                        for name in ("question", "options", "summary", "reason", "topic")
                        if name in detail
                    },
                }
                self.store.event(
                    "needs_response",
                    key=f"request:{rid}:{version}",
                    request_id=rid,
                    mission_id=mid,
                    payload=projected,
                )

    def ack(self, event_id: str) -> dict[str, Any]:
        if self.session.parent is not None:
            raise SessionError("notice_not_found")
        if self.host.principal.kind != "human":
            raise SessionError(
                "permission_denied", "only the authenticated person can acknowledge results"
            )
        with self.store.transaction():
            row = self.store.get("inbox", event_id)
            if row is None:
                raise SessionError("notice_not_found")
            row["acked"] = True
            self.store.put("inbox", event_id, row)
        return row


__all__ = ("SessionInbox",)
