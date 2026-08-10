from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace

import pytest

from deskpet.companion.build_admission import (
    CandidateDraftReceiptExpectationV1,
    CandidateDraftReceiptV1,
    GrowthBuildAdmissionError,
)
from deskpet.companion.candidate_receipts import (
    CANDIDATE_DRAFT_RECEIPT_SCHEMA_SQL,
    CandidateDraftReceiptRepository,
    CandidateDraftReceiptTerminalExtension,
    CandidateFinalizeProofV1,
    SqliteCandidateDraftMaterialQuery,
    SqliteCandidateDraftReceiptQuery,
)
from deskpet.execution.contracts import (
    ActorContext,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunRef,
    RunStartSnapshotRecord,
    RunStatus,
    canonical_json,
    fingerprint_json,
)
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork
from deskpet.workflows.store.schema import (
    WORKFLOW_SCHEMA_VERSION,
    initialize_workflow_db,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


class FinalizeMarkerQuery:
    def verify_exact_in_uow(
        self,
        db: sqlite3.Connection,
        proof: CandidateFinalizeProofV1,
    ) -> bool:
        return (
            db.execute(
                """SELECT 1 FROM execution_child_finalize_markers
                   WHERE marker_ref=? AND marker_hash=? AND builder_launch_id=?
                     AND child_run_id=? AND child_start_hash=?""",
                (
                    proof.finalize_marker_ref,
                    proof.finalize_marker_hash,
                    proof.builder_launch_id,
                    proof.child_run_id,
                    proof.child_start_hash,
                ),
            ).fetchone()
            is not None
        )


def create_db(path) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None)
    db.executescript(
        """
        CREATE TABLE execution_child_finalize_markers (
            marker_ref TEXT PRIMARY KEY,
            marker_hash TEXT NOT NULL,
            builder_launch_id TEXT NOT NULL,
            child_run_id TEXT NOT NULL,
            child_start_hash TEXT NOT NULL
        );
        """
        + CANDIDATE_DRAFT_RECEIPT_SCHEMA_SQL
    )
    return db


def receipt(
    *,
    builder_launch_id: str = "launch-1",
    proposal_hash: str = DIGEST_A,
) -> CandidateDraftReceiptV1:
    return CandidateDraftReceiptV1.issue_from_host(
        builder_launch_id=builder_launch_id,
        child_run_id="child-1",
        child_start_hash=DIGEST_A,
        proposal_ref="proposal-1",
        proposal_hash=proposal_hash,
        evidence_set_hash=DIGEST_B,
        target_fence_hash=DIGEST_C,
        validated_draft_hash=DIGEST_A,
        manifest_hash=DIGEST_B,
        archive_hash=DIGEST_C,
        file_set_hash=DIGEST_A,
        effect_topology_hash=DIGEST_B,
    )


def proof() -> CandidateFinalizeProofV1:
    return CandidateFinalizeProofV1(
        finalize_marker_ref="finalize-1",
        finalize_marker_hash=DIGEST_C,
        builder_launch_id="launch-1",
        child_run_id="child-1",
        child_start_hash=DIGEST_A,
    )


def expectation(value: CandidateDraftReceiptV1):
    return CandidateDraftReceiptExpectationV1(
        receipt_id=value.receipt_id,
        receipt_hash=value.receipt_hash,
        builder_launch_id=value.builder_launch_id,
        child_run_id=value.child_run_id,
        child_start_hash=value.child_start_hash,
        proposal_ref=value.proposal_ref,
        proposal_hash=value.proposal_hash,
        evidence_set_hash=value.evidence_set_hash,
        target_fence_hash=value.target_fence_hash,
        validated_draft_hash=value.validated_draft_hash,
        manifest_hash=value.manifest_hash,
        archive_hash=value.archive_hash,
        file_set_hash=value.file_set_hash,
        effect_topology_hash=value.effect_topology_hash,
    )


