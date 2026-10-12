"""宿主进程的连接登记表：控制通道连接、按连接的检视查询任务、聊天对端分组。只改内容、不重新绑定。"""

from __future__ import annotations

import asyncio

from fastapi import WebSocket

# Track control channel connections for lip-sync forwarding
_control_connections: dict[str, WebSocket] = {}
# One independently cancellable Inspector query per connection/kind/root.
_harness_inspector_tasks: dict[tuple[int, str, str], asyncio.Task] = {}
# Transport websocket sid -> effective chat sid shown by that peer.
_chat_peer_groups: dict[str, str] = {
    "default": "default",
    "message-panel-main": "default",
}
