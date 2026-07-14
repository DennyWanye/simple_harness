from __future__ import annotations

from pathlib import Path

from deskpet.agent.assembler.components.tool import is_deep_research_request


def test_strong_deepresearch_intent_is_deterministic() -> None:
    assert is_deep_research_request("帮我深度调研 Agent-Reach 并给出完整报告")
    assert is_deep_research_request("请做一份竞品研究")
    assert not is_deep_research_request("打开这个网页看看")
    assert not is_deep_research_request("Agent-Reach 是什么")


def test_main_starts_deepresearch_before_outer_agent_loop() -> None:
    source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")

    ingress = source.index('"deepresearch_workflow_accepted sid=%s run_id=%s"')
    outer_loop = source.index("async for ev in _agent.run(", ingress)
    assert ingress < outer_loop
    section = source[source.rfind("if (", 0, ingress):ingress]
    assert "_is_deep_research_request" in section
    assert "_start_deepresearch" in section
    assert '"text": ""' in section
