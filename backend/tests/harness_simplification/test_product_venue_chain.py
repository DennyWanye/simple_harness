# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""R5 test-only Text product chain through the real generation-one Kernel."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import MappingProxyType
from types import SimpleNamespace
from pathlib import Path
from typing import Any

import pytest

from deskpet.agent.product_domain_sink import ProductDomainSink
from deskpet.agent.run_presenter import (
    CanonicalRunEventPresentationAdapter,
    PresentationState,
    RunPresentationContext,
    build_product_run_presenter,
)
from deskpet.agent.turn_preparer import (
    PlannedTurnDecision,
    PreparedTurnContext,
    ProductDomainCommand,
    ProductTurnPreparer,
    RoutedTurnIntent,
    TurnInput,
)
from deskpet.capabilities.platform import CapabilityPlatform
from deskpet.capabilities.run_catalog import (
    SqliteRunCatalogLeasePreparer,
)
from deskpet.capabilities.store import CapabilityStore
from deskpet.companion.turn_authority import (
    FinalizedCompanionTurnV1,
)
from deskpet.execution.contracts import (
    ActorContext,
    LiveCursor,
    OutcomeStatus,
    ProviderLaunchSnapshot,
    RunEvent,
    RunEventCandidate,
    RunRef,
)
from deskpet.harness.adapters.venues import (
    KernelRunClient,
    ProductVenueRunAdapter,
    ProductVenueRunResult,
    ProductVenueRunSession,
)
from deskpet.harness.contracts import driver_catalog
from deskpet.harness.kernel import HostContext, RegisteredDriver, RunKernel
from deskpet.harness.ports import DriverTerminalCandidate, TokenCandidate
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.harness.router import ClassifiedRoute, RegisteredRouter
from deskpet.memory.session_db import SessionDB
from deskpet.tools.build_identity import ExecutionBuildIdentity
from deskpet.tools.capabilities import (
    ToolCapabilityResolver,
    ToolEligibilityContext,
    ToolExposureIntent,
)
from deskpet.tools.registry import ToolRegistry
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork
from pipeline.voice_pipeline import VoicePipeline


def _host(session_id: str) -> HostContext:
    return HostContext(
        session_id, f"principal-{session_id}", 1, "c" * 64,
        frozenset(), ("fixture",), "trace-product-chain",
    )

class _Classifier:
    def classify(self, _request):
        return ClassifiedRoute("react.default", "fixture", 1.0)


def _profiles(profile_key: str, driver_kind: str) -> ProfileRegistry:
    return ProfileRegistry((ProfileSpec(profile_key, profile_key, driver_kind),))


class _Driver:
    def __init__(self, *, fail: bool = False, order: list[str] | None = None) -> None:
        self.fail = fail
        self.starts = []
        self.order = order if order is not None else []

    async def start(self, request):
        self.starts.append(request)
        self.order.append("driver")
        yield TokenCandidate(request.run_id, "hello ")
        if self.fail:
            yield DriverTerminalCandidate(
                request.run_id, "failed", error="provider unavailable"
            )
        else:
            yield DriverTerminalCandidate(request.run_id, "completed", "hello world")

    async def signal(self, _signal):
        if False:
            yield TokenCandidate("unused", "")

    async def cancel(self, _run_id, _reason):
        if False:
            yield TokenCandidate("unused", "")

    async def recover(self, _run_id, _recovery_lease):
        if False:
            yield TokenCandidate("unused", "")

    async def close(self):
        return None


class _Pipeline:
    enabled = True

    async def run_pre_loop(self, _text: str, *, prior_task_type=None):
        del prior_task_type
        return SimpleNamespace(
            events=[{"type": "chat_v2_intent", "payload": {"kind": "ask"}}],
            short_circuit=False,
            needs_clarification=False,
            intent=SimpleNamespace(problem_type="simple"),
            system_injections=["pipeline context"],
            contradiction=None,
        )


class _ClarificationPipeline:
    enabled = True

    async def run_pre_loop(self, _text: str, *, prior_task_type=None):
        del prior_task_type
        return SimpleNamespace(
            events=[],
            short_circuit=False,
            needs_clarification=True,
            intent=SimpleNamespace(
                problem_type="clarification",
                clarifying_questions=["Which harness path?"],
            ),
            system_injections=[],
            contradiction=None,
        )


