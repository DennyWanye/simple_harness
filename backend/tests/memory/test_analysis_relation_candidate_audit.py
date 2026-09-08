# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 S：分析车道拿自己的 run_id 去问「当前 Tool 适用性」，整条关系候选通道被 KeyError 吞掉。

原生 attempt 8（`.local-test-evidence/2026-09-09/native-a6-run8/`）里 20 个分析批次
每一个都记了同一条告警：

    {"error_message": "'fb154920-9038-5bd4-8a4f-5c6875c2464a'", "error_type": "KeyError",
     "event": "memory.analysis_relation_candidates_unavailable key=…"}

那个 UUID 不是记忆 id，而是**分析车道自己的 run_id**（`analysis_batches.request_json`
的 `/run_id` 与 `/disclosure_context/run_id`，20 个批次逐字相同）。
`SdkRunToolAuthorityRegistry.resolve` 只认活着的前台 Run，并且在 Run 终态时把记录
`pop` 掉（native run 8：前台 `COMPLETED` 于 1788896447.76，分析批次 `reserved_at`
1788896448.24），所以这条查询**永远**只会得到 `KeyError(<analysis run id>)`。

它落在 `_relation_candidates` 唯一的一层 `except Exception` 里，于是整条通道——
连同根本不依赖适用性指纹的 Prospective 端点——每一批都被丢弃，审计里只剩一个布尔值。

