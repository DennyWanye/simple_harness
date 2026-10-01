# SPDX-License-Identifier: Apache-2.0
"""中间目标按层声明类型（HTN 精简 片 B，2026-10-01）。

层数上限不靠计数器，靠类型结构：每个目标类型声明自己在第几层，做法里只能放**更深一层**
的子目标。第 2 层没有更深的类型可放，所以它的做法只能由普通步骤组成；互相嵌套出环
（第 1 层 → 第 2 层 → 第 1 层）在注册时就被拒收。

交给子目标的要求保持原编号：子目标的类型不声明判据，用户要求用同一个编号一路往下传，
审阅员在每一层看到的都是用户的原话。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for extra in (HERE, HERE / "fixtures" / "htn"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from htn_world import Env, method, param, step  # noqa: E402

from agent_orchestrator.contracts.htn import TaskForm  # noqa: E402
from agent_orchestrator.planning.htn.registry import TaskTypeSpec  # noqa: E402

USER = ("c-user-1", "c-user-2")


def _env() -> Env:
    env = Env(mission="mission-levels")
    for name, level, criteria in (("lv.goal", 0, USER), ("lv.part-1", 1, ()), ("lv.part-2", 2, ())):
        env.register_type(name, form=TaskForm.COMPOUND, parameters=(("subject", "string"),),
                          criteria=criteria, domain="lv", level=level)
    env.register_type("lv.free", form=TaskForm.COMPOUND, parameters=(("subject", "string"),), domain="lv")
    env.register_type("lv.write", parameters=(("subject", "string"),),
                      outputs=(("delivery", "lv.delivery"),), capabilities=("plan.read",), domain="lv")
    return env


def _method(goal: str, child: str, *, method_id: str = "lv.m", links=None):
    return method(
        method_id, goal, parameter_schema=f"{goal}.params",
        steps=(step("part", child, TaskForm.COMPOUND, {"subject": param("subject")}),
               step("tail", "lv.write", TaskForm.PRIMITIVE, {"subject": param("subject")},
                    capabilities=("plan.read",))),
        ordering=(("part", "tail"),),
        links=links if links is not None else (("c-user-1", "part", "c-user-1"), ("c-user-2", "tail", None)),
        finalizer="tail")


def _problems(env: Env, contract) -> list[str]:
    receipt = env.admit(contract)
    return [f"{problem.code}: {problem.detail}" for problem in receipt.problems]


def test_a_level_is_part_of_the_type_and_absent_levels_keep_their_identity():
    env = _env()
    levelled = env.catalog.require(_ref(env, "lv.part-1"))
    assert levelled.refinement_level == 1
    assert TaskTypeSpec.from_json(levelled.to_json()).refinement_level == 1
    plain = env.catalog.require(_ref(env, "lv.write"))
    assert plain.refinement_level is None
    assert "refinement_level" not in plain.to_json()  # an unlevelled type's body is unchanged


def _ref(env: Env, name: str):
    return next(spec.task_type_ref for spec in env.catalog.task_types() if spec.task_type_ref.id == name)


def test_a_method_may_hold_a_goal_one_level_deeper():
    env = _env()
    assert _problems(env, _method("lv.goal", "lv.part-1")) == []
    assert _problems(env, _method("lv.part-1", "lv.part-2", method_id="lv.m1")) == []
    # skipping a level only makes the tree shallower
    assert _problems(env, _method("lv.goal", "lv.part-2", method_id="lv.m2")) == []


def test_a_method_cannot_hold_a_goal_of_its_own_level_or_above():
    env = _env()
    for goal, child in (("lv.part-2", "lv.part-1"), ("lv.part-1", "lv.goal"), ("lv.part-2", "lv.part-2")):
        problems = _problems(env, _method(goal, child, method_id=f"lv.bad-{goal[-1]}{child[-1]}"))
        assert any(item.startswith("UNBOUNDED_RECURSION") and "level" in item for item in problems), problems


def test_a_levelled_goal_cannot_hold_an_unlevelled_goal():
    """No level, no bound: a goal type outside the layering could be expanded without end."""
    env = _env()
    problems = _problems(env, _method("lv.part-1", "lv.free", method_id="lv.unlevelled"))
    assert any(item.startswith("UNBOUNDED_RECURSION") and "level" in item for item in problems), problems


def test_a_requirement_handed_to_a_sub_goal_keeps_its_identifier():
    env = _env()
    renamed = _method("lv.goal", "lv.part-1", method_id="lv.renamed",
                      links=(("c-user-1", "part", "c-local"), ("c-user-2", "tail", None)))
    problems = _problems(env, renamed)
    assert any("c-user-1" in item and "keeps its identifier" in item for item in problems), problems


def test_types_outside_the_layering_are_not_touched_by_it():
    env = _env()
    env.register_type("lv.free-2", form=TaskForm.COMPOUND, parameters=(("subject", "string"),), domain="lv")
    contract = _method("lv.free", "lv.free-2", method_id="lv.legacy",
                       links=(("c-own", "part", "c-renamed"), ("c-other", "tail", None)))
    assert _problems(env, contract) == []
