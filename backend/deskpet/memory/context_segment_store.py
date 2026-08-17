# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""STUB — context_segment_store。

原模块已移除，等待 simple-harness-memory-sdk 集成。
此 stub 仅防止 session_history_planner.py 在导入时崩溃。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass(frozen=True)
class ContextSegment:
    segment_id: str
    session_id: str
    first_message_id: int
    last_message_id: int
    summary: str = ""
    source_hash: str = ""
    created_at: float = 0.0


@dataclass(frozen=True)
class CausalMessageGroup:
    message_ids: tuple[int, ...]
    first_message_id: int
    last_message_id: int
    source_hash: str = ""


@dataclass(frozen=True)
class CoverageCommitProof:
    segment_id: str
    first_message_id: int
    last_message_id: int
    source_hash: str = ""


def canonical_message_hash(messages: Sequence[Any]) -> str:
    """对消息序列计算规范 hash（SHA-256 前16位）。"""
    raw = str([(m.get("id"), m.get("content", "")[:32]) for m in messages])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def eligible_session_messages(messages: Sequence[Any]) -> list[Any]:
    """返回所有可进入段存储的消息（stub: 直接透传）。"""
    return list(messages)


def group_causal_messages(messages: Sequence[Any]) -> list[CausalMessageGroup]:
    """将消息分组为因果组（stub: 每条消息一组）。"""
    return []


def recover_broken_causal_messages(groups: Sequence[Any]) -> list[Any]:
    """恢复断裂的因果消息组（stub: 空操作）。"""
    return []


class ContextSegmentStore:
    """STUB — 上下文段存储，等待 SDK 替换。"""

    async def get_segments(self, session_id: str) -> list[ContextSegment]:
        return []

    async def upsert(self, segment: ContextSegment) -> None:
        pass

    async def delete(self, segment_id: str) -> None:
        pass

    async def list_coverage(
        self, session_id: str, limit: int = 100
    ) -> list[ContextSegment]:
        return []

    async def commit_coverage(self, proof: CoverageCommitProof) -> None:
        pass
