"""Deployment-owned reconciliation adapters and durable proof verification.

Remote observation is outside the Commit transaction. A registered adapter verifies
its protocol's raw receipts; Commit rechecks the frozen identity and all handoffs.
No built-in best-effort lookup is promoted to a negative proof.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ..contracts.models import ContractError
from ..contracts.operation_reconciliation import ScopedReconciliationObservationV1
from ..contracts.semantic_base import content_hash_of
from ..storage.planning_admission_store import PlanningAdmissionStore
from .operation_payloads import require_connector_profile
from .operation_ref_resolver import OperationReferenceResolver


class ReconciliationProofError(ContractError):
    code = "OP_NONAPPLICATION_PROOF_INSUFFICIENT"


@dataclass(frozen=True, slots=True)
class ReconciliationEvidence:
    observation: ScopedReconciliationObservationV1
    # Original protocol documents, keyed by tool receipt reference id.
    receipts: Mapping[str, Mapping[str, Any]]


class ReconciliationAdapter(Protocol):
    connector: Any
    profile: Any

    def observe(
        self,
        *,
        action: Mapping[str, Any],
        resolved: Any,
        handoff_events: tuple[Any, ...],
        now_ms: int,
    ) -> ReconciliationEvidence: ...

    def verify(
        self,
        evidence: ReconciliationEvidence,
        *,
        action: Mapping[str, Any],
        resolved: Any,
        handoff_events: tuple[Any, ...],
    ) -> None:
        """Verify raw service protocol semantics; pure, no network or writes."""
        ...


def handoff_events(store: Any, action: Mapping[str, Any]) -> tuple[Any, ...]:
    events = tuple(
        e
        for e in store.iter_events(str(action["mission_id"]))
        if e.type == "ActionHandedOff" and e.payload.get("action_key") == action["action_key"]
    )
    count = action.get("handoffs")
    if type(count) is not int or count < 1 or len(events) != count:
        raise ReconciliationProofError("incomplete handoff ledger")
    if (
        sorted(e.payload.get("handoff", -1) for e in events) != list(range(1, count + 1))
        or len({e.id for e in events}) != count
        or any(e.payload.get("idempotency_key") != action["idempotency_key"] for e in events)
    ):
        raise ReconciliationProofError("handoff ledger identity differs")
    return events


def scope(store: Any, runtime: Any, action: Mapping[str, Any]) -> tuple[Any, Any, tuple[Any, ...]]:
    """The frozen identity a proof about this action must match: (resolved, profile, handoffs)."""
    link = PlanningAdmissionStore(store).get_operation_action_link_for_action(
        str(action["action_key"])
    )
    if link is None:
        raise ReconciliationProofError("operation link missing")
    resolved = OperationReferenceResolver(store, runtime.profiles).resolve_historical(
        dict(action), link
    )
    profile = require_connector_profile(
        runtime.profiles,
        connector_id=str(action["connector"]),
        operation_name=str(action["operation"]),
        expected_hash=resolved.parameters.connector_profile_hash,
    )
    return resolved, profile, handoff_events(store, action)


def context(
    store: Any, runtime: Any, action: Mapping[str, Any]
) -> tuple[Any, Any, Any, tuple[Any, ...]]:
    resolved, profile, events = scope(store, runtime, action)
    registration = runtime.profiles.resolve_connector_operation(
        action["connector"], action["operation"]
    )
    adapter = registration.reconciliation_adapter
    if (
        adapter is None
        or getattr(adapter, "connector", None) is not runtime.connectors.get(action["connector"])
        or getattr(adapter, "profile", None) != profile
        or not callable(getattr(adapter, "observe", None))
        or not callable(getattr(adapter, "verify", None))
    ):
        raise ReconciliationProofError("no registered scoped reconciliation adapter")
    return resolved, profile, adapter, events


def validate_evidence(
    evidence: ReconciliationEvidence,
    *,
    action: Mapping[str, Any],
    resolved: Any,
    profile: Any,
    events: tuple[Any, ...],
) -> dict[str, Any]:
    if not isinstance(evidence, ReconciliationEvidence):
        raise ReconciliationProofError("untyped reconciliation evidence")
    observation = ScopedReconciliationObservationV1.from_json(evidence.observation.to_json())
    data = observation.to_json()
    expected = {
        "action_key": action["action_key"],
        "action_version": action["version"],
        "operation_id": str(resolved.envelope.operation_id),
        "operation_occurrence_id": str(resolved.envelope.operation_occurrence_id),
        "request_hash": str(resolved.envelope.request_hash),
        "params_hash": action["params_hash"],
        "idempotency_key": action["idempotency_key"],
        "connector_profile_hash": profile.content_hash(),
        "namespace": dict(profile.namespace),
        "normalized_target_ref": action["target"],
        "covered_handoff_ids": sorted(e.id for e in events),
    }
    if any(data[k] != v for k, v in expected.items()):
        raise ReconciliationProofError("observation identity or coverage differs")
    if data["query_scope"]["request_identity"] != action["idempotency_key"]:
        raise ReconciliationProofError("query does not bind the idempotency key")
    if data["outcome"] == "NOT_APPLIED_FINAL":
        kind = data["proof"]["kind"]
        # A person's ruling is accepted wherever the effect froze it; every other kind
        # must also be one the connector profile registered (阶段 B 裁决第 3 类).
        human = kind == "HUMAN_RULED_NOT_APPLIED"
        if (
            (not human and kind not in profile.nonapplication_proofs)
            or kind not in resolved.effect_contract.accepted_nonapplication_proofs
        ):
            raise ReconciliationProofError("proof kind not frozen and registered")
    for ref in observation.receipt_refs():
        raw = evidence.receipts.get(ref.id)
        if raw is None or content_hash_of(raw) != ref.content_hash:
            raise ReconciliationProofError("raw protocol receipt missing or altered")
    return data


def action_outcome_unresolved(store: Any, action: Mapping[str, Any]) -> bool:
    """Whether nobody knows yet if this action took effect outside: it left our hands with
    no outcome (HANDED_OFF / UNKNOWN), or an operation-linked one failed after leaving our
    hands with no stored proof that it did not happen.  The one reading behind a stopped
    Mission's ``unresolved_actions`` and behind "its result is in now" (H-2)."""
    state = action.get("state")
    if state in {"HANDED_OFF", "UNKNOWN"}:
        return True
    if state != "FAILED" or int(action.get("handoffs") or 0) < 1:
        return False
    if PlanningAdmissionStore(store).get_operation_action_link_for_action(str(action["action_key"])) is None:
        return False
    return not stored_negative_proof(store, action)


