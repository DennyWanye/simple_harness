# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""``OrchestrationService`` — the process-level owner of the Host's orchestration runtime
(plan 2026-09-11 §3.3; user's Phase3 P3.1, Host direct path).

What it owns: the directory lock, the provider snapshot, the ``Orchestrator`` instance and
its driver loop, the SDK facade (``MissionControlV1``), the deployment manifest and the
Host's door checks.  What it never does: write the orchestration library itself — every
command goes through the facade (Commit Service / Approval API behind it).

Start never raises into the Host lifespan: any failure leaves the service ``unavailable``
with a reason and the chat keeps working.  Correctness never depends on :meth:`close` —
the App ends the backend with SIGKILL; a restart recovers from durable state (a new owner
takes over expired leases; a turn killed inside a model call is shown as unknown and can
be taken over).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .lock import InstanceLock
from .manifest import MANIFEST_SCHEMA, build_manifest, distributions, write_manifest
from .projection import (
    project_approval,
    project_detail,
    project_events,
    ui_state,
)
from .provider import NO_MODEL, ProviderSnapshot, ProviderUnavailable
from .storage_usage import StorageUsage
from agent_orchestrator.deployment.native_pools import CONTEXT_INPUT_LIMITS
from agent_orchestrator.orchestrator.event_handler import ACTION_SETTLED_AFTER_STOP

from .runtime_profile import (
    ONLY_DEEPSEEK_REASON,
    source_runtime_options,
)
from .settings import OrchestrationSettings

logger = logging.getLogger(__name__)

TENANT = "local-desktop"
WORKSPACE_TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list")
KNOWLEDGE_TOOLS = ("knowledge_list", "knowledge_read")
TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
FACADE_CODES = {
    "invalid_request": "invalid_request",
    "conflict": "conflict",
    "not_found": "not_found",
    "secret_rejected": "secret_rejected",
    "integrity_error": "integrity_error",
    "refused": "sdk_refused",
}


class OrchestrationRequestError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _notice_line(row: Mapping[str, Any]) -> str:
    """One line of the main Agent's notice context: a Mission that ended, or an outside
    action of a stopped Mission whose result is known now (一致性补改 H-2)."""
    head = f"- mission_id={row['mission_id']} status={row['status']} goal={row['goal'][:80]!r}"
    settled = row.get("action_settled")
    if settled:
        return (head + f" action_checked={settled['operation']} {settled['target']}"
                f" result={'applied' if settled['applied'] else 'not_applied'}")
    return (head + (f" stop_reason={row['stop_reason']}" if row["stop_reason"] else "")
            + (f" unresolved_actions={row['unresolved_actions']} (outcome unknown; the system keeps checking)"
               if row.get("unresolved_actions") else ""))


def backoff_delay(failures: int, maximum: float) -> float:
    """Seconds to wait after ``failures`` failed rounds in a row.  The exponent is capped:
    ``2.0 ** 1024`` overflows, and the error would end the driver loop (review P2-7)."""

    return min(maximum, 2.0 ** min(max(failures, 0), 16))


PUBLISH_PREFIX = "action:file_publish.publish:"


def _with_publish_sources(criteria: list[str]) -> list[str]:
    """2026-09-29（第 5 批）：每个要发布的文件先得有一步写出它。

    每条 ``action:file_publish.publish:X`` 前补一条 ``file:X``（已有就不补）：它是普通
    内容要求，规划时必须链接到恰好一个步骤（那一步就是 X 的声明产出者），核对时检查文件
    存在，系统发布时只从那一步取。界面和主 Agent 都经这里，确认页上用户看得到这一条。
    """

    present = {str(item).strip() for item in criteria}
    result: list[str] = []
    for item in criteria:
        text = str(item).strip()
        if text.startswith(PUBLISH_PREFIX):
            source = "file:" + text[len(PUBLISH_PREFIX):].strip()
            if source != "file:" and source not in present:
                result.append(source)
                present.add(source)
        result.append(item)
    return result


def _strings(value: Any) -> Iterator[str]:
    """Every string inside a request value (keys included), for the secret check."""

    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _strings(item)


class OrchestrationService:
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
        # HTN 补齐阶段 B 第 1 条：任务结束通知的唯一 Host 记录（主对话卡片、主 Agent 上下文都读它）
        from .notices import MissionNotices

        self._notices = MissionNotices(self.root)
        # 2026-09-25 UI 全量点击：自动模式下每轮"授权本轮规划"都要人点，不点任务就一直卡着。
        # 读当前权限模式（每轮现读；读不到按手动处理，照旧等人点）。
        self._permission_mode_reader = permission_mode_reader
        # 用户任务的部署（安装、建任务、每轮职责）在 SDK 里只有一份（HTN 补齐阶段 A′）；Host 交出
        # 桌面规划世界、认证身份、通知去处与构建指纹，并告诉它"现在是不是自动模式"。
        from agent_orchestrator.deployment.assembly import RootNames, UserMissionDeployment

        from .assurance import host_fingerprint
        from .hierarchical import ROOT_DUTY_PREFIX, ROOT_TASK_PREFIX, ROOT_TYPE, planning_world

        def notify(payload: Mapping[str, Any]) -> None:
            # At-least-once local status transport: the notice is kept (de-duplicated by
            # event id) until the person acknowledges it in the main conversation; the
            # change pump pushes ``mission_changed`` so the cards re-read.
            self._notices.record(payload)
            self.wake()

        self._user_missions = UserMissionDeployment(
            tenant_id=self.tenant_id, principal=self._principal, world_factory=planning_world,
            names=RootNames(ROOT_TYPE, ROOT_TASK_PREFIX, ROOT_DUTY_PREFIX),
            host_fingerprint=host_fingerprint(), notify=notify, wake=lambda: self.wake(),
            taskgraph_ports=taskgraph_deployment)
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
                    {"schema": MANIFEST_SCHEMA, "distributions": imported, "refused": "pin_mismatch"},
                )
                raise RuntimeError("实际导入的 SDK 与钉版不一致（见 deployment-manifest.json）")
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

    async def _fail(self, reason: str) -> None:
        self._state, self._reason = "unavailable", reason
        await self._close_runtime()
        self._lock.release()

    async def _open(self) -> None:
        from agent_orchestrator.api.facade import MissionControlV1
        from agent_orchestrator.api.policies import PolicyApi
        from agent_orchestrator.governance.policies import DeploymentPolicy
        from agent_orchestrator.orchestrator.event_handler import Orchestrator
        from agent_orchestrator.runtime.assembly import OrchestratorConfig

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
        base_tools = (*WORKSPACE_TOOLS, *KNOWLEDGE_TOOLS, "run_tests") if sandboxed else (*WORKSPACE_TOOLS, *KNOWLEDGE_TOOLS)
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
            knobs = {"default_max_output_tokens": 16384, "max_output_tokens_ceiling": 32768}
        self._config = OrchestratorConfig(
            evidence_root=self.root,
            model=self._snapshot.requested_model if self._snapshot is not None else "agent-model",
            max_concurrency=self.settings.max_concurrency,
            max_concurrent_model_calls=self.settings.max_concurrent_model_calls,
            lease_seconds=self.settings.lease_seconds,
            deployment_policy=self._deployment,
            sandbox_executor=self._executor,  # P3.2 D2: required when sandboxed
            task_max_tokens=self.settings.task_max_tokens,  # fixed per-leaf allowance
            **knobs,
        )
        self._effective_provider = provider  # kept for a rebuild after repeated failures
        self._native = self._build_native()
        self._runtime_options = source_runtime_options(
            self._config, provider, self._snapshot,
            settings=self.settings, native=self._native,
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

        self._orchestrator = Orchestrator(
            self._config, provider, owner=self.owner, connectors=self._connectors,
            startup_assembly=assemble_startup,
            assurance_root_setup=self._user_missions.assurance_root_setup(),
            **self._runtime_options,
        )
        await self._orchestrator.__aenter__()
        self._bind_host_duties(self._orchestrator)
        self._notices.backfill(self._orchestrator.store)
        self._taskgraph = self._user_missions.taskgraph
        self._assurance = self._user_missions.assurance
        self._control = MissionControlV1(
            self._orchestrator, tenant_id=self.tenant_id, principal=self._principal
        )
        self._user_missions.bind(self._orchestrator, self._control)
        self._diagnostics_available = self._detect_diagnostics()
        self._policy = PolicyApi(self._orchestrator.commit, self._principal)

    def _publish_connector(self) -> Any:
        """The file publish connector, but only for a directory the user really authorised.

        Three things must hold before anything can be published (P3.2 plan D9, P32-14): the
        user named a directory, it exists, and it can carry a hard link — the connector's
        only atomic commit point.  A volume that cannot (exFAT, some network shares) is
        refused *here*, at authorisation time, rather than turning into an UNKNOWN action
        later.  Without all three the connector is not enabled at all, so a Mission may not
        even carry a publish criterion.
        """

        from agent_orchestrator.runtime.connectors_publish import FilePublishConnector

        configured = (self.settings.publish_dir or "").strip()
        if not configured:
            self._publish = {"enabled": False, "reason": "未授权发布目录"}
            return None
        root = Path(configured).expanduser()
        if not root.is_dir():
            self._publish = {"enabled": False, "root": str(root), "reason": "授权目录不存在"}
            return None
        if not FilePublishConnector.supports_hardlinks(root):
            self._publish = {
                "enabled": False,
                "root": str(root),
                "reason": "该卷不支持硬链接，无法原子发布",
            }
            return None
        self._publish = {"enabled": True, "root": str(root)}
        return FilePublishConnector(root, self.root / "connectors" / "file_publish")

    async def _probe_sandbox(self) -> dict[str, Any]:
        """Run the SDK's capability probe once per environment (P3.2 plan D9, review P2-9).

        A machine without a usable interpreter (a frozen build) or without seatbelt simply
        gets ``ok=False`` and a reason; the deployment then runs nothing model-written at
        all.  The last result is written next to the library for the manifest and for
        support, but it is **not** trusted as a pass: the SDK only accepts an executor whose
        probe ran in this process against this very environment digest, so a start always
        proves isolation again rather than believing a file (plan review P2-9 asked for a
        cache; caching the *decision* would mean shipping "it worked once" — registered in
        the P3.2 journal as a deliberate deviation).
        """

        from agent_orchestrator.runtime.sandbox import (
            SandboxUnavailable,
            SeatbeltExecutor,
        )
        from agent_orchestrator.runtime.sandbox import probe_sandbox as run_probe

        self._executor = None
        if getattr(sys, "frozen", False):
            # This executable starts the backend; it does not interpret -I/-c.
            # Probing it would start another backend without this user-data env.
            return {"ok": False, "reason": "frozen_backend_is_not_a_python_interpreter"}
        try:
            executor = SeatbeltExecutor.for_interpreter(sys.executable)
        except SandboxUnavailable as error:
            return {"ok": False, "reason": str(error)[:300]}
        try:
            report = await run_probe(executor)
        except Exception as error:  # noqa: BLE001 - a probe that cannot run is not a pass
            logger.warning("sandbox probe failed to run: %s", error)
            return {"ok": False, "reason": f"{type(error).__name__}: {error}"[:300]}
        result = report.to_json()
        try:
            (self.root / "sandbox-probe.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:  # a report that cannot be written changes nothing about the run
            pass
        if report.ok:
            self._executor = executor
        return result

    async def _close_runtime(self) -> None:
        orchestrator, self._orchestrator = self._orchestrator, None
        self._control = None
        self._user_missions.bind(None, None)
        self._policy = None
        self._taskgraph = None
        self._assurance = None
        if orchestrator is not None:
            try:
                await orchestrator.__aexit__(None, None, None)
            except Exception:
                logger.exception("orchestrator close failed")
        client, self._http_client = self._http_client, None
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                logger.debug("provider http client close failed")

    async def close(self) -> None:
        """Test and orderly-shutdown path only; SIGKILL never reaches it."""

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
        self._lock.release()
        self._state = "closed"

    # ------------------------------------------------------------ driver loop
    def wake(self) -> None:
        self._wake.set()
        if self.on_write is not None:
            try:
                self.on_write()
            except Exception:  # noqa: BLE001
                logger.debug("change pump poke failed")

    def _tick(self) -> float:
        try:
            store = self._orchestrator.store
            active = [m for m in store.list_missions() if str(m.status) not in TERMINAL]
        except Exception:  # noqa: BLE001
            return self.settings.tick_active_seconds
        if not active:
            return self.settings.tick_idle_seconds
        if all(store.waiting_on(m.id) for m in active):
            return self.settings.tick_waiting_seconds  # only people are awaited
        if self._quiet_round:
            # NEXT-TG-1.0 2B (real run 2026-09-28): two old Missions waiting on
            # nothing the loop can do kept the 2 s tick, and every tick re-ran the
            # whole recovery: a core at 100% with nothing written. A round that left
            # no durable change waits like a person-only wait; a write wakes us.
            return self.settings.tick_waiting_seconds
        return self.settings.tick_active_seconds

    def _note_quiet_round(self) -> None:
        reader = getattr(self._orchestrator, "durable_watermark", None)
        if reader is None:
            self._quiet_round = False
            return
        try:
            mark = reader()
        except Exception:  # noqa: BLE001 - unreadable means "assume busy"
            self._quiet_round, self._last_watermark = False, None
            return
        self._quiet_round = mark == self._last_watermark
        self._last_watermark = mark

    async def _host_duties(self) -> None:
        """The Host's own per-round work: policies, grants, TaskGraph enables, confirmations."""
        self._project_assurance_policies()
        await self._auto_authorize_planning()
        await self._auto_confirm_content_completion()

    def _bind_host_duties(self, orchestrator: Any) -> None:
        """Run the Host duties between loop cycles too, not only after ``run()`` returns.

        NEXT-TG-1.0 2A.1g (2026-09-27): ``run()`` stays in its waiting branch while any
        turn anywhere is in flight, so one Mission's long model turn held every other
        Mission's planning grant; the new Mission sat in planning for eleven minutes
        with nothing written. The SDK now calls these duties between cycles.
        """
        if hasattr(orchestrator, "set_between_cycles"):
            orchestrator.set_between_cycles(
                self._host_duties, every_seconds=self.settings.tick_active_seconds
            )

    async def _drive(self) -> None:
        while not self._closing:
            try:
                if self._orchestrator is None:
                    await self._rebuild()
                await self._orchestrator.run()
                await self._host_duties()
                self._note_quiet_round()
                self._storage.schedule_if_stale()
                self._failures = 0
                if self._state == "degraded":
                    self._state, self._reason = "available", None
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._failures += 1
                logger.exception("orchestrator run failed (%s in a row)", self._failures)
                if self._failures >= self.settings.degraded_after_failures:
                    self._state, self._reason = "degraded", f"编排循环连续失败：{error}"[:300]
                if (
                    self._orchestrator is not None
                    and self._failures % self.settings.rebuild_after_failures == 0
                ):
                    try:
                        await self._rebuild()
                    except Exception as rebuild_error:
                        logger.exception("orchestrator rebuild failed")
                        self._state = "degraded"
                        self._reason = f"编排循环重建失败：{rebuild_error}"[:300]
                await asyncio.sleep(backoff_delay(self._failures, self.settings.backoff_max_seconds))
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._tick())
            except TimeoutError:
                pass
            self._wake.clear()

    # ------------------------------------------------------------ native plane (RP-E3)
    def _native_skill_tools(self) -> tuple[str, ...]:
        """The Skill tools this deployment's native pools serve."""
        from agent_orchestrator.governance.policies import SKILL_TOOL_NAMES

        return tuple(SKILL_TOOL_NAMES)

    def _build_native(self) -> Any:
        """The Host's native-plane composition (the only execution plane since 2026-09-30)."""
        from .native_plane import build_native_pools

        if self._native_test_counter is not None:
            meter_factory = self._native_test_counter.meter_factory
        else:
            from agent_orchestrator.runtime.deepseek_meter import deepseek_meter_binding
            meter_factory = deepseek_meter_binding
        models_dir = None
        try:
            from paths import user_models_dir
            models_dir = Path(user_models_dir())
        except Exception:  # noqa: BLE001 - no model directory means lexical-only, reported
            models_dir = None
        return build_native_pools(
            tenant_id=self.tenant_id, principal_id=str(self._principal.principal_id),
            allowed_tools=tuple(self._deployment.allowed_tools) if self._deployment is not None else (),
            models_dir=models_dir, meter_factory=meter_factory, script_executor=self._executor,
        )

    def _native_profile_ids(self) -> list[str]:
        from agent_orchestrator.deployment.native_pools import is_native_profile
        return [key for key in self._runtime_options.get("profiles", {}) if is_native_profile(key)]

    def _native_pool(self, profile_id: str | None) -> Any:
        self._require()
        native = self._native_profile_ids()
        if self._native is None or not native:
            raise OrchestrationRequestError("native_plane_unavailable", ONLY_DEEPSEEK_REASON)
        selected = profile_id or self._context_default()
        if selected not in native:
            raise OrchestrationRequestError("invalid_request", "所选运行配置不在原生运行平面上")
        return self._orchestrator.assembled.pool(selected)

    async def runtime_plane(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """One ``HostRequest`` of the SDK runtime plane (``agent_*`` verbs) → one ``HostResponse``.

        The caller is the Host's principal, derived here and never from the body; the SDK
        answers every failure as its typed ``Error`` DTO inside the response."""
        if not isinstance(request, Mapping):
            raise OrchestrationRequestError("invalid_request", "请求必须是一个对象")
        body = dict(request)
        profile_id = body.pop("profile_id", None)
        if profile_id is not None and not isinstance(profile_id, str):
            raise OrchestrationRequestError("invalid_request", "profile_id 必须是字符串")
        self._refuse_secrets(body)
        from simple_harness.agents.arp.shared_catalogue import SKILL_READ_VERBS, SKILL_WRITE_VERBS

        if body.get("verb") in SKILL_READ_VERBS | SKILL_WRITE_VERBS:
            # NEXT-TG-1.0 §11: Skills have one authority for every pool; no per-pool install
            return await self.skill_request(body)
        pool = self._native_pool(profile_id)
        from simple_harness.api.runtime_plane import RuntimePlaneService

        response = await RuntimePlaneService(pool.runtime).handle(body, caller=self._native.control_caller(body))
        self.wake()
        return response

    # ---- the shared Skill catalogue (NEXT-TG-1.0 §11; logic in skill_catalogue.py / the SDK) ----

    async def _skill_call(self, call: Any) -> dict[str, Any]:
        from simple_harness.agents.arp.errors import ArpError

        from .skill_catalogue import SkillCatalogueRefused

        self._require()
        try:
            result = call()
            return await result if hasattr(result, "__await__") else result
        except SkillCatalogueRefused as error:
            raise OrchestrationRequestError(error.code, str(error)) from error
        except ArpError as error:
            raise OrchestrationRequestError("refused", f"{error.code}: {error}") from error

    async def skill_catalogue(self, request: Mapping[str, Any]) -> dict[str, Any]:
        from .skill_catalogue import overview

        del request
        return await self._skill_call(lambda: overview(self))

    async def skill_install_file(self, request: Mapping[str, Any]) -> dict[str, Any]:
        from .skill_catalogue import install_file

        if not isinstance(request, Mapping):
            raise OrchestrationRequestError("invalid_request", "请求必须是一个对象")
        return await self._skill_call(lambda: install_file(self, request))

    async def skill_lifecycle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        from .skill_catalogue import lifecycle

        if not isinstance(request, Mapping):
            raise OrchestrationRequestError("invalid_request", "请求必须是一个对象")
        return await self._skill_call(lambda: lifecycle(self, request))

    async def skill_evaluate(self, request: Mapping[str, Any]) -> dict[str, Any]:
        from .skill_catalogue import evaluate

        if not isinstance(request, Mapping):
            raise OrchestrationRequestError("invalid_request", "请求必须是一个对象")
        return await self._skill_call(lambda: evaluate(self, request))

    async def skill_admit(self, request: Mapping[str, Any]) -> dict[str, Any]:
        from .skill_catalogue import admit

        if not isinstance(request, Mapping):
            raise OrchestrationRequestError("invalid_request", "请求必须是一个对象")
        return await self._skill_call(lambda: admit(self, request))

    async def skill_request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        from .skill_catalogue import request as skill_request

        if not isinstance(request, Mapping):
            raise OrchestrationRequestError("invalid_request", "请求必须是一个对象")
        body = dict(request)
        body.pop("profile_id", None)
        self._refuse_secrets(body)
        return await self._skill_call(lambda: skill_request(self, body))

    @staticmethod
    def _evaluation_pin(request: Mapping[str, Any]) -> Any:
        from simple_harness.agents.arp.pins import Pin

        try:
            pin = Pin.from_json(dict(request.get("evaluation_ref") or {}))
        except Exception as error:  # noqa: BLE001 - malformed reference
            raise OrchestrationRequestError("invalid_request", "evaluation_ref 不是有效的评估引用") from error
        if pin.kind != "evaluation":
            raise OrchestrationRequestError("invalid_request", "evaluation_ref 必须指向一条 Skill 评估")
        return pin

    def skill_evaluation_mission(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Create (idempotently) the original Assurance Mission of one Skill evaluation: its
        idempotency key is the evaluation's own key, which is what later ties an acceptance
        certificate back to the evaluation."""
        from simple_harness.agents.arp.assurance_acceptance import evaluation_mission_key

        if not isinstance(request, Mapping):
            raise OrchestrationRequestError("invalid_request", "请求必须是一个对象")
        pin = self._evaluation_pin(request)
        key = evaluation_mission_key(pin)
        body = {k: v for k, v in request.items() if k not in ("evaluation_ref", "idempotency_key")}
        body["idempotency_key"] = key
        receipt = self.create_mission(body)
        return {**receipt, "idempotency_key": key, "evaluation_ref": pin.to_json()}

    def skill_evaluation_dispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Record which Mission and which task an evaluation was dispatched to (once).  The
        Mission must be this tenant's and must be the evaluation's original Mission; the
        task must belong to it.  The record never marks the evaluation as passed."""
        from simple_harness.agents.arp.assurance_acceptance import evaluation_mission_key

        if not isinstance(request, Mapping):
            raise OrchestrationRequestError("invalid_request", "请求必须是一个对象")
        pin = self._evaluation_pin(request)
        mission_id, task_id, command_id = request.get("mission_id"), request.get("task_id"), request.get("command_id")
        for name, value in (("mission_id", mission_id), ("task_id", task_id), ("command_id", command_id)):
            if not isinstance(value, str) or not value.strip():
                raise OrchestrationRequestError("invalid_request", f"{name} 不能为空")
        control = self._require()
        mission = control._mission(mission_id)
        if mission.idempotency_key != evaluation_mission_key(pin):
            raise OrchestrationRequestError("invalid_request", "这个 Mission 不是该评估的原始 Mission")
        task = self._orchestrator.store.get_task(task_id)
        if task is None or task.mission_id != mission_id:
            raise OrchestrationRequestError("not_found", "任务不属于这个 Mission")
        # the evaluation belongs to the shared catalogue's owner (the only pool that admits)
        pool = self._native_pool(self._native.catalogue_owner_id)
        caller = self._native.control_caller({"command_id": command_id, "evaluation_ref": pin.to_json(), "mission_id": mission_id, "task_id": task_id})
        body = pool.runtime.arp.lifecycle.record_evaluation_dispatch(pin, mission_id=mission_id, task_id=task_id, caller=caller, command_id=command_id)
        self.wake()
        return dict(body)

    def _project_assurance_policies(self) -> int:
        """After every loop round: the per-Scope check policies an assured Mission needs
        before its content reviews (the SDK's ``DeploymentDuties.project_check_policies``)."""
        if self._assurance is None:
            return 0
        try:
            return self._duties.project_check_policies()
        except Exception:  # noqa: BLE001 - never stops the loop; retried next round
            logger.exception("assurance check policy projection failed")
            return 0

    async def _auto_mode(self) -> bool:
        """Whether the person chose auto permission; unreadable means manual."""
        if self._permission_mode_reader is None:
            return False
        try:
            return str(await self._permission_mode_reader()) == "auto"
        except Exception:  # noqa: BLE001 - unreadable mode means manual
            logger.warning("permission mode unreadable; confirmations and planning authority stay manual")
            return False

    async def _auto_confirm_content_completion(self) -> int:
        """Auto permission mode: the SDK confirms content-only completion requirements."""
        return self._duties.auto_confirm_content_completion(auto=await self._auto_mode())

    async def _auto_authorize_planning(self) -> int:
        """Auto permission mode: the SDK issues each pending planning authority."""
        issued = self._duties.auto_authorize_planning(auto=await self._auto_mode())
        return issued

    async def _rebuild(self) -> None:
        from agent_orchestrator.api.facade import MissionControlV1
        from agent_orchestrator.api.policies import PolicyApi
        from agent_orchestrator.orchestrator.event_handler import Orchestrator

        old, self._orchestrator = self._orchestrator, None
        self._control = None
        self._user_missions.bind(None, None)
        self._policy = None
        self._taskgraph = None
        self._assurance = None
        if old is not None:
            try:
                await old.__aexit__(None, None, None)
            except Exception:
                logger.exception("orchestrator close during rebuild failed")
        self._native = self._build_native()
        self._runtime_options = source_runtime_options(
            self._config, self._effective_provider, self._snapshot,
            settings=self.settings, native=self._native,
            native_test_counter=self._native_test_counter,
            thinking_provider=self._thinking_provider(),
        )
        if not self._runtime_options["profiles"]:
            raise ProviderUnavailable(ONLY_DEEPSEEK_REASON)
        def assemble_startup(orchestrator: Any) -> None:
            self._user_missions.assemble(orchestrator)
            if self._native is not None:
                self._native.bind_orchestrator(orchestrator)

        candidate = Orchestrator(
            self._config, self._effective_provider, owner=self.owner, connectors=self._connectors,
            startup_assembly=assemble_startup,
            assurance_root_setup=self._user_missions.assurance_root_setup(),
            **self._runtime_options,
        )
        try:
            await candidate.__aenter__()
            control = MissionControlV1(
                candidate, tenant_id=self.tenant_id, principal=self._principal
            )
            policy = PolicyApi(candidate.commit, self._principal)
        except BaseException as error:
            try:
                await candidate.__aexit__(type(error), error, error.__traceback__)
            except BaseException as cleanup_error:  # noqa: BLE001 - preserve the original start failure
                error.add_note(f"Rebuild resource cleanup failed: {type(cleanup_error).__name__}")
            raise
        # Publish only after enter and facade construction succeeded. A failed
        # candidate leaves no runnable object; the existing driver retries rebuild.
        self._orchestrator = candidate
        self._bind_host_duties(candidate)
        self._notices.backfill(candidate.store)
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
            await self._host_duties()
            return True
        except TimeoutError:
            return False

    # ------------------------------------------------------------ status
    def _context_profiles(self) -> list[dict[str, Any]]:
        profiles = self._runtime_options.get("profiles", {})
        from agent_orchestrator.deployment.native_pools import native_profile_id

        rows = []
        for tokens in CONTEXT_INPUT_LIMITS:
            for identifier in (native_profile_id(tokens), native_profile_id(tokens, thinking=True)):
                if identifier not in profiles:
                    continue
                rows.append({
                    "profile_id": identifier,
                    "max_input_tokens": profiles[identifier].context_policy.input_budget(),
                    "default_max_output_tokens": 8192,
                    "max_output_tokens_ceiling": 32768,
                    # A bounded multi-turn allowance, not a charge for unused capacity.
                    "mission_max_tokens": self.settings.default_mission_max_tokens,
                })
        return rows

    def _thinking_provider(self) -> Any:
        """The thinking-mode provider for the separate thinking pools: the same endpoint,
        model and client as the shared provider, with thinking enabled (reasoning replayed).
        None unless this deployment talks to a DeepSeek model through a real client."""

        from .runtime_profile import DEEPSEEK_COUNTER_MODELS

        snapshot, client = self._snapshot, self._http_client
        if snapshot is None or client is None or snapshot.requested_model not in DEEPSEEK_COUNTER_MODELS:
            return None
        cached = getattr(self, "_thinking_provider_instance", None)
        if cached is None:
            from .provider import provider_on_client

            from .runtime_profile import THINKING_EFFORT

            cached = provider_on_client(
                client, snapshot, timeout=900.0,
                thinking="enabled",
                reasoning_effort=THINKING_EFFORT,
            )
            self._thinking_provider_instance = cached
        return cached

    def _context_default(self) -> str | None:
        from agent_orchestrator.deployment.native_pools import native_profile_id

        available = {p["profile_id"] for p in self._context_profiles()}
        # The thinking setting picks the pool of a *new* Mission only; an existing Mission keeps
        # the pool (and so the thinking mode) it was frozen on.
        thinking = getattr(self.settings, "thinking", "disabled") == "enabled"
        for selected in (native_profile_id(self.settings.context_input_tokens, thinking=thinking),
                         native_profile_id(self.settings.context_input_tokens)):
            if selected in available:
                return selected
        return None

    def _mission_token_default(self, profile_id: str | None = None) -> int:
        selected = profile_id or self._context_default()
        return next((p["mission_max_tokens"] for p in self._context_profiles()
                     if p["profile_id"] == selected), self.settings.default_mission_max_tokens)

    def status(self) -> dict[str, Any]:
        import agent_orchestrator
        import simple_harness

        active = 0
        if self._orchestrator is not None and self._state in ("available", "degraded"):
            try:
                active = sum(
                    1 for m in self._orchestrator.store.list_missions() if str(m.status) not in TERMINAL
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
            "storage_over_warn": bool(self._storage.over_warn),
            "context_profiles": self._context_profiles(),
            "native_plane": (
                self._native.status() if self._native is not None
                else {"enabled": True, "available": False, "reason": self._reason or ONLY_DEEPSEEK_REASON}
            ),
            "default_context_profile_id": self._context_default(),
            "mission_budget_defaults": {
                "max_tokens": self._mission_token_default(),
                "max_attempts": self.settings.default_mission_max_attempts,
            },
            "deployment_manifest": self._manifest,
            "owner": self.owner,
        }

    def note_authorization_mode(self, mode: str) -> None:
        """The Host's auto / manual mode governs chat tool effects only (plan §3.7); it is
        recorded for the status view and never decides an orchestration approval."""

        self._authorization_mode = mode

    def runtime_tool_names(self) -> tuple[str, ...]:
        return tuple(self._deployment.allowed_tools) if self._deployment is not None else ()

    # ------------------------------------------------------------ helpers
    def _require(self) -> Any:
        if self._state not in ("available", "degraded") or self._control is None:
            raise OrchestrationRequestError("orchestration_unavailable", self._reason or "编排服务不可用")
        return self._control

    def _deploy(self, method: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Create through the SDK's one user-Mission deployment (root and TaskGraph binding in
        the same transaction), translating facade refusals like ``_call``."""
        from agent_orchestrator.api.facade import FacadeError

        control = self._require()
        try:
            return dict(getattr(self._user_missions, method)(self._orchestrator, control, body))
        except FacadeError as error:
            raise OrchestrationRequestError(FACADE_CODES.get(error.code, error.code), str(error)) from error

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        from agent_orchestrator.api.facade import FacadeError

        control = self._require()
        try:
            return getattr(control, method)(*args, **kwargs)
        except FacadeError as error:
            raise OrchestrationRequestError(FACADE_CODES.get(error.code, error.code), str(error)) from error

    def _secret_values(self) -> tuple[str, ...]:
        return () if self._snapshot is None else (self._snapshot.api_key,)

    def _refuse_secrets(self, *values: Any) -> None:
        """Every string a person sends in (review P1-3): generic key patterns *and* the
        provider's own key, whatever its shape."""

        from agent_orchestrator.observability.secrets import find_secrets

        for text in _strings(values):
            if find_secrets(text, extra=self._secret_values()):
                raise OrchestrationRequestError("secret_rejected", "内容里有像密钥的文本，未接受")

    def _check_criteria(self, criteria: list[str]) -> None:
        """What this deployment can really do with these requirements: ``pytest:`` needs the
        isolated environment, ``action:`` needs its connector enabled.  One check for creating
        a Mission and for amending its requirements (阶段 E)."""
        deployment = self._deployment
        if any(c.strip().startswith("pytest:") for c in criteria) and not (
            deployment is not None and deployment.code_execution == "sandboxed"
        ):
            raise OrchestrationRequestError(
                "local_tests_disabled",
                "本机没有可用的隔离环境：成功条件不能使用 pytest:"
                + (f"（{self._sandbox.get('reason')}）" if (self._sandbox or {}).get("reason") else ""),
            )
        enabled = set(getattr(deployment, "enabled_connectors", ()) or ())
        for criterion in criteria:
            text = criterion.strip()
            if not text.startswith("action:"):
                continue
            connector = text[len("action:") :].partition(".")[0].strip()
            if not enabled:
                raise OrchestrationRequestError(
                    "action_criteria_disabled", "这个部署没有启用真实动作：成功条件不能使用 action:"
                )
            if connector not in enabled:
                reason = self._publish.get("reason") if connector == "file_publish" else None
                raise OrchestrationRequestError(
                    "action_criteria_disabled",
                    f"连接器 {connector} 未启用" + (f"：{reason}" if reason else ""),
                )

    def _door(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """The Host's own checks before the facade (plan §3.3 门口检查)."""

        body = dict(request)
        goal = body.get("goal")
        criteria = body.get("success_criteria")
        if not isinstance(goal, str) or not goal.strip():
            raise OrchestrationRequestError("invalid_request", "任务目标不能为空")
        if (
            not isinstance(criteria, list)
            or not criteria
            or not all(isinstance(c, str) and c.strip() for c in criteria)
        ):
            raise OrchestrationRequestError("invalid_request", "成功条件不能为空，每行一条")
        # every string of the request — stop conditions and the workspace seed
        # included, not only the goal and the criteria (review P1-3)
        self._refuse_secrets(body)
        self._check_criteria(criteria)
        body["success_criteria"] = _with_publish_sources(criteria)
        # No Mission without bounds (native run 2026-09-12, adjudication C): with a null
        # budget a real Planner invents Task budgets far below one model turn and the
        # Mission cannot succeed.  A blank item takes the deployment default; an item the
        # person gave stays as written; a non-positive one is refused.  Filled here, before
        # the facade, so the persisted receipt's spec hash includes the defaults.
        # Default selection applies only to new Missions. Old blank-request retries
        # must preserve their original charter and budget across a source upgrade.
        found = self._orchestrator.store.find_mission(
            self.tenant_id, str(body.get("idempotency_key", "")),
        ) if self._orchestrator is not None else None
        existing = found[0] if found else None
        profiles = self._context_profiles()
        if "runtime_profile_id" in body:
            selected = body["runtime_profile_id"]
            if not isinstance(selected, str) or not any(p["profile_id"] == selected for p in profiles):
                raise OrchestrationRequestError("invalid_request", "所选上下文配置当前不可用")
        elif existing is not None:
            selected = (existing.final_report or {}).get("runtime_profile_id")
            if selected is not None:
                body["runtime_profile_id"] = selected
        else:
            selected = self._context_default()
            if selected is not None:
                body["runtime_profile_id"] = selected
        token_default = (existing.budget.max_tokens if existing is not None
                         else self._mission_token_default(selected))
        attempt_default = (existing.budget.max_attempts if existing is not None
                           else self.settings.default_mission_max_attempts)
        budget = body.get("budget")
        if budget is None:
            budget = {}
        if not isinstance(budget, Mapping):
            raise OrchestrationRequestError("invalid_request", "预算必须是一个对象")
        budget = dict(budget)
        for name, default in (
            ("max_tokens", token_default),
            ("max_attempts", attempt_default),
        ):
            value = budget.get(name)
            if value is None:
                budget[name] = default
            elif isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise OrchestrationRequestError("invalid_request", f"预算 {name} 必须是正整数")
        body["budget"] = budget
        return body

    # ------------------------------------------------------------ commands
    def create_mission(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self._require()
        if self._state == "degraded":  # review P2-1: stop and take over yes, new work no
            raise OrchestrationRequestError(
                "orchestration_degraded", "编排循环目前不正常，暂不接受新的 Mission；已有的仍可取消或接管"
            )
        receipt = self._deploy("create_mission", self._door(request))
        return {"mission_id": receipt["mission_id"], "created": receipt["created"], "spec_hash": receipt["spec_hash"]}

    def create_mission_with_sources(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """The UI's atomic batch; never create and then register in separate transactions."""
        self._require()
        if self._state == "degraded":
            raise OrchestrationRequestError("orchestration_degraded", "编排循环异常，暂不接受新 Mission")
        if set(request) != {"mission", "sources"} or not isinstance(request["mission"], Mapping):
            raise OrchestrationRequestError("invalid_request", "需要 mission 与 sources 原子批次")
        self._refuse_secrets(request)
        receipt = self._deploy("create_mission_with_sources", {
            "mission": self._door(request["mission"]), "sources": request["sources"],
        })
        return dict(receipt)

    def planning_authorization(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self._require()
        self._refuse_secrets(request)
        # Planning delegation is a separate authority from the TaskGraph kernel.
        # Enabling the kernel remains an explicit authenticated internal command;
        # do not turn every active planning grant into a durable policy binding.
        receipt = self._call("planning_authorization", dict(request))
        self.wake()
        return dict(receipt)

    def answer_planning_question(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self._require()
        self._refuse_secrets(request)
        receipt = self._call("answer_planning_question", dict(request))
        self.wake()
        return dict(receipt)

    def amend_requirements(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """The user amends a running Mission's requirements (add / rewrite / remove entries).
        The same door checks as creating a Mission; one transaction in the SDK facade."""
        self._require()
        if self._state == "degraded":
            raise OrchestrationRequestError("orchestration_degraded", "编排循环异常，暂不接受改要求")
        self._refuse_secrets(request)
        changes = [dict(item) for item in request.get("changes") or () if isinstance(item, Mapping)]
        statements = [str(item.get("statement") or "").strip() for item in changes if item.get("statement")]
        self._check_criteria(statements)
        # 新增"发布某文件"时，先得有一条"写出这个文件"的要求（与建任务同一条规矩）
        detail = self._call("snapshot", str(request.get("mission_id") or ""))
        workspace = (detail.get("snapshot") or {}).get("operation_workspace") or {}
        current = {str(item.get("id")): str(item.get("statement") or "").strip()
                   for item in workspace.get("criteria") or ()}
        added: list[str] = []
        for item in changes:  # 改完之后的要求清单
            if item.get("op") == "add":
                added.append(str(item.get("statement") or "").strip())
            elif item.get("op") == "remove":
                current.pop(str(item.get("criterion_id")), None)
            elif str(item.get("criterion_id")) in current:
                current[str(item.get("criterion_id"))] = str(item.get("statement") or "").strip()
        resulting = [*current.values(), *added]
        # 同一个函数：每个要发布的文件先得有一条"写出它"的要求；缺的补成新增条目
        sources = [{"op": "add", "statement": source}
                   for source in _with_publish_sources(resulting) if source not in resulting]
        receipt = self._call("amend_requirements", {**dict(request), "changes": [*sources, *changes]})
        self.wake()
        return dict(receipt)

    def method_library(self) -> dict[str, Any]:
        """The library of reusable methods (precedents promoted from delivered Missions)."""
        self._require()
        return dict(self._call("list_method_library"))

    def retire_library_entry(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Retire one library entry on the user's word; one transaction in the SDK facade."""
        self._require()
        self._refuse_secrets(request)
        return dict(self._call("retire_library_entry", dict(request)))

    def approve_operation_completion_spec(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Confirm requirements through the existing authenticated SDK facade."""
        self._require()
        self._refuse_secrets(request)
        receipt = self._call("approve_operation_completion_spec", dict(request))
        self.wake()
        return dict(receipt)

    def submit_operation_intent(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Explicit operation submission; caller identity stays in the SDK facade."""
        self._require()
        if self._state == "degraded":
            raise OrchestrationRequestError("orchestration_degraded", "编排循环异常，暂不接受新操作")
        self._refuse_secrets(request)
        receipt = self._call("submit_operation_intent", dict(request))
        self.wake()
        return dict(receipt)

    def operation_intent_status(self, intent_id: str) -> dict[str, Any]:
        self._require()
        return dict(self._call("operation_intent_status", intent_id))

    def source_command(self, operation: str, request: Mapping[str, Any]) -> dict[str, Any]:
        """Explicit IPC verbs, using the same facade's fixed authenticated principal."""
        methods = {"register": "register_source", "supersede": "supersede_source", "revoke": "revoke_source"}
        if operation not in methods:
            raise OrchestrationRequestError("invalid_request", "未知来源操作")
        self._refuse_secrets(request)
        result = self._call(methods[operation], dict(request))
        self.wake()
        return dict(result)

    def cancel_mission(self, mission_id: str) -> dict[str, Any]:
        result = self._call("cancel", mission_id)
        self.wake()
        return {"mission_id": result["mission_id"], "status": result["status"]}

    def decide(self, request_id: str, decision: str, **fields: str) -> dict[str, Any]:
        self._refuse_secrets(fields)
        result = self._call("decide", request_id, decision, **fields)
        self.wake()
        return result

    def takeover(self, task_id: str, action: str, *, basis: str, note: str = "") -> dict[str, Any]:
        self._refuse_secrets(basis, note)
        result = self._call("takeover", task_id, action, basis=basis, note=note)
        self.wake()
        return result

    def resolve_unknown(self, action_key: str, outcome: str, *, basis: str) -> dict[str, Any]:
        """A person's ruling on an action nothing else can settle (阶段 B 裁决第 3 类)."""
        self._refuse_secrets(basis)
        result = self._call("resolve_unknown", action_key, outcome=outcome, basis=basis)
        self.wake()
        return result

    def comment(self, target_id: str, text: str) -> dict[str, Any]:
        self._refuse_secrets(text)
        result = self._call("comment", target_id, text)
        self.wake()
        return result

    # ------------------------------------------------------------ mission notices
    def pending_notices(self, _body: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Notices the person has not acknowledged yet, newest last, with the Mission's
        current status (a notice names the final event; the status is read now)."""

        from .chat_tool import _STATUS_ZH

        rows = []
        for notice in self._notices.pending():
            mission = self._orchestrator.store.get_mission(notice["mission_id"])
            if mission is None:
                continue
            status = str(getattr(mission.status, "value", mission.status))
            row = {
                **notice,
                "status": status,
                "status_zh": _STATUS_ZH.get(status, status),
                "goal": str(mission.goal or "")[:200],
                "stop_reason": None if mission.stop_reason is None else str(mission.stop_reason),
                "unresolved_actions": len((mission.final_report or {}).get("unresolved_actions") or ()),
            }
            # 一致性补改 H-2：通知的事件若是"停下后对外操作有了结果"，卡片说的是那个结果
            found = self._orchestrator.store.connection.execute(
                "SELECT type, payload_json FROM events WHERE event_id=?", (notice["notice_id"],)).fetchone()
            if found is not None and found[0] == ACTION_SETTLED_AFTER_STOP:
                payload = json.loads(found[1])
                row["action_settled"] = {"operation": str(payload.get("operation") or ""),
                                         "target": str(payload.get("target") or ""),
                                         "applied": payload.get("applied") is True}
            rows.append(row)
        return rows

    def ack_notice(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Only the person's click on the card in the main conversation reaches here."""

        if body.get("all") is True:
            acked = self._notices.ack_all()
            self.wake()
            return {"acked": acked}
        notice_id = body.get("notice_id")
        if not isinstance(notice_id, str) or not notice_id:
            from .notices import NoticeNotFound

            raise NoticeNotFound("notice_id is required")
        row = self._notices.ack(notice_id)
        self.wake()
        return row

    def notice_context_text(self, *, limit: int = 3) -> str:
        """A few lines for the main Agent's next turn: background tasks that ended and
        that the person has not acknowledged yet.  Empty when there are none."""

        try:
            pending = self.pending_notices()
        except Exception:  # noqa: BLE001 - the chat must never fail on a notice read
            return ""
        if not pending:
            return ""
        lines = [_notice_line(row) for row in pending[-limit:]]
        more = len(pending) - len(lines)
        return ("\nHost task notices (background tasks that ended; the user has not acknowledged them "
                "in this chat yet; use mission_status for details and tell the user when relevant):\n"
                + "\n".join(lines) + (f"\n- …and {more} more" if more > 0 else ""))

    # ------------------------------------------------------------ reads
    def list_missions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = []
        planning_waits = self._planning_waits()
        for mission in self._call("missions", limit=limit):
            mission_id = mission["mission_id"]
            # NEXT-TG-1.0 §9 (2026-09-28): a finished Mission's word is fixed by its status;
            # reading its whole event log for "blocked"/"waiting" made every list (sent on
            # every push while anything runs) cost ~0.35 s of the event loop.
            terminal = str(mission["status"]) in TERMINAL
            blocked = False if terminal else bool(self._blocked(mission_id))
            task_statuses = self._task_statuses(mission_id)
            rows.append(
                {
                    **mission,
                    "id": mission_id,
                    "blocked": blocked,
                    # 2026-09-26 列表进度条：子任务完成数（含分层任务的根复合任务）
                    "task_counts": {
                        "completed": sum(status.endswith("COMPLETED") for status in task_statuses),
                        "total": len(task_statuses),
                    },
                    "ui_state": ui_state(
                        mission["status"],
                        attempt_statuses=() if terminal else self._attempt_statuses(mission_id),
                        task_statuses=task_statuses,
                        waiting=not terminal and (bool(mission.get("pending_approvals")) or self._waiting(mission_id)
                                                  or mission_id in planning_waits),
                        blocked=blocked,
                    ),
                }
            )
        return rows

    def _task_statuses(self, mission_id: str) -> list[str]:
        try:
            return [str(t.status) for t in self._orchestrator.store.list_tasks(mission_id)]
        except Exception:  # noqa: BLE001
            return []

    def _attempt_statuses(self, mission_id: str) -> list[str]:
        try:
            store = self._orchestrator.store
            return [str(a.status) for t in store.list_tasks(mission_id) for a in store.list_attempts(t.id)]
        except Exception:  # noqa: BLE001
            return []

    def _planning_waits(self) -> set[str]:
        """Missions waiting on a person for planning — an authorization request or a blocking
        question — counted exactly as the detail snapshot counts them, so the list and the
        detail show the same state word (2026-09-25 desktop run: the list said "请求已接收"
        while the detail said "等你处理")."""

        try:
            from agent_orchestrator.orchestrator.planning_selection import awaits_authority
            from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
            from agent_orchestrator.storage.planning_human_store import PlanningHumanStore

            store = self._orchestrator.store
            planning = PlanningDecisionStore(store)
            waits = {
                intent.mission_id
                for intent in store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED")
                if awaits_authority(store, intent)
                and planning.get_planning_request_for_intent(intent.intent_id) is not None
            }
            humans = PlanningHumanStore(store)
            for mission_id in {m.id for m in store.list_missions()} - waits:
                for row in humans.list(mission_id):
                    request = row.get("request") or {}
                    if row.get("state") == "PENDING" and (request.get("payload") or {}).get("blocking"):
                        waits.add(mission_id)
                        break
            return waits
        except Exception:  # noqa: BLE001 - the list must still render
            return set()

    def _waiting(self, mission_id: str) -> bool:
        try:
            return bool(self._orchestrator.store.waiting_on(mission_id))
        except Exception:  # noqa: BLE001
            return False

    def mission_detail(self, mission_id: str) -> dict[str, Any]:
        self._require()
        store = self._orchestrator.store
        with store.read_view():
            # The facade authenticates the Mission before any optional source read.
            view = self._call("snapshot", mission_id)
            detail = project_detail(view, blocked=self._blocked(mission_id))
            raw = view["snapshot"].get("mission", {})
            identifier = (raw.get("final_report") or {}).get("runtime_profile_id")
            profile = self._runtime_options.get("profiles", {}).get(identifier)
            if profile is not None and profile.context_policy is not None:
                detail["runtime_context"] = {
                    "profile_id": identifier,
                    "max_input_tokens": profile.context_policy.input_budget(),
                    **({"max_total_tokens": profile.context_policy.max_total_tokens}
                       if profile.context_policy.max_total_tokens is not None else {}),
                    "fingerprint": profile.context_snapshot()["fingerprint"],
                }
            return detail

    def taskgraph_read(self, operation: str, request: Mapping[str, Any]) -> dict[str, Any]:
        from .taskgraph import read_taskgraph
        return read_taskgraph(self, operation, request)

    def taskgraph_operate(self, verb: str, request: Mapping[str, Any]) -> dict[str, Any]:
        from .taskgraph import operate_taskgraph
        return operate_taskgraph(self, verb, request)

    def assurance_read(self, verb: str, request: Mapping[str, Any]) -> dict[str, Any]:
        from .assurance import read_assurance
        return read_assurance(self, verb, request)

    def enable_taskgraph_contract(self, mission_id: str, command_id: str) -> dict[str, Any]:
        """Authenticated deployment composition; deliberately absent from IPC verbs.

        The original policy command checks delegation, final deployment evidence,
        protocol and quiescence in one transaction. A failed check leaves the old
        Mission unchanged; this Host never creates a grant or baseline proof.
        """
        self._require()._mission(mission_id)
        if self._taskgraph is None:
            raise OrchestrationRequestError("taskgraph_unavailable", "当前部署尚未安装执行图")
        receipt = dict(self._taskgraph.policy.enable_taskgraph_contract(mission_id, command_id))
        self._wake.set()
        if self.on_write is not None:
            self.on_write()
        return receipt

    def events(self, mission_id: str, *, after_seq: Any = 0, limit: Any = 50) -> dict[str, Any]:
        # whitelisted rows: the raw payloads stay inside (review P2-3)
        return project_events(self._call("events", mission_id, after_seq=after_seq, limit=limit))

    def _detect_diagnostics(self) -> bool:
        """Inspect the running SDK APIs, equally for verified wheels and source builds."""
        try:
            from . import diagnostics
        except ImportError:
            return False
        store = getattr(self._orchestrator, "store", None)
        return all(callable(value) for value in (
            getattr(self._control, "snapshot", None),
            getattr(store, "read_view", None),
            getattr(store, "snapshot", None),
            getattr(store, "iter_events", None),
            diagnostics.verify_mission,
            diagnostics.failure_timeline, diagnostics.attribution,
        ))

    def mission_diagnostics(self, request: Mapping[str, Any], *, export: bool = False) -> dict[str, Any]:
        self._require()
        if (set(request) != {"mission_id"} or not isinstance(request["mission_id"], str)
                or not request["mission_id"].strip()):
            raise OrchestrationRequestError("invalid_request", "只接受当前任务的 mission_id")
        if not self.status()["diagnostics_available"]:
            raise OrchestrationRequestError("diagnostics_unavailable", "当前运行版本未开放任务诊断")
        from .diagnostics import build_diagnostics, export_support

        mission_id = request["mission_id"]
        with self._orchestrator.store.read_view():
            # Authenticate before any event, attribution or support-file read/write.
            snapshot = self._call("snapshot", mission_id)
            report = build_diagnostics(
                self._orchestrator, mission_id=mission_id, snapshot_view=snapshot,
                versions=dict(self._manifest or {}),
                extra_secrets=self._secret_values(),
            )
        if not export:
            return report
        # 导出时才核对执行图历史（要在临时副本上重建，比只看诊断贵）；读视图之外做。
        from .diagnostics import taskgraph_history
        try:
            reads = self._orchestrator.taskgraph_read_api(tenant_id=self.tenant_id, principal=self._principal)
            history = taskgraph_history(self._orchestrator.store, reads, mission_id, self.root / "support" / "tmp")
        except Exception as error:  # noqa: BLE001 - the rest of the report is still exported
            history = {"status": "READ_UNAVAILABLE", "error": type(error).__name__}
        report = {**report, "taskgraph_history": history}
        try:
            return export_support(self.root / "support", report)
        except ValueError as error:
            raise OrchestrationRequestError("support_export_refused", str(error)) from error

    def approvals(self, mission_id: str | None = None) -> list[dict[str, Any]]:
        return [project_approval(item) for item in self._call("approvals", mission_id)]

    def artifact_read(self, artifact_id: str) -> dict[str, Any]:
        return self._call("artifact_read", artifact_id)

    async def storage_usage(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """``orchestration_storage_get``: cached disk usage of the task/session data,
        re-measured on ``{"refresh": true}`` (throttled).  Works in every service state."""

        refresh = body.get("refresh") is True
        return await self._storage.get(refresh=refresh)

    def policy_status(self) -> dict[str, Any]:
        self._require()
        status = dict(self._policy.status())  # read-only: no writing verb is reachable (HA-7)
        status["drift"] = self._policy_drift()
        return status

    def _policy_drift(self) -> list[dict[str, Any]]:
        """The Host setting reaches the ACTIVE policy only when the library is first seeded
        (plan review P1-5): a later edit is shown here, never applied silently."""

        try:
            active = self._orchestrator.store.active_policy()
        except Exception:  # noqa: BLE001
            return []
        params = dict((active or {}).get("params") or {})
        configured = int(self.settings.max_concurrency)
        if "mission_concurrency" in params and int(params["mission_concurrency"]) != configured:
            return [
                {"name": "mission_concurrency", "config": configured, "active": int(params["mission_concurrency"])}
            ]
        return []

    def _blocked(self, mission_id: str) -> list[dict[str, Any]]:
        """Attempts whose SDK turn has an unknown outcome (for example, a process
        killed inside a model call), excluding known bounded live waits."""

        try:
            store = self._orchestrator.store
            events = store.list_events(mission_id)
        except Exception:  # noqa: BLE001
            return []
        last: dict[str, Mapping[str, Any]] = {}
        for event in events:
            if event.type == "HeartbeatReceived" and event.attempt_id:
                last[event.attempt_id] = dict(event.payload.get("liveness") or {})
        blocked = []
        for attempt_id, liveness in last.items():
            attempt = store.get_attempt(attempt_id)
            if attempt is None or str(attempt.status) in {"COMPLETED", "FAILED", "CANCELLED", "LOST"}:
                continue
            # Queueing and a bounded, still-awaited response keep the Attempt
            # alive. The latter is billable, but neither proves a lost process.
            # Match the SDK shapes exactly; unknown/future blockers stay visible.
            known_wait = liveness.get("blocker") in (
                {"kind": "provider_slot_wait", "billable": False},
                {"kind": "provider_response_wait", "billable": True, "bounded": True},
            )
            if liveness.get("blocked") and not known_wait:
                blocked.append(
                    {"task_id": attempt.task_id, "attempt_id": attempt_id, "reason": "turn_outcome_unknown"}
                )
        return blocked


__all__ = ("TENANT", "OrchestrationRequestError", "OrchestrationService")
