from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from deskpet.execution.contracts import fingerprint_json
from deskpet.tools.build_identity import ExecutionBuildIdentity
from deskpet.tools.registry import ToolRegistry, tool_spec_fingerprint
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows import EffectKind, EffectPolicy
from deskpet.workflows.adapters.personal_runtime import (
    JournaledPersonalWorkflowEffects,
    PersonalWorkflowEffectReceiptV1,
    PersonalWorkflowRuntime,
)
from deskpet.workflows.definitions.personal_workflow import (
    PersonalWorkflowSelectionError,
    PersonalWorkflowSelectionV1,
    personal_workflow_query_hash,
)
from deskpet.workflows.contracts import NodeExecutionIdentity
from deskpet.workflows.store import schema as workflow_schema
from deskpet.workflows.effects import EffectExecutionContext, EffectJournal
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork
from deskpet.workflows.store.run_store import WorkflowRunStore
from deskpet.workflows.store.schema import initialize_workflow_db


def _tool_binding() -> dict[str, object]:
    return {
        "stable_handler_id": "memory-recall-v1",
        "tool_name": "memory_recall",
        "spec_ref": "tool:memory_recall:v1",
        "schema_hash": fingerprint_json({"schema": "memory"}),
        "execution_build_identity": fingerprint_json({"build": "memory"}),
        "effect_policy_hash": fingerprint_json({"effect": "read"}),
        "effect": "read_only",
        "idempotent": True,
    }


def _graph() -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": "daily-three",
        "description": "Recall and return daily priorities",
        "entry_node": "input",
        "nodes": [
            {"id": "input", "type": "input", "bindings": {}, "config": {}},
            {
                "id": "recall",
                "type": "tool_call",
                "bindings": {"query": "/input/objective"},
                "config": {"tool_name": "memory_recall"},
            },
            {
                "id": "output",
                "type": "output",
                "bindings": {"value": "/nodes/recall/result"},
                "config": {},
            },
        ],
        "outputs": {"value": "/nodes/output/value"},
        "max_steps": 3,
    }


def _selection(
    objective: str = "daily priorities",
    *,
    tool_binding: dict[str, object] | None = None,
):
    return PersonalWorkflowSelectionV1.issue(
        owner_key="companion:profile-1:1",
        pack_id="personal.daily-three",
        version="1.0.0",
        manifest_hash=fingerprint_json({"manifest": "daily-three"}),
        binding_generation=1,
        graph=_graph(),
        query_hash=personal_workflow_query_hash(objective),
        run_catalog_content_stamp="catalog-stamp-1",
        lease_entries=({"spec_ref": "tool:memory_recall:v1"},),
        effect_topology={"effects": ["read_only"]},
        tool_bindings={
            "memory_recall": tool_binding or _tool_binding()
        },
    )


