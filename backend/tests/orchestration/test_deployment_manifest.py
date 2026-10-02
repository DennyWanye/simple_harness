# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-24 / P3.1-A08: the deployment manifest proves what the running process imported.

Review round 1: P2-9 (a dirty working tree must be visible next to ``host_commit``) and
P2-2 (a version that differs from the pin is refused *before* the library is opened, so a
wrong SDK never migrates the orchestration database).
"""

from __future__ import annotations

import json

import pytest

import deskpet.orchestration.service as service_module
from deskpet.orchestration.manifest import MANIFEST_NAME, MANIFEST_SCHEMA
from ._word_counter import FixtureWordCounter
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings
from deskpet.sdk_adapters.sdk_candidate import SDK_VERSION, SDK_WHEEL_SHA256

from ._support import notes_provider


@pytest.mark.asyncio
async def test_manifest_records_the_imported_sdk_and_the_fixed_policy(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root, OrchestrationSettings(), provider=notes_provider(), principal=principal, drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await service.start()
    try:
        manifest = json.loads((orchestration_root / MANIFEST_NAME).read_text(encoding="utf-8"))
        assert manifest == service.status()["deployment_manifest"]
        assert manifest["schema"] == MANIFEST_SCHEMA
        assert "host_commit" in manifest and "host_dirty" in manifest
        assert manifest["host_dirty"] in (True, False, None)
        dists = manifest["distributions"]
        assert dists["consistent"] is True
        assert dists["simple_harness"]["version"] == SDK_VERSION
        assert dists["pin"] == {"version": SDK_VERSION, "wheel_sha256": SDK_WHEEL_SHA256}
        policy = manifest["features"]["deployment_policy"]
        # P3.2 (plan D9): the probe decides — sandboxed when it passed here, off otherwise,
        # and never process_only (an unisolated child process is for trusted code only)
        sandbox = manifest["features"]["sandbox"]
        assert policy["code_execution"] == ("sandboxed" if sandbox.get("ok") else "off")
        assert policy["local_code_execution"] is bool(sandbox.get("ok"))
        assert ("run_tests" in policy["allowed_tools"]) is bool(sandbox.get("ok"))
        if not sandbox.get("ok"):
            assert sandbox.get("reason") or sandbox.get("items")  # never a silent off
        assert policy["max_action_level"] == "L2"
        assert manifest["model"] is None  # a scripted provider: no real model, no key
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_a_version_that_differs_from_the_pin_never_opens_the_library(
    orchestration_root, principal, monkeypatch
):
    real = service_module.distributions

    def mismatched():  # type: ignore[no-untyped-def]
        imported = real()
        return {**imported, "consistent": False}

    monkeypatch.setattr(service_module, "distributions", mismatched)
    service = OrchestrationService(
        orchestration_root, OrchestrationSettings(), provider=notes_provider(), principal=principal, drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await service.start()
    try:
        status = service.status()
        assert status["available"] is False and status["state"] == "unavailable"
        assert "钉版不一致" in (status["reason"] or "")
        assert not (orchestration_root / "orchestrator.db").exists()  # nothing was migrated
        manifest = json.loads((orchestration_root / MANIFEST_NAME).read_text(encoding="utf-8"))
        assert manifest["refused"] == "pin_mismatch"
        assert manifest["distributions"]["consistent"] is False
    finally:
        await service.close()
