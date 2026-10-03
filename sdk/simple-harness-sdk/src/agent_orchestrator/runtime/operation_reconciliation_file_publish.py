# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Scoped reconciliation for the built-in file publisher (阶段 B 裁决第 1、3 类).

The publisher's own ledger is the authority on whether a publish ever linked: the intent
line is always written before the only commit point (``os.link``), and a publish that
gave up after its intent writes ``ABORTED`` (:mod:`.connectors_publish`).  So, read under
the ledger lock (no publish in flight):

* no line for the key → the link never happened (``NO_INTENT``) — this also covers a
  service refusal, which is always raised before the intent is written;
* the last line is ``ABORTED`` → the link never happened (``ABORTED``).

Either is a final, authoritative non-application proof
(``CONNECTOR_LEDGER_NOT_LINKED``).  Anything else (an intent with no outcome, a commit
whose file is gone or changed) is not decided here: the adapter raises, the action
stays unresolved, and a person rules.  That the publish *did* happen is not this
adapter's answer either — the connector's own ``lookup`` returns the receipt.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from ..contracts.models import ContractError
from ..contracts.operation_payloads import NonapplicationProofKind
from ..contracts.operation_reconciliation import ScopedReconciliationObservationV1
from ..contracts.semantic_base import TypedRef, TypedRefKind, content_hash_of
from .connectors_publish import LEDGER_PROTOCOL
from .operation_reconciliation import ReconciliationEvidence, ReconciliationProofError

_PROOF = str(NonapplicationProofKind.CONNECTOR_LEDGER_NOT_LINKED)


def _ledger_state(raw: Mapping[str, Any]) -> str:
    entries = list(raw.get("entries") or ())
    if not entries:
        return "NO_INTENT"
    if entries[-1].get("state") == "ABORTED":
        return "ABORTED"
    raise ReconciliationProofError("the ledger shows an intent that may have linked")


class FilePublishReconciliationAdapter:
    """Registered with the file-publish profile; identity-checked by ``context``."""

    def __init__(self, connector: Any, profile: Any) -> None:
        self.connector = connector
        self.profile = profile

    def observe(self, *, action: Mapping[str, Any], resolved: Any, handoff_events: tuple[Any, ...],
                now_ms: int) -> ReconciliationEvidence:
        key = str(action["idempotency_key"])
        raw = dict(self.connector.ledger_record(key))  # raises while a publish is in flight
        state = _ledger_state(raw)
        proof_id = "ledger:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16] + ":" + str(raw["line_count"])
        ref = TypedRef(TypedRefKind.TOOL_RECEIPT, proof_id, 1, content_hash_of(raw))
        envelope = resolved.envelope
        observation = ScopedReconciliationObservationV1.from_json({
            "schema_version": 1,
            "action_key": action["action_key"],
            "action_version": action["version"],
            "operation_id": str(envelope.operation_id),
            "operation_occurrence_id": str(envelope.operation_occurrence_id),
            "request_hash": str(envelope.request_hash),
            "params_hash": action["params_hash"],
            "idempotency_key": key,
            "connector_profile_hash": self.profile.content_hash(),
            "namespace": dict(self.profile.namespace),
            "normalized_target_ref": action["target"],
            "covered_handoff_ids": sorted(event.id for event in handoff_events),
            "queried_at_ms": int(now_ms),
            "observation_receipt_ref": ref.to_json(),
            "observation_origin": "REMOTE_QUERY",
            "query_scope": {"namespace": dict(self.profile.namespace), "request_identity": key,
                            "consistency_kind": "AUTHORITATIVE", "high_watermark": str(raw["line_count"])},
            "outcome": "NOT_APPLIED_FINAL",
            "proof": {"kind": _PROOF, "proof_id": proof_id, "basis_receipt_refs": [ref.to_json()],
                      "ledger_protocol_id": LEDGER_PROTOCOL, "ledger_state": state,
                      "ledger_receipt_ref": ref.to_json()},
        })
        return ReconciliationEvidence(observation=observation, receipts={ref.id: raw})

    def verify(self, evidence: ReconciliationEvidence, *, action: Mapping[str, Any], resolved: Any,
               handoff_events: tuple[Any, ...]) -> None:
        """Pure: the proof says exactly what the raw ledger record says."""
        data = evidence.observation.to_json()
        proof = data["proof"]
        if proof.get("kind") != _PROOF or proof.get("ledger_protocol_id") != LEDGER_PROTOCOL:
            raise ReconciliationProofError("not a file-publish ledger proof")
        raw = evidence.receipts.get(TypedRef.from_json(proof["ledger_receipt_ref"]).id)
        if not isinstance(raw, Mapping) or raw.get("protocol") != LEDGER_PROTOCOL:
            raise ReconciliationProofError("ledger record missing")
        if raw.get("key") != action["idempotency_key"] or any(
                entry.get("key") != action["idempotency_key"] for entry in raw.get("entries") or ()):
            raise ReconciliationProofError("ledger record is for another key")
        if raw.get("ledger") != hashlib.sha256(str(self.connector.ledger_path).encode("utf-8")).hexdigest():
            raise ReconciliationProofError("ledger record is from another ledger")
        try:
            state = _ledger_state(raw)
        except ContractError as error:
            raise ReconciliationProofError(str(error)) from error
        if proof.get("ledger_state") != state:
            raise ReconciliationProofError("proof state differs from the ledger record")


__all__ = ("FilePublishReconciliationAdapter",)
