from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from deskpet.capabilities.brokered_planner import BrokeredEffectPlanner
from deskpet.capabilities.input_views import (
    InputViewRequest,
    InputViewResolver,
)
from deskpet.capabilities.tool_proxy import BrokeredPlanEnvelope
from deskpet.execution.contracts import (
    ActorContext,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunRecord,
    RunRef,
    fingerprint_json,
    root_idempotency_key,
)
from deskpet.harness.context import HostContextFactory
from deskpet.harness.drivers.react import (
    ReActDriver,
    ReactEmission,
    ReactFinal,
    ReactToolBatch,
)
from deskpet.harness.live_index import BoundedLiveIndex
from deskpet.harness.ports import (
    DriverStart,
    ExecuteTools,
    ToolOutcomesSignal,
)
from deskpet.harness.tool_executor import EffectBatchExecutor
from deskpet.tools.os_tools.registration import register_os_tools
from deskpet.tools.receipt_store import ReceiptStore
from deskpet.tools.registry import ToolRegistry
from deskpet.workflows.effects import NormalizedToolOutcome
from deskpet.workflows.store import SqliteExecutionUnitOfWork

CAPABILITY_HASH = fingerprint_json({"tools": ["generated.rename", "move_file"]})


async def _collect(iterator: AsyncIterator[Any]) -> list[Any]:
    return [item async for item in iterator]


class ScriptedCollaborator:
    def __init__(
        self,
        start: Iterable[ReactEmission] = (),
        resumes: Iterable[Iterable[ReactEmission]] = (),
    ) -> None:
        self.start_emissions = list(start)
        self.resume_emissions = [list(items) for items in resumes]
        self.resume_inputs: list[tuple[Any, Mapping[str, Any]]] = []
        self.cancelled: list[tuple[str, str]] = []

    async def start(self, _request: DriverStart) -> AsyncIterator[ReactEmission]:
        for emission in self.start_emissions:
            yield emission

    async def resume(
        self, boundary, response: Mapping[str, Any]
    ) -> AsyncIterator[ReactEmission]:
        self.resume_inputs.append((boundary, dict(response)))
        values = self.resume_emissions.pop(0) if self.resume_emissions else []
        for emission in values:
            yield emission

    async def cancel(self, run_id: str, reason: str) -> None:
        self.cancelled.append((run_id, reason))

    async def close(self) -> None:
        return None


def _schema(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": name,
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    }


def _run_spec(workspace: Path) -> RunCreate:
    run_id = "run-brokered"
    request_id = "request-brokered"
    turn_id = "turn-brokered"
    scope_hash = fingerprint_json({"workspace": str(workspace)})
    context = RunContext(
        session_id="session-brokered",
        root_run_id=run_id,
        parent_run_id=None,
        request_id=request_id,
        turn_id=turn_id,
        venue="text",
        workspace={
            "root": str(workspace),
            "write_scope_root": str(workspace),
            "scope_hash": scope_hash,
        },
        capability_hash=CAPABILITY_HASH,
        provider_plan={"providers": ["fixture"]},
        trace_id="trace-brokered",
        principal_id="principal-brokered",
    )
    return RunCreate(
        run_id=run_id,
        idempotency_key=root_idempotency_key(
            context.session_id, request_id, turn_id
        ),
        context=context,
        payload_fingerprint=fingerprint_json({"prompt": "rename"}),
        capability_fingerprint=CAPABILITY_HASH,
        driver_kind="react",
        profile_key="test-only",
        persistence_level=PersistenceLevel.DURABLE,
    )


def _actor() -> ActorContext:
    return ActorContext(
        "principal-brokered",
        "session-brokered",
        0,
        "run-brokered",
    )


def _bind(driver: ReActDriver) -> ReActDriver:
    live = BoundedLiveIndex()
    live.add("run-brokered", _actor())
    driver.bind_live_index(live)
    return driver


def _signal(
    command_id: str,
    index: int,
    outcome: NormalizedToolOutcome,
    *,
    metadata: Mapping[str, Any] = {},
) -> Any:
    return ToolOutcomesSignal(
        "run-brokered",
        command_id,
        (outcome,),
        (OutcomeStatus.SUCCEEDED,),
        (index,),
        (metadata,),
    )


