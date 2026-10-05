# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Assembly of operation reviews through the existing service AgentBridge lane."""

from __future__ import annotations

import logging
from typing import Any


from ..contracts import ContractError
from ..contracts.operation_intents import SubmitOperationIntentV2
from ..contracts.resolution import ReviewVerdict
from ..contracts.semantic_base import TypedRef, TypedRefKind, content_hash_of
from ..governance.permissions import Principal
from ..storage.htn_store import HtnStore
from ..storage.operation_intent_store import OperationIntentStore
from .operation_completion import OperationCompletionError
from .operation_materialization import OperationMaterializationRuntime

logger = logging.getLogger("agent_orchestrator")


def ensure_operation_runtime(orchestrator: Any) -> OperationMaterializationRuntime:
    existing = getattr(orchestrator.commit, "_operation_materialization_runtime", None)
    if existing is not None:
        return existing
    from ..runtime.operation_profiles import BuiltinOperationProfiles

    profiles = getattr(orchestrator, "_operation_profiles", None) or BuiltinOperationProfiles(
        orchestrator.connectors
    )
    policy_for = getattr(orchestrator, "_operation_policy_for", None) or profiles.policy_for
    runtime = OperationMaterializationRuntime(
        connectors=orchestrator.connectors,
        deployment=orchestrator.config.deployment_policy,
        profiles=profiles,
        policy_for=policy_for,
        prepare_review=lambda sources, payloads, package_id: prepare_review(
            orchestrator,
            sources,
            payloads,
            package_id,
        ),
        service_authority=object(),
    )
    orchestrator.commit.bind_operation_materialization_runtime(runtime)
    return runtime


def coordinator_for(orchestrator: Any, sources: Any) -> Any:
    from .operation_proposal_review import ActionProposalReviewCoordinator

    runtime = ensure_operation_runtime(orchestrator)
    return ActionProposalReviewCoordinator(
        store=orchestrator.store,
        connectors=runtime.connectors,
        deployment=runtime.deployment,
        profile_registry=runtime.profiles,
        deployment_policy=runtime.policy_for(sources),
    )


def prepare_review(orchestrator: Any, sources: Any, payloads: Any, package_id: str) -> Any:

    coordinator = coordinator_for(orchestrator, sources)
    draft = coordinator.prepare_review(sources, payloads, package_id=package_id)
    coordinator.persist_package(draft)
    # BW03: the frozen draft is reviewed on the Assurance round transport. T0 only
    # persists the draft (package, input manifest, four check receipts): the review
    # input embeds the frozen payload files and CAS bytes are never read inside a
    # transaction (real run 2026-09-27: CAS_READ_INSIDE_TRANSACTION at submission).
    # ``ensure_assured_proposal_reviews`` opens the round after T0 commits.
    return draft


def _current_inputs(orchestrator: Any, row: dict[str, Any]) -> tuple[Any, Any]:
    return orchestrator.commit._operation_inputs(
        SubmitOperationIntentV2.from_json(row["binding"]["command"]),
        row["tenant_id"],
        Principal(row["principal_id"]),
        row["intent_id"],
        (row["parameters_object_id"], row["effect_object_id"], row["proposal_object_id"]),
    )


def ensure_assured_proposal_reviews(orchestrator: Any, mission_id: str) -> bool:
    """Open the Assurance ACTION_PROPOSAL round for each submitted intent, outside T0.

    One round per frozen package (``assurance_review_invocations`` is the durable
    marker), so a later loop round or a restart never opens a second one.  A
    refusal is logged and retried next round; it never falls back to the legacy
    reviewer.
    """
    from ..assurance.codec import AssuranceError
    from ..storage.assurance_store import AssuranceStore
    from .assurance_purpose_reviews import assurance_review_runtime, purpose_review_key

    store = orchestrator.store
    rows = OperationIntentStore(store).for_mission(mission_id)
    if not rows:
        return False
    try:
        if AssuranceStore(store).lane(mission_id) != "ASSURANCE_1_1":
            return False
    except AssuranceError:
        return False
    ensure_operation_runtime(orchestrator)  # _current_inputs needs the bound runtime
    progressed = False
    for row in rows:
        package_id = str(row["review_package_id"])
        if store.get_receipt("materialize:" + row["intent_id"]) is not None:
            continue
        if HtnStore(store).official_review_record(package_id) is not None:
            continue
        key = purpose_review_key("ACTION_PROPOSAL", mission_id, package_id)
        if store.connection.execute(
            "SELECT 1 FROM assurance_review_invocations WHERE mission_id=? AND review_key=?",
            (mission_id, key),
        ).fetchone() is not None:
            continue
        try:
            with store.transaction():
                sources, payloads = _current_inputs(orchestrator, row)
                draft = coordinator_for(orchestrator, sources).prepare_review(
                    sources, payloads, package_id=package_id
                )
            assurance_review_runtime(orchestrator.commit).ensure_action_proposal(
                store.get_mission(mission_id), draft=draft, sources=sources, payloads=payloads
            )
            progressed = True
        except Exception as error:  # noqa: BLE001 - one intent must never stop the loop
            # Real run 2026-09-27: an uncaught BudgetExhausted here failed every
            # orchestrator round.  Logged and retried next round instead.
            logger.warning(
                "assured proposal review not opened intent=%s: %s: %s",
                row["intent_id"], type(error).__name__, error,
            )
    return progressed


