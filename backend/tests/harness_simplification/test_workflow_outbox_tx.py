from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from deskpet.execution import (
    DeliveryPolicy,
    DeliverySpec,
    OutcomeStatus,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunStatus,
    fingerprint_json,
)
from deskpet.workflows.outbox import (
    WorkflowOutbox,
    enqueue_event_tx,
    mutate_delivery_tx,
)
from deskpet.workflows.store import WorkflowRunStore
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


CAPABILITY_HASH = "a" * 64


async def _run_store(path: Path, *, run_id: str, clock=lambda: 100.0) -> WorkflowRunStore:
    store = WorkflowRunStore(path, clock=clock)
    created_run_id, created = await store.create_run(
        request_key=f"request:{run_id}",
        session_id="session",
        request_id="request",
        turn_id="turn",
        workflow_name="deep_research",
        workflow_version="v2",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="capability",
        capability_snapshot={},
        state_schema_version=2,
        run_id=run_id,
    )
    assert created is True
    assert created_run_id == run_id
    return store


def _execution_spec(run_id: str) -> RunCreate:
    return RunCreate(
        run_id=run_id,
        idempotency_key=f"root:session:request:{run_id}",
        context=RunContext(
            session_id="session",
            root_run_id=run_id,
            parent_run_id=None,
            request_id="request",
            turn_id="turn",
            venue="text",
            workspace={},
            capability_hash=CAPABILITY_HASH,
            provider_plan={},
            trace_id=f"trace:{run_id}",
            principal_id="user",
        ),
        payload_fingerprint=fingerprint_json({"run_id": run_id}),
        capability_fingerprint=CAPABILITY_HASH,
        driver_kind="react",
        profile_key="react_short",
        persistence_level="durable",
    )


async def _execution_store_with_delivery(
    path: Path, *, run_id: str
) -> tuple[SqliteExecutionUnitOfWork, str]:
    store = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    await store.create(_execution_spec(run_id))
    final = await store.finalize(
        run_id,
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=RunEventCandidate(
            event_key="terminal",
            kind="run.final",
            status=OutcomeStatus.SUCCEEDED,
            driver_kind="react",
        ),
        deliveries=(
            DeliverySpec(
                sink_kind="session_db",
                sink_instance="local",
                target_id="session",
                policy=DeliveryPolicy.DURABLE_REQUIRED,
            ),
        ),
    )
    rows = await store.list_event_deliveries(final.event.event_id)
    assert len(rows) == 1
    return store, rows[0].delivery_id


@pytest.mark.asyncio
async def test_enqueue_tx_obeys_caller_rollback(tmp_path: Path) -> None:
    store = await _run_store(tmp_path / "rollback.db", run_id="run-rollback")
    db = await store._connect()
    try:
        await db.execute("BEGIN IMMEDIATE")
        event = await enqueue_event_tx(
            db,
            run_id="run-rollback",
            event_key="progress:search",
            event_type="workflow.progress",
            payload={"status": "completed"},
            deliveries=(("session_message", "session"),),
            now=100.0,
        )
        assert len(event["deliveries"]) == 1
        await db.rollback()
    finally:
        await db.close()

    check = await store._connect()
    try:
        event_count = await (
            await check.execute(
                "SELECT COUNT(*) AS count FROM workflow_events WHERE run_id=?",
                ("run-rollback",),
            )
        ).fetchone()
        delivery_count = await (
            await check.execute(
                "SELECT COUNT(*) AS count FROM workflow_deliveries WHERE run_id=?",
                ("run-rollback",),
            )
        ).fetchone()
        run = await (
            await check.execute(
                "SELECT event_seq FROM workflow_runs WHERE run_id=?", ("run-rollback",)
            )
        ).fetchone()
        assert event_count["count"] == 0
        assert delivery_count["count"] == 0
        assert run["event_seq"] == 0
    finally:
        await check.close()


@pytest.mark.asyncio
async def test_claim_and_complete_tx_are_idempotent_in_one_transaction(tmp_path: Path) -> None:
    store = await _run_store(tmp_path / "idempotent.db", run_id="run-idempotent")
    outbox = WorkflowOutbox(store, clock=lambda: 100.0)
    event = await outbox.ensure_event(
        run_id="run-idempotent",
        event_key="terminal",
        event_type="workflow.final",
        payload={"status": "completed"},
        deliveries=(("session_message", "session"),),
    )
    delivery_id = event["deliveries"][0]["delivery_id"]

    db = await store._connect()
    try:
        await db.execute("BEGIN IMMEDIATE")
        claimed = await mutate_delivery_tx(
            db, delivery_id, action="begin", expected_version=0, now=101.0
        )
        duplicate_claim = await mutate_delivery_tx(
            db, delivery_id, action="begin", expected_version=0, now=102.0
        )
        completed = await mutate_delivery_tx(
            db,
            delivery_id,
            action="delivered",
            expected_version=claimed["delivery"]["version"],
            now=103.0,
        )
        duplicate_complete = await mutate_delivery_tx(
            db,
            delivery_id,
            action="delivered",
            expected_version=claimed["delivery"]["version"],
            now=104.0,
        )
        await db.commit()
    finally:
        await db.close()

    assert claimed["idempotent"] is False
    assert duplicate_claim["idempotent"] is True
    assert duplicate_claim["delivery"] == claimed["delivery"]
    assert completed["idempotent"] is False
    assert duplicate_complete["idempotent"] is True
    assert duplicate_complete["delivery"] == completed["delivery"]
    assert completed["delivery"]["status"] == "delivered"
    assert completed["delivery"]["attempts"] == 1


