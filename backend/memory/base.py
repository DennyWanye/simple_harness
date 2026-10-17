# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""短期会话记忆的 ``MemoryStore`` 协议与值对象。

``MemoryStore`` 只管按会话取最近几轮、追加一轮、清空一个会话。
``agent/providers/simple_llm.py`` 可选注入它，给请求带上最近的对话历史；
会话库 ``SessionDB``（``deskpet/memory/session_db_domains/messages.py``）实现它，
并使用这里的 ``ConversationTurn`` / ``StoredTurn`` / ``SessionSummary``。

管理类接口（列轮次、删单轮、列会话、``clear_all``）放在具体类上，不放进协议，
所以最小实现（例如内存里的测试替身）不必实现这些管理接口。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ConversationTurn:
    """One exchange leg — `role` is 'user' or 'assistant'."""

    role: str
    content: str
    created_at: float  # unix timestamp, for ordering


@dataclass(frozen=True)
class StoredTurn:
    """Turn as returned to the management UI — includes DB id + session.

    Separate from ``ConversationTurn`` so the agent-facing Protocol stays
    minimal (agent doesn't need row ids).
    """

    id: int
    session_id: str
    role: str
    content: str
    created_at: float


@dataclass(frozen=True)
class SessionSummary:
    """One row per session — for the "pick a session to inspect" list."""

    session_id: str
    turn_count: int
    last_message_at: float


@runtime_checkable
class MemoryStore(Protocol):
    """Per-session conversation memory. Contract:
    - get_recent returns turns ordered oldest → newest (ready to prepend).
    - append persists one turn; idempotency is NOT guaranteed.
    - clear removes all turns for session_id (test/reset path).
    """

    async def get_recent(self, session_id: str, limit: int = 10) -> list[ConversationTurn]: ...

    async def append(self, session_id: str, role: str, content: str) -> None: ...

    async def clear(self, session_id: str) -> None: ...
