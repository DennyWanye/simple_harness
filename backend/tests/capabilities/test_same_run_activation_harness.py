from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from deskpet.execution.contracts import (
    ActorContext,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunStartSnapshotRecord,
    canonical_json,
    fingerprint_json,
    root_idempotency_key,
)
from deskpet.harness.drivers.react import (
    AgentLoopCollaborator,
    ReActDriver,
    ReactCommandBoundary,
)
from deskpet.harness.live_index import BoundedLiveIndex
from deskpet.tools.capabilities import (
    ToolCapabilityResolver,
    ToolCapabilityScopeStore,
    ToolEligibilityContext,
    ToolExecutionContext,
    ToolExposureIntent,
)
from deskpet.tools.prepared_snapshot import (
    dump_context_os_snapshot,
    load_context_os_snapshot,
)
from deskpet.tools.registry import ToolRegistry
from deskpet.workflows.effects import NormalizedToolOutcome
from deskpet.workflows.store import SqliteExecutionUnitOfWork


def _schema(name: str, description: str) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": {}},
    }


class _Snapshots:
    def __init__(self) -> None:
        self.values: dict[str, dict] = {}

    async def put_context_os(self, snapshot_ref: str, payload):
        assert fingerprint_json(dict(payload)) == snapshot_ref
        self.values.setdefault(snapshot_ref, dict(payload))
        return snapshot_ref


class _Collaborator:
    def __init__(self) -> None:
        self.reset_calls: list[str] = []

    async def reset_runtime(self, run_id: str) -> None:
        self.reset_calls.append(run_id)


