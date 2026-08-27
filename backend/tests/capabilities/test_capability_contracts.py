from __future__ import annotations

import pytest

from deskpet.capabilities.contracts import (
    CapabilityBinding,
    CapabilityContractError,
    CapabilityScope,
    CapabilityVersionDescriptor,
    CatalogStamp,
    canonical_project_identity_scope_key,
    canonical_project_scope_key,
    provider_tool_name,
)
from deskpet.harness.context import HostContextFactory


def _digest(seed: str) -> str:
    import hashlib

    return hashlib.sha256(seed.encode()).hexdigest()


def test_catalog_stamp_is_derived_from_every_revision() -> None:
    first = CatalogStamp(1, 2, 3, 4, 5)
    assert len(first.fingerprint) == 64
    assert CatalogStamp(1, 2, 3, 4, 6).fingerprint != first.fingerprint
    with pytest.raises(CapabilityContractError, match="does not match"):
        CatalogStamp(1, 2, 3, 4, 5, fingerprint=_digest("wrong"))


def test_provider_name_is_deterministic_and_never_truncated() -> None:
    assert provider_tool_name("godot", "project_check") == "godot__project_check"
    with pytest.raises(CapabilityContractError, match="<=64"):
        provider_tool_name("x" * 63, "tool")
    with pytest.raises(CapabilityContractError, match="ASCII"):
        provider_tool_name("bad.pack", "tool")


def test_project_scope_is_stable_without_writing_project(tmp_path) -> None:
    root = tmp_path / "项目 with spaces"
    first = canonical_project_scope_key(root)
    legacy = CapabilityScope.for_run("root-1", project_root=root)
    assert first.startswith("project:")
    assert legacy.project_key is None
    assert not root.exists()


def test_project_scope_binds_frozen_identity_not_path() -> None:
    first = canonical_project_identity_scope_key("project-1", 1, "fs-a")
    restarted = CapabilityScope.for_run(
        "run-restarted",
        project_id="project-1",
        project_revision=1,
        project_identity="fs-a",
    )
    other_identity = canonical_project_identity_scope_key("project-2", 1, "fs-b")
    relocated = canonical_project_identity_scope_key("project-1", 2, "fs-a")

    assert restarted.project_key == first
    assert first.startswith("project:v2:")
    assert other_identity != first
    assert relocated != first
    assert ("project", first) in restarted.binding_keys()
    assert ("project", relocated) not in restarted.binding_keys()


def test_project_scope_rejects_partial_or_stale_identity() -> None:
    with pytest.raises(CapabilityContractError, match="supplied together"):
        CapabilityScope.for_run("run", project_id="project-1")
    with pytest.raises(CapabilityContractError, match="positive integer"):
        CapabilityScope.for_run(
            "run",
            project_id="project-1",
            project_revision=0,
            project_identity="fs-a",
        )


def test_host_factory_projects_identity_into_trusted_tool_context(tmp_path) -> None:
    factory = HostContextFactory()
    run = factory.create_run_context(
        session_id="session",
        root_run_id="root",
        request_id="request",
        turn_id="turn",
        venue="text",
        capability_hash="a" * 64,
        provider_plan=(),
        trace_id="trace",
        principal_id="principal",
        workspace=tmp_path,
        project_id="project-1",
        project_revision=3,
        project_identity="filesystem-1",
    )
    context = factory.create_tool_context(
        run,
        run_id="run",
        call_id="call",
        effect_id="effect",
    )
    assert (
        context.project_id,
        context.project_revision,
        context.project_identity,
    ) == ("project-1", 3, "filesystem-1")


def test_version_and_binding_validate_tool_mapping() -> None:
    descriptor = CapabilityVersionDescriptor(
        capability_id="godot",
        display_name="Godot",
        version="1.0.0",
        kind="function_tool",
        source="builtin:godot",
        description="Godot helper",
        aliases=("game",),
        logical_tool_ids=("check",),
        provider_tool_names=("godot__check",),
        permission_categories=("filesystem_read",),
        effect_kinds=("read_only",),
        schema_hash=_digest("schema"),
        manifest_hash=_digest("manifest"),
        health="healthy",
    )
    binding = CapabilityBinding(
        binding_id="binding-1",
        capability_id="godot",
        version="1.0.0",
        manifest_hash=descriptor.manifest_hash,
        scope="user",
        scope_key="default",
        active=True,
        generation=1,
    )
    assert descriptor.fingerprint
    assert binding.fingerprint

    with pytest.raises(CapabilityContractError, match="equal length"):
        CapabilityVersionDescriptor(
            capability_id="godot",
            display_name="Godot",
            version="1.0.0",
            kind="function_tool",
            source="builtin:godot",
            description="Godot helper",
            aliases=(),
            logical_tool_ids=("one",),
            provider_tool_names=(),
            permission_categories=(),
            effect_kinds=(),
            schema_hash=_digest("schema"),
            manifest_hash=_digest("manifest"),
        )
