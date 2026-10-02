# SPDX-License-Identifier: Apache-2.0
"""2026-09-26 真机文档任务：分层任务的成功条件存的是需求编号 c-user-N，核验曾把它当准则原文，
工人只看到"c-user-1"，任何结论都无法与之逐字绑定，文档 Mission 永远交付不了。"""
from agent_orchestrator.verification.criteria import task_criterion_text

STATEMENT = "summary.md 列出三条要点并注明来自哪个来源"


def test_user_requirement_ids_resolve_to_the_original_statement():
    texts = [STATEMENT, "第二条"]
    assert task_criterion_text("c-user-1", texts) == STATEMENT
    assert task_criterion_text("c-user-2", texts) == "第二条"
    # Anything else is kept exactly: out of range, derived ids, prefixed criteria.
    for kept in ("c-user-3", "c-user-0", "c-user-1-summary-written", "file:a.md", "cite:sources/a.md"):
        assert task_criterion_text(kept, texts) == kept
