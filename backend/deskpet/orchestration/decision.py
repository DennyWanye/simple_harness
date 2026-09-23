# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""The Host-side NanoJev decision seam (plan §57 PR-7, Host ruling 2026-09-20).

The SDK owns the decision contract, the non-blocking shadow service and the event
journal; it deliberately owns no configuration and no caller.  This module is the
Host half of that seam, and it is written to the parent's ruling:

* **The Host owns ``decision.mode``.**  It is read from ``config.toml``
  ``[orchestration]`` by :mod:`deskpet.orchestration.settings` and handed to the
  SDK as an explicit typed :class:`~agent_orchestrator.decision.DecisionPolicy`.
  Nothing here reads an environment variable, and the default is ``EXISTING``:
  an absent key, an unknown value and a malformed value all resolve to the
  existing path, so a typo can never quietly turn an observation on.
* **Shadow-only, observation-only.**  ``READY_TASK_PRIORITY`` is the one decision
  type wired here, and it is observed — never enforced.  The allocator's grant set
  is authoritative, and the plan the SDK seam returns is the untouched
  ``allocate()`` result.  ``RETRY_OR_ESCALATE`` is deliberately absent: mapping
  ``RetryAction``'s six values onto the two-value contract is a ruling this slice
  does not have, and inventing one would be worse than leaving it out.
* **``NANOJEV`` is not reachable from here.**  The parser has no value that
  produces it.  Primary is a later gate.
* **A failure is never a production failure.**  Every call into the SDK is
  guarded; a missing SDK, a broken provider or a journal problem degrades to "the
  plan is returned, the observation is lost", and is reported in the status
  projection rather than raised into the drive loop.

The Host's own behaviour is unchanged when the mode is ``EXISTING``: this module
is not even asked to observe.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: The Host's own vocabulary for the rollout mode.  It is deliberately smaller
#: than the SDK's ``DecisionMode``: there is no Host value that yields ``nanojev``.
EXISTING = "existing"
SHADOW = "shadow"
HOST_MODES = (EXISTING, SHADOW)

#: What the Host calls an observation it could not complete.
OBSERVATION_FAILED = "failed"


@dataclass
class DecisionSeamStatus:
    """A secret-free, read-only projection of the seam's state for ``status()``."""

    mode: str = EXISTING
    observation_enabled: bool = False
    observations: int = 0
    failures: int = 0
    last_error: str | None = None
    #: The SDK seam refused to load (absent, or too old to carry the contract).
    available: bool = True
    detail: str | None = None
    retry_wired: bool = False  # always false in this slice; pinned by the tests

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "observation_enabled": self.observation_enabled,
            "observations": self.observations,
            "failures": self.failures,
            "last_error": self.last_error,
            "available": self.available,
            "detail": self.detail,
            "retry_wired": self.retry_wired,
        }


