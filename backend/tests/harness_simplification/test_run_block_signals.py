from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest

from deskpet.execution.contracts import (
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunStartSnapshotRecord,
    RunStatus,
    TerminalConflict,
    canonical_json,
    fingerprint_json,
)
from deskpet.execution.run_block_signals import (
    PreflightBlocked,
    RootBlockReasonV1,
    RunBlockReporter,
    RunBlockSignalV1,
    block_signal_for_terminal,
)
from deskpet.agent.turn_preparer import ProductTurnPreparer, TurnInput
from deskpet.harness.adapters.product_turn_open import ProductTurnIdentityResolver
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork
from deskpet.harness.adapters.venues import KernelRunClient
from deskpet.harness.contracts import HostContext, RegisteredDriver, driver_catalog
from deskpet.harness.kernel import RunKernel
from deskpet.harness.ports import DriverTerminalCandidate, TokenCandidate
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.harness.router import ClassifiedRoute, RegisteredRouter


def _spec(run_id: str = "blocked-root") -> RunCreate:
    capability_hash = fingerprint_json({"capabilities": []})
    return RunCreate(
        run_id=run_id,
        idempotency_key=f"root:{run_id}",
        context=RunContext(
            session_id="session-1",
            root_run_id=run_id,
            parent_run_id=None,
            request_id="request-1",
            turn_id="turn-1",
            venue="text",
            workspace={},
            capability_hash=capability_hash,
            provider_plan={"route_availability": "unavailable"},
            trace_id="trace-1",
            principal_id="principal-1",
        ),
        payload_fingerprint=fingerprint_json({"request": "request-1"}),
        capability_fingerprint=capability_hash,
        driver_kind="react",
        profile_key="agent.general",
        persistence_level=PersistenceLevel.DURABLE,
    )


def _snapshot(spec: RunCreate) -> RunStartSnapshotRecord:
    empty_list = canonical_json([])
    empty_map = canonical_json({})
    payload = {
        "run_id": spec.run_id,
        "request_identity": spec.context.request_id,
        "route_availability": "unavailable",
    }
    return RunStartSnapshotRecord(
        run_id=spec.run_id,
        snapshot_schema_version=1,
        start_fingerprint=fingerprint_json(payload),
        canonical_messages_json=empty_list,
        session_cursor_json=empty_map,
        prepared_refs_json=canonical_json({"request_identity": "request-1"}),
        sanitized_request_json=canonical_json({"request_id": "request-1"}),
        run_context_json=canonical_json(spec.context.to_dict()),
        run_spec_json=canonical_json(spec.to_dict()),
        capability_snapshot_json=empty_map,
        capability_snapshot_hash=fingerprint_json({}),
        provider_launch_policy_json=canonical_json(
            {"route_availability": "unavailable"}
        ),
        terminal_deliveries_json=empty_list,
        terminal_deliveries_hash=hashlib.sha256(empty_list.encode()).hexdigest(),
        capability_lease_intent_ref=None,
        capability_lease_intent_hash=None,
        created_at=1.0,
    )


def _event(spec: RunCreate, event_key: str = "preflight.final") -> RunEventCandidate:
    return RunEventCandidate(
        event_key=event_key,
        kind="run.final",
        status=OutcomeStatus.FAILED,
        driver_kind=spec.driver_kind,
        payload={"kind": "final", "route_availability": "unavailable"},
    )


