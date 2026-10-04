# SPDX-License-Identifier: Apache-2.0
"""全业务事件重放 v3（HTN 补齐阶段 G）。

覆盖清单 ``business_replay_inventory.json``（第 3 版）把库里每张表归成五类：

* ``business`` —— 任务的业务事实，必须能由事件重建。存储层在每个事务提交前按任务各写一条
  ``RowsWritten``（见 :mod:`agent_orchestrator.storage.source_records`），二选一：
  * ``immutable_source``：只增、库层不许改删；``named`` 一栏按主键与内容哈希点名。v3 核"每行
    恰好被点名一次、哈希对得上、点名的行都在"。
  * ``fold``：会改的表；``changed`` 一栏记每个被改的键的改前/改后整行哈希与改后整行。v3 按
    事件序号逐条接链（改前哈希 = 折叠到此刻的哈希；改后哈希 = 所带整行的哈希），最后与库里
    本任务的行逐列精确比对（时间列也比）。一个通用折叠覆盖全部会改的表（偏差裁决 1）。
  没有 ``mission_id`` 的业务表由 ``owner`` 写明经哪张表找到任务。
* ``derived`` —— 能从业务表推出来，重建时现算，不重放。
* ``runtime`` —— 租约、队列、游标、钉住、工作目录等运行态，重建时不恢复。
* ``global`` —— 做法表、策略表，不属于任何任务：同一机制记整行变化、归部署时间线，全库检查的
  "全局"一节用同一个折叠核对（阶段 G 第 6 批）。
* ``log`` —— 事件日志本身与命令回执账（回执账带 ``named``：每条回执被点名）。

另核一条秩序（偏差裁决 1 第 6 条）：改了业务行的事务，必须有本任务的领域事件（``with_events``
非空）；确属内部记账、没有消费者的表在清单写 ``silent_ok`` 与理由。没有就是"静默改动"，算不一致。
领域事件只是给消费者的信号，内容不作为重建依据。

非业务表必须写排除理由（``note``）。新表、新字段、删掉的表都要先改清单，
:func:`check_inventory` 报错（守护测试钉住）。

范围：只核"按当前口径建的任务"——``MissionCreated`` 里的 ``replay_scope`` 等于当前的覆盖清单
加编码清单的摘要；别的任务如实报"范围外"，既不算一致也不算不一致（裁决 G-6）。

执行图历史重建（:mod:`.taskgraph_replay`）是 ``taskgraph_revision_records`` 结构历史的专用
重建器；v3 对它和别的只增表一样只核点名。
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

from ..storage.source_records import (
    DEPLOYMENT_TIMELINE,
    EVENT_TYPE,
    content_hash,
    content_of,
    named_tables,
    primary_key,
    replay_scope_digest,
    row_identity,
)
from ..storage.store import Store

REPLAY_VERSION = "business-replay-v3"
INVENTORY_VERSION = 3
CLASSES = ("business", "derived", "runtime", "global", "log")
REBUILDS = ("immutable_source", "fold")

CONSISTENT = "CONSISTENT"
INCONSISTENT = "INCONSISTENT"
OUT_OF_SCOPE = "OUT_OF_SCOPE"


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
            allowed = {"class", "fields", "note", "writers", "events", "rebuild", "owner", "silent_ok"}
            if not set(entry) <= allowed or not {"writers", "events", "rebuild"} <= set(entry):
                problems.append(f"{name}: a business table lists writers, events and rebuild")
            elif entry["rebuild"] not in REBUILDS:
                problems.append(f"{name}: rebuild must be one of {REBUILDS}")
            elif "mission_id" not in entry["fields"] and not isinstance(entry.get("owner"), dict):
                problems.append(f"{name}: a business table without mission_id names its owner")
            elif "silent_ok" in entry and not str(entry["silent_ok"]).strip():
                problems.append(f"{name}: silent_ok says why the table changes without a domain event")
        elif kind == "global":
            if not set(entry) <= {"class", "fields", "note", "rebuild", "silent_ok"} or entry.get("rebuild") != "fold" \
                    or not str(entry.get("note") or "").strip():
                problems.append(f"{name}: a global table is folded on the deployment timeline and says what it is")
        elif not set(entry) <= {"class", "fields", "note", "named"} or not str(entry.get("note") or "").strip():
            problems.append(f"{name}: a table outside business says why it is not rebuilt")
    if problems:
        raise InventoryError("; ".join(problems))
    return raw


def check_inventory(store: Store) -> None:
    """库结构与覆盖清单逐表逐字段一致；只增表有不许改删守卫；会改的表有显式主键。"""

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
        if entry.get("rebuild") == "fold" and not primary_key(connection, name):
            problems.append(f"table {name} changes but has no explicit primary key")
    if problems:
        raise InventoryError("; ".join(problems))


def _events(store: Store, mission_id: str | None) -> list[tuple[int, dict[str, Any]]]:
    sql, args = "SELECT seq, payload_json FROM events WHERE type=?", [EVENT_TYPE]
    if mission_id is not None:
        sql, args = sql + " AND mission_id=?", [EVENT_TYPE, mission_id]
    return [(int(seq), json.loads(payload)) for seq, payload in store.connection.execute(
        sql + " ORDER BY seq", args)]


def _key_text(key: Mapping[str, Any]) -> str:
    return json.dumps(key, sort_keys=True)


def _mission_rows(store: Store, mission_id: str, table: str, named_keys: set[str]) -> list[Any]:
    """This Mission's rows of ``table``: by its own column, its declared owner, or (the
    receipt log) the rows its events name."""

    entry = inventory()["tables"][table]
    if "mission_id" in entry["fields"]:
        return store.connection.execute(
            f"SELECT rowid, * FROM {table} WHERE mission_id=?", (mission_id,)).fetchall()
    owner = entry.get("owner")
    if owner is not None and "table" in owner:
        return store.connection.execute(
            f"SELECT t.rowid, t.* FROM {table} t JOIN {owner['table']} o ON t.{owner['column']}=o.{owner['key']}"
            " WHERE o.mission_id=?", (mission_id,)).fetchall()
    if owner is not None:
        return store.connection.execute(
            f"SELECT rowid, * FROM {table} WHERE {owner['column']}=?", (mission_id,)).fetchall()
    return [row for row in store.connection.execute(f"SELECT rowid, * FROM {table}")
            if _key_text(row_identity(store.connection, table, row)[0]) in named_keys]


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
    report, silent = _rebuild(store, mission_id, "business",
                              lambda table, named_keys: _mission_rows(store, mission_id, table, named_keys))
    statuses = Counter(item["status"] for item in report.values())
    return {"version": REPLAY_VERSION, "mission_id": mission_id,
            "status": INCONSISTENT if statuses[INCONSISTENT] else CONSISTENT,
            "counts": dict(sorted(statuses.items())), "silent_changes": silent[:20], "tables": report}


def verify_global(store: Store) -> dict[str, Any]:
    """The global tables (method library, policies), folded from the deployment timeline's
    whole-row changes and compared with the library (阶段 G 第 6 批)."""

    report, silent = _rebuild(store, DEPLOYMENT_TIMELINE, "global",
                              lambda table, _named: store.connection.execute(f"SELECT rowid, * FROM {table}").fetchall())
    statuses = Counter(item["status"] for item in report.values())
    return {"status": INCONSISTENT if statuses[INCONSISTENT] else CONSISTENT,
            "counts": dict(sorted(statuses.items())), "silent_changes": silent[:20], "tables": report}


def _rebuild(store: Store, owner: str, kind: str, rows_of: Any) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """One generic fold over ``owner``'s ``RowsWritten``: chain every change, compare the result
    column by column with the library, and report changes made without a domain event."""

    tables = inventory()["tables"]
    problems: dict[str, list[str]] = {name: [] for name, entry in tables.items() if entry["class"] == kind}
    named: Counter[tuple[str, str, str]] = Counter()
    state: dict[str, dict[str, tuple[str, dict[str, Any]]]] = {}
    silent: list[str] = []
    for seq, payload in _events(store, owner):
        for item in payload["named"]:
            named[(item["table"], _key_text(item["key"]), item["content_hash"])] += 1
        for item in payload["changed"]:
            table, key = item["table"], _key_text(item["key"])
            if tables[table]["class"] != kind:
                continue
            current = state.setdefault(table, {}).get(key)
            if item["before"] != (None if current is None else current[0]):
                problems.setdefault(table, []).append(f"seq {seq}: row {key} does not chain")
            if item["after"] is None:
                state[table].pop(key, None)
                continue
            if content_hash(item["row"]) != item["after"]:
                problems.setdefault(table, []).append(f"seq {seq}: row {key} differs from its hash")
            state[table][key] = (item["after"], item["row"])
        touched = {item["table"] for item in payload["changed"]} & set(problems) | {
            item["table"] for item in payload["named"] if tables[item["table"]]["class"] == kind}
        if touched and not payload["with_events"] and not all("silent_ok" in tables[t] for t in touched):
            silent.append(f"seq {seq}: {sorted(touched)} changed without a domain event")
            for table in touched:
                problems.setdefault(table, []).append(f"seq {seq}: changed without a domain event")
    report: dict[str, dict[str, Any]] = {}
    for table in sorted(problems):
        entry = tables[table]
        connection = store.connection
        named_keys = {key for (name, key, _), _count in named.items() if name == table}
        rows = rows_of(table, named_keys)
        if entry["rebuild"] == "immutable_source":
            present: set[tuple[str, str]] = set()
            for row in rows:
                key, digest = row_identity(connection, table, row)
                present.add((_key_text(key), digest))
                count = named[(table, _key_text(key), digest)]
                if count != 1:
                    problems[table].append(f"row {_key_text(key)} is named {count} times")
            problems[table] += [f"named row {key} is missing or differs"
                                for (name, key, digest) in named if name == table and (key, digest) not in present]
        else:
            columns = list(entry["fields"])
            rebuilt = {key: digest for key, (digest, _row) in state.get(table, {}).items()}
            actual: dict[str, str] = {}
            for row in rows:
                content = content_of(row, columns)
                actual[_key_text({name: content[name] for name in primary_key(connection, table)})] = \
                    content_hash(content)
            problems[table] += [f"row {key} is not rebuilt" for key in actual if key not in rebuilt]
            problems[table] += [f"rebuilt row {key} is not in the library" for key in rebuilt if key not in actual]
            problems[table] += [f"row {key} differs from its rebuilt version"
                                for key in actual if key in rebuilt and actual[key] != rebuilt[key]]
        report[table] = {"status": INCONSISTENT if problems[table] else CONSISTENT, "rows": len(rows),
                         "rebuild": entry["rebuild"], "problems": problems[table][:20]}
    return report, silent




def verify_library(store: Store) -> dict[str, Any]:
    """Library-wide checks no single Mission owns: every row of a named table is named
    exactly once, by some Mission or by the deployment timeline; the global tables rebuild."""

    names: Counter[tuple[str, str, str]] = Counter()
    for _seq, payload in _events(store, None):
        for item in payload["named"]:
            names[(item["table"], _key_text(item["key"]), item["content_hash"])] += 1
    unnamed: list[str] = []
    twice: list[str] = []
    for table in named_tables():
        for row in store.connection.execute(f"SELECT rowid, * FROM {table}"):
            key, digest = row_identity(store.connection, table, row)
            count = names[(table, _key_text(key), digest)]
            if count == 0:
                unnamed.append(f"{table} {_key_text(key)}")
            elif count > 1:
                twice.append(f"{table} {_key_text(key)}")
    deployment = int(store.connection.execute(
        "SELECT count(*) FROM events WHERE type=? AND mission_id=?", (EVENT_TYPE, DEPLOYMENT_TIMELINE)
    ).fetchone()[0])
    global_report = verify_global(store)
    bad = unnamed or twice or global_report["status"] != CONSISTENT
    return {"version": REPLAY_VERSION, "status": INCONSISTENT if bad else CONSISTENT,
            "unnamed_rows": unnamed[:50], "unnamed_count": len(unnamed), "named_twice": twice[:50],
            "deployment_events": deployment, "global": global_report}


USAGE_PREFIX = "provider-invocation:"


def verify_execution_ledgers(store: Store, execution_paths: list[Path]) -> dict[str, Any]:
    """两库对照（只读，不进重建；阶段 G 裁决 G-7）：编排导入的每条用量回执，在执行库里恰好有
    一条调用、已知用量一致。执行库仍是调用事实的唯一权威，编排只记"消费了哪一条、按多少算"。

    还记着"未知"、执行库此刻已有用量的，是等下一轮补导入的迟到用量，单独计数，不算不一致。"""

    import sqlite3
    from contextlib import closing

    from simple_harness.execution.sqlite.database import Database
    from simple_harness.execution.sqlite.uow import SqliteExecutionUnitOfWork

    calls: dict[str, list[Any]] = {}
    for path in execution_paths:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True,
                                     isolation_level=None)) as connection:
            connection.row_factory = sqlite3.Row
            if not connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='provider_invocations'").fetchone():
                continue
            uow = SqliteExecutionUnitOfWork(Database(Path(path), connection))
            for (invocation_id,) in connection.execute("SELECT invocation_id FROM provider_invocations"):
                calls.setdefault(invocation_id, []).append(uow.read_effective_provider_invocation(invocation_id))
    missing, mismatched, foreign, ambiguous, late = [], [], [], [], []
    for row in store.connection.execute(
            "SELECT usage_ref,input_tokens,output_tokens,unknown FROM imported_usage ORDER BY usage_ref"):
        usage_ref = row["usage_ref"]
        if not usage_ref.startswith(USAGE_PREFIX):
            foreign.append(usage_ref)
            continue
        records = calls.get(usage_ref[len(USAGE_PREFIX):], [])
        if not records:
            missing.append(usage_ref)
            continue
        if len(records) > 1:
            ambiguous.append(usage_ref)
            continue
        usage = records[0].usage_json if isinstance(records[0].usage_json, Mapping) else {}
        tokens = usage.get("usage") if isinstance(usage.get("usage"), Mapping) else {}
        known = (str(records[0].state) in {"succeeded", "failed"}
                 and all(type(tokens.get(k)) is int for k in ("input_tokens", "output_tokens")))
        if row["unknown"]:
            if known and tokens["input_tokens"] > 0 and tokens["output_tokens"] > 0:
                late.append(usage_ref)
        elif not known or (row["input_tokens"], row["output_tokens"]) != (
                tokens["input_tokens"], tokens["output_tokens"]):
            mismatched.append(usage_ref)
    bad = missing or mismatched or foreign or ambiguous
    return {"status": INCONSISTENT if bad else CONSISTENT, "calls": len(calls),
            "missing": missing[:50], "mismatched": mismatched[:50], "foreign": foreign[:50],
            "ambiguous": ambiguous[:50], "late_known": len(late)}


__all__ = ("CLASSES", "CONSISTENT", "INCONSISTENT", "OUT_OF_SCOPE", "REPLAY_VERSION",
           "InventoryError", "check_inventory", "inventory", "verify_execution_ledgers",
           "verify_global", "verify_library", "verify_mission")
