# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Decisive tests for the Host code review round 1 (reports/code-review-round1.md).

* P1-2 — a rebuild that fails keeps the driver loop alive and says so (degraded).
* P2-7 — the backoff exponent is capped, so a long failure streak never ends the loop.
* P2-1 — a degraded loop still answers cancel (a person can stop the damage), refuses new
  Missions.
* P2-6 — a payload that is not an object is answered, never raised into the socket loop.
* HA-23 — an artifact is read by its id, bytes checked against the recorded hash.
"""

from __future__ import annotations

import asyncio

import pytest

from deskpet.orchestration.handlers import handle
from deskpet.orchestration.service import (
    OrchestrationRequestError,
    OrchestrationService,
    OrchestrationSettings,
    backoff_delay,
)

from ._support import notes_provider, notes_request

FAST = OrchestrationSettings(
    tick_active_seconds=0.01,
    tick_waiting_seconds=0.01,
    tick_idle_seconds=0.01,
    backoff_max_seconds=0.01,
    rebuild_after_failures=1,
    degraded_after_failures=1,
)


async def _eventually(predicate, seconds: float = 3.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


@pytest.mark.asyncio
async def test_a_failed_rebuild_keeps_the_loop_alive_and_degraded(orchestration_root, principal):
    service = OrchestrationService(orchestration_root, FAST, provider=notes_provider(), principal=principal)
    await service.start()
    runs = {"fail": True, "ok": 0}

    async def run() -> None:
        if runs["fail"]:
            raise RuntimeError("store went away")
        runs["ok"] += 1

    async def rebuild() -> None:
        raise RuntimeError("cannot reopen the library")

    try:
        service._orchestrator.run = run  # noqa: SLF001 - white-box: force the failure path
        service._rebuild = rebuild  # type: ignore[method-assign]  # noqa: SLF001
        service.wake()
        assert await _eventually(lambda: "重建失败" in (service.status()["reason"] or ""))
        await asyncio.sleep(0.1)  # many failed rounds later …
        assert service.status()["state"] == "degraded"
        assert service._driver is not None and not service._driver.done()  # noqa: SLF001 - … still looping
        runs["fail"] = False
        service.wake()
        assert await _eventually(lambda: service.status()["state"] == "available")
        assert runs["ok"] >= 1
    finally:
        await service.close()


def test_the_backoff_delay_is_capped_and_never_overflows():
    assert backoff_delay(1, 60.0) == 2.0
    assert backoff_delay(3, 60.0) == 8.0
    assert backoff_delay(5000, 60.0) == 60.0  # 2.0 ** 5000 would raise OverflowError
    assert backoff_delay(0, 60.0) == 1.0


@pytest.mark.asyncio
async def test_a_long_failure_streak_never_ends_the_loop(orchestration_root, principal):
    """Before the cap, ``2.0 ** 5001`` raised OverflowError outside the loop's try and the
    driver task ended; now it keeps failing, backing off and counting."""

    service = OrchestrationService(orchestration_root, FAST, provider=notes_provider(), principal=principal)
    await service.start()

    async def run() -> None:
        raise RuntimeError("always failing")

    async def rebuild() -> None:
        return None

    try:
        service._failures = 5000  # noqa: SLF001 - far past the float range of 2.0**n
        service._orchestrator.run = run  # noqa: SLF001
        service._rebuild = rebuild  # type: ignore[method-assign]  # noqa: SLF001
        service.wake()
        assert await _eventually(lambda: service._failures >= 5003)  # noqa: SLF001
        assert service._driver is not None and not service._driver.done()  # noqa: SLF001
        assert service.status()["state"] == "degraded"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_degraded_still_cancels_but_refuses_new_missions(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root, OrchestrationSettings(), provider=notes_provider(), principal=principal, drive=False
    )
    await service.start()
    try:
        created = service.create_mission(notes_request("k-degraded"))
        service._state = "degraded"  # noqa: SLF001 - white-box: the loop is failing
        cancelled = await handle(service, "mission_cancel", {"mission_id": created["mission_id"]}, request_id="r-1")
        assert cancelled["payload"]["ok"] is True, cancelled
        assert cancelled["payload"]["data"]["status"] == "CANCELLED"
        refused = await handle(service, "mission_create", notes_request("k-new"), request_id="r-2")
        assert refused["payload"]["ok"] is False
        assert refused["payload"]["error_code"] == "orchestration_degraded"
        listed = await handle(service, "mission_list", {}, request_id="r-3")
        assert listed["payload"]["ok"] is True
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [["mission-1"], "mission-1", 7])
async def test_a_payload_that_is_not_an_object_is_answered(orchestration_root, principal, payload):
    service = OrchestrationService(
        orchestration_root, OrchestrationSettings(), provider=notes_provider(), principal=principal, drive=False
    )
    await service.start()
    try:
        answer = await handle(service, "mission_get", payload, request_id="r-bad")
        assert answer["type"] == "mission_get_response"
        assert answer["payload"] == {
            "request_id": "r-bad",
            "ok": False,
            "error_code": "invalid_request",
            "error": "payload must be an object",
        }
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_an_artifact_is_read_by_id_with_its_hash_checked(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root, OrchestrationSettings(), provider=notes_provider(), principal=principal, drive=False
    )
    await service.start()
    try:
        created = service.create_mission(notes_request("k-artifact"))
        await service.drain()
        detail = service.mission_detail(created["mission_id"])
        notes = [a for a in detail["artifacts"] if a["path"] == "NOTES.md"]
        assert notes, detail["artifacts"]
        read = service.artifact_read(notes[0]["id"])
        assert read["encoding"] == "utf-8" and read["truncated"] is False
        assert read["content"].startswith("# 要点")
        assert read["content_hash"] == notes[0]["content_hash"]
        assert "storage_uri" not in read
        with pytest.raises(OrchestrationRequestError) as missing:
            service.artifact_read("artifact-does-not-exist")
        assert missing.value.code == "not_found"
    finally:
        await service.close()