async def _install_v29_block_table(path) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS execution_run_block_signals(
            signal_id TEXT PRIMARY KEY,
            root_run_id TEXT NOT NULL UNIQUE,
            reason_code TEXT NOT NULL,
            evidence_refs_json TEXT NOT NULL,
            producer TEXT NOT NULL,
            created_event_id TEXT NOT NULL UNIQUE,
            payload_hash TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            created_at REAL NOT NULL
            )"""
        )
        await db.commit()


class _Classifier:
    def classify(self, _request):
        return ClassifiedRoute("react.default", "fixture", 1.0)


class _BlockTestDriver:
    def __init__(self, *, external: bool = False) -> None:
        self.starts = 0
        self.external = external

    async def start(self, request):
        self.starts += 1
        if self.external:
            yield DriverTerminalCandidate(
                request.run_id,
                "failed",
                error="external dependency exhausted",
                correlation={
                    "failure_code": "external_dependency_unavailable",
                    "run_block_signal": {
                        "reason_code": "external_dependency_unavailable",
                        "retry_exhausted": True,
                        "replacement_pending": False,
                        "evidence_refs": ["external-retry-set-1"],
                    },
                },
            )
        else:
            yield TokenCandidate(request.run_id, "unused")

    async def signal(self, _signal):
        if False:
            yield TokenCandidate("unused", "")

    async def cancel(self, _run_id, _reason):
        if False:
            yield TokenCandidate("unused", "")

    async def recover(self, _run_id, _lease):
        if False:
            yield TokenCandidate("unused", "")

    async def close(self):
        return None


def _kernel_host() -> HostContext:
    return HostContext(
        session_id="session-1",
        principal_id="principal-1",
        auth_epoch=1,
        capability_hash="c" * 64,
        available_capabilities=frozenset(),
        provider_plan=("fixture",),
        trace_id="trace-1",
        provider_bindings=(("fixture", "model-1", "inc-1", 1, 1),),
    )


def _kernel_with_driver(uow, driver) -> RunKernel:
    profiles = ProfileRegistry(
        (ProfileSpec("react.default", "fixture", "react"),)
    )
    return RunKernel(
        uow=uow,
        router=RegisteredRouter(_Classifier(), profiles),
        drivers=driver_catalog(
            (RegisteredDriver("react", driver, durable_from_start=True),)
        ),
    )


@pytest.mark.parametrize(
    ("reason", "producer"),
    [
        ("provider_binding_unavailable", "session_route_resolver"),
        ("capability_unavailable", "capability_admission"),
        ("external_dependency_unavailable", "external_dependency_retry"),
        ("workspace_unavailable", "workspace_resolver"),
    ],
)
def test_each_block_reason_has_exactly_one_authorized_producer(reason, producer):
    signal = RunBlockSignalV1(
        root_run_id="root-1",
        reason_code=reason,
        evidence_refs=("evidence-1",),
        producer=producer,
        created_event_id="event-1",
        created_at=1.0,
    )

    assert signal.reason_code.value == reason
    with pytest.raises(ValueError, match="not owned"):
        RunBlockSignalV1(
            root_run_id="root-1",
            reason_code=reason,
            evidence_refs=("evidence-1",),
            producer="some_other_producer",
            created_event_id="event-1",
            created_at=1.0,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [
        RootBlockReasonV1.PROVIDER_BINDING_UNAVAILABLE,
        RootBlockReasonV1.CAPABILITY_UNAVAILABLE,
        RootBlockReasonV1.WORKSPACE_UNAVAILABLE,
    ],
)
async def test_start_blocked_root_atomically_persists_minimal_root_terminal_and_signal(
    tmp_path, reason
):
    path = tmp_path / f"{reason.value}.db"
    uow = SqliteExecutionUnitOfWork(path, clock=lambda: 10.0)
    await uow.initialize()
    await _install_v29_block_table(path)
    spec = _spec(f"root-{reason.value}")
    event = _event(spec)
    signal = block_signal_for_terminal(
        root_run_id=spec.run_id,
        event_key=event.event_key,
        reason_code=reason,
        evidence_refs=(f"evidence-{reason.value}",),
        created_at=10.0,
    )

    result = await uow.start_blocked_root(
        spec,
        _snapshot(spec),
        event=event,
        block_reporter=RunBlockReporter(signal),
    )
    replay = await uow.start_blocked_root(
        spec,
        _snapshot(spec),
        event=event,
        block_reporter=RunBlockReporter(signal),
    )

    assert result.record.status is RunStatus.FAILED
    assert result.record.terminal_event_id == signal.created_event_id
    assert replay.idempotent is True
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute("SELECT * FROM execution_run_block_signals")
        ).fetchall()
        invocations = await (
            await db.execute(
                "SELECT COUNT(*) FROM execution_provider_invocations WHERE run_id=?",
                (spec.run_id,),
            )
        ).fetchone()
    assert len(rows) == 1
    assert rows[0]["reason_code"] == reason.value
    assert invocations[0] == 0
    await uow.close()


@pytest.mark.asyncio
async def test_external_dependency_block_signal_commits_with_started_root_terminal(tmp_path):
    path = tmp_path / "external.db"
    uow = SqliteExecutionUnitOfWork(path, clock=lambda: 10.0)
    await uow.initialize()
    await _install_v29_block_table(path)
    spec = _spec("external-root")
    await uow.create_with_start_snapshot(spec, _snapshot(spec))
    event = _event(spec, "external.final")
    signal = block_signal_for_terminal(
        root_run_id=spec.run_id,
        event_key=event.event_key,
        reason_code=RootBlockReasonV1.EXTERNAL_DEPENDENCY_UNAVAILABLE,
        evidence_refs=("retry-exhausted-1",),
        created_at=10.0,
    )

    result = await uow.commit_run_outcome(
        spec.run_id,
        expected_version=0,
        terminal_status=RunStatus.FAILED,
        event=event,
        terminal_commit_extensions=(RunBlockReporter(signal),),
    )

    assert result.record.status is RunStatus.FAILED
    await uow.close()


@pytest.mark.asyncio
async def test_block_signal_rejects_completed_or_cancelled_terminal(tmp_path):
    path = tmp_path / "conflict.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.initialize()
    await _install_v29_block_table(path)
    spec = _spec("conflict-root")
    await uow.create_with_start_snapshot(spec, _snapshot(spec))
    event = RunEventCandidate(
        event_key="done.final",
        kind="run.final",
        status=OutcomeStatus.SUCCEEDED,
        driver_kind=spec.driver_kind,
        payload={"kind": "final"},
    )
    signal = block_signal_for_terminal(
        root_run_id=spec.run_id,
        event_key=event.event_key,
        reason_code=RootBlockReasonV1.WORKSPACE_UNAVAILABLE,
        evidence_refs=("workspace-1",),
        created_at=1.0,
    )

    with pytest.raises(TerminalConflict, match="requires a failed terminal"):
        await uow.commit_run_outcome(
            spec.run_id,
            expected_version=0,
            terminal_status=RunStatus.COMPLETED,
            event=event,
            terminal_commit_extensions=(RunBlockReporter(signal),),
        )

    assert (await uow.read_run(spec.run_id)).status is RunStatus.CREATED
    await uow.close()


@pytest.mark.asyncio
async def test_start_blocked_root_rolls_back_everything_when_reporter_fails(tmp_path):
    path = tmp_path / "rollback.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.initialize()
    await _install_v29_block_table(path)
    spec = _spec("rollback-root")
    event = _event(spec)

    class BrokenReporter:
        async def apply_terminal_commit(self, *args, **kwargs):
            raise RuntimeError("injected reporter failure")

    with pytest.raises(RuntimeError, match="injected reporter failure"):
        await uow.start_blocked_root(
            spec,
            _snapshot(spec),
            event=event,
            block_reporter=BrokenReporter(),
        )

    assert await uow.read_run(spec.run_id) is None
    await uow.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [
        "provider_binding_unavailable",
        "workspace_unavailable",
        "capability_unavailable",
    ],
)
async def test_kernel_client_preflight_producers_use_shared_builder_without_driver_launch(
    tmp_path, reason
):
    path = tmp_path / f"kernel-{reason}.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.initialize()
    await uow.activate_runtime()
    await _install_v29_block_table(path)
    driver = _BlockTestDriver()
    client = KernelRunClient(_kernel_with_driver(uow, driver))

    host = _kernel_host()
    if reason == "provider_binding_unavailable":
        host = HostContext(
            session_id=host.session_id,
            principal_id=host.principal_id,
            auth_epoch=host.auth_epoch,
            capability_hash=host.capability_hash,
            available_capabilities=host.available_capabilities,
            provider_plan=(),
            trace_id=host.trace_id,
            provider_bindings=(),
        )
    handle = await client.start_blocked(
        {
            "text": "create project",
            "request_id": f"request-{reason}",
            "turn_id": f"turn-{reason}",
            "payload": {"route_availability": "unavailable"},
        },
        host,
        reason_code=reason,
        evidence_refs=(f"evidence-{reason}",),
    )
    events = [event async for event in handle.events]

    assert driver.starts == 0
    assert len(events) == 1
    assert events[0].candidate.status is OutcomeStatus.FAILED
    run = await uow.read_run(handle.run_id)
    assert run is not None and run.status is RunStatus.FAILED
    async with aiosqlite.connect(path) as db:
        row = await (
            await db.execute(
                "SELECT reason_code FROM execution_run_block_signals WHERE root_run_id=?",
                (handle.run_id,),
            )
        ).fetchone()
    assert row == (reason,)
    await uow.close()


@pytest.mark.asyncio
async def test_kernel_external_dependency_terminal_hook_writes_structured_signal(tmp_path):
    path = tmp_path / "kernel-external.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.initialize()
    await uow.activate_runtime()
    await _install_v29_block_table(path)
    driver = _BlockTestDriver(external=True)
    client = KernelRunClient(_kernel_with_driver(uow, driver))

    handle = await client.start(
        {
            "text": "call external service",
            "request_id": "request-external",
            "turn_id": "turn-external",
        },
        _kernel_host(),
    )
    events = [event async for event in handle.events]

    assert driver.starts == 1
    assert events[-1].candidate.status is OutcomeStatus.FAILED
    async with aiosqlite.connect(path) as db:
        row = await (
            await db.execute(
                "SELECT reason_code,producer FROM execution_run_block_signals WHERE root_run_id=?",
                (handle.run_id,),
            )
        ).fetchone()
    assert row == (
        "external_dependency_unavailable",
        "external_dependency_retry",
    )
    await uow.close()


def test_workspace_preflight_raises_typed_block_before_root_start(tmp_path):
    unavailable_root = tmp_path / "not-a-directory"
    unavailable_root.write_text("occupied", encoding="utf-8")
    host = _kernel_host()
    host = HostContext(
        session_id=host.session_id,
        principal_id=host.principal_id,
        auth_epoch=host.auth_epoch,
        capability_hash=host.capability_hash,
        available_capabilities=host.available_capabilities,
        provider_plan=host.provider_plan,
        trace_id=host.trace_id,
        write_scope_root=str(unavailable_root),
        provider_bindings=host.provider_bindings,
    )

    with pytest.raises(PreflightBlocked) as caught:
        ProductTurnIdentityResolver().resolve(
            TurnInput(
                text="create a project",
                session_id="session-1",
                request_id="request-workspace",
                turn_id="turn-workspace",
            ),
            host,
            current_message_id=None,
        )

    assert caught.value.reason_code is RootBlockReasonV1.WORKSPACE_UNAVAILABLE
    assert caught.value.evidence_refs[0].startswith("workspace-resolution:")


@pytest.mark.asyncio
async def test_context_os_missing_runtime_raises_typed_capability_block():
    config = SimpleNamespace(
        features=SimpleNamespace(
            context_os_v1=True,
            summary_quality_loop=False,
        )
    )

    with pytest.raises(PreflightBlocked) as caught:
        await ProductTurnPreparer().prepare_context(
            TurnInput(
                text="create a project",
                session_id="session-1",
                request_id="request-capability",
                turn_id="turn-capability",
                root_run_id="root-capability",
                task_scope_id="task-capability",
            ),
            services={},
            config=config,
            local_llm=SimpleNamespace(model="fixture", base_url="local"),
            tool_registry=SimpleNamespace(has=lambda _name: False),
            current_message_id=None,
            summary_user_is_confused=lambda _text: False,
            summary_latest_task_snapshot=lambda _entries: None,
            summary_build_reinject_msg=lambda _value: {},
        )

    assert caught.value.reason_code is RootBlockReasonV1.CAPABILITY_UNAVAILABLE
    assert caught.value.evidence_refs == ("capability-context:bundle-unavailable",)


def test_provider_binding_ingress_uses_typed_preflight_block_contract():
    source = (
        Path(__file__).parents[2] / "main.py"
    ).read_text(encoding="utf-8")

    assert "isinstance(exc, SessionProviderUnavailable)" in source
    assert "RootBlockReasonV1.PROVIDER_BINDING_UNAVAILABLE" in source
    assert "await _commit_product_preflight_block(" in source
