"""opt.177 核验建议 S6（第二批）：取消一个服务意图（规划器 / 审阅员的回合）后，只有执行库物理安静
且账完整时才结清它的预留；用量未知、线路已结束的那种：不抛错、回执照记（调用方记）、预留留着
（之后按上限计入）。"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from agent_orchestrator.orchestrator import taskgraph_runtime
from agent_orchestrator.orchestrator.taskgraph_runtime import TaskGraphRuntimeCommands


def _commands(monkeypatch, *, physical: bool, complete: bool) -> tuple[Any, list]:
    settled: list = []
    facts = SimpleNamespace(physical_settled=physical, accounting_complete=complete)
    monkeypatch.setattr(taskgraph_runtime, "TaskGraphRuntimeImports",
                        lambda orch: SimpleNamespace(read_subject=lambda current: facts), raising=False)
    import agent_orchestrator.orchestrator.taskgraph_runtime_imports as imports
    monkeypatch.setattr(imports, "TaskGraphRuntimeImports", lambda orch: SimpleNamespace(read_subject=lambda current: facts))
    orchestrator = SimpleNamespace(commit=SimpleNamespace(settle_subject=lambda subject, mission: settled.append((subject, mission))))
    return SimpleNamespace(orchestrator=orchestrator), settled


@pytest.mark.parametrize("physical,complete,expect", [(True, True, True), (True, False, False), (False, True, False), (False, False, False)])
def test_only_quiet_and_complete_settles_the_service_subject(monkeypatch, physical, complete, expect):
    commands, settled = _commands(monkeypatch, physical=physical, complete=complete)
    current = SimpleNamespace(subject_id="m-1:planner:3", mission_id="m-1")
    done = TaskGraphRuntimeCommands._settle_service_subject_if_quiet_and_complete(commands, current)
    assert done is expect
    assert settled == ([("m-1:planner:3", "m-1")] if expect else [])  # unknown charge: nothing raised, hold kept