def insert_marker(db: sqlite3.Connection, value: CandidateFinalizeProofV1) -> None:
    db.execute(
        """INSERT INTO execution_child_finalize_markers(
             marker_ref,marker_hash,builder_launch_id,child_run_id,child_start_hash
           ) VALUES (?,?,?,?,?)""",
        (
            value.finalize_marker_ref,
            value.finalize_marker_hash,
            value.builder_launch_id,
            value.child_run_id,
            value.child_start_hash,
        ),
    )


def test_finalize_marker_and_receipt_share_caller_owned_commit(tmp_path) -> None:
    path = tmp_path / "execution.db"
    db = create_db(path)
    repository = CandidateDraftReceiptRepository(FinalizeMarkerQuery())
    value = receipt()
    final = proof()

    db.execute("BEGIN IMMEDIATE")
    insert_marker(db, final)
    repository.insert_in_uow(
        db,
        value,
        finalize_proof=final,
        created_at="2026-07-25T04:30:00Z",
    )
    db.rollback()
    assert db.execute(
        "SELECT COUNT(*) FROM execution_child_finalize_markers"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM execution_candidate_draft_receipts"
    ).fetchone()[0] == 0

    db.execute("BEGIN IMMEDIATE")
    insert_marker(db, final)
    repository.insert_in_uow(
        db,
        value,
        finalize_proof=final,
        created_at="2026-07-25T04:30:00Z",
    )
    db.commit()

    assert db.execute(
        "SELECT COUNT(*) FROM execution_child_finalize_markers"
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM execution_candidate_draft_receipts"
    ).fetchone()[0] == 1
    assert SqliteCandidateDraftReceiptQuery(path).read_exact(
        expectation(value)
    ) == value
    db.close()


def test_missing_finalize_marker_fails_before_receipt_insert(tmp_path) -> None:
    db = create_db(tmp_path / "execution.db")
    repository = CandidateDraftReceiptRepository(FinalizeMarkerQuery())

    with pytest.raises(
        GrowthBuildAdmissionError,
        match="candidate_finalize_marker_missing_or_mismatch",
    ):
        repository.insert_in_uow(
            db,
            receipt(),
            finalize_proof=proof(),
            created_at="2026-07-25T04:30:00Z",
        )

    assert db.execute(
        "SELECT COUNT(*) FROM execution_candidate_draft_receipts"
    ).fetchone()[0] == 0
    db.close()


def test_same_receipt_replay_is_idempotent_but_launch_conflict_fails(tmp_path) -> None:
    db = create_db(tmp_path / "execution.db")
    repository = CandidateDraftReceiptRepository(FinalizeMarkerQuery())
    final = proof()
    insert_marker(db, final)
    original = receipt()

    first = repository.insert_in_uow(
        db,
        original,
        finalize_proof=final,
        created_at="2026-07-25T04:30:00Z",
    )
    replay = repository.insert_in_uow(
        db,
        original,
        finalize_proof=final,
        created_at="2026-07-25T05:30:00Z",
    )

    assert first == replay
    assert db.execute(
        "SELECT COUNT(*) FROM execution_candidate_draft_receipts"
    ).fetchone()[0] == 1

    conflict = receipt(proposal_hash=DIGEST_C)
    with pytest.raises(
        GrowthBuildAdmissionError,
        match="candidate_draft_receipt_replay_conflict",
    ):
        repository.insert_in_uow(
            db,
            conflict,
            finalize_proof=final,
            created_at="2026-07-25T05:30:00Z",
        )
    assert repository.read_exact_in_uow(db, expectation(original)) == original
    db.close()


def test_exact_query_rejects_any_field_mismatch(tmp_path) -> None:
    path = tmp_path / "execution.db"
    db = create_db(path)
    repository = CandidateDraftReceiptRepository(FinalizeMarkerQuery())
    final = proof()
    insert_marker(db, final)
    value = receipt()
    repository.insert_in_uow(
        db,
        value,
        finalize_proof=final,
        created_at="2026-07-25T04:30:00Z",
    )
    db.close()

    wrong = replace(expectation(value), archive_hash=DIGEST_A)
    with pytest.raises(
        GrowthBuildAdmissionError,
        match="candidate_draft_receipt_archive_hash_mismatch",
    ):
        SqliteCandidateDraftReceiptQuery(path).read_exact(wrong)


