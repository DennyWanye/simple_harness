from __future__ import annotations

import pytest

from deskpet.workflows.routing import WorkflowRoute, route_task


@pytest.mark.parametrize(
    "prompt",
    [
        "Explain how backend/api.py handles retries",
        "Where is the workflow checkpoint created in this repo?",
        "Search the repository for uses of ProposalStateV1",
        "grep the codebase for todo_write",
        "请解释这个后端模块的状态机",
        "在仓库里查找 workflow_step_id 的引用",
        "这个文件为什么这样设计？",
        "Review this function for race conditions",
    ],
)
def test_pure_workspace_questions_and_read_only_requests_stay_react(prompt: str) -> None:
    assert route_task(prompt, workspace_context=True).route is WorkflowRoute.REACT


def test_explicit_negative_write_instruction_overrides_write_tokens_and_tools() -> None:
    decision = route_task(
        "不要修改任何文件，只解释这个测试为什么失败",
        workspace_context=True,
        proposed_tools=["write_file"],
    )
    assert decision.route is WorkflowRoute.REACT
    assert decision.reason == "read_only_or_chat"


@pytest.mark.parametrize(
    "prompt",
    [
        "Review this function and fix the race condition",
        "Search the repo, then implement the missing handler",
        "请分析这个模块并修复测试",
        "修改 backend/api.py 并运行测试",
        "Execute the test command and fix any failures",
    ],
)
def test_mixed_or_explicit_workspace_actions_route_to_complex_code(prompt: str) -> None:
    assert route_task(prompt, workspace_context=True).route is WorkflowRoute.CODE_COMPLEX


def test_write_tool_signal_wins_but_chat_mode_remains_legacy() -> None:
    assert (
        route_task("Please take a look", proposed_tools=["edit_file"]).route
        is WorkflowRoute.CODE_COMPLEX
    )
    assert (
        route_task(
            "Please take a look",
            proposed_tools=["edit_file"],
            workspace_context=True,
            mode="chat",
        ).route
        is WorkflowRoute.REACT
    )


def test_unknown_non_question_workspace_task_prefers_graph() -> None:
    decision = route_task("Handle the repository task", workspace_context=True)

    assert decision.route is WorkflowRoute.CODE_COMPLEX
    assert decision.reason == "unknown_workspace_task"


@pytest.mark.parametrize(
    "prompt",
    [
        "请创建文件 backend/example.txt 并写入测试内容",
        "请新增文件 plans/ppt-session-progress/code-smoke.txt 并读取验证",
        "Modify plans/deep_research/checkpoint.txt in this repository",
    ],
)
def test_code_actions_are_not_stolen_by_keywords_inside_paths(prompt: str) -> None:
    assert route_task(prompt, workspace_context=True).route is WorkflowRoute.CODE_COMPLEX
