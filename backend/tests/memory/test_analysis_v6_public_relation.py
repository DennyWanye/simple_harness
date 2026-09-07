"""v6 端到端：假 Provider 提案（claim + procedure + applies_to 关系）经真实 outbox/analysis job
写入公开 Memory SDK 后，twin graph 必须出现一条 applies_to 边；不写 SDK 私有 SQL。"""
import pytest

from tests.memory.test_analysis_proposal_v6 import TEXT, _proposal, _relation
from tests.memory.test_procedure_adoption import public_env
from tests.sdk_adapters import s5b_closure_harness as ch, s5b_memory_harness as mh


@pytest.mark.asyncio
async def test_v6_relation_materializes_as_public_graph_edge(tmp_path):
    env = await mh.bound_turn_run(tmp_path, "v6-relation-run", text=TEXT)
    await mh.finish_clean_run(env)
    item_id = mh.item_id(env)
    proposal = _proposal(item_id, _relation("rel-1", item_id, "整理文件时备份目录用外接硬盘", "claim-lang", "procedure-1"))
    adapter = ch.FakeAdapter([mh.proposal_call(proposal["operations"])])
    menv = public_env(env, adapter, version=6)
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
        assert len(adapter.calls) == 1
        # 线上请求确实按 v6 协议发出（schema 含 semantic_relation 分支）。
        from simple_harness import thaw_json
        item_schema = thaw_json(adapter.calls[0].tools[0].parameters)["properties"]["operations"]["items"]
        kinds = [b["properties"]["memory_type"]["enum"] for b in item_schema["anyOf"]]
        assert ["semantic_relation"] in kinds
        manager = await menv.runtime.manager()
        view = await manager.get_twin_graph_view(principal=menv.runtime.principal())
        visible = [n for n in view.nodes if not n.redacted]
        assert sorted(n.memory_type for n in visible) == ["procedure", "semantic"]
        assert len(view.edges) == 1
        edge = view.edges[0]
        assert edge.relation_kind == "applies_to" and edge.label == "applies_to"
        by_id = {n.node_id: n for n in visible}
        assert by_id[edge.source_node_id].memory_type == "semantic"
        assert by_id[edge.target_node_id].memory_type == "procedure"
        [(request_json,)] = await mh.memory_rows(menv, "SELECT request_json FROM analysis_batches WHERE state='applied'")
        assert '"host-analysis-prompt/v6"' in request_json
    finally:
        await mh.close(menv)
