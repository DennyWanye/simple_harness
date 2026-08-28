# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Durable product routing for internal Skill-install verification Runs."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from simple_harness import DriverResult, HostControlAuthorityV1, RuntimeDriver
from simple_harness.execution.uow import RunState

from deskpet.capabilities.contracts import fingerprint_json
from deskpet.capabilities.store import CapabilitySkillInstallVerificationAttempt

SKILL_INSTALL_VERIFICATION_PURPOSE = "skill.install.verify"
_UNRESOLVED_ATTEMPT_STATES = frozenset({"prepared", "launching", "running", "unknown"})


def skill_install_verification_authority_hash(
    attempt: CapabilitySkillInstallVerificationAttempt,
) -> str:
    """Hash the immutable attempt fields consumed by SDK routing."""

    return fingerprint_json(
        {
            "schema": "skill-install-verification-route-v1",
            "attempt_id": attempt.attempt_id,
            "intent_id": attempt.intent_id,
            "attempt_generation": attempt.attempt_generation,
            "verifier_session_id": attempt.verifier_session_id,
            "request_id": attempt.request_id,
            "turn_id": attempt.turn_id,
            "expected_run_id": attempt.expected_run_id,
            "manager_operation_id": attempt.manager_operation_id,
            "manager_receipt_hash": attempt.manager_receipt_hash,
            "committed_set_stamp": attempt.committed_set_stamp,
            "project_scope_key": attempt.project_scope_key,
            "expected_member_set_stamp": attempt.expected_member_set_stamp,
        }
    )


class SkillInstallVerificationAttemptStore(Protocol):
    async def get_skill_install_verification_attempt(
        self, attempt_id: str
    ) -> CapabilitySkillInstallVerificationAttempt | None: ...


class SkillInstallVerificationAttemptResolver:
    """Resolve only current, unresolved ProductState attempts."""

    def __init__(self, store: SkillInstallVerificationAttemptStore) -> None:
        self._store = store

    async def resolve(
        self, authority: HostControlAuthorityV1
    ) -> CapabilitySkillInstallVerificationAttempt | None:
        if authority.purpose != SKILL_INSTALL_VERIFICATION_PURPOSE:
            return None
        attempt = await self._store.get_skill_install_verification_attempt(
            authority.authority_ref
        )
        if (
            attempt is None
            or attempt.status not in _UNRESOLVED_ATTEMPT_STATES
            or attempt.attempt_generation != authority.generation
            or skill_install_verification_authority_hash(attempt)
            != authority.authority_hash
        ):
            return None
        return attempt


VerificationDriverFactory = Callable[
    [CapabilitySkillInstallVerificationAttempt], RuntimeDriver | Awaitable[RuntimeDriver]
]


class ProductRootDriverRouter:
    """Route trusted Host-control roots without changing the SDK root profile."""

    def __init__(
        self,
        *,
        react_driver: RuntimeDriver,
        attempt_resolver: SkillInstallVerificationAttemptResolver,
        verification_driver_factory: VerificationDriverFactory,
    ) -> None:
        self._react_driver = react_driver
        self._attempt_resolver = attempt_resolver
        self._verification_driver_factory = verification_driver_factory

    @property
    def policy_fingerprint(self) -> str | None:
        return getattr(self._react_driver, "policy_fingerprint", None)

    async def start(self, invocation: Any, *, context: Any, cancel: Any) -> DriverResult:
        snapshot = invocation.start
        if snapshot.start_mode == "ordinary":
            return await self._react_driver.start(invocation, context=context, cancel=cancel)
        authority = snapshot.host_control_authority
        if not isinstance(authority, HostControlAuthorityV1):
            return _route_failure("verification_authority_missing")
        attempt = await self._attempt_resolver.resolve(authority)
        run = invocation.run
        if (
            attempt is None
            or run.run_id != attempt.expected_run_id
            or run.execution_session_id != attempt.verifier_session_id
            or run.request_id != attempt.request_id
            or snapshot.turn_id != attempt.turn_id
        ):
            return _route_failure("verification_authority_mismatch")
        driver = self._verification_driver_factory(attempt)
        if inspect.isawaitable(driver):
            driver = await driver  # type: ignore[assignment,misc]
        return await driver.start(invocation, context=context, cancel=cancel)


class BindableSkillInstallVerificationDriverFactory:
    """Composition holder whose unavailable state remains a bounded failed Run."""

    def __init__(self) -> None:
        self._factory: VerificationDriverFactory | None = None

    def bind(self, factory: VerificationDriverFactory) -> None:
        if self._factory is not None and self._factory is not factory:
            raise RuntimeError("Skill verification driver factory is already bound")
        self._factory = factory

    def __call__(
        self, attempt: CapabilitySkillInstallVerificationAttempt
    ) -> RuntimeDriver | Awaitable[RuntimeDriver]:
        if self._factory is None:
            return _RejectedVerificationDriver("verification_driver_unavailable")
        return self._factory(attempt)


class _RejectedVerificationDriver:
    def __init__(self, code: str) -> None:
        self._code = code

    async def start(self, invocation: Any, *, context: Any, cancel: Any) -> DriverResult:
        del invocation, context, cancel
        return _route_failure(self._code)


def _route_failure(code: str) -> DriverResult:
    payload: Mapping[str, object] = {
        "schema": "skill-install-verification-final-v1",
        "status": "failed",
        "reason_code": code,
        "provider_invocation_count": 0,
        "effect_count": 0,
        "checkpoint_count": 0,
        "continuation_count": 0,
    }
    return DriverResult(RunState.FAILED, payload)  # type: ignore[arg-type]


__all__ = (
    "BindableSkillInstallVerificationDriverFactory",
    "ProductRootDriverRouter",
    "SKILL_INSTALL_VERIFICATION_PURPOSE",
    "SkillInstallVerificationAttemptResolver",
    "skill_install_verification_authority_hash",
)
