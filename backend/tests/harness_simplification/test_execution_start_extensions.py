from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from deskpet.execution.contracts import (
    ActorContext,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunNotFound,
    RunRef,
    RunRecord,
    RunStartSnapshotRecord,
    RunStatus,
    canonical_json,
    fingerprint_json,
    thaw_json,
)
from deskpet.harness.contracts import HostExtensionRefV1, PreparedRunContextV1
from deskpet.harness.kernel import RunKernel
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


def _spec(run_id: str) -> RunCreate:
    capability_hash = fingerprint_json({"capabilities": []})
    return RunCreate(
        run_id=run_id,
        idempotency_key=f"root:{run_id}",
        context=RunContext(
            session_id="session-1",
            root_run_id=run_id,
            parent_run_id=None,
            request_id=f"request-{run_id}",
            turn_id=f"turn-{run_id}",
            venue="text",
            workspace={},
            capability_hash=capability_hash,
            provider_plan={},
            trace_id=f"trace-{run_id}",
            principal_id="principal-1",
        ),
        payload_fingerprint=fingerprint_json({"text": run_id}),
        capability_fingerprint=capability_hash,
        driver_kind="react",
        profile_key="agent.general",
        persistence_level=PersistenceLevel.DURABLE,
    )


def _snapshot(spec: RunCreate) -> RunStartSnapshotRecord:
    empty_list = canonical_json([])
    empty_map = canonical_json({})
    terminal_deliveries = [
        {
            "policy": "durable_required",
            "sink_instance": "main",
            "sink_kind": "session_message",
            "target_id": "session-1",
        }
    ]
    payload = {
        "run_id": spec.run_id,
        "run_spec": spec.to_dict(),
        "terminal_deliveries": terminal_deliveries,
    }
    return RunStartSnapshotRecord(
        run_id=spec.run_id,
        snapshot_schema_version=1,
        start_fingerprint=fingerprint_json(payload),
        canonical_messages_json=empty_list,
        session_cursor_json=empty_map,
        prepared_refs_json=empty_map,
        sanitized_request_json=empty_map,
        run_context_json=canonical_json(spec.context.to_dict()),
        run_spec_json=canonical_json(spec.to_dict()),
        capability_snapshot_json=empty_map,
        capability_snapshot_hash=fingerprint_json({}),
        provider_launch_policy_json=empty_map,
        terminal_deliveries_json=canonical_json(terminal_deliveries),
        terminal_deliveries_hash=fingerprint_json(terminal_deliveries),
        capability_lease_intent_ref=None,
        capability_lease_intent_hash=None,
        created_at=1.0,
    )


@pytest.mark.asyncio
async def test_read_run_restores_immutable_provider_plan(tmp_path):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    base = _spec("run-provider-plan")
    spec = replace(
        base,
        context=replace(
            base.context,
            provider_plan={"providers": ["local-ollama", "relay-cloud"]},
        ),
    )
    await uow.create(spec)

    restored = await uow.read_run(spec.run_id)

    assert restored is not None
    assert thaw_json(restored.spec.context.provider_plan) == {
        "providers": ["local-ollama", "relay-cloud"]
    }
    assert restored.spec.context.session_id == spec.context.session_id
    await uow.close()


class _StartExtension:
    descriptor = HostExtensionRefV1(
        "deskpet.test.start", "start-receipt-1", "a" * 64
    )

    async def apply_start_commit(self, transaction, *, spec, start_snapshot):
        assert transaction is not None
        assert start_snapshot.run_id == spec.run_id
        return self.descriptor


class _FailingStartExtension(_StartExtension):
    async def apply_start_commit(self, transaction, *, spec, start_snapshot):
        raise RuntimeError("injected-start-extension-failure")


class _TerminalExtension:
    descriptor = HostExtensionRefV1(
        "deskpet.test.terminal", "terminal-receipt-1", "b" * 64
    )

    async def apply_terminal_commit(self, transaction, *, record, terminal_event):
        assert transaction is not None
        assert terminal_event.kind == "run.final"
        return self.descriptor


