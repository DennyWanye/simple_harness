"""Scoped reconciliation writes on the original action/Commit receipt ledger."""

from __future__ import annotations

from typing import Any

from ..contracts.semantic_base import content_hash_of
from ..runtime.operation_reconciliation import (
    ReconciliationEvidence,
    ReconciliationProofError,
    context,
    validate_evidence,
)


class OperationReconciliationCommitsMixin:
    def record_scoped_reconciliation(
        self,
        action_key: str,
        *,
        evidence: ReconciliationEvidence,
        adapter: object,
        service_authority: object,
    ) -> dict[str, Any]:
        runtime = self._operation_runtime()
        if service_authority is not runtime.service_authority:
            raise ReconciliationProofError("unbound reconciliation service")
        with self._store.transaction():
            action = self._store.get_action(action_key)
            if action is None:
                raise ReconciliationProofError("action missing")
            resolved, profile, registered, events = context(self._store, runtime, action)
            if adapter is not registered:
                raise ReconciliationProofError("unregistered receipt verifier")
            data = validate_evidence(
                evidence, action=action, resolved=resolved, profile=profile, events=events
            )
            if data["queried_at_ms"] > int(self._store.now * 1000):
                raise ReconciliationProofError("observation is from the future")
            if (data.get("proof") or {}).get("kind") == "HUMAN_RULED_NOT_APPLIED":
                raise ReconciliationProofError("an adapter cannot speak for a person's ruling")
            # Protocol interpretation is trusted adapter code, never caller proof fields.
            registered.verify(evidence, action=action, resolved=resolved, handoff_events=events)
            return self._store_scoped_observation(
                action, data, dict(evidence.receipts),
                runtime.profiles.resolve_connector_operation(
                    action["connector"], action["operation"]).implementation_ref.to_json())

    def _store_scoped_observation(self, action: dict[str, Any], data: dict[str, Any],
                                  raw_receipts: dict[str, Any], implementation_ref: Any) -> dict[str, Any]:
        """Write one validated observation onto the action, in the caller's transaction."""
        action_key = str(action["action_key"])
        receipt = {
            "kind": "scoped_operation_reconciliation",
            "mission_id": action["mission_id"],
            "observation": data,
            "raw_receipts": dict(raw_receipts),
            "adapter_implementation_ref": implementation_ref,
        }
        digest = content_hash_of(receipt)
        receipt_id = "scoped-reconciliation:" + digest
        previous = self._store.get_receipt(receipt_id)
        if previous is not None:
            if previous != receipt:
                raise ReconciliationProofError("receipt replay conflict")
            return action
        lapsed = (
            action["state"] == "HANDED_OFF"
            and float(action.get("lease_expires_at") or 0) <= self._store.now
        )
        if action["state"] not in ("UNKNOWN", "FAILED") and not lapsed:
            raise ReconciliationProofError("action is not available for reconciliation")
        if lapsed:
            action = self._resolve_action(
                action, "UNKNOWN", error="lease_lapsed_without_outcome"
            )
        self._store.insert_receipt(
            commit_id=receipt_id,
            kind=receipt["kind"],
            subject_id=action_key,
            base_version=action["version"],
            proposal_hash=digest,
            receipt=receipt,
        )
        ref = {"id": receipt_id, "content_hash": digest}
        negative = data["outcome"] == "NOT_APPLIED_FINAL"
        action = self._update_action(
            action_key,
            scoped_reconciliation_ref=ref,
            history=[
                *action.get("history", []),
                {
                    "at": self._store.now,
                    "state": action["state"],
                    "scoped_reconciliation_ref": ref,
                },
            ],
            reconcile="CONFIRMED_NOT_STARTED" if negative else "STILL_UNKNOWN",
            needs_human=not negative,
            reconcile_note=None if negative else ReconciliationProofError.code,
        )
        self._emit(
            "ActionScopedReconciled",
            str(action["mission_id"]),
            key=receipt_id,
            task_id=action.get("task_id"),
            payload={
                "action_key": action_key,
                "observation_hash": content_hash_of(data),
                "receipt_ref": ref,
                "outcome": data["outcome"],
            },
        )
        return action

    def record_human_nonapplication(self, action_key: str, *, override: dict[str, Any],
                                    principal_id: str) -> dict[str, Any]:
        """A person ruled an operation-linked action not applied: the ruling is the proof
        (阶段 B 裁决第 3 类), stored like any scoped proof so the operation gate opens.
        Called in the override's own transaction; no adapter is asked."""
        from ..contracts.operation_reconciliation import ScopedReconciliationObservationV1
        from ..contracts.semantic_base import TypedRef, TypedRefKind
        from ..runtime.operation_reconciliation import scope

        if not self._store.connection.in_transaction:
            raise ReconciliationProofError("a human ruling proof needs the override's transaction")
        runtime = self._operation_runtime()
        action = self._store.get_action(action_key)
        if action is None:
            raise ReconciliationProofError("action missing")
        resolved, profile, events = scope(self._store, runtime, action)
        override_id = str(override["override_id"])
        ref = TypedRef(TypedRefKind.TOOL_RECEIPT, override_id, 1, content_hash_of(override))
        envelope = resolved.envelope
        observation = ScopedReconciliationObservationV1.from_json({
            "schema_version": 1, "action_key": action["action_key"], "action_version": action["version"],
            "operation_id": str(envelope.operation_id),
            "operation_occurrence_id": str(envelope.operation_occurrence_id),
            "request_hash": str(envelope.request_hash), "params_hash": action["params_hash"],
            "idempotency_key": action["idempotency_key"],
            "connector_profile_hash": profile.content_hash(), "namespace": dict(profile.namespace),
            "normalized_target_ref": action["target"],
            "covered_handoff_ids": sorted(event.id for event in events),
            "queried_at_ms": int(self._store.now * 1000),
            "observation_receipt_ref": ref.to_json(), "observation_origin": "HUMAN_RULING",
            "query_scope": {"namespace": dict(profile.namespace), "request_identity": action["idempotency_key"],
                            "consistency_kind": "AUTHORITATIVE", "high_watermark": None},
            "outcome": "NOT_APPLIED_FINAL",
            "proof": {"kind": "HUMAN_RULED_NOT_APPLIED", "proof_id": override_id,
                      "basis_receipt_refs": [ref.to_json()], "ruling_principal_id": str(principal_id),
                      "override_receipt_ref": ref.to_json()},
        })
        evidence = ReconciliationEvidence(observation=observation, receipts={ref.id: dict(override)})
        data = validate_evidence(evidence, action=action, resolved=resolved, profile=profile, events=events)
        return self._store_scoped_observation(action, data, {ref.id: dict(override)}, None)

    def record_reconciliation_unavailable(self, action_key: str) -> dict[str, Any]:
        """A failed, handed-off action whose proof could not be read this round (the
        service unreachable, a publish in flight).  Counted; at the shared non-model cap it
        is marked for a person instead of being asked again (阶段 B 裁决第 1 类)."""
        from .failure_classes import NON_MODEL_FAILURE_CAP

        with self._store.transaction():
            action = self._store.get_action(action_key)
            if action is None:
                raise ReconciliationProofError("action missing")
            count = int(action.get("reconcile_unavailable") or 0) + 1
            fields: dict[str, Any] = {"reconcile_unavailable": count}
            marked = count >= NON_MODEL_FAILURE_CAP
            if marked:
                fields.update(needs_human=True, reconcile="STILL_UNKNOWN",
                              reconcile_note="proof_unavailable_after_retries")
            updated = self._update_action(action_key, **fields)
            # 每一轮读不到证明都是这个动作的事实（到上限转人工更是）：留事件，不静默改动作行
            # （阶段 G 收尾大语料）
            self._emit("ActionReconciliationUnavailable", str(action["mission_id"]),
                       key=f"{action_key}:{count}",
                       payload={"action_id": str(action["action_id"]), "action_key": action_key,
                                "count": count, "marked_for_person": marked})
            return updated

    def finish_proven_nonapplication(
        self, action_key: str, *, reason: str, service_authority: object
    ) -> dict[str, Any]:
        """Close a proven old handoff when a fresh send failed the current gates.

        Recheck under the write lock: a concurrently admitted new handoff invalidates
        the old proof and must retain its own reservation and effect uncertainty.
        """
        from ..runtime.operation_reconciliation import stored_negative_proof

        runtime = self._operation_runtime()
        if service_authority is not runtime.service_authority:
            raise ReconciliationProofError("unbound reconciliation service")
        with self._store.transaction():
            action = self._store.get_action(action_key)
            if action is None:
                raise ReconciliationProofError("action missing")
            if action["state"] != "UNKNOWN" or not stored_negative_proof(self._store, action):
                return action
            return self._resolve_action(
                action,
                "FAILED",
                error="proven_not_applied:rehandoff_refused:" + reason,
                event="ActionNonapplicationFinalized",
                extra={"scoped_reconciliation_ref": action["scoped_reconciliation_ref"]},
            )
