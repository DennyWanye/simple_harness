# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from types import SimpleNamespace

import pytest
from simple_harness import RunId
from simple_harness.tools import (
    CatalogRunToolExposure,
    RuntimeToolCatalog,
    SkillResourceRecord,
    ToolExposureMode,
)

from deskpet.capabilities.contracts import fingerprint_json
from deskpet.companion.skills import ResolvedSkillInstructionV1
from deskpet.sdk_adapters.skill_resolver import SdkThenLegacyFrozenSkillResolver


@pytest.mark.asyncio
async def test_sdk_run_resolves_exact_frozen_skill_resource() -> None:
    scope_payload = {
        "schema": "prepared_skill_invocation_scope/v1",
        "owner_key": "builtin",
        "pack_id": "skill-translate-doc",
        "skill_id": "translate-doc",
        "version": "0.1.0",
        "manifest_hash": "a" * 64,
        "content_hash": "b" * 64,
        "allowed_tools": [],
    }
    scope_payload["scope_hash"] = fingerprint_json(scope_payload)
    record = SkillResourceRecord(
        capability_id="skill:translate-doc",
        namespace="skill",
        source="skill-pack:builtin",
        source_revision="builtin",
        exposure_mode=ToolExposureMode.DEFERRED,
        skill_locator="translate-doc",
        content_hash="b" * 64,
        description="Translate a document.",
        metadata={
            "owner_key": "builtin",
            "pack_id": "skill-translate-doc",
            "version": "0.1.0",
            "manifest_hash": "a" * 64,
            "scope_hash": scope_payload["scope_hash"],
            "allowed_tools": [],
        },
    )
    exposure = CatalogRunToolExposure(RuntimeToolCatalog((record,), generation=1))
    exposure.restore(RunId("sdk-run"), None)
    authority = SimpleNamespace(
        catalog_fingerprint=exposure.catalog.snapshot.fingerprint,
        specs={},
    )

    class Authorities:
        def resolve(self, run_id):
            assert run_id == "sdk-run"
            return authority

        def resolve_exposure(self, run_id):
            assert run_id == RunId("sdk-run")
            return exposure

    class SnapshotResolver:
        async def resolve_instruction(self, scope, arguments):
            assert scope.skill_id == "translate-doc"
            assert arguments == ()
            return ResolvedSkillInstructionV1(scope, "frozen body")

    resolver = SdkThenLegacyFrozenSkillResolver(
        authorities=Authorities(),
        snapshot_resolver=SnapshotResolver(),
        legacy_resolver=SimpleNamespace(),
    )
    result = await resolver.resolve_frozen_instruction(
        run_id="sdk-run",
        skill_name="translate-doc",
        arguments=(),
    )

    assert result["instruction"] == "frozen body"
    assert result["skill_id"] == "translate-doc"
    assert result["allowed_tool_refs"] == []
    assert result["effective_tool_ref_hashes"] == []
    assert len(result["effective_tool_refs_hash"]) == 64


@pytest.mark.asyncio
async def test_sdk_run_reads_frozen_skill_support_resource() -> None:
    scope_payload = {
        "schema": "prepared_skill_invocation_scope/v1",
        "owner_key": "profile",
        "pack_id": "plan-bs",
        "skill_id": "plan-bs",
        "version": "1.0.0",
        "manifest_hash": "a" * 64,
        "content_hash": "b" * 64,
        "allowed_tools": [],
    }
    scope_payload["scope_hash"] = fingerprint_json(scope_payload)
    record = SkillResourceRecord(
        capability_id="skill:plan-bs",
        namespace="skill",
        source="skill-pack:profile",
        source_revision="test",
        exposure_mode=ToolExposureMode.DEFERRED,
        skill_locator="plan-bs",
        content_hash="b" * 64,
        description="Brainstorm.",
        metadata={
            "owner_key": "profile",
            "pack_id": "plan-bs",
            "version": "1.0.0",
            "manifest_hash": "a" * 64,
            "scope_hash": scope_payload["scope_hash"],
            "allowed_tools": [],
        },
    )
    exposure = CatalogRunToolExposure(RuntimeToolCatalog((record,), generation=1))
    exposure.restore(RunId("sdk-resource-run"), None)
    authority = SimpleNamespace(
        catalog_fingerprint=exposure.catalog.snapshot.fingerprint,
        specs={},
    )

    class Authorities:
        def resolve(self, run_id):
            assert run_id == "sdk-resource-run"
            return authority

        def resolve_exposure(self, run_id):
            assert run_id == RunId("sdk-resource-run")
            return exposure

    class SnapshotResolver:
        async def resolve_skill_resource(self, scope, relative_path):
            assert scope.skill_id == "plan-bs"
            assert relative_path == "../plan-test/config.md"
            return "skills/plan-test/config.md", b"shared config"

    resolver = SdkThenLegacyFrozenSkillResolver(
        authorities=Authorities(),
        snapshot_resolver=SnapshotResolver(),
        legacy_resolver=SimpleNamespace(),
    )
    result = await resolver.resolve_frozen_resource(
        run_id="sdk-resource-run",
        skill_name="plan-bs",
        relative_path="../plan-test/config.md",
    )

    assert result["content"] == "shared config"
    assert result["resolved_path"] == "skills/plan-test/config.md"
    assert len(result["content_hash"]) == 64
