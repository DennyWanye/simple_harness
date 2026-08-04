from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest

from deskpet.execution.harness_public_read_service import HarnessPublicReadService
from deskpet.execution.run_read_model import PublicReadError
from deskpet.harness.contracts import PreparedRunContextV1
from deskpet.memory.migrator import run_migrations
from deskpet.security.tool_public_projection import (
    ToolPresentationPolicyV1,
    ToolPresentationSnapshotExtension,
    ToolPublicProjectorV1,
)
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork
from deskpet.workflows.store.schema import initialize_workflow_db


def _fake_reducer(facts, read_cut, session_id, root_run_id):
    assert session_id == "session-1"
    assert root_run_id == "root-1"
    assert read_cut.workflow.complete
    return (
        {
            "status": "running",
            "explanation_code": "root_running",
            "evidence_refs": ("execution_run:root-1",),
            "child_warnings": (),
        },
        (
            {
                "phase_id": "phase-1",
                "taxonomy": "执行",
                "title": "执行",
                "status": "running",
                "mapping_reason": "fixture",
                "evidence_refs": (),
                "tool_refs": (),
                "child_refs": (),
                "workflow_steps": (),
                "current_step": 1,
                "total_steps": 1,
                "order_key": (0, 0, 0, "phase-1"),
            },
        ),
        True,
        (),
    )


async def _seed_databases(tmp_path: Path) -> tuple[Path, Path]:
    workflow = tmp_path / "workflow.db"
    state = tmp_path / "state.db"
    await initialize_workflow_db(workflow)
    await run_migrations(state)
    digest = "a" * 64
    with sqlite3.connect(workflow) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            """INSERT INTO execution_runs(
            run_id,schema_version,idempotency_key,session_id,root_run_id,
            parent_run_id,request_id,turn_id,venue,workspace_json,
            capability_hash,provider_plan_json,trace_id,principal_id,auth_epoch,
            payload_fingerprint,capability_fingerprint,driver_kind,profile_key,
            persistence_level,status,version,durable_seq,created_at,updated_at
            ) VALUES(?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?)""",
            (
                "root-1",
                "root:fixture",
                "session-1",
                "root-1",
                None,
                "request-1",
                "turn-1",
                "chat",
                "{}",
                digest,
                "{}",
                "trace-1",
                "principal-1",
                1,
                digest,
                digest,
                "agent.general",
                "agent.general",
                "durable",
                "running",
                1.0,
                1.0,
            ),
        )
        db.executemany(
            """INSERT INTO execution_events(
            event_id,schema_version,event_key,run_id,durable_seq,kind,status,
            driver_kind,correlation_json,payload_json,error_json,
            artifact_refs_json,created_at) VALUES(?,1,?, 'root-1',?,
            'progress','accepted','agent.general','{}',?,'{}','[]',?)""",
            [
                (
                    f"event-{index:04d}",
                    f"event-key-{index:04d}",
                    index + 1,
                    json.dumps(
                        {
                            "status": "running",
                            "workflow_step_id": f"step-{index}",
                            "step_index": index,
                            "private_prompt": "do-not-publish",
                        }
                    ),
                    2.0 + index,
                )
                for index in range(1500)
            ],
        )
        db.execute(
            """INSERT INTO execution_effects(
            effect_id,schema_version,run_id,effect_fingerprint,call_id,tool_name,
            args_hash,capability_hash,scope_hash,effect_type,status,policy_json,
            handoff_state,completion_disposition,
            prepared_json,outcome_json,artifact_refs_json,effect_version,
            created_at,updated_at,ended_at)
            VALUES('effect-1',1,'root-1',?,'call-1','removed_tool',?,?,NULL,
            'tool','succeeded','{}','reconciled','normal',?,?,'[]',1,3,4,4)""",
            (
                "b" * 64,
                "c" * 64,
                digest,
                json.dumps({"password": "secret-value", "blob": "data:text/plain,TOPSECRET"}),
                json.dumps({"stdout": "TOPSECRET" * 10000}),
            ),
        )
        db.commit()
    with sqlite3.connect(state) as db:
        db.executemany(
            """INSERT INTO messages_archive(
            id,session_id,role,content,created_at,archived_at,
            workflow_event_id,projection_kind,context_visibility,root_run_id)
            VALUES(?, 'session-1','assistant',?,?,?,?,'assistant_message',
            'conversation','root-1')""",
            [
                (
                    index + 1,
                    f"archived message {index}",
                    10_000.0 + index,
                    20_000.0 + index,
                    f"event-{index:04d}",
                )
                for index in range(106)
            ],
        )
        db.execute(
            """INSERT INTO messages(
            session_id,role,content,created_at,workflow_event_id,projection_kind,
            context_visibility,root_run_id)
            VALUES('session-1','assistant',?,30000,'event-1499',
            'assistant_message','conversation','root-1')""",
            ("password=hunter2 sk-ABCDEFGHIJKLMNOPQRSTUV user@example.com",),
        )
        db.execute(
            """INSERT INTO messages_archive(
            id,session_id,role,content,created_at,archived_at,projection_kind,
            context_visibility,root_run_id)
            VALUES(999,'session-1','assistant','legacy',1,2,'legacy_message',
            'conversation',NULL)"""
        )
        db.commit()
    return workflow, state


