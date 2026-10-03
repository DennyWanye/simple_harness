# SPDX-License-Identifier: Apache-2.0
"""规划族用例的真实主循环世界：产品同形部署 + 脚本化模型回复（HTN 补齐阶段 A′，2026-10-03 换芯）。

此前这里自己拼一个保证通道主循环：旧执行池、没绑执行图、``install_hierarchical(planning=env)``
的内存规划世界（``plan.goal`` / ``plan.leaf``）、手写根 ``task-root``、库内做法、手批检查策略、
``decision_loop.auto_grant`` 在建意图的同一事务里签授权。现在全部换成产品那一份：

* 任务经 :func:`agent_orchestrator.testing.product_world.product_world` 建出——部署组装、
  原生执行池、保证通道、建任务即绑定执行图；规划世界是通用"用户目标"世界（根 ``user-goal``，
  子目标 ``sub-goal-1`` / ``sub-goal-2``，步骤 ``prepare-delivery`` / ``continue-delivery``）；
* 完成映射确认、规划授权、检查策略投影都由部署职责在**两轮之间**做（自动模式，与产品同一条
  路）；带 ``action:`` 要求的任务由 :func:`confirm_completion` 替人在确认页确认；
* 产品没有库内做法：做法一律由规划器经 :func:`propose` 提出、过独立审阅，再经 :func:`adopt`
  采用。

替身只有模型回复：:func:`script` 按次序给一个角色的回复；审阅员（保证通道审阅请求不带角色
标记，归 ``"unknown"``）的回复由 :func:`review` 按审查包里的准则现写。
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    REVIEWER,
    LayeredScriptedProvider,
    decision,
    one_step_method,
    review_input,
    worker_reply,
)

#: the first user requirement (the product names success criteria ``c-user-<n>``)
CRITERION = "c-user-1"
CONFIG = {"max_concurrency": 3, "test_timeout_seconds": 60}
Reply = Callable[[Any], Any]


# ---------------------------------------------------------------- scripted replies


def script(*steps: Any) -> Reply:
    """One role's replies in order; a step is a reply text or a function of the request.
    Past the last step the role has nothing to say (the provider fails that call loudly)."""

    queue = list(steps)

    def reply(request: Any) -> Any:
        if not queue:
            return None
        step = queue.pop(0)
        return step(request) if callable(step) else step

    return reply


def provider(*, planner: Iterable[Any] = (), reviewer: Iterable[Any] | None = None,
             worker: Reply = worker_reply) -> LayeredScriptedProvider:
    """A layered scripted provider whose planner (and, if given, reviewer) answer in order;
    the reviewer defaults to :func:`review` ("ACCEPT") for every call."""

    return LayeredScriptedProvider(planner=script(*planner),
                                   reviewer=script(*reviewer) if reviewer is not None else review(),
                                   worker=worker)


def open_goal(package: Mapping[str, Any]) -> dict[str, Any]:
    return next(row for row in package["views"]["goals"] if row["open"])


def context_for(package: Mapping[str, Any], goal_type: str | None = None) -> dict[str, Any]:
    """The method-writing material for the open goal (or the first goal of ``goal_type``)."""

    contexts = package.get("method_proposal_contexts") or []
    if goal_type is not None:
        return next(row for row in contexts if row["request"]["goal_type_ref"]["id"] == goal_type)
    goal = open_goal(package)
    return next(row for row in contexts if row["subject_key"] == goal["subject_key"])


def type_ref(request: Mapping[str, Any], type_id: str) -> dict[str, Any]:
    for row in [*request["operators"], *request.get("subgoal_types", ())]:
        if row["task_type_ref"]["id"] == type_id:
            return dict(row["task_type_ref"])
    raise AssertionError(f"{type_id} is not offered to this goal")


def method_body(context: Mapping[str, Any], *, steps: Sequence[tuple[Any, ...]],
                links: Sequence[tuple[str, str]], finalizer: str | None,
                ordering: Sequence[tuple[str, str]] = (), method_id: str | None = None,
                version: int | None = None) -> dict[str, Any]:
    """A method proposal body.  ``steps`` are ``(local_id, task_type, arguments)``; a
    compound step's type is a sub-goal type.  ``links`` are ``(criterion, step)`` (the
    child answers for the criterion under the same id)."""

    request = context["request"]
    identity = request["new_method_identity"]
    rows = []
    for local_id, type_id, arguments in steps:
        ref = type_ref(request, type_id)
        operator = next((row for row in request["operators"] if row["task_type_ref"]["id"] == type_id), None)
        rows.append({"local_id": local_id, "task_type_ref": ref,
                     "form": "primitive" if operator is not None else "compound",
                     "arguments": dict(arguments),
                     "required_capabilities": list(operator["required_capabilities"]) if operator else [],
                     "obligation_relation": "refines_parent"})
    return {
        "schema_version": 1, "method_id": method_id or identity["method_id"],
        "method_version": identity["method_version"] if version is None else version,
        "goal_type_ref": request["goal_type_ref"],
        "parameter_schema_ref": request["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [], "steps": rows,
        "ordering": [{"before": before, "after": after} for before, after in ordering],
        "required_capabilities": [], "expected_effects": [],
        "composition": {
            "criterion_links": [{"parent_criterion_id": criterion, "child_step": step, "child_criterion_id": criterion,
                                 "evidence_requirement": f"{step} 这一步负责 {criterion}"}
                                for criterion, step in links],
            "outputs": {}, "finalizer_step": finalizer, "independent_review_required": True,
        },
        "basis_refs": [],
    }


def propose(builder: Callable[[dict[str, Any]], dict[str, Any]] = one_step_method, *,
            goal_type: str | None = None, seen: list[dict[str, Any]] | None = None) -> Reply:
    """A scripted Planner: PROPOSE_METHOD ``builder(context)`` for the open goal."""

    def reply(request: Any) -> str:
        package = package_of(request)
        if seen is not None:
            seen.append(package)
        context = context_for(package, goal_type)
        return decision(context["subject_key"], "PROPOSE_METHOD",
                        {"method_proposal": {"method": builder(context), "rationale": "没有适用的做法，提一个。"}},
                        "现有做法都不适用，提出一个新做法。")

    return reply


def adopt(version: int | None = None, *, seen: list[dict[str, Any]] | None = None) -> Reply:
    """A scripted Planner: REFINE the open goal with the proposed method of ``version`` (by
    default the newest one shown), whatever its review said."""

    def reply(request: Any) -> str:
        package = package_of(request)
        if seen is not None:
            seen.append(package)
        goal = open_goal(package)
        rows = [row for row in package["views"]["methods"]
                if row["goal_signature_id"] == goal["signature_id"]
                and (version is None or row["method_ref"]["semantic_revision"] == version)]
        assert rows, f"no proposed method v{version} is shown for {goal['signature_id']}"
        chosen = max(rows, key=lambda row: row["method_ref"]["semantic_revision"])
        return decision(goal["subject_key"], "REFINE",
                        {"method_ref": dict(chosen["method_ref"]), "bindings": dict(goal["params"])},
                        "采用这个做法。")

    return reply


def adopt_unknown(request: Any) -> str:
    """A scripted Planner: REFINE with a method it was never shown."""

    goal = open_goal(package_of(request))
    return decision(goal["subject_key"], "REFINE",
                    {"method_ref": {"kind": "method", "id": "plan.never-proposed", "semantic_revision": 1,
                                    "content_hash": "0" * 64},
                     "bindings": dict(goal["params"])}, "采用一个没见过的做法。")


def no_change(request: Any) -> str:
    goal = open_goal(package_of(request))
    return decision(goal["subject_key"], "NO_CHANGE", {"reason": "nothing to change"}, "计划不需要改动。")


def review(verdict: str = "ACCEPT", *, limitation: str = "") -> Reply:
    """A scripted independent reviewer: one verdict for every criterion of the package."""

    grade = {"ACCEPT": "PASS", "INCONCLUSIVE": "UNKNOWN"}.get(verdict, "FAIL")

    def reply(request: Any) -> str | None:
        package = review_input(request)
        if package is None:
            return None
        return json.dumps({"schema_version": 4, "verdict": verdict, "assessments": [
            {"criterion_id": criterion, "verdict": grade, "evidence_ids": [],
             "reason": limitation or "fixture method review", "limitations": [limitation] if limitation else []}
            for criterion in package["criterion_ids"]], "findings": []})

    return reply


# ---------------------------------------------------------------- the world


@asynccontextmanager
async def assured_loop(root: Path, scripted: Any, *, key: str = "assured-loop",
                       success_criteria: Sequence[str] = ("file:report.md",),
                       goal: str = "写一份报告 report.md", publishing: bool = False, **config: Any):
    """A Mission created on the product deployment with ``scripted`` model replies.  The
    loop has not run yet; drive it with :func:`run_until`.  ``publishing`` deploys the real
    file-publish connector the way the product enables it (for ``action:`` requirements)."""

    if publishing:
        from agent_orchestrator.governance.policies import DeploymentPolicy
        from agent_orchestrator.runtime.connectors_publish import FilePublishConnector

        published = Path(root) / "published"
        published.mkdir(parents=True, exist_ok=True)
        config = {"connectors": {"file_publish": FilePublishConnector(
                      published, Path(root) / "root" / "connectors" / "file_publish")},
                  "deployment_policy": DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2"),
                  **config}
    async with product_world(Path(root) / "root", scripted, **{**CONFIG, **config}) as product:
        created = product.create({"goal": goal, "idempotency_key": key, "success_criteria": list(success_criteria)})
        mission = product.store.get_mission(created["mission_id"])
        yield SimpleNamespace(loop=product.loop, store=product.store, commit=product.loop.commit, mission=mission,
                              provider=scripted, htn=HtnStore(product.store), product=product,
                              control=product.control)


def confirm_completion(world: Any) -> dict[str, Any]:
    """What the person does on the confirmation page of a Mission with an ``action:``
    requirement (auto mode never signs those): content requirements as content, the action
    as a required effect on the root duty, settled when the content hash matches."""

    workspace = world.control.snapshot(world.mission.id)["snapshot"]["operation_workspace"]
    assert workspace["state"] == "CONFIRMATION_REQUIRED" and workspace["editable"] is True, workspace
    actions = [c["id"] for c in workspace["criteria"] if c["statement"].startswith("action:")]
    content = [c["id"] for c in workspace["criteria"] if c["required"] and c["id"] not in actions]
    [obligation] = workspace["obligations"]
    milestone = next(m for m in workspace["milestones"] if m["id"] == "CONTENT_HASH_VERIFIED")
    ref = workspace["requirements_ref"]
    return world.control.approve_operation_completion_spec({
        "mission_id": world.mission.id, "command_id": "confirm-completion",
        "expected_requirements_ref": ref,
        "proposal": {
            "schema_version": 1, "mission_id": world.mission.id,
            "requirements_ref": {"id": ref["id"], "revision": ref["revision"], "content_hash": ref["content_hash"]},
            "mode": "REQUIRED_EFFECTS", "content_criterion_ids": content,
            "effects": [{"effect_key": "publish", "source_slot_key": "publish", "obligation_id": obligation["id"],
                         "criterion_ids": actions, "required_milestone": milestone["id"],
                         "milestone_policy_ref": milestone["milestone_policy_ref"],
                         "evidence_policy_ref": milestone["evidence_policy_ref"]}],
        },
    })


async def step(world: Any) -> None:
    """One round the way ``run()`` takes it: the deployment's duties, then one cycle."""

    await world.product.deployment.between_cycles(auto=world.product.auto)
    await world.loop._cycle()
    await asyncio.sleep(0.01)


