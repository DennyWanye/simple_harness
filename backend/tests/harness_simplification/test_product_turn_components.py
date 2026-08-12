# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import asyncio
import ast
import json
from dataclasses import fields
from pathlib import Path
from types import MappingProxyType
from types import SimpleNamespace
from typing import Any

import pytest

from agent.agent_loop import (
    AssistantDeltaEvent,
    AsyncHandoffEvent,
    ContextCompactedEvent,
    ErrorEvent,
    FinalEvent,
    PipelineEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from deskpet.agent.run_presenter import (
    PresentationState,
    RunPresentationContext,
    build_product_run_presenter,
)
from deskpet.agent.product_domain_sink import LegacyProductDomainSink
from deskpet.agent.turn_preparer import (
    PreparedTurnContext,
    ProductDomainCommand,
    ProductTurnPreparer,
    RoutedTurnIntent,
    TurnInput,
)
from deskpet.agent.assembler.bundle import ContextBundle
from deskpet.tools.capabilities import ToolExposureIntent
from llm.types import ToolCall


ROOT = Path(__file__).resolve().parents[3]


class _WS:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def send_json(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)


class _SessionDB:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def append_message(self, **message: Any) -> int:
        self.messages.append(message)
        return len(self.messages)

    async def upsert_session_plan(
        self,
        *values: Any,
        **metadata: Any,
    ) -> None:
        self.plan = (values, metadata)

    async def clear_session_plan_awaiting(self, session_id: str) -> None:
        self.cleared = session_id


class _Tools:
    def __init__(self) -> None:
        self.read_only: list[tuple[str, bool]] = []

    def set_plan_read_only(self, sid: str, enabled: bool) -> None:
        self.read_only.append((sid, enabled))


class _Activity:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def set_status(self, sid: str, status: str) -> None:
        self.calls.append(("status", sid, status))

    async def bump(self, sid: str, **payload: Any) -> None:
        self.calls.append(("bump", sid, payload))

    async def mark_error_pending(self, sid: str) -> None:
        self.calls.append(("error", sid))


def test_profile_catalog_requires_durable_workflow_for_multifile_projects() -> None:
    descriptor = SimpleNamespace(compact=lambda: {"key": "workflow.durable_task"})
    profiles = SimpleNamespace(
        generation=7,
        model_spawnable={"workflow.durable_task": descriptor},
    )
    messages = [{"role": "user", "content": "build a game demo"}]

    ProductTurnPreparer(profiles)._inject_profile_catalog(messages)

    catalog = next(
        item for item in messages if item.get("_is_execution_profile_catalog")
    )
    assert "MUST use workflow.durable_task before the first effectful" in catalog[
        "content"
    ]
    assert "possibly misspelled" in catalog["content"]


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


class _AuthorizationPolicyStore:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    async def get_policy_state(self) -> SimpleNamespace:
        return SimpleNamespace(mode=self.mode)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_commands", "auto_confirmed"),
    (
        ("manual", ("plan_proposed", "plan_confirmation"), False),
        ("auto", ("plan_proposed",), True),
    ),
)
async def test_plan_decision_uses_model_categories_and_policy_mode(
    monkeypatch,
    mode,
    expected_commands,
    auto_confirmed,
) -> None:
    captured: dict[str, Any] = {}

    async def extract_plan(*_args, **_kwargs):
        captured.update(_kwargs)
        return SimpleNamespace(
            rationale="create and launch",
            steps=(
                SimpleNamespace(
                    title="Create",
                    detail="Write project files",
                    action_categories=(
                        "filesystem_write",
                        "application",
                    ),
                ),
            ),
        )

    monkeypatch.setattr(
        "deskpet.agent.turn_preparer.maybe_extract_general_plan",
        extract_plan,
    )
    turn = TurnInput(
        text="Create a runnable project and launch it for verification.",
        session_id="session-policy",
        workspace_ref="F:/workspace/project",
    )
    routed = RoutedTurnIntent(
        prepared=PreparedTurnContext(turn, [], None, None),
        problem_type="creation",
        requires_action_plan=True,
    )

    result = await ProductTurnPreparer().plan_decision(
        routed,
        services={
            "capability_store": _AuthorizationPolicyStore(mode),
        },
        config=_config(),
        provider=object(),
    )

    assert tuple(item.kind for item in result.commands) == expected_commands
    proposed = result.commands[0].payload
    assert proposed["target_directory"] == "F:/workspace/project"
    assert proposed["action_categories"] == [
        "application",
        "filesystem_write",
    ]
    assert proposed["awaiting_confirm"] is (mode == "manual")
    assert proposed["auto_confirmed"] is auto_confirmed
    assert captured["authorization_required"] is (mode == "manual")
    assert captured["planning_enabled"] is (mode == "manual")


