# SPDX-License-Identifier: Apache-2.0
"""Durable session-to-Mission intent, recovery, and cancellation convergence."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from .session_ledger import SessionError, digest

TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


def normalize_spec(body: Mapping[str, Any]) -> dict[str, Any]:
    if set(body) - {"goal", "success_criteria", "sources", "reason"}:
        raise SessionError("invalid_arguments", "identity and policy are supplied by the SDK")
    goal, criteria, sources = (
        body.get("goal"),
        body.get("success_criteria"),
        body.get("sources", []),
    )
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 12000:
        raise SessionError("invalid_arguments", "goal must be bounded nonempty text")
    if (
        not isinstance(criteria, list)
        or not 1 <= len(criteria) <= 12
        or any(not isinstance(c, str) or not c.strip() or len(c) > 12000 for c in criteria)
    ):
        raise SessionError("invalid_arguments", "provide 1 to 12 success criteria")
    if not isinstance(sources, list) or len(sources) > 8:
        raise SessionError("invalid_arguments", "provide at most 8 sources")
    attached = []
    for source in sources:
        if not isinstance(source, Mapping) or set(source) != {"path", "content"}:
            raise SessionError("invalid_arguments", "sources require path and content")
        path, content = source["path"], source["content"]
        if not isinstance(path, str) or not path.strip() or "\\" in path:
            raise SessionError("invalid_arguments", "invalid source path")
        path = path.strip()
        if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise SessionError("invalid_arguments", "invalid source path")
        path = str(PurePosixPath(path))
        if not path.startswith("sources/"):
            path = "sources/" + path
        if not isinstance(content, str) or not content or len(content) > 100000:
            raise SessionError("invalid_arguments", "source content must be bounded nonempty text")
        attached.append(
            {
                "path": path,
                "content": content,
                "kind": "markdown" if path.lower().endswith(".md") else "text",
            }
        )
    if len({s["path"] for s in attached}) != len(attached):
        raise SessionError("invalid_arguments", "duplicate source path")
    if not isinstance(body.get("reason", ""), str):
        raise SessionError("invalid_arguments", "reason must be text")
    return {
        "goal": " ".join(unicodedata.normalize("NFC", goal).split()),
        "success_criteria": [c.strip() for c in criteria],
        "sources": sorted(attached, key=lambda s: s["path"]),
    }


class Delegations:
    def __init__(self, session: Any) -> None:
        self.session = session
        self.store, self.host = session._store, session._host

    def delegate(self, turn_id: str, call_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        spec = normalize_spec(arguments)
        key = f"session:{self.session.session_id}:{turn_id}:{digest(spec['goal'])}"
        fingerprint = digest(spec)
        with self.store.transaction():
            row = self.store.get("delegations", key)
            if row is not None:
                if row["spec_hash"] != fingerprint:
                    raise SessionError(
                        "idempotency_conflict", "same intent has different requirements or sources"
                    )
            else:
                for pending in self.store.rows("delegations"):
                    if not pending.get("error") and (
                        not pending.get("mission_id") or self.status(pending) not in TERMINAL
                    ):
                        raise SessionError("busy", "one delegated task is already in flight")
                request = self.host.door(
                    {
                        "goal": spec["goal"],
                        "success_criteria": spec["success_criteria"],
                        "idempotency_key": key,
                    }
                )
                row = {
                    "key": key,
                    "delegation_id": f"delegation-{digest(key)[:32]}",
                    "session_id": self.session.session_id,
                    "turn_id": turn_id,
                    "call_id": call_id,
                    "principal_id": self.host.principal.principal_id,
                    "spec_hash": fingerprint,
                    "request": request,
                    "sources": spec["sources"],
                    "reason": arguments.get("reason", ""),
                    "mission_id": None,
                    "cancel_requested": False,
                }
                self.store.put("delegations", key, row)
        self.session._checkpoint("delegation_recorded")
        return self._create(row)

    def _create(self, row: dict[str, Any]) -> dict[str, Any]:
        if row.get("error"):
            raise SessionError(row["error"])
        if row.get("receipt") is not None:
            if row["cancel_requested"] and not row.get("cancel_converged"):
                self._converge_cancel(row)
            return row["receipt"]
        try:
            self.session._reserve_result(row)
        except Exception as error:
            if getattr(error, "code", None) == "budget_exhausted":
                with self.store.transaction():
                    row["error"] = "budget_exhausted"
                    self.store.put("delegations", row["key"], row)
                    self.store.event(
                        "error",
                        key=f"rejected:{row['delegation_id']}",
                        delegation_id=row["delegation_id"],
                        turn_id=row["turn_id"],
                        payload={"code": "budget_exhausted"},
                    )
            raise
        if row["sources"]:
            receipt = self.host.create_mission_with_sources(
                {"mission": row["request"], "sources": row["sources"]}
            )
        else:
            receipt = self.host.create_mission(row["request"])
        self.session._checkpoint("mission_created")
        # A resumed create must preserve cancellation persisted while creation was pending.
        with self.store.transaction():
            current = self.store.get("delegations", row["key"])
            row = {**current, "mission_id": receipt["mission_id"]}
            row["receipt"] = {
                "delegation_id": row["delegation_id"],
                "mission_id": row["mission_id"],
                "created": True,
            }
            self.store.put("delegations", row["key"], row)
            self.store.event(
                "delegated",
                key=row["delegation_id"],
                turn_id=row["turn_id"],
                delegation_id=row["delegation_id"],
                mission_id=row["mission_id"],
            )
        self._converge_cancel(row)
        return row["receipt"]

    def status(self, row: Mapping[str, Any]) -> str:
        detail = self.host.snapshot(str(row["mission_id"]))
        return str(detail["snapshot"]["mission"]["status"])

    def find(self, task_id: str) -> dict[str, Any]:
        for row in self.store.rows("delegations"):
            if task_id in {row["delegation_id"], row.get("mission_id")}:
                return row
        raise SessionError("permission_denied", "task is not associated with this session")

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self.store.transaction():
            row = self.find(task_id)
            row["cancel_requested"] = True
            self.store.put("delegations", row["key"], row)
        return self._converge_cancel(row)

    def _converge_cancel(self, row: dict[str, Any]) -> dict[str, Any]:
        if not row.get("mission_id"):
            return {
                "delegation_id": row["delegation_id"],
                "status": "CREATING",
                "cancel_requested": row["cancel_requested"],
            }
        status = self.status(row)
        if row["cancel_requested"]:
            result = (
                self.host.cancel(row["mission_id"])
                if status not in TERMINAL
                else {"mission_id": row["mission_id"], "status": status}
            )
            if result["status"] in TERMINAL:
                with self.store.transaction():
                    row["cancel_converged"] = True
                    self.store.put("delegations", row["key"], row)
            return result
        return {"mission_id": row["mission_id"], "status": status}

    def recover(self) -> None:
        for row in self.store.rows("delegations"):
            if not row.get("error"):
                self._create(row)


__all__ = ("Delegations",)
