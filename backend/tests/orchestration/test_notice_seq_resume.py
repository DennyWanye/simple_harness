# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""第 2 批 U04：Host 断线后按序号续读通知（Assurance 原计划 §7.3 "Host 按 event_id 去重、重连从 seq 补读"）。

此前 Host 只在启动 / 重建运行时整表补读一次（``backfill``），运行中掉了一条推送就要等下次重启。现在：

* Host 记住已读到的事件序号 ``last_seq``（随通知记录一起落盘）；
* ``catch_up`` 只读 ``seq > last_seq`` 的终态通知事件，启动、重建、以及每次前端（重连后）来拉列表时都续读一次；
* 记录文件丢了 → ``last_seq`` 回到 0 → 整表补读（原有保证不变）。

**改坏检验**：``last_seq`` 不落盘（``_save`` 里去掉它）→ 第一条变红。
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from deskpet.orchestration.notices import NOTICES_FILE, NOTIFIED_EVENT, MissionNotices


def _store() -> SimpleNamespace:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE,"
                       " type TEXT, payload_json TEXT, created_at REAL)")
    return SimpleNamespace(connection=connection)


def _notified(store: SimpleNamespace, event_id: str, mission_id: str, *, at: float, kind: str = NOTIFIED_EVENT) -> None:
    store.connection.execute("INSERT INTO events(event_id, type, payload_json, created_at) VALUES (?,?,?,?)",
                             (event_id, kind, json.dumps({"event_id": event_id, "mission_id": mission_id, "state_version": 2}), at))


def test_catch_up_remembers_the_sequence_number_and_resumes_from_it(tmp_path):
    store = _store()
    _notified(store, "ev-1", "m-1", at=1.0)
    _notified(store, "ev-2", "m-2", at=2.0)
    _notified(store, "x-3", "m-2", at=2.5, kind="MissionCompleted")  # 别的事件也占序号，但不是通知
    notices = MissionNotices(tmp_path, clock=lambda: 10.0)
    assert notices.catch_up(store) == 2
    assert notices.last_seq == 3  # 读到了表尾（含非通知事件的序号）
    assert [row["notice_id"] for row in notices.pending()] == ["ev-1", "ev-2"]
    saved = json.loads((tmp_path / NOTICES_FILE).read_text(encoding="utf-8"))
    assert saved["last_seq"] == 3

    # 断线期间又来了一条；重连后续读只拿新的，已读的不重读
    _notified(store, "ev-4", "m-3", at=4.0)
    again = MissionNotices(tmp_path, clock=lambda: 11.0)  # 重启：从文件里的序号接着读
    assert again.last_seq == 3
    assert again.catch_up(store) == 1
    assert again.last_seq == 4
    assert [row["notice_id"] for row in again.pending()] == ["ev-1", "ev-2", "ev-4"]
    assert again.catch_up(store) == 0  # 没有新事件就什么都不写


def test_a_live_push_and_the_later_catch_up_of_the_same_event_make_one_notice(tmp_path):
    store = _store()
    notices = MissionNotices(tmp_path, clock=lambda: 10.0)
    notices.record({"event_id": "ev-1", "mission_id": "m-1", "state_version": 2})  # 运行中推送先到
    _notified(store, "ev-1", "m-1", at=1.0)
    assert notices.catch_up(store) == 0  # 按 event_id 去重
    assert notices.last_seq == 1
    assert [row["notice_id"] for row in notices.pending()] == ["ev-1"]


def test_the_host_catches_up_from_the_sequence_whenever_the_list_is_pulled(tmp_path):
    """前端重连后重新拉取列表；Host 在应答前从已读序号续读，掉了的推送就补回来了。"""
    from deskpet.orchestration.service import OrchestrationService

    store = _store()
    store.get_mission = lambda _id: SimpleNamespace(status=SimpleNamespace(value="COMPLETED"), goal="写周报",
                                                      stop_reason=None, final_report={})
    notices = MissionNotices(tmp_path, clock=lambda: 10.0)
    host = SimpleNamespace(_notices=notices, _orchestrator=SimpleNamespace(store=store))
    assert OrchestrationService.pending_notices(host) == []
    _notified(store, "ev-1", "m-1", at=1.0)  # 推送没送到 Host（断线）
    [row] = OrchestrationService.pending_notices(host)
    assert row["notice_id"] == "ev-1" and row["status"] == "COMPLETED"