@pytest.mark.asyncio
async def test_manual_fact_statement_does_not_create_plan_admission(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    async def extract_plan(*_args, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(
        "deskpet.agent.turn_preparer.maybe_extract_general_plan",
        extract_plan,
    )
    turn = TurnInput(
        text="今天完成了成长闭环自动化测试。",
        session_id="session-fact",
    )
    routed = RoutedTurnIntent(
        prepared=PreparedTurnContext(turn, [], None, None),
        problem_type="factual_qa",
        requires_action_plan=False,
    )

    result = await ProductTurnPreparer().plan_decision(
        routed,
        services={
            "capability_store": _AuthorizationPolicyStore("manual"),
        },
        config=_config(),
        provider=object(),
    )

    assert result.plan is None
    assert result.commands == ()
    assert captured["authorization_required"] is False
    assert captured["planning_enabled"] is False


def _domain_sink(
    *,
    ws: _WS,
    db: _SessionDB,
    waiters: dict[str, Any],
    tools: _Tools,
    activity: _Activity | None = None,
) -> tuple[LegacyProductDomainSink, list[dict[str, Any]]]:
    peers: list[dict[str, Any]] = []

    async def broadcast(_origin: Any, frame: dict[str, Any]) -> None:
        peers.append(frame)

    return (
        LegacyProductDomainSink(
            websocket=ws,
            services={"session_activity": activity} if activity else {},
            session_db=db,
            broadcast=broadcast,
            plan_waiters=waiters,
            tool_registry=tools,
        ),
        peers,
    )


def _plan_command(timeout: float = 1.0) -> ProductDomainCommand:
    return ProductDomainCommand(
        "plan_confirmation",
        {
            "session_id": "session-r2",
            "text": "do it",
            "timeout_seconds": timeout,
            "read_only": True,
            "in_code_mode": True,
        },
    )


@pytest.mark.asyncio
async def test_store_plan_normalizes_immutable_product_payloads() -> None:
    ws, db, waiters, tools = _WS(), _SessionDB(), {}, _Tools()
    sink, _peers = _domain_sink(
        ws=ws,
        db=db,
        waiters=waiters,
        tools=tools,
    )
    await sink.store_plan(
        MappingProxyType(
            {
                "session_id": "session-r2",
                "rationale": "write and verify",
                "steps": (
                    MappingProxyType(
                        {
                            "title": "Write",
                            "detail": "Create the requested file",
                        }
                    ),
                ),
                "target_directory": "F:/workspace/project",
                "action_categories": ("filesystem_write", "shell"),
                "awaiting_confirm": True,
                "auto_confirmed": False,
            }
        )
    )

    values, metadata = db.plan
    assert values == (
        "session-r2",
        "write and verify",
        [{"title": "Write", "detail": "Create the requested file"}],
        True,
    )
    assert metadata == {
        "target_directory": "F:/workspace/project",
        "action_categories": ["filesystem_write", "shell"],
        "auto_confirmed": False,
    }


@pytest.mark.asyncio
async def test_non_waiting_plan_clears_stale_manual_sidecar() -> None:
    ws, db, waiters, tools = _WS(), _SessionDB(), {}, _Tools()
    sink, _peers = _domain_sink(
        ws=ws,
        db=db,
        waiters=waiters,
        tools=tools,
    )

    await sink.store_plan(
        {
            "session_id": "session-r2",
            "rationale": "auto mode plan",
            "steps": [],
            "awaiting_confirm": False,
            "auto_confirmed": True,
        }
    )

    assert db.cleared == "session-r2"
    assert not hasattr(db, "plan")


def _run_context(
    ws: _WS,
    db: Any,
    *,
    services: dict[str, Any] | None = None,
    activity: Any = None,
    vector: Any = None,
    assembler: Any = None,
    bundle: Any = None,
    ledger: Any = None,
    provider: Any = None,
) -> tuple[RunPresentationContext, dict[str, Any]]:
    seen: dict[str, Any] = {"peers": [], "usage": []}

    async def broadcast(_origin: Any, frame: dict[str, Any]) -> None:
        seen["peers"].append(frame)

    async def send_final(target: _WS, frame: dict[str, Any], **_kwargs: Any) -> None:
        await target.send_json(frame)

    async def emit_usage(_ws: Any, sid: str, **_kwargs: Any) -> None:
        seen["usage"].append(sid)

    return RunPresentationContext(
        session_id="session-r2",
        text="use a tool",
        websocket=ws,
        services=services or {},
        config=_config(),
        messages=[],
        session_db=db,
        vector_worker=vector,
        activity_store=activity,
        provider_chain=None,
        fallback_provider=None,
        request_id="request-r2",
        max_iterations=16,
        is_sentinel=False,
        broadcast=broadcast,
        send_final=send_final,
        emit_context_usage=emit_usage,
        intent_label_from_turn=lambda used: "task" if used else "ask",
        assembler=assembler,
        bundle=bundle,
        billing_ledger=ledger,
        provider=provider,
    ), seen


@pytest.mark.asyncio
async def test_turn_input_survives_prepare_context_without_field_loss() -> None:
    turn = TurnInput(
        text="inspect",
        session_id="session-r2",
        request_id="request-r2",
        turn_id="turn-r2",
        venue="text",
        memory_policy={"status": "disabled"},
        explicit_new=True,
        provider_ref="provider-r2",
        capability_ref="capability-r2",
        workspace_ref="workspace-r2",
    )
    result = await ProductTurnPreparer().prepare_context(
        turn,
        services={},
        config=_config(),
        local_llm=SimpleNamespace(model="model-r2", base_url="local"),
        tool_registry=object(),
        current_message_id=3,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
    )
    assert result.turn is turn
    assert result.messages == [{"role": "user", "content": "inspect"}]
    assert [item.name for item in fields(TurnInput)] == [
        "text",
        "session_id",
            "request_id",
            "turn_id",
            "venue",
            "memory_policy",
        "explicit_new",
        "attachment_blocks",
        "provider_ref",
        "capability_ref",
        "workspace_ref",
        "root_run_id",
        "task_scope_id",
        "target_root_run_id",
        "active_execution_budget_seconds",
        "context_usage_basis_sample_id",
    ]


@pytest.mark.asyncio
async def test_prepare_context_requires_projection_gate_before_assembly() -> None:
    calls: list[str] = []

    class _Gate:
        async def ensure_current(self, session_id: str) -> None:
            calls.append(f"gate:{session_id}")
            raise RuntimeError("projection unavailable")

    class _Assembler:
        enabled = True

        async def assemble(self, **_kwargs: Any) -> ContextBundle:
            calls.append("assemble")
            return ContextBundle(task_type="chat")

    with pytest.raises(RuntimeError, match="projection unavailable"):
        await ProductTurnPreparer().prepare_context(
            TurnInput(text="inspect", session_id="session-r2"),
            services={
                "session_terminal_projection_gate": _Gate(),
                "context_assembler": _Assembler(),
            },
            config=_config(),
            local_llm=SimpleNamespace(model="model-r2", base_url="local"),
            tool_registry=object(),
            current_message_id=None,
            summary_user_is_confused=lambda _text: False,
            summary_latest_task_snapshot=lambda _entries: None,
            summary_build_reinject_msg=lambda _value: {},
        )

    assert calls == ["gate:session-r2"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "请用一句话说明 Harness/Kernel 负责什么。",
        "你有哪些能力，能帮我做 Godot 游戏吗？",
        "帮我读取今天的项目笔记",
    ],
)
async def test_prepare_context_keeps_model_visible_tools_for_every_turn(
    text: str,
) -> None:
    bundle = ContextBundle(
        task_type="chat",
        tool_schemas=[{"name": "memory_read"}],
        tool_exposure_intent=ToolExposureIntent(
            direct_selectors=("memory_read",),
        ),
    )

    class _Assembler:
        enabled = True

        async def assemble(self, **_kwargs: Any) -> ContextBundle:
            return bundle

    class _Registry:
        @staticmethod
        def has(name: str) -> bool:
            return name == "capability_search"

    await ProductTurnPreparer().prepare_context(
        TurnInput(text=text, session_id="session-r2"),
        services={"context_assembler": _Assembler()},
        config=_config(),
        local_llm=SimpleNamespace(model="model-r2", base_url="local"),
        tool_registry=_Registry(),
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
    )

    assert bundle.tool_schemas == [{"name": "memory_read"}]
    assert bundle.tool_exposure_intent.direct_selectors == ("memory_read",)
    assert bundle.tool_exposure_intent.required_direct_names == (
        "capability_search",
    )
    assert bundle.tool_exposure_intent.deny_selectors == (
        "deepresearch",
        "ppt_create",
        "ppt_pro",
    )


@pytest.mark.asyncio
async def test_prepare_context_preserves_existing_denies_and_hides_route_owned_tools() -> None:
    bundle = ContextBundle(
        task_type="chat",
        tool_exposure_intent=ToolExposureIntent(
            direct_selectors=("*",),
            discoverable_selectors=("*",),
            deny_selectors=("write_file", "ppt_pro"),
        ),
    )

    class _Assembler:
        enabled = True

        async def assemble(self, **_kwargs: Any) -> ContextBundle:
            return bundle

    class _Registry:
        @staticmethod
        def has(name: str) -> bool:
            return name in {"capability_search", "workflow_spawn"}

    await ProductTurnPreparer().prepare_context(
        TurnInput(text="生成一份复杂 PPT", session_id="session-ppt"),
        services={"context_assembler": _Assembler()},
        config=_config(),
        local_llm=SimpleNamespace(model="model-r2", base_url="local"),
        tool_registry=_Registry(),
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
    )

    assert bundle.tool_exposure_intent.deny_selectors == (
        "write_file",
        "ppt_pro",
        "deepresearch",
        "ppt_create",
    )
    assert bundle.tool_exposure_intent.required_direct_names == (
        "capability_search",
        "workflow_spawn",
    )


@pytest.mark.asyncio
async def test_prepare_context_keeps_process_start_direct_for_gui_launches() -> None:
    bundle = ContextBundle(
        task_type="chat",
        tool_exposure_intent=ToolExposureIntent(
            discoverable_selectors=("source:builtin",),
        ),
    )

    class _Assembler:
        enabled = True

        async def assemble(self, **_kwargs: Any) -> ContextBundle:
            return bundle

    class _Registry:
        @staticmethod
        def has(name: str) -> bool:
            return name in {
                "capability_search",
                "workflow_spawn",
                "process_start",
            }

    await ProductTurnPreparer().prepare_context(
        TurnInput(
            text="启动 Godot 并操作游戏",
            session_id="session-gui",
        ),
        services={"context_assembler": _Assembler()},
        config=_config(),
        local_llm=SimpleNamespace(model="model-r2", base_url="local"),
        tool_registry=_Registry(),
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
    )

    assert bundle.tool_exposure_intent.required_direct_names == (
        "capability_search",
        "workflow_spawn",
        "process_start",
    )


@pytest.mark.asyncio
async def test_prepare_context_hides_process_start_from_word_creation_turn() -> None:
    bundle = ContextBundle(
        task_type="chat",
        tool_exposure_intent=ToolExposureIntent(
            discoverable_selectors=("source:builtin",),
        ),
    )

    class _Assembler:
        enabled = True

        async def assemble(self, **_kwargs: Any) -> ContextBundle:
            return bundle

    class _Registry:
        @staticmethod
        def has(name: str) -> bool:
            return name in {
                "capability_search",
                "workflow_spawn",
                "process_start",
            }

    await ProductTurnPreparer().prepare_context(
        TurnInput(
            text="请生成一份 Word 验收报告并保存到桌面",
            session_id="session-word",
        ),
        services={"context_assembler": _Assembler()},
        config=_config(),
        local_llm=SimpleNamespace(model="model-r2", base_url="local"),
        tool_registry=_Registry(),
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
    )

    assert bundle.tool_exposure_intent.required_direct_names == (
        "capability_search",
        "workflow_spawn",
    )


@pytest.mark.parametrize(
    "text",
    [
        "你能启动哪些应用软件？只介绍能力，不要实际操作",
        "不要启动服务器，只告诉我命令",
        "如何运行 Godot 游戏？",
        "刚才启动服务器失败的原因是什么？",
        "Can you open browser apps? Just describe capabilities",
        "Do not start the server",
        "Explain the open source server architecture",
        "How do I start a game server?",
        "Show the server runtime status",
        "The server is running normally",
        "What browser apps can you open?",
        "Run `echo server`",
        "运行 echo 服务器",
    ],
)
def test_process_start_intent_rejects_non_execution_requests(text: str) -> None:
    from deskpet.agent.turn_preparer import _needs_process_start_direct

    assert _needs_process_start_direct(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "启动 Godot 并操作游戏",
        "不要解释，直接启动服务器",
        "Can you open the browser and navigate to the dashboard?",
        "Could you launch the game now?",
        "Do not explain; start the server now.",
        "Don't just describe it; open the browser now.",
        "Earlier failures aside, start the game server now.",
    ],
)
def test_process_start_intent_accepts_explicit_execution_requests(text: str) -> None:
    from deskpet.agent.turn_preparer import _needs_process_start_direct

    assert _needs_process_start_direct(text) is True


