# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P2 runtime closure: ``trigger_local`` must reach the model's tool receipt.

Oracle source: run-01j review §3.2 — the projection unit tests passed while
C04-12/13/19/20 saw no ``trigger_local`` at all, because those tests fed a
hand-written payload carrying ``memory_type`` and the real SDK public payload
of a prospective memory carries only ``action``/``trigger``. Nothing but a
runtime test that seeds a real reminder and reads the real ``context_route``
tool return can close that gap, so this file does exactly that: real corpus
C04 fixture, real settled scheduler registration, real typed recall, real
``ContextRouteToolService``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
from deskpet.quality.corpus_prospective import settle_prospective_registrations
from tests.quality.test_corpus_prospective_settlement import CASE, _seeded_case
from tests.sdk_adapters.test_context_route_tool import _service, state_db  # noqa: F401

# corpus_c04.SPECS['C04-01'] P anchor: 2026-09-07T09:00 Asia/Shanghai, a Monday.
EXPECTED_LOCAL = "2026-09-07T09:00+08:00 周一"


async def _recalled_fragments(tmp_path: Path, route_state_db: Path, query: str):
    host, memory_path, manager, principal, lane, reminder = await _seeded_case(tmp_path)
    try:
        settlement = await settle_prospective_registrations(
            lane=lane, manager=manager, path=host.path, principal=principal)
        assert settlement["registered"] == [reminder.memory_id], settlement
        runtime = HumanMemoryV7Runtime(memory_path, principal=principal,
                                       clock=lambda: _SCENARIO)
        # The seeded manager is already open on this store; typed_recall runs
        # the production SDK path over it rather than a second connection.
        runtime._manager = manager
        tool = _service(route_state_db)
        tool._recall_executor = runtime.typed_recall
        result = await tool.handle_context_route(
            {"route": "memory_standalone", "query": query,
             "memory_types": ["prospective"]})
        assert "context_route_receipt" in result, result
        return result
    finally:
        await lane.close()
        await manager.close()


_SCENARIO = datetime(2026, 9, 6, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()


@pytest.mark.asyncio
async def test_tool_receipt_fragments_carry_the_host_rendered_trigger_local(
    tmp_path: Path, state_db: Path  # noqa: F811
) -> None:
    result = await _recalled_fragments(tmp_path, state_db, "索取修正版")
    prospective = [f for f in result["fragments"] if f["memory_type"] == "prospective"]
    assert prospective, result["fragments"]
    fragment = prospective[0]
    # The failure run-01j actually observed: the key was simply absent from the
    # tool value the model reads.
    assert "trigger_local" in fragment, fragment
    assert fragment["trigger_local"] == EXPECTED_LOCAL
    # Rendered in the trigger's own zone, not the host's and not UTC.
    trigger = fragment["payload"]["trigger"]
    assert trigger["timezone"] == "Asia/Shanghai"
    rendered = datetime.fromisoformat(fragment["trigger_local"].split(" ")[0])
    assert rendered.timestamp() == trigger["trigger_at"]
    assert rendered.utcoffset() == ZoneInfo(trigger["timezone"]).utcoffset(rendered)
    # Beside, never inside: the SDK payload and its hash stay byte-identical.
    assert "trigger_local" not in fragment["payload"]
    assert set(fragment["payload"]) == {"action", "trigger"}
    assert len(fragment["payload_hash"]) == 64


@pytest.mark.asyncio
async def test_case_id_is_the_one_whose_anchor_this_file_asserts() -> None:
    """Guard the constant above against a corpus edit moving the anchor."""

    from deskpet.quality.corpus_c04 import SPECS

    assert CASE == "C04-01"
    label, kind, _, local, zone, precision, _ = SPECS[CASE][1]
    assert (label, kind, precision) == ("P", "prospective", "minute")
    assert datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(zone)).isoformat(
        timespec="minutes") == EXPECTED_LOCAL.split(" ")[0]
