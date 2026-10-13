"""控制连接状态对象"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket


@dataclass
class _ControlConnection:
    """一条控制连接的状态：分发时交给各消息处理函数。"""

    ws: WebSocket
    session_id: str
    requested_scope: str
    companion_challenge: Any
    human_memory_control_binding: Any
