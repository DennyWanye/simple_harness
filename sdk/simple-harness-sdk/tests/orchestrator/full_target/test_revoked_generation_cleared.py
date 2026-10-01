"""结构修复真机第 7 局（2026-09-30）：新一代的目标结果提交后，"旧派发作废"标记要清掉。

后继步骤提交第 2 版计划时，被换代的目标（含任务根目标）各记一条"旧派发作废"标记
（dispatch_generation_revoked）。整个 SDK 没有任何地方再把它清掉，而收尾检查只要看到
根目标上有标记就判"内容未完成"——于是终审通过后任务永远收不了尾。换代同时把合同版本
加一，目标结果提交又要求合同版本等于当前版本，所以提交的结果一定属于新一代：标记在
这里清掉。别的原因的标记、别的目标的标记照旧保留。

世界取自 ``test_resolution_commits``（带协议绑定的真实世界，根结论命令由
``attempt_root_resolution`` 装出）。那里根目标的发生编号与任务编号相同，所以根目标上
"按发生记"和"按任务记"的两条作废标记是同一条。
"""
from __future__ import annotations

from typing import Any

from test_resolution_commits import ROOT_TASK, world  # noqa: F401


def _marks(built: Any) -> dict[tuple[str, str], str]:
    rows = built.store.connection.execute(
        "SELECT subject_id, reason, state FROM validity_dirty WHERE mission_id=?", (built.mission.id,))
    return {(str(r[0]), str(r[1])): str(r[2]) for r in rows}


def test_a_committed_goal_resolution_clears_its_revoked_generation_marks(world: Any) -> None:  # noqa: F811
    semantics = world.semantics
    root_subjects = sorted({world.root_occurrence, ROOT_TASK})
    for subject, reason in ((*((item, "dispatch_generation_revoked") for item in root_subjects),
                             (world.leaf_occurrence, "dispatch_generation_revoked"),
                             (world.root_occurrence, "evidence_invalidated"))):
        semantics.mark_dirty(world.mission.id, subject_kind="occurrence", subject_id=subject,
                             scope_id="mission", epoch=0 if reason != "evidence_invalidated" else 1,
                             reason=reason)
    world.accept()
    world.resolve()
    marks = _marks(world)
    for subject in root_subjects:
        assert marks[(subject, "dispatch_generation_revoked")] == "CLEARED"
    # another goal's revocation and a mark for another reason are not this result's to clear
    assert marks[(world.leaf_occurrence, "dispatch_generation_revoked")] == "PENDING"
    assert marks[(world.root_occurrence, "evidence_invalidated")] == "PENDING"
