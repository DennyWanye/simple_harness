from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace

import pytest

from deskpet.execution.contracts import (
    ActorContext,
    AttemptRecord,
    PersistenceLevel,
    PlanVersionRecord,
    ProfileLaunchTicketState,
    ProviderActionBatch,
    ProviderActionCall,
    ProviderTurnFence,
    RunContext,
    RunCreate,
    RunRef,
    TaskGoalRecord,
    fingerprint_json,
)
from deskpet.harness.adapters.subagent_registry import (
    ProductDelegateFactory,
    _child_context_os,
    _complete_presentation_objective,
)
from deskpet.harness.child_runs import ChildRunCoordinator
from deskpet.harness.context import HostContextFactory
from deskpet.harness.drivers.react import (
    ReActDriver,
    ReactCommandBoundary,
    ReactControlBatch,
)
from deskpet.harness.live_index import BoundedLiveIndex
from deskpet.harness.ports import DelegateRun, DriverStart, JoinPolicy
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.permissions.runtime import PreparedAuthorizationRuntime
from deskpet.capabilities.builder import CapabilityBuilderHost
from deskpet.capabilities.contracts import CatalogStamp
from deskpet.capabilities.store import CapabilityStore
from deskpet.tools.orchestration_controls import (
    CAPABILITY_BUILD,
    CORE_CONTROL_NAMES,
    EXTERNAL_ACTION_WAIT,
    PROJECT_DIRECTORY_SELECT,
    WORKSPACE_PREPARE,
    WORKFLOW_SPAWN,
    register_orchestration_controls,
)
from deskpet.tools.registry import ToolRegistry
from deskpet.tools.capabilities import (
    ToolCapabilityResolver,
    ToolEligibilityContext,
    ToolExposureIntent,
)
from deskpet.tools.prepared_snapshot import (
    dump_context_os_snapshot,
    load_context_os_snapshot,
)
from deskpet.types.task_grants import canonical_filesystem_path
from deskpet.types.task_work_context import TaskWorkContextResolver
from deskpet.workflows.store import SqliteExecutionUnitOfWork
from deskpet.workflows.definitions.personal_workflow import (
    SELECTION_EXTENSION_KEY,
    PersonalWorkflowSelectionV1,
    personal_workflow_query_hash,
)


def _profiles() -> ProfileRegistry:
    return ProfileRegistry(
        (
            ProfileSpec(
                "agent.general",
                None,
                "react",
                display_name="General",
                description="Root agent.",
                launch_policy="reserved_control",
            ),
            ProfileSpec(
                "workflow.deep_research",
                None,
                "workflow",
                capabilities=frozenset({"workflow"}),
                workflow_key="research.deep.v7",
                workflow_name="deep_research",
                workflow_version="v7",
                state_factory=lambda **values: values,
                context_factory=lambda *args, **kwargs: (args, kwargs),
                display_name="Research",
                description="Evidence-backed research.",
                launch_policy="model_spawnable",
            ),
            ProfileSpec(
                "workflow.presentation",
                "ppt_workflow",
                "workflow",
                capabilities=frozenset({"workflow"}),
                workflow_key="ppt.create.v1",
                workflow_name="ppt_pro",
                workflow_version="v1",
                state_factory=lambda **values: values,
                context_factory=lambda *args, **kwargs: (args, kwargs),
                display_name="Presentation",
                description="Create and validate a presentation.",
                launch_policy="model_spawnable",
            ),
            ProfileSpec(
                "workflow.personal_v1",
                None,
                "workflow",
                capabilities=frozenset({"workflow"}),
                workflow_key="personal.workflow.v1",
                workflow_name="personal_workflow",
                workflow_version="v1",
                state_factory=lambda **values: values,
                context_factory=lambda *args, **kwargs: (args, kwargs),
                display_name="Personal Workflow",
                description="Run one host-selected frozen personal workflow.",
                launch_policy="model_spawnable",
            ),
            ProfileSpec(
                "workflow.capability_build",
                None,
                "workflow",
                capabilities=frozenset({"workflow", CAPABILITY_BUILD}),
                workflow_key="capability.build.v1",
                workflow_name="durable_task",
                workflow_version="v1",
                state_factory=lambda **values: values,
                context_factory=lambda *args, **kwargs: (args, kwargs),
                display_name="Capability Builder",
                description="Build a validated process-isolated capability pack.",
                launch_policy="reserved_control",
            ),
        ),
        generation=7,
    )


def test_child_context_os_is_stable_per_spawn_and_isolated_between_children():
    registry = ToolRegistry()
    eligibility = ToolEligibilityContext("session-1", "request-1", "chat")
    prepared = ToolCapabilityResolver(registry).resolve_draft(
        ToolExposureIntent(), eligibility=eligibility
    ).finalize(scope_id="parent-scope")
    parent = dump_context_os_snapshot(prepared, eligibility)

    first = _child_context_os(
        parent,
        parent_run_id="root-run",
        stable_call_id="spawn-call-a",
    )
    first_replay = _child_context_os(
        parent,
        parent_run_id="root-run",
        stable_call_id="spawn-call-a",
    )
    second = _child_context_os(
        parent,
        parent_run_id="root-run",
        stable_call_id="spawn-call-b",
    )

    first_set, first_eligibility = load_context_os_snapshot(first)
    second_set, second_eligibility = load_context_os_snapshot(second)
    assert first == first_replay
    assert first_set.scope_id.startswith("child-scope:")
    assert first_set.scope_id != prepared.scope_id
    assert first_set.scope_id != second_set.scope_id
    assert replace(first_set, scope_id=prepared.scope_id) == prepared
    assert replace(second_set, scope_id=prepared.scope_id) == prepared
    assert first_eligibility == eligibility
    assert second_eligibility == eligibility


