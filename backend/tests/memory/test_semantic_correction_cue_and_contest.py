"""Incident H: a natural explicit correction must supersede; a hedged one must contest.

The real HM-TO-A6 turn-20 sentence is used verbatim.  The model's actual proposal that day
was valid in every respect the Host can check mechanically (issued candidate_key, preserved
subject/predicate/qualifiers, verbatim quote) and was still rejected with
``analysis_explicit_correction_intent_missing``, because the only Host correction grammar
was two full-sentence templates keyed to a drink-preference alias table.
"""
import json
import sqlite3

import pytest

from deskpet.memory import semantic_correction as sc
from tests.memory.test_semantic_correction import memory_env, recalled
from tests.sdk_adapters import s5b_memory_harness as mh
from tests.sdk_adapters import s5b_closure_harness as ch

PREDICATE = "material_proofreading_python_version"
OLD_VALUE = "Python 3.12"
NEW_VALUE = "Python 3.13"
ORIGIN_TEXT = "记住：我做资料校对时，统一用 Python 3.12 跑脚本。"
CORRECTION_TEXT = "更正一下：校对脚本我现在统一用 Python 3.13，不是 3.12。"
HEDGED_TEXT = "不过我印象里上周好像还是按 3.12 在跑的，你说呢？"


def _rows(db_path, sql):
    with sqlite3.connect(db_path) as db:
        return db.execute(sql).fetchall()


def _authority_rows(db_path):
    return _rows(db_path, "SELECT count(*) FROM human_memory_evidence "
                          "WHERE evidence_id LIKE 'semantic-action%'")[0][0]


def _audit_codes(db_path):
    return [row[0] for row in _rows(db_path, "SELECT reason_code FROM host_pre_admission_audit")]


async def _seed_claim(tmp_path):
    """One applied semantic CREATE: ``material_proofreading_python_version = Python 3.12``."""
    env = await mh.bound_turn_run(tmp_path, "origin-run", text=ORIGIN_TEXT)
    await mh.finish_clean_run(env)
    adapter = ch.FakeAdapter([mh.proposal_call(
        [mh.semantic_op(mh.item_id(env), ORIGIN_TEXT, predicate=PREDICATE, object_value=OLD_VALUE)])])
    menv = memory_env(env, adapter)
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
        incumbent = (await recalled(menv, OLD_VALUE, 1))[0].selected_item
        assert incumbent.source_revision == 1
    finally:
        await mh.close(menv)
    return env, incumbent


class _ProposalAdapter:
    """Replays one operation built from the candidate list the Host actually issued."""

    def __init__(self, build):
        self._build = build
        self.calls = []
        self.candidates = []

    async def invoke(self, request, *, cancel):
        self.calls.append(request)
        body = json.loads(request.messages[-1].content.split("\n", 1)[1])
        self.candidates.append(body["semantic_candidates"])
        operation = self._build(body["semantic_candidates"])
        if operation is None:
            return mh.proposal_call([], outcome="no_mutation", provider_request_id="analysis-none")
        return mh.proposal_call([operation], provider_request_id="analysis-1")


# --------------------------------------------------------------------- unit grammar


class _FakeEnvelope:
    def __init__(self, kind="user_message"):
        self.source_kind = type("K", (), {"value": kind})()
        self.envelope_hash = "envelope-hash"


class _FakeItem:
    def __init__(self, text, kind="user_message"):
        self.text = text
        self.evidence_id = "evidence-1"
        self.item_id = "item-1"
        self.envelope = _FakeEnvelope(kind)


def _candidate(object_value=OLD_VALUE, predicate=PREDICATE, subject="user:self", memory_id="m-1"):
    return {"memory_id": memory_id,
            "payload": {"subject_entity": subject, "predicate": predicate,
                        "object_value": object_value, "qualifiers": []}}


