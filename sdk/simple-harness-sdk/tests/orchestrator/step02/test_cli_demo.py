# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 2 · C4: the operator commands read back the library a deployment wrote.

Missions are created only by a deployment (``mission create`` was removed on 2026-10-03);
here the SDK's product-shaped world creates one, and the CLI reads it back."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from agent_orchestrator.__main__ import EXIT_FAILED, EXIT_OK, EXIT_USAGE, main
from agent_orchestrator.testing.fixtures import RoleScriptedProvider, lift_immutable_guards
from agent_orchestrator.testing.product_world import product_world


def _created(evidence: Path, key: str) -> str:
    async def case() -> str:
        async with product_world(evidence, RoleScriptedProvider({})) as world:
            return world.create({"goal": "x", "success_criteria": ["file:a.py"], "idempotency_key": key})["mission_id"]

    return asyncio.run(case())


def test_the_cli_has_no_create_command(capsys):
    with pytest.raises(SystemExit):
        main(["mission", "create", "--evidence-dir", "x"])


def test_read_commands_answer_for_a_deployment_created_mission(tmp_path, capsys):
    evidence = Path(tmp_path) / "evidence"
    mission_id = _created(evidence, "k")
    assert main(["mission", "get", "--evidence-dir", str(evidence), mission_id]) == EXIT_OK
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["mission"]["id"] == mission_id
    assert main(["mission", "events", "--evidence-dir", str(evidence), mission_id]) == EXIT_OK
    events = json.loads(capsys.readouterr().out)
    assert events and events[0]["type"] == "MissionCreated"


def test_replay_answers_with_a_usage_error_or_a_complete_report(tmp_path, capsys):
    """``replay`` (全业务重放 v3): a missing library is an answer (exit 2), never a traceback;
    a Mission just created has no inconsistent table and its library is fully named; a
    damaged source record makes it inconsistent (exit 1)."""

    evidence = Path(tmp_path) / "evidence"
    assert main(["replay", "--evidence-dir", str(evidence), "mission-x"]) == EXIT_USAGE
    assert "no library" in json.loads(capsys.readouterr().out)["error"]

    mission_id = _created(evidence, "r")
    code = main(["replay", "--evidence-dir", str(evidence), "--attribution", mission_id])
    report = json.loads(capsys.readouterr().out)
    consistent = report["mission"]["status"] == "CONSISTENT" and report["library"]["status"] == "CONSISTENT"
    assert code == (EXIT_OK if consistent else EXIT_FAILED)
    assert report["library"]["status"] == "CONSISTENT"
    assert "INCONSISTENT" not in {item["status"] for item in report["mission"]["tables"].values()}
    assert report["attribution"]["mission_id"] == mission_id
    assert main(["replay", "--evidence-dir", str(evidence), "no-such-mission"]) == EXIT_USAGE
    assert "no Mission" in json.loads(capsys.readouterr().out)["error"]

    # 库里一条源记录被改坏：重放报不一致、退出 1（改坏检验 G-16：遇到不一致仍退出 0 → 变红）
    library = sqlite3.connect(evidence / "orchestrator.db")
    lift_immutable_guards(library, "task_semantics")
    library.execute("UPDATE task_semantics SET binding_json = binding_json || ' ' WHERE mission_id = ?",
                    (mission_id,))
    library.commit()
    library.close()
    assert main(["replay", "--evidence-dir", str(evidence), mission_id]) == EXIT_FAILED
    assert json.loads(capsys.readouterr().out)["mission"]["status"] == "INCONSISTENT"