def stored_negative_proof(
    store: Any, action: Mapping[str, Any], link: Mapping[str, Any] | None = None
) -> bool:
    """Read only the original Commit receipt and history, never caller booleans."""
    try:
        ref = action.get("scoped_reconciliation_ref")
        if not isinstance(ref, Mapping):
            return False
        receipt = store.get_receipt(str(ref["id"]))
        if (
            receipt is None
            or receipt.get("kind") != "scoped_operation_reconciliation"
            or content_hash_of(receipt) != ref["content_hash"]
        ):
            return False
        data = ScopedReconciliationObservationV1.from_json(receipt["observation"]).to_json()
        if data["outcome"] != "NOT_APPLIED_FINAL":
            return False
        bridge = link or PlanningAdmissionStore(store).get_operation_action_link_for_action(
            action["action_key"]
        )
        if bridge is None:
            return False
        if any(
            data[k] != action[v]
            for k, v in (
                ("action_key", "action_key"),
                ("action_version", "version"),
                ("params_hash", "params_hash"),
                ("idempotency_key", "idempotency_key"),
                ("normalized_target_ref", "target"),
            )
        ):
            return False
        if any(
            data[k] != bridge[k]
            for k in ("operation_id", "operation_occurrence_id", "request_hash")
        ):
            return False
        if data["covered_handoff_ids"] != sorted(e.id for e in handoff_events(store, action)):
            return False
        if not any(h.get("scoped_reconciliation_ref") == ref for h in action.get("history", ())):
            return False
        if not any(
            e.type == "ActionScopedReconciled"
            and e.payload.get("action_key") == action["action_key"]
            and e.payload.get("receipt_ref") == ref
            and e.payload.get("observation_hash") == content_hash_of(data)
            for e in store.iter_events(action["mission_id"])
        ):
            return False
        parsed = ScopedReconciliationObservationV1.from_json(data)
        for raw_ref in parsed.receipt_refs():
            if content_hash_of(receipt["raw_receipts"][raw_ref.id]) != raw_ref.content_hash:
                return False
        return True
    except (ContractError, KeyError, TypeError, ValueError):
        return False


def nonapplication_outcome(store: Any, action: Mapping[str, Any]) -> dict[str, Any]:
    """How one ended attempt of an effect did not happen, in plain facts for the next card
    and the stop report (阶段 B 裁决第 1 类): a person's ruling, a send that never reached
    the service, or the service's refusal in its own words."""
    kind = None
    ref = action.get("scoped_reconciliation_ref")
    if isinstance(ref, Mapping):
        receipt = store.get_receipt(str(ref.get("id")))
        if receipt is not None:
            kind = ((receipt.get("observation") or {}).get("proof") or {}).get("kind")
    error = str(action.get("error") or "")
    if kind == "HUMAN_RULED_NOT_APPLIED":
        outcome = "human_ruled_not_applied"
    elif error.startswith("proven_not_applied:") or not error:
        outcome = "not_delivered"
    else:
        outcome = "service_refused"
    return {"action_key": str(action["action_key"]), "attempt": int(action.get("version") or 1),
            "outcome": outcome, "reason": error[:300]}
