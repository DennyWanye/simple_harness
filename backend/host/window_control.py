"""窗口控制凭证的一次性引导：Rust 外壳只在启动时从标准输入传一行凭证。手动或测试启动没有这个标记，绝不读标准输入；此时伴侣的特权变更保持关闭，其他功能照常。"""

from __future__ import annotations

import os
import sys

from deskpet.companion.control_credentials import (
    WindowControlBootstrap,
    WindowControlCredentialError,
)

# Rust marks only its one-shot stdin bootstrap. Manual/pytest launches omit
# the marker and must never block on stdin; privileged Companion mutations
# then remain fail-closed while all ordinary backend features keep working.
_WINDOW_CONTROL_BOOTSTRAP: WindowControlBootstrap | None = None
if os.environ.get("DESKPET_WINDOW_CONTROL_BOOTSTRAP") in {"stdin-v1", "stdin-v2"}:
    try:
        _WINDOW_CONTROL_BOOTSTRAP = WindowControlBootstrap.parse_line(sys.stdin.readline().strip())
    except WindowControlCredentialError:
        _WINDOW_CONTROL_BOOTSTRAP = None
