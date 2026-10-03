# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 7 · slice C (D7-4' / D7-7'): the waiting view, the time a person took, and open
work that ends with the Mission.

HTN 补齐阶段 A′（分诊裁决③ D′）：跑在产品同形世界上（``helpers_step07.ledger_world``）；库的时钟
就是真实时间，人等待的时长按库里记下的开合时刻核对。"""

from __future__ import annotations

import asyncio

import pytest
from helpers_step07 import ALICE, ENABLED, candidate, ledger_world


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_human_wait_is_the_union_of_the_open_request_spans(tmp_path):
    async def case():
        async with ledger_world(tmp_path) as w:
            a = w.propose(candidate(target="a.md"))
            await asyncio.sleep(0.05)
            b = w.propose(candidate(target="b.md"))
            await asyncio.sleep(0.05)
            w.service.decide_approval(a["approval_request_id"], principal=ALICE, decision="grant",
                                      nonce="n-a", deployment=ENABLED)
            first = w.store.get_approval(a["approval_request_id"])
            second = w.store.get_approval(b["approval_request_id"])
            # a was open [created, closed]; b opened inside it and is still open
            assert first["created_at"] < second["created_at"] < first["closed_at"]
            now = second["created_at"] + 10.0
            assert w.store.human_wait_seconds(w.mission_id, now) == pytest.approx(now - first["created_at"])
            waiting = w.store.waiting_on(w.mission_id)
            assert [item["kind"] for item in waiting] == ["action"]
            assert waiting[0]["subject"] == b["action_key"] and waiting[0]["subject"].endswith(":v1")

    asyncio.run(case())


def test_an_ending_mission_cancels_its_open_actions_but_not_what_was_handed_off(tmp_path):
    async def case():
        async with ledger_world(tmp_path) as w:
            open_action = w.propose(candidate(target="a.md"))
            assert w.control.cancel(w.mission_id)["changed"] is True  # the person cancels the Mission
            assert w.store.get_action(open_action["action_key"])["state"] == "CANCELLED"
            request = w.store.get_approval(open_action["approval_request_id"])
            assert request["state"] == "CANCELLED" and request["closed_at"] is not None
            assert w.store.waiting_on(w.mission_id) == []

    asyncio.run(case())
