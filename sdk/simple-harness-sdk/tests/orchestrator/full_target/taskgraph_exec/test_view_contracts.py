# SPDX-License-Identifier: Apache-2.0
"""三个只读视图走严格编解码，返回物与对外合同 Schema 一致（第 2 批 T06、T07）。

生产来源的任务：视图、"为什么还没开工"解释、收敛视图三种返回都要按各自的 Schema 合规；收敛视图是
v2（``schema_version=2``，带 ``blocked_notifications``）；已验证修订的网络文档也按它的 Schema 合规。
视图组装出不合规的文档时报 ``GRAPH_INTEGRITY``，不把半成品发给界面。

**改坏检验**：收敛视图不经 codec 直接返回字典并多带一个字段 → 第一条变红。
"""
import asyncio

import pytest

from production_fixture import enabled_world
from agent_orchestrator.api.taskgraph import TaskGraphReadError
from agent_orchestrator.graph import schemas
from agent_orchestrator.graph.view_contracts import TaskGraphViewV1
from agent_orchestrator.storage.taskgraph_store import TaskGraphStore
from agent_orchestrator.testing.schema_oracle import violations


def test_read_views_conform_to_their_published_schemas(tmp_path):
    async def case():
        async with enabled_world(tmp_path, key='tg-view-contracts', hold_worker=True) as world:
            await world.commit_seed()
            store, mission = world.loop.store, world.mission.id
            reads = world.graph.reads
            view = reads.snapshot(mission)
            assert violations(schemas.load_schema('taskgraph-view-v1'), view) == []
            assert view['nodes'], 'the seed plan has occurrences'
            explanation = reads.why_not_ready(mission, view['nodes'][0]['occurrence_id'])
            assert violations(schemas.load_schema('taskgraph-explanation-v1'), explanation) == []
            assert explanation['read_token'] == view['read_token']
            convergence = reads.convergence(mission)
            assert violations(schemas.load_schema('taskgraph-convergence-view-v2'), convergence) == []
            assert convergence['schema_version'] == 2 and convergence['blocked_notifications'] == []
            historical = reads.snapshot(mission, revision=1)
            assert violations(schemas.load_schema('taskgraph-view-v1'), historical) == []
            document = TaskGraphStore(store).read_revision(mission, 1).record.document.to_json()
            assert violations(schemas.load_schema('network-document-v1'), document) == []
    asyncio.run(case())


def test_a_view_that_violates_its_contract_is_graph_integrity_not_a_half_document(tmp_path, monkeypatch):
    async def case():
        async with enabled_world(tmp_path, key='tg-view-contract-breach', hold_worker=True) as world:
            await world.commit_seed()
            reads, mission = world.graph.reads, world.mission.id
            real = TaskGraphViewV1.from_json

            def corrupt(value):
                return real({**value, 'view_mode': 'SOMETHING_ELSE'})

            monkeypatch.setattr(TaskGraphViewV1, 'from_json', staticmethod(corrupt))
            with pytest.raises(TaskGraphReadError) as caught:
                reads.snapshot(mission)
            assert caught.value.error.code == 'GRAPH_INTEGRITY'
    asyncio.run(case())