def test_cue_anchor_grammar_grants_intent_for_the_real_incident_sentence():
    candidate = _candidate()
    intent = sc.explicit_correction_intent(candidate, (_FakeItem(CORRECTION_TEXT),), (candidate,))
    assert intent is not None
    assert intent["grammar"] == "cue-anchor/v1" and intent["cue"] == "更正一下"
    assert intent["old_value"] == OLD_VALUE and intent["new_value"] is None
    assert intent["exact_quote"] == CORRECTION_TEXT
    # The Host fixes the slot only; the anchor is the old-value token the user quoted.
    assert intent["anchor"] in ("Python", "3.12")


@pytest.mark.parametrize("text", [
    "“更正一下：校对脚本我现在统一用 Python 3.13，不是 3.12。”",       # quoted
    "有人说更正一下：校对脚本我现在统一用 Python 3.13，不是 3.12。",     # reported, cue not opening
    "如果我说更正一下：校对脚本用 Python 3.13，不是 3.12，你会改吗？",   # hypothetical + question
    "更正一下：校对脚本我好像该用 Python 3.13，不是 3.12。",             # hedged
    "更正一下：校对脚本我现在统一用最新版本。",                          # old value not quoted
    "昨天我说更正一下：校对脚本用 Python 3.13，不是 3.12。",             # historical
])
def test_cue_anchor_grammar_fails_closed(text):
    candidate = _candidate()
    assert sc.explicit_correction_intent(candidate, (_FakeItem(text),), (candidate,)) is None


def test_shared_anchor_between_candidates_is_not_a_slot_anchor():
    a, b = _candidate(memory_id="m-a"), _candidate("Python only", memory_id="m-b")
    text = "更正一下：校对脚本我现在统一用 Python 3.13。"
    assert sc.explicit_correction_intent(a, (_FakeItem(text),), (a, b)) is None
    assert sc.explicit_correction_intent(b, (_FakeItem(text),), (a, b)) is None
    # …while the discriminating token still anchors the right one.
    both = "更正一下：校对脚本我现在统一用 Python 3.13，不是 3.12。"
    assert sc.explicit_correction_intent(a, (_FakeItem(both),), (a, b))["anchor"] == "3.12"
    assert sc.explicit_correction_intent(b, (_FakeItem(both),), (a, b)) is None


def test_non_user_subject_and_non_user_source_never_grant_intent():
    other = _candidate(subject="某个流程")
    assert sc.explicit_correction_intent(other, (_FakeItem(CORRECTION_TEXT),), (other,)) is None
    candidate = _candidate()
    assert sc.explicit_correction_intent(
        candidate, (_FakeItem(CORRECTION_TEXT, kind="runtime_event"),), (candidate,)) is None


def test_hedge_marker_is_recognized_only_for_a_current_user_hesitation():
    assert sc.hedged_contradiction_marker(HEDGED_TEXT) == "印象里"
    assert sc.hedged_contradiction_marker(CORRECTION_TEXT) is None      # explicit → revise
    assert sc.hedged_contradiction_marker("上周就是按 3.12 跑的。") is None  # assertive, no hedge
    assert sc.hedged_contradiction_marker("有人说好像还是 3.12。") is None   # reported
    assert sc.hedged_contradiction_marker("如果好像还是 3.12 呢？") is None  # hypothetical


# ------------------------------------------------------------------ real store flows


@pytest.mark.asyncio
async def test_incident_h_explicit_correction_supersedes_with_partial_quote(tmp_path):
    env, incumbent = await _seed_claim(tmp_path)
    current = await mh.next_turn_run(env, "correction-run", text=CORRECTION_TEXT, delivery_key="correct-1")
    await mh.finish_clean_run(current)

    def build(candidates):
        assert len(candidates) == 1
        operation = mh.semantic_op(mh.item_id(current), NEW_VALUE,  # partial, in-sentence quote
                                   predicate=PREDICATE, object_value=NEW_VALUE)
        operation.update(action="revise_semantic", candidate_key=candidates[0]["candidate_key"])
        return operation

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
        assert all(r.public_payload["object_value"] != OLD_VALUE for r in await recalled(menv, OLD_VALUE, 3))
        assert _authority_rows(env.db_path) == 1
    finally:
        await mh.close(menv)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["hedged_text", "quoted_text", "reported_text",
                                  "new_value_is_substring_of_old", "new_value_outside_sentence",
                                  "quote_from_other_item"])
