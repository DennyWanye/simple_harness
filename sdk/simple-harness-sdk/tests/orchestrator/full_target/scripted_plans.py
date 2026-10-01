# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Scripted typed plan proposals, for tests that commit a plan at the dispatch level.

The Planner's wire format is the planning Decision; what the compiler and the commit
consume is the typed :class:`PlanProposal` the decision adapter produces.  A test that
is about the compile/commit half scripts that typed proposal directly, as JSON, and
hands it to :meth:`HierarchicalDispatch.apply_plan_proposal` — no model text is parsed
anywhere in production for it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from agent_orchestrator.contracts.htn import PlanProposal

_OPEN = "<plan_revision_proposal>"
_CLOSE = "</plan_revision_proposal>"


def plan_revision_proposal_step(
    *,
    proposal_id: str = "prop-1",
    expected_plan_revision: int = 0,
    read_set: Sequence[Mapping[str, Any]] = (),
    operations: Sequence[Mapping[str, Any]] = (),
    rationale: str = "脚本化提案",
    trigger_refs: Sequence[Mapping[str, Any]] = (),
    running_work_policy: str = "retain_if_bindings_unchanged",
    schema_version: Any = 1,
    extras: Mapping[str, Any] | None = None,
    drop: Sequence[str] = (),
) -> str:
    """One scripted plan proposal body, as text.

    Deliberately unvalidated: shaping the JSON is the caller's business, and ``extras``
    / ``drop`` exist to script a body the typed contract must refuse.  ``mission_id`` is
    never written — :func:`scripted_plan_proposal` supplies it, as the request that
    produced a proposal does in production.
    """

    body: dict[str, Any] = {
        "schema_version": schema_version,
        "proposal_id": proposal_id,
        "expected_plan_revision": expected_plan_revision,
        "trigger_refs": [dict(ref) for ref in trigger_refs],
        "read_set": [dict(item) for item in read_set],
        "operations": [dict(operation) for operation in operations],
        "rationale": rationale,
        "running_work_policy": running_work_policy,
    }
    body.update({key: value for key, value in dict(extras or {}).items()})
    for key in drop:
        body.pop(key, None)
    return _OPEN + json.dumps(body, ensure_ascii=False) + _CLOSE


def scripted_plan_proposal(text: str, *, mission_id: str) -> PlanProposal:
    """Decode one :func:`plan_revision_proposal_step` body into the typed proposal."""

    start, end = text.index(_OPEN) + len(_OPEN), text.rindex(_CLOSE)
    return PlanProposal.from_json({**json.loads(text[start:end]), "mission_id": mission_id})


def apply_scripted_plan(
    dispatch: Any,
    mission_id: str,
    text: str,
    *,
    principal: Any,
    command_id: str,
    source: Mapping[str, Any] | None = None,
    owner: str | None = None,
) -> Any:
    """Compile and commit one scripted proposal through the production dispatch."""

    return dispatch.apply_plan_proposal(
        mission_id,
        scripted_plan_proposal(text, mission_id=mission_id),
        principal=principal,
        command_id=command_id,
        source=source,
        owner=owner,
        proposal_text=text,
    )


def approve_content_only_completion(service: Any, mission: Any, binding: Any, *,
                                    command_id: str) -> Any:
    """Publish and confirm the root's requirement contract (CONTENT_ONLY).

    A hierarchical Mission cannot publish a dispatchable plan without an approved
    completion mapping; this is the person's confirmation of the frozen root criteria,
    through the production API — never an inferred fallback.
    """

    from agent_orchestrator.api.operation_completion import OperationCompletionApi
    from agent_orchestrator.governance.permissions import Principal
    from agent_orchestrator.orchestrator.root_review import root_requirements
    from agent_orchestrator.storage.htn_store import HtnStore

    requirements = root_requirements(mission.id, binding, revision=1)
    HtnStore(service.store).insert_requirements_revision(requirements)
    reference = {
        "id": str(requirements.revision_id),
        "revision": int(requirements.revision),
        "content_hash": requirements.content_hash(),
    }
    OperationCompletionApi(
        service, tenant_id=mission.tenant_id, principal=Principal("human-completion-fixture")
    ).approve(
        {
            "mission_id": mission.id,
            "command_id": command_id,
            "expected_requirements_ref": {"kind": "requirements", **reference},
            "proposal": {
                "schema_version": 1,
                "mission_id": mission.id,
                "requirements_ref": reference,
                "mode": "CONTENT_ONLY",
                "content_criterion_ids": list(requirements.required_criterion_ids()),
                "effects": [],
            },
        }
    )
    return requirements


def detach_completion_protocol(store: Any, mission_id: str) -> None:
    """Take a hierarchical fixture Mission off the completion protocol (a test seam).

    DEBT (2026-10-01, "验收双路径"): about a hundred sites still branch on
    ``uses_completion_protocol`` — "does this Mission hold a planning-protocol binding" —
    and the fixtures of roughly ninety test files exercise the shared plan / readiness /
    DATA / resolution mechanics on the branch *without* one.  Production can no longer
    create such a Mission (every hierarchical Mission is bound at creation, and the loop
    stops an unbound one by name), so this seam removes the row for those fixtures until
    they are migrated to the completion protocol and the other branch is deleted.
    A Mission detached here must never be run through ``Orchestrator.run``.
    """

    store.connection.execute(
        "DELETE FROM mission_planning_protocols WHERE mission_id = ?", (mission_id,)
    )


__all__ = (
    "apply_scripted_plan",
    "approve_content_only_completion",
    "detach_completion_protocol",
    "plan_revision_proposal_step",
    "scripted_plan_proposal",
)
