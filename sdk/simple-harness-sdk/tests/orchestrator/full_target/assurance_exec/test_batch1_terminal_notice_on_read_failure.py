# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""第 1 批车道 B，A06：终态通知在"保证状态读取失败"时照发，并如实写明。

此前 ``is_assured`` 读错一律返回 None/False，终态写入的通知请求被静默吞掉，用户收不到结束通知
（原计划 §7.3：终态同事务写通知意图）。现在：没有建任务合同 = 不是保证通道任务，不发；合同说是
保证通道、读绑定却失败 = 读取失败，照发，通知请求里写"保证状态读取失败：<码>"。
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from agent_orchestrator.orchestrator.assurance_consumers import AssuranceNotifyConsumer
from agent_orchestrator.orchestrator.assurance_final_writer import (
    NOTIFICATION_EVENT,
    is_assured,
    request_assured_notification,
)
from agent_orchestrator.storage.assurance_work import AssuranceWorkStore

FINAL = SimpleNamespace(id="event-final-1", type="MissionFailed")


@pytest.fixture
def seeded(monkeypatch):
    """第 2 批 A20 起终态写入同事务建 NOTIFY 待办；这里的替身库没有待办表，记下建了什么。"""
    calls: list = []
    monkeypatch.setattr(
        AssuranceWorkStore, "seed",
        lambda self, mission_id, consumer, event, targets, *, now_ms: calls.append(
            (mission_id, consumer, event.id, tuple(t.work_key for t in targets))))
    return calls


def _store(*, contract: str | None, bound: bool):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE assurance_creation_contracts(mission_id TEXT PRIMARY KEY, lane TEXT NOT NULL)")
    connection.execute("CREATE TABLE assurance_mission_bindings(mission_id TEXT PRIMARY KEY)")
    if contract is not None:
        connection.execute("INSERT INTO assurance_creation_contracts VALUES(?,?)", ("m-1", contract))
    if bound:
        connection.execute("INSERT INTO assurance_mission_bindings VALUES(?)", ("m-1",))
    return SimpleNamespace(connection=connection, now=1.0)


def _commit(store):
    emitted = []

    def emit(event_type, mission_id, *, key, payload=None, **_):
        event = SimpleNamespace(type=event_type, mission_id=mission_id, key=key, payload=dict(payload or {}),
                                id="event-notify-1", to_json=lambda: {"type": event_type, "payload": payload})
        emitted.append(event)
        return event

    return SimpleNamespace(_store=store, _emit=emit, emitted=emitted)


def test_a06_no_creation_contract_means_not_assured_and_nothing_is_requested(seeded):
    commit = _commit(_store(contract=None, bound=False))
    assert request_assured_notification(commit, "m-1", FINAL, state_version=3) is None
    assert commit.emitted == [] and seeded == []
    assert is_assured(commit._store, "m-1") is False


def test_a06_an_assured_mission_requests_the_plain_notification(seeded):
    commit = _commit(_store(contract="ASSURANCE_1_1", bound=True))
    event = request_assured_notification(commit, "m-1", FINAL, state_version=3)
    assert event is not None and event.type == NOTIFICATION_EVENT
    assert event.payload == {"final_event_id": FINAL.id, "state_version": 3, "final_event_type": "MissionFailed"}
    assert is_assured(commit._store, "m-1") is True
    # 第 2 批 A20：NOTIFY 待办与这条事件同事务建
    assert seeded == [("m-1", "NOTIFY", event.id, ("notify:m-1:" + FINAL.id,))]


def test_a06_a_failed_assurance_read_still_requests_the_notification_and_says_so(seeded):
    """合同说是保证通道、绑定却读不到（ASSURANCE_PROFILE_UNBOUND）：不静默。"""
    commit = _commit(_store(contract="ASSURANCE_1_1", bound=False))
    event = request_assured_notification(commit, "m-1", FINAL, state_version=5)
    assert event is not None and event.type == NOTIFICATION_EVENT, commit.emitted
    assert event.payload["final_event_id"] == FINAL.id and event.payload["state_version"] == 5
    assert "保证状态读取失败" in event.payload["note"] and "ASSURANCE_PROFILE_UNBOUND" in event.payload["note"]
    assert seeded == []  # 没有绑定行可挂待办：只发事件，待办由游标摄取建
    # NOTIFY 消费者认得这条请求（字段表允许 note），送出的仍只是 {mission_id, event_id, state_version}
    consumer = AssuranceNotifyConsumer(SimpleNamespace(store=None), tenant_id="t", transport=None)
    body = consumer._body(event)
    assert body["final_event_id"] == FINAL.id and body["state_version"] == 5
    [target] = consumer.classify(event)
    assert target.work_key == "notify:m-1:" + FINAL.id
    # 读失败不是"已保证"：别的读者（定稿写入等）仍按不是保证通道处理
    assert is_assured(commit._store, "m-1") is False
