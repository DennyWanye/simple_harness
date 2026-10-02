"""An Attempt's template is fixed by its Task kind (the Worker variants were removed on
2026-10-02; nothing sets ``context.role`` any more, and a stale one is ignored)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_orchestrator.runtime.role_templates import ROLES, role_for_task


@pytest.mark.parametrize(
    ("kind", "role"), [("work", "worker"), ("conflict", "arbiter"), ("synthesis", "synthesizer")]
)
def test_template_follows_the_task_kind(kind, role):
    task = SimpleNamespace(kind=kind, context={"role": "explorer"})
    assert role_for_task(task) is ROLES[role]


def test_removed_worker_variants_are_not_roles():
    assert set(ROLES) == {"arbiter", "critic", "planner", "synthesizer", "worker"}
