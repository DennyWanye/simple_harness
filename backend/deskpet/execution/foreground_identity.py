# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""主对话前台运行的执行会话号。

每轮主对话运行用一个由宿主运行号确定的执行会话号；执行层建运行时用它，读取模型按
运行号算回同一个值去读这一轮的用量，不另存映射。
"""

from __future__ import annotations

import hashlib


def foreground_execution_session_id(host_run_id: str) -> str:
    digest = hashlib.sha256(host_run_id.encode("utf-8")).hexdigest()
    return f"foreground-execution-{digest}"


__all__ = ["foreground_execution_session_id"]
