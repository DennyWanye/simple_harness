# SPDX-License-Identifier: BUSL-1.1
"""Assurance 1.1 on the existing authenticated control transport (plan §13, S25).

Three read verbs (``mission_assurance_snapshot`` / ``_review`` / ``_use_check``)
delegate to the SDK's fixed-caller ``AssuranceApi`` through the same
``MissionControlV1`` the Host already builds from its authenticated Principal:
a request body can never name a tenant or principal. Deployment assembly
(``install_assurance``) binds the four consumers, factory and native root on
each Orchestrator lifetime; nothing here is reachable from model or IPC input.
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Mapping
from typing import Any

from .service import OrchestrationRequestError

ROOT_COMMAND_ID = "host-native-root"
ASSURANCE_VERBS = ("snapshot", "review", "use_check")


class AssuranceRequestError(OrchestrationRequestError):
    """A refused Assurance read; ``wire`` is the SDK's ``host-error-v1`` body."""

    def __init__(self, wire: Mapping[str, Any]) -> None:
        self.wire = dict(wire)
        super().__init__(str(wire["code"]), str(wire["message"]))


def host_fingerprint() -> str:
    """SHA-256 identity of the Host build reading through the verbs.

    Binds the Host commit and dirty flag with the pinned SDK wheel bytes, so two
    Host processes whose code or wheel differ never share a fingerprint even
    when their version strings match (plan §13.2).
    """
    from deskpet.sdk_adapters.sdk_candidate import SDK_VERSION, SDK_WHEEL_SHA256

    from .manifest import _host_commit, _host_dirty

    body = {
        "schema": "host-assurance-fingerprint-v1",
        "host_commit": _host_commit(),
        "host_dirty": _host_dirty(),
        "sdk_version": SDK_VERSION,
        "sdk_wheel_sha256": SDK_WHEEL_SHA256,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def root_setup(service: Any):
    """``Orchestrator(assurance_root_setup=...)``: the authenticated native installation.

    Idempotent per root directory and caller: a second lifetime over the same
    root replays the same receipt; a different principal or tenant is refused by
    the SDK as an identity conflict, never silently adopted.
    """

    def install(orchestrator: Any) -> None:
        orchestrator.commit.install_assurance_root(
            principal=service._principal, tenant_id=service.tenant_id, command_id=ROOT_COMMAND_ID,
        )

    return install


def install_assurance(service: Any, orchestrator: Any) -> Any:
    """Bind the SDK's Assurance deployment on this Orchestrator lifetime.

    Returns the SDK's ``InstalledAssurance`` or ``None`` when this wheel has no
    Assurance assembly. Fixture lanes (``test_scenario``) keep their original
    protocol and never install it.
    """
    if service._test_scenario is not None:
        return None
    try:
        from agent_orchestrator.assurance.policy import AssurancePolicy
        from agent_orchestrator.orchestrator.assurance_assembly import (
            AssuranceDeploymentPorts,
            install_assurance as sdk_install,
        )
    except ImportError:
        return None
    from .hierarchical import root_requirements

    profile = service.settings.assurance_profile
    notices: deque[dict[str, Any]] = service._assurance_notices

    def select_profile(spec: Any) -> Any:
        # The single Host selection point: "on" assures every planning-decision
        # Mission of this tenant (the default); "off" is the explicit opt-out that
        # keeps the original lane. Both are passed, so the SDK default never decides.
        if profile == "on":
            return AssurancePolicy()
        return None

    def notify(payload: Mapping[str, Any]) -> None:
        # At-least-once local status transport: the change pump re-reads the
        # Mission and pushes ``mission_changed``; the payload is retained for
        # the status surface, never re-interpreted as a completion.
        notices.append(dict(payload))
        service.wake()

    ports = AssuranceDeploymentPorts(
        tenant_id=service.tenant_id,
        principal=service._principal,
        requirements=lambda mission, spec: root_requirements(mission, service._principal),
        select_profile=select_profile if profile in ("on", "off") else None,
        notify_transport=notify,
        host_fingerprint=host_fingerprint(),
    )
    return sdk_install(orchestrator, ports)


def _invalid(message: str = "Assurance 请求的字段或版本无效") -> None:
    raise OrchestrationRequestError("invalid_request", message)


def read_assurance(service: Any, verb: str, request: Mapping[str, Any]) -> dict[str, Any]:
    if verb not in ASSURANCE_VERBS or not isinstance(request, Mapping):
        _invalid()
    mission_id = request.get("mission_id")
    if not isinstance(mission_id, str) or not mission_id.strip() or len(mission_id) > 2048:
        _invalid("只接受当前任务的 mission_id")
    control = service._require()
    # Ownership through the original facade rule before any Assurance read;
    # tenant and principal are the Host's, never the body's.
    control._mission(mission_id)
    reader = getattr(control, f"assurance_{verb}", None)
    if not callable(reader):
        raise OrchestrationRequestError("assurance_unavailable", "当前 SDK 尚未安装 Assurance 读取接口")
    from agent_orchestrator.api.facade import FacadeError

    try:
        return reader(dict(request))
    except FacadeError as error:
        wire = getattr(error, "wire", None)
        if isinstance(wire, Mapping):
            raise AssuranceRequestError(wire) from error
        raise OrchestrationRequestError(
            "assurance_unavailable" if error.code == "PROFILE_UNBOUND" else error.code, str(error)
        ) from error