@pytest.mark.asyncio
async def test_brokered_plan_waits_for_batch_then_recovers_by_receipt(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    temporary = tmp_path / "temporary.txt"
    final = tmp_path / "final.txt"
    source.write_text("payload", encoding="utf-8")

    registry = ToolRegistry()
    registry.register(
        "generated.rename",
        "capability:test",
        _schema("generated.rename"),
        lambda _args, _task_id: "{}",
        dispatch_kind="brokered_effect",
    )
    registry.register(
        "sibling.read",
        "test",
        _schema("sibling.read"),
        lambda _args, _task_id: "{}",
    )
    register_os_tools(registry)
    registry.set_receipt_store_provider(
        lambda: ReceiptStore(tmp_path / "receipts", key=b"r" * 32)
    )

    spec = _run_spec(tmp_path)
    command_id = "batch-brokered"
    context_factory = HostContextFactory()
    parent_context = context_factory.create_tool_context(
        spec.context,
        run_id=spec.run_id,
        command_id=command_id,
        call_id="provider-parent",
        effect_id="effect-parent",
    )
    sibling_context = context_factory.create_tool_context(
        spec.context,
        run_id=spec.run_id,
        command_id=command_id,
        call_id="provider-sibling",
        effect_id="effect-sibling",
    )
    parent_call = registry.prepare_call(
        "generated.rename",
        {},
        spec.context.session_id,
        "provider-parent",
        execution_context=parent_context,
    )
    sibling_call = registry.prepare_call(
        "sibling.read",
        {},
        spec.context.session_id,
        "provider-sibling",
        execution_context=sibling_context,
    )
    planner = BrokeredEffectPlanner(resource_authorizer=lambda *_: True)
    snapshot = InputViewResolver().resolve(
        [InputViewRequest("opaque:source", source, "file", "metadata")],
        root_run_id=spec.run_id,
        workspace_roots=[tmp_path],
    )
    record = planner.validate_and_record(
        {
            "actions": [
                {
                    "kind": "rename_file",
                    "source_ref": "input:0",
                    "target_name": temporary.name,
                },
                {
                    "kind": "rename_file",
                    "source_ref": "action:0",
                    "target_name": final.name,
                },
            ]
        },
        snapshot=snapshot,
        root_run_id=spec.run_id,
        parent_call_id=parent_call.stable_call_id,
        provider_call_id=parent_call.stable_call_id,
        tool_spec_fingerprint=parent_call.tool_spec_fingerprint,
        task_grant_id="task-grant-1",
        catalog_stamp={"generation": 7},
    )
    deferred = NormalizedToolOutcome.success(
        BrokeredPlanEnvelope(
            value={"summary": "rename planned"},
            artifacts=(),
            observations=({"actions": 2},),
            input_snapshot=snapshot,
            plan_record=record,
        ).to_durable_envelope()
    )
    batch = ReactToolBatch(
        command_id,
        (parent_call, sibling_call),
        (parent_context, sibling_context),
        canonical_messages=(
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "provider-parent"},
                    {"id": "provider-sibling"},
                ],
            },
        ),
    )
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await store.activate_runtime()
    collaborator = ScriptedCollaborator([batch])
    driver = _bind(
        ReActDriver(
            collaborator,
            store,
            registry,
            auto_mode_check=lambda: True,
            brokered_planner=planner,
        )
    )
    request = DriverStart(
        run_id=spec.run_id,
        session_id=spec.context.session_id,
        canonical_messages=({"role": "user", "content": "rename"},),
        run_context=spec.context,
        run_spec=spec,
    )

    initial = await _collect(driver.start(request))
    assert len(initial) == 1 and initial[0].kind == "execute_tools"
    after_parent = await _collect(
        driver.signal(_signal(command_id, 0, deferred))
    )
    assert len(after_parent) == 1
    assert after_parent[0].calls == (sibling_call,)

    after_sibling = await _collect(
        driver.signal(
            _signal(
                command_id,
                1,
                NormalizedToolOutcome.success({"sibling": "done"}),
            )
        )
    )
    assert [item.kind for item in after_sibling] == [
        "persisted_event",
        "execute_tools",
    ]
    first_inner: ExecuteTools = after_sibling[-1]
    assert first_inner.calls[0].tool_name == "move_file"
    assert first_inner.calls[0].stable_call_id == f"{record.plan_ref}:0"
    assert first_inner.original_indexes == (0,)
    assert first_inner.grant_refs[0].grant_id

    run = await store.query(
        RunRef(spec.run_id, spec.context.session_id), _actor()
    )
    assert isinstance(run, RunRecord)
    executor = EffectBatchExecutor(store, registry)
    first_effect = await executor.execute(run, _actor(), first_inner)
    assert first_effect is not None
    first_progress = await _collect(driver.signal(first_effect.signal))
    await executor.acknowledge_committed(first_effect.ready_refs)
    assert temporary.is_file() and not source.exists()
    assert first_progress[-1].kind == "execute_tools"
    assert (
        first_progress[-1].calls[0].stable_call_id
        == f"{record.plan_ref}:1"
    )

    recovered_collaborator = ScriptedCollaborator(
        resumes=[[ReactFinal("complete")]]
    )
    recovered_driver = _bind(
        ReActDriver(
            recovered_collaborator,
            store,
            registry,
            auto_mode_check=lambda: True,
            brokered_planner=planner,
        )
    )
    lease = await store.recovery_scope(
        spec.run_id, owner="brokered-recovery"
    )
    recovered = await _collect(
        recovered_driver.recover(spec.run_id, lease)
    )
    assert len(recovered) == 1
    second_inner: ExecuteTools = recovered[0]
    assert second_inner.kind == "execute_tools"
    assert [call.stable_call_id for call in second_inner.calls] == [
        f"{record.plan_ref}:1"
    ]

    second_effect = await executor.execute(
        run,
        _actor(),
        second_inner,
        recovery_lease=lease,
    )
    assert second_effect is not None
    completed = await _collect(
        recovered_driver.signal(
            second_effect.signal, recovery_lease=lease
        )
    )
    await executor.acknowledge_committed(second_effect.ready_refs)
    assert final.read_text(encoding="utf-8") == "payload"
    assert completed[-1].kind == "terminal"
    assert completed[-1].content == "complete"

    resumed_boundary = recovered_collaborator.resume_inputs[0][0]
    parent_outcome = resumed_boundary.outcomes[0]
    assert parent_outcome is not None
    result = dict(parent_outcome.value)
    plan_result = dict(result["effect_plan"])
    assert plan_result["status"] == "succeeded"
    assert len(plan_result["action_receipt_refs"]) == 2
    assert all(plan_result["action_receipt_refs"])
    tool_messages = [
        item
        for item in resumed_boundary.canonical_messages
        if item.get("role") == "tool"
    ]
    assert [item["tool_call_id"] for item in tool_messages] == [
        "provider-parent",
        "provider-sibling",
    ]


