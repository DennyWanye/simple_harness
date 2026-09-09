# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""F-EPI-1: episode fragments carry a Host-rendered occurrence time.

Oracle source: ``plans/2026-09-07-corpus-c01-local/RUN-C04-RERUN-2-REVIEW.md``
§八缺陷 1 and 2. Prospective fragments carried ``trigger_local`` and the model
transcribed it verbatim 18/18 times; episode fragments carried only the raw
epoch ``occurred_start``, and

* C04-10 (``undated``) converted that epoch itself and answered a date two days
  off the anchor,
* C04-14 (``undated``) reported the synthetic anchor's own date as fact,
* C04-17 (``month``) narrowed 「8月」 into 「8月中旬」, which only the anchor's
  12:00 could have produced.

The symmetric field renders at the precision the memory's own SDK valid-time
interval states, and renders nothing at all when that interval bounds no
occurrence. Like ``trigger_local`` it rides beside - never inside - the SDK
public payload, so ``payload_hash`` and the typed-use carrier still compare
identical bytes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from deskpet.memory.human_memory_v7 import (
    EPISODE_LOCAL_ZONE,
    project_recall_fragments,
    render_episode_occurred_local,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
LONDON = ZoneInfo("Europe/London")


def _at(local: str, zone: ZoneInfo = SHANGHAI) -> float:
    return datetime.fromisoformat(local).replace(tzinfo=zone).timestamp()


def _episode(start: float, end: float | None, *, title: str = "验样封面偏暗") -> dict:
    """The real SDK public payload of an episode memory.

    ``EpisodeMemoryPayload.to_json`` - exact keys, no zone field anywhere, and
    no self-describing precision. The interval is the only temporal channel.
    """

    return {
        "memory_type": "episode",
        "title": title,
        "participants": ["user:self"],
        "goals": [],
        "actions": [title],
        "results": [],
        "impacts": [],
        "occurred_start": start,
        "occurred_end": end,
        "thread_ref": None,
    }


def _lanes(payload, *, privacy: str = "personal", memory_type: str = "episode"):
    item = SimpleNamespace(
        selected_item=SimpleNamespace(
            item_id="recall-item:e1:1",
            memory_type=memory_type,
            public_payload_hash="a" * 64,
            source_kind="cognitive_memory",
        ),
        effective_privacy_class=privacy,
        score=1.0,
        public_payload=payload,
        source_task_scope_ids=(),
        result_item_hash="b" * 64,
    )
    return SimpleNamespace(
        execution=SimpleNamespace(
            result=SimpleNamespace(items=(item,), result_id="res-1", result_hash="c" * 64)
        ),
        short_horizon=None,
    )


# One row per precision C04 actually states, written the way the fixture writes
# it: the anchor it has always had, plus the first instant after the occurrence.
PRECISIONS = [
    ("minute", "2026-09-05T09:00", "2026-09-05T09:00", "2026-09-05T09:00+08:00 周六"),
    ("day", "2026-09-04T12:00", "2026-09-05T00:00", "2026年9月4日 周五"),
    ("night", "2026-09-04T21:00", "2026-09-05T00:00", "2026年9月4日 周五"),
    ("week", "2026-08-24T12:00", "2026-08-31T00:00", "2026年8月24–30日那周"),
    ("week-across-months", "2026-08-31T12:00", "2026-09-07T00:00", "2026年8月31日–9月6日那周"),
    ("week-across-years", "2025-12-29T00:00", "2026-01-05T00:00", "2025年12月29日–2026年1月4日那周"),
    ("month", "2026-08-01T00:00", "2026-09-01T00:00", "2026年8月"),
    ("month-across-years", "2025-12-01T00:00", "2026-01-01T00:00", "2025年12月"),
    ("year", "2026-01-01T00:00", "2027-01-01T00:00", "2026年"),
    ("odd-span", "2026-08-24T00:00", "2026-09-03T00:00", "2026年8月24日–9月2日"),
]


@pytest.mark.parametrize("name,start,end,expected", PRECISIONS,
                         ids=[row[0] for row in PRECISIONS])
def test_each_precision_renders_exactly_what_the_memory_states(name, start, end, expected):
    payload = _episode(_at(start), _at(end))
    assert render_episode_occurred_local(payload, "episode") == expected


def test_an_unbounded_occurrence_renders_nothing_at_all():
    """C04-10 / C04-14: ``undated``.

    The memory says only 「上次」. The anchor beside it is minute-precise
    fixture scaffolding, so the honest rendering is no rendering - and the
    fragment then carries no time for the model to transcribe.
    """

    payload = _episode(_at("2026-09-05T10:00"), None)
    assert render_episode_occurred_local(payload, "episode") is None
    fragment, = project_recall_fragments(_lanes(payload))
    assert "occurred_local" not in fragment
    # The raw anchor is still in the SDK payload; the Host may not rewrite it
    # (typed_context_use.build_carrier compares the projected payload byte for
    # byte against item.public_payload). Its absence from the rendered surface
    # is the whole mechanism.
    assert fragment["payload"]["occurred_start"] == _at("2026-09-05T10:00")


def test_a_point_occurrence_renders_like_trigger_local():
    """What ``analysis_proposal.compile_operation`` writes for a real episode.

    It sets ``occurred_end = occurred_start`` from the durable Host observation
    time, so a real (non-corpus) episode is a point occurrence and gets the
    full local time, exactly as a reminder does.
    """

    at = _at("2026-09-05T10:00")
    assert render_episode_occurred_local(_episode(at, at), "episode") == (
        "2026-09-05T10:00+08:00 周六")
    with_seconds = _at("2026-09-05T10:00:30")
    assert render_episode_occurred_local(_episode(with_seconds, with_seconds), "episode") == (
        "2026-09-05T10:00:30+08:00 周六")


def test_the_rendered_field_rides_beside_the_untouched_sdk_payload():
    payload = _episode(_at("2026-09-04T12:00"), _at("2026-09-05T00:00"))
    fragment, = project_recall_fragments(_lanes(payload))
    assert fragment["occurred_local"] == "2026年9月4日 周五"
    assert fragment["payload"] == payload
    assert "occurred_local" not in fragment["payload"]
    assert fragment["payload_hash"] == "a" * 64


def test_the_episode_fragment_shape_is_pinned():
    """A new model-visible key is a deliberate change, not a side effect."""

    payload = _episode(_at("2026-09-04T12:00"), _at("2026-09-05T00:00"))
    fragment, = project_recall_fragments(_lanes(payload))
    assert set(fragment) == {
        "ref", "memory_type", "privacy_class", "conflict_status", "score", "payload",
        "payload_hash", "occurred_local", "source_task_scope_ids", "bytes", "tokens",
        "lane", "history_binding",
    }
    # bytes/tokens still measure the SDK payload alone, exactly as before: the
    # rendered string is not part of what the payload budget accounts for (the
    # same pre-existing property trigger_local has).
    import json

    assert fragment["bytes"] == len(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))