@pytest.mark.asyncio
async def test_manifest_rebuilds_1500_facts_and_106_archive_rows_without_leaks(tmp_path):
    workflow, state = await _seed_databases(tmp_path)
    service = HarnessPublicReadService(
        workflow_db_path=workflow,
        state_db_path=state,
        cursor_secret=b"cursor-secret-for-tests-32-bytes!",
        reducer=_fake_reducer,
    )
    manifest = await service.create_manifest(session_id="session-1", root_run_id="root-1")

    assert manifest.totals.workflow_facts >= 1501
    assert manifest.totals.content_facts == 107
    assert manifest.totals.tool_details == 1
    assert "legacy_unscoped:1" in manifest.diagnostics
    assert manifest.projection_complete is False
    wire = json.dumps(
        [fact.to_dict() for fact in manifest.facts], ensure_ascii=False
    )
    assert "do-not-publish" not in wire
    assert "hunter2" not in wire
    assert "TOPSECRET" not in wire
    assert "data:text/plain" not in wire
    unknown = manifest.detail_rows["tool_details"][0]["public_payload"]
    assert unknown["safe_input"] == {}
    assert unknown["bounded_result"] == {}
    assert unknown["mapping_reason"] == "legacy_unknown_tool"


@pytest.mark.asyncio
async def test_manifest_emits_semantic_reducer_canonical_fact_contract(tmp_path):
    workflow, state = await _seed_databases(tmp_path)
    with sqlite3.connect(workflow) as db:
        db.execute(
            "UPDATE execution_events SET kind='child.terminal',status='failed',"
            "payload_json='{\"child_run_id\":\"child-1\"}' "
            "WHERE event_id='event-0000'"
        )
        db.execute(
            """INSERT INTO execution_decisions(
            decision_id,schema_version,run_id,nonce,kind,status,
            prompt_schema_version,prompt_json,created_at
            ) VALUES('decision-1',1,'root-1','nonce-1','clarification','open',
            1,'{}',5.0)"""
        )
        db.commit()

    captured = {}

    def reducer(facts, read_cut, session_id, root_run_id):
        captured["facts"] = facts
        return _fake_reducer(facts, read_cut, session_id, root_run_id)

    service = HarnessPublicReadService(
        workflow_db_path=workflow,
        state_db_path=state,
        cursor_secret=b"cursor-secret-for-tests-32-bytes!",
        reducer=reducer,
    )
    await service.create_manifest(session_id="session-1", root_run_id="root-1")
    facts = captured["facts"]
    root = next(fact for fact in facts if fact.kind == "run")
    assert root.public_payload["run_id"] == "root-1"
    event = next(fact for fact in facts if fact.workflow_event_id == "event-0001")
    assert event.public_payload["run_id"] == "root-1"
    assert event.public_payload["source_stream"] == "execution_events:root-1"
    child = next(fact for fact in facts if fact.workflow_event_id == "event-0000")
    assert child.kind == "child"
    assert child.public_payload["run_id"] == "child-1"
    assert child.public_payload["role"] == "child"
    boundary = next(fact for fact in facts if fact.kind == "boundary")
    assert boundary.public_payload["boundary_kind"] == "human"
    assert boundary.public_payload["resolved"] is False
    tool = next(fact for fact in facts if fact.kind == "tool")
    assert tool.public_payload["effect_id"] == "effect-1"
    assert tool.public_payload["run_id"] == "root-1"
    assert tool.public_payload["call_id"] == "call-1"


