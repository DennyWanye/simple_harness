# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""``config.toml [orchestration]`` and the test-only scenario gate (plan §3.3, §3.8).

There is still no *switch* for running model-written code on this machine, and there will
not be one: P3.2 replaced the question "may it run" with "has isolation proven itself
here".  At every start the SDK's capability probe runs (cached by its environment digest);
the deployment calls itself ``sandboxed`` only when all eight checks pass, and ``off``
otherwise.  This Host never uses ``process_only`` — an unisolated child process is for
trusted code, which model-written code is not.

``max_concurrency`` / ``max_concurrent_model_calls`` enter the ACTIVE policy only when
the library is first seeded; a later change of the config records ``PolicyConfigDrift``
and the ACTIVE version still governs until a promotion (plan review P1-5).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TEST_SCENARIO_ENV = "DESKPET_ORCHESTRATION_TEST_SCENARIO"
KNOWN_SCENARIOS = ("approval-action", "document-ui")
EVIDENCE_MARKER = ".local-test-evidence"


@dataclass(frozen=True)
class OrchestrationSettings:
    enabled: bool = True  # CLAUDE.md: capabilities that passed testing ship on
    max_concurrency: int = 1  # the chat shares the provider quota
    max_concurrent_model_calls: int = 1
    tick_active_seconds: float = 2.0  # work or a turn in flight
    tick_waiting_seconds: float = 20.0  # only a person is awaited (plan review P2)
    tick_idle_seconds: float = 30.0
    lease_seconds: float = 60.0  # tests shorten it; the product does not expose it
    backoff_max_seconds: float = 60.0
    rebuild_after_failures: int = 3
    degraded_after_failures: int = 5
    # This deployment offers no Mission without bounds (native run 2026-09-12, adjudication
    # C): a blank budget item takes these.  400000 tokens is the value both HA-11 real runs
    # passed with; 12 attempts because the Mission count covers every Worker Attempt of
    # every Task (3 would fail a three-Task Mission on its first retry).
    default_mission_max_tokens: int = 400_000
    default_mission_max_attempts: int = 12
    # P3.2 (plan D9 / P32-14): the one directory the user authorised for published files.
    # Empty means no directory is authorised, and then nothing can be published at all —
    # the connector is not even enabled, so a Mission may not carry a publish criterion.
    publish_dir: str = ""


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(low, min(high, value))


def load_settings(section: Mapping[str, Any] | None) -> OrchestrationSettings:
    """Read ``[orchestration]``; unknown keys are ignored, bad values fall back."""

    raw = dict(section or {})
    enabled = raw.get("enabled", True)
    publish_dir = raw.get("publish_dir")
    return OrchestrationSettings(
        enabled=enabled if isinstance(enabled, bool) else True,
        max_concurrency=_bounded_int(raw.get("max_concurrency"), 1, 1, 4),
        max_concurrent_model_calls=_bounded_int(raw.get("max_concurrent_model_calls"), 1, 1, 4),
        # a path only; whether it exists and can carry a hard link is decided at start-up,
        # and a directory that cannot is never authorised (P3.2 review round 2 P2-5)
        publish_dir=str(publish_dir).strip() if isinstance(publish_dir, str) else "",
    )


def resolve_test_scenario(env: Mapping[str, str], user_data: str | Path) -> str | None:
    """The approval-action test scenario needs *both* gates: the environment variable
    and a user-data directory inside ``.local-test-evidence/`` (plan review P1-2).
    ``DESKPET_DEV_MODE`` plays no part — it changes the product path by itself."""

    scenario = env.get(TEST_SCENARIO_ENV)
    if scenario not in KNOWN_SCENARIOS:
        return None
    if EVIDENCE_MARKER not in Path(user_data).resolve(strict=False).parts:
        return None
    return scenario


__all__ = (
    "EVIDENCE_MARKER",
    "KNOWN_SCENARIOS",
    "TEST_SCENARIO_ENV",
    "OrchestrationSettings",
    "load_settings",
    "resolve_test_scenario",
)
