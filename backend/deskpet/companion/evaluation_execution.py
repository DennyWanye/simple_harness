"""Short-fenced execution of one frozen Companion evaluation case.

The durable Companion store owns claims, launch identities, and results.  This
module owns the only production ordering that is allowed to cross from those
facts into a physical child or an in-process evaluation adapter:

``claim -> short fence -> durable launch -> start ACK -> long completion
-> short fence -> atomic result``.

There is deliberately no compatibility fallback to an unfenced callback.  A
store that cannot provide the full fence/start/result/abort API is rejected
before a process adapter is called.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, Mapping, Protocol

from deskpet.capabilities.process_job import (
    ManagedProcessStartPort,
    ManagedProcessStartReceipt,
)

from .authority import RevocationBarrier, SharedRevocationLease
from .contracts import EvaluationCaseLaunch, EvaluationExecutionPermit, OwnerRef
from .store import canonical_hash


class EvaluationExecutionError(RuntimeError):
    """Fail-closed case execution error with a stable reason code."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class FrozenEvaluationCaseV1:
    """Immutable case facts selected before a worker may claim execution."""

    evaluation_id: str
    case_id: str
    variant: Literal["old", "candidate"]
    candidate_id: str
    package_hash: str
    manifest_hash: str
    archive_hash: str
    suite_hash: str
    runner_policy_hash: str
    input_hash: str
    adapter_id: str
    adapter_version: str
    adapter_fingerprint: str
    permit: EvaluationExecutionPermit
    execution_kind: Literal["managed_process", "in_process"]
    start_envelope: Mapping[str, Any]
    completion_envelope: Mapping[str, Any]

    def __post_init__(self) -> None:
        required = (
            self.evaluation_id,
            self.case_id,
            self.candidate_id,
            self.package_hash,
            self.manifest_hash,
            self.archive_hash,
            self.suite_hash,
            self.runner_policy_hash,
            self.input_hash,
            self.adapter_id,
            self.adapter_version,
            self.adapter_fingerprint,
        )
        if any(not value for value in required):
            raise ValueError("frozen evaluation case fields are required")
        permit = self.permit
        if (
            permit.evaluation_id != self.evaluation_id
            or permit.candidate_id != self.candidate_id
            or permit.package_hash != self.package_hash
            or permit.manifest_hash != self.manifest_hash
            or permit.archive_hash != self.archive_hash
            or permit.suite_hash != self.suite_hash
            or permit.runner_policy_hash != self.runner_policy_hash
        ):
            raise ValueError("frozen case and execution permit disagree")


