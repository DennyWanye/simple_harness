from __future__ import annotations

import asyncio
import ast
from pathlib import Path

import pytest
import pytest_asyncio

from deskpet.execution.contracts import (
    LiveCursor,
    OutcomeStatus,
    RunEvent,
    RunEventCandidate,
    RunStatus,
)
from deskpet.execution.ledger import ExecutionLedger
from deskpet.harness.kernel import (
    DriverTerminalCandidate,
    HostContext,
    RunKernel,
    RunRequest,
    RunSignal,
    SignalReceipt,
    kernel_public_operations,
)
from deskpet.harness.router import ClassifiedRoute, RegisteredRouter, RouteProfile
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


class StaticClassifier:
    def __init__(self, profile: str) -> None:
        self.profile = profile
        self.calls = 0

    def classify(self, request):
        self.calls += 1
        return ClassifiedRoute(self.profile, "test", 1.0)


class FakeDriver:
    kind = "react"
    durable_from_start = False

    def __init__(self) -> None:
        self.starts = 0
        self.recovers = 0
        self.signals = 0
        self.cancelled: list[str] = []

    async def start(self, record, request, emit):
        self.starts += 1
        await emit(
            RunEvent(
                event_id=f"live:{record.run_id}:1",
                run_id=record.run_id,
                root_run_id=record.context.root_run_id,
                session_id=record.context.session_id,
                durable_seq=None,
                live_cursor=LiveCursor("epoch-1", 1),
                candidate=RunEventCandidate(
                    "token-1", "assistant_delta", OutcomeStatus.SUCCEEDED, self.kind,
                    payload={"text": "ok"},
                ),
                created_at=1.0,
            )
        )
        return None

    async def recover(self, record, emit):
        self.recovers += 1
        return None

    async def signal(self, record, signal):
        self.signals += 1
        return SignalReceipt(True)

    async def cancel(self, record, reason):
        self.cancelled.append(reason)
        return True


@pytest_asyncio.fixture
async def kernel(tmp_path):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    ledger = ExecutionLedger(uow)
    await ledger.initialize()
    classifier = StaticClassifier("react.default")
    driver = FakeDriver()
    value = RunKernel(
        ledger=ledger,
        router=RegisteredRouter(classifier, [RouteProfile("react.default", "react")]),
        drivers=[driver],
    )
    return value, classifier, driver


def host(session: str = "s1") -> HostContext:
    return HostContext(
        session_id=session,
        principal_id=f"principal-{session}",
        auth_epoch=1,
        capability_hash="c" * 64,
        available_capabilities=frozenset(),
        provider_plan=("primary",),
        trace_id="trace-1",
    )


@pytest.mark.asyncio
async def test_start_routes_once_and_idempotent_retry_does_not_start_twice(kernel) -> None:
    value, classifier, driver = kernel
    request = RunRequest("hello", "req-1", "turn-1")
    first = await value.start(request, host())
    second = await value.start(request, host())
    await asyncio.sleep(0)
    assert first.ref == second.ref
    assert classifier.calls == 1
    assert driver.starts == 1


@pytest.mark.asyncio
async def test_observe_preserves_first_live_event_and_foreign_actor_is_rejected(kernel) -> None:
    value, _, _ = kernel
    handle = await value.start(RunRequest("hello", "req-2", "turn-1"), host())
    await asyncio.sleep(0)
    stream = value.observe(handle.ref, host().actor(root_run_id=handle.root_run_id))
    event = await anext(stream)
    await stream.aclose()
    assert event.candidate.payload["text"] == "ok"
    with pytest.raises(Exception):
        await anext(value.observe(handle.ref, host("other").actor()))


@pytest.mark.asyncio
async def test_signal_cancel_recover_and_close_use_explicit_run_scope(kernel) -> None:
    value, _, driver = kernel
    handle = await value.start(RunRequest("hello", "req-3", "turn-1"), host())
    actor = host().actor(root_run_id=handle.root_run_id)
    receipt = await value.signal(handle.ref, actor, RunSignal("permission", "d1", "n1", 0))
    assert receipt.accepted is True
    await value.recover(handle.ref, actor)
    cancelled = await value.cancel(handle.ref, actor, "user_stop")
    assert cancelled.acknowledged is True
    assert driver.cancelled == ["user_stop"]
    await value.close(handle.ref, actor)


def test_kernel_surface_is_six_operations_and_has_no_product_branches() -> None:
    assert kernel_public_operations() == ("start", "observe", "signal", "cancel", "recover", "close")
    source = Path(__file__).parents[2] / "deskpet" / "harness" / "kernel.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    kernel_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RunKernel"
    )
    public = {
        node.name
        for node in kernel_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    assert public == set(kernel_public_operations())
    text = source.read_text(encoding="utf-8").casefold()
    for product in ("deepresearch", "deep_research", "ppt", "code_complex", "voice"):
        assert product not in text
