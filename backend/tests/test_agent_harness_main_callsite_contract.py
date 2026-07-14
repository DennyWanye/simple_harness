# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = BACKEND_ROOT / "main.py"


def _main_tree() -> ast.Module:
    return ast.parse(MAIN_PATH.read_text(encoding="utf-8"), filename=str(MAIN_PATH))


def _calls_named(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    ]


def _attribute_calls(tree: ast.AST, attr: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
    ]


def _kw(call: ast.Call, name: str) -> ast.AST:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    raise AssertionError(f"missing keyword {name}")


def _kw_names(call: ast.Call) -> set[str]:
    return {keyword.arg for keyword in call.keywords if keyword.arg is not None}


def _unparse(node: ast.AST) -> str:
    return ast.unparse(node)


def _production_build_agent_call() -> ast.Call:
    calls = _calls_named(_main_tree(), "build_agent")
    production = [
        call
        for call in calls
        if "llm_registry" in _kw_names(call)
        and "tool_registry" in _kw_names(call)
        and "receipt_store_getter" in _kw_names(call)
    ]
    assert len(production) == 1
    return production[0]


def test_context_assembler_uses_agent_loop_v2_tool_registry() -> None:
    calls = [
        call
        for call in _attribute_calls(_main_tree(), "assemble")
        if "tool_registry" in _kw_names(call)
        and "current_message_id" in _kw_names(call)
    ]
    assert len(calls) == 1
    assert _unparse(_kw(calls[0], "tool_registry")) == "deskpet_tool_registry_v2"


def test_chat_path_build_agent_call_passes_all_harness_kwargs() -> None:
    call = _production_build_agent_call()

    required = {
        "completion_probe",
        "code_todo_getter",
        "signature_repeat_threshold",
        "session_goal_store",
        "goal_checker",
        "compressor",
        "skill_loader",
        "skill_matcher",
        "tool_path_recorder",
        "memory_curator",
        "evidence_gate",
        "pipeline_problem_type",
        "pipeline_needs_investigation",
        "pipeline_observability",
        "convergence_report_on_stop",
    }

    assert required <= _kw_names(call)


def test_chat_path_fetches_manifest_services_from_service_context() -> None:
    tree = _main_tree()
    service_gets = {
        call.args[0].value
        for call in _attribute_calls(tree, "get")
        if isinstance(call.func.value, ast.Name)
        and call.func.value.id == "service_context"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    }

    assert {
        "session_goal_store",
        "goal_checker",
        "context_compressor",
        "skill_loader",
        "skill_matcher",
        "tool_path_recorder",
        "memory_curator",
        "pipeline_evidence_gate",
    } <= service_gets


def test_chat_path_evidence_gate_is_guarded_by_preloop_short_circuit() -> None:
    node = _kw(_production_build_agent_call(), "evidence_gate")
    expr = _unparse(node)
    constants = {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }

    assert "pipeline_evidence_gate" in constants
    assert "_pre" in expr
    assert "short_circuit" in expr
    assert "else None" in expr


def test_chat_path_pipeline_flags_derive_from_preloop_and_config() -> None:
    call = _production_build_agent_call()

    needs_expr = _unparse(_kw(call, "pipeline_needs_investigation"))
    observability_expr = _unparse(_kw(call, "pipeline_observability"))
    convergence_expr = _unparse(_kw(call, "convergence_report_on_stop"))

    assert "_pre.intent.needs_investigation" in needs_expr
    assert "problem_pipeline" in observability_expr
    assert "observability_events" in observability_expr
    assert "convergence_report_on_stop" in convergence_expr
    assert "not _pre.short_circuit" in convergence_expr


def test_chat_path_agent_run_receives_runtime_context_kwargs() -> None:
    run_calls = [
        call
        for call in _attribute_calls(_main_tree(), "run")
        if isinstance(call.func.value, ast.Name) and call.func.value.id == "_agent"
    ]
    assert len(run_calls) == 1
    call = run_calls[0]

    assert {"session_id", "stream", "provider_chain", "loop_user_request", "is_sentinel_run"} <= _kw_names(call)
    assert _unparse(_kw(call, "session_id")) == "_sid"
    assert _unparse(_kw(call, "stream")) == "True"
    assert _unparse(_kw(call, "provider_chain")) == "_provider_chain"


def test_chat_path_closes_originating_turn_after_async_workflow_handoff() -> None:
    source = MAIN_PATH.read_text(encoding="utf-8")

    assert "AsyncHandoffEvent as _AsyncHandoffEv" in source
    assert "isinstance(ev, _AsyncHandoffEv)" in source
    assert '"type": "chat_v2_final"' in source
    assert '"handoff_run_id": ev.run_id' in source


