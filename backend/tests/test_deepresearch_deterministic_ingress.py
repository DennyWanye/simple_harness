from __future__ import annotations

from pathlib import Path

from deskpet.agent.assembler.components.tool import is_deep_research_request


def test_strong_deepresearch_intent_is_deterministic() -> None:
    assert is_deep_research_request("帮我深度调研 Agent-Reach 并给出完整报告")
    assert is_deep_research_request(
        "可以帮我调研一下，现在AI 相关的最新最有价值的技术相关的信息吗？"
    )
    assert is_deep_research_request("请做一份竞品研究")
    assert is_deep_research_request("请基于国家统计局官方资料，调研2024年中国人口")
    assert is_deep_research_request("请用国家统计局原始资料调研中国人口")
    assert not is_deep_research_request("打开这个网页看看")
    assert not is_deep_research_request("Agent-Reach 是什么")
    assert not is_deep_research_request("研究显示今年人口结构发生变化")


def test_main_starts_deepresearch_before_outer_agent_loop() -> None:
    source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")

    ingress = source.index('"deepresearch_workflow_accepted sid=%s run_id=%s"')
    outer_loop = source.index("async for ev in _agent.run(", ingress)
    assert ingress < outer_loop
    section = source[source.rfind("if (", 0, ingress):ingress]
    assert "_is_deep_research_request" in section
    assert "_start_deepresearch" in section
    assert '"text": ""' in section


def test_main_snapshots_research_source_switches_into_durable_run() -> None:
    source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")

    start = source.index("async def _start_deepresearch_graph")
    end = source.index("sid = str(args.get", start)
    section = source[start:end]

    assert 'config.raw.get("research", {})' in section
    assert '("direct_sources", "source_packs")' in section
    assert "research_config.setdefault" in section
