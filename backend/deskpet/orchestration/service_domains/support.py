# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Shared helpers for the split class."""

from __future__ import annotations

import logging

# 日志记录器名字钉在拆分前的模块名：按名字配置的日志级别与过滤照旧生效
logger = logging.getLogger("deskpet.orchestration.service")
WORKSPACE_TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list")
TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
#: 第 2 批 A02：保证通道根隔离时服务状态里的说明（原计划 §10.1：标记缺失 / 不符 → 隔离，只开非披露诊断）
QUARANTINE_REASON = (
    "保证通道根已隔离（库标记不符或状态文件缺失）：只开非披露诊断，不派发任务，不自动写回"
)


class OrchestrationRequestError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


#: 重启恢复第 3 步把"库与自己历史对不上"的任务单独隔离（SDK ``recovery_status().isolated_missions``）；
#: 列表、详情、主对话都用这一句告诉人（第 2 批车道 R）。
RECOVERY_ISOLATED_NOTE = "重启核对没通过，已隔离，不再推进；可以取消"


_STATUS_ZH = {
    "CREATED": "已创建，等确认",
    "PLANNING": "规划中",
    "ACTIVE": "进行中",
    "COMPLETED": "已完成",
    "FAILED": "未完成（已停止）",
    "CANCELLED": "已取消",
}
