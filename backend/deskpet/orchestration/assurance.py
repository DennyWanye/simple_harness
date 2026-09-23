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
    from agent_orchestrator.orchestrator.assurance_check_policy import lossless_scope_mapping
    from agent_orchestrator.storage.assurance_store import AssuranceStore

    store = orchestrator.store
    done: set[str] = service._assurance_policy_scopes
    sql = "SELECT mission_id, scope_id FROM operation_completion_scopes"
    args: tuple[Any, ...] = ()
    if mission_id is not None:
        sql += " WHERE mission_id=?"
        args = (mission_id,)
    rows = store.connection.execute(sql + " ORDER BY created_at_ms, scope_id", args).fetchall()
    approved = 0
    for row in rows:
        mid, scope_id = str(row[0]), str(row[1])
        if scope_id in done:
            continue
        try:
            if AssuranceStore(store).lane(mid) != "ASSURANCE_1_1":
                done.add(scope_id)
                continue
        except Exception:  # noqa: BLE001 - a Mission without a lane row is not assured
            done.add(scope_id)
            continue
        command_id = f"host-check-policy:{scope_id}"
        receipt_id = "assurance-check-policy-approval:" + fingerprint({
            "mission": mid, "tenant": service.tenant_id,
            "principal": service._principal.principal_id, "command": command_id,
        })
        if store.get_receipt(receipt_id) is not None:
            done.add(scope_id)
            continue
        try:
            requirements_ref, scope_ref, mapping = lossless_scope_mapping(
                orchestrator.commit, mission_id=mid, scope_id=scope_id)
            service._call("approve_assurance_check_policy", {
                "mission_id": mid, "command_id": command_id,
                "requirements_ref": requirements_ref.to_json(),
                "completion_scope": scope_ref.to_json(),
                "candidate_mapping": [policy.to_json() for policy in mapping],
            })
        except (AssuranceError, OrchestrationRequestError) as error:
            # Retried on the next round; a Scope whose projection is not current
            # yet (or never resolvable) is reported, never guessed.
            logger.warning("assurance check policy not projected for %s: %s", scope_id, error)
            continue
        done.add(scope_id)
        approved += 1
    return approved
