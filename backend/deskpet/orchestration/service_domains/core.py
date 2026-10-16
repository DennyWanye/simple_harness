# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Construction, start, open, quarantine and root diagnostics."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_orchestrator.api import facade, policies
from agent_orchestrator.contracts import Budget
from agent_orchestrator.deployment.assembly import RootNames, UserMissionDeployment
from agent_orchestrator.deployment.native_pools import NATIVE_OUTPUT_TOKENS
from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.orchestrator import event_handler
from agent_orchestrator.orchestrator.taskgraph_deployment import InstalledHtnWiringAcceptance
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.runtime.planning_operations import SourceUnavailable

from ..assurance import host_fingerprint
from ..hierarchical import ROOT_DUTY_PREFIX, ROOT_TASK_PREFIX, ROOT_TYPE, planning_world
from ..lock import InstanceLock
from ..manifest import MANIFEST_SCHEMA, build_manifest, distributions, write_manifest
from ..notices import MissionNotices
from ..provider import NO_MODEL, ProviderSnapshot, ProviderUnavailable
from ..runtime_profile import ONLY_DEEPSEEK_REASON, source_runtime_options
from ..settings import OrchestrationSettings
from ..storage_usage import StorageUsage
from .support import QUARANTINE_REASON, WORKSPACE_TOOLS, OrchestrationRequestError

# 与原模块同名的记录器（getLogger 按名字返回同一个对象）；就地定义，ruff 才认得出
# 捕获异常后写日志的写法
logger = logging.getLogger("deskpet.orchestration.service")

TENANT = "local-desktop"
KNOWLEDGE_TOOLS = ("knowledge_list", "knowledge_read")


