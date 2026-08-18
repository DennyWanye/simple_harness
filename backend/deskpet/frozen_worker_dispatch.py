# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Frozen worker dispatch (memory SDK not integrated yet).

旧 embedder 子进程 worker 已随记忆系统移除。SDK 上线后如需独立 worker，
再在这里恢复对应的 ``-m`` 派发分支。
"""

from __future__ import annotations


def dispatch_frozen_worker_if_requested(argv: list[str] | None = None) -> None:
    """No-op：当前没有需要派发的 frozen worker 模块。"""
    return
