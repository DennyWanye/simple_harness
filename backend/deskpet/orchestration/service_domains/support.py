# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Shared helpers for the split class."""

from __future__ import annotations

import logging

from agent_orchestrator.host import HostRequestError
from agent_orchestrator.host.host import (
    QUARANTINE_REASON,
    RECOVERY_ISOLATED_NOTE,
    TERMINAL,
)

# 日志记录器名字钉在拆分前的模块名：按名字配置的日志级别与过滤照旧生效
logger = logging.getLogger("deskpet.orchestration.service")
WORKSPACE_TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list")


class OrchestrationRequestError(HostRequestError):
    """桌面端的请求拒绝：码与 SDK 宿主的一致，界面与主对话按码处理。"""


_STATUS_ZH = {
    "CREATED": "已创建，等确认",
    "PLANNING": "规划中",
    "ACTIVE": "进行中",
    "COMPLETED": "已完成",
    "FAILED": "未完成（已停止）",
    "CANCELLED": "已取消",
}

__all__ = (
    "QUARANTINE_REASON",
    "RECOVERY_ISOLATED_NOTE",
    "TERMINAL",
    "WORKSPACE_TOOLS",
    "_STATUS_ZH",
    "OrchestrationRequestError",
)
