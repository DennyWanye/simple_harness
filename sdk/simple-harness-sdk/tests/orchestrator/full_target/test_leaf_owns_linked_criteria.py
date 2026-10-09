# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Desktop 2026-09-26: a leaf owes only the criteria its method links to it.

Thermos Mission, five files, five steps: every step's Task row carried all five
``file:`` checks and the whole Mission goal, so the first step wrote all five files
(and each later step would have had to write them again to pass).  A leaf the
adopted method links to some criteria now carries only those, and its goal says so.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_read_only_leaf_policy import _binding  # noqa: E402

from agent_orchestrator.contracts.htn import SideEffectKind  # noqa: E402
from agent_orchestrator.orchestrator.accepted_outputs import owned_criteria  # noqa: E402
from agent_orchestrator.orchestrator.occurrence_tasks import (  # noqa: E402
    occurrence_criteria,
    scoped_goal,
)


def _leaf(refs):
    from dataclasses import replace

    return replace(_binding(side_effect=SideEffectKind.LOCAL_WRITE), requirement_refs=tuple(refs))


def test_an_owned_leaf_carries_only_its_criteria():
    leaf = _leaf(("c-user-1", "c-user-2", "c-user-3"))
    assert occurrence_criteria(leaf, None, owned=("c-user-2",)) == ("c-user-2",)


def test_an_unlinked_leaf_keeps_every_criterion():
    leaf = _leaf(("c-user-1", "c-user-2"))
    assert occurrence_criteria(leaf, None) == ("c-user-1", "c-user-2")
    # a link to a criterion the leaf does not owe changes nothing
    assert occurrence_criteria(leaf, None, owned=("c-other",)) == ("c-user-1", "c-user-2")


def test_the_scoped_goal_names_the_share_first():
    text = scoped_goal("整个任务", ("c-user-1", "file:01-画像.md"), ["写出画像"],
                       others=("02-路线.md", "01-画像.md"))
    assert text.startswith("本步骤只负责：01-画像.md")
    assert "写出画像" in text and text.endswith("整个任务")
    # 只点名其他步骤认领的文件；本步骤自己的文件不在禁止名单里
    assert "其他步骤负责这些文件，本步骤不要创建或改写它们：02-路线.md。" in text
    assert "由本步骤自己写" in text


def test_the_scoped_goal_forbids_nothing_nobody_claimed():
    """2026-10-09 库存题：四个模块没被任何步骤列成文件检查，旧套话一刀切"其他文件不要创建"，
    执行者只写了 __main__.py。没有别的步骤认领文件时，不出现禁止句。"""

    text = scoped_goal("整个任务", ("file:inventory/__main__.py",), ["分成四个模块"])
    assert "不要创建" not in text and "由本步骤自己写" in text


def test_the_other_steps_files_come_from_their_criteria():
    from agent_orchestrator.orchestrator.occurrence_tasks import _files_of

    def item(ref, statement, checks=()):
        return SimpleNamespace(criterion_id=ref, statement=statement,
                               required_evidence_policy=SimpleNamespace(required_check_ids=checks))

    requirements = SimpleNamespace(criteria=(
        item("c-1", "file:inventory/__main__.py"), item("c-2", "file:README.md"),
        item("c-3", "附测试", ("pytest:tests", "file:tests/test_cli.py"))))
    assert _files_of(requirements, {"c-2", "c-3"}) == ("README.md", "tests/test_cli.py")
    assert _files_of(None, {"c-2"}) == ()


def test_owned_criteria_pairs_links_with_the_bound_occurrences():
    links = (
        SimpleNamespace(child_step="a", parent_criterion_id="c-user-1", evidence_requirement="A"),
        SimpleNamespace(child_step="b", parent_criterion_id="c-user-2", evidence_requirement="B"),
        SimpleNamespace(child_step=None, parent_criterion_id="c-user-3", evidence_requirement="C"),
    )
    method = SimpleNamespace(composition=SimpleNamespace(criterion_links=links, finalizer_step="b"))
    draft = SimpleNamespace(
        instance_id="mi-1",
        method_ref=SimpleNamespace(method_id="m", version=1),
        child_bindings=(SimpleNamespace(slot_key="a", occurrence_id="occ-a"),
                        SimpleNamespace(slot_key="b", occurrence_id="occ-b")))
    network = SimpleNamespace(method_instances=(draft,), is_adopted=lambda _id: True)
    semantics = SimpleNamespace(get_method=lambda _id, _v: SimpleNamespace(contract=method))
    assert owned_criteria(network, semantics) == {
        "occ-a": [("c-user-1", "A")],
        "occ-b": [("c-user-2", "B"), ("c-user-3", "C")],
    }