def test_rendering_is_zone_dependent_and_deterministic():
    """A calendar month in London is not a calendar month seen from Shanghai."""

    payload = _episode(_at("2026-08-01T00:00", LONDON), _at("2026-09-01T00:00", LONDON))
    assert render_episode_occurred_local(payload, "episode", zone_name="Europe/London") == "2026年8月"
    assert render_episode_occurred_local(payload, "episode") == "2026年8月1日–9月1日"
    assert render_episode_occurred_local(payload, "episode", zone_name="Europe/London") == (
        render_episode_occurred_local(payload, "episode", zone_name="Europe/London"))


def test_london_summer_time_is_carried_by_the_zone_not_by_arithmetic():
    """The review's own hardest prospective case, on the episode side.

    C04-06 rendered ``2026-09-08T09:00+01:00 周二`` for a London trigger; the
    same instant and zone must render the same offset for an occurrence.
    """

    at = _at("2026-09-08T09:00", LONDON)
    assert render_episode_occurred_local(_episode(at, at), "episode",
                                         zone_name="Europe/London") == "2026-09-08T09:00+01:00 周二"


def test_a_dst_shortened_day_is_still_one_day():
    """2026-03-29 is 23 hours long in London; a calendar day is not 86400s."""

    start = _at("2026-03-29T00:00", LONDON)
    end = _at("2026-03-30T00:00", LONDON)
    assert end - start == 23 * 3600
    assert render_episode_occurred_local(_episode(start, end), "episode",
                                         zone_name="Europe/London") == "2026年3月29日 周日"


def test_a_dst_lengthened_week_is_still_one_week():
    start = _at("2026-10-19T00:00", LONDON)
    end = _at("2026-10-26T00:00", LONDON)
    assert end - start == 7 * 86400 + 3600
    assert render_episode_occurred_local(_episode(start, end), "episode",
                                         zone_name="Europe/London") == "2026年10月19–25日那周"


def test_the_default_zone_is_the_one_the_host_already_tells_the_model():
    import inspect

    from deskpet.execution.primary_context import PrimaryForegroundContextPort

    assert EPISODE_LOCAL_ZONE == "Asia/Shanghai"
    # The fixed clock line the model reads every request says
    # "timezone=Asia/Shanghai"; an occurrence rendered in any other zone would
    # contradict the Host's own statement of today.
    assert (inspect.signature(PrimaryForegroundContextPort.__init__)
            .parameters["clock_timezone"].default == EPISODE_LOCAL_ZONE)


