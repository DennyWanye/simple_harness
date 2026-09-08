# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Explicit, ordered execution-time limits for the foreground ReAct driver.

Incident (corpus run-02, C10-13): the Host built ``TerminationLimits`` without
``max_wall_seconds``, so the SDK default 900.0s applied and *equalled* the corpus
batch's external SIGTERM deadline (``scripts/run_corpus_batch.py --seconds``,
also 900). The external supervisor won the race at 900.167s, the Run died before
the driver could settle, and the case scored ``NO_PACKET`` — a timeout with no
failed terminal receipt at all.

The repair is one explicit ordered chain. Each layer must be able to fire
strictly before the layer outside it, and only the driver layer writes a
terminal receipt:

    provider transport timeout   240s  one HTTP call (anti-hang net only)
  < driver max_wall_seconds      600s  one SDK Run -> failed terminal receipt
  < foreground active budget     900s  Run authority active time (pausable)
  < external supervisor deadline       SIGTERM; must strictly exceed the worst
                                       case driver settlement.

Worst-case settlement matters because the wall clock is sampled only at
reservation boundaries (``TerminationState.before_provider`` /
``before_tool_batch`` in the SDK's ``runtime/termination.py``). A Run that is
already inside a provider call when the budget expires cannot notice until that
call returns, so the external floor is

    max_wall_seconds + provider transport timeout + settlement margin.

Everything here is stdlib-only and import-light on purpose: ``main.py`` reads it
at startup, and ``scripts/run_corpus_batch.py`` loads it straight from this file
(no ``deskpet.execution`` package import) to derive its own deadline from the
same numbers instead of hardcoding a second copy.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

# Mirrors ``ProductProviderAdapter.__init__(timeout=...)`` in
# deskpet/sdk_adapters/provider.py. tests/execution/test_termination_budget.py
# pins the two together so this copy can never drift.
PROVIDER_TRANSPORT_TIMEOUT_SECONDS = 240.0

# One SDK Run's wall clock. 600s leaves room for the full 25-turn ReAct budget
# at the observed median provider turn (12.7-59.3s in the C10 run-02 traces)
# while firing strictly before the pausable foreground active budget, so a
# runaway Run is always terminated by the layer that writes a receipt.
DEFAULT_MAX_WALL_SECONDS = 600.0

# Head-room for the driver to write the failed terminal receipt and for the
# worker to flush its evidence once the wall budget has been observed.
TERMINAL_SETTLEMENT_MARGIN_SECONDS = 60.0

# ``_chat_turn_timeout_s()`` default in main.py (chat_turn_timeout_minutes=15).
# Recorded here only so the ordering invariant can be asserted in one place.
FOREGROUND_ACTIVE_BUDGET_DEFAULT_SECONDS = 900.0

# A corpus case executes several Runs in one supervised process (setup turns,
# the scored turn, follow-ups). The floor below bounds the *last* Run only, so
# the batch adds a whole-case allowance on top of it.
CASE_MULTI_RUN_ALLOWANCE_SECONDS = 300.0

# A wall budget below one provider timeout plus settlement could expire before
# a single provider turn can complete, which would make every Run fail on its
# first call; above an hour it stops being a safety net.
MIN_MAX_WALL_SECONDS = PROVIDER_TRANSPORT_TIMEOUT_SECONDS + TERMINAL_SETTLEMENT_MARGIN_SECONDS
MAX_MAX_WALL_SECONDS = 3600.0

CONFIG_SECTION = "agent"
CONFIG_KEY = "max_wall_seconds"

__all__ = (
    "CASE_MULTI_RUN_ALLOWANCE_SECONDS",
    "CONFIG_KEY",
    "CONFIG_SECTION",
    "DEFAULT_MAX_WALL_SECONDS",
    "FOREGROUND_ACTIVE_BUDGET_DEFAULT_SECONDS",
    "MAX_MAX_WALL_SECONDS",
    "MIN_MAX_WALL_SECONDS",
    "PROVIDER_TRANSPORT_TIMEOUT_SECONDS",
    "TERMINAL_SETTLEMENT_MARGIN_SECONDS",
    "corpus_batch_deadline_seconds",
    "external_deadline_floor_seconds",
    "resolve_max_wall_seconds",
)


def resolve_max_wall_seconds(raw_config: Mapping[str, Any] | None = None) -> float:
    """Return ``[agent].max_wall_seconds`` clamped into the supported range.

    A malformed or absent value falls back to the default rather than failing
    startup: this is an operational safety net, and a typo in config.toml must
    never leave the driver with *no* wall limit at all.
    """

    value: Any = None
    if isinstance(raw_config, Mapping):
        section = raw_config.get(CONFIG_SECTION)
        if isinstance(section, Mapping):
            value = section.get(CONFIG_KEY)
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_MAX_WALL_SECONDS
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        return DEFAULT_MAX_WALL_SECONDS
    return max(MIN_MAX_WALL_SECONDS, min(MAX_MAX_WALL_SECONDS, seconds))


def external_deadline_floor_seconds(max_wall_seconds: float | None = None) -> float:
    """Smallest external deadline that still lets one Run settle by itself.

    An external supervisor (``run_resource_bounded.py``, a CI timeout, a service
    manager) must use a deadline strictly greater than this, or it can SIGTERM a
    Run that was about to write its own failed terminal receipt.
    """

    wall = DEFAULT_MAX_WALL_SECONDS if max_wall_seconds is None else float(max_wall_seconds)
    return wall + PROVIDER_TRANSPORT_TIMEOUT_SECONDS + TERMINAL_SETTLEMENT_MARGIN_SECONDS


def corpus_batch_deadline_seconds(max_wall_seconds: float | None = None) -> int:
    """Default ``--seconds`` for one corpus case: floor + multi-Run allowance."""

    return int(
        math.ceil(
            external_deadline_floor_seconds(max_wall_seconds)
            + CASE_MULTI_RUN_ALLOWANCE_SECONDS
        )
    )
