# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Product TaskExecutionEnvelope authority (S5a).

Issues envelopes by exact identity echo from the SDK request plus the frozen
route receipt.  PROJECT_EFFECT tools additionally require an exact single-root
resolution from the S4 binding set; no resolver means stable fail-closed —
never a default root, never the first / most recent / current directory.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from simple_harness import CallId
from simple_harness.contracts import EffectId, RunId
from simple_harness.execution.effects import TaskExecutionEnvelope
from simple_harness.tools.runtime_catalog import ToolEffectClass


class TaskExecutionAuthorityError(RuntimeError):
    code = "sdk_task_execution_authority_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


RootResolution = tuple[str, str]
"""(root_id, root_identity_hash) for the exact single workspace root."""


class ProductTaskExecutionAuthority:
    """Issue TaskExecutionEnvelopes bound to the exact requested effect."""

    def __init__(
        self,
        *,
        root_resolver: Callable[[Any], Awaitable[RootResolution]] | None = None,
    ) -> None:
        self._root_resolver = root_resolver

    async def issue_envelope(self, request: Any) -> TaskExecutionEnvelope:
        policy = request.policy
        receipt = request.route_receipt
        run_id = request.run_id
        if not isinstance(run_id, RunId):
            raise TaskExecutionAuthorityError("sdk_task_execution_run_id_invalid")
        task_scope_id: str | None = None
        root_id: str | None = None
        root_identity_hash: str | None = None
        binding_set_revision: int | None = None
        binding_set_receipt_id: str | None = None
        binding_set_receipt_hash: str | None = None
        if policy.effect_class is ToolEffectClass.PROJECT_EFFECT:
            if (
                receipt is None
                or receipt.task_scope_id is None
                or receipt.binding_set_revision is None
                or receipt.binding_set_receipt_id is None
                or receipt.binding_set_receipt_hash is None
            ):
                raise TaskExecutionAuthorityError(
                    "sdk_task_execution_route_authority_missing"
                )
            if self._root_resolver is None:
                raise TaskExecutionAuthorityError(
                    "sdk_task_execution_root_authority_unavailable"
                )
            root_id, root_identity_hash = await self._root_resolver(receipt)
            task_scope_id = receipt.task_scope_id
            binding_set_revision = receipt.binding_set_revision
            binding_set_receipt_id = receipt.binding_set_receipt_id
            binding_set_receipt_hash = receipt.binding_set_receipt_hash
        return TaskExecutionEnvelope(
            run_id=run_id,
            call_id=CallId(request.call_id),
            effect_id=EffectId(request.effect_id),
            raw_call_id=request.raw_call_id,
            turn_ordinal=request.turn_ordinal,
            call_ordinal=request.call_ordinal,
            tool_name=request.tool_name,
            capability_id=policy.capability_id,
            capability_fingerprint=policy.capability_fingerprint,
            route_receipt_id=None if receipt is None else receipt.receipt_id,
            route_receipt_hash=None if receipt is None else receipt.receipt_hash,
            task_scope_id=task_scope_id,
            root_id=root_id,
            root_identity_hash=root_identity_hash,
            binding_set_revision=binding_set_revision,
            idempotency_key=request.effect_id,
            binding_set_receipt_id=binding_set_receipt_id,
            binding_set_receipt_hash=binding_set_receipt_hash,
        )


__all__ = [
    "ProductTaskExecutionAuthority",
    "TaskExecutionAuthorityError",
]
