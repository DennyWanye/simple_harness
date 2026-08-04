# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


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


def test_build_agent_has_no_internal_tool_dispatch_switch() -> None:
    agent = _build_minimal_agent(_CfgStub())

    assert not hasattr(agent, "external_tool_dispatch")


def test_product_loop_factory_rehydrates_run_scoped_provider_and_fences(monkeypatch) -> None:
    import asyncio
    import main

    provider = SimpleNamespace(model="fixture-model")
    frozen_skill_resolver = SimpleNamespace(manager_backed=True)
    captured = {}
    loop = SimpleNamespace()

    async def resolve(*_args, **_kwargs):
        return [provider]

    def build(_config, **kwargs):
        captured.update(kwargs)
        return loop

    monkeypatch.setattr(main, "_resolve_agent_provider_chain", resolve)
    monkeypatch.setattr(main, "build_agent", build)
    monkeypatch.setattr(
        main,
        "service_context",
        SimpleNamespace(
            get=lambda name: (
                frozen_skill_resolver
                if name == "frozen_skill_instruction_resolver"
                else None
            )
        ),
    )
    request = SimpleNamespace(
        run_context=SimpleNamespace(
            venue="text",
            session_id="session-1",
            workspace={"root": None},
            request_id="request-1",
        ),
        request_payload={
            "loop": {
                "user_request": "hello",
                "pipeline_problem_type": "simple",
                "pipeline_needs_investigation": True,
                "response_quality": {
                    "schema_version": 1,
                    "mode": "semantic_completeness",
                    "preference_key": "response.detail",
                    "preference_value": "brief",
                    "snapshot_hash": "snapshot-1",
                },
            }
        },
    )

    bound_loop, loop_options = asyncio.run(main._build_product_agent_loop(request))

    assert bound_loop is loop
    assert loop_options["provider_chain"] == [provider]
    assert loop_options["loop_user_request"] == "hello"
    assert loop_options["context_request_id"] == "request-1"
    assert loop_options["host_validated_zero_tool_terminal"] is False
    assert "external_tool_dispatch" not in captured
    assert captured["skill_loader"] is frozen_skill_resolver
    assert captured["pipeline_problem_type"] == "simple"
    assert captured["pipeline_needs_investigation"] is True
    assert (
        type(captured["response_quality_gate"]).__name__
        == "ModelResponseCompletenessGate"
    )


def test_product_loop_factory_marks_companion_background_as_host_validated(
    monkeypatch,
) -> None:
    import asyncio
    import main

    provider = SimpleNamespace(model="fixture-model")
    captured = {}
    loop = SimpleNamespace()

    async def resolve(*_args, **_kwargs):
        return [provider]

    def build(_config, **kwargs):
        captured.update(kwargs)
        return loop

    monkeypatch.setattr(main, "_resolve_agent_provider_chain", resolve)
    monkeypatch.setattr(main, "build_agent", build)
    monkeypatch.setattr(
        main,
        "service_context",
        SimpleNamespace(get=lambda _name: None),
    )
    request = SimpleNamespace(
        run_context=SimpleNamespace(
            venue="background",
            session_id="companion:profile-a:1:job-1",
            workspace={"root": None},
            request_id="request-1",
        ),
        request_payload={
            "companion_background": {
                "schema_version": 1,
                "job_id": "job-1",
                "purpose": "delegated_task",
                "evidence_ids": ["event-1"],
                "capture_growth": False,
            }
        },
    )

    bound_loop, loop_options = asyncio.run(main._build_product_agent_loop(request))

    assert bound_loop is loop
    assert loop_options["host_validated_zero_tool_terminal"] is True
    assert captured["enable_verify_gate"] is False
    assert captured["skill_loader"] is None


@pytest.mark.parametrize("purpose", ["reflection", "evaluation", "delegated_task"])
@pytest.mark.parametrize("recovered", [False, True])
def test_product_loop_recognizes_every_companion_background_purpose(
    purpose: str,
    recovered: bool,
) -> None:
    from types import MappingProxyType

    import main

    background = MappingProxyType(
        {
            "schema_version": 1,
            "job_id": "job-1",
            "purpose": purpose,
            "evidence_ids": ["event-1"],
            "capture_growth": False,
        }
    )
    product_payload = MappingProxyType({"companion_background": background})
    request_payload = (
        MappingProxyType({"payload": product_payload, "text": "recovered"})
        if recovered
        else product_payload
    )
    request = SimpleNamespace(
        run_context=SimpleNamespace(
            venue="background",
            session_id="companion:profile-a:1:job-1",
        ),
        request_payload=request_payload,
    )

    assert main._is_companion_background_request(request) is True


def test_product_loop_rejects_companion_stage_as_untrusted_purpose() -> None:
    import main

    request = SimpleNamespace(
        run_context=SimpleNamespace(
            venue="background",
            session_id="companion:profile-a:1:job-1",
        ),
        request_payload={
            "companion_background": {
                "schema_version": 1,
                "job_id": "job-1",
                "purpose": "candidate_build",
                "evidence_ids": ["event-1"],
                "capture_growth": False,
            }
        },
    )

    assert main._is_companion_background_request(request) is False


