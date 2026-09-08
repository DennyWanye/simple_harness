# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""F-S1b 端到端：真的被用过的 Procedure 当关系端点，落进 `cognitive_relations` 与孪生图。

A6-6 要的是「同一 plan 里新建节点 + 新建 relation memory」。`applies_to` 的目标端可以是
Procedure 或 Prospective；Prospective 那一半在
`test_analysis_relation_prospective_applied.py` 里已经端到端可达，Procedure 这一半此前被
两条 SDK 事实挡住（备忘 `DECISION-S-RELATION-KEYERROR.md` §3）：

1. 观测提交出来的 revision 没有自己的 `cognitive_classification_decisions` 行 →
   端点解析判损坏。**SDK 0.6.35 修好**（血缘上最近的已分类祖先管辖）。
2. `check_history_visibility` 对 Procedure 恒判 stale（写死空指纹集合）→ `check()` 打掉整批。
   **SDK 0.6.36 修好**（F-S1b：本车道显式提交 applied-use 指纹，Memory 用自己的
   `procedure_observations` 审计佐证）。

本用例一步不伪造：真实前台 Run 三次独立成功执行同一流程，`observe_group` 走公开 SDK，
流程进 `active`；分析车道第二批把它作为端点下发；fixture 模型用
`source_operation_id`（本轮新建的环境事实）+ `target_candidate_key`（那条流程）提一条
`applies_to`；SDK 自己重解析端点后落下一行关系，孪生图出现对应的边。

装的是 0.6.34/0.6.35 时整条链在第 2 步就该被 Host 按名扣下（那条分支由
`test_analysis_relation_applied.py` 断言），需要 0.6.36 的两项在本文件里显式 skip；
文件末尾那两项「门本身」的用例在两版 SDK 上都跑。
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

ENVIRONMENT_VALUE = "Python 3.12"

needs_0_6_36 = pytest.mark.skipif(
    not sc.sdk_offline_applicability_capable(),
    reason="Procedure relation endpoints need the SDK 0.6.36 applicability entry point (F-S1b)",
)


class ScriptedAdapter:
    """Links a new environment fact to whichever Procedure endpoint is issued."""

    def __init__(self):
        self.bodies = []
        self.linked = []

    async def invoke(self, request, *, cancel):
        body = json.loads(request.messages[-1].content.split("\n", 1)[1])
        self.bodies.append(body)
        endpoints = [row for row in body["procedure_candidates"]
                     if row["memory_type"] == "procedure"]
        if not endpoints or not body["evidence_items"]:
            return mh.proposal_call([], outcome="no_mutation", provider_request_id="analysis-none")
        item = body["evidence_items"][0]
        base = {"evidence_item_id": item["evidence_item_id"], "exact_quote": item["text"],
                "reason_code": "explicit_user_statement"}
        claim = {"operation_id": "op-fact", "memory_type": "semantic", "action": "create", **base,
                 "semantic": {"subject_entity": "user:self",
                              "predicate": "procedure_runtime_environment",
                              "object_value": ENVIRONMENT_VALUE}}
        relation = {"operation_id": "op-relation", "memory_type": "semantic_relation",
                    "action": "create", **base,
                    "semantic_relation": {"relation_kind": "applies_to",
                                          "source_operation_id": "op-fact",
                                          "target_candidate_key": endpoints[0]["candidate_key"]}}
        self.linked.append(endpoints[0]["candidate_key"])
        return mh.proposal_call([claim, relation], provider_request_id="analysis-relation")


async def _analysis_runtime(ctx, tmp_path, adapter):
    async def builder(path, **kwargs):
        return await MemoryManager.build_human_memory_v7(path, **kwargs,
                                                         allow_development_embedder=True)

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


