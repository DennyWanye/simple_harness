# SPDX-License-Identifier: Apache-2.0
"""不可变源记录的自动点名（HTN 补齐阶段 G，裁决 G-1）。

"能由事件重建"的一半是：只增、库层不许改删的业务表（覆盖清单里 ``rebuild:
"immutable_source"``）与命令回执账，每一行都要被**同一事务**里的一条事件按主键与内容哈希
点名。这件事不逐处改写入方，在存储层一处收口：

* 连接打开时，在临时库里给每张这样的表装一个插入后触发器，记下本事务插入了哪张表的哪一行；
  也记下本事务写了哪些任务的事件（给没有 ``mission_id`` 的行定归属）。
* 最外层事务提交前，:func:`name_written_rows` 按任务各追加一条 ``ImmutableRowsNamed``
  ``{rows: [{table, key, content_hash}]}``，再清空记录。行的任务取它的 ``mission_id`` 列，或
  它 JSON 正文里写的 ``mission_id``；都没有的，归到本事务里唯一的那个任务；本事务没有任务事件、
  或涉及不止一个任务时，记在部署时间线上（时钟观察、保证通道根的安装这类部署级事实）。

事件内容只由已提交的行确定地算出（不取时钟、不取随机数），同样的写入得到同样的幂等键。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Any

from simple_harness.contracts import canonical_json

from ..contracts.models import Event

if TYPE_CHECKING:
    from .store import Store

EVENT_TYPE = "ImmutableRowsNamed"
DEPLOYMENT_TIMELINE = "deployment"


@cache
def named_tables() -> tuple[str, ...]:
    """The tables whose rows are named: immutable business sources and the receipt log."""

    raw = json.loads(
        resources.files("agent_orchestrator.observability")
        .joinpath("business_replay_inventory.json")
        .read_text("utf-8")
    )
    return tuple(sorted(
        name for name, entry in raw["tables"].items()
        if entry.get("rebuild") == "immutable_source" or entry.get("named") is True
    ))


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


def install(connection: sqlite3.Connection, tables: Iterable[str] | None = None) -> None:
    """Per-connection temporary bookkeeping; nothing is written to the main database."""

    live = {row[0] for row in connection.execute(
        "SELECT name FROM main.sqlite_master WHERE type='table'")}
    script = [
        "CREATE TEMP TABLE IF NOT EXISTS source_rows_written"
        "(tbl TEXT NOT NULL, rid INTEGER NOT NULL)",
        "CREATE TEMP TABLE IF NOT EXISTS source_tx_missions(mission_id TEXT NOT NULL)",
        f"CREATE TEMP TRIGGER IF NOT EXISTS source_tx_events AFTER INSERT ON main.events"
        f" WHEN NEW.type <> '{EVENT_TYPE}'"
        " BEGIN INSERT INTO temp.source_tx_missions VALUES (NEW.mission_id); END",
    ]
    for table in tables if tables is not None else named_tables():
        if table not in live:
            continue
        script.append(
            f"CREATE TEMP TRIGGER IF NOT EXISTS source_named_{table} AFTER INSERT ON main.{table}"
            f" BEGIN INSERT INTO temp.source_rows_written VALUES ('{table}', NEW.rowid); END"
        )
    connection.executescript(";\n".join(script) + ";")


def _value(value: Any) -> Any:
    return value.hex() if isinstance(value, bytes) else value


def row_identity(
    connection: sqlite3.Connection, table: str, row: sqlite3.Row
) -> tuple[dict[str, Any], str]:
    """The row's primary key and the hash of its full content (every column, by name)."""

    columns = connection.execute(f"PRAGMA main.table_info({table})").fetchall()
    keys = sorted((column[5], column[1]) for column in columns if column[5])
    content = {column[1]: _value(row[column[1]]) for column in columns}
    key = {name: content[name] for _, name in keys} if keys else {"rowid": row["rowid"]}
    return key, hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


def _owner(row: sqlite3.Row) -> str | None:
    """The row's own Mission: its ``mission_id`` column, or the ``mission_id`` its JSON body
    names (a receipt for one Mission's pin)."""

    if "mission_id" in row.keys():
        return str(row["mission_id"]) if row["mission_id"] else None
    for column in row.keys():
        if column.endswith("_json") and isinstance(row[column], str):
            try:
                body = json.loads(row[column])
            except ValueError:
                continue
            owner = body.get("mission_id") if isinstance(body, dict) else None
            if isinstance(owner, str) and owner:
                return owner
    return None


def name_written_rows(store: Store, connection: sqlite3.Connection) -> None:
    """Called by the store right before the outermost COMMIT."""

    written = connection.execute(
        "SELECT tbl, rid FROM temp.source_rows_written ORDER BY rowid").fetchall()
    if not written:
        connection.execute("DELETE FROM temp.source_tx_missions")
        return
    missions = sorted({row[0] for row in connection.execute(
        "SELECT DISTINCT mission_id FROM temp.source_tx_missions")})
    fallback = missions[0] if len(missions) == 1 else DEPLOYMENT_TIMELINE
    by_mission: dict[str, list[dict[str, Any]]] = {}
    for table, rid in written:
        row = connection.execute(
            f"SELECT rowid, * FROM main.{table} WHERE rowid=?", (rid,)).fetchone()
        if row is None:  # inserted and removed in the same transaction: nothing stands to name
            continue
        key, content_hash = row_identity(connection, table, row)
        owner = _owner(row) or fallback
        by_mission.setdefault(str(owner), []).append(
            {"table": table, "key": key, "content_hash": content_hash})
    connection.execute("DELETE FROM temp.source_rows_written")
    connection.execute("DELETE FROM temp.source_tx_missions")
    for mission_id in sorted(by_mission):
        payload = {"rows": by_mission[mission_id]}
        digest = hashlib.sha256(
            canonical_json({"mission_id": mission_id, **payload}).encode("utf-8")).hexdigest()
        identity = f"source-records:{mission_id}:{digest}"
        store.append_event(Event(
            id=identity, type=EVENT_TYPE, trace_id=identity, mission_id=mission_id,
            task_id=None, attempt_id=None, actor_type="system", actor_id="source-records-v1",
            payload=payload, idempotency_key=identity, created_at=store.now,
        ))


__all__ = ("DEPLOYMENT_TIMELINE", "EVENT_TYPE", "install", "name_written_rows", "named_tables",
           "replay_scope_digest", "row_identity")
