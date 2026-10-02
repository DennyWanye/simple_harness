# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 2 · C4: ``python -m agent_orchestrator mission create`` validates and is idempotent,
and the operator commands read back the same library (the flat-mode demos were removed on
2026-10-02)."""

from __future__ import annotations

import json
from pathlib import Path

from agent_orchestrator.__main__ import EXIT_OK, main

EVIDENCE_FILES = {
    "baseline.json",
    "events.jsonl",
    "final_state.json",
    "verification.json",
    "costs.json",
    "test-report.json",
}


def test_mission_create_validates_and_is_idempotent(tmp_path, capsys):
    evidence = Path(tmp_path) / "evidence"
    spec_path = Path(tmp_path) / "spec.json"
    spec_path.write_text(json.dumps({"goal": "x", "success_criteria": [], "idempotency_key": "k"}))
    import pytest

    from agent_orchestrator.api.missions import MissionRequestError

    with pytest.raises(MissionRequestError):
        main(
            [
                "mission",
                "create",
                "--evidence-dir",
                str(evidence),
                "--spec",
                str(spec_path),
                "--tenant",
                "t",
            ]
        )
    spec_path.write_text(
        json.dumps(
            {
                "goal": "x",
                "success_criteria": ["file:a.py"],
                "idempotency_key": "k",
                "budget": {"max_attempts": 1},
            }
        )
    )
    assert (
        main(
            [
                "mission",
                "create",
                "--evidence-dir",
                str(evidence),
                "--spec",
                str(spec_path),
                "--tenant",
                "t",
            ]
        )
        == EXIT_OK
    )
    first = json.loads(capsys.readouterr().out)
    assert first["created"] is True
    assert (
        main(
            [
                "mission",
                "create",
                "--evidence-dir",
                str(evidence),
                "--spec",
                str(spec_path),
                "--tenant",
                "t",
            ]
        )
        == EXIT_OK
    )
    second = json.loads(capsys.readouterr().out)
    assert second["created"] is False and second["mission_id"] == first["mission_id"]
    # the read commands answer for the Mission just created
    assert main(["mission", "get", "--evidence-dir", str(evidence), first["mission_id"]]) == EXIT_OK
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["mission"]["id"] == first["mission_id"]
    assert main(["mission", "events", "--evidence-dir", str(evidence), first["mission_id"]]) == EXIT_OK
    events = json.loads(capsys.readouterr().out)
    assert events and events[0]["type"] == "MissionCreated"


def test_replay_answers_with_a_usage_error_or_a_complete_report(tmp_path, capsys):
    """``replay`` (plan D8-9'): a missing library is an answer (exit 2), never a traceback;
    a Mission just created replays consistently with full coverage, and so does its
    attribution read (exit 0)."""

    from agent_orchestrator.__main__ import EXIT_USAGE

    evidence = Path(tmp_path) / "evidence"
    assert main(["replay", "--evidence-dir", str(evidence), "mission-x"]) == EXIT_USAGE
    assert "no library" in json.loads(capsys.readouterr().out)["error"]

    spec_path = Path(tmp_path) / "spec.json"
    spec_path.write_text(json.dumps({"goal": "x", "success_criteria": ["file:a.py"], "idempotency_key": "r"}))
    assert main(["mission", "create", "--evidence-dir", str(evidence), "--spec", str(spec_path), "--tenant", "t"]) == EXIT_OK
    mission_id = json.loads(capsys.readouterr().out)["mission_id"]
    assert main(["replay", "--evidence-dir", str(evidence), "--attribution", mission_id]) == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["comparison"]["consistent"] is True and report["gaps"] == []
    assert report["attribution"]["mission_id"] == mission_id
    assert main(["replay", "--evidence-dir", str(evidence), "no-such-mission"]) == EXIT_USAGE