@pytest.mark.parametrize("msg_type", ["chat", "chat_v2"])
def test_control_ws_chat_path_dynamically_wires_build_agent(monkeypatch, msg_type: str) -> None:
    import main
    from agent.agent_loop import FinalEvent
    from deskpet.agent.assembler.bundle import ContextBundle
    from deskpet.agent.intent_triage import IntentCard
    from deskpet.agent.problem_pipeline import PreLoopResult

    captured: dict[str, Any] = {}
    call_order: list[str] = []
    sentinels = {
        "session_goal_store": object(),
        "goal_checker": object(),
        "context_compressor": object(),
        "skill_loader": object(),
        "skill_matcher": object(),
        "tool_path_recorder": object(),
        "memory_curator": object(),
        "pipeline_evidence_gate": object(),
    }
    previous = {name: main.service_context.get(name) for name in sentinels}
    previous_assembler = main.service_context.get("context_assembler")

    class _FakeAssembler:
        async def assemble(self, **kwargs):  # noqa: ANN003
            call_order.append("context_assembly")
            return ContextBundle(task_type="chat")

        def feedback(self, *_args, **_kwargs):  # noqa: ANN001
            return None

    class _FakePipeline:
        enabled = True

        async def run_pre_loop(self, user_message: str, *, prior_task_type=None):  # noqa: ANN001
            call_order.append("problem_pipeline")
            return PreLoopResult(
                short_circuit=False,
                intent=IntentCard(
                    restated_intent=user_message,
                    problem_type="debug",
                    needs_investigation=True,
                ),
                system_injections=["<intent>debug</intent>"],
                events=[
                    {
                        "type": "chat_v2_intent",
                        "payload": {
                            "restated_intent": user_message,
                            "problem_type": "debug",
                            "ambiguity_score": 0.0,
                        },
                    }
                ],
            )

    class _FakeAgent:
        async def run(self, messages, **kwargs):  # noqa: ANN001
            call_order.append("agent_run")
            captured["run_messages"] = messages
            captured["run_kwargs"] = dict(kwargs)
            yield FinalEvent(
                type="final",
                task_id="dynamic-contract",
                iteration=1,
                content="dynamic contract ok",
                stop_reason="end_turn",
            )

    def _fake_build_agent(cfg, **kwargs):  # noqa: ANN001
        call_order.append("build_agent")
        captured["cfg"] = cfg
        captured["build_kwargs"] = dict(kwargs)
        return _FakeAgent()

    async def _noop_broadcast(*_args, **_kwargs):  # noqa: ANN001
        return None

    async def _noop_context_usage(*_args, **_kwargs):  # noqa: ANN001
        return None

    pp_cfg = main.config.features.problem_pipeline
    monkeypatch.setattr(pp_cfg, "observability_events", True, raising=False)
    monkeypatch.setattr(pp_cfg, "convergence_report_on_stop", True, raising=False)
    monkeypatch.setattr(pp_cfg, "plan_companion_enabled", False, raising=False)
    monkeypatch.setattr(main.config.features, "plan_confirm_gate", False, raising=False)
    monkeypatch.setattr(main, "build_agent", _fake_build_agent)
    monkeypatch.setattr(main, "_broadcast_default_chat_peers", _noop_broadcast)
    monkeypatch.setattr(main, "_emit_context_usage", _noop_context_usage)

    try:
        for name, value in sentinels.items():
            main.service_context.register(name, value)
        main.service_context.register("context_assembler", _FakeAssembler())
        main.service_context.register("problem_pipeline", _FakePipeline())

        client = TestClient(main.app)
        with client.websocket_connect(
            "/ws/control",
            headers={"X-Shared-Secret": main.SHARED_SECRET},
            params={"session_id": f"dynamic_{msg_type}"},
        ) as ws:
            ws.receive_json()
            ws.send_json(
                {
                    "type": msg_type,
                    "payload": {
                        "text": "please debug dynamic harness",
                        "session_id": f"dynamic_{msg_type}",
                    },
                }
            )
            frames = []
            for _ in range(10):
                frame = ws.receive_json()
                frames.append(frame)
                if frame["type"] == "chat_v2_final":
                    break
    finally:
        for name, value in previous.items():
            main.service_context.register(name, value)
        main.service_context.register("context_assembler", previous_assembler)
        main.service_context.register("problem_pipeline", None)

    assert any(frame["type"] == "chat_v2_final" for frame in frames)
    final = next(frame for frame in frames if frame["type"] == "chat_v2_final")
    assert final["payload"]["text"] == "dynamic contract ok"

    build_kwargs = captured["build_kwargs"]
    assert build_kwargs["session_goal_store"] is sentinels["session_goal_store"]
    assert build_kwargs["goal_checker"] is sentinels["goal_checker"]
    assert build_kwargs["compressor"] is sentinels["context_compressor"]
    assert build_kwargs["skill_loader"] is sentinels["skill_loader"]
    assert build_kwargs["skill_matcher"] is sentinels["skill_matcher"]
    assert build_kwargs["tool_path_recorder"] is sentinels["tool_path_recorder"]
    assert build_kwargs["memory_curator"] is sentinels["memory_curator"]
    assert build_kwargs["evidence_gate"] is sentinels["pipeline_evidence_gate"]
    assert build_kwargs["pipeline_problem_type"] == "debug"
    assert build_kwargs["pipeline_needs_investigation"] is True
    assert build_kwargs["pipeline_observability"] is True
    assert build_kwargs["convergence_report_on_stop"] is True

    run_kwargs = captured["run_kwargs"]
    assert run_kwargs["session_id"] == f"dynamic_{msg_type}"
    assert run_kwargs["stream"] is True
    assert "provider_chain" in run_kwargs
    assert run_kwargs["loop_user_request"] == "please debug dynamic harness"
    assert run_kwargs["is_sentinel_run"] is False
    assert any(m.get("role") == "system" and "<intent>debug</intent>" in m.get("content", "") for m in captured["run_messages"])
    assert call_order == [
        "context_assembly",
        "problem_pipeline",
        "build_agent",
        "agent_run",
    ]