@pytest.mark.asyncio
async def test_prepare_context_promotes_active_skill_allowed_tools_to_direct() -> None:
    scope = {
        "scope_id": "scope-summarize",
        "allowed_tools": ["memory_recall"],
    }
    bundle = ContextBundle(
        task_type="chat",
        tool_exposure_intent=ToolExposureIntent(),
    )
    bundle.decisions = SimpleNamespace(
        timestamp=None,
        session_id=None,
        components={
            "skill": SimpleNamespace(
                included=True,
                meta={
                    "skill_invocation_scopes": [scope],
                    "active_skill_scope_ids": ["scope-summarize"],
                },
            )
        },
    )

    class _Assembler:
        enabled = True

        async def assemble(self, **_kwargs: Any) -> ContextBundle:
            return bundle

    class _Registry:
        @staticmethod
        def has(name: str) -> bool:
            return name == "capability_search"

    await ProductTurnPreparer().prepare_context(
        TurnInput(text="summarize today", session_id="session-skill"),
        services={"context_assembler": _Assembler()},
        config=_config(),
        local_llm=SimpleNamespace(model="model-r2", base_url="local"),
        tool_registry=_Registry(),
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
    )

    assert bundle.tool_exposure_intent.required_direct_names == (
        "capability_search",
        "memory_recall",
    )