@dataclass
class DecisionSeam:
    """One process's decision seam: a typed policy plus a non-fatal observation path.

    Built once at orchestration start from the effective settings, then reused for
    every cycle.  It holds no connection and owns no store: the SDK seam is handed
    the journal the caller supplies, and a seam with no journal observes nothing.
    """

    mode: str = EXISTING
    journal: Any = None
    existing_provider: Any = None
    shadow_provider: Any = None
    shadow_timeout_seconds: float | None = None
    _import_error: str | None = None
    _status: DecisionSeamStatus = field(default_factory=DecisionSeamStatus)

    def __post_init__(self) -> None:
        if self.mode not in HOST_MODES:
            # Fail closed to the existing path: an unknown mode is not a licence
            # to observe, and it is not a startup failure either.
            self.mode = EXISTING
        if self.shadow_timeout_seconds is not None and self.shadow_timeout_seconds <= 0:
            self.shadow_timeout_seconds = None
        self._status = DecisionSeamStatus(
            mode=self.mode,
            observation_enabled=self.mode == SHADOW and self.journal is not None,
        )

    # ------------------------------------------------------------------ contract

    @property
    def observation_enabled(self) -> bool:
        """Whether this seam will observe.  A mode alone is not enough."""

        return self.mode == SHADOW and self.journal is not None and self._sdk() is not None

    @property
    def status(self) -> DecisionSeamStatus:
        """The current projection; refreshed by :meth:`observe_ready_priority`."""

        self._status.observation_enabled = self.observation_enabled
        self._status.available = self._sdk() is not None
        self._status.detail = self._import_error
        return self._status

    def policy(self) -> Any:
        """The explicit typed policy the SDK is handed.  Never inferred from env."""

        from agent_orchestrator.decision import DecisionMode, DecisionPolicy

        mode = DecisionMode.SHADOW if self.mode == SHADOW else DecisionMode.EXISTING
        return DecisionPolicy(mode=mode)

    # --------------------------------------------------------------------- seam

    def ready_priority_plan(
        self,
        tasks: Sequence[Any],
        attempts: Sequence[Any],
        **allocate_kwargs: Any,
    ) -> tuple[Any, bool]:
        """Allocate once, observing the frontier order if this seam is enabled.

        Returns ``(plan, observed)`` where ``observed`` is true only when a
        shadow provider was actually asked.  The plan is the SDK allocator's own
        result: with no observation requested it is literally the call the
        production path always made.  Any SDK problem leaves the plan intact and
        reports ``observed=False``; this method does not raise for an observation
        problem.
        """

        from agent_orchestrator.scheduling.allocator import allocate

        if not self.observation_enabled:
            return allocate(tasks, attempts, **allocate_kwargs), False
        return self._observe(tasks, attempts, allocate_kwargs)

    async def observe_ready_priority(
        self,
        mission: Any,
        tasks: Sequence[Any],
        attempts: Sequence[Any],
        plan: Any,
    ) -> bool:
        """Observe the already-computed plan from the live Mission driver.

        ``event_handler._decide`` owns allocation authority.  This async callback is
        deliberately called after that allocation and never recomputes or replaces the
        plan.  It exists so the real Host driver can reach the SDK seam without calling
        ``asyncio.run`` from inside its event loop.
        """

        del plan  # the observer records the request; the caller retains the plan
        if not self.observation_enabled:
            return False
        from agent_orchestrator.decision import (
            compute_ready_task_decision_id,
            frontier_priority_candidates,
            observe_frontier_priority,
            ready_task_priority_decision,
        )

        ordered = frontier_priority_candidates(tasks)
        if len(ordered) < 2:
            return False
        mission_id = getattr(mission, "id", None)
        if not isinstance(mission_id, str) or not mission_id.strip():
            self._status.failures += 1
            self._status.last_error = "observation declined: no mission_id"
            return False
        decision_id = compute_ready_task_decision_id(
            mission_id=mission_id,
            task_id=None,
            ordinal=0,
            candidate_ids=tuple(task.id for task in ordered),
            context={"frontier_size": len(ordered)},
        )
        try:
            _result, observations = await observe_frontier_priority(
                tasks,
                attempts,
                decision_id=decision_id,
                context={"frontier_size": len(ordered)},
                policy=self.policy(),
                shadow_provider=self.shadow_provider,
                shadow_timeout_seconds=self.shadow_timeout_seconds,
                journal=self.journal,
            )
        except Exception as error:  # noqa: BLE001 - observation cannot stop production
            self._status.failures += 1
            self._status.last_error = f"{type(error).__name__}: {error}"[:200]
            logger.warning("decision_shadow_failed error=%s", self._status.last_error)
            return False
        self._status.observations += len(observations)
        for observation in observations:
            if getattr(observation, "error", None):
                self._status.failures += 1
                self._status.last_error = str(observation.error)[:200]
        return bool(observations)

    def _observe(
        self,
        tasks: Sequence[Any],
        attempts: Sequence[Any],
        allocate_kwargs: dict[str, Any],
    ) -> tuple[Any, bool]:
        """Run the SDK seam, converting every failure into a recorded non-failure.

        The boolean is "a shadow provider was asked", not "the answer was good":
        a provider that fails is still an observation, and the status projection
        is where the failure shows up.
        """

        from agent_orchestrator.decision import (
            compute_ready_task_decision_id,
            frontier_priority_candidates,
            ready_task_priority_decision,
        )

        ordered = frontier_priority_candidates(tasks)
        if len(ordered) < 2:
            # Plan §57: a 0/1 candidate frontier is deterministic.  The SDK seam
            # makes the same decision; asking here keeps the call count at zero
            # for the ordinary single-ready-Task cycle.
            return self._plan_only(tasks, attempts, allocate_kwargs), False
        mission_id = getattr(tasks[0], "mission_id", None)
        if not isinstance(mission_id, str) or not mission_id.strip():
            # A deterministic id needs the Mission; without it the seam declines
            # rather than inventing an identity for the thread.
            self._status.failures += 1
            self._status.last_error = "observation declined: no mission_id on the Tasks"
            logger.warning("decision_shadow_declined reason=no_mission_id")
            return self._plan_only(tasks, attempts, allocate_kwargs), False
        decision_id = compute_ready_task_decision_id(
            mission_id=mission_id,
            task_id=None,
            ordinal=0,
            candidate_ids=tuple(task.id for task in ordered),
            context={"frontier_size": len(ordered)},
        )
        try:
            # The live Mission driver uses ``observe_ready_priority`` below.  If a
            # legacy synchronous caller nevertheless invokes this helper from an
            # event loop, fail open to the unchanged allocator plan instead of
            # calling ``asyncio.run`` and raising ``RuntimeError``.
            try:
                running = asyncio.get_event_loop().is_running()
            except RuntimeError:
                running = False
            if running:
                self._status.failures += 1
                self._status.last_error = (
                    "sync decision seam called from an event loop; use async observer"
                )
                return self._plan_only(tasks, attempts, allocate_kwargs), False
            plan, observations = asyncio.run(
                ready_task_priority_decision(
                    tasks,
                    attempts,
                    decision_id=decision_id,
                    context={"frontier_size": len(ordered)},
                    policy=self.policy(),
                    shadow_provider=self.shadow_provider,
                    shadow_timeout_seconds=self.shadow_timeout_seconds,
                    journal=self.journal,
                    **allocate_kwargs,
                )
            )
        except Exception as error:  # noqa: BLE001 - the plan must survive any observation problem
            self._status.failures += 1
            self._status.last_error = f"{type(error).__name__}: {error}"[:200]
            logger.warning("decision_shadow_failed error=%s", self._status.last_error)
            return self._plan_only(tasks, attempts, allocate_kwargs), False
        self._status.observations += len(observations)
        asked = bool(observations)
        for observation in observations:
            if getattr(observation, "error", None):
                self._status.failures += 1
                self._status.last_error = str(observation.error)[:200]
        return plan, asked

    def _plan_only(
        self,
        tasks: Sequence[Any],
        attempts: Sequence[Any],
        allocate_kwargs: dict[str, Any],
    ) -> Any:
        from agent_orchestrator.scheduling.allocator import allocate

        return allocate(tasks, attempts, **allocate_kwargs)

    def _sdk(self) -> Any | None:
        """Import the SDK seam lazily, remembering a failure instead of raising it.

        The vendored wheel this Host ships may predate the decision package.  That
        is a deployment fact, not a crash: the seam becomes unavailable, the status
        says so, and the allocator keeps allocating.
        """

        if self._import_error is not None:
            return None
        try:
            from agent_orchestrator.decision import observe_frontier_priority
        except Exception as error:  # noqa: BLE001 - an absent/old SDK is an ordinary state
            self._import_error = f"{type(error).__name__}: {error}"[:200]
            logger.info("decision_contract_unavailable reason=%s", self._import_error)
            return None
        return observe_frontier_priority


