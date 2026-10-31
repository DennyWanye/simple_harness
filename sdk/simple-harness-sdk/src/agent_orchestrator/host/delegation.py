# SPDX-License-Identifier: Apache-2.0
"""Durable session-to-Mission intent, recovery, and cancellation convergence."""

from __future__ import annotations

import asyncio
import time
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import PurePosixPath
from typing import Any

from ..artifacts.source_paths import source_paths_conflict
from .session_ledger import SessionError, digest

_UNSET = object()

TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


def normalize_spec(body: Mapping[str, Any]) -> dict[str, Any]:
    if set(body) - {"goal", "success_criteria", "sources", "reason", "child_key"}:
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
        if not isinstance(path, str) or not path.strip() or "\\" in path or "\x00" in path:
            raise SessionError("invalid_arguments", "invalid source path")
        path = path.strip()
        if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise SessionError("invalid_arguments", "invalid source path")
        path = str(PurePosixPath(path))
        if path == ".":
            raise SessionError("invalid_arguments", "invalid source path")
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
    for index, source in enumerate(attached):
        if any(
            source_paths_conflict(source["path"], previous["path"]) for previous in attached[:index]
        ):
            raise SessionError("invalid_arguments", "source paths conflict")
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
        self._delegate_lock = asyncio.Lock()
        self._create_lock = asyncio.Lock()

    async def delegate(
        self,
        turn_id: str,
        call_id: str,
        arguments: Mapping[str, Any],
        *,
        attempt_id: str = "direct",
        _slot: int | None = None,
        _goal_ref: Any = _UNSET,
        _origin_session_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._delegate_lock:
            return await self._delegate(
                turn_id,
                call_id,
                arguments,
                attempt_id=attempt_id,
                _slot=_slot,
                _goal_ref=_goal_ref,
                _origin_session_id=_origin_session_id,
            )

    async def _delegate(
        self,
        turn_id: str,
        call_id: str,
        arguments: Mapping[str, Any],
        *,
        attempt_id: str = "direct",
        _slot: int | None = None,
        _goal_ref: Any = _UNSET,
        _origin_session_id: str | None = None,
    ) -> dict[str, Any]:
        self.session._check_open()
        goal_ref = self.session._goal_ref() if _goal_ref is _UNSET else _goal_ref
        origin_session_id = _origin_session_id or self.session.session_id
        child_key = arguments.get("child_key")
        child = None
        if child_key is not None:
            if not isinstance(child_key, str):
                raise SessionError("invalid_argument", "child_key must be text")
            child = self.session.child(child_key)
        spec = normalize_spec({k: v for k, v in arguments.items() if k != "child_key"})
        if self.session.parent is None:
            # 槽位属于父回合，不能换一个子账本就把原调用变成新的业务效果。
            _slot = self._route(turn_id, attempt_id, call_id, child_key)
        if child is not None:
            return await child._delegations.delegate(
                turn_id,
                call_id,
                {k: v for k, v in arguments.items() if k != "child_key"},
                attempt_id=attempt_id,
                _slot=_slot,
                _goal_ref=goal_ref,
                _origin_session_id=origin_session_id,
            )
        fingerprint = digest(spec)
        binding_key = digest([turn_id, attempt_id, call_id])
        binding = self.store.get("delegation_calls", binding_key)
        slot = (
            _slot
            if _slot is not None
            else (
                binding["slot"]
                if binding
                else sum(
                    r["turn_id"] == turn_id and r["attempt_id"] == attempt_id
                    for r in self.store.rows("delegation_calls")
                )
            )
        )
        key = f"session:{self.session.session_id}:{turn_id}:slot:{slot}"
        authorization = None
        if self.store.get("delegations", key) is None:
            self.session._check_origin(origin_session_id, turn_id)
            self.session._check_dispatch(goal_ref=goal_ref)
            authorization = await self.session._authorization.ensure(
                "delegate",
                self.session.session_id,
                spec,
                goal_ref=goal_ref,
                delegation_id=f"delegation-{digest(key)[:32]}",
                _guard=lambda: self.session._check_origin(origin_session_id, turn_id),
            )
            self.session._check_origin(origin_session_id, turn_id)
            self.session._check_dispatch(goal_ref=goal_ref)
        with self.store.transaction():
            binding = self.store.get("delegation_calls", binding_key)
            if _slot is not None:
                slot = _slot
            elif binding is None:
                # 连续调用沿用序号；换执行者后仍从原回合的第一个槽位重放。
                slot = sum(
                    r["turn_id"] == turn_id and r["attempt_id"] == attempt_id
                    for r in self.store.rows("delegation_calls")
                )
            else:
                slot = binding["slot"]
            key = f"session:{self.session.session_id}:{turn_id}:slot:{slot}"
            row = self.store.get("delegations", key)
            replayed = row is not None
            drifted = replayed and row["spec_hash"] != fingerprint
            if row is None:
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
                    "origin_session_id": origin_session_id,
                    "origin_turn_id": turn_id,
                    "source_input_id": (self.session._current_input or {}).get("source_input_id")
                    if (self.session._current_input or {}).get("turn_id") == turn_id
                    else None,
                    "call_id": call_id,
                    "slot": slot,
                    "principal_id": self.host.principal.principal_id,
                    "spec_hash": fingerprint,
                    "request": request,
                    "sources": spec["sources"],
                    "reason": arguments.get("reason", ""),
                    "goal_ref": goal_ref,
                    "authorization_params": spec,
                    "authorization": asdict(authorization),
                    "child_key": None
                    if self.session.parent is None
                    else self.session.parent["child_key"],
                    "mission_id": None,
                    "cancel_requested": False,
                }
                self.store.put("delegations", key, row)
            if drifted:
                drift = {
                    "old_spec_hash": row["spec_hash"],
                    "new_spec_hash": fingerprint,
                    "at": time.time(),
                }
                event_key = f"drift:{binding_key}:{fingerprint}"
                event = self.store.event(
                    "delegation_replayed_with_drift",
                    key=event_key,
                    turn_id=turn_id,
                    delegation_id=row["delegation_id"],
                    payload=drift,
                )
                drifts = row.setdefault("spec_drift", [])
                if not any(d["event_id"] == event["event_id"] for d in drifts):
                    drifts.append({**drift, "event_id": event["event_id"]})
                self.store.put("delegations", key, row)
            self.store.put(
                "delegation_calls",
                binding_key,
                {
                    "turn_id": turn_id,
                    "attempt_id": attempt_id,
                    "call_id": call_id,
                    "slot": slot,
                    "intent_binding": "replayed" if replayed else "created",
                    "spec_drift": bool(drifted),
                },
            )
        self.session._checkpoint("delegation_recorded")
        receipt = await self._create(row)
        return {
            **receipt,
            "intent_binding": "replayed" if replayed else "created",
            "spec_drift": bool(drifted),
        }

    def _route(self, turn_id: str, attempt_id: str, call_id: str, child_key: str | None) -> int:
        binding_key = digest([turn_id, attempt_id, call_id])
        with self.store.transaction():
            binding = self.store.get("delegation_routes", binding_key)
            slot = (
                binding["slot"]
                if binding is not None
                else sum(
                    row["turn_id"] == turn_id and row["attempt_id"] == attempt_id
                    for row in self.store.rows("delegation_routes")
                )
            )
            key = digest([turn_id, slot])
            destination = self.store.get("delegation_destinations", key)
            if destination is not None and destination["child_key"] != child_key:
                raise SessionError("idempotency_conflict", "delegation destination changed")
            self.store.put("delegation_destinations", key, {"child_key": child_key})
            self.store.put(
                "delegation_routes",
                binding_key,
                {
                    "turn_id": turn_id,
                    "attempt_id": attempt_id,
                    "call_id": call_id,
                    "slot": slot,
                },
            )
        return slot

    def receipts(self, turn_id: str) -> list[dict[str, Any]]:
        bindings = {
            r["slot"]: r for r in self.store.rows("delegation_calls") if r["turn_id"] == turn_id
        }
        own = [
            {
                **row["receipt"],
                "slot": row["slot"],
                "intent_binding": bindings[row["slot"]]["intent_binding"],
                "spec_drift": bool(row.get("spec_drift")),
            }
            for row in self.store.rows("delegations")
            if row["turn_id"] == turn_id and row.get("receipt")
        ]
        for child in self.session._children.values():
            own.extend(child._delegations.receipts(turn_id))
        return own

    async def _create(self, row: dict[str, Any]) -> dict[str, Any]:
        async with self._create_lock:
            return await self._create_locked(self.store.get("delegations", row["key"]) or row)

    async def _create_locked(self, row: dict[str, Any]) -> dict[str, Any]:
        if row.get("error"):
            self.session._release_unused_result(row)
            raise SessionError(row["error"])
        if row.get("receipt") is not None:
            if row["cancel_requested"] and not row.get("cancel_converged"):
                self._converge_cancel(row)
            return row["receipt"]
        if row.get("terminal_status") == "CANCELLED":
            raise SessionError("cancelled")
        if row["cancel_requested"]:
            result = self._converge_cancel(row)
            current = self.store.get("delegations", row["key"])
            if current.get("receipt") is not None:
                return current["receipt"]
            raise SessionError("effect_unknown" if result["status"] == "UNKNOWN" else "cancelled")
        if row.get("dispatch_state") == "claimed":
            found = self._lookup_created(row)
            if found is not None:
                return self._bind_created(row, found[0].id)
        self.session._check_origin(row["origin_session_id"], row["origin_turn_id"])
        self.session._check_dispatch(
            goal_ref=row.get("goal_ref"), delegation_id=row["delegation_id"]
        )
        await self.session._authorization.ensure(
            "delegate",
            self.session.session_id,
            row["authorization_params"],
            goal_ref=row.get("goal_ref"),
            delegation_id=row["delegation_id"],
            _guard=lambda: self.session._check_origin(
                row["origin_session_id"], row["origin_turn_id"]
            ),
        )
        row = self.store.get("delegations", row["key"])
        if row["cancel_requested"]:
            result = self._converge_cancel(row)
            raise SessionError("effect_unknown" if result["status"] == "UNKNOWN" else "cancelled")
        if row.get("error"):
            raise SessionError(row["error"])
        self.session._check_origin(row["origin_session_id"], row["origin_turn_id"])
        self.session._check_dispatch(
            goal_ref=row.get("goal_ref"), delegation_id=row["delegation_id"]
        )
        try:
            self.session._reserve_result(row)
        except Exception as error:
            if getattr(error, "code", None) == "budget_exhausted":
                self._reject(row, "budget_exhausted")
            raise
        with self.store.transaction():
            row["dispatch_state"] = "claimed"
            self.store.put("delegations", row["key"], row)
        try:
            if row["sources"]:
                receipt = self.host.create_mission_with_sources(
                    {"mission": row["request"], "sources": row["sources"]}
                )
            else:
                receipt = self.host.create_mission(row["request"])
        except Exception as error:
            code = getattr(error, "code", None)
            if code in {
                "conflict",
                "invalid_request",
                "permission_denied",
                "secret_rejected",
                "sdk_refused",
                "not_found",
            } and self._creation_absent(row):
                self._reject(row, code)
            raise
        self.session._check_open()
        self.session._checkpoint("mission_created")
        return self._bind_created(row, receipt["mission_id"])

    def _lookup_created(self, row: Mapping[str, Any]):
        orchestrator = getattr(self.host, "orchestrator", None)
        if orchestrator is None:
            raise SessionError("effect_unknown", "creation has no authoritative lookup")
        try:
            return orchestrator.store.find_mission(self.host.tenant_id, row["key"])
        except Exception:
            raise SessionError("effect_unknown", "creation lookup unavailable") from None

    def _bind_created(self, row: dict[str, Any], mission_id: str) -> dict[str, Any]:
        self.session._check_open()
        # A resumed create must preserve cancellation persisted while creation was pending.
        with self.store.transaction():
            current = self.store.get("delegations", row["key"])
            row = {**current, "mission_id": mission_id}
            row["receipt"] = {
                "delegation_id": row["delegation_id"],
                "mission_id": row["mission_id"],
                "created": True,
                "goal_ref": row.get("goal_ref"),
                "child_key": row.get("child_key"),
            }
            row.pop("held_reason", None)
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
        return self._mission_facts(row)[0]

    def _mission_facts(self, row: Mapping[str, Any]) -> tuple[str, bool]:
        """读一次任务快照：任务状态，以及快照里是否还有结果不明 / 已交接的动作。"""
        snapshot = self.host.snapshot(str(row["mission_id"]))["snapshot"]
        unresolved = any(
            str(effect.get("state", "")).upper() in {"UNKNOWN", "HANDED_OFF"}
            for effect in snapshot.get("actions", [])
        )
        return str(snapshot["mission"]["status"]), unresolved

    def _record_terminal(self, row: dict[str, Any], status: str, unresolved: bool) -> None:
        """任务到终态后记在委派行上，之后不必再读快照判断在途。

        同时记下"快照里还有没有结果不明的动作"：终态任务不会再有在途工作和待答请求，但取消时
        刚交接出去的连接器动作可能结果不明，Goal 结构检查还得看它。"""
        with self.store.transaction():
            row["terminal_status"] = status
            row["terminal_actions_unresolved"] = unresolved
            self.store.put("delegations", row["key"], row)

    def has_in_flight(self) -> bool:
        results = {notice["delegation_id"] for notice in self.store.rows("inbox")}
        for row in self.store.rows("delegations"):
            if (
                row.get("error")
                or row["delegation_id"] in results
                or row.get("terminal_status") in TERMINAL
            ):
                continue
            if row["mission_id"] is None:
                return True
            status, unresolved = self._mission_facts(row)
            if status not in TERMINAL:
                return True
            self._record_terminal(row, status, unresolved)
        return False

    def _creation_absent(self, row: Mapping[str, Any]) -> bool:
        orchestrator = getattr(self.host, "orchestrator", None)
        if orchestrator is None:
            return False
        try:
            return orchestrator.store.find_mission(self.host.tenant_id, row["key"]) is None
        except Exception:
            return False

    def _reject(self, row: dict[str, Any], code: str) -> None:
        with self.store.transaction():
            row["error"] = code
            self.store.put("delegations", row["key"], row)
            self.store.event(
                "error",
                key=f"rejected:{row['delegation_id']}",
                delegation_id=row["delegation_id"],
                turn_id=row["turn_id"],
                payload={"code": code},
            )
        self.session._release_unused_result(row)

    def find(self, task_id: str) -> dict[str, Any]:
        for row in self.store.rows("delegations"):
            if task_id in {row["delegation_id"], row.get("mission_id")}:
                return row
        raise SessionError("permission_denied", "task is not associated with this session")

    def cancel(self, task_id: str) -> dict[str, Any]:
        self.session._check_open()
        row = self.find(task_id)
        if not row["cancel_requested"]:
            with self.store.transaction():
                row["cancel_requested"] = True
                self.store.put("delegations", row["key"], row)
        return self._converge_cancel(row)

    def _converge_cancel(self, row: dict[str, Any]) -> dict[str, Any]:
        if not row.get("mission_id"):
            if not row["cancel_requested"]:
                return {
                    "delegation_id": row["delegation_id"],
                    "status": "CREATING",
                    "cancel_requested": False,
                }
            absent = row.get("dispatch_state") != "claimed"
            if not absent:
                orchestrator = getattr(self.host, "orchestrator", None)
                try:
                    found = orchestrator.store.find_mission(self.host.tenant_id, row["key"])
                    absent = found is None
                    if found is not None:
                        row["mission_id"] = found[0].id
                        row["receipt"] = {
                            "delegation_id": row["delegation_id"],
                            "mission_id": row["mission_id"],
                            "created": True,
                            "goal_ref": row.get("goal_ref"),
                            "child_key": row.get("child_key"),
                        }
                        with self.store.transaction():
                            self.store.put("delegations", row["key"], row)
                except Exception:
                    absent = False
            if not row.get("mission_id"):
                updated = dict(row)
                if absent:
                    updated.update(
                        terminal_status="CANCELLED", cancel_converged=True, error="cancelled"
                    )
                else:
                    updated["cancel_effect_state"] = "unknown"
                if updated != row:
                    with self.store.transaction():
                        self.store.put("delegations", row["key"], updated)
                return {
                    "delegation_id": row["delegation_id"],
                    "status": "CANCELLED" if absent else "UNKNOWN",
                    "cancel_requested": True,
                    "effect_state": "not_committed" if absent else "unknown",
                }
        if row.get("terminal_status") in TERMINAL:
            status = row["terminal_status"]
        else:
            status, unresolved = self._mission_facts(row)
            if status in TERMINAL:
                # 终态不会再变：记下来，屏障收不了口时每拍收敛不必再读任务快照。
                self._record_terminal(row, status, unresolved)
        if row["cancel_requested"]:
            result = (
                self.host.cancel(row["mission_id"])
                if status not in TERMINAL
                else {"mission_id": row["mission_id"], "status": status}
            )
            if result["status"] in TERMINAL and not row.get("cancel_converged"):
                with self.store.transaction():
                    row["cancel_converged"] = True
                    self.store.put("delegations", row["key"], row)
            return {
                **result,
                "effect_state": "completed" if result["status"] in TERMINAL else "handed_off",
            }
        return {"mission_id": row["mission_id"], "status": status}

    async def recover(self) -> None:
        for row in self.store.rows("delegations"):
            if row.get("error"):
                self.session._release_unused_result(row)
            else:
                try:
                    await self._create(row)
                except Exception as error:
                    if getattr(error, "code", None) in {
                        "authorization_required",
                        "authorization_revoked",
                        "request_expired",
                        "cancelled",
                        "effect_unknown",
                    }:
                        current = self.store.get("delegations", row["key"])
                        if current.get("held_reason") != error.code:
                            with self.store.transaction():
                                current["held_reason"] = error.code
                                self.store.put("delegations", row["key"], current)
                        continue
                    current = self.store.get("delegations", row["key"])
                    if not current.get("error"):
                        raise


__all__ = ("Delegations",)
