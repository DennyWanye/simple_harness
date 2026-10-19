# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""源记录的临时触发器在旧 SQLite 上也能装（AIPhone 2026-10-20 交接的 SDK-01）。

SQLite 规定触发器体内 INSERT/UPDATE/DELETE 的目标表不能带库名（``temp.``/``main.``）；
3.53 放宽了这条，本机测试看不出来，3.50/3.51 的消费者一开库就倒。这里钉死生成的脚本：
触发器体内的写目标一律不带库名；``ON main.<table>`` 与 SELECT 子句里的限定名不在此限。
"""

from __future__ import annotations

import re
import sqlite3

from agent_orchestrator.storage import source_records
from agent_orchestrator.storage.store import Store


def _install_script(tmp_path) -> str:
    """开一个真库拿到全部主表，再抓 ``install`` 发给连接的脚本。"""

    store = Store.open(tmp_path / "orchestrator.db")
    try:
        connection = store.connection
    except AttributeError:  # pragma: no cover - 名字随实现
        connection = store._connection
    captured: list[str] = []

    class _Capture:
        def execute(self, *args, **kwargs):
            return connection.execute(*args, **kwargs)

        def executescript(self, script):
            captured.append(script)
            return connection.executescript(script)

    source_records.install(_Capture())
    store.close()
    return captured[0]


def test_trigger_bodies_never_write_to_a_schema_qualified_table(tmp_path) -> None:
    script = _install_script(tmp_path)
    bodies = re.findall(r"CREATE TEMP TRIGGER .*? BEGIN (.*?) END", script, re.S)
    assert len(bodies) > 10
    for body in bodies:
        assert not re.search(r"\b(INSERT INTO|UPDATE|DELETE FROM)\s+(temp|main)\.", body), body


def test_the_script_installs_on_a_strict_in_memory_database(tmp_path) -> None:
    """在当前 SQLite 上装一遍并写一行，确认改成不带库名后触发器仍落在 temp 表上。"""

    script = _install_script(tmp_path)
    db = sqlite3.connect(":memory:")
    # 只要主表存在：把真库的表结构搬过来（不带数据）
    source = sqlite3.connect(tmp_path / "orchestrator.db")
    for (sql,) in source.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"
        " AND name NOT LIKE 'sqlite_%'"
    ):
        db.execute(sql)
    source.close()
    db.executescript(script)
    db.execute(
        "INSERT INTO main.events(event_id, idempotency_key, type, trace_id, mission_id,"
        " actor_type, actor_id, payload_json, created_at, schema_version)"
        " VALUES ('e-1', 'k-1', 'Probe', 't', 'm', 'system', 'test', '{}', 0, 1)"
    )
    assert db.execute("SELECT mission_id, type FROM temp.rows_tx_events").fetchall() == [
        ("m", "Probe")
    ]
