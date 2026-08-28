# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

import pytest
from context import ServiceContext
from providers.base import LLMProvider


class FakeLLM:
    def __init__(self, name: str = "fake"):
        self.name = name

    async def chat_stream(self, messages, *, temperature=0.7, max_tokens=2048):
        yield "hello"

    async def health_check(self) -> bool:
        return True


def test_service_context_creation():
    ctx = ServiceContext()
    assert ctx.llm_engine is None
    assert ctx.asr_engine is None
    assert ctx.tts_engine is None


def test_service_context_register_and_get():
    ctx = ServiceContext()
    fake_llm = FakeLLM("test")
    ctx.register("llm_engine", fake_llm)
    assert ctx.llm_engine is fake_llm
    assert ctx.llm_engine.name == "test"


def test_service_context_deep_copy_isolation():
    ctx = ServiceContext()
    fake_llm = FakeLLM("original")
    ctx.register("llm_engine", fake_llm)

    ctx_copy = ctx.create_session()
    ctx_copy.llm_engine.name = "modified"

    assert ctx.llm_engine.name == "original"
    assert ctx_copy.llm_engine.name == "modified"


def test_service_context_register_unknown_raises():
    ctx = ServiceContext()
    with pytest.raises(ValueError, match="Unknown service"):
        ctx.register("unknown_engine", object())


def test_service_context_accepts_search_gateway_runtime():
    ctx = ServiceContext()
    gateway = object()

    ctx.register("search_gateway", gateway)

    assert ctx.get("search_gateway") is gateway


def test_service_context_accepts_project_skill_install_runtime():
    ctx = ServiceContext()
    bindings = {
        "project_skill_install_service": object(),
        "skill_install_runtime_verifier": object(),
        "skill_install_verification_driver_factory": object(),
    }

    for name, runtime in bindings.items():
        ctx.register(name, runtime)
        assert ctx.get(name) is runtime


def test_service_context_accepts_legacy_frozen_skill_fallback():
    ctx = ServiceContext()
    resolver = object()

    ctx.register("legacy_frozen_skill_instruction_resolver", resolver)

    assert ctx.get("legacy_frozen_skill_instruction_resolver") is resolver


def test_context_os_task_projection_hooks_are_registered_services():
    ctx = ServiceContext()
    project = object()
    bindings = object()
    creation = object()
    attach = object()

    ctx.register("project_initial_context_snapshot", project)
    ctx.register("project_binding_service", bindings)
    ctx.register("session_creation_service", creation)
    ctx.register("attach_task_snapshot_to_request", attach)

    assert ctx.get("project_initial_context_snapshot") is project
    assert ctx.get("project_binding_service") is bindings
    assert ctx.get("session_creation_service") is creation
    assert ctx.get("attach_task_snapshot_to_request") is attach


@pytest.mark.parametrize(
    "name",
    [
        "companion_detail_query",
        "companion_ingress_dispatcher",
        "companion_notification_service",
        "companion_projection_visibility",
        "companion_growth_action_decision_service",
        "companion_growth_evaluation_decision_service",
        "companion_growth_activation_decision_service",
        "companion_rollback_service",
        "companion_forget_service",
    ],
)
def test_companion_projection_and_action_services_are_registered(name: str):
    ctx = ServiceContext()
    service = object()

    ctx.register(name, service)

    assert ctx.get(name) is service


@pytest.mark.parametrize(
    "name",
    [
        "context_assembler",
        "context_compressor",
        "subagent_scheduler",
        "pipeline_evidence_gate",
        "pipeline_self_check_gate",
        "pipeline_convergence_controller",
        "permission_gate",
    ],
)
def test_critical_harness_services_register_none_without_error(name: str):
    ctx = ServiceContext()
    ctx.register(name, None)
    assert ctx.get(name) is None
