# SPDX-License-Identifier: Apache-2.0
"""代码领域测试世界（分诊裁决④：代码领域只作 SDK 测试用规划世界，本轮保留）。

与产品同一份部署组装（:func:`agent_orchestrator.testing.product_world.product_world`：建任务时
初始化根并绑定执行图、保证通道、原生执行池），只把规划世界换成随 SDK 发的代码领域种子库，根目标
换成 ``code.fix-failing-test``。三样是测试替身：模型回复（脚本）、用量计数器、这个规划世界。

* 根参数：代码目标的类型参数要 ``repository`` / ``failing_test``；这里给的是工作区里的相对名字，
  不放主机绝对路径（分诊裁决⑧-2：绝对路径会进终审材料和各处哈希）。
* 做法必须由规划器**提出**：种子做法的判据链接挂在代码目标自己的判据号（``c-test-passes`` 等）上，
  产品根的要求号是 ``c-user-<n>``，种子做法对不上产品根；规划器按请求里的 ``criterion_evidence``
  写链接，与桌面世界一样经独立审阅再采用。

世界本身在 :mod:`agent_orchestrator.testing.code_domain_world`（裁决④）；这里只放脚本化回复与夹具。
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    planner_reply,
)

from agent_orchestrator.testing.code_domain_world import (  # noqa: E402
    CODE_NAMES,
    CONFIG,
    ROOT_TYPE,
    TOOLS,
    code_root_parameters,
    code_world,
    code_world_factory,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "htn"


def c1_method() -> dict[str, Any]:
    """C1-r1 那次真实合成、登记进库的做法（读事实 → 复现 → 打补丁 → {验证, 检查} → 总结）。"""
    return json.loads((FIXTURES / "c1_inspect_input" / "method.json").read_text())


def proposing_planner(shape: Callable[[], dict[str, Any]]) -> Callable[[Any], Any]:
    """规划器：库里没有适用的做法时提出 ``shape()`` 这个做法（身份、目标、参数结构取自请求，
    根判据链接改挂请求给的 ``criterion_evidence``）；之后照常采用通过审阅的做法。"""

    def planner(request: Any) -> Any:
        package = package_of(request)
        contexts = package.get("method_proposal_contexts") or []
        if contexts and not (package.get("method_selection") or [{}])[0].get("applicable"):
            context = contexts[0]
            body = _proposed(shape(), context["request"])
            return decision(context["subject_key"], "PROPOSE_METHOD",
                            {"method_proposal": {"method": body, "rationale": "按事实、复现、补丁、验证、解释来修。"}},
                            "库里没有挂在这些要求上的做法，提出一个。")
        return planner_reply(request)

    return planner


def _proposed(method: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(method)
    identity = request["new_method_identity"]
    body["method_id"], body["method_version"] = identity["method_id"], identity["method_version"]
    body["goal_type_ref"] = request["goal_type_ref"]
    body["parameter_schema_ref"] = request["goal_signature"]["parameter_schema_ref"]
    body["output_schema_ref"] = request["goal_signature"]["output_schema_ref"]
    owed = [item["id"] for item in request["criterion_evidence"]]
    links = body["composition"]["criterion_links"]
    for index, link in enumerate(links):
        link["parent_criterion_id"] = owed[min(index, len(owed) - 1)]
    return body


def code_worker(request: Any) -> Any:
    """每个叶子：先把声明的每个输出端口写成 ``out/<端口>.json``，再交结果并认领这些端口。"""

    package = package_of(request)
    declared = [item["port"] for item in (package.get("declared_output_ports") or {}).get("ports", ())]
    written = sum(1 for message in request.messages if "tool" in str(message.role).lower())
    if written < len(declared):
        port = declared[written]
        return ("workspace_write_file", {"path": f"out/{port}.json", "content": json.dumps({port: "scripted"}) + "\n"})
    return envelope(package, {port: f"out/{port}.json" for port in declared})


def envelope(package: dict[str, Any], outputs: dict[str, str], *, artifacts: list[str] | None = None) -> str:
    contract = package.get("task_contract", {})
    files = list(artifacts if artifacts is not None else outputs.values())
    body = {
        "task_id": contract.get("task_id", ""),
        "attempt_id": package.get("attempt", {}).get("attempt_id", ""),
        "outcome": "candidate",
        "summary": "脚本化叶子完成",
        "claims": [{"content": f"{path} 已写出", "confidence": 0.8, "evidence": [path]} for path in files],
        "evidence": files,
        "artifacts": files,
        "outputs": dict(outputs),
        "proposed_tasks": [],
        "used_knowledge": [],
        "risks": [],
        "cost": {"tool_calls": len(files)},
    }
    return "<result_envelope>" + json.dumps(body, ensure_ascii=False) + "</result_envelope>"


def code_provider(shape: Callable[[], dict[str, Any]], *, worker: Callable[[Any], Any] = code_worker) -> LayeredScriptedProvider:
    return LayeredScriptedProvider(planner=proposing_planner(shape), worker=worker)


__all__ = ("CODE_NAMES", "ROOT_TYPE", "CONFIG", "TOOLS", "c1_method", "code_provider", "code_root_parameters",
           "code_world", "code_world_factory", "code_worker", "envelope", "proposing_planner")
