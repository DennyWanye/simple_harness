# SPDX-License-Identifier: Apache-2.0
"""产品同形的测试世界（HTN 补齐阶段 A′ 第 2 步）。

SDK 测试里任务走的路，与产品（桌面 Host）一样：分层规划 + 执行图（建任务时绑定）+ 保证通道 +
原生执行池，部署组装就是产品那一份 :class:`~agent_orchestrator.deployment.assembly.UserMissionDeployment`。
只有三样是测试替身：

* 模型回复：调用方给的脚本化提供方；
* 用量计数器：一词一个 token 的认证测试计数器（:mod:`.word_counter`，与 Host 测试同一份）；
* 规划世界：:func:`user_goal_world`，与产品的桌面世界同形的通用"用户目标"世界（产品领域知识
  留在 Host；这一份只服务 SDK 测试）。

用户在界面上做的两件事——确认内容完成映射、授权规划——在自动模式下由部署职责代签，与产品同
一条路；``auto=False`` 时它们等着测试自己用门面命令去做。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..deployment.assembly import RootNames, UserMissionDeployment

#: The SDK test world's root names (product deployments use their own).
USER_GOAL_NAMES = RootNames("user-goal", "user-root-", "user-duty-")
DEFAULT_TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list")
TENANT = "tenant-product-world"


def user_goal_world(loop: Any, mission: Any) -> Any:
    """A planning world shaped like the product's: a root goal, two levels of sub-goals and a
    workspace step (plus a continuation step that consumes an upstream delivery)."""
    from ..contracts.htn import GoalSignature, PortSpec, SideEffectKind, TaskForm
    from ..contracts.semantic_base import VersionedRef, content_hash_of
    from ..planning.htn.registry import ObjectSchema, SchemaField, TaskTypeSpec
    from ..planning.htn.world import build_planning_world, capability_records
    from ..storage.htn_store import HtnStore

    from ..planning.htn.observers.workspace import workspace_observers, workspace_predicates

    world = build_planning_world(mission.id, domains=(), semantics=HtnStore(loop.store),
                                 predicates=workspace_predicates(),
                                 observers=workspace_observers(loop.store, mission.id))
    from ..deployment.root import current_criteria

    requirements = current_criteria(loop.store, mission)  # 现行要求（最新要求修订），不读章程
    criteria = tuple(name for name, _ in requirements)
    params_body = {"fields": [{"name": "goal", "type": "string", "required": True}]}
    params = VersionedRef("user.goal-parameters", 1, content_hash_of(params_body))
    outputs = VersionedRef("user.workspace-outputs", 1, content_hash_of({"fields": []}))
    world.schemas.register(ObjectSchema(params, (SchemaField("goal", "string"),)))
    world.schemas.register(ObjectSchema(outputs))
    content = tuple(name for name, statement in requirements if not statement.startswith("action:"))
    signature = GoalSignature("user-goal", 1, params, outputs, mission.goal, content or criteria)
    # 步骤类型不带本任务的要求（阶段 E）：一步负责哪些要求只来自上级做法的链接，所以改要求之后
    # 类型不变，旧步骤还能被新做法沿用。
    step = GoalSignature("prepare-delivery", 1, params, outputs, mission.goal, ())
    continuation = GoalSignature("continue-delivery", 1, params, outputs,
                                 "Continue from an accepted upstream delivery: " + mission.goal, ())
    ports = (PortSpec("delivery", outputs),)
    levels = {"user-goal": 0, "sub-goal-1": 1, "sub-goal-2": 2}
    for name, form in (("user-goal", TaskForm.COMPOUND), ("sub-goal-1", TaskForm.COMPOUND),
                       ("sub-goal-2", TaskForm.COMPOUND), ("prepare-delivery", TaskForm.PRIMITIVE),
                       ("continue-delivery", TaskForm.PRIMITIVE)):
        goal_signature = step if form is TaskForm.PRIMITIVE else signature
        level = levels.get(name)
        if level:
            goal_signature = GoalSignature(
                name, 1, params, outputs,
                "A part of the user's goal; its goal parameter says what this part has to achieve.", ())
        input_ports: tuple[Any, ...] = ()
        if name == "continue-delivery":
            goal_signature = continuation
            input_ports = (PortSpec("delivery", outputs),)
        body: dict[str, Any] = {"name": name, "form": str(form), "signature": goal_signature.to_json(),
                                "ports": [p.to_json() for p in ports]}
        if input_ports:
            body["input_ports"] = [p.to_json() for p in input_ports]
        if level:
            body["refinement_level"] = level
        # 内容步骤只写本次尝试自己的隔离工作区，对外发布由系统步骤负责：效果身份固定，
        # 所以同一个内容步骤可以被几个分支共用（阶段 D，补全方案 2.4）。类型仍是"本地写"。
        effect_identity = "desktop.attempt-workspace" if form is TaskForm.PRIMITIVE else None
        if effect_identity:
            body["effect_identity"] = effect_identity
        world.catalog.register(TaskTypeSpec(
            task_type_ref=VersionedRef(name, 1, content_hash_of(body)), form=form, goal_signature=goal_signature,
            input_ports=input_ports, output_ports=ports, parameter_schema_ref=params, output_schema_ref=outputs,
            operator_ref=(VersionedRef("user.workspace-worker", 1, content_hash_of({"tools": list(mission.allowed_tools)}))
                          if form is TaskForm.PRIMITIVE else None),
            required_capabilities=("workspace.prepare",) if form is TaskForm.PRIMITIVE else (),
            side_effect_kind=SideEffectKind.LOCAL_WRITE if form is TaskForm.PRIMITIVE else SideEffectKind.NONE,
            reversible=True, domain="user", refinement_level=level, effect_identity=effect_identity,
        ))
    offered = set(mission.allowed_tools)
    world.records = capability_records(world.catalog, capability_layers={"workspace.prepare": None},
        unauthorized=() if set(DEFAULT_TOOLS) <= offered else ("workspace.prepare",))
    return world


@dataclass
class ProductWorld:
    loop: Any
    control: Any
    deployment: UserMissionDeployment
    native: Any
    provider: Any
    auto: bool = True
    notices: list[dict[str, Any]] = field(default_factory=list)

    @property
    def store(self) -> Any:
        return self.loop.store

    def create(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Create a user Mission the product way (root and TaskGraph binding in one transaction)."""
        body = {"budget": {"max_tokens": 8_000_000, "max_attempts": 12},
                **dict(request)}
        return self.deployment.create_mission(self.loop, self.control, body)

    async def drain(self, *, timeout: float = 30.0) -> bool:
        """Run the loop until idle (the deployment duties also run between its cycles), then
        the duties once more — what the product's ``drain`` does."""
        try:
            await asyncio.wait_for(self.loop.run(), timeout=timeout)
        except TimeoutError:
            return False
        await self.deployment.between_cycles(auto=self.auto)
        return True

    async def run_until_settled(self, mission_id: str, *, rounds: int = 12, timeout: float = 30.0) -> Any:
        mission = self.store.get_mission(mission_id)
        for _ in range(rounds):
            await self.drain(timeout=timeout)
            mission = self.store.get_mission(mission_id)
            if str(getattr(mission.status, "value", mission.status)) in {"COMPLETED", "FAILED", "STOPPED", "CANCELLED"}:
                break
        return mission


