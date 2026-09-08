"""事件 L：Host 真的在 v8 请求里下发关系端点候选通道，且不污染 v3..v7 的持久化线格式。

关系端点的编译规则由 `test_analysis_proposal_v8.py` 覆盖（含「已有事实 applies_to 已有流程」
的成功形状与全部拒绝理由）。这里覆盖的是它上游的 Host 装配：

* v8 的分析提示体里出现 `procedure_candidates`（哪怕为空），v7 里**不能**出现——
  `bind_attempt` 会对提示体取哈希，多一个键就会让旧请求重放失败；
* 关系候选走的是同一条公开 typed recall，并把 Host 的 procedure 适用性指纹带进去，
  因此 SDK 自己的适用性/调度门仍然决定哪些 Procedure/Prospective 可以露面
  （从未被使用过的 Procedure、没有 accepted 调度登记的 Prospective 都不出现）；
* 这条通道是**附加**的：它失败只降级成空列表，不会让本轮分析失败。
"""
import json

import pytest

from deskpet.memory import analysis_proposal_v7 as v7
from deskpet.memory import analysis_proposal_v8 as v8
from tests.memory.test_semantic_correction import memory_env
from tests.sdk_adapters import s5b_closure_harness as ch
from tests.sdk_adapters import s5b_memory_harness as mh

ORIGIN_TEXT = "记住：我做资料校对时，统一用 Python 3.12 跑脚本。"
TURN15 = "记住：秋分资料整理这套校对流程，就按我前面说的 Python 环境执行。"
PREDICATE = "proofreading_python_version"
VALUE = "Python 3.12"


class _BodyAdapter:
    """Records the analysis prompt body and proposes nothing."""

    def __init__(self):
        self.bodies = []

    async def invoke(self, request, *, cancel):
        self.bodies.append(json.loads(request.messages[-1].content.split("\n", 1)[1]))
        return mh.proposal_call([], outcome="no_mutation", provider_request_id="analysis-none")


async def _seed(tmp_path):
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
async def test_v8_request_carries_a_relation_endpoint_candidate_channel(tmp_path):
    env = await _seed(tmp_path)
    env2 = await mh.next_turn_run(env, "relation-run", text=TURN15, delivery_key="turn-15")
    await mh.finish_clean_run(env2)
    adapter = _BodyAdapter()
    menv = memory_env(env2, adapter)
    # v9 is the current protocol (事件 T); this case is about v8's own assembly, so pin it.
    from dataclasses import replace
    menv.config = replace(menv.config, prompt_version=v8.PROMPT_VERSION,
        result_schema_version=v8.RESULT_SCHEMA_VERSION, policy_version=v8.POLICY_VERSION,
        validator_version=v8.VALIDATOR_VERSION)
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
        [body] = adapter.bodies
        # The existing fact is offered as a relation *source* candidate today.
        assert [c["semantic"]["object_value"] for c in body["semantic_candidates"]] == [VALUE]
        # The target channel exists on the v8 wire even when nothing qualifies: no
        # Procedure has ever been used and no Prospective is scheduled in this store,
        # so the SDK's own gates keep it empty rather than the Host inventing rows.
        assert body["procedure_candidates"] == []
        # …and the additive channel did not degrade: no exception was swallowed.
        [(payload,)] = mh.rows(env2.db_path,
            "SELECT payload_json FROM human_memory_evidence "
            "WHERE source_ref LIKE 'analysis-candidates-%' ORDER BY rowid DESC LIMIT 1")
        snapshot = json.loads(payload)
        assert snapshot["relation_candidates"] == []
        assert snapshot["relation_candidates_unavailable"] is False
        [(request_json,)] = await mh.memory_rows(menv,
            "SELECT request_json FROM analysis_batches WHERE state='applied' "
            "ORDER BY rowid DESC LIMIT 1")
        assert f'"{v8.PROMPT_VERSION}"' in request_json
    finally:
        await mh.close(menv)


@pytest.mark.asyncio
async def test_persisted_v7_prompt_body_gains_no_new_key(tmp_path):
    """``bind_attempt`` hashes the prompt body — a v7 replay must render byte identically."""
    env = await _seed(tmp_path)
    env2 = await mh.next_turn_run(env, "v7-run", text=TURN15, delivery_key="turn-15-v7")
    await mh.finish_clean_run(env2)
    adapter = _BodyAdapter()
    menv = memory_env(env2, adapter)
    from dataclasses import replace
    menv.config = replace(menv.config, prompt_version=v7.PROMPT_VERSION,
        result_schema_version=v7.RESULT_SCHEMA_VERSION, policy_version=v7.POLICY_VERSION)
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
        [body] = adapter.bodies
        assert "procedure_candidates" not in body
        assert set(body) == {"now_iso", "subject", "evidence_items", "semantic_candidates"}
    finally:
        await mh.close(menv)