def _request(tmp_path) -> tuple[DriverStart, object]:
    context = RunContext(
        session_id="session-1",
        root_run_id="run-1",
        parent_run_id=None,
        request_id="request-1",
        turn_id="turn-1",
        venue="text",
        workspace={
            "root": str(tmp_path),
            "write_scope_root": str(tmp_path),
            "scope_hash": fingerprint_json({"root": str(tmp_path)}),
        },
        capability_hash=fingerprint_json({"tools": ["workflow"]}),
        provider_plan={"providers": ["fixture"]},
        trace_id="trace-1",
        principal_id="local:session-1",
        auth_epoch=1,
    )
    spec = RunCreate(
        run_id="run-1",
        idempotency_key="root:test",
        context=context,
        payload_fingerprint="a" * 64,
        capability_fingerprint=context.capability_hash,
        driver_kind="react",
        profile_key="agent.general",
        persistence_level=PersistenceLevel.EPHEMERAL,
    )
    start = DriverStart(
        run_id="run-1",
        session_id="session-1",
        canonical_messages=(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "provider-call-1",
                        "type": "function",
                        "function": {
                            "name": WORKFLOW_SPAWN,
                            "arguments": "{}",
                        },
                    }
                ],
            },
        ),
        run_context=context,
        run_spec=spec,
        profile_key="agent.general",
        request_payload={
            "task_scope_id": "task-1",
            "execution_capabilities": ["workflow"],
        },
        capability_snapshot={"tools": ["workflow"]},
    )
    tool_context = HostContextFactory().create_tool_context(
        context,
        run_id="run-1",
        call_id="provider-call-1",
        effect_id="effect-1",
    )
    return start, tool_context


def test_presentation_objective_restores_suffix_copied_from_user_turn() -> None:
    objective = (
        "生成一个新的 8 页、全部元素可编辑的中文 PowerPoint。"
    )
    complete = (
        objective
        + "第3页必须是原生可编辑表格；第6页必须是原生可编辑柱状图。"
        + "不要使用图片，不要把表格或图表栅格化。"
    )

    assert _complete_presentation_objective(
        objective,
        (
            {"role": "user", "content": "立即调用 workflow_spawn。目标：" + complete},
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": WORKFLOW_SPAWN}}],
            },
        ),
    ) == complete


def test_presentation_objective_does_not_graft_unrelated_user_text() -> None:
    objective = "生成一个 8 页演示文稿。"

    assert _complete_presentation_objective(
        objective,
        ({"role": "user", "content": "生成一个完全不同的 Word 文档。"},),
    ) == objective


def test_presentation_objective_uses_only_latest_canonical_user_text() -> None:
    objective = "生成一个 8 页演示文稿。"
    old_complete = objective + "第3页使用旧表格。"

    assert _complete_presentation_objective(
        objective,
        (
            {"role": "user", "content": old_complete},
            {"role": "assistant", "content": old_complete},
            {"role": "tool", "content": old_complete},
            {"role": "user", "content": "当前请求是生成 Word 文档。"},
        ),
    ) == objective


@pytest.mark.parametrize(
    "messages",
    [
        (
            {"role": "assistant", "content": "生成一个 8 页演示文稿。旧约束"},
            {"role": "tool", "content": "生成一个 8 页演示文稿。旧约束"},
        ),
        (
            {"role": "user", "content": "生成一个 8 页演示文稿。旧约束"},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "生成一个 8 页演示文稿。新约束",
                    }
                ],
            },
        ),
    ],
)
def test_presentation_objective_ignores_non_user_or_structured_content(
    messages,
) -> None:
    objective = "生成一个 8 页演示文稿。"

    assert _complete_presentation_objective(objective, messages) == objective


def test_presentation_objective_fails_closed_on_repeated_prefix() -> None:
    objective = "生成一个 8 页演示文稿。"

    assert _complete_presentation_objective(
        objective,
        (
            {
                "role": "user",
                "content": (
                    "旧版本：" + objective + "不要表格。"
                    "最终版本：" + objective + "第3页必须使用原生表格。"
                ),
            },
        ),
    ) == objective


@pytest.mark.parametrize("objective", ["", "   "])
def test_presentation_objective_does_not_expand_empty_objective(
    objective,
) -> None:
    assert _complete_presentation_objective(
        objective,
        ({"role": "user", "content": "生成一个 8 页演示文稿。"},),
    ) == objective


async def _seed_attempt(
    tmp_path,
    start: DriverStart,
    prepared,
) -> tuple[DriverStart, SqliteExecutionUnitOfWork]:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    assert start.run_spec is not None
    durable_spec = replace(
        start.run_spec, persistence_level=PersistenceLevel.DURABLE
    )
    await uow.create(durable_spec)
    await uow.create_task_goal(
        TaskGoalRecord(
            goal_id="goal-1",
            root_run_id=start.run_id,
            task_scope_id="task-1",
            objective_ref="objective:research",
        ),
        PlanVersionRecord(root_run_id=start.run_id, plan_version=1),
    )
    await uow.open_provider_turn_fence(
        ProviderTurnFence(
            provider_turn_id="provider-batch-1",
            root_run_id=start.run_id,
            idempotency_key="provider-turn:provider-batch-1",
            request_hash=fingerprint_json({"call": prepared.stable_call_id}),
        )
    )
    call = ProviderActionCall(
        call_record_id="call-record-1",
        root_run_id=start.run_id,
        provider_batch_id="provider-batch-1",
        call_order=0,
        provider_call_id=prepared.stable_call_id,
        raw_tool_name=prepared.tool_name,
        raw_arguments_ref=f"inline:{prepared.args_hash}",
        raw_arguments_hash=prepared.args_hash,
    )
    attempt = AttemptRecord(
        attempt_id="attempt-1",
        root_run_id=start.run_id,
        run_id=start.run_id,
        provider_turn_id="provider-batch-1",
        provider_batch_id="provider-batch-1",
        plan_version=1,
        strategy_fingerprint=fingerprint_json({"strategy": "research"}),
        planned_call_refs=(call.call_record_id,),
    )
    await uow.accept_provider_batch_and_create_attempt(
        ProviderActionBatch(
            provider_batch_id="provider-batch-1",
            root_run_id=start.run_id,
            provider_turn_id="provider-batch-1",
            canonical_assistant_batch_ref="assistant-batch:1",
            batch_fingerprint=fingerprint_json(
                {"call": prepared.stable_call_id}
            ),
            pending_call_count=1,
        ),
        attempt,
        (call,),
    )
    return (
        replace(
            start,
            run_spec=durable_spec,
            completion_state={
                "current_attempt": {
                    "attempt_id": attempt.attempt_id,
                    "provider_turn_id": attempt.provider_turn_id,
                }
            },
        ),
        uow,
    )