@pytest.mark.asyncio
async def test_route_intent_clarification_preserves_legacy_order() -> None:
    class _Pipeline:
        enabled = True

        async def run_pre_loop(
            self, text: str, *, prior_task_type=None, workload_context=None
        ):
            assert text == "ambiguous"
            return SimpleNamespace(
                events=[{"type": "chat_v2_intent", "payload": {"kind": "ask"}}],
                short_circuit=False,
                needs_clarification=True,
                intent=SimpleNamespace(clarifying_questions=["which target?"]),
                system_injections=[],
                contradiction=None,
            )

    ws = _WS()
    peers: list[dict[str, Any]] = []
    session_db = _SessionDB()

    async def broadcast(_origin: Any, frame: dict[str, Any]) -> None:
        peers.append(frame)

    prepared = await ProductTurnPreparer().prepare_context(
        TurnInput(
            text="ambiguous",
            session_id="session-r2",
            request_id="request-ambiguous",
            turn_id="turn-ambiguous",
            root_run_id="root-ambiguous",
        ),
        services={},
        config=_config(),
        local_llm=object(),
        tool_registry=object(),
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
    )
    routed = await ProductTurnPreparer().route_intent(
        prepared, services={"problem_pipeline": _Pipeline()}
    )
    sink = LegacyProductDomainSink(
        websocket=ws,
        services={},
        session_db=session_db,
        broadcast=broadcast,
        plan_waiters={},
        tool_registry=object(),
    )
    presenter = build_product_run_presenter()
    outcomes = [await presenter.present_domain(command, sink) for command in routed.commands]
    assert routed.continue_turn is False
    assert outcomes == [True, False]
    assert [frame["type"] for frame in ws.frames] == [
        "chat_v2_intent",
        "chat_v2_final",
    ]
    assert peers == ws.frames
    assert session_db.messages[0]["content"] == "which target?"


