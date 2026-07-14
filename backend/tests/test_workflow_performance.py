from __future__ import annotations

import os
import time

import pytest

from deskpet.workflows.outbox import WorkflowOutbox
from deskpet.workflows.store import WorkflowRunStore


OPERATION_COUNT = 100
DEFAULT_WINDOWS_CI_LIMIT_SECONDS = 30.0


async def _create_run(store: WorkflowRunStore, index: int) -> str:
    run_id, created = await store.create_run(
        request_key=f"perf-request-{index}",
        session_id="perf-session",
        request_id=f"request-{index}",
        turn_id=f"turn-{index}",
        workflow_name="perf-workflow",
        workflow_version="1",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="capability",
        capability_snapshot={},
        state_schema_version=1,
    )
    assert created is True
    return run_id


@pytest.mark.asyncio
async def test_100_run_event_and_list_round_trips_stay_within_local_ci_gate(tmp_path):
    """Exercise real SQLite I/O; the threshold is intentionally broad for Windows CI."""

    store = WorkflowRunStore(tmp_path / "workflow.db")
    outbox = WorkflowOutbox(store)

    warmup_id = await _create_run(store, -1)
    await outbox.ensure_event(
        run_id=warmup_id,
        event_key="warmup",
        event_type="workflow.progress",
        payload={"index": -1},
    )
    assert await store.list_runs(session_id="perf-session", limit=1)

    started = time.perf_counter()
    run_ids: list[str] = []
    for index in range(OPERATION_COUNT):
        run_id = await _create_run(store, index)
        run_ids.append(run_id)
        event = await outbox.ensure_event(
            run_id=run_id,
            event_key="created",
            event_type="workflow.progress",
            payload={"index": index},
        )
        assert event["seq"] == 1
        listed = await store.list_runs(session_id="perf-session", limit=100)
        assert any(row["run_id"] == run_id for row in listed)
    elapsed = time.perf_counter() - started

    latest = await outbox.events_after(run_ids[-1], 0)
    assert len(set(run_ids)) == OPERATION_COUNT
    assert len(await store.list_runs(session_id="perf-session", limit=100)) == 100
    assert [event["payload"]["index"] for event in latest["events"]] == [99]

    limit = float(
        os.environ.get(
            "DESKPET_WORKFLOW_PERF_LIMIT_SECONDS",
            DEFAULT_WINDOWS_CI_LIMIT_SECONDS,
        )
    )
    assert elapsed < limit, (
        f"{OPERATION_COUNT} run/event/list round trips took {elapsed:.3f}s "
        f"(limit {limit:.3f}s)"
    )