def _registered_memory_tool() -> tuple[ToolRegistry, dict[str, object], list[int]]:
    registry = ToolRegistry()
    physical_calls = [0]

    def handler(args, _task_id):
        physical_calls[0] += 1
        return json.dumps(
            {
                "ok": True,
                "result": {
                    "result": f"recalled:{args['query']}",
                },
            }
        )

    policy = EffectPolicy(
        "personal-memory-recall-v1",
        "v1",
        EffectKind.IDEMPOTENT_READ,
    )
    build = ExecutionBuildIdentity(
        provider="builtin",
        handler_id="memory-recall-v1",
        build_digest="1" * 64,
        sources_manifest_hash="2" * 64,
        artifacts=(),
    )
    registry.register(
        "memory_recall",
        "memory",
        {
            "name": "memory_recall",
            "description": "Recall memory without writes",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        handler,
        stable_handler_id="memory-recall-v1",
        execution_build_identity=build,
        effect_policy=policy,
        outcome_parser_id="json_error_envelope_v1",
    )
    spec = registry.get("memory_recall")
    assert spec is not None
    binding = {
        "stable_handler_id": spec.stable_handler_id,
        "tool_name": spec.name,
        "spec_ref": tool_spec_fingerprint(spec),
        "schema_hash": spec.schema_hash,
        "execution_build_identity": build.fingerprint,
        "effect_policy_hash": fingerprint_json(
            {
                "policy_id": policy.policy_id,
                "version": policy.version,
                "kind": policy.kind.value,
                "max_attempts": policy.max_attempts,
                "reusable_across_branches": (
                    policy.reusable_across_branches
                ),
            }
        ),
        "effect": "read_only",
        "idempotent": True,
    }
    return registry, binding, physical_calls


def test_selection_freezes_owner_pack_graph_lease_and_tool_facts() -> None:
    selection = _selection()
    replay = PersonalWorkflowSelectionV1.from_authoritative_mapping(
        selection.to_child_payload()
    )

    assert replay == selection
    assert replay.workflow.graph_hash == selection.graph_hash
    assert replay.tool_bindings["memory_recall"]["spec_ref"] == (
        "tool:memory_recall:v1"
    )

    tampered = selection.to_child_payload()
    tampered["owner_key"] = "companion:attacker:1"
    with pytest.raises(
        PersonalWorkflowSelectionError,
        match="personal_selection_id_mismatch",
    ):
        PersonalWorkflowSelectionV1.from_authoritative_mapping(tampered)


@pytest.mark.asyncio
async def test_effect_settle_before_checkpoint_recovery_never_executes_twice(
    tmp_path,
) -> None:
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path)
    child_run_id, _ = await store.create_run(
        request_key="personal-child-1",
        session_id="session-1",
        request_id="request-1",
        turn_id="turn-1",
        workflow_name="personal_workflow",
        workflow_version="v1",
        manifest_hash="personal-manifest-v1",
        implementation_hash="personal-runtime-v1",
        capability_hash="capability-personal-v1",
        capability_snapshot={},
        state_schema_version=1,
    )
    fence = await store.claim(child_run_id, "personal-worker")
    journal = EffectJournal(path)
    registry, binding, physical_calls = _registered_memory_tool()
    selection = _selection(tool_binding=binding)
    effects = JournaledPersonalWorkflowEffects(
        journal=journal,
        tool_registry=registry,
        session_id="session-1",
        effect_context=EffectExecutionContext(
            journal=journal,
            fence=fence,
            node_execution_id="personal-node-execute",
            workflow_name="personal_workflow",
            workflow_version="v1",
            node_id="execute",
        ),
        execution_context=ToolExecutionContext(
            scope_id="scope-1",
            session_id="session-1",
            request_id="request-1",
            run_id=child_run_id,
            root_run_id=child_run_id,
            turn_id="turn-1",
            capability_hash="c" * 64,
            scope_hash="scope-hash-1",
            trace_id="trace-1",
            call_id="pending-call",
            effect_id="pending-effect",
            capability_snapshot_ref="8" * 64,
        ),
    )
    crash_once = [True]

    def crash_after_effect(
        _receipt: PersonalWorkflowEffectReceiptV1,
    ) -> None:
        if crash_once[0]:
            crash_once[0] = False
            raise RuntimeError("crash_after_effect_before_checkpoint")

    runtime = PersonalWorkflowRuntime(
        effects,
        after_effect_settled=crash_after_effect,
    )
    identity = NodeExecutionIdentity(
        workflow_name="personal_workflow",
        workflow_version="v1",
        thread_id=child_run_id,
        run_id=child_run_id,
        checkpoint_id="root",
        checkpoint_ns="",
        task_id="task-execute",
        node_id="execute",
        attempt=1,
    )
    with pytest.raises(RuntimeError, match="crash_after_effect"):
        await runtime.execute(
            child_run_id=child_run_id,
            selection=selection,
            inputs={"objective": "daily priorities"},
            execution_identity=identity,
        )
    assert physical_calls[0] == 1

    result = await PersonalWorkflowRuntime(effects).execute(
        child_run_id=child_run_id,
        selection=selection,
        inputs={"objective": "daily priorities"},
        execution_identity=identity,
    )
    assert result == {"value": "recalled:daily priorities"}
    assert physical_calls[0] == 1
    with sqlite3.connect(path) as db:
        assert db.execute(
            """SELECT COUNT(*) FROM workflow_effects
               WHERE run_id=? AND status='committed'""",
            (child_run_id,),
        ).fetchone() == (1,)
        assert db.execute(
            """SELECT COUNT(*) FROM workflow_checkpoint_effects
               WHERE thread_id=?""",
            (child_run_id,),
        ).fetchone() == (0,)
        expected_node_execution_id = hashlib.sha256(
            "|".join(
                (
                    child_run_id,
                    identity.checkpoint_id,
                    identity.task_id,
                    identity.node_id,
                )
            ).encode("utf-8")
        ).hexdigest()
        assert db.execute(
            """SELECT node_execution_id FROM workflow_node_effects"""
        ).fetchall() == [(expected_node_execution_id,)]