@pytest.mark.parametrize("start,end", [
    ("2026-09-04T12:00", "2026-09-04T00:00"),  # inverted
])
def test_an_inverted_interval_is_skipped_rather_than_guessed(start, end):
    assert render_episode_occurred_local(_episode(_at(start), _at(end)), "episode") is None


@pytest.mark.parametrize("raw", ["2026-09-04T12:00+08:00", True, None, {}])
def test_a_malformed_start_is_skipped_rather_than_guessed(raw):
    payload = _episode(_at("2026-09-04T12:00"), _at("2026-09-05T00:00"))
    payload["occurred_start"] = raw
    assert render_episode_occurred_local(payload, "episode") is None


@pytest.mark.parametrize("raw", ["2026-09-05T00:00+08:00", True, []])
def test_a_malformed_end_is_skipped_rather_than_guessed(raw):
    payload = _episode(_at("2026-09-04T12:00"), _at("2026-09-05T00:00"))
    payload["occurred_end"] = raw
    assert render_episode_occurred_local(payload, "episode") is None


def test_an_unknown_zone_falls_back_to_utc_not_the_host_zone():
    at = _at("2026-09-05T10:00")
    assert render_episode_occurred_local(_episode(at, at), "episode",
                                         zone_name="Not/AZone") == "2026-09-05T02:00+00:00 周六"


def test_the_kind_comes_from_the_fragment_not_from_the_payload():
    """The run-01j regression that once left trigger_local unrendered."""

    payload = _episode(_at("2026-09-04T12:00"), _at("2026-09-05T00:00"))
    assert "occurred_local" in project_recall_fragments(_lanes(payload))[0]
    assert "occurred_local" not in project_recall_fragments(
        _lanes(payload, memory_type="semantic"))[0]
    assert render_episode_occurred_local(payload, "prospective") is None


def test_a_prospective_fragment_is_untouched_by_this_change():
    trigger_at = datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc).timestamp()
    payload = {"action": "提交周报",
               "trigger": {"trigger_kind": "time", "trigger_at": trigger_at,
                           "timezone": "Asia/Shanghai"}}
    fragment, = project_recall_fragments(_lanes(payload, memory_type="prospective"))
    assert fragment["trigger_local"] == "2026-09-07T09:00+08:00 周一"
    assert "occurred_local" not in fragment


# ── The hint that says what an absent occurred_local means ──────────────────


def _hint(fragments):
    from deskpet.sdk_adapters.context_route import ContextRouteToolService

    return ContextRouteToolService._temporal_hint(fragments)


def test_the_hint_fires_only_when_an_episode_states_no_occurrence_time():
    dated, = project_recall_fragments(
        _lanes(_episode(_at("2026-09-04T12:00"), _at("2026-09-05T00:00"))))
    undated, = project_recall_fragments(_lanes(_episode(_at("2026-09-05T10:00"), None)))
    assert _hint([dated]) is None
    assert _hint([]) is None
    hint = _hint([dated, undated])
    assert hint["reason"] == "episode_fragment_states_no_occurrence_time"
    assert "never recompute a date from occurred_start" in hint["message"]
    assert "no date, month or weekday" in hint["message"]


def test_the_hint_names_no_case_and_reads_no_query():
    """Advisory and gold-free, exactly like procedure_hint."""

    undated, = project_recall_fragments(_lanes(_episode(_at("2026-09-05T10:00"), None)))
    hint = _hint([undated])
    assert hint == _hint([undated])
    assert "上次" not in hint["message"] and "C04" not in hint["message"]


# ── The C04 fixture's own precisions, end to end ────────────────────────────


C04_EPISODE_RENDERINGS = {
    ("C04-01", "E"): "2026年9月4日 周五",
    ("C04-02", "E"): "2026年8月31日 周一",
    ("C04-03", "E"): "2025年12月31日 周三",
    ("C04-05", "E1"): "2026-09-05T09:00+08:00 周六",
    ("C04-05", "E2"): "2026-09-05T15:00+08:00 周六",
    ("C04-09", "E1"): "2026年8月24–30日那周",
    ("C04-09", "E2"): "2026年8月31日–9月6日那周",
    ("C04-10", "E"): None,
    ("C04-14", "E"): None,
    ("C04-15", "E"): "2026-09-30T16:00+08:00 周三",
    ("C04-16", "E1"): "2026年7月",
    ("C04-16", "E2"): "2026年9月4日 周五",
    ("C04-17", "E"): "2026年8月",
    ("C04-20", "E"): "2026年9月4日 周五",
}


def _oracle(case_id):
    from deskpet.quality.corpus_c04 import (SCENARIO_CLOCKS, SETUPS, compile_c04_setup,
                                            precision_oracle)

    return precision_oracle(compile_c04_setup(case_id, SETUPS[case_id][0],
                                              scenario_clock=SCENARIO_CLOCKS[case_id]))


