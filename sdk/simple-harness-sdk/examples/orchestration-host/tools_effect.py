# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""注册一个有副作用的连接器（相册），看自动模式代批、"总要人批"等人（不联网，脚本模型）。

    python tools_effect.py

两个任务：

1. "把 /photos/a.jpg 移到归档"：要求写成 ``{"statement": "action:photos.move:/photos/a.jpg",
   "params": {"to": "/archive/a.jpg"}}``。自动模式下系统确认完成要求、准备申请单、审阅、代批、
   执行一次，回执核对后任务完成。
2. "把 /photos/b.jpg 分享给家人"：``share`` 标了 ``always_needs_approval``，自动模式也不代批；
   这里由脚本代替人在 ``host.approvals()`` 里找到它、点批准。

连接器自己按幂等键去重、能按键查询（``lookup``），并且查询为空后封口（``lookup_fenced``）：
这样结果不明时系统能证明"没有生效"。
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from agent_orchestrator.host import HostSettings, OrchestrationHost, Principal
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider
from agent_orchestrator.runtime.connectors import (
    ConnectorRejected,
    OperationSpec,
    Receipt,
    params_hash,
)


class PhotoLibrary:
    """假的相册服务。真实应用里 ``execute`` 调自己的服务，按 ``idempotency_key`` 去重。"""

    name = "photos"
    supports_idempotency = True
    supports_reconciliation = True
    lookup_authority = "authoritative"  # 执行与记账是原子的，记录不会被别人回滚
    lookup_fenced = True  # 查询为空之后，这个键以后再来的执行一律拒绝
    operations = {
        "move": OperationSpec("move", "L2", ("to",), description="把照片移到 to 指定的相册路径"),
        "share": OperationSpec(
            "share",
            "L2",
            ("with",),
            description="把照片分享给 with 指定的人",
            always_needs_approval=True,
        ),
    }

    def __init__(self) -> None:
        self.ledger: dict[str, Receipt] = {}
        self.fenced: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def normalize_target(self, target: str) -> str:
        return target.strip()

    async def execute(self, operation, target, params, *, idempotency_key):
        """``async def``：跑在宿主事件循环上，里面只能 await，不能阻塞。"""

        if idempotency_key in self.fenced:
            raise ConnectorRejected("this key was fenced by a lookup")
        if idempotency_key not in self.ledger:  # 同一个键只执行一次
            self.calls.append((operation, target))
            self.ledger[idempotency_key] = Receipt(
                idempotency_key,
                self.name,
                operation,
                target,
                params_hash(params),
                applied=True,
                after={"operation": operation, **params},
            )
        return self.ledger[idempotency_key]

    def lookup(self, idempotency_key):
        found = self.ledger.get(idempotency_key)
        if found is None:
            self.fenced.add(idempotency_key)
        return found


async def approve_when_asked(host: OrchestrationHost, mission_id: str) -> None:
    """代替人：等到批准卡片出现就点批准。"""

    while True:
        for row in host.approvals(mission_id):
            if row.get("kind") == "action" and row.get("state") == "PENDING":
                print("等人批准：", row["action"]["operation"], row["action"]["target"])
                host.decide(row["request_id"], "approve")
                return
        await asyncio.sleep(0.2)


async def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="tools-effect-"))
    photos = PhotoLibrary()
    host = await OrchestrationHost.open(
        root,
        principal=Principal("demo-user", "演示用户"),
        provider=ScriptedProvider(),
        counter=FixtureWordCounter(),
        settings=HostSettings(max_action_level="L2"),  # 注册了连接器就必须写明最高风险等级
        require_acceptance=False,  # 脚本模型只证明接线；正式用法不要关
        connectors=[photos],
    )
    try:
        moved = host.create_mission(
            {
                "goal": "把 /photos/a.jpg 移到归档，并在 NOTES.md 记一笔",
                "success_criteria": [
                    "file:NOTES.md",
                    {
                        "statement": "action:photos.move:/photos/a.jpg",
                        "params": {"to": "/archive/a.jpg"},
                    },
                ],
                "idempotency_key": "tools-effect-move",
            }
        )
        first = await host.run_until_settled(moved["mission_id"], timeout=300)
        print("任务一：", first["snapshot"]["mission"]["status"], photos.calls)

        shared = host.create_mission(
            {
                "goal": "把 /photos/b.jpg 分享给家人，并在 NOTES.md 记一笔",
                "success_criteria": [
                    "file:NOTES.md",
                    {
                        "statement": "action:photos.share:/photos/b.jpg",
                        "params": {"with": "family"},
                    },
                ],
                "idempotency_key": "tools-effect-share",
            }
        )
        person = asyncio.create_task(approve_when_asked(host, shared["mission_id"]))
        second = await host.run_until_settled(shared["mission_id"], timeout=300)
        await person
        print("任务二：", second["snapshot"]["mission"]["status"], photos.calls)
        ok = (
            str(first["snapshot"]["mission"]["status"]) == "COMPLETED"
            and str(second["snapshot"]["mission"]["status"]) == "COMPLETED"
            and photos.calls == [("move", "/photos/a.jpg"), ("share", "/photos/b.jpg")]
        )
        return 0 if ok else 1
    finally:
        await host.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