def test_orchestration_controls_have_checked_execution_build_identities() -> None:
    registry = ToolRegistry()
    register_orchestration_controls(registry, _profiles())

    assert CORE_CONTROL_NAMES.issubset(set(registry.list_tools()))
    for name in CORE_CONTROL_NAMES:
        spec = registry.get(name)
        assert spec is not None
        assert spec.stable_handler_id == f"core.{name}.v1"
        assert spec.execution_build_identity is not None
        assert (
            spec.execution_build_identity.handler_id
            == spec.stable_handler_id
        )


def test_workflow_spawn_scope_ignores_model_workspace_and_fails_closed(
    tmp_path,
) -> None:
    registry = ToolRegistry()
    register_orchestration_controls(registry, _profiles())
    _start, tool_context = _request(tmp_path)
    prepared = registry.prepare_call(
        WORKFLOW_SPAWN,
        {
            "profile_key": "workflow.deep_research",
            "objective": "research",
            "workspace_ref": str(tmp_path.parent),
            "catalog_generation": 7,
        },
        "session-1",
        "provider-call-1",
        execution_context=tool_context,
    )
    filesystem = next(
        item
        for item in prepared.resource_selectors
        if item.kind == "filesystem"
    )
    assert filesystem.canonical_value == canonical_filesystem_path(tmp_path)
    with pytest.raises(
        ValueError, match="workflow_spawn_catalog_binding_mismatch"
    ):
        registry.prepare_call(
            WORKFLOW_SPAWN,
            {
                "profile_key": "workflow.deep_research",
                "objective": "research",
                "catalog_generation": 8,
            },
            "session-1",
            "provider-call-2",
            execution_context=replace(
                tool_context,
                call_id="provider-call-2",
                effect_id="effect-2",
            ),
        )


