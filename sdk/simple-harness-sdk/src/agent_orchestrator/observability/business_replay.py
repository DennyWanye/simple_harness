# SPDX-License-Identifier: Apache-2.0
"""全业务事件重放 v3 的骨架与覆盖清单（HTN 补齐阶段 A 第 5 条，"G0"）。

**这一版是骨架**：它不重放任何东西，只如实报告"这个任务的业务事实里，哪些表已经能由事件
重建、哪些还不能"。覆盖清单 ``business_replay_inventory.json`` 把库里每张表归成五类：

* ``business`` —— 任务的业务事实，要能由事件重建；每张表列出字段、写它的入口
  （``writers``）与它发的事件（``events``），以及不发事件的写入口（``gaps``）。``events`` 为空即
  "未覆盖"，有 ``gaps`` 即"部分覆盖"。
* ``derived`` —— 能从业务表推出来（如执行图修订的钉住投影），重建时现算，不重放。
* ``runtime`` —— 租约、队列、游标、待办、令牌等运行态，重建时不恢复，恢复运行前重新核对。
* ``global`` —— 做法表、策略表，不属于任何任务，单独核对，不按任务重放。
* ``log`` —— 事件日志本身。

新表或新字段没有登记，:func:`check_inventory` 报错（守护测试钉住）；各阶段新增的业务事实
当批登记、当批写上事件，阶段 G 收尾时只补清单里剩下的老表。

与另外两种重放的关系：

* v2（:mod:`.replay`）按 9 类对象的少数"正式字段"折叠事件，阶段 G 由 v3 取代后删除；
  它现在的使用方见 :data:`V2_CONSUMERS`。
* 执行图历史重建（:mod:`.taskgraph_replay`）只核对并重建执行图的结构历史（修订记录链与
  钉住投影），是 v3 里 ``taskgraph_revision_records`` 等表的专用重建器，v3 收尾后由它供数。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

from ..storage.store import Store

REPLAY_VERSION = "business-replay-v3"
CLASSES = ("business", "derived", "runtime", "global", "log")

#: v2 的使用方，阶段 G 收尾时一起迁到 v3。
V2_CONSUMERS = (
    "SDK 命令行 replay（agent_orchestrator/__main__.py cmd_replay）",
    "SDK 命令行策略命令借用的 library_copy（agent_orchestrator/__main__.py）",
    "Host 诊断导出（backend/deskpet/orchestration/diagnostics.py：events_from_store、Projection、"
    "compare、formal_from_snapshot、failure_timeline）",
    "Host 测试 backend/tests/orchestration/test_mission_diagnostics.py",
)


class InventoryError(ValueError):
    pass


@cache
def inventory() -> Mapping[str, Any]:
    raw = json.loads(Path(__file__).with_name("business_replay_inventory.json").read_text("utf-8"))
    if raw.get("inventory_version") != 1 or raw.get("replay_version") != REPLAY_VERSION:
        raise InventoryError("business replay inventory has the wrong version")
    tables = raw.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise InventoryError("business replay inventory has no tables")
    for name, entry in tables.items():
        expected = {"class", "fields", "note"} if entry.get("class") != "business" else {
            "class", "fields", "note", "writers", "events", "gaps"}
        if entry.get("class") not in CLASSES or not set(entry) <= expected or "fields" not in entry:
            raise InventoryError(f"inventory entry {name!r} is malformed")
        if entry["class"] == "business" and not {"writers", "events"} <= set(entry):
            raise InventoryError(f"business table {name!r} must list writers and events")
    return raw


def check_inventory(store: Store) -> None:
    """库结构与覆盖清单逐表逐字段一致；新表、新字段、删掉的表都要先改清单。"""

    tables = inventory()["tables"]
    connection = store.connection
    live = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    problems: list[str] = []
    for name in sorted(live - set(tables)):
        problems.append(f"table {name} is not classified")
    for name in sorted(set(tables) - live):
        problems.append(f"table {name} is classified but does not exist")
    for name in sorted(live & set(tables)):
        fields = [row[1] for row in connection.execute(f"PRAGMA table_info({name})")]
        if fields != list(tables[name]["fields"]):
            problems.append(f"table {name} fields differ from the inventory")
    if problems:
        raise InventoryError("; ".join(problems))


def coverage_report(store: Store, mission_id: str) -> dict[str, Any]:
    """这个任务每张业务表有几行、其中哪些表还不能由事件重建（只读）。"""

    tables = inventory()["tables"]
    connection = store.connection
    rows: dict[str, int] = {}
    for name, entry in tables.items():
        if entry["class"] != "business" or "mission_id" not in entry["fields"]:
            continue
        rows[name] = int(connection.execute(
            f"SELECT COUNT(*) FROM {name} WHERE mission_id=?", (mission_id,)).fetchone()[0])
    business = [name for name, entry in tables.items() if entry["class"] == "business"]
    def state(name: str) -> str:
        entry = tables[name]
        if not entry["events"]:
            return "not_covered"
        return "partial" if entry.get("gaps") else "covered"

    return {
        "version": REPLAY_VERSION,
        "status": "SKELETON",
        "business_tables": len(business),
        "covered": sorted(name for name in business if state(name) == "covered"),
        "partial": sorted(name for name in business if state(name) == "partial"),
        "not_covered": sorted(name for name in business if state(name) == "not_covered"),
        "mission_rows": {name: count for name, count in sorted(rows.items()) if count},
        "unscoped_business_tables": sorted(
            name for name in business if "mission_id" not in tables[name]["fields"]),
    }


__all__ = ("CLASSES", "REPLAY_VERSION", "V2_CONSUMERS", "InventoryError", "check_inventory",
           "coverage_report", "inventory")
