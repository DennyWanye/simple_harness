# SPDX-License-Identifier: Apache-2.0
"""删旧平面模式第三刀第 4 步：库里一条旧档案结构的任务停在执行轮次中途（进程在执行者
交回结果后退出），编排器照样能启动、恢复，第一轮由门按名停掉它。

核验员 2026-10-02 复现过：启动时 ``_bind_startup_tools`` → ``_bind_agent`` 读冻结档案
抛 ``CommitRejected``，``__aenter__`` 直接失败、门根本跑不到，开发库里只要有一个这样的
任务，宿主编排每次都起不来。

**改坏检验**：去掉 ``_bind_startup_tools`` / ``recover`` 里的 ``_domain_unreadable`` 跳过
→ 本条失败。
"""
import asyncio
import json
from pathlib import Path
import subprocess
import sys

from production_fixture import _config
from agent_orchestrator.contracts import MissionStatus
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.governance.policies import deployed_layers
from agent_orchestrator.graph.task_network import DEFAULT_PROJECTION_BUDGET
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.orchestrator.taskgraph_assembly import TaskGraphDeploymentPorts
from agent_orchestrator.orchestrator.taskgraph_deployment import InstalledHtnWiringAcceptance
from agent_orchestrator.planning.htn.observers.code import code_observers
from agent_orchestrator.planning.htn.world import build_planning_world
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.store import Store
from agent_orchestrator.testing.fixtures import RoleScriptedProvider


def test_an_old_profile_mission_mid_turn_does_not_stop_startup(tmp_path):
    child = subprocess.run([sys.executable, str(Path(__file__).with_name('crash_seed.py')),
        str(tmp_path), 'after_executor'], capture_output=True, text=True, timeout=60, check=False)
    assert child.returncode == 84, (child.returncode, child.stdout, child.stderr)
    source = json.loads((tmp_path / 'recovery-source.json').read_text())
    config = _config(tmp_path)
    store = Store.open(config.orchestrator_db)
    try:
        row = store.connection.execute(
            'SELECT json FROM mission_domains WHERE mission_id=?', (source['mission_id'],)).fetchone()
        body = json.loads(row[0])
        body.update(schema=1, planner_floor=[], synthesis_default_policy=[],
                    conflict_template={'policy': [], 'decides_with': 'code_test', 'probe': None})
        with store.transaction():
            store.connection.execute('UPDATE mission_domains SET json=? WHERE mission_id=?',
                                     (json.dumps(body, sort_keys=True), source['mission_id']))
        assert store.connection.execute(
            "SELECT COUNT(*) FROM dispatch_intents WHERE state IN ('AGENT_CREATED','SUBMITTED')"
        ).fetchone()[0] >= 1
    finally:
        store.close()

    def assemble(loop):
        mission = loop.store.get_mission(source['mission_id'])
        planning = build_planning_world(mission.id, domains=('code',), semantics=HtnStore(loop.store),
            deployed_layers=deployed_layers(loop._config.deployment_policy),
            observers=code_observers(source['repository'], allow_test_execution=True))
        loop.install_hierarchical(planning=planning)
        loop.install_taskgraph(TaskGraphDeploymentPorts(tenant_id=mission.tenant_id,
            principal=Principal(source['issuer_id']), deployment_acceptance=InstalledHtnWiringAcceptance(),
            graph_budget=DEFAULT_PROJECTION_BUDGET))

    async def recover():
        provider = RoleScriptedProvider({})
        async with Orchestrator(config, provider, startup_assembly=assemble) as loop:
            await loop.recover()
            await loop._cycle()
            stopped = loop.store.get_mission(source['mission_id'])
            assert stopped.status is MissionStatus.FAILED
            failed = [e for e in loop.store.list_events(stopped.id) if e.type == 'MissionFailed']
            assert len(failed) == 1 and 'unsupported_domain_profile' in json.dumps(failed[0].payload)
            assert provider.calls == 0
    asyncio.run(recover())
