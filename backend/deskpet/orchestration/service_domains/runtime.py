# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Connector, sandbox, runtime lifecycle, the drive loop, native pools and skill calls."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import paths
from agent_orchestrator.deployment.native_pools import is_native_profile
from agent_orchestrator.governance.policies import SKILL_TOOL_NAMES
from agent_orchestrator.runtime import sandbox
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector
from agent_orchestrator.runtime.deepseek_meter import deepseek_meter_binding
from agent_orchestrator.runtime.sandbox import SandboxUnavailable, SeatbeltExecutor
from simple_harness.agents.arp.assurance_acceptance import evaluation_mission_key
from simple_harness.agents.arp.errors import ArpError
from simple_harness.agents.arp.pins import Pin
from simple_harness.agents.arp.shared_catalogue import SKILL_READ_VERBS, SKILL_WRITE_VERBS
from simple_harness.api.runtime_plane import RuntimePlaneService

from ..native_plane import build_native_pools
from ..runtime_profile import ONLY_DEEPSEEK_REASON
from ..skill_catalogue import (
    SkillCatalogueRefused,
    admit,
    evaluate,
    install_file,
    lifecycle,
    overview,
)
from ..skill_catalogue import request as skill_request
from .core import OrchestrationServiceCore
from .support import TERMINAL, OrchestrationRequestError, logger


def backoff_delay(failures: int, maximum: float) -> float:
    """Seconds to wait after ``failures`` failed rounds in a row.  The exponent is capped:
    ``2.0 ** 1024`` overflows, and the error would end the driver loop (review P2-7)."""

    return min(maximum, 2.0 ** min(max(failures, 0), 16))