def test_receipt_rows_are_immutable(tmp_path) -> None:
    db = create_db(tmp_path / "execution.db")
    repository = CandidateDraftReceiptRepository(FinalizeMarkerQuery())
    final = proof()
    insert_marker(db, final)
    value = receipt()
    repository.insert_in_uow(
        db,
        value,
        finalize_proof=final,
        created_at="2026-07-25T04:30:00Z",
    )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="execution_candidate_draft_receipt_immutable",
    ):
        db.execute(
            """UPDATE execution_candidate_draft_receipts
               SET archive_hash=? WHERE receipt_id=?""",
            (DIGEST_A, value.receipt_id),
        )
    with pytest.raises(
        sqlite3.IntegrityError,
        match="execution_candidate_draft_receipt_immutable",
    ):
        db.execute(
            "DELETE FROM execution_candidate_draft_receipts WHERE receipt_id=?",
            (value.receipt_id,),
        )

    assert repository.read_exact_in_uow(db, expectation(value)) == value
    db.close()


def execution_spec(run_id: str) -> RunCreate:
    capability_hash = fingerprint_json({"capabilities": []})
    return RunCreate(
        run_id=run_id,
        idempotency_key=f"root:candidate:{run_id}",
        context=RunContext(
            session_id="candidate-session",
            root_run_id=run_id,
            parent_run_id=None,
            request_id=f"request-{run_id}",
            turn_id=f"turn-{run_id}",
            venue="background",
            workspace={},
            capability_hash=capability_hash,
            provider_plan={},
            trace_id=f"trace-{run_id}",
            principal_id="companion-builder",
        ),
        payload_fingerprint=fingerprint_json({"run_id": run_id}),
        capability_fingerprint=capability_hash,
        driver_kind="react",
        profile_key="workflow.capability_build",
        persistence_level=PersistenceLevel.DURABLE,
    )


def execution_snapshot(spec: RunCreate) -> RunStartSnapshotRecord:
    empty_list = canonical_json([])
    empty_map = canonical_json({})
    payload = {
        "run_id": spec.run_id,
        "run_spec": spec.to_dict(),
        "terminal_deliveries": [],
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
        terminal_deliveries_json=empty_list,
        terminal_deliveries_hash=fingerprint_json([]),
        capability_lease_intent_ref=None,
        capability_lease_intent_hash=None,
        created_at=1.0,
    )


def terminal_event(value: CandidateDraftReceiptV1) -> RunEventCandidate:
    return RunEventCandidate(
        event_key="run:final",
        kind="run.final",
        status=OutcomeStatus.SUCCEEDED,
        driver_kind="react",
        payload={
            "builder_launch_id": value.builder_launch_id,
            "child_start_hash": value.child_start_hash,
            "candidate_draft_receipt_hash": value.receipt_hash,
        },
    )


@pytest.mark.asyncio
async def test_workflow_v20_fresh_schema_installs_receipt_and_material_tables(
    tmp_path,
) -> None:
    path = tmp_path / "workflow.db"
    await initialize_workflow_db(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (
            WORKFLOW_SCHEMA_VERSION,
        )
        assert db.execute(
            """SELECT COUNT(*) FROM sqlite_master
               WHERE type='table'
                 AND name='execution_candidate_draft_receipts'"""
        ).fetchone() == (1,)
        triggers = {
            row[0]
            for row in db.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='trigger'
                     AND tbl_name='execution_candidate_draft_receipts'"""
            )
        }
        assert triggers == {
            "execution_candidate_draft_receipts_immutable_update",
            "execution_candidate_draft_receipts_immutable_delete",
        }
        assert db.execute(
            """SELECT COUNT(*) FROM sqlite_master
               WHERE type='table'
                 AND name='execution_candidate_draft_materials'"""
        ).fetchone() == (1,)
        material_triggers = {
            row[0]
            for row in db.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='trigger'
                     AND tbl_name='execution_candidate_draft_materials'"""
            )
        }
        assert material_triggers == {
            "execution_candidate_draft_materials_immutable_update",
            "execution_candidate_draft_materials_immutable_delete",
        }


