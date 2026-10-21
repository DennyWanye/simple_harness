# SPDX-License-Identifier: Apache-2.0
"""新做法审阅的说明里要有"子目标"这两条系统事实（HTN 精简 片 B 真机第 5 局，2026-10-01）。

真机上审阅员把一个带子目标的根做法打回，理由是"要求应挂在真正产出文件的步骤上，把子目标
拆成两个独立步骤""子目标与最后一步之间要加一个显式的审阅步骤"。它不知道两件系统保证的事：
子目标自己的做法之后会单独规划、单独过同样的审阅；子目标做完先过独立的组合审阅，排在它后面
的步骤在那之后才开工。说明里只补事实，怎么判仍由审阅员定。
"""

from __future__ import annotations

from agent_orchestrator.assurance.review_input import REVIEW_INSTRUCTIONS


def test_the_instructions_state_how_a_sub_goal_step_is_planned_and_gated() -> None:
    section = REVIEW_INSTRUCTIONS[REVIEW_INSTRUCTIONS.index("METHOD_PLAN") :]
    for fact in ("form 为 compound", "单独规划", "组合审阅", "之后才开工"):
        assert fact in section, fact
    # still the reviewer's judgement: nothing here tells it what verdict to give a sub-goal
    assert "由你判断" in section


def test_the_reviewer_is_told_how_to_read_ordering_and_output_bindings() -> None:
    """2026-10-21 Sol 真实模型局：做法原文键按字母排序，after 排在 before 前面，审阅员把先后读反了，
    四版做法都因"顺序反了"被打回。说明里要写清 before 先做、按字段名读。"""

    paragraph = REVIEW_INSTRUCTIONS[REVIEW_INSTRUCTIONS.index("METHOD_PLAN") :]
    assert '{"before": 步骤甲, "after": 步骤乙}' in paragraph and "甲先做" in paragraph
    assert "按字母" in paragraph and "不要按出现顺序读" in paragraph
    assert '"op":"output"' in paragraph
