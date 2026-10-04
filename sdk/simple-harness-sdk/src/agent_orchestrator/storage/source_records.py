# SPDX-License-Identifier: Apache-2.0
"""业务行写入的统一记账（HTN 补齐阶段 G，裁决 G-1 与偏差裁决 1）。

"能由事件重建"在存储层一处收口，不逐处改写入方。每个最外层事务提交前，按任务各追加一条
``RowsWritten``::

    {"named":   [{table, key, content_hash}],               # 只增业务表与回执账：点名
     "changed": [{table, key, before, after, row}],          # 会被改的业务表：改前/改后整行哈希与改后整行
     "with_events": [类型, ...]}                              # 本事务里本任务的领域事件类型

* 只增表（覆盖清单 ``rebuild: "immutable_source"``）与命令回执账（``named``）：插入后触发器记下
  行号，提交前按整行规范 JSON 算内容哈希点名。
* 会被改的表（``rebuild: "fold"``）：插入、更新、删除后触发器按**主键**记"本事务碰过哪些键"，
  第一次碰到时把原行（前像）存进临时影子表；提交前读现行行，前后哈希相同（含前后都不存在、
  同一事务先插后删）就不记；``after`` 为空表示删除；主键被改算"旧键删 + 新键写"。
* 归属：本行的 ``mission_id``，或清单 ``owner`` 写明的关联（找不到就抛错，事务回滚）；回执账
  另认 JSON 正文里的 ``mission_id``，再不行取本事务唯一的任务，否则记部署时间线（时钟观察、
  保证通道根与环境安装这类部署级事实）。保证通道屏障触发器写的 ``AssuranceEvidenceChanged``
  只是信号，不参与归属，也不算"领域事件"。
* 幂等键 ``rows-written:{任务}:{追加前最大序号}:{内容摘要}``，直接插入、撞键报错（来回改成同样
  内容的行每次都记）。内容只由已提交的行确定地算出。
* 每个最外层事务开头清空临时记录；事务外的写入不会串进下一个事务（重放的链检查会报出来）。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Any

from simple_harness.contracts import canonical_json

if TYPE_CHECKING:
    from .store import Store

EVENT_TYPE = "RowsWritten"
DEPLOYMENT_TIMELINE = "deployment"
#: 屏障触发器自动写的信号：不算领域事件，也不参与归属（偏差裁决 1 R11）。
TRIGGER_EVENTS = frozenset({"AssuranceEvidenceChanged"})


@cache
def _inventory() -> Mapping[str, Any]:
    return json.loads(resources.files("agent_orchestrator.observability")
                      .joinpath("business_replay_inventory.json").read_text("utf-8"))["tables"]


@cache
def named_tables() -> tuple[str, ...]:
    """Immutable business sources and the receipt log: their rows are named."""

    return tuple(sorted(name for name, entry in _inventory().items()
                        if entry.get("rebuild") == "immutable_source" or entry.get("named") is True))


@cache
def folded_tables() -> tuple[str, ...]:
    """Tables that change (business and global): every change is logged with the whole row."""

    return tuple(sorted(name for name, entry in _inventory().items() if entry.get("rebuild") == "fold"))


def owner_rule(table: str) -> Mapping[str, str] | None:
    return _inventory()[table].get("owner")


@cache
def replay_scope_digest() -> str:
    """Which rules a Mission was born under: the replay inventory plus the network codec
    manifest.  ``MissionCreated`` carries it; v3 checks only Missions whose digest is the
    current one and reports the others "out of scope" (阶段 G 裁决 G-6)."""

    def read(package: str, name: str) -> bytes:
        return resources.files(package).joinpath(name).read_bytes()

    parts = [
        hashlib.sha256(read("agent_orchestrator.observability",
                            "business_replay_inventory.json")).hexdigest(),
        hashlib.sha256(read("agent_orchestrator.graph",
                            "network_codec_manifest_v5.json")).hexdigest(),
    ]
    return hashlib.sha256(":".join(parts).encode("ascii")).hexdigest()


def primary_key(connection: sqlite3.Connection, table: str) -> list[str]:
    columns = connection.execute(f"PRAGMA main.table_info({table})").fetchall()
    return [name for _, name in sorted((column[5], column[1]) for column in columns if column[5])]


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [column[1] for column in connection.execute(f"PRAGMA main.table_info({table})")]


def install(connection: sqlite3.Connection) -> None:
    """Per-connection temporary bookkeeping; nothing is written to the main database."""

    live = {row[0] for row in connection.execute(
        "SELECT name FROM main.sqlite_master WHERE type='table'")}
    ignored = ",".join(f"'{kind}'" for kind in sorted({EVENT_TYPE, *TRIGGER_EVENTS}))
    script = [
        "CREATE TEMP TABLE IF NOT EXISTS rows_named(tbl TEXT NOT NULL, rid INTEGER NOT NULL)",
        "CREATE TEMP TABLE IF NOT EXISTS rows_touched(tbl TEXT NOT NULL, key TEXT NOT NULL,"
        " existed INTEGER NOT NULL, PRIMARY KEY(tbl, key))",
        "CREATE TEMP TABLE IF NOT EXISTS rows_tx_events(mission_id TEXT NOT NULL, type TEXT NOT NULL)",
        f"CREATE TEMP TRIGGER IF NOT EXISTS rows_tx_events AFTER INSERT ON main.events"
        f" WHEN NEW.type NOT IN ({ignored})"
        " BEGIN INSERT INTO temp.rows_tx_events VALUES (NEW.mission_id, NEW.type); END",
    ]
    for table in named_tables():
        if table in live:
            script.append(
                f"CREATE TEMP TRIGGER IF NOT EXISTS rows_named_{table} AFTER INSERT ON main.{table}"
                f" BEGIN INSERT INTO temp.rows_named VALUES ('{table}', NEW.rowid); END")
    for table in folded_tables():
        if table not in live:
            continue
        keys = primary_key(connection, table)
        columns = _columns(connection, table)

        def key(alias: str, keys: list[str] = keys) -> str:
            return "json_array(" + ",".join(f"{alias}.{name}" for name in keys) + ")"

        touched = f"SELECT 1 FROM temp.rows_touched WHERE tbl='{table}' AND key={{}}"
        old_image = ",".join(f"OLD.{column}" for column in columns)
        # 触发器里不用 OR IGNORE：外层语句（如 upsert）的冲突策略会盖过触发器里的，写成"不存在才插"
        def mark(alias: str, existed: int) -> str:
            return (f"INSERT INTO temp.rows_touched SELECT '{table}', {key(alias)}, {existed}"
                    f" WHERE NOT EXISTS ({touched.format(key(alias))});")

        script += [
            f"CREATE TEMP TABLE IF NOT EXISTS shadow_{table} AS SELECT * FROM main.{table} WHERE 0",
            f"CREATE TEMP TRIGGER IF NOT EXISTS rows_insert_{table} AFTER INSERT ON main.{table}"
            f" BEGIN {mark('NEW', 0)} END",
        ]
        for operation in ("UPDATE", "DELETE"):
            body = (f"INSERT INTO temp.shadow_{table} SELECT {old_image}"
                    f" WHERE NOT EXISTS ({touched.format(key('OLD'))}); {mark('OLD', 1)}")
            if operation == "UPDATE":
                body += f" {mark('NEW', 0)}"
            script.append(f"CREATE TEMP TRIGGER IF NOT EXISTS rows_{operation.lower()}_{table}"
                          f" AFTER {operation} ON main.{table} BEGIN {body} END")
    connection.executescript(";\n".join(script) + ";")


def begin(connection: sqlite3.Connection) -> None:
    """At the start of an outermost transaction: nothing from outside it is carried in."""

    connection.execute("DELETE FROM temp.rows_named")
    connection.execute("DELETE FROM temp.rows_touched")
    connection.execute("DELETE FROM temp.rows_tx_events")
    for table in folded_tables():
        try:
            connection.execute(f"DELETE FROM temp.shadow_{table}")
        except sqlite3.OperationalError:  # a table this library does not have
            continue


def _value(value: Any) -> Any:
    return value.hex() if isinstance(value, bytes) else value


def content_of(row: Mapping[str, Any] | sqlite3.Row, columns: Iterable[str]) -> dict[str, Any]:
    return {column: _value(row[column]) for column in columns}


def content_hash(content: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(content)).encode("utf-8")).hexdigest()


def row_identity(
    connection: sqlite3.Connection, table: str, row: sqlite3.Row
) -> tuple[dict[str, Any], str]:
    """The row's primary key and the hash of its full content (every column, by name)."""

    columns = _columns(connection, table)
    content = content_of(row, columns)
    keys = primary_key(connection, table)
    key = {name: content[name] for name in keys} if keys else {"rowid": row["rowid"]}
    return key, content_hash(content)