@asynccontextmanager
async def product_world(
    root: Path, provider: Any, *, auto: bool = True, tenant_id: str = TENANT, principal: Any = None,
    world_factory: Callable[[Any, Any], Any] = user_goal_world, names: RootNames = USER_GOAL_NAMES,
    allowed_tools: tuple[str, ...] = DEFAULT_TOOLS, connectors: Mapping[str, Any] | None = None,
    root_parameters: Callable[[Any], dict[str, Any]] | None = None,
    model: str = "agent-model",
    **config: Any,
):
    """A started Orchestrator on the product's deployment, with scripted model replies.

    ``connectors`` 与 ``deployment_policy=``（落在 ``**config`` 里交给 ``OrchestratorConfig``）
    照产品的接法透传：要发布文件的测试给一个真实的 ``FilePublishConnector`` 和启用它的部署策略。
    """
    from ..api.facade import MissionControlV1
    from ..deployment.native_pools import NativePools, pool_options
    from ..governance.permissions import Principal
    from ..orchestrator.event_handler import Orchestrator
    from ..runtime.assembly import OrchestratorConfig
    from .word_counter import FixtureWordCounter

    principal = principal or Principal("product-world-user")
    counter = FixtureWordCounter()
    cfg = OrchestratorConfig(evidence_root=Path(root), model=model, **config)
    native = NativePools(tenant_id=tenant_id, principal_id=principal.principal_id, allowed_tools=allowed_tools,
                         meter_factory=counter.meter_factory)
    options = pool_options(cfg, native=native, provider=provider, counter=counter, provider_kind="fixtures")
    world_holder: dict[str, Any] = {}
    deployment = UserMissionDeployment(
        tenant_id=tenant_id, principal=principal, world_factory=world_factory, names=names,
        host_fingerprint="ab" * 32, notify=lambda payload: world_holder["world"].notices.append(dict(payload)),
        **({} if root_parameters is None else {"root_parameters": root_parameters}))

    def assemble(orchestrator: Any) -> None:
        deployment.assemble(orchestrator)
        native.bind_orchestrator(orchestrator)

    if connectors is not None:
        options = {**options, "connectors": dict(connectors)}
    async with Orchestrator(cfg, provider, owner="product-world", startup_assembly=assemble,
                            assurance_root_setup=deployment.assurance_root_setup(), **options) as loop:
        control = MissionControlV1(loop, tenant_id=tenant_id, principal=principal)
        deployment.bind(loop, control)
        world = ProductWorld(loop=loop, control=control, deployment=deployment, native=native,
                             provider=provider, auto=auto)
        world_holder["world"] = world
        loop.set_between_cycles(lambda: deployment.between_cycles(auto=world.auto), every_seconds=0.0)
        try:
            yield world
        finally:
            deployment.bind(None, None)


__all__ = ("DEFAULT_TOOLS", "USER_GOAL_NAMES", "ProductWorld", "product_world", "user_goal_world")
