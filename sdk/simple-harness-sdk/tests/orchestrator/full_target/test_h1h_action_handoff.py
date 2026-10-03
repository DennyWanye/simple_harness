"""H1-H new-protocol actions may not bypass the operation-link handoff gate.

HTN 补齐阶段 A′：全部跑在产品同形世界上（``step07/helpers_step07.py``）。

* 没有操作链接的动作（标记缺失、被删、被伪造）：D′ 世界里经台账写入口递交一条候选、批准后走
  交接入口，按 ``operation_link_missing`` 拒绝。原"标记删除 / 篡改"参数化用例并入第一条（同一个
  世界里依次改坏已存字节，裁决①b1）。
* 桥接身份错：代表用例 3 的变体。系统自己物化出带真实链接的发布动作、人已批准，改坏已存链接的
  字节（①b1）后交接被拒、什么都不预留不发送；恢复原字节后主循环照常发布完成（正对照）。
* O06：代表用例 3 的变体。发布服务写下意图后连接中断（外界事件），台账留下"已放弃"。2026-10-03
  阶段 B 裁决第 3 类之后，登记的发布对账适配器据台账证明"没开始"（权威的否定证明），系统原地
  重交一次，发布成功、只发布一份；人工直接再交接在拿到证明之前仍被拒（``rehandoff_needs_confirmed_not_started``）。
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


def _lose_after_intent_once(publish) -> None:
    real = publish.execute
    calls = {"n": 0}

    def execute(*args, **kwargs):  # the connection drops once the service has written its intent
        calls["n"] += 1
        publish.fail_after = "intent" if calls["n"] == 1 else None
        return real(*args, **kwargs)

    publish.execute = execute


def test_o06_a_publish_lost_after_its_intent_is_proven_not_started_and_sent_again(tmp_path) -> None:
    async def case() -> None:
        async with operation_world(tmp_path, key="h1h-o06", publish_setup=_lose_after_intent_once) as world:
            action = await until_pending(world)
            key = action["action_key"]
            executor = ActionExecutor(world.service, world.connectors, world.deployment, owner="h1h-o06",
                                      source_storage_roots=(tmp_path / "root",))
            request = [a for a in world.control.approvals(world.mission_id) if a.get("state") == "PENDING"][0]
            world.control.decide(request["request_id"], "approve")
            await run_until(world.product, lambda: world.store.get_action(key)["state"] == "SUCCEEDED")
            done = world.store.get_action(key)
            assert done["handoffs"] == 2
            [proof] = [e.payload for e in world.store.list_events(world.mission_id)
                       if e.type == "ActionScopedReconciled" and e.payload["action_key"] == key]
            assert proof["outcome"] == "NOT_APPLIED_FINAL"
            assert world.publish_ledger() == ["PREPARED", "ABORTED", "PREPARED", "COMMITTED"]
            assert len(world.published_files()) == 1
            # a manual re-hand-off of the finished action is refused, the service is not called again
            assert await executor.hand_off(key, rehandoff=True) is None
            assert world.publish_ledger() == ["PREPARED", "ABORTED", "PREPARED", "COMMITTED"]

    asyncio.run(case())
