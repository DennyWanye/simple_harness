# SPDX-License-Identifier: Apache-2.0
"""Public factory for sessions that borrow an already running Host."""

from __future__ import annotations

import asyncio
from typing import Any

from .continuation import ContinuationPolicy
from .host import HostUnavailable, OrchestrationHost
from .session import Session, SessionPolicy
from .session_chat import COMMAND_SCHEMAS, RESERVED_TOOLS
from .session_ledger import SessionError


async def open_session(
    host: OrchestrationHost,
    session_id: str = "default",
    *,
    principal: Any = None,
    policy: SessionPolicy | None = None,
    parent: tuple[str, str] | None = None,
    continuation_policy: ContinuationPolicy | None = None,
) -> Session:
    """Reuse Host identity, execution store, driver, and output event stream."""
    async with host._session_open_lock:
        if host._closing or host.state not in {"available", "quarantined"}:
            raise HostUnavailable("a running Host is required")
        if principal is not None and principal != host.principal:
            raise SessionError("permission_denied", "use the Host's authenticated principal")
        if set(host.tools) & (set(RESERVED_TOOLS) | set(COMMAND_SCHEMAS)):
            raise SessionError("invalid_request", "tool conflicts with a session command")
        session = host._sessions.get(session_id)
        if session is not None:
            if session._store.get("meta", "archived"):
                raise SessionError("not_found_or_forbidden", "session is archived")
            given = (
                None if parent is None else {"parent_session_id": parent[0], "child_key": parent[1]}
            )
            if given != session.parent:
                raise SessionError(
                    "permission_denied", "child sessions are opened through their parent"
                )
            if session._closed:
                raise SessionError("session_closing", "await session.close() before reopening")
            if policy is not None and policy != session.policy:
                raise SessionError("session_config_frozen")
            if (
                continuation_policy is not None
                and continuation_policy is not session._continuation.policy
            ):
                raise SessionError("session_config_frozen", "continuation policy already selected")
        else:
            session = Session(
                host,
                session_id=session_id,
                policy=policy,
                parent=parent,
                continuation_policy=continuation_policy,
            )
        if not session._chat.available:
            try:
                await session._chat.open()
                if not session._chat.available:
                    raise SessionError("chat_unavailable", session._chat.reason or "")
                await session._delegations.recover()
                session._inbox.catch_up()
                host.start_driver()
                session._pump_task = asyncio.create_task(
                    session._pump(), name=f"session-{session_id}"
                )
                host._sessions[session_id] = session
            except BaseException:
                await session._chat.close()
                session._closed = True
                if host._sessions.get(session_id) is session:
                    host._sessions.pop(session_id)
                raise
    # 恢复子会话会再次拿宿主锁，因此根会话必须先释放它。
    try:
        await session.recover()
    except BaseException:
        await session.close()
        raise
    return session


__all__ = ("open_session",)
