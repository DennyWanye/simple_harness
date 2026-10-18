# SPDX-License-Identifier: BUSL-1.1

"""Driving a claimed foreground run: freeze the turn, bind the SDK run, prepare and authorize the start."""

from __future__ import annotations

import asyncio
import json
import uuid

from simple_harness import ContextRouteOrigin, ContextRouteReceipt, TaskScopeRoute, thaw_json

from deskpet.execution.foreground_identity import foreground_execution_session_id
from deskpet.execution.foreground_queue import (
    ContextLineage,
    EffectBoundary,
    PreparationCandidate,
    RunState,
)
from deskpet.execution.preparation_rejection import PreparationDisclosureRejected
from deskpet.sdk_adapters.ingress import SdkRuntimeIngress
from deskpet.task_scope.protocol import canonical_hash


class ForegroundRuntimeError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _uuid(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"simple-harness:{label}"))


def _candidate_turn_payload(candidate: PreparationCandidate) -> dict[str, object]:
    try:
        raw = json.loads(candidate.candidate_json)
        turn = raw["turn"]
        payload = turn["payload"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ForegroundRuntimeError("foreground_claimed_turn_payload_invalid") from exc
    if not isinstance(payload, dict):
        raise ForegroundRuntimeError("foreground_claimed_turn_payload_invalid")
    return payload


class ClaimedDriveMixin:
    """Driving a claimed foreground run: freeze the turn, bind the SDK run, prepare and authorize the start."""

    __slots__ = ()

    async def _drive_claimed(
        self, host_run_id: str, previously_bound_sdk_run_id: str | None
    ) -> None:
        snapshot = await self._store.current_snapshot(self._subject)
        if snapshot is None or snapshot.host_run_id != host_run_id:
            raise ForegroundRuntimeError("foreground_runtime_snapshot_changed")
        claimed = await self._store.read_claimed_execution(
            host_run_id=host_run_id,
            owner_id=self._owner_id,
            generation=snapshot.generation,
        )
        execution_session_id = foreground_execution_session_id(host_run_id)
        request_id = f"foreground-request-{claimed.candidate.turn_id}"
        sdk_run_id = SdkRuntimeIngress._compute_run_id(
            execution_session_id,
            request_id,
            claimed.candidate.turn_id,
        ).value
        if previously_bound_sdk_run_id not in (None, sdk_run_id):
            raise ForegroundRuntimeError("foreground_runtime_sdk_binding_drift")

        # Recovery consumes the actual durable Run, not a newly prepared copy
        # of its original context. In particular, forgetting old sources must
        # not prevent STOP or the recording of an already-failed SDK terminal.
        record = None if previously_bound_sdk_run_id is None else self._ingress.query(sdk_run_id)
        if record is not None:
            if (
                getattr(record, "run_id", None),
                getattr(record, "execution_session_id", None),
                getattr(record, "request_id", None),
            ) != (sdk_run_id, execution_session_id, request_id):
                raise ForegroundRuntimeError("foreground_runtime_sdk_binding_drift")
            await self._bind_claimed_sdk_run(
                claimed,
                host_run_id=host_run_id,
                record=record,
                sdk_run_id=sdk_run_id,
                snapshot=snapshot,
            )
            return

        def _check_identity(authority: object) -> None:
            self._assert_authority_identity(
                authority,
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                owner_id=self._owner_id,
                generation=claimed.generation,
            )

        provider = await self._provider.freeze(
            claimed=claimed,
            execution_session_id=execution_session_id,
            request_id=request_id,
            sdk_run_id=sdk_run_id,
        )
        _check_identity(provider)
        tools = await self._tools.freeze(
            claimed=claimed,
            execution_session_id=execution_session_id,
            request_id=request_id,
            sdk_run_id=sdk_run_id,
        )
        _check_identity(tools)

        try:
            context = await self._context.prepare(
                claimed=claimed,
                expected_context=ContextLineage(
                    snapshot.context_snapshot_id,
                    snapshot.context_snapshot_revision,
                    snapshot.context_snapshot_hash,
                ),
                execution_session_id=execution_session_id,
                request_id=request_id,
                sdk_run_id=sdk_run_id,
                provider=provider,
                tools=tools,
            )
        except PreparationDisclosureRejected as exc:
            await self._raise_claimed_rejection(claimed, exc, host_run_id)
            return
        _check_identity(context)
        bound_provider = await self._provider.bind(
            frozen=provider,
            context=context,
            tools=tools,
            execution_session_id=execution_session_id,
            request_id=request_id,
            sdk_run_id=sdk_run_id,
        )
        _check_identity(bound_provider)
        candidate = claimed.candidate
        route = await self._claimed_route(
            candidate,
            claimed=claimed,
            context=context,
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
        )
        await self._freeze_claimed_turn(
            bound_provider,
            candidate=candidate,
            claimed=claimed,
            context=context,
            execution_session_id=execution_session_id,
            host_run_id=host_run_id,
            previously_bound_sdk_run_id=previously_bound_sdk_run_id,
            request_id=request_id,
            route=route,
            sdk_run_id=sdk_run_id,
            tools=tools,
        )

    async def _raise_claimed_rejection(self, claimed, exc, host_run_id: str) -> None:
        rejection = exc.rejection
        candidate = claimed.candidate
        if (
            rejection.host_run_id != host_run_id
            or rejection.subject != candidate.subject
            or rejection.turn_id != candidate.turn_id
            or rejection.turn_hash != candidate.turn_hash
            or rejection.candidate_hash != candidate.candidate_hash
            or rejection.evidence_id != candidate.evidence_id
            or rejection.evidence_hash != candidate.evidence_hash
        ):
            raise ForegroundRuntimeError(
                "foreground_preparation_rejection_identity_mismatch"
            ) from exc
        await self._store.settle_preparation_rejection(
            rejection=exc.rejection,
            owner_id=claimed.owner_id,
            generation=claimed.generation,
        )
        self._notify_state_changed()
        return

    async def _freeze_claimed_turn(
        self,
        bound_provider,
        *,
        candidate,
        claimed,
        context,
        execution_session_id,
        host_run_id: str,
        previously_bound_sdk_run_id: str | None,
        request_id,
        route,
        sdk_run_id,
        tools,
    ) -> None:
        turn_payload = _candidate_turn_payload(candidate)
        run_binding = bound_provider.binding
        catalog_generation = int(run_binding.catalog_generation)
        catalog_fingerprint = str(run_binding.catalog_fingerprint)
        budget_fingerprint = str(run_binding.budget_fingerprint)
        start_payload = self._claimed_start_payload(
            bound_provider,
            candidate=candidate,
            context=context,
            execution_session_id=execution_session_id,
            host_run_id=host_run_id,
            request_id=request_id,
            route=route,
            run_binding=run_binding,
            tools=tools,
            turn_payload=turn_payload,
        )
        execution_request_hash = canonical_hash(start_payload)
        await self._store.record_execution_preparation(
            host_run_id=host_run_id,
            owner_id=self._owner_id,
            generation=claimed.generation,
            context_ref=context.authority_ref,
            context_hash=context.authority_hash,
            provider_ref=bound_provider.authority_ref,
            provider_hash=bound_provider.authority_hash,
            tool_ref=tools.authority_ref,
            tool_hash=tools.authority_hash,
            execution_request_hash=execution_request_hash,
            idempotency_key=f"runtime-preparation:{host_run_id}",
        )
        should_start, tool_start_ready = await self._prepare_claimed_start(
            claimed,
            execution_request_hash=execution_request_hash,
            host_run_id=host_run_id,
            previously_bound_sdk_run_id=previously_bound_sdk_run_id,
            route=route,
            sdk_run_id=sdk_run_id,
        )
        if should_start:
            await self._authorize_and_start_claimed(
                budget_fingerprint,
                candidate=candidate,
                catalog_fingerprint=catalog_fingerprint,
                catalog_generation=catalog_generation,
                claimed=claimed,
                context=context,
                execution_session_id=execution_session_id,
                host_run_id=host_run_id,
                request_id=request_id,
                route=route,
                sdk_run_id=sdk_run_id,
                start_payload=start_payload,
            )
        tool_start_ready.set()
        self._notify_state_changed()
        self._record_audit(
            "foreground.runtime.bound",
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            generation=claimed.generation,
            route_receipt_id=None if route is None else route.receipt_id,
            route_receipt_hash=None if route is None else route.receipt_hash,
        )
        await self._finish_bound(claimed, sdk_run_id)

    async def _bind_claimed_sdk_run(
        self, claimed, *, host_run_id: str, record, sdk_run_id, snapshot
    ) -> None:
        await self._store.bind_sdk_run(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=self._owner_id,
            generation=claimed.generation,
            idempotency_key=f"runtime-sdk-bind:{host_run_id}",
        )
        await self._store.record_start_observation(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=self._owner_id,
            generation=claimed.generation,
            outcome="QUERY_FOUND",
            result_ref=f"sdk-run:{sdk_run_id}:v{getattr(record, 'version', 0)}",
            result_hash=canonical_hash(
                {
                    "run_id": sdk_run_id,
                    "state": str(getattr(getattr(record, "state", None), "value", "")),
                    "version": int(getattr(record, "version", 0)),
                }
            ),
            idempotency_key=f"runtime-recovery-query:{host_run_id}:g{claimed.generation}:v{getattr(record, 'version', 0)}",
        )
        if snapshot.state is RunState.CLAIMED:
            await self._store.record_sdk_started(
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                owner_id=self._owner_id,
                generation=claimed.generation,
                sdk_event_id=f"sdk-query:{sdk_run_id}:v{getattr(record, 'version', 0)}",
                idempotency_key=f"runtime-recovered-running:{host_run_id}:g{claimed.generation}",
            )
        if self._effect_gate is not None:
            self._effect_gate.register(
                store=self._store,
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                owner_id=self._owner_id,
                generation=claimed.generation,
            )
        await self._finish_bound(claimed, sdk_run_id)
        return

    async def _claimed_route(self, candidate, *, claimed, context, host_run_id: str, sdk_run_id):
        if candidate.task_scope_id is not None and (
            candidate.binding_set_revision < 1
            or candidate.binding_set_receipt_id is None
            or candidate.binding_set_receipt_hash is None
        ):
            raise ForegroundRuntimeError("foreground_runtime_route_authority_missing")
        if not claimed.admission_receipt_id or not claimed.admission_receipt_hash:
            raise ForegroundRuntimeError("foreground_runtime_admission_receipt_missing")
        route = None
        if candidate.task_scope_id is not None:
            host_ref = claimed.admission_receipt_id
            host_hash = claimed.admission_receipt_hash
            route_receipt_id = _uuid(
                f"foreground-initial-route:{host_run_id}:{host_ref}:{host_hash}"
            )
            route = ContextRouteReceipt(
                route_receipt_id,
                sdk_run_id,
                None,
                None,
                TaskScopeRoute.RESUME_EXISTING,
                candidate.task_scope_id,
                candidate.binding_set_revision,
                context.resume_refs,
                schema_version=3,
                binding_set_receipt_id=candidate.binding_set_receipt_id,
                binding_set_receipt_hash=candidate.binding_set_receipt_hash,
                origin=ContextRouteOrigin.HOST_INITIAL,
                host_authority_ref=host_ref,
                host_authority_hash=host_hash,
            )
            await self._context.verify_initial_route(route)
        return route

    def _claimed_start_payload(
        self,
        bound_provider,
        *,
        candidate,
        context,
        execution_session_id,
        host_run_id: str,
        request_id,
        route,
        run_binding,
        tools,
        turn_payload,
    ):
        tool_names = tools.catalog.get("tool_names")
        if not isinstance(tool_names, (list, tuple)):
            raise ForegroundRuntimeError("foreground_runtime_tool_catalog_invalid")

        start_payload = {
            "input": {"text": context.current_text},
            "messages": [dict(item) for item in context.provider_messages],
            "capability_snapshot": {"tools": [str(item) for item in tool_names]},
            "context_metadata": {
                "session_id": execution_session_id,
                "root_run_id": host_run_id,
                "request_id": request_id,
                "task_scope_id": candidate.task_scope_id,
                "visibility_dependencies": thaw_json(context.visibility_dependencies),
                "primary_effect_index_version": 1,
                "scope_disclosure": thaw_json(context.scope_disclosure),
                "context_authority_ref": context.authority_ref,
                "context_authority_hash": context.authority_hash,
                "provider_authority_ref": bound_provider.authority_ref,
                "provider_authority_hash": bound_provider.authority_hash,
                "tool_authority_ref": tools.authority_ref,
                "tool_authority_hash": tools.authority_hash,
                "snapshot_id": context.snapshot_id,
                # 用量投影按这三个值判断样本可信；可用上限与有任务范围的路径同口径，等于窗口。
                "binding_epoch": int(run_binding.binding_epoch),
                "context_window": int(run_binding.context_window),
                "effective_ceiling": int(run_binding.context_window),
                "run_binding": run_binding.to_record(),
                "tool_authority": dict(tools.run_start_record),
                "initial_route_receipt_id": None if route is None else route.receipt_id,
                "initial_route_receipt_hash": None if route is None else route.receipt_hash,
            },
            "turn": turn_payload,
        }
        return start_payload

    async def _prepare_claimed_start(
        self,
        claimed,
        *,
        execution_request_hash,
        host_run_id: str,
        previously_bound_sdk_run_id: str | None,
        route,
        sdk_run_id,
    ):
        start_request_hash = canonical_hash(
            {
                "sdk_run_id": sdk_run_id,
                "execution_request_hash": execution_request_hash,
                "route_receipt_hash": None if route is None else route.receipt_hash,
            }
        )
        await self._store.record_start_intent(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=self._owner_id,
            generation=claimed.generation,
            start_request_hash=start_request_hash,
            idempotency_key=f"runtime-start-intent:{host_run_id}",
        )
        await self._store.bind_sdk_run(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=self._owner_id,
            generation=claimed.generation,
            idempotency_key=f"runtime-sdk-bind:{host_run_id}",
        )
        self._notify_state_changed()
        tool_start_ready = asyncio.Event()
        if self._effect_gate is not None:
            self._effect_gate.register(
                store=self._store,
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                owner_id=self._owner_id,
                generation=claimed.generation,
                start_ready=tool_start_ready,
            )

        should_start = previously_bound_sdk_run_id is None
        if previously_bound_sdk_run_id is not None:
            record = self._ingress.query(sdk_run_id)
            if record is None:
                outcomes = await self._store.read_start_observation_outcomes(
                    host_run_id=host_run_id,
                    owner_id=self._owner_id,
                    generation=claimed.generation,
                )
                await self._store.record_start_observation(
                    host_run_id=host_run_id,
                    sdk_run_id=sdk_run_id,
                    owner_id=self._owner_id,
                    generation=claimed.generation,
                    outcome="QUERY_MISSING",
                    error_code="foreground_runtime_orphaned_start",
                    idempotency_key=f"runtime-restart-query:{host_run_id}:missing",
                )
                if {"RETURNED", "QUERY_FOUND"}.intersection(outcomes):
                    await self._store.record_reconciliation(
                        host_run_id=host_run_id,
                        sdk_run_id=sdk_run_id,
                        owner_id=self._owner_id,
                        generation=claimed.generation,
                        observed_state="FAILED_CLOSED",
                        idempotency_key=(
                            f"runtime-reconcile:{host_run_id}:g{claimed.generation}:missing"
                        ),
                    )
                    raise ForegroundRuntimeError("foreground_runtime_orphaned_start")
                await self._store.record_reconciliation(
                    host_run_id=host_run_id,
                    sdk_run_id=sdk_run_id,
                    owner_id=self._owner_id,
                    generation=claimed.generation,
                    observed_state="UNBOUND_RETRY",
                    idempotency_key=(
                        f"runtime-reconcile:{host_run_id}:g{claimed.generation}:pre-start"
                    ),
                )
                should_start = True
            else:
                await self._store.record_start_observation(
                    host_run_id=host_run_id,
                    sdk_run_id=sdk_run_id,
                    owner_id=self._owner_id,
                    generation=claimed.generation,
                    outcome="QUERY_FOUND",
                    result_ref=f"sdk-run:{sdk_run_id}:v{getattr(record, 'version', 0)}",
                    result_hash=canonical_hash(
                        {
                            "run_id": sdk_run_id,
                            "state": str(getattr(getattr(record, "state", None), "value", "")),
                            "version": int(getattr(record, "version", 0)),
                        }
                    ),
                    # 同上：驱动重入时 record.version 会变，键必须含版本，
                    # 否则同键写不同 result_hash 触发
                    # ``foreground_execution_start_observation_idempotency_conflict``
                    # （实测 .local-test-evidence/real-ui-channel/verify-03）。
                    idempotency_key=(
                        f"runtime-restart-query:{host_run_id}:found:"
                        f"v{getattr(record, 'version', 0)}"
                    ),
                )
                current = await self._store.current_snapshot(self._subject)
                if current is not None and current.state is RunState.CLAIMED:
                    await self._store.record_sdk_started(
                        host_run_id=host_run_id,
                        sdk_run_id=sdk_run_id,
                        owner_id=self._owner_id,
                        generation=claimed.generation,
                        sdk_event_id=(f"sdk-query:{sdk_run_id}:v{getattr(record, 'version', 0)}"),
                        idempotency_key=f"runtime-running:{host_run_id}",
                    )
        return should_start, tool_start_ready

    async def _authorize_and_start_claimed(
        self,
        budget_fingerprint,
        *,
        candidate,
        catalog_fingerprint,
        catalog_generation,
        claimed,
        context,
        execution_session_id,
        host_run_id: str,
        request_id,
        route,
        sdk_run_id,
        start_payload,
    ) -> None:
        await self._store.authorize_effect(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=self._owner_id,
            generation=claimed.generation,
            boundary=EffectBoundary.SDK_START,
        )
        start_returned = True
        try:
            conversation = None
            if self._conversation_entrypoint is not None:
                conversation = await self._conversation_entrypoint(
                    session_id=execution_session_id,
                    sdk_run_id=sdk_run_id,
                    text=context.current_text,
                    context_snapshot_id=context.snapshot_id,
                    provider_messages=context.provider_messages,
                )
            receipt = await self._ingress.start(
                session_id=execution_session_id,
                request_id=request_id,
                turn_id=candidate.turn_id,
                payload=start_payload,
                session_generation=catalog_generation,
                tool_catalog_fingerprint=catalog_fingerprint,
                provider_budget_fingerprint=budget_fingerprint,
                conversation=conversation,
                initial_route_receipt=route,
                initial_route_receipt_hash=None if route is None else route.receipt_hash,
            )
        except Exception as exc:
            await self._store.record_start_observation(
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                owner_id=self._owner_id,
                generation=claimed.generation,
                outcome="RAISED",
                error_code=str(getattr(exc, "code", type(exc).__name__)),
                idempotency_key=f"runtime-start-raised:{host_run_id}",
            )
            discovered = self._ingress.query(sdk_run_id)
            if discovered is None:
                await self._store.record_start_observation(
                    host_run_id=host_run_id,
                    sdk_run_id=sdk_run_id,
                    owner_id=self._owner_id,
                    generation=claimed.generation,
                    outcome="QUERY_MISSING",
                    error_code="foreground_runtime_start_not_committed",
                    idempotency_key=f"runtime-start-query:{host_run_id}:missing",
                )
                await self._store.record_reconciliation(
                    host_run_id=host_run_id,
                    sdk_run_id=sdk_run_id,
                    owner_id=self._owner_id,
                    generation=claimed.generation,
                    observed_state="UNBOUND_RETRY",
                    idempotency_key=(
                        f"runtime-reconcile:{host_run_id}:g{claimed.generation}:start-missing"
                    ),
                )
                raise
            await self._store.record_start_observation(
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                owner_id=self._owner_id,
                generation=claimed.generation,
                outcome="QUERY_FOUND",
                result_ref=(f"sdk-run:{sdk_run_id}:v{getattr(discovered, 'version', 0)}"),
                result_hash=canonical_hash(
                    {
                        "run_id": sdk_run_id,
                        "state": str(getattr(getattr(discovered, "state", None), "value", "")),
                        "version": int(getattr(discovered, "version", 0)),
                    }
                ),
                # 幂等键必须含 Run 版本：驱动可能被多次唤醒（授权决策后
                # `after_control` 会重入），每次 query 到的 record.version 不同，
                # 用同一把键写不同内容会撞
                # ``foreground_execution_start_observation_idempotency_conflict``
                # （实测 .local-test-evidence/real-ui-channel/final-ui）。
                idempotency_key=(
                    f"runtime-start-query:{host_run_id}:found:v{getattr(discovered, 'version', 0)}"
                ),
            )
            start_returned = False
            result_ref = f"sdk-query:{sdk_run_id}:v{getattr(discovered, 'version', 0)}"
        if start_returned:
            if receipt.run_id != sdk_run_id:
                raise ForegroundRuntimeError("foreground_runtime_ingress_identity_drift")
            result_ref = f"sdk-start:{sdk_run_id}:g{receipt.generation}"
            result_hash = canonical_hash(
                {
                    "run_id": receipt.run_id,
                    "generation": receipt.generation,
                    "session_id": receipt.session_id,
                    "request_id": receipt.request_id,
                }
            )
            await self._store.record_start_observation(
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                owner_id=self._owner_id,
                generation=claimed.generation,
                outcome="RETURNED",
                result_ref=result_ref,
                result_hash=result_hash,
                idempotency_key=f"runtime-start-returned:{host_run_id}",
            )
        await self._store.record_sdk_started(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=self._owner_id,
            generation=claimed.generation,
            sdk_event_id=result_ref,
            idempotency_key=f"runtime-running:{host_run_id}",
        )
