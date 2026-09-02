# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Product TaskExecutionEnvelope authority (S5a) + exact root resolution (S5b).

Issues envelopes by exact identity echo from the SDK request plus the frozen
route receipt.  PROJECT_EFFECT tools additionally require an exact single-root
resolution from the S4 binding set; no resolver means stable fail-closed —
never a default root, never the first / most recent / current directory.

S5b design-freeze §4 (整 Run 故障): every ``TaskExecutionAuthorityError`` that
escapes ``issue_envelope`` aborts the whole SDK Run.  Its stable code is
recorded into the optional ``fault_sink`` (``RunFaultMemo``) so the Host
terminal observer can carry it into the ``run_terminal`` evidence.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from simple_harness import CallId
from simple_harness.contracts import EffectId, RunId
from simple_harness.execution.effects import TaskExecutionEnvelope
from simple_harness.tools.runtime_catalog import ToolEffectClass

from deskpet.task_scope.workspace_bindings import WorkspaceBindingError


class TaskExecutionAuthorityError(RuntimeError):
    code = "sdk_task_execution_authority_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)
        # Instance code == the stable reason string (foreground runtime reads
        # ``getattr(exc, "code", ...)`` for audit rows).
        self.code = message or type(self).code


RootResolution = tuple[str, str]
"""(root_id, root_identity_hash) for the exact single workspace root."""


class RunFaultSink(Protocol):
    def record(self, run_id: object, code: str) -> None: ...


class BindingRootResolver:
    """Resolve the exact single root of a route receipt's binding-set receipt.

    Reads the *immutable* receipt named by the route receipt
    (``exact_receipt``) — never the live head, so a Manual/Auto append that
    lands mid-Run cannot silently switch the root (that case is rejected per
    effect by the EffectGate as ``workspace_binding_receipt_superseded``).

    * exactly one root  -> ``(root_id, root_identity_hash)``
    * zero roots        -> ``sdk_task_execution_root_authority_missing``
    * more than one     -> ``sdk_task_execution_root_authority_ambiguous``
    * receipt missing/stale -> the S4 code passes through unchanged

    All failures are whole-Run faults (``TaskExecutionAuthorityError``).
    """

    def __init__(self, binding_store: Any) -> None:
        self._store = binding_store

    async def __call__(self, receipt: Any) -> RootResolution:
        try:
            exact = await self._store.exact_receipt(
                task_scope_id=str(receipt.task_scope_id),
                binding_set_revision=int(receipt.binding_set_revision),
                binding_set_receipt_id=str(receipt.binding_set_receipt_id),
                binding_set_receipt_hash=str(receipt.binding_set_receipt_hash),
            )
        except WorkspaceBindingError as exc:
            raise TaskExecutionAuthorityError(exc.code) from exc
        hashes = tuple(exact.root_identity_hashes)
        if len(hashes) == 0:
            raise TaskExecutionAuthorityError(
                "sdk_task_execution_root_authority_missing"
            )
        if len(hashes) > 1:
            raise TaskExecutionAuthorityError(
                "sdk_task_execution_root_authority_ambiguous"
            )
        root = exact.appended_root
        if root is None or root.root_identity_hash != hashes[0]:
            raise TaskExecutionAuthorityError(
                "sdk_task_execution_root_authority_missing"
            )
        return str(root.root_id), str(hashes[0])


class ProductTaskExecutionAuthority:
    """Issue TaskExecutionEnvelopes bound to the exact requested effect."""

    def __init__(
        self,
        *,
        root_resolver: Callable[[Any], Awaitable[RootResolution]] | None = None,
        fault_sink: RunFaultSink | None = None,
    ) -> None:
        self._root_resolver = root_resolver
        self._fault_sink = fault_sink

    @property
    def root_resolver(self) -> Callable[[Any], Awaitable[RootResolution]] | None:
        return self._root_resolver

    async def issue_envelope(self, request: Any) -> TaskExecutionEnvelope:
        try:
            return await self._issue(request)
        except TaskExecutionAuthorityError as exc:
            if self._fault_sink is not None:
                self._fault_sink.record(getattr(request, "run_id", None), exc.code)
            raise

    async def _issue(self, request: Any) -> TaskExecutionEnvelope:
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
    "BindingRootResolver",
    "ProductTaskExecutionAuthority",
    "RunFaultSink",
    "TaskExecutionAuthorityError",
]