@pytest.mark.asyncio
async def test_start_extension_receipt_is_atomic_with_run_and_snapshot(tmp_path):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    spec = _spec("run-success")
    await uow.create_with_start_snapshot(
        spec,
        _snapshot(spec),
        start_commit_extensions=(_StartExtension(),),
    )

    stored = await uow.read_run_start_snapshot(spec.run_id)
    assert stored is not None
    assert "start-receipt-1" in stored.start_extension_receipts_json
    await uow.close()


@pytest.mark.asyncio
async def test_start_extension_failure_rolls_back_run_and_snapshot(tmp_path):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    spec = _spec("run-rollback")
    with pytest.raises(RuntimeError, match="injected-start-extension-failure"):
        await uow.create_with_start_snapshot(
            spec,
            _snapshot(spec),
            start_commit_extensions=(_FailingStartExtension(),),
        )

    assert await uow.read_run_start_snapshot(spec.run_id) is None
    with pytest.raises(RunNotFound):
        await uow.query(
            RunRef(spec.run_id, spec.context.session_id),
            ActorContext(
                principal_id=spec.context.principal_id,
                session_id=spec.context.session_id,
                auth_epoch=0,
            ),
        )
    await uow.close()


@pytest.mark.asyncio
async def test_terminal_extension_receipt_commits_with_terminal_event(tmp_path):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    spec = _spec("run-terminal")
    created = await uow.create_with_start_snapshot(spec, _snapshot(spec))
    event = RunEventCandidate(
        event_key="run:final",
        kind="run.final",
        status=OutcomeStatus.SUCCEEDED,
        driver_kind="react",
        payload={"text": "done"},
    )

    result = await uow.commit_run_outcome(
        spec.run_id,
        expected_version=created.record.version,
        terminal_status=RunStatus.COMPLETED,
        event=event,
        terminal_commit_extensions=(_TerminalExtension(),),
    )

    receipts = await uow.read_terminal_extension_receipts(
        spec.run_id, result.event.event_id
    )
    assert tuple(item["ref"] for item in receipts) == ("terminal-receipt-1",)
    await uow.close()


@pytest.mark.asyncio
async def test_kernel_forwards_terminal_extensions_to_child_finalize():
    child_base = _spec("child-terminal")
    child_spec = replace(
        child_base,
        context=replace(
            child_base.context,
            root_run_id="parent-terminal",
            parent_run_id="parent-terminal",
        ),
    )
    record = RunRecord(
        spec=child_spec,
        status=RunStatus.RUNNING,
        persistence_level=PersistenceLevel.DURABLE,
        version=1,
        durable_seq=1,
        terminal_event_id=None,
        created_at=1.0,
        updated_at=1.0,
        started_at=1.0,
    )

    class Uow:
        def __init__(self):
            self.kwargs = None

        async def get_child_command_for_run(self, run_id):
            assert run_id == record.run_id
            return SimpleNamespace(operation_id="operation-child-terminal")

        async def finalize_child_and_enqueue_parent_signal(
            self, operation_id, **kwargs
        ):
            assert operation_id == "operation-child-terminal"
            self.kwargs = kwargs
            final = replace(
                record,
                status=RunStatus.COMPLETED,
                version=2,
                durable_seq=2,
                terminal_event_id="event-child-terminal",
                updated_at=2.0,
                ended_at=2.0,
            )
            return SimpleNamespace(
                record=final,
                event=SimpleNamespace(event_id="event-child-terminal"),
            )

    uow = Uow()
    kernel = RunKernel(uow=uow, router=object(), drivers={})
    active = kernel._live.add(record.run_id, record.context.actor())
    active.record = record
    extension = _TerminalExtension()
    active.prepared_context = PreparedRunContextV1(
        persistence_required=True,
        terminal_commit_extensions=(extension,),
    )
    event = RunEventCandidate(
        event_key="terminal:child-terminal",
        kind="run.final",
        status=OutcomeStatus.SUCCEEDED,
        driver_kind="react",
        payload={"text": "done"},
    )

    await kernel._finalize(
        record,
        expected_version=record.version,
        status=RunStatus.COMPLETED,
        event=event,
    )

    assert uow.kwargs is not None
    assert uow.kwargs["terminal_commit_extensions"] == (extension,)
