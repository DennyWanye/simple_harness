# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Rebuild, drain, recovery state, context and budget defaults, and service status."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Any

import agent_orchestrator
import simple_harness
from agent_orchestrator.api import facade, policies
from agent_orchestrator.deployment.native_pools import (
    CONTEXT_INPUT_LIMITS,
    NATIVE_OUTPUT_TOKENS,
    native_profile_id,
)
from agent_orchestrator.orchestrator import event_handler
from agent_orchestrator.orchestrator.commit_service import GLOBAL_ACCOUNT_PREFIX, global_account_id

from ..provider import ProviderUnavailable, provider_on_client
from ..runtime_profile import (
    DEEPSEEK_COUNTER_MODELS,
    ONLY_DEEPSEEK_REASON,
    THINKING_EFFORT,
    source_runtime_options,
)
from .core import OrchestrationServiceCore
from .support import QUARANTINE_REASON, TERMINAL, WORKSPACE_TOOLS, OrchestrationRequestError

# 与原模块同名的记录器（getLogger 按名字返回同一个对象）；就地定义，ruff 才认得出
# 捕获异常后写日志的写法
logger = logging.getLogger("deskpet.orchestration.service")

#: 第 2～4 批补齐评估处置（H01）：SDK 重启恢复协议降级时，服务不当"可用"——不派发、不收新任务与改要求，
#: 只开读取与诊断；失败的那一步写在 ``status().recovery`` 里，界面照实显示。
DEGRADED_RECOVERY_REASON = "重启恢复没有完成（降级恢复）：任务不派发，只开读取与诊断"


