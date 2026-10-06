# SPDX-License-Identifier: BUSL-1.1
"""Assurance 1.1 on the existing authenticated control transport (plan §13, S25).

Three read verbs (``mission_assurance_snapshot`` / ``_review`` / ``_use_check``)
delegate to the SDK's fixed-caller ``AssuranceApi`` through the same
``MissionControlV1`` the Host already builds from its authenticated Principal:
a request body can never name a tenant or principal. Deployment assembly
(``agent_orchestrator.deployment.assembly``) binds the four consumers, factory and native root on
each Orchestrator lifetime; nothing here is reachable from model or IPC input.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any

from .service import OrchestrationRequestError

logger = logging.getLogger(__name__)

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


def _invalid(message: str = "Assurance 请求的字段或版本无效") -> None:
    raise OrchestrationRequestError("invalid_request", message)


def quarantined_read(request: Mapping[str, Any]) -> AssuranceRequestError:
    """隔离只读分支（原计划 §10.1 / §10.3，第 2 批 A40）：根隔离时三个读动词一律答 SDK 的
    ``ROOT_QUARANTINED``（host-error-v1），不看对象存不存在、不披露任何正文。"""
    from agent_orchestrator.api.assurance import AssuranceReadError

    request_id = request.get("request_id") if isinstance(request, Mapping) else None
    error = AssuranceReadError("ROOT_QUARANTINED", "assurance root is quarantined; only the non-disclosing diagnostic is open")
    return AssuranceRequestError(error.to_json(request_id if isinstance(request_id, str) and request_id else "unknown"))


def read_assurance(service: Any, verb: str, request: Mapping[str, Any]) -> dict[str, Any]:
    if verb not in ASSURANCE_VERBS or not isinstance(request, Mapping):
        _invalid()
    if service.quarantined:
        raise quarantined_read(request)
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
