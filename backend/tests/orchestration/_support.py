# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Request builders and small helpers for the Host orchestration tests.

The default Mission uses only ``file:`` criteria, because the Host deployment does not
run model-written tests on this machine (plan 2026-09-11 §3.4).  Scripted model replies
come from the layered scripted lane (``_layered_lane``): the Host's default deployment
(hierarchical + task graph + assurance) with only the model replies scripted.  The flat
fixture lane and its ``test_scenario`` switch were removed on 2026-10-02.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

WORKSPACE_TOOLS = ["workspace_read_file", "workspace_write_file", "workspace_list"]

#: Native pools take one turn + its Critic per step; budgets are caps, not spending.
NATIVE_TASK_TOKENS = 1_200_000
NATIVE_MISSION_TOKENS = 8_000_000


def notes_request(key: str = "notes-1", **overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "goal": "写一份 NOTES.md，列出三个要点",
        "success_criteria": ["file:NOTES.md"],
        "idempotency_key": key,
        "budget": {"max_tokens": NATIVE_MISSION_TOKENS, "max_attempts": 4},
    }
    request.update(overrides)
    return request


def notes_provider(*, missions: int = 1) -> Any:
    """A scripted provider for NOTES Missions on the default deployment (replies are
    computed per request, so any number of Missions is served)."""

    del missions
    from ._layered_lane import LayeredScriptedProvider

    return LayeredScriptedProvider()


def mission_count(root: Path) -> int:
    with sqlite3.connect(root / "orchestrator.db") as db:
        return int(db.execute("SELECT COUNT(*) FROM missions").fetchone()[0])