def recover_materializations(orchestrator: Any, mission_id: str) -> bool:
    """Consume durable accepted reviews after restart without asking the model again."""
    rows = OperationIntentStore(orchestrator.store).for_mission(mission_id)
    if not rows:
        return False
    runtime = ensure_operation_runtime(orchestrator)
    progressed = False
    for row in rows:
        if orchestrator.store.get_receipt("materialize:" + row["intent_id"]) is not None:
            continue
        record = HtnStore(orchestrator.store).official_review_record(row["review_package_id"])
        if record is None or record.verdict is not ReviewVerdict.ACCEPT:
            continue
        try:
            orchestrator.commit.materialize_reviewed_operation(
                intent_id=row["intent_id"],
                command_id="materialize:" + row["intent_id"],
                official_review_ref=TypedRef(
                    TypedRefKind.REVIEW, str(record.record_id), 1, content_hash_of(record.to_json())
                ),
                service_authority=runtime.service_authority,
            )
            progressed = True
        except (ContractError, ValueError, RuntimeError) as error:
            # Refusal is durable and idempotent; it does not purchase another review.
            with orchestrator.store.transaction():
                orchestrator.commit._emit(
                    "OperationMaterializationDeferred",
                    mission_id,
                    key=row["intent_id"] + ":" + str(getattr(error, "code", type(error).__name__)),
                    payload={
                        "intent_id": row["intent_id"],
                        "reason": getattr(error, "code", type(error).__name__),
                    },
                )
    return progressed


async def dispatch_materialized_operations(orchestrator: Any, mission_id: str) -> bool:
    """Drive approved Operation actions before root review waits on their outcomes.

    The original ActionExecutor owns every permission, freshness, budget and
    idempotency check. This bridge supplies no new authority and never re-sends
    HANDED_OFF, UNKNOWN or SUCCEEDED actions.
    """
    from ..contracts.state_machines import MissionStopReason
    from .action_commits import HANDOFF_READY_STATES

    for row in OperationIntentStore(orchestrator.store).for_mission(mission_id):
        receipt = orchestrator.store.get_receipt("materialize:" + row["intent_id"])
        if receipt is None:
            continue
        action = orchestrator.store.get_action(receipt["action_key"])
        if action is None or action["mission_id"] != mission_id or action["state"] not in HANDOFF_READY_STATES:
            continue
        key = str(action["action_key"])
        await orchestrator.actions.hand_off(key)
        after = orchestrator.store.get_action(key)
        if after is not None and after["state"] in HANDOFF_READY_STATES:
            from ..contracts.error_table import handoff_refusal_transient
            if handoff_refusal_transient(orchestrator.actions.last_refusal.get(key, "")):
                return False  # stays handoff-ready; tried again next round
            orchestrator._commit_fail_mission(mission_id, stop_reason=MissionStopReason.ACTION_FAILED,
                detail={"action_key": key, "reason": "handoff_refused:"
                        + orchestrator.actions.last_refusal.get(key, "")})
            await orchestrator._release_mission(mission_id)
        return True
    return False


