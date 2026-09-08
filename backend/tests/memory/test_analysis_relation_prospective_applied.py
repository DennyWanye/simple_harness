# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 S 正向面（在 SDK 0.6.31 上可达的那一半）：已有记忆当关系端点，真的落进 `cognitive_relations`。

A6-6 要的是「同一 plan 里新建节点 + 新建 relation memory」。`applies_to` 的目标端可以是
Procedure 或 Prospective；Procedure 那一端在 0.6.31 上不可达
（见 `test_analysis_relation_applied.py` 与 `_endpoint_unverifiable` 的实测），Prospective
这一端可达，而且走的是**同一条**关系端点候选通道与**同一段**修复代码：

1. 第一批分析建出一条 Prospective 记忆；
2. `prospective_lane.tick()` 走公开 SDK 落下 accepted 调度登记——这正是 SDK 让
   Prospective 参与召回的门（`_cognitive_recall_type_authority_allowed_unlocked`）；
3. 第二批分析里，Host 在 `procedure_candidates` 下发这条已有提醒；
4. fixture 模型用 `source_operation_id`（本轮新建的环境事实）+ `target_candidate_key`
   （已有提醒）提一条 `applies_to`；
5. SDK 自己重解析端点后，`cognitive_relations` 出现一行，指向那条已有记忆的 exact revision。

主干上这条用例在第 3 步就断：`_relation_candidates` 的 `KeyError` 让整条通道不可用。
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest
from simple_harness_memory import MemoryManager

from deskpet.memory import semantic_correction as sc
from deskpet.memory.analysis_executor import binding_model_config_hash
from deskpet.memory.memory_ingestion_outbox import build_worker_config
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.sdk_adapters.tool_authority import SdkRunToolAuthorityRegistry
from tests.memory.test_procedure_recovery_runtime import execute, session
from tests.sdk_adapters import s5b_memory_harness as mh

REMINDER_ACTION = "复查记录和备份"
ENVIRONMENT_VALUE = "Python 3.12"


def _trigger_iso(now):
    return datetime.fromtimestamp(now + 86_400, tz=timezone(timedelta(hours=8))).isoformat(
        timespec="seconds")


class ScriptedAdapter:
    """First batch creates the reminder; the next one links a new fact to it."""

    def __init__(self, now):
        self.bodies = []
        self._now = now

    async def invoke(self, request, *, cancel):
        body = json.loads(request.messages[-1].content.split("\n", 1)[1])
        self.bodies.append(body)
        item = body["evidence_items"][0]
        base = {"evidence_item_id": item["evidence_item_id"], "exact_quote": item["text"],
                "reason_code": "explicit_future_action"}
        if not body["procedure_candidates"]:
            return mh.proposal_call([{"operation_id": "op-reminder", "memory_type": "prospective",
                **base, "prospective": {"action": REMINDER_ACTION,
                                        "trigger_at_iso": _trigger_iso(self._now()),
                                        "timezone": "Asia/Shanghai"}}],
                provider_request_id="analysis-reminder")
        claim = {"operation_id": "op-fact", "memory_type": "semantic", "action": "create",
                 "evidence_item_id": item["evidence_item_id"], "exact_quote": item["text"],
                 "reason_code": "explicit_user_statement",
                 "semantic": {"subject_entity": "user:self", "predicate": "record_backup_environment",
                              "object_value": ENVIRONMENT_VALUE}}
        relation = {"operation_id": "op-relation", "memory_type": "semantic_relation",
                    "action": "create", "evidence_item_id": item["evidence_item_id"],
                    "exact_quote": item["text"], "reason_code": "explicit_user_statement",
                    "semantic_relation": {"relation_kind": "applies_to",
                                          "source_operation_id": "op-fact",
                                          "target_candidate_key":
                                              body["procedure_candidates"][0]["candidate_key"]}}
        return mh.proposal_call([claim, relation], provider_request_id="analysis-relation")


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