@needs_0_6_36
@pytest.mark.asyncio
async def test_an_existing_procedure_endpoint_is_issued_and_the_relation_is_applied(tmp_path):
    async with session(tmp_path) as ctx:
        revision = ctx.revision
        # SDK 的资格阶梯：draft → eligible_for_activation → active，三次各自独立的真实
        # 成功执行，不能伪造次数；只有 active/reinforced 才进召回、才可能当端点。
        last_use = None
        for index, expected in enumerate(("draft", "eligible_for_activation", "active"), 1):
            group, _scope = await execute(ctx, index, revision=revision)
            manager = await ctx.memory.manager()
            await ctx.memory.procedure_runtime.observe_group(group, manager)
            last_use = await ctx.memory.procedure_runtime.store.use_for_run(
                group.terminal_source[0].run_id)
            applied = await ctx.memory.procedure_runtime.store.journal(
                last_use["use_id"], "applied")
            assert applied["result"]["lifecycle_state"] == expected
            revision = applied["result"]["committed_revision"]
        fingerprints = await ctx.memory.procedure_runtime.applied_use_fingerprints()
        assert last_use["applicability_fingerprint"] in fingerprints
        await ctx.memory.close()

        adapter = ScriptedAdapter()
        ctx.memory = await _analysis_runtime(ctx, tmp_path, adapter)
        runner = await ctx.memory.job_runner(ctx.memory.analysis_authority,
            _analysis_config(ctx.state), worker_id="relation-analysis",
            now=lambda: time.time() + ctx.offset[0])
        # 第一批是 `create_draft` 直接落的 fixture 证据，没有 outbox 血缘；
        # `max_attempts=1` 让它一次进 dead_letter，下一批才是真实前台轮次。
        assert str(await runner.run_once()) == "dead_letter"
        # 关键：端点被下发、被引用、被 SDK 重解析并落库——整批照常成功。
        assert str(await runner.run_once()) == "applied"

        snapshot = _snapshots(ctx.state)[-1]
        assert snapshot["relation_candidates_unavailable"] is False
        assert snapshot["relation_candidate_reasons"] == []
        assert [row["memory_id"] for row in snapshot["relation_candidates"]] == [ctx.memory_id]
        # `check` 复核用的就是召回时那一份指纹，快照是它唯一的持久来源。
        assert sorted(snapshot["relation_applicability_fingerprints"]) == sorted(fingerprints)
        assert adapter.linked == [snapshot["relation_candidates"][0]["candidate_key"]]

        relations = await mh.memory_rows(mh.MemoryEnv(runtime=ctx.memory),
            "SELECT relation_kind,source_memory_id,target_memory_id,target_revision "
            "FROM cognitive_relations")
        assert len(relations) == 1
        kind, source_id, target_id, target_revision = relations[0]
        assert (kind, target_id) == ("applies_to", ctx.memory_id)
        # 端点指向的是观测提交出来的那一版 head，而不是 revision 1——0.6.35 之前
        # 正是这一版被判「分类缺失」。
        assert target_revision == revision
        assert revision > 1
        assert source_id != ctx.memory_id

        manager = await ctx.memory.manager()
        graph = await manager.get_twin_graph_view(principal=ctx.memory.principal())
        edges = [e for e in graph.edges if e.relation_kind == "applies_to"]
        assert len(edges) == 1
        node_ids = {node.node_id for node in graph.nodes}
        assert edges[0].target_node_id == f"{ctx.memory_id}@{revision}"
        assert {edges[0].source_node_id, edges[0].target_node_id} <= node_ids


# --------------------------------------------------------------- 门本身（不依赖装的是哪版 SDK）

_PROCEDURE_ROW = {"memory_type": "procedure", "memory_id": "procedure-1", "revision": 4,
                  "result_id": "result-1", "result_hash": "a" * 64,
                  "item_id": "item-1", "item_hash": "b" * 64}
_PROSPECTIVE_ROW = {**_PROCEDURE_ROW, "memory_type": "prospective"}


@pytest.mark.parametrize("capable,fingerprints,expected", [
    (False, ("fp-1",), sc.SDK_PROCEDURE_APPLICABILITY_GATE_UNAVAILABLE),
    (False, (), sc.SDK_PROCEDURE_APPLICABILITY_GATE_UNAVAILABLE),
    (True, (), sc.SDK_PROCEDURE_APPLICABILITY_ABSENT),
    (True, ("fp-1",), None),
])
def test_procedure_endpoint_withholding_follows_the_installed_sdk(
        monkeypatch, capable, fingerprints, expected):
    """扣留只在装着的 SDK 通告了 0.6.36 入口、且这一批真有指纹可提交时解除。"""
    monkeypatch.setattr(sc, "sdk_offline_applicability_capable", lambda: capable)
    authority = sc.SemanticCorrectionAuthority
    assert authority._endpoint_unverifiable(
        _PROCEDURE_ROW, fingerprints=fingerprints) == expected
    # Prospective 端点从来不受这两条事实影响。
    assert authority._endpoint_unverifiable(
        _PROSPECTIVE_ROW, fingerprints=fingerprints) is None


class _Request:
    subject = "actor-1"
    disclosure_context = object()
    ordered_evidence_refs = ()


class _Principal:
    actor_id = "actor-1"


class _Observed:
    subject = "actor-1"
    procedure_applicability = None

    def __init__(self, items):
        self.items = items


class _Item:
    def __init__(self, binding_hash):
        self.binding_hash = binding_hash
        self.visible = True


class _Receipt:
    def __init__(self, attestation, admitted):
        self.provenance = attestation.provenance.value
        self.attestation_hash = attestation.attestation_hash
        self.fingerprint_count = len(attestation.fingerprints)
        self.admitted_binding_hashes = tuple(admitted)


class _Manager:
    """Stands in for the SDK facade; `receipt` chooses what the widening reports back."""

    def __init__(self, receipt="honest"):
        self.calls = []
        self._receipt = receipt

    async def check_history_visibility(self, *, principal, disclosure_context, bindings, **kwargs):
        from deskpet.memory.primary_visibility import _binding_hash
        self.calls.append(kwargs)
        observed = _Observed([_Item(_binding_hash(b)) for b in bindings])
        attestation = kwargs.get("procedure_applicability")
        if attestation is not None and self._receipt == "honest":
            observed.procedure_applicability = _Receipt(
                attestation, [_binding_hash(b) for b in bindings])
        elif attestation is not None and self._receipt == "empty":
            observed.procedure_applicability = _Receipt(attestation, [])
        return observed