本文件钉死三件事：
1. 分析车道不再向「活着的 Run」提问，KeyError 不再发生；
2. 端点缺席时必须留下**具名理由码**，而不是匿名的空列表；
3. `current_fingerprints` 对一个没有 Tool 授权的 Run 回答 `()`，而不是抛异常。
"""
import json
import sqlite3

import pytest
from simple_harness.contracts import canonical_json

from deskpet.memory import semantic_correction as sc
from deskpet.memory.procedure_applicability import ProcedureUseRejected
from deskpet.memory.procedure_runtime import ProcedureRuntime
from deskpet.memory.procedure_schema import initialize_procedure_state_db
from deskpet.memory.procedure_use_store import ProcedureUseStore
from deskpet.sdk_adapters.tool_authority import SdkRunToolAuthorityRegistry
from deskpet.task_scope.protocol import canonical_hash
from tests.memory.test_semantic_correction import memory_env
from tests.sdk_adapters import s5b_closure_harness as ch
from tests.sdk_adapters import s5b_memory_harness as mh

TEXT = "记住：我做资料校对时，统一用 Python 3.12 跑脚本。"


def _reasons(db_path):
    [(payload,)] = mh.rows(db_path,
        "SELECT payload_json FROM human_memory_evidence "
        "WHERE source_ref LIKE 'analysis-candidates-%' ORDER BY rowid DESC LIMIT 1")
    return json.loads(payload)


@pytest.mark.asyncio
async def test_a_bound_tool_registry_no_longer_kills_the_relation_channel(tmp_path):
    """生产装配（`main.py` 会 `bind_tools`）下跑一整批分析。

    主干上这一批会以 `relation_candidates_unavailable=True` 结束——因为
    `current_fingerprints` 对分析 run 抛 `KeyError`。测试环境从来没有 `bind_tools`，
    所以既有用例看不见这个缺陷；这里补上生产才有的那一步。

    修复之后这次 `bind_tools` 对本车道已经是**惰性**的（分析车道不再查注册表），
    它留在这里是对着主干的变更探测器。
    """
    env = await mh.bound_turn_run(tmp_path, "relation-audit-run", text=TEXT)
    await mh.finish_clean_run(env)
    # 这套 harness 的 state.db 是逐块拼出来的；生产里 Procedure 域 schema 一定在。
    await initialize_procedure_state_db(env.db_path)
    adapter = ch.FakeAdapter([mh.proposal_call([], outcome="no_mutation",
                                               provider_request_id="analysis-none")])
    menv = memory_env(env, adapter)
    # 生产里 main.py:8657 就是这么绑的；registry 里没有分析 run 的记录。
    menv.runtime.procedure_runtime.bind_tools(SdkRunToolAuthorityRegistry(), object())
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
        snapshot = _reasons(env.db_path)
        assert snapshot["relation_candidates_unavailable"] is False
        assert snapshot["relation_candidates"] == []
        # 端点为空必须有名有姓：本 store 里没有任何一次真实 Procedure 使用，
        # 所以 SDK 的适用性门本来就不会放行任何 Procedure（F-L1）。
        assert [row["reason"] for row in snapshot["relation_candidate_reasons"]] == [
            sc.RELATION_APPLICABILITY_ABSENT]
        assert snapshot["relation_candidate_reasons"][0] == {
            "reason": sc.RELATION_APPLICABILITY_ABSENT,
            "memory_id": None, "revision": None, "detail": None}
        # 主干在这里记的是 True + 一条只带 UUID 的 KeyError。
    finally:
        await mh.close(menv)


@pytest.mark.asyncio
async def test_an_applicability_failure_is_named_and_does_not_degrade_the_channel(tmp_path):
    """指纹解析失败时：召回照跑、理由码入审计、通道不再整条标记为不可用。

    「代价只落在 Procedure 那一端」这条更强的性质由
    `test_analysis_relation_prospective_applied.py` 证明——那里在同一处注入失败，
    Prospective 端点仍被下发并写进 `cognitive_relations`；本 store 里没有任何可露面的
    端点，所以这里只能证明前半段。
    """
    env = await mh.bound_turn_run(tmp_path, "relation-fault-run", text=TEXT)
    await mh.finish_clean_run(env)
    adapter = ch.FakeAdapter([mh.proposal_call([], outcome="no_mutation",
                                               provider_request_id="analysis-none")])
    menv = memory_env(env, adapter)

    async def broken():
        raise RuntimeError("applicability store is gone")

    menv.runtime._memory_action_authority._procedure_fingerprints = broken
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
        snapshot = _reasons(env.db_path)
        assert snapshot["relation_candidates_unavailable"] is False
        # 「解析失败」和「解析成功但没有可用指纹」是两件事，不能混成一个码。
        assert [row["reason"] for row in snapshot["relation_candidate_reasons"]] == [
            sc.RELATION_APPLICABILITY_UNAVAILABLE]
        assert snapshot["relation_candidate_reasons"][0]["detail"] == "RuntimeError"
    finally:
        await mh.close(menv)


@pytest.mark.asyncio
async def test_current_fingerprints_answers_an_unknown_run_instead_of_raising(tmp_path):
    state = tmp_path / "state.db"
    await initialize_procedure_state_db(state)

    class _Principal:
        actor_id = "deskpet-local-owner-v1"

    store = ProcedureUseStore(state, principal=_Principal(), clock=lambda: 1.0)
    runtime = ProcedureRuntime(store=store, runtime_getter=lambda: None)
    # 没有 bind_tools：既有的早退分支。
    assert await runtime.current_fingerprints("any-run") == ()
    runtime.bind_tools(SdkRunToolAuthorityRegistry(), object())
    # 绑了、但 registry 里没有这个 Run —— 主干在这里抛 KeyError('analysis-run')。
    assert await runtime.current_fingerprints("analysis-run") == ()


@pytest.mark.asyncio
async def test_applied_use_fingerprints_counts_only_consumed_observations(tmp_path):
    state = tmp_path / "state.db"
    await initialize_procedure_state_db(state)

    class _Principal:
        actor_id = "deskpet-local-owner-v1"

    store = ProcedureUseStore(state, principal=_Principal(), clock=lambda: 1.0)
    runtime = ProcedureRuntime(store=store, runtime_getter=lambda: None)
    assert await runtime.applied_use_fingerprints() == ()

    def insert(use_id, scope, fingerprint, created_at, *, corrupt=False):
        body = dict(use_id=use_id, subject=_Principal.actor_id, task_scope_id=scope,
                    sdk_run_id="run-" + use_id, memory_id="memory-" + use_id, target_revision=1,
                    applicability_fingerprint=fingerprint)
        text = canonical_json(body)
        with sqlite3.connect(state) as db:
            db.execute("INSERT INTO task_scopes(task_scope_id,subject,title,created_at) VALUES(?,?,?,?)",
                       (scope, _Principal.actor_id, "t", created_at))
            db.execute("INSERT INTO procedure_uses(use_id,subject,task_scope_id,sdk_run_id,memory_id,"
                       "target_revision,body_json,body_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (use_id, _Principal.actor_id, scope, body["sdk_run_id"], body["memory_id"], 1,
                        text, canonical_hash(body) if not corrupt else "0" * 64, created_at))

    insert("use-b", "scope-b", "fingerprint-b", 2.0)
    insert("use-a", "scope-a", "fingerprint-a", 1.0)
    # 只是「绑定过」还不算：观测必须被 SDK 消费掉（journal 阶段 applied）。
    assert await runtime.applied_use_fingerprints() == ()
    await store.journal("use-a", "applied", {"result": {"independent_successes": 1}})
    await store.journal("use-b", "rejected", {"reason": "procedure_observation_source_already_counted"})
    assert await runtime.applied_use_fingerprints() == ("fingerprint-a",)
    await store.journal("use-b", "applied", {"result": {"independent_successes": 1}})
    # 排序而不是按 created_at：同一批分析读到的是同一个集合。
    assert await runtime.applied_use_fingerprints() == ("fingerprint-a", "fingerprint-b")


@pytest.mark.asyncio
async def test_a_tampered_use_row_is_not_swallowed(tmp_path):
    """篡改过的持久化行是 corruption 信号，不能被当成「少一个端点」悄悄跳过。"""
    state = tmp_path / "state.db"
    await initialize_procedure_state_db(state)

    class _Principal:
        actor_id = "deskpet-local-owner-v1"

    store = ProcedureUseStore(state, principal=_Principal(), clock=lambda: 1.0)
    runtime = ProcedureRuntime(store=store, runtime_getter=lambda: None)
    body = dict(use_id="use-x", subject=_Principal.actor_id, task_scope_id="scope-x",
                sdk_run_id="run-x", memory_id="memory-x", target_revision=1,
                applicability_fingerprint="fingerprint-x")
    with sqlite3.connect(state) as db:
        db.execute("INSERT INTO task_scopes(task_scope_id,subject,title,created_at) VALUES(?,?,?,?)",
                   ("scope-x", _Principal.actor_id, "t", 1.0))
        db.execute("INSERT INTO procedure_uses(use_id,subject,task_scope_id,sdk_run_id,memory_id,"
                   "target_revision,body_json,body_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   ("use-x", _Principal.actor_id, "scope-x", "run-x", "memory-x", 1,
                    canonical_json(body), "0" * 64, 1.0))
    with pytest.raises(ProcedureUseRejected, match="procedure_persisted_body_corrupt"):
        await runtime.applied_use_fingerprints()
