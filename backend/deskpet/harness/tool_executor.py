"""Batch scheduling over the canonical ToolRegistry V2 execution kernel."""
from __future__ import annotations

import asyncio
from typing import Protocol, Sequence

from deskpet.execution.contracts import (
    DecisionAuthorization,
    OutcomeStatus,
    RecoveryLease,
    StaleRecoveryLease,
)
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.effects import (
    NormalizedToolOutcome,
    PreparedToolCall,
)


class PreparedCallRegistry(Protocol):
    def is_concurrency_safe(self, tool_name: str) -> bool: ...
    def get(self, tool_name: str) -> object | None: ...
    def prepared_execution_policy(self, prepared: PreparedToolCall) -> tuple[bool, bool]: ...
    def prepared_outcome_status(self, prepared: PreparedToolCall, outcome: NormalizedToolOutcome) -> OutcomeStatus: ...
    def take_prepared_execution_metadata(self, effect_id: str) -> dict[str, object]: ...
    def acknowledge_prepared_effect(self, effect_id: str) -> None: ...
    async def observe_late_prepared(self, effect_id: str) -> tuple[str, NormalizedToolOutcome | None]: ...
    async def execute_prepared(
        self,
        prepared: PreparedToolCall,
        *,
        effect_id: str,
        authorization: object | None,
        execution_context: ToolExecutionContext,
    ) -> NormalizedToolOutcome: ...


class UnifiedToolExecutor:
    """Order-aware adapter; ToolRegistry remains the sole execution owner."""

    def __init__(self, registry: PreparedCallRegistry) -> None:
        self._registry = registry

    async def execute_one(
        self,
        call: PreparedToolCall,
        context: ToolExecutionContext,
        *,
        authorization: object | None = None,
        recovery_lease: RecoveryLease | None = None,
    ) -> NormalizedToolOutcome:
        del recovery_lease  # settlement fencing belongs to the canonical effect service
        canonical_authorization = authorization
        if isinstance(authorization, DecisionAuthorization):
            canonical_authorization = {
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
        return await self._registry.execute_prepared(
            call,
            effect_id=context.effect_id,
            authorization=canonical_authorization,
            execution_context=context,
        )

    def outcome_status(
        self, call: PreparedToolCall, outcome: NormalizedToolOutcome
    ) -> OutcomeStatus:
        return self._registry.prepared_outcome_status(call, outcome)

    def prepared_policy(self, call: PreparedToolCall) -> tuple[bool, bool]:
        return self._registry.prepared_execution_policy(call)

    def take_metadata(self, effect_id: str) -> dict[str, object]:
        return self._registry.take_prepared_execution_metadata(effect_id)

    async def observe_late(
        self, effect_id: str
    ) -> tuple[str, NormalizedToolOutcome | None]:
        return await self._registry.observe_late_prepared(effect_id)

    def acknowledge_effect(self, effect_id: str) -> None:
        self._registry.acknowledge_prepared_effect(effect_id)

    async def execute_batch(
        self,
        calls: Sequence[PreparedToolCall],
        contexts: Sequence[ToolExecutionContext],
        *,
        authorizations: Sequence[object | None] | None = None,
        precomputed: Sequence[NormalizedToolOutcome | None] | None = None,
        recovery_lease: RecoveryLease | None = None,
    ) -> list[NormalizedToolOutcome]:
        if len(calls) != len(contexts):
            raise ValueError("calls and contexts must have the same length")
        grants = list(authorizations or [None] * len(calls))
        if len(grants) != len(calls):
            raise ValueError("authorizations and calls must have the same length")
        results = list(precomputed or [None] * len(calls))
        if len(results) != len(calls):
            raise ValueError("precomputed outcomes and calls must have the same length")

        async def execute_at(index: int) -> None:
            if results[index] is not None:
                return
            try:
                results[index] = await self.execute_one(
                    calls[index],
                    contexts[index],
                    authorization=grants[index],
                    recovery_lease=recovery_lease,
                )
            except StaleRecoveryLease:
                raise
            except Exception as exc:  # sibling calls must not be cancelled
                results[index] = NormalizedToolOutcome.failure(
                    "executor_error", f"{type(exc).__name__}: {exc}"
                )

        index = 0
        while index < len(calls):
            if results[index] is not None:
                index += 1
                continue
            if not self._registry.is_concurrency_safe(calls[index].tool_name):
                await execute_at(index)
                index += 1
                continue
            end = index + 1
            while end < len(calls) and results[end] is None and self._registry.is_concurrency_safe(
                calls[end].tool_name
            ):
                end += 1
            await asyncio.gather(*(execute_at(pos) for pos in range(index, end)))
            index = end
        return [outcome for outcome in results if outcome is not None]
