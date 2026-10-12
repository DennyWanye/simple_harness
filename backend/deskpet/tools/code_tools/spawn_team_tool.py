# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Public schema for homogeneous team delegation.

Team execution is normalized into durable Harness ``DelegateRun`` commands.
No process-local team scheduler or nested ``AgentLoop`` lives here.
"""

from __future__ import annotations

from typing import Any

_KIND_ENUM = ["general", "research", "code", "fileops", "doc", "web"]

_SCHEMA: dict[str, Any] = {
    "name": "spawn_team",
    "description": (
        "Delegate a homogeneous task pool to 1-8 teammates. Use "
        "agent_parallel when child tasks need different kinds or prompts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_descriptions": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "Initial shared task pool.",
            },
            "num_teammates": {
                "type": "integer",
                "description": "Concurrent teammate count, from 1 to 8.",
            },
            "kind": {
                "type": "string",
                "enum": _KIND_ENUM,
                "description": "Capability and execution profile for the team.",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "Wall-clock limit for the team.",
            },
        },
        "required": ["task_descriptions"],
    },
}


__all__ = ["_SCHEMA"]
