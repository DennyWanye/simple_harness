# SPDX-License-Identifier: Apache-2.0
"""执行图各张表的直接负控（联测 F2"数据表直接测试"，TaskGraph 场景 C07）。

不经任何接口，直接对库下 SQL：把甲任务的一行原样改挂到乙任务名下插入、改一行、删一行——库自己
（触发器、外键、主键）必须拒绝。接口层的拒绝另有用例；这里守的是"绕过接口也写不进去"。
"""
from __future__ import annotations

import sqlite3
from typing import Any

import pytest

#: 只增不改的表
IMMUTABLE = ("taskgraph_policy_bindings", "taskgraph_revision_records", "taskgraph_member_pins",
             "taskgraph_method_pins", "taskgraph_demand_refs", "taskgraph_attempt_inputs",
             "taskgraph_convergence_targets")
#: 可以推进状态、但身份不能改的表：表名 → 一个身份列
VERSIONED = {"taskgraph_convergence_jobs": "candidate_hash", "taskgraph_followups": "payload_hash"}


def _one(connection: sqlite3.Connection, table: str, mission_id: str) -> dict[str, Any] | None:
    cursor = connection.execute(f"SELECT * FROM {table} WHERE mission_id=? LIMIT 1", (mission_id,))
    row = cursor.fetchone()
    return None if row is None else dict(zip([c[0] for c in cursor.description], tuple(row)))


def check_table_guards(connection: sqlite3.Connection, mine: str, other: str, tables: tuple[str, ...]) -> list[str]:
    """Every listed table must hold a row of ``mine``; returns the tables exercised."""
    done = []
    for table in tables:
        row = _one(connection, table, mine)
        assert row is not None, f"{table}: the scenario left no row to try"
        # 1. 原样改挂到另一个任务名下：身份对不上（触发器）或撞主键 / 外键
        moved = dict(row, mission_id=other)
        columns = ", ".join(moved)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({', '.join('?' for _ in moved)})",
                               tuple(moved.values()))
        # 2. 改、删
        if table in IMMUTABLE:
            with pytest.raises(sqlite3.IntegrityError, match="TG_IMMUTABLE"):
                connection.execute(f"UPDATE {table} SET mission_id=? WHERE mission_id=?", (other, mine))
            with pytest.raises(sqlite3.IntegrityError, match="TG_IMMUTABLE"):
                connection.execute(f"DELETE FROM {table} WHERE mission_id=?", (mine,))
        else:
            identity = VERSIONED[table]
            with pytest.raises(sqlite3.IntegrityError, match="IDENTITY_OR_VERSION"):
                connection.execute(f"UPDATE {table} SET {identity}=?, row_version=row_version+1 WHERE mission_id=?",
                                   ("0" * 64, mine))
            with pytest.raises(sqlite3.IntegrityError, match="IDENTITY_OR_VERSION"):  # 版本号不许跳、不许不动
                connection.execute(f"UPDATE {table} SET row_version=row_version+2 WHERE mission_id=?", (mine,))
            if table == "taskgraph_convergence_jobs":
                with pytest.raises(sqlite3.IntegrityError, match="TG_IMMUTABLE"):
                    connection.execute(f"DELETE FROM {table} WHERE mission_id=?", (mine,))
        assert _one(connection, table, mine) == row, f"{table}: the row changed"
        done.append(table)
    return done
