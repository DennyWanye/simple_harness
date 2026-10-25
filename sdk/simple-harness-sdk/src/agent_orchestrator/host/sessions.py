# SPDX-License-Identifier: Apache-2.0
"""Public factory for sessions that borrow an already running Host."""

from __future__ import annotations

import asyncio
from typing import Any

from .host import HostUnavailable, OrchestrationHost
from .session import Session, SessionPolicy
from .session_chat import RESERVED_TOOLS
from .session_ledger import SessionError


async def open_session(
    host: OrchestrationHost,
    session_id: str = "default",
    *,
    principal: Any = None,
    policy: SessionPolicy | None = None,
) -> Session:
    """Reuse Host identity, execution store, driver, and output event stream."""
    async with host._session_open_lock:
        if host._closing or host.state not in {"available", "quarantined"}:
            raise HostUnavailable("a running Host is required")
        if principal is not None and principal != host.principal:
            raise SessionError("permission_denied", "use the Host's authenticated principal")
        if set(host.tools) & set(RESERVED_TOOLS):
            raise SessionError("invalid_request", "tool conflicts with a session command")
        existing = host._sessions.get(session_id)
        if existing is not None:
            if existing._closed:
                raise SessionError("session_closing", "await session.close() before reopening")
            if policy is not None and policy != existing.policy:
                raise SessionError("session_config_frozen")
            return existing
        session = Session(host, session_id=session_id, policy=policy)
        try:
            await session._chat.open()
            if not session._chat.available:
                raise SessionError("chat_unavailable", session._chat.reason or "")
            session._delegations.recover()
            session._inbox.catch_up()
            host.start_driver()
            session._pump_task = asyncio.create_task(session._pump(), name=f"session-{session_id}")
            host._sessions[session_id] = session
            return session
        except BaseException:
            await session._chat.close()
            session._closed = True
            raise


__all__ = ("open_session",)