@pytest.mark.asyncio
async def test_schema_v20_to_v21_is_repeatable_and_keeps_legacy_ticket_nulls(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "workflow.db"
    monkeypatch.setattr(workflow_schema, "WORKFLOW_SCHEMA_VERSION", 20)
    await initialize_workflow_db(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (20,)
        assert "personal_selection_id" not in {
            row[1]
            for row in db.execute(
                "PRAGMA table_info(execution_profile_launch_tickets)"
            )
        }

    monkeypatch.setattr(workflow_schema, "WORKFLOW_SCHEMA_VERSION", 21)
    await initialize_workflow_db(path)
    await initialize_workflow_db(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (21,)
        columns = {
            row[1]
            for row in db.execute(
                "PRAGMA table_info(execution_profile_launch_tickets)"
            )
        }
        assert {
            "personal_selection_id",
            "personal_selection_fingerprint",
        } <= columns
        assert db.execute(
            """SELECT COUNT(*) FROM sqlite_master
               WHERE type='table'
                 AND name='execution_skill_scope_activations'"""
        ).fetchone() == (1,)
        triggers = {
            row[0]
            for row in db.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='trigger'
                     AND tbl_name='execution_skill_scope_activations'"""
            )
        }
        assert triggers == {
            "execution_skill_scope_activations_immutable_update",
            "execution_skill_scope_activations_immutable_delete",
        }


@pytest.mark.asyncio
async def test_schema_v21_fresh_matches_migrated_task10_objects(
    tmp_path, monkeypatch
) -> None:
    migrated_path = tmp_path / "workflow-migrated.db"
    fresh_path = tmp_path / "workflow-fresh.db"

    monkeypatch.setattr(workflow_schema, "WORKFLOW_SCHEMA_VERSION", 20)
    await initialize_workflow_db(migrated_path)
    monkeypatch.setattr(workflow_schema, "WORKFLOW_SCHEMA_VERSION", 21)
    await initialize_workflow_db(migrated_path)
    await initialize_workflow_db(fresh_path)

    def task10_schema_snapshot(path) -> tuple[object, ...]:
        with sqlite3.connect(path) as db:
            return (
                db.execute("PRAGMA user_version").fetchone(),
                tuple(
                    db.execute(
                        "PRAGMA table_info(execution_profile_launch_tickets)"
                    )
                ),
                tuple(
                    db.execute(
                        "PRAGMA table_info(execution_skill_scope_activations)"
                    )
                ),
                tuple(
                    db.execute(
                        """SELECT type,name,tbl_name,sql
                           FROM sqlite_master
                           WHERE name IN (
                             'idx_execution_profile_ticket_personal_selection_once',
                             'execution_profile_ticket_personal_identity_immutable',
                             'execution_profile_ticket_personal_fields_consistent_insert',
                             'execution_profile_ticket_personal_fields_consistent_update',
                             'execution_skill_scope_activations_immutable_update',
                             'execution_skill_scope_activations_immutable_delete'
                           )
                           ORDER BY type,name"""
                    )
                ),
            )

    assert task10_schema_snapshot(fresh_path) == task10_schema_snapshot(
        migrated_path
    )


def _insert_ticket(
    db: sqlite3.Connection,
    *,
    ticket_ref: str,
    spawn_call_id: str,
    profile_key: str,
    selection_id: str | None,
    selection_hash: str | None,
) -> None:
    db.execute(
        """INSERT INTO execution_profile_launch_tickets(
           ticket_ref,schema_version,parent_run_id,root_run_id,task_scope_id,
           attempt_id,provider_turn_id,profile_key,driver_kind,
           profile_catalog_generation,capability_snapshot_ref,task_grant_ref,
           spawn_call_id,personal_selection_id,personal_selection_fingerprint,
           trigger_failure_set_id,request_fingerprint,state,child_command_id,
           child_run_id,ticket_version,created_at,updated_at,consumed_at,
           cancelled_at
           ) VALUES(?,1,'parent-1','parent-1','task-1','attempt-1','turn-1',
                    ?,'workflow',1,?,?,?, ?,?,NULL,?,'issued',
                    NULL,NULL,0,1,1,NULL,NULL)""",
        (
            ticket_ref,
            profile_key,
            "a" * 64,
            "grant-1",
            spawn_call_id,
            selection_id,
            selection_hash,
            fingerprint_json({"ticket": ticket_ref}),
        ),
    )


@pytest.mark.asyncio
async def test_personal_ticket_selection_is_partial_unique_and_immutable(
    tmp_path,
) -> None:
    path = tmp_path / "workflow.db"
    await initialize_workflow_db(path)
    selection = _selection()
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        _insert_ticket(
            db,
            ticket_ref="ticket-legacy",
            spawn_call_id="call-legacy",
            profile_key="workflow.deep_research",
            selection_id=None,
            selection_hash=None,
        )
        _insert_ticket(
            db,
            ticket_ref="ticket-personal-1",
            spawn_call_id="call-personal-1",
            profile_key="workflow.personal_v1",
            selection_id=selection.selection_id,
            selection_hash=selection.selection_fingerprint,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_ticket(
                db,
                ticket_ref="ticket-personal-2",
                spawn_call_id="call-personal-2",
                profile_key="workflow.personal_v1",
                selection_id=selection.selection_id,
                selection_hash=selection.selection_fingerprint,
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="execution_profile_ticket_identity_immutable",
        ):
            db.execute(
                """UPDATE execution_profile_launch_tickets
                   SET personal_selection_id='drift'
                   WHERE ticket_ref='ticket-personal-1'"""
            )


async def _insert_execution_run(uow: SqliteExecutionUnitOfWork) -> None:
    await uow.activate_runtime()
    async with uow._write_transaction() as db:
        await db.execute(
            """INSERT INTO execution_runs(
               run_id,schema_version,idempotency_key,session_id,root_run_id,
               parent_run_id,request_id,turn_id,venue,workspace_json,
               capability_hash,provider_plan_json,trace_id,principal_id,
               auth_epoch,payload_fingerprint,capability_fingerprint,
               driver_kind,profile_key,persistence_level,status,version,
               durable_seq,terminal_event_id,cancel_reason,created_at,
               started_at,updated_at,ended_at,owner_kind,owner_generation
               ) VALUES(
               'run-1',1,'run:test','session-1','run-1',NULL,'request-1',
               'turn-1','text','{}',?,'{}','trace-1','principal-1',0,?,?,
               'react','agent.general','durable','running',0,0,NULL,NULL,
               1,1,1,NULL,'kernel',1)""",
            ("a" * 64, "b" * 64, "a" * 64),
        )


def _activation_values() -> dict[str, object]:
    scope_hash = fingerprint_json({"scope": "daily"})
    capability_snapshot_ref = "a" * 64
    instruction_hash = fingerprint_json({"instruction": "daily"})
    effective_hash = fingerprint_json({"tools": ["memory_recall"]})
    activation_id = "skill-scope-activation:" + fingerprint_json(
        {
            "schema": "skill-scope-activation/v1",
            "run_id": "run-1",
            "scope_id": "skill-scope:" + scope_hash,
            "scope_hash": scope_hash,
            "capability_snapshot_ref": capability_snapshot_ref,
            "instruction_content_hash": instruction_hash,
            "effective_tool_refs_hash": effective_hash,
        }
    )
    values: dict[str, object] = {
        "activation_id": activation_id,
        "run_id": "run-1",
        "root_run_id": "run-1",
        "scope_id": "skill-scope:" + scope_hash,
        "scope_hash": scope_hash,
        "capability_snapshot_ref": capability_snapshot_ref,
        "run_catalog_content_stamp": "catalog-stamp-1",
        "allowed_tool_names": ["memory_recall"],
        "allowed_tool_refs": [
            {
                "name": "memory_recall",
                "capability_id": "tool:memory_recall:v1",
            },
        ],
        "effective_tool_ref_hashes": [
            fingerprint_json({"ref": "memory_recall"}),
        ],
        "effective_tool_refs_hash": effective_hash,
        "instruction_content_hash": instruction_hash,
        "continuation_version": 1,
        "boundary_ref": "boundary-1",
        "boundary_hash": fingerprint_json({"boundary": 1}),
        "status": "committed",
        "receipt_ref": f"{activation_id}:receipt",
    }
    values["receipt_hash"] = fingerprint_json(
        {
            "schema": "skill-scope-activation-receipt-v1",
            **values,
        }
    )
    return values


@pytest.mark.asyncio
async def test_skill_scope_activation_is_caller_owned_replayable_and_queryable(
    tmp_path,
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await _insert_execution_run(uow)
    values = _activation_values()
    async with uow._write_transaction() as db:
        tx = uow.bind(db)
        first = await tx.insert_or_verify_skill_scope_activation(
            values, created_at=2.0
        )
        replay = await tx.insert_or_verify_skill_scope_activation(
            values, created_at=3.0
        )
        assert replay["activation_id"] == first["activation_id"]

    queried = await uow.get_skill_scope_activation(
        str(values["activation_id"])
    )
    assert queried is not None
    assert json.loads(queried["allowed_tool_refs_json"]) == [
        {
            "capability_id": "tool:memory_recall:v1",
            "name": "memory_recall",
        }
    ]
    with sqlite3.connect(uow.path) as db:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="execution_skill_scope_activation_immutable",
        ):
            db.execute(
                """DELETE FROM execution_skill_scope_activations
                   WHERE activation_id=?""",
                (values["activation_id"],),
            )
    await uow.close()


@pytest.mark.asyncio
async def test_skill_scope_activation_rolls_back_with_caller_transaction(
    tmp_path,
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await _insert_execution_run(uow)
    values = _activation_values()
    with pytest.raises(RuntimeError, match="rollback"):
        async with uow._write_transaction() as db:
            await uow.bind(db).insert_or_verify_skill_scope_activation(
                values, created_at=2.0
            )
            raise RuntimeError("rollback")
    assert (
        await uow.get_skill_scope_activation(str(values["activation_id"]))
        is None
    )
    await uow.close()
