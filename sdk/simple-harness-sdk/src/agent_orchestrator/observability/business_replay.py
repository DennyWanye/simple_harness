# SPDX-License-Identifier: Apache-2.0
"""全业务事件重放 v3（HTN 补齐阶段 G）。

覆盖清单 ``business_replay_inventory.json``（第 2 版）把库里每张表归成五类：

* ``business`` —— 任务的业务事实，必须能由事件重建，二选一（裁决 G-1）：
  * ``immutable_source``：只增、库层不许改删；每一行都被**同一事务**里的
    ``ImmutableRowsNamed`` 事件按主键与内容哈希点名（存储层自动做，见
    :mod:`agent_orchestrator.storage.source_records`）。v3 核"每行恰好被点名一次、哈希对得上、
    点名的行都在"。
  * ``fold``：会改的表；建行事件带整行、每次改动都有事件，由 ``rebuilder`` 指名的折叠函数
    （:data:`FOLDERS`）从事件算出各行，与库里逐列比对。还没有折叠函数、或 ``gaps`` 非空的，
    报"未覆盖"。
* ``derived`` —— 能从业务表推出来，重建时现算，不重放。
* ``runtime`` —— 租约、队列、游标、钉住、工作目录等运行态，重建时不恢复。
* ``global`` —— 做法表、策略表，不属于任何任务，按全库事件单独核对（阶段 G 第 6 批）。
* ``log`` —— 事件日志本身与命令回执账（回执账带 ``named``：每条回执被点名）。

非业务表必须写排除理由（``note``）。新表、新字段、删掉的表都要先改清单，
:func:`check_inventory` 报错（守护测试钉住）。

范围：只核"按当前口径建的任务"——``MissionCreated`` 里的 ``replay_scope`` 等于当前的覆盖清单
加编码清单的摘要；别的任务如实报"范围外"，既不算一致也不算不一致（裁决 G-6）。

时间字段（以 ``_at`` / ``_at_ms`` 结尾）在折叠比对里只核先后，不按值相等比较。

执行图历史重建（:mod:`.taskgraph_replay`）是 ``taskgraph_revision_records`` 结构历史的专用
重建器；v3 对它和别的只增表一样只核点名。
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping
from functools import cache
from pathlib import Path
from typing import Any

from ..storage.source_records import (
    DEPLOYMENT_TIMELINE,
    EVENT_TYPE,
    named_tables,
    replay_scope_digest,
    row_identity,
)
from ..storage.store import Store

REPLAY_VERSION = "business-replay-v3"
INVENTORY_VERSION = 2
CLASSES = ("business", "derived", "runtime", "global", "log")
REBUILDS = ("immutable_source", "fold")

CONSISTENT = "CONSISTENT"
INCONSISTENT = "INCONSISTENT"
NOT_COVERED = "NOT_COVERED"
OUT_OF_SCOPE = "OUT_OF_SCOPE"

#: 会改的业务表的折叠函数：``(store, mission_id, events) -> 各行（列名 → 值）``。各批接上。
Folder = Callable[[Store, str, list[Any]], list[dict[str, Any]]]
FOLDERS: dict[str, Folder] = {}


class InventoryError(ValueError):
    pass


@cache
def inventory() -> Mapping[str, Any]:
    raw = json.loads(Path(__file__).with_name("business_replay_inventory.json").read_text("utf-8"))
    if raw.get("inventory_version") != INVENTORY_VERSION or raw.get("replay_version") != REPLAY_VERSION:
        raise InventoryError("business replay inventory has the wrong version")
    tables = raw.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise InventoryError("business replay inventory has no tables")
    problems = []
    for name, entry in tables.items():
        kind = entry.get("class")
        if kind not in CLASSES or "fields" not in entry:
            problems.append(f"{name}: class and fields are required")
        elif kind == "business":
            allowed = {"class", "fields", "note", "writers", "events", "gaps", "rebuild", "rebuilder"}
            if not set(entry) <= allowed or not {"writers", "events", "rebuild"} <= set(entry):
                problems.append(f"{name}: a business table lists writers, events and rebuild")
            elif entry["rebuild"] not in REBUILDS:
                problems.append(f"{name}: rebuild must be one of {REBUILDS}")
            elif entry["rebuild"] == "fold" and "rebuilder" not in entry:
                problems.append(f"{name}: a folded table names its rebuilder (or null)")
        elif not set(entry) <= {"class", "fields", "note", "named"} or not str(entry.get("note") or "").strip():
            problems.append(f"{name}: a table outside business says why it is not rebuilt")
    if problems:
        raise InventoryError("; ".join(problems))
    return raw


def check_inventory(store: Store) -> None:
    """库结构与覆盖清单逐表逐字段一致；只增表有不许改删守卫；指名的折叠函数都在。"""

    tables = inventory()["tables"]
    connection = store.connection
    live = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    triggers = connection.execute(
        "SELECT tbl_name, upper(sql) FROM sqlite_master WHERE type='trigger'").fetchall()
    problems: list[str] = []
    for name in sorted(live - set(tables)):
        problems.append(f"table {name} is not classified")
    for name in sorted(set(tables) - live):
        problems.append(f"table {name} is classified but does not exist")
    for name in sorted(live & set(tables)):
        entry = tables[name]
        fields = [row[1] for row in connection.execute(f"PRAGMA table_info({name})")]
        if fields != list(entry["fields"]):
            problems.append(f"table {name} fields differ from the inventory")
        if entry.get("rebuild") == "immutable_source" or entry.get("named") is True:
            for operation in ("UPDATE", "DELETE"):
                if not any(table == name and f"BEFORE {operation} ON {name.upper()}" in sql
                           and "RAISE(" in sql for table, sql in triggers):
                    problems.append(f"table {name} is an immutable source without a {operation} guard")
        rebuilder = entry.get("rebuilder")
        if rebuilder is not None and rebuilder not in FOLDERS:
            problems.append(f"table {name} names rebuilder {rebuilder!r}, which is not registered")
    if problems:
        raise InventoryError("; ".join(problems))


def _names(store: Store, mission_id: str | None) -> Counter[tuple[str, str, str]]:
    """``(table, key, hash)`` → how many naming events name it (one Mission, or all)."""

    sql = "SELECT payload_json FROM events WHERE type=?"
    args: tuple[Any, ...] = (EVENT_TYPE,)
    if mission_id is not None:
        sql, args = sql + " AND mission_id=?", (EVENT_TYPE, mission_id)
    counted: Counter[tuple[str, str, str]] = Counter()
    for (payload,) in store.connection.execute(sql, args):
        for row in json.loads(payload)["rows"]:
            counted[(row["table"], json.dumps(row["key"], sort_keys=True), row["content_hash"])] += 1
    return counted


def _source_table(store: Store, mission_id: str, table: str,
                  names: Counter[tuple[str, str, str]]) -> dict[str, Any]:
    connection = store.connection
    columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
    named_here = {key: count for (name, key, _), count in names.items() if name == table}
    if "mission_id" in columns:
        rows = connection.execute(f"SELECT rowid, * FROM {table} WHERE mission_id=?", (mission_id,)).fetchall()
    else:  # attributed by its naming event: only the rows this Mission's events name
        rows = [row for row in connection.execute(f"SELECT rowid, * FROM {table}")
                if json.dumps(row_identity(connection, table, row)[0], sort_keys=True) in named_here]
    problems: list[str] = []
    present: set[tuple[str, str]] = set()
    for row in rows:
        key, digest = row_identity(connection, table, row)
        key_text = json.dumps(key, sort_keys=True)
        present.add((key_text, digest))
        count = names[(table, key_text, digest)]
        if count == 0:
            problems.append(f"row {key_text} is not named with its content"
                            if key_text in named_here else f"row {key_text} is not named")
        elif count > 1:
            problems.append(f"row {key_text} is named {count} times")
    for (name, key_text, digest), _count in names.items():
        if name == table and (key_text, digest) not in present:
            problems.append(f"named row {key_text} is missing or differs")
    return {"status": INCONSISTENT if problems else CONSISTENT, "rows": len(rows),
            "rebuild": "immutable_source", "problems": problems[:20]}


def _folded_table(store: Store, mission_id: str, table: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    folder = FOLDERS.get(str(entry.get("rebuilder") or ""))
    columns = [row[1] for row in store.connection.execute(f"PRAGMA table_info({table})")]
    count = int(store.connection.execute(
        f"SELECT count(*) FROM {table} WHERE mission_id=?", (mission_id,)).fetchone()[0]) \
        if "mission_id" in columns else None
    if folder is None or entry.get("gaps"):
        return {"status": NOT_COVERED, "rows": count, "rebuild": "fold",
                "problems": [f"{len(entry.get('gaps') or [])} gaps" if entry.get("gaps") else "no rebuilder"]}
    events = list(store.list_events(mission_id))
    rebuilt = folder(store, mission_id, events)
    actual = [dict(row) for row in store.connection.execute(
        f"SELECT * FROM {table} WHERE mission_id=?", (mission_id,))]
    problems = _compare_rows(table, columns, rebuilt, actual)
    return {"status": INCONSISTENT if problems else CONSISTENT, "rows": len(actual), "rebuild": "fold",
            "problems": problems[:20]}


def _compare_rows(table: str, columns: list[str], rebuilt: list[dict[str, Any]],
                  actual: list[dict[str, Any]]) -> list[str]:
    timed = {column for column in columns if column.endswith(("_at", "_at_ms"))}

    def comparable(row: Mapping[str, Any]) -> str:
        return json.dumps({column: row.get(column) for column in columns if column not in timed},
                          sort_keys=True, default=str)

    left, right = Counter(map(comparable, rebuilt)), Counter(map(comparable, actual))
    problems = [f"{table}: rebuilt row not in the library: {row[:160]}" for row in (left - right)]
    problems += [f"{table}: library row not rebuilt: {row[:160]}" for row in (right - left)]
    return problems


def verify_mission(store: Store, mission_id: str) -> dict[str, Any]:
    """Rebuild one Mission's business tables from its events and compare (read only)."""

    created = store.connection.execute(
        "SELECT payload_json FROM events WHERE mission_id=? AND type='MissionCreated'", (mission_id,)
    ).fetchone()
    scope = None if created is None else json.loads(created[0]).get("replay_scope")
    if scope != replay_scope_digest():
        return {"version": REPLAY_VERSION, "mission_id": mission_id, "status": OUT_OF_SCOPE,
                "reason": "created under another inventory or codec manifest" if created else "no MissionCreated",
                "tables": {}}
    tables = inventory()["tables"]
    names = _names(store, mission_id)
    report: dict[str, dict[str, Any]] = {}
    for table, entry in sorted(tables.items()):
        if entry["class"] != "business":
            continue
        report[table] = (_source_table(store, mission_id, table, names)
                         if entry["rebuild"] == "immutable_source"
                         else _folded_table(store, mission_id, table, entry))
    statuses = Counter(item["status"] for item in report.values())
    overall = (INCONSISTENT if statuses[INCONSISTENT] else NOT_COVERED if statuses[NOT_COVERED]
               else CONSISTENT)
    return {"version": REPLAY_VERSION, "mission_id": mission_id, "status": overall,
            "counts": dict(sorted(statuses.items())), "tables": report}


