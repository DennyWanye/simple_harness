# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""The task orchestration's Skill catalogue entry (NEXT-TG-1.0 §11).

All native pools share one Skill authority: the SDK's ``SharedSkillCatalogue`` on the
owner pool (``native_plane.catalogue_owner_profile_id``).  This module is the Host's thin
entry to it — it builds no Skill logic of its own:

* ``overview``: every Skill revision with its state and whether each pool can use it;
* ``install_file``: a local ``.zip`` bundle goes into the deployment's artifact store and
  is installed once on the owner (the SDK mirrors it into every pool);
* ``lifecycle``: suspend / resume / retire on the owner (every pool follows);
* ``request``: one SDK Skill ``HostRequest`` (the eight ``agent_skill*`` verbs only).

Admission still needs the original Assurance evaluation (``agent_skill_trial`` →
evaluation Mission → ``agent_skill_admit``); nothing here admits a Skill by itself.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

MAX_BUNDLE_BYTES = 32 * 1024 * 1024
ACTIONS = ("SUSPEND", "RESUME", "RETIRE")


class SkillCatalogueRefused(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _shared(service: Any) -> Any:
    native = getattr(service, "_native", None)
    shared = getattr(native, "shared_catalogue", None)
    if shared is None or shared.owner_id is None:
        raise SkillCatalogueRefused("native_plane_unavailable", "技能目录需要原生运行平面（当前没有装配）")
    return shared


def overview(service: Any) -> dict[str, Any]:
    return _shared(service).overview()


def _pin(raw: Any) -> Any:
    from simple_harness.agents.arp.pins import Pin

    try:
        return Pin.from_json(dict(raw))
    except Exception as error:  # noqa: BLE001 - malformed reference from the client
        raise SkillCatalogueRefused("invalid_request", "skill_ref 不是有效的技能引用") from error


async def install_file(service: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    raw = request.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise SkillCatalogueRefused("invalid_request", "请给出技能包文件的完整路径")
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        raise SkillCatalogueRefused("invalid_request", "请填写完整路径（从 / 开始）")
    if path.suffix.lower() != ".zip" or path.is_symlink() or not path.is_file():
        raise SkillCatalogueRefused("invalid_request", "技能包必须是一个 .zip 文件")
    size = path.stat().st_size
    if size > MAX_BUNDLE_BYTES:
        raise SkillCatalogueRefused("invalid_request", "技能包超过 32MB")
    data = path.read_bytes()
    from simple_harness.agents.arp.pins import Pin
    from simple_harness.agents.arp.skills import inspect_bundle
    from simple_harness.agents.arp.errors import ArpError

    shared = _shared(service)
    try:
        fmt = "NATIVE" if inspect_bundle(data).skill_json is not None else "SKILL_MD"
    except ArpError as error:
        raise SkillCatalogueRefused("invalid_request", f"技能包无法解析：{error.code}") from error
    store = service._orchestrator.assembled.workspaces.artifact_store
    content_hash = store.put_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    if content_hash != expected:
        raise SkillCatalogueRefused("internal", "技能包写入产物库后哈希不一致")
    ref = Pin("artifact", "bundle:" + expected[:12], 0, expected)
    caller = service._native.control_caller({"command": "skill_install_file", "bundle": expected})
    response = await shared.install(ref, data_format=fmt, caller=caller, command_id=f"skill-install:{expected[:32]}")
    service.wake()
    return {"response": response, "catalogue": shared.overview()}


async def lifecycle(service: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    action = request.get("action")
    if action not in ACTIONS:
        raise SkillCatalogueRefused("invalid_request", "action 只能是 SUSPEND / RESUME / RETIRE")
    command_id = request.get("command_id")
    if not isinstance(command_id, str) or not command_id.strip():
        raise SkillCatalogueRefused("invalid_request", "command_id 不能为空")
    reason = request.get("reason") if isinstance(request.get("reason"), str) and request.get("reason") else "用户在任务编排页操作"
    shared = _shared(service)
    pin = _pin(request.get("skill_ref"))
    caller = service._native.control_caller({"command_id": command_id, "action": action, "skill_ref": pin.to_json()})
    response = await shared.lifecycle(pin, str(action), caller=caller, command_id=command_id, reason=str(reason)[:500])
    service.wake()
    return {"response": response, "catalogue": shared.overview()}


async def request(service: Any, body: Mapping[str, Any]) -> dict[str, Any]:
    from simple_harness.agents.arp.shared_catalogue import SKILL_READ_VERBS, SKILL_WRITE_VERBS

    verb = body.get("verb") if isinstance(body, Mapping) else None
    if verb not in SKILL_READ_VERBS | SKILL_WRITE_VERBS:
        raise SkillCatalogueRefused("invalid_request", "这个入口只接受技能目录的请求")
    shared = _shared(service)
    response = await shared.handle(dict(body), caller=service._native.control_caller(dict(body)))
    service.wake()
    return response


__all__ = ("ACTIONS", "MAX_BUNDLE_BYTES", "SkillCatalogueRefused", "install_file", "lifecycle", "overview", "request")
