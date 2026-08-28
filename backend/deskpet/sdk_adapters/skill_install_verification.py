# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Durable product routing for internal Skill-install verification Runs."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from simple_harness import DriverResult, HostControlAuthorityV1, RuntimeDriver
from simple_harness.execution.uow import RunState

from deskpet.capabilities.contracts import CapabilityScope, fingerprint_json
from deskpet.capabilities.store import (
    CapabilitySkillInstallMember,
    CapabilitySkillInstallVerificationAttempt,
)
from deskpet.tools.capabilities import PreparedToolSet

from .capability_catalog import ProductCapabilityCatalogSourceAdapter
from .composition import ProductSdkRuntimeStack
from .ingress import SdkRuntimeIngress

SKILL_INSTALL_VERIFICATION_PURPOSE = "skill.install.verify"
_UNRESOLVED_ATTEMPT_STATES = frozenset(
    {
        "start_submitted",
        "run_durable",
        "catalog_ready",
        "page_in_proven",
        "unknown",
    }
)


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
            "lease_intent_id": attempt.lease_intent_id,
            "lease_intent_hash": attempt.lease_intent_hash,
            "capability_snapshot_ref": attempt.capability_snapshot_ref,
            "run_catalog_content_stamp": attempt.run_catalog_content_stamp,
            "process_catalog_stamp": attempt.process_catalog_stamp,
            "projection_receipt_id": attempt.projection_receipt_id,
            "projection_receipt_hash": attempt.projection_receipt_hash,
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