def _run_spec() -> RunCreate:
    context = RunContext(
        session_id="session-activation",
        root_run_id="run-activation",
        parent_run_id=None,
        request_id="request-activation",
        turn_id="turn-activation",
        venue="text",
        workspace={"root": "F:/workspace"},
        capability_hash=fingerprint_json({"tools": ["tool_activate"]}),
        provider_plan={"model": "fixture"},
        trace_id="trace-activation",
        principal_id="principal-activation",
    )
    return RunCreate(
        run_id="run-activation",
        idempotency_key=root_idempotency_key(
            context.session_id, context.request_id, context.turn_id
        ),
        context=context,
        payload_fingerprint=fingerprint_json({"prompt": "activate"}),
        capability_fingerprint=context.capability_hash,
        driver_kind="react",
        profile_key="agent.general",
        persistence_level=PersistenceLevel.DURABLE,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("use_unqualified_id", [False, True])
async def test_tool_activate_materializes_schema_in_same_root_run(
    tmp_path,
    use_unqualified_id: bool,
):
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await store.activate_runtime()
    registry = ToolRegistry()
    for name in ("tool_search", "tool_describe", "tool_activate"):
        registry.register(
            name,
            "control",
            _schema(name, f"{name} bridge"),
            lambda _args, _task: json.dumps({"ok": True}),
        )
    registry.register(
        "file_write",
        "file",
        _schema("file_write", "Create or replace one workspace file."),
        lambda _args, _task: json.dumps({"ok": True}),
        permission_category="write_file",
    )
    eligibility = ToolEligibilityContext(
        "session-activation", "request-activation", "chat"
    )
    prepared = ToolCapabilityResolver(registry).resolve_draft(
        ToolExposureIntent(discoverable_selectors=("file_write",)),
        eligibility=eligibility,
    ).finalize(scope_id="scope-activation")
    deferred = next(
        item for item in prepared.deferred if item.name == "file_write"
    )
    scope_store = ToolCapabilityScopeStore()
    scope_store.open(prepared, eligibility)
    snapshots = _Snapshots()
    collaborator = _Collaborator()
    driver = ReActDriver(
        collaborator,
        store,
        registry,
        capability_refresh_service=SimpleNamespace(snapshots=snapshots),
        capability_scope_store=scope_store,
    )
    live = BoundedLiveIndex()
    live.add(
        "run-activation",
        ActorContext(
            "principal-activation",
            "session-activation",
            0,
            "run-activation",
        ),
    )
    driver.bind_live_index(live)

    spec = _run_spec()
    context_os = dump_context_os_snapshot(prepared, eligibility)
    old_ref = fingerprint_json(context_os)
    activation_args = {
        "capability_id": (
            deferred.name if use_unqualified_id else deferred.capability_id
        ),
        "schema_hash": deferred.schema_hash,
        "describe_nonce": "nonce-activation",
    }
    execution_context = ToolExecutionContext(
        scope_id=prepared.scope_id,
        session_id=eligibility.session_id,
        request_id=eligibility.request_id,
        root_run_id=spec.run_id,
        turn_id=spec.context.turn_id,
        venue=spec.context.venue,
        workspace="F:/workspace",
        write_scope_root="F:/workspace",
        capability_hash=spec.context.capability_hash,
        scope_hash=fingerprint_json({"workspace": "F:/workspace"}),
        provider_plan=("fixture",),
        run_id=spec.run_id,
        command_id="command-activation",
        call_id="call-activation",
        effect_id="effect-activation",
        trace_id=spec.context.trace_id,
        capability_snapshot_ref="catalog-snapshot-activation",
    )
    call = registry.prepare_call(
        "tool_activate",
        activation_args,
        eligibility.session_id,
        "call-activation",
        execution_context=execution_context,
    )
    proposal = NormalizedToolOutcome.success(
        {
            "status": "activation_proposed",
            "__deskpet_control": {
                "kind": "tool_activation",
                "base_scope_revision": prepared.revision,
                "nonce": activation_args["describe_nonce"],
                "capability_id": deferred.capability_id,
                "schema_hash": deferred.schema_hash,
                "schema": {
                    "type": "function",
                    "function": registry.get("file_write").schema,
                },
            },
        }
    )
    boundary = ReactCommandBoundary(
        run_id=spec.run_id,
        session_id=eligibility.session_id,
        command_id="command-activation",
        command_kind="execute_tools",
        canonical_messages=(
            {
                "role": "assistant",
                "tool_calls": [{"id": call.stable_call_id}],
            },
        ),
        session_projection_cursor=0,
        prepared_context_ref=None,
        tool_set_snapshot_ref=old_ref,
        pending_calls=(call,),
        tool_contexts=(execution_context,),
        outcomes=(proposal,),
        outcome_statuses=(OutcomeStatus.SUCCEEDED,),
        outcome_metadata=({"receipt_ref": "receipt-activation"},),
        provider_state={},
        iteration=1,
        completion_state={"model_backfilled": False},
        capability_snapshot={
            "catalog_snapshot_ref": "catalog-snapshot-activation",
            "run_catalog_content_stamp": "a" * 64,
            "process_catalog_stamp": "b" * 64,
            "capability_hash": spec.context.capability_hash,
            "prepared_tool_set_ref": old_ref,
        },
        request_payload={
            "context_os": context_os,
            "tool_set_snapshot_ref": old_ref,
            "capability_refresh": {"tool_set_snapshot_ref": old_ref},
        },
        run_context=spec.context,
        run_spec=spec,
    )
    capability_snapshot = dict(boundary.capability_snapshot)
    sanitized_request = {
        "payload": dict(boundary.request_payload),
        "text": "activate",
    }
    empty_map = canonical_json({})
    empty_list = canonical_json([])
    await store.create_with_start_snapshot(
        spec,
        RunStartSnapshotRecord(
            run_id=spec.run_id,
            snapshot_schema_version=1,
            start_fingerprint=fingerprint_json(
                {
                    "capability_snapshot": capability_snapshot,
                    "sanitized_request": sanitized_request,
                }
            ),
            canonical_messages_json=canonical_json(
                [dict(item) for item in boundary.canonical_messages]
            ),
            session_cursor_json=empty_map,
            prepared_refs_json=empty_map,
            sanitized_request_json=canonical_json(sanitized_request),
            run_context_json=canonical_json(spec.context.to_dict()),
            run_spec_json=canonical_json(spec.to_dict()),
            capability_snapshot_json=canonical_json(capability_snapshot),
            capability_snapshot_hash=fingerprint_json(
                capability_snapshot
            ),
            provider_launch_policy_json=empty_map,
            terminal_deliveries_json=empty_list,
            terminal_deliveries_hash=fingerprint_json([]),
            capability_lease_intent_ref=None,
            capability_lease_intent_hash=None,
            created_at=1.0,
        ),
    )
    saved = await driver._persist_boundary(
        boundary.to_start(), boundary
    )

    activated = await driver._consume_pending_tool_activation(saved)

    assert activated.run_id == spec.run_id
    assert activated.version == saved.version + 1
    assert collaborator.reset_calls == [spec.run_id]
    assert "file_write" in activated.capability_snapshot["capabilities"]
    assert (
        activated.capability_snapshot["prepared_tool_set_ref"]
        == activated.tool_set_snapshot_ref
    )
    assert (
        activated.request_payload["capability_refresh"][
            "tool_set_snapshot_ref"
        ]
        == activated.tool_set_snapshot_ref
    )
    outcome = activated.outcomes[0]
    assert outcome is not None
    assert outcome.value["status"] == "activated"
    assert "__deskpet_control" not in outcome.value
    assert (
        outcome.value["activation_receipt"]["scope_revision"]
        == prepared.revision + 1
    )
    restored, restored_eligibility = load_context_os_snapshot(
        activated.request_payload["context_os"]
    )
    assert restored_eligibility == eligibility
    assert restored.revision == prepared.revision + 1
    assert restored.has_direct("file_write")
    assert activated.tool_set_snapshot_ref in snapshots.values
    scoped = scope_store.get(
        prepared.scope_id,
        session_id=eligibility.session_id,
        request_id=eligibility.request_id,
    )
    assert scoped is not None
    assert scoped.prepared.schema_fingerprint == restored.schema_fingerprint

    durable = await store.load_continuation(spec.run_id)
    assert durable is not None
    recovered = ReactCommandBoundary.from_record(durable)
    assert recovered.run_id == spec.run_id
    assert recovered.tool_set_snapshot_ref == activated.tool_set_snapshot_ref
    loaded = await driver._load_boundary(spec.run_id)
    assert loaded.tool_set_snapshot_ref == activated.tool_set_snapshot_ref
    assert "file_write" in AgentLoopCollaborator._allowed_tool_names(
        loaded.to_start()
    )
    start_snapshot = await store.read_run_start_snapshot(spec.run_id)
    assert start_snapshot is not None
    tampered_snapshot = dict(loaded.capability_snapshot)
    tampered_snapshot["process_catalog_stamp"] = "c" * 64
    with pytest.raises(
        ValueError,
        match="react boundary capability snapshot drifted from RunStart",
    ):
        driver._verify_tool_activation_snapshot_evolution(
            start_snapshot=start_snapshot,
            start_capabilities=capability_snapshot,
            boundary=replace(
                loaded, capability_snapshot=tampered_snapshot
            ),
        )
    tampered_state = dict(loaded.completion_state)
    tampered_receipts = {
        key: dict(value)
        for key, value in tampered_state[
            "tool_activation_receipts"
        ].items()
    }
    receipt_key = next(iter(tampered_receipts))
    tampered_receipts[receipt_key]["tool_set_snapshot_ref"] = "d" * 64
    tampered_state["tool_activation_receipts"] = tampered_receipts
    with pytest.raises(
        ValueError,
        match="react boundary capability snapshot drifted from RunStart",
    ):
        driver._verify_tool_activation_snapshot_evolution(
            start_snapshot=start_snapshot,
            start_capabilities=capability_snapshot,
            boundary=replace(loaded, completion_state=tampered_state),
        )
    recovered_set, _ = load_context_os_snapshot(
        recovered.request_payload["context_os"]
    )
    assert recovered_set.has_direct("file_write")


@pytest.mark.asyncio
async def test_tool_activate_snapshot_failure_returns_to_same_model(tmp_path):
    """A host commit failure becomes a normal failed tool outcome."""

    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await store.activate_runtime()
    registry = ToolRegistry()
    collaborator = _Collaborator()
    driver = ReActDriver(collaborator, store, registry)
    live = BoundedLiveIndex()
    live.add(
        "run-activation",
        ActorContext(
            "principal-activation",
            "session-activation",
            0,
            "run-activation",
        ),
    )
    driver.bind_live_index(live)
    spec = _run_spec()
    call = registry.register(
        "tool_activate",
        "control",
        _schema("tool_activate", "activate"),
        lambda _args, _task: json.dumps({"ok": True}),
    )
    del call
    prepared_call = registry.prepare_call(
        "tool_activate",
        {
            "capability_id": "builtin:file_write",
            "schema_hash": "a" * 64,
            "describe_nonce": "nonce",
        },
        "session-activation",
        "call-activation",
    )
    boundary = ReactCommandBoundary(
        run_id=spec.run_id,
        session_id=spec.context.session_id,
        command_id="command-activation",
        command_kind="execute_tools",
        canonical_messages=(),
        session_projection_cursor=0,
        prepared_context_ref=None,
        tool_set_snapshot_ref="old",
        pending_calls=(prepared_call,),
        tool_contexts=(
            ToolExecutionContext(
                scope_id="scope",
                session_id=spec.context.session_id,
                request_id=spec.context.request_id,
            ),
        ),
        outcomes=(
            NormalizedToolOutcome.success(
                {
                    "status": "activation_proposed",
                    "__deskpet_control": {"kind": "tool_activation"},
                }
            ),
        ),
        outcome_statuses=(OutcomeStatus.SUCCEEDED,),
        provider_state={},
        iteration=1,
        completion_state={},
        request_payload={},
        run_context=spec.context,
        run_spec=spec,
    )
    saved = await driver._persist_boundary(
        boundary.to_start(), boundary
    )

    failed = await driver._consume_pending_tool_activation(saved)

    assert failed.outcome_statuses == (OutcomeStatus.FAILED,)
    assert failed.outcomes[0].error["code"] == (
        "tool_activation_runtime_unavailable"
    )
    assert collaborator.reset_calls == []
    assert failed.run_id == saved.run_id