class StatusMixin(OrchestrationServiceCore):
    """Rebuild, drain, recovery state, context and budget defaults, and service status."""

    __slots__ = ()

    async def _rebuild(self) -> None:

        old, self._orchestrator = self._orchestrator, None
        self._control = None
        self._user_missions.bind(None, None)
        self._policy = None
        self._taskgraph = None
        self._assurance = None
        self._quarantine = None
        if old is not None:
            try:
                await old.__aexit__(None, None, None)
            except Exception:
                logger.exception("orchestrator close during rebuild failed")
        self._native = self._build_native()
        self._runtime_options = source_runtime_options(
            self._config,
            self._effective_provider,
            self._snapshot,
            settings=self.settings,
            native=self._native,
            native_test_counter=self._native_test_counter,
            thinking_provider=self._thinking_provider(),
        )
        if not self._runtime_options["profiles"]:
            raise ProviderUnavailable(ONLY_DEEPSEEK_REASON)

        def assemble_startup(orchestrator: Any) -> None:
            self._user_missions.assemble(orchestrator)
            if self._native is not None:
                self._native.bind_orchestrator(orchestrator)

        candidate = event_handler.Orchestrator(
            self._config,
            self._effective_provider,
            owner=self.owner,
            connectors=self._connectors,
            startup_assembly=assemble_startup,
            assurance_root_setup=self._user_missions.assurance_root_setup(),
            **self._runtime_options,
        )
        try:
            await candidate.__aenter__()
            control = facade.MissionControlV1(
                candidate, tenant_id=self.tenant_id, principal=self._principal
            )
            policy = policies.PolicyApi(candidate.commit, self._principal)
        except BaseException as error:
            try:
                await candidate.__aexit__(type(error), error, error.__traceback__)
            except BaseException as cleanup_error:  # noqa: BLE001 - preserve the original start failure
                error.add_note(f"Rebuild resource cleanup failed: {type(cleanup_error).__name__}")
            raise
        # Publish only after enter and facade construction succeeded. A failed
        # candidate leaves no runnable object; the existing driver retries rebuild.
        self._orchestrator = candidate
        self._quarantine = self._root_quarantine(control)
        if self._quarantine is not None:
            # 重建时发现根隔离：同启动时一样，只留门面给非披露诊断，驱动循环就此停下
            self._control = control
            self._state, self._reason = "quarantined", QUARANTINE_REASON
            return
        self._bind_host_duties(candidate)
        self._notices.catch_up(candidate.store)  # U04: a rebuild is a reconnect; resume from seq
        self._control = control
        self._user_missions.bind(candidate, control)
        self._diagnostics_available = self._detect_diagnostics()
        self._policy = policy
        self._taskgraph = self._user_missions.taskgraph
        self._assurance = self._user_missions.assurance

    async def drain(self, timeout: float = 60.0) -> bool:
        """Tests (``drive=False``): run the loop until idle or ``timeout``.  False when it
        did not idle — a turn blocked on an unknown outcome never does."""

        try:
            await asyncio.wait_for(self._orchestrator.run(), timeout=timeout)
            if self._degraded_recovery():
                return True
            await self._host_duties()
            return True
        except TimeoutError:
            return False

    def _recovery_status(self) -> dict[str, Any] | None:
        """``status().recovery``：降级后用记下的那份；否则现读 SDK（读不到为 None）。"""
        if self._recovery is not None:
            return dict(self._recovery)
        if self._orchestrator is None or self._state not in ("available", "degraded"):
            return None
        try:
            return dict(self._orchestrator.recovery_status())
        except Exception:  # noqa: BLE001 - the status view never raises
            logger.exception("recovery status unreadable")
            return None

    def _recovery_isolated(self) -> dict[str, dict[str, Any]]:
        """重启恢复隔离的任务：``{mission_id: {"tables": [...]}}``。唯一来源是 SDK 的
        ``recovery_status()["isolated_missions"]``；读不到当作没有隔离，不抛。"""
        try:
            isolated = dict(self._orchestrator.recovery_status()).get("isolated_missions") or {}
            return {
                str(mission_id): {
                    "tables": [str(name) for name in (item or {}).get("tables") or ()]
                }
                for mission_id, item in dict(isolated).items()
            }
        except Exception:  # noqa: BLE001 - 读不到当作没有隔离
            return {}

    def _degraded_recovery(self) -> bool:
        """H01：读 SDK 的恢复状态；DEGRADED_RECOVERY 时把服务置成 ``degraded_recovery`` 并记下失败步骤。"""
        if self._orchestrator is None:
            return False
        try:
            recovery = dict(self._orchestrator.recovery_status())
        except Exception:  # noqa: BLE001 - 诊断读不到不改变服务状态
            logger.exception("recovery status unreadable")
            return False
        self._recovery = recovery
        if recovery.get("state") != "DEGRADED_RECOVERY":
            return False
        latest = recovery.get("latest")
        step = latest.get("failed_step") if isinstance(latest, Mapping) else None
        self._state = "degraded_recovery"
        self._reason = f"{DEGRADED_RECOVERY_REASON}（失败步骤：{step or '未知'}）"
        return True

    # ------------------------------------------------------------ status
    def _context_profiles(self) -> list[dict[str, Any]]:
        profiles = self._runtime_options.get("profiles", {})

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
                        # A bounded multi-turn allowance, not a charge for unused capacity.
                        "mission_max_tokens": self.settings.default_mission_max_tokens,
                    }
                )
        return rows

    def _thinking_provider(self) -> Any:
        """The thinking-mode provider for the separate thinking pools: the same endpoint,
        model and client as the shared provider, with thinking enabled (reasoning replayed).
        None unless this deployment talks to a DeepSeek model through a real client."""

        snapshot, client = self._snapshot, self._http_client
        if (
            snapshot is None
            or client is None
            or snapshot.requested_model not in DEEPSEEK_COUNTER_MODELS
        ):
            return None
        cached = getattr(self, "_thinking_provider_instance", None)
        if cached is None:
            cached = provider_on_client(
                client,
                snapshot,
                timeout=900.0,
                thinking="enabled",
                reasoning_effort=THINKING_EFFORT,
            )
            self._thinking_provider_instance = cached
        return cached

    def _context_default(self) -> str | None:

        available = {p["profile_id"] for p in self._context_profiles()}
        # The thinking setting picks the pool of a *new* Mission only; an existing Mission keeps
        # the pool (and so the thinking mode) it was frozen on.
        thinking = getattr(self.settings, "thinking", "disabled") == "enabled"
        for selected in (
            native_profile_id(self.settings.context_input_tokens, thinking=thinking),
            native_profile_id(self.settings.context_input_tokens),
        ):
            if selected in available:
                return selected
        return None

    def _global_budget_status(self) -> dict[str, Any]:
        """第 2 批 H11 / 车道 P：本月全局总账（所有任务按建立月份合计）的月份、上限、已用、预留中、
        剩余，设置页据此写说明。当月总账在当月第一个任务建立时才打开；没打开时用量为 0。读不到
        账本（服务没起来）也报月份与配置的月配额。月份与账户都由 SDK 算（``global_account_id``）。"""

        running = self._orchestrator is not None and self._state in ("available", "degraded")
        now = self._orchestrator.store.now if running else time.time()
        limit = int(self.settings.global_monthly_max_tokens)
        row = {
            "month": global_account_id(now).removeprefix(GLOBAL_ACCOUNT_PREFIX),
            "max_tokens": limit,
            "opened": False,
            "used_tokens": 0,
            "reserved_tokens": 0,
            "remaining_tokens": limit,
        }
        pool = self._orchestrator.commit.global_account() if running else None
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

    def _mission_token_default(self, profile_id: str | None = None) -> int:
        selected = profile_id or self._context_default()
        return next(
            (
                p["mission_max_tokens"]
                for p in self._context_profiles()
                if p["profile_id"] == selected
            ),
            self.settings.default_mission_max_tokens,
        )

    def status(self) -> dict[str, Any]:

        active = 0
        if self._orchestrator is not None and self._state in (
            "available",
            "degraded",
            "degraded_recovery",
        ):
            try:
                active = sum(
                    1
                    for m in self._orchestrator.store.list_missions()
                    if str(m.status) not in TERMINAL
                )
            except Exception:  # noqa: BLE001
                active = 0
        return {
            "available": self._state == "available",
            "state": self._state,
            "reason": self._reason,
            "orchestrator_version": agent_orchestrator.__version__,
            "sdk_version": simple_harness.__version__,
            "model": None if self._snapshot is None else self._snapshot.public(),
            "active_missions": active,
            # P3.2: what this deployment really allows, and whether model-written code can
            # run here at all (``off`` until the sandbox probe passes on this machine)
            "allowed_tools": list(self.runtime_tool_names() or WORKSPACE_TOOLS),
            "code_execution": (
                None if self._deployment is None else self._deployment.code_execution
            ),
            "sandbox": dict(self._sandbox or {"ok": False, "reason": "尚未探测"}),
            "publish": dict(self._publish),
            "diagnostics_available": self._diagnostics_available,
            "assurance_available": self._assurance is not None,
            # 第 2 批 A40：保证通道根的非披露诊断；隔离时如实写明，上面的 available 为 False
            "assurance_root": self._assurance_root_status(),
            # H01：SDK 重启恢复协议的只读诊断；降级恢复时上面的 state 为 degraded_recovery
            "recovery": self._recovery_status(),
            "storage_over_warn": bool(self._storage.over_warn),
            "context_profiles": self._context_profiles(),
            "native_plane": (
                self._native.status()
                if self._native is not None
                else {
                    "enabled": True,
                    "available": False,
                    "reason": self._reason or ONLY_DEEPSEEK_REASON,
                }
            ),
            "default_context_profile_id": self._context_default(),
            "mission_budget_defaults": {
                "max_tokens": self._mission_token_default(),
                "max_attempts": self.settings.default_mission_max_attempts,
            },
            "global_budget": self._global_budget_status(),
            "deployment_manifest": self._manifest,
            "owner": self.owner,
        }

    def _assurance_root_status(self) -> dict[str, Any] | None:
        if self._quarantine is not None:
            return dict(self._quarantine)
        if self._control is None or self._state not in (
            "available",
            "degraded",
            "degraded_recovery",
        ):
            return None
        try:
            return dict(self._control.assurance_root_diagnostic())
        except Exception:  # noqa: BLE001 - the status view never raises
            logger.exception("assurance root diagnostic failed")
            return None

    def note_authorization_mode(self, mode: str) -> None:
        """The Host's auto / manual mode governs chat tool effects only (plan §3.7); it is
        recorded for the status view and never decides an orchestration approval."""

        self._authorization_mode = mode

    def runtime_tool_names(self) -> tuple[str, ...]:
        return tuple(self._deployment.allowed_tools) if self._deployment is not None else ()

    # ------------------------------------------------------------ helpers
    def _require(self) -> Any:
        if (
            self._state not in ("available", "degraded", "degraded_recovery")
            or self._control is None
        ):
            raise OrchestrationRequestError(
                "orchestration_unavailable", self._reason or "编排服务不可用"
            )
        return self._control

    def _refuse_new_work(self, message: str) -> None:
        """review P2-1 / H01：循环异常或降级恢复时，停与接管可以，新工作不可以。"""
        if self._state == "degraded":
            raise OrchestrationRequestError("orchestration_degraded", message)
        if self._state == "degraded_recovery":
            raise OrchestrationRequestError(
                "orchestration_degraded_recovery", self._reason or DEGRADED_RECOVERY_REASON
            )
