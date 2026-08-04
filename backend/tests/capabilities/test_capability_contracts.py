from __future__ import annotations

import pytest

from deskpet.capabilities.contracts import (
    CapabilityBinding,
    CapabilityContractError,
    CapabilityScope,
    CapabilityVersionDescriptor,
    CatalogStamp,
    canonical_project_scope_key,
    provider_tool_name,
)


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
    second = CapabilityScope.for_run("root-1", project_root=root).project_key
    assert first == second
    assert first.startswith("project:")
    assert not root.exists()


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