@pytest.mark.asyncio
async def test_an_existing_prospective_endpoint_is_issued_and_the_relation_is_applied(tmp_path):
    async with session(tmp_path) as ctx:
        clock = lambda: time.time() + ctx.offset[0]
        await execute(ctx, 1)
        await execute(ctx, 2, revision=ctx.revision)
        await ctx.memory.close()

        async def builder(path, **kwargs):
            return await MemoryManager.build_human_memory_v7(path, **kwargs,
                                                             allow_development_embedder=True)

        adapter = ScriptedAdapter(clock)
        ctx.memory = compose_human_memory_runtime(ctx.state, tmp_path / "memory.db",
            adapter_factory=lambda _: adapter, backend_factory=builder, clock=clock)
        # 生产装配（main.py:8657）把活着的前台 Run 注册表绑到同一个 runtime 上；
        # 分析车道的 run 不在里面，主干正是在这里 KeyError。
        ctx.memory.procedure_runtime.bind_tools(SdkRunToolAuthorityRegistry(), object())
        runner = await ctx.memory.job_runner(ctx.memory.analysis_authority,
            _analysis_config(ctx.state), worker_id="relation-analysis", now=clock)
        # 第一批是 `create_draft` 直接落的 fixture 证据，没有 outbox 血缘；
        # `max_attempts=1` 让它一次进 dead_letter。
        assert str(await runner.run_once()) == "dead_letter"
        # 第二批：真实前台轮次 1 —— 建提醒。
        assert str(await runner.run_once()) == "applied"
        rows = await mh.memory_rows(mh.MemoryEnv(runtime=ctx.memory),
            "SELECT memory_id FROM cognitive_memory_heads WHERE memory_type='prospective'")
        [(memory_id,)] = rows

        # 公开 SDK 的调度登记：这是 Prospective 能参与召回的门，不是测试桩。
        await ctx.memory.prospective_lane.tick()
        assert ctx.memory.prospective_lane.last_registration_error is None
        from deskpet.memory.s5c_store import S5cStore
        accepted = await S5cStore(ctx.state, ctx.memory.principal()).accepted_registration(
            memory_id=memory_id, revision=1)
        assert accepted is not None

        # 第三批：真实前台轮次 2 —— 这一次端点候选里有那条提醒。
        # 同时把「适用性解析失败」注入进去：事故 S 的核心就是这一步的失败不该
        # 牵连不依赖适用性指纹的 Prospective 端点。主干在这里整条通道为空。
        async def broken():
            raise RuntimeError("applicability store is gone")

        ctx.memory._memory_action_authority._procedure_fingerprints = broken
        assert str(await runner.run_once()) == "applied"
        assert [row["memory_type"] for row in adapter.bodies[-1]["procedure_candidates"]] == [
            "prospective"]
        assert adapter.bodies[-1]["procedure_candidates"][0]["name"] == REMINDER_ACTION

        relations = await mh.memory_rows(mh.MemoryEnv(runtime=ctx.memory),
            "SELECT relation_kind,source_memory_id,target_memory_id,target_revision "
            "FROM cognitive_relations")
        assert len(relations) == 1
        kind, source_id, target_id, target_revision = relations[0]
        assert (kind, target_id, target_revision) == ("applies_to", memory_id, 1)
        assert source_id != memory_id

        with sqlite3.connect(ctx.state) as db:
            [(payload,)] = db.execute(
                "SELECT payload_json FROM human_memory_evidence "
                "WHERE source_ref LIKE 'analysis-candidates-%' ORDER BY rowid DESC LIMIT 1").fetchall()
        snapshot = json.loads(payload)
        assert snapshot["relation_candidates_unavailable"] is False
        assert [row["memory_id"] for row in snapshot["relation_candidates"]] == [memory_id]
        # 代价只落在 Procedure 那一端：理由码记下来，Prospective 端点照常下发并被应用。
        assert [row["reason"] for row in snapshot["relation_candidate_reasons"]] == [
            sc.RELATION_APPLICABILITY_UNAVAILABLE]
