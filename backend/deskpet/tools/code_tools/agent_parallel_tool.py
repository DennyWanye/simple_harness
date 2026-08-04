# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Public schema for parallel child-run delegation.

The product Harness owns scheduling, persistence, restart, and result
delivery. The removed legacy implementation created process-local child
``AgentLoop`` instances and duplicated those responsibilities.
"""

from __future__ import annotations

from typing import Any


_MIN_SUBAGENTS = 2
_MAX_SUBAGENTS = 8
_KIND_ENUM = ["general", "research", "code", "fileops", "doc", "web"]

_SCHEMA: dict[str, Any] = {
    "name": "agent_parallel",
    "description": (
        "Run 2-8 independent child tasks concurrently. Each task may select "
        "a kind, capability subset, file contract, and prompt-cache mode."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subagents": {
                "type": "array",
                "minItems": _MIN_SUBAGENTS,
                "maxItems": _MAX_SUBAGENTS,
                "description": "Independent child-run task specifications.",
                "items": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Stable identifier for this child task.",
                        },
                        "kind": {
                            "type": "string",
                            "enum": _KIND_ENUM,
                            "description": "Capability and execution profile.",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "Task or question for this child run.",
                        },
                        "tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional child capability subset.",
                        },
                        "input_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files this child may read.",
                        },
                        "output_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files this child is expected to write.",
                        },
                        "forbidden_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files this child must not modify.",
                        },
                        "success_criteria": {
                            "type": "string",
                            "description": "How to judge this child task complete.",
                        },
                        "cache_mode": {
                            "type": "string",
                            "enum": ["fork", "fresh"],
                            "description": "Prompt cache strategy for this child.",
                        },
                    },
                    "required": ["task_id", "prompt"],
                },
            },
            "cache_mode": {
                "type": "string",
                "enum": ["fork", "fresh"],
                "description": "Default prompt cache strategy for the batch.",
            },
        },
        "required": ["subagents"],
    },
}


__all__ = ["_SCHEMA"]
