# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 7 · slice B (D7-5 / D7-5'): hand-off re-checks what the approval was bound to and
refuses a ledger row that no longer matches its own hash.

删旧平面模式 第三刀：交接之后的回执 / UNKNOWN / 对账 / 交接上限 / 人工裁决 / 再交接用尽这些条随
平面支删了；只剩交接前就拒绝的两条。HTN 补齐阶段 A′（分诊裁决③ D′）：跑在产品同形世界上
（``helpers_step07.ledger_world``），经台账的交接入口 ``begin_handoff`` 测；交接之后的真实情形见
``test_action_ledger.py::test_the_ledger_never_rewrites_what_reality_did`` 与 h1h 对外操作用例。"""

from __future__ import annotations

import asyncio

import pytest
from helpers_step07 import ALICE, ENABLED, candidate, ledger_world

from agent_orchestrator.governance.policies import DeploymentPolicy

HASH_B = "b" * 64


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _approve(w, action, *, nonce, deployment=ENABLED):
    w.service.decide_approval(action["approval_request_id"], principal=ALICE, decision="grant",
                              nonce=nonce, deployment=deployment)


def _hand_off(w, action, deployment=ENABLED):
    handed, reason = w.service.begin_handoff(action["action_key"], owner="orch-1", lease_seconds=30.0,
                                             connectors=w.connectors, deployment=deployment)
    assert handed is None
    return reason


# ------------------------------------------------------------------ hand-off re-checks
def test_hand_off_rechecks_everything_the_approval_was_bound_to(tmp_path):
    async def case():
        async with ledger_world(tmp_path) as w:
            short = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2",
                                     approval_ttl_seconds=0.6)
            pending = w.propose(candidate(target="a.md"))
            late = w.propose(candidate(target="b.md"), deployment=short)
            _approve(w, late, nonce="n-b", deployment=short)
            v1 = w.propose(candidate(target="c.md"))
            _approve(w, v1, nonce="n-c1")
            w.propose(candidate(target="c.md", value="beta"), artifact_hash=HASH_B)
            live = w.propose(candidate())
            _approve(w, live, nonce="n-live")
            await asyncio.sleep(0.7)  # the short approval on b.md runs out (no decision in between)
            reasons = [
                _hand_off(w, pending),  # not approved
                _hand_off(w, late),  # approved, but the approval ran out
                _hand_off(w, v1),  # the approval was for v1 only
                _hand_off(w, live, DeploymentPolicy()),  # the deployment changed
                _hand_off(w, live),  # every action is linked to the operation it was materialised from
            ]
            assert reasons == [
                "not_ready:AWAITING_APPROVAL",
                "approval_expired",
                "not_ready:SUPERSEDED",
                "connector_not_enabled",
                "operation_link_missing",
            ]
            assert [e["reason"] for e in w.events("ActionHandoffRefused")] == reasons
            assert w.store.get_action(late["action_key"])["state"] == "EXPIRED"
            assert not w.events("ActionHandedOff") and w.publish_ledger() == []
            assert w.control.cancel(w.mission_id)["changed"] is True
            assert _hand_off(w, live) == "mission_not_active"

    asyncio.run(case())


def test_altered_stored_parameters_are_refused_at_hand_off(tmp_path):
    """The ledger row's bytes are altered on disk (adjudication ①b1): the stored parameters
    no longer match the row's own hash, and the hand-off refuses before anything else."""

    async def case():
        async with ledger_world(tmp_path) as w:
            action = w.propose(candidate())
            _approve(w, action, nonce="n-1")
            with w.store.transaction() as connection:
                connection.execute(
                    "UPDATE actions SET json = json_set(json, '$.params.content_hash', ?) WHERE action_key = ?",
                    ("e" * 64, action["action_key"]))
            assert _hand_off(w, action) == "params_hash_mismatch"
            assert w.events("ActionHandoffRefused")[-1]["reason"] == "params_hash_mismatch"
            assert w.store.get_action(action["action_key"])["handoffs"] == 0
            assert w.publish_ledger() == [] and w.published_files() == []

    asyncio.run(case())