class RuntimeDriveMixin(OrchestrationServiceCore):
    """Connector, sandbox, runtime lifecycle, the drive loop, native pools and skill calls."""

    __slots__ = ()

    def _publish_connector(self) -> Any:
        """The file publish connector, but only for a directory the user really authorised.

        Three things must hold before anything can be published (P3.2 plan D9, P32-14): the
        user named a directory, it exists, and it can carry a hard link — the connector's
        only atomic commit point.  A volume that cannot (exFAT, some network shares) is
        refused *here*, at authorisation time, rather than turning into an UNKNOWN action
        later.  Without all three the connector is not enabled at all, so a Mission may not
        even carry a publish criterion.
        """

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
            report = await sandbox.probe_sandbox(executor)
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
        self._quarantine = None
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
                if self._quarantine is not None:
                    return  # 根隔离：不派发任何任务（第 2 批 A02）
                await self._orchestrator.run()
                if self._degraded_recovery():
                    return  # 降级恢复：SDK 不进周期，这里也不再驱动（H01）
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
                await asyncio.sleep(
                    backoff_delay(self._failures, self.settings.backoff_max_seconds)
                )
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._tick())
            except TimeoutError:
                pass
            self._wake.clear()

    # ------------------------------------------------------------ native plane (RP-E3)
    def _native_skill_tools(self) -> tuple[str, ...]:
        """The Skill tools this deployment's native pools serve."""

        return tuple(SKILL_TOOL_NAMES)

    def _build_native(self) -> Any:
        """The Host's native-plane composition (the only execution plane since 2026-09-30)."""

        if self._native_test_counter is not None:
            meter_factory = self._native_test_counter.meter_factory
        else:
            meter_factory = deepseek_meter_binding
        models_dir = None
        try:
            models_dir = Path(paths.user_models_dir())
        except Exception:  # noqa: BLE001 - no model directory means lexical-only, reported
            models_dir = None
        return build_native_pools(
            tenant_id=self.tenant_id,
            principal_id=str(self._principal.principal_id),
            allowed_tools=tuple(self._deployment.allowed_tools)
            if self._deployment is not None
            else (),
            models_dir=models_dir,
            meter_factory=meter_factory,
            script_executor=self._executor,
        )

    def _native_profile_ids(self) -> list[str]:

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

        if body.get("verb") in SKILL_READ_VERBS | SKILL_WRITE_VERBS:
            # NEXT-TG-1.0 §11: Skills have one authority for every pool; no per-pool install
            return await self.skill_request(body)
        pool = self._native_pool(profile_id)

        response = await RuntimePlaneService(pool.runtime).handle(
            body, caller=self._native.control_caller(body)
        )
        self.wake()
        return response

    # ---- the shared Skill catalogue (NEXT-TG-1.0 §11; logic in skill_catalogue.py / the SDK) ----

    async def _skill_call(self, call: Any) -> dict[str, Any]:

        self._require()
        try:
            result = call()
            return await result if hasattr(result, "__await__") else result
        except SkillCatalogueRefused as error:
            raise OrchestrationRequestError(error.code, str(error)) from error
        except ArpError as error:
            raise OrchestrationRequestError("refused", f"{error.code}: {error}") from error

    async def skill_catalogue(self, request: Mapping[str, Any]) -> dict[str, Any]:

        del request
        return await self._skill_call(lambda: overview(self))

    async def skill_install_file(self, request: Mapping[str, Any]) -> dict[str, Any]:

        if not isinstance(request, Mapping):
            raise OrchestrationRequestError("invalid_request", "请求必须是一个对象")
        return await self._skill_call(lambda: install_file(self, request))

    async def skill_lifecycle(self, request: Mapping[str, Any]) -> dict[str, Any]:

        if not isinstance(request, Mapping):
            raise OrchestrationRequestError("invalid_request", "请求必须是一个对象")
        return await self._skill_call(lambda: lifecycle(self, request))

    async def skill_evaluate(self, request: Mapping[str, Any]) -> dict[str, Any]:

        if not isinstance(request, Mapping):
            raise OrchestrationRequestError("invalid_request", "请求必须是一个对象")
        return await self._skill_call(lambda: evaluate(self, request))

    async def skill_admit(self, request: Mapping[str, Any]) -> dict[str, Any]:

        if not isinstance(request, Mapping):
            raise OrchestrationRequestError("invalid_request", "请求必须是一个对象")
        return await self._skill_call(lambda: admit(self, request))

    async def skill_request(self, request: Mapping[str, Any]) -> dict[str, Any]:

        if not isinstance(request, Mapping):
            raise OrchestrationRequestError("invalid_request", "请求必须是一个对象")
        body = dict(request)
        body.pop("profile_id", None)
        self._refuse_secrets(body)
        return await self._skill_call(lambda: skill_request(self, body))

    @staticmethod
    def _evaluation_pin(request: Mapping[str, Any]) -> Any:

        try:
            pin = Pin.from_json(dict(request.get("evaluation_ref") or {}))
        except Exception as error:  # noqa: BLE001 - malformed reference
            raise OrchestrationRequestError(
                "invalid_request", "evaluation_ref 不是有效的评估引用"
            ) from error
        if pin.kind != "evaluation":
            raise OrchestrationRequestError(
                "invalid_request", "evaluation_ref 必须指向一条 Skill 评估"
            )
        return pin

    def skill_evaluation_mission(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Create (idempotently) the original Assurance Mission of one Skill evaluation: its
        idempotency key is the evaluation's own key, which is what later ties an acceptance
        certificate back to the evaluation."""

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

        if not isinstance(request, Mapping):
            raise OrchestrationRequestError("invalid_request", "请求必须是一个对象")
        pin = self._evaluation_pin(request)
        mission_id, task_id, command_id = (
            request.get("mission_id"),
            request.get("task_id"),
            request.get("command_id"),
        )
        for name, value in (
            ("mission_id", mission_id),
            ("task_id", task_id),
            ("command_id", command_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise OrchestrationRequestError("invalid_request", f"{name} 不能为空")
        control = self._require()
        mission = control._mission(mission_id)
        if mission.idempotency_key != evaluation_mission_key(pin):
            raise OrchestrationRequestError(
                "invalid_request", "这个 Mission 不是该评估的原始 Mission"
            )
        task = self._orchestrator.store.get_task(task_id)
        if task is None or task.mission_id != mission_id:
            raise OrchestrationRequestError("not_found", "任务不属于这个 Mission")
        # the evaluation belongs to the shared catalogue's owner (the only pool that admits)
        pool = self._native_pool(self._native.catalogue_owner_id)
        caller = self._native.control_caller(
            {
                "command_id": command_id,
                "evaluation_ref": pin.to_json(),
                "mission_id": mission_id,
                "task_id": task_id,
            }
        )
        body = pool.runtime.arp.lifecycle.record_evaluation_dispatch(
            pin, mission_id=mission_id, task_id=task_id, caller=caller, command_id=command_id
        )
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
            logger.warning(
                "permission mode unreadable; confirmations and planning authority stay manual"
            )
            return False

    async def _auto_confirm_content_completion(self) -> int:
        """Auto permission mode: the SDK confirms content-only completion requirements."""
        return self._duties.auto_confirm_content_completion(auto=await self._auto_mode())

    async def _auto_authorize_planning(self) -> int:
        """Auto permission mode: the SDK issues each pending planning authority."""
        issued = self._duties.auto_authorize_planning(auto=await self._auto_mode())
        return issued
