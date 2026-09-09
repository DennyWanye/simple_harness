# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 AJ：同一个 predicate 下的两条无关记忆，不是「歧义的更正」。

HM-TO-A6 第 12 次 T20/T21 的真实形状（证据 `.local-test-evidence/2026-09-09/native-a6-run12/`
`primary-ui-z9j48osx`，快照 `analysis-candidates-f18430f5…` / `analysis-candidates-d7d66f64…`）：

* T1/T2 两条完全不相干的事实都被写成了 ``user:self · workflow_environment_preference``
  （一条是 Python 版本，一条是归档目录），qualifiers 都是 ``["doing 资料校对"]``；
* T20「更正一下：校对脚本我现在统一用 Python 3.13，不是 3.12。」——Host 自己的 cue-anchor
  语法只给 **第一条** 发了 ``correction_intent``（anchor="Python"，另一条 ``null``），
  也就是说目标是唯一的；旧的按 predicate 计数的同槽检查仍然判它
  ``analysis_correction_candidate_ambiguous``，记忆停在 revision 1；
* T21「不过我印象里上周好像还是按 3.12 在跑的，你说呢？」——contest 因为模型没有把
  ``qualifiers`` 逐字重打一遍而整条被拒（``analysis_contest_slot_mismatch/qualifiers``），
  可 CONTEST 的 payload 本来就是拿在位那条的 qualifiers 组装的。

