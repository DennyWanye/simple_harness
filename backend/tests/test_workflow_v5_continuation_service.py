from __future__ import annotations

import json
import time

import aiosqlite
import pytest

from deskpet.workflows.bootstrap import build_workflow_service
from deskpet.workflows.definitions.deep_research_v5_contracts import (
    DimensionCoverage,
    EvidenceSourceFamily,
    ResearchEvidenceSnapshot,
)
from deskpet.workflows.definitions.v5 import deep_research_initial_state
from deskpet.workflows.definitions.v5.deep_research import DEEP_RESEARCH_V5_DEFINITION
from deskpet.workflows.service import WorkflowServiceError


class RecordingLauncher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def launch_existing_run(self, run_id: str, *, start_payload):
        self.calls.append((run_id, dict(start_payload)))
        return {"run_id": run_id, "accepted": True, "created": True}


def _snapshot(parent_run_id: str) -> ResearchEvidenceSnapshot:
    return ResearchEvidenceSnapshot.create(
        parent_run_id=parent_run_id,
        dimension_coverages=(
            DimensionCoverage(
                "current-state",
                "partially_covered",
                ("passage-1",),
                ("passage-1",),
                ("family-1",),
                True,
                0.7,
                ("missing_recent_detail",),
            ),
        ),
        passage_blob_refs=("a" * 64,),
        source_families=(
            EvidenceSourceFamily(
                "family-1",
                "https://example.gov/report",
                "source-1",
                "official_report",
                "first_party",
                ("https://example.gov/report",),
                "https://example.gov/report",
                "valid",
                ("body-hash",),
            ),
        ),
        query_fingerprints=("query-old",),
        budget_summary={"input_tokens": 123, "io_documents": 4},
        created_at="2026-07-16T00:00:00+00:00",
        continue_until="2026-08-16T00:00:00+00:00",
    )


async def _continuable_service(tmp_path):
    service = await build_workflow_service(tmp_path)
    started = await service.start_workflow(
        venue="chat",
        base_session_id="session-1",
        delivery_session_id="session-1",
        request_id="request-parent",
        turn_id="turn-parent",
        workflow_name="deep_research",
        workflow_version="v5",
        capability_snapshot={"tools": ["deepresearch"]},
        start_payload={
            "topic": "education policy",
            "mode": "standard",
            "research_config": {"max_rounds": 3},
            "blob_root": str(tmp_path / "workflows" / "blobs"),
        },
    )
    run_id = str(started["run_id"])
    snapshot = _snapshot(run_id)
    manifest = snapshot.to_json()
    repository = service.research_repository
    await repository.ensure_root_lineage(
        run_id=run_id,
        operation_id=run_id,
        budget_lease_id=f"research-budget:{run_id}",
    )
    async with aiosqlite.connect(service.run_store.path) as db:
        now = time.time()
        for digest in (snapshot.snapshot_hash, "a" * 64):
            await db.execute(
                """INSERT INTO workflow_blobs(
                sha256,size_bytes,media_type,relative_path,created_at
                ) VALUES(?,1,'application/json',?,?)""",
                (digest, f"{digest[:2]}/{digest[2:]}", now),
            )
        await db.execute(
            "UPDATE workflow_runs SET status='completed',run_version=7 WHERE run_id=?",
            (run_id,),
        )
        await db.execute(
            """INSERT INTO workflow_events(
            event_id,event_key,run_id,seq,event_type,payload_json,created_at
            ) VALUES(?, 'run:terminal', ?, 2, 'workflow.final', ?, ?)""",
            (
                f"final:{run_id}",
                run_id,
                json.dumps(
                    {
                        "status": "completed",
                        "delivery_status": "partial",
                        "report_ref": "sha256:parent-report",
                    }
                ),
                now,
            ),
        )
        await db.commit()
    await repository.persist_snapshot_manifest(
        run_id=run_id,
        operation_id=run_id,
        manifest=manifest,
        manifest_ref=snapshot.snapshot_hash,
        continue_until=time.time() + 3600,
    )

    async def load_snapshot(ref: str):
        assert ref == snapshot.snapshot_hash
        content = dict(manifest)
        content.pop("snapshot_hash")
        return json.dumps(content, sort_keys=True, separators=(",", ":")).encode()

    launcher = RecordingLauncher()
    service._research_snapshot_loader = load_snapshot
    service.launcher = launcher
    return service, run_id, snapshot, launcher


@pytest.mark.asyncio
async def test_continue_research_derives_identity_server_side_and_double_clicks_once(tmp_path):
    service, run_id, snapshot, launcher = await _continuable_service(tmp_path)
    first = await service.execute_run_action(
        run_id,
        action_id="continue_research",
        idempotency_key="same-click",
        expected_version=7,
    )
    second = await service.execute_run_action(
        run_id,
        action_id="continue_research",
        idempotency_key="same-click",
        expected_version=7,
    )

    assert first["run_id"] == second["run_id"]
    assert first["created"] is True and second["created"] is False
    assert len(launcher.calls) == 1
    payload = launcher.calls[0][1]
    assert payload["topic"] == "education policy"
    assert payload["snapshot_hash"] == snapshot.snapshot_hash
    assert payload["parent_operation_id"] == run_id
    assert payload["continuation_snapshot"] == snapshot.to_json()
    assert payload["only_gaps"] is True
    child = await service.run_store.get_start_snapshot(first["run_id"])
    assert child is not None
    assert child["start_payload"] == payload
    assert child["identity"]["delivery_session_id"] == "session-1"


@pytest.mark.asyncio
async def test_continue_research_rejects_ui_snapshot_or_parent_identity(tmp_path):
    service, run_id, snapshot, _ = await _continuable_service(tmp_path)
    with pytest.raises(WorkflowServiceError) as caught:
        await service.execute_run_action(
            run_id,
            action_id="continue_research",
            idempotency_key="client-selected",
            expected_version=7,
            payload={
                "snapshot_hash": snapshot.snapshot_hash,
                "parent_operation_id": "client-parent",
            },
        )
    assert caught.value.code == "invalid_action_payload"


def test_v5_continuation_initial_state_hydrates_snapshot_and_only_gap_route():
    snapshot = _snapshot("parent-run")
    state = deep_research_initial_state(
        topic="education policy",
        run_id="child-run",
        operation_id="research:child-run",
        parent_operation_id="parent-operation",
        continuation_snapshot=snapshot.to_json(),
        only_gaps=True,
    )
    values = state["values"]
    assert values["operation_id"] == "research:child-run"
    assert values["parent_operation_id"] == "parent-operation"
    assert values["dimension_coverages"] == [
        item.to_json() for item in snapshot.dimension_coverages
    ]
    assert values["passage_blob_refs"] == list(snapshot.passage_blob_refs)
    assert values["source_families"] == [item.to_json() for item in snapshot.source_families]
    assert values["executed_query_fingerprints"] == list(snapshot.query_fingerprints)
    assert values["budget_summary"] == snapshot.budget_summary
    route = next(
        item
        for item in DEEP_RESEARCH_V5_DEFINITION.conditional_edges
        if item.source == "model"
    )
    assert route.selector(state, object()) == "gap_evaluate"
