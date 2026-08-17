# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""STUB — MemoryComponent。

原记忆组件已移除，等待 simple-harness-memory-sdk 集成。
返回空 Slice，不向 context bundle 注入任何记忆内容。
"""
from __future__ import annotations

from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext


class MemoryComponent:
    """STUB — 记忆上下文组件，SDK 集成前返回空 Slice。"""

    name: str = "memory"

    async def provide(self, ctx: ComponentContext) -> Slice:
        return Slice(
            component_name=self.name,
            text_content="",
            tokens=0,
            priority=80,
            bucket="dynamic",
            meta={"status": "memory_sdk_not_integrated"},
        )


# 验证协议合规性
_ASSERT_PROTOCOL: Component = MemoryComponent()
