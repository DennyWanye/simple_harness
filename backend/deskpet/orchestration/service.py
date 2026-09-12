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
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .lock import InstanceLock
from .manifest import MANIFEST_SCHEMA, build_manifest, distributions, write_manifest
from .projection import (
    citation_identity,
    is_document_snapshot,
    project_approval,
    project_detail,
    project_events,
    ui_state,
)
from .provider import NO_MODEL, ProviderSnapshot, ProviderUnavailable
from .settings import OrchestrationSettings

logger = logging.getLogger(__name__)

TENANT = "local-desktop"
WORKSPACE_TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list")
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


def backoff_delay(failures: int, maximum: float) -> float:
    """Seconds to wait after ``failures`` failed rounds in a row.  The exponent is capped:
    ``2.0 ** 1024`` overflows, and the error would end the driver loop (review P2-7)."""

    return min(maximum, 2.0 ** min(max(failures, 0), 16))


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
        test_scenario: str | None = None,
        drive: bool = True,
    ) -> None:
        self.root = Path(root)
        self.settings = settings
        self.tenant_id = TENANT
        self.owner = f"deskpet-orchestrator-{os.getpid()}-{secrets.token_hex(4)}"  # P0-2
        self._principal = principal
        self._provider = provider
        self._snapshot = provider_snapshot
        self._http_client = http_client
        self._test_scenario = test_scenario
        self._drive_enabled = drive
        self._state = "created"
        self._reason: str | None = None
        self._lock = InstanceLock(self.root)
        self._orchestrator: Any = None
        self._effective_provider: Any = None
        self._config: Any = None
        self._connectors: dict[str, Any] = {}
        self._deployment: Any = None
        # P3.2: what the sandbox capability probe found here, and the executor it proved
        self._sandbox: dict[str, Any] | None = None
        self._executor: Any = None
        # P3.2 P32-14: whether a publish directory is authorised, and if not, why
        self._publish: dict[str, Any] = {"enabled": False, "reason": "尚未探测"}
        self._control: Any = None
        self._policy: Any = None
        self._manifest: dict[str, Any] | None = None
        self._driver: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._closing = False
        self._failures = 0
        self._authorization_mode: str | None = None
        self.on_write: Callable[[], None] | None = None  # the change pump's poke

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
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
                test_scenario=self._test_scenario,
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
        except Exception as error:  # noqa: BLE001 - never into the Host lifespan
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
        if self._test_scenario == "approval-action":  # plan §3.8: test-only, own directory
            from agent_orchestrator.runtime.connectors import TestConfigService
            from agent_orchestrator.testing.fixtures import demo_approval_action_provider

            # the Host deployment offers the workspace tools only (SDK 0.9.10 parameter)
            provider = demo_approval_action_provider(allowed_tools=WORKSPACE_TOOLS)
            self._connectors = {
                "test_config": TestConfigService(self.root / "test-services" / "config.json")
            }
            enabled_connectors = ("test_config",)
        elif provider is None:
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
        self._deployment = DeploymentPolicy(
            allowed_tools=(*WORKSPACE_TOOLS, "run_tests") if sandboxed else WORKSPACE_TOOLS,
            code_execution="sandboxed" if sandboxed else "off",
            enabled_connectors=enabled_connectors,
            max_action_level="L2",  # one person: L3's two distinct people cannot be met
        )
        knobs: dict[str, Any] = {}
        if self._snapshot is not None:  # a real model: the bounds the SDK real runs use
            knobs = {"default_max_output_tokens": 8192, "max_output_tokens_ceiling": 32768}
        self._config = OrchestratorConfig(
            evidence_root=self.root,
            model=self._snapshot.requested_model if self._snapshot is not None else "agent-model",
            max_concurrency=self.settings.max_concurrency,
            max_concurrent_model_calls=self.settings.max_concurrent_model_calls,
            lease_seconds=self.settings.lease_seconds,
            deployment_policy=self._deployment,
            sandbox_executor=self._executor,  # P3.2 D2: required when sandboxed
            price_table=None,  # unpriced: money is recorded as null, never 0
            **knobs,
        )
        self._effective_provider = provider  # kept for a rebuild after repeated failures
        self._orchestrator = Orchestrator(
            self._config, provider, owner=self.owner, connectors=self._connectors
        )
        await self._orchestrator.__aenter__()
        self._control = MissionControlV1(
            self._orchestrator, tenant_id=self.tenant_id, principal=self._principal
        )
        self._policy = PolicyApi(
            self._orchestrator.commit, self._principal, deployment=self._deployment
        )

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

        from agent_orchestrator.runtime.sandbox import SandboxUnavailable, SeatbeltExecutor
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
        if orchestrator is not None:
            try:
                await orchestrator.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                logger.exception("orchestrator close failed")
        if self._http_client is not None:
            try:
                await self._http_client.aclose()
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
            except Exception:  # noqa: BLE001
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
        return self.settings.tick_active_seconds

    async def _drive(self) -> None:
        while not self._closing:
            try:
                await self._orchestrator.run()
                self._failures = 0
                if self._state == "degraded":
                    self._state, self._reason = "available", None
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - the backend must not fall over
                self._failures += 1
                logger.exception("orchestrator run failed (%s in a row)", self._failures)
                if self._failures >= self.settings.degraded_after_failures:
                    self._state, self._reason = "degraded", f"编排循环连续失败：{error}"[:300]
                if self._failures % self.settings.rebuild_after_failures == 0:
                    try:
                        await self._rebuild()
                    except Exception as rebuild_error:  # noqa: BLE001 - keep looping, and say so (review P1-2)
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

    async def _rebuild(self) -> None:
        from agent_orchestrator.api.facade import MissionControlV1
        from agent_orchestrator.api.policies import PolicyApi
        from agent_orchestrator.orchestrator.event_handler import Orchestrator

        old = self._orchestrator
        try:
            await old.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            logger.exception("orchestrator close during rebuild failed")
        self._orchestrator = Orchestrator(
            self._config, self._effective_provider, owner=self.owner, connectors=self._connectors
        )
        await self._orchestrator.__aenter__()
        self._control = MissionControlV1(
            self._orchestrator, tenant_id=self.tenant_id, principal=self._principal
        )
        self._policy = PolicyApi(self._orchestrator.commit, self._principal, deployment=self._deployment)

    async def drain(self, timeout: float = 60.0) -> bool:
        """Tests (``drive=False``): run the loop until idle or ``timeout``.  False when it
        did not idle — a turn blocked on an unknown outcome never does."""

        try:
            await asyncio.wait_for(self._orchestrator.run(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    # ------------------------------------------------------------ status
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
            "test_scenario": self._test_scenario,
            "mission_budget_defaults": {
                "max_tokens": self.settings.default_mission_max_tokens,
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

    def _door(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """The Host's own checks before the facade (plan §3.3 门口检查)."""

        body = dict(request)
        goal = body.get("goal")
        criteria = body.get("success_criteria")
        if not isinstance(goal, str) or not goal.strip():
            raise OrchestrationRequestError("invalid_request", "Mission 目标不能为空")
        if (
            not isinstance(criteria, list)
            or not criteria
            or not all(isinstance(c, str) and c.strip() for c in criteria)
        ):
            raise OrchestrationRequestError("invalid_request", "成功条件不能为空，每行一条")
        # every string of the request — stop conditions, synthesis and the workspace seed
        # included, not only the goal and the criteria (review P1-3)
        self._refuse_secrets(body)
        # P3.2 (plan D9): both gates now ask what this deployment can really do, instead of
        # the P3.1 answers ("never" / "only in the test scenario")
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
        # No Mission without bounds (native run 2026-09-12, adjudication C): with a null
        # budget a real Planner invents Task budgets far below one model turn and the
        # Mission cannot succeed.  A blank item takes the deployment default; an item the
        # person gave stays as written; a non-positive one is refused.  Filled here, before
        # the facade, so the persisted receipt's spec hash includes the defaults.
        budget = body.get("budget")
        if budget is None:
            budget = {}
        if not isinstance(budget, Mapping):
            raise OrchestrationRequestError("invalid_request", "预算必须是一个对象")
        budget = dict(budget)
        for name, default in (
            ("max_tokens", self.settings.default_mission_max_tokens),
            ("max_attempts", self.settings.default_mission_max_attempts),
        ):
            value = budget.get(name)
            if value is None:
                budget[name] = default
            elif isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise OrchestrationRequestError("invalid_request", f"预算 {name} 必须是正整数")
        body["budget"] = budget
        if self._test_scenario == "approval-action":
            from agent_orchestrator.testing.fixtures import APPROVAL_SEED

            existing = [m for m in self._orchestrator.store.list_missions() if m.tenant_id == self.tenant_id]
            if existing and existing[0].idempotency_key != body.get("idempotency_key"):
                raise OrchestrationRequestError(
                    "test_scenario_single_mission", "测试场景只允许一个 Mission（夹具脚本只够一次）"
                )
            body.setdefault("workspace_seed", dict(APPROVAL_SEED))
        return body

    # ------------------------------------------------------------ commands
    def create_mission(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self._require()
        if self._state == "degraded":  # review P2-1: stop and take over yes, new work no
            raise OrchestrationRequestError(
                "orchestration_degraded", "编排循环目前不正常，暂不接受新的 Mission；已有的仍可取消或接管"
            )
        receipt = self._call("create", self._door(request))
        self.wake()
        return {"mission_id": receipt["mission_id"], "created": receipt["created"], "spec_hash": receipt["spec_hash"]}

    def create_mission_with_sources(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """The UI's atomic batch; never create and then register in separate transactions."""
        self._require()
        if self._state == "degraded":
            raise OrchestrationRequestError("orchestration_degraded", "编排循环异常，暂不接受新 Mission")
        if set(request) != {"mission", "sources"} or not isinstance(request["mission"], Mapping):
            raise OrchestrationRequestError("invalid_request", "需要 mission 与 sources 原子批次")
        self._refuse_secrets(request)
        receipt = self._call("create_with_sources", {
            "mission": self._door(request["mission"]), "sources": request["sources"],
        })
        self.wake()
        return dict(receipt)

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

    def comment(self, target_id: str, text: str) -> dict[str, Any]:
        self._refuse_secrets(text)
        result = self._call("comment", target_id, text)
        self.wake()
        return result

    # ------------------------------------------------------------ reads
    def list_missions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = []
        for mission in self._call("missions", limit=limit):
            mission_id = mission["mission_id"]
            blocked = bool(self._blocked(mission_id))
            rows.append(
                {
                    **mission,
                    "id": mission_id,
                    "blocked": blocked,
                    "ui_state": ui_state(
                        mission["status"],
                        attempt_statuses=self._attempt_statuses(mission_id),
                        waiting=bool(mission.get("pending_approvals")) or self._waiting(mission_id),
                        blocked=blocked,
                    ),
                }
            )
        return rows

    def _attempt_statuses(self, mission_id: str) -> list[str]:
        try:
            store = self._orchestrator.store
            return [str(a.status) for t in store.list_tasks(mission_id) for a in store.list_attempts(t.id)]
        except Exception:  # noqa: BLE001
            return []

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
            stale = None
            if is_document_snapshot(view["snapshot"]):
                from agent_orchestrator.memory.verified_knowledge import KnowledgeIndex

                index = KnowledgeIndex.load(store, mission_id)
                used = set(index.records)
                used.update(kid for claim in view["snapshot"].get("claims", ())
                            for kid in claim.get("dependencies", ()))
                stale = index.stale(sorted(used))
            return project_detail(view, blocked=self._blocked(mission_id), source_issues=stale)

    def events(self, mission_id: str, *, after_seq: Any = 0, limit: Any = 50) -> dict[str, Any]:
        # whitelisted rows: the raw payloads stay inside (review P2-3)
        return project_events(self._call("events", mission_id, after_seq=after_seq, limit=limit))

    def approvals(self, mission_id: str | None = None) -> list[dict[str, Any]]:
        return [project_approval(item) for item in self._call("approvals", mission_id)]

    def artifact_read(self, artifact_id: str) -> dict[str, Any]:
        return self._call("artifact_read", artifact_id)

    def citation_read(self, request: Mapping[str, Any]) -> dict[str, Any]:
        required = {"mission_id", "result_id", "receipt_id", "citation_index"}
        if not required <= set(request) or set(request) - required - {"offset", "limit"}:
            raise OrchestrationRequestError("invalid_request", "引用读取只接受记录身份与字符分页")
        for name in ("mission_id", "result_id", "receipt_id"):
            if not isinstance(request[name], str) or not request[name].strip():
                raise OrchestrationRequestError("invalid_request", f"缺少 {name}")
        index, offset, limit = request["citation_index"], request.get("offset", 0), request.get("limit", 65536)
        if (type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 65536):
            raise OrchestrationRequestError("invalid_request", "引用 offset/limit 无效")
        result = self._call("citation_read", request["mission_id"],
                            result_id=request["result_id"], receipt_id=request["receipt_id"],
                            citation_index=index, offset=offset, limit=limit)
        return {**dict(result), **{name: request[name] for name in required},
                "version": result.get("version_hash"),
                "citation_id": citation_identity(request["mission_id"], request["result_id"],
                                                 request["receipt_id"], index)}

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
        """Attempts whose SDK turn is blocked on an unknown outcome (a process killed
        inside a model call): the last heartbeat says ``liveness.blocked``."""

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
            if liveness.get("blocked"):
                blocked.append(
                    {"task_id": attempt.task_id, "attempt_id": attempt_id, "reason": "turn_outcome_unknown"}
                )
        return blocked


__all__ = ("TENANT", "OrchestrationRequestError", "OrchestrationService")
