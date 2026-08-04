from __future__ import annotations

import hashlib
import json

import aiosqlite
import pytest

from deskpet.workflows.adapters.research_runtime import (
    DurableResearchSnapshotPort,
    DurableV5ControlPort,
    WorkflowControlSignalHub,
)
from deskpet.workflows.contracts import NodeExecutionIdentity
from deskpet.workflows.store import RegisteredBlobStore, WorkflowRunStore
from deskpet.workflows.store.research_repository import ResearchWorkflowRepository


class FakeClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


async def _run(path, run_id: str = "run-v5") -> WorkflowRunStore:
    store = WorkflowRunStore(path)
    await store.create_run(
        request_key=f"start:{run_id}",
        session_id="session",
        request_id=f"request:{run_id}",
        turn_id=f"turn:{run_id}",
        workflow_name="deep_research",
        workflow_version="v5",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="capability",
        capability_snapshot={"research": True},
        state_schema_version=5,
        run_id=run_id,
        trace_id=f"trace:{run_id}",
        thread_id=f"thread:{run_id}",
    )
    return store


async def _install_head(path, run_id: str) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """UPDATE workflow_runs SET status='running',run_version=7,
            head_checkpoint_ns='',head_checkpoint_id='head',started_at=900,updated_at=900
            WHERE run_id=?""",
            (run_id,),
        )
        for checkpoint_id, parent in (("brief", None), ("head", "brief")):
            await db.execute(
                """INSERT INTO workflow_checkpoints(
                thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,
                checkpoint_type,checkpoint_blob,metadata_blob,engine_kind,snapshot_version,created_at
                ) VALUES(?,'',?,?,?,'native',X'7B7D',X'7B7D','deskpet-native',1,901)""",
                (f"thread:{run_id}", checkpoint_id, parent, run_id),
            )
            await db.execute(
                """INSERT INTO workflow_checkpoint_owners(
                run_id,thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at
                ) VALUES(?,?,'',?,?,901)""",
                (run_id, f"thread:{run_id}", checkpoint_id, parent),
            )
        await db.commit()


@pytest.mark.asyncio
async def test_durable_control_port_observes_settles_consumes_and_recovers_watchdog(tmp_path):
    path = tmp_path / "workflow.db"
    await _run(path)
    await _install_head(path, "run-v5")
    clock = FakeClock()
    repository = ResearchWorkflowRepository(path, clock=clock)
    opened, _ = await repository.open_control(
        run_id="run-v5",
        idempotency_key="generate-1",
        action="generate_now",
        expected_run_version=7,
        expected_head_checkpoint_ns="",
        expected_head_checkpoint_id="head",
    )
    await repository.accept_generate_now(
        opened["command_id"], brief_checkpoint_ns="", brief_checkpoint_id="brief"
    )
    hub = WorkflowControlSignalHub(repository, poll_interval=0.005)
    port = DurableV5ControlPort(repository, signal_hub=hub, clock=clock)

    observed = await port.poll(
        run_id="run-v5", checkpoint_ns="", checkpoint_id="head"
    )
    assert observed is not None and observed.status == "observed"
    settled = await port.settle(
        observed.command_id,
        checkpoint_ns="",
        checkpoint_id="head",
        result={"route": "insufficient_summary"},
    )
    assert settled.status == "settled"
    consumed = await port.consume(
        observed.command_id,
        checkpoint_ns="",
        checkpoint_id="head",
        result={"delivery_status": "insufficient_evidence"},
    )
    assert consumed.status == "consumed"

    # A fresh run recovers the durable 30-second deadline even without an
    # in-process signal or a separately inserted cancel command row.
    await _run(path, "watchdog")
    await _install_head(path, "watchdog")
    opened, _ = await repository.open_control(
        run_id="watchdog",
        idempotency_key="generate-watchdog",
        action="generate_now",
        expected_run_version=7,
        expected_head_checkpoint_ns="",
        expected_head_checkpoint_id="head",
    )
    await repository.accept_generate_now(
        opened["command_id"], brief_checkpoint_ns="", brief_checkpoint_id="brief"
    )
    clock.value = 1_031.0
    recovered_hub = WorkflowControlSignalHub(repository, poll_interval=0.005)
    recovered_port = DurableV5ControlPort(
        repository, signal_hub=recovered_hub, clock=clock
    )
    command = await recovered_port.poll(
        run_id="watchdog", checkpoint_ns="", checkpoint_id="head"
    )
    assert command is not None and command.action == "cancel_settle"
    assert command.payload["_watchdog"]["reason"] == "settle_deadline_exceeded"
    assert await recovered_hub.cancelled("watchdog")


@pytest.mark.asyncio
async def test_snapshot_port_writes_exact_canonical_bytes_before_parent_pin(tmp_path):
    path = tmp_path / "workflow.db"
    await _run(path)
    identity = NodeExecutionIdentity(
        workflow_name="deep_research",
        workflow_version="v5",
        thread_id="thread:run-v5",
        run_id="run-v5",
        checkpoint_id="head",
        checkpoint_ns="",
        task_id="finalize-task",
        node_id="insufficient_finalize",
        attempt=1,
    )
    blobs = RegisteredBlobStore(tmp_path / "blobs", path)
    passage = await blobs.put(b"winning passage", identity, media_type="text/plain")
    repository = ResearchWorkflowRepository(path, clock=lambda: 1_000.0)
    port = DurableResearchSnapshotPort(blobs=blobs, repository=repository)
    result = await port.persist_research_snapshot(
        run_id="run-v5",
        operation_id="operation-v5",
        values={
            "dimension_coverages": [],
            "passage_blob_refs": [f"sha256:{passage.sha256}"],
            "source_families": [],
            "executed_query_fingerprints": ["query-fingerprint"],
            "budget_summary": {"charged_io": 1},
        },
        execution_identity=identity,
        created_at="2026-07-16T00:00:00+00:00",
        continue_until="2026-08-15T00:00:00+00:00",
    )
    content = dict(result)
    manifest_ref = content.pop("manifest_ref")
    snapshot_hash = content.pop("snapshot_hash")
    encoded = json.dumps(
        content, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert manifest_ref == snapshot_hash == hashlib.sha256(encoded).hexdigest()
    assert await blobs.get(manifest_ref) == encoded
    async with aiosqlite.connect(path) as db:
        pin = await (
            await db.execute(
                """SELECT pin_kind,expires_at FROM workflow_research_snapshot_pins
                WHERE snapshot_hash=?""",
                (snapshot_hash,),
            )
        ).fetchone()
    assert pin is not None and pin[0] == "continue_parent"