def _personal_selection(objective: str) -> PersonalWorkflowSelectionV1:
    graph = {
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
    tool_binding = {
        "stable_handler_id": "memory-recall-v1",
        "tool_name": "memory_recall",
        "spec_ref": "tool:memory_recall:v1",
        "schema_hash": fingerprint_json({"schema": "memory"}),
        "execution_build_identity": fingerprint_json({"build": "memory"}),
        "effect_policy_hash": fingerprint_json({"effect": "read"}),
        "effect": "read_only",
        "idempotent": True,
    }
    return PersonalWorkflowSelectionV1.issue(
        owner_key="companion:profile-1:1",
        pack_id="personal.daily-three",
        version="1.0.0",
        manifest_hash=fingerprint_json({"manifest": "daily-three"}),
        binding_generation=1,
        graph=graph,
        query_hash=personal_workflow_query_hash(objective),
        run_catalog_content_stamp="catalog-stamp-1",
        lease_entries=({"spec_ref": "tool:memory_recall:v1"},),
        effect_topology={"effects": ["read_only"]},
        tool_bindings={"memory_recall": tool_binding},
    )


@pytest.mark.asyncio
async def test_workflow_spawn_is_dynamic_prepared_control(tmp_path) -> None:
    profiles = _profiles()
    registry = ToolRegistry()
    register_orchestration_controls(registry, profiles)
    start, tool_context = _request(tmp_path)
    prepared = registry.prepare_call(
        WORKFLOW_SPAWN,
        {
            "profile_key": "workflow.deep_research",
            "objective": "research current evidence",
            "catalog_generation": 7,
        },
        "session-1",
        "provider-call-1",
        execution_context=tool_context,
    )

    start, uow = await _seed_attempt(tmp_path, start, prepared)
    factory = ProductDelegateFactory(profiles, registry, uow)

    assert registry.dispatch_kind(WORKFLOW_SPAWN) == "delegate_control"
    selectors = {item.kind: item for item in prepared.resource_selectors}
    assert selectors["system_change"].canonical_value == (
        "workflow_spawn:run-1:7:workflow.deep_research"
    )
    assert selectors["system_change"].access == ("delegate",)
    assert selectors["filesystem"].canonical_value == canonical_filesystem_path(
        tmp_path
    )
    assert selectors["filesystem"].access == ("read", "write")
    exact = PreparedAuthorizationRuntime(object()).build_exact_request(
        call=prepared,
        context=tool_context,
        permission_category="shell",
        decision_expires_at=time.time() + 60.0,
    )
    assert exact.call_id == prepared.stable_call_id
    delegate = await factory(start, prepared)
    replay = await factory(start, prepared)
    assert delegate.kind == "delegate_run"
    assert replay.command_id == delegate.command_id
    assert replay.child_request == delegate.child_request
    assert delegate.route_hint == "workflow.deep_research"
    assert delegate.child_request["driver_kind"] == "workflow"
    assert delegate.child_request["topic"] == "research current evidence"
    assert delegate.join_policy is JoinPolicy.JOIN_BEFORE_FINAL
    ticket_ref = "profile-launch:" + hashlib.sha256(
        f"{start.run_id}|{prepared.stable_call_id}".encode("utf-8")
    ).hexdigest()
    ticket = await uow.get_profile_launch_ticket(ticket_ref)
    assert ticket is not None
    assert ticket.state is ProfileLaunchTicketState.ISSUED
    assert ticket.child_command_id is None
    parent = await uow.query(
        RunRef(start.run_id, start.session_id), start.run_context.actor()
    )
    command = await ChildRunCoordinator(uow).submit(parent, delegate)
    replay_command = await ChildRunCoordinator(uow).submit(parent, replay)
    ticket = await uow.get_profile_launch_ticket(ticket_ref)
    assert ticket is not None
    assert ticket.state is ProfileLaunchTicketState.CONSUMED
    assert ticket.child_command_id == delegate.command_id
    assert ticket.child_run_id == factory._child_run_id(
        start.run_id, delegate.command_id
    )
    assert command.operation_id == replay_command.operation_id
    assert command.intent.child_spec.profile_key == "workflow.deep_research"
    assert command.intent.child_spec.driver_kind == "workflow"
    leased = await uow.lease_child_commands(
        owner="profile-test", limit=1, lease_seconds=30.0
    )
    assert tuple(item.operation_id for item in leased) == (command.operation_id,)
    scheduled = await uow.schedule_child_command(
        command.operation_id,
        lease_owner="profile-test",
        lease_epoch=leased[0].schedule_lease_epoch,
    )
    assert scheduled.status.value == "scheduled"


@pytest.mark.asyncio
async def test_presentation_spawn_uses_completed_current_turn_objective(
    tmp_path,
) -> None:
    profiles = _profiles()
    registry = ToolRegistry()
    register_orchestration_controls(registry, profiles)
    start, tool_context = _request(tmp_path)
    objective = "生成一个新的 8 页可编辑中文 PowerPoint。"
    complete = (
        objective
        + "第3页必须是原生可编辑表格；"
        + "第6页必须是原生可编辑柱状图；不要栅格化正文。"
    )
    start = replace(
        start,
        canonical_messages=(
            {"role": "user", "content": "立即生成，完整目标：" + complete},
            *start.canonical_messages,
        ),
    )
    prepared = registry.prepare_call(
        WORKFLOW_SPAWN,
        {
            "profile_key": "workflow.presentation",
            "objective": objective,
            "catalog_generation": 7,
        },
        "session-1",
        "provider-call-1",
        execution_context=tool_context,
    )
    start, uow = await _seed_attempt(tmp_path, start, prepared)

    delegate = await ProductDelegateFactory(
        profiles,
        registry,
        uow,
    )(start, prepared)

    assert delegate.route_hint == "workflow.presentation"
    assert delegate.child_request["objective"] == complete
    assert delegate.child_request["topic"] == complete
    assert delegate.child_request["request"] == complete


@pytest.mark.asyncio
async def test_workflow_profile_runtime_lane_is_not_a_parent_tool_name(
    tmp_path,
) -> None:
    profiles = _profiles()
    registry = ToolRegistry()
    register_orchestration_controls(registry, profiles)
    start, tool_context = _request(tmp_path)
    start = replace(
        start,
        capability_snapshot={
            "tools": [WORKFLOW_SPAWN, "read_file"],
        },
    )
    prepared = registry.prepare_call(
        WORKFLOW_SPAWN,
        {
            "profile_key": "workflow.deep_research",
            "objective": "research current evidence",
            "catalog_generation": 7,
        },
        "session-1",
        "provider-call-1",
        execution_context=tool_context,
    )
    start, uow = await _seed_attempt(tmp_path, start, prepared)

    delegate = await ProductDelegateFactory(
        profiles,
        registry,
        uow,
    )(start, prepared)

    assert delegate.route_hint == "workflow.deep_research"
    assert delegate.capability_subset == ("read_file",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault_point",
    (
        "profile_ticket_claim_after_ticket_cas",
        "profile_ticket_claim_after_child_command",
        "profile_ticket_claim_after_child_link",
        "profile_ticket_claim_before_commit",
    ),
)
async def test_profile_launch_claim_crash_rolls_back_ticket_child_and_link(
    tmp_path, fault_point
) -> None:
    profiles = _profiles()
    registry = ToolRegistry()
    register_orchestration_controls(registry, profiles)
    start, tool_context = _request(tmp_path)
    prepared = registry.prepare_call(
        WORKFLOW_SPAWN,
        {
            "profile_key": "workflow.deep_research",
            "objective": "research current evidence",
            "catalog_generation": 7,
        },
        "session-1",
        "provider-call-1",
        execution_context=tool_context,
    )
    start, uow = await _seed_attempt(tmp_path, start, prepared)
    delegate = await ProductDelegateFactory(
        profiles, registry, uow
    )(
        start, prepared
    )
    actor = start.run_context.actor()
    parent = await uow.query(RunRef(start.run_id, start.session_id), actor)

    def crash(point: str) -> None:
        if point == fault_point:
            raise RuntimeError(f"crash:{fault_point}")

    crashing = SqliteExecutionUnitOfWork(
        tmp_path / "workflow.db", fault_injector=crash
    )
    with pytest.raises(RuntimeError, match=f"crash:{fault_point}"):
        await ChildRunCoordinator(crashing).submit(parent, delegate)

    restarted = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    ticket = await restarted.get_profile_launch_ticket(
        delegate.profile_launch_ticket_ref
    )
    assert ticket is not None
    assert ticket.state is ProfileLaunchTicketState.ISSUED
    operation_id = hashlib.sha256(
        f"execution-child-operation|{parent.run_id}|{delegate.command_id}".encode()
    ).hexdigest()
    assert await restarted.get_child_command(operation_id) is None
    assert await restarted.list_child_links(
        RunRef(parent.run_id, parent.context.session_id), actor
    ) == ()

    recovered_parent = await restarted.query(
        RunRef(parent.run_id, parent.context.session_id), actor
    )
    command = await ChildRunCoordinator(restarted).submit(
        recovered_parent, delegate
    )
    consumed = await restarted.get_profile_launch_ticket(
        delegate.profile_launch_ticket_ref
    )
    assert consumed is not None
    assert consumed.state is ProfileLaunchTicketState.CONSUMED
    assert consumed.child_command_id == command.intent.command_id
    links = await restarted.list_child_links(
        RunRef(parent.run_id, parent.context.session_id), actor
    )
    assert tuple(link.child_run_id for link in links) == (
        command.intent.child_run_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper_kind", ("driver", "route", "request"))
async def test_profile_launch_rejects_payload_route_and_request_tampering(
    tmp_path, tamper_kind
) -> None:
    profiles = _profiles()
    registry = ToolRegistry()
    register_orchestration_controls(registry, profiles)
    start, tool_context = _request(tmp_path)
    prepared = registry.prepare_call(
        WORKFLOW_SPAWN,
        {
            "profile_key": "workflow.deep_research",
            "objective": "research current evidence",
            "catalog_generation": 7,
        },
        "session-1",
        "provider-call-1",
        execution_context=tool_context,
    )
    start, uow = await _seed_attempt(tmp_path, start, prepared)
    delegate = await ProductDelegateFactory(
        profiles, registry, uow
    )(
        start, prepared
    )
    child_request = dict(delegate.child_request)
    route_hint = delegate.route_hint
    launch_request = dict(delegate.profile_launch_request)
    if tamper_kind == "driver":
        child_request["driver_kind"] = "react"
    elif tamper_kind == "route":
        route_hint = "agent.general"
    else:
        launch_request["objective"] = "tampered objective"
    tampered = DelegateRun(
        delegate.run_id,
        delegate.command_id,
        child_request,
        route_hint,
        delegate.capability_subset,
        delegate.attachment_policy,
        delegate.join_policy,
        profile_launch_ticket_ref=delegate.profile_launch_ticket_ref,
        profile_launch_request=launch_request,
    )
    parent = await uow.query(
        RunRef(start.run_id, start.session_id), start.run_context.actor()
    )

    with pytest.raises(ValueError, match="profile launch"):
        await ChildRunCoordinator(uow).submit(parent, tampered)

    ticket = await uow.get_profile_launch_ticket(
        delegate.profile_launch_ticket_ref
    )
    assert ticket is not None
    assert ticket.state is ProfileLaunchTicketState.ISSUED


@pytest.mark.asyncio
async def test_pre_atomic_consumed_ticket_is_reconciled_as_frozen_legacy(
    tmp_path,
) -> None:
    profiles = _profiles()
    registry = ToolRegistry()
    register_orchestration_controls(registry, profiles)
    start, tool_context = _request(tmp_path)
    prepared = registry.prepare_call(
        WORKFLOW_SPAWN,
        {
            "profile_key": "workflow.deep_research",
            "objective": "research current evidence",
            "catalog_generation": 7,
        },
        "session-1",
        "provider-call-1",
        execution_context=tool_context,
    )
    start, uow = await _seed_attempt(tmp_path, start, prepared)
    delegate = await ProductDelegateFactory(profiles, registry, uow)(
        start, prepared
    )
    child_run_id = ProductDelegateFactory._child_run_id(
        start.run_id, delegate.command_id
    )
    await uow.consume_profile_launch_ticket(
        delegate.profile_launch_ticket_ref,
        request_fingerprint=fingerprint_json(
            dict(delegate.profile_launch_request)
        ),
        child_command_id=delegate.command_id,
        child_run_id=child_run_id,
        expected_version=0,
    )
    parent = await uow.query(
        RunRef(start.run_id, start.session_id), start.run_context.actor()
    )

    command = await ChildRunCoordinator(uow).submit(parent, delegate)

    assert command.intent.child_run_id == child_run_id
    links = await uow.list_child_links(
        RunRef(start.run_id, start.session_id), start.run_context.actor()
    )
    assert tuple(link.child_run_id for link in links) == (child_run_id,)


def test_external_action_wait_is_model_visible_but_host_intercepted() -> None:
    registry = ToolRegistry()
    register_orchestration_controls(registry, _profiles())

    assert registry.dispatch_kind(EXTERNAL_ACTION_WAIT) == "handler"
    schema = next(
        item["function"]
        for item in registry.schemas()
        if item["function"]["name"] == EXTERNAL_ACTION_WAIT
    )
    assert schema["parameters"]["properties"]["wait_kind"]["enum"] == [
        "uac",
        "credential",
        "user_content",
        "third_party",
    ]
    prepared = registry.prepare_call(
        EXTERNAL_ACTION_WAIT,
        {
            "wait_kind": "uac",
            "required_action": "Confirm the Windows prompt.",
        },
        "session-1",
        "provider-call-external",
    )
    assert registry.prepared_execution_policy(prepared) == (False, False)


def test_project_directory_select_is_model_visible_and_path_free() -> None:
    registry = ToolRegistry()
    register_orchestration_controls(registry, _profiles())

    assert PROJECT_DIRECTORY_SELECT in CORE_CONTROL_NAMES
    schema = next(
        item["function"]
        for item in registry.schemas()
        if item["function"]["name"] == PROJECT_DIRECTORY_SELECT
    )
    assert "parent_directory" not in schema["parameters"]["properties"]
    assert schema["parameters"]["required"] == [
        "project_name",
        "folder_name",
        "project_kind",
        "directory_mode",
    ]
    assert schema["parameters"]["properties"]["directory_mode"]["enum"] == [
        "create_new",
        "use_existing",
    ]
    assert "this project/这个项目" in schema["description"]
    assert "check/fix" in schema["parameters"]["properties"]["directory_mode"][
        "description"
    ]
    prepared = registry.prepare_call(
        PROJECT_DIRECTORY_SELECT,
        {
            "project_name": "末日生存 Demo",
            "folder_name": "apocalypse-demo",
            "project_kind": "Godot 游戏",
            "directory_mode": "create_new",
        },
        "session-1",
        "provider-call-project-directory",
    )
    assert registry.prepared_execution_policy(prepared) == (False, False)


def test_workspace_prepare_freezes_the_host_bound_write_scope(tmp_path) -> None:
    registry = ToolRegistry()
    register_orchestration_controls(registry, _profiles())
    _start, tool_context = _request(tmp_path)

    prepared = registry.prepare_call(
        WORKSPACE_PREPARE,
        {},
        "session-1",
        "provider-call-1",
        execution_context=tool_context,
    )

    assert len(prepared.resource_selectors) == 1
    selector = prepared.resource_selectors[0]
    assert selector.kind == "filesystem"
    assert selector.canonical_value == canonical_filesystem_path(tmp_path)
    assert selector.access == ("write",)
    assert registry.get(WORKSPACE_PREPARE).outcome_parser_id == (
        "json_error_envelope_v1"
    )
    assert registry.prepared_execution_policy(prepared) == (True, True)


@pytest.mark.asyncio
async def test_workspace_prepare_reports_success_after_exact_authorization(
    tmp_path,
) -> None:
    registry = ToolRegistry()
    register_orchestration_controls(registry, _profiles())
    _start, tool_context = _request(tmp_path)
    prepared = registry.prepare_call(
        WORKSPACE_PREPARE,
        {},
        "session-1",
        "provider-call-1",
        execution_context=tool_context,
    )
    authorization = {
        "run_id": tool_context.run_id,
        "call_id": prepared.stable_call_id,
        "effect_id": tool_context.effect_id,
        "tool_name": prepared.tool_name,
        "args_hash": prepared.args_hash,
        "capability_hash": tool_context.capability_hash,
        "scope_hash": tool_context.scope_hash,
        "permission_policy_version": prepared.permission_policy_version,
        "expires_at": time.time() + 60,
    }

    outcome = await registry.execute_prepared(
        prepared,
        effect_id=tool_context.effect_id,
        authorization=authorization,
        execution_context=tool_context,
    )

    assert outcome.state.value == "success"
    assert outcome.value["ok"] is True
    assert outcome.value["result"]["workspace_root"] == str(tmp_path.resolve())


@pytest.mark.asyncio
async def test_react_control_admits_attempt_before_consuming_launch_ticket(
    tmp_path,
) -> None:
    profiles = _profiles()
    registry = ToolRegistry()
    register_orchestration_controls(registry, profiles)
    start, tool_context = _request(tmp_path)
    prepared = registry.prepare_call(
        WORKFLOW_SPAWN,
        {
            "profile_key": "workflow.deep_research",
            "objective": "research current evidence",
            "catalog_generation": 7,
        },
        "session-1",
        "provider-call-1",
        execution_context=tool_context,
    )
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.activate_runtime()
    factory = ProductDelegateFactory(profiles, registry, uow)

    class Collaborator:
        async def start(self, _request):
            yield ReactControlBatch(
                "provider-batch-control",
                prepared,
                tool_context,
                tuple(start.canonical_messages),
            )

        async def prepare_control(self, request, call):
            return await factory(request, call)

    driver = ReActDriver(
        Collaborator(),
        uow,
        registry,
        auto_mode_check=lambda: True,
    )
    live = BoundedLiveIndex()
    actor = ActorContext(
        "local:session-1",
        "session-1",
        1,
        start.run_id,
        start.run_context.capability_hash,
    )
    live.add(
        start.run_id,
        actor,
    )
    driver.bind_live_index(live)

    events = [item async for item in driver.start(start)]

    delegate = next(item for item in events if item.kind == "delegate_run")
    assert delegate.kind == "delegate_run"
    record = await uow.load_continuation(start.run_id)
    boundary = ReactCommandBoundary.from_record(record)
    attempt_id = str(boundary.completion_state["current_attempt"]["attempt_id"])
    attempt = await uow.get_attempt(attempt_id)
    calls = await uow.list_provider_action_calls("provider-batch-control")
    assert attempt is not None
    assert tuple(call.admission_state.value for call in calls) == ("prepared",)
    replay = await factory(boundary.to_start(), prepared)
    assert replay.command_id == delegate.command_id
    ticket_ref = "profile-launch:" + hashlib.sha256(
        f"{start.run_id}|{prepared.stable_call_id}".encode("utf-8")
    ).hexdigest()
    ticket = await uow.get_profile_launch_ticket(ticket_ref)
    assert ticket is not None
    assert ticket.state is ProfileLaunchTicketState.ISSUED
    assert ticket.attempt_id == attempt_id
    parent = await uow.query(RunRef(start.run_id, start.session_id), actor)
    child_command = await ChildRunCoordinator(uow).submit(parent, delegate)
    ticket = await uow.get_profile_launch_ticket(ticket_ref)
    assert ticket is not None
    assert ticket.state is ProfileLaunchTicketState.CONSUMED
    assert ticket.child_command_id == delegate.command_id
    assert ticket.child_run_id == child_command.intent.child_spec.run_id


@pytest.mark.asyncio
async def test_workflow_spawn_rejects_stale_catalog_generation(tmp_path) -> None:
    profiles = _profiles()
    registry = ToolRegistry()
    register_orchestration_controls(registry, profiles)
    start, tool_context = _request(tmp_path)
    with pytest.raises(
        ValueError, match="workflow_spawn_catalog_binding_mismatch"
    ):
        registry.prepare_call(
            WORKFLOW_SPAWN,
            {
                "profile_key": "workflow.deep_research",
                "objective": "research current evidence",
                "catalog_generation": 6,
            },
            "session-1",
            "provider-call-1",
            execution_context=tool_context,
        )


@pytest.mark.asyncio
async def test_capability_builder_spawn_freezes_bounded_builder_protocol(
    tmp_path,
) -> None:
    profiles = _profiles()
    registry = ToolRegistry()
    register_orchestration_controls(registry, profiles)
    start, tool_context = _request(tmp_path)
    start = replace(
        start,
        request_payload={
            **dict(start.request_payload),
            "execution_capabilities": ["workflow", CAPABILITY_BUILD],
        },
        capability_snapshot={"tools": ["workflow", CAPABILITY_BUILD]},
    )
    prepared = registry.prepare_call(
        CAPABILITY_BUILD,
        {
            "objective": "build a reusable photo renamer",
            "original_args": {"folder": "photos"},
            "scope": "run",
            "catalog_generation": 7,
        },
        "session-1",
        "provider-call-1",
        execution_context=tool_context,
    )
    start, uow = await _seed_attempt(tmp_path, start, prepared)
    store = CapabilityStore(uow)
    state = await store.state()
    stamp = CatalogStamp(
        catalog_generation=state.catalog_generation,
        registry_revision=registry.catalog_snapshot().revision,
        binding_generation=state.binding_generation,
        skill_revision=0,
        mcp_revision=0,
    )
    receipt_ref = fingerprint_json({"search": "photo renamer"})
    snapshot_ref = "catalog:fixture"
    await store.record_search_receipt(
        receipt_id=receipt_ref,
        root_run_id=start.run_id,
        catalog_stamp_fingerprint=stamp.fingerprint,
        query_hash=fingerprint_json({"query": "photo renamer"}),
        result={"snapshot_ref": snapshot_ref, "hits": [], "semantic_used": False},
        created_at=1.0,
    )
    search_result = {
        "search_receipt_ref": receipt_ref,
        "catalog_stamp": stamp.to_dict(),
        "snapshot_ref": snapshot_ref,
        "matches": [],
    }
    start = replace(
        start,
        canonical_messages=(
            {
                "role": "tool",
                "tool_call_id": "search-call",
                "name": "capability_search",
                "content": json.dumps(search_result),
            },
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "provider-call-1",
                        "type": "function",
                        "function": {
                            "name": CAPABILITY_BUILD,
                            "arguments": "{}",
                        },
                    }
                ],
            },
        ),
    )
    builder_host = CapabilityBuilderHost(
        uow,
        registry,
        staging_base=tmp_path / "managed-builder",
    )
    delegate = await ProductDelegateFactory(
        profiles,
        registry,
        uow,
        capability_builder_host=builder_host,
        provider_snapshot_resolver=lambda provider_id: (
            {"provider_id": provider_id},
            {"model": "fixture-model"},
        ),
    )(
        start, prepared
    )
    payload = delegate.child_request
    assert payload["driver_kind"] == "workflow"
    assert payload["fix_budget"] == 3
    assert payload["capability_builder"]["max_repair_drafts"] == 3
    assert payload["plan_steps"][-1].startswith("Return the final draft")
    assert payload["messages"][0]["role"] == "system"
    assert "Never edit DeskPet core source" in payload["messages"][0]["content"]
    assert payload["workspace_ref"].startswith(str(tmp_path / "managed-builder"))
    assert payload["session_ref"]["session_id"] == "session-1"
    assert payload["session_ref"]["task_scope_id"] == "task-1"
    assert "code_session_id" not in payload["session_ref"]
    assert "base_session_id" not in payload["session_ref"]
    assert not (tmp_path / "capability-staging").exists()


