from __future__ import annotations

import asyncio
import json

import aiosqlite
import pytest

from deskpet.workflows.definitions.deep_research_v6_evidence import EvidenceFactBatchV1
from deskpet.workflows.definitions.v6.deep_research import (
    decode_continuation_snapshot,
    initial_state,
)
from deskpet.workflows.launcher import WorkflowLauncher
from deskpet.workflows.service import WorkflowService
from deskpet.workflows.store.research_repository import ResearchWorkflowRepository
from tests.test_deep_research_v6_production_runner import TOPICS, _runtime


class _CrashAfterTerminalCommitService(WorkflowService):
    """Model process loss after runner terminal commit but before snapshot pin."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.crashed_after_terminal = False

    async def persist_v6_continuation_snapshot(self, run_id: str) -> bool:
        if not self.crashed_after_terminal:
            self.crashed_after_terminal = True
            raise asyncio.CancelledError()
        return await super().persist_v6_continuation_snapshot(run_id)


async def _json_ref(blobs, ref: str) -> dict:
    return json.loads((await blobs.get(ref.removeprefix("sha256:"))).decode("utf-8"))


@pytest.mark.asyncio
async def test_created_child_startup_scan_hydrates_snapshot_runs_eleven_nodes_and_recovers_pin(
    tmp_path,
):
    # Build a real insufficient terminal parent so continuation is admitted by
    # the production repository contract rather than by a test-only fixture.
    database, parent_store, parent_runner, parent_id, parent_context, _, _, blobs = (
        await _runtime(tmp_path, "comparison", force_llm_deadline=True)
    )
    repository = ResearchWorkflowRepository(database, blob_root=tmp_path / "blobs")
    await repository.ensure_root_lineage(
        run_id=parent_id,
        operation_id=f"research:{parent_id}",
        budget_lease_id=f"budget:{parent_id}",
    )
    parent_result = await parent_runner.run(
        parent_id,
        initial_state(
            topic=TOPICS["comparison"],
            run_id=parent_id,
            session_id="session-comparison",
        ),
        parent_context,
    )
    assert parent_result.output["values"]["answer_status"] == "insufficient_evidence"

    parent_service = WorkflowService(
        run_store=parent_store,
        runner=parent_runner,
        research_repository=repository,
        research_snapshot_loader=blobs.get,
    )
    assert await parent_service.persist_v6_continuation_snapshot(parent_id)
    created = await repository.create_or_get_continuation_v6(parent_id, "caller-startup")
    child_id = created.child_run_id
    persisted_start = dict(created.start_payload)
    inherited = decode_continuation_snapshot(
        await repository.load_continuation_snapshot_v6(child_id)
    )
    inherited_fact_refs = list(inherited["fact_batch_refs"])
    inherited_head = str(inherited["evidence_head_hash"])

    # Simulate commit-before-notify process loss: construct a fresh runner,
    # service, launcher, and adapter. Recovery starts solely from its generic
    # status=created scan and the repository-persisted start payload.
    (
        _,
        child_store,
        child_runner,
        _,
        child_context,
        _,
        _,
        child_blobs,
    ) = await _runtime(
        tmp_path,
        "comparison",
        run_id=child_id,
        force_llm_deadline=True,
    )
    restart_repository = ResearchWorkflowRepository(
        database, blob_root=tmp_path / "blobs"
    )
    hydrated_calls: list[dict] = []

    async def hydrate_state(**values):
        hydrated_calls.append(dict(values))
        assert {
            key: values[key]
            for key in ("schema_version", "parent_run_id", "source_snapshot_hash")
        } == persisted_start
        snapshot = decode_continuation_snapshot(
            await restart_repository.load_continuation_snapshot_v6(str(values["run_id"]))
        )
        spec = await _json_ref(child_blobs, str(snapshot["spec_ref"]))
        return initial_state(
            topic=str(spec["normalized_question"]),
            run_id=str(values["run_id"]),
            thread_id=str(values["thread_id"]),
            session_id=str(values["session_id"]),
            answer_locale=str(spec.get("answer_locale") or "zh-CN"),
            schema_version=int(values["schema_version"]),
            parent_run_id=str(values["parent_run_id"]),
            source_snapshot_hash=str(values["source_snapshot_hash"]),
            continuation_snapshot=snapshot,
        )

    crash_service = _CrashAfterTerminalCommitService(
        run_store=child_store,
        runner=child_runner,
        research_repository=restart_repository,
        research_snapshot_loader=child_blobs.get,
    )
    launcher = WorkflowLauncher(crash_service)
    crash_service.launcher = launcher
    launcher.register_adapter(
        "deep_research",
        "v6",
        state_factory=hydrate_state,
        context_factory=lambda: child_context,
    )

    assert await launcher.recover_pending() == [child_id]
    tasks = tuple(launcher._tasks)
    assert tasks
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    assert any(isinstance(item, asyncio.CancelledError) for item in outcomes)
    assert len(hydrated_calls) == 1

    child_row = await child_store.get_run(child_id)
    assert child_row is not None and child_row["status"] == "completed"
    head = await child_runner.saver.load_head(child_id)
    assert head is not None
    values = head["state"]["values"]
    assert values["spec_hash"] == inherited["spec_hash"]
    assert values["policy_refs"] == inherited["policy_refs"]
    assert values["inherited_provenance_refs"] == inherited["provenance_refs"]
    assert values["fact_batch_refs"][: len(inherited_fact_refs)] == inherited_fact_refs
    assert len(values["fact_batch_refs"]) > len(inherited_fact_refs)
    first_new_batch = EvidenceFactBatchV1.from_json(
        await _json_ref(child_blobs, values["fact_batch_refs"][len(inherited_fact_refs)])
    )
    assert first_new_batch.previous_head_hash == inherited_head

    async with aiosqlite.connect(database) as db:
        node_ids = {
            row[0]
            for row in await (
                await db.execute(
                    """SELECT node_id FROM workflow_nodes
                    WHERE run_id=? AND latest_status='succeeded'""",
                    (child_id,),
                )
            ).fetchall()
        }
        pre_recovery_snapshots = (
            await (
                await db.execute(
                    "SELECT COUNT(*) FROM workflow_research_snapshots WHERE run_id=?",
                    (child_id,),
                )
            ).fetchone()
        )[0]
    assert node_ids == {
        "compile_spec",
        "plan_route",
        "load_pages",
        "extract_candidate_bundles",
        "admit_facts",
        "synthesize_inferences",
        "register_inferences",
        "assess_answer",
        "render_claims",
        "integrity",
        "persist_manifest",
    }
    assert pre_recovery_snapshots == 0

    # A new process startup hook repairs the terminal-commit -> pin window.
    recovery_repository = ResearchWorkflowRepository(
        database, blob_root=tmp_path / "blobs"
    )
    recovery_service = WorkflowService(
        run_store=child_store,
        runner=child_runner,
        research_repository=recovery_repository,
        research_snapshot_loader=child_blobs.get,
    )
    recovered = await recovery_service.recover_v6_continuation_snapshots()
    assert child_id in recovered
    async with aiosqlite.connect(database) as db:
        snapshot_count = (
            await (
                await db.execute(
                    "SELECT COUNT(*) FROM workflow_research_snapshots WHERE run_id=?",
                    (child_id,),
                )
            ).fetchone()
        )[0]
        pin_count = (
            await (
                await db.execute(
                    """SELECT COUNT(*) FROM workflow_research_snapshot_pins
                    WHERE run_id=? AND pin_kind='continue_parent'""",
                    (child_id,),
                )
            ).fetchone()
        )[0]
    assert (snapshot_count, pin_count) == (1, 1)

    # The recovered child is now itself a valid server-owned continuation
    # parent. Create a real grandchild and prove a second fresh launcher can
    # hydrate only from its canonical persisted start payload and the child's
    # newly committed snapshot (no synthetic terminal rows/events).
    grandchild = await recovery_repository.create_or_get_continuation_v6(
        child_id, "caller-grandchild"
    )
    grandchild_id = grandchild.child_run_id
    grandchild_start = dict(grandchild.start_payload)
    grandchild_inherited = decode_continuation_snapshot(
        await recovery_repository.load_continuation_snapshot_v6(grandchild_id)
    )
    child_snapshot = decode_continuation_snapshot(
        await _json_ref(child_blobs, values["continuation_snapshot_ref"])
    )
    assert grandchild_inherited == child_snapshot
    assert grandchild_inherited["spec_hash"] == inherited["spec_hash"]
    assert grandchild_inherited["evidence_head_hash"] == values["evidence_head_hash"]
    assert grandchild_inherited["fact_batch_refs"] == values["fact_batch_refs"]
    assert grandchild_inherited["closure_refs"] == child_snapshot["closure_refs"]

    (
        _,
        grandchild_store,
        grandchild_runner,
        _,
        grandchild_context,
        _,
        _,
        grandchild_blobs,
    ) = await _runtime(
        tmp_path,
        "comparison",
        run_id=grandchild_id,
        force_llm_deadline=True,
    )
    grandchild_repository = ResearchWorkflowRepository(
        database, blob_root=tmp_path / "blobs"
    )
    hydrated_grandchild_states: list[dict] = []

    async def hydrate_grandchild(**start_values):
        assert {
            key: start_values[key]
            for key in ("schema_version", "parent_run_id", "source_snapshot_hash")
        } == grandchild_start
        snapshot = decode_continuation_snapshot(
            await grandchild_repository.load_continuation_snapshot_v6(
                str(start_values["run_id"])
            )
        )
        spec = await _json_ref(grandchild_blobs, str(snapshot["spec_ref"]))
        state = initial_state(
            topic=str(spec["normalized_question"]),
            run_id=str(start_values["run_id"]),
            thread_id=str(start_values["thread_id"]),
            session_id=str(start_values["session_id"]),
            answer_locale=str(spec.get("answer_locale") or "zh-CN"),
            schema_version=int(start_values["schema_version"]),
            parent_run_id=str(start_values["parent_run_id"]),
            source_snapshot_hash=str(start_values["source_snapshot_hash"]),
            continuation_snapshot=snapshot,
        )
        hydrated_grandchild_states.append(state)
        return state

    grandchild_service = WorkflowService(
        run_store=grandchild_store,
        runner=grandchild_runner,
        research_repository=grandchild_repository,
        research_snapshot_loader=grandchild_blobs.get,
    )
    grandchild_launcher = WorkflowLauncher(grandchild_service)
    grandchild_service.launcher = grandchild_launcher
    grandchild_launcher.register_adapter(
        "deep_research",
        "v6",
        state_factory=hydrate_grandchild,
        context_factory=lambda: grandchild_context,
    )
    assert await grandchild_launcher.recover_pending() == [grandchild_id]
    grandchild_tasks = tuple(grandchild_launcher._tasks)
    assert grandchild_tasks
    assert await asyncio.gather(*grandchild_tasks, return_exceptions=True) == [None]
    assert len(hydrated_grandchild_states) == 1
    grandchild_initial = hydrated_grandchild_states[0]
    assert grandchild_initial["values"]["spec_hash"] == inherited["spec_hash"]
    assert grandchild_initial["values"]["evidence_head_hash"] == values[
        "evidence_head_hash"
    ]
    assert grandchild_initial["values"]["fact_batch_refs"] == values[
        "fact_batch_refs"
    ]
    assert {
        "sha256:" + str(item["sha256"])
        for item in grandchild_initial["blob_refs"]
    } == set(grandchild_inherited["closure_refs"])

    async with aiosqlite.connect(database) as db:
        lineage = [
            tuple(row)
            for row in await (
                await db.execute(
                    """SELECT run_id,parent_run_id,parent_operation_id,snapshot_hash
                    FROM workflow_research_lineage
                    WHERE run_id IN (?,?,?) ORDER BY created_at,run_id""",
                    (parent_id, child_id, grandchild_id),
                )
            ).fetchall()
        ]
        heads = {
            tuple(row)
            for row in await (
                await db.execute(
                    """SELECT parent_run_id,child_run_id
                    FROM workflow_research_continuation_heads
                    WHERE parent_run_id IN (?,?)""",
                    (parent_id, child_id),
                )
            ).fetchall()
        }
    by_run = {row[0]: row for row in lineage}
    assert by_run[parent_id][1:] == (None, None, None)
    assert by_run[child_id][1:] == (
        parent_id,
        f"research:{parent_id}",
        inherited["snapshot_hash"],
    )
    assert by_run[grandchild_id][1:] == (
        child_id,
        f"research:{child_id}",
        child_snapshot["snapshot_hash"],
    )
    assert heads == {(parent_id, child_id), (child_id, grandchild_id)}
