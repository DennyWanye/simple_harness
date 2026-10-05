# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Pure persisted clock transition. Caller writes with the original Commit receipt."""

from __future__ import annotations

from dataclasses import dataclass

from .codec import integer, one_of


#: 正常前进时，最高水位每多走这么久才落一次库（联测裁决 2026-10-05）。进程内的水位是精确的，
#: 进程内的回拨照样发现；落库只防重启后丢水位，重启后最多少记这么多。小于最短的默认时限
#: （准备超时 20 秒），漏掉一次更小的跨重启回拨只让租约晚回收不到这么久。
CLOCK_PERSIST_STEP_MS = 10_000


@dataclass(frozen=True, slots=True)
class ClockState:
    generation: int
    wall_high_ms: int
    state: str

    def __post_init__(self) -> None:
        integer(self.generation)
        integer(self.wall_high_ms)
        one_of(self.state, {"STABLE", "ROLLBACK"})

    def observe(self, now_ms: int) -> ClockState:
        integer(now_ms)
        if now_ms < self.wall_high_ms:
            return ClockState(
                self.generation + (self.state == "STABLE"), self.wall_high_ms, "ROLLBACK"
            )
        return ClockState(self.generation, max(self.wall_high_ms, now_ms), "STABLE")
