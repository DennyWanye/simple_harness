from __future__ import annotations

import json

import pytest

from deskpet.agent.assembler.bundle import (
    AssemblyPolicy,
    ContextBundle,
    ContextFragment,
    Slice,
    legacy_slice_to_fragment,
)
from deskpet.agent.assembler.components.base import ComponentContext
from deskpet.agent.assembler.components.persona import PersonaComponent
from deskpet.agent.assembler.components.tool import ToolComponent
from deskpet.agent.assembler.policy import load_policies


def test_prepare_request_is_byte_compatible_with_build_messages() -> None:
    bundle = ContextBundle(
        task_type="chat",
        frozen_system="stable",
        skill_prelude="skill",
        memory_block="dynamic",
    )
    kwargs = {
        "history": [{"role": "assistant", "content": "prior"}],
        "late_system_nudge": "control",
        "user_message": "now",
    }
    legacy = bundle.build_messages("base", **kwargs)
    prepared = bundle.prepare_request("base", **kwargs)
    assert json.dumps(prepared.messages, ensure_ascii=False) == json.dumps(
        legacy, ensure_ascii=False
    )


def test_slice_defaults_preserve_legacy_constructor() -> None:
    slice_ = Slice(component_name="persona", text_content="hello")
    assert slice_.fragments == []
    assert slice_.tool_exposure_intent is None
    fragment = legacy_slice_to_fragment(slice_)
    assert fragment is not None
    assert fragment.fragment_id == "legacy:persona"
    assert fragment.placement == "prefix"


def test_component_cannot_double_inject_legacy_text_and_fragments() -> None:
    slice_ = Slice(
        component_name="bad",
        text_content="duplicate",
        fragments=[
            ContextFragment(
                fragment_id="bad:1",
                source="bad",
                role="system",
                content="typed",
                lifetime="stable",
                placement="prefix",
                priority=50,
                trim_policy="truncate",
            )
        ],
    )
    with pytest.raises(ValueError, match="fragments and legacy text"):
        legacy_slice_to_fragment(slice_)


def test_control_fragment_requires_explicit_causal_position() -> None:
    fragment = ContextFragment(
        fragment_id="control:1",
        source="self_check",
        role="system",
        content="check",
        lifetime="current",
        placement="control",
        priority=100,
        trim_policy="never",
        protected=True,
        causal_group_id="turn-1",
        anchor_after="tool-result-1",
    )
    assert fragment.anchor_after == "tool-result-1"


@pytest.mark.asyncio
async def test_persona_context_os_splits_stable_identity_and_workspace() -> None:
    component = PersonaComponent()
    slice_ = await component.provide(
        ComponentContext(
            task_type="code",
            policy=AssemblyPolicy(task_type="code"),
            user_message="fix",
            config={
                "features": {"context_os_v1": True},
                "llm": {"model": "volatile", "base_url": "secret-host"},
                "code_mode": {"enabled": True, "project_root": "F:/work"},
            },
        )
    )
    assert slice_.text_content == ""
    assert [f.lifetime for f in slice_.fragments] == ["platform", "task"]
    assert "volatile" not in str(slice_.fragments[0].content)
    assert "secret-host" not in str(slice_.fragments[0].content)
    assert slice_.fragments[1].content == "[当前工作区]\nF:/work"


@pytest.mark.asyncio
async def test_tool_component_outputs_intent_only_when_context_os_on() -> None:
    policy = load_policies()["chat"]
    slice_ = await ToolComponent().provide(
        ComponentContext(
            task_type="chat",
            policy=policy,
            user_message="继续",
            config={"features": {"context_os_v1": True}},
        )
    )
    assert slice_.tool_schemas == []
    assert slice_.tool_exposure_intent is not None
    assert slice_.tool_exposure_intent.direct_selectors == (
        "memory_read",
        "memory_search",
    )
    assert "source:builtin" in slice_.tool_exposure_intent.discoverable_selectors
    assert "generate_image" in slice_.tool_exposure_intent.deny_selectors


@pytest.mark.asyncio
async def test_tool_component_e2e_direct_override_is_dev_only(monkeypatch) -> None:
    policy = load_policies()["chat"]
    production = await ToolComponent().provide(
        ComponentContext(
            task_type="chat",
            policy=policy,
            user_message="fixture",
            config={"features": {"context_os_v1": True}},
        )
    )
    assert "mcp_context-os-e2e_mcp_ctx_fixture_tool_001" not in (
        production.tool_exposure_intent.direct_selectors
    )

    monkeypatch.setenv("DESKPET_DEV_MODE", "1")
    monkeypatch.setenv("DESKPET_CONTEXT_OS_E2E_CASE_ID", "E2E-CTX-10")
    monkeypatch.setenv("DESKPET_CONTEXT_OS_E2E_SESSION_ID", "fixture-session")
    monkeypatch.setenv("DESKPET_CONTEXT_OS_E2E_DAEMON_URL", "http://127.0.0.1:18991")
    monkeypatch.setenv("DESKPET_CONTEXT_OS_E2E_PROVIDER_URL", "http://127.0.0.1:18992/v1")
    e2e = await ToolComponent().provide(
        ComponentContext(
            task_type="chat",
            policy=policy,
            user_message="fixture",
            session_id="fixture-session",
            config={"features": {"context_os_v1": True}},
        )
    )
    assert e2e.tool_exposure_intent.direct_selectors.count(
        "mcp_context-os-e2e_mcp_ctx_fixture_tool_001"
    ) == 1
    assert e2e.meta["e2e_direct_tools"] == (
        "mcp_context-os-e2e_mcp_ctx_fixture_tool_001",
    )

    other_session = await ToolComponent().provide(
        ComponentContext(
            task_type="chat",
            policy=policy,
            user_message="fixture",
            session_id="other-session",
            config={"features": {"context_os_v1": True}},
        )
    )
    assert "mcp_context-os-e2e_mcp_ctx_fixture_tool_001" not in (
        other_session.tool_exposure_intent.direct_selectors
    )


def test_legacy_tools_and_on_exposure_are_independent() -> None:
    policy = load_policies()["chat"]
    assert policy.tools == ["*"]
    assert policy.tool_exposure is not None
    assert policy.tool_exposure.direct == ("memory_read", "memory_search")
    assert "source:builtin" in policy.tool_exposure.discoverable