def _authority(tmp_path, manager):
    async def manager_getter():
        return manager

    return sc.SemanticCorrectionAuthority(tmp_path / "state.db",
        manager_getter=manager_getter, principal_getter=_Principal)


def test_capability_probe_agrees_with_the_installed_facade():
    """探测的是**真实**的 facade 签名与根导出，不是一个替身。"""
    import inspect

    import simple_harness_memory as sdk

    signature = inspect.signature(sdk.MemoryManager.check_history_visibility)
    exported = all(hasattr(sdk, name) for name in (
        "ProcedureApplicabilityAttestation", "ProcedureApplicabilityProvenance"))
    assert sc.sdk_offline_applicability_capable() == (
        exported and "procedure_applicability" in signature.parameters)


@pytest.mark.asyncio
async def test_check_fails_closed_when_the_snapshot_outlives_the_capability(
        monkeypatch, tmp_path):
    """新 SDK 准备的快照在老 SDK 上重放：整批失败，绝不把 Procedure 混进一次无适用性的复核。

    这一项**不**受 0.6.36 保护——它断言的正是装着老 SDK 时该发生什么。
    """
    snapshot = {"subject": "actor-1", "candidates": [],
                "relation_candidates": [dict(_PROCEDURE_ROW)],
                "relation_applicability_fingerprints": ["fp-1"]}
    authority = _authority(tmp_path, _Manager())
    monkeypatch.setattr(sc, "sdk_offline_applicability_capable", lambda: False)
    with pytest.raises(ValueError, match="analysis_candidate_no_longer_visible"):
        await authority.check(_Request(), snapshot)


@needs_0_6_36
@pytest.mark.asyncio
async def test_check_presents_the_recalled_fingerprints_or_fails_closed(monkeypatch, tmp_path):
    """`check` 用的是快照里那一份指纹；没有能力或没有指纹就整批失败，绝不跳过这道门。"""
    from simple_harness_memory import ProcedureApplicabilityProvenance

    snapshot = {"subject": "actor-1", "candidates": [],
                "relation_candidates": [dict(_PROCEDURE_ROW)],
                "relation_applicability_fingerprints": ["fp-2", "fp-1"]}
    manager = _Manager()
    authority = _authority(tmp_path, manager)

    monkeypatch.setattr(sc, "sdk_offline_applicability_capable", lambda: True)
    await authority.check(_Request(), snapshot)
    [kwargs] = manager.calls
    attestation = kwargs["procedure_applicability"]
    # 排序去重后逐字提交，provenance 说明这是「曾经真的用过」而非「此刻仍适用」。
    assert attestation.fingerprints == ("fp-1", "fp-2")
    assert attestation.provenance is ProcedureApplicabilityProvenance.APPLIED_USE_FINGERPRINTS

    # 有能力但这一批没有指纹可提交，fail closed。
    with pytest.raises(ValueError, match="analysis_candidate_no_longer_visible"):
        await authority.check(_Request(), {**snapshot, "relation_applicability_fingerprints": []})
    # 空白串不算指纹（否则会以 SDK 形状的异常从 authorize_plan 逃出去）。
    with pytest.raises(ValueError, match="analysis_candidate_no_longer_visible"):
        await authority.check(_Request(), {**snapshot,
                                          "relation_applicability_fingerprints": ["  ", ""]})

    # 没有 Procedure 端点时不提交任何 attestation：普通批次逐字保持 0.6.35 的调用形状。
    manager.calls.clear()
    await authority.check(_Request(), {**snapshot,
                                       "relation_candidates": [dict(_PROSPECTIVE_ROW)]})
    assert manager.calls == [{}]


@needs_0_6_36
@pytest.mark.asyncio
async def test_check_refuses_a_backend_that_takes_the_kwarg_and_ignores_it(monkeypatch, tmp_path):
    """能力探测只证明「有这么一个参数名」，收据才证明这次扩面真的被兑付了。

    一个接受 kwarg 却什么都不做的后端（未来的重构、部分回移、fork）会让 Procedure 端点
    零适用性复核地进 `authorize_plan`。这里把它挡住：没有收据、或收据指向另一份
    attestation、或这条 Procedure 绑定不在逐条命中清单里，一律整批失败。
    """
    snapshot = {"subject": "actor-1", "candidates": [],
                "relation_candidates": [dict(_PROCEDURE_ROW)],
                "relation_applicability_fingerprints": ["fp-1"]}
    monkeypatch.setattr(sc, "sdk_offline_applicability_capable", lambda: True)
    for mode in ("missing", "empty"):
        authority = _authority(tmp_path, _Manager(receipt=mode))
        with pytest.raises(ValueError, match="analysis_candidate_no_longer_visible"):
            await authority.check(_Request(), snapshot)
