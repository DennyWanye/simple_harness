"""H1-H new-protocol actions may not bypass the operation-link handoff gate.

HTN 补齐阶段 A′：全部跑在产品同形世界上（``step07/helpers_step07.py``）。

* 没有操作链接的动作（标记缺失、被删、被伪造）：D′ 世界里经台账写入口递交一条候选、批准后走
  交接入口，按 ``operation_link_missing`` 拒绝。原"标记删除 / 篡改"参数化用例并入第一条（同一个
  世界里依次改坏已存字节，裁决①b1）。
* 桥接身份错：代表用例 3 的变体。系统自己物化出带真实链接的发布动作、人已批准，改坏已存链接的
  字节（①b1）后交接被拒、什么都不预留不发送；恢复原字节后主循环照常发布完成（正对照）。
* O06：代表用例 3 的变体。发布服务写下意图后连接中断（外界事件），动作结果不明；对账只拿到空的
  查询结果（弱证据），系统不再交接、预留不释放、闸门判"未了结"、等人裁决。原用例直接改写台账行
  造出"已确认未开始 + 弱证明"的状态（违反①b2），产品上对带链接的动作从不由空查询记"已确认未开始"，
  所以"弱证明再交接"那一支（``rehandoff_needs_authoritative_not_applied_proof``）在真实路径上走不到，
  改为断言再交接在更早一道就被拒（``rehandoff_needs_confirmed_not_started``）且发布服务没被再调用。
"""
# ruff: noqa: E402, E501 -- shared step07 fixture path is installed before imports.

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_STEP07 = Path(__file__).resolve().parent.parent / "step07"
if str(_STEP07) not in sys.path:
    sys.path.insert(0, str(_STEP07))

from helpers_step07 import (
    ALICE,
    ENABLED,
    candidate,
    ledger_world,
    operation_world,
    run_until,
    until_pending,
)

