# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""``OrchestrationHost``：一个 Python 应用接入任务编排的唯一入口。

它把编排的零件按产品（桌面）的接法拼起来并驱动：实例锁 → 部署验收门 → 发布连接器 → 沙箱探测
→ 部署策略 → 原生执行池（每池一个认证计数器）→ 用户任务部署（规划世界、执行图、保证通道）→
``Orchestrator`` → 门面 ``MissionControlV1``。对外的每个命令都走门面；建任务前的门口检查（空目标、
密钥、要求能否做到、默认预算与运行档）在这里。

状态字：``created`` → ``available``（正常）／``degraded``（循环连续失败）／``degraded_recovery``
（重启恢复没完成，只读）／``quarantined``（保证通道根隔离，只开诊断）／``unavailable``（没起来）
→ ``closed``。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import secrets
import sys
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx

from ..api import facade, policies
from ..contracts import Budget
from ..deployment.assembly import RootNames, UserMissionDeployment
from ..deployment.goal_world import WORKSPACE_TOOLS, goal_world, root_names
from ..deployment.native_pools import (
    CONTEXT_INPUT_LIMITS,
    NATIVE_OUTPUT_TOKENS,
    NativePools,
    is_native_profile,
    native_profile_id,
    pool_options,
)
from ..governance.policies import SKILL_TOOL_NAMES, DeploymentPolicy
from ..observability.secrets import find_secrets
from ..orchestrator import event_handler
from ..orchestrator.commit_service import GLOBAL_ACCOUNT_PREFIX, global_account_id
from ..orchestrator.taskgraph_deployment import InstalledHtnWiringAcceptance
from ..runtime import sandbox
from ..runtime.assembly import OrchestratorConfig
from ..runtime.connectors_publish import FilePublishConnector
from ..runtime.deepseek_meter import deepseek_meter_binding
from ..runtime.planning_operations import SourceUnavailable
from ..runtime.sandbox import SandboxUnavailable, SeatbeltExecutor
from ..version import __version__
from .embedding import embedding_port
from .instance_lock import InstanceLock
from .provider import build_providers, deepseek_counter, is_deepseek, requested_model
from .script_runner import SandboxScriptRunner
from .settings import ONLY_DEEPSEEK_REASON, HostSettings, ModelConfig
from .tokenizer import ensure_deepseek_tokenizer, tokenizer_matches

logger = logging.getLogger(__name__)

KNOWLEDGE_TOOLS = ("knowledge_list", "knowledge_read")
TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
PUBLISH_PREFIX = "action:file_publish.publish:"
QUARANTINE_REASON = (
    "保证通道根已隔离（库标记不符或状态文件缺失）：只开非披露诊断，不派发任务，不自动写回"
)
DEGRADED_RECOVERY_REASON = "重启恢复没有完成（降级恢复）：任务不派发，只开读取与诊断"
RECOVERY_ISOLATED_NOTE = "重启核对没通过，已隔离，不再推进；可以取消"
FACADE_CODES = {"refused": "sdk_refused"}
FACADE_MESSAGES = {"MISSION_RECOVERY_ISOLATED": RECOVERY_ISOLATED_NOTE}

AutoMode = bool | Callable[[], bool | Awaitable[bool]]