两个都是 Host 准入侧的回归，与 AE 的 prospective 接地无关。
"""
import json
import sqlite3

import pytest

from deskpet.memory import analysis_proposal as legacy
from deskpet.memory import analysis_proposal_v10 as v10
from deskpet.memory import semantic_correction as sc
from tests.memory.test_semantic_correction import memory_env, recalled
from tests.sdk_adapters import s5b_closure_harness as ch
from tests.sdk_adapters import s5b_memory_harness as mh

PREDICATE = "workflow_environment_preference"
QUALIFIERS = ["doing 资料校对"]
PY_TEXT = "记住：我做资料校对时，统一用 Python 3.12 跑脚本。"
PY_VALUE = "统一用 Python 3.12 跑脚本"
DIR_TEXT = "另外记住：我的校对结果一律存到「外接硬盘 / 校对归档」这个目录。"
DIR_VALUE = "校对结果一律存到「外接硬盘 / 校对归档」这个目录"
CORRECTION_TEXT = "更正一下：校对脚本我现在统一用 Python 3.13，不是 3.12。"
NEW_VALUE = "Python 3.13"
HEDGED_TEXT = "不过我印象里上周好像还是按 3.12 在跑的，你说呢？"


def _rows(db_path, sql):
    with sqlite3.connect(db_path) as db:
        return db.execute(sql).fetchall()


def _claim(item_id, quote, value, *, operation_id="op-1", qualifiers=QUALIFIERS):
    op = mh.semantic_op(item_id, quote, operation_id=operation_id,
                        predicate=PREDICATE, object_value=value)
    op["semantic"]["qualifiers"] = list(qualifiers)
    return op


# ------------------------------------------------------------------ 编译器层（确定性复现）


_COUNTER = iter(range(1, 10_000))


def _item(text):
    """真实的已入库 USER 证据项（离线构造），``derive_span`` 与语法都吃这一个对象。"""
    from deskpet.memory.human_memory_service import build_foreground_turn_evidence

    envelope, receipt = build_foreground_turn_evidence(
        subject="actor-1", authority_ref=mh.AUTHORITY_REF,
        delivery_key=f"aj-{next(_COUNTER)}", text=text)
    return legacy.admitted_item(envelope, receipt, occurred_at=1788931816.0)


def _candidates(items, *, qualifiers=QUALIFIERS):
    """run12 那两条同槽候选，``correction_intent`` 用 Host 自己的语法现算。"""
    issued = [
        {"candidate_key": "candidate-python", "memory_id": "cognitive-memory-python",
         "revision": 1, "memory_type": "semantic", "privacy_class": "personal",
         "information_attributes": ["preference"],
         "payload": {"subject_entity": "user:self", "predicate": PREDICATE,
                     "object_value": PY_VALUE, "qualifiers": list(qualifiers)}},
        {"candidate_key": "candidate-archive", "memory_id": "cognitive-memory-archive",
         "revision": 1, "memory_type": "semantic", "privacy_class": "personal",
         "information_attributes": ["preference"],
         "payload": {"subject_entity": "user:self", "predicate": PREDICATE,
                     "object_value": DIR_VALUE, "qualifiers": list(qualifiers)}},
    ]
    for row in issued:
        row["correction_intent"] = sc.explicit_correction_intent(row, items, issued)
    return issued


def _span(item, quote):
    return legacy.derive_span(item, quote, span_id="span-1")


def test_only_the_anchored_candidate_of_a_shared_predicate_carries_intent():
    """前提事实：目标本来就是唯一的——歧义只存在于旧的 predicate 计数里。"""
    item = _item(CORRECTION_TEXT)
    rows = _candidates((item,))
    assert rows[0]["correction_intent"] is not None
    assert rows[0]["correction_intent"]["grammar"] == "cue-anchor/v1"
    assert rows[0]["correction_intent"]["anchor"] == "Python"
    assert rows[1]["correction_intent"] is None


def test_revise_compiles_when_an_unrelated_memory_shares_the_predicate():
    item = _item(CORRECTION_TEXT)
    rows = _candidates((item,))
    raw = _claim(item.item_id, CORRECTION_TEXT, NEW_VALUE)
    raw.update(action="revise_semantic", candidate_key="candidate-python")
    operation = legacy.compile_operation(raw, _span(item, NEW_VALUE), item=item,
                                         now=1788931819.0, candidates=rows)
    assert operation.kind.value == "revise"
    assert operation.target.memory_id == "cognitive-memory-python"
    assert operation.target.revision == 1
    assert operation.payload.object_value == NEW_VALUE
    assert tuple(operation.payload.qualifiers) == tuple(QUALIFIERS)
    assert operation.evidence_spans[0].support_kind.value == "explicit_user_correction"


def test_revise_inherits_the_incumbent_qualifiers_when_the_model_omits_them():
    item = _item(CORRECTION_TEXT)
    rows = _candidates((item,))
    raw = _claim(item.item_id, CORRECTION_TEXT, NEW_VALUE, qualifiers=[])
    raw.update(action="revise_semantic", candidate_key="candidate-python")
    operation = legacy.compile_operation(raw, _span(item, NEW_VALUE), item=item,
                                         now=1788931819.0, candidates=rows)
    assert tuple(operation.payload.qualifiers) == tuple(QUALIFIERS)


def test_revise_still_refuses_a_stated_but_different_qualifier_set():
    item = _item(CORRECTION_TEXT)
    rows = _candidates((item,))
    raw = _claim(item.item_id, CORRECTION_TEXT, NEW_VALUE, qualifiers=["model-added"])
    raw.update(action="revise_semantic", candidate_key="candidate-python")
    with pytest.raises(legacy.AnalysisProposalRejected) as excinfo:
        legacy.compile_operation(raw, _span(item, NEW_VALUE), item=item,
                                 now=1788931819.0, candidates=rows)
    assert excinfo.value.code == "analysis_correction_qualifiers_mismatch"


def test_two_candidates_the_anchor_cannot_tell_apart_are_still_ambiguous():
    """放宽的只是 predicate 计数；真正分不清的目标仍然整条拒收。"""
    item = _item("更正一下：校对脚本我现在统一用 Python 3.13。")  # 没有引 3.12，anchor 不唯一
    rows = _candidates((item,))
    rows[1]["payload"]["object_value"] = "统一用 Python 跑别的脚本"
    for row in rows:
        row["correction_intent"] = sc.explicit_correction_intent(row, (item,), rows)
    assert all(row["correction_intent"] is None for row in rows)
    raw = _claim(item.item_id, item.text, "Python 3.13")
    raw.update(action="revise_semantic", candidate_key="candidate-python")
    with pytest.raises(legacy.AnalysisProposalRejected) as excinfo:
        legacy.compile_operation(raw, _span(item, "Python 3.13"), item=item,
                                 now=1788931819.0, candidates=rows)
    assert excinfo.value.code == "analysis_explicit_correction_intent_missing"


def test_contest_compiles_when_the_model_omits_the_qualifiers():
    from deskpet.memory import analysis_proposal_v7 as v7

    item = _item(HEDGED_TEXT)
    rows = _candidates((item,))[:1]
    raw = _claim(item.item_id, HEDGED_TEXT, "3.12", qualifiers=[])
    raw.update(action="contest_semantic", candidate_key="candidate-python")
    operation = v7._compile_contest(raw, _span(item, HEDGED_TEXT), item=item, candidates=rows)
    assert operation.kind.value == "contest"
    assert operation.conflict_status.value == "contested"
    # payload 的 qualifiers 一直取在位那条，模型给不给都一样。
    assert tuple(operation.payload.qualifiers) == tuple(QUALIFIERS)


def test_contest_still_refuses_a_stated_but_different_qualifier_set():
    from deskpet.memory import analysis_proposal_v7 as v7

    item = _item(HEDGED_TEXT)
    rows = _candidates((item,))[:1]
    raw = _claim(item.item_id, HEDGED_TEXT, "3.12", qualifiers=["model-added"])
    raw.update(action="contest_semantic", candidate_key="candidate-python")
    with pytest.raises(legacy.AnalysisProposalRejected) as excinfo:
        v7._compile_contest(raw, _span(item, HEDGED_TEXT), item=item, candidates=rows)
    assert excinfo.value.code == v7.CONTEST_SLOT_MISMATCH
    assert excinfo.value.detail["reason"] == "qualifiers"


def test_v10_grounding_never_runs_for_a_revise_or_contest_operation():
    """AE 的接地只对 prospective 的 create 生效——它不是这次回归的原因，也不能变成原因。"""
    from simple_harness.runtime import AnalysisBudget, EvidenceRef, MemoryAnalysisRequest

    from deskpet.memory.human_memory_service import build_foreground_turn_evidence

    envelope, receipt = build_foreground_turn_evidence(
        subject="actor-1", authority_ref=mh.AUTHORITY_REF,
        delivery_key="aj-grounding", text=CORRECTION_TEXT)
    item = legacy.admitted_item(envelope, receipt, occurred_at=1788931816.0)
    request = MemoryAnalysisRequest(
        job_id="aj", run_id=envelope.run_id, subject=envelope.subject,
        ordered_evidence_refs=(EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),),
        prompt_version=v10.PROMPT_VERSION, result_schema_version=v10.RESULT_SCHEMA_VERSION,
        policy_version=v10.POLICY_VERSION, provider_id="p", model_id="m",
        model_config_hash="a" * 64, attempt=1,
        budget=AnalysisBudget(2048, 1024, 5000, 1000),
        disclosure_context=envelope.disclosure_context, idempotency_key="aj")
    rows = _candidates((item,))
    raw = _claim(item.item_id, CORRECTION_TEXT, NEW_VALUE)
    raw.update(action="revise_semantic", candidate_key="candidate-python")

    calls = []
    original = v10.grounding.check_time_trigger
    v10.grounding.check_time_trigger = lambda **kwargs: calls.append(kwargs)
    try:
        compiled = v10.compile_proposal({"outcome": "mutate", "operations": [raw]},
                                        request=request, items=[item], base_revision=1,
                                        plan_id="host-aj", now=1788931819.0, candidates=rows)
    finally:
        v10.grounding.check_time_trigger = original
    assert not compiled.rejected and compiled.outcome == "mutate"
    assert [op.kind.value for op in compiled.plan.operations] == ["revise"]
    assert calls == []


# ------------------------------------------------------------------ 真实存储层


class _ProposalAdapter:
    def __init__(self, build):
        self._build = build
        self.candidates = []

    async def invoke(self, request, *, cancel):
        body = json.loads(request.messages[-1].content.split("\n", 1)[1])
        self.candidates.append(body["semantic_candidates"])
        operation = self._build(body["semantic_candidates"])
        if operation is None:
            return mh.proposal_call([], outcome="no_mutation", provider_request_id="analysis-none")
        return mh.proposal_call([operation], provider_request_id="analysis-1")


async def _seed(tmp_path, *, both):
    """一条（或两条同槽的）带 qualifiers 的语义记忆，走真实 public 存储。"""
    env = await mh.bound_turn_run(tmp_path, "origin-run", text=PY_TEXT)
    await mh.finish_clean_run(env)
    adapter = ch.FakeAdapter([mh.proposal_call([_claim(mh.item_id(env), PY_TEXT, PY_VALUE)])])
    menv = memory_env(env, adapter)
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
        incumbent = (await recalled(menv, PY_VALUE, 1))[0].selected_item
    finally:
        await mh.close(menv)
    if not both:
        return env, incumbent
    env = await mh.next_turn_run(env, "origin-run-2", text=DIR_TEXT, delivery_key="turn-2",
                                 claim_key="claim-origin-2")
    await mh.finish_clean_run(env)
    adapter = ch.FakeAdapter([mh.proposal_call(
        [_claim(mh.item_id(env), DIR_TEXT, DIR_VALUE, operation_id="op-dir")])])
    menv = memory_env(env, adapter)
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
    finally:
        await mh.close(menv)
    return env, incumbent


@pytest.mark.asyncio
async def test_a6_7_correction_supersedes_despite_a_shared_predicate(tmp_path):
    """A6-7：T1/T2 撞槽时，T20 仍然必须落成 revision 2。"""
    env, incumbent = await _seed(tmp_path, both=True)
    current = await mh.next_turn_run(env, "correction-run", text=CORRECTION_TEXT,
                                     delivery_key="correct-1", claim_key="claim-correct")
    await mh.finish_clean_run(current)

    def build(candidates):
        assert len(candidates) == 2, candidates
        keys = [c["candidate_key"] for c in candidates
                if c["semantic"]["object_value"] == PY_VALUE]
        assert len(keys) == 1
        op = _claim(mh.item_id(current), NEW_VALUE, NEW_VALUE)
        op.update(action="revise_semantic", candidate_key=keys[0])
        return op

    adapter = _ProposalAdapter(build)
    menv = memory_env(current, adapter)
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
        rows = await recalled(menv, NEW_VALUE, 2)
        assert len(rows) == 1
        assert rows[0].selected_item.source_ref == incumbent.source_ref
        assert rows[0].selected_item.source_revision == incumbent.source_revision + 1
        assert rows[0].public_payload["object_value"] == NEW_VALUE
        assert list(rows[0].public_payload["qualifiers"]) == QUALIFIERS
        # 撞槽的那条无关记忆一动没动。
        others = [r for r in await recalled(menv, DIR_VALUE, 3)
                  if r.public_payload.get("object_value") == DIR_VALUE]
        assert len(others) == 1 and others[0].selected_item.source_revision == 1
    finally:
        await mh.close(menv)


@pytest.mark.asyncio
async def test_a6_8_hedged_contradiction_contests_without_retyped_qualifiers(tmp_path):
    """A6-8：模型不重打 qualifiers，contest 也必须成立。"""
    env, incumbent = await _seed(tmp_path, both=False)
    current = await mh.next_turn_run(env, "hedge-run", text=HEDGED_TEXT, delivery_key="hedge-1",
                                     claim_key="claim-hedge")
    await mh.finish_clean_run(current)

    def build(candidates):
        assert len(candidates) == 1
        op = _claim(mh.item_id(current), HEDGED_TEXT, "3.12", qualifiers=[])
        op.update(action="contest_semantic", candidate_key=candidates[0]["candidate_key"])
        return op

    adapter = _ProposalAdapter(build)
    menv = memory_env(current, adapter)
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
        semantic_db = env.db_path.parent / "semantic.db"
        assert _rows(semantic_db, "SELECT count(*) FROM cognitive_conflict_groups")[0][0] == 1
        assert _rows(semantic_db, "SELECT count(*) FROM cognitive_conflict_members")[0][0] == 2
        head = _rows(semantic_db,
                     "SELECT r.revision, r.conflict_status, r.content_json FROM cognitive_memory_revisions r "
                     "JOIN cognitive_memory_heads h ON h.memory_id=r.memory_id AND h.current_revision=r.revision "
                     f"WHERE r.memory_id='{incumbent.source_ref}'")
        assert head[0][0] == incumbent.source_revision + 1 and head[0][1] == "contested"
        content = json.loads(head[0][2])
        assert content["object_value"] == "3.12"
        assert list(content["qualifiers"]) == QUALIFIERS
    finally:
        await mh.close(menv)
