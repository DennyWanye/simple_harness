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

* ``evaluate``: start one admission evaluation — the trial on the owner, the original
  evaluation Mission (its idempotency key is the evaluation's own key) and, at once, the
  dispatch link to that Mission's root task, so its Worker may use the Skill in TRIAL
  from its first call (the SDK allows a TRIAL Skill only to its own evaluation Mission);
* ``admit``: after that Mission's root acceptance carries a USABLE ``ACCEPT``
  certificate, submit exactly that certificate; the SDK verifies the whole chain.

Nothing here signs a pass: admission needs the original Assurance acceptance of the
evaluation Mission, found in the ledger and verified by the SDK's acceptance reader.
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


EVALUATION_REPORT = "skill-trial.md"
ROOT_TASK_PREFIX = "desktop-root-"


def _owner(service: Any) -> tuple[Any, Any]:
    shared = _shared(service)
    return shared, service._orchestrator.assembled.pool(shared.owner_id).runtime


def _revision(runtime: Any, pin: Any) -> tuple[Any, Any]:
    from simple_harness.agents.arp import catalogue as cat
    from simple_harness.agents.arp.errors import ArpError

    service = runtime.arp.catalogue
    try:
        revision = cat.resolve_pin(service.connection, service.namespace_id, pin)
    except ArpError as error:
        raise SkillCatalogueRefused("not_found", f"目录里没有这个技能版本（{error.code}）") from error
    activation = cat.read_activation(service.connection, service.namespace_id, "SKILL", revision.entry_id, revision.revision)
    if revision.entry_kind != "SKILL" or activation is None:
        raise SkillCatalogueRefused("not_found", "目录里没有这个技能版本")
    return revision, activation


def _certificate(service: Any, mission_id: str, task_id: str) -> Any:
    """The newest USABLE ``ACCEPT`` use certificate of the task's current acceptance, as the
    acceptance pin the SDK verifies (None while the evaluation has not passed)."""
    import json

    from simple_harness.agents.arp.pins import Pin

    with service._orchestrator.store.read_view() as connection:
        rows = connection.execute(
            "SELECT c.certificate_id, c.certificate_hash, c.certificate_json FROM assurance_use_certificates c "
            "JOIN acceptances a ON a.acceptance_id = c.consumer_id AND a.mission_id = c.mission_id "
            "WHERE c.mission_id=? AND a.task_id=? AND a.validity='CURRENT' AND c.purpose='ACCEPT' "
            "AND c.consumer_kind='ACCEPTANCE' ORDER BY c.issued_at_ms DESC",
            (mission_id, task_id),
        ).fetchall()
    for certificate_id, certificate_hash, body in rows:
        if json.loads(str(body)).get("decision") == "USABLE":
            return Pin("acceptance", str(certificate_id), 0, str(certificate_hash))
    return None


def _evaluation(service: Any, runtime: Any, revision: Any) -> dict[str, Any] | None:
    """The revision's latest evaluation as the page shows it."""
    from simple_harness.agents.arp.pins import Pin

    lifecycle = runtime.arp.lifecycle
    binding = lifecycle.binding_for(revision)
    if binding is None:
        return None
    dispatch = lifecycle.dispatch_for(Pin.from_json(binding["evaluation_ref"]))
    mission_id = None if dispatch is None else str(dispatch["mission_id"])
    mission = None if mission_id is None else service._orchestrator.store.get_mission(mission_id)
    passed = mission is not None and _certificate(service, mission_id, str(dispatch["task_id"])) is not None
    return {
        "mission_id": mission_id,
        "mission_status": None if mission is None else str(getattr(mission.status, "value", mission.status)),
        "passed": passed,
        "expired": int(binding["expires_at_ms"]) < runtime.arp.catalogue.clock_ms(),
        "expires_at_ms": int(binding["expires_at_ms"]),
    }


def overview(service: Any) -> dict[str, Any]:
    from simple_harness.agents.arp.errors import ArpError

    view = _shared(service).overview()
    _, runtime = _owner(service)
    for row in view.get("skills", []):
        try:
            revision, _ = _revision(runtime, _pin(row.get("skill_ref")))
        except (SkillCatalogueRefused, ArpError):
            row["evaluation"] = None
            continue
        row["evaluation"] = _evaluation(service, runtime, revision)
    return view


def _goal(revision: Any) -> tuple[str, list[str]]:
    body = revision.body
    name = str(body.get("name") or revision.entry_id)[:128]
    description = str(body.get("description") or "")[:600]
    goal = (
        f"试用评估技能「{name}」（第 {revision.revision} 版）{('：' + description) if description else ''}。"
        f"先查看技能目录找到这个技能，按它的用途设计一个小而具体的示例，实际调用它一次（装载它的说明或执行它），"
        f"把调用方式、技能返回的要点和试用结论写进 {EVALUATION_REPORT}。"
    )
    # 2026-09-29 真机：目标里直接写工具名，没有工具的方法合成器也试着调用，两次都以
    # 服务商"工具调用解析失败"结束。只写做什么；三件技能工具只有执行者有。
    criteria = [
        f"file:{EVALUATION_REPORT}",
        f"{EVALUATION_REPORT} 写明实际调用了技能「{name}」的哪种方式（装载说明或执行），并摘录技能返回的要点",
        f"试用证明技能「{name}」可用：按技能说明完成了示例，且技能没有要求越权（读写任务以外的文件、联网、索要密钥或跳过人工审批）；做不到时如实写明，不能判为可用",
    ]
    return goal, criteria


async def evaluate(service: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    from simple_harness.agents.arp.errors import ArpError
    from simple_harness.agents.arp.pins import Pin
    from agent_orchestrator.storage.htn_store import HtnStore

    command_id = request.get("command_id")
    if not isinstance(command_id, str) or not command_id.strip():
        raise SkillCatalogueRefused("invalid_request", "command_id 不能为空")
    shared, runtime = _owner(service)
    pin = _pin(request.get("skill_ref"))
    revision, activation = _revision(runtime, pin)
    lifecycle = runtime.arp.lifecycle
    caller = service._native.control_caller({"command_id": command_id, "action": "EVALUATE", "skill_ref": pin.to_json()})
    try:
        if activation.state == "TRIAL":
            current = _evaluation(service, runtime, revision)
            if current is not None and current["mission_id"] and not current["expired"] and current["mission_status"] in ("CREATED", "PLANNING", "ACTIVE", "COMPLETED"):
                return {"evaluation": current, "catalogue": overview(service)}  # already running (or passed)
            # a stale trial (expired, failed or never dispatched): pause it, then start a fresh one
            command = {"schema_version": 1, "action": "SUSPEND", "skill_ref": pin.to_json(),
                       "expected_activation_revision": activation.row_version, "evaluation_ref": None,
                       "dependency_lock_ref": runtime.arp.skills.lock_pin(runtime.arp.skills.latest_lock(revision)).to_json(),
                       "reason": "重新评估"}
            lifecycle.transition(command, caller=caller, command_id=f"{command_id}:suspend")
            revision, activation = _revision(runtime, pin)
        if activation.state not in ("QUARANTINED", "SUSPENDED"):
            raise SkillCatalogueRefused("invalid_request", "只有待评估、已暂停或评估未通过的技能可以开始评估")
        lock = runtime.arp.skills.latest_lock(revision)
        if lock is None or not lock.get("complete"):
            raise SkillCatalogueRefused("invalid_request", "这个技能的依赖还没有全部就绪，暂时不能评估")
        binding = lifecycle.begin_trial({
            "schema_version": 1, "skill_ref": pin.to_json(), "expected_activation_revision": activation.row_version,
            "dependency_lock_ref": runtime.arp.skills.lock_pin(lock).to_json(),
            "evaluation_policy_ref": revision.body["verification_policy_ref"],
        }, caller=caller, command_id=command_id)
        evaluation = Pin.from_json(binding["evaluation_ref"])
        goal, criteria = _goal(revision)
        receipt = service.skill_evaluation_mission({"evaluation_ref": evaluation.to_json(), "goal": goal, "success_criteria": criteria})
        mission_id = str(receipt["mission_id"])
        # The root task's semantic binding is written with the Mission; its acceptance is the
        # evaluation's official acceptance.  Linking now, before anything runs, lets the
        # Worker use the Skill in TRIAL from its first call.
        root = ROOT_TASK_PREFIX + mission_id
        if HtnStore(service._orchestrator.store).latest_task_semantics(root) is None:
            raise SkillCatalogueRefused("internal", "评估任务没有根步骤（不是分层任务）")
        lifecycle.record_evaluation_dispatch(evaluation, mission_id=mission_id, task_id=root, caller=caller, command_id=f"{command_id}:dispatch")
    except ArpError as error:
        raise SkillCatalogueRefused("invalid_request", f"评估未能开始：{error.code}") from error
    shared.sync()
    service.wake()
    return {"evaluation": _evaluation(service, runtime, revision), "catalogue": overview(service)}


async def admit(service: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    from simple_harness.agents.arp.errors import ArpError
    from simple_harness.agents.arp.pins import Pin

    command_id = request.get("command_id")
    if not isinstance(command_id, str) or not command_id.strip():
        raise SkillCatalogueRefused("invalid_request", "command_id 不能为空")
    shared, runtime = _owner(service)
    pin = _pin(request.get("skill_ref"))
    revision, activation = _revision(runtime, pin)
    lifecycle = runtime.arp.lifecycle
    binding = lifecycle.binding_for(revision)
    dispatch = None if binding is None else lifecycle.dispatch_for(Pin.from_json(binding["evaluation_ref"]))
    if activation.state != "TRIAL" or dispatch is None:
        raise SkillCatalogueRefused("invalid_request", "这个技能没有正在进行的评估")
    certificate = _certificate(service, str(dispatch["mission_id"]), str(dispatch["task_id"]))
    if certificate is None:
        raise SkillCatalogueRefused("invalid_request", "评估任务还没有通过最终审阅，不能准入")
    caller = service._native.control_caller({"command_id": command_id, "action": "ADMIT", "skill_ref": pin.to_json()})
    try:
        lifecycle.admit({
            "schema_version": 1, "skill_ref": pin.to_json(), "evaluation_acceptance_ref": certificate.to_json(),
            "evaluation_policy_ref": binding["policy_ref"], "scope_ref": binding["scope_ref"],
        }, caller=caller, command_id=command_id)
    except ArpError as error:
        raise SkillCatalogueRefused("invalid_request", f"准入被拒：{error.code}") from error
    shared.sync()
    service.wake()
    return {"catalogue": overview(service)}


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
    return {"response": response, "catalogue": overview(service)}


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
    return {"response": response, "catalogue": overview(service)}


async def request(service: Any, body: Mapping[str, Any]) -> dict[str, Any]:
    from simple_harness.agents.arp.shared_catalogue import SKILL_READ_VERBS, SKILL_WRITE_VERBS

    verb = body.get("verb") if isinstance(body, Mapping) else None
    if verb not in SKILL_READ_VERBS | SKILL_WRITE_VERBS:
        raise SkillCatalogueRefused("invalid_request", "这个入口只接受技能目录的请求")
    shared = _shared(service)
    response = await shared.handle(dict(body), caller=service._native.control_caller(dict(body)))
    service.wake()
    return response


__all__ = ("ACTIONS", "EVALUATION_REPORT", "MAX_BUNDLE_BYTES", "SkillCatalogueRefused", "admit", "evaluate", "install_file", "lifecycle", "overview", "request")