@pytest.mark.asyncio
async def test_legacy_facade_matches_direct_tx_primitives(tmp_path: Path) -> None:
    direct_store = await _run_store(tmp_path / "direct.db", run_id="run-parity")
    facade_store = await _run_store(tmp_path / "facade.db", run_id="run-parity")
    direct_db = await direct_store._connect()
    try:
        await direct_db.execute("BEGIN IMMEDIATE")
        direct_event = await enqueue_event_tx(
            direct_db,
            run_id="run-parity",
            event_key="progress:parity",
            event_type="workflow.progress",
            payload={"stage": "parity"},
            deliveries=(("session_message", "session"),),
            now=100.0,
        )
        direct_claim = await mutate_delivery_tx(
            direct_db,
            direct_event["deliveries"][0]["delivery_id"],
            action="begin",
            expected_version=0,
            now=100.0,
        )
        await direct_db.commit()
    finally:
        await direct_db.close()

    facade = WorkflowOutbox(facade_store, clock=lambda: 100.0)
    facade_event = await facade.ensure_event(
        run_id="run-parity",
        event_key="progress:parity",
        event_type="workflow.progress",
        payload={"stage": "parity"},
        deliveries=(("session_message", "session"),),
    )
    facade_claim = await facade.mutate_delivery(
        facade_event["deliveries"][0]["delivery_id"],
        action="begin",
        expected_version=0,
    )

    assert facade_event == direct_event
    assert facade_claim == direct_claim


@pytest.mark.asyncio
async def test_execution_claim_and_complete_tx_obey_caller_rollback(tmp_path: Path) -> None:
    store, delivery_id = await _execution_store_with_delivery(
        tmp_path / "execution-rollback.db", run_id="execution-rollback"
    )
    db = await store._connect()
    try:
        await db.execute("BEGIN IMMEDIATE")
        claimed = await store.claim_delivery_tx(db, now=101.0)
        assert claimed is not None
        assert claimed.delivery_id == delivery_id
        duplicate_claim = await store.claim_delivery_tx(db, now=101.0)
        assert duplicate_claim is None
        completed = await store.complete_delivery_tx(
            db,
            delivery_id,
            expected_version=claimed.delivery_version,
            now=102.0,
        )
        assert completed.status.value == "delivered"
        duplicate_complete = await store.complete_delivery_tx(
            db,
            delivery_id,
            expected_version=claimed.delivery_version,
            now=103.0,
        )
        assert duplicate_complete == completed
        await db.rollback()
    finally:
        await db.close()

    event_id = completed.event_id
    rows = await store.list_event_deliveries(event_id)
    assert len(rows) == 1
    assert rows[0].status.value == "pending"
    assert rows[0].attempts == 0
    assert rows[0].delivery_version == 0


@pytest.mark.asyncio
async def test_execution_facade_matches_direct_tx_primitives(tmp_path: Path) -> None:
    direct, direct_id = await _execution_store_with_delivery(
        tmp_path / "execution-direct.db", run_id="execution-parity"
    )
    facade, facade_id = await _execution_store_with_delivery(
        tmp_path / "execution-facade.db", run_id="execution-parity"
    )
    assert direct_id == facade_id

    db = await direct._connect()
    try:
        await db.execute("BEGIN IMMEDIATE")
        direct_claim = await direct.claim_delivery_tx(db, now=100.0)
        assert direct_claim is not None
        direct_release = await direct.release_delivery_tx(
            db,
            direct_id,
            expected_version=direct_claim.delivery_version,
            error="temporary",
            retry_at=105.0,
            discard=False,
            now=100.0,
        )
        await db.commit()
    finally:
        await db.close()

    facade_claim = await facade.claim_delivery()
    assert facade_claim is not None
    facade_release = await facade.release_delivery(
        facade_id,
        expected_version=facade_claim.delivery_version,
        error="temporary",
        retry_at=105.0,
        discard=False,
    )
    assert facade_claim == direct_claim
    assert facade_release == direct_release


def test_tx_primitives_never_own_connection_or_transaction_lifecycle() -> None:
    primitives = (
        enqueue_event_tx,
        mutate_delivery_tx,
    )
    forbidden_attributes = {"_connect", "connect", "commit", "rollback", "close"}
    for primitive in primitives:
        source = inspect.getsource(primitive)
        tree = ast.parse(source)
        used_attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert used_attributes.isdisjoint(forbidden_attributes), primitive.__name__
        assert "BEGIN IMMEDIATE" not in source, primitive.__name__

    ensure_source = inspect.getsource(WorkflowOutbox.ensure_event)
    mutate_source = inspect.getsource(WorkflowOutbox.mutate_delivery)
    assert "enqueue_event_tx" in ensure_source
    assert "mutate_delivery_tx" in mutate_source

    execution_primitives = (
        SqliteExecutionUnitOfWork.claim_delivery_tx,
        SqliteExecutionUnitOfWork.complete_delivery_tx,
        SqliteExecutionUnitOfWork.release_delivery_tx,
    )
    for primitive in execution_primitives:
        source = textwrap.dedent(inspect.getsource(primitive))
        tree = ast.parse(source)
        used_attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert used_attributes.isdisjoint(forbidden_attributes), primitive.__name__
        assert "BEGIN IMMEDIATE" not in source, primitive.__name__

    assert "claim_delivery_tx" in inspect.getsource(
        SqliteExecutionUnitOfWork.claim_delivery
    )
    assert "complete_delivery_tx" in inspect.getsource(
        SqliteExecutionUnitOfWork.complete_delivery
    )
    assert "release_delivery_tx" in inspect.getsource(
        SqliteExecutionUnitOfWork.release_delivery
    )