@pytest.mark.asyncio
@pytest.mark.parametrize("short_circuit", [False, True])
async def test_route_intent_active_skill_scope_beats_text_only_early_exit(
    short_circuit: bool,
) -> None:
    class _Pipeline:
        enabled = True

        async def run_pre_loop(
            self, text: str, *, prior_task_type=None, workload_context=None
        ):
            assert text == "summarize today"
            return SimpleNamespace(
                events=[{"type": "chat_v2_intent", "payload": {"kind": "ask"}}],
                short_circuit=short_circuit,
                needs_clarification=True,
                intent=SimpleNamespace(
                    problem_type="ambiguous",
                    clarifying_questions=["which conversation?"],
                ),
                system_injections=["retain pipeline context"],
                contradiction=None,
            )

    prepared = PreparedTurnContext(
        turn=TurnInput(
            text="summarize today",
            session_id="session-skill",
            request_id="request-skill",
            turn_id="turn-skill",
            root_run_id="root-skill",
        ),
        messages=[{"role": "user", "content": "summarize today"}],
        bundle=None,
        assembler=None,
        companion_authority_state=SimpleNamespace(
            active_skill_scope_ids=("scope-summarize-day",),
        ),
    )

    routed = await ProductTurnPreparer().route_intent(
        prepared,
        services={"problem_pipeline": _Pipeline()},
    )

    assert routed.continue_turn is True
    assert [command.kind for command in routed.commands] == ["pipeline_event"]
    assert any(
        message.get("content") == "retain pipeline context"
        for message in prepared.messages
    )


