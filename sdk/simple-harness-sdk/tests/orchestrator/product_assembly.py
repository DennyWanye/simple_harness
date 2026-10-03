# SPDX-License-Identifier: Apache-2.0
"""产品同形部署的"未启动"编排服务（HTN 补齐阶段 A′）。

:func:`agent_orchestrator.testing.product_world.product_world` 直接给出一个已启动的世界。少数测试要的是
启动之前的那个对象（测启动失败、关闭顺序），或者部署多配一组思考池（产品的"思考模式"设置）。这里照
``product_world`` 的拼装——同一份 :class:`UserMissionDeployment`、同一份 ``pool_options`` 原生执行池、
同一个认证测试计数器——只多出这两个口子。本应放在 ``agent_orchestrator.testing``，本轮不改 src，放在测试里。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_orchestrator.api.facade import MissionControlV1
from agent_orchestrator.deployment.assembly import UserMissionDeployment
from agent_orchestrator.deployment.native_pools import NativePools, pool_options
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.testing.product_world import (
    DEFAULT_TOOLS,
    TENANT,
    USER_GOAL_NAMES,
    ProductWorld,
    user_goal_world,
)
from agent_orchestrator.testing.word_counter import FixtureWordCounter


@dataclass
class Unstarted:
    """An Orchestrator on the product's deployment, not entered yet."""

    orchestrator: Orchestrator
    deployment: UserMissionDeployment
    native: NativePools
    provider: Any

    def world(self, loop: Any) -> ProductWorld:
        control = MissionControlV1(loop, tenant_id=TENANT, principal=self.deployment.principal)
        self.deployment.bind(loop, control)
        return ProductWorld(loop=loop, control=control, deployment=self.deployment, native=self.native,
                            provider=self.provider)


def unstarted(root: Path, provider: Any, *, thinking_provider: Any = None, owner: str = "product-world",
              installed: bool = True, **config: Any) -> Unstarted:
    """``installed=False``：执行池照常拼好，但部署的安装（规划世界、执行图、保证通道、根）没有接上——
    模拟一个装配缺失的进程打开了已有的库（守卫"没装分层装配就不发规划"用）。"""
    principal = Principal("product-world-user")
    counter = FixtureWordCounter()
    cfg = OrchestratorConfig(evidence_root=Path(root), model="agent-model", **config)
    native = NativePools(tenant_id=TENANT, principal_id=principal.principal_id, allowed_tools=DEFAULT_TOOLS,
                         meter_factory=counter.meter_factory)
    options = pool_options(cfg, native=native, provider=provider, counter=counter, provider_kind="fixtures",
                           thinking_provider=thinking_provider,
                           thinking_counter=None if thinking_provider is None else FixtureWordCounter())
    deployment = UserMissionDeployment(tenant_id=TENANT, principal=principal, world_factory=user_goal_world,
                                       names=USER_GOAL_NAMES, host_fingerprint="ab" * 32)

    def assemble(orchestrator: Any) -> None:
        deployment.assemble(orchestrator)
        native.bind_orchestrator(orchestrator)

    wiring = ({"startup_assembly": assemble, "assurance_root_setup": deployment.assurance_root_setup()}
              if installed else {"startup_assembly": native.bind_orchestrator})
    orchestrator = Orchestrator(cfg, provider, owner=owner, **wiring, **options)
    return Unstarted(orchestrator, deployment, native, provider)


@asynccontextmanager
async def started(root: Path, provider: Any, **kwargs: Any):
    """:func:`unstarted`, entered: a :class:`ProductWorld` (the deployment's per-round duties are
    run by the caller, as with ``product_world``)."""

    parts = unstarted(root, provider, **kwargs)
    async with parts.orchestrator as loop:
        world = parts.world(loop)
        loop.set_between_cycles(lambda: parts.deployment.between_cycles(auto=world.auto), every_seconds=0.0)
        try:
            yield world
        finally:
            parts.deployment.bind(None, None)


__all__ = ("Unstarted", "started", "unstarted")
