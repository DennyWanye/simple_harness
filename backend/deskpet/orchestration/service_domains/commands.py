# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Mission commands (forwarded to the SDK host)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_orchestrator.host import with_publish_sources as _with_publish_sources

from .core import OrchestrationServiceCore



class CommandMixin(OrchestrationServiceCore):
    """Mission commands (forwarded to the SDK host)."""

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

    def approve_operation_completion_spec(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._h(self._host.approve_operation_completion_spec, request)

    def operation_intent_status(self, intent_id: str) -> dict[str, Any]:
        return self._h(self._host.operation_intent_status, intent_id)

    def source_command(self, operation: str, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._h(self._host.source_command, operation, request)

    def cancel_mission(self, mission_id: str) -> dict[str, Any]:
        return self._h(self._host.cancel, mission_id)

    def decide(self, request_id: str, decision: str, **fields: str) -> dict[str, Any]:
        return self._h(self._host.decide, request_id, decision, **fields)

    def takeover(self, task_id: str, action: str, *, basis: str, note: str = "") -> dict[str, Any]:
        return self._h(self._host.takeover, task_id, action, basis=basis, note=note)

    def resolve_unknown(self, action_key: str, outcome: str, *, basis: str) -> dict[str, Any]:
        return self._h(self._host.resolve_unknown, action_key, outcome, basis=basis)

    def comment(self, target_id: str, text: str) -> dict[str, Any]:
        return self._h(self._host.comment, target_id, text)


__all__ = ("CommandMixin", "_with_publish_sources")
