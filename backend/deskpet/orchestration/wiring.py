# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Lifespan wiring of the orchestration service (plan §3.3), kept out of ``main.py``.

``activate_orchestration`` runs after the chat's SDK stack; nothing it does can fail the
Host startup — a failure leaves the service unavailable with a reason, recorded in
``startup_errors`` unless it is the ordinary "no model configured yet" of a fresh install.
``deactivate_orchestration`` is the orderly-shutdown path only (the App ends the backend
with SIGKILL).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from .paths import orchestration_root, test_scenario_root
from .provider import (
    NO_MODEL,
    ProviderUnavailable,
    build_provider,
    snapshot_from_registry,
)
from .pump import MissionChangePump
from .service import OrchestrationService
from .settings import load_settings, resolve_test_scenario, response_model_aliases

logger = logging.getLogger(__name__)


def read_section(config_path: str | Path) -> Mapping[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        with open(config_path, "rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, ValueError):
        return {}
    section = raw.get("orchestration", {})
    return section if isinstance(section, dict) else {}


def _principal(user_data: Path) -> Any:
    from agent_orchestrator.governance.permissions import Principal

    from deskpet.companion.identity import load_or_create_local_identity

    identity = load_or_create_local_identity(user_data)
    return Principal(f"local-user:{identity.profile_id}", "本机用户")


def _broadcaster(targets: Callable[[], Iterable[Any]]) -> Callable[[dict[str, Any]], Any]:
    async def broadcast(envelope: dict[str, Any]) -> None:
        seen: set[int] = set()
        for socket in list(targets()):
            if id(socket) in seen:
                continue
            seen.add(id(socket))
            try:
                await asyncio.wait_for(socket.send_json(envelope), timeout=1.0)
            except Exception:  # noqa: BLE001 - a closed socket must not stop the others
                logger.debug("mission_changed push to one socket failed")

    return broadcast


async def activate_orchestration(
    service_context: Any,
    *,
    config_path: str | Path,
    user_data: str | Path,
    broadcast_targets: Callable[[], Iterable[Any]],
    record_startup_error: Callable[[str, BaseException], Any],
) -> OrchestrationService | None:
    try:
        settings = load_settings(read_section(config_path))
        data = Path(user_data)
        scenario = resolve_test_scenario(os.environ, data)
        root = test_scenario_root(data) if scenario else orchestration_root(data)
        provider = snapshot = client = None
        if scenario is None:
            try:
                snapshot = snapshot_from_registry(service_context.get("provider_registry"))
                aliases = response_model_aliases(settings)
                if aliases:
                    snapshot = dataclasses.replace(snapshot, response_model_aliases=aliases)
                from .runtime_profile import deepseek_thinking

                thinking = deepseek_thinking(snapshot, settings)
                provider, client = (build_provider(snapshot, timeout=900.0, allow_private_http=True, thinking=thinking)
                                    if settings.local_model_profile else build_provider(snapshot, thinking=thinking))
            except ProviderUnavailable as error:
                logger.info("orchestration_without_model reason=%s", error)
        service = OrchestrationService(
            root,
            settings,
            principal=_principal(data),
            provider=provider,
            provider_snapshot=snapshot,
            http_client=client,
            test_scenario=scenario,
        )
        await service.start()
    except Exception as error:
        logger.exception("orchestration activation failed")
        record_startup_error("orchestration", error)
        return None
    status = service.status()
    if status["state"] == "unavailable" and not str(status["reason"] or "").startswith(NO_MODEL):
        record_startup_error("orchestration", RuntimeError(str(status["reason"])))
    service_context.register("orchestration", service)
    pump = MissionChangePump(service, _broadcaster(broadcast_targets), interval=1.0)
    service.on_write = pump.poke
    if status["available"]:
        pump.start()
    service_context.register("orchestration_pump", pump)
    logger.info(
        "orchestration_ready state=%s scenario=%s root=%s", status["state"], scenario, root.name
    )
    return service


async def deactivate_orchestration(service_context: Any) -> None:
    pump = service_context.get("orchestration_pump")
    if pump is not None:
        await pump.stop()
        service_context.register("orchestration_pump", None)
    service = service_context.get("orchestration")
    if service is not None:
        await service.close()
        service_context.register("orchestration", None)


__all__ = ("activate_orchestration", "deactivate_orchestration", "read_section")
