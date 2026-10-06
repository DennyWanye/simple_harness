# SPDX-License-Identifier: Apache-2.0
"""任务级判定树带着任务的资料（2026-10-06 真实模型验收，编程题）。

每次尝试的工作区都带着登记的资料，模型写的测试会读它们当测试数据；任务判定时系统另起一棵"整合副本"
跑 pytest，这棵树原来只有产物没有资料，测试全红，任务被判"要求未满足"。现在判定树同样带现行版本的资料。

**改坏检验**：判定树不放资料（JDG-01）→ 变红。"""
from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

SPEC = "# 规格\n价格：每月 18 元\n"


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_the_judgment_tree_carries_the_current_sources(tmp_path):
    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            mission_id = world.create({"goal": "按规格写一份说明", "success_criteria": ["file:notes/a.md"],
                                       "idempotency_key": "judge-sources"})["mission_id"]
            world.control.register_source({"mission_id": mission_id, "path": "sources/spec.md", "content": SPEC,
                                           "kind": "markdown", "idempotency_key": "reg-spec"})
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            # 判定时系统在 CAS 里读回现行资料，和产物一起放进判定树
            assert world.loop._current_source_files(mission) == {"sources/spec.md": SPEC.encode("utf-8")}
            [tree] = [p for p in (tmp_path / "root").rglob(f"{mission_id}-judge-*-verify") if p.is_dir()]
            assert (tree / "notes/a.md").is_file()
            assert (tree / "sources/spec.md").is_file(), sorted(str(p.relative_to(tree)) for p in tree.rglob("*"))
            assert (tree / "sources/spec.md").read_text(encoding="utf-8") == SPEC

    asyncio.run(case())
