# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Sensitive-information filter for memory writes.

V5 §6 threat model: "记忆写入前经过敏感信息过滤器（正则+分类器）".

MVP strategy — regex-based redaction. When a message looks like it contains
secrets (API keys, passwords, credit cards, emails, phone numbers), replace
the secret span with a ``[REDACTED:KIND]`` marker before the message reaches
the persistent store. A future slice can layer a classifier on top for
semantic detection; the contract (string → string) stays the same.

Design notes:
- The filter is applied only on writes, never on reads — so historical
  unredacted data (imported from elsewhere) is still returned verbatim.
- Patterns err on the side of caution: it is better to over-redact a public
  string than to leak a real secret.
- Order matters: we redact long, specific patterns first (JWT, API keys)
  before shorter generic ones (hex tokens) so we don't bite off a prefix.
"""
from __future__ import annotations

from deskpet.security.sensitive_text import redact_sensitive_text as redact


class RedactingMemoryStore:
    """MemoryStore decorator that redacts content before calling ``append``.

    Duck-typed against ``memory.base.MemoryStore`` — wraps any concrete
    implementation. Reads pass through unchanged.
    """

    def __init__(self, inner) -> None:  # noqa: ANN001 — duck-typed Protocol
        self._inner = inner

    async def get_recent(self, session_id: str, limit: int = 10):
        return await self._inner.get_recent(session_id, limit)

    async def append(self, session_id: str, role: str, content: str) -> None:
        await self._inner.append(session_id, role, redact(content))

    async def clear(self, session_id: str) -> None:
        await self._inner.clear(session_id)

    # ---- S14 management passthrough (if the inner store provides them) ----
    # Inner content was already redacted on write, so reads don't need any
    # further processing. These pass through unconditionally so the UI works
    # regardless of which decorator layer the service holds.

    async def list_turns(self, session_id=None, limit=None):
        return await self._inner.list_turns(session_id, limit)

    async def delete_turn(self, turn_id: int) -> bool:
        return await self._inner.delete_turn(turn_id)

    async def list_sessions(self):
        return await self._inner.list_sessions()

    async def clear_all(self) -> int:
        return await self._inner.clear_all()
