# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-4: what the UI sees — a whitelisted projection, gap-free event paging, change pushes.

Draft written before the implementation (plan 2026-09-11 H3).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from deskpet.orchestration.projection import MISSION_FIELDS
from deskpet.orchestration.pump import MissionChangePump
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import notes_provider, notes_request


async def _completed_service(root, principal):  # type: ignore[no-untyped-def]
    service = OrchestrationService(
        root, OrchestrationSettings(), provider=notes_provider(), principal=principal, drive=False
    )
    await service.start()
    created = service.create_mission(notes_request("k-proj"))
    await service.drain()
    return service, created["mission_id"]


@pytest.mark.asyncio
async def test_detail_is_whitelisted_and_marks_model_text(orchestration_root, principal):
    service, mission_id = await _completed_service(orchestration_root, principal)
    try:
        detail = service.mission_detail(mission_id)
        assert set(detail["mission"]) <= set(MISSION_FIELDS)
        summaries = [r["summary"] for r in detail["results"]]
        assert summaries and all(s["source"] == "model" for s in summaries)
        layers = [layer for r in detail["results"] for layer in r["verification_layers"]]
        assert {"format_check", "rule_check"} <= {layer["layer"] for layer in layers}
        # NOT_REQUIRED stays NOT_REQUIRED (original §14.1) — never shown as a pass
        assert all(
            layer["status"] != "PASS" for layer in layers if layer["layer"] == "code_test"
        )
        assert detail["usage"]["amount_micros"] is None  # unpriced, not zero
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_event_paging_is_gap_free(orchestration_root, principal):
    service, mission_id = await _completed_service(orchestration_root, principal)
    try:
        seen: list[int] = []
        after = 0
        while True:
            page = service.events(mission_id, after_seq=after, limit=3)
            seen += [e["seq"] for e in page["events"]]
            after = page["last_seq"]
            if not page["has_more"]:
                break
        assert seen == sorted(set(seen))
        assert len(seen) == service.mission_detail(mission_id)["event_count"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_pump_announces_a_status_change_within_two_seconds(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(tick_active_seconds=0.05, tick_idle_seconds=0.2),
        provider=notes_provider(),
        principal=principal,
    )
    await service.start()
    pushed: list[tuple[float, dict]] = []

    async def broadcast(message: dict) -> None:
        pushed.append((time.monotonic(), message))

    pump = MissionChangePump(service, broadcast, interval=0.1)
    pump.start()
    try:
        created = service.create_mission(notes_request("k-pump"))
        deadline = time.monotonic() + 20
        completed_at = None
        while time.monotonic() < deadline and completed_at is None:
            if service.mission_detail(created["mission_id"])["mission"]["status"] == "COMPLETED":
                completed_at = time.monotonic()
            await asyncio.sleep(0.02)
        assert completed_at is not None
        await asyncio.sleep(2.0)
        done = [
            at
            for at, message in pushed
            if message["type"] == "mission_changed"
            and message["payload"]["mission_id"] == created["mission_id"]
            and message["payload"]["status"] == "COMPLETED"
        ]
        assert done and done[0] - completed_at <= 2.0
        assert all("sk-" not in str(message) for _, message in pushed)
    finally:
        await pump.stop()
        await service.close()