class _DomainSink(ProductDomainSink):
    def __init__(self, order: list[str]) -> None:
        self.frames: list[dict[str, Any]] = []
        self.plans: list[dict[str, Any]] = []
        self.order = order

    async def emit(self, frame):
        self.order.append("domain")
        self.frames.append(dict(frame))

    async def set_idle(self, _session_id):
        return None

    async def persist_assistant(self, _session_id, _text):
        return None

    async def store_plan(self, payload):
        self.plans.append(dict(payload))

    async def await_plan(self, _payload):
        raise AssertionError("durable admission must not call the legacy plan waiter")


class _WS:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
        self.binary_frames: list[bytes] = []

    async def send_json(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)

    async def send_bytes(self, frame: bytes) -> None:
        self.binary_frames.append(bytes(frame))


class _Vector:
    def __init__(self) -> None:
        self.items: list[tuple[int, str]] = []

    async def enqueue(self, message_id: int, text: str) -> None:
        self.items.append((message_id, text))


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        raw={},
        features=SimpleNamespace(
            context_os_v1=False,
            summary_quality_loop=False,
            plan_confirm_gate=False,
            plan_read_only=False,
            problem_pipeline=SimpleNamespace(plan_companion_enabled=False),
        ),
        skills=SimpleNamespace(
            auto_disclosure=SimpleNamespace(
                enabled=False,
                strong_threshold=0.8,
                budget_tokens=100,
                per_skill_max_tokens=50,
            )
        ),
    )


def _presentation_context(
    ws: _WS,
    session_db: SessionDB,
    vector: _Vector,
) -> tuple[RunPresentationContext, list[dict[str, Any]]]:
    peers: list[dict[str, Any]] = []

    async def broadcast(_origin: object, frame: dict[str, Any]) -> None:
        peers.append(frame)

    async def send_final(target: _WS, frame: dict[str, Any], **_kwargs: Any) -> None:
        await target.send_json(frame)

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    return RunPresentationContext(
        session_id="text-session",
        text="explain the harness",
        websocket=ws,
        services={},
        config=_config(),
        messages=[],
        session_db=session_db,
        vector_worker=vector,
        activity_store=None,
        provider_chain=None,
        fallback_provider=None,
        request_id="request-text",
        max_iterations=16,
        is_sentinel=False,
        broadcast=broadcast,
        send_final=send_final,
        emit_context_usage=nothing,
        intent_label_from_turn=lambda _used: "ask",
    ), peers


async def _stack(tmp_path, *, fail: bool = False):
    order: list[str] = []
    uow = SqliteExecutionUnitOfWork(tmp_path / "execution.db")
    await uow.initialize()
    activation = await uow.activate_runtime()
    assert (activation.phase, activation.generation) == ("open", 1)
    driver = _Driver(fail=fail, order=order)
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            _Classifier(), _profiles("react.default", "react")
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver, durable_from_start=True),)),
    )
    run_client = KernelRunClient(kernel)
    presenter = build_product_run_presenter()
    adapter = ProductVenueRunAdapter(
        preparer=ProductTurnPreparer(),
        run_client=run_client,
        presenter=presenter,
    )
    session_db = SessionDB(tmp_path / "state.db")
    await session_db.initialize()
    ws, vector = _WS(), _Vector()
    context, peers = _presentation_context(ws, session_db, vector)
    sink = _DomainSink(order)
    return adapter, kernel, driver, uow, session_db, ws, vector, context, peers, sink, order


async def _execute_adapter(adapter, *args, **kwargs):
    outcome = await adapter.open(*args, **kwargs)
    if isinstance(outcome, ProductVenueRunResult):
        return outcome
    try:
        async for _event in outcome.events:
            pass
    finally:
        await outcome.close()
    return outcome.result