class SkillInstallVerificationRunService:
    """Execute the install verification saga on its single execution owner."""

    def __init__(
        self,
        *,
        store: Any,
        platform: Any,
        catalog_source: ProductCapabilityCatalogSourceAdapter,
        resolver: Any,
        ingress: SdkRuntimeIngress,
        runtime_stack: ProductSdkRuntimeStack,
        tool_catalog_generation: Callable[[], int],
    ) -> None:
        if bool(getattr(store, "product_owned", False)):
            raise RuntimeError("skill_install_verification_execution_owner_required")
        self.store = store
        self.platform = platform
        self.catalog_source = catalog_source
        self.resolver = resolver
        self.ingress = ingress
        self.runtime_stack = runtime_stack
        self.tool_catalog_generation = tool_catalog_generation
        self._prepared_leases: dict[str, Any] = {}

    async def verify_skill_install(
        self,
        *,
        intent: Any,
        manager_receipt: Mapping[str, Any],
        members: Sequence[CapabilitySkillInstallMember],
    ) -> Mapping[str, Any]:
        operation_id = str(manager_receipt.get("operation_id") or "")
        receipt_hash = str(manager_receipt.get("manager_receipt_hash") or "")
        committed_stamp = str(manager_receipt.get("committed_set_stamp") or "")
        if (
            not operation_id
            or not receipt_hash
            or committed_stamp != intent.member_set_stamp
        ):
            raise RuntimeError("skill_install_manager_receipt_mismatch")
        session_id = f"skill-install-verifier:{intent.intent_id}"
        attempt = await self.store.allocate_skill_install_verification_attempt(
            intent.intent_id,
            expected_state_version=intent.state_version,
            manager_operation_id=operation_id,
            manager_receipt_hash=receipt_hash,
            committed_set_stamp=committed_stamp,
            project_scope_key=intent.project_scope_key,
            expected_member_set_stamp=intent.member_set_stamp,
            verifier_session_id=session_id,
        )
        if attempt.status == "allocated":
            catalog = self.platform.registry.catalog_snapshot()
            prepared = PreparedToolSet.create(
                scope_id=f"verify:{attempt.attempt_id}",
                revision=1,
                registry_revision=catalog.revision,
                direct=(),
                deferred=(),
                activated=(),
                denied_names=(),
                policy_fingerprint="skill-install-verification-v1",
                decisions=(),
            )
            external_ref = fingerprint_json(
                {
                    "schema": "skill-install-verification-tool-set-v1",
                    "attempt_id": attempt.attempt_id,
                    "registry_revision": catalog.revision,
                }
            )
            source = dict(intent.source)
            scope = CapabilityScope.for_run(
                attempt.expected_run_id,
                project_id=str(source.get("project_id") or ""),
                project_revision=int(source.get("project_revision") or 0),
                project_identity=str(source.get("project_identity") or ""),
                user_key=intent.principal_id,
            )
            lease = await self.platform.prepare_run_catalog_lease(
                scope=scope,
                owner_key=intent.principal_id,
                prepared_tool_set=prepared,
                prepared_tool_set_fingerprint=external_ref,
                run_id=attempt.expected_run_id,
                root_run_id=attempt.expected_run_id,
                request_id=attempt.request_id,
                turn_id=attempt.turn_id,
                owner_operation_id=attempt.attempt_id,
            )
            attempt = await self.store.cas_skill_install_verification_attempt(
                attempt.attempt_id,
                expected_state_version=attempt.state_version,
                status="start_submitted",
                lease_intent_id=lease.lease_intent_id,
                lease_intent_hash=lease.lease_intent_hash,
                capability_snapshot_ref=lease.snapshot_ref,
                run_catalog_content_stamp=lease.run_catalog_content_stamp,
                process_catalog_stamp=lease.process_catalog_stamp,
                projection_receipt_id=lease.projection_receipt_id,
                projection_receipt_hash=lease.projection_receipt_hash,
            )
            self._prepared_leases[attempt.attempt_id] = lease
        if attempt.status == "start_submitted":
            await self.ingress.start_skill_install_verification(
                session_id=attempt.verifier_session_id,
                run_id=attempt.expected_run_id,
                request_id=attempt.request_id,
                turn_id=attempt.turn_id,
                user_id=intent.principal_id,
                attempt_id=attempt.attempt_id,
                attempt_generation=attempt.attempt_generation,
                authority_hash=skill_install_verification_authority_hash(attempt),
                input={"attempt_id": attempt.attempt_id},
                tool_catalog_generation=int(self.tool_catalog_generation()),
            )
        await self.ingress.wait_idle(attempt.expected_run_id)
        return await self.reconcile_attempt(attempt.intent_id)

    def driver_for_attempt(
        self, attempt: CapabilitySkillInstallVerificationAttempt
    ) -> RuntimeDriver:
        return _SkillInstallVerificationDriver(self, attempt)

    async def reconcile_attempt(self, intent_id: str) -> Mapping[str, Any]:
        attempt = await self.store.get_current_skill_install_verification_attempt(
            intent_id
        )
        if attempt is None:
            raise RuntimeError("skill_install_verification_attempt_missing")
        evidence = self.runtime_stack.read_skill_install_verification_evidence(attempt)
        if evidence.status not in {"terminal_succeeded", "terminal_failed"}:
            raise RuntimeError(f"skill_install_verification_{evidence.status}")
        if attempt.status == "page_in_proven":
            attempt = await self.store.cas_skill_install_verification_attempt(
                attempt.attempt_id,
                expected_state_version=attempt.state_version,
                status="terminal_observed",
                terminal_event_id=evidence.terminal_event_id,
                terminal_event_hash=evidence.terminal_event_hash,
                error=(
                    None
                    if evidence.status == "terminal_succeeded"
                    else {"code": evidence.reason_code or "verification_failed"}
                ),
            )
        if evidence.status != "terminal_succeeded":
            raise RuntimeError(evidence.reason_code or "skill_install_verification_failed")
        reconciler = SdkTerminalCapabilityReleaseReconciler(
            store=self.store,
            platform=self.platform,
            runtime_stack=self.runtime_stack,
        )
        attempt = await reconciler.reconcile_one(attempt)
        intent = await self.store.get_skill_install_intent(intent_id)
        if intent is None:
            raise RuntimeError("skill_install_intent_missing")
        attestation_payload = {
            "schema": "skill-install-verification-attestation-v1",
            "attempt_id": attempt.attempt_id,
            "run_id": attempt.expected_run_id,
            "project_scope_key": attempt.project_scope_key,
            "run_catalog_content_stamp": attempt.run_catalog_content_stamp,
            "page_in_evidence_hash": attempt.evidence_hash,
            "terminal_event_hash": attempt.terminal_event_hash,
            "release_receipt_hash": attempt.release_receipt_hash,
        }
        final_hash = fingerprint_json(attestation_payload)
        attestation = await self.store.attach_skill_install_verification_attestation(
            intent_id,
            expected_intent_state_version=intent.state_version,
            attempt_id=attempt.attempt_id,
            expected_attempt_state_version=attempt.state_version,
            attestation={
                **attestation_payload,
                "evidence_hash": final_hash,
                "provenance": "runtime_v4",
                "runtime_proof_valid": True,
            },
        )
        return {
            "status": "succeeded",
            "attempt_id": attempt.attempt_id,
            "run_id": attempt.expected_run_id,
            "verification_ref": attestation.verification_ref,
            "evidence_hash": final_hash,
        }