@pytest.mark.asyncio
async def test_manifest_details_cursor_has_no_duplicates_and_is_owner_fenced(tmp_path):
    workflow, state = await _seed_databases(tmp_path)
    service = HarnessPublicReadService(
        workflow_db_path=workflow,
        state_db_path=state,
        cursor_secret=b"cursor-secret-for-tests-32-bytes!",
        reducer=_fake_reducer,
    )
    manifest = await service.create_manifest(session_id="session-1", root_run_id="root-1")
    seen: list[str] = []
    cursor = None
    while True:
        page = await service.query_details(
            projection_id=manifest.projection_id,
            session_id="session-1",
            root_run_id="root-1",
            query_kind="workflow_facts",
            page_size=73,
            cursor=cursor,
        )
        seen.extend(str(item["stable_id"]) for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(seen) == manifest.totals.workflow_facts
    assert len(set(seen)) == len(seen)

    first = await service.query_details(
        projection_id=manifest.projection_id,
        session_id="session-1",
        root_run_id="root-1",
        query_kind="workflow_facts",
        page_size=10,
    )
    assert first.next_cursor
    with pytest.raises(PublicReadError, match="invalid") as error:
        await service.query_details(
            projection_id=manifest.projection_id,
            session_id="session-1",
            root_run_id="root-1",
            query_kind="workflow_facts",
            page_size=10,
            cursor=first.next_cursor[:-1] + "0",
        )
    assert error.value.code == "invalid_cursor"


@pytest.mark.asyncio
async def test_manifest_caps_and_expiry_are_explicit(tmp_path):
    workflow, state = await _seed_databases(tmp_path)
    service = HarnessPublicReadService(
        workflow_db_path=workflow,
        state_db_path=state,
        cursor_secret=b"cursor-secret-for-tests-32-bytes!",
        reducer=_fake_reducer,
        max_public_facts=1000,
    )
    with pytest.raises(PublicReadError) as error:
        await service.create_manifest(session_id="session-1", root_run_id="root-1")
    assert error.value.code == "projection_too_large"


def test_tool_projector_redacts_and_bounds_declared_fields():
    policy = ToolPresentationPolicyV1(
        tool_name="shell",
        activity_kind="verify",
        action_code="run_test",
        safe_arg_paths=("/command", "/password"),
        safe_result_paths=("/stdout",),
        target_label_template="{command}",
        field_byte_cap=64,
        total_byte_cap=256,
    )
    projection = ToolPublicProjectorV1().project(
        policy,
        arguments={"command": "pytest", "password": "secret-value"},
        result={"stdout": "x" * 1000},
        status="succeeded",
    ).to_dict()
    wire = json.dumps(projection)
    assert "secret-value" not in wire
    assert "[REDACTED]" in wire
    assert projection["truncation_hashes"]["result:/stdout"]


@pytest.mark.asyncio
async def test_start_extension_freezes_policy_without_execution_fingerprint(tmp_path):
    workflow = tmp_path / "workflow.db"
    await initialize_workflow_db(workflow)
    digest = "a" * 64
    with sqlite3.connect(workflow) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            """INSERT INTO execution_runs(
            run_id,schema_version,idempotency_key,session_id,root_run_id,
            request_id,turn_id,venue,workspace_json,capability_hash,
            provider_plan_json,trace_id,principal_id,payload_fingerprint,
            capability_fingerprint,driver_kind,profile_key,persistence_level,
            status,created_at,updated_at)
            VALUES('root-1',1,'root:extension','session-1','root-1','r','t',
            'chat','{}',?,'{}','trace','principal',?,?,'agent.general',
            'agent.general','durable','running',1,1)""",
            (digest, digest, digest),
        )
        db.commit()
    policy = ToolPresentationPolicyV1(
        tool_name="read_file",
        activity_kind="inspect",
        action_code="read",
        safe_arg_paths=("/path",),
        safe_result_paths=(),
        target_label_template="{path}",
    )
    extension = ToolPresentationSnapshotExtension((policy,))
    base_context = PreparedRunContextV1(persistence_required=True)
    assert replace(
        base_context,
        start_commit_extensions=(extension,),
    ).prepared_fingerprint == base_context.prepared_fingerprint
    uow = SqliteExecutionUnitOfWork(workflow)
    async with aiosqlite.connect(workflow) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("BEGIN IMMEDIATE")
        receipt = await extension.apply_start_commit(
            uow.bind(db),
            spec=SimpleNamespace(
                run_id="root-1",
                context=SimpleNamespace(root_run_id="root-1"),
            ),
            start_snapshot=SimpleNamespace(created_at=1.0),
        )
        await db.commit()
        assert receipt.kind == "deskpet.tool-presentation.v1"
    with sqlite3.connect(workflow) as db:
        row = db.execute(
            "SELECT policy_hash FROM execution_run_tool_presentation_specs "
            "WHERE root_run_id='root-1' AND tool_name='read_file'"
        ).fetchone()
    assert row == (policy.policy_hash,)
    await uow.close()
