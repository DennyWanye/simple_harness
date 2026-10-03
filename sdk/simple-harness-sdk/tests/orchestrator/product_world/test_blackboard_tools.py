# SPDX-License-Identifier: Apache-2.0
"""执行者的黑板读工具（HTN 补齐阶段 C）。

金丝雀：产品路径（原生执行池）上执行者调 ``knowledge_list`` 能路由到编排网关并拿到目录。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.testing.product_world import DEFAULT_TOOLS, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, worker_reply

KNOWLEDGE_TOOLS = ("knowledge_list", "knowledge_read")


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _tool_results(request: Any) -> list[str]:
    return [str(message.content) for message in request.messages if "tool" in str(message.role).lower()]


def test_worker_can_call_the_knowledge_tools_on_the_native_pool(tmp_path):
    seen: list[str] = []

    def worker(request: Any) -> Any:
        results = _tool_results(request)
        if not results:
            return ("knowledge_list", {})
        if len(results) == 1:
            seen.append(results[0])
            # hand the rest to the ordinary script, which counts tool messages: skip ours
            return ("workspace_write_file", {"path": "NOTES.md", "content": "# 要点\n\n- 一\n- 二\n- 三\n"})
        reply = worker_reply(request)
        return reply if isinstance(reply, str) else worker_reply_final(request)

    def worker_reply_final(request: Any) -> str:
        # ``worker_reply`` counts tool messages against the declared outputs; with our extra
        # list call it already sees enough of them and returns the envelope.
        raise AssertionError("the script should have reached the result envelope")

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(worker=worker),
                                 allowed_tools=DEFAULT_TOOLS + KNOWLEDGE_TOOLS) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "kn-canary"})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert mission.status.value == "COMPLETED", mission.final_report
            assert seen, "the worker never got a knowledge_list result"
            body = json.loads(seen[0]) if seen[0].lstrip().startswith("{") else {"raw": seen[0]}
            assert "error" not in json.dumps(body).lower() or "denied" not in seen[0].lower(), seen[0]
            return seen[0]

    print(asyncio.run(case())[:600])


# --------------------------------------------------------------------------------------
# 摘要层（阶段 C3）：执行者自己写的 summary，审阅员核对忠实后进黑板第四层，也推给后面的步骤
# --------------------------------------------------------------------------------------
from agent_orchestrator.testing.fixtures import package_of  # noqa: E402
from agent_orchestrator.testing.scripted_replies import (  # noqa: E402
    decision,
    planner_reply,
    review_input,
    review_reply,
)


def _two_steps(context: dict[str, Any]) -> dict[str, Any]:
    request = context["request"]
    operator = next(item for item in request["operators"]
                    if str(item["task_type_ref"]["id"]).endswith("prepare-delivery"))
    first, second = [item["id"] for item in request["criterion_evidence"]]
    identity = request["new_method_identity"]
    step = {"task_type_ref": operator["task_type_ref"], "form": "primitive", "arguments": {},
            "required_capabilities": list(operator["required_capabilities"]), "obligation_relation": "refines_parent"}
    return {
        "schema_version": 1, "method_id": identity["method_id"], "method_version": identity["method_version"],
        "goal_type_ref": request["goal_type_ref"],
        "parameter_schema_ref": request["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [],
        "steps": [{"local_id": "a", **step}, {"local_id": "b", **step}],
        "ordering": [{"before": "a", "after": "b"}],
        "required_capabilities": [], "expected_effects": [],
        "composition": {"criterion_links": [
            {"parent_criterion_id": first, "child_step": "a", "child_criterion_id": first, "evidence_requirement": "a 写第一份"},
            {"parent_criterion_id": second, "child_step": "b", "child_criterion_id": second, "evidence_requirement": "b 写第二份"}],
            "outputs": {}, "finalizer_step": "b", "independent_review_required": True},
        "basis_refs": [],
    }


def _two_step_planner(request: Any):
    package = package_of(request)
    contexts = package.get("method_proposal_contexts") or []
    if contexts and not (package.get("method_selection") or [{}])[0].get("applicable"):
        return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                        {"method_proposal": {"method": _two_steps(contexts[0]), "rationale": "两步各写一份。"}},
                        "先 a 后 b。")
    return planner_reply(request)


def _find(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for item in value.values():
            found = _find(item, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find(item, key)
            if found is not None:
                return found
    return None


@pytest.mark.parametrize("case", ["faithful", "not_faithful", "acceptance_gone"])
def test_checked_summary_layer(tmp_path, monkeypatch, case):
    """核对过忠实、且这一步的验收仍站着的摘要，才以 layer=summary 出现在黑板目录里，带原结果指纹、
    哈希、产物与核对它的审阅记录；后面的步骤收到的 step_summaries 是同一行。

    **改坏检验**：step_summaries 不看 faithful → 第二支出现摘要行；不看验收是否仍站着 → 第三支出现。"""
    from agent_orchestrator.context import knowledge_tools

    pushed: list[Any] = []

    def reviewer(request: Any):
        package = review_input(request)
        if package is None:
            return None
        return review_reply(package, summary=lambda row: {"faithful": case != "not_faithful",
                                                          "reason": "与结果里的文件一致"})

    def worker(request: Any):
        package = package_of(request)
        if "notes/b.md" in json.dumps(package.get("task_contract", {}).get("outputs") or []):
            pushed.append(_find(package, "step_summaries"))
        return worker_reply(request)

    async def run():
        provider = LayeredScriptedProvider(planner=_two_step_planner, reviewer=reviewer, worker=worker)
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写两份笔记", "idempotency_key": f"sum-{case}",
                                       "success_criteria": ["file:notes/a.md", "file:notes/b.md"]})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=30)
            assert mission.status.value == "COMPLETED", mission.final_report
            if case == "acceptance_gone":
                import agent_orchestrator.memory.knowledge_standing as standing

                monkeypatch.setattr(standing, "acceptance_is_current", lambda *args: (False, "test"))
            rows = knowledge_tools.step_summaries(world.store, mission_id)
            listed = knowledge_tools.read_knowledge_tool(world.store, mission_id, "knowledge_list", {"limit": 5})
            layers = [item["layer"] for item in listed["items"]]
            if case != "faithful":
                assert rows == [] and "summary" not in layers and "raw_ref" in layers
                return
            assert len(rows) == 2 and layers.count("summary") == 2
            row = rows[-1]  # step a
            assert set(row) == {"layer", "id", "source_task", "summary", "summary_sha256", "result_ref",
                                "artifacts", "checked_by"}
            assert row["summary"] == "写好了要求的文件" and row["artifacts"][0]["path"] == "notes/a.md"
            read = knowledge_tools.read_knowledge_tool(world.store, mission_id, "knowledge_read", {"id": row["id"]})
            assert read["content"] == row["summary"] and read["summary_sha256"] == row["summary_sha256"]
            assert pushed and pushed[-1] == [row]

    asyncio.run(run())
