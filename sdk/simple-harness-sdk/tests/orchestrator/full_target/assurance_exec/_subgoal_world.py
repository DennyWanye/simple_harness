# SPDX-License-Identifier: Apache-2.0
"""三层目标的规划用例：产品同形世界里的做法与读数（HTN 精简 片 B；HTN 补齐阶段 A′ 换芯）。

规划世界就是产品同形的通用"用户目标"世界（:func:`agent_orchestrator.testing.product_world.user_goal_world`）：
根 ``user-goal`` 声明用户的全部要求；子目标类型 ``sub-goal-1`` / ``sub-goal-2`` 不声明要求（子目标
按上级做法交给它的那几条要求、以原编号受审）；步骤 ``prepare-delivery`` / ``continue-delivery``
声明全部内容要求，输出端口都是 ``delivery``，``continue-delivery`` 另有输入端口 ``delivery``。

此前这里自建内存规划世界（``sg.goal`` …）并把做法登记成库内做法；产品没有库内做法，下面的
做法一律由规划器为各目标提出（:func:`by_goal_type`），过独立审阅后再采用。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from _assured_loop import method_body, plan_revision  # noqa: F401  (plan_revision re-exported)

from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.scripted_replies import decision

USER = ("c-user-1", "c-user-2", "c-user-3")
STATEMENTS = ("写出 notes/a.md，列三条要点", "写出 notes/b.md，给出两个例子",
              "写出 README.md，概述前两份并指向它们")
ROOT, PART, DEEPER, WRITE, NEXT = "user-goal", "sub-goal-1", "sub-goal-2", "prepare-delivery", "continue-delivery"
Builder = Callable[[dict[str, Any]], dict[str, Any]]


def _goal(value: str) -> dict[str, Any]:
    return {"goal": {"op": "constant", "value": value}}


def output_of(step: str) -> dict[str, Any]:
    return {"delivery": {"op": "output", "step": step, "port": "delivery"}}


def outer(part_share: tuple[str, ...] = ("c-user-1", "c-user-2"),
          tail_share: tuple[str, ...] = ("c-user-3",)) -> Builder:
    """root → a sub-goal carrying ``part_share``, then a closing step (``tail_share``)."""

    def build(context: dict[str, Any]) -> dict[str, Any]:
        return method_body(context, steps=[("part", PART, _goal("前两份笔记")), ("tail", WRITE, {})],
                           ordering=[("part", "tail")], finalizer="tail",
                           links=[(item, "part") for item in part_share] + [(item, "tail") for item in tail_share])

    return build


INNER_LINKS = (("c-user-1", "a"), ("c-user-2", "b"))


def inner(links: tuple[tuple[str, str], ...] = INNER_LINKS) -> Builder:
    """the sub-goal → two steps, one requirement each (by default)."""

    def build(context: dict[str, Any]) -> dict[str, Any]:
        return method_body(context, steps=[("a", WRITE, {}), ("b", WRITE, {})], ordering=[("a", "b")],
                           links=list(links), finalizer="b")

    return build


def inner_with_deeper() -> Builder:
    """the sub-goal → a deeper sub-goal (first requirement) and one step (second)."""

    def build(context: dict[str, Any]) -> dict[str, Any]:
        return method_body(context, steps=[("deeper", DEEPER, _goal("第一份笔记")), ("b", WRITE, {})],
                           ordering=[("deeper", "b")], links=[("c-user-1", "deeper"), ("c-user-2", "b")],
                           finalizer="b")

    return build


def deepest() -> Builder:
    def build(context: dict[str, Any]) -> dict[str, Any]:
        return method_body(context, steps=[("only", WRITE, {})], links=[("c-user-1", "only")], finalizer="only")

    return build


def by_goal_type(builders: Mapping[str, Builder | list[Builder]], *, seen: dict[str, list[dict]] | None = None,
                 asked: list[str] | None = None) -> Callable[[Any], str | None]:
    """A scripted Planner for a whole tree: for the open goal, adopt the newest method that
    passed its review, else propose the next builder given for the goal's type.  ``seen``
    collects the package shown on each proposal, per goal type; ``asked`` the goal types."""

    queues = {key: list(value) if isinstance(value, list) else [value] for key, value in builders.items()}

    def reply(request: Any) -> str | None:
        package = package_of(request)
        goals = [row for row in package["views"]["goals"] if row["open"]]
        if not goals:
            return None
        goal = goals[0]
        kind = str(goal["signature_id"])
        if asked is not None:
            asked.append(kind)
        passed = [row for row in package["views"]["methods"] if row["goal_signature_id"] == kind
                  and (row.get("review") or {}).get("outcome") == "PASSED"]
        if passed:
            chosen = max(passed, key=lambda row: row["method_ref"]["semantic_revision"])
            return decision(goal["subject_key"], "REFINE",
                            {"method_ref": dict(chosen["method_ref"]), "bindings": dict(goal["params"])},
                            "采用通过审阅的做法。")
        queue = queues.get(kind) or []
        if not queue:
            return None
        builder = queue.pop(0) if len(queue) > 1 else queue[0]
        context = next(row for row in package["method_proposal_contexts"] if row["subject_key"] == goal["subject_key"])
        if seen is not None:
            seen.setdefault(kind, []).append(package)
        return decision(goal["subject_key"], "PROPOSE_METHOD",
                        {"method_proposal": {"method": builder(context), "rationale": "为这个目标提一个做法。"}},
                        "这个目标还没有做法，提一个。")

    return reply


def open_goal_requests(world: Any) -> list[Any]:
    return [event for event in world.store.list_events(world.mission.id)
            if event.type == "PlanningRepairRequested"
            and (event.payload.get("request") or {}).get("trigger_source") == "GOAL_UNREFINED"]


def scopes(world: Any) -> dict[str, list[str]]:
    """``<goal type>/<step>`` → the requirements that occurrence answers for."""

    from agent_orchestrator.contracts.operation_completion import PlanRevisionPinV1
    from agent_orchestrator.orchestrator.operation_completion import OperationCompletionReader

    active = world.htn.active_plan_revision(world.mission.id)
    pin = PlanRevisionPinV1(revision=active.revision, snapshot_hash=active.snapshot_hash)
    network = world.loop._new_mode(world.mission).network(world.mission.id)
    reader = OperationCompletionReader(world.store)
    step_of = {str(child.occurrence_id): str(child.slot_key)
               for instance in network.method_instances for child in instance.child_bindings}
    rows: dict[str, list[str]] = {}
    for spec in network.occurrences:
        signature = str(network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id)
        scope = reader.read_scope(world.mission.id, pin, str(spec.occurrence_id))
        rows[f"{signature}/{step_of.get(str(spec.occurrence_id), 'root')}"] = sorted(scope.content_criterion_ids)
    return rows
