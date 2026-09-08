# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P2: prospective fragments carry a Host-rendered local trigger time.

Oracle source: run-01g review §4 P2 — the SDK payload only ships an epoch
float plus an IANA name, and the model converted it wrong in 4/20 C04 cases.
The Host renders one deterministic string beside (never inside) the public
payload, so the SDK payload bytes and their hash stay untouched.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from deskpet.memory.human_memory_v7 import project_recall_fragments


def _lanes(payload, *, privacy: str = "personal", memory_type: str = "prospective"):
    item = SimpleNamespace(
        selected_item=SimpleNamespace(
            item_id="recall-item:p1:1",
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


def _prospective(trigger_at: float, tz):
    trigger = {"trigger_kind": "time", "trigger_at": trigger_at}
    if tz is not None:
        trigger["timezone"] = tz
    return {"memory_type": "prospective", "action": "提交周报", "trigger": trigger}


def test_prospective_renders_scenario_local_time_and_chinese_weekday():
    at = datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc).timestamp()  # 09:00 +08:00, Monday
    fragment, = project_recall_fragments(_lanes(_prospective(at, "Asia/Shanghai")))
    assert fragment["trigger_local"] == "2026-09-07T09:00+08:00 周一"
    # The rendered string rides beside the SDK payload; payload and hash are untouched.
    assert fragment["payload"] == _prospective(at, "Asia/Shanghai")
    assert "trigger_local" not in fragment["payload"]
    assert fragment["payload_hash"] == "a" * 64


def test_rendering_is_deterministic_and_zone_dependent():
    at = datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc).timestamp()
    shanghai = project_recall_fragments(_lanes(_prospective(at, "Asia/Shanghai")))
    assert shanghai[0]["trigger_local"] == project_recall_fragments(
        _lanes(_prospective(at, "Asia/Shanghai")))[0]["trigger_local"]
    london, = project_recall_fragments(_lanes(_prospective(at, "Europe/London")))
    assert london["trigger_local"] == "2026-09-07T02:00+01:00 周一"


@pytest.mark.parametrize("weekday,day", [("周一", 7), ("周六", 12), ("周日", 13)])
def test_chinese_weekday_covers_the_week_boundary(weekday: str, day: int):
    at = datetime(2026, 9, day, 1, 0, tzinfo=timezone.utc).timestamp()
    fragment, = project_recall_fragments(_lanes(_prospective(at, "Asia/Shanghai")))
    assert fragment["trigger_local"].endswith(f" {weekday}")


@pytest.mark.parametrize("tz", [None, "", "Not/AZone"])
def test_missing_or_unknown_timezone_falls_back_to_utc_not_the_host_zone(tz):
    at = datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc).timestamp()
    fragment, = project_recall_fragments(_lanes(_prospective(at, tz)))
    assert fragment["trigger_local"] == "2026-09-07T01:00+00:00 周一"


def test_seconds_are_kept_only_when_the_trigger_actually_carries_them():
    at = datetime(2026, 9, 7, 1, 0, 30, tzinfo=timezone.utc).timestamp()
    fragment, = project_recall_fragments(_lanes(_prospective(at, "Asia/Shanghai")))
    assert fragment["trigger_local"] == "2026-09-07T09:00:30+08:00 周一"


def test_non_time_and_non_prospective_payloads_get_no_rendered_field():
    event = {"memory_type": "prospective", "action": "提交周报",
             "trigger": {"trigger_kind": "event", "event_authority_ref": "ref",
                         "condition": "when asked", "condition_hash": "d" * 64}}
    assert "trigger_local" not in project_recall_fragments(_lanes(event))[0]
    semantic = {"memory_type": "semantic", "subject_entity": "user",
                "predicate": "prefers", "object_value": "Markdown"}
    assert "trigger_local" not in project_recall_fragments(
        _lanes(semantic, memory_type="semantic"))[0]


def test_malformed_trigger_at_is_skipped_rather_than_guessed():
    for raw in ("2026-09-07T09:00+08:00", None, True):
        payload = {"memory_type": "prospective", "action": "提交周报",
                   "trigger": {"trigger_kind": "time", "trigger_at": raw,
                               "timezone": "Asia/Shanghai"}}
        assert "trigger_local" not in project_recall_fragments(_lanes(payload))[0]
