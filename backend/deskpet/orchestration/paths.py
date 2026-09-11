# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Where the orchestration library lives (plan §3.3, decision D1).

``<user_data>/data/agent-orchestrator/`` holds ``orchestrator.db``, the orchestrator's own
SDK execution library and the Attempt workspaces — apart from the chat's
``execution-v6.sqlite3`` (two runtimes, two owners, two lease tables).  The test-only
scenario gets ``agent-orchestrator-test/`` so it never touches the production library.
Neither may be a symlink or resolve outside the user data directory.
"""

from __future__ import annotations

from pathlib import Path

ORCHESTRATION_DIR = "agent-orchestrator"
TEST_SCENARIO_DIR = "agent-orchestrator-test"


class OrchestrationPathError(RuntimeError):
    pass


def _checked(user_data: str | Path, name: str) -> Path:
    base = Path(user_data)
    data = base / "data"
    root = data / name
    for candidate in (data, root):
        if candidate.is_symlink():
            raise OrchestrationPathError(f"{candidate.name} must not be a symlink")
    try:
        root.resolve(strict=False).relative_to(base.resolve(strict=False))
    except ValueError as error:
        raise OrchestrationPathError("the orchestration directory escapes user data") from error
    return root


def orchestration_root(user_data: str | Path) -> Path:
    return _checked(user_data, ORCHESTRATION_DIR)


def test_scenario_root(user_data: str | Path) -> Path:
    return _checked(user_data, TEST_SCENARIO_DIR)


__all__ = (
    "ORCHESTRATION_DIR",
    "TEST_SCENARIO_DIR",
    "OrchestrationPathError",
    "orchestration_root",
    "test_scenario_root",
)
