# SPDX-License-Identifier: Apache-2.0
"""联测 S4（真实模型，``--run-real-provider`` 才跑）：计划提交之后、回执还没交回时进程强退。

子进程在产品同形部署上用真实模型跑到"计划已提交、第一条派发意图还没写"这一点退出
（``crash_seed.py <dir> after_commit real``）；本进程用同一个数据目录、同一个真实模型重开，跑到任务
结束。只断言恢复的秩序，不断言模型写了什么：

* 重开后第 1 版计划还是原来那一次提交（命令号 = ``plan:`` + 原规划意图号），没有第二份；
* 原规划意图的那条回复没有被再问一次（同一意图只有一条决定，哈希与强退前一致）；
* 任务完成。
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'agents'))

from production_fixture import product_loop, root_of  # noqa: E402
from real_provider_config import build_real_provider, resolve_real_provider  # noqa: E402

from agent_orchestrator.storage.store import Store  # noqa: E402
from agent_orchestrator.storage.taskgraph_store import TaskGraphStore  # noqa: E402

pytestmark = pytest.mark.real_provider


def test_real_model_plan_commit_survives_a_lost_receipt(tmp_path):
    config = resolve_real_provider()
    if config is None:
        pytest.skip('no real provider configured')
    child = subprocess.run([sys.executable, str(Path(__file__).with_name('crash_seed.py')), str(tmp_path),
                            'after_commit', 'real'], capture_output=True, text=True, timeout=1200, check=False)
    (tmp_path / 'child.log').write_text(child.stdout + child.stderr)
    assert child.returncode == 82, (child.returncode, child.stdout[-2000:], child.stderr[-2000:])
    source = json.loads((tmp_path / 'recovery-source.json').read_text())
    store = Store.open(root_of(tmp_path) / 'orchestrator.db')
    try:
        assert store.connection.execute('SELECT COUNT(*) FROM taskgraph_revision_records').fetchone()[0] == 1
        assert store.connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] == 0
    finally:
        store.close()

    async def recover():
        async with product_loop(tmp_path, build_real_provider(config)) as product:
            mission_id = source['mission_id']
            mission = await asyncio.wait_for(product.run_until_settled(mission_id, rounds=200, timeout=120), 2400)
            store = product.store
            record = TaskGraphStore(store).read_revision(mission_id, 1).record
            assert record.command_id == 'plan:' + source['intent_id']
            first = [e.payload for e in store.list_events(mission_id)
                     if e.type == 'PlanRevisionCommitted' and e.payload['base_plan_revision'] == 0]
            assert len(first) == 1
            decisions = [tuple(r) for r in store.connection.execute(
                'SELECT d.raw_output_hash FROM planning_decisions d JOIN planning_requests r '
                'ON r.request_id=d.request_id WHERE r.intent_id=?', (source['intent_id'],))]
            assert decisions == [(source['raw_output_hash'],)]
            report = {'status': str(mission.status.value), 'plan_revisions': store.connection.execute(
                'SELECT COUNT(*) FROM plan_revisions').fetchone()[0],
                'attempts': store.connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],
                'stop_reason': (mission.final_report or {}).get('stop_reason')}
            (tmp_path / 'report.json').write_text(json.dumps(report, ensure_ascii=False) + '\n')
            assert str(mission.status.value) == 'COMPLETED', report

    asyncio.run(recover())
