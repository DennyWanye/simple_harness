"""2026-09-30（结构修复真机第 6 局）：已结束任务的 Agent 由编排器收尾关闭。

编排每个模型回合建一个 Agent，从来不关；执行池的实例上限（1000）按建过的 Agent 数，
桌面数据目录用了几天就满了，新步骤建不出执行者，任务原地卡住。现在已结束任务的 Agent
由编排器定期关掉（只改生命周期，绑定与会话记录都保留供审计），上限只数没关闭的。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from agent_orchestrator.contracts import Budget, MissionStatus
from agent_orchestrator.orchestrator.commit_service import MissionSpec
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.testing.fixtures import (
    RECORDER_SEED,
    RECORDER_SPEC,
    RECORDER_TASKS,
    demo_dynamic_dag_provider,
    recorder_scripts,
)


def test_the_agents_of_a_finished_mission_are_closed_and_kept(tmp_path):
    provider = demo_dynamic_dag_provider(
        tasks=[t for t in RECORDER_TASKS if t["key"] in "AD"],
        scripts={"A": recorder_scripts()["A"], "D": recorder_scripts()["D"]},
    )

    async def case():  # type: ignore[no-untyped-def]
        async with Orchestrator(
            OrchestratorConfig(evidence_root=Path(tmp_path) / "e", max_concurrency=1, test_timeout_seconds=60),
            provider,
        ) as orchestrator:
            mission = await orchestrator.submit_mission(MissionSpec(
                goal=RECORDER_SPEC["goal"], success_criteria=("file:DOCS.md",), tenant_id="t",
                idempotency_key="close-agents", allowed_tools=tuple(RECORDER_SPEC["allowed_tools"]),
                budget=Budget(max_tokens=300_000, max_attempts=16), workspace_seed=RECORDER_SEED, orchestration_semantics_version="legacy"))
            await orchestrator.run()
            store = orchestrator.store
            assert store.get_mission(mission.id).status is MissionStatus.COMPLETED, orchestrator.progress_log
            intents = [row for row in store.connection.execute(
                "SELECT intent_id FROM dispatch_intents WHERE mission_id=? AND agent_id IS NOT NULL",
                (mission.id,))]
            assert intents
            closed = await orchestrator._close_finished_agents(force=True)
            assert closed == len(intents)
            for (intent_id,) in intents:
                intent = store.get_intent(intent_id)
                binding = orchestrator.bridge_for(intent).runtime.uow.read_agent_binding(intent.agent_id)
                assert binding is not None and binding.lifecycle == "closed"  # kept, only closed
            assert await orchestrator._close_finished_agents(force=True) == 0  # idempotent

    asyncio.run(case())