@pytest.mark.asyncio
async def test_personal_workflow_spawn_uses_only_trusted_frozen_selection(
    tmp_path,
) -> None:
    objective = "run my daily three workflow"
    selection = _personal_selection(objective)
    profiles = _profiles()
    registry = ToolRegistry()
    register_orchestration_controls(registry, profiles)
    start, tool_context = _request(tmp_path)
    start = replace(
        start,
        request_payload={
            **dict(start.request_payload),
            "execution_capabilities": ["workflow", "secret_tool"],
            "capability_refresh": {
                "catalog_snapshot_ref": "payload-forgery",
                "catalog_stamp": {"generation": 999},
            },
            "owner_key": "attacker",
            "graph": {"attacker": True},
        },
        capability_snapshot={
            "tools": ["workflow"],
            "host_extensions": {
                SELECTION_EXTENSION_KEY: {
                    "personal_workflow_selection": (
                        selection.to_child_payload()
                    )
                }
            },
        },
    )
    prepared = registry.prepare_call(
        WORKFLOW_SPAWN,
        {
            "profile_key": "workflow.personal_v1",
            "objective": objective,
            "input_refs": ["message:1"],
            "catalog_generation": 7,
        },
        "session-1",
        "provider-call-1",
        execution_context=tool_context,
    )
    start, uow = await _seed_attempt(tmp_path, start, prepared)

    delegate = await ProductDelegateFactory(profiles, registry, uow)(
        start, prepared
    )
    ticket = await uow.get_profile_launch_ticket(
        "profile-launch:"
        + hashlib.sha256(
            f"{start.run_id}|{prepared.stable_call_id}".encode()
        ).hexdigest()
    )

    assert delegate.route_hint == "workflow.personal_v1"
    assert delegate.capability_subset == ("workflow",)
    assert "capability_snapshot_lease" not in delegate.child_request
    assert (
        delegate.child_request["personal_workflow_selection"]
        == selection.to_child_payload()
    )
    assert ticket is not None
    assert ticket.personal_selection_id == selection.selection_id
    assert (
        ticket.personal_selection_fingerprint
        == selection.selection_fingerprint
    )