@dataclass(frozen=True, slots=True)
class EvaluationExecutionFenceV1:
    """One store-produced snapshot used inside a short revocation lease."""

    evaluation_id: str
    evaluation_status: str
    candidate_id: str
    candidate_status: str
    package_hash: str
    manifest_hash: str
    archive_hash: str
    suite_hash: str
    runner_policy_hash: str
    permit_id: str
    permit_mode: str
    permit_hash: str
    permit_status: str
    issued_revocation_epoch: int
    preflight_ref: str
    preflight_hash: str
    risk_ref: str
    risk_hash: str
    risk_level: str
    authorization_id: str | None
    authorization_consumed: bool
    policy_verified: bool
    case_id: str
    variant: str
    case_status: str
    claim_owner: str
    claim_epoch: int
    attempt_ordinal: int
    recovery_only: bool
    input_hash: str
    input_content_state: str
    adapter_id: str
    adapter_version: str
    adapter_fingerprint: str

    @classmethod
    def from_mapping(
        cls, row: Mapping[str, Any]
    ) -> "EvaluationExecutionFenceV1":
        try:
            return cls(
                evaluation_id=str(row["evaluation_id"]),
                evaluation_status=str(row["evaluation_status"]),
                candidate_id=str(row["candidate_id"]),
                candidate_status=str(row["candidate_status"]),
                package_hash=str(row["package_hash"]),
                manifest_hash=str(row["manifest_hash"]),
                archive_hash=str(row["archive_hash"]),
                suite_hash=str(row["suite_hash"]),
                runner_policy_hash=str(row["runner_policy_hash"]),
                permit_id=str(row["permit_id"]),
                permit_mode=str(row["permit_mode"]),
                permit_hash=str(row["permit_hash"]),
                permit_status=str(row["permit_status"]),
                issued_revocation_epoch=int(row["issued_revocation_epoch"]),
                preflight_ref=str(row["preflight_ref"]),
                preflight_hash=str(row["preflight_hash"]),
                risk_ref=str(row["risk_ref"]),
                risk_hash=str(row["risk_hash"]),
                risk_level=str(row["risk_level"]),
                authorization_id=(
                    None
                    if row["authorization_id"] is None
                    else str(row["authorization_id"])
                ),
                authorization_consumed=bool(row["authorization_consumed"]),
                policy_verified=bool(row["policy_verified"]),
                case_id=str(row["case_id"]),
                variant=str(row["variant"]),
                case_status=str(row["case_status"]),
                claim_owner=str(row["claim_owner"]),
                claim_epoch=int(row["claim_epoch"]),
                attempt_ordinal=int(row["attempt_ordinal"]),
                recovery_only=bool(row["recovery_only"]),
                input_hash=str(row["input_hash"]),
                input_content_state=str(row["input_content_state"]),
                adapter_id=str(row["adapter_id"]),
                adapter_version=str(row["adapter_version"]),
                adapter_fingerprint=str(row["adapter_fingerprint"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EvaluationExecutionError(
                "evaluation_fence_snapshot_incomplete"
            ) from exc


@dataclass(frozen=True, slots=True)
class EvaluationCaseStartAckV1:
    launch_id: str
    runtime_instance_id: str
    adapter_identity: str
    job_identity: str | None
    pid: int | None
    process_create_time: float | None
    command_line_hash: str | None
    start_receipt_hash: str


@dataclass(frozen=True, slots=True)
class EvaluationCaseCompletionV1:
    outcome_ref: str
    outcome_hash: str
    assertions: Mapping[str, Any]
    judge_result: Mapping[str, Any]
    usage: Mapping[str, Any]
    result_hash: str
    reason_code: str
    cleanup_receipt_hash: str | None = None
    survivor_count: int | None = None

    @classmethod
    def from_mapping(
        cls, row: Mapping[str, Any]
    ) -> "EvaluationCaseCompletionV1":
        if row.get("status") != "completed":
            raise EvaluationExecutionError(
                "evaluation_completion_not_successful"
            )
        try:
            assertions = dict(row["assertions"])
            judge_result = dict(row["judge_result"])
            usage = dict(row["usage"])
            outcome_ref = str(row["outcome_ref"])
            outcome_hash = str(row["outcome_hash"])
            reason_code = str(row.get("reason_code") or "evaluation_completed")
        except (KeyError, TypeError, ValueError) as exc:
            raise EvaluationExecutionError(
                "evaluation_completion_incomplete"
            ) from exc
        payload = {
            "assertions": assertions,
            "judge_result": judge_result,
            "usage": usage,
        }
        result_hash = canonical_hash(payload)
        if not outcome_ref or not outcome_hash:
            raise EvaluationExecutionError("evaluation_completion_incomplete")
        supplied = row.get("result_hash")
        if supplied is not None and str(supplied) != result_hash:
            raise EvaluationExecutionError(
                "evaluation_completion_result_hash_mismatch"
            )
        return cls(
            outcome_ref=outcome_ref,
            outcome_hash=outcome_hash,
            assertions=assertions,
            judge_result=judge_result,
            usage=usage,
            result_hash=result_hash,
            reason_code=reason_code,
        )


@dataclass(frozen=True, slots=True)
class EvaluationCaseExecutionOutcomeV1:
    status: Literal[
        "committed",
        "not_started",
        "inconclusive",
        "cleanup_required",
        "recovery_only",
    ]
    reason_code: str
    launch_id: str | None = None
    result_hash: str | None = None


class EvaluationExecutionStorePort(Protocol):
    """Required durable API; every mutating method is one Companion tx."""

    def claim_evaluation_case(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_owner: str,
        lease_seconds: float,
    ) -> Mapping[str, Any]: ...

    def get_evaluation_execution_fence(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
    ) -> Mapping[str, Any]: ...

    def create_evaluation_case_launch(
        self,
        owner: OwnerRef,
        launch: EvaluationCaseLaunch,
        *,
        claim_owner: str,
    ) -> Mapping[str, Any]: ...

    def record_evaluation_case_start_ack(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_owner: str,
        claim_epoch: int,
        ack: EvaluationCaseStartAckV1,
        reason_code: str,
    ) -> Mapping[str, Any]: ...

    def commit_evaluation_case_completion(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_owner: str,
        claim_epoch: int,
        launch_id: str,
        completion: EvaluationCaseCompletionV1,
    ) -> Mapping[str, Any]: ...

    def abort_evaluation_case_execution(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_owner: str,
        claim_epoch: int,
        launch_id: str,
        expected_launch_status: str,
        cleanup_receipt_hash: str | None,
        survivor_count: int | None,
        reason_code: str,
    ) -> Mapping[str, Any]: ...

    def settle_evaluation_case_launch(
        self,
        owner: OwnerRef,
        *,
        launch_id: str,
        expected_status: str,
        status: str,
        outcome_ref: str | None = None,
        outcome_hash: str | None = None,
        cleanup_receipt_hash: str | None = None,
        survivor_count: int | None = None,
        reason_code: str,
    ) -> Mapping[str, Any]: ...

    def get_evaluation_case_recovery_state(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_epoch: int,
    ) -> Mapping[str, Any] | None: ...


class FenceAwareEvaluationCaseExecutor:
    """The sole production adapter from a durable case to physical execution."""

    def __init__(
        self,
        *,
        store: EvaluationExecutionStorePort,
        barrier: RevocationBarrier,
        managed_process_port: ManagedProcessStartPort,
        in_process_port: ManagedProcessStartPort,
    ) -> None:
        self._store = store
        self._barrier = barrier
        self._managed_process_port = managed_process_port
        self._in_process_port = in_process_port

    async def execute_case(
        self,
        owner: OwnerRef,
        frozen: FrozenEvaluationCaseV1,
        *,
        claim_owner: str,
        lease_seconds: float = 30.0,
    ) -> EvaluationCaseExecutionOutcomeV1:
        self._require_store_execution_api()
        claim = self._store.claim_evaluation_case(
            owner,
            evaluation_id=frozen.evaluation_id,
            case_id=frozen.case_id,
            variant=frozen.variant,
            claim_owner=claim_owner,
            lease_seconds=lease_seconds,
        )
        claim_epoch = _int_field(claim, "claim_epoch")
        attempt_ordinal = _int_field(claim, "attempt")
        if bool(claim.get("recovery_only")):
            return await self._recover_only(
                owner,
                frozen,
                claim_owner=claim_owner,
                claim_epoch=claim_epoch,
            )

        port = (
            self._managed_process_port
            if frozen.execution_kind == "managed_process"
            else self._in_process_port
        )
        launch: EvaluationCaseLaunch
        receipt: ManagedProcessStartReceipt
        runtime_instance_id: str
        authorization_nonce: str
        async with self._barrier.shared() as lease:
            fence = self._read_and_validate_fence(
                owner,
                frozen,
                claim_owner=claim_owner,
                claim_epoch=claim_epoch,
                attempt_ordinal=attempt_ordinal,
                lease=lease,
            )
            launch = self._make_launch(frozen, fence, lease)
            self._store.create_evaluation_case_launch(
                owner, launch, claim_owner=claim_owner
            )
            runtime_instance_id = f"evaluation:{launch.launch_id}"
            authorization_nonce = canonical_hash(
                {
                    "schema_version": 1,
                    "purpose": "evaluation_case_start",
                    "launch_fingerprint": launch.launch_fingerprint,
                    "runtime_instance_id": runtime_instance_id,
                }
            )
            try:
                receipt = await port.start_and_ack(
                    runtime_instance_id=runtime_instance_id,
                    start_envelope=frozen.start_envelope,
                    authorization_nonce=authorization_nonce,
                )
            except BaseException:
                await port.abort_unacknowledged(
                    runtime_instance_id=runtime_instance_id,
                    authorization_nonce=authorization_nonce,
                )
                await self._abort_without_ack(
                    owner,
                    frozen,
                    claim_owner=claim_owner,
                    claim_epoch=claim_epoch,
                    launch_id=launch.launch_id,
                    expected_launch_status="claimed",
                    port=port,
                    runtime_instance_id=runtime_instance_id,
                    reason_code="evaluation_start_exception",
                )
                raise
            try:
                self._validate_start_receipt(
                    frozen,
                    receipt,
                    runtime_instance_id=runtime_instance_id,
                )
            except EvaluationExecutionError:
                if receipt.outcome == "started":
                    await port.abort_exact(receipt)
                else:
                    await port.abort_unacknowledged(
                        runtime_instance_id=runtime_instance_id,
                        authorization_nonce=authorization_nonce,
                    )
                await self._abort_without_ack(
                    owner,
                    frozen,
                    claim_owner=claim_owner,
                    claim_epoch=claim_epoch,
                    launch_id=launch.launch_id,
                    expected_launch_status="claimed",
                    port=port,
                    runtime_instance_id=runtime_instance_id,
                    reason_code="evaluation_start_receipt_invalid",
                )
                raise
            if receipt.outcome == "not_started":
                await port.abort_unacknowledged(
                    runtime_instance_id=runtime_instance_id,
                    authorization_nonce=authorization_nonce,
                )
                cleanup_hash, survivors = await self._verify_absent(
                    port, receipt, runtime_instance_id=runtime_instance_id
                )
                self._store.settle_evaluation_case_launch(
                    owner,
                    launch_id=launch.launch_id,
                    expected_status="claimed",
                    status="not_started",
                    cleanup_receipt_hash=cleanup_hash,
                    survivor_count=survivors,
                    reason_code=receipt.reason_code
                    or "evaluation_process_not_started",
                )
                return EvaluationCaseExecutionOutcomeV1(
                    status="not_started",
                    reason_code=receipt.reason_code
                    or "evaluation_process_not_started",
                    launch_id=launch.launch_id,
                )
            if receipt.outcome == "unknown":
                await port.abort_unacknowledged(
                    runtime_instance_id=runtime_instance_id,
                    authorization_nonce=authorization_nonce,
                )
                cleanup_hash, survivors = await self._verify_absent(
                    port, receipt, runtime_instance_id=runtime_instance_id
                )
                self._store.abort_evaluation_case_execution(
                    owner,
                    evaluation_id=frozen.evaluation_id,
                    case_id=frozen.case_id,
                    variant=frozen.variant,
                    claim_owner=claim_owner,
                    claim_epoch=claim_epoch,
                    launch_id=launch.launch_id,
                    expected_launch_status="claimed",
                    cleanup_receipt_hash=cleanup_hash,
                    survivor_count=survivors,
                    reason_code=receipt.reason_code
                    or "evaluation_start_unknown",
                )
                return EvaluationCaseExecutionOutcomeV1(
                    status=(
                        "inconclusive" if survivors == 0 else "cleanup_required"
                    ),
                    reason_code=receipt.reason_code
                    or "evaluation_start_unknown",
                    launch_id=launch.launch_id,
                )
            ack = self._start_ack(launch.launch_id, receipt)
            self._store.record_evaluation_case_start_ack(
                owner,
                evaluation_id=frozen.evaluation_id,
                case_id=frozen.case_id,
                variant=frozen.variant,
                claim_owner=claim_owner,
                claim_epoch=claim_epoch,
                ack=ack,
                reason_code="evaluation_start_acknowledged",
            )

        try:
            completion_row = await port.await_health(
                receipt, frozen.completion_envelope
            )
            completion = EvaluationCaseCompletionV1.from_mapping(
                completion_row
            )
            cleanup_hash, survivors = await self._abort_started(
                port, receipt, runtime_instance_id=runtime_instance_id
            )
            if cleanup_hash is None or survivors != 0:
                raise EvaluationExecutionError(
                    "evaluation_completion_cleanup_required"
                )
            completion = replace(
                completion,
                cleanup_receipt_hash=cleanup_hash,
                survivor_count=survivors,
            )
            async with self._barrier.shared() as lease:
                self._read_and_validate_fence(
                    owner,
                    frozen,
                    claim_owner=claim_owner,
                    claim_epoch=claim_epoch,
                    attempt_ordinal=attempt_ordinal,
                    lease=lease,
                )
                self._store.commit_evaluation_case_completion(
                    owner,
                    evaluation_id=frozen.evaluation_id,
                    case_id=frozen.case_id,
                    variant=frozen.variant,
                    claim_owner=claim_owner,
                    claim_epoch=claim_epoch,
                    launch_id=launch.launch_id,
                    completion=completion,
                )
            return EvaluationCaseExecutionOutcomeV1(
                status="committed",
                reason_code=completion.reason_code,
                launch_id=launch.launch_id,
                result_hash=completion.result_hash,
            )
        except BaseException as exc:
            reason_code = (
                exc.reason_code
                if isinstance(exc, EvaluationExecutionError)
                else "evaluation_completion_exception"
            )
            cleanup_hash, survivors = await self._abort_started(
                port, receipt, runtime_instance_id=runtime_instance_id
            )
            self._store.abort_evaluation_case_execution(
                owner,
                evaluation_id=frozen.evaluation_id,
                case_id=frozen.case_id,
                variant=frozen.variant,
                claim_owner=claim_owner,
                claim_epoch=claim_epoch,
                launch_id=launch.launch_id,
                expected_launch_status="started",
                cleanup_receipt_hash=cleanup_hash,
                survivor_count=survivors,
                reason_code=reason_code,
            )
            return EvaluationCaseExecutionOutcomeV1(
                status=(
                    "inconclusive" if survivors == 0 else "cleanup_required"
                ),
                reason_code=reason_code,
                launch_id=launch.launch_id,
            )

    def _require_store_execution_api(self) -> None:
        required = (
            "claim_evaluation_case",
            "get_evaluation_execution_fence",
            "create_evaluation_case_launch",
            "record_evaluation_case_start_ack",
            "commit_evaluation_case_completion",
            "abort_evaluation_case_execution",
            "settle_evaluation_case_launch",
        )
        missing = tuple(
            name
            for name in required
            if not callable(getattr(self._store, name, None))
        )
        if missing:
            raise EvaluationExecutionError(
                "evaluation_store_execution_api_unavailable:"
                + ",".join(missing)
            )

    def _read_and_validate_fence(
        self,
        owner: OwnerRef,
        frozen: FrozenEvaluationCaseV1,
        *,
        claim_owner: str,
        claim_epoch: int,
        attempt_ordinal: int,
        lease: SharedRevocationLease,
    ) -> EvaluationExecutionFenceV1:
        reader = getattr(self._store, "get_evaluation_execution_fence", None)
        if not callable(reader):
            raise EvaluationExecutionError(
                "evaluation_fence_snapshot_api_unavailable"
            )
        fence = EvaluationExecutionFenceV1.from_mapping(
            reader(
                owner,
                evaluation_id=frozen.evaluation_id,
                case_id=frozen.case_id,
                variant=frozen.variant,
            )
        )
        permit = frozen.permit
        exact = (
            fence.evaluation_id == frozen.evaluation_id
            and fence.evaluation_status in {"queued", "running"}
            and fence.candidate_id == frozen.candidate_id
            and fence.candidate_status == "evaluating"
            and fence.package_hash == frozen.package_hash
            and fence.manifest_hash == frozen.manifest_hash
            and fence.archive_hash == frozen.archive_hash
            and fence.suite_hash == frozen.suite_hash
            and fence.runner_policy_hash == frozen.runner_policy_hash
            and fence.permit_id == permit.permit_id
            and fence.permit_mode == permit.mode
            and fence.permit_hash == permit.permit_hash
            and fence.permit_status in {"issued", "claimed"}
            and fence.issued_revocation_epoch
            == permit.issued_revocation_epoch
            == lease.epoch
            and fence.preflight_ref == permit.preflight_ref
            and fence.preflight_hash == permit.preflight_hash
            and fence.risk_ref == permit.risk_ref
            and fence.risk_hash == permit.risk_hash
            and fence.case_id == frozen.case_id
            and fence.variant == frozen.variant
            and fence.case_status == "leased"
            and fence.claim_owner == claim_owner
            and fence.claim_epoch == claim_epoch
            and fence.attempt_ordinal == attempt_ordinal
            and not fence.recovery_only
            and fence.input_hash == frozen.input_hash
            and fence.input_content_state == "live"
            and fence.adapter_id == frozen.adapter_id
            and fence.adapter_version == frozen.adapter_version
            and fence.adapter_fingerprint == frozen.adapter_fingerprint
            and fence.policy_verified
            and self._barrier.is_current(lease)
        )
        if permit.mode == "safe_auto":
            exact = (
                exact
                and fence.risk_level == "low"
                and fence.authorization_id is None
            )
        elif permit.mode == "user_authorized":
            exact = (
                exact
                and permit.authorization_id is not None
                and fence.authorization_id == permit.authorization_id
                and fence.authorization_consumed
            )
        else:
            exact = False
        if not exact:
            reason = (
                "evaluation_input_redacted"
                if fence.input_content_state != "live"
                else "evaluation_execution_fence_stale"
            )
            raise EvaluationExecutionError(reason)
        return fence

    @staticmethod
    def _make_launch(
        frozen: FrozenEvaluationCaseV1,
        fence: EvaluationExecutionFenceV1,
        lease: SharedRevocationLease,
    ) -> EvaluationCaseLaunch:
        launch_key = {
            "schema_version": 1,
            "evaluation_id": frozen.evaluation_id,
            "case_id": frozen.case_id,
            "variant": frozen.variant,
            "attempt_ordinal": fence.attempt_ordinal,
            "claim_epoch": fence.claim_epoch,
        }
        launch_id = f"evaluation-launch:{canonical_hash(launch_key)}"
        facts = {
            "schema_version": 1,
            "launch_id": launch_id,
            "evaluation_id": frozen.evaluation_id,
            "case_id": frozen.case_id,
            "variant": frozen.variant,
            "attempt_ordinal": fence.attempt_ordinal,
            "candidate_package_hash": frozen.package_hash,
            "candidate_manifest_hash": frozen.manifest_hash,
            "candidate_archive_hash": frozen.archive_hash,
            "suite_hash": frozen.suite_hash,
            "permit_mode": frozen.permit.mode,
            "permit_id": frozen.permit.permit_id,
            "permit_hash": frozen.permit.permit_hash,
            "case_lease_epoch": fence.claim_epoch,
            "revocation_epoch": lease.epoch,
            "adapter_id": frozen.adapter_id,
            "adapter_version": frozen.adapter_version,
            "adapter_fingerprint": frozen.adapter_fingerprint,
        }
        return EvaluationCaseLaunch(
            **facts,
            launch_fingerprint=canonical_hash(facts),
            reason_code="evaluation_case_launch_claimed",
        )

    @staticmethod
    def _validate_start_receipt(
        frozen: FrozenEvaluationCaseV1,
        receipt: ManagedProcessStartReceipt,
        *,
        runtime_instance_id: str,
    ) -> None:
        if receipt.runtime_instance_id != runtime_instance_id:
            raise EvaluationExecutionError(
                "evaluation_start_runtime_identity_mismatch"
            )
        if receipt.outcome not in {"started", "not_started", "unknown"}:
            raise EvaluationExecutionError(
                "evaluation_start_outcome_invalid"
            )
        if frozen.execution_kind == "managed_process" and receipt.outcome == "started":
            if (
                not receipt.job_identity
                or receipt.pid is None
                or receipt.process_create_time is None
                or not receipt.command_line_hash
            ):
                raise EvaluationExecutionError(
                    "evaluation_start_ack_incomplete"
                )

    @staticmethod
    def _start_ack(
        launch_id: str, receipt: ManagedProcessStartReceipt
    ) -> EvaluationCaseStartAckV1:
        facts = {
            "schema_version": 1,
            "launch_id": launch_id,
            "runtime_instance_id": receipt.runtime_instance_id,
            "adapter_identity": receipt.adapter_identity,
            "job_identity": receipt.job_identity,
            "pid": receipt.pid,
            "process_create_time": receipt.process_create_time,
            "command_line_hash": receipt.command_line_hash,
        }
        return EvaluationCaseStartAckV1(
            launch_id=launch_id,
            runtime_instance_id=receipt.runtime_instance_id,
            adapter_identity=receipt.adapter_identity,
            job_identity=receipt.job_identity,
            pid=receipt.pid,
            process_create_time=receipt.process_create_time,
            command_line_hash=receipt.command_line_hash,
            start_receipt_hash=canonical_hash(facts),
        )

    async def _recover_only(
        self,
        owner: OwnerRef,
        frozen: FrozenEvaluationCaseV1,
        *,
        claim_owner: str,
        claim_epoch: int,
    ) -> EvaluationCaseExecutionOutcomeV1:
        reader = getattr(
            self._store, "get_evaluation_case_recovery_state", None
        )
        if not callable(reader):
            return EvaluationCaseExecutionOutcomeV1(
                status="recovery_only",
                reason_code="evaluation_recovery_api_unavailable",
            )
        state = reader(
            owner,
            evaluation_id=frozen.evaluation_id,
            case_id=frozen.case_id,
            variant=frozen.variant,
            claim_epoch=claim_epoch,
        )
        # A recovery worker never creates a new launch.  Reconciliation of a
        # durable completed/not-started/started row is a separate store-owned
        # operation because the old claim epoch must be settled, not guessed.
        reason = (
            "evaluation_recovery_state_missing"
            if state is None
            else f"evaluation_recovery_{state.get('launch_status', 'unknown')}"
        )
        del claim_owner
        return EvaluationCaseExecutionOutcomeV1(
            status="recovery_only",
            reason_code=reason,
            launch_id=(
                None
                if state is None or state.get("launch_id") is None
                else str(state["launch_id"])
            ),
        )

    async def _abort_without_ack(
        self,
        owner: OwnerRef,
        frozen: FrozenEvaluationCaseV1,
        *,
        claim_owner: str,
        claim_epoch: int,
        launch_id: str,
        expected_launch_status: str,
        port: ManagedProcessStartPort,
        runtime_instance_id: str,
        reason_code: str,
    ) -> None:
        probe = ManagedProcessStartReceipt(
            outcome="unknown",
            runtime_instance_id=runtime_instance_id,
            adapter_identity="evaluation-unacknowledged",
            reason_code=reason_code,
        )
        cleanup_hash, survivors = await self._verify_absent(
            port, probe, runtime_instance_id=runtime_instance_id
        )
        aborter = getattr(
            self._store, "abort_evaluation_case_execution", None
        )
        if callable(aborter):
            aborter(
                owner,
                evaluation_id=frozen.evaluation_id,
                case_id=frozen.case_id,
                variant=frozen.variant,
                claim_owner=claim_owner,
                claim_epoch=claim_epoch,
                launch_id=launch_id,
                expected_launch_status=expected_launch_status,
                cleanup_receipt_hash=cleanup_hash,
                survivor_count=survivors,
                reason_code=reason_code,
            )

    @staticmethod
    async def _abort_started(
        port: ManagedProcessStartPort,
        receipt: ManagedProcessStartReceipt,
        *,
        runtime_instance_id: str,
    ) -> tuple[str | None, int | None]:
        await port.abort_exact(receipt)
        return await FenceAwareEvaluationCaseExecutor._verify_absent(
            port, receipt, runtime_instance_id=runtime_instance_id
        )

    @staticmethod
    async def _verify_absent(
        port: ManagedProcessStartPort,
        receipt: ManagedProcessStartReceipt,
        *,
        runtime_instance_id: str,
    ) -> tuple[str | None, int | None]:
        start_identity = {
            "job_identity": receipt.job_identity,
            "pid": receipt.pid,
            "process_create_time": receipt.process_create_time,
            "command_line_hash": receipt.command_line_hash,
        }
        recovered = await port.recover_exact(
            runtime_instance_id=runtime_instance_id,
            start_identity=start_identity,
        )
        if recovered is not None:
            return None, None
        cleanup = {
            "schema_version": 1,
            "runtime_instance_id": runtime_instance_id,
            "start_identity": start_identity,
            "survivor_count": 0,
        }
        return canonical_hash(cleanup), 0


def _int_field(row: Mapping[str, Any], key: str) -> int:
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise EvaluationExecutionError(
            f"evaluation_claim_{key}_missing"
        ) from exc


__all__ = [
    "EvaluationCaseCompletionV1",
    "EvaluationCaseExecutionOutcomeV1",
    "EvaluationCaseStartAckV1",
    "EvaluationExecutionError",
    "EvaluationExecutionFenceV1",
    "EvaluationExecutionStorePort",
    "FenceAwareEvaluationCaseExecutor",
    "FrozenEvaluationCaseV1",
]
