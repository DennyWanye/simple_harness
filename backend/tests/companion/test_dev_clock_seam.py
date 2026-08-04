from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from deskpet.companion.clock import (
    DevFrozenClock,
    SystemClock,
    select_companion_clock,
)


def test_production_store_and_scheduler_share_selected_clock() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "main.py"
    ).read_text(encoding="utf-8")
    start = source.index("async def _initialize_growth_authority()")
    end = source.index(
        "\n\nasync def _initialize_companion_projection_services",
        start,
    )
    composition = source[start:end]
    selected = composition.index("companion_clock = select_companion_clock(")
    store = composition.index("store = CompanionStore(")
    assert selected < store
    assert "clock=companion_clock.now_utc" in composition


def test_production_mode_cannot_activate_dev_clock_and_warns() -> None:
    warnings: list[str] = []
    clock = select_companion_clock(
        environ={
            "DESKPET_DEV_MODE": "0",
            "DESKPET_E2E_CLOCK_UTC": "2026-07-25T08:00:00Z",
        },
        warning_sink=warnings.append,
    )
    assert isinstance(clock, SystemClock)
    assert warnings == ["companion_dev_clock_ignored_outside_dev_mode"]


def test_dev_mode_requires_both_process_environment_values() -> None:
    assert isinstance(
        select_companion_clock(environ={"DESKPET_DEV_MODE": "1"}),
        SystemClock,
    )
    clock = select_companion_clock(
        environ={
            "DESKPET_DEV_MODE": "1",
            "DESKPET_E2E_CLOCK_UTC": "2026-07-25T08:00:00.123Z",
        }
    )
    assert isinstance(clock, DevFrozenClock)
    assert clock.now_utc() == datetime(
        2026, 7, 25, 8, 0, 0, 123000, tzinfo=UTC
    )


def test_frozen_build_cannot_activate_dev_clock() -> None:
    warnings: list[str] = []
    clock = select_companion_clock(
        environ={
            "DESKPET_DEV_MODE": "1",
            "DESKPET_E2E_CLOCK_UTC": "2026-07-25T08:00:00Z",
        },
        frozen_build=True,
        warning_sink=warnings.append,
    )
    assert isinstance(clock, SystemClock)
    assert warnings == ["companion_dev_clock_ignored_in_frozen_build"]


@pytest.mark.parametrize(
    "value",
    [
        "",
        "2026-07-25T08:00:00",
        "2026-07-25T16:00:00+08:00",
        "not-a-clock",
    ],
)
def test_dev_clock_rejects_non_absolute_or_non_utc_values(value: str) -> None:
    with pytest.raises(ValueError, match="DESKPET_E2E_CLOCK_UTC"):
        select_companion_clock(
            environ={
                "DESKPET_DEV_MODE": "1",
                "DESKPET_E2E_CLOCK_UTC": value,
            }
        )


def test_dev_clock_exposes_no_runtime_tick_control() -> None:
    clock = DevFrozenClock.parse("2026-07-25T08:00:00Z")
    assert not hasattr(clock, "tick")
    assert not hasattr(clock, "advance")
    assert not hasattr(clock, "set")
