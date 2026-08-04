from __future__ import annotations

import pytest

from deskpet.workflows.routing import WorkflowRoute, route_task


@pytest.mark.parametrize(
    ("prompt", "route"),
    [
        ("请对固态电池行业做一次深度调研", WorkflowRoute.DEEP_RESEARCH),
        ("Deep research the latest battery supply chain", WorkflowRoute.DEEP_RESEARCH),
        (
            "调研 2026 年本地 AI 助手的主要技术路线、隐私取舍和适用团队，给出带来源的选型建议。",
            WorkflowRoute.DEEP_RESEARCH,
        ),
        (
            "帮我调研一下，金价的变动和国际局势以及国家央行操作的时间线图",
            WorkflowRoute.DEEP_RESEARCH,
        ),
        ("帮我生成一份季度汇报 PPT", WorkflowRoute.PPT_PRO),
        ("Build a PowerPoint deck for the launch", WorkflowRoute.PPT_PRO),
        ("请修改这个项目并运行测试", WorkflowRoute.CODE_COMPLEX),
        ("Fix the API implementation and test it", WorkflowRoute.CODE_COMPLEX),
        ("解释这个模块为什么这样设计", WorkflowRoute.REACT),
        ("Review this code and tell me the risks", WorkflowRoute.REACT),
        ("今天天气怎么样", WorkflowRoute.REACT),
    ],
)
def test_route_golden_corpus(prompt, route):
    assert route_task(prompt).route is route


def test_write_tool_preflight_routes_to_graph():
    assert route_task("处理一下", proposed_tools=["write_file"]).route is WorkflowRoute.CODE_COMPLEX


def test_unknown_workspace_task_prefers_graph_but_chat_override_does_not():
    assert route_task("帮我处理一下", workspace_context=True).route is WorkflowRoute.CODE_COMPLEX
    assert route_task("帮我处理一下", workspace_context=True, mode="chat").route is WorkflowRoute.REACT


def test_negated_research_stays_in_react():
    assert route_task("不用调研，只解释这句话").route is WorkflowRoute.REACT