def verify_library(store: Store) -> dict[str, Any]:
    """Library-wide checks no single Mission owns: every row of a named table is named
    exactly once, by some Mission or by the deployment timeline."""

    names = _names(store, None)
    unnamed: list[str] = []
    twice: list[str] = []
    for table in named_tables():
        for row in store.connection.execute(f"SELECT rowid, * FROM {table}"):
            key, digest = row_identity(store.connection, table, row)
            count = names[(table, json.dumps(key, sort_keys=True), digest)]
            if count == 0:
                unnamed.append(f"{table} {json.dumps(key, sort_keys=True)}")
            elif count > 1:
                twice.append(f"{table} {json.dumps(key, sort_keys=True)}")
    deployment = int(store.connection.execute(
        "SELECT count(*) FROM events WHERE type=? AND mission_id=?", (EVENT_TYPE, DEPLOYMENT_TIMELINE)
    ).fetchone()[0])
    return {"version": REPLAY_VERSION, "status": INCONSISTENT if unnamed or twice else CONSISTENT,
            "unnamed_rows": unnamed[:50], "unnamed_count": len(unnamed), "named_twice": twice[:50],
            "deployment_naming_events": deployment}


__all__ = ("CLASSES", "CONSISTENT", "FOLDERS", "INCONSISTENT", "NOT_COVERED", "OUT_OF_SCOPE",
           "REPLAY_VERSION", "InventoryError", "check_inventory", "inventory", "verify_library",
           "verify_mission")
