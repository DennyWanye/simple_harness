# SPDX-License-Identifier: Apache-2.0
"""产品同形通道的脚本化模型回复（Host 与 SDK 测试共用这一份，2026-10-03 起）。

* 规划器：库里没有适用做法时提出一个"一步写出所要文件"的做法；做法通过独立审阅后采用它。
* 审阅员（与执行者同一个模型的独立会话，请求里没有角色标记）：对审查包里的每条准则判通过，
  引用包里已经展示的证据。
* 执行者：把这一步声明的每个文件写出来，再交一份结果，认领声明的输出端口。

每个角色都可以换成自己的函数（``planner=`` / ``reviewer=`` / ``worker=``），用来写"某一步答错了会
怎样"的测试；函数收到请求，返回一段回复文字或一个 ``(工具名, 参数)``。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from .fixtures import RoleScriptedProvider, package_of, role_of

#: 保证通道的审阅请求不带 ``[role:…]`` 标记，脚本化提供者把它归在这个名字下。
REVIEWER = "unknown"
Reply = str | tuple[str, dict[str, Any]]


def decision(subject_key: str, kind: str, payload: dict[str, Any], rationale: str) -> str:
    """规划器的一条决定，按 planning-decision-v1 的外形。"""

    body = {
        "schema_version": 1,
        "decision_type": kind,
        "subject_key": subject_key,
        "rationale": rationale,
        "reason_refs": [],
        "assumptions": [],
        "uncertainties": [],
        "alternatives": [],
        "replan_triggers": [],
        "payload": payload,
    }
    return "<planning_decision>" + json.dumps(body, ensure_ascii=False) + "</planning_decision>"


def one_step_method(context: dict[str, Any]) -> dict[str, Any]:
    """一个只有一步的做法：用"准备交付"这个原子步骤承接目标的全部要求。"""

    request = context["request"]
    operator = next(
        item for item in request["operators"]
        if str(item["task_type_ref"]["id"]).endswith("prepare-delivery")
    )
    identity = request["new_method_identity"]
    return {
        "schema_version": 1,
        "method_id": identity["method_id"],
        "method_version": identity["method_version"],
        "goal_type_ref": request["goal_type_ref"],
        "parameter_schema_ref": request["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request["goal_signature"]["output_schema_ref"],
        "applicable_when": [],
        "exploration_assumptions": [],
        "steps": [
            {
                "local_id": "write",
                "task_type_ref": operator["task_type_ref"],
                "form": "primitive",
                "arguments": {},
                "required_capabilities": list(operator["required_capabilities"]),
                "obligation_relation": "refines_parent",
            }
        ],
        "ordering": [],
        "required_capabilities": [],
        "expected_effects": [],
        "composition": {
            "criterion_links": [
                {
                    "parent_criterion_id": criterion,
                    "child_step": "write",
                    "child_criterion_id": criterion,
                    "evidence_requirement": f"write 这一步在任务工作区完成 {criterion} 要求的产出",
                }
                # 目标负责的每条要求（中间目标的 required_criteria 是空的，要求在 criterion_evidence 里）
                for criterion in [item["id"] for item in request["criterion_evidence"]]
            ],
            "outputs": {},
            "finalizer_step": "write",
            "independent_review_required": True,
        },
        "basis_refs": [],
    }


def planner_reply(request: Any) -> Reply | None:
    """有适用的做法就采用；没有就提一个一步的做法。别的局面（修复请求等）不替测试回答。"""

    package = package_of(request)
    # 子目标还没有做法时，系统以"目标未细化"的修复请求来问；这与普通规划一样，提做法或采用做法。
    repairs = [entry for entry in package.get("repair_requests") or ()
               if ((entry.get("request") or {}).get("context") or {}).get("event_type") != "GoalUnrefined"]
    if repairs:
        return None
    selection = (package.get("method_selection") or [{}])[0]
    if selection.get("applicable"):
        chosen = selection["applicable"][0]
        goal = next(item for item in package["views"]["goals"] if item["open"])
        return decision(
            goal["subject_key"],
            "REFINE",
            {
                "method_ref": {
                    "kind": "method",
                    "id": chosen["method_id"],
                    "semantic_revision": chosen["method_version"],
                    "content_hash": chosen["method_content_hash"],
                },
                "bindings": dict(selection.get("bindings") or goal["params"]),
            },
            "采用已通过独立审阅的做法。",
        )
    contexts = package.get("method_proposal_contexts") or []
    if not contexts:
        return None
    return decision(
        contexts[0]["subject_key"],
        "PROPOSE_METHOD",
        {"method_proposal": {"method": one_step_method(contexts[0]), "rationale": "一步写出要求的文件。"}},
        "库里没有适用的做法，提出一个一步完成的做法。",
    )


def retry_same_method(request: Any) -> Reply | None:
    """有一步失败、系统来问怎么修时：答"同一做法再试一次"。没有这样的修复请求返回 None。"""

    package = package_of(request)
    for entry in package.get("repair_requests") or ():
        refs = [str(ref) for ref in (entry.get("request") or {}).get("trigger_refs", ()) if ":attempt-" in str(ref)]
        if len(refs) != 1:
            continue
        task_id = refs[0].rpartition(":attempt-")[0]
        subject = next((item for item in package.get("planning_subjects", ()) if item.get("task_id") == task_id), None)
        if subject is None:
            continue
        instances = {
            str(child.get("instance_id") or instance.get("instance_id"))
            for plan in package["views"].get("plans", ())
            for instance in plan.get("adopted_methods", ())
            for child in instance.get("child_bindings", ())
            if subject["occurrence_id"] in (child.get("occurrence_id"), child.get("goal_occurrence_id"))
        }
        refs_visible = [ref for ref in package.get("visible_refs", ())
                        if ref.get("kind") == "method_instance" and (not instances or ref.get("id") in instances)]
        if len(refs_visible) != 1:
            continue
        return decision(
            subject["subject_key"],
            "REPAIR",
            {"repair_kind": "RETRY_SAME_METHOD", "failed_attempt_id": refs[0], "method_instance_ref": refs_visible[0]},
            "审查意见可以在同一做法下改好，原样再做一次。",
        )
    return None


def review_input(request: Any) -> dict[str, Any] | None:
    """审阅员收到的审查包（第一条用户消息里的那个 JSON 对象）。"""

    for message in request.messages:
        content = message.content if isinstance(message.content, str) else str(message.content)
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "criterion_ids" in data:
            return data
    return None


def review_reply(
    package: dict[str, Any], *, verdict: str = "ACCEPT", grade: str = "PASS", reason: str = "脚本化审阅：材料满足这条要求。"
) -> str:
    labels = [item["label"] for item in package.get("evidence", ())][:64]
    return json.dumps(
        {
            "schema_version": 3,
            "verdict": verdict,
            "assessments": [
                {
                    "criterion_id": criterion,
                    "verdict": grade,
                    "evidence_ids": labels,
                    "reason": reason,
                    "limitations": [],
                }
                for criterion in package["criterion_ids"]
            ],
            "findings": [],
        },
        ensure_ascii=False,
    )


def reviewer_reply(request: Any) -> Reply | None:
    package = review_input(request)
    return None if package is None else review_reply(package)


def worker_reply(request: Any) -> Reply | None:
    """先把这一步声明的文件逐个写出来，再交结果、认领必需的输出端口。"""

    package = package_of(request)
    contract = package.get("task_contract", {})
    outputs = list(contract.get("outputs") or [])
    written = sum(1 for message in request.messages if "tool" in str(message.role).lower())
    if written < len(outputs):
        return ("workspace_write_file", {"path": outputs[written], "content": "# 要点\n\n- 一\n- 二\n- 三\n"})
    declared = package.get("declared_output_ports") or {}
    ports = [item["port"] for item in declared.get("ports", ()) if item.get("required", True)]
    envelope = {
        "task_id": contract.get("task_id", ""),
        "attempt_id": package.get("attempt", {}).get("attempt_id", ""),
        "outcome": "candidate",
        "summary": "写好了要求的文件",
        "claims": [{"content": f"{path} 已写出", "confidence": 0.8, "evidence": [path]} for path in outputs],
        "evidence": outputs,
        "artifacts": outputs,
        "outputs": {port: outputs[0] for port in ports[:1]} if outputs else {},
        "proposed_tasks": [],
        "used_knowledge": [],
        "risks": [],
        "cost": {"tool_calls": len(outputs)},
    }
    return "<result_envelope>" + json.dumps(envelope, ensure_ascii=False) + "</result_envelope>"


def broken_result(request: Any) -> Reply | None:
    """一份格式不对的结果：认领了一个这一步没有声明的输出端口（文件照常写出）。"""

    reply = worker_reply(request)
    if isinstance(reply, tuple):
        return reply
    body = json.loads(reply[len("<result_envelope>"):-len("</result_envelope>")])
    body["outputs"] = {"no-such-port": (body["artifacts"] or ["x"])[0]}
    return "<result_envelope>" + json.dumps(body, ensure_ascii=False) + "</result_envelope>"


class LayeredScriptedProvider(RoleScriptedProvider):
    """按角色现算回复的脚本化提供者；``asked`` 记下每次被问到的角色，按顺序。"""

    def __init__(
        self,
        *,
        planner: Callable[[Any], Reply | None] = planner_reply,
        reviewer: Callable[[Any], Reply | None] = reviewer_reply,
        worker: Callable[[Any], Reply | None] = worker_reply,
    ) -> None:
        super().__init__({})
        self._answer = {"planner": planner, REVIEWER: reviewer, "worker": worker}
        self.asked: list[str] = []
        #: 被扣住的角色：对它的调用停在半路不返回，直到 ``release`` 被置位（模拟一次很慢
        #: 的模型调用，用来在调用进行中重启服务或取消任务）。
        self.held: set[str] = set()
        self.release = asyncio.Event()
        self.entered = asyncio.Event()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        role = role_of(request)
        self.asked.append(role)
        if role in self.held:
            self.entered.set()
            await self.release.wait()
        answer = self._answer.get(role)
        reply = None if answer is None else answer(request)
        if reply is None:
            raise AssertionError(f"分层脚本化通道没有为角色 {role!r} 的这个请求准备回复")
        self.scripts[role] = [reply]
        return await super().invoke(request, cancel=cancel)
