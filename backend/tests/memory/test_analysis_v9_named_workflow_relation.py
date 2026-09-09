"""事件 T 端到端：turn 15 的真实模型响应经真实 apply 路径落成一条 `applies_to` 边。

这条链路只有一处是录制的——**模型说了什么**。请求是 HM-TO-A6 第 9 次原生跑持久化下来的
turn-15 attempt-input（`.local-test-evidence/2026-09-09/native-a6-run9/primary-ui-8whts2lo/`
的 `state.db.human_memory_evidence` → `analysis-attempt-input-5c2f2d1b…`），响应是把那条请求
按 v9 提示词重放给 `deepseek-v4-pro` 得到的第 3 个样本（`DECISION-T-RELATION-FORM.md` §3）。
从那以后全是真的：真实 outbox、真实分析车道、真实候选通道、真实 Memory SDK 0.6.34 apply。

它钉住 A6-6 需要的四件事：

1. 本轮 accepted plan 里既有新建的流程节点，又有新建的 relation memory；
2. `cognitive_relations` 恰好 +1，`relation_kind='applies_to'`；
3. **source 端是 T1 那条已有事实的当前 revision**（不是本轮复制的一份副本——复制会造出
   第二个槽位，正是事件 L 要消灭的东西），target 端是本 plan 新建流程节点的 exact revision，
   且该 revision 的 `plan_id`/`plan_hash` 与关系行相同；
4. 普通图谱（`build_twin_graph_view`）真的显示这条边：semantic → procedure，
   relation memory 自己不成为节点。

第 3 条同时说明为什么 `00-PLAN.md` A6-6 那一行的「两端都是本 plan 的 exact revision」被改写：
按 SDK 0.6.34 的契约那要求本轮再造一条同值 semantic，而 `acceptance.md` 第 162 行只要求
「在同一 plan 创建节点与 relation memory」。
"""
import json

import pytest

from deskpet.memory import analysis_protocol
from tests.memory.test_semantic_correction import memory_env
from tests.sdk_adapters import s5b_closure_harness as ch
from tests.sdk_adapters import s5b_memory_harness as mh

ORIGIN_TEXT = "记住：我做资料校对时，统一用 Python 3.12 跑脚本。"
TURN15 = "记住：秋分资料整理这套校对流程，就按我前面说的 Python 环境执行。"
PREDICATE = "proofreading_script_python_version"
VALUE = "Python 3.12"
FLOW_NAME = "秋分资料整理校对流程"

# Recorded verbatim from the replay (deepseek-v4-pro, v9 arm, sample 3).  Only the two
# Host-assigned identities — the evidence item id and the candidate key — are substituted
# at call time, because they are minted per store and the model copies whatever it was given.
RECORDED_RESPONSE = {
    "outcome": "mutate",
    "operations": [
        {
            "operation_id": "op-proc-001",
            "action": "create",
            "candidate_key": "",
            "memory_type": "procedure",
            "evidence_item_id": "<evidence_item_id>",
            "exact_quote": TURN15,
            "reason_code": "用户回指“我前面说的 Python 环境”，且本句点名了可复用流程“秋分资料整理这套校对流程”"
                           "并明确决定照此执行（记住…就按…执行），按回指规则分支②建流程节点，不另造 semantic。",
            "procedure": {
                "name": FLOW_NAME,
                "steps": ["就按我前面说的 Python 环境执行"],
                "intent_kind": "adoption",
                "adoption_quote": TURN15,
            },
        },
        {
            "operation_id": "op-rel-001",
            "action": "create",
            "memory_type": "semantic_relation",
            "evidence_item_id": "<evidence_item_id>",
            "exact_quote": TURN15,
            "reason_code": "用户明确说该校对流程按此前说的 Python 环境执行，"
                           "故将已有 Python 版本事实与本次流程节点建立 applies_to 关系。",
            "semantic_relation": {
                "relation_kind": "applies_to",
                "source_candidate_key": "<candidate_key>",
                "target_operation_id": "op-proc-001",
            },
        },
    ],
}


class RecordedAdapter:
    """Replays the recorded turn-15 response against whatever identities this store minted."""

    def __init__(self):
        self.bodies = []

    async def invoke(self, request, *, cancel):
        body = json.loads(request.messages[-1].content.split("\n", 1)[1])
        self.bodies.append(body)
        item = body["evidence_items"][0]
        [candidate] = [c for c in body["semantic_candidates"]
                       if c["semantic"]["object_value"] == VALUE]
        raw = json.dumps(RECORDED_RESPONSE, ensure_ascii=False)
        raw = raw.replace("<evidence_item_id>", item["evidence_item_id"])
        raw = raw.replace("<candidate_key>", candidate["candidate_key"])
        return mh.proposal_call(json.loads(raw)["operations"], provider_request_id="analysis-t15")


