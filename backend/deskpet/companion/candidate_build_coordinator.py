"""Durable cross-database coordinator for governed candidate builds.

The Companion store owns the monotonic state machine.  Execution owns the
precreated child and immutable draft receipt.  This coordinator never holds
writers in both stores at once and only advances Companion state after exact
execution identities have been observed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .build_admission import (
    CandidateDraftReceiptExpectationV1,
    CandidateDraftReceiptQueryPort,
    CapabilityBuildOutputPort,
    GrowthCandidateBuildPermitQueryPort,
    GrowthCandidateBuildPermitV1,
    ReservedBuilderLaunchPort,
)
from .contracts import JsonValue


class CandidateBuildStatus(StrEnum):
    PROPOSED = "proposed"
    LAUNCH_PENDING = "launch_pending"
    CHILD_PRECREATED = "child_precreated"
    RUNNING = "running"
    HANDOFF_PENDING = "handoff_pending"
    BUILT = "built"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    CANCELLED = "cancelled"
    STALE = "stale"


_TERMINAL = frozenset(
    {
        CandidateBuildStatus.BUILT,
        CandidateBuildStatus.FAILED,
        CandidateBuildStatus.INCONCLUSIVE,
        CandidateBuildStatus.CANCELLED,
        CandidateBuildStatus.STALE,
    }
)


class CandidateBuildCoordinationError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CandidateBuildLaunchStateV1:
    build_id: str
    status: CandidateBuildStatus | str
    permit_id: str
    permit_hash: str
    lease_epoch: int
    builder_launch_id: str
    child_run_id: str
    expected_child_start_hash: str
    task_workspace: str
    draft_receipt_expectation: (
        CandidateDraftReceiptExpectationV1 | None
    ) = None
    candidate_ref: str | None = None
    candidate_hash: str | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        status = CandidateBuildStatus(self.status)
        object.__setattr__(self, "status", status)
        for name in (
            "build_id",
            "permit_id",
            "builder_launch_id",
            "child_run_id",
            "task_workspace",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name}_required")
        for name in ("permit_hash", "expected_child_start_hash"):
            value = str(getattr(self, name) or "")
            if len(value) != 64 or any(
                char not in "0123456789abcdef" for char in value
            ):
                raise ValueError(f"{name}_invalid")
        if self.lease_epoch < 1:
            raise ValueError("lease_epoch_invalid")
        if (
            status
            in {CandidateBuildStatus.HANDOFF_PENDING, CandidateBuildStatus.BUILT}
            and self.draft_receipt_expectation is None
        ):
            raise ValueError("draft_receipt_expectation_required")
        if status is CandidateBuildStatus.BUILT and (
            not self.candidate_ref or not self.candidate_hash
        ):
            raise ValueError("built_candidate_identity_required")
        if self.candidate_hash is not None and (
            len(self.candidate_hash) != 64
            or any(
                char not in "0123456789abcdef"
                for char in self.candidate_hash
            )
        ):
            raise ValueError("candidate_hash_invalid")


class CandidateBuildStatePort(Protocol):
    """Caller-owned Companion transactions behind monotonic CAS methods."""

    def prepare_launch_pending(
        self,
        permit: GrowthCandidateBuildPermitV1,
        *,
        task_workspace: str,
    ) -> CandidateBuildLaunchStateV1: ...

    def ack_child_precreated(
        self,
        permit: GrowthCandidateBuildPermitV1,
        *,
        builder_launch_id: str,
        child_run_id: str,
        child_start_hash: str,
    ) -> CandidateBuildLaunchStateV1: ...

    def ack_running(
        self,
        permit: GrowthCandidateBuildPermitV1,
        *,
        builder_launch_id: str,
        child_run_id: str,
        child_start_hash: str,
    ) -> CandidateBuildLaunchStateV1: ...

    def ack_handoff_pending(
        self,
        permit: GrowthCandidateBuildPermitV1,
        *,
        expectation: CandidateDraftReceiptExpectationV1,
    ) -> CandidateBuildLaunchStateV1: ...

    def ack_built(
        self,
        permit: GrowthCandidateBuildPermitV1,
        *,
        expectation: CandidateDraftReceiptExpectationV1,
        handoff: Mapping[str, JsonValue],
    ) -> CandidateBuildLaunchStateV1: ...

    def mark_stale(
        self,
        permit: GrowthCandidateBuildPermitV1,
        *,
        reason_code: str,
    ) -> CandidateBuildLaunchStateV1: ...

    def mark_failed(
        self,
        permit: GrowthCandidateBuildPermitV1,
        *,
        reason_code: str,
    ) -> CandidateBuildLaunchStateV1: ...


class CompanionCandidateBuildCoordinator:
    """Recover one proposal through child launch and candidate handoff."""

    def __init__(
        self,
        *,
        permits: GrowthCandidateBuildPermitQueryPort,
        states: CandidateBuildStatePort,
        launches: ReservedBuilderLaunchPort,
        receipts: CandidateDraftReceiptQueryPort,
        output: CapabilityBuildOutputPort,
    ) -> None:
        self._permits = permits
        self._states = states
        self._launches = launches
        self._receipts = receipts
        self._output = output

    async def drive_claim(
        self,
        *,
        build_id: str,
        claim_owner: str,
        claim_epoch: int,
        task_workspace: str,
    ) -> CandidateBuildLaunchStateV1:
        permit = self._permits.issue_for_claim(
            build_id=build_id,
            claim_owner=claim_owner,
            claim_epoch=claim_epoch,
        )
        return await self.reconcile(
            permit, task_workspace=task_workspace
        )

    async def reconcile(
        self,
        permit: GrowthCandidateBuildPermitV1,
        *,
        task_workspace: str,
    ) -> CandidateBuildLaunchStateV1:
        state = self._states.prepare_launch_pending(
            permit, task_workspace=task_workspace
        )
        self._validate_state(permit, state, task_workspace)
        for _ in range(8):
            self._validate_state(permit, state, task_workspace)
            if state.status in _TERMINAL:
                return state
            if state.status is CandidateBuildStatus.LAUNCH_PENDING:
                observed = await self._launches.read_exact(permit)
                if observed is None:
                    observed = await self._launches.precreate_exact(
                        permit, task_workspace=task_workspace
                    )
                try:
                    self._validate_child(
                        state, observed, allowed_statuses={"precreated"}
                    )
                except CandidateBuildCoordinationError:
                    self._states.mark_stale(
                        permit, reason_code="stale_fence"
                    )
                    raise
                state = self._states.ack_child_precreated(
                    permit,
                    builder_launch_id=state.builder_launch_id,
                    child_run_id=state.child_run_id,
                    child_start_hash=state.expected_child_start_hash,
                )
                continue
            if state.status is CandidateBuildStatus.CHILD_PRECREATED:
                observed = await self._launches.start_precreated(permit)
                try:
                    self._validate_child(
                        state,
                        observed,
                        allowed_statuses={
                            "running",
                            "succeeded",
                            "failed",
                        },
                    )
                except CandidateBuildCoordinationError:
                    self._states.mark_stale(
                        permit, reason_code="stale_fence"
                    )
                    raise
                state = self._states.ack_running(
                    permit,
                    builder_launch_id=state.builder_launch_id,
                    child_run_id=state.child_run_id,
                    child_start_hash=state.expected_child_start_hash,
                )
                continue
            if state.status is CandidateBuildStatus.RUNNING:
                observed = await self._launches.read_exact(permit)
                if observed is None:
                    raise CandidateBuildCoordinationError(
                        "reserved_builder_child_missing"
                    )
                try:
                    self._validate_child(
                        state,
                        observed,
                        allowed_statuses={
                            "running",
                            "succeeded",
                            "failed",
                        },
                    )
                except CandidateBuildCoordinationError:
                    self._states.mark_stale(
                        permit, reason_code="stale_fence"
                    )
                    raise
                child_status = str(observed["status"])
                if child_status == "running":
                    return state
                if child_status == "failed":
                    return self._states.mark_failed(
                        permit, reason_code="builder_child_failed"
                    )
                expectation = self._expectation(state, observed)
                state = self._states.ack_handoff_pending(
                    permit, expectation=expectation
                )
                continue
            if state.status is CandidateBuildStatus.HANDOFF_PENDING:
                expectation = state.draft_receipt_expectation
                if expectation is None:  # pragma: no cover - dataclass guard
                    raise CandidateBuildCoordinationError(
                        "candidate_draft_receipt_expectation_missing"
                    )
                receipt = self._receipts.read_exact(expectation)
                expectation.verify(receipt)
                handoff = self._output.handoff_candidate(
                    permit=permit, receipt=receipt
                )
                self._validate_handoff(permit, handoff)
                state = self._states.ack_built(
                    permit,
                    expectation=expectation,
                    handoff=handoff,
                )
                continue
            raise CandidateBuildCoordinationError(
                "candidate_build_state_unsupported"
            )
        raise CandidateBuildCoordinationError(
            "candidate_build_reconcile_did_not_converge"
        )

    @staticmethod
    def _validate_state(
        permit: GrowthCandidateBuildPermitV1,
        state: CandidateBuildLaunchStateV1,
        task_workspace: str,
    ) -> None:
        if (
            state.build_id != permit.build_id
            or state.permit_id != permit.permit_id
            or state.permit_hash != permit.permit_hash
            or state.lease_epoch != permit.lease_epoch
            or state.task_workspace != task_workspace
        ):
            raise CandidateBuildCoordinationError(
                "candidate_build_permit_state_mismatch"
            )

    @staticmethod
    def _validate_child(
        state: CandidateBuildLaunchStateV1,
        observed: Mapping[str, JsonValue],
        *,
        allowed_statuses: set[str],
    ) -> None:
        if (
            str(observed.get("builder_launch_id") or "")
            != state.builder_launch_id
            or str(observed.get("child_run_id") or "")
            != state.child_run_id
            or str(observed.get("child_start_hash") or "")
            != state.expected_child_start_hash
            or str(observed.get("status") or "") not in allowed_statuses
        ):
            raise CandidateBuildCoordinationError(
                "reserved_builder_child_identity_mismatch"
            )

    @staticmethod
    def _expectation(
        state: CandidateBuildLaunchStateV1,
        observed: Mapping[str, JsonValue],
    ) -> CandidateDraftReceiptExpectationV1:
        raw = observed.get("draft_receipt")
        if not isinstance(raw, Mapping):
            raise CandidateBuildCoordinationError(
                "candidate_draft_receipt_expectation_missing"
            )
        values = {
            name: str(raw.get(name) or "")
            for name in CandidateDraftReceiptExpectationV1.__dataclass_fields__
        }
        expectation = CandidateDraftReceiptExpectationV1(**values)
        if (
            expectation.builder_launch_id != state.builder_launch_id
            or expectation.child_run_id != state.child_run_id
            or expectation.child_start_hash
            != state.expected_child_start_hash
        ):
            raise CandidateBuildCoordinationError(
                "candidate_draft_receipt_child_mismatch"
            )
        return expectation

    @staticmethod
    def _validate_handoff(
        permit: GrowthCandidateBuildPermitV1,
        handoff: Mapping[str, JsonValue],
    ) -> None:
        candidate_ref = str(handoff.get("candidate_ref") or "")
        candidate_hash = str(handoff.get("candidate_hash") or "")
        build_id = str(handoff.get("build_id") or permit.build_id)
        if (
            not candidate_ref
            or len(candidate_hash) != 64
            or any(
                char not in "0123456789abcdef"
                for char in candidate_hash
            )
            or build_id != permit.build_id
        ):
            raise CandidateBuildCoordinationError(
                "candidate_handoff_identity_mismatch"
            )


__all__ = [
    "CandidateBuildCoordinationError",
    "CandidateBuildLaunchStateV1",
    "CandidateBuildStatePort",
    "CandidateBuildStatus",
    "CompanionCandidateBuildCoordinator",
]
