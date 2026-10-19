# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Mission commands (forwarded to the SDK host) and the Host's mission notices."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from agent_orchestrator.host import with_publish_sources as _with_publish_sources
from agent_orchestrator.orchestrator.event_handler import ACTION_SETTLED_AFTER_STOP

from ..notices import NoticeNotFound
from .core import OrchestrationServiceCore
from .support import _STATUS_ZH

logger = logging.getLogger(__name__)


def _notice_line(row: Mapping[str, Any]) -> str:
    """One line of the main Agent's notice context: a Mission that ended, or an outside
    action of a stopped Mission whose result is known now (一致性补改 H-2)."""
    head = f"- mission_id={row['mission_id']} status={row['status']} goal={row['goal'][:80]!r}"
    settled = row.get("action_settled")
    if settled:
        return (
            head + f" action_checked={settled['operation']} {settled['target']}"
            f" result={'applied' if settled['applied'] else 'not_applied'}"
        )
    return (
        head
        + (f" stop_reason={row['stop_reason']}" if row["stop_reason"] else "")
        + (
            f" unresolved_actions={row['unresolved_actions']}"
            " (outcome unknown; the system keeps checking)"
            if row.get("unresolved_actions")
            else ""
        )
    )


class CommandMixin(OrchestrationServiceCore):
    """Mission commands (forwarded to the SDK host) and the Host's mission notices."""

    __slots__ = ()

    def _door(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._h(self._host.door, request)

    # ------------------------------------------------------------ commands
    def create_mission(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._h(self._host.create_mission, request)

    def create_mission_with_sources(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._h(self._host.create_mission_with_sources, request)

    def planning_authorization(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._h(self._host.planning_authorization, request)

    def answer_planning_question(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._h(self._host.answer_planning_question, request)

    def amend_requirements(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._h(self._host.amend_requirements, request)

    def method_library(self) -> dict[str, Any]:
        return self._h(self._host.method_library)

    def retire_library_entry(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._h(self._host.retire_library_entry, request)

    def approve_operation_completion_spec(
        self, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._h(self._host.approve_operation_completion_spec, request)

    def submit_operation_intent(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._h(self._host.submit_operation_intent, request)

    def operation_intent_status(self, intent_id: str) -> dict[str, Any]:
        return self._h(self._host.operation_intent_status, intent_id)

    def source_command(
        self, operation: str, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._h(self._host.source_command, operation, request)

    def cancel_mission(self, mission_id: str) -> dict[str, Any]:
        return self._h(self._host.cancel, mission_id)

    def decide(self, request_id: str, decision: str, **fields: str) -> dict[str, Any]:
        return self._h(self._host.decide, request_id, decision, **fields)

    def takeover(
        self, task_id: str, action: str, *, basis: str, note: str = ""
    ) -> dict[str, Any]:
        return self._h(self._host.takeover, task_id, action, basis=basis, note=note)

    def resolve_unknown(
        self, action_key: str, outcome: str, *, basis: str
    ) -> dict[str, Any]:
        return self._h(self._host.resolve_unknown, action_key, outcome, basis=basis)

    def comment(self, target_id: str, text: str) -> dict[str, Any]:
        return self._h(self._host.comment, target_id, text)

    # ------------------------------------------------------------ mission notices
    def pending_notices(
        self, _body: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Notices the person has not acknowledged yet, newest last, with the Mission's
        current status (a notice names the final event; the status is read now)."""

        # 每次拉列表（前端重连后会重新拉）先从已读序号续读，断线期间丢的推送补回来
        self._notices.catch_up(self._orchestrator.store)
        rows = []
        for notice in self._notices.pending():
            mission = self._orchestrator.store.get_mission(notice["mission_id"])
            if mission is None:
                continue
            status = str(getattr(mission.status, "value", mission.status))
            row = {
                **notice,
                "status": status,
                "status_zh": _STATUS_ZH.get(status, status),
                "goal": str(mission.goal or "")[:200],
                "stop_reason": None
                if mission.stop_reason is None
                else str(mission.stop_reason),
                "unresolved_actions": len(
                    (mission.final_report or {}).get("unresolved_actions") or ()
                ),
            }
            # 一致性补改 H-2：通知的事件若是"停下后对外操作有了结果"，卡片说的是那个结果
            found = self._orchestrator.store.connection.execute(
                "SELECT type, payload_json FROM events WHERE event_id=?",
                (notice["notice_id"],),
            ).fetchone()
            if found is not None and found[0] == ACTION_SETTLED_AFTER_STOP:
                payload = json.loads(found[1])
                row["action_settled"] = {
                    "operation": str(payload.get("operation") or ""),
                    "target": str(payload.get("target") or ""),
                    "applied": payload.get("applied") is True,
                }
            rows.append(row)
        return rows

    def ack_notice(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Only the person's click on the card in the main conversation reaches here."""

        if body.get("all") is True:
            acked = self._notices.ack_all()
            self.wake()
            return {"acked": acked}
        notice_id = body.get("notice_id")
        if not isinstance(notice_id, str) or not notice_id:
            raise NoticeNotFound("notice_id is required")
        row = self._notices.ack(notice_id)
        self.wake()
        return row

    def notice_context_text(self, *, limit: int = 3) -> str:
        """A few lines for the main Agent's next turn: background tasks that ended and
        that the person has not acknowledged yet.  Empty when there are none."""

        try:
            pending = self.pending_notices()
        except Exception:
            logger.debug("orchestration_notice_read_failed", exc_info=True)
            return ""
        if not pending:
            return ""
        lines = [_notice_line(row) for row in pending[-limit:]]
        more = len(pending) - len(lines)
        return (
            "\nHost task notices (background tasks that ended; the user has not acknowledged them "
            "in this chat yet; use mission_status for details and tell the user when relevant):\n"
            + "\n".join(lines)
            + (f"\n- …and {more} more" if more > 0 else "")
        )


__all__ = ("CommandMixin", "_with_publish_sources")