class HostRequestError(RuntimeError):
    """一个被拒绝的请求；``code`` 稳定，供上层按码处理。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class HostUnavailable(RuntimeError):
    """宿主没有起来（没有模型、没有认证计数器、目录被占、验收门没过……）。"""


def backoff_delay(failures: int, maximum: float) -> float:
    """连续失败 ``failures`` 轮后等多少秒；指数封顶，``2.0 ** 1024`` 会溢出。"""

    return min(maximum, 2.0 ** min(max(failures, 0), 16))


def with_publish_sources(criteria: list[str]) -> list[str]:
    """每条 ``action:file_publish.publish:X`` 前补一条 ``file:X``（已有就不补）：那一步就是 X 的
    声明产出者，核对时检查文件存在，系统发布时只从那一步取。"""

    present = {str(item).strip() for item in criteria}
    result: list[str] = []
    for item in criteria:
        text = str(item).strip()
        if text.startswith(PUBLISH_PREFIX):
            source = "file:" + text[len(PUBLISH_PREFIX) :].strip()
            if source != "file:" and source not in present:
                result.append(source)
                present.add(source)
        result.append(item)
    return result


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _strings(item)


def default_host_fingerprint() -> str:
    """没给指纹时：SDK 版本 + 安装包里的部署清单编号。"""

    try:
        deployment_id = InstalledHtnWiringAcceptance()._read()["deployment_id"]
    except Exception:  # noqa: BLE001 - 清单读不到时指纹只含版本
        deployment_id = None
    body = {
        "schema": "sdk-host-fingerprint-v1",
        "sdk_version": __version__,
        "deployment_id": deployment_id,
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class OrchestrationHost:
    """见模块说明。用 ``await OrchestrationHost.open(...)`` 或 ``async with`` 得到已启动的实例。"""

    def __init__(
        self,
        root: str | Path,
        *,
        principal: Any,
        settings: HostSettings | None = None,
        tenant_id: str = "local-host",
        model: ModelConfig | None = None,
        tokenizer_path: str | Path | None = None,
        http_client: httpx.AsyncClient | None = None,
        provider: Any = None,
        thinking_provider: Any = None,
        counter: Any = None,
        thinking_counter: Any = None,
        world_domain: str = "host",
        world_factory: Callable[[Any, Any], Any] | None = None,
        names: RootNames | None = None,
        host_fingerprint: str | None = None,
        notify: Callable[[Mapping[str, Any]], None] | None = None,
        auto_mode: AutoMode = True,
        models_dir: str | Path | None = None,
        require_acceptance: bool = True,
        taskgraph_ports: Any = None,
        owner: str | None = None,
        model_name: str | None = None,
        provider_kind: str | None = None,
        extra_secrets: tuple[str, ...] = (),
    ) -> None:
        """
        ``model`` + ``tokenizer_path``：用 DeepSeek 端点（正常用法）。
        ``provider`` [+ ``thinking_provider``] + ``counter`` [+ ``thinking_counter``]：调用方自带的
        提供方与认证计数器（测试替身，或别的接法）；给了就不再按 ``model`` 构建。
        ``auto_mode``：自动模式下 SDK 代签"授权本轮规划"与"确认纯内容完成要求"；可以是布尔，
        也可以是每轮现读的函数。
        """

        self.root = Path(root)
        self.settings = settings or HostSettings()
        self.tenant_id = tenant_id
        self.principal = principal
        self.owner = owner or f"orchestration-host-{os.getpid()}-{secrets.token_hex(4)}"
        self.model = model
        self._tokenizer_path = None if tokenizer_path is None else Path(tokenizer_path)
        self._http_client = http_client
        self._own_client = False
        self._given_provider = provider
        self._given_thinking_provider = thinking_provider
        self._given_counter = counter
        self._given_thinking_counter = thinking_counter
        # 自带提供方时的模型名与池种类（产品后端传真实的；默认按测试替身）
        self._model_name = model_name
        self._provider_kind = provider_kind
        self._extra_secrets = tuple(extra_secrets)
        self._world_factory = world_factory or goal_world(world_domain)
        self._names = names or root_names(world_domain)
        self._notify = notify or (lambda _payload: None)
        self._auto_mode = auto_mode
        self._models_dir = None if models_dir is None else Path(models_dir)
        self._require_acceptance = require_acceptance
        self._taskgraph_ports = taskgraph_ports
        self.host_fingerprint = host_fingerprint or default_host_fingerprint()

        self.state = "created"
        self.reason: str | None = None
        self.lock = InstanceLock(self.root)
        self.orchestrator: Any = None
        self.control: Any = None
        self.provider: Any = None
        self.thinking_provider: Any = None
        self.config: Any = None
        self.policy: Any = None  # DeploymentPolicy
        self.policy_api: Any = None
        self.native: Any = None
        self.runtime_options: dict[str, Any] = {}
        self.connectors: dict[str, Any] = {}
        self.sandbox: dict[str, Any] | None = None
        self.executor: Any = None
        self.publish: dict[str, Any] = {"enabled": False, "reason": "尚未探测"}
        self.quarantine: dict[str, Any] | None = None
        self.recovery: dict[str, Any] | None = None
        self.on_write: Callable[[], None] | None = None
        #: 重建成功后的回调（宿主应用续读事件、重新探测诊断）。
        self.on_rebuilt: Callable[[], None] | None = None
        # 每轮驱动后的回调（宿主应用自己的轻量杂务，例如存储用量刷新）；只收同步函数，出错只记日志
        self.on_round: Callable[[], Any] | None = None
        self._wake = asyncio.Event()
        self._closing = False
        self._failures = 0
        self._quiet_round = False
        self._last_watermark: Any = None
        self._driver: asyncio.Task[None] | None = None

        def notify_and_wake(payload: Mapping[str, Any]) -> None:
            self._notify(payload)
            self.wake()

        self.deployment = UserMissionDeployment(
            tenant_id=self.tenant_id,
            principal=self.principal,
            world_factory=self._world_factory,
            names=self._names,
            host_fingerprint=self.host_fingerprint,
            notify=notify_and_wake,
            wake=self.wake,
            taskgraph_ports=taskgraph_ports,
        )
        self.duties = self.deployment.duties

    # ------------------------------------------------------------ lifecycle
    @classmethod
    async def open(cls, root: str | Path, **options: Any) -> OrchestrationHost:
        """构造并启动；起不来就抛 ``HostUnavailable``（原因在异常文字里）。"""

        host = cls(root, **options)
        await host.start()
        if host.state not in ("available", "quarantined"):
            raise HostUnavailable(host.reason or "编排宿主没有启动")
        return host

    async def __aenter__(self) -> OrchestrationHost:
        await self.start()
        if self.state not in ("available", "quarantined"):
            raise HostUnavailable(self.reason or "编排宿主没有启动")
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def start(self) -> None:
        """只启动一次；失败后状态是 ``unavailable``，原因在 ``reason``。"""

        if self.state != "created":
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if not self.lock.acquire():
                self.state, self.reason = "unavailable", "另一个实例正在使用编排目录"
                return
            await self._ensure_tokenizer()
            if self._require_acceptance:
                acceptance = self.taskgraph_acceptance_status()
                if acceptance != "VALIDATED":
                    raise HostUnavailable(
                        f"执行图部署验收门没有通过（清单记为 {acceptance}），不开生产开关"
                    )
            await self._open()
            if self.quarantine is not None:
                self.state, self.reason = "quarantined", QUARANTINE_REASON
                return
            self.state, self.reason = "available", None
        except HostUnavailable as error:
            await self._fail(str(error))
        except Exception as error:
            logger.exception("orchestration host failed to start")
            await self._fail(f"启动失败：{type(error).__name__}: {error}"[:300])

    @staticmethod
    def taskgraph_acceptance_status() -> str:
        """安装包里部署清单的验收结论：``VALIDATED`` / ``NOT_RUN``；清单核不过算 ``UNVERIFIED``。"""

        try:
            return InstalledHtnWiringAcceptance().acceptance_status()
        except SourceUnavailable:
            return "UNVERIFIED"

    async def _fail(self, reason: str) -> None:
        self.state, self.reason = "unavailable", reason
        await self._close_runtime()
        self.lock.release()

    @property
    def taskgraph(self) -> Any:
        """执行图的读写口；没起来或根隔离时是 None（隔离只开非披露诊断）。"""
        if self.control is None or self.quarantine is not None:
            return None
        return self.deployment.taskgraph

    @property
    def assurance(self) -> Any:
        """保证通道的读口；没起来或根隔离时是 None。"""
        if self.control is None or self.quarantine is not None:
            return None
        return self.deployment.assurance

    def _build_providers(self) -> None:
        if self._given_provider is not None:
            self.provider = self._given_provider
            self.thinking_provider = self._given_thinking_provider
            return
        if self.model is None:
            raise HostUnavailable("未配置模型：给 model=ModelConfig(...) 或 provider=")
        if self._http_client is None:
            self._http_client = httpx.AsyncClient()
            self._own_client = True
        self.provider, self.thinking_provider = build_providers(self.model, self._http_client)

    def _counters(self) -> tuple[Any, Any]:
        """``(普通计数器, 思考计数器)``；没有认证计数器就 ``(None, None)``。"""

        if self._given_counter is not None:
            return self._given_counter, self._given_thinking_counter
        if self.model is None or self._tokenizer_path is None:
            return None, None
        plain = deepseek_counter(
            self.model, self._tokenizer_path, thinking="disabled", state_dir=self.root
        )
        if plain is None:
            return None, None
        thinking = (
            deepseek_counter(
                self.model, self._tokenizer_path, thinking="enabled", state_dir=self.root
            )
            if self.thinking_provider is not None
            else None
        )
        return plain, thinking

    def _publish_connector(self) -> Any:
        """发布连接器，只给用户真正授权的目录：目录存在，且这个卷能做硬链接（连接器唯一的原子
        提交点）。三样缺一个连接器就不启用，任务也就不能带发布要求。"""

        configured = (self.settings.publish_dir or "").strip()
        if not configured:
            self.publish = {"enabled": False, "reason": "未授权发布目录"}
            return None
        root = Path(configured).expanduser()
        if not root.is_dir():
            self.publish = {"enabled": False, "root": str(root), "reason": "授权目录不存在"}
            return None
        if not FilePublishConnector.supports_hardlinks(root):
            self.publish = {
                "enabled": False,
                "root": str(root),
                "reason": "该卷不支持硬链接，无法原子发布",
            }
            return None
        self.publish = {"enabled": True, "root": str(root)}
        return FilePublishConnector(root, self.root / "connectors" / "file_publish")

    async def _probe_sandbox(self) -> dict[str, Any]:
        """每次启动都重新证明隔离（不信文件）：探测过不了就 ``ok=False``，部署不跑模型写的代码。"""

        self.executor = None
        if getattr(sys, "frozen", False):
            return {"ok": False, "reason": "frozen_backend_is_not_a_python_interpreter"}
        try:
            executor = SeatbeltExecutor.for_interpreter(sys.executable)
        except SandboxUnavailable as error:
            return {"ok": False, "reason": str(error)[:300]}
        try:
            report = await sandbox.probe_sandbox(executor)
        except Exception as error:  # noqa: BLE001 - 探测跑不了不算通过
            logger.warning("sandbox probe failed to run: %s", error)
            return {"ok": False, "reason": f"{type(error).__name__}: {error}"[:300]}
        result = report.to_json()
        with contextlib.suppress(OSError):
            (self.root / "sandbox-probe.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if report.ok:
            self.executor = executor
        return result

    def _build_native(self, meter_factory: Any) -> NativePools:
        embedding, reason = embedding_port(self._models_dir)
        executor = self.executor
        return NativePools(
            tenant_id=self.tenant_id,
            principal_id=str(self.principal.principal_id),
            allowed_tools=tuple(self.policy.allowed_tools),
            meter_factory=meter_factory,
            embedding=embedding,
            embedding_reason=reason,
            script_runner=None
            if executor is None
            else (lambda runs_dir: SandboxScriptRunner(executor, runs_dir)),
            script_runner_label=None if executor is None else f"sandbox:{executor.kind}",
        )

    def _assemble_runtime(self) -> None:
        """执行池与运行选项（启动与重建共用）。"""

        counter, thinking_counter = self._counters()
        meter_factory = (
            counter.meter_factory
            if counter is not None and hasattr(counter, "meter_factory")
            else deepseek_meter_binding
        )
        self.native = self._build_native(meter_factory)
        self.runtime_options = pool_options(
            self.config,
            native=self.native,
            provider=self.provider,
            counter=counter,
            provider_kind=self._provider_kind or ("env" if self.model is not None else "fixtures"),
            thinking_provider=self.thinking_provider,
            thinking_counter=thinking_counter,
        )
        if not self.runtime_options["profiles"]:
            raise HostUnavailable(self._no_pool_reason())

    async def _ensure_tokenizer(self) -> None:
        """DeepSeek 端点的钉死分词文件：给了路径就核对那份（不在就下载到那里），没给就下载到
        数据目录；只下载一次，在线程里下载不卡事件循环。给了计数器就不需要。"""

        if self._given_counter is not None or self.model is None or not is_deepseek(self.model):
            return
        target = self._tokenizer_path or self.root / "deepseek-v41-tokenizer.json"
        if target.exists() and not tokenizer_matches(target):
            # 调用方给的文件不是钉死的那份：不覆盖它，如实拒绝
            raise HostUnavailable(f"DeepSeek 分词文件不是钉死的那份（哈希不符）：{target}")
        try:
            self._tokenizer_path = await asyncio.to_thread(ensure_deepseek_tokenizer, target)
        except Exception as error:
            raise HostUnavailable(
                f"取 DeepSeek 分词文件失败（{target}）：{type(error).__name__}: {error}"[:300]
            ) from error

    def _no_pool_reason(self) -> str:
        """没有一个执行池能起来的原因：DeepSeek 端点缺分词文件，或者端点不是 DeepSeek。"""

        if (
            self._given_counter is None
            and self.model is not None
            and is_deepseek(self.model)
            and self._tokenizer_path is None
        ):
            return "DeepSeek 端点缺钉死的分词文件：请传 tokenizer_path（见接入说明）"
        return ONLY_DEEPSEEK_REASON

    def _new_orchestrator(self) -> Any:
        def assemble_startup(orchestrator: Any) -> None:
            self.deployment.assemble(orchestrator)
            self.native.bind_orchestrator(orchestrator)

        return event_handler.Orchestrator(
            self.config,
            self.provider,
            owner=self.owner,
            connectors=self.connectors,
            startup_assembly=assemble_startup,
            assurance_root_setup=self.deployment.assurance_root_setup(),
            **self.runtime_options,
        )

    async def _open(self) -> None:
        self._build_providers()
        publish = self._publish_connector()
        enabled_connectors: tuple[str, ...] = ()
        if publish is not None:
            self.connectors["file_publish"] = publish
            enabled_connectors = ("file_publish",)
        self.sandbox = await self._probe_sandbox()
        sandboxed = bool(self.sandbox.get("ok"))
        skill_tools = tuple(SKILL_TOOL_NAMES)
        base_tools = (
            (*WORKSPACE_TOOLS, *KNOWLEDGE_TOOLS, "run_tests")
            if sandboxed
            else (*WORKSPACE_TOOLS, *KNOWLEDGE_TOOLS)
        )
        self.policy = DeploymentPolicy(
            allowed_tools=(*base_tools, *skill_tools),
            skill_tools=skill_tools,
            code_execution="sandboxed" if sandboxed else "off",
            enabled_connectors=enabled_connectors,
            max_action_level=self.settings.max_action_level,
        )
        # 真实模型：思考与输出共用输出上限，8K 会把审阅结论截在信封闭合前；起步 16K，写满翻倍。
        knobs: dict[str, Any] = (
            {"default_max_output_tokens": 16384, "max_output_tokens_ceiling": NATIVE_OUTPUT_TOKENS}
            if self.model is not None
            else {}
        )
        self.config = OrchestratorConfig(
            evidence_root=self.root,
            model=(
                requested_model(self.model)
                if self.model is not None
                else (self._model_name or "agent-model")
            ),
            max_concurrency=self.settings.max_concurrency,
            max_concurrent_model_calls=self.settings.max_concurrent_model_calls,
            lease_seconds=self.settings.lease_seconds,
            deployment_policy=self.policy,
            sandbox_executor=self.executor,
            task_max_tokens=self.settings.task_max_tokens,
            global_budget=Budget(max_tokens=self.settings.global_monthly_max_tokens),
            **knobs,
        )
        self._assemble_runtime()
        await self._enter(self._new_orchestrator())

    async def _enter(self, candidate: Any) -> None:
        """进入一个新的 ``Orchestrator``：进入与门面都成功才发布；失败就把它关掉再抛。"""

        try:
            await candidate.__aenter__()
            control = facade.MissionControlV1(
                candidate, tenant_id=self.tenant_id, principal=self.principal
            )
            policy_api = policies.PolicyApi(candidate.commit, self.principal)
        except BaseException as error:
            try:
                await candidate.__aexit__(type(error), error, error.__traceback__)
            except BaseException as cleanup_error:  # noqa: BLE001 - 保留原始失败
                error.add_note(f"cleanup failed: {type(cleanup_error).__name__}")
            raise
        self.orchestrator = candidate
        report = dict(control.assurance_root_diagnostic())
        self.quarantine = None if report.get("state") == "NATIVE" else report
        if self.quarantine is not None:
            # 根隔离：只留门面给非披露诊断；不绑职责、不开策略读
            self.control = control
            return
        candidate.set_between_cycles(
            self._host_duties, every_seconds=self.settings.tick_active_seconds
        )
        self.control = control
        self.deployment.bind(candidate, control)
        self.policy_api = policy_api

    async def _rebuild(self) -> None:
        old, self.orchestrator = self.orchestrator, None
        self.control = None
        self.deployment.bind(None, None)
        self.policy_api = None
        self.quarantine = None
        if old is not None:
            try:
                await old.__aexit__(None, None, None)
            except Exception:
                logger.exception("orchestrator close during rebuild failed")
        self._assemble_runtime()
        await self._enter(self._new_orchestrator())
        if self.quarantine is not None:
            self.state, self.reason = "quarantined", QUARANTINE_REASON
            return
        if self.on_rebuilt is not None:
            self.on_rebuilt()

    async def _close_runtime(self) -> None:
        orchestrator, self.orchestrator = self.orchestrator, None
        self.control = None
        self.deployment.bind(None, None)
        self.policy_api = None
        self.quarantine = None
        if orchestrator is not None:
            try:
                await orchestrator.__aexit__(None, None, None)
            except Exception:
                logger.exception("orchestrator close failed")
        if self._own_client and self._http_client is not None:
            client, self._http_client = self._http_client, None
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                logger.debug("provider http client close failed", exc_info=True)

    async def close(self) -> None:
        """有序关闭（测试与正常退出）；被 SIGKILL 时锁由内核释放，重启按持久状态恢复。"""

        self._closing = True
        self._wake.set()
        driver, self._driver = self._driver, None
        if driver is not None:
            try:
                await asyncio.wait_for(driver, timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                driver.cancel()
            except Exception:
                logger.exception("orchestration driver ended with an error")
        await self._close_runtime()
        self.lock.release()
        self.state = "closed"

    # ------------------------------------------------------------ drive
    def wake(self) -> None:
        self._wake.set()
        if self.on_write is not None:
            try:
                self.on_write()
            except Exception:  # noqa: BLE001
                logger.debug("on_write hook failed", exc_info=True)

    @property
    def store(self) -> Any:
        return None if self.orchestrator is None else self.orchestrator.store

    async def _auto(self) -> bool:
        mode = self._auto_mode
        if isinstance(mode, bool):
            return mode
        try:
            value = mode()
            if hasattr(value, "__await__"):
                value = await value
            return bool(value)
        except Exception:  # noqa: BLE001 - 读不到按手动
            logger.warning("auto mode unreadable; planning authority and confirmations stay manual")
            return False

    async def _host_duties(self) -> None:
        """每轮：保证通道检查策略投影、自动授权规划、自动确认纯内容完成要求。"""

        if self.assurance is not None:
            try:
                self.duties.project_check_policies()
            except Exception:  # noqa: BLE001 - 不停循环，下一轮再试
                logger.exception("assurance check policy projection failed")
        auto = await self._auto()
        self.duties.auto_authorize_planning(auto=auto)
        self.duties.auto_confirm_content_completion(auto=auto)

    def _degraded_recovery(self) -> bool:
        if self.orchestrator is None:
            return False
        try:
            recovery = dict(self.orchestrator.recovery_status())
        except Exception:  # noqa: BLE001
            logger.exception("recovery status unreadable")
            return False
        self.recovery = recovery
        if recovery.get("state") != "DEGRADED_RECOVERY":
            return False
        latest = recovery.get("latest")
        step = latest.get("failed_step") if isinstance(latest, Mapping) else None
        self.state = "degraded_recovery"
        self.reason = f"{DEGRADED_RECOVERY_REASON}（失败步骤：{step or '未知'}）"
        return True

    async def run_once(self) -> bool:
        """跑循环到空闲，再做一轮职责。False：没有进入空闲（有一轮结果不明的调用卡着）。"""

        await self.orchestrator.run()
        if self._degraded_recovery():
            return True
        await self._host_duties()
        return True

    async def drain(self, timeout: float = 60.0) -> bool:
        try:
            return await asyncio.wait_for(self.run_once(), timeout=timeout)
        except TimeoutError:
            return False

    def _tick(self) -> float:
        try:
            store = self.orchestrator.store
            active = [m for m in store.list_missions() if str(m.status) not in TERMINAL]
        except Exception:  # noqa: BLE001 - 读不到就按忙的节拍，下一轮再读
            logger.debug("mission list unreadable; keeping the active tick", exc_info=True)
            return self.settings.tick_active_seconds
        if not active:
            return self.settings.tick_idle_seconds
        if all(store.waiting_on(m.id) for m in active):
            return self.settings.tick_waiting_seconds
        if self._quiet_round:
            # 一轮没有任何持久改动就按只等人的节奏等；有写入会唤醒
            return self.settings.tick_waiting_seconds
        return self.settings.tick_active_seconds

    def _note_quiet_round(self) -> None:
        try:
            mark = self.orchestrator.durable_watermark()
        except Exception:  # noqa: BLE001 - 读不到当作忙
            self._quiet_round, self._last_watermark = False, None
            return
        self._quiet_round = mark == self._last_watermark
        self._last_watermark = mark

    async def run_forever(self) -> None:
        """驱动循环：跑到空闲 → 职责 → 按节奏等（被唤醒就提前）；连续失败退避、重建、降级。"""

        while not self._closing:
            try:
                if self.orchestrator is None:
                    await self._rebuild()
                if self.quarantine is not None:
                    return
                await self.orchestrator.run()
                if self._degraded_recovery():
                    return
                await self._host_duties()
                self._note_quiet_round()
                if self.on_round is not None:
                    try:
                        self.on_round()
                    except Exception:  # noqa: BLE001 - 应用自己的杂务出错不算驱动循环失败
                        logger.exception("on_round callback failed")
                self._failures = 0
                if self.state == "degraded":
                    self.state, self.reason = "available", None
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._failures += 1
                logger.exception("orchestrator run failed (%s in a row)", self._failures)
                if self._failures >= self.settings.degraded_after_failures:
                    self.state, self.reason = "degraded", f"编排循环连续失败：{error}"[:300]
                if (
                    self.orchestrator is not None
                    and self._failures % self.settings.rebuild_after_failures == 0
                ):
                    try:
                        await self._rebuild()
                    except Exception as rebuild_error:
                        logger.exception("orchestrator rebuild failed")
                        self.state = "degraded"
                        self.reason = f"编排循环重建失败：{rebuild_error}"[:300]
                await asyncio.sleep(
                    backoff_delay(self._failures, self.settings.backoff_max_seconds)
                )
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._tick())
            self._wake.clear()

    def start_driver(self) -> None:
        """在当前事件循环里后台跑 ``run_forever``（宿主进程常驻时用）。"""

        if self._driver is None and self.state == "available":
            self._driver = asyncio.get_running_loop().create_task(
                self.run_forever(), name="orchestration-driver"
            )

    async def run_until_settled(
        self, mission_id: str, *, timeout: float = 3600.0, poll_seconds: float = 2.0
    ) -> dict[str, Any]:
        """驱动到这个任务结束（或超时），返回它的快照。等人的时候也在等：调用方要在别的协程里
        批准／回答，或者用自动模式。"""

        deadline = time.monotonic() + timeout
        while True:
            await self.run_once()
            mission = self.store.get_mission(mission_id)
            if mission is None:
                raise HostRequestError("not_found", f"任务 {mission_id} 不存在")
            if str(getattr(mission.status, "value", mission.status)) in TERMINAL:
                return self.snapshot(mission_id)
            if time.monotonic() >= deadline:
                raise TimeoutError(f"任务 {mission_id} 在 {timeout:.0f} 秒内没有结束")
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=poll_seconds)

    # ------------------------------------------------------------ guards
    def _require(self) -> Any:
        if self.state not in ("available", "degraded", "degraded_recovery") or self.control is None:
            raise HostRequestError("orchestration_unavailable", self.reason or "编排服务不可用")
        return self.control

    def _refuse_new_work(self, message: str) -> None:
        if self.state == "degraded":
            raise HostRequestError("orchestration_degraded", message)
        if self.state == "degraded_recovery":
            raise HostRequestError(
                "orchestration_degraded_recovery", self.reason or DEGRADED_RECOVERY_REASON
            )

    def secret_values(self) -> tuple[str, ...]:
        own = () if self.model is None else (self.model.api_key,)
        return (*own, *self._extra_secrets)

    def refuse_secrets(self, *values: Any) -> None:
        """请求里任何一个字符串像密钥（通用模式，或本部署自己的密钥）都拒绝。"""

        for text in _strings(values):
            if find_secrets(text, extra=self.secret_values()):
                raise HostRequestError("secret_rejected", "内容里有像密钥的文本，未接受")

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        control = self._require()
        try:
            return getattr(control, method)(*args, **kwargs)
        except facade.FacadeError as error:
            raise HostRequestError(
                FACADE_CODES.get(error.code, error.code),
                FACADE_MESSAGES.get(error.code, str(error)),
            ) from error

    def check_criteria(self, criteria: list[str]) -> None:
        """这个部署真做得到的要求：``pytest:`` 要沙箱，``action:`` 要启用了的连接器。"""

        if any(c.strip().startswith("pytest:") for c in criteria) and not (
            self.policy is not None and self.policy.code_execution == "sandboxed"
        ):
            reason = (self.sandbox or {}).get("reason")
            raise HostRequestError(
                "local_tests_disabled",
                "本机没有可用的隔离环境：成功条件不能使用 pytest:"
                + (f"（{reason}）" if reason else ""),
            )
        enabled = set(getattr(self.policy, "enabled_connectors", ()) or ())
        for criterion in criteria:
            text = criterion.strip()
            if not text.startswith("action:"):
                continue
            connector = text[len("action:") :].partition(".")[0].strip()
            if not enabled:
                raise HostRequestError(
                    "action_criteria_disabled", "这个部署没有启用真实动作：成功条件不能使用 action:"
                )
            if connector not in enabled:
                reason = self.publish.get("reason") if connector == "file_publish" else None
                raise HostRequestError(
                    "action_criteria_disabled",
                    f"连接器 {connector} 未启用" + (f"：{reason}" if reason else ""),
                )

    # ------------------------------------------------------------ profiles & defaults
    def context_profiles(self) -> list[dict[str, Any]]:
        profiles = self.runtime_options.get("profiles", {})
        rows = []
        for tokens in CONTEXT_INPUT_LIMITS:
            for identifier in (native_profile_id(tokens), native_profile_id(tokens, thinking=True)):
                if identifier not in profiles:
                    continue
                rows.append(
                    {
                        "profile_id": identifier,
                        "max_input_tokens": profiles[identifier].context_policy.input_budget(),
                        "default_max_output_tokens": 8192,
                        "max_output_tokens_ceiling": NATIVE_OUTPUT_TOKENS,
                        "mission_max_tokens": self.settings.default_mission_max_tokens,
                    }
                )
        return rows

    def context_default(self) -> str | None:
        """新任务默认的池：设置的窗口大小，思考档优先（模型配置说开思考时）。"""

        available = {p["profile_id"] for p in self.context_profiles()}
        thinking = self.model is not None and self.model.thinking == "enabled"
        for selected in (
            native_profile_id(self.settings.context_input_tokens, thinking=thinking),
            native_profile_id(self.settings.context_input_tokens),
        ):
            if selected in available:
                return selected
        return None

    def native_profile_ids(self) -> list[str]:
        return [key for key in self.runtime_options.get("profiles", {}) if is_native_profile(key)]

    def native_pool(self, profile_id: str | None) -> Any:
        """某个运行档的原生执行池（技能目录、运行平面动词走它）。"""

        self._require()
        native = self.native_profile_ids()
        if self.native is None or not native:
            raise HostRequestError("native_plane_unavailable", ONLY_DEEPSEEK_REASON)
        selected = profile_id or self.context_default()
        if selected not in native:
            raise HostRequestError("invalid_request", "所选运行配置不在原生运行平面上")
        return self.orchestrator.assembled.pool(selected)

    def mission_token_default(self, profile_id: str | None = None) -> int:
        selected = profile_id or self.context_default()
        return next(
            (
                p["mission_max_tokens"]
                for p in self.context_profiles()
                if p["profile_id"] == selected
            ),
            self.settings.default_mission_max_tokens,
        )

    def door(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """建任务前的门口检查：目标与要求非空、没有密钥、要求做得到、补发布来源、补默认运行档
        与预算（同一幂等键的重试沿用原任务的）。"""

        body = dict(request)
        goal = body.get("goal")
        criteria = body.get("success_criteria")
        if not isinstance(goal, str) or not goal.strip():
            raise HostRequestError("invalid_request", "任务目标不能为空")
        if (
            not isinstance(criteria, list)
            or not criteria
            or not all(isinstance(c, str) and c.strip() for c in criteria)
        ):
            raise HostRequestError("invalid_request", "成功条件不能为空，每行一条")
        self.refuse_secrets(body)
        self.check_criteria(criteria)
        body["success_criteria"] = with_publish_sources(criteria)
        found = (
            self.orchestrator.store.find_mission(
                self.tenant_id, str(body.get("idempotency_key", ""))
            )
            if self.orchestrator is not None
            else None
        )
        existing = found[0] if found else None
        profiles = self.context_profiles()
        if "runtime_profile_id" in body:
            selected = body["runtime_profile_id"]
            if not isinstance(selected, str) or not any(
                p["profile_id"] == selected for p in profiles
            ):
                raise HostRequestError("invalid_request", "所选上下文配置当前不可用")
        elif existing is not None:
            selected = (existing.final_report or {}).get("runtime_profile_id")
            if selected is not None:
                body["runtime_profile_id"] = selected
        else:
            selected = self.context_default()
            if selected is not None:
                body["runtime_profile_id"] = selected
        token_default = (
            existing.budget.max_tokens
            if existing is not None
            else self.mission_token_default(selected)
        )
        attempt_default = (
            existing.budget.max_attempts
            if existing is not None
            else self.settings.default_mission_max_attempts
        )
        budget = body.get("budget")
        if budget is None:
            budget = {}
        if not isinstance(budget, Mapping):
            raise HostRequestError("invalid_request", "预算必须是一个对象")
        budget = dict(budget)
        for name, default in (("max_tokens", token_default), ("max_attempts", attempt_default)):
            value = budget.get(name)
            if value is None:
                budget[name] = default
            elif isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise HostRequestError("invalid_request", f"预算 {name} 必须是正整数")
        body["budget"] = budget
        return body

    # ------------------------------------------------------------ commands
    def _deploy(self, method: str, body: Mapping[str, Any]) -> dict[str, Any]:
        control = self._require()
        try:
            return dict(getattr(self.deployment, method)(self.orchestrator, control, body))
        except facade.FacadeError as error:
            raise HostRequestError(FACADE_CODES.get(error.code, error.code), str(error)) from error

    def create_mission(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """建一个任务：``goal``、``success_criteria``（每行一条）、可选 ``idempotency_key``、
        ``budget``、``runtime_profile_id``、``stop_conditions``、``workspace_seed``。
        返回 ``{"mission_id", "created", "spec_hash"}``。"""

        self._require()
        self._refuse_new_work("编排循环目前不正常，暂不接受新任务；已有的仍可取消或接管")
        receipt = self._deploy("create_mission", self.door(request))
        return {
            "mission_id": receipt["mission_id"],
            "created": receipt["created"],
            "spec_hash": receipt["spec_hash"],
        }

    def create_mission_with_sources(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """``{"mission": {...}, "sources": [{"path", "content", "kind", "idempotency_key"}, ...]}``
        一个事务里建任务并登记资料。"""

        self._require()
        self._refuse_new_work("编排循环异常，暂不接受新任务")
        if set(request) != {"mission", "sources"} or not isinstance(request["mission"], Mapping):
            raise HostRequestError("invalid_request", "需要 mission 与 sources 原子批次")
        self.refuse_secrets(request)
        return self._deploy(
            "create_mission_with_sources",
            {"mission": self.door(request["mission"]), "sources": request["sources"]},
        )

    def source_command(self, operation: str, request: Mapping[str, Any]) -> dict[str, Any]:
        """资料：``register`` / ``supersede`` / ``revoke``。"""

        methods = {
            "register": "register_source",
            "supersede": "supersede_source",
            "revoke": "revoke_source",
        }
        if operation not in methods:
            raise HostRequestError("invalid_request", "未知来源操作")
        self._require()
        self._refuse_new_work("编排循环异常，暂不接受换资料")
        self.refuse_secrets(request)
        result = self._call(methods[operation], dict(request))
        self.wake()
        return dict(result)

    def planning_authorization(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """授权本轮规划：``{"operation": "issue"|"bind"|"renew"|"revoke", ...}``。"""

        self._require()
        self.refuse_secrets(request)
        receipt = self._call("planning_authorization", dict(request))
        self.wake()
        return dict(receipt)

    def pending_planning_authorizations(self) -> list[dict[str, Any]]:
        return list(self._call("pending_planning_authorizations"))

    def answer_planning_question(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self._require()
        self.refuse_secrets(request)
        receipt = self._call("answer_planning_question", dict(request))
        self.wake()
        return dict(receipt)

    def approve_operation_completion_spec(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self._require()
        self.refuse_secrets(request)
        receipt = self._call("approve_operation_completion_spec", dict(request))
        self.wake()
        return dict(receipt)

    def amend_requirements(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """改一个在跑任务的要求（add / rewrite / remove / goal）；与建任务同一套门口检查。"""

        self._require()
        self._refuse_new_work("编排循环异常，暂不接受改要求")
        self.refuse_secrets(request)
        changes = [dict(item) for item in request.get("changes") or () if isinstance(item, Mapping)]
        statements = [
            str(item.get("statement") or "").strip()
            for item in changes
            if item.get("statement") and item.get("op") != "goal"
        ]
        self.check_criteria(statements)
        detail = self._call("snapshot", str(request.get("mission_id") or ""))
        workspace = (detail.get("snapshot") or {}).get("operation_workspace") or {}
        current = {
            str(item.get("id")): str(item.get("statement") or "").strip()
            for item in workspace.get("criteria") or ()
        }
        added: list[str] = []
        for item in changes:
            if item.get("op") == "add":
                added.append(str(item.get("statement") or "").strip())
            elif item.get("op") == "remove":
                current.pop(str(item.get("criterion_id")), None)
            elif str(item.get("criterion_id")) in current:
                current[str(item.get("criterion_id"))] = str(item.get("statement") or "").strip()
        resulting = [*current.values(), *added]
        sources = [
            {"op": "add", "statement": source}
            for source in with_publish_sources(resulting)
            if source not in resulting
        ]
        receipt = self._call(
            "amend_requirements", {**dict(request), "changes": [*sources, *changes]}
        )
        self.wake()
        return dict(receipt)

    def submit_operation_intent(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self._require()
        if self.state == "degraded":
            raise HostRequestError("orchestration_degraded", "编排循环异常，暂不接受新操作")
        self.refuse_secrets(request)
        receipt = self._call("submit_operation_intent", dict(request))
        self.wake()
        return dict(receipt)

    def operation_intent_status(self, intent_id: str) -> dict[str, Any]:
        self._require()
        return dict(self._call("operation_intent_status", intent_id))

    def method_library(self) -> dict[str, Any]:
        self._require()
        return dict(self._call("list_method_library"))

    def retire_library_entry(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self._require()
        self._refuse_new_work("编排循环异常，暂不接受改方法库")
        self.refuse_secrets(request)
        return dict(self._call("retire_library_entry", dict(request)))

    def cancel(self, mission_id: str) -> dict[str, Any]:
        result = self._call("cancel", mission_id)
        self.wake()
        return {"mission_id": result["mission_id"], "status": result["status"]}

    def decide(self, request_id: str, decision: str, **fields: str) -> dict[str, Any]:
        """人工批准：``decision`` 为 ``approve`` / ``reject``，附 ``reason`` / ``note`` 等。"""

        self.refuse_secrets(fields)
        result = self._call("decide", request_id, decision, **fields)
        self.wake()
        return result

    def takeover(self, task_id: str, action: str, *, basis: str, note: str = "") -> dict[str, Any]:
        self.refuse_secrets(basis, note)
        result = self._call("takeover", task_id, action, basis=basis, note=note)
        self.wake()
        return result

    def resolve_unknown(self, action_key: str, outcome: str, *, basis: str) -> dict[str, Any]:
        self.refuse_secrets(basis)
        result = self._call("resolve_unknown", action_key, outcome=outcome, basis=basis)
        self.wake()
        return result

    def comment(self, target_id: str, text: str) -> dict[str, Any]:
        self.refuse_secrets(text)
        result = self._call("comment", target_id, text)
        self.wake()
        return result

    # ------------------------------------------------------------ reads
    def missions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return list(self._call("missions", limit=limit))

    def snapshot(self, mission_id: str) -> dict[str, Any]:
        return dict(self._call("snapshot", mission_id))

    def events(self, mission_id: str, *, after_seq: int = 0, limit: int = 100) -> dict[str, Any]:
        return dict(self._call("events", mission_id, after_seq=after_seq, limit=limit))

    def approvals(self, mission_id: str | None = None) -> list[dict[str, Any]]:
        return list(self._call("approvals", mission_id))

    def artifact_read(self, artifact_id: str) -> dict[str, Any]:
        return dict(self._call("artifact_read", artifact_id))

    def assurance_root_diagnostic(self) -> dict[str, Any]:
        """非披露诊断（隔离时也开）：状态、能否执行、匿名阻塞码，加宿主自己的状态。"""

        if (
            self.state not in ("available", "degraded", "degraded_recovery", "quarantined")
            or self.control is None
        ):
            raise HostRequestError("orchestration_unavailable", self.reason or "编排服务不可用")
        report = (
            self.quarantine
            if self.quarantine is not None
            else self.control.assurance_root_diagnostic()
        )
        return {**dict(report), "host_state": self.state}

    def recovery_isolated(self) -> dict[str, dict[str, Any]]:
        """重启恢复隔离的任务：``{mission_id: {"tables": [...]}}``（读不到当作没有）。"""

        try:
            isolated = dict(self.orchestrator.recovery_status()).get("isolated_missions") or {}
            return {
                str(mission_id): {"tables": [str(n) for n in (item or {}).get("tables") or ()]}
                for mission_id, item in dict(isolated).items()
            }
        except Exception:  # noqa: BLE001 - 状态视图不抛；没起来时每次列任务都会走到这里
            logger.debug("recovery isolation table unreadable", exc_info=True)
            return {}

    def global_budget_status(self) -> dict[str, Any]:
        running = self.orchestrator is not None and self.state in ("available", "degraded")
        now = self.orchestrator.store.now if running else time.time()
        limit = int(self.settings.global_monthly_max_tokens)
        row = {
            "month": global_account_id(now).removeprefix(GLOBAL_ACCOUNT_PREFIX),
            "max_tokens": limit,
            "opened": False,
            "used_tokens": 0,
            "reserved_tokens": 0,
            "remaining_tokens": limit,
        }
        pool = self.orchestrator.commit.global_account() if running else None
        if pool is None:
            return row
        remaining = pool.remaining_tokens()
        return {
            **row,
            "month": pool.account_id.removeprefix(GLOBAL_ACCOUNT_PREFIX),
            "max_tokens": int(pool.limits.max_tokens or limit),
            "opened": True,
            "used_tokens": int(pool.settled_tokens),
            "reserved_tokens": int(pool.reserved_tokens),
            "remaining_tokens": int(limit if remaining is None else remaining),
        }

    def status(self) -> dict[str, Any]:
        active = 0
        if self.orchestrator is not None and self.state in (
            "available",
            "degraded",
            "degraded_recovery",
        ):
            try:
                active = sum(
                    1
                    for m in self.orchestrator.store.list_missions()
                    if str(m.status) not in TERMINAL
                )
            except Exception:  # noqa: BLE001 - 状态视图不抛
                logger.exception("active mission count unreadable")
                active = 0
        assurance_root: dict[str, Any] | None = None
        if self.quarantine is not None:
            assurance_root = dict(self.quarantine)
        elif self.control is not None and self.state in (
            "available",
            "degraded",
            "degraded_recovery",
        ):
            try:
                assurance_root = dict(self.control.assurance_root_diagnostic())
            except Exception:  # noqa: BLE001
                logger.exception("assurance root diagnostic failed")
        recovery: dict[str, Any] | None = None
        if self.recovery is not None:
            recovery = dict(self.recovery)
        elif self.orchestrator is not None and self.state in ("available", "degraded"):
            try:
                recovery = dict(self.orchestrator.recovery_status())
            except Exception:  # noqa: BLE001
                logger.exception("recovery status unreadable")
        return {
            "available": self.state == "available",
            "state": self.state,
            "reason": self.reason,
            "orchestrator_version": __version__,
            "model": None if self.model is None else self.model.public(),
            "active_missions": active,
            "allowed_tools": list(self.policy.allowed_tools if self.policy else WORKSPACE_TOOLS),
            "code_execution": None if self.policy is None else self.policy.code_execution,
            "sandbox": dict(self.sandbox or {"ok": False, "reason": "尚未探测"}),
            "publish": dict(self.publish),
            "assurance_available": self.assurance is not None,
            "assurance_root": assurance_root,
            "recovery": recovery,
            "context_profiles": self.context_profiles(),
            "native_plane": (
                self.native.status()
                if self.native is not None
                else {
                    "enabled": True,
                    "available": False,
                    "reason": self.reason or ONLY_DEEPSEEK_REASON,
                }
            ),
            "default_context_profile_id": self.context_default(),
            "mission_budget_defaults": {
                "max_tokens": self.mission_token_default(),
                "max_attempts": self.settings.default_mission_max_attempts,
            },
            "global_budget": self.global_budget_status(),
            "owner": self.owner,
        }


__all__ = (
    "DEGRADED_RECOVERY_REASON",
    "FACADE_CODES",
    "KNOWLEDGE_TOOLS",
    "QUARANTINE_REASON",
    "RECOVERY_ISOLATED_NOTE",
    "TERMINAL",
    "HostRequestError",
    "HostUnavailable",
    "OrchestrationHost",
    "backoff_delay",
    "default_host_fingerprint",
    "with_publish_sources",
)
