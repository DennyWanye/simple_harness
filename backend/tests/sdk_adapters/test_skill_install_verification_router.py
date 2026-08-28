# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from types import SimpleNamespace

import pytest
from simple_harness import DriverResult, HostControlAuthorityV1
from simple_harness.execution.uow import RunState

from deskpet.capabilities.store import CapabilitySkillInstallVerificationAttempt
from deskpet.sdk_adapters.skill_install_verification import (
    SKILL_INSTALL_VERIFICATION_PURPOSE,
    ProductRootDriverRouter,
    SkillInstallVerificationAttemptResolver,
    skill_install_verification_authority_hash,
)


def _attempt() -> CapabilitySkillInstallVerificationAttempt:
    return CapabilitySkillInstallVerificationAttempt(
        attempt_id="attempt-1",
        intent_id="intent-1",
        attempt_generation=1,
        state_version=1,
        status="start_submitted",
        verifier_session_id="session-1",
        request_id="request-1",
        turn_id="turn-1",
        expected_run_id="run-1",
        actual_run_id=None,
        manager_operation_id="operation-1",
        manager_receipt_hash="a" * 64,
        committed_set_stamp="b" * 64,
        project_scope_key="scope-1",
        expected_member_set_stamp="c" * 64,
        lease_intent_id="lease-1",
        lease_intent_hash="d" * 64,
        capability_snapshot_ref="e" * 64,
        run_catalog_content_stamp="f" * 64,
        process_catalog_stamp="1" * 64,
        projection_receipt_id="projection-1",
        projection_receipt_hash="2" * 64,
        terminal_event_id=None,
        terminal_event_hash=None,
        evidence_hash=None,
        release_receipt_id=None,
        release_receipt_hash=None,
        release_owner_event_hash=None,
        superseded_by_attempt_id=None,
        migration_classification=None,
        migration_classification_hash=None,
        error=None,
        created_at=1.0,
        updated_at=1.0,
        terminal_at=None,
    )


class _Store:
    def __init__(self, attempt) -> None:  # type: ignore[no-untyped-def]
        self.attempt = attempt

    async def get_skill_install_verification_attempt(self, attempt_id: str):  # type: ignore[no-untyped-def]
        return self.attempt if self.attempt.attempt_id == attempt_id else None


class _Driver:
    def __init__(self, route: str) -> None:
        self.route = route
        self.calls = 0
        self.policy_fingerprint = "policy-react" if route == "react" else None

    async def start(self, invocation, *, context, cancel):  # type: ignore[no-untyped-def]
        del invocation, context, cancel
        self.calls += 1
        return DriverResult(RunState.COMPLETED, {"route": self.route})


def _invocation(*, authority=None, mode="host_control"):  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        start=SimpleNamespace(
            start_mode=mode,
            host_control_authority=authority,
            turn_id="turn-1",
        ),
        run=SimpleNamespace(
            run_id="run-1", execution_session_id="session-1", request_id="request-1"
        ),
    )


@pytest.mark.asyncio
async def test_router_preserves_react_and_routes_exact_durable_attempt() -> None:
    attempt = _attempt()
    authority = HostControlAuthorityV1(
        SKILL_INSTALL_VERIFICATION_PURPOSE,
        attempt.attempt_id,
        skill_install_verification_authority_hash(attempt),
        attempt.attempt_generation,
    )
    react = _Driver("react")
    verifier = _Driver("verifier")
    router = ProductRootDriverRouter(
        react_driver=react,
        attempt_resolver=SkillInstallVerificationAttemptResolver(_Store(attempt)),
        verification_driver_factory=lambda _attempt: verifier,
    )

    ordinary = await router.start(_invocation(mode="ordinary"), context=None, cancel=None)
    verified = await router.start(
        _invocation(authority=authority), context=None, cancel=None
    )

    assert ordinary.payload == {"route": "react"}
    assert verified.payload == {"route": "verifier"}
    assert react.calls == verifier.calls == 1
    assert router.policy_fingerprint == "policy-react"


@pytest.mark.asyncio
async def test_router_never_falls_back_to_react_for_mismatch() -> None:
    attempt = _attempt()
    react = _Driver("react")
    router = ProductRootDriverRouter(
        react_driver=react,
        attempt_resolver=SkillInstallVerificationAttemptResolver(_Store(attempt)),
        verification_driver_factory=lambda _attempt: _Driver("verifier"),
    )
    bad = HostControlAuthorityV1(
        SKILL_INSTALL_VERIFICATION_PURPOSE, attempt.attempt_id, "d" * 64, 1
    )
    result = await router.start(_invocation(authority=bad), context=None, cancel=None)
    assert result.state is RunState.FAILED
    assert result.payload["reason_code"] == "verification_authority_mismatch"
    assert react.calls == 0