@pytest.mark.asyncio
async def test_route_intent_settles_model_growth_signal_before_delivery() -> None:
    class _Pipeline:
        enabled = True

        async def run_pre_loop(
            self, text: str, *, prior_task_type=None, workload_context=None
        ):
            assert text == "modify the summary Skill"
            return SimpleNamespace(
                events=[],
                short_circuit=False,
                needs_clarification=False,
                intent=SimpleNamespace(
                    problem_type="creation",
                    requires_action_plan=True,
                    growth_signal_kind="explicit_capability_request",
                    clarifying_questions=[],
                ),
                system_injections=[],
                contradiction=None,
            )

    class _Dispatcher:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def settle_semantic_intent(self, **kwargs: Any) -> None:
            self.calls.append(kwargs)

    dispatcher = _Dispatcher()
    owner = SimpleNamespace(name="frozen-owner")
    prepared = PreparedTurnContext(
        turn=TurnInput(
            text="modify the summary Skill",
            session_id="session-growth",
            request_id="request-growth",
            turn_id="turn-growth",
            root_run_id="root-growth",
        ),
        messages=[
            {"role": "user", "content": "modify the summary Skill"}
        ],
        bundle=None,
        assembler=None,
        current_message_id=17,
        companion_ingress_owner=owner,
    )

    routed = await ProductTurnPreparer().route_intent(
        prepared,
        services={
            "problem_pipeline": _Pipeline(),
            "companion_ingress_dispatcher": dispatcher,
        },
    )

    assert routed.requires_action_plan is True
    assert dispatcher.calls == [
        {
            "session_id": "session-growth",
            "message_id": 17,
            "request_id": "request-growth",
            "turn_id": "turn-growth",
            "owner": owner,
            "growth_signal_kind": "explicit_capability_request",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("decision", "expected"), [("go", True), ("cancel", False)])
async def test_plan_confirmation_go_cancel_and_cleanup(
    decision: str, expected: bool
) -> None:
    ws, db, waiters, tools, activity = _WS(), _SessionDB(), {}, _Tools(), _Activity()
    sink, peers = _domain_sink(
        ws=ws, db=db, waiters=waiters, tools=tools, activity=activity
    )
    task = asyncio.create_task(
        build_product_run_presenter().present_domain(_plan_command(), sink)
    )
    await asyncio.sleep(0)
    waiters["session-r2"]["fut"].set_result(decision)
    assert await task is expected
    assert waiters == {}
    assert db.cleared == "session-r2"
    assert tools.read_only == [("session-r2", True), ("session-r2", False)]
    assert peers == ws.frames
    assert ([frame["type"] for frame in ws.frames] == []) if expected else (
        [frame["type"] for frame in ws.frames] == ["chat_v2_plan_cancelled"]
    )
    assert activity.calls == ([] if expected else [("status", "session-r2", "idle")])


@pytest.mark.asyncio
async def test_plan_confirmation_timeout_cancels_and_cleans_up() -> None:
    ws, db, waiters, tools = _WS(), _SessionDB(), {}, _Tools()
    sink, _peers = _domain_sink(ws=ws, db=db, waiters=waiters, tools=tools)
    assert (
        await build_product_run_presenter().present_domain(
            _plan_command(timeout=0.001), sink
        )
        is False
    )
    assert waiters == {}
    assert db.cleared == "session-r2"
    assert tools.read_only[-1] == ("session-r2", False)
    assert ws.frames[-1]["type"] == "chat_v2_plan_cancelled"


@pytest.mark.asyncio
async def test_plan_confirmation_task_cancel_still_cleans_waiter_and_read_only() -> None:
    ws, db, waiters, tools = _WS(), _SessionDB(), {}, _Tools()
    sink, _peers = _domain_sink(ws=ws, db=db, waiters=waiters, tools=tools)
    task = asyncio.create_task(
        build_product_run_presenter().present_domain(_plan_command(), sink)
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert waiters == {}
    assert db.cleared == "session-r2"
    assert tools.read_only == [("session-r2", True), ("session-r2", False)]


def test_presenter_registers_one_handler_per_live_durable_domain_event() -> None:
    assert build_product_run_presenter().registrations == (
        ("AssistantDeltaEvent", "live"),
        ("AssistantMessageEvent", "live"),
        ("ToolCallEvent", "durable"),
        ("ToolResultEvent", "durable"),
        ("AsyncHandoffEvent", "domain"),
        ("FinalEvent", "durable"),
        ("ErrorEvent", "durable"),
        ("ContextCompactedEvent", "domain"),
        ("PipelineEvent", "domain"),
        ("ProviderChainFallbackEvent", "domain"),
    )


@pytest.mark.asyncio
async def test_presenter_keeps_legacy_frame_and_persistence_golden() -> None:
    ws = _WS()
    peers: list[dict[str, Any]] = []
    session_db = _SessionDB()
    usage: list[str] = []

    async def broadcast(_origin: Any, frame: dict[str, Any]) -> None:
        peers.append(frame)

    async def send_final(target: _WS, frame: dict[str, Any], **_kwargs: Any) -> None:
        await target.send_json(frame)

    async def emit_usage(_ws: Any, sid: str, **_kwargs: Any) -> None:
        usage.append(sid)

    context = RunPresentationContext(
        session_id="session-r2",
        text="use a tool",
        websocket=ws,
        services={},
        config=_config(),
        messages=[],
        session_db=session_db,
        vector_worker=None,
        activity_store=None,
        provider_chain=None,
        fallback_provider=None,
        request_id="request-r2",
        max_iterations=16,
        is_sentinel=False,
        broadcast=broadcast,
        send_final=send_final,
        emit_context_usage=emit_usage,
        intent_label_from_turn=lambda used: "task" if used else "ask",
    )
    presenter = build_product_run_presenter()
    state = PresentationState()
    await presenter.present(
        AssistantDeltaEvent(content="hi", kind="content", iteration=1),
        context,
        state,
    )
    await presenter.present(
        ToolCallEvent(
            tool_call=ToolCall(id="call-r2", name="read_file", arguments={"path": "a"}),
            iteration=1,
        ),
        context,
        state,
    )
    await presenter.present(
        PipelineEvent(type="chat_v2_selfcheck", payload={"ok": True}),
        context,
        state,
    )
    await presenter.present(
        FinalEvent(content="done", reasoning_content="reason", iteration=2),
        context,
        state,
    )
    assert [frame["type"] for frame in ws.frames] == [
        "chat_v2_delta",
        "tool_use_event",
        "tool_call",
        "chat_v2_selfcheck",
        "chat_v2_final",
    ]
    assert [frame["type"] for frame in peers] == [
        "chat_v2_delta",
        "tool_use_event",
        "tool_call",
        "chat_v2_selfcheck",
    ]
    assert session_db.messages == [
        {
            "session_id": "session-r2",
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-r2",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "a"}',
                    },
                }
            ],
        },
        {
            "session_id": "session-r2",
            "role": "assistant",
            "content": "done",
            "reasoning_content": "reason",
        },
    ]
    assert state.final_text == "done"
    assert state.final_reasoning == "reason"
    assert state.had_tool_call is True
    assert usage == ["session-r2"]


@pytest.mark.asyncio
async def test_presenter_projects_public_reasoning_summary_without_raw_cot() -> None:
    ws, db = _WS(), _SessionDB()
    context, seen = _run_context(ws, db)
    context.run_id = "run-summary"
    context.task_scope_id = "scope-summary"
    presenter, state = build_product_run_presenter(), PresentationState()

    await presenter.present(
        AssistantDeltaEvent(
            content="private hidden reasoning",
            kind="reasoning",
            iteration=1,
        ),
        context,
        state,
    )
    await presenter.present(
        AssistantDeltaEvent(
            content="我会先检查项目，再启动编辑器。",
            kind="content",
            iteration=1,
        ),
        context,
        state,
    )
    await presenter.present(
        ToolCallEvent(
            tool_call=ToolCall(
                id="call-summary",
                name="process_start",
                arguments={"command": "godot"},
            ),
            iteration=1,
        ),
        context,
        state,
    )

    activity = ws.frames[0]
    assert activity["type"] == "chat_v2_reasoning_activity"
    assert "content" not in activity["payload"]
    summary = next(
        frame for frame in ws.frames
        if frame["type"] == "chat_v2_reasoning_summary"
    )
    assert summary["payload"]["text"] == "我会先检查项目，再启动编辑器。"
    assert "private hidden reasoning" not in str(ws.frames)
    assert "private hidden reasoning" not in str(seen["peers"])
    persisted = db.messages[0]
    assert persisted["projection_kind"] == "workflow_progress"
    assert persisted["context_visibility"] == "exclude"
    assert persisted["root_run_id"] == "run-summary"
    assert persisted["task_scope_id"] == "scope-summary"


