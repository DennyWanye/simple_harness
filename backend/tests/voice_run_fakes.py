"""Small typed RunHandle fixtures for Voice transport-only tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import AsyncIterator


@dataclass
class StaticRunHandle:
    run_id: str
    session_id: str
    text: str
    events: AsyncIterator[object] = field(init=False)

    def __post_init__(self) -> None:
        self.events = self._events()

    async def _events(self) -> AsyncIterator[object]:
        candidate = SimpleNamespace(
            kind="run.final",
            status="succeeded",
            driver_kind="react",
            correlation={},
            payload={"text": self.text},
            error=None,
            artifact_refs=(),
        )
        yield SimpleNamespace(
            event_id=f"event-{self.run_id}-1",
            run_id=self.run_id,
            root_run_id=self.run_id,
            session_id=self.session_id,
            durable_seq=1,
            live_cursor=None,
            candidate=candidate,
        )

    async def signal(self, _signal: object) -> object:
        return {"accepted": True}

    async def cancel(self, _reason: str) -> object:
        return {"accepted": True}

    async def close(self) -> None:
        return None


def static_run(text: str, session_id: str = "default") -> StaticRunHandle:
    return StaticRunHandle(f"voice-{session_id}-run", session_id, text)
