# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""Cross-table read queries: waits, snapshots and budget usage."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...contracts import Artifact, Attempt, Claim, Mission, Task
from ...memory.verified_knowledge import KnowledgeRecord
from .core import StoreCore
from .records import DispatchIntent, StoredResult, StoreError


class StoreQueryMixin(StoreCore):
    """Cross-table read queries: waits, snapshots and budget usage."""

    __slots__ = ()

    if TYPE_CHECKING:

        def count_events(self, mission_id: str, event_type: str | None = None) -> int: ...
        def find_result_for_attempt(self, attempt_id: str) -> StoredResult | None: ...
        def get_intent_for_subject(self, subject_id: str) -> DispatchIntent | None: ...
        def get_mission(self, mission_id: str) -> Mission | None: ...
        def get_mission_domain(self, mission_id: str) -> dict[str, Any] | None: ...
        def get_mission_policy(self, mission_id: str) -> dict[str, Any] | None: ...
        def list_actions(
            self, mission_id: str | None = None, *states: str
        ) -> list[dict[str, Any]]: ...
        def list_approvals(
            self, mission_id: str | None = None, *states: str
        ) -> list[dict[str, Any]]: ...
        def list_artifacts(self, attempt_id: str) -> list[Artifact]: ...
        def list_attempts(self, task_id: str) -> list[Attempt]: ...
        def list_knowledge(
            self, mission_id: str, *, status: str | None = None
        ) -> list[KnowledgeRecord]: ...
        def list_mission_claims(self, mission_id: str) -> list[Claim]: ...
        def list_overrides(self, mission_id: str) -> list[dict[str, Any]]: ...
        def list_sources(
            self, mission_id: str, active_only: bool = False
        ) -> list[dict[str, Any]]: ...
        def list_tasks(self, mission_id: str) -> list[Task]: ...
        def list_verifications(
            self, result_id: str, *, requirements_revision: int | None
        ) -> list[dict[str, Any]]: ...

    # ------------------------------------------------------------ step 7 waiting view
    def waiting_on(self, mission_id: str) -> list[dict[str, Any]]:
        """D7-7': what an ACTIVE Mission waits on a person or a reconciliation for — a
        derived view, not a Mission state (ORCH §13 keeps the Mission state set)."""

        waiting: list[dict[str, Any]] = [
            {
                "kind": str(request["kind"]),
                "request_id": request["request_id"],
                "subject": request["subject_key"],
                "since": request.get("created_at"),
            }
            for request in self.list_approvals(mission_id, "PENDING")
        ]
        waiting.extend(
            {
                "kind": "reconciliation",
                "action_key": action["action_key"],
                "needs_human": bool(action.get("needs_human")),
                "since": action.get("handed_off_at"),
            }
            for action in self.list_actions(mission_id, "UNKNOWN")
        )
        waiting.extend(  # 阶段 B 裁决第 1 类: a failed hand-off whose proof could not be read
            {
                "kind": "reconciliation",
                "action_key": action["action_key"],
                "needs_human": True,
                "state": "FAILED",
                "since": action.get("updated_at") or action.get("handed_off_at"),
            }
            for action in self.list_actions(mission_id, "FAILED")
            if action.get("needs_human")
        )
        waiting.extend(  # review P2-6: a hand-off whose outcome is not in yet (maybe a crash)
            {
                "kind": "handoff",
                "action_key": action["action_key"],
                "owner": action.get("owner"),
                "lease_expires_at": action.get("lease_expires_at"),
            }
            for action in self.list_actions(mission_id, "HANDED_OFF")
        )
        return waiting

    def human_wait_seconds(self, mission_id: str, now: float) -> float:
        """D7-7': the union of the spans during which a request of this Mission waited for
        a person (created → closed, or → now while still open)."""

        spans = sorted(
            (
                float(request.get("created_at") or 0.0),
                now
                if request["state"] == "PENDING"
                else float(request.get("closed_at") or request.get("created_at") or 0.0),
            )
            for request in self.list_approvals(mission_id)
        )
        total = 0.0
        current: tuple[float, float] | None = None
        for start, end in spans:
            if current is not None and start <= current[1]:
                current = (current[0], max(current[1], end))
                continue
            if current is not None:
                total += current[1] - current[0]
            current = (start, end)
        if current is not None:
            total += current[1] - current[0]
        return max(total, 0.0)

    def snapshot(self, mission_id: str) -> dict[str, Any]:
        """Everything about one Mission, for ``final_state.json`` and CLI ``get``."""

        mission = self.get_mission(mission_id)
        if mission is None:
            raise StoreError(f"unknown mission {mission_id}")
        tasks = self.list_tasks(mission_id)
        attempts = [attempt for task in tasks for attempt in self.list_attempts(task.id)]
        results = [self.find_result_for_attempt(attempt.id) for attempt in attempts]
        return {
            "mission": mission.to_json(),
            "budget_usage": self.mission_budget_usage(mission_id),
            "tasks": [task.to_json() for task in tasks],
            "attempts": [attempt.to_json() for attempt in attempts],
            "results": [
                {
                    **stored.to_json(),
                    "verifications": self.list_verifications(
                        stored.envelope.id, requirements_revision=None
                    ),
                }
                for stored in results
                if stored is not None
            ],
            "claims": [claim.to_json() for claim in self.list_mission_claims(mission_id)],
            "knowledge": [record.to_json() for record in self.list_knowledge(mission_id)],
            "artifacts": [
                artifact.to_json()
                for attempt in attempts
                for artifact in self.list_artifacts(attempt.id)
            ],
            "intents": [
                intent.to_json()
                for attempt in attempts
                for intent in [self.get_intent_for_subject(attempt.id)]
                if intent is not None
            ],
            # step 7 (D7-11); an older library read by replay has no such tables (D8-1')
            "actions": self.list_actions(mission_id) if self.has_table("actions") else [],
            "approvals": self.list_approvals(mission_id) if self.has_table("approvals") else [],
            "human_overrides": self.list_overrides(mission_id)
            if self.has_table("human_overrides")
            else [],
            "waiting_on": self.waiting_on(mission_id) if self.has_table("approvals") else [],
            "mission_policy": self.get_mission_policy(mission_id),  # step 9 (plan D9-3')
            "mission_domain": self.get_mission_domain(mission_id),  # P3.3 (plan v3 D1/D9)
            "sources": self.list_sources(mission_id) if self.has_table("sources") else [],
            "event_count": self.count_events(mission_id),
        }

    def mission_budget_usage(self, mission_id: str) -> dict[str, Any] | None:
        """Current Mission allocation ledger, never summed historical reservations.

        The facade reads this within its snapshot transaction. Older replay libraries
        without a ledger report unavailable rather than inventing zero usage.
        """
        if not self.has_table("budget_accounts"):
            return None
        row = self.connection.execute(
            "SELECT reserved_tokens, settled_tokens, attempts_created, version "
            "FROM budget_accounts WHERE mission_id = ? AND scope = 'mission'",
            (mission_id,),
        ).fetchone()
        if row is None:
            return None
        usage = dict(row)
        # V34（核验 opt.179 建议）：超出准入上限的调用照记不拦（可多算不可少算）；把它们的数目与
        # 多出的 token 一起交给任务详情与审计视图，只显示、不拦。
        overrun = (
            self.connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(MAX(actual_tokens - total_upper, 0)), 0) "
                "FROM provider_token_grants WHERE mission_id = ? AND state = 'OVERRUN'",
                (mission_id,),
            ).fetchone()
            if self.has_table("provider_token_grants")
            else (0, 0)
        )
        usage["overrun_calls"] = int(overrun[0])
        usage["overrun_tokens"] = int(overrun[1])
        return usage
