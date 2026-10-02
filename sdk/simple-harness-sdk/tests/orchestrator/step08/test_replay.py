# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 8 · slice A (plan D8-1' / D8-2' / D8-3'; S8-02, S8-05): Replay rebuilds facts that
already happened — a pure fold of events compared with the library — never executing,
never writing, and reporting what the record cannot decide instead of guessing."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from agent_orchestrator.__main__ import main
from agent_orchestrator.observability.replay import (
    Projection,
    replay_mission,
)


def _demo(tmp_path, scenario):
    evidence = Path(tmp_path) / scenario
    code = main(
        [
            "demo",
            "--scenario",
            scenario,
            "--provider",
            "fixtures",
            "--evidence-dir",
            str(evidence),
            "--idempotency-key",
            f"r-{scenario}",
        ]
    )
    report = json.loads((evidence / "test-report.json").read_text(encoding="utf-8"))
    return code, evidence, report["mission_id"]


# ------------------------------------------------------------------ S8-05


def test_s8_05_old_events_without_a_field_leave_it_uncovered(tmp_path, capsys):
    _code, evidence, mission_id = _demo(tmp_path, "approval-action")
    capsys.readouterr()
    events = [
        json.loads(line)
        for line in (evidence / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    for event in events:
        if event["type"] == "ActionSucceeded":
            event["payload"].pop("receipt_hash", None)  # an older build did not record it
    old = Path(tmp_path) / "old.jsonl"
    old.write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8"
    )
    report = replay_mission(
        mission_id=mission_id, library=evidence / "orchestrator.db", events_file=old
    )
    uncovered = [n for n in report["comparison"]["not_covered"] if n["object"] == "action"]
    assert [n["field"] for n in uncovered] == ["receipt_hash"] and report["comparison"][
        "mismatches"
    ] == []
    shutil.rmtree(Path(tmp_path) / "approval-action" / "workspaces", ignore_errors=True)


# ------------------------------------------------------------------ step-7 states on record
def _approval_demo(evidence, key, *extra):
    return main(
        [
            "demo",
            "--scenario",
            "approval-action",
            "--provider",
            "fixtures",
            "--evidence-dir",
            str(evidence),
            "--idempotency-key",
            key,
            *extra,
        ]
    )


def test_s8_02_a_rejected_approval_and_a_cancelled_open_action_replay_exactly(tmp_path, capsys):
    rejected = Path(tmp_path) / "rejected"
    assert _approval_demo(rejected, "r-reject", "--pause-for-approval") == 4
    capsys.readouterr()
    assert main(["approval", "list", "--evidence-dir", str(rejected), "--as", "alice"]) == 0
    [request] = json.loads(capsys.readouterr().out)
    assert (
        main(
            [
                "approval",
                "reject",
                request["request_id"],
                "--evidence-dir",
                str(rejected),
                "--as",
                "alice",
                "--reason",
                "不在窗口",
            ]
        )
        == 0
    )
    assert _approval_demo(rejected, "r-reject") == 1
    capsys.readouterr()
    mission_id = json.loads((rejected / "test-report.json").read_text(encoding="utf-8"))[
        "mission_id"
    ]
    report = replay_mission(mission_id=mission_id, library=rejected / "orchestrator.db")
    assert report["comparison"]["mismatches"] == [] and report["comparison"]["coverage"] == 1.0
    assert report["formal_state"]["approval"][request["request_id"]]["state"] == "REJECTED"

    cancelled = Path(tmp_path) / "cancelled"
    assert _approval_demo(cancelled, "r-cancel", "--pause-for-approval") == 4
    capsys.readouterr()
    mission_id = json.loads((cancelled / "test-report.json").read_text(encoding="utf-8"))[
        "mission_id"
    ]
    assert main(["mission", "cancel", "--evidence-dir", str(cancelled), mission_id]) == 0
    capsys.readouterr()
    report = replay_mission(mission_id=mission_id, library=cancelled / "orchestrator.db")
    assert report["comparison"]["mismatches"] == [] and report["comparison"]["coverage"] == 1.0
    [action] = report["formal_state"]["action"].values()
    assert action["state"] == "CANCELLED"  # decided by ActionCancelled, not by the library


# ------------------------------------------------------------------ code re-review (round 2)


def test_re_review_a_created_attempt_makes_its_ready_task_active():
    events = [
        {"id": "e1", "seq": 1, "type": "MissionCreated", "mission_id": "m", "payload": {}},
        {
            "id": "e2",
            "seq": 2,
            "type": "TaskCommitted",
            "mission_id": "m",
            "task_id": "t",
            "payload": {"dependencies": []},
        },
        {
            "id": "e3",
            "seq": 3,
            "type": "AttemptCreated",
            "mission_id": "m",
            "task_id": "t",
            "attempt_id": "a",
            "payload": {},
        },
    ]
    projection = Projection().feed(events)
    assert (
        projection.objects["task"]["t"]["status"] == "ACTIVE"
    )  # the library's accept of AttemptCreated
    assert projection.objects["attempt"]["a"]["status"] == "PENDING"
