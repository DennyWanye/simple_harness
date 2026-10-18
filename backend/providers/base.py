# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    async def chat_stream(
        self, messages: list[dict[str, str]], *, temperature: float = 0.7, max_tokens: int = 2048
    ) -> AsyncIterator[str]: ...
    async def health_check(self) -> bool: ...
