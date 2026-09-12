# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-2: the Host orchestration service's assembly, failure isolation and shutdown.

Draft written before the implementation (plan 2026-09-11 H2).
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest
from context import _VALID_SERVICES
from deskpet.orchestration.paths import OrchestrationPathError, orchestration_root
from deskpet.orchestration.service import (
    OrchestrationRequestError,
    OrchestrationService,
    OrchestrationSettings,
)

from ._support import notes_provider, notes_request


def test_service_name_is_registered():
    assert "orchestration" in _VALID_SERVICES


def test_settings_default_on_and_conservative():
    settings = OrchestrationSettings()
    assert settings.enabled is True  # CLAUDE.md: tested capabilities ship on
    assert settings.max_concurrency == 1
    assert settings.max_concurrent_model_calls == 1
    # plan v3 (P3.1 §3.1): no switch for running model-written code on this machine
    assert not hasattr(settings, "allow_local_tests")


def test_root_lives_beside_but_apart_from_the_sdk_execution_library(tmp_path):
    root = orchestration_root(tmp_path)
    assert root == tmp_path / "data" / "agent-orchestrator"
    assert "simple-harness-sdk" not in root.relative_to(tmp_path).parts


def test_root_refuses_a_symlink(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "data").mkdir()
    os.symlink(tmp_path / "elsewhere", tmp_path / "data" / "agent-orchestrator")
    with pytest.raises(OrchestrationPathError):
        orchestration_root(tmp_path)


@pytest.mark.asyncio
async def test_start_creates_the_library_and_reports_available(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root, OrchestrationSettings(), provider=notes_provider(), principal=principal
    )
    await service.start()
    try:
        status = service.status()
        assert status["available"] is True and status["state"] == "available"
        assert (orchestration_root / "orchestrator.db").is_file()
        import agent_orchestrator

        assert status["orchestrator_version"] == agent_orchestrator.__version__
        assert status["deployment_manifest"]["distributions"]["consistent"] is True
        assert (orchestration_root / "deployment-manifest.json").is_file()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_start_failure_is_isolated(tmp_path, principal):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    service = OrchestrationService(
        blocker, OrchestrationSettings(), provider=notes_provider(), principal=principal
    )
    await service.start()  # never raises into the Host lifespan
    status = service.status()
    assert status["available"] is False
    assert status["reason"]
    with pytest.raises(OrchestrationRequestError) as refused:
        service.create_mission(notes_request("k-down"))
    assert refused.value.code == "orchestration_unavailable"
    await service.close()


@pytest.mark.asyncio
async def test_no_model_configured_is_unavailable_not_a_crash(orchestration_root, principal):
    """HA-17: a fresh install has no provider chain; the chat still starts."""

    service = OrchestrationService(
        orchestration_root, OrchestrationSettings(), provider=None, principal=principal
    )
    await service.start()
    try:
        status = service.status()
        assert status["available"] is False and "未配置模型" in status["reason"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_each_process_has_its_own_owner(orchestration_root, principal):
    """Plan review P0-2: owner = deskpet-orchestrator-<pid>-<random>, never shared."""

    service = OrchestrationService(
        orchestration_root, OrchestrationSettings(), provider=notes_provider(), principal=principal
    )
    await service.start()
    try:
        owner = service.owner
        assert owner.startswith(f"deskpet-orchestrator-{os.getpid()}-")
        assert len(owner.rsplit("-", 1)[-1]) >= 8
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_driver_loop_runs_a_mission_without_being_asked(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(tick_active_seconds=0.2, tick_idle_seconds=0.5),
        provider=notes_provider(),
        principal=principal,
    )
    await service.start()
    try:
        created = service.create_mission(notes_request("k-drive"))
        deadline = time.monotonic() + 20
        status = None
        while time.monotonic() < deadline:
            status = service.mission_detail(created["mission_id"])["mission"]["status"]
            if status == "COMPLETED":
                break
            await asyncio.sleep(0.1)
        assert status == "COMPLETED"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_close_leaves_no_task_behind(orchestration_root, principal):
    before = {t for t in asyncio.all_tasks() if not t.done()}
    service = OrchestrationService(
        orchestration_root, OrchestrationSettings(), provider=notes_provider(), principal=principal
    )
    await service.start()
    await service.close()
    after = {t for t in asyncio.all_tasks() if not t.done()} - before
    assert after <= {asyncio.current_task()}
    assert service.status()["state"] == "closed"