def build_decision_seam(
    settings: Any,
    *,
    journal: Any = None,
    existing_provider: Any = None,
    shadow_provider: Any = None,
) -> DecisionSeam:
    """Build the seam from the Host's effective settings.

    ``settings.decision_mode`` is the only input that selects a mode, and the mode
    it names is validated against the Host's own vocabulary — a value the parser
    did not recognise is already ``existing`` by the time it arrives here.
    """

    mode = getattr(settings, "decision_mode", EXISTING)
    if not isinstance(mode, str) or mode not in HOST_MODES:
        mode = EXISTING
    return DecisionSeam(
        mode=mode,
        journal=journal,
        existing_provider=existing_provider,
        shadow_provider=shadow_provider,
        shadow_timeout_seconds=getattr(settings, "decision_shadow_timeout_seconds", None),
    )


def seam_available() -> bool:
    """Whether the installed SDK carries the Host-facing decision seam."""

    try:
        from agent_orchestrator.decision import (
            observe_frontier_priority,  # noqa: F401
        )
    except Exception:  # noqa: BLE001 - an absent/old SDK is an ordinary state
        return False
    return True


__all__ = (
    "EXISTING",
    "HOST_MODES",
    "OBSERVATION_FAILED",
    "SHADOW",
    "DecisionSeam",
    "DecisionSeamStatus",
    "build_decision_seam",
    "seam_available",
)