async def test_correction_rejections_leave_the_head_untouched_and_audited(tmp_path, mode):
    env, incumbent = await _seed_claim(tmp_path)
    text = {
        "hedged_text": "更正一下：校对脚本我好像统一用 Python 3.13，不是 3.12。",
        "quoted_text": "“更正一下：校对脚本我现在统一用 Python 3.13，不是 3.12。”",
        "reported_text": "有人说更正一下：校对脚本我现在统一用 Python 3.13，不是 3.12。",
    }.get(mode, CORRECTION_TEXT)
    current = await mh.next_turn_run(env, "correction-run", text=text, delivery_key="correct-1")
    await mh.finish_clean_run(current)

    def build(candidates):
        if not candidates:
            return None
        value = NEW_VALUE
        quote = text
        if mode == "new_value_is_substring_of_old":
            value, quote = "3.12", "3.12"
        if mode == "new_value_outside_sentence":
            value = "Python 4.0"
        if mode == "quote_from_other_item":
            quote = "跑脚本"  # verbatim in the ORIGIN item, not in the correction sentence
        operation = mh.semantic_op(mh.item_id(current), quote, predicate=PREDICATE, object_value=value)
        operation.update(action="revise_semantic", candidate_key=candidates[0]["candidate_key"])
        return operation

    adapter = _ProposalAdapter(build)
    menv = memory_env(current, adapter)
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
        assert _authority_rows(env.db_path) == 0
        rows = await recalled(menv, OLD_VALUE, 2)
        assert len(rows) == 1
        assert rows[0].selected_item.source_revision == incumbent.source_revision
        assert rows[0].public_payload["object_value"] == OLD_VALUE
        assert all(r.public_payload["object_value"] != NEW_VALUE
                   for r in await recalled(menv, NEW_VALUE, 3))
        # The candidate was issued and the model did propose a revision …
        assert adapter.candidates and adapter.candidates[0]
        # … so the closure must name its own cause durably (incident H part 2).
        assert "analysis_all_operations_rejected" in _audit_codes(env.db_path)
    finally:
        await mh.close(menv)


@pytest.mark.asyncio
async def test_hedged_contradiction_contests_the_head_instead_of_dropping_it(tmp_path):
    """HM-S3 「含糊冲突 contested」 / acceptance A6-8."""
    env, incumbent = await _seed_claim(tmp_path)
    current = await mh.next_turn_run(env, "hedge-run", text=HEDGED_TEXT, delivery_key="hedge-1")
    await mh.finish_clean_run(current)

    def build(candidates):
        assert len(candidates) == 1
        operation = mh.semantic_op(mh.item_id(current), HEDGED_TEXT,
                                   predicate=PREDICATE, object_value="3.12")
        operation.update(action="contest_semantic", candidate_key=candidates[0]["candidate_key"])
        return operation

    adapter = _ProposalAdapter(build)
    menv = memory_env(current, adapter)
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
        # CONTEST is not a protected action: no MemoryActionAuthority is issued.
        assert _authority_rows(env.db_path) == 0
        semantic_db = env.db_path.parent / "semantic.db"
        groups = _rows(semantic_db, "SELECT count(*) FROM cognitive_conflict_groups")[0][0]
        members = _rows(semantic_db, "SELECT count(*) FROM cognitive_conflict_members")[0][0]
        assert (groups, members) == (1, 2)
        head = _rows(semantic_db,
                     "SELECT r.revision, r.conflict_status, r.content_json FROM cognitive_memory_revisions r "
                     "JOIN cognitive_memory_heads h ON h.memory_id=r.memory_id AND h.current_revision=r.revision "
                     f"WHERE r.memory_id='{incumbent.source_ref}'")
        assert head[0][0] == incumbent.source_revision + 1 and head[0][1] == "contested"
        # The challenger carries the hedged reading; the incumbent revision is intact.
        assert json.loads(head[0][2])["object_value"] == "3.12"
        incumbent_json = _rows(semantic_db, "SELECT content_json FROM cognitive_memory_revisions "
                               f"WHERE memory_id='{incumbent.source_ref}' AND revision=1")[0][0]
        assert json.loads(incumbent_json)["object_value"] == OLD_VALUE
        assert _rows(semantic_db, "SELECT role,revision FROM cognitive_conflict_members "
                                  "ORDER BY ordinal") == [("incumbent", 1), ("challenger", 2)]
        # 「不选边」: neither side is served by ordinary recall while contested.
        assert not await recalled(menv, OLD_VALUE, 2)
    finally:
        await mh.close(menv)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["no_hedge", "slot_mutated", "value_not_quoted", "same_value"])
