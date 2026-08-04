"""Trusted clock seam for Companion scheduling.

The development clock is selected only from process environment at composition
time.  It is intentionally immutable: production code exposes no tick/advance
surface that a chat payload, tool call, or WebSocket frame could reach.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import UTC, datetime
from typing import Awaitable, Callable, Mapping, Protocol


class ClockPort(Protocol):
    def now_utc(self) -> datetime: ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> Awaitable[None]: ...


class SystemClock:
    def __deepcopy__(self, _memo):
        return self

    def now_utc(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> Awaitable[None]:
        return asyncio.sleep(seconds)


class DevFrozenClock:
    """Immutable wall clock used by trusted Tauri DEV/E2E composition."""

    def __init__(self, frozen_utc: datetime) -> None:
        if frozen_utc.tzinfo is None or frozen_utc.utcoffset() is None:
            raise ValueError("DESKPET_E2E_CLOCK_UTC must be timezone-aware")
        if frozen_utc.utcoffset().total_seconds() != 0:
            raise ValueError("DESKPET_E2E_CLOCK_UTC must use UTC offset")
        self._frozen_utc = frozen_utc.astimezone(UTC)
        self._monotonic_origin = time.monotonic()

    def __deepcopy__(self, _memo):
        return self

    @classmethod
    def parse(cls, value: str) -> "DevFrozenClock":
        raw = value.strip()
        if not raw:
            raise ValueError("DESKPET_E2E_CLOCK_UTC must not be empty")
        normalized = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(
                "DESKPET_E2E_CLOCK_UTC must be an absolute ISO-8601 UTC timestamp"
            ) from exc
        return cls(parsed)

    def now_utc(self) -> datetime:
        return self._frozen_utc

    def monotonic(self) -> float:
        # Deadlines remain real and monotonic even while wall time is frozen.
        return time.monotonic() - self._monotonic_origin

    def sleep(self, seconds: float) -> Awaitable[None]:
        return asyncio.sleep(seconds)


WarningSink = Callable[[str], None]


def select_companion_clock(
    *,
    environ: Mapping[str, str] | None = None,
    warning_sink: WarningSink | None = None,
    frozen_build: bool | None = None,
) -> ClockPort:
    """Select the only clock allowed by the trusted process composition."""

    values = os.environ if environ is None else environ
    packaged = bool(getattr(sys, "frozen", False)) if frozen_build is None else frozen_build
    dev_mode = values.get("DESKPET_DEV_MODE") == "1"
    frozen_value = values.get("DESKPET_E2E_CLOCK_UTC")
    if packaged:
        if frozen_value is not None and warning_sink is not None:
            warning_sink("companion_dev_clock_ignored_in_frozen_build")
        return SystemClock()
    if not dev_mode:
        if frozen_value is not None and warning_sink is not None:
            warning_sink("companion_dev_clock_ignored_outside_dev_mode")
        return SystemClock()
    if frozen_value is None:
        return SystemClock()
    return DevFrozenClock.parse(frozen_value)


__all__ = [
    "ClockPort",
    "DevFrozenClock",
    "SystemClock",
    "select_companion_clock",
]