async def run_until(world: Any, done: Callable[[Any], Any], *, cycles: int = 600) -> bool:
    """Drive the loop one round at a time until ``done(world)``; False when it never was."""

    for _ in range(cycles):
        if done(world):
            return True
        await step(world)
    return bool(done(world))


async def spin(world: Any, cycles: int = 25) -> None:
    for _ in range(cycles):
        await step(world)


def event_types(world: Any) -> list[str]:
    return [event.type for event in world.store.list_events(world.mission.id)]


def events_of(world: Any, kind: str) -> list[Any]:
    return [event for event in world.store.list_events(world.mission.id) if event.type == kind]


def plan_revision(world: Any) -> int:
    active = world.htn.active_plan_revision(world.mission.id)
    return 0 if active is None else int(active.revision)


def adopted_methods(world: Any) -> list[str]:
    """``<method id>@<version>`` of every adopted method instance (method ids are the
    product's fresh ``proposed-…`` ids, so only versions are compared by the cases)."""

    network = world.loop._new_mode(world.mission).network(world.mission.id)
    return sorted(str(draft.method_ref.method_id) + "@" + str(int(draft.method_ref.version))
                  for draft in network.method_instances if draft.instance_id in network.adopted_instance_ids)


__all__ = ("CONFIG", "CRITERION", "REVIEWER", "adopt", "adopt_unknown", "adopted_methods", "assured_loop",
           "confirm_completion", "context_for", "event_types", "events_of", "method_body", "no_change", "open_goal",
           "plan_revision", "propose", "provider", "review", "run_until", "script", "spin", "step", "type_ref")