def owner_of(connection: sqlite3.Connection, table: str, content: Mapping[str, Any]) -> str | None:
    """The row's Mission by its own column or the inventory's declared association; a global
    table (method library, policies) belongs to the deployment timeline (阶段 G 第 6 批)."""

    if _inventory()[table]["class"] == "global":
        return DEPLOYMENT_TIMELINE
    if content.get("mission_id"):
        return str(content["mission_id"])
    rule = owner_rule(table)
    if rule is None:
        return None
    value = content.get(rule["column"])
    if "table" not in rule:  # the column itself names the Mission (``origin_mission_id``)
        return None if value is None else str(value)
    found = connection.execute(
        f"SELECT mission_id FROM main.{rule['table']} WHERE {rule['key']}=?", (value,)).fetchone()
    return None if found is None else str(found[0])


def _receipt_owner(content: Mapping[str, Any]) -> str | None:
    for column, value in content.items():
        if column.endswith("_json") and isinstance(value, str):
            try:
                body = json.loads(value)
            except ValueError:
                continue
            owner = body.get("mission_id") if isinstance(body, dict) else None
            if isinstance(owner, str) and owner:
                return owner
    return None


def record_written_rows(store: Store, connection: sqlite3.Connection) -> None:
    """Called by the store right before the outermost COMMIT."""

    from .store import StoreError

    tx_events: dict[str, set[str]] = {}
    for mission_id, kind in connection.execute("SELECT mission_id, type FROM temp.rows_tx_events"):
        tx_events.setdefault(str(mission_id), set()).add(str(kind))
    fallback = next(iter(tx_events)) if len(tx_events) == 1 else DEPLOYMENT_TIMELINE
    named: dict[str, list[dict[str, Any]]] = {}
    for table, rid in connection.execute(
            "SELECT tbl, rid FROM temp.rows_named ORDER BY rowid").fetchall():
        row = connection.execute(
            f"SELECT rowid, * FROM main.{table} WHERE rowid=?", (rid,)).fetchone()
        if row is None:  # inserted and removed in the same transaction: nothing stands to name
            continue
        key, digest = row_identity(connection, table, row)
        content = content_of(row, _columns(connection, table))
        owner = owner_of(connection, table, content)
        if owner is None and _inventory()[table].get("named") is True:
            owner = _receipt_owner(content) or fallback
        if owner is None:
            raise StoreError(f"ROWS_WRITTEN_OWNER_UNKNOWN: {table} {canonical_json(key)}")
        named.setdefault(owner, []).append({"table": table, "key": key, "content_hash": digest})
    changed: dict[str, list[dict[str, Any]]] = {}
    for table, key_json, existed in connection.execute(
            "SELECT tbl, key, existed FROM temp.rows_touched ORDER BY rowid").fetchall():
        keys = primary_key(connection, table)
        columns = _columns(connection, table)
        values = json.loads(key_json)
        where = " AND ".join(f"{name} IS ?" for name in keys)
        before_row = connection.execute(
            f"SELECT * FROM temp.shadow_{table} WHERE {where}", values).fetchone() if existed else None
        after_row = connection.execute(f"SELECT * FROM main.{table} WHERE {where}", values).fetchone()
        before = None if before_row is None else content_of(before_row, columns)
        after = None if after_row is None else content_of(after_row, columns)
        before_hash = None if before is None else content_hash(before)
        after_hash = None if after is None else content_hash(after)
        if before_hash == after_hash:  # unchanged, or inserted and removed again
            continue
        owner = owner_of(connection, table, after or before or {})
        if owner is None:
            raise StoreError(f"ROWS_WRITTEN_OWNER_UNKNOWN: {table} {key_json}")
        changed.setdefault(owner, []).append({
            "table": table, "key": dict(zip(keys, values, strict=True)),
            "before": before_hash, "after": after_hash, "row": after})
    begin(connection)
    seq = int(connection.execute("SELECT coalesce(max(seq), 0) FROM events").fetchone()[0])
    for mission_id in sorted(set(named) | set(changed)):
        # 全局表的改动由某个任务的动作或部署命令引起：本事务里任何一条领域事件都算它的信号
        signals = (set().union(*tx_events.values()) if mission_id == DEPLOYMENT_TIMELINE and tx_events
                   else tx_events.get(mission_id, ()))
        payload = {"named": named.get(mission_id, []), "changed": changed.get(mission_id, []),
                   "with_events": sorted(signals)}
        digest = content_hash({"mission_id": mission_id, **payload})
        identity = f"rows-written:{mission_id}:{seq}:{digest}"
        connection.execute(
            "INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,"
            "attempt_id,actor_type,actor_id,payload_json,created_at,schema_version)"
            " VALUES (?,?,?,?,?,NULL,NULL,'system','rows-written-v1',?,?,1)",
            (identity, identity, EVENT_TYPE, identity, mission_id, canonical_json(payload), store.now))


__all__ = ("DEPLOYMENT_TIMELINE", "EVENT_TYPE", "TRIGGER_EVENTS", "begin", "content_hash",
           "content_of", "folded_tables", "install", "named_tables", "owner_of", "owner_rule",
           "primary_key", "record_written_rows", "replay_scope_digest", "row_identity")
