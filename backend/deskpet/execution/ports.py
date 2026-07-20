"""Storage-facing protocols for the product-neutral execution control plane."""

from __future__ import annotations

from typing import Protocol, Sequence, TypeAlias, runtime_checkable

from .contracts import (
    ActorAction,
    ActorContext,
    ChildCommandIntent,
    ChildCommandRecord,
    ChildSignalRecord,
    CreateRunResult,
    DecisionAuthorization,
    DecisionOpen,
    DecisionRecord,
    DecisionSignal,
    DeliveryRecord,
    DeliverySpec,
    FinalizeRunResult,
    GrantConsume,
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
SinkKey: TypeAlias = tuple[str, str]


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
class ExecutionEventStore(Protocol):
    async def get_event(self, event_id: str) -> RunEvent: ...

    async def list_events(
        self,
        run_id: str,
        *,
        after_durable_seq: int = 0,
    ) -> tuple[RunEvent, ...]: ...


@runtime_checkable
class ExecutionDeliveryStore(Protocol):
    async def list_event_deliveries(
        self, event_id: str
    ) -> tuple[DeliveryRecord, ...]: ...

    async def claim_delivery(
        self,
        *,
        sink_keys: Sequence[SinkKey] = (),
        claim_ttl_seconds: float = 30.0,
    ) -> DeliveryRecord | None: ...

    async def complete_delivery(
        self,
        delivery_id: str,
        *,
        expected_version: int,
    ) -> DeliveryRecord: ...

    async def release_delivery(
        self,
        delivery_id: str,
        *,
        expected_version: int,
        error: str,
        retry_at: float | None,
        discard: bool,
    ) -> DeliveryRecord: ...

    async def required_deliveries_complete(self, event_id: str) -> bool: ...


@runtime_checkable
class ExecutionDecisionStore(Protocol):
    async def open_decision(
        self,
        request: DecisionOpen,
        actor: ActorContext,
        *,
        expected_run_version: int,
    ) -> DecisionRecord: ...

    async def get_decision(
        self,
        decision_id: str,
        *,
        ref: RunRef,
        actor: ActorContext,
    ) -> DecisionRecord: ...

    async def resolve_decision(
        self,
        signal: DecisionSignal,
        actor: ActorContext,
    ) -> tuple[DecisionRecord, DecisionAuthorization | None]: ...

    async def cancel_open_decisions(
        self,
        ref: RunRef,
        actor: ActorContext,
        *,
        expected_run_version: int,
    ) -> tuple[DecisionRecord, ...]: ...

    async def consume_authorization(
        self,
        request: GrantConsume,
        actor: ActorContext,
    ) -> DecisionAuthorization: ...


@runtime_checkable
class ExecutionUnitOfWork(
    ExecutionLedger,
    ExecutionEventStore,
    ExecutionDeliveryStore,
    ExecutionDecisionStore,
    Protocol,
):
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

    async def commit_child_command(
        self, intent: ChildCommandIntent
    ) -> ChildCommandRecord: ...

    async def get_child_command(
        self, operation_id: str
    ) -> ChildCommandRecord | None: ...

    async def lease_child_commands(
        self,
        *,
        owner: str,
        limit: int,
        lease_seconds: float,
    ) -> tuple[ChildCommandRecord, ...]: ...

    async def schedule_child_command(
        self,
        operation_id: str,
        *,
        lease_owner: str,
        lease_epoch: int,
    ) -> ChildCommandRecord: ...

    async def acknowledge_child_command(
        self,
        operation_id: str,
        *,
        lease_owner: str,
        lease_epoch: int,
    ) -> ChildCommandRecord: ...

    async def record_child_terminal(
        self,
        operation_id: str,
        *,
        terminal_status: str,
        value: object = None,
    ) -> ChildSignalRecord: ...

    async def list_pending_child_signals(
        self, parent_run_id: str
    ) -> tuple[ChildSignalRecord, ...]: ...

    async def acknowledge_child_signal(self, signal_id: str) -> ChildSignalRecord: ...


ExecutionLedgerPort = ExecutionLedger


__all__ = [
    "ExecutionLedger",
    "ExecutionLedgerPort",
    "ExecutionDecisionStore",
    "ExecutionDeliveryStore",
    "ExecutionEventStore",
    "ExecutionUnitOfWork",
    "RunView",
    "SinkKey",
]