@pytest.mark.parametrize("key,expected", sorted(C04_EPISODE_RENDERINGS.items(),
                                                key=lambda row: row[0]),
                         ids=[f"{case}-{label}" for case, label in
                              sorted(C04_EPISODE_RENDERINGS, key=lambda row: row)])
def test_the_three_failing_c04_precisions_and_their_controls(key, expected):
    """The scoring-side expectation is the Host's own rendering.

    C04-10 and C04-14 must render nothing (they answered a date), C04-17 must
    render 「2026年8月」 and never a 旬 (it answered 「8月中旬」), and the
    controls that already passed must not have moved.
    """

    case_id, label = key
    assert _oracle(case_id)[label]["rendered_occurred_local"] == expected


def test_every_c04_episode_renders_within_its_authored_precision():
    from deskpet.quality.corpus_c04 import SETUPS

    for case_id in SETUPS:
        for label, record in _oracle(case_id).items():
            if record["memory_type"] != "episode":
                continue
            rendered = record["rendered_occurred_local"]
            precision = record["precision"]
            if precision == "undated":
                assert rendered is None, (case_id, label)
                assert record["occurred_end"] is None, (case_id, label)
                continue
            assert rendered is not None, (case_id, label)
            assert record["occurred_end"] is not None, (case_id, label)
            if precision == "minute":
                assert "T" in rendered, (case_id, label)
            else:
                # No coarse memory may hand the model a clock time.
                assert "T" not in rendered and ":" not in rendered, (case_id, label)
            if precision == "month":
                assert rendered.endswith("月"), (case_id, label)
            if precision == "week":
                assert rendered.endswith("那周"), (case_id, label)


def test_the_oracle_is_scoring_side_and_never_a_model_visible_payload():
    from deskpet.quality.corpus_c04 import SCORING_METADATA_MARKERS, SETUPS, temporal_payload
    from deskpet.quality.corpus_c04 import SCENARIO_CLOCKS, compile_c04_setup

    for case_id in SETUPS:
        batch = compile_c04_setup(case_id, SETUPS[case_id][0],
                                  scenario_clock=SCENARIO_CLOCKS[case_id])
        for spec in batch.specs:
            payload = temporal_payload(batch, spec).to_json()
            assert "rendered_occurred_local" not in payload
            for marker in SCORING_METADATA_MARKERS:
                assert marker not in str(payload.get("title", "")), (case_id, marker)


def test_the_interval_never_moves_an_anchor_it_does_not_have_to():
    """Only `month` re-anchors; every other precision keeps its historical anchor."""

    from deskpet.quality.corpus_c04 import SPECS, episode_interval, timestamp

    for case_id, specs in SPECS.items():
        for spec in specs:
            label, kind, _text, local, zone, precision, _authored = spec
            if kind != "episode":
                continue
            start, end = episode_interval(local, zone, precision)
            if precision == "month":
                assert start < timestamp(local, zone), (case_id, label)
                assert datetime.fromtimestamp(start, ZoneInfo(zone)).day == 1
            else:
                assert start == timestamp(local, zone), (case_id, label)
            if precision == "undated":
                assert end is None, (case_id, label)
            elif precision == "minute":
                assert end == start, (case_id, label)
            else:
                assert end > start, (case_id, label)


def test_an_unknown_episode_precision_is_a_fixture_error_not_a_guess():
    from deskpet.quality.corpus_c04 import episode_interval

    with pytest.raises(ValueError, match="c04_unknown_episode_precision"):
        episode_interval("2026-09-04T12:00", "Asia/Shanghai", "decade")


def test_the_interval_is_deterministic_across_compilations():
    from deskpet.quality.corpus_c04 import (SCENARIO_CLOCKS, SETUPS, compile_c04_setup,
                                            temporal_payload)

    for case_id in SETUPS:
        first = compile_c04_setup(case_id, SETUPS[case_id][0],
                                  scenario_clock=SCENARIO_CLOCKS[case_id])
        second = compile_c04_setup(case_id, SETUPS[case_id][0],
                                   scenario_clock=SCENARIO_CLOCKS[case_id])
        for spec in first.specs:
            assert temporal_payload(first, spec).to_json() == temporal_payload(second, spec).to_json()


def test_a_week_boundary_never_slides_by_a_second():
    """``occurred_end`` is the first instant *after* the occurrence.

    One second past the boundary touches an eighth calendar day, so the span
    stops being a week and is rendered as the explicit range it now is - never
    silently as the wrong week.
    """

    start = _at("2026-08-24T12:00")
    end = _at("2026-08-31T00:00")
    assert render_episode_occurred_local(_episode(start, end), "episode") == "2026年8月24–30日那周"
    assert render_episode_occurred_local(_episode(start, end + 1.0), "episode") == "2026年8月24–31日"
