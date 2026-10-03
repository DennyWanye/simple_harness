# SPDX-License-Identifier: Apache-2.0
"""准则只留一个来源（HTN 补齐阶段 E）。

任务的完成标准现行版本只有一处：最新的要求修订（``deployment.root.current_criteria``）。建任务时
的章程 ``Mission.success_criteria`` 只是初始输入——除了建任务门口、第 1 版要求书的构造和受保护尾部的
任务身份哈希，谁都不许再读它；否则用户中途改了要求，那一处还在按旧的办。

**改坏检验**：规划包改回读 ``mission.success_criteria`` → 变红。
"""
from __future__ import annotations

import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[3] / "src" / "agent_orchestrator"
#: 允许读章程的文件：建任务请求与门口、Mission 行、第 1 版要求书的唯一构造、任务身份哈希
ALLOWED = {
    "api/missions.py", "api/facade.py", "contracts/models.py",
    "orchestrator/commit_service.py",      # MissionSpec → Mission 行
    "orchestrator/assurance_assembly.py",  # 建任务：章程 → 第 1 版要求书
    "deployment/root.py",                  # 第 1 版要求书的唯一构造
    "verification/criteria.py",            # 受保护尾部的任务身份哈希（初始输入的身份）
}
#: 建任务门口里的一处（把章程交给"能不能用 action / pytest"的检查）
DOOR = {"orchestrator/event_handler.py": 1}
#: 任意变量名读章程都算；步骤合同（``task`` / ``contract`` / ``self`` 等）的同名字段不算
TASK_SIDE = {"task", "contract", "self", "dep", "previous", "leaf", "item", "binding", "row", "node", "t", "child"}
CHARTER_READ = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.success_criteria\b")


def test_charter_criteria_read_only_at_the_door():
    offenders: dict[str, int] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        name = path.relative_to(SOURCE).as_posix()
        count = sum(1 for line in path.read_text(encoding="utf-8").splitlines()
                    if not line.lstrip().startswith("#")
                    and any(name not in TASK_SIDE for name in CHARTER_READ.findall(line)))
        if count and name not in ALLOWED and count != DOOR.get(name, 0):
            offenders[name] = count
    assert offenders == {}, f"这些文件读了建任务时的章程，应改读现行要求：{offenders}"