@pytest.mark.asyncio
async def test_personal_workflow_spawn_without_selection_fails_before_ticket(
    tmp_path,
) -> None:
    profiles = _profiles()
    registry = ToolRegistry()
    register_orchestration_controls(registry, profiles)
    start, tool_context = _request(tmp_path)
    prepared = registry.prepare_call(
        WORKFLOW_SPAWN,
        {
            "profile_key": "workflow.personal_v1",
            "objective": "run my daily three workflow",
            "catalog_generation": 7,
        },
        "session-1",
        "provider-call-1",
        execution_context=tool_context,
    )
    start, uow = await _seed_attempt(tmp_path, start, prepared)

    with pytest.raises(ValueError, match="frozen parent selection"):
        await ProductDelegateFactory(profiles, registry, uow)(start, prepared)
    assert await uow.get_profile_launch_ticket(
        "profile-launch:"
        + hashlib.sha256(
            f"{start.run_id}|{prepared.stable_call_id}".encode()
        ).hexdigest()
    ) is None


@pytest.mark.asyncio
async def test_durable_task_child_keeps_ticket_bound_capability_records(
    tmp_path,
) -> None:
    base = _profiles()
    profiles = ProfileRegistry(
        (
            *base.specs.values(),
            ProfileSpec(
                "workflow.durable_task",
                None,
                "workflow",
                capabilities=frozenset({"workflow"}),
                workflow_key="durable.task.v1",
                workflow_name="durable_task",
                workflow_version="v1",
                state_factory=lambda **values: values,
                context_factory=lambda *args, **kwargs: (args, kwargs),
                display_name="Durable Task",
                description="Execute a durable multi-step task.",
                launch_policy="model_spawnable",
            ),
        ),
        generation=7,
    )
    registry = ToolRegistry()
    register_orchestration_controls(registry, profiles)
    start, tool_context = _request(tmp_path)
    start = replace(
        start,
        run_context=replace(
            start.run_context,
            provider_plan={
                "providers": ["fixture"],
                "bindings": [
                    {"provider_id": "fixture", "model_id": "kimi-k3"}
                ],
            },
        ),
    )
    start = replace(
        start,
        request_payload={
            **dict(start.request_payload),
            "execution_capabilities": [
                WORKFLOW_SPAWN,
                WORKSPACE_PREPARE,
                PROJECT_DIRECTORY_SELECT,
                EXTERNAL_ACTION_WAIT,
            ],
        },
        capability_snapshot={
            "tools": [
                WORKFLOW_SPAWN,
                WORKSPACE_PREPARE,
                PROJECT_DIRECTORY_SELECT,
                EXTERNAL_ACTION_WAIT,
            ],
        },
    )
    prepared = registry.prepare_call(
        WORKFLOW_SPAWN,
        {
            "profile_key": "workflow.durable_task",
            "objective": "build a game",
            "plan_steps": [
                "创建 Godot 项目结构",
                "实现核心玩法",
                "运行项目验证",
            ],
            "output_refs": ["project.godot", "main.gd"],
            "scratch_refs": [".task-tmp/"],
            "workspace_ref": str(tmp_path),
            "catalog_generation": 7,
        },
        "session-1",
        "provider-call-1",
        execution_context=tool_context,
    )
    start, uow = await _seed_attempt(tmp_path, start, prepared)

    resolver_calls: list[str] = []

    def legacy_resolver(provider_id: str):
        resolver_calls.append(provider_id)
        return {"provider_id": provider_id}, {"model": "wrong-default-model"}

    delegate = await ProductDelegateFactory(
        profiles,
        registry,
        uow,
        provider_snapshot_resolver=legacy_resolver,
    )(
        start, prepared
    )
    parent = await uow.query(
        RunRef(start.run_id, start.session_id), start.run_context.actor()
    )
    command = await ChildRunCoordinator(uow).submit(parent, delegate)

    assert delegate.profile_launch_request["trusted_child_payload_hash"]
    assert PROJECT_DIRECTORY_SELECT not in delegate.capability_subset
    assert EXTERNAL_ACTION_WAIT not in delegate.capability_subset
    assert command.intent.child_request["capability_snapshot"]
    assert {
        item["tool_name"]
        for item in command.intent.child_request["capability_snapshot"]
    }.issubset(set(delegate.capability_subset))
    assert command.intent.child_request["provider_snapshot"] == {
        "provider_id": "fixture"
    }
    assert command.intent.child_request["model_snapshot"] == {
        "model": "kimi-k3"
    }
    assert resolver_calls == []
    assert command.intent.child_request["plan_steps"] == (
        "创建 Godot 项目结构",
        "实现核心玩法",
        "运行项目验证",
    )
    assert command.intent.child_request["output_contract"]["output_refs"] == (
        "main.gd",
        "project.godot",
    )
    assert command.intent.child_request["output_contract"]["scratch_refs"] == (
        ".task-tmp/",
    )
    assert len(
        command.intent.child_request["output_contract"]["baseline_digest"]
    ) == 64


