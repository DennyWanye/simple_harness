# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Commit one scripted proposal: compile it, admit it, commit it.

A plan revision reaches the store only through ``commit_planning_revision`` — the
commit under a planning authorization and the producer snapshots it was previewed
against.  A test that is about what happens *after* a plan lands still needs a plan,
so this helper stands in for the Planner turn of a fixture world (one without the
execution-graph assembly): it reads the same things the loop reads, compiles the one
refinement with the compiler's own functions, issues the grant a person (or the auto
mode) would have issued, and commits under that admission.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_orchestrator.contracts.htn import PlanProposal


def compile_scripted(dispatch: Any, mission_id: str, proposal: PlanProposal) -> Any:
    """The proposal's one refinement (optionally replacing the adopted method),
    compiled against the Mission's current plan."""

    from agent_orchestrator.contracts import ContractError
    from agent_orchestrator.contracts.htn import (
        MethodInstanceId,
        OccurrenceId,
        RefineOperation,
        RetireMethodOperation,
        TaskRef,
    )
    from agent_orchestrator.orchestrator.hierarchical_dispatch import shared_goal_index
    from agent_orchestrator.planning.htn.applicability import assess_method
    from agent_orchestrator.planning.htn.compiler import compile_refinement_bundle
    from agent_orchestrator.planning.htn.grounding import ground_method
    from agent_orchestrator.planning.plan_preview import _refined_occurrence
    from agent_orchestrator.storage.htn_store import HtnStore

    world = dispatch._world()
    network = dispatch.network(mission_id)
    refinements = [item for item in proposal.operations if isinstance(item, RefineOperation)]
    retirements = [item for item in proposal.operations if isinstance(item, RetireMethodOperation)]
    if len(refinements) != 1 or len(refinements) + len(retirements) != len(proposal.operations):
        raise ContractError("a scripted proposal carries one refine and at most one retire")
    if len(retirements) > 1:
        raise ContractError("a scripted proposal retires at most the one instance it replaces")
    operation = refinements[0]
    retiring = tuple(MethodInstanceId(str(item.method_instance_id)) for item in retirements)
    parent = network.binding_for_task(TaskRef(str(operation.goal_id)))
    occurrence = _refined_occurrence(network, operation, tuple(str(item) for item in retiring))
    semantics = HtnStore(dispatch.store)
    contract = semantics.get_method(
        operation.method_ref.id, int(operation.method_ref.version)
    ).contract
    report = assess_method(
        parent, contract, world.snapshot(), world.capabilities(), registry=world.predicates
    )
    # The steps the scripted decision named (TaskGraph 补全第三批), resolved against what
    # this network holds; the slots of an instance being replaced leave with it.
    held = {str(entry.occurrence_id): entry for entry in shared_goal_index(
        network,
        catalog=world.catalog,
        exclude_occurrence_ids=tuple(_occurrences_leaving_with(network, retiring)),
    )}
    reuse = {step: held[str(occurrence)] for step, occurrence in operation.reuse.items()}
    draft = ground_method(
        parent,
        contract,
        dict(operation.bindings),
        report,
        catalog=world.catalog,
        schemas=world.schemas,
        reuse=reuse,
        plan_revision=network.plan_revision,
        goal_occurrence_id=OccurrenceId(occurrence),
    )
    confirmed = semantics.latest_requirements_revision(mission_id)
    return compile_refinement_bundle(
        draft,
        network,
        method=contract,
        catalog=world.catalog,
        schemas=world.schemas,
        registry=world.registry,
        reuse=reuse,
        retire_instance_ids=retiring,
        requirements_revision=0 if confirmed is None else int(confirmed.revision),
        compiled_from_proposal_id=proposal.proposal_id,
    )


def _occurrences_leaving_with(network: Any, retiring: Any) -> frozenset[Any]:
    """Occurrences that exist only because of the instances this proposal retires.

    A shared child another adopted instance still binds is not leaving: one consumer
    departing must not cancel work another consumer still needs.
    """

    if not retiring:
        return frozenset()
    dropped: set[Any] = set()
    surviving: set[Any] = set()
    retired = set(retiring)
    for instance in network.method_instances:
        children = {child.occurrence_id for child in instance.child_bindings}
        if instance.instance_id in retired:
            dropped |= children
        elif instance.instance_id in set(network.adopted_instance_ids) - retired:
            surviving |= children
    return frozenset(dropped - surviving)