@pytest.mark.asyncio
async def test_text_product_chain_prepares_drives_presents_and_closes(tmp_path) -> None:
    (
        adapter,
        kernel,
        driver,
        uow,
        session_db,
        ws,
        vector,
        context,
        peers,
        sink,
        order,
    ) = await _stack(tmp_path)

    result = await _execute_adapter(adapter,
        TurnInput(
            text="explain the harness",
            session_id="text-session",
            request_id="request-text",
            turn_id="turn-text",
            venue="text",
            context_usage_basis_sample_id="sample-before",
        ),
        _host("text-session"),
        services={"problem_pipeline": _Pipeline()},
        config=_config(),
        local_llm=SimpleNamespace(model="fixture", base_url="local"),
        tool_registry=object(),
        provider=object(),
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
        presentation_context=context,
        domain_sink=sink,
    )

    assert result.status == "succeeded"
    assert result.final_text == "hello world"
    assert order == ["driver"]
    assert sink.frames == []
    assert [dict(item) for item in driver.starts[0].canonical_messages] == [
        {"role": "user", "content": "explain the harness"},
    ]
    assert driver.starts[0].request_payload["loop"] == {
        "user_request": "explain the harness",
        "is_sentinel_run": False,
        "pipeline_problem_type": None,
        "pipeline_needs_investigation": False,
        "pipeline_observability": False,
        "convergence_report_on_stop": False,
        "response_quality": None,
    }
    assert (
        driver.starts[0].request_payload[
            "context_usage_basis_sample_id"
        ]
        == "sample-before"
    )
    stage_trace = driver.starts[0].request_payload["product_turn_trace"]
    assert [step["step"] for step in stage_trace] == [
        "prepare_context",
        "direct_run",
    ]
    assert all(step["duration_ms"] >= 0 for step in stage_trace)
    assert stage_trace[0]["input"]["turn"]["session_id"] == "text-session"
    assert stage_trace[0]["output"]["messages"][0]["content"] == (
        "explain the harness"
    )
    assert stage_trace[1]["output"] == {
        "authority": "main_agent",
        "context_source": "assembled_session",
    }
    assert [frame["type"] for frame in ws.frames] == ["chat_v2_delta", "chat_v2_final"]
    assert [frame["type"] for frame in peers] == ["chat_v2_delta"]
    messages = await session_db.get_messages("text-session")
    assert messages[-1]["content"] == "hello world"
    assert vector.items == [(messages[-1]["id"], "hello world")]

    actor = _host("text-session").actor(root_run_id=str(result.run_id))
    record = await uow.query(RunRef(str(result.run_id), "text-session"), actor)
    assert record.status.value == "completed"
    assert kernel._live.values() == ()


@pytest.mark.asyncio
async def test_text_product_chain_materializes_its_default_task_workspace(
    tmp_path,
) -> None:
    (
        adapter,
        _kernel,
        driver,
        _uow,
        _session_db,
        _ws,
        _vector,
        context,
        _peers,
        sink,
        _order,
    ) = await _stack(tmp_path)
    workspace_base = tmp_path / "workspace"
    host = replace(
        _host("text-session"),
        write_scope_root=str(workspace_base),
    )

    result = await _execute_adapter(
        adapter,
        TurnInput(
            text="run a command",
            session_id="text-session",
            request_id="request-workspace",
            turn_id="turn-workspace",
            venue="text",
        ),
        host,
        services={},
        config=_config(),
        local_llm=object(),
        tool_registry=object(),
        provider=object(),
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
        presentation_context=context,
        domain_sink=sink,
    )

    task_workspace = Path(driver.starts[0].run_context.workspace["root"])
    assert result.status == "succeeded"
    assert task_workspace.parent == workspace_base.resolve()
    assert task_workspace.is_dir()
    assert driver.starts[0].run_context.workspace["write_scope_root"] == str(
        task_workspace
    )


@pytest.mark.asyncio
async def test_failed_terminal_never_projects_green_success(tmp_path) -> None:
    stack = await _stack(tmp_path, fail=True)
    adapter, kernel, _driver, _uow, session_db, ws, _vector, context, _peers, sink, _order = stack

    result = await _execute_adapter(adapter,
        TurnInput(
            text="explain the harness",
            session_id="text-session",
            request_id="request-fail",
            turn_id="turn-fail",
            venue="text",
        ),
        _host("text-session"),
        services={},
        config=_config(),
        local_llm=object(),
        tool_registry=object(),
        provider=object(),
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
        presentation_context=context,
        domain_sink=sink,
    )

    assert result.status == "failed"
    assert result.final_text == ""
    assert [frame["type"] for frame in ws.frames] == ["chat_v2_delta", "chat_v2_error"]
    assert not any(frame["type"] == "chat_v2_final" for frame in ws.frames)
    assert await session_db.get_messages("text-session") == []
    assert kernel._live.values() == ()


@pytest.mark.asyncio
async def test_legacy_clarification_cannot_short_circuit_the_main_agent(tmp_path) -> None:
    stack = await _stack(tmp_path)
    adapter, kernel, driver, _uow, _db, _ws, _vector, context, _peers, sink, _order = stack

    outcome = await adapter.open(
        TurnInput(
            text=context.text,
            session_id=context.session_id,
            request_id="request-clarify",
            turn_id="turn-clarify",
            venue="text",
        ),
        _host(context.session_id),
        services={"problem_pipeline": _ClarificationPipeline()},
        config=_config(),
        local_llm=object(),
        tool_registry=object(),
        provider=object(),
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
        presentation_context=context,
        domain_sink=sink,
    )

    assert isinstance(outcome, ProductVenueRunSession)
    async for _event in outcome.events:
        pass
    await outcome.close()
    assert len(driver.starts) == 1
    assert sink.frames == []
    assert kernel._live.values() == ()


