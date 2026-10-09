# SPDX-License-Identifier: BUSL-1.1
"""Desktop-owned HTN declarations: the desktop planning world (product domain knowledge).

The root initialisation, the one requirements document and the start gate are the SDK's
(``agent_orchestrator.deployment.root``); the Host hands in this world and the root names.
No model, observer result or approval is fabricated during assembly.
"""
from __future__ import annotations

from typing import Any


def planning_world(loop: Any, mission: Any) -> Any:
    from agent_orchestrator.contracts.htn import (
        GoalSignature, PortCardinality, PortOrdering, PortSpec, SideEffectKind, TaskForm,
    )
    from agent_orchestrator.contracts.semantic_base import VersionedRef, content_hash_of
    from agent_orchestrator.planning.htn.registry import ObjectSchema, SchemaField, TaskTypeSpec
    from agent_orchestrator.planning.htn.world import build_planning_world, capability_records
    from agent_orchestrator.storage.htn_store import HtnStore

    htn = HtnStore(loop.store)
    # 桌面的三样只读观察（文件在不在、文件内容哈希、资料是否当前版本）：实现在 SDK 一份，这里注册
    from agent_orchestrator.planning.htn.observers.workspace import workspace_observers, workspace_predicates

    world = build_planning_world(mission.id, domains=(), semantics=htn, predicates=workspace_predicates(),
                                 observers=workspace_observers(loop.store, mission.id))
    params_body = {"fields": [{"name": "goal", "type": "string", "required": True}]}
    params = VersionedRef("desktop.goal-parameters", 1, content_hash_of(params_body))
    outputs = VersionedRef("desktop.workspace-outputs", 1, content_hash_of({"fields": []}))
    world.schemas.register(ObjectSchema(params, (SchemaField("goal", "string"),)))
    world.schemas.register(ObjectSchema(outputs))
    # 类型与任务无关（阶段 C3）：用户原话与本任务的要求只在根绑定上（SDK deployment.root.root_binding，
    # 规划器要覆盖的只有内容要求，发布等操作由系统在任务层面准备）；一步负责哪些要求只来自上级
    # 做法的链接。同一部署上任意两个任务的类型目录逐字节相同，全库做法才能按目标类型跨任务列出。
    signature = GoalSignature("desktop.user-goal", 1, params, outputs, "The user's goal; the root binding carries the user's own words and requirements.", ())
    preparation = GoalSignature("desktop.prepare-delivery", 1, params, outputs, "One step of the user's goal; the plan's links say which requirements it answers for.", ())
    continuation = GoalSignature("desktop.continue-delivery", 1, params, outputs,
        "One step of the user's goal that continues from one or more accepted upstream deliveries (its delivery input port takes several, laid into the workspace in the order the plan lists them); the plan's links say which requirements it answers for.", ())
    ports = (PortSpec("delivery", outputs),)
    # 内容步骤不再有申请单端口：申请单由系统按已批准效果生成（2026-09-29）。
    preparation_ports = ports
    # These are actual workspace operations available to this deployment. External
    # effects still require an OperationIntent, review and the original connector.
    # 2026-10-01（HTN 精简 片 B）：中间目标按层注册。类型不声明判据——子目标负责哪几条要求，是
    # 上级做法用链接交给它的（原编号、用户原话），审阅也按那几条审。层级决定能往下放什么：
    # 根目标 0 层，第一层子目标的做法里还能放第二层子目标，第二层只能放普通步骤；层数上限
    # 就是这里注册了几层，由 SDK 的注册检查保证，Host 不另写计数。子目标声明一个 delivery 输出
    # 端口：一步执行时只铺通过输入端口接进来的上游产出，后面的步骤要用子目标里做出来的文件，
    # 就接这个端口——SDK 把它对到子目标收尾步骤的 delivery（真机第 5 局：不声明端口时最后一步
    # 看不到子目标下写出的文件）。
    levels = {"desktop.user-goal": 0, "desktop.sub-goal-1": 1, "desktop.sub-goal-2": 2}
    for name, form in (("desktop.user-goal", TaskForm.COMPOUND),
                       ("desktop.sub-goal-1", TaskForm.COMPOUND),
                       ("desktop.sub-goal-2", TaskForm.COMPOUND),
                       ("desktop.prepare-delivery", TaskForm.PRIMITIVE),
                       ("desktop.continue-delivery", TaskForm.PRIMITIVE)):
        goal_signature = preparation if form is TaskForm.PRIMITIVE else signature
        level = levels.get(name)
        if level:
            goal_signature = GoalSignature(
                name, 1, params, outputs,
                "A part of the user's goal; its goal parameter says what this part has to achieve.", ())
        # Add a separately identified consumer type. Previously admitted methods
        # keep the exact prepare-delivery v1 declaration and need no new input.
        input_ports = ()
        if name == "desktop.continue-delivery":
            goal_signature = continuation
            # 2026-10-09 四项修复第 4 条：输入端口接多个上游（集合、按做法里数组的先后），并行分支
            # 才能在一步汇合；此前单值端口让一步只能接一个上游，规划器只能排成一条链。
            input_ports = (PortSpec("delivery", outputs, cardinality=PortCardinality.SET,
                                    ordering=PortOrdering.EXPLICIT),)
        declared_ports = preparation_ports if form is TaskForm.PRIMITIVE else ports
        body = {"name": name, "form": str(form), "signature": goal_signature.to_json(),
                "ports": [p.to_json() for p in declared_ports]}
        if input_ports:
            body["input_ports"] = [p.to_json() for p in input_ports]
        if level:
            body["refinement_level"] = level
        # 内容步骤只写本次尝试自己的隔离工作区，对外发布由系统步骤负责：效果身份固定，
        # 所以同一个内容步骤可以被几个分支共用（阶段 D，补全方案 2.4）。类型仍是"本地写"。
        effect_identity = "desktop.attempt-workspace" if form is TaskForm.PRIMITIVE else None
        if effect_identity:
            body["effect_identity"] = effect_identity
        ref = VersionedRef(name, 1, content_hash_of(body))
        world.catalog.register(TaskTypeSpec(
            task_type_ref=ref, form=form, goal_signature=goal_signature,
            input_ports=input_ports, output_ports=declared_ports,
            parameter_schema_ref=params, output_schema_ref=outputs,
            # 执行者引用是固定体：一步实际能用哪些工具由"任务 ∩ 步骤 ∩ 角色 ∩ 部署"的交集决定
            operator_ref=(VersionedRef("desktop.workspace-worker", 1,
                content_hash_of({"capability": "workspace.prepare"}))
                if form is TaskForm.PRIMITIVE else None),
            required_capabilities=("workspace.prepare",) if form is TaskForm.PRIMITIVE else (),
            side_effect_kind=SideEffectKind.LOCAL_WRITE if form is TaskForm.PRIMITIVE else SideEffectKind.NONE,
            reversible=True, domain="desktop", refinement_level=level, effect_identity=effect_identity,
        ))
    offered = set(mission.allowed_tools)
    world.records = capability_records(world.catalog, capability_layers={"workspace.prepare": None},
        unauthorized=() if {"workspace_read_file", "workspace_write_file", "workspace_list"} <= offered
        else ("workspace.prepare",))
    return world


#: The root goal type and the task/duty id prefixes of a user Mission's root.  They are
#: part of every existing Mission's identity; the SDK's ``deployment.root`` builds the
#: root from them (one copy of the requirements and the root initialisation).
ROOT_TYPE = "desktop.user-goal"
ROOT_TASK_PREFIX = "desktop-root-"
ROOT_DUTY_PREFIX = "desktop-duty-"