from agent_orchestrator.runtime.actions import ActionExecutor
from agent_orchestrator.runtime.planning_operations import (
    OperationEffect,
    SourceUnavailable,
    StoreOperationReader,
    build_operation_snapshot,
    operation_gate,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _handoff(world, action_key):
    return world.service.begin_handoff(
        action_key,
        owner="h1h-handoff-owner",
        lease_seconds=30.0,
        connectors=world.connectors,
        deployment=world.deployment,
    )


def _assert_not_handed_off(world, action_key: str, state: str) -> None:
    stored = world.store.get_action(action_key)
    assert stored is not None
    assert stored["state"] == state
    assert stored["handoffs"] == 0
    assert world.service.ledger.reservation(f"action:{action_key}") is None
    assert not [e for e in world.events("ActionHandedOff") if e["action_key"] == action_key]
    assert world.publish_ledger() == [] and world.published_files() == []


def test_new_protocol_missing_marker_and_link_refuses_without_handoff_side_effect(tmp_path) -> None:
    async def case() -> None:
        async with ledger_world(tmp_path, key="h1h-no-link") as world:
            action = world.propose(candidate())
            world.service.decide_approval(action["approval_request_id"], principal=ALICE, decision="grant",
                                          nonce="n-1", deployment=ENABLED)
            key = action["action_key"]
            assert "planning_origin" not in world.store.get_action(key)
            for marker in (None, '{"operation_id": "forged"}'):  # no marker, then a forged one on disk
                if marker is not None:
                    with world.store.transaction() as connection:
                        connection.execute(
                            "UPDATE actions SET json = json_set(json, '$.planning_origin', json(?)) WHERE action_key = ?",
                            (marker, key))
                handed, reason = _handoff(world, key)
                assert handed is None
                assert reason == "operation_link_missing"
                _assert_not_handed_off(world, key, "APPROVED")

    asyncio.run(case())


def test_new_protocol_bad_bridge_identity_refuses_handoff(tmp_path) -> None:
    async def case() -> None:
        async with operation_world(tmp_path, key="h1h-bad-bridge") as world:
            action = await until_pending(world)
            key = action["action_key"]
            request = [a for a in world.control.approvals(world.mission_id) if a.get("state") == "PENDING"][0]
            assert world.control.decide(request["request_id"], "approve")["request_state"] == "GRANTED"
            [original] = world.store.connection.execute(
                "SELECT link_json FROM planning_operation_action_links WHERE action_key = ?", (key,)).fetchone()
            for path, value in (("$.mission_id", "foreign-mission"), ("$.action_id", "wrong-action-id"),
                                ("$.envelope_hash", "f" * 64)):
                with world.store.transaction() as connection:  # the stored bridge bytes are altered (①b1)
                    connection.execute(
                        "UPDATE planning_operation_action_links SET link_json = json_set(link_json, ?, ?) "
                        "WHERE action_key = ?", (path, value, key))
                handed, reason = _handoff(world, key)
                assert handed is None, path
                assert reason in {"operation_link_missing", "operation_link_mismatch"}, (path, reason)
                _assert_not_handed_off(world, key, "APPROVED")
                with world.store.transaction() as connection:
                    connection.execute(
                        "UPDATE planning_operation_action_links SET link_json = ? WHERE action_key = ?",
                        (original, key))
            # with its exact bytes back, the same action goes out once through the main loop
            await run_until(world.product, lambda: world.store.get_action(key)["state"] == "SUCCEEDED")
            assert world.publish_ledger().count("PREPARED") == 1 and len(world.published_files()) == 1

    asyncio.run(case())


def _lose_after_intent(publish) -> None:
    publish.fail_after = "intent"  # the connection drops once the service has written its intent


async def _lost(world):
    """Approve the system's publish action and run until the loop's own reconciliation has
    looked once and found only an empty lookup."""
    action = await until_pending(world)
    key = action["action_key"]
    request = [a for a in world.control.approvals(world.mission_id) if a.get("state") == "PENDING"][0]
    world.control.decide(request["request_id"], "approve")
    await run_until(world.product, lambda: world.store.get_action(key).get("reconcile") == "STILL_UNKNOWN")
    stored = world.store.get_action(key)
    assert (stored["state"], stored["handoffs"]) == ("UNKNOWN", 1)
    assert "reconciliation_proof" not in stored
    # the service wrote its intent, then gave up before the only commit point: nothing published
    assert world.publish_ledger() == ["PREPARED", "ABORTED"] and world.published_files() == []
    reservation = world.service.ledger.reservation(f"action:{key}")
    assert reservation is not None and reservation["state"] == "RESERVED"
    return key, reservation


def test_o06_executor_weak_reconcile_rehandoff_cannot_call_connector(tmp_path) -> None:
    async def case() -> None:
        async with operation_world(tmp_path, key="h1h-o06-weak", publish_setup=_lose_after_intent) as world:
            key, reservation = await _lost(world)
            executor = ActionExecutor(world.service, world.connectors, world.deployment, owner="h1h-o06",
                                      source_storage_roots=(tmp_path / "root",))
            assert await executor.hand_off(key, rehandoff=True) is None
            assert executor.last_refusal[key] == "rehandoff_needs_confirmed_not_started"
            reconciled = await executor.reconcile_one(key, allow_rehandoff=True)  # weak evidence again
            assert reconciled is not None and reconciled["reconcile"] == "STILL_UNKNOWN"
            after = world.store.get_action(key)
            assert (after["state"], after["handoffs"]) == ("UNKNOWN", 1)
            assert world.service.ledger.reservation(f"action:{key}") == reservation
            assert world.publish_ledger() == ["PREPARED", "ABORTED"]  # the service was not called again
            assert world.published_files() == []

    asyncio.run(case())


def test_o06_executor_empty_lookup_rehandoff_keeps_new_protocol_hold(tmp_path) -> None:
    async def case() -> None:
        async with operation_world(tmp_path, key="h1h-o06-empty", publish_setup=_lose_after_intent) as world:
            key, reservation = await _lost(world)
            # more rounds of the product's own loop: still no hand-off, the hold is kept
            for _ in range(3):
                await world.product.drain(timeout=5.0)
            after = world.store.get_action(key)
            assert (after["state"], after["handoffs"], after["reconcile"]) == ("UNKNOWN", 1, "STILL_UNKNOWN")
            assert "reconciliation_proof" not in after
            assert world.service.ledger.reservation(f"action:{key}") == reservation
            assert world.publish_ledger() == ["PREPARED", "ABORTED"] and world.published_files() == []
            assert [(w["kind"], w["needs_human"]) for w in world.store.waiting_on(world.mission_id)] == [
                ("reconciliation", True)]  # a person rules on it
            snapshot = build_operation_snapshot(world.mission_id, reader=StoreOperationReader(world.store))
            assert [effect for _operation, effect in snapshot.effects] == [OperationEffect.UNRESOLVED]
            with pytest.raises(SourceUnavailable, match="operation_unresolved"):
                operation_gate(snapshot)

    asyncio.run(case())