async def _seed(tmp_path):
    """Turn 1: the fact the edge will point at, created by its own accepted plan."""
    env = await mh.bound_turn_run(tmp_path, "origin-run", text=ORIGIN_TEXT)
    await mh.finish_clean_run(env)
    adapter = ch.FakeAdapter([mh.proposal_call(
        [mh.semantic_op(mh.item_id(env), ORIGIN_TEXT, predicate=PREDICATE, object_value=VALUE)])])
    menv = memory_env(env, adapter)
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
    finally:
        await mh.close(menv)
    return env


@pytest.mark.asyncio
async def test_turn15_creates_the_flow_node_and_the_applies_to_edge_in_one_plan(tmp_path):
    env = await _seed(tmp_path)
    env2 = await mh.next_turn_run(env, "turn15-run", text=TURN15, delivery_key="turn-15")
    await mh.finish_clean_run(env2)
    adapter = RecordedAdapter()
    menv = memory_env(env2, adapter)
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
        # The lane's wire really carried both channels; the target channel was empty, which is
        # exactly why branch ② (create the node here) is the reachable one — F-L1.  The version
        # asserted is whatever protocol the lane is currently configured with (v9 when this test
        # was written, v10 since event AE inherited branch ②) — pinning a literal here would only
        # measure the protocol table, which `test_analysis_proposal_v10` already pins.
        [body] = adapter.bodies
        assert body["procedure_candidates"] == []
        assert [c["semantic"]["object_value"] for c in body["semantic_candidates"]] == [VALUE]
        [(request_json,)] = await mh.memory_rows(menv,
            "SELECT request_json FROM analysis_batches WHERE state='applied' "
            "ORDER BY rowid DESC LIMIT 1")
        assert f'"{analysis_protocol.PROMPT_VERSION}"' in request_json

        # 1) exactly one applies_to edge.
        relations = await mh.memory_rows(menv,
            "SELECT relation_kind, relation_domain, plan_id, plan_hash, source_memory_id, "
            "source_revision, target_memory_id, target_revision, relation_memory_id, "
            "relation_memory_revision FROM cognitive_relations")
        assert len(relations) == 1
        (kind, domain, plan_id, plan_hash, source_id, source_revision,
         target_id, target_revision, relation_memory_id, relation_memory_revision) = relations[0]
        assert (kind, domain) == ("applies_to", "knowledge")

        # 2) the source endpoint is T1's own memory at its current revision — no copy.
        heads = dict(await mh.memory_rows(menv,
            "SELECT memory_id, memory_type FROM cognitive_memory_heads"))
        assert heads[source_id] == "semantic" and heads[target_id] == "procedure"
        assert relation_memory_id in heads
        [(claim_predicate,)] = await mh.memory_rows(menv,
            "SELECT predicate FROM semantic_claims WHERE memory_id=? AND revision=?",
            (source_id, source_revision))
        assert claim_predicate == PREDICATE
        [(current,)] = await mh.memory_rows(menv,
            "SELECT current_revision FROM cognitive_memory_heads WHERE memory_id=?", (source_id,))
        assert current == source_revision
        # …and this turn minted no second copy of the value: the store holds exactly one
        # semantic *claim* (T1's).  The relation memory is also memory_type='semantic', which
        # is why the claim table — not the head count — is the honest place to look.
        claims = await mh.memory_rows(menv,
            "SELECT memory_id, subject_entity, predicate FROM semantic_claims")
        assert claims == [(source_id, "user:self", PREDICATE)]

        # 3) the created endpoint and the relation memory belong to THIS plan.
        assert target_revision == relation_memory_revision == 1
        created = await mh.memory_rows(menv,
            "SELECT memory_id, plan_id, plan_hash FROM cognitive_memory_revisions "
            "WHERE (memory_id=? AND revision=?) OR (memory_id=? AND revision=?)",
            (target_id, target_revision, relation_memory_id, relation_memory_revision))
        assert {row[0] for row in created} == {target_id, relation_memory_id}
        assert all((row[1], row[2]) == (plan_id, plan_hash) for row in created)
        # This is exactly `scripts/native/a6_verify.py::item_a6_6`'s reworded criterion:
        # the relation memory and *at least one* endpoint node were created by this plan,
        # both endpoints resolve at their exact revision, relation_memory_id is a head.
        # The source predates this plan, which is the whole point of the reworded A6-6 row.
        [(source_plan,)] = await mh.memory_rows(menv,
            "SELECT plan_id FROM cognitive_memory_revisions WHERE memory_id=? AND revision=?",
            (source_id, source_revision))
        assert source_plan != plan_id

        # 4) the ordinary twin graph shows the edge, and the relation memory is not a node.
        manager = await menv.runtime.manager()
        view = await manager.get_twin_graph_view(principal=menv.runtime.principal())
        visible = [n for n in view.nodes if not n.redacted]
        assert relation_memory_id not in {n.node_id.split("@")[0] for n in view.nodes}
        assert len(view.edges) == 1
        edge = view.edges[0]
        assert edge.relation_kind == "applies_to"
        by_id = {n.node_id: n for n in visible}
        assert by_id[edge.source_node_id].memory_type == "semantic"
        assert by_id[edge.target_node_id].memory_type == "procedure"
    finally:
        await mh.close(menv)
