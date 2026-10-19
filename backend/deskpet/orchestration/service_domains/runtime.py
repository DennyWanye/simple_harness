# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Drive, rebuild and native-pool forwarding to the SDK host; the shared Skill catalogue."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from simple_harness.agents.arp.assurance_acceptance import evaluation_mission_key
from simple_harness.agents.arp.errors import ArpError
from simple_harness.agents.arp.pins import Pin
from simple_harness.agents.arp.shared_catalogue import (
    SKILL_READ_VERBS,
    SKILL_WRITE_VERBS,
)
from simple_harness.api.runtime_plane import RuntimePlaneService

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
from .support import OrchestrationRequestError

# 与原模块同名的记录器（getLogger 按名字返回同一个对象）；就地定义，ruff 才认得出
# 捕获异常后写日志的写法
logger = logging.getLogger("deskpet.orchestration.service")


class RuntimeDriveMixin(OrchestrationServiceCore):
    """Drive, rebuild and native-pool forwarding to the SDK host; the shared Skill catalogue."""

    __slots__ = ()

    # ------------------------------------------------------------ driver loop (the SDK's)
    def wake(self) -> None:
        self._host.wake()

    async def _drive(self) -> None:
        await self._host.run_forever()

    async def _rebuild(self) -> None:
        await self._host._rebuild()

    async def _host_duties(self) -> None:
        await self._host._host_duties()

    async def _auto_authorize_planning(self) -> int:
        """Auto permission mode: the SDK issues each pending planning authority."""
        return self._duties.auto_authorize_planning(auto=await self._auto_mode())

    async def _auto_confirm_content_completion(self) -> int:
        return self._duties.auto_confirm_content_completion(
            auto=await self._auto_mode()
        )

    def _project_assurance_policies(self) -> int:
        if self._assurance is None:
            return 0
        try:
            return self._duties.project_check_policies()
        except Exception:
            logger.exception("assurance check policy projection failed")
            return 0

    def _tick(self) -> float:
        return self._host._tick()

    def _note_quiet_round(self) -> None:
        self._host._note_quiet_round()

    async def drain(self, timeout: float = 60.0) -> bool:
        """Tests (``drive=False``): run the loop until idle or ``timeout``.  False when it
        did not idle — a turn blocked on an unknown outcome never does."""

        return await self._host.drain(timeout)

    def _degraded_recovery(self) -> bool:
        return self._host._degraded_recovery()

    # ------------------------------------------------------------ native plane
    def _native_profile_ids(self) -> list[str]:
        return self._host.native_profile_ids()

    def _native_pool(self, profile_id: str | None) -> Any:
        return self._h(self._host.native_pool, profile_id)

    async def runtime_plane(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """One ``HostRequest`` of the SDK runtime plane (``agent_*`` verbs) → one ``HostResponse``.

        The caller is the Host's principal, derived here and never from the body; the SDK
        answers every failure as its typed ``Error`` DTO inside the response."""
        if not isinstance(request, Mapping):
            raise OrchestrationRequestError("invalid_request", "请求必须是一个对象")
        body = dict(request)
        profile_id = body.pop("profile_id", None)
        if profile_id is not None and not isinstance(profile_id, str):
            raise OrchestrationRequestError(
                "invalid_request", "profile_id 必须是字符串"
            )
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
            raise OrchestrationRequestError(
                "refused", f"{error.code}: {error}"
            ) from error

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
        except Exception as error:
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
        body = {
            k: v
            for k, v in request.items()
            if k not in ("evaluation_ref", "idempotency_key")
        }
        body["idempotency_key"] = key
        receipt = self.create_mission(body)  # type: ignore[attr-defined]
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
            pin,
            mission_id=mission_id,
            task_id=task_id,
            caller=caller,
            command_id=command_id,
        )
        self.wake()
        return dict(body)


__all__ = ("RuntimeDriveMixin",)