@pytest.mark.asyncio
async def test_cancel_marks_brokered_plan_terminal_without_next_action(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    planner = BrokeredEffectPlanner(resource_authorizer=lambda *_: True)
    registry = ToolRegistry()
    registry.register(
        "generated.rename",
        "capability:test",
        _schema("generated.rename"),
        lambda _args, _task_id: "{}",
        dispatch_kind="brokered_effect",
    )
    register_os_tools(registry)
    spec = _run_spec(tmp_path)
    command_id = "batch-cancel"
    context = HostContextFactory().create_tool_context(
        spec.context,
        run_id=spec.run_id,
        command_id=command_id,
        call_id="provider-parent",
        effect_id="effect-parent",
    )
    call = registry.prepare_call(
        "generated.rename",
        {},
        spec.context.session_id,
        "provider-parent",
        execution_context=context,
    )
    snapshot = InputViewResolver().resolve(
        [InputViewRequest("opaque:source", source, "file", "metadata")],
        root_run_id=spec.run_id,
        workspace_roots=[tmp_path],
    )
    record = planner.validate_and_record(
        {
            "actions": [
                {
                    "kind": "rename_file",
                    "source_ref": "input:0",
                    "target_name": "target.txt",
                }
            ]
        },
        snapshot=snapshot,
        root_run_id=spec.run_id,
        parent_call_id=call.stable_call_id,
        provider_call_id=call.stable_call_id,
        tool_spec_fingerprint=call.tool_spec_fingerprint,
        task_grant_id="task-grant-1",
        catalog_stamp={"generation": 7},
    )
    deferred = NormalizedToolOutcome.success(
        BrokeredPlanEnvelope(
            value=None,
            artifacts=(),
            observations=(),
            input_snapshot=snapshot,
            plan_record=record,
        ).to_durable_envelope()
    )
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await store.activate_runtime()
    collaborator = ScriptedCollaborator(
        [ReactToolBatch(command_id, (call,), (context,))]
    )
    driver = _bind(
        ReActDriver(
            collaborator,
            store,
            registry,
            brokered_planner=planner,
        )
    )
    await _collect(
        driver.start(
            DriverStart(
                run_id=spec.run_id,
                session_id=spec.context.session_id,
                canonical_messages=({"role": "user", "content": "rename"},),
                run_context=spec.context,
                run_spec=spec,
            )
        )
    )
    waiting = await _collect(driver.signal(_signal(command_id, 0, deferred)))
    assert waiting[0].kind == "open_decision"

    cancelled = await _collect(driver.cancel(spec.run_id, "user cancelled"))

    assert cancelled[0].kind == "cancel_acknowledged"
    assert source.is_file()
    durable = await store.load_continuation(spec.run_id)
    assert durable is not None
    from deskpet.harness.drivers.react import ReactCommandBoundary

    boundary = ReactCommandBoundary.from_record(durable)
    assert boundary.brokered_commands[0].plan_record.status == "cancelled"
    assert boundary.outcome_statuses[0] is OutcomeStatus.CANCELLED
    assert boundary.pending_decision is None
