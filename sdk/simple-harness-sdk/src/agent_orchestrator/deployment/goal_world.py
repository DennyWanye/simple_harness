# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""用户目标的规划世界：一个根目标、两层子目标、两种工作区步骤。

这是产品（桌面）一直在用的那份世界，名字按 ``domain`` 加前缀（桌面是 ``desktop``）。类型与任务无关：
用户原话与本任务的要求只在根绑定上，一步负责哪些要求只来自上级做法的链接；同一部署上任意两个任务
的类型目录逐字节相同。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from ..contracts.htn import (
    GoalSignature,
    PortCardinality,
    PortOrdering,
    PortSpec,
    SideEffectKind,
    TaskForm,
)
from ..contracts.semantic_base import VersionedRef, content_hash_of
from ..planning.htn.observers.workspace import workspace_observers, workspace_predicates
from ..planning.htn.registry import ObjectSchema, SchemaField, TaskTypeSpec
from ..planning.htn.world import build_planning_world, capability_records
from ..storage import htn_store
from .assembly import RootNames

if TYPE_CHECKING:
    from ..knowledge.predicates import ArgumentType

WORKSPACE_TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list")


def root_names(domain: str) -> RootNames:
    """根目标类型与根任务／义务编号前缀（它们是每个已有任务身份的一部分）。"""

    return RootNames(f"{domain}.user-goal", f"{domain}-root-", f"{domain}-duty-")


def goal_world(domain: str) -> Callable[[Any, Any], Any]:
    """``UserMissionDeployment(world_factory=...)`` 要的工厂。"""

    def factory(loop: Any, mission: Any) -> Any:
        world = build_planning_world(
            mission.id,
            domains=(),
            semantics=htn_store.HtnStore(loop.store),
            predicates=workspace_predicates(),
            observers=workspace_observers(loop.store, mission.id),
        )
        params_body = {"fields": [{"name": "goal", "type": "string", "required": True}]}
        params = VersionedRef(f"{domain}.goal-parameters", 1, content_hash_of(params_body))
        outputs = VersionedRef(f"{domain}.workspace-outputs", 1, content_hash_of({"fields": []}))
        world.schemas.register(
            ObjectSchema(params, (SchemaField("goal", cast("ArgumentType", "string")),))
        )
        world.schemas.register(ObjectSchema(outputs))
        root = f"{domain}.user-goal"
        signature = GoalSignature(
            root,
            1,
            params,
            outputs,
            "The user's goal; the root binding carries the user's own words and requirements.",
            (),
        )
        preparation = GoalSignature(
            f"{domain}.prepare-delivery",
            1,
            params,
            outputs,
            "One step of the user's goal; the plan's links say which requirements it answers for.",
            (),
        )
        continuation = GoalSignature(
            f"{domain}.continue-delivery",
            1,
            params,
            outputs,
            "One step of the user's goal that continues from one or more accepted upstream "
            "deliveries (its delivery input port takes several, laid into the workspace in the "
            "order the plan lists them); the plan's links say which requirements it answers for.",
            (),
        )
        ports = (PortSpec("delivery", outputs),)
        # 中间目标按层注册：根 0 层，第一层做法里还能放第二层子目标，第二层只能放普通步骤；
        # 层数上限就是这里注册了几层，由 SDK 的注册检查保证。
        levels = {root: 0, f"{domain}.sub-goal-1": 1, f"{domain}.sub-goal-2": 2}
        for name, form in (
            (root, TaskForm.COMPOUND),
            (f"{domain}.sub-goal-1", TaskForm.COMPOUND),
            (f"{domain}.sub-goal-2", TaskForm.COMPOUND),
            (f"{domain}.prepare-delivery", TaskForm.PRIMITIVE),
            (f"{domain}.continue-delivery", TaskForm.PRIMITIVE),
        ):
            goal_signature = preparation if form is TaskForm.PRIMITIVE else signature
            level = levels.get(name)
            if level:
                goal_signature = GoalSignature(
                    name,
                    1,
                    params,
                    outputs,
                    "A part of the user's goal; its goal parameter says what this part has to "
                    "achieve.",
                    (),
                )
            input_ports: tuple[Any, ...] = ()
            if name == f"{domain}.continue-delivery":
                goal_signature = continuation
                # 输入端口接多个上游（集合、按做法里数组的先后），并行分支才能在一步汇合。
                input_ports = (
                    PortSpec(
                        "delivery",
                        outputs,
                        cardinality=PortCardinality.SET,
                        ordering=PortOrdering.EXPLICIT,
                    ),
                )
            body: dict[str, Any] = {
                "name": name,
                "form": str(form),
                "signature": goal_signature.to_json(),
                "ports": [p.to_json() for p in ports],
            }
            if input_ports:
                body["input_ports"] = [p.to_json() for p in input_ports]
            if level:
                body["refinement_level"] = level
            # 内容步骤只写本次尝试自己的隔离工作区，对外发布由系统步骤负责：效果身份固定，
            # 所以同一个内容步骤可以被几个分支共用。类型仍是"本地写"。
            effect_identity = f"{domain}.attempt-workspace" if form is TaskForm.PRIMITIVE else None
            if effect_identity:
                body["effect_identity"] = effect_identity
            world.catalog.register(
                TaskTypeSpec(
                    task_type_ref=VersionedRef(name, 1, content_hash_of(body)),
                    form=form,
                    goal_signature=goal_signature,
                    input_ports=input_ports,
                    output_ports=ports,
                    parameter_schema_ref=params,
                    output_schema_ref=outputs,
                    # 执行者引用是固定体：一步实际能用哪些工具由任务∩步骤∩角色∩部署的交集决定
                    operator_ref=(
                        VersionedRef(
                            f"{domain}.workspace-worker",
                            1,
                            content_hash_of({"capability": "workspace.prepare"}),
                        )
                        if form is TaskForm.PRIMITIVE
                        else None
                    ),
                    required_capabilities=("workspace.prepare",)
                    if form is TaskForm.PRIMITIVE
                    else (),
                    side_effect_kind=SideEffectKind.LOCAL_WRITE
                    if form is TaskForm.PRIMITIVE
                    else SideEffectKind.NONE,
                    reversible=True,
                    domain=domain,
                    refinement_level=level,
                    effect_identity=effect_identity,
                )
            )
        offered = set(mission.allowed_tools)
        world.records = capability_records(
            world.catalog,
            capability_layers={"workspace.prepare": None},
            unauthorized=() if set(WORKSPACE_TOOLS) <= offered else ("workspace.prepare",),
        )
        return world

    return factory


__all__ = ("WORKSPACE_TOOLS", "goal_world", "root_names")
