# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""STUB — session_db.SessionDB。

原模块已移除，等待 simple-harness-memory-sdk 集成。
此 stub 防止 product_delivery.py 模块级导入崩溃。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SessionDB:
    """STUB — 会话数据库，等待 SDK 替换。所有操作返回空值。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        logger.warning("SessionDB stub: memory SDK not integrated yet")
        self._write_lock = asyncio.Lock()

    async def get_session_goals(self, session_id: str) -> list[dict]:
        return []

    async def upsert_goal(self, *args: Any, **kwargs: Any) -> str:
        return ""

    async def update_goal_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def list_goal_tasks(self, *args: Any, **kwargs: Any) -> list[dict]:
        return []

    async def create_task(self, *args: Any, **kwargs: Any) -> str:
        return ""

    async def claim_ready_task(self, *args: Any, **kwargs: Any) -> Any:
        return None

    async def update_task(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def get_messages(self, *args: Any, **kwargs: Any) -> list[dict]:
        return []

    async def append_message(self, *args: Any, **kwargs: Any) -> int:
        return 0

    async def close(self) -> None:
        pass

    # 支持 async context manager
    async def __aenter__(self) -> "SessionDB":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