@pytest.mark.asyncio
async def test_presenter_logs_blocking_ui_wait_without_sensitive_params(capsys) -> None:
    ws, db = _WS(), _SessionDB()
    context, _seen = _run_context(ws, db)
    context.run_id = "run-permission"
    presenter, state = build_product_run_presenter(), PresentationState()

    await presenter.present(
        PipelineEvent(
            type="permission_request",
            payload={
                "request_id": "decision-1",
                "tool_name": "workflow_spawn",
                "params": {"api_key": "must-not-be-logged"},
            },
            iteration=1,
        ),
        context,
        state,
    )

    assert ws.frames[0]["type"] == "permission_request"
    output = capsys.readouterr().out
    assert "harness_blocking_ui_event_emitted" in output
    assert "event_type=permission_request" in output
    assert "run_id=run-permission" in output
    assert "request_id=decision-1" in output
    assert "must-not-be-logged" not in output


@pytest.mark.asyncio
async def test_presenter_does_not_duplicate_tool_lifecycle_as_progress() -> None:
    ws, db = _WS(), _SessionDB()
    context, _seen = _run_context(ws, db)
    context.run_id = "run-tool-ui"
    presenter, state = build_product_run_presenter(), PresentationState()

    await presenter.present(
        ToolCallEvent(
            tool_call=ToolCall(
                id="call-tool-ui",
                name="read_file",
                arguments={"path": "README.md"},
            ),
            iteration=1,
        ),
        context,
        state,
    )
    await presenter.present(
        ToolResultEvent(
            tool_call_id="call-tool-ui",
            tool_name="read_file",
            result='{"content": "ok"}',
            iteration=1,
        ),
        context,
        state,
    )

    assert [frame["type"] for frame in ws.frames] == [
        "tool_use_event",
        "tool_call",
        "tool_use_event",
        "tool_result",
    ]
    assert all(
        frame["type"] != "chat_v2_reasoning_summary"
        for frame in ws.frames
    )
    assert all(
        message.get("projection_kind") != "workflow_progress"
        for message in db.messages
    )


@pytest.mark.asyncio
async def test_presenter_projects_enabled_artifact_to_live_and_durable_card() -> None:
    ws, db = _WS(), _SessionDB()
    context, _seen = _run_context(ws, db)
    context.config.tools = SimpleNamespace(
        last_mile=SimpleNamespace(
            artifact_envelope=True,
            frontend_artifact_card=True,
        )
    )
    context.run_id = "run-artifact-ui"
    presenter, state = build_product_run_presenter(), PresentationState()
    envelope = {
        "ok": True,
        "result": '{"path":"report.txt"}',
        "error": None,
        "artifacts": [
            {"kind": "file", "path": "report.txt", "title": "report.txt"}
        ],
    }

    await presenter.present(
        ToolResultEvent(
            tool_call_id="call-artifact-ui",
            tool_name="write_file",
            result=json.dumps(envelope),
            iteration=1,
        ),
        context,
        state,
    )

    frame = next(item for item in ws.frames if item["type"] == "tool_result")
    assert frame["payload"]["artifacts"][0]["path"] == "report.txt"
    assert db.messages == [
        {
            "session_id": "session-r2",
            "role": "tool",
            "content": json.dumps(envelope, ensure_ascii=False),
            "tool_call_id": "call-artifact-ui",
            "projection_kind": "artifact_card",
            "skip_embed": True,
            "root_run_id": "run-artifact-ui",
        }
    ]


@pytest.mark.asyncio
async def test_presenter_tool_result_handoff_error_compaction_and_activity_golden() -> None:
    ws, db, activity = _WS(), _SessionDB(), _Activity()
    context, seen = _run_context(ws, db, activity=activity)
    presenter, state = build_product_run_presenter(), PresentationState()
    await presenter.present(
        ToolResultEvent(
            tool_call_id="call-r2",
            tool_name="read_file",
            result='{"value": 7}',
            iteration=2,
        ),
        context,
        state,
    )
    await presenter.present(
        AsyncHandoffEvent(tool_name="deepresearch", run_id="run-r2", iteration=3),
        context,
        state,
    )
    await presenter.present(
        ContextCompactedEvent(
            reduction=0.5, tokens_in=100, tokens_out=50, model="m-r2"
        ),
        context,
        state,
    )
    await presenter.present(
        ErrorEvent(reason="fatal", detail="detail", error_class="RuntimeError"),
        context,
        state,
    )
    assert [frame["type"] for frame in ws.frames] == [
        "tool_use_event",
        "tool_result",
        "chat_v2_final",
        "context_compacted",
        "chat_v2_error",
    ]
    assert [frame["type"] for frame in seen["peers"]] == [
        "tool_use_event",
        "tool_result",
        "chat_v2_final",
        "chat_v2_error",
    ]
    assert db.messages == [
        {
            "session_id": "session-r2",
            "role": "tool",
            "content": '{"value": 7}',
            "tool_call_id": "call-r2",
        }
    ]
    assert [call[0] for call in activity.calls] == [
        "bump",
        "bump",
        "status",
        "bump",
        "error",
    ]


