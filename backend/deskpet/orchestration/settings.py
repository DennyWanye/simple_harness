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

#: The only Host decision modes.  Order matters: the first entry is the fallback,
#: and there is deliberately no member that maps to the SDK's Primary mode.
HOST_DECISION_MODES = ("existing", "shadow")

#: A shadow observation is a background nicety; the Host refuses to let a
#: misconfigured number turn it into an unbounded wait on the allocation path.
SHADOW_TIMEOUT_CEILING_SECONDS = 60.0


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
    # Secret-free, pinned local-model profile selected by this deployment.
    local_model_profile: str = ""
    # New official DeepSeek source Missions; existing Missions keep their profile.
    context_input_tokens: int = 262_144
    # P3.2 (plan D9 / P32-14): the one directory the user authorised for published files.
    # Empty means no directory is authorised, and then nothing can be published at all —
    # the connector is not even enabled, so a Mission may not carry a publish criterion.
    publish_dir: str = ""
    # NanoJev decision rollout (NanoJevAdd.md §57 PR-7).  The Host owns this key and
    # passes an explicit typed policy to the SDK; the SDK reads no configuration.
    # Only "existing" and "shadow" are valid, and anything unrecognised — including
    # the absence of the key — is "existing".  "nanojev" (Primary) is not a Host
    # value: it is a later gate, and a config typo must not be able to reach it.
    decision_mode: str = "existing"
    # A shadow observation that exceeds this is recorded as a timeout and dropped;
    # the production plan is unaffected.  None means no Host-side bound.
    decision_shadow_timeout_seconds: float | None = None
    # Assurance 1.1 (plan §13/§16): which new planning-decision Missions take the
    # assured lane. "on" assures every one; "off" leaves the SDK's single default
    # selection point. Flipped in the same delivery as the verified default-ON.
    assurance_profile: str = "off"


def _assurance_profile(value: Any) -> str:
    if not isinstance(value, str):
        return "off"
    normalised = value.strip().lower()
    return normalised if normalised in ("on", "off") else "off"


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(low, min(high, value))


def _decision_mode(value: Any) -> str:
    """The Host's decision mode, or ``existing`` for anything unrecognised.

    This is the one place ``decision.mode`` is read, and it is fail-closed on
    purpose: an unknown value, a missing key and a non-string all land on the
    existing production path.  Nothing here consults the environment, so the mode
    cannot be changed by an ad-hoc variable.

    ``nanojev`` (Primary) is deliberately not in :data:`HOST_DECISION_MODES`: the
    Host has no value that produces the SDK's ``DecisionMode.NANOJEV``, so Primary
    is unreachable by configuration in this slice.
    """

    if not isinstance(value, str):
        return HOST_DECISION_MODES[0]
    normalised = value.strip().lower()
    return normalised if normalised in HOST_DECISION_MODES else HOST_DECISION_MODES[0]


def _shadow_timeout(value: Any) -> float | None:
    """A bounded, positive shadow timeout, or ``None`` for "no Host-side bound"."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    seconds = float(value)
    if not seconds > 0:
        return None
    return min(seconds, SHADOW_TIMEOUT_CEILING_SECONDS)


def load_settings(section: Mapping[str, Any] | None) -> OrchestrationSettings:
    """Read ``[orchestration]``; unknown keys are ignored, bad values fall back."""

    raw = dict(section or {})
    enabled = raw.get("enabled", True)
    publish_dir = raw.get("publish_dir")
    return OrchestrationSettings(
        enabled=enabled if isinstance(enabled, bool) else True,
        max_concurrency=_bounded_int(raw.get("max_concurrency"), 1, 1, 4),
        max_concurrent_model_calls=_bounded_int(raw.get("max_concurrent_model_calls"), 1, 1, 4),
        local_model_profile=(str(raw.get("local_model_profile", "")).strip()
                             if isinstance(raw.get("local_model_profile", ""), str) else ""),
        context_input_tokens=(524_288 if type(raw.get("context_input_tokens")) is int
                              and raw["context_input_tokens"] == 524_288 else 262_144),
        # a path only; whether it exists and can carry a hard link is decided at start-up,
        # and a directory that cannot is never authorised (P3.2 review round 2 P2-5)
        publish_dir=str(publish_dir).strip() if isinstance(publish_dir, str) else "",
        decision_mode=_decision_mode(raw.get("decision_mode")),
        decision_shadow_timeout_seconds=_shadow_timeout(raw.get("decision_shadow_timeout_seconds")),
        assurance_profile=_assurance_profile(raw.get("assurance_profile")),
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
    "HOST_DECISION_MODES",
    "KNOWN_SCENARIOS",
    "SHADOW_TIMEOUT_CEILING_SECONDS",
    "TEST_SCENARIO_ENV",
    "OrchestrationSettings",
    "load_settings",
    "resolve_test_scenario",
)
