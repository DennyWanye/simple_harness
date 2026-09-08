# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 S 正向面：真实被用过的 Procedure 走完整条关系端点通道。

这条链路一步不伪造：真实前台 Run 绑定并执行流程的两个步骤（真实写文件、真实 Scope 终态），
`observe_group` 走公开 SDK 记录观测，三次独立成功后流程进入 `active`，`procedure_uses`
落下三行真实使用记录，分析车道再对同一批证据跑 v8 批次。

它钉住三件事：

1. 分析车道的适用性指纹来自**持久化的真实使用**，不再向「活着的 Run」提问（主干在这里
   `KeyError`，见 `test_analysis_relation_candidate_audit.py`）；SDK 的适用性门因此真的
   放行了这条流程，它出现在候选召回里；
2. 但 SDK 0.6.31 解析不了任何 Procedure 关系端点（见 `_endpoint_unverifiable` 的两条实测），
   所以 Host **在下发前**就把它扣下，并留下具名理由码；
3. 扣下的代价只落在这一个端点上：这一批分析照常 `applied`，本轮其它记忆一条不丢。

第 2 条不是本次想要的终局，是本次量到的事实：放行它会让整批分析以
`MemoryCorruptionError('relation endpoint classification is missing')` 死掉，比事故本身更糟。
备忘录 `DECISION-S-RELATION-KEYERROR.md` §F-S1 记了解除条件。
"""
from __future__ import annotations

import json
import sqlite3
import time

import pytest
from simple_harness_memory import MemoryManager

from deskpet.memory import semantic_correction as sc
from deskpet.memory.analysis_executor import binding_model_config_hash
from deskpet.memory.memory_ingestion_outbox import build_worker_config
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from tests.memory.test_procedure_recovery_runtime import execute, session
from tests.sdk_adapters import s5b_memory_harness as mh


class BodyAdapter:
    """Records the analysis prompt body and proposes nothing."""

    def __init__(self):
        self.bodies = []

    async def invoke(self, request, *, cancel):
        self.bodies.append(json.loads(request.messages[-1].content.split("\n", 1)[1]))
        return mh.proposal_call([], outcome="no_mutation", provider_request_id="analysis-none")


async def _analysis_runtime(ctx, tmp_path, adapter):
    async def builder(path, **kwargs):
        return await MemoryManager.build_human_memory_v7(path, **kwargs, allow_development_embedder=True)

    return compose_human_memory_runtime(ctx.state, tmp_path / "memory.db",
        adapter_factory=lambda _: adapter, backend_factory=builder,
        clock=lambda: time.time() + ctx.offset[0])


def _analysis_config(state):
    with sqlite3.connect(state) as db:
        [(lineage_json,)] = db.execute(
            "SELECT analysis_lineage_json FROM memory_ingestion_outbox ORDER BY created_at DESC LIMIT 1"
        ).fetchall()
    lineage = json.loads(lineage_json)
    binding = lineage["run_binding"]
    return build_worker_config(provider_id=binding["provider_id"], model_id=binding["model_id"],
        model_config_hash=binding_model_config_hash(binding,
            endpoint_identity=lineage.get("endpoint_identity")),
        deadline_ms=20_000, max_attempts=1)


def _snapshots(state):
    with sqlite3.connect(state) as db:
        return [json.loads(row[0]) for row in db.execute(
            "SELECT payload_json FROM human_memory_evidence "
            "WHERE source_ref LIKE 'analysis-candidates-%' ORDER BY rowid").fetchall()]


@pytest.mark.asyncio
async def test_a_really_used_procedure_reaches_the_endpoint_channel_and_is_withheld_by_name(tmp_path):
    async with session(tmp_path) as ctx:
        revision = ctx.revision
        # `_cognitive_recall_state_allowed` 只让 active/reinforced 的 Procedure 参与召回，
        # SDK 的资格阶梯是 draft → eligible_for_activation → active：必须三次各自独立的
        # 真实成功执行，不能伪造次数。
        for index, expected in enumerate(("draft", "eligible_for_activation", "active"), 1):
            group, _scope = await execute(ctx, index, revision=revision)
            manager = await ctx.memory.manager()
            await ctx.memory.procedure_runtime.observe_group(group, manager)
            use = await ctx.memory.procedure_runtime.store.use_for_run(group.terminal_source[0].run_id)
            applied = await ctx.memory.procedure_runtime.store.journal(use["use_id"], "applied")
            assert applied["result"]["lifecycle_state"] == expected
            revision = applied["result"]["committed_revision"]
        # 每一次真实使用留下的适用性指纹，就是分析车道离线时的诚实答案。
        fingerprints = await ctx.memory.procedure_runtime.applied_use_fingerprints()
        assert use["applicability_fingerprint"] in fingerprints
        await ctx.memory.close()

        adapter = BodyAdapter()
        ctx.memory = await _analysis_runtime(ctx, tmp_path, adapter)
        runner = await ctx.memory.job_runner(ctx.memory.analysis_authority, _analysis_config(ctx.state),
                                             worker_id="relation-analysis",
                                             now=lambda: time.time() + ctx.offset[0])
        # 第一批是 `create_draft` 直接落的 fixture 证据，它没有 outbox 血缘
        # （`analysis_binding_unresolved`）；`max_attempts=1` 让它一次进 dead_letter，
        # 下一批才是真实前台轮次。
        assert str(await runner.run_once()) == "dead_letter"
        # 关键：这一批**照常成功**。主干上它会先 `KeyError` 丢掉整条通道；若把这条端点
        # 放出去，它会以 MemoryCorruptionError 整批失败。
        assert str(await runner.run_once()) == "applied"

        snapshot = _snapshots(ctx.state)[-1]
        assert snapshot["relation_candidates_unavailable"] is False
        # SDK 的适用性门确实放行了（没有 `relation_procedure_applicability_absent`），
        # 这条流程被召回到了，然后被 Host 按名扣下。
        assert [row["reason"] for row in snapshot["relation_candidate_reasons"]] == [
            sc.RELATION_MEMBER_ENDPOINT_UNVERIFIABLE]
        withheld = snapshot["relation_candidate_reasons"][0]
        assert withheld["memory_id"] == ctx.memory_id
        assert withheld["detail"] == "sdk_procedure_endpoint_unresolvable"
        assert snapshot["relation_candidates"] == []
        # 被扣下的端点不会出现在提示体里，模型无从引用一个 SDK 解析不了的 key。
        assert adapter.bodies[-1]["procedure_candidates"] == []