def advance_operation_outcomes(orchestrator: Any, mission_id: str) -> bool:
    """Queue one review or consume a durable verdict, without re-sending an action."""
    from ..storage.operation_completion_store import OperationCompletionStore
    from .review_adjudication import accepted_or_adjudicated, adjudication_of
    from .operation_outcomes import (
        _effect_owner,
        accept_operation_outcome,
        outcome_retake_due,
        persist_operation_outcome_review,
        prepare_operation_outcome_review,
    )

    store = orchestrator.store
    rows = OperationIntentStore(store).for_mission(mission_id)
    if not rows:
        return False
    runtime = ensure_operation_runtime(orchestrator)
    completion = OperationCompletionStore(store)
    for row in rows:
        materialized = store.get_receipt("materialize:" + row["intent_id"])
        if materialized is None:
            continue
        action = store.get_action(materialized["action_key"])
        if action is None or action["state"] != "SUCCEEDED":
            continue
        try:
            owner, spec, _ = _effect_owner(
                store, mission_id, row["binding"]["completion"]["effect_key"]
            )
            current = tuple(
                item
                for item in completion.list_outcome_bindings_for_intent(
                    mission_id, row["intent_id"]
                )
                if item["document"].completion_scope_id == owner.scope_id
                and item["document"].spec_hash == spec.content_hash()
            )
            retake = False
            if current:
                # An official rejection or failed dispatch is not permission to buy
                # another verdict. Recovery consumes only the existing accepted one.
                for existing in current:
                    if completion.get_acceptance_scope_exact(
                        mission_id, "acc-" + existing["binding_id"]
                    ):
                        continue
                    record = HtnStore(store).official_review_record(existing["review_package_id"])
                    if record is None:
                        continue
                    if accepted_or_adjudicated(store, record):
                        accept_operation_outcome(
                            orchestrator.commit,
                            mission_id=mission_id,
                            binding_id=existing["binding_id"], service_authority=runtime.service_authority,
                        )
                        return True
                    if (record.verdict is ReviewVerdict.INCONCLUSIVE
                            and adjudication_of(store, str(record.record_id)) is None
                            and orchestrator._ask_person_to_adjudicate_outcome(
                                store.get_mission(mission_id), record, existing["document"])):
                        # 判不下来（含审阅员两次回复都无法采用）→ 问人；答复被消费后下一轮验收。
                        return True
                    if record.verdict in {ReviewVerdict.REJECTED, ReviewVerdict.REWORK}:
                        # 结果审阅员判这次操作的结果不满足要求：操作不能重发，把它的结论交给规划器
                        # 一次（问用户、改计划由它判断）；它回应过仍没解决就具名停下，不挂着等。
                        from .system_operations import _ask_planner_once, _stop

                        facts = {"reason": "operation_outcome_rejected", "binding_id": existing["binding_id"],
                                 "effect_key": row["binding"]["completion"]["effect_key"],
                                 "verdict": str(record.verdict), "review_record_id": str(record.record_id),
                                 "requirements_revision": int(owner.requirements_ref.revision),
                                 "criteria": [dict(item) for item in record.to_json().get("criteria") or ()]}
                        if _ask_planner_once(
                                orchestrator, store.get_mission(mission_id),
                                key="operation-outcome-rejected:" + existing["binding_id"],
                                event_type="VerifierAcceptanceRejected", detail=facts,
                                stop=lambda why, facts=facts: _stop(orchestrator, mission_id, {
                                    **facts, "explanation": "操作的结果没有通过审阅" + why})):
                            return True
                # 2026-09-29：唯一一份审阅被重启打断而用完（不是审阅员的结论）——重审一次。
                if not outcome_retake_due(store, mission_id, current):
                    continue
                retake = True
            prepared = prepare_operation_outcome_review(
                store,
                intent_id=row["intent_id"],
                connectors=runtime.connectors,
                profiles=runtime.profiles,
                retake=retake,
            )
            with store.transaction():
                persist_operation_outcome_review(orchestrator.commit, prepared, runtime=runtime)
                mission = store.get_mission(mission_id)
                # BW03: the persisted outcome preparation is reviewed on the
                # Assurance round transport in this same UoW.
                from ..assurance.codec import AssuranceError
                from .assurance_purpose_reviews import assurance_review_runtime

                try:
                    assurance_review_runtime(orchestrator.commit).ensure_operation_outcome(
                        mission, prepared=prepared
                    )
                except AssuranceError as error:
                    raise OperationCompletionError(
                        "OP_OUTCOME_REVIEW_UNAVAILABLE", str(error)
                    ) from error
            return True
        except (ContractError, ValueError, RuntimeError) as error:
            with store.transaction():
                orchestrator.commit._emit(
                    "OperationOutcomeDeferred",
                    mission_id,
                    key=row["intent_id"] + ":" + str(getattr(error, "code", type(error).__name__)),
                    payload={
                        "intent_id": row["intent_id"],
                        "reason": getattr(error, "code", type(error).__name__),
                    },
                )
    return False
