# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock


@dataclass
class _VerifierStub:
    verify_gate_mode: str = "off"
    emit_receipts: bool = False
    claim_patterns_file: str = "verify/claim_patterns.yaml"
    max_verify_nudges: int = 2
    structured_reflection: bool = False
    external_evaluator: bool = False
    ephemeral_subagent_model: str = "haiku"


@dataclass
class _ToolsStub:
    verifier: _VerifierStub = field(default_factory=_VerifierStub)


@dataclass
class _CfgStub:
    tools: _ToolsStub = field(default_factory=_ToolsStub)
    raw: dict[str, Any] = field(default_factory=dict)


class _PathsStub:
    def __init__(self, user_data: Path) -> None:
        self._user_data = user_data

    def user_data_dir(self) -> Path:
        return self._user_data


class _ServiceContextStub:
    def __init__(self, registry: object | None) -> None:
        self._registry = registry

    def get(self, name: str) -> object | None:
        if name == "subagent_registry":
            return self._registry
        return None


def _build_minimal_agent(cfg: _CfgStub, **kwargs: Any):
    from main import build_agent

    return build_agent(
        cfg,
        llm_registry=MagicMock(name="llm_registry"),
        tool_registry=MagicMock(name="tool_registry"),
        context_manager=MagicMock(name="context_manager"),
        receipt_store_getter=lambda: None,
        **kwargs,
    )


def test_build_agent_wires_problem_pipeline_runtime_knobs() -> None:
    evidence_gate = MagicMock(name="evidence_gate")

    agent = _build_minimal_agent(
        _CfgStub(),
        evidence_gate=evidence_gate,
        pipeline_problem_type="debug",
        pipeline_needs_investigation=True,
        pipeline_observability=True,
        convergence_report_on_stop=True,
    )

    assert agent._evidence_gate is evidence_gate
    assert agent._pipeline_problem_type == "debug"
    assert agent._pipeline_needs_investigation is True
    assert agent._pipeline_observability is True
    assert agent._convergence_controller is not None


def test_build_agent_wires_subagent_registry_from_service_context(monkeypatch) -> None:
    import main

    registry = MagicMock(name="subagent_registry")
    monkeypatch.setattr(main, "service_context", _ServiceContextStub(registry))

    agent = _build_minimal_agent(_CfgStub())

    assert agent._subagent_registry is registry


def test_build_agent_iteration_trace_flag_constructs_tracer(monkeypatch, tmp_path) -> None:
    import main

    cfg = _CfgStub(raw={"agent": {"iteration_trace_enabled": True}})
    monkeypatch.setattr(main, "_paths", _PathsStub(tmp_path))

    agent = _build_minimal_agent(cfg)

    assert agent._tracer is not None
    assert agent._tracer.trace_dir == tmp_path / "traces"


def test_build_agent_iteration_trace_uses_new_agent_flag_name() -> None:
    cfg = _CfgStub(raw={"context": {"assembler": {"trace_enabled": True}}})

    agent = _build_minimal_agent(cfg)

    assert agent._tracer is None
