
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations
import asyncio
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Any, Protocol
import structlog
logger = structlog.get_logger(__name__)

class ProductDomainSink(Protocol):

    async def emit(self, frame: Mapping[str, Any]) -> None:
        ...

    async def set_idle(self, session_id: str) -> None:
        ...

    async def persist_assistant(self, session_id: str, text: str) -> None:
        ...

    async def store_plan(self, payload: Mapping[str, Any]) -> None:
        ...

    async def await_plan(self, payload: Mapping[str, Any]) -> bool:
        ...

class LegacyProductDomainSink:
    """Narrow compatibility port for the old product ingress."""

    def __init__(self, *, websocket: Any, services: Mapping[str, Any], session_db: Any | None, broadcast: Callable[[Any, dict[str, Any]], Awaitable[None]], plan_waiters: MutableMapping[str, Any], tool_registry: Any) -> None:
        self._ws = websocket
        self._services = services
        self._db = session_db
        self._broadcast = broadcast
        self._waiters = plan_waiters
        self._tools = tool_registry

    async def emit(self, frame: Mapping[str, Any]) -> None:
        projected = dict(frame)
        await self._ws.send_json(projected)
        await self._broadcast(self._ws, projected)

    async def set_idle(self, session_id: str) -> None:
        activity = self._services.get('session_activity')
        if activity is not None:
            try:
                await activity.set_status(session_id, 'idle')
            except Exception:
                pass

    async def persist_assistant(self, session_id: str, text: str) -> None:
        if self._db is not None:
            try:
                await self._db.append_message(session_id=session_id, role='assistant', content=text)
            except Exception as exc:
                logger.warning('clarify_persist_assistant_failed', sid=session_id, error=str(exc))

    async def store_plan(self, payload: Mapping[str, Any]) -> None:
        if not payload.get('awaiting_confirm') or self._db is None:
            return
        try:
            await self._db.upsert_session_plan(str(payload['session_id']), str(payload['rationale']), list(payload['steps']), True)
        except Exception as exc:
            logger.warning('session_plan_upsert_failed', sid=str(payload['session_id']), error=str(exc))

    async def await_plan(self, payload: Mapping[str, Any]) -> bool:
        sid, text = (str(payload['session_id']), str(payload['text']))
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._waiters[sid] = {'fut': future, 'text': text}
        read_only = bool(payload.get('read_only'))
        if read_only:
            self._set_read_only(sid, True)
        try:
            decision = await asyncio.wait_for(future, timeout=float(payload.get('timeout_seconds', 900.0)))
        except asyncio.TimeoutError:
            decision = 'cancel'
        finally:
            self._waiters.pop(sid, None)
            if read_only:
                self._set_read_only(sid, False)
            if self._db is not None:
                try:
                    await self._db.clear_session_plan_awaiting(sid)
                except Exception as exc:
                    logger.debug('session_plan_clear_failed', sid=sid, error=str(exc))
        return decision == 'go'

    def _set_read_only(self, session_id: str, enabled: bool) -> None:
        try:
            self._tools.set_plan_read_only(session_id, enabled)
        except Exception as exc:
            logger.warning('plan_read_only_toggle_failed', sid=session_id, enabled=enabled, error=str(exc))
__all__ = ['LegacyProductDomainSink', 'ProductDomainSink']
