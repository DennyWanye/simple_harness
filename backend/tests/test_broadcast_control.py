# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""控制通道广播：尽力而为，一个连接发送失败不影响其他连接。"""

from __future__ import annotations

import pytest
from host import chat_delivery, connections


def _use_control_connections(monkeypatch, mapping) -> None:
    """原地替换共享的控制通道连接表内容，结束后自动恢复。

    连接表是 host.connections 里的同一个字典，被多个模块按名字导入；
    换掉某个模块里的名字绑定，别的模块看不见。
    """
    table = connections._control_connections
    for key in list(table):
        monkeypatch.delitem(table, key)
    for key, value in mapping.items():
        monkeypatch.setitem(table, key, value)


class _FakeWebSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        if self.fail:
            raise RuntimeError("socket closed")
        self.sent.append(message)


@pytest.mark.asyncio
async def test_broadcast_control_is_best_effort(monkeypatch):
    ok = _FakeWebSocket()
    bad = _FakeWebSocket(fail=True)
    _use_control_connections(monkeypatch, {"ok": ok, "bad": bad})

    await chat_delivery._broadcast_control({"type": "mission_changed", "payload": {"id": "m1"}})

    assert ok.sent == [{"type": "mission_changed", "payload": {"id": "m1"}}]
