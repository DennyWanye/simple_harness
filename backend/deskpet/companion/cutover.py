# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Recoverable cutover into the single Companion growth authority."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, cast

from .authority import (
    GrowthAuthorityPhase,
    GrowthAuthorityRouter,
    GrowthIngressKind,
    GrowthIngressRequest,
    GrowthIngressUnavailable,
)
from .store import canonical_hash


DEFAULT_PRE_MARKER_STEPS = (
    "legacy_import",
    "inactive_stage",
    "preflight",
)
DEFAULT_POST_MARKER_STEPS = (
    "capability_publish_intent",
    "owner_projection_reconcile",
    "handler_switch",
    "companion_services_ready",
    "final_integrity_verify",
)


class GrowthCutoverError(RuntimeError):
    pass


class GrowthCutoverPlanConflict(GrowthCutoverError):
    pass


class GrowthCutoverStepFailed(GrowthCutoverError):
    pass


@dataclass(frozen=True, slots=True)
class GrowthCutoverPlan:
    cutover_operation_id: str
    migration_version: int
    migration_hash: str
    legacy_owner_profile_id: str
    legacy_owner_generation: int
    old_binding_generation: int
    new_binding_generation: int
    old_owner_binding_set_stamp: str
    new_owner_binding_set_stamp: str
    step_hashes: Mapping[str, str]
    source_owner_policy: str = "legacy_local_only"
    pre_marker_steps: Sequence[str] = DEFAULT_PRE_MARKER_STEPS
    post_marker_steps: Sequence[str] = DEFAULT_POST_MARKER_STEPS
    plan_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            not self.cutover_operation_id
            or self.migration_version < 1
            or not self.migration_hash
            or not self.legacy_owner_profile_id
            or self.legacy_owner_generation < 1
        ):
            raise ValueError("growth cutover identity is incomplete")
        if self.source_owner_policy != "legacy_local_only":
            raise ValueError("legacy migration must remain local-owner-only")
        if self.new_binding_generation < self.old_binding_generation:
            raise ValueError("binding generation cannot move backwards")
        pre = tuple(str(item) for item in self.pre_marker_steps)
        post = tuple(str(item) for item in self.post_marker_steps)
        if (
            pre != DEFAULT_PRE_MARKER_STEPS
            or post != DEFAULT_POST_MARKER_STEPS
            or len(set(pre + post)) != len(pre + post)
        ):
            raise ValueError("growth cutover step order is fixed")
        hashes = {str(key): str(value) for key, value in self.step_hashes.items()}
        if set(hashes) != set(pre + post) or any(not value for value in hashes.values()):
            raise ValueError("every cutover step requires one exact hash")
        object.__setattr__(self, "pre_marker_steps", pre)
        object.__setattr__(self, "post_marker_steps", post)
        object.__setattr__(self, "step_hashes", MappingProxyType(hashes))
        object.__setattr__(
            self,
            "plan_hash",
            canonical_hash(
                {
                    "schema_version": 1,
                    "cutover_operation_id": self.cutover_operation_id,
                    "migration_version": self.migration_version,
                    "migration_hash": self.migration_hash,
                    "legacy_owner": [
                        self.legacy_owner_profile_id,
                        self.legacy_owner_generation,
                    ],
                    "source_owner_policy": self.source_owner_policy,
                    "old_binding_generation": self.old_binding_generation,
                    "new_binding_generation": self.new_binding_generation,
                    "old_owner_binding_set_stamp":
                        self.old_owner_binding_set_stamp,
                    "new_owner_binding_set_stamp":
                        self.new_owner_binding_set_stamp,
                    "steps": [
                        [name, hashes[name]] for name in pre + post
                    ],
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class GrowthCutoverStepAuthorization:
    cutover_operation_id: str
    plan_hash: str
    step: str
    expected_step_hash: str
    marker_committed: bool
    legacy_owner_profile_id: str
    legacy_owner_generation: int
    source_owner_policy: str
    old_binding_generation: int
    new_binding_generation: int
    old_owner_binding_set_stamp: str
    new_owner_binding_set_stamp: str


class GrowthCutoverStepExecutorPort(Protocol):
    async def execute(
        self,
        authorization: GrowthCutoverStepAuthorization,
    ) -> Mapping[str, Any]: ...


class GrowthCutoverStorePort(Protocol):
    def list_growth_authority_cutover_journal(
        self,
        *,
        cutover_operation_id: str,
    ) -> Sequence[Mapping[str, Any]]: ...

    def record_growth_authority_cutover_step(
        self,
        *,
        expected_phase: str,
        expected_generation: int,
        cutover_operation_id: str,
        substep_phase: str,
        substep_hash: str,
        marker_committed: bool,
        journal_payload: Mapping[str, Any],
        reason_code: str,
    ) -> Mapping[str, Any]: ...


GrowthHandler = Callable[
    [GrowthIngressRequest],
    object | Awaitable[object],
]


class CompanionGrowthAuthorityAdapter:
    """Typed Task-4 handlers behind the existing GrowthAuthorityRouter."""

    def __init__(
        self,
        *,
        readers: Mapping[GrowthIngressKind, GrowthHandler],
        writers: Mapping[GrowthIngressKind, GrowthHandler],
    ) -> None:
        self._readers = MappingProxyType(dict(readers))
        self._writers = MappingProxyType(dict(writers))
        if any(kind.is_write for kind in self._readers):
            raise ValueError("write ingress cannot be registered as a reader")
        if any(not kind.is_write for kind in self._writers):
            raise ValueError("read ingress cannot be registered as a writer")

    async def read(self, request: GrowthIngressRequest) -> object:
        if request.kind.is_write:
            raise GrowthIngressUnavailable("write request reached Companion reader")
        return await self._invoke(self._readers, request)

    async def write(self, request: GrowthIngressRequest) -> object:
        if not request.kind.is_write:
            raise GrowthIngressUnavailable("read request reached Companion writer")
        return await self._invoke(self._writers, request)

    @staticmethod
    async def _invoke(
        handlers: Mapping[GrowthIngressKind, GrowthHandler],
        request: GrowthIngressRequest,
    ) -> object:
        handler = handlers.get(request.kind)
        if handler is None:
            raise GrowthIngressUnavailable(
                f"Companion handler unavailable: {request.kind.value}"
            )
        result = handler(request)
        if inspect.isawaitable(result):
            return await cast(Awaitable[object], result)
        return result


async def _await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class GrowthAuthorityCutoverCoordinator:
    """Execute or recover the one irreversible authority cutover."""

    def __init__(
        self,
        *,
        router: GrowthAuthorityRouter,
        store: GrowthCutoverStorePort,
        executor: GrowthCutoverStepExecutorPort,
    ) -> None:
        self._router = router
        self._store = store
        self._executor = executor

    async def execute(self, plan: GrowthCutoverPlan):
        async with self._router.cutover_session() as session:
            try:
                state = session.current
                if state.phase is GrowthAuthorityPhase.COMPANION:
                    self._require_same_operation(state, plan)
                    return state
                if state.phase is GrowthAuthorityPhase.LEGACY:
                    state = await session.transition(
                        GrowthAuthorityPhase.PREPARING,
                        expected_generation=state.generation,
                        marker_committed=False,
                        reason="growth_cutover_ingress_drained",
                        journal_payload={
                            **self._journal_facts(plan),
                            "substep_phase": "ingress_drain",
                            "substep_hash": canonical_hash(
                                [plan.plan_hash, "ingress_drain"]
                            ),
                            "drain_state": "completed",
                        },
                    )
                else:
                    self._require_same_operation(state, plan)
                completed = await self._completed_steps(plan)
                if not state.roll_forward_required:
                    if state.phase is not GrowthAuthorityPhase.PREPARING:
                        raise GrowthCutoverPlanConflict(
                            "pre-marker recovery is not preparing"
                        )
                    for step in plan.pre_marker_steps:
                        await self._run_step(
                            session=session,
                            plan=plan,
                            step=step,
                            marker_committed=False,
                            completed=completed,
                        )
                    state = await session.transition(
                        GrowthAuthorityPhase.PREPARING,
                        expected_generation=state.generation,
                        marker_committed=True,
                        reason="growth_cutover_roll_forward_marker_committed",
                        journal_payload={
                            **self._journal_facts(plan),
                            "marker_hash": canonical_hash(
                                [plan.plan_hash, "roll_forward_required"]
                            ),
                            "substep_phase": "roll_forward_marker",
                            "substep_hash": canonical_hash(
                                [plan.plan_hash, "roll_forward_marker"]
                            ),
                            "drain_state": "completed",
                        },
                    )
                for step in plan.post_marker_steps:
                    await self._run_step(
                        session=session,
                        plan=plan,
                        step=step,
                        marker_committed=True,
                        completed=completed,
                    )
                return await session.transition(
                    GrowthAuthorityPhase.COMPANION,
                    expected_generation=session.current.generation,
                    marker_committed=True,
                    preflight_passed=True,
                    reason="growth_cutover_companion_pointer_committed",
                    journal_payload={
                        **self._journal_facts(plan),
                        "substep_phase": "companion_pointer",
                        "substep_hash": canonical_hash(
                            [plan.plan_hash, "companion_pointer"]
                        ),
                        "drain_state": "completed",
                        "preflight_passed": True,
                    },
                )
            except Exception as exc:
                await self._settle_failure(session, plan, exc)
                raise

    async def _run_step(
        self,
        *,
        session: Any,
        plan: GrowthCutoverPlan,
        step: str,
        marker_committed: bool,
        completed: Mapping[str, tuple[str, str]],
    ) -> None:
        expected_hash = plan.step_hashes[step]
        existing = completed.get(step)
        if existing is not None:
            if (
                not isinstance(existing, Sequence)
                or isinstance(existing, (str, bytes))
                or len(existing) != 2
                or str(existing[0]) != expected_hash
                or not str(existing[1])
            ):
                raise GrowthCutoverPlanConflict(
                    f"cutover step proof drift: {step}"
                )
            return
        authorization = GrowthCutoverStepAuthorization(
            cutover_operation_id=plan.cutover_operation_id,
            plan_hash=plan.plan_hash,
            step=step,
            expected_step_hash=expected_hash,
            marker_committed=marker_committed,
            legacy_owner_profile_id=plan.legacy_owner_profile_id,
            legacy_owner_generation=plan.legacy_owner_generation,
            source_owner_policy=plan.source_owner_policy,
            old_binding_generation=plan.old_binding_generation,
            new_binding_generation=plan.new_binding_generation,
            old_owner_binding_set_stamp=plan.old_owner_binding_set_stamp,
            new_owner_binding_set_stamp=plan.new_owner_binding_set_stamp,
        )
        raw = await _await_if_needed(self._executor.execute(authorization))
        if not isinstance(raw, Mapping):
            raise GrowthCutoverStepFailed(
                f"cutover step returned no structured proof: {step}"
            )
        actual_hash = str(raw.get("receipt_hash") or "")
        result_hash = str(raw.get("result_hash") or "")
        if actual_hash != expected_hash:
            raise GrowthCutoverStepFailed(
                f"cutover step receipt mismatch: {step}"
            )
        if not result_hash:
            raise GrowthCutoverStepFailed(
                f"cutover step result proof missing: {step}"
            )
        await _await_if_needed(
            self._store.record_growth_authority_cutover_step(
                expected_phase=session.current.phase.value,
                expected_generation=session.current.generation,
                cutover_operation_id=plan.cutover_operation_id,
                substep_phase=step,
                substep_hash=actual_hash,
                marker_committed=marker_committed,
                journal_payload={
                    **self._journal_facts(plan),
                    "substep_phase": step,
                    "substep_hash": actual_hash,
                    "result_hash": result_hash,
                    "drain_state": "completed",
                },
                reason_code=f"growth_cutover_{step}_committed",
            )
        )

    async def _completed_steps(
        self, plan: GrowthCutoverPlan
    ) -> Mapping[str, tuple[str, str]]:
        rows = await _await_if_needed(
            self._store.list_growth_authority_cutover_journal(
                cutover_operation_id=plan.cutover_operation_id
            )
        )
        completed: dict[str, tuple[str, str]] = {}
        known = set(plan.pre_marker_steps) | set(plan.post_marker_steps)
        for row in rows:
            step = str(row.get("substep_phase") or "")
            if step not in known:
                continue
            value = str(row.get("substep_hash") or "")
            payload = row.get("journal_payload")
            result_hash = (
                str(payload.get("result_hash") or "")
                if isinstance(payload, Mapping)
                else ""
            )
            if value and not result_hash and isinstance(payload, Mapping):
                result_hash = self._legacy_committed_result_hash(
                    plan=plan,
                    step=step,
                    row=row,
                    payload=payload,
                    step_hash=value,
                )
            proof = (value, result_hash)
            if not value or not result_hash:
                raise GrowthCutoverPlanConflict(
                    f"cutover step proof incomplete: {step}"
                )
            prior = completed.setdefault(step, proof)
            if prior != proof:
                raise GrowthCutoverPlanConflict(
                    f"multiple proofs recorded for cutover step: {step}"
                )
        return MappingProxyType(completed)

    @classmethod
    def _legacy_committed_result_hash(
        cls,
        *,
        plan: GrowthCutoverPlan,
        step: str,
        row: Mapping[str, Any],
        payload: Mapping[str, Any],
        step_hash: str,
    ) -> str:
        """Recognize receipts written before result_hash became mandatory."""

        expected_marker = step in plan.post_marker_steps
        # Capability binding generations are global and may legitimately
        # advance for unrelated owners while a post-marker cutover is paused.
        # Step hashes intentionally bind only operation, migration, and step,
        # so legacy receipts remain valid across that catalog movement.
        immutable_facts = {
            "cutover_operation_id": plan.cutover_operation_id,
            "migration_version": plan.migration_version,
            "migration_hash": plan.migration_hash,
            "source_owner_policy": plan.source_owner_policy,
            "legacy_owner_profile_id": plan.legacy_owner_profile_id,
            "legacy_owner_generation": plan.legacy_owner_generation,
        }
        facts_match = all(
            payload.get(key) == value
            for key, value in immutable_facts.items()
        )
        recorded_plan_hash = str(payload.get("plan_hash") or "")
        if not (
            step_hash == plan.step_hashes[step]
            and bool(recorded_plan_hash)
            and str(row.get("cutover_operation_id") or "")
            == plan.cutover_operation_id
            and str(row.get("reason_code") or "")
            == f"growth_cutover_{step}_committed"
            and bool(row.get("marker_committed")) is expected_marker
            and str(payload.get("substep_phase") or "") == step
            and str(payload.get("substep_hash") or "") == step_hash
            and str(payload.get("drain_state") or "") == "completed"
            and facts_match
        ):
            return ""
        return canonical_hash(
            {
                "schema_version": 1,
                "kind": "legacy_committed_cutover_receipt",
                "cutover_operation_id": plan.cutover_operation_id,
                "recorded_plan_hash": recorded_plan_hash,
                "step": step,
                "step_hash": step_hash,
                "marker_committed": expected_marker,
            }
        )

    async def _settle_failure(
        self,
        session: Any,
        plan: GrowthCutoverPlan,
        exc: Exception,
    ) -> None:
        state = session.current
        payload = {
            **self._journal_facts(plan),
            "last_error": type(exc).__name__,
            "error_hash": canonical_hash(
                [type(exc).__name__, str(exc), plan.plan_hash]
            ),
            "drain_state": "completed",
        }
        if (
            state.phase is GrowthAuthorityPhase.PREPARING
            and not state.roll_forward_required
        ):
            await session.transition(
                GrowthAuthorityPhase.LEGACY,
                expected_generation=state.generation,
                marker_committed=False,
                reason="growth_cutover_pre_marker_aborted",
                journal_payload=payload,
            )
            return
        if state.roll_forward_required and state.phase is not GrowthAuthorityPhase.PAUSED:
            await session.transition(
                GrowthAuthorityPhase.PAUSED,
                expected_generation=state.generation,
                marker_committed=True,
                reason="growth_cutover_roll_forward_paused",
                journal_payload=payload,
            )

    @staticmethod
    def _require_same_operation(state: Any, plan: GrowthCutoverPlan) -> None:
        if (
            str(state.cutover_operation_id or "")
            != plan.cutover_operation_id
            or (
                state.migration_hash is not None
                and str(state.migration_hash) != plan.migration_hash
            )
        ):
            raise GrowthCutoverPlanConflict(
                "durable cutover belongs to another operation"
            )
        if (
            state.phase is GrowthAuthorityPhase.PAUSED
            and not state.roll_forward_required
        ):
            raise GrowthCutoverPlanConflict(
                "paused pre-marker authority cannot guess a direction"
            )

    @staticmethod
    def _journal_facts(plan: GrowthCutoverPlan) -> dict[str, object]:
        return {
            "cutover_operation_id": plan.cutover_operation_id,
            "migration_version": plan.migration_version,
            "migration_hash": plan.migration_hash,
            "plan_hash": plan.plan_hash,
            "source_owner_policy": plan.source_owner_policy,
            "legacy_owner_profile_id": plan.legacy_owner_profile_id,
            "legacy_owner_generation": plan.legacy_owner_generation,
            "old_binding_generation": plan.old_binding_generation,
            "new_binding_generation": plan.new_binding_generation,
            "old_owner_binding_set_stamp": plan.old_owner_binding_set_stamp,
            "new_owner_binding_set_stamp": plan.new_owner_binding_set_stamp,
        }


__all__ = [
    "CompanionGrowthAuthorityAdapter",
    "DEFAULT_POST_MARKER_STEPS",
    "DEFAULT_PRE_MARKER_STEPS",
    "GrowthAuthorityCutoverCoordinator",
    "GrowthCutoverError",
    "GrowthCutoverPlan",
    "GrowthCutoverPlanConflict",
    "GrowthCutoverStepAuthorization",
    "GrowthCutoverStepExecutorPort",
    "GrowthCutoverStepFailed",
]
