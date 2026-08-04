# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Public schema for the single-subagent delegation tool.

Execution is owned by the product Harness ``DelegateRun`` boundary. This
module intentionally contains no runtime builder and no nested ``AgentLoop``
owner; it remains only so the stable public tool contract has a small,
dependency-free home.
"""

from __future__ import annotations


_SCHEMA = {
    "name": "agent",
    "description": (
        "Delegate one focused, self-contained task to a child run and return "
        "its final result."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "One-line summary of the delegated task.",
            },
            "prompt": {
                "type": "string",
                "description": "The task or question for the child run.",
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional capability subset for the child run.",
            },
        },
        "required": ["description", "prompt"],
    },
}


__all__ = ["_SCHEMA"]
