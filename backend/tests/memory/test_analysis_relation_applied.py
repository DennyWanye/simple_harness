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
2. 这条端点能不能**下发**取决于**装着的 SDK**，两条分支都在本用例里断言：
   * 0.6.31–0.6.35：`check_history_visibility` 对 Procedure 恒判 stale，Host 在下发前
     按名扣下（`sdk_procedure_applicability_gate_unavailable`）。0.6.31–0.6.34 上还叠加
     第二条（端点解析判「分类缺失」，放行会让整批分析死掉），0.6.35 已修好那一条，
     所以扣留的理由码只能命名**可见性门**——命名端点解析在 0.6.35 上是假的；
   * **0.6.36 起（F-S1b）**：`check_history_visibility` 接受本车道显式提交的
     applied-use 指纹，扣留解除，端点被下发、并被持久进候选快照。
3. 无论走哪条分支，这一批分析都照常 `applied`，本轮其它记忆一条不丢。

备忘录 `DECISION-S-RELATION-KEYERROR.md` §F-S1 与
`DECISION-F-S1B-PROCEDURE-ENDPOINT-LIFT.md` 记了两条分支的由来。
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
async def test_a_really_used_procedure_reaches_the_endpoint_channel(tmp_path):
    async with session(tmp_path) as ctx:
        revision = ctx.revision
        # `_cognitive_recall_state_allowed` 只让 active/reinforced 的 Procedure 参与召回，
        # SDK 的资格阶梯是 draft → eligible_for_activation → active：必须三次各自独立的
        # 真实成功执行，不能伪造次数。
        last_use = None
        for index, expected in enumerate(("draft", "eligible_for_activation", "active"), 1):
            group, _scope = await execute(ctx, index, revision=revision)
            manager = await ctx.memory.manager()
            await ctx.memory.procedure_runtime.observe_group(group, manager)
            last_use = await ctx.memory.procedure_runtime.store.use_for_run(
                group.terminal_source[0].run_id)
            applied = await ctx.memory.procedure_runtime.store.journal(last_use["use_id"], "applied")
            assert applied["result"]["lifecycle_state"] == expected
            revision = applied["result"]["committed_revision"]
        use = last_use
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
        # 这条流程被召回到了。指纹被持久进快照，`check` 复核时用的就是这一份。
        assert sorted(snapshot["relation_applicability_fingerprints"]) == sorted(fingerprints)
        assert use["applicability_fingerprint"] in snapshot["relation_applicability_fingerprints"]
        if not sc.sdk_offline_applicability_capable():
            # 0.6.31–0.6.35：按名扣下，代价只落在这一个端点上。
            assert [row["reason"] for row in snapshot["relation_candidate_reasons"]] == [
                sc.RELATION_MEMBER_ENDPOINT_UNVERIFIABLE]
            withheld = snapshot["relation_candidate_reasons"][0]
            assert withheld["memory_id"] == ctx.memory_id
            assert withheld["detail"] == sc.SDK_PROCEDURE_APPLICABILITY_GATE_UNAVAILABLE
            assert snapshot["relation_candidates"] == []
            # 被扣下的端点不会出现在提示体里，模型无从引用一个 SDK 解析不了的 key。
            assert adapter.bodies[-1]["procedure_candidates"] == []
            return
        # 0.6.36 起：扣留解除，端点被下发。
        assert snapshot["relation_candidate_reasons"] == []
        assert [row["memory_id"] for row in snapshot["relation_candidates"]] == [ctx.memory_id]
        assert [row["memory_type"] for row in snapshot["relation_candidates"]] == ["procedure"]
        assert [row["candidate_key"] for row in adapter.bodies[-1]["procedure_candidates"]] == [
            snapshot["relation_candidates"][0]["candidate_key"]]