@pytest.mark.asyncio
async def test_existing_v19_database_upgrades_to_material_snapshot_v20(
    tmp_path,
) -> None:
    path = tmp_path / "workflow.db"
    await initialize_workflow_db(path)
    with sqlite3.connect(path) as db:
        # Reconstruct a real v19 boundary even when the current schema has
        # later migrations.  Keeping post-v19 objects while only rewinding
        # user_version would create an impossible mixed-version database and
        # make the replayed DDL collide before this test can verify the v20
        # upgrade.
        #
        # ⚠️ 维护义务（2026-08-09 补）：**每新增一个迁移，都要把它创建的对象
        # 加进下面两张表**。原清单只覆盖到 v22，v23 起新增的 5 张表与若干触发器
        # 没跟上，于是重放到 v22→v23 时撞 `table ... already exists`——这条用例
        # 从那次迁移落地起就一直红着。删表会连带删掉它自己的索引与触发器，
        # 所以这里只需列出「表」和「建在既有表上的独立触发器」。
        post_v19_triggers = (
            # v20
            "execution_candidate_draft_materials_immutable_update",
            "execution_candidate_draft_materials_immutable_delete",
            # v21
            "execution_profile_ticket_personal_identity_immutable",
            "execution_profile_ticket_personal_fields_consistent_insert",
            "execution_profile_ticket_personal_fields_consistent_update",
            "execution_skill_scope_activations_immutable_update",
            "execution_skill_scope_activations_immutable_delete",
            # v22
            "execution_run_context_owner_identity_insert",
            "execution_run_context_owner_identity_immutable",
            # v23 / v24
            "execution_provider_invocation_audit_immutable_update",
            "execution_provider_invocation_audit_immutable_delete",
            "execution_provider_invocation_input_immutable_update",
            "execution_provider_invocation_input_immutable_delete",
            # v26 / v27 / v28（建在既有表上，不随建表回收）
            "execution_task_work_identity_immutable",
            "execution_task_workspace_rebind_guard",
            "execution_root_active_budget_insert",
            "execution_root_active_budget_status",
        )
        post_v19_tables = (
            "execution_candidate_draft_materials",          # v20
            "execution_skill_scope_activations",            # v21
            "execution_provider_invocation_audits",         # v23
            "execution_provider_invocation_inputs",         # v24
            "execution_provider_action_batches_v25",        # v25
            "execution_run_active_budgets",                 # v27
            "execution_run_tool_presentation_specs",        # v29
            "execution_tool_public_projections",            # v29
            "execution_run_block_signals",                  # v29
        )
        for trigger in post_v19_triggers:
            db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        db.execute(
            "DROP INDEX IF EXISTS "
            "idx_execution_profile_ticket_personal_selection_once"
        )
        for table in post_v19_tables:
            db.execute(f"DROP TABLE IF EXISTS {table}")
        db.execute("DELETE FROM workflow_schema_migrations WHERE version>=20")
        db.execute("PRAGMA user_version=19")
        db.commit()

    await initialize_workflow_db(path)

    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (
            WORKFLOW_SCHEMA_VERSION,
        )
        assert db.execute(
            """SELECT COUNT(*) FROM sqlite_master
               WHERE type='table'
                 AND name='execution_candidate_draft_materials'"""
        ).fetchone() == (1,)


