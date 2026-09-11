# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Scripted providers and request builders for the Host orchestration tests.

Built from the SDK's shipped ``agent_orchestrator.testing.fixtures`` helpers.  The default
Mission uses only ``file:`` criteria and the format / rule / critic layers, because the Host
deployment does not run model-written tests on this machine (plan 2026-09-11 §3.4).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from agent_orchestrator.testing.fixtures import (
    RoleScriptedProvider,
    critic_step,
    envelope_step,
    graph_proposal_step,
    package_of,
)

WORKSPACE_TOOLS = ["workspace_read_file", "workspace_write_file", "workspace_list"]

NOTES_TASK: dict[str, Any] = {
    "key": "A",
    "goal": "在工作区写一份 NOTES.md，列出三个要点",
    "rationale": "Mission 只有这一件工作；文件存在且 Critic 认可即满足成功条件",
    "dependencies": [],
    "success_criteria": ["file:NOTES.md"],
    "verification_policy": ["format_check", "rule_check", "critic_review"],
    "outputs": ["NOTES.md"],
    "allowed_tools": WORKSPACE_TOOLS,
    "budget": {"max_tokens": 30_000, "max_attempts": 2},
    "priority": 1.0,
}


def notes_request(key: str = "notes-1", **overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "goal": "写一份 NOTES.md，列出三个要点",
        "success_criteria": ["file:NOTES.md"],
        "idempotency_key": key,
        "budget": {"max_tokens": 200_000, "max_attempts": 4},
    }
    request.update(overrides)
    return request


def _notes_worker() -> list[object]:
    return [
        ("workspace_write_file", {"path": "NOTES.md", "content": "# 要点\n\n- 一\n- 二\n- 三\n"}),
        envelope_step(
            summary="写好了 NOTES.md",
            artifacts=["NOTES.md"],
            claims=["NOTES.md 列出了三个要点"],
        ),
    ]


def notes_provider(*, missions: int = 1) -> RoleScriptedProvider:
    """Enough script steps for ``missions`` Missions run one after another (an exhausted
    script means UNKNOWN and a hang — SDK HANDOFF §4)."""

    worker: list[object] = []
    for _ in range(missions):
        worker += _notes_worker()
    return RoleScriptedProvider(
        {
            "planner": [graph_proposal_step([NOTES_TASK]) for _ in range(missions)],
            "worker": worker,
            "critic": [critic_step(verdict="PASS", criteria_met=True) for _ in range(missions * 3)],
        }
    )


def needs_human_critic_step():  # type: ignore[no-untyped-def]
    """A Critic that passes but says it cannot reliably judge (D7-8': a person decides)."""

    def step(request):  # type: ignore[no-untyped-def]
        criteria = package_of(request).get("mission_success_criteria", [])
        body = {
            "verdict": "PASS",
            "needs_human": True,
            "findings": [],
            "mission_criteria": [
                {"criterion": c, "met": True, "reason": "scripted"} for c in criteria
            ],
        }
        return "<critic_verdict>" + json.dumps(body, ensure_ascii=False) + "</critic_verdict>"

    return step


def review_provider() -> RoleScriptedProvider:
    """One NOTES Task whose Critic escalates to a human review."""

    return RoleScriptedProvider(
        {
            "planner": [graph_proposal_step([NOTES_TASK])],
            "worker": _notes_worker(),
            "critic": [needs_human_critic_step()]
            + [critic_step(verdict="PASS", criteria_met=True) for _ in range(3)],
        }
    )


def mission_count(root: Path) -> int:
    with sqlite3.connect(root / "orchestrator.db") as db:
        return int(db.execute("SELECT COUNT(*) FROM missions").fetchone()[0])
