from __future__ import annotations

import asyncio

import pytest

from deskpet.execution.contracts import ActorContext
from deskpet.execution.ledger import ExecutionLedger
from deskpet.harness.bootstrap import build_harness_runtime
from deskpet.harness.kernel import HostContext, RegisteredDriver
from deskpet.harness.ports import DriverTerminalCandidate
from deskpet.harness.router import ClassifiedRoute, RouteProfile
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork
from deskpet.workflows.store.schema import WORKFLOW_SCHEMA_VERSION


class Classifier:
    def classify(self, request):
        return ClassifiedRoute("react.default", "fixture", 1.0)


class Driver:
    async def start(self, request):
        yield DriverTerminalCandidate(request.run_id, "completed", "ok")

    async def signal(self, signal):
        if False:
            yield DriverTerminalCandidate(signal.run_id, "completed", "")

    async def cancel(self, run_id, reason):
        if False:
            yield DriverTerminalCandidate(run_id, "cancelled", "")

    async def recover(self, run_id):
        if False:
            yield DriverTerminalCandidate(run_id, "completed", "")

    async def close(self):
        return None


class Resolver:
    def resolve_host(self, transport):
        session_id = str(transport["session_id"])
        return HostContext(
            session_id=session_id,
            principal_id=f"principal-{session_id}",
            auth_epoch=1,
            capability_hash="c" * 64,
            available_capabilities=frozenset(),
            provider_plan=("primary",),
            trace_id="trace-1",
        )

    def resolve_actor(self, transport, *, root_run_id):
        session_id = str(transport["session_id"])
        return ActorContext(
            principal_id=f"principal-{session_id}",
            session_id=session_id,
            auth_epoch=1,
            root_run_id=root_run_id,
        )


@pytest.mark.asyncio
async def test_bootstrap_exports_manifest_from_actual_registrations(tmp_path) -> None:
    runtime = await build_harness_runtime(
        ledger=ExecutionLedger(SqliteExecutionUnitOfWork(tmp_path / "workflow.db")),
        classifier=Classifier(),
        profiles=[RouteProfile("react.default", "react")],
        drivers=[RegisteredDriver("react", Driver())],
        resolver=Resolver(),
    )

    assert runtime.manifest.to_dict() == {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "operations": ["start", "observe", "signal", "cancel", "recover", "close"],
        "drivers": ["react"],
        "profiles": [{"key": "react.default", "driver_kind": "react"}],
        "event_contract": "execution.run-event.v1",
        "tool_stage": "prepared-call.v1",
    }
    assert runtime.health.to_dict()["active_owner"] == "execution_ledger"

    handle = await runtime.run_client.start(
        {"text": "hello", "request_id": "request-1", "turn_id": "turn-1"},
        {"session_id": "session-1", "venue": "text"},
    )
    events = [event async for event in handle.events]
    assert events[-1].candidate.payload["text"] == "ok"

    second = await runtime.run_client.start(
        {"text": "late", "request_id": "request-2", "turn_id": "turn-2"},
        {"session_id": "session-1", "venue": "text"},
    )
    await asyncio.sleep(0.02)
    late_events = [event async for event in second.events]
    assert late_events[-1].kind == "final"


@pytest.mark.asyncio
async def test_bootstrap_fails_closed_for_unavailable_profile_driver(tmp_path) -> None:
    with pytest.raises(ValueError, match="unavailable drivers: workflow"):
        await build_harness_runtime(
            ledger=ExecutionLedger(SqliteExecutionUnitOfWork(tmp_path / "workflow.db")),
            classifier=Classifier(),
            profiles=[RouteProfile("durable.default", "workflow")],
            drivers=[RegisteredDriver("react", Driver())],
            resolver=Resolver(),
        )