def test_product_context_os_recovery_uses_exact_snapshot_ref_not_legacy_names(
    monkeypatch,
) -> None:
    import main
    from deskpet.capabilities.refresh import context_os_snapshot_ref
    from deskpet.tools.capabilities import (
        PreparedToolSet,
        ToolEligibilityContext,
    )
    from deskpet.tools.prepared_snapshot import dump_context_os_snapshot

    prepared = PreparedToolSet.create(
        scope_id="scope-exact-recovery",
        revision=1,
        registry_revision=1,
        direct=(),
        deferred=(),
        activated=(),
        denied_names=(),
        policy_fingerprint="policy-exact-recovery",
        decisions=(),
    )
    eligibility = ToolEligibilityContext(
        session_id="session-exact-recovery",
        request_id="request-exact-recovery",
        task_type="chat",
        mode="general",
    )
    snapshot_ref = context_os_snapshot_ref(prepared, eligibility)
    existing_scope = SimpleNamespace(prepared=prepared)
    scope_store = SimpleNamespace(
        get=lambda *_args, **_kwargs: existing_scope,
    )
    monkeypatch.setattr(
        main,
        "config",
        SimpleNamespace(features=SimpleNamespace(context_os_v1=True)),
    )
    monkeypatch.setattr(
        main,
        "service_context",
        SimpleNamespace(
            get=lambda name: (
                scope_store
                if name == "tool_capability_scope_store"
                else None
            )
        ),
    )
    monkeypatch.setattr(
        main.deskpet_tool_registry_v2,
        "validate_prepared_tool_set",
        lambda *_args, **_kwargs: None,
    )
    request = SimpleNamespace(
        request_payload={
            "context_os": dump_context_os_snapshot(prepared, eligibility),
            "tool_set_snapshot_ref": snapshot_ref,
        },
        run_context=SimpleNamespace(
            session_id=eligibility.session_id,
            request_id=eligibility.request_id,
        ),
        capability_snapshot={
            "prepared_tool_set_ref": snapshot_ref,
            "capability_hash": "a" * 64,
        },
        canonical_messages=({"role": "user", "content": "hello"},),
    )

    restored = main._restore_product_context_os(request)

    assert restored.tool_set == prepared
    assert restored.messages == [{"role": "user", "content": "hello"}]

    request.capability_snapshot["prepared_tool_set_ref"] = "b" * 64
    with pytest.raises(
        RuntimeError,
        match="product recovery capability snapshot mismatch",
    ):
        main._restore_product_context_os(request)


def test_production_skill_resolver_binds_agent_and_assembler(monkeypatch) -> None:
    import main
    from deskpet.agent.assembler import build_default_assembler

    assembler = build_default_assembler(skill_loader=object())
    values = {"context_assembler": assembler}

    class _Services:
        def get(self, name: str):
            return values.get(name)

        def register(self, name: str, value: object) -> None:
            values[name] = value

    resolver = SimpleNamespace(manager_backed=True)
    monkeypatch.setattr(main, "service_context", _Services())

    main._bind_frozen_skill_snapshot_resolver(resolver)
    main._bind_frozen_skill_snapshot_resolver(resolver)

    assert values["frozen_skill_instruction_resolver"] is resolver
    with pytest.raises(
        RuntimeError,
        match="manager_skill_snapshot_resolver_already_bound",
    ):
        main._bind_frozen_skill_snapshot_resolver(
            SimpleNamespace(manager_backed=True)
        )
    assert values["frozen_skill_instruction_resolver"] is resolver


def test_managed_skill_discovery_projection_is_a_public_service_slot() -> None:
    from context import _VALID_SERVICES, ServiceContext

    projection = object()
    services = ServiceContext()
    services.register("managed_skill_discovery_projection", projection)

    assert "managed_skill_discovery_projection" in _VALID_SERVICES
    assert services.get("managed_skill_discovery_projection") is projection


def test_service_context_snapshot_is_an_immutable_turn_mapping() -> None:
    from context import ServiceContext

    services = ServiceContext()
    first = object()
    second = object()
    services.register("managed_skill_discovery_projection", first)

    snapshot = services.snapshot()
    services.register("managed_skill_discovery_projection", second)

    assert snapshot["managed_skill_discovery_projection"] is first
    with pytest.raises(TypeError):
        snapshot["managed_skill_discovery_projection"] = second


def test_build_agent_does_not_restore_process_local_subagent_registry(monkeypatch) -> None:
    import main

    registry = MagicMock(name="subagent_registry")
    monkeypatch.setattr(main, "service_context", _ServiceContextStub(registry))

    agent = _build_minimal_agent(_CfgStub())

    assert not hasattr(agent, "_subagent_registry")


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
