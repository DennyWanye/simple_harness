# SPDX-License-Identifier: Apache-2.0
"""用户任务的部署：安装与建任务（2026-10-03，HTN 补齐阶段 A′）。

产品（桌面 Host）与 SDK 的产品同形测试世界都经由这一份：

* **安装**（主循环的 ``startup_assembly``）：按任务的规划世界与开工条件、执行图、保证通道
  （每个分层任务都走保证通道，要求书只有 ``user_requirements`` 一份，检查策略由
  :class:`DeploymentDuties` 投影）；保证通道根由 ``assurance_root_setup`` 以部署身份安装。
* **建任务**：经认证门面建任务；提交层在建任务的同一事务里调部署装上的"建任务收尾"，初始化根、
  绑定执行图——任何建任务入口都走这一处，没有"已要求、还没绑定"的等待期（用户 2026-10-03 定）；
  规划授权照样卡住每一次计划提交。
* **每轮职责**：:class:`DeploymentDuties`（自动确认、自动授权、检查策略投影）。

部署只给：认证身份、规划世界（产品领域知识）与根的名字、通知去处、构建指纹、执行图预算与部署
验收；以及"现在是不是自动模式"。命令号是已有回执的身份，原样沿用。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..graph.task_network import DEFAULT_PROJECTION_BUDGET
from .duties import DeploymentDuties
from .root import goal_parameters, initialize_root, install_planning, user_requirements

#: The native Assurance root installation command of a deployment (receipt identity).
ROOT_COMMAND_ID = "host-native-root"


@dataclass(frozen=True, slots=True)
class RootNames:
    """The root goal type and the task/duty id prefixes of a user Mission's root."""

    root_type: str
    task_prefix: str
    duty_prefix: str


@dataclass(slots=True)
class UserMissionDeployment:
    tenant_id: str
    principal: Any
    world_factory: Callable[[Any, Any], Any]
    names: RootNames
    host_fingerprint: str
    notify: Callable[[Mapping[str, Any]], None] = lambda _payload: None
    wake: Callable[[], None] = lambda: None
    graph_budget: Any = DEFAULT_PROJECTION_BUDGET
    deployment_acceptance: Any = None
    taskgraph_ports: Any = None  # a deployment's own TaskGraph ports (tests inject doubles)
    #: The root goal's parameters for a Mission (the product: the user's goal, verbatim).
    root_parameters: Callable[[Any], dict[str, Any]] = goal_parameters
    duties: DeploymentDuties = field(init=False)
    taskgraph: Any = field(init=False, default=None)
    assurance: Any = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.duties = DeploymentDuties(None, None, tenant_id=self.tenant_id, principal=self.principal,
                                       wake=self.wake)

    # ---- install ------------------------------------------------------------------------------

    def assurance_root_setup(self) -> Callable[[Any], None]:
        """``Orchestrator(assurance_root_setup=...)``: the authenticated native installation.

        Idempotent per root directory and caller; a different principal or tenant is refused
        by the SDK as an identity conflict, never silently adopted."""

        def install(orchestrator: Any) -> None:
            orchestrator.commit.install_assurance_root(
                principal=self.principal, tenant_id=self.tenant_id, command_id=ROOT_COMMAND_ID)

        return install

    def assemble(self, orchestrator: Any) -> None:
        """``Orchestrator(startup_assembly=...)``: planning, TaskGraph and Assurance."""
        from ..orchestrator.assurance_assembly import AssuranceDeploymentPorts, install_assurance
        from ..orchestrator.taskgraph_assembly import TaskGraphDeploymentPorts
        from ..orchestrator.taskgraph_deployment import InstalledHtnWiringAcceptance

        install_planning(orchestrator, self.world_factory)
        ports = self.taskgraph_ports or TaskGraphDeploymentPorts(
            tenant_id=self.tenant_id, principal=self.principal, graph_budget=self.graph_budget,
            deployment_acceptance=self.deployment_acceptance or InstalledHtnWiringAcceptance())
        if ports.tenant_id != self.tenant_id or ports.principal != self.principal:
            raise RuntimeError("TaskGraph deployment must bind the authenticated principal and tenant")
        self.taskgraph = orchestrator.install_taskgraph(ports)
        self.assurance = install_assurance(orchestrator, AssuranceDeploymentPorts(
            tenant_id=self.tenant_id, principal=self.principal,
            requirements=lambda mission, spec: user_requirements(mission, self.principal),
            notify_transport=self.notify, host_fingerprint=self.host_fingerprint,
            # Projects each frozen Scope's check policy right before its first review.
            check_policy_projector=lambda mission_id: self.duties.project_check_policies(mission_id),
        ))
        orchestrator.commit._mission_completer = lambda mission: self._complete(orchestrator, mission)

    def bind(self, orchestrator: Any, control: Any) -> None:
        self.duties.bind(orchestrator, control)
        if orchestrator is None:
            self.taskgraph = self.assurance = None

    # ---- create -------------------------------------------------------------------------------

    def create_mission(self, orchestrator: Any, control: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        """Create a user Mission through the authenticated facade; the commit layer
        completes it (root and TaskGraph binding) in the creation transaction."""
        receipt = control.create(body)
        self.wake()
        return receipt

    def create_mission_with_sources(self, orchestrator: Any, control: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        """The atomic batch: the Mission, its sources, its root and its binding together."""
        receipt = control.create_with_sources(body)
        self.wake()
        return receipt

    def _complete(self, orchestrator: Any, mission: Any) -> None:
        from ..orchestrator.taskgraph_policy import enable_command_id

        initialize_root(orchestrator, mission, self.principal, world_factory=self.world_factory,
                        root_type=self.names.root_type, task_prefix=self.names.task_prefix,
                        duty_prefix=self.names.duty_prefix, root_parameters=self.root_parameters)
        if self.taskgraph is None:
            raise RuntimeError("TASKGRAPH_DEPLOYMENT_UNINSTALLED")
        self.taskgraph.policy.enable_taskgraph_contract(mission.id, enable_command_id(mission.id))

    # ---- per round ----------------------------------------------------------------------------

    async def between_cycles(self, *, auto: bool) -> int:
        """The deployment's per-round work, in the product's order."""
        done = self.duties.project_check_policies()
        done += self.duties.auto_authorize_planning(auto=auto)
        done += self.duties.auto_confirm_content_completion(auto=auto)
        return done


__all__ = ("ROOT_COMMAND_ID", "RootNames", "UserMissionDeployment")
