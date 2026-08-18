# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""STUB — context_snapshot_store。

原模块已移除，等待 simple-harness-memory-sdk 集成。
此 stub 防止 tool_context_persistence.py / agent_loop.py 在运行时崩溃。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


class SnapshotConflictError(Exception):
    """CAS 冲突：另一个写入方已更新了快照。"""


class SnapshotCommitCancelled(Exception):
    """快照提交被取消（任务已超时/中止）。"""


@dataclass(frozen=True, slots=True)
class ContextSnapshotHandle:
    """上下文快照的 CAS 句柄（host 侧工具能力水合所需的稳定 shape）。"""

    session_id: str
    task_scope_id: str
    row_revision: int
    snapshot_hash: str


@dataclass(frozen=True, slots=True)
class SnapshotWriteReceipt:
    """上下文快照写入回执。"""

    previous_row_revision: int
    new_handle: ContextSnapshotHandle
    persisted_tool_scope_revision: int | None = None
    attempt_id: str | None = None
    changed: bool = True
    idempotent: bool = False


async def await_snapshot_commit_ack(task: asyncio.Task) -> Any:  # type: ignore[type-arg]
    """等待快照提交任务并返回结果（stub: 直接 await）。"""
    return await task


class ContextSnapshotStore:
    """STUB — 上下文快照存储，等待 SDK 替换。"""

    async def get(self, session_id: str, task_scope_id: str) -> None:
        return None

    async def flush_once(self, *args: Any, **kwargs: Any) -> Any:
        return None

    async def update_tool_context_cas(self, *args: Any, **kwargs: Any) -> Any:
        return None
