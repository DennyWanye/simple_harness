"""One durable owner for prepared tool batches and effect reconciliation."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Sequence

from deskpet.execution.contracts import (
    ActorContext,
    DecisionAuthorization,
    GrantConsume,
    OutcomeStatus,
    PersistenceLevel,
    RecoveryLease,
    RunRecord,
    StaleRecoveryLease,
    IdempotencyConflict,
    VersionConflict,
    fingerprint_json,
)
from deskpet.execution.ports import (
    CurrentExecutionScopeLease,
    CurrentExecutionScopeLeasePort,
)
from deskpet.execution.uow_ports import ToolExecutionUnitOfWork
from deskpet.execution.dispatch import (
    PreparedToolDispatch,
    PreparedToolDispatchNotStarted,
    PreparedToolDispatchStartedAck,
    PreparedToolDispatchStartUnknown,
    dispatch_with_run_fence,
)
from deskpet.execution.fences import validate_run_execution_epoch
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.registry import ToolRegistry
from deskpet.workflows.effects import NormalizedToolOutcome, PreparedToolCall

from .ports import ExecuteTools, ToolOutcomesSignal


@dataclass(frozen=True, slots=True)
class EffectBatch:
    signal: ToolOutcomesSignal
    ready_refs: tuple[tuple[PreparedToolCall, ToolExecutionContext], ...]


class _SharedExecutionScopeState:
    def __init__(
        self, lease: CurrentExecutionScopeLease, remaining: int
    ) -> None:
        self.lease = lease
        self.remaining = remaining
        self.released = False

    async def release_one(self) -> None:
        if self.released:
            return
        self.remaining -= 1
        if self.remaining <= 0:
            self.released = True
            await self.lease.release()


class _SharedExecutionScopeRef:
    def __init__(self, state: _SharedExecutionScopeState) -> None:
        self._state = state
        self._released = False
        self.owner_key = state.lease.owner_key
        self.profile_generation = state.lease.profile_generation
        self.binding_epoch = state.lease.binding_epoch
        self.capability_hash = state.lease.capability_hash
        self.scope_hash = state.lease.scope_hash

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._state.release_one()


@dataclass(slots=True)
class _ResourceExecutionLock:
    lock: asyncio.Lock
    users: int = 0


class _ResourceExecutionCoordinator:
    """Serialize prepared calls that name the same exclusive resource.

    Prepared resource selectors are frozen into the durable call, so the
    scheduler never has to reinterpret model arguments at execution time.
    Desktop targets are always serialized, including observe-only captures,
    because their ordering relative to focus/input is itself meaningful.
    """

    _EXCLUSIVE_ACCESS = frozenset(
        {"append", "control", "execute", "input", "manage", "mutate", "write"}
    )

    def __init__(self) -> None:
        self._locks: dict[tuple[str, str], _ResourceExecutionLock] = {}

    @classmethod
    def _keys(cls, call: PreparedToolCall) -> tuple[tuple[str, str], ...]:
        keys = {
            (selector.kind, selector.canonical_value)
            for selector in call.resource_selectors
            if selector.kind == "desktop_target"
            or bool(cls._EXCLUSIVE_ACCESS.intersection(selector.access))
        }
        return tuple(sorted(keys))

    async def acquire(
        self, call: PreparedToolCall
    ) -> tuple[tuple[tuple[str, str], _ResourceExecutionLock], ...]:
        acquired: list[tuple[tuple[str, str], _ResourceExecutionLock]] = []
        try:
            for key in self._keys(call):
                state = self._locks.get(key)
                if state is None:
                    state = _ResourceExecutionLock(asyncio.Lock())
                    self._locks[key] = state
                state.users += 1
                try:
                    await state.lock.acquire()
                except BaseException:
                    state.users -= 1
                    if state.users == 0 and self._locks.get(key) is state:
                        self._locks.pop(key, None)
                    raise
                acquired.append((key, state))
        except BaseException:
            self.release(acquired)
            raise
        return tuple(acquired)

    def release(
        self,
        locks: Sequence[tuple[tuple[str, str], _ResourceExecutionLock]],
    ) -> None:
        for key, state in reversed(locks):
            state.lock.release()
            state.users -= 1
            if state.users == 0 and self._locks.get(key) is state:
                self._locks.pop(key, None)


class EffectBatchExecutor:
    """Claim, execute and reconcile one ordered batch; Drivers settle boundaries."""

    def __init__(
        self,
        uow: ToolExecutionUnitOfWork,
        registry: ToolRegistry,
        *,
        provider_fence_acquirer: object | None = None,
        current_execution_scope_port: CurrentExecutionScopeLeasePort | None = None,
        dispatch_start_timeout: float = 5.0,
    ) -> None:
        self._uow, self._registry = uow, registry
        self._provider_fence_acquirer = provider_fence_acquirer
        self._current_execution_scope_port = current_execution_scope_port
        self._dispatch_start_timeout = max(0.05, float(dispatch_start_timeout))
        self._resource_execution = _ResourceExecutionCoordinator()

    async def _release_execution_scope(
        self, lease: CurrentExecutionScopeLease | None
    ) -> None:
        if lease is not None:
            await lease.release()

    async def _converge_cancelled_started_dispatch(
        self,
        *,
        dispatch: PreparedToolDispatch,
        start_result: PreparedToolDispatchStartedAck,
        context: ToolExecutionContext,
        claim: dict[str, object],
        metadata: dict[str, object],
    ) -> None:
        """Persist may-complete from the latest durable handoff version."""

        metadata["late_pending"] = True
        mark_unknown = getattr(
            self._uow, "mark_effect_inflight_may_complete", None
        )
        if not callable(mark_unknown):
            return
        read_handoff = getattr(self._uow, "read_effect_handoff", None)
        receipt_ref = (
            f"tool-dispatch-consumer-cancelled:{context.run_id}:"
            f"{context.call_id}:{context.effect_id}"
        )
        receipt_hash = fingerprint_json(
            {
                "receipt_ref": receipt_ref,
                "request_hash": dispatch.request_hash,
                "started_ack_ref": start_result.ack_ref,
            }
        )
        for _attempt in range(3):
            attempt_no = int(claim["attempt_no"])
            effect_version = int(claim["effect_version"])
            if callable(read_handoff):
                current = await read_handoff(context.effect_id)
                if current is None:
                    return
                claim["attempt_no"] = int(current.attempt_no)
                claim["effect_version"] = int(current.effect_version)
                attempt_no = int(current.attempt_no)
                effect_version = int(current.effect_version)
                if (
                    current.status == "unknown"
                    and current.handoff_state == "started_may_complete"
                ):
                    return
                if current.status != "running" or current.handoff_state not in {
                    "unresolved",
                    "started",
                }:
                    return
            try:
                handoff = await mark_unknown(
                    context.effect_id,
                    attempt_no,
                    effect_version,
                    receipt_ref,
                    receipt_hash,
                )
            except (VersionConflict, IdempotencyConflict):
                if callable(read_handoff):
                    continue
                return
            claim["effect_version"] = int(handoff.effect_version)
            return

    async def _verify_trusted_tool_refs(
        self,
        record: RunRecord,
        calls: Sequence[PreparedToolCall],
        contexts: Sequence[ToolExecutionContext],
    ) -> None:
        """Recompute frozen Skill membership before any grant/effect claim."""

        if len(calls) != len(contexts):
            raise ValueError("prepared calls and trusted contexts must align")
        if not contexts:
            return
        needs_snapshot = any(
            context.capability_snapshot_ref
            or context.active_skill_scope_ids
            or context.effective_skill_tool_ref_hashes
            or context.effective_skill_tool_refs_hash
            for context in contexts
        )
        if not needs_snapshot:
            return
        start = await self._uow.read_run_start_snapshot(record.run_id)
        if start is None:
            raise ValueError(
                "prepared tool batch has no trusted RunStart snapshot"
            )
        capability_snapshot = json.loads(start.capability_snapshot_json)
        if (
            not isinstance(capability_snapshot, Mapping)
            or fingerprint_json(capability_snapshot)
            != start.capability_snapshot_hash
        ):
            raise ValueError("RunStart capability snapshot is corrupt")
        snapshot_ref = str(
            capability_snapshot.get("catalog_snapshot_ref") or ""
        )
        if any(
            context.capability_snapshot_ref != snapshot_ref
            or call.catalog_snapshot_ref != snapshot_ref
            for call, context in zip(calls, contexts)
        ):
            raise ValueError(
                "prepared call capability snapshot binding drifted"
            )
        first = contexts[0]
        skill_identity = (
            first.active_skill_scope_ids,
            first.effective_skill_tool_ref_hashes,
            first.effective_skill_tool_refs_hash,
        )
        if any(
            (
                context.active_skill_scope_ids,
                context.effective_skill_tool_ref_hashes,
                context.effective_skill_tool_refs_hash,
            )
            != skill_identity
            for context in contexts[1:]
        ):
            raise ValueError("prepared tool batch mixes Skill ToolRef scopes")
        if not first.active_skill_scope_ids:
            if (
                first.effective_skill_tool_ref_hashes
                or first.effective_skill_tool_refs_hash
            ):
                raise ValueError(
                    "inactive Skill scope carries effective ToolRefs"
                )
            return
        sanitized = json.loads(start.sanitized_request_json)
        request_payload = (
            sanitized.get("payload")
            if isinstance(sanitized, Mapping)
            else None
        )
        if not isinstance(request_payload, Mapping):
            raise ValueError(
                "RunStart request payload cannot rebuild Skill ToolRefs"
            )
        from deskpet.harness.skill_scope import (
            skill_tool_intersection_from_snapshot,
        )

        activated_scopes: tuple[Mapping[str, Any], ...] = ()
        selection = (
            capability_snapshot.get("host_extensions", {}).get(
                "deskpet.companion.selection.v1"
            )
            if isinstance(
                capability_snapshot.get("host_extensions"), Mapping
            )
            else None
        )
        selected_scope_ids = {
            str(item.get("scope_id") or "")
            for item in (
                selection.get("skill_invocation_scopes", ())
                if isinstance(selection, Mapping)
                else ()
            )
            if isinstance(item, Mapping)
        }
        dynamic_scope_ids = (
            set(first.active_skill_scope_ids) - selected_scope_ids
        )
        if dynamic_scope_ids:
            continuation = await self._uow.load_continuation(record.run_id)
            payload = (
                continuation.payload
                if continuation is not None
                else None
            )
            raw_activated = (
                payload.get("activated_skill_scopes", ())
                if isinstance(payload, Mapping)
                else ()
            )
            if isinstance(raw_activated, (str, bytes)) or not isinstance(
                raw_activated, (list, tuple)
            ):
                raise ValueError(
                    "dynamic Skill scopes have no trusted continuation"
                )
            by_scope = {
                str(item.get("scope_id") or ""): dict(item)
                for item in raw_activated
                if isinstance(item, Mapping)
            }
            if not dynamic_scope_ids.issubset(by_scope):
                raise ValueError(
                    "dynamic Skill scope is absent from the continuation"
                )
            verified: list[Mapping[str, Any]] = []
            for scope_id in sorted(dynamic_scope_ids):
                scope = by_scope[scope_id]
                activation_id = str(
                    scope.get("activation_id") or ""
                )
                receipt = await self._uow.get_skill_scope_activation(
                    activation_id
                )
                if receipt is None:
                    raise ValueError(
                        "dynamic Skill scope has no committed receipt"
                    )
                allowed_refs = json.loads(
                    str(receipt["allowed_tool_refs_json"])
                )
                if (
                    str(receipt["run_id"]) != record.run_id
                    or str(receipt["scope_id"]) != scope_id
                    or str(receipt["scope_hash"])
                    != str(scope.get("scope_hash") or "")
                    or str(receipt["capability_snapshot_ref"])
                    != snapshot_ref
                    or str(receipt["status"]) != "committed"
                    or int(receipt["continuation_version"])
                    > int(continuation.version)
                    or allowed_refs
                    != list(scope.get("allowed_tool_refs", ()))
                    or json.loads(
                        str(receipt["allowed_tool_names_json"])
                    )
                    != list(scope.get("allowed_tools", ()))
                    or str(receipt["instruction_content_hash"])
                    != str(
                        scope.get("instruction_content_hash") or ""
                    )
                ):
                    raise ValueError(
                        "dynamic Skill scope receipt identity drifted"
                    )
                verified.append(scope)
            activated_scopes = tuple(verified)
        intersection = skill_tool_intersection_from_snapshot(
            capability_snapshot,
            request_payload,
            active_scope_ids=first.active_skill_scope_ids,
            activated_scopes=activated_scopes,
        )
        if intersection is None:
            raise ValueError("active Skill scope has no ToolRef intersection")
        if (
            first.effective_skill_tool_ref_hashes
            != intersection.effective_tool_ref_hashes
            or first.effective_skill_tool_refs_hash
            != intersection.effective_tool_refs_hash
        ):
            raise ValueError("effective Skill ToolRef membership drifted")
        exact_refs = {
            ref.name: (ref, dict(fact), fingerprint_json(dict(fact)))
            for ref, fact in zip(
                intersection.effective_tool_refs,
                intersection.effective_tool_fact_payloads,
            )
        }
        membership = frozenset(first.effective_skill_tool_ref_hashes)
        for call in calls:
            item = exact_refs.get(call.tool_name)
            if item is None or item[2] not in membership:
                raise ValueError(
                    f"prepared call is outside active Skill ToolRefs: "
                    f"{call.tool_name}"
                )
            ref = item[0]
            fact = item[1]
            effect = fact.get("effect_policy")
            if effect is not None and not isinstance(effect, Mapping):
                raise ValueError("captured exact Tool effect policy is invalid")
            expected_effect_type = (
                "opaque_manual"
                if effect is None
                else str(effect.get("kind") or "")
            )
            expected_effect_version = (
                ""
                if effect is None
                else str(effect.get("version") or "")
            )
            if (
                call.tool_spec_fingerprint
                != str(fact.get("tool_spec_fingerprint") or "")
                or call.tool_spec_version != ref.spec_version
                or call.schema_hash != ref.schema_hash
                or call.permission_policy_version
                != ref.permission_policy_version
                or call.effect_type != expected_effect_type
                or call.effect_policy_version
                != expected_effect_version
            ):
                raise ValueError(
                    "prepared call differs from its exact Skill ToolRef"
                )

    async def _acquire_execution_scopes(
        self, contexts: Sequence[ToolExecutionContext]
    ) -> list[CurrentExecutionScopeLease | None]:
        if self._current_execution_scope_port is None:
            return [None] * len(contexts)
        if not contexts:
            return []
        first = contexts[0]
        identity = (
            first.run_id,
            first.owner_key,
            first.profile_generation,
            first.binding_epoch,
            first.capability_hash,
            first.scope_hash,
        )
        if any(
            (
                context.run_id,
                context.owner_key,
                context.profile_generation,
                context.binding_epoch,
                context.capability_hash,
                context.scope_hash,
            )
            != identity
            for context in contexts[1:]
        ):
            raise RuntimeError("prepared tool batch mixes execution scopes")
        lease: CurrentExecutionScopeLease | None = None
        try:
            if (
                not first.owner_key
                or first.profile_generation < 1
                or first.binding_epoch < 1
            ):
                raise RuntimeError("trusted execution owner identity is missing")
            lease = (
                await self._current_execution_scope_port.acquire_current_execution_scope(
                    run_id=first.run_id,
                    owner_key=first.owner_key,
                    profile_generation=first.profile_generation,
                    binding_epoch=first.binding_epoch,
                    capability_hash=first.capability_hash,
                    scope_hash=first.scope_hash,
                )
            )
            shared = _SharedExecutionScopeState(lease, len(contexts))
            return [
                _SharedExecutionScopeRef(shared)
                for _ in contexts
            ]
        except BaseException:
            if lease is not None:
                await self._release_execution_scope(lease)
            raise

    async def _revalidate_execution_scope(
        self, context: ToolExecutionContext
    ) -> None:
        if self._current_execution_scope_port is None:
            return
        lease = await self._current_execution_scope_port.acquire_current_execution_scope(
            run_id=context.run_id,
            owner_key=context.owner_key,
            profile_generation=context.profile_generation,
            binding_epoch=context.binding_epoch,
            capability_hash=context.capability_hash,
            scope_hash=context.scope_hash,
        )
        await lease.release()

    async def _abort_dispatches(
        self,
        dispatches: Sequence[PreparedToolDispatch | None],
        leases: Sequence[CurrentExecutionScopeLease | None],
    ) -> None:
        for dispatch in dispatches:
            if dispatch is not None:
                await dispatch.abort_unstarted()
        for lease in reversed(leases):
            await self._release_execution_scope(lease)

    def _validate_confirm_only_snapshot(
        self,
        command: ExecuteTools,
    ) -> tuple[bool, ...]:
        markers = tuple(
            bool(item)
            for item in getattr(
                command, "confirm_only", (False,) * len(command.calls)
            )
        )
        if len(markers) != len(command.calls):
            raise ValueError("confirm-only markers do not align with calls")
        if not any(markers):
            return markers
        snapshot_ref = str(
            getattr(command, "confirm_only_snapshot_ref", "") or ""
        )
        expected_hash = str(
            getattr(command, "confirm_only_snapshot_hash", "") or ""
        )
        scope_store = getattr(self._registry, "capability_scope_store", None)
        if (
            not snapshot_ref
            or not expected_hash
            or scope_store is None
            or not command.contexts
        ):
            raise ValueError("confirm-only scope snapshot is unavailable")
        first = command.contexts[0]
        if any(context.scope_id != first.scope_id for context in command.contexts):
            raise ValueError("confirm-only batch mixes tool scope snapshots")
        record = scope_store.get(
            first.scope_id,
            session_id=first.session_id,
            request_id=first.request_id,
        )
        if record is None:
            raise ValueError("confirm-only scope snapshot is unavailable")
        prepared = record.prepared
        actual_hash = fingerprint_json(
            {
                "snapshot_ref": snapshot_ref,
                "scope_id": prepared.scope_id,
                "revision": prepared.revision,
                "schema_fingerprint": prepared.schema_fingerprint,
                "effect_policy_version": prepared.effect_policy_version,
                "effect_policy_hash": prepared.effect_policy_hash,
                "confirm_only_names": list(prepared.confirm_only_names),
            }
        )
        if actual_hash != expected_hash:
            raise ValueError("confirm-only scope snapshot drifted")
        for marker, call in zip(markers, command.calls):
            if marker != (call.tool_name in prepared.confirm_only_names):
                raise ValueError("driver confirm-only policy does not match scope authority")
        return markers

    @staticmethod
    def _authorization(
        authorization: object | None,
        call: PreparedToolCall,
        context: ToolExecutionContext,
    ) -> object | None:
        if not isinstance(authorization, DecisionAuthorization):
            return authorization
        return {
            "grant_id": authorization.grant_id,
            "decision_id": authorization.decision_id,
            "run_id": authorization.run_id,
            "session_id": context.session_id,
            "call_id": call.stable_call_id,
            "effect_id": authorization.effect_id,
            "tool_name": authorization.tool_name,
            "args_hash": call.args_hash,
            "capability_hash": authorization.capability_hash,
            "scope_hash": authorization.scope_hash,
            "permission_policy_version": call.permission_policy_version,
            "expires_at": authorization.expires_at,
        }

    async def _execute_segmented(
        self,
        calls: Sequence[PreparedToolCall],
        contexts: Sequence[ToolExecutionContext],
        authorizations: Sequence[object | None],
        precomputed: Sequence[NormalizedToolOutcome | None],
        *,
        dispatches: Sequence[PreparedToolDispatch | None] | None = None,
        execution_scope_leases: Sequence[
            CurrentExecutionScopeLease | None
        ] | None = None,
        metadata: Sequence[dict[str, object]] | None = None,
    ) -> list[NormalizedToolOutcome]:
        results = list(precomputed)
        prepared_dispatches = list(dispatches or (None,) * len(calls))
        scope_leases = list(
            execution_scope_leases or (None,) * len(calls)
        )
        effect_metadata = list(metadata or ({},) * len(calls))

        async def execute_at(index: int) -> None:
            if results[index] is not None:
                return
            pinned_scope = None
            resource_locks: tuple[
                tuple[tuple[str, str], _ResourceExecutionLock], ...
            ] = ()
            start_result: object | None = None
            try:
                call, context = calls[index], contexts[index]
                resource_locks = await self._resource_execution.acquire(call)
                scope_store = getattr(
                    self._registry, "capability_scope_store", None
                )
                if scope_store is not None and context.scope_id:
                    pinned_scope = scope_store.pin(
                        context.scope_id,
                        session_id=context.session_id,
                        request_id=context.request_id,
                    )
                dispatch = prepared_dispatches[index]
                if dispatch is None:
                    results[index] = await dispatch_with_run_fence(
                        acquire_fence=self._provider_fence_acquirer,
                        run_id=context.run_id,
                        operation_kind="tool.execute_prepared",
                        operation_id=context.effect_id,
                        invoke=lambda: self._registry.execute_prepared(
                            call, effect_id=context.effect_id,
                            authorization=self._authorization(authorizations[index], call, context),
                            execution_context=context,
                        ),
                    )
                    await self._release_execution_scope(scope_leases[index])
                    scope_leases[index] = None
                    return

                start_result, dispatch_fence_epoch = await dispatch_with_run_fence(
                    acquire_fence=self._provider_fence_acquirer,
                    run_id=context.run_id,
                    operation_kind="tool.dispatch_start",
                    operation_id=context.effect_id,
                    invoke=lambda: dispatch.start(
                        deadline=time.time() + self._dispatch_start_timeout
                    ),
                    return_fence_epoch=True,
                )
                claim = effect_metadata[index].get("effect_claim")
                if isinstance(start_result, PreparedToolDispatchStartedAck):
                    if isinstance(claim, dict):
                        mark_started = getattr(
                            self._uow, "mark_effect_dispatch_started", None
                        )
                        if callable(mark_started):
                            handoff = await mark_started(
                                context.effect_id,
                                int(claim["attempt_no"]),
                                int(claim["effect_version"]),
                                start_result.ack_ref,
                                start_result.ack_hash,
                                ack_at=start_result.started_at,
                            )
                            claim["effect_version"] = int(
                                handoff.effect_version
                            )
                    await self._release_execution_scope(scope_leases[index])
                    scope_leases[index] = None
                    completed = await dispatch.completion()
                    try:
                        await validate_run_execution_epoch(
                            self._provider_fence_acquirer,
                            run_id=context.run_id,
                            expected_epoch=dispatch_fence_epoch,
                        )
                    except Exception:
                        effect_metadata[index]["late_pending"] = True
                        results[index] = NormalizedToolOutcome.malformed(
                            "completion suppressed by run execution fence"
                        )
                        return
                    results[index] = completed
                elif isinstance(start_result, PreparedToolDispatchNotStarted):
                    if isinstance(claim, dict):
                        mark_not_started = getattr(
                            self._uow,
                            "mark_effect_dispatch_not_started",
                            None,
                        )
                        if callable(mark_not_started):
                            receipt_ref = (
                                f"tool-dispatch-not-started:{context.run_id}:"
                                f"{context.call_id}:{context.effect_id}"
                            )
                            receipt_hash = fingerprint_json(
                                {
                                    "receipt_ref": receipt_ref,
                                    "request_hash": dispatch.request_hash,
                                    "reason_code": start_result.reason_code,
                                }
                            )
                            handoff = await mark_not_started(
                                context.effect_id,
                                int(claim["attempt_no"]),
                                int(claim["effect_version"]),
                                receipt_ref,
                                receipt_hash,
                            )
                            claim["effect_version"] = int(
                                handoff.effect_version
                            )
                            effect_metadata[index][
                                "handoff_not_started"
                            ] = True
                    await dispatch.abort_unstarted()
                    await self._release_execution_scope(scope_leases[index])
                    scope_leases[index] = None
                    results[index] = NormalizedToolOutcome.failure(
                        "dispatch_not_started", start_result.reason_code
                    )
                elif isinstance(start_result, PreparedToolDispatchStartUnknown):
                    if isinstance(claim, dict):
                        mark_unknown = getattr(
                            self._uow,
                            "mark_effect_inflight_may_complete",
                            None,
                        )
                        if callable(mark_unknown):
                            receipt_ref = (
                                f"tool-dispatch-start-unknown:{context.run_id}:"
                                f"{context.call_id}:{context.effect_id}"
                            )
                            receipt_hash = fingerprint_json(
                                {
                                    "receipt_ref": receipt_ref,
                                    "request_hash": dispatch.request_hash,
                                    "reason_code": start_result.reason_code,
                                }
                            )
                            handoff = await mark_unknown(
                                context.effect_id,
                                int(claim["attempt_no"]),
                                int(claim["effect_version"]),
                                receipt_ref,
                                receipt_hash,
                            )
                            claim["effect_version"] = int(
                                handoff.effect_version
                            )
                    effect_metadata[index]["late_pending"] = True
                    await self._release_execution_scope(scope_leases[index])
                    scope_leases[index] = None
                    results[index] = NormalizedToolOutcome.malformed(
                        "physical dispatch may have started; reconciliation required"
                    )
                else:
                    raise RuntimeError("prepared dispatch returned an invalid start result")
            except asyncio.CancelledError:
                claim = effect_metadata[index].get("effect_claim")
                if (
                    isinstance(start_result, PreparedToolDispatchStartedAck)
                    and isinstance(claim, dict)
                ):
                    try:
                        await self._converge_cancelled_started_dispatch(
                            dispatch=dispatch,
                            start_result=start_result,
                            context=context,
                            claim=claim,
                            metadata=effect_metadata[index],
                        )
                    except Exception:
                        # Cancellation remains the caller-visible result.  The
                        # detached completion is retained for a later durable
                        # reconciliation attempt even if this read/CAS failed.
                        effect_metadata[index]["late_pending"] = True
                    detach = getattr(dispatch, "detach_completion", None)
                    if callable(detach):
                        detach()
                raise
            except StaleRecoveryLease:
                raise
            except Exception as exc:  # one bad sibling must not cancel the batch
                dispatch = prepared_dispatches[index]
                if dispatch is not None:
                    await dispatch.abort_unstarted()
                results[index] = NormalizedToolOutcome.failure(
                    "executor_error", f"{type(exc).__name__}: {exc}"
                )
            finally:
                await self._release_execution_scope(scope_leases[index])
                scope_leases[index] = None
                if pinned_scope is not None:
                    scope_store.unpin(
                        context.scope_id,
                        session_id=context.session_id,
                        request_id=context.request_id,
                    )
                self._resource_execution.release(resource_locks)

        index = 0
        while index < len(calls):
            if results[index] is not None:
                index += 1
            elif not self._registry.is_concurrency_safe(calls[index]):
                await execute_at(index)
                index += 1
            else:
                end = index + 1
                while (
                    end < len(calls)
                    and results[end] is None
                    and self._registry.is_concurrency_safe(calls[end])
                ):
                    end += 1
                await asyncio.gather(*(execute_at(pos) for pos in range(index, end)))
                index = end
        if any(outcome is None for outcome in results):
            raise RuntimeError("prepared batch did not produce every outcome")
        if self._current_execution_scope_port is not None:
            await self._revalidate_execution_scope(contexts[0])
        return [outcome for outcome in results if outcome is not None]

    async def execute(
        self,
        record: RunRecord,
        actor: ActorContext,
        command: ExecuteTools,
        *,
        recovery_lease: RecoveryLease | None = None,
    ) -> EffectBatch | None:
        """Return only outcomes ready for the Driver's atomic boundary settlement."""
        await self._verify_trusted_tool_refs(
            record,
            command.calls,
            command.contexts,
        )
        refs = command.grant_refs or (None,) * len(command.calls)
        confirm_only = self._validate_confirm_only_snapshot(command)
        for call, grant_ref, declared_effectful, explicit_only in zip(
            command.calls, refs, command.effectful, confirm_only
        ):
            if explicit_only and grant_ref is None:
                raise ValueError(
                    "confirm-only tool is missing an exact one-shot decision grant"
                )
            if not declared_effectful:
                requires_authorization, effectful = (
                    self._registry.prepared_execution_policy(call)
                )
                if effectful:
                    raise ValueError(
                        "driver tool policy does not match registry authority"
                    )
                if requires_authorization and grant_ref is None:
                    raise ValueError("authorized tool is missing a grant")

        authorizations: list[object | None] = [None] * len(command.calls)
        execution_scope_leases = await self._acquire_execution_scopes(
            command.contexts
        )
        dispatches: list[PreparedToolDispatch | None] = [
            None
        ] * len(command.calls)
        begin_prepared = getattr(self._registry, "begin_prepared", None)
        if callable(begin_prepared):
            try:
                for index, (call, context) in enumerate(
                    zip(command.calls, command.contexts)
                ):
                    dispatches[index] = await begin_prepared(
                        call,
                        effect_id=context.effect_id,
                        execution_context=context,
                        authorization_provider=(
                            lambda index=index: self._authorization(
                                authorizations[index],
                                command.calls[index],
                                command.contexts[index],
                            )
                        ),
                    )
            except BaseException:
                await self._abort_dispatches(
                    dispatches, execution_scope_leases
                )
                raise

        metadata: list[dict[str, object]] = []
        precomputed: list[NormalizedToolOutcome | None] = []
        authoritative: list[OutcomeStatus | None] = []
        durable_tracking = (
            record.persistence_level is PersistenceLevel.DURABLE
        )

        try:
            for index, (
                call,
                context,
                grant_ref,
                declared_effectful,
            ) in enumerate(
                zip(
                    command.calls,
                    command.contexts,
                    refs,
                    command.effectful,
                )
            ):
                consume = None if grant_ref is None else GrantConsume(
                    grant_id=grant_ref.grant_id,
                    decision_id=grant_ref.decision_id,
                    decision_nonce=grant_ref.decision_nonce,
                    run_id=record.run_id,
                    expected_session_id=record.context.session_id,
                    call_id=call.stable_call_id,
                    effect_id=context.effect_id,
                    tool_name=call.tool_name,
                    args_hash=call.args_hash,
                    capability_hash=context.capability_hash,
                    scope_hash=context.scope_hash,
                    expected_version=grant_ref.version,
                )
                if not declared_effectful and not durable_tracking:
                    authorizations[index] = (
                        None
                        if consume is None
                        else await self._uow.claim_tool_call(consume, actor)
                    )
                    metadata.append({})
                    precomputed.append(None)
                    authoritative.append(None)
                    continue

                owner = (
                    recovery_lease.owner
                    if recovery_lease is not None
                    else "kernel"
                )
                epoch = (
                    recovery_lease.epoch
                    if recovery_lease is not None
                    else 1
                )
                claim = await self._uow.claim_tool_call(
                    consume,
                    actor,
                    run_id=record.run_id,
                    expected_session_id=record.context.session_id,
                    call_id=call.stable_call_id,
                    effect_id=context.effect_id,
                    tool_name=call.tool_name,
                    args_hash=call.args_hash,
                    capability_hash=context.capability_hash,
                    scope_hash=context.scope_hash,
                    effect_type=call.effect_type,
                    policy={
                        "kind": call.effect_type,
                        "version": call.effect_policy_version,
                    },
                    prepared=call.to_dict(),
                    worker_owner=owner,
                    worker_epoch=epoch,
                    recovery_lease=recovery_lease,
                )
                authorizations[index] = claim.authorization
                item: dict[str, object] = {
                    "effect_action": claim.action,
                    "durable_tracking": True,
                }
                if claim.action in {"execute", "reconcile"}:
                    item["effect_claim"] = {
                        "effect_id": claim.effect_id,
                        "attempt_no": claim.attempt_no,
                        "worker_owner": claim.worker_owner,
                        "worker_epoch": claim.worker_epoch,
                        "effect_version": claim.effect_version,
                    }
                known = None
                if claim.action == "reuse":
                    known = await self._uow.read_effect_outcome(
                        run_id=record.run_id,
                        call_id=call.stable_call_id,
                        effect_id=context.effect_id,
                        args_hash=call.args_hash,
                        capability_hash=context.capability_hash,
                        scope_hash=context.scope_hash,
                    )
                if known is not None:
                    known_status, payload, receipt_ref, artifact_refs = known
                    precomputed.append(
                        NormalizedToolOutcome.from_dict(payload)
                    )
                    authoritative.append(OutcomeStatus(known_status))
                    item.update(
                        {
                            "receipt_ref": receipt_ref,
                            "artifact_refs": list(artifact_refs),
                        }
                    )
                elif claim.action == "reconcile":
                    late_state, late_outcome = (
                        await self._registry.observe_late_prepared(
                            context.effect_id
                        )
                    )
                    if late_state == "pending":
                        item["late_pending"] = True
                        precomputed.append(
                            NormalizedToolOutcome.malformed(
                                "effect is still running under its original physical call"
                            )
                        )
                    else:
                        precomputed.append(
                            late_outcome
                            or NormalizedToolOutcome.malformed(
                                "late effect evidence unavailable after process recovery"
                            )
                        )
                        item.update(
                            self._registry.take_prepared_execution_metadata(
                                context.effect_id
                            )
                        )
                        item["reconciliation"] = True
                    authoritative.append(
                        OutcomeStatus(
                            str(item.get("outcome_status") or "unknown")
                        )
                    )
                elif claim.action == "in_flight":
                    item["late_pending"] = True
                    precomputed.append(
                        NormalizedToolOutcome.malformed(
                            "effect is still owned by its original attempt"
                        )
                    )
                    authoritative.append(OutcomeStatus.UNKNOWN)
                elif claim.action != "execute":
                    precomputed.append(
                        NormalizedToolOutcome.malformed(
                            f"effect_{claim.action}; canonical reconciliation required"
                        )
                    )
                    authoritative.append(None)
                else:
                    precomputed.append(None)
                    authoritative.append(None)
                metadata.append(item)
        except BaseException:
            await self._abort_dispatches(
                dispatches, execution_scope_leases
            )
            raise

        try:
            for index, (call, declared_effectful) in enumerate(
                zip(command.calls, command.effectful)
            ):
                if (
                    not declared_effectful
                    or metadata[index].get("effect_action") != "execute"
                ):
                    continue
                requires_authorization, effectful = (
                    self._registry.prepared_execution_policy(call)
                )
                if not effectful:
                    raise ValueError(
                        "driver tool policy does not match registry authority"
                    )
                if (
                    requires_authorization
                    and authorizations[index] is None
                ):
                    raise ValueError("authorized tool is missing a grant")
        except BaseException:
            await self._abort_dispatches(
                dispatches, execution_scope_leases
            )
            raise

        for index, outcome in enumerate(precomputed):
            if outcome is None:
                continue
            if dispatches[index] is not None:
                await dispatches[index].abort_unstarted()
            dispatches[index] = None
            await self._release_execution_scope(
                execution_scope_leases[index]
            )
            execution_scope_leases[index] = None

        outcomes = await self._execute_segmented(
            command.calls,
            command.contexts,
            authorizations,
            precomputed,
            dispatches=dispatches,
            execution_scope_leases=execution_scope_leases,
            metadata=metadata,
        )
        for context, item in zip(command.contexts, metadata):
            item.update(self._registry.take_prepared_execution_metadata(context.effect_id))
        for index, item in enumerate(metadata):
            if not item.get("late_pending") or item.get("effect_action") != "execute":
                continue
            claim = dict(item["effect_claim"])
            pending = outcomes[index].to_dict()
            pending["reconciliation_pending"] = True
            await self._uow.settle_effect(
                command.contexts[index].effect_id,
                expected_effect_version=int(claim["effect_version"]),
                attempt_no=int(claim["attempt_no"]),
                worker_owner=str(claim["worker_owner"]),
                worker_epoch=int(claim["worker_epoch"]),
                outcome=pending,
                recovery_lease=recovery_lease,
            )

        statuses = tuple(
            fixed or self._registry.prepared_outcome_status(call, outcome)
            for call, outcome, fixed in zip(command.calls, outcomes, authoritative)
        )
        ready = tuple(index for index, item in enumerate(metadata) if not item.get("late_pending"))
        if not ready:
            return None
        signal = ToolOutcomesSignal(
            command.run_id, command.command_id,
            tuple(outcomes[index] for index in ready),
            tuple(statuses[index] for index in ready),
            tuple(command.original_indexes[index] for index in ready),
            tuple(metadata[index] for index in ready),
        )
        ready_refs = tuple(
            (call, context)
            for index, (call, context) in enumerate(zip(command.calls, command.contexts))
            if index in ready and "effect_claim" in metadata[index]
        )
        return EffectBatch(signal, ready_refs)

    async def acknowledge_committed(
        self, refs: Sequence[tuple[PreparedToolCall, ToolExecutionContext]]
    ) -> None:
        """Drop live evidence only after the durable Driver settlement is observable."""
        for call, context in refs:
            settled = await self._uow.read_effect_outcome(
                run_id=context.run_id, call_id=call.stable_call_id,
                effect_id=context.effect_id, args_hash=call.args_hash,
                capability_hash=context.capability_hash, scope_hash=context.scope_hash,
            )
            if settled is not None:
                self._registry.acknowledge_prepared_effect(context.effect_id)

    def ready_run_ids(self) -> frozenset[str]:
        return self._registry.ready_late_prepared_run_ids()

    async def quarantine_terminal_late(self, run_id: str) -> int:
        """Settle ready late effects without ever resuming a terminal Driver."""

        ready = self._registry.ready_late_prepared_effect_ids(run_id)
        settled = 0
        for effect_id in ready:
            state, outcome = await self._registry.observe_late_prepared(effect_id)
            if state != "complete" or outcome is None:
                continue
            for _attempt in range(3):
                handoff = await self._uow.read_effect_handoff(effect_id)
                if handoff is None:
                    self._registry.acknowledge_prepared_effect(effect_id)
                    break
                if handoff.status == "late_reconciled":
                    self._registry.acknowledge_prepared_effect(effect_id)
                    settled += 1
                    break
                if handoff.status not in {"running", "unknown"}:
                    # A separate durable settlement already won.  The
                    # process-local observation is duplicate evidence.
                    self._registry.acknowledge_prepared_effect(effect_id)
                    break
                if (
                    handoff.status != "unknown"
                    or handoff.handoff_state != "started_may_complete"
                ):
                    # Observation remains ready; a later trigger can retry once
                    # the cancellation handoff reaches may-complete.
                    break
                late_outcome_hash = fingerprint_json(outcome.to_dict())
                try:
                    suppressed = await self._uow.suppress_late_effect_completion(
                        effect_id,
                        handoff.attempt_no,
                        handoff.effect_version,
                        late_outcome_hash,
                    )
                    receipt_ref = f"late-effect-suppressed:{run_id}:{effect_id}"
                    receipt_hash = fingerprint_json(
                        {
                            "receipt_ref": receipt_ref,
                            "late_outcome_hash": late_outcome_hash,
                            "completed_suppressed": True,
                        }
                    )
                    await self._uow.reconcile_effect_handoff(
                        effect_id,
                        suppressed.attempt_no,
                        suppressed.effect_version,
                        receipt_ref,
                        receipt_hash,
                        completed_suppressed=True,
                    )
                except (VersionConflict, IdempotencyConflict):
                    # Re-read: another reconciler may already have produced the
                    # same terminal decision.  Keep the observation ready if not.
                    continue
                self._registry.acknowledge_prepared_effect(effect_id)
                settled += 1
                break
        return settled

    def _bind_ready_callback(self, callback: Callable[[str], None]) -> None:
        bind = getattr(self._registry, "_set_prepared_ready_callback", None)
        if callable(bind):
            bind(callback)

    async def drain(self, timeout: float) -> frozenset[str]:
        await self._registry.close_prepared_executions(timeout)
        return self.ready_run_ids()
