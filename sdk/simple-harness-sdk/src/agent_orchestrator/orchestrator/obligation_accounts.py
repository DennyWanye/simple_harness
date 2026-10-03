# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""What one duty has cost so far — derived when read, never stored (阶段 D 裁决 1.6).

Spend and attempts are already recorded, once, where they happen: on the attempt and on
its reservation.  A duty's account is a reading of those: the attempts of every step
that works for it **or for any duty under it**, so a replaced method, a split, or a new
executor does not reset the count — the new steps hang under the same duty.

Bookkeeping only: nothing here is a limit, and nothing here enters a plan binding.
"""
from __future__ import annotations

from typing import Any

from ..storage.obligation_store import ObligationStore
from ..storage.store import Store
from .failure_classes import charges_attempt

def obligation_accounts(store: Store, mission_id: str) -> dict[str, dict[str, int]]:
    """``obligation id → {attempts, failed_attempts, settled_tokens, unknown_usage_attempts}``,
    each the total of the duty and everything under it.

    ``failed_attempts`` counts what the attempt limit counts: an attempt that ended with a
    failure that is the model's (infrastructure failures and interruptions are not
    charged — the same judgement the retry accounting uses)."""

    duties = {str(duty.obligation_id): duty for duty in ObligationStore(store).list_obligations(mission_id)}
    own = {key: {"attempts": 0, "failed_attempts": 0, "settled_tokens": 0, "unknown_usage_attempts": 0}
           for key in duties}
    owner = {str(task): str(duty) for task, duty in store.connection.execute(
        "SELECT task_id, obligation_id FROM task_semantics WHERE mission_id=?", (mission_id,))}
    settled = {str(subject): int(tokens or 0) for subject, tokens in store.connection.execute(
        "SELECT subject_id, settled_tokens FROM budget_reservations WHERE mission_id=?", (mission_id,))}
    unknown = {str(row[0]) for row in store.connection.execute(
        "SELECT DISTINCT subject_id FROM imported_usage WHERE mission_id=? AND unknown=1", (mission_id,))}
    for task in store.list_tasks(mission_id):
        account = own.get(owner.get(task.id, ""))
        if account is None:
            continue
        for attempt in store.list_attempts(task.id):
            account["attempts"] += 1
            if attempt.failure and charges_attempt(attempt.failure):
                account["failed_attempts"] += 1
            account["settled_tokens"] += settled.get(attempt.id, 0)
            if attempt.id in unknown:
                account["unknown_usage_attempts"] += 1
    totals = {key: dict(value) for key, value in own.items()}
    for key, duty in duties.items():
        seen = {key}
        parent = duty.parent_obligation_id
        while parent is not None and str(parent) in duties and str(parent) not in seen:
            seen.add(str(parent))
            for name, value in own[key].items():
                totals[str(parent)][name] += value
            parent = duties[str(parent)].parent_obligation_id
    return totals


def account_of(accounts: dict[str, dict[str, int]], obligation_id: Any) -> dict[str, int]:
    return accounts.get(str(obligation_id), {"attempts": 0, "failed_attempts": 0, "settled_tokens": 0,
                                              "unknown_usage_attempts": 0})


def _labels(store: Store, mission_id: str) -> dict[str, str]:
    """duty id → what the work under it is for, in the words its task was given."""
    import json

    labels: dict[str, str] = {}
    for duty, raw in store.connection.execute(
            "SELECT obligation_id, binding_json FROM task_semantics WHERE mission_id=?"
            " ORDER BY task_id, binding_revision", (mission_id,)):
        goal = (json.loads(raw).get("typed_parameters") or {}).get("goal")
        if isinstance(goal, str) and goal.strip():
            labels.setdefault(str(duty), goal.strip()[:60])
    return labels


def obligation_rows(store: Store, mission_id: str) -> list[dict[str, Any]]:
    """Where the budget went, duty by duty (阶段 E): one row per duty, parents before
    children, each with the totals of :func:`obligation_accounts` (the same reading — nothing
    is computed twice).  A cancelled or replaced duty is still listed: what a dropped method
    spent is part of where the budget went."""

    duties = {str(duty.obligation_id): duty for duty in ObligationStore(store).list_obligations(mission_id)}
    accounts = obligation_accounts(store, mission_id)
    labels = _labels(store, mission_id)
    children: dict[str | None, list[str]] = {}
    for key, duty in duties.items():
        parent = None if duty.parent_obligation_id is None else str(duty.parent_obligation_id)
        children.setdefault(parent if parent in duties else None, []).append(key)
    rows: list[dict[str, Any]] = []

    def walk(key: str, depth: int) -> None:
        duty = duties[key]
        rows.append({
            "obligation_id": key,
            "parent_obligation_id": None if duty.parent_obligation_id is None else str(duty.parent_obligation_id),
            "depth": depth, "label": "整个任务" if depth == 0 else labels.get(key, key),
            "lifecycle": str(duty.lifecycle), **account_of(accounts, key)})
        for child in sorted(children.get(key, ())):
            walk(child, depth + 1)

    for root in sorted(children.get(None, ())):
        walk(root, 0)
    return rows


__all__ = ("account_of", "obligation_accounts", "obligation_rows")
