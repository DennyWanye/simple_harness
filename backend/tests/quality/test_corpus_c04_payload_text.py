# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""F-C04-1: no scoring metadata in any model-visible C04 payload.

The rerun review found ``temporal_payload`` concatenating
``【原时间=…；精度=…；具体日时仅synthetic fixture锚点，非原文事实或评分答案】``
into the memory text itself: 11 of 20 answers repeated it to the user (C04-04
wrote the word 评分 out in full), and it handed the model the date precision
this class exists to measure - so that run's "date precision correct" could not
be read as the model's own judgement.

These are pure text assertions over all 20 cases: no database, no clock, no
model. The fixture behaviour they pin is that the precision label lives only in
:func:`precision_oracle`, while the source document's own time wording stays in
the memory, since dropping that too would leave a synthetic noon anchor
asserting a precision the source never stated.
"""
import pytest

from deskpet.quality.corpus_c04 import (
    SCENARIO_CLOCKS,
    SCORING_METADATA_MARKERS,
    SETUPS,
    SPECS,
    compile_c04_setup,
    precision_oracle,
    public_memory_text,
    temporal_payload,
)

CASE_IDS = list(SETUPS)


def _batch(case_id):
    return compile_c04_setup(case_id, SETUPS[case_id][0], scenario_clock=SCENARIO_CLOCKS[case_id])


def _visible_strings(batch):
    """Everything the model can read out of one case's payloads."""

    strings = []
    for spec in batch.specs:
        payload = temporal_payload(batch, spec).to_json()
        stack = [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, (list, tuple)):
                stack.extend(item)
            elif isinstance(item, str):
                strings.append(item)
    return strings


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_no_scoring_metadata_reaches_a_model_visible_payload(case_id):
    batch = _batch(case_id)
    for value in _visible_strings(batch):
        for marker in SCORING_METADATA_MARKERS:
            assert marker.lower() not in value.lower(), (case_id, marker, value)


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_no_memory_text_names_the_precision_being_scored(case_id):
    """The precision vocabulary itself is the answer key for this class.

    Checked on the prose the model reads. The unresolved event-trigger
    namespace (``corpus:unobserved-event:…``) is a structured authority ref
    that predates this defect and carries no precision label.
    """

    texts = " ".join(public_memory_text(spec) for spec in SPECS[case_id]).lower()
    for precision in {spec[5] for specs in SPECS.values() for spec in specs}:
        assert precision.lower() not in texts, (case_id, precision)


def test_the_old_annotation_is_gone_everywhere():
    for case_id in CASE_IDS:
        joined = " ".join(_visible_strings(_batch(case_id)))
        assert "原时间=" not in joined
        assert "非原文事实或评分答案" not in joined


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_precision_expectation_is_still_available_to_the_scorer(case_id):
    batch = _batch(case_id)
    oracle = precision_oracle(batch)
    assert set(oracle) == {spec[0] for spec in batch.specs}
    for spec in batch.specs:
        entry = oracle[spec[0]]
        assert entry["precision"] == spec[5]
        assert entry["authored_time_text"] == spec[6]
        assert entry["anchor_local"] == spec[3] and entry["anchor_zone"] == spec[4]


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_every_dated_episode_keeps_the_source_time_wording(case_id):
    """Without it the synthetic noon anchor would invent a precision.

    A `day`/`week`/`month`/`night` episode carries its time only in
    ``occurred_start``; the source's own words are a fact of the memory, not an
    answer key, so they stay visible.
    """

    for spec in _batch(case_id).specs:
        if spec[1] == "episode" and spec[5] in {"day", "week", "month", "night"}:
            assert public_memory_text(spec).startswith(spec[6]), spec


def test_an_undated_source_gets_no_invented_date_wording():
    # C04-14's source states no date at all; C04-10 says only 上次.
    assert public_memory_text(SPECS["C04-14"][0]) == SPECS["C04-14"][0][2]
    assert public_memory_text(SPECS["C04-10"][0]).startswith("上次")


def test_the_anchor_join_never_doubles_the_wording():
    # 9月4日夜间 + 夜间传稿漏附件, 7月首次 + 首次试课麦克风失效.
    assert public_memory_text(SPECS["C04-20"][0]) == "9月4日夜间传稿漏附件"
    assert public_memory_text(SPECS["C04-16"][0]) == "7月首次试课麦克风失效"
    assert public_memory_text(SPECS["C04-16"][1]) == "9月4日最近试课计时超长"


def test_prospective_text_leaves_the_time_to_its_trigger():
    """A trigger is a structured field; repeating it in prose adds nothing."""

    for case_id in CASE_IDS:
        for spec in SPECS[case_id]:
            if spec[1] == "prospective":
                assert public_memory_text(spec) == spec[2], (case_id, spec)


def test_payloads_stay_deterministic_across_compilations():
    for case_id in CASE_IDS:
        first, second = _batch(case_id), _batch(case_id)
        for spec in SPECS[case_id]:
            assert temporal_payload(first, spec).to_json() == temporal_payload(second, spec).to_json()