class OrchestrationServiceCore:
    """Construction, start, open, quarantine and root diagnostics."""

    if TYPE_CHECKING:

        def _bind_host_duties(self, orchestrator: Any) -> None: ...
        def _build_native(self) -> Any: ...
        def _call(self, method: str, *args: Any, **kwargs: Any) -> Any: ...
        async def _close_runtime(self) -> None: ...
        def _context_default(self) -> str | None: ...
        def _context_profiles(self) -> list[dict[str, Any]]: ...
        def _degraded_recovery(self) -> bool: ...
        def _detect_diagnostics(self) -> bool: ...
        async def _drive(self) -> None: ...
        async def _host_duties(self) -> None: ...
        def _mission_token_default(self, profile_id: str | None = ...) -> int: ...
        def _native_skill_tools(self) -> tuple[str, ...]: ...
        async def _probe_sandbox(self) -> dict[str, Any]: ...
        def _publish_connector(self) -> Any: ...
        async def _rebuild(self) -> None: ...
        def _recovery_isolated(self) -> dict[str, dict[str, Any]]: ...
        def _refuse_new_work(self, message: str) -> None: ...
        def _refuse_secrets(self, *values: Any) -> None: ...
        def _require(self) -> Any: ...
        def _secret_values(self) -> tuple[str, ...]: ...
        def _thinking_provider(self) -> Any: ...
        def create_mission(self, request: Mapping[str, Any]) -> dict[str, Any]: ...
        def status(self) -> dict[str, Any]: ...
        def wake(self) -> None: ...

    def __init__(
        self,
        root: str | Path,
        settings: OrchestrationSettings,
        *,
        principal: Any,
        provider: Any = None,
        provider_snapshot: ProviderSnapshot | None = None,
        http_client: Any = None,
        drive: bool = True,
        taskgraph_deployment: Any = None,
        native_test_counter: Any = None,
        permission_mode_reader: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self.root = Path(root)
        self.settings = settings
        self.tenant_id = TENANT
        self.owner = f"deskpet-orchestrator-{os.getpid()}-{secrets.token_hex(4)}"  # P0-2
        self._principal = principal
        self._provider = provider
        self._snapshot = provider_snapshot
        self._http_client = http_client
        self._drive_enabled = drive
        # Trusted deployment composition only: never populated from IPC/model
        # input. None selects the package-owned production deployment reader.
        self._taskgraph_deployment = taskgraph_deployment
        self._taskgraph: Any = None
        # ARP-EXEC-1.1.1 (RP-E3): the Host's native runtime plane composition, built per
        # Orchestrator lifetime; ``native_test_counter`` is trusted test composition only
        # (a certified fixture counter standing in for the DeepSeek one).
        self._native: Any = None
        self._native_test_counter = native_test_counter
        self._state = "created"
        self._reason: str | None = None
        self._lock = InstanceLock(self.root)
        self._orchestrator: Any = None
        self._effective_provider: Any = None
        self._config: Any = None
        self._runtime_options: dict[str, Any] = {}
        self._connectors: dict[str, Any] = {}
        self._deployment: Any = None
        # P3.2: what the sandbox capability probe found here, and the executor it proved
        self._sandbox: dict[str, Any] | None = None
        self._executor: Any = None
        # P3.2 P32-14: whether a publish directory is authorised, and if not, why
        self._publish: dict[str, Any] = {"enabled": False, "reason": "尚未探测"}
        self._control: Any = None
        self._diagnostics_available = False
        self._policy: Any = None
        self._manifest: dict[str, Any] | None = None
        self._driver: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._quiet_round = False
        self._last_watermark: Any = None
        self._closing = False
        self._failures = 0
        # 2026-09-25 条目 7: disk taken by task/session data, measured off the loop; the
        # Settings page reads it and warns past ``storage_warn_bytes`` — nothing is deleted.
        self._storage = StorageUsage(self.root, warn_bytes=settings.storage_warn_bytes)
        self._authorization_mode: str | None = None
        self.on_write: Callable[[], None] | None = None  # the change pump's poke
        # Assurance 1.1 (plan §13): the SDK deployment installed on the current
        # Orchestrator lifetime.
        self._assurance: Any = None
        # 第 2 批 A02 / A40：保证通道根隔离时 SDK 的非披露诊断（原计划 §10.1 / §10.3）；NATIVE 时为 None。
        # 隔离的编排只开管理模式：这里不绑职责、不开驱动、不当"可用"。
        self._quarantine: dict[str, Any] | None = None
        # H01：SDK 恢复协议的只读诊断（state / failed_step / 八步结果）；降级时服务进 degraded_recovery
        self._recovery: dict[str, Any] | None = None
        # HTN 补齐阶段 B 第 1 条：任务结束通知的唯一 Host 记录（主对话卡片、主 Agent 上下文都读它）

        self._notices = MissionNotices(self.root)
        # 2026-09-25 UI 全量点击：自动模式下每轮"授权本轮规划"都要人点，不点任务就一直卡着。
        # 读当前权限模式（每轮现读；读不到按手动处理，照旧等人点）。
        self._permission_mode_reader = permission_mode_reader
        # 用户任务的部署（安装、建任务、每轮职责）在 SDK 里只有一份（HTN 补齐阶段 A′）；Host 交出
        # 桌面规划世界、认证身份、通知去处与构建指纹，并告诉它"现在是不是自动模式"。

        def notify(payload: Mapping[str, Any]) -> None:
            # At-least-once local status transport: the notice is kept (de-duplicated by
            # event id) until the person acknowledges it in the main conversation; the
            # change pump pushes ``mission_changed`` so the cards re-read.
            self._notices.record(payload)
            self.wake()

        self._user_missions = UserMissionDeployment(
            tenant_id=self.tenant_id,
            principal=self._principal,
            world_factory=planning_world,
            names=RootNames(ROOT_TYPE, ROOT_TASK_PREFIX, ROOT_DUTY_PREFIX),
            host_fingerprint=host_fingerprint(),
            notify=notify,
            wake=lambda: self.wake(),
            taskgraph_ports=taskgraph_deployment,
        )
        self._duties = self._user_missions.duties

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        """Start once. After failure, deactivate/activate with a fresh service and client."""
        if self._state != "created":
            return
        if not self.settings.enabled:
            self._state, self._reason = "disabled", "编排服务已在配置中关闭"
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if not self._lock.acquire():
                self._state, self._reason = "unavailable", "另一个实例正在使用编排目录"
                return
            # review P2-2: the pin is checked before the library is opened, so a wrong SDK
            # version never migrates the orchestration database
            imported = distributions()
            if not imported["consistent"]:
                write_manifest(
                    self.root,
                    {
                        "schema": MANIFEST_SCHEMA,
                        "distributions": imported,
                        "refused": "pin_mismatch",
                    },
                )
                raise RuntimeError("实际导入的 SDK 与钉版不一致（见 deployment-manifest.json）")
            # 执行图原计划 §0.3 / §16（补齐第 1 批 V12）：部署验收门没通过就不开生产开关。安装包里的
            # 部署清单只有带门报告哈希的 VALIDATED 才算通过；NOT_RUN 不是通过，这里不替它放行。
            acceptance = self._taskgraph_acceptance_status()
            if acceptance != "VALIDATED":
                write_manifest(
                    self.root,
                    {
                        "schema": MANIFEST_SCHEMA,
                        "distributions": imported,
                        "refused": "taskgraph_acceptance_not_validated",
                        "taskgraph_acceptance": acceptance,
                    },
                )
                raise RuntimeError(
                    f"执行图部署验收门没有通过（清单记为 {acceptance}），不开生产开关；"
                    "先跑 scripts/acceptance/taskgraph_gate.py 并用 taskgraph_manifest.py validate 写入"
                )
            await self._open()
            self._manifest = build_manifest(
                root=self.root,
                deployment=self._deployment.to_json(),
                settings=asdict(self.settings),
                model=None if self._snapshot is None else self._snapshot.public(),
                sandbox=self._sandbox,
                publish=self._publish,
            )
            write_manifest(self.root, self._manifest)
            if self._quarantine is not None:
                # 根隔离：服务起来了，但不是"可用"——只开非披露诊断与隔离只读分支，不驱动循环
                self._state, self._reason = "quarantined", QUARANTINE_REASON
                return
            self._state, self._reason = "available", None
            if self._drive_enabled:
                self._driver = asyncio.get_running_loop().create_task(
                    self._drive(), name="orchestration-driver"
                )
        except ProviderUnavailable as error:
            await self._fail(str(error))
        except Exception as error:
            logger.exception("orchestration service failed to start")
            await self._fail(f"启动失败：{type(error).__name__}: {error}"[:300])

    @staticmethod
    def _taskgraph_acceptance_status() -> str:
        """``VALIDATED`` / ``NOT_RUN`` from the installed SDK's package-owned manifest; a manifest
        that does not verify reads as ``UNVERIFIED`` (refused the same way)."""

        try:
            return InstalledHtnWiringAcceptance().acceptance_status()
        except SourceUnavailable:
            return "UNVERIFIED"

    async def _fail(self, reason: str) -> None:
        self._state, self._reason = "unavailable", reason
        await self._close_runtime()
        self._lock.release()

    async def _open(self) -> None:

        provider = self._provider
        enabled_connectors: tuple[str, ...] = ()
        if provider is None:
            raise ProviderUnavailable(NO_MODEL)
        publish = self._publish_connector()  # P3.2 P32-14: only a directory the user authorised
        if publish is not None:
            self._connectors["file_publish"] = publish
            enabled_connectors = (*enabled_connectors, "file_publish")
        # P3.2 §4.3 / plan D9: model-written code runs only inside a sandbox that has
        # proven itself here, and never in a plain child process.  The probe decides;
        # a failure is not fatal — the deployment simply runs nothing model-written.
        self._sandbox = await self._probe_sandbox()
        sandboxed = bool(self._sandbox.get("ok"))
        # NEXT-TG-1.0 §11: native-plane pools serve the shared Skill catalogue's three
        # model-side tools; a Mission's Worker gets them through the original four-way
        # intersection (the SDK never freezes them into a request on a legacy pool).
        skill_tools = self._native_skill_tools()
        base_tools = (
            (*WORKSPACE_TOOLS, *KNOWLEDGE_TOOLS, "run_tests")
            if sandboxed
            else (*WORKSPACE_TOOLS, *KNOWLEDGE_TOOLS)
        )
        self._deployment = DeploymentPolicy(
            allowed_tools=(*base_tools, *skill_tools),
            skill_tools=skill_tools,
            code_execution="sandboxed" if sandboxed else "off",
            enabled_connectors=enabled_connectors,
            max_action_level="L2",  # one person: L3's two distinct people cannot be met
        )
        knobs: dict[str, Any] = {}
        if self._snapshot is not None:  # a real model: the bounds the SDK real runs use
            # Reasoning tokens share the output limit. 8K truncated a real
            # operation verdict before its closing envelope reached the Host.
            # 2026-10-10 用户决定：封顶 32768 → 131072（线路实测一次输出 103,000 正常结束；10-09 两局
            # 执行者 9 次把 32768 全部用来思考）。起步值不动，写满才翻倍。
            knobs = {
                "default_max_output_tokens": 16384,
                "max_output_tokens_ceiling": NATIVE_OUTPUT_TOKENS,
            }
        self._config = OrchestratorConfig(
            evidence_root=self.root,
            model=self._snapshot.requested_model if self._snapshot is not None else "agent-model",
            max_concurrency=self.settings.max_concurrency,
            max_concurrent_model_calls=self.settings.max_concurrent_model_calls,
            lease_seconds=self.settings.lease_seconds,
            deployment_policy=self._deployment,
            sandbox_executor=self._executor,  # P3.2 D2: required when sandboxed
            task_max_tokens=self.settings.task_max_tokens,  # fixed per-leaf allowance
            # 第 2 批 H11 / 车道 P：§18.2 Global → Mission → Task；全局预算是月配额，
            # 当月总账在当月第一个任务建立时打开
            global_budget=Budget(max_tokens=self.settings.global_monthly_max_tokens),
            **knobs,
        )
        self._effective_provider = provider  # kept for a rebuild after repeated failures
        self._native = self._build_native()
        self._runtime_options = source_runtime_options(
            self._config,
            provider,
            self._snapshot,
            settings=self.settings,
            native=self._native,
            native_test_counter=self._native_test_counter,
            thinking_provider=self._thinking_provider(),
        )
        if not self._runtime_options["profiles"]:
            # 2026-09-30 用户决定：旧式执行池删除，编排只在原生池上跑；没有认证计数器
            # （非 DeepSeek 模型）就明说不支持，不再退回旧池。
            raise ProviderUnavailable(ONLY_DEEPSEEK_REASON)

        def assemble_startup(orchestrator: Any) -> None:
            self._user_missions.assemble(orchestrator)
            if self._native is not None:
                self._native.bind_orchestrator(orchestrator)

        self._orchestrator = event_handler.Orchestrator(
            self._config,
            provider,
            owner=self.owner,
            connectors=self._connectors,
            startup_assembly=assemble_startup,
            assurance_root_setup=self._user_missions.assurance_root_setup(),
            **self._runtime_options,
        )
        await self._orchestrator.__aenter__()
        control = facade.MissionControlV1(
            self._orchestrator, tenant_id=self.tenant_id, principal=self._principal
        )
        self._quarantine = self._root_quarantine(control)
        if self._quarantine is not None:
            # 只留门面给非披露诊断用：不绑职责、不回填通知、不开策略读
            self._control = control
            return
        self._bind_host_duties(self._orchestrator)
        self._notices.catch_up(self._orchestrator.store)  # U04: from the last read seq
        self._taskgraph = self._user_missions.taskgraph
        self._assurance = self._user_missions.assurance
        self._control = control
        self._user_missions.bind(self._orchestrator, self._control)
        self._diagnostics_available = self._detect_diagnostics()
        self._policy = policies.PolicyApi(self._orchestrator.commit, self._principal)

    @staticmethod
    def _root_quarantine(control: Any) -> dict[str, Any] | None:
        """SDK 的非披露诊断不是 NATIVE 就是隔离（状态文件缺失 / 标记不符 / 安装身份冲突；原计划 §10.1）。"""
        report = dict(control.assurance_root_diagnostic())
        return None if report.get("state") == "NATIVE" else report

    @property
    def quarantined(self) -> bool:
        return self._quarantine is not None

    def assurance_root_diagnostic(self) -> dict[str, Any]:
        """非披露诊断（原计划 §10.3，第 2 批 A40）：状态、能否执行、是否要当前认证、匿名阻塞码，加 Host
        自己的状态。没有任务标题、路径、对象 id、通知正文。隔离时也开。"""
        if (
            self._state not in ("available", "degraded", "degraded_recovery", "quarantined")
            or self._control is None
        ):
            raise OrchestrationRequestError(
                "orchestration_unavailable", self._reason or "编排服务不可用"
            )
        report = (
            self._quarantine
            if self._quarantine is not None
            else self._control.assurance_root_diagnostic()
        )
        return {**dict(report), "host_state": self._state}
