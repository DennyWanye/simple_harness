# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""分层脚本化通道的子进程：跑到执行者的模型调用进行中就停在那里，等父进程强杀。

真机上后端被强杀（SIGKILL）时没有任何收尾代码会运行；父测试用同样的办法杀掉这个子进程，
再在同一个库上起新服务，看被打断的那一步能不能接着做完。

    python -m tests.orchestration._layered_child <root> <marker>
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.testing.fixtures import role_of

from ._layered_lane import LayeredScriptedProvider, layered_service, notes_mission


class _StopsInsideTheWorkerCall(LayeredScriptedProvider):
    def __init__(self, marker: Path) -> None:
        super().__init__()
        self._marker = marker

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        if role_of(request) == "worker":
            self._marker.write_text("in the worker's model call", encoding="utf-8")
            await asyncio.Event().wait()  # 永远不返回，直到进程被杀
        return await super().invoke(request, cancel=cancel)


async def main(root: Path, marker: Path) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    import deskpet.orchestration.native_plane as native_plane

    # 与 ``_layered_lane.quick_runtime`` 相同的两处提速（子进程里没有 monkeypatch）。
    event_handler.WAIT_BACKOFF_MAX = 0.05
    native_plane.embedding_port = lambda _models_dir: (None, "测试里不装向量模型")
    service = layered_service(
        root, Principal("local-user:test", "本机用户"), _StopsInsideTheWorkerCall(marker),
        lease_seconds=4.0, tick_active_seconds=0.05, tick_idle_seconds=0.2,
    )
    service._drive_enabled = True  # 子进程里由服务自己的循环驱动
    await service.start()
    service.create_mission(notes_mission("layered-kill-1"))
    while True:  # until SIGKILL
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2])))