@pytest.mark.asyncio
async def test_terminal_event_and_receipt_commit_or_rollback_together(
    tmp_path,
) -> None:
    path = tmp_path / "workflow.db"
    value = receipt()
    spec = execution_spec(value.child_run_id)
    extension = CandidateDraftReceiptTerminalExtension(
        value, "2026-07-25T05:00:00Z"
    )

    def fail_after_outbox(point: str) -> None:
        if point == "finalize_after_outbox":
            raise RuntimeError("crash:finalize_after_outbox")

    crashing = SqliteExecutionUnitOfWork(path, fault_injector=fail_after_outbox)
    created = await crashing.create_with_start_snapshot(
        spec, execution_snapshot(spec)
    )
    with pytest.raises(RuntimeError, match="crash:finalize_after_outbox"):
        await crashing.commit_run_outcome(
            spec.run_id,
            expected_version=created.record.version,
            terminal_status=RunStatus.COMPLETED,
            event=terminal_event(value),
            terminal_commit_extensions=(extension,),
        )
    await crashing.close()

    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM execution_candidate_draft_receipts"
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT COUNT(*) FROM execution_events WHERE kind='run.final'"
        ).fetchone() == (0,)

    recovered = SqliteExecutionUnitOfWork(path)
    queried = await recovered.query(
        # The failed terminal transaction leaves the exact run version intact.
        RunRef(spec.run_id, spec.context.session_id),
        ActorContext(
            principal_id=spec.context.principal_id,
            session_id=spec.context.session_id,
            auth_epoch=0,
        ),
    )
    result = await recovered.commit_run_outcome(
        spec.run_id,
        expected_version=queried.version,
        terminal_status=RunStatus.COMPLETED,
        event=terminal_event(value),
        terminal_commit_extensions=(extension,),
    )
    assert result.idempotent is False
    replay = await recovered.commit_run_outcome(
        spec.run_id,
        expected_version=result.record.version,
        terminal_status=RunStatus.COMPLETED,
        event=terminal_event(value),
        terminal_commit_extensions=(extension,),
    )
    assert replay.idempotent is True
    await recovered.close()

    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM execution_candidate_draft_receipts"
        ).fetchone() == (1,)
        assert db.execute(
            "SELECT COUNT(*) FROM execution_events WHERE kind='run.final'"
        ).fetchone() == (1,)


@pytest.mark.asyncio
async def test_terminal_uow_freezes_exact_material_and_read_query_replays(
    tmp_path,
) -> None:
    path = tmp_path / "workflow.db"
    archive_bytes = b"exact-candidate-archive"
    value = CandidateDraftReceiptV1.issue_from_host(
        builder_launch_id="launch-material",
        child_run_id="child-material",
        child_start_hash=DIGEST_A,
        proposal_ref="proposal-material",
        proposal_hash=DIGEST_A,
        evidence_set_hash=DIGEST_B,
        target_fence_hash=DIGEST_C,
        validated_draft_hash=DIGEST_A,
        manifest_hash=DIGEST_B,
        archive_hash=hashlib.sha256(archive_bytes).hexdigest(),
        file_set_hash=DIGEST_A,
        effect_topology_hash=DIGEST_B,
    )
    spec = execution_spec(value.child_run_id)
    extension = CandidateDraftReceiptTerminalExtension(
        value,
        "2026-07-25T05:00:00Z",
        archive_bytes=archive_bytes,
    )
    uow = SqliteExecutionUnitOfWork(path)
    created = await uow.create_with_start_snapshot(spec, execution_snapshot(spec))
    await uow.commit_run_outcome(
        spec.run_id,
        expected_version=created.record.version,
        terminal_status=RunStatus.COMPLETED,
        event=terminal_event(value),
        terminal_commit_extensions=(extension,),
    )
    await uow.close()

    material = SqliteCandidateDraftMaterialQuery(path).read_exact(value)
    assert material.archive_bytes == archive_bytes
    assert material.receipt_id == value.receipt_id
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM execution_candidate_draft_materials"
        ).fetchone() == (1,)
        with pytest.raises(
            sqlite3.IntegrityError,
            match="candidate_draft_material_immutable",
        ):
            db.execute(
                """UPDATE execution_candidate_draft_materials
                   SET archive_blob=? WHERE receipt_id=?""",
                (b"changed", value.receipt_id),
            )
