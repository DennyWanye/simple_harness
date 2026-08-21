# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from deskpet.agent.assembler import build_default_assembler, load_policies
from deskpet.agent.assembler.bundle import TASK_TYPES


def _components(policy) -> set[str]:
    return set(policy.must) | set(policy.prefer)


def test_default_policy_manifest_for_harness_task_types() -> None:
    policies = load_policies()
    assert set(TASK_TYPES) <= set(policies)

    assert {"memory", "persona", "preference_profile"} <= _components(policies["chat"])
    assert "tool" in _components(policies["chat"])
    assert policies["chat"].tools == ["*"]
    assert {
        "memory_read",
        "memory_search",
        "reminder_create",
        "reminder_list",
        "reminder_cancel",
    } <= set(policies["chat"].tool_exposure.direct)
    assert "source:builtin" in policies["chat"].tool_exposure.discoverable
    assert (
        "source:capability:*"
        in policies["chat"].tool_exposure.discoverable
    )
    assert {"tool", "skill"} <= _components(policies["task"])
    assert {
        "file_read",
        "file_glob",
        "file_grep",
        "reminder_create",
        "reminder_list",
        "reminder_cancel",
    } <= set(
        policies["task"].tool_exposure.direct
    )
    assert {"tool", "workspace", "workspace_memory", "skill"} <= _components(
        policies["code"]
    )
    assert {
        "deepresearch",
        "web_search",
        "web_fetch",
        "scrapling_fetch",
    } <= set(policies["web_search"].tools)
    assert policies["emotion"].tools == []
    assert "tool" not in _components(policies["emotion"])
    assert "goal_task_create" in policies["plan"].tool_exposure.discoverable


def test_every_policy_with_nonempty_tools_runs_tool_component() -> None:
    for task_type, policy in load_policies().items():
        if policy.tools:
            assert "tool" in _components(policy), task_type


def test_every_policy_component_is_registered_by_default_assembler() -> None:
    policies = load_policies()
    expected = {
        component
        for policy in policies.values()
        for component in [*policy.must, *policy.prefer]
    }
    assembler = build_default_assembler()
    registry = assembler._registry  # policy/registry contract guard
    registered = set(registry._components)
    assert expected <= registered
