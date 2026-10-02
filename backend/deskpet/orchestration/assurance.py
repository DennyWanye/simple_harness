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
import logging
from collections import deque
from collections.abc import Mapping
from typing import Any

from .service import OrchestrationRequestError

logger = logging.getLogger(__name__)

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

    Returns the SDK's ``InstalledAssurance``.
    """
    from agent_orchestrator.assurance.policy import AssurancePolicy
    from agent_orchestrator.orchestrator.assurance_assembly import (
        AssuranceDeploymentPorts,
        install_assurance as sdk_install,
    )
    from .hierarchical import root_requirements

    notices: deque[dict[str, Any]] = service._assurance_notices

    def select_profile(spec: Any) -> Any:
        # The single Host selection point: every planning-decision Mission of this
        # tenant is assured. There is no opt-out (片 D 第 6 项, 2026-10-02): the lane
        # without Assurance was only reachable through one, and it is gone.
        return AssurancePolicy()

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
        select_profile=select_profile,
        notify_transport=notify,
        host_fingerprint=host_fingerprint(),
        # Projects each frozen Scope's check policy right before its first review
        # (the after-run projection in ``service._drive`` is the catch-up path).
        check_policy_projector=lambda mission_id: project_check_policies(service, mission_id=mission_id),
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


def project_check_policies(service: Any, mission_id: str | None = None) -> int:
    """Approve, under the Host's authenticated caller, the lossless check-policy
    mapping for every frozen completion Scope of an assured Mission that has none.

    The person already confirmed the requirements mapping (the completion Spec);
    each Scope the plan later freezes needs the SDK's per-Scope check policy before
    its content review can run (``CHECK_POLICY_UNRESOLVED`` otherwise — Host real
    model run 2, 2026-09-23). The mapping is the SDK's lossless projection of the
    original requirements (semantic criteria → SEMANTIC, named checks → the exact
    registered CheckSpecs); the Host adds nothing and drops nothing. One command
    per Scope, replay-safe through the SDK's own approval receipt.
    """
    orchestrator = service._orchestrator
    if service._assurance is None or orchestrator is None:
        return 0
    from agent_orchestrator.assurance.codec import AssuranceError, fingerprint
    from agent_orchestrator.orchestrator.assurance_check_policy import (
        lossless_scope_mapping,
        mission_final_scope_id,
    )
    from agent_orchestrator.storage.assurance_store import AssuranceStore

    store = orchestrator.store
    done: set[str] = service._assurance_policy_scopes
    sql = "SELECT mission_id, scope_id, document_json FROM operation_completion_scopes"
    args: tuple[Any, ...] = ()
    if mission_id is not None:
        sql += " WHERE mission_id=?"
        args = (mission_id,)
    rows = store.connection.execute(sql + " ORDER BY created_at_ms, scope_id", args).fetchall()
    def approve(mid: str, scope_id: str, purpose: str, effect_key: str | None = None) -> bool:
        # One command per (Scope, purpose[, effect]); the SDK approval receipt makes
        # replays free. MISSION_FINAL is the root review's own domain (root Scope + the
        # whole root requirements) — Host real model run 15, 2026-09-23. The two
        # operation reviews live on the effect owner's Scope (NEXT-TG-1.0, 2026-09-27:
        # an assured publish was refused CHECK_POLICY_UNRESOLVED at submission).
        key = {"CONTENT": scope_id, "MISSION_FINAL": f"mission-final:{scope_id}",
               "ACTION_PROPOSAL": f"action-proposal:{scope_id}",
               "OPERATION_OUTCOME": f"operation-outcome:{scope_id}:{effect_key}"}[purpose]
        if key in done:
            return False
        command_id = f"host-check-policy:{key}"
        receipt_id = "assurance-check-policy-approval:" + fingerprint({
            "mission": mid, "tenant": service.tenant_id,
            "principal": service._principal.principal_id, "command": command_id,
        })
        if store.get_receipt(receipt_id) is not None:
            done.add(key)
            return False
        try:
            requirements_ref, scope_ref, mapping = lossless_scope_mapping(
                orchestrator.commit, mission_id=mid, scope_id=scope_id, purpose=purpose,
                effect_key=effect_key)
            command = {
                "mission_id": mid, "command_id": command_id,
                "requirements_ref": requirements_ref.to_json(),
                "completion_scope": scope_ref.to_json(),
                "candidate_mapping": [policy.to_json() for policy in mapping],
                # 2026-09-25: this is the Host approving on the principal's behalf, and
                # the audit event says so (actor_type=system), not "a human approved".
                "approval_source": "HOST_LOSSLESS_AUTO",
            }
            if purpose != "CONTENT":
                command["purpose"] = purpose
            if effect_key is not None:
                command["effect_key"] = effect_key
            service._call("approve_assurance_check_policy", command)
        except Exception as error:  # noqa: BLE001 - one Scope must never stop the round
            # Retried on the next round; a Scope whose projection is not current
            # yet (or never resolvable) is reported, never guessed. 2026-09-27: an old
            # Mission's stale plan raised OperationCompletionError here and the whole
            # round stopped, so a new Mission got no policy at all.
            logger.warning("assurance %s check policy not projected for %s: %s: %s",
                           purpose, scope_id, type(error).__name__, error)
            return False
        done.add(key)
        return True

    approved = 0
    assured: set[str] = set()
    unassured: set[str] = service._assurance_unassured_missions
    for row in rows:
        mid, scope_id = str(row[0]), str(row[1])
        if mid in unassured:
            continue
        if mid not in assured:
            mission = store.get_mission(mid)
            if mission is None or str(getattr(mission.status, "value", mission.status)) in {
                    "COMPLETED", "FAILED", "CANCELLED"}:
                unassured.add(mid)  # a finished Mission needs no new policy
                continue
            try:
                if AssuranceStore(store).lane(mid) != "ASSURANCE_1_1":
                    unassured.add(mid)
                    continue
            except Exception:  # noqa: BLE001 - a Mission without a lane row is not assured
                unassured.add(mid)
                continue
            assured.add(mid)
        approved += approve(mid, scope_id, "CONTENT")
        owned = tuple(json.loads(row[2]).get("owned_effect_keys") or ())
        if owned:
            approved += approve(mid, scope_id, "ACTION_PROPOSAL")
            for effect_key in owned:
                approved += approve(mid, scope_id, "OPERATION_OUTCOME", str(effect_key))
    for mid in sorted(assured):
        try:
            if AssuranceStore(store).lane(mid) != "ASSURANCE_1_1":
                continue
        except Exception:  # noqa: BLE001 - not assured
            continue
        root_scope = mission_final_scope_id(orchestrator, mid)
        if root_scope is not None:
            approved += approve(mid, root_scope, "MISSION_FINAL")
    approved += _project_method_plan_policies(service, mission_id, unassured)
    return approved


def _project_method_plan_policies(service: Any, mission_id: str | None, unassured: set[str]) -> int:
    """Approve the METHOD_PLAN check policy for every goal a method may be proposed for.

    2026-10-01 (HTN 精简 片 A): the Planner proposes its own methods, and a proposed
    method is adopted only after its independent review.  That review is prepared
    inside the Planner decision's own transaction, where this projector cannot run,
    so the policy has to be there *before* the Planner is asked: from Mission creation
    for the root goal (no plan and no Scope exist yet), and from the commit of the plan
    that introduced it for a sub-goal.  The policy is approved on the exact goal Task
    (its contract revision and hash), by the SDK's own lossless mapping of the
    requirements that goal covers; the Host adds nothing and drops nothing.
    """
    from agent_orchestrator.orchestrator.assurance_check_policy import (
        lossless_planning_subject_mapping,
    )
    from agent_orchestrator.storage.assurance_store import AssuranceStore
    from agent_orchestrator.storage.htn_store import HtnStore

    orchestrator = service._orchestrator
    store = orchestrator.store
    done: set[str] = service._assurance_policy_scopes
    sql = ("SELECT mission_id FROM missions WHERE status NOT IN ('COMPLETED','FAILED','CANCELLED')")
    args: tuple[Any, ...] = ()
    if mission_id is not None:
        sql += " AND mission_id=?"
        args = (mission_id,)
    approved = 0
    for (mid,) in store.connection.execute(sql + " ORDER BY created_at, mission_id", args).fetchall():
        mid = str(mid)
        if mid in unassured:
            continue
        try:
            if AssuranceStore(store).lane(mid) != "ASSURANCE_1_1":
                unassured.add(mid)
                continue
        except Exception:  # noqa: BLE001 - a Mission without a lane row is not assured
            unassured.add(mid)
            continue
        htn = HtnStore(store)
        requirements = htn.latest_requirements_revision(mid)
        # The policy is approved against one requirements revision; a revised requirements
        # document needs its own approval, so the revision is part of what "done" means.
        revision = 0 if requirements is None else int(requirements.revision)
        for binding in htn.list_task_semantics(mid, form="compound"):
            key = f"method-plan:{binding.task_id}:{int(binding.contract_revision)}:r{revision}"
            if key in done:
                continue
            try:
                requirements_ref, subject_ref, mapping = lossless_planning_subject_mapping(
                    orchestrator.commit, mission_id=mid, task_id=str(binding.task_id))
                service._call("approve_assurance_check_policy", {
                    "mission_id": mid, "command_id": f"host-check-policy:{key}",
                    "requirements_ref": requirements_ref.to_json(),
                    "planning_subject": subject_ref.to_json(),
                    "candidate_mapping": [policy.to_json() for policy in mapping],
                    "purpose": "METHOD_PLAN", "approval_source": "HOST_LOSSLESS_AUTO"})
            except Exception as error:  # noqa: BLE001 - one goal must never stop the round
                if key not in service._assurance_policy_warned:  # retried every round; said once
                    service._assurance_policy_warned.add(key)
                    logger.warning("assurance METHOD_PLAN check policy not projected for %s: %s: %s",
                                   binding.task_id, type(error).__name__, error)
                continue
            done.add(key)
            approved += 1
    return approved
