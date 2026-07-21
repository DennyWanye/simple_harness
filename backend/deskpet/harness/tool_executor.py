"""One durable owner for prepared tool batches and effect reconciliation."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Sequence

from deskpet.execution.contracts import (
    ActorContext,
    DecisionAuthorization,
    GrantConsume,
    OutcomeStatus,
    RecoveryLease,
    RunRecord,
    StaleRecoveryLease,
)
from deskpet.execution.ports import ExecutionUnitOfWork
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.registry import ToolRegistry
from deskpet.workflows.effects import NormalizedToolOutcome, PreparedToolCall

from .ports import ExecuteTools, ToolOutcomesSignal


@dataclass(frozen=True, slots=True)
class EffectBatch:
    signal: ToolOutcomesSignal
    ready_refs: tuple[tuple[PreparedToolCall, ToolExecutionContext], ...]


class EffectBatchExecutor:
    """Claim, execute and reconcile one ordered batch; Drivers settle boundaries."""

    def __init__(self, uow: ExecutionUnitOfWork, registry: ToolRegistry) -> None:
        self._uow, self._registry = uow, registry

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
    ) -> list[NormalizedToolOutcome]:
        results = list(precomputed)

        async def execute_at(index: int) -> None:
            if results[index] is not None:
                return
            try:
                call, context = calls[index], contexts[index]
                results[index] = await self._registry.execute_prepared(
                    call, effect_id=context.effect_id,
                    authorization=self._authorization(authorizations[index], call, context),
                    execution_context=context,
                )
            except StaleRecoveryLease:
                raise
            except Exception as exc:  # one bad sibling must not cancel the batch
                results[index] = NormalizedToolOutcome.failure(
                    "executor_error", f"{type(exc).__name__}: {exc}"
                )

        index = 0
        while index < len(calls):
            if results[index] is not None:
                index += 1
            elif not self._registry.is_concurrency_safe(calls[index].tool_name):
                await execute_at(index)
                index += 1
            else:
                end = index + 1
                while (
                    end < len(calls)
                    and results[end] is None
                    and self._registry.is_concurrency_safe(calls[end].tool_name)
                ):
                    end += 1
                await asyncio.gather(*(execute_at(pos) for pos in range(index, end)))
                index = end
        if any(outcome is None for outcome in results):
            raise RuntimeError("prepared batch did not produce every outcome")
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
        refs = command.grant_refs or (None,) * len(command.calls)
        authorizations: list[object | None] = []
        metadata: list[dict[str, object]] = []
        precomputed: list[NormalizedToolOutcome | None] = []
        authoritative: list[OutcomeStatus | None] = []

        for call, context, grant_ref, declared_effectful in zip(
            command.calls, command.contexts, refs, command.effectful
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
            if not declared_effectful:
                requires_authorization, effectful = self._registry.prepared_execution_policy(call)
                if effectful:
                    raise ValueError("driver tool policy does not match registry authority")
                if requires_authorization and consume is None:
                    raise ValueError("authorized tool is missing a grant")
                authorizations.append(
                    None if consume is None else await self._uow.claim_tool_call(consume, actor)
                )
                metadata.append({})
                precomputed.append(None)
                authoritative.append(None)
                continue

            owner = recovery_lease.owner if recovery_lease is not None else "kernel"
            epoch = recovery_lease.epoch if recovery_lease is not None else 1
            claim = await self._uow.claim_tool_call(
                consume, actor,
                run_id=record.run_id,
                expected_session_id=record.context.session_id,
                call_id=call.stable_call_id,
                effect_id=context.effect_id,
                tool_name=call.tool_name,
                args_hash=call.args_hash,
                capability_hash=context.capability_hash,
                scope_hash=context.scope_hash,
                effect_type=call.effect_type,
                policy={"kind": call.effect_type, "version": call.effect_policy_version},
                prepared=call.to_dict(),
                worker_owner=owner,
                worker_epoch=epoch,
                recovery_lease=recovery_lease,
            )
            authorizations.append(claim.authorization)
            item: dict[str, object] = {"effect_action": claim.action}
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
                    run_id=record.run_id, call_id=call.stable_call_id,
                    effect_id=context.effect_id, args_hash=call.args_hash,
                    capability_hash=context.capability_hash, scope_hash=context.scope_hash,
                )
            if known is not None:
                known_status, payload, receipt_ref, artifact_refs = known
                precomputed.append(NormalizedToolOutcome.from_dict(payload))
                authoritative.append(OutcomeStatus(known_status))
                item.update({"receipt_ref": receipt_ref, "artifact_refs": list(artifact_refs)})
            elif claim.action == "reconcile":
                late_state, late_outcome = await self._registry.observe_late_prepared(context.effect_id)
                if late_state == "pending":
                    item["late_pending"] = True
                    precomputed.append(NormalizedToolOutcome.malformed(
                        "effect is still running under its original physical call"
                    ))
                else:
                    precomputed.append(late_outcome or NormalizedToolOutcome.malformed(
                        "late effect evidence unavailable after process recovery"
                    ))
                    item.update(self._registry.take_prepared_execution_metadata(context.effect_id))
                    item["reconciliation"] = True
                authoritative.append(OutcomeStatus(str(item.get("outcome_status") or "unknown")))
            elif claim.action == "in_flight":
                item["late_pending"] = True
                precomputed.append(NormalizedToolOutcome.malformed(
                    "effect is still owned by its original attempt"
                ))
                authoritative.append(OutcomeStatus.UNKNOWN)
            elif claim.action != "execute":
                precomputed.append(NormalizedToolOutcome.malformed(
                    f"effect_{claim.action}; canonical reconciliation required"
                ))
                authoritative.append(None)
            else:
                precomputed.append(None)
                authoritative.append(None)
            metadata.append(item)

        for index, (call, declared_effectful) in enumerate(zip(command.calls, command.effectful)):
            if declared_effectful and metadata[index].get("effect_action") == "execute":
                requires_authorization, effectful = self._registry.prepared_execution_policy(call)
                if not effectful:
                    raise ValueError("driver tool policy does not match registry authority")
                if requires_authorization and authorizations[index] is None:
                    raise ValueError("authorized tool is missing a grant")

        outcomes = await self._execute_segmented(
            command.calls, command.contexts, authorizations, precomputed
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
            if index in ready and command.effectful[index]
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

    async def drain(self, timeout: float) -> frozenset[str]:
        await self._registry.close_prepared_executions(timeout)
        return self.ready_run_ids()
