# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""长期主会话：一句话的开销不随历史长。

聊天层算用量、记基线时只解码本回合新增的调用；基线存 rowid 水位而不是整份编号清单。
改坏检验：`_usage` 改回解码全部记录 → 第 8 句解码次数大于第 2 句，变红。"""

from __future__ import annotations

import asyncio

from agent_orchestrator.host import Principal, Session
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider


def test_turn_decodes_do_not_grow_with_history(tmp_path, monkeypatch):
    from test_chat import _chat_script

    async def run():
        session = await Session.open(
            tmp_path / "host",
            Principal("u", "User"),
            provider=ScriptedProvider(chat=_chat_script(["好的。"])),
            counter=FixtureWordCounter(),
            require_acceptance=False,
        )
        try:
            await session.send("第 0 句", key="k0")
            uow_type = type(session._chat._pool().runtime.uow)
            seen: list[str] = []
            original = uow_type.read_effective_provider_invocation

            def counting(self, invocation_id: str):
                seen.append(invocation_id)
                return original(self, invocation_id)

            monkeypatch.setattr(uow_type, "read_effective_provider_invocation", counting)
            counts = []
            for i in range(1, 8):
                seen.clear()
                await session.send(f"第 {i} 句", key=f"k{i}")
                counts.append(len(seen))
            assert counts[-1] == counts[0] <= 4, counts
            baselines = session._store.rows("invocation_baselines")
            assert baselines and all(isinstance(value, int) for value in baselines)
        finally:
            await session.close()

    asyncio.run(asyncio.wait_for(run(), 120))