async def test_contest_requires_a_current_user_hedge_and_an_immutable_slot(tmp_path, mode):
    env, incumbent = await _seed_claim(tmp_path)
    text = "上周就是按 3.12 在跑的。" if mode == "no_hedge" else HEDGED_TEXT
    current = await mh.next_turn_run(env, "hedge-run", text=text, delivery_key="hedge-1")
    await mh.finish_clean_run(current)

    def build(candidates):
        if not candidates:
            return None
        value = {"value_not_quoted": "Python 3.11", "same_value": OLD_VALUE}.get(mode, "3.12")
        operation = mh.semantic_op(mh.item_id(current), text, predicate=PREDICATE, object_value=value)
        if mode == "slot_mutated":
            operation["semantic"]["predicate"] = "favorite_python_version"
        operation.update(action="contest_semantic", candidate_key=candidates[0]["candidate_key"])
        return operation

    adapter = _ProposalAdapter(build)
    menv = memory_env(current, adapter)
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
        semantic_db = env.db_path.parent / "semantic.db"
        assert _rows(semantic_db, "SELECT count(*) FROM cognitive_conflict_groups")[0][0] == 0
        rows = await recalled(menv, OLD_VALUE, 2)
        assert len(rows) == 1
        assert rows[0].selected_item.source_revision == incumbent.source_revision
        assert rows[0].public_payload["object_value"] == OLD_VALUE
    finally:
        await mh.close(menv)


@pytest.mark.asyncio
async def test_all_operations_rejected_names_its_cause_in_log_and_audit(tmp_path, caplog):
    """Incident H part 2: the closure used to be indistinguishable from "nothing to remember"."""
    env, _ = await _seed_claim(tmp_path)
    text = "“更正一下：校对脚本我现在统一用 Python 3.13，不是 3.12。”"  # quoted → no Host intent
    current = await mh.next_turn_run(env, "correction-run", text=text, delivery_key="correct-1")
    await mh.finish_clean_run(current)

    def build(candidates):
        operation = mh.semantic_op(mh.item_id(current), text, predicate=PREDICATE, object_value=NEW_VALUE)
        operation.update(action="revise_semantic",
                         candidate_key=candidates[0]["candidate_key"] if candidates else "")
        return operation

    adapter = _ProposalAdapter(build)
    menv = memory_env(current, adapter)
    try:
        assert await menv.worker.run_once() == "delivered"
        with caplog.at_level("WARNING", logger="deskpet.memory.analysis_executor"):
            assert await mh.run_job(menv) == "applied"
    finally:
        await mh.close(menv)
    messages = [record.getMessage() for record in caplog.records]
    collapsed = [m for m in messages if "memory.analysis_all_operations_rejected" in m]
    per_operation = [m for m in messages if "memory.analysis_operations_rejected" in m]
    assert collapsed and per_operation
    assert "analysis_explicit_correction_intent_missing" in collapsed[0]
    # Payload-free: no admitted evidence text reaches the log.
    for message in collapsed + per_operation:
        assert "Python 3.13" not in message and "更正一下" not in message
    assert "analysis_all_operations_rejected" in _audit_codes(env.db_path)
