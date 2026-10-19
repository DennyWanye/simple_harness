# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Construction, start, close and the SDK host underneath.

编排的拼装、驱动与命令只有 SDK 那一份（``agent_orchestrator.host.OrchestrationHost``）；这里只补桌面
自己的：提供方链快照、钉版核对与部署清单、通知存档、存储用量、诊断探测、进度推送的唤醒钩子。
服务的私有属性名保留，都是对宿主同名东西的转发（界面投影、诊断、技能目录与测试都在读）。
"""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import simple_harness
from agent_orchestrator.deployment.goal_world import root_names
from agent_orchestrator.host import HostRequestError, OrchestrationHost

from ..assurance import host_fingerprint
from ..manifest import MANIFEST_SCHEMA, build_manifest, distributions, write_manifest
from ..notices import MissionNotices
from ..provider import (
    NO_MODEL,
    ProviderSnapshot,
    model_config,
    models_dir,
    tokenizer_path,
)
from ..settings import OrchestrationSettings
from ..storage_usage import StorageUsage
from .support import OrchestrationRequestError

# 与原模块同名的记录器（getLogger 按名字返回同一个对象）；就地定义，ruff 才认得出
# 捕获异常后写日志的写法
logger = logging.getLogger("deskpet.orchestration.service")

TENANT = "local-desktop"
WORLD_DOMAIN = "desktop"
#: The root goal type and the task/duty id prefixes of a user Mission's root: part of every
#: existing Mission's identity.
ROOT_NAMES = root_names(WORLD_DOMAIN)


class OrchestrationServiceCore:
    """Construction, start, close and the SDK host underneath."""

    if TYPE_CHECKING:

        def status(self) -> dict[str, Any]: ...
        def _detect_diagnostics(self) -> bool: ...

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
        self.owner = f"deskpet-orchestrator-{os.getpid()}-{secrets.token_hex(4)}"
        self._principal = principal
        self._snapshot = provider_snapshot
        self._http_client = http_client
        self._drive_enabled = drive
        self._local_state: str | None = (
            None  # 宿主起来之前桌面自己的状态（disabled / 钉版不符）
        )
        self._local_reason: str | None = None
        self._diagnostics_available = False
        self._manifest: dict[str, Any] | None = None
        # 2026-09-25 条目 7: disk taken by task/session data, measured off the loop; the
        # Settings page reads it and warns past ``storage_warn_bytes`` — nothing is deleted.
        self._storage = StorageUsage(self.root, warn_bytes=settings.storage_warn_bytes)
        # 任务结束通知的唯一 Host 记录（主对话卡片、主 Agent 上下文都读它）
        self._notices = MissionNotices(self.root)
        self._permission_mode_reader = permission_mode_reader

        def notify(payload: Mapping[str, Any]) -> None:
            # At-least-once local status transport: the notice is kept (de-duplicated by
            # event id) until the person acknowledges it in the main conversation; the
            # change pump pushes ``mission_changed`` so the cards re-read.
            self._notices.record(payload)

        model = (
            None
            if provider_snapshot is None
            else model_config(provider_snapshot, settings)
        )
        self._host = OrchestrationHost(
            self.root,
            principal=principal,
            settings=settings,
            tenant_id=TENANT,
            model=model,
            tokenizer_path=tokenizer_path(),
            http_client=http_client,
            provider=provider,
            counter=native_test_counter,
            world_domain=WORLD_DOMAIN,
            host_fingerprint=host_fingerprint(),
            notify=notify,
            auto_mode=self._auto_mode,
            models_dir=models_dir(),
            taskgraph_ports=taskgraph_deployment,
            owner=self.owner,
            model_name=None
            if provider_snapshot is None
            else provider_snapshot.requested_model,
            provider_kind=None if provider_snapshot is None else "env",
            extra_secrets=()
            if provider_snapshot is None
            else (provider_snapshot.api_key,),
        )
        self._host.on_rebuilt = self._after_rebuild
        self._host.on_round = (
            self._storage.schedule_if_stale
        )  # 每轮看一眼存储用量是否该刷新

    # ------------------------------------------------------------ host forwarding
    @property
    def _state(self) -> str:
        return self._local_state if self._local_state is not None else self._host.state

    @_state.setter
    def _state(self, value: str) -> None:
        self._local_state = None
        self._host.state = value

    @property
    def _reason(self) -> str | None:
        return (
            self._local_reason if self._local_state is not None else self._host.reason
        )

    @_reason.setter
    def _reason(self, value: str | None) -> None:
        self._host.reason = value

    @property
    def _orchestrator(self) -> Any:
        return self._host.orchestrator

    @_orchestrator.setter
    def _orchestrator(self, value: Any) -> None:  # 测试换桩用
        self._host.orchestrator = value

    @property
    def _control(self) -> Any:
        return self._host.control

    @_control.setter
    def _control(self, value: Any) -> None:  # 测试换桩用
        self._host.control = value

    @property
    def _native(self) -> Any:
        return self._host.native

    @property
    def _config(self) -> Any:
        return self._host.config

    @property
    def _policy(self) -> Any:
        return self._host.policy_api

    @property
    def _deployment(self) -> Any:
        return self._host.policy

    @property
    def _runtime_options(self) -> dict[str, Any]:
        return self._host.runtime_options

    @property
    def _executor(self) -> Any:
        return self._host.executor

    @property
    def _sandbox(self) -> dict[str, Any] | None:
        return self._host.sandbox

    @property
    def _publish(self) -> dict[str, Any]:
        return self._host.publish

    @property
    def _quarantine(self) -> dict[str, Any] | None:
        return self._host.quarantine

    @property
    def _user_missions(self) -> Any:
        return self._host.deployment

    @property
    def _duties(self) -> Any:
        return self._host.duties

    @property
    def _taskgraph(self) -> Any:
        return self._host.taskgraph

    @property
    def _assurance(self) -> Any:
        return self._host.assurance

    @property
    def _lock(self) -> Any:
        return self._host.lock

    @property
    def _wake(self) -> Any:
        return self._host._wake

    @property
    def _driver(self) -> Any:
        return self._host._driver

    @property
    def _closing(self) -> bool:
        return self._host._closing

    @property
    def _failures(self) -> int:
        return self._host._failures

    @property
    def on_write(self) -> Callable[[], None] | None:
        return self._host.on_write

    @on_write.setter
    def on_write(self, value: Callable[[], None] | None) -> None:
        self._host.on_write = value

    @property
    def quarantined(self) -> bool:
        return self._host.quarantine is not None

    def _h(self, call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """宿主的拒绝换成桌面的同码异常（界面与主对话按 ``OrchestrationRequestError`` 处理）。"""

        try:
            return call(*args, **kwargs)
        except OrchestrationRequestError:
            raise
        except HostRequestError as error:
            raise OrchestrationRequestError(error.code, str(error)) from error

    def _require(self) -> Any:
        return self._h(self._host._require)

    def _refuse_new_work(self, message: str) -> None:
        self._h(self._host._refuse_new_work, message)

    def _refuse_secrets(self, *values: Any) -> None:
        self._h(self._host.refuse_secrets, *values)

    def _secret_values(self) -> tuple[str, ...]:
        return self._host.secret_values()

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        return self._h(self._host._call, method, *args, **kwargs)

    async def _auto_mode(self) -> bool:
        """Whether the person chose auto permission; unreadable means manual."""
        if self._permission_mode_reader is None:
            return False
        try:
            return str(await self._permission_mode_reader()) == "auto"
        except Exception:  # noqa: BLE001 - unreadable mode means manual
            logger.warning(
                "permission mode unreadable; confirmations and planning authority stay manual"
            )
            return False

    def _after_rebuild(self) -> None:
        """重建是一次重连：从已读序号续读通知，重新探测诊断接口。"""
        self._notices.catch_up(self._host.orchestrator.store)
        self._diagnostics_available = self._detect_diagnostics()

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        """Start once. After failure, deactivate/activate with a fresh service and client."""
        if self._state != "created":
            return
        if not self.settings.enabled:
            self._local_state, self._local_reason = "disabled", "编排服务已在配置中关闭"
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
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
                raise RuntimeError(
                    "实际导入的 SDK 与钉版不一致（见 deployment-manifest.json）"
                )
            await self._host.start()
            if self._host.state == "unavailable":
                reason = str(self._host.reason or "")
                if reason.startswith("执行图部署验收门"):
                    write_manifest(
                        self.root,
                        {
                            "schema": MANIFEST_SCHEMA,
                            "distributions": imported,
                            "refused": "taskgraph_acceptance_not_validated",
                            "taskgraph_acceptance": self._host.taskgraph_acceptance_status(),
                        },
                    )
                await self._close_client()
                return
            self._manifest = build_manifest(
                root=self.root,
                deployment=self._host.policy.to_json(),
                settings=asdict(self.settings),
                model=None if self._snapshot is None else self._snapshot.public(),
                sandbox=self._host.sandbox,
                publish=self._host.publish,
            )
            write_manifest(self.root, self._manifest)
            if self._host.quarantine is not None:
                # 根隔离：服务起来了，但不是"可用"——只开非披露诊断与隔离只读分支，不驱动循环
                return
            self._notices.catch_up(
                self._host.orchestrator.store
            )  # from the last read seq
            self._diagnostics_available = self._detect_diagnostics()
            if self._drive_enabled:
                self._host.start_driver()
        except Exception as error:
            logger.exception("orchestration service failed to start")
            await self._fail(f"启动失败：{type(error).__name__}: {error}"[:300])

    async def _fail(self, reason: str) -> None:
        self._local_state, self._local_reason = "unavailable", reason
        await self._host._close_runtime()
        self._host.lock.release()
        await self._close_client()

    async def _close_client(self) -> None:
        client, self._http_client = self._http_client, None
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                logger.debug("provider http client close failed", exc_info=True)

    async def close(self) -> None:
        """Test and orderly-shutdown path only; SIGKILL never reaches it."""

        await self._host.close()
        await self._close_client()
        self._local_state, self._local_reason = "closed", None

    def no_model(self) -> bool:
        """起不来的原因是不是"没配模型"（全新安装的正常情形，不记启动错误）。"""

        return str(self._reason or "").startswith(NO_MODEL) or str(
            self._reason or ""
        ).startswith("未配置模型")

    @property
    def sdk_version(self) -> str:
        return simple_harness.__version__
