"""Storage-facing protocols for the product-neutral execution control plane."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from .contracts import (
    ActorAction,
    ActorContext,
    CreateRunResult,
    DeliverySpec,
    FinalizeRunResult,
    LegacyRunProjection,
    RunCreate,
    RunEvent,
    RunEventCandidate,
    RunLinkSpec,
    RunRecord,
    RunRef,
    RunStatus,
    WorkflowRunSeed,
)


RunView = RunRecord | LegacyRunProjection


@runtime_checkable
class ExecutionLedger(Protocol):
    """Authoritative coarse run lifecycle and ownership operations."""

    async def create(self, spec: RunCreate) -> CreateRunResult: ...

    async def promote(
        self,
        spec: RunCreate,
        *,
        expected_version: int,
    ) -> CreateRunResult: ...

    async def finalize(
        self,
        run_id: str,
        *,
        expected_version: int,
        terminal_status: RunStatus,
        event: RunEventCandidate,
        deliveries: Sequence[DeliverySpec] = (),
    ) -> FinalizeRunResult: ...

    async def request_cancel(
        self,
        run_id: str,
        *,
        expected_version: int,
        reason: str,
        event: RunEventCandidate,
        deliveries: Sequence[DeliverySpec] = (),
    ) -> RunRecord: ...

    async def link(
        self,
        link: RunLinkSpec,
        *,
        expected_child_version: int,
    ) -> RunRecord: ...

    async def query(self, ref: RunRef, actor: ActorContext) -> RunView: ...

    async def authorize(
        self,
        ref: RunRef,
        actor: ActorContext,
        action: ActorAction | str,
    ) -> RunView: ...

    async def list_children(
        self,
        ref: RunRef,
        actor: ActorContext,
    ) -> tuple[RunRecord, ...]: ...


@runtime_checkable
class ExecutionUnitOfWork(ExecutionLedger, Protocol):
    """One-connection durable operations shared by execution and drivers."""

    async def initialize(self) -> None: ...

    async def append_event(
        self,
        run_id: str,
        *,
        expected_version: int,
        event: RunEventCandidate,
        deliveries: Sequence[DeliverySpec] = (),
    ) -> RunEvent: ...

    async def start_workflow(
        self,
        spec: RunCreate,
        workflow: WorkflowRunSeed,
        *,
        accepted_event: RunEventCandidate | None = None,
        deliveries: Sequence[DeliverySpec] = (),
    ) -> CreateRunResult: ...


ExecutionLedgerPort = ExecutionLedger


__all__ = [
    "ExecutionLedger",
    "ExecutionLedgerPort",
    "ExecutionUnitOfWork",
    "RunView",
]
