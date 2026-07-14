# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

import pytest
from context import ServiceContext
from providers.base import LLMProvider
from deskpet.agent.harness_manifest import harness_service_names


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


@pytest.mark.parametrize("name", harness_service_names())
def test_harness_manifest_services_are_whitelisted(name: str):
    ctx = ServiceContext()
    ctx.register(name, None)
    assert ctx.get(name) is None


@pytest.mark.parametrize(
    "name",
    [
        "context_assembler",
        "context_compressor",
        "subagent_scheduler",
        "subagent_registry",
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