def test_an_unlinked_leaf_does_not_take_a_file_another_step_was_given():
    """2026-09-29 第 5 批（审阅）：Host 为每个要发布的文件补了 file:X；方法把它链接给了别的
    步骤时，没被链接的步骤兜底不再承担它，否则要写出不归它的文件、反复"文件缺失"。"""

    def item(ref, statement):
        return SimpleNamespace(criterion_id=ref, statement=statement,
                               required_evidence_policy=SimpleNamespace(required_check_ids=()))

    requirements = SimpleNamespace(criteria=(item("c-user-1", "先调研"), item("c-user-2", "file:README.md")))
    leaf = _leaf(("c-user-1", "c-user-2"))
    assert occurrence_criteria(leaf, requirements, claimed={"c-user-2"}) == ("c-user-1",)
    # 没有别的步骤认领时照旧兜底（旧行为）
    assert occurrence_criteria(leaf, requirements) == ("c-user-1", "c-user-2", "file:README.md")
    # 被认领的普通内容要求照旧兜底：只排除写文件要求
    text_only = SimpleNamespace(criteria=(item("c-user-1", "先调研"), item("c-user-2", "说明结论")))
    assert occurrence_criteria(leaf, text_only, claimed={"c-user-2"}) == ("c-user-1", "c-user-2")


def test_a_linked_leaf_goal_names_only_the_files_other_steps_own():
    """The Task row's goal lists the other steps' file criteria and not its own.

    **Mutation**: drop ``others=`` at the ``scoped_goal`` call in ``occurrence_task`` → red."""
    import dataclasses

    from test_read_only_leaf_policy import DEPLOYED

    from agent_orchestrator.contracts import Budget, Mission
    from agent_orchestrator.contracts.htn import ObligationId, OccurrenceId, OccurrenceSpec, TaskForm, TaskRef
    from agent_orchestrator.contracts.models import MissionStatus
    from agent_orchestrator.orchestrator.occurrence_tasks import occurrence_task

    def item(ref, statement):
        return SimpleNamespace(criterion_id=ref, statement=statement,
                               required_evidence_policy=SimpleNamespace(required_check_ids=()))

    requirements = SimpleNamespace(criteria=(
        item("c-1", "file:inventory/__main__.py"), item("c-2", "file:README.md"), item("c-3", "写测试")))
    mission = Mission(id="m", tenant_id="t", goal="写 inventory 包", success_criteria=("c",), status=MissionStatus.PLANNING,
                      allowed_tools=(), budget=Budget(max_tokens=1000), idempotency_key="k", version=1,
                      stop_conditions=(), risk_level="sandbox", created_at=0.0)
    binding = dataclasses.replace(_leaf(("c-1", "c-2", "c-3")), requirement_refs=("c-1", "c-2", "c-3"))
    spec = OccurrenceSpec(occurrence_id=OccurrenceId("occ-impl"), task_id=TaskRef("task-impl"),
                          obligation_id=ObligationId("obl"), form=TaskForm.PRIMITIVE)
    goal = occurrence_task(mission, spec, binding, plan_revision=1, budget=Budget(max_tokens=100), ordinal=1,
                           deployed=DEPLOYED, requirements=requirements, owned=(("c-1", "写主入口"),),
                           claimed={"c-1", "c-2", "c-3"}).task.goal
    assert goal.startswith("本步骤只负责：inventory/__main__.py")
    assert "本步骤不要创建或改写它们：README.md。" in goal and "由本步骤自己写" in goal
