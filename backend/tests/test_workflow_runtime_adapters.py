from __future__ import annotations

import pytest

from deskpet.workflows.bootstrap import build_workflow_service
from deskpet.workflows.contracts import WorkflowContext
from deskpet.workflows.runtime_adapters import (
    DEEP_RESEARCH_EXTENSION,
    DeepResearchRuntimeExtension,
    WorkflowRuntimeAdapter,
    WorkflowRuntimeAdapterError,
    WorkflowRuntimeAdapterRegistry,
)


def _state_factory(**_values):
    return {}


def _context_factory(*_args, **_kwargs):
    return WorkflowContext(ports={})


def test_generic_runtime_registry_is_single_owner_and_fail_closed():
    registry = WorkflowRuntimeAdapterRegistry()
    deep = WorkflowRuntimeAdapter(
        "deep_research",
        "v5",
        _state_factory,
        _context_factory,
        extensions={
            DEEP_RESEARCH_EXTENSION: DeepResearchRuntimeExtension(
                new_runs_enabled=True,
                retry_from_start=True,
                action_ids=("retry_from_start", "generate_now"),
            )
        },
    )
    code = WorkflowRuntimeAdapter(
        "code_complex", "v1", _state_factory, _context_factory
    )
    ppt = WorkflowRuntimeAdapter("ppt_pro", "v1", _state_factory, _context_factory)
    registry.register(deep)
    registry.register(code)
    registry.register(ppt)

    assert registry.deep_research.versions() == ("v5",)
    assert registry.deep_research.require("v5") is deep.deep_research
    assert registry.deep_research.resolve_default("v5") == "v5"
    assert registry.identities() == (
        ("code_complex", "v1"),
        ("deep_research", "v5"),
        ("ppt_pro", "v1"),
    )
    with pytest.raises(WorkflowRuntimeAdapterError, match="already registered"):
        registry.register(deep)

    registry.seal(registry.identities())
    registry.activate()
    registry.require_active()
    with pytest.raises(WorkflowRuntimeAdapterError, match="after seal"):
        registry.register(
            WorkflowRuntimeAdapter(
                "deep_research", "v6", _state_factory, _context_factory
            )
        )


def test_runtime_registry_validates_required_identity_before_seal():
    registry = WorkflowRuntimeAdapterRegistry()
    registry.register(
        WorkflowRuntimeAdapter("deep_research", "v5", _state_factory, _context_factory)
    )
    with pytest.raises(WorkflowRuntimeAdapterError) as exc:
        registry.seal((("deep_research", "v6"),))
    assert exc.value.code == "required_runtime_adapter_missing"
    assert registry.sealed is False


def test_deep_research_extension_defaults_new_roots_fail_closed():
    extension = DeepResearchRuntimeExtension()
    assert extension.new_runs_enabled is False

    registry = WorkflowRuntimeAdapterRegistry()
    registry.register(
        WorkflowRuntimeAdapter(
            "deep_research",
            "v6",
            _state_factory,
            _context_factory,
            extensions={DEEP_RESEARCH_EXTENSION: extension},
        )
    )
    with pytest.raises(WorkflowRuntimeAdapterError) as exc:
        registry.deep_research.resolve_default("v6")
    assert exc.value.code == "deep_research_new_runs_disabled"


@pytest.mark.asyncio
async def test_bootstrap_can_prepare_then_activate_runtime(tmp_path):
    service = await build_workflow_service(tmp_path, activate=False)
    assert service.runtime_adapters.sealed is False
    assert service.runtime_adapters.active is False
    with pytest.raises(WorkflowRuntimeAdapterError) as exc:
        service.ensure_runtime_active()
    assert exc.value.code == "runtime_registry_not_active"

    service.runtime_adapters.register(
        WorkflowRuntimeAdapter("deep_research", "v5", _state_factory, _context_factory)
    )
    await service.activate_runtime(
        required_runtime_identities=(("deep_research", "v5"),)
    )
    service.ensure_runtime_active()
    assert service.runtime_adapters.active is True
