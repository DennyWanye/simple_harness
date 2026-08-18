# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""
deskpet.memory — SDK 宿主接口 / 适配层。

原 35 个文件 / 17579 行的旧记忆实现已删除。本包现在只保留 host 依赖的
接口契约（Port）与宿主持久适配层，不再包含具体记忆实现：

- :mod:`deskpet.memory.contracts` —— MemoryBackend / Embedder / WorldModelPort
  Protocol，host 唯一依赖的接口。
- :mod:`deskpet.memory.session_db` —— 宿主持久适配层（将薄封装 SDK 后端）。

非记忆模块已 re-home：
- context_snapshot_store → deskpet.agent.context_snapshot_store
- context_segment_store  → deskpet.agent.context_segment_store
- companion_message_projection → deskpet.companion.companion_message_projection

SDK 仓库：simple-harness-memory-sdk（独立 git 仓库）。
边界与接口契约见：ARCHITECTURE/MEMORY_SDK_BOUNDARY.md
"""

from deskpet.memory.contracts import (
    Embedder,
    Fact,
    Hit,
    MemoryBackend,
    Message,
    SessionDB,
    WorldModelPort,
)

__all__ = [
    "MemoryBackend",
    "SessionDB",
    "Embedder",
    "WorldModelPort",
    "Message",
    "Fact",
    "Hit",
]
