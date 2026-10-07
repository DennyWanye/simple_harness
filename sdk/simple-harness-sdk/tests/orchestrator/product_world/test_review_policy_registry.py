# SPDX-License-Identifier: Apache-2.0
"""审阅员政策与上下文政策的钉住有登记读者（推后第 2 批 A18；原计划 review-binding-v2 的
``reviewer_policy_ref`` / ``context_policy_ref``，``ref-resolution-map`` "registered reviewer policy /
Context policy exact reader"）。

审阅开出之前，运行时把两种政策正文各登记成一条不可变回执；绑定里钉的就是这条回执的精确引用。
审计拿绑定里的钉住，用登记读者取回当时那一版政策全文；交接门也只读登记正文，再与当前部署比。

**改坏检验**（记录见车道 Q2 记录）：交接门不读登记正文 → 交接支变绿为红。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.assurance.codec import AssuranceError, decode
from agent_orchestrator.assurance.refs import Pin
from agent_orchestrator.orchestrator import assurance_review_policies as policies
from agent_orchestrator.storage.store import InjectedCrash
from agent_orchestrator.testing.product_world import TENANT, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


def _bindings(store: Any, mission_id: str) -> list[dict[str, Any]]:
    return [decode(row[0]) for row in store.connection.execute(
        "SELECT binding_json FROM assurance_review_bindings WHERE mission_id=? ORDER BY rowid", (mission_id,))]


def _profile_of(store: Any, review_key: str) -> str:
    [row] = store.connection.execute(
        "SELECT i.config_json FROM assurance_review_invocations v JOIN dispatch_intents i "
        "ON i.intent_id=v.dispatch_intent_id WHERE v.review_key=? AND v.ordinal=1", (review_key,)).fetchall()
    return json.loads(row[0])["agent_config"]["model_profile_ref"]


def _read(store: Any, mission_id: str, pin: dict[str, Any], kind: str) -> dict[str, Any]:
    with store.read_view():
        return policies.read_registered_policy(store, tenant_id=TENANT, mission_id=mission_id,
                                               pin=Pin.from_json(pin), kind=kind)


async def _complete(world: Any, key: str) -> str:
    mission_id = world.create({"goal": "写两份笔记", "idempotency_key": key,
                               "success_criteria": ["file:notes/a.md", "file:notes/b.md"]})["mission_id"]
    mission = await world.run_until_settled(mission_id, rounds=40)
    assert str(mission.status.value) == "COMPLETED", mission.final_report
    return mission_id


def test_every_review_binding_pins_registered_policies_on_the_default_path(tmp_path):
    """默认路径：每个审阅绑定的两个政策钉住都是一条登记回执的精确引用；登记读者取回的正文就是
    当时部署的审阅员政策（指令、编解码、工具）与那个执行池的上下文政策。"""

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            store = world.store
            mission_id = await _complete(world, "policy-registry-default")
            bindings = _bindings(store, mission_id)
            assert bindings
            for body in bindings:
                assert body["reviewer_policy_ref"]["revision"] == 0
                reviewer = _read(store, mission_id, body["reviewer_policy_ref"], policies.REVIEWER_POLICY_KIND)
                assert reviewer == policies.reviewer_policy_body()
                profile = _profile_of(store, body["review_key"])
                context = _read(store, mission_id, body["context_policy_ref"], policies.CONTEXT_POLICY_KIND)
                assert context == policies.context_policy_body(world.loop, profile)
                # 同一钉住换一种政策读：写者种类不符，拒绝
                with pytest.raises(AssuranceError) as wrong:
                    _read(store, mission_id, body["reviewer_policy_ref"], policies.CONTEXT_POLICY_KIND)
                assert wrong.value.code == "REF_ISSUER_MISMATCH"

    asyncio.run(case())


def test_a_changed_policy_is_a_new_registration_and_the_old_pin_still_reads_back(tmp_path):
    """政策正文变了就是新的一条登记；旧绑定仍读回旧正文（审计说得清按哪版审）。登记回执被改写，
    读者按哈希不符拒绝；钉住指向不存在的登记，按来源不可用拒绝。"""

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            store = world.store
            mission_id = await _complete(world, "policy-registry-change")
            old_pin = _bindings(store, mission_id)[0]["reviewer_policy_ref"]
            changed = {**policies.reviewer_policy_body(), "instructions_version": "changed-for-test"}
            with store.transaction():
                new_pin = policies.register_policy_locked(store, mission_id=mission_id,
                                                          kind=policies.REVIEWER_POLICY_KIND, body=changed)
                again = policies.register_policy_locked(store, mission_id=mission_id,
                                                        kind=policies.REVIEWER_POLICY_KIND, body=changed)
            assert new_pin == again and new_pin.to_json() != old_pin
            assert _read(store, mission_id, new_pin.to_json(), policies.REVIEWER_POLICY_KIND) == changed
            assert _read(store, mission_id, old_pin, policies.REVIEWER_POLICY_KIND) == policies.reviewer_policy_body()

            with pytest.raises(AssuranceError) as missing:
                _read(store, mission_id, {**old_pin, "id": old_pin["id"] + "-gone"}, policies.REVIEWER_POLICY_KIND)
            assert missing.value.code == "SOURCE_UNAVAILABLE"
            store.connection.execute("DROP TRIGGER commit_receipts_immutable_update")
            store.connection.execute(
                "UPDATE commit_receipts SET receipt_json=json_set(receipt_json,'$.policy.instructions','tampered') "
                "WHERE commit_id=?", (old_pin["id"],))
            with pytest.raises(AssuranceError) as tampered:
                _read(store, mission_id, old_pin, policies.REVIEWER_POLICY_KIND)
            assert tampered.value.code == "REF_BODY_CONFLICT"

    asyncio.run(case())


def test_the_handoff_reads_the_registered_policy_and_refuses_another_deployment(tmp_path, monkeypatch):
    """一个在途审阅（执行侧刚建好就断了，意图还在途）：交接门按绑定钉住读登记正文，与当前部署一致 → 放行；
    部署的审阅员政策换了（绑定仍钉旧版）→ 不交，``REVIEW_DEPLOYMENT_IDENTITY_MISMATCH``。"""
    from agent_orchestrator.orchestrator.assurance_review_transport import require_review_handoff

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            store = world.store
            world.loop.arm_fault("after_agent_created", kind="critic")
            mission_id = world.create({"goal": "写两份笔记", "idempotency_key": "policy-registry-handoff",
                                       "success_criteria": ["file:notes/a.md", "file:notes/b.md"]})["mission_id"]
            for _ in range(40):
                try:
                    await world.drain(timeout=10)
                except InjectedCrash:
                    break
                if "after_agent_created:critic" in store.fired:
                    break
            assert "after_agent_created:critic" in store.fired
            [intent] = [intent for intent in store.list_intents("CLAIMED", "AGENT_CREATED", "SUBMITTED")
                        if intent.mission_id == mission_id and intent.config.get("review_key")]
            require_review_handoff(world.loop.commit, intent)

            changed = {**policies.reviewer_policy_body(), "instructions_version": "another-deployment"}
            monkeypatch.setattr(policies, "reviewer_policy_body", lambda: changed)
            with pytest.raises(AssuranceError) as refused:
                require_review_handoff(world.loop.commit, intent)
            assert refused.value.code == "REVIEW_DEPLOYMENT_IDENTITY_MISMATCH"

    asyncio.run(case())