def admission_for(dispatch: Any, command: Any, principal: Any) -> Any:
    """The planning authorization and producer snapshots one command commits under."""

    from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi
    from agent_orchestrator.contracts.models import sha256_hex
    from agent_orchestrator.contracts.planning_decisions import (
        PLANNING_DECISION_V1,
        PlanningRequestBinding,
    )
    from agent_orchestrator.governance.permissions import Principal
    from agent_orchestrator.governance.planning_authorization import (
        PlanningAuthorizationSnapshot,
        StorePlanningAuthorityReader,
        build_planning_authorization,
        planning_policy_for_mission,
    )
    from agent_orchestrator.orchestrator.planning_admission_commits import (
        PlanningCommitAdmission,
    )
    from agent_orchestrator.planning.plan_preview import _source_snapshot_payload
    from agent_orchestrator.runtime.planning_operations import (
        StoreOperationReader,
        build_operation_snapshot,
        read_running_work,
    )
    from agent_orchestrator.storage.planning_admission_store import PlanningAdmissionStore
    from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore

    store = dispatch.store
    mission_id = command.mission_id
    mission = store.get_mission(mission_id)
    decisions = PlanningDecisionStore(store)
    protocol = decisions.get_mission_protocol(mission_id)
    assert protocol is not None, "a hierarchical Mission holds its planning-protocol binding"
    request_id = f"request-{command.command_id}"
    decision_hash = sha256_hex({"delta": command.delta.to_json(), "command": command.command_id})
    if decisions.get_planning_request(request_id) is None:
        decisions.insert_planning_request(
            PlanningRequestBinding(
                request_id=request_id,
                mission_id=mission_id,
                protocol_version=PLANNING_DECISION_V1,
                package_version=int(protocol["package_version"]),
                package_hash="a" * 64,
                base_plan_revision=int(command.delta.base_plan_revision),
                requirements_revision=int(command.read_set.requirements_revision),
                scope_epoch_digest="b" * 64,
                subject_bindings_hash="c" * 64,
                visible_refs_digest="d" * 64,
                prompt_version=str(protocol["prompt_version"]),
                prompt_hash="e" * 64,
                created_at=store.now,
                intent_id=f"intent-{command.command_id}",
            )
        )
        PlanningAuthorizationApi(
            store, tenant_id=mission.tenant_id, principal=Principal(principal.principal_id)
        ).issue(mission_id, command_id=f"grant-{command.command_id}", request_id=request_id)
    authority = build_planning_authorization(
        request_id,
        read=StorePlanningAuthorityReader(PlanningAdmissionStore(store), store),
        caller=principal,
        policy=planning_policy_for_mission(store, mission_id),
        now_ms=int(store.now * 1000),
    )
    assert isinstance(authority, PlanningAuthorizationSnapshot), authority
    reader = StoreOperationReader(store)
    return PlanningCommitAdmission(
        request_id=request_id,
        decision_hash=decision_hash,
        decision_key="REFINE",
        authority=authority,
        operations=build_operation_snapshot(mission_id, reader=reader),
        runtime_work=read_running_work(
            mission_id,
            tuple(str(item) for item in command.delta.retired_instance_ids),
            reader=reader,
        ),
        preview_request_id=request_id,
        preview_decision_hash=decision_hash,
        preview_compilation_hash=sha256_hex(
            {
                "delta": command.delta.to_json(),
                "network": _source_snapshot_payload(command.network),
            }
        ),
        preview_read_set_hash=sha256_hex(command.read_set.to_json()),
    )


def apply_admitted_plan(
    dispatch: Any,
    mission_id: str,
    proposal: PlanProposal,
    *,
    principal: Any,
    command_id: str,
    source: Mapping[str, Any] | None = None,
) -> Any:
    """Compile, admit and commit one proposal; a ``PlanRoundOutcome`` either way.

    A commit the service refuses is recorded as ``PlanCommitRefused`` and returned as
    the round's refusal, the way the loop reports it.  A proposal that does not compile
    raises: there is nothing to commit and nothing was written.
    """

    from agent_orchestrator.orchestrator.hierarchical_dispatch import (
        PlanRefusal,
        PlanRoundOutcome,
    )
    from agent_orchestrator.orchestrator.plan_commits import PlanCommitRejected

    dispatch.require_hierarchical(mission_id)
    compilation = compile_scripted(dispatch, mission_id, proposal)
    command = dispatch.build_command(
        mission_id,
        proposal,
        compilation,
        principal=principal,
        command_id=f"{command_id}:1",
        source=dict(source or {}),
    )
    try:
        receipt = dispatch.commit.commit_planning_revision(
            command, principal, admission=admission_for(dispatch, command, principal)
        )
    except PlanCommitRejected as refused:
        refusal = PlanRefusal(
            attempt=1, reason=refused.reason, detail=refused.detail, recompilable=False
        )
        dispatch._record_refusal(mission_id, proposal, (refusal,))
        return PlanRoundOutcome(proposal_id=proposal.proposal_id, refusals=(refusal,))
    return PlanRoundOutcome(proposal_id=proposal.proposal_id, receipt=receipt)


__all__ = ("admission_for", "apply_admitted_plan", "compile_scripted")