@pytest.mark.asyncio
async def test_presenter_closed_websocket_does_not_break_durable_error_projection() -> None:
    class ClosedWS(_WS):
        async def send_json(self, frame: dict[str, Any]) -> None:
            del frame
            raise RuntimeError("Cannot call send once a close message has been sent")

    ws, db, activity = ClosedWS(), _SessionDB(), _Activity()
    context, seen = _run_context(ws, db, activity=activity)
    presenter, state = build_product_run_presenter(), PresentationState()

    await presenter.present(
        ErrorEvent(
            reason="workspace_selection_required",
            detail="select a project directory",
            error_class="WorkspaceSelectionRequired",
        ),
        context,
        state,
    )

    assert [frame["type"] for frame in seen["peers"]] == ["chat_v2_error"]
    assert activity.calls[-1][0] == "error"


@pytest.mark.asyncio
async def test_presenter_closed_websocket_does_not_break_durable_final_projection() -> None:
    class ClosedWS(_WS):
        async def send_json(self, frame: dict[str, Any]) -> None:
            del frame
            raise RuntimeError("Cannot call send once a close message has been sent")

    ws, db, activity = ClosedWS(), _SessionDB(), _Activity()
    context, seen = _run_context(ws, db, activity=activity)

    async def emit_usage(_ws: Any, _sid: str, **_kwargs: Any) -> None:
        raise RuntimeError("usage socket is closed")

    context.emit_context_usage = emit_usage
    presenter, state = build_product_run_presenter(), PresentationState()

    await presenter.present(FinalEvent(content="durable answer"), context, state)

    assert db.messages[-1]["role"] == "assistant"
    assert db.messages[-1]["content"] == "durable answer"
    assert activity.calls[-1][0] == "status"


@pytest.mark.asyncio
async def test_presenter_final_vector_billing_and_feedback_golden() -> None:
    class _Vector:
        def __init__(self) -> None:
            self.items: list[tuple[int, str]] = []

        async def enqueue(self, message_id: int, text: str) -> None:
            self.items.append((message_id, text))

    class _Assembler:
        def __init__(self) -> None:
            self.items: list[tuple[Any, str]] = []

        def feedback(self, bundle: Any, *, final_response: str) -> None:
            self.items.append((bundle, final_response))

    class _Ledger:
        def __init__(self) -> None:
            self.items: list[dict[str, Any]] = []

        async def record(self, **item: Any) -> None:
            self.items.append(item)

    ws, db, activity = _WS(), _SessionDB(), _Activity()
    vector, assembler, ledger, bundle = _Vector(), _Assembler(), _Ledger(), object()
    provider = SimpleNamespace(
        last_usage={"prompt_tokens": 11, "completion_tokens": 7},
        base_url="https://relay.example/v1",
        model="model-r2",
    )
    context, seen = _run_context(
        ws,
        db,
        services={"session_activity": activity},
        activity=activity,
        vector=vector,
        assembler=assembler,
        bundle=bundle,
        ledger=ledger,
        provider=provider,
    )
    presenter, state = build_product_run_presenter(), PresentationState()
    await presenter.present(FinalEvent(content="done", iteration=4), context, state)
    await presenter.finish_turn(context, state)
    assert vector.items == [(1, "done")]
    assert seen["usage"] == ["session-r2"]
    assert assembler.items == [(bundle, "done")]
    assert ledger.items == [
        {
            "provider": "cloud",
            "model": "model-r2",
            "prompt_tokens": 11,
            "completion_tokens": 7,
        }
    ]
    assert provider.last_usage is None
    assert ("status", "session-r2", "idle") in activity.calls


@pytest.mark.asyncio
async def test_presenter_best_effort_failures_do_not_hide_final_or_leak_usage() -> None:
    class _BrokenDB:
        async def append_message(self, **_message: Any) -> int:
            raise RuntimeError("db failed")

    class _BrokenAssembler:
        def feedback(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("feedback failed")

    class _BrokenLedger:
        async def record(self, **_item: Any) -> None:
            raise RuntimeError("billing failed")

    provider = SimpleNamespace(
        last_usage={"prompt_tokens": 1, "completion_tokens": 2},
        base_url="http://localhost:1",
        model="m",
    )
    ws = _WS()
    context, seen = _run_context(
        ws,
        _BrokenDB(),
        assembler=_BrokenAssembler(),
        bundle=object(),
        ledger=_BrokenLedger(),
        provider=provider,
    )
    presenter, state = build_product_run_presenter(), PresentationState()
    await presenter.present(FinalEvent(content="still visible"), context, state)
    await presenter.finish_turn(context, state)
    assert ws.frames[-1]["payload"]["text"] == "still visible"
    assert provider.last_usage is None


def test_product_chat_has_no_legacy_loop_and_uses_product_venue() -> None:
    tree = ast.parse((ROOT / "backend" / "main.py").read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_chat"
        for node in ast.walk(tree)
    )
    product_chat = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_run_product_harness_chat"
    )
    calls = {
        ast.unparse(node.func)
        for node in ast.walk(product_chat)
        if isinstance(node, ast.Call)
    }
    assert "_harness_venue.open" in calls
    venue_open = next(
        node
        for node in ast.walk(product_chat)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "_harness_venue"
        and node.func.attr == "open"
    )
    assert "proposed_tools" not in {keyword.arg for keyword in venue_open.keywords}
