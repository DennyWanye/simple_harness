# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""冻结包的子进程派发入口，``main.py`` 在模块加载时调用。

现在没有需要派发的冻结子进程模块，函数是空操作；原先的向量模型子进程已随认知记忆
系统一起移除。
"""

from __future__ import annotations


def dispatch_frozen_worker_if_requested(argv: list[str] | None = None) -> None:
    """No-op：当前没有需要派发的 frozen worker 模块。"""
    return
