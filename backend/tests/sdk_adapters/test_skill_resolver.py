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
