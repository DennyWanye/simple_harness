# SPDX-License-Identifier: Apache-2.0
"""根终审用例的产品同形种子（HTN 补齐阶段 A′，``test_root_review_coordinator`` /
``test_root_review_evidence`` 共用）。

任务经产品那一份部署组装建出（:func:`~agent_orchestrator.testing.product_world.product_world`），
两条要求、规划器提一个两步并行的做法：``facts`` 写 facts.md、承接 ``c-user-1``；``notes`` 写
NOTES.md、承接 ``c-user-2``。只有模型回复是脚本，替身只模拟外界真会发生的事：

* 终审（``MISSION_FINAL``）审阅员的结论可以是通过或打回；
* 终审调用可以被扣住（提供方很慢，用来在它挂着的窗口里让外界动一下），也可以以服务商
  协议错误结束（``ProviderProtocolError``，审阅员一个字都没说上，算"被打断"）；
* 叶子内容审阅可以按文件打回；
* 规划器被问到修复时，默认把那次调用扣住不答（用例只看系统把什么交到了它手里），也可以
  交给用例给的函数答。
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from agent_orchestrator.testing.fixtures import package_of, role_of
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    planner_reply,
    review_input,
    review_reply,
)

CRITERIA = ("file:facts.md", "file:NOTES.md")
GOAL = "整理事实并写笔记"
FINAL = "MISSION_FINAL"


def two_step_method(context: dict[str, Any]) -> dict[str, Any]:
    """两个互不依赖的"准备交付"步骤，各管一条要求（``facts`` ← 第一条，``notes`` ← 第二条）。"""

    request = context["request"]
    operator = next(item for item in request["operators"]
                    if str(item["task_type_ref"]["id"]).endswith("prepare-delivery"))
    first, second = [item["id"] for item in request["criterion_evidence"]]
    identity = request["new_method_identity"]

    def step(local_id: str) -> dict[str, Any]:
        return {"local_id": local_id, "task_type_ref": operator["task_type_ref"], "form": "primitive",
                "arguments": {}, "required_capabilities": list(operator["required_capabilities"]),
                "obligation_relation": "refines_parent"}

    return {
        "schema_version": 1, "method_id": identity["method_id"], "method_version": identity["method_version"],
        "goal_type_ref": request["goal_type_ref"],
        "parameter_schema_ref": request["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [],
        "steps": [step("facts"), step("notes")], "ordering": [],
        "required_capabilities": [], "expected_effects": [],
        "composition": {
            "criterion_links": [
                {"parent_criterion_id": first, "child_step": "facts", "child_criterion_id": first,
                 "evidence_requirement": "facts 这一步写出 facts.md"},
                {"parent_criterion_id": second, "child_step": "notes", "child_criterion_id": second,
                 "evidence_requirement": "notes 这一步写出 NOTES.md"},
            ],
            "outputs": {}, "finalizer_step": "notes", "independent_review_required": True,
        },
        "basis_refs": [],
    }


def open_repairs(package: dict[str, Any]) -> list[dict[str, Any]]:
    """修复请求（"目标未细化"不算：那是普通规划）。"""

    return [entry for entry in package.get("repair_requests") or ()
            if ((entry.get("request") or {}).get("context") or {}).get("event_type") != "GoalUnrefined"]


def purpose_of(request: Any) -> str | None:
    data = review_input(request)
    return None if data is None else str((data.get("package") or {}).get("purpose"))


class FinalReviewProvider(LayeredScriptedProvider):
    """脚本化的三个角色，外加终审与修复轮的几种外界情形（见模块文档）。"""

    def __init__(self, *, final_verdict: str = "ACCEPT", interrupt_final: int = 0,
                 hold_final_first: bool = False, reject_content: tuple[str, ...] = (),
                 repair: Callable[[Any], Any] | None = None) -> None:
        self.final_verdict = final_verdict
        self.interrupt_final = interrupt_final
        self.hold_final_first = hold_final_first
        self.reject_content = set(reject_content)
        self.repair = repair
        self.final_calls = 0
        self.final_held = asyncio.Event()
        self.final_release = asyncio.Event()
        self.repair_asked = asyncio.Event()
        self.repair_packages: list[dict[str, Any]] = []
        self.worker_packages: dict[str, dict[str, Any]] = {}
        self._forever = asyncio.Event()

        def planner(request: Any) -> Any:
            package = package_of(request)
            if open_repairs(package):
                return None if self.repair is None else self.repair(request)
            contexts = package.get("method_proposal_contexts") or []
            if contexts and not (package.get("method_selection") or [{}])[0].get("applicable"):
                return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                                {"method_proposal": {"method": two_step_method(contexts[0]),
                                                     "rationale": "两份文件分开写。"}}, "两步并行。")
            return planner_reply(request)

        def reviewer(request: Any) -> Any:
            data = review_input(request)
            if data is None:
                return None
            package = data.get("package") or {}
            purpose = str(package.get("purpose"))
            if purpose == FINAL and self.final_verdict != "ACCEPT":
                return review_reply(data, verdict=self.final_verdict, grade="FAIL",
                                    reason="脚本化终审：各步都过了，合起来仍不满足用户目标。")
            if purpose == "TASK_CONTENT":
                text = json.dumps(package, ensure_ascii=False)
                for path in sorted(self.reject_content):
                    if path in text:
                        self.reject_content.discard(path)
                        return review_reply(data, verdict="REJECTED", grade="FAIL",
                                            reason=f"脚本化审阅：{path} 不满足要求。")
            return review_reply(data)

        super().__init__(planner=planner, reviewer=reviewer)

    def close(self) -> None:
        """放开所有扣住的调用（用例结束时）。"""
        self.final_release.set()
        self._forever.set()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        from simple_harness.providers.errors import ProviderProtocolError

        role = role_of(request)
        if role == "worker":
            package = package_of(request)
            outputs = tuple(package.get("task_contract", {}).get("outputs") or ())
            if outputs:
                self.worker_packages.setdefault(outputs[0], package)
        elif role == "planner" and self.repair is None and open_repairs(package_of(request)):
            self.repair_packages.append(package_of(request))
            self.repair_asked.set()
            await self._forever.wait()
        elif purpose_of(request) == FINAL:
            self.final_calls += 1
            if self.final_calls <= self.interrupt_final:
                raise ProviderProtocolError(public_message="脚本化：服务商回了一段解析不了的应答。")
            if self.hold_final_first and self.final_calls == 1:
                self.final_held.set()
                await self.final_release.wait()
        return await super().invoke(request, cancel=cancel)


def events(store: Any, mission_id: str, *types: str) -> list[Any]:
    return [event for event in store.list_events(mission_id) if event.type in types]


async def run_for(product: Any, seconds: float) -> None:
    """Let the main loop (with the deployment's duties) run for a while with nothing to wait for."""

    from h1i_seed import run_until

    try:
        await run_until(product, lambda: False, timeout=seconds)
    except TimeoutError:
        pass


__all__ = ("CRITERIA", "FINAL", "GOAL", "FinalReviewProvider", "events", "open_repairs", "purpose_of",
           "run_for", "two_step_method")
