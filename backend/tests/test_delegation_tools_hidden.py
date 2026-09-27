# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""NEXT-TG-1.0 §9: a tool the model can see but can never use is not "enabled".

Delegation (agent / agent_parallel / spawn_team / spawn_subagents, and the
await_subagents join) is not wired into the foreground Run: the four always answered
``delegation_unavailable``.  They are kept out of the model-visible SDK catalog until
the child-run path is wired (a named gap in PLAN-STATUS); background work goes
through ``mission_start``.
"""
from __future__ import annotations

from types import SimpleNamespace

import main
from deskpet.tools.code_tools.spawn_subagents_tool import UNWIRED_DELEGATION_TOOL_NAMES


def _spec(name: str):
    return SimpleNamespace(name=name, description=name, input_schema={"type": "object", "properties": {}})


def test_unwired_delegation_tools_are_not_in_the_model_visible_catalog():
    assert UNWIRED_DELEGATION_TOOL_NAMES == {"agent", "agent_parallel", "spawn_team", "spawn_subagents",
                                             "await_subagents"}
    adapter = SimpleNamespace(specs=[_spec(n) for n in ("read_file", "agent", "mission_start", "spawn_team",
                                                         "await_subagents")])
    catalog = main._freeze_sdk_catalog(adapter, 1)
    assert catalog["tool_names"] == ["read_file", "mission_start"]