@pytest.mark.asyncio
async def test_non_chat_venue_is_not_a_product_ingress() -> None:
    with pytest.raises(ValueError, match="unsupported product venue"):
        TurnInput(
            text="fix the bug",
            session_id="base-session",
            request_id="request-code",
            turn_id="turn-code",
            venue="code",  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_venue_finalizes_companion_from_its_single_real_catalog_lease(
    tmp_path,
) -> None:
    stack = await _stack(tmp_path)
    (
        adapter,
        _kernel,
        driver,
        uow,
        _session_db,
        _ws,
        _vector,
        context,
        _peers,
        sink,
        _order,
    ) = stack
    registry = ToolRegistry()

    async def fixture_read(_args):
        return "ok"

    registry.register(
        "fixture_read",
        "core",
        {
            "name": "fixture_read",
            "description": "fixture",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        fixture_read,
        stable_handler_id="fixture.read.v1",
        execution_build_identity=ExecutionBuildIdentity(
            provider="core",
            handler_id="fixture.read.v1",
            build_digest="a" * 64,
            sources_manifest_hash="b" * 64,
            artifacts=(("fixture.py", "c" * 64),),
        ),
    )
    store = CapabilityStore(uow)
    await store.initialize()
    platform = CapabilityPlatform(
        registry=registry,
        store=store,
        user_data_root=tmp_path / "capabilities",
        register_control_surface=False,
        first_party_pack_roots=(),
    )
    platform.run_catalog_lease_preparer = SqliteRunCatalogLeasePreparer(
        store=store,
        registry=registry,
        hub=platform.hub,
        process_instance_id="process-venue-fixture",
    )
    exposure = ToolExposureIntent(
        required_direct_names=("fixture_read",)
    )
    eligibility = ToolEligibilityContext(
        session_id="text-session",
        request_id="request-authority",
        task_type="chat",
    )
    tool_set = (
        ToolCapabilityResolver(registry)
        .resolve_draft(exposure, eligibility=eligibility)
        .finalize(scope_id="scope-authority")
    )
    authority_state = SimpleNamespace(
        preparation_id="prepared-companion-turn-1",
        owner_key="companion:alice:4",
        owner=SimpleNamespace(profile_generation=4),
        binding_epoch=7,
    )

    class Finalizer:
        calls = []

        async def finalize_after_catalog(
            self, state, capture, *, run_id: str
        ):
            self.calls.append((state, capture, run_id))
            return FinalizedCompanionTurnV1(
                preparation_id=state.preparation_id,
                owner_key=state.owner_key,
                profile_generation=4,
                binding_epoch=7,
                product_snapshot_ref="growth-snapshot-authority-1",
                product_snapshot_hash="d" * 64,
                growth_dependencies=(),
                selection_payload={
                    "companion_snapshot_ref": (
                        "growth-snapshot-authority-1"
                    ),
                    "owner_key": state.owner_key,
                    "selected_instruction_refs": [],
                    "skill_invocation_scopes": [],
                    "active_skill_scope_ids": [],
                    "personal_workflow_selection": None,
                },
            )

    finalizer = Finalizer()
    prepared = PreparedTurnContext(
        turn=TurnInput("placeholder", "text-session"),
        messages=[
            {"role": "user", "content": "use the frozen catalog"}
        ],
        bundle=SimpleNamespace(tool_exposure_intent=exposure),
        assembler=None,
        prepared_context=SimpleNamespace(tool_set=tool_set),
        eligibility=eligibility,
        companion_authority_state=authority_state,
        companion_turn_finalizer=finalizer,
    )

    class PreparedFixture:
        async def prepare_context(self, turn, **_kwargs):
            return replace(prepared, turn=turn)

        async def route_intent(self, value, *, services):
            del services
            return RoutedTurnIntent(prepared=value)

        async def plan_decision(self, routed, **_kwargs):
            return PlannedTurnDecision(routed=routed)

        @staticmethod
        def prepare_workflow_request_payload(_routed, turn, _config):
            return {
                "loop": {
                    "user_request": turn.text,
                    "is_sentinel_run": False,
                }
            }

    class Snapshots:
        async def put_context_os(self, _ref, _payload):
            return None

        async def put_exposure_intent(self, _ref, _payload):
            return None

    adapter._preparer = PreparedFixture()
    result = await _execute_adapter(
        adapter,
        TurnInput(
            text="use the frozen catalog",
            session_id="text-session",
            request_id="request-authority",
            turn_id="turn-authority",
            venue="text",
        ),
        _host("text-session"),
        services={
            "capability_platform": platform,
            "capability_refresh_snapshots": Snapshots(),
        },
        config=_config(),
        local_llm=object(),
        tool_registry=registry,
        provider=object(),
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
        presentation_context=context,
        domain_sink=sink,
    )

    assert result.status == "succeeded"
    assert len(finalizer.calls) == 1
    captured = finalizer.calls[0][1]
    assert captured.run_catalog_content_stamp
    assert captured.process_catalog_stamp
    assert captured.catalog_snapshot_ref
    assert captured.capability_lease_intent_ref
    assert captured.exact_tools[0]["name"] == "fixture_read"
    assert captured.lease_entries
    assert "canonical_envelope" in captured.lease_entries[0]
    start = driver.starts[-1]
    assert start.capability_snapshot["product_snapshot_ref"] == (
        "growth-snapshot-authority-1"
    )
    assert start.capability_snapshot["host_extensions"][
        "deskpet.companion.selection.v1"
    ]["owner_key"] == "companion:alice:4"
    assert start.run_context.owner_key == "companion:alice:4"
    assert start.run_context.profile_generation == 4
    assert start.run_context.binding_epoch == 7
@pytest.mark.asyncio
async def test_legacy_plan_gate_cannot_create_a_product_mode(tmp_path, monkeypatch) -> None:
    stack = await _stack(tmp_path)
    adapter, kernel, driver, _uow, _db, ws, _vector, context, _peers, sink, _order = stack
    config = _config()
    config.features.plan_confirm_gate = True
    provider = SimpleNamespace(provider_id="fixture", adapter_id="fixture-adapter", adapter_version="v1")

    async def extract_plan(*_args, **_kwargs):
        return SimpleNamespace(rationale="durable gate", steps=(SimpleNamespace(title="Inspect", detail="Then run"),))

    monkeypatch.setattr(
        "deskpet.agent.turn_preparer.maybe_extract_general_plan",
        extract_plan,
    )
    session = await adapter.open(
        TurnInput(context.text, context.session_id, "request-plan", "turn-plan", "text"),
        _host(context.session_id),
        services={}, config=config, local_llm=provider, tool_registry=object(), provider=provider,
        current_message_id=None, summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {}, presentation_context=context,
        domain_sink=sink,
    )
    assert isinstance(session, ProductVenueRunSession)
    events = session.events
    async for _event in events:
        pass
    await session.close()
    assert len(driver.starts) == 1
    assert [frame["type"] for frame in ws.frames] == [
        "chat_v2_delta",
        "chat_v2_final",
    ]
    assert sink.plans == []
    assert "code_mode" not in driver.starts[0].request_payload
    assert kernel._live.values() == ()


@pytest.mark.asyncio
async def test_legacy_plan_decision_cannot_install_a_pre_run_admission(
    tmp_path, monkeypatch
) -> None:
    stack = await _stack(tmp_path)
    adapter, kernel, _driver, _uow, _db, _ws, _vector, context, _peers, sink, _order = stack

    async def planned(routed, **_kwargs):
        common = {
            "session_id": context.session_id,
            "target_directory": None,
            "action_categories": ("filesystem_write", "shell"),
        }
        return PlannedTurnDecision(
            routed=routed,
            commands=(
                ProductDomainCommand(
                    "plan_proposed",
                    MappingProxyType(
                        {
                            **common,
                            "rationale": "write then verify",
                            "steps": (
                                MappingProxyType(
                                    {"title": "Write", "detail": "Create a file"}
                                ),
                            ),
                            "awaiting_confirm": True,
                            "auto_confirmed": False,
                        }
                    ),
                ),
                ProductDomainCommand(
                    "plan_confirmation",
                    MappingProxyType(
                        {
                            **common,
                            "text": context.text,
                            "timeout_seconds": 900.0,
                            "read_only": False,
                        }
                    ),
                ),
            ),
        )

    monkeypatch.setattr(adapter._preparer, "plan_decision", planned)
    session = await adapter.open(
        TurnInput(
            context.text,
            context.session_id,
            "request-json-admission",
            "turn-json-admission",
            "text",
        ),
        _host(context.session_id),
        services={},
        config=_config(),
        local_llm=object(),
        tool_registry=object(),
        provider=object(),
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
        presentation_context=context,
        domain_sink=sink,
        provider_launch_snapshot=ProviderLaunchSnapshot(
            "fixture", "fixture-adapter", "v1", False, None
        ),
    )

    assert isinstance(session, ProductVenueRunSession)
    assert sink.plans == []
    async for _event in session.events:
        pass
    await session.close()
    assert kernel._live.values() == ()


@pytest.mark.asyncio
async def test_voice_transport_consumes_the_activated_product_session(tmp_path) -> None:
    stack = await _stack(tmp_path)
    adapter, kernel, driver, uow, session_db, _ws, _vector, context, _peers, sink, order = stack
    context.session_id = "voice-session"
    context.text = "explain the harness by voice"
    context.request_id = "request-voice"

    session = await adapter.open(
        TurnInput(
            text=context.text,
            session_id=context.session_id,
            request_id=context.request_id,
            turn_id="turn-voice",
            venue="voice",
        ),
        _host(context.session_id),
        services={"problem_pipeline": _Pipeline()},
        config=_config(),
        local_llm=SimpleNamespace(model="fixture", base_url="local"),
        tool_registry=object(),
        provider=object(),
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
        presentation_context=context,
        domain_sink=sink,
    )
    assert isinstance(session, ProductVenueRunSession)

    class _ASR:
        async def transcribe(self, _audio: bytes) -> str:
            return context.text

    class _VAD:
        threshold = 0.5

        def set_threshold(self, value: float) -> None:
            self.threshold = value

    class _TTS:
        def __init__(self) -> None:
            self.texts: list[str] = []

        async def synthesize_pcm_stream(self, text: str):
            self.texts.append(text)
            yield b"\x00\x00\x00\x00"

    class _ForbiddenAgent:
        async def chat_stream(self, *_args: object, **_kwargs: object):
            raise AssertionError("shared product session must own agent execution")
            yield ""

    audio, control, tts = _WS(), _WS(), _TTS()
    voice = VoicePipeline(
        vad=_VAD(),
        asr=_ASR(),
        agent=_ForbiddenAgent(),
        tts=tts,
        control_ws=control,
        session_id=context.session_id,
        run_session=session,
    )

    result = await voice._process_utterance(b"pcm", audio)

    assert result == "hello world"
    assert tts.texts == ["hello world"]
    assert order == ["driver"]
    assert [dict(item) for item in driver.starts[0].canonical_messages] == [
        {"role": "user", "content": context.text},
    ]
    assert await uow.get_execution_owner(session.run_id) == ("kernel", 1)
    assert (await uow.get_runtime_state()).generation == 1
    assert kernel._live.values() == ()
    assert voice._current_run_handle is None
    assert any(frame["type"] == "chat_v2_final" for frame in context.websocket.frames)
    assert any(frame["type"] == "transcript" for frame in audio.frames)
    assert audio.binary_frames == [b"\x01\x00\x00\x00\x00"]
    messages = await session_db.get_messages(context.session_id)
    assert messages[-1]["content"] == "hello world"


@pytest.mark.asyncio
async def test_product_session_is_single_consumer_and_close_finishes_once(tmp_path) -> None:
    stack = await _stack(tmp_path)
    adapter, kernel, _driver, _uow, _db, _ws, _vector, context, _peers, sink, _order = stack

    class _Billing:
        def __init__(self) -> None:
            self.records: list[dict[str, Any]] = []

        async def record(self, **record: Any) -> None:
            self.records.append(record)

    billing = _Billing()
    provider = SimpleNamespace(
        model="fixture",
        base_url="http://127.0.0.1:9999",
        last_usage={"prompt_tokens": 2, "completion_tokens": 3},
    )
    context.provider = provider
    context.billing_ledger = billing
    session = await adapter.open(
        TurnInput(
            text=context.text,
            session_id=context.session_id,
            request_id="request-close-once",
            turn_id="turn-close-once",
            venue="text",
        ),
        _host(context.session_id),
        services={},
        config=_config(),
        local_llm=provider,
        tool_registry=object(),
        provider=provider,
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
        presentation_context=context,
        domain_sink=sink,
    )
    assert isinstance(session, ProductVenueRunSession)
    events = session.events
    with pytest.raises(RuntimeError, match="single-consumer"):
        _ = session.events
    async for _event in events:
        pass

    await session.close()
    await session.close()

    assert session.result.final_text == "hello world"
    assert len(billing.records) == 1
    assert provider.last_usage is None
    assert kernel._live.values() == ()


@pytest.mark.asyncio
async def test_canonical_event_adapter_redacts_tools_and_preserves_failure_status(tmp_path) -> None:
    session_db = SessionDB(tmp_path / "projection.db")
    await session_db.initialize()
    ws, vector = _WS(), _Vector()
    context, _peers = _presentation_context(ws, session_db, vector)
    presenter = build_product_run_presenter()
    adapter = CanonicalRunEventPresentationAdapter()
    state = PresentationState()

    requested = RunEvent(
        event_id="event-request",
        run_id="run-tool",
        root_run_id="run-tool",
        session_id="text-session",
        durable_seq=1,
        candidate=RunEventCandidate(
            event_key="tool-request",
            kind="tool_requested",
            status=OutcomeStatus.WAITING,
            driver_kind="react",
            correlation={"command_id": "command-1"},
            payload={
                "calls": [
                    {
                        "id": "call-1",
                        "name": "web_search",
                        "arguments": {"query": "private query"},
                    }
                ]
            },
        ),
        created_at=1.0,
    )
    failed = RunEvent(
        event_id="event-failed",
        run_id="run-tool",
        root_run_id="run-tool",
        session_id="text-session",
        durable_seq=2,
        candidate=RunEventCandidate(
            event_key="tool-failed",
            kind="tool.outcome",
            status=OutcomeStatus.FAILED,
            driver_kind="react",
            correlation={"call_id": "call-1"},
            payload={
                "tool_name": "web_search",
                "outcome": {
                    "state": "failure",
                    "value": {"results": [{"url": "https://secret.example"}]},
                    "error": {"code": "upstream", "message": "failed"},
                },
            },
            error={"code": "upstream", "message": "failed"},
        ),
        created_at=2.0,
    )

    await presenter.present_run_event(requested, adapter, context, state)
    await presenter.present_run_event(failed, adapter, context, state)

    tool_call = next(frame for frame in ws.frames if frame["type"] == "tool_call")
    tool_result = next(frame for frame in ws.frames if frame["type"] == "tool_result")
    assert tool_call["payload"]["arguments"] == {"search_scope": "web"}
    assert "private query" not in str(ws.frames)
    assert tool_result["payload"]["ok"] is False
    assert tool_result["payload"]["status"] == "failed"
    assert tool_result["payload"]["result"] == '{"result_kind": "web_search", "status": "completed", "item_count": 0}'
    assert "secret.example" not in str(ws.frames)


@pytest.mark.parametrize(
    ("kind", "expected_type"),
    (("workflow.progress", "workflow_event"), ("workflow.final", "workflow_final")),
)
def test_canonical_workflow_events_keep_product_frame_contract(
    kind: str, expected_type: str
) -> None:
    event = RunEvent(
        event_id=f"event-{expected_type}",
        run_id="run-workflow",
        root_run_id="run-workflow",
        session_id="text-session",
        durable_seq=7,
        candidate=RunEventCandidate(
            event_key=f"key-{expected_type}",
            kind=kind,
            status=(
                OutcomeStatus.SUCCEEDED
                if kind == "workflow.final"
                else OutcomeStatus.WAITING
            ),
            driver_kind="workflow",
            correlation={
                "request_id": "request-workflow",
                "turn_id": "turn-workflow",
                "workflow_name": "deep_research",
                "workflow_version": "v7",
            },
            payload={"kind": "final" if kind == "workflow.final" else "progress"},
        ),
        created_at=7.0,
    )

    projected = CanonicalRunEventPresentationAdapter().to_presentation_events(event)

    assert len(projected) == 1
    frame = projected[0]
    assert frame.type == expected_type
    assert frame.payload["event_id"] == event.event_id
    assert frame.payload["event_type"] == kind
    assert frame.payload["request_id"] == "request-workflow"
    assert frame.payload["turn_id"] == "turn-workflow"


def test_canonical_context_compaction_restores_typed_event() -> None:
    event = RunEvent(
        event_id="event-context-compaction",
        run_id="run-context-compaction",
        root_run_id="run-context-compaction",
        session_id="text-session",
        durable_seq=None,
        live_cursor=LiveCursor("stream-context-compaction", 1),
        candidate=RunEventCandidate(
            event_key="context-compaction:source-1",
            kind="context_compacted",
            status=OutcomeStatus.SUCCEEDED,
            driver_kind="react",
            correlation={
                "iteration": 3,
                "source_event_id": "source-1",
            },
            payload={
                "reduction": 0.75,
                "tokens_in": 8000,
                "tokens_out": 2000,
                "model": "kimi-k3",
                "source_event_id": "source-1",
                "based_on_sample_id": "sample-before",
                "occurred_at": 7.5,
            },
        ),
        created_at=8.0,
    )

    projected = CanonicalRunEventPresentationAdapter().to_presentation_events(event)

    assert len(projected) == 1
    compacted = projected[0]
    assert type(compacted).__name__ == "ContextCompactedEvent"
    assert compacted.iteration == 3
    assert compacted.tokens_in == 8000
    assert compacted.tokens_out == 2000
    assert compacted.model == "kimi-k3"
    assert compacted.source_event_id == "source-1"
    assert compacted.based_on_sample_id == "sample-before"
    assert compacted.occurred_at == 7.5


def test_canonical_external_wait_projects_complete_durable_fence() -> None:
    event = RunEvent(
        event_id="event-external-wait",
        run_id="run-external-wait",
        root_run_id="run-external-wait",
        session_id="text-session",
        durable_seq=3,
        candidate=RunEventCandidate(
            event_key="decision:external",
            kind="decision",
            status=OutcomeStatus.WAITING,
            driver_kind="react",
            correlation={
                "command_id": "command-external",
                "decision_id": "decision-external",
            },
            payload={
                "run_id": "run-external-wait",
                "command_id": "command-external",
                "request_id": "decision-external",
                "decision_id": "decision-external",
                "nonce": "nonce-external",
                "version": 0,
                "kind": "workflow_hitl",
                "prompt": {
                    "title": "请完成 Windows 系统确认",
                    "required_action": "在 UAC 窗口中点击允许。",
                    "wait_kind": "uac",
                    "wait_ref": "external-wait:1",
                    "evidence_refs": ["evidence:uac-visible"],
                },
                "domain_kind": "external",
                "domain_id": "windows_uac",
                "call_id": "call-external",
                "effect_id": "effect-external",
                "tool_name": "external_action_wait",
            },
        ),
        created_at=3.0,
    )

    projected = CanonicalRunEventPresentationAdapter().to_presentation_events(
        event
    )

    assert len(projected) == 1
    frame = projected[0]
    assert frame.type == "external_wait_request"
    assert frame.payload["run_id"] == "run-external-wait"
    assert frame.payload["decision_id"] == "decision-external"
    assert frame.payload["nonce"] == "nonce-external"
    assert frame.payload["version"] == 0
    assert frame.payload["wait_kind"] == "uac"
    assert frame.payload["wait_ref"] == "external-wait:1"
    assert frame.payload["required_action"] == "在 UAC 窗口中点击允许。"


def test_canonical_project_directory_wait_projects_specialized_card() -> None:
    event = RunEvent(
        event_id="event-project-directory",
        run_id="run-project-directory",
        root_run_id="run-project-directory",
        session_id="text-session",
        durable_seq=4,
        candidate=RunEventCandidate(
            event_key="decision:project-directory",
            kind="decision",
            status=OutcomeStatus.WAITING,
            driver_kind="react",
            correlation={"decision_id": "decision-project-directory"},
            payload={
                "run_id": "run-project-directory",
                "request_id": "decision-project-directory",
                "decision_id": "decision-project-directory",
                "nonce": "nonce-project-directory",
                "version": 0,
                "kind": "workflow_hitl",
                "prompt": {
                    "title": "选择项目保存位置",
                    "required_action": "请选择项目保存到哪个文件夹下面",
                    "wait_ref": "external-wait:project",
                    "project_name": "末日生存 Demo",
                    "folder_name": "apocalypse-demo",
                    "project_kind": "Godot 游戏",
                    "directory_mode": "use_existing",
                },
                "domain_kind": "external",
                "domain_id": "project_directory",
                "call_id": "call-project-directory",
                "effect_id": "effect-project-directory",
                "tool_name": "project_directory_select",
            },
        ),
        created_at=4.0,
    )

    projected = CanonicalRunEventPresentationAdapter().to_presentation_events(
        event
    )

    assert len(projected) == 1
    frame = projected[0]
    assert frame.type == "project_directory_request"
    assert frame.payload["directory_mode"] == "use_existing"
    assert frame.payload["project_name"] == "末日生存 Demo"
    assert frame.payload["folder_name"] == "apocalypse-demo"
    assert frame.payload["project_kind"] == "Godot 游戏"
