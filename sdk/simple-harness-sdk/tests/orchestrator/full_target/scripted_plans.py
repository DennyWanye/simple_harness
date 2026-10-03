# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Scripted typed plan proposals, for tests that commit a plan at the dispatch level.

The Planner's wire format is the planning Decision; what the compiler and the commit
consume is the typed :class:`PlanProposal` the decision adapter produces.  A test that
is about the compile/commit half scripts that typed proposal directly, as JSON, and
commits it under a planning admission (:mod:`admitted_plans`) — no model text is parsed
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
    """Compile one scripted proposal and commit it under a planning admission.

    See :mod:`admitted_plans`: the plan is compiled with the compiler's own functions
    and committed through ``commit_planning_revision``, the only entry a plan revision
    has.  ``owner`` is accepted for the callers that name the loop's owner; a scripted
    commit holds no lease.
    """

    from admitted_plans import apply_admitted_plan

    del owner
    return apply_admitted_plan(
        dispatch,
        mission_id,
        scripted_plan_proposal(text, mission_id=mission_id),
        principal=principal,
        command_id=command_id,
        source=source,
    )


def approve_content_only_completion(service: Any, mission: Any, binding: Any, *,
                                    command_id: str, delivery: str | None = None,
                                    statements: Mapping[str, str] | None = None) -> Any:
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
    if delivery is not None:  # the goal declares a delivery contract
        import dataclasses

        requirements = dataclasses.replace(requirements, delivery_contract_ref=delivery)
    if statements:
        # 用户写的要求原文：以 ``file:`` / ``pytest:`` 开头的，承担它的步骤会带上这条检查
        # （产品里就是这样从要求原文落到步骤判据上的）。
        import dataclasses

        requirements = dataclasses.replace(requirements, criteria=tuple(
            dataclasses.replace(item, statement=statements[item.criterion_id])
            if item.criterion_id in statements else item
            for item in requirements.criteria))
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


def seed_verified_result(
    service: Any,
    dispatch: Any,
    mission_id: str,
    task_id: str,
    *,
    result_id: str,
    layers: Sequence[Any],
    items: Sequence[Any] = (),
    claims: Sequence[Any] = (),
    now_ms: int = 1_000_000,
    complete_row: bool = True,
    bodies: Mapping[str, bytes] | None = None,
) -> tuple[Any, ...]:
    """One real, verified result for a leaf, written in the production order.

    A Mission on the completion protocol accepts only a stored, verified result: an
    Attempt whose inputs are the frozen manifest's upstream outputs, the result it
    handed back (real bytes in the content store, each claimed for a declared port),
    one verification record per layer, then the result and its outputs marked
    verified and the Attempt closed.  ``items`` carry ``id`` / ``path`` / ``version``;
    ``bodies`` gives the bytes of an item by id (a small default otherwise).  Returns
    the stored artifacts, in order.  A result id is seeded once.
    """

    import hashlib

    from agent_orchestrator.artifacts.versioning import manifest_upstream_inputs
    from agent_orchestrator.contracts import (
        Artifact,
        AttemptStatus,
        ClaimProposal,
        ResultEnvelope,
        TaskStatus,
    )
    from agent_orchestrator.orchestrator.commit_service import Reservation
    from agent_orchestrator.orchestrator.state_machine import next_attempt, next_task

    store = service.store
    if store.get_result(result_id) is not None:
        return tuple(store.get_artifact(item.id) for item in items)
    # The loop re-issues input witnesses before every dispatch; a step that consumes an
    # earlier output has its input manifest frozen by that.
    network = dispatch.network(mission_id)
    dispatch.issue_input_witnesses(mission_id, network, now_ms=now_ms)
    spec = next(item for item in network.occurrences if str(item.task_id) == task_id)
    manifest = dispatch.resolved_inputs(mission_id, network, spec).manifest
    upstream = (
        ()
        if manifest is None or not manifest.is_frozen
        else dispatch.overlay_attempt_inputs(
            mission_id,
            manifest_upstream_inputs(manifest, dispatch.target_rules_for(task_id), network=network),
        )
    )
    attempt, intent = service.create_attempt(
        task_id,
        role="worker",
        model="fixture-worker",
        prompt_version="fixture-worker-v1",
        context_version="fixture-v1",
        reservation=Reservation(tokens=1_000),
        intent_config={"message": "do the leaf"},
        input_hash="a" * 64,
        inputs=tuple(item.to_json() for item in upstream),
    )
    turn = f"turn-{result_id}"
    service.claim_intent(intent.intent_id, owner="fixture-orchestrator", lease_seconds=60)
    service.record_agent_created(intent.intent_id, agent_id="agent-worker", expected_turn_id=turn)
    service.record_submitted(intent.intent_id, receipt={"turn_id": turn, "seq": 1})
    cas = service._source_artifact_store
    files = []
    for item in items:
        body = (bodies or {}).get(item.id)
        if body is None:
            body = f"{item.id}:{item.path}\n".encode()
        digest = cas.put_bytes(body)
        files.append(
            Artifact(
                id=item.id,
                mission_id=mission_id,
                task_id=task_id,
                attempt_id=attempt.id,
                type="file",
                path=item.path,
                version=int(item.version),
                content_hash=hashlib.sha256(body).hexdigest(),
                size_bytes=len(body),
                produced_by="agent-worker",
                storage_uri=str(cas.path_for(digest)),
            )
        )
    service.record_result(
        attempt.id,
        turn_id=turn,
        artifacts=tuple(files),
        usage_refs=(),
        port_claims=tuple(claims),
        envelope=ResultEnvelope(
            id=result_id,
            mission_id=mission_id,
            task_id=task_id,
            attempt_id=attempt.id,
            outcome="candidate",
            summary="leaf done",
            claims=(ClaimProposal(content="leaf done", confidence=0.9),),
            evidence=(),
            artifacts=tuple(item.path for item in files),
            proposed_tasks=(),
            used_knowledge=(),
            risks=(),
            cost={},
        ),
    )
    # The loop closes the dispatch once the turn's result is collected.
    service.settle_intent(intent.intent_id, "SETTLED")
    service.start_verification(result_id)
    for layer in layers:
        service.record_verification_layer(
            result_id, layer=layer.layer, status=layer.status, detail={"producer": "fixture"}
        )
    store.set_result_verification(result_id, state="DONE", verdict="PASS")
    for item in files:
        store.update_artifact_verification(item.id, "VERIFIED")
    current = store.get_attempt(attempt.id)
    store.update_attempt(
        next_attempt(current, AttemptStatus.COMPLETED), expected_version=current.version
    )
    if complete_row:
        row = store.get_task(task_id)
        store.update_task(
            next_task(
                row,
                TaskStatus.COMPLETED,
                accepted_result_id=result_id,
                accepted_artifacts=tuple(item.id for item in files),
            ),
            expected_version=row.version,
        )
    return tuple(store.get_artifact(item.id) for item in files)


__all__ = (
    "apply_scripted_plan",
    "approve_content_only_completion",
    "plan_revision_proposal_step",
    "scripted_plan_proposal",
    "seed_verified_result",
)
