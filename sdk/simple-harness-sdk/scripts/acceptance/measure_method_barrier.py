# SPDX-License-Identifier: Apache-2.0
"""HTN 补齐 F1-3：量"提出一个新做法"给保证通道带来的开销。

做法定义表的插入触发器会给全局纪元加 1，并给**所有曾经绑定过保证通道的任务**（含已结束的）
各写一条证据变更事件。这里在产品同形世界里先跑完 N 个已结束的任务，再登记一个新做法（与规划器
提出做法写的是同一张表、同一个触发器），记下：这一次写入多出的证据变更事件条数、全局纪元加了几、
这次写入耗时。只量不断言，数字交阶段 G 决定是否改成只给未结束任务写。

用法（在 ``sdk/simple-harness-sdk`` 下）::

    uv run --frozen python scripts/acceptance/measure_method_barrier.py 1 10 50
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


def _changes(store) -> int:
    return int(store.connection.execute(
        "SELECT count(*) FROM events WHERE type='AssuranceEvidenceChanged'").fetchone()[0])


def _epoch(store) -> int:
    row = store.connection.execute("SELECT epoch FROM assurance_environment_state WHERE singleton=1").fetchone()
    return -1 if row is None else int(row[0])


async def measure(finished: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="method-barrier-") as root:
        async with product_world(Path(root) / "root", LayeredScriptedProvider()) as world:
            for index in range(finished):
                created = world.create({"goal": f"写第 {index} 份笔记", "idempotency_key": f"barrier-{index}",
                                        "success_criteria": [f"file:notes/{index}.md"]})
                mission = await world.run_until_settled(created["mission_id"], rounds=20)
                if str(mission.status.value) != "COMPLETED":
                    raise SystemExit(f"mission {index} did not complete: {mission.status}")
            htn = HtnStore(world.store)
            stored = htn.list_methods()[0]
            missions = int(world.store.connection.execute(
                "SELECT count(DISTINCT mission_id) FROM assurance_mission_bindings").fetchone()[0])
            before_changes, before_epoch = _changes(world.store), _epoch(world.store)
            contract = replace(stored.contract, method_id="barrier-probe")
            registration = replace(stored.registration, method_ref=contract.method_ref())
            started = time.perf_counter()
            with world.store.transaction():
                htn.register_method(contract, registration)
            elapsed_ms = (time.perf_counter() - started) * 1000
            return {"finished_missions": finished, "bound_missions": missions,
                    "evidence_changed_events": _changes(world.store) - before_changes,
                    "global_epoch_delta": _epoch(world.store) - before_epoch,
                    "write_ms": round(elapsed_ms, 2)}


def main(argv: list[str]) -> int:
    counts = [max(1, int(item)) for item in argv] or [1, 10, 50]  # at least one: it gives the method template
    for count in counts:
        print(json.dumps(asyncio.run(measure(count)), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