class _SkillInstallVerificationDriver:
    def __init__(
        self,
        service: SkillInstallVerificationRunService,
        attempt: CapabilitySkillInstallVerificationAttempt,
    ) -> None:
        self.service = service
        self.attempt = attempt

    async def start(self, invocation: Any, *, context: Any, cancel: Any) -> DriverResult:
        del context, cancel
        attempt = await self.service.store.get_skill_install_verification_attempt(
            self.attempt.attempt_id
        )
        if attempt is None:
            return _route_failure("verification_attempt_missing")
        try:
            if attempt.status == "start_submitted":
                attempt = await self.service.store.cas_skill_install_verification_attempt(
                    attempt.attempt_id,
                    expected_state_version=attempt.state_version,
                    status="run_durable",
                    actual_run_id=invocation.run.run_id,
                )
            lease = await self.service.platform.activate_sdk_root_snapshot_after_start(
                lease_intent_id=attempt.lease_intent_id,
                lease_intent_hash=attempt.lease_intent_hash,
                capability_snapshot_ref=attempt.capability_snapshot_ref,
                run_catalog_content_stamp=attempt.run_catalog_content_stamp,
                run_id=attempt.expected_run_id,
                root_run_id=attempt.expected_run_id,
                start_fingerprint=invocation.start.start_fingerprint,
                prepared_lease=self.service._prepared_leases.get(attempt.attempt_id),
            )
            if attempt.status == "run_durable":
                attempt = await self.service.store.cas_skill_install_verification_attempt(
                    attempt.attempt_id,
                    expected_state_version=attempt.state_version,
                    status="catalog_ready",
                )
            intent = await self.service.store.get_skill_install_intent(attempt.intent_id)
            members = await self.service.store.skill_install_members(attempt.intent_id)
            if intent is None:
                raise RuntimeError("verification_intent_missing")
            records = await self.service.catalog_source.sdk_project_resource_records_from_lease(
                store=self.service.store,
                lease=lease,
                owner_key=intent.principal_id,
                project_scope_key=intent.project_scope_key,
            )
            proof = await self.service.catalog_source.verify_fresh_run_page_in(
                lease=lease,
                resolver=self.service.resolver,
                owner_key=intent.principal_id,
                project_scope_key=intent.project_scope_key,
                records=records,
                expected_skill_names=tuple(
                    str(item.member.get("skill_name") or item.normalized_name)
                    for item in members
                ),
            )
            if attempt.status == "catalog_ready":
                await self.service.store.cas_skill_install_verification_attempt(
                    attempt.attempt_id,
                    expected_state_version=attempt.state_version,
                    status="page_in_proven",
                    evidence_hash=proof.evidence_hash,
                )
            return DriverResult(
                RunState.COMPLETED,
                {
                    "schema": "skill-install-verification-final-v1",
                    "status": "succeeded",
                    "page_in_evidence_hash": proof.evidence_hash,
                    "provider_invocation_count": 0,
                    "effect_count": 0,
                    "checkpoint_count": 0,
                    "continuation_count": 0,
                },
            )
        except Exception as exc:  # noqa: BLE001 - bounded terminal algebra
            return _route_failure(
                f"verification_driver_{type(exc).__name__.lower()}"
            )


