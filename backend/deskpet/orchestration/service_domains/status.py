# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Recovery state, context and budget defaults, and service status."""

from __future__ import annotations

import logging
from typing import Any

from agent_orchestrator.host.host import DEGRADED_RECOVERY_REASON

from .core import OrchestrationServiceCore
from .support import WORKSPACE_TOOLS

# 与原模块同名的记录器（getLogger 按名字返回同一个对象）；就地定义，ruff 才认得出
# 捕获异常后写日志的写法
logger = logging.getLogger("deskpet.orchestration.service")


class StatusMixin(OrchestrationServiceCore):
    """Recovery state, context and budget defaults, and service status."""

    __slots__ = ()

    def _recovery_isolated(self) -> dict[str, dict[str, Any]]:
        return self._host.recovery_isolated()

    # ------------------------------------------------------------ status
    def _context_profiles(self) -> list[dict[str, Any]]:
        return self._host.context_profiles()

    def _context_default(self) -> str | None:
        return self._host.context_default()

    def _mission_token_default(self, profile_id: str | None = None) -> int:
        return self._host.mission_token_default(profile_id)

    def _global_budget_status(self) -> dict[str, Any]:
        return self._host.global_budget_status()

    def status(self) -> dict[str, Any]:
        base = self._host.status()
        return {
            **base,
            "available": self._state == "available",
            "state": self._state,
            "reason": self._reason,
            "sdk_version": self.sdk_version,
            "model": None if self._snapshot is None else self._snapshot.public(),
            "allowed_tools": list(self.runtime_tool_names() or WORKSPACE_TOOLS),
            "diagnostics_available": self._diagnostics_available,
            "storage_over_warn": bool(self._storage.over_warn),
            "deployment_manifest": self._manifest,
        }

    def assurance_root_diagnostic(self) -> dict[str, Any]:
        """非披露诊断（隔离时也开），加 Host 自己的状态。"""
        return self._h(self._host.assurance_root_diagnostic)

    def runtime_tool_names(self) -> tuple[str, ...]:
        return tuple(self._deployment.allowed_tools) if self._deployment is not None else ()


__all__ = ("DEGRADED_RECOVERY_REASON", "StatusMixin")