@pytest.mark.asyncio
async def test_new_project_spawn_requires_native_directory_binding(
    tmp_path,
) -> None:
    base = _profiles()
    profiles = ProfileRegistry(
        (
            *base.specs.values(),
            ProfileSpec(
                "workflow.durable_task",
                None,
                "workflow",
                capabilities=frozenset({"workflow"}),
                workflow_key="durable.task.v1",
                workflow_name="durable_task",
                workflow_version="v1",
                state_factory=lambda **values: values,
                context_factory=lambda *args, **kwargs: (args, kwargs),
                display_name="Durable Task",
                description="Execute a durable multi-step task.",
                launch_policy="model_spawnable",
            ),
        ),
        generation=7,
    )
    registry = ToolRegistry()
    register_orchestration_controls(registry, profiles)
    start, tool_context = _request(tmp_path)
    prepared = registry.prepare_call(
        WORKFLOW_SPAWN,
        {
            "profile_key": "workflow.durable_task",
            "objective": "创建一个 Godot 游戏 Demo",
            "plan_steps": ["创建项目结构", "实现核心玩法"],
            "catalog_generation": 7,
        },
        "session-1",
        "provider-call-1",
        execution_context=tool_context,
    )
    start, uow = await _seed_attempt(tmp_path, start, prepared)
    resolver = TaskWorkContextResolver(tmp_path)
    work = resolver.resolve(
        session_id=start.session_id,
        root_run_id=start.run_id,
        task_scope_id="task-1",
        require_workspace=True,
    )
    await uow.create_task_context(
        work,
        resolver.conversation_boundary(work, ("request:new-project",)),
        resolver.projection(work),
    )

    with pytest.raises(ValueError, match="project_workspace_selection_required"):
        await ProductDelegateFactory(profiles, registry, uow)(start, prepared)
