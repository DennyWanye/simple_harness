# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""STUB — WorkspaceMemoryComponent。

原工作区记忆组件已移除，等待 simple-harness-memory-sdk 集成。
"""
from __future__ import annotations

from typing import Any

from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext


class WorkspaceMemoryComponent:
    """STUB — 代码工作区记忆组件，SDK 集成前返回空 Slice。"""

    name: str = "workspace_memory"

    def __init__(self, store: Any = None) -> None:
        self._store = store  # 预留接口，SDK 集成后注入

    async def provide(self, ctx: ComponentContext) -> Slice:
        return Slice(
            component_name=self.name,
            text_content="",
            tokens=0,
            priority=75,
            bucket="dynamic",
            meta={"status": "memory_sdk_not_integrated"},
        )


# 验证协议合规性
_ASSERT_PROTOCOL: Component = WorkspaceMemoryComponent()
