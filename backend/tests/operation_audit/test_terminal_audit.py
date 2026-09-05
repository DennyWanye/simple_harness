"""Real foreground factory + public SDK snapshots; no network Provider."""

from __future__ import annotations

import ast
import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import simple_harness as sdk
from deskpet.memory.human_memory_service import (
    HumanMemoryHostServiceFactory,
    QueueTurnRequest,
)
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.operation_audit.composition import compose_terminal_audit
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.execution.test_primary_foreground_runtime import Provider, build

pytestmark = pytest.mark.asyncio


async def setup(tmp_path, *, wake=None, ready=None, provider=None):
    state = tmp_path / "state.db"
    epoch = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, epoch).bind(local_owner_auth())
    await service.open_primary()
    provider = provider or Provider()
    runtime, stack, queue = await build(
        tmp_path, state, provider, terminal_audit_wake=wake
    )
    if ready is not None:
        await ready(stack)
    await service.enqueue_turn(QueueTurnRequest(None, "request", "PRIVATE_USER_CANARY"))
    assert await asyncio.wait_for(runtime._drive_once(), 15)
    assert runtime.last_error is None
    return state, service, provider, runtime, stack, queue


async def drain(consumer):
    for _ in range(50):
        if not await consumer.tick(max_pages=4):
            break
    else:
        pytest.fail("bounded terminal audit fixture did not drain")


async def test_actual_terminal_factory_discovers_without_wake_and_preserves_safe_pages(
    tmp_path,
):
    state, _, provider, runtime, stack, _ = await setup(tmp_path)
    consumer = await compose_terminal_audit(
        state,
        stack_getter=lambda: stack,
        subject=local_owner_auth().subject,
        start=False,
        page_size=2,
    )
    try:
        sources = await consumer.sources.read()
        assert len(sources) == 1
        assert consumer.store.path != state
        await drain(consumer)
        record = await consumer.store.inspect(sources[0].job_id)
        job = record["job"]
        if not hasattr(sdk, "RunOperationAuditPageV1"):
            assert job["status"] == "unavailable"
            assert job["last_code"] == "capability_unavailable"
            assert record["pages"] == record["findings"] == []
            return
        assert job["status"] == "enumerated"
        assert job["total_pages"] > 1
        pages = [json.loads(p["payload_json"]) for p in record["pages"]]
        assert len(pages) == job["total_pages"]
        assert sum(len(p["operations"]) for p in pages) == job["total_operations"]
        assert {p["snapshot_hash"] for p in pages} == {job["snapshot_hash"]}
        assert sorted(p["page_index"] for p in pages) == list(range(job["total_pages"]))
        assert all(a["outcome"] == "returned" for a in record["attempts"])
        assert all(a["settled_at"] >= a["started_at"] for a in record["attempts"])
        text = json.dumps(record)
        for secret in (
            "PRIVATE_USER_CANARY",
            "PRIVATE_CANARY",
            "HIDDEN_CANARY",
            "fixture-opaque",
        ):
            assert secret not in text
        assert len(provider.requests) == 1
        with sqlite3.connect(state) as db:
            assert (
                db.execute(
                    "SELECT COUNT(*) FROM foreground_terminal_receipts"
                ).fetchone()[0]
                == 1
            )
            assert (
                db.execute("SELECT COUNT(*) FROM memory_ingestion_outbox").fetchone()[0]
                == 1
            )
        await drain(consumer)
        assert await consumer.store.inspect(sources[0].job_id) == record
    finally:
        await consumer.close()
        await runtime.close()
        await stack.close()


async def test_actual_main_factory_starts_and_terminal_wakes_lane(tmp_path):
    # Execute the exact small production activation function, not a fake factory.
    # Importing main's unrelated global boot would start/configure other products.
    main_path = Path(__file__).parents[2] / "main.py"
    tree = ast.parse(main_path.read_text())
    node = next(
        n
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef)
        and n.name == "_activate_terminal_operation_audit"
    )
    services = {}
    namespace = {
        "service_context": SimpleNamespace(
            get=services.get, register=services.__setitem__
        ),
        "_state_db_path": tmp_path / "state.db",
        "_sdk_runtime_stack": None,
    }
    exec(  # noqa: S102 - execute only the exact checked-in activation function
        compile(ast.Module(body=[node], type_ignores=[]), str(main_path), "exec"),
        namespace,
    )
    consumer = None

    async def ready(stack):
        nonlocal consumer
        namespace["_sdk_runtime_stack"] = stack
        consumer = await namespace["_activate_terminal_operation_audit"]()
        assert consumer._task is not None
        consumer.poll_seconds = 0.01

    wake_count = []

    def wake():
        wake_count.append(True)
        consumer.wake()

    _state, _, provider, runtime, stack, _ = await setup(
        tmp_path, wake=wake, ready=ready
    )
    try:
        assert wake_count == [True]
        assert await namespace["_activate_terminal_operation_audit"]() is consumer
        consumer.wake()
        for _ in range(200):
            with consumer.store.connect() as db:
                rows = db.execute("SELECT status FROM audit_jobs").fetchall()
            if rows and rows[0][0] in {"enumerated", "unavailable"}:
                break
            await asyncio.sleep(0.01)
        assert rows
        assert rows[0][0] == (
            "enumerated" if hasattr(sdk, "RunOperationAuditPageV1") else "unavailable"
        )
        assert len(provider.requests) == 1
        # main passes this exact lane's wake into the actual foreground constructor.
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "ForegroundRuntimeExecutionAuthority"
        ]
        assert len(calls) == 1
        assert any(
            k.arg == "terminal_audit_wake"
            and ast.unparse(k.value)
            == "_terminal_audit.wake if _terminal_audit is not None else None"
            for k in calls[0].keywords
        )
    finally:
        await runtime.close()
        await consumer.close()
        await stack.close()


async def test_actual_main_storage_failure_does_not_block_foreground(tmp_path):
    node = next(
        n
        for n in ast.parse((Path(__file__).parents[2] / "main.py").read_text()).body
        if isinstance(n, ast.AsyncFunctionDef)
        and n.name == "_activate_terminal_operation_audit"
    )
    services, warnings = {}, []
    namespace = {
        "service_context": SimpleNamespace(
            get=services.get, register=services.__setitem__
        ),
        "_state_db_path": tmp_path / "state.db",
        "_sdk_runtime_stack": None,
        "logger": SimpleNamespace(warning=warnings.append),
    }
    exec(  # noqa: S102 - exact production factory failure path
        compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), namespace
    )
    (tmp_path / "operation-audit.db").mkdir()  # Actual sqlite open failure.

    async def ready(stack):
        namespace["_sdk_runtime_stack"] = stack
        assert await namespace["_activate_terminal_operation_audit"]() is None

    _, _, provider, runtime, stack, _ = await setup(tmp_path, ready=ready)
    try:
        assert services["terminal_operation_audit_status"] == "storage_unavailable"
        assert warnings == ["terminal_operation_audit_storage_unavailable"]
        assert len(provider.requests) == 1
        assert runtime.last_error is None
    finally:
        await runtime.close()
        await stack.close()