class SdkTerminalCapabilityReleaseReconciler:
    """Join typed SDK terminal evidence to the Capability release CAS."""

    def __init__(self, *, store: Any, platform: Any, runtime_stack: Any) -> None:
        self.store = store
        self.platform = platform
        self.runtime_stack = runtime_stack

    async def reconcile_pending(self) -> None:
        for attempt in await self.store.list_pending_skill_install_verification_attempts():
            if attempt.status in {
                "run_durable",
                "catalog_ready",
                "page_in_proven",
                "terminal_observed",
                "lease_released",
            }:
                await self.reconcile_one(attempt)

    async def reconcile_one(
        self, attempt: CapabilitySkillInstallVerificationAttempt
    ) -> CapabilitySkillInstallVerificationAttempt:
        evidence = self.runtime_stack.read_skill_install_verification_evidence(attempt)
        terminal_succeeded = evidence.status == "terminal_succeeded"
        if attempt.status in {"run_durable", "catalog_ready"}:
            if evidence.status not in {"terminal_succeeded", "terminal_failed"}:
                return attempt
            return await self._release_terminal_attempt(
                attempt, evidence=evidence, succeeded=False
            )
        if attempt.status == "page_in_proven":
            if evidence.status not in {"terminal_succeeded", "terminal_failed"}:
                return attempt
            attempt = await self.store.cas_skill_install_verification_attempt(
                attempt.attempt_id,
                expected_state_version=attempt.state_version,
                status="terminal_observed",
                terminal_event_id=evidence.terminal_event_id,
                terminal_event_hash=evidence.terminal_event_hash,
                error=(
                    None
                    if terminal_succeeded
                    else {"code": evidence.reason_code or "verification_failed"}
                ),
            )
        if attempt.status == "terminal_observed":
            return await self._release_terminal_attempt(
                attempt, evidence=evidence, succeeded=terminal_succeeded
            )
        return attempt

    async def _release_terminal_attempt(
        self, attempt: CapabilitySkillInstallVerificationAttempt, *, evidence: Any,
        succeeded: bool,
    ) -> CapabilitySkillInstallVerificationAttempt:
        owner_ref = f"sdk-execution-run-terminal:{attempt.expected_run_id}"
        owner_hash = fingerprint_json(
            {
                "owner_ref": owner_ref,
                "terminal_event_id": evidence.terminal_event_id,
                "terminal_event_hash": evidence.terminal_event_hash,
            }
        )
        receipt = await self.store.get_snapshot_lease_release_receipt(
            attempt.lease_intent_id
        )
        if receipt is None:
            async with self.store.write_transaction() as db:
                receipt = await self.store.bind(db).release_snapshot_lease_intent_in_tx(
                    attempt.lease_intent_id,
                    intent_hash=attempt.lease_intent_hash,
                    release_reason="terminal",
                    owner_terminal_or_transition_ref=owner_ref,
                    owner_terminal_or_transition_hash=owner_hash,
                )
        elif (
            receipt.run_id != attempt.expected_run_id
            or receipt.owner_terminal_or_transition_ref != owner_ref
            or receipt.owner_terminal_or_transition_hash != owner_hash
        ):
            raise RuntimeError("skill_install_release_receipt_mismatch")
        await self.platform.retire_run_catalog_ready(attempt.expected_run_id)
        return await self.store.cas_skill_install_verification_attempt(
            attempt.attempt_id,
            expected_state_version=attempt.state_version,
            status="lease_released" if succeeded else "terminal_failed",
            terminal_event_id=evidence.terminal_event_id,
            terminal_event_hash=evidence.terminal_event_hash,
            release_receipt_id=receipt.release_receipt_id,
            release_receipt_hash=receipt.release_receipt_hash,
            release_owner_event_hash=owner_hash,
            error=(
                None
                if succeeded
                else {"code": evidence.reason_code or "verification_failed"}
            ),
        )


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
    "SdkTerminalCapabilityReleaseReconciler",
    "SkillInstallVerificationAttemptResolver",
    "SkillInstallVerificationRunService",
    "skill_install_verification_authority_hash",
)
