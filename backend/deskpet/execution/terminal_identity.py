"""Normalize Host terminal authorities to the raw SDK terminal identity.

Host evidence commitments and SDK event commitments are different namespaces.
This reader only touches Host tables; the caller supplies the actual terminal
from the SDK public reader to ``verify_sdk_terminal``.
"""
from dataclasses import dataclass
import json

from deskpet.task_scope.protocol import canonical_hash


@dataclass(frozen=True)
class PrimaryTerminalIdentity:
    sdk_run_id: str
    raw_sdk_event_id: str
    raw_sdk_event_hash: str
    terminal_state: str
    host_receipt_ref: str
    host_receipt_hash: str
    legacy_scoped: bool
    observation_evidence_id: str | None

    def verify_sdk_terminal(self, actual) -> None:
        state = "cancelled" if self.terminal_state == "STOPPED" else self.terminal_state.lower()
        if (actual is None or actual.run_id != self.sdk_run_id
                or actual.event_id != self.raw_sdk_event_id or actual.event_hash != self.raw_sdk_event_hash
                or actual.state != state):
            raise RuntimeError("primary_history_terminal_mismatch")


async def read_primary_terminal_identity_tx(db, *, subject, primary_ref, host_run_id, sdk_run_id):
    """Read in the caller's Host snapshot; None means no committed terminal."""
    from deskpet.execution.primary_history import terminal_observation_tx

    async def one(sql, args):
        cursor = await db.execute(sql, args)
        row = await cursor.fetchone()
        await cursor.close()
        return row

    row = await one(
        "SELECT f.*,r.task_scope_id,t.turn_id,t.evidence_id AS input_evidence_id,t.evidence_hash AS input_evidence_hash,b.binding_json,b.binding_hash FROM foreground_terminal_receipts f "
        "JOIN foreground_runs r ON r.host_run_id=f.host_run_id "
        "JOIN foreground_turns t ON t.turn_id=r.turn_id AND t.subject=r.subject AND t.primary_conversation_id=r.primary_conversation_id "
        "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id AND b.sdk_run_id=f.sdk_run_id "
        "WHERE r.subject=? AND r.primary_conversation_id=? AND r.host_run_id=? AND b.sdk_run_id=?",
        (subject, primary_ref, host_run_id, sdk_run_id),
    )
    if row is None:
        return None
    binding = json.loads(row["binding_json"])
    if (canonical_hash(binding) != row["binding_hash"] or binding.get("host_run_id") != host_run_id
            or binding.get("sdk_run_id") != sdk_run_id):
        raise RuntimeError("primary_history_run_binding_corrupt")
    receipt = json.loads(row["receipt_json"])
    if canonical_hash(receipt) != row["receipt_hash"] or any(
        receipt.get(key) != row[key] for key in (
            "terminal_receipt_id", "host_run_id", "sdk_run_id", "terminal_state", "generation", "sdk_event_id", "sdk_event_hash"
        )
    ):
        raise RuntimeError("primary_history_terminal_receipt_corrupt")
    observed = await terminal_observation_tx(db, host_run_id=host_run_id, sdk_run_id=sdk_run_id, subject=subject)
    if observed is not None:
        observation, payload = observed
        if (receipt.get("primary_observation_ref") != observation["evidence_id"]
                or receipt.get("primary_observation_hash") != observation["envelope_sha256"]
                or payload.get("terminal_state") != row["terminal_state"]
                or payload.get("turn_id") != row["turn_id"]
                or type(payload.get("generation")) is not int
                or not 1 <= payload["generation"] <= row["generation"]):
            raise RuntimeError("primary_history_observation_corrupt")
        raw_id, raw_hash = payload["sdk_event_id"], payload["sdk_event_hash"]
        if receipt.get("terminal_authority_kind") == "primary_runtime_observation" and (
            row["sdk_event_id"] != observation["evidence_id"] or row["sdk_event_hash"] != observation["envelope_sha256"]
        ):
            raise RuntimeError("primary_history_observation_corrupt")
    else:
        if (receipt.get("primary_observation_ref") is not None
                or receipt.get("terminal_authority_kind") == "primary_runtime_observation"
                or row["task_scope_id"] is None):
            raise RuntimeError("primary_history_observation_corrupt")
        raw_id, raw_hash = None, None
    if receipt.get("terminal_authority_kind") != "primary_runtime_observation":
        evidence = await one(
            "SELECT e.payload_json,e.payload_hash,i.source_event_id,i.evidence_hash,i.task_scope_id "
            "FROM task_scope_execution_ingest_receipts i "
            "JOIN task_scope_events e ON e.event_id=i.event_id AND e.task_scope_id=i.task_scope_id "
            "JOIN task_scope_terminal_gate_receipts g ON g.run_id=i.run_id AND g.task_scope_id=i.task_scope_id "
            "AND g.terminal_source_sequence=i.source_sequence "
            "WHERE i.run_id=? AND i.source_event_id=? AND i.evidence_hash=? AND i.evidence_kind='run_terminal' "
            "AND g.gate_receipt_id=? AND g.durable_source_sequence>=g.terminal_source_sequence",
            (sdk_run_id, row["sdk_event_id"], row["sdk_event_hash"], receipt.get("terminal_gate_receipt_id")),
        )
        if evidence is None:
            raise RuntimeError("primary_history_terminal_evidence_corrupt")
        raw = json.loads(evidence["payload_json"])
        public = raw.get("public_payload", {})
        if (canonical_hash(raw) != evidence["evidence_hash"] or evidence["payload_hash"] != evidence["evidence_hash"]
                or raw.get("event_id") != row["sdk_event_id"] or raw.get("run_id") != sdk_run_id
                or raw.get("subject") != subject or raw.get("kind") != "run_terminal"
                or public.get("terminal_state") != row["terminal_state"]
                or type(public.get("generation")) is not int or not 1 <= public["generation"] <= row["generation"]
                or raw.get("evidence_refs") != [{"evidence_id": row["input_evidence_id"], "content_hash": row["input_evidence_hash"], "ordinal": 1}]
                or raw.get("disclosure_context", {}).get("subject") != subject
                or raw.get("disclosure_context", {}).get("run_id") != sdk_run_id):
            raise RuntimeError("primary_history_terminal_evidence_corrupt")
        if raw_id is not None and (raw_id != raw["event_id"] or raw_hash != public.get("sdk_terminal_event_hash")):
            raise RuntimeError("primary_history_terminal_evidence_corrupt")
        raw_id, raw_hash = raw["event_id"], public.get("sdk_terminal_event_hash")
    if not isinstance(raw_id, str) or not raw_id or not isinstance(raw_hash, str) or len(raw_hash) != 64:
        raise RuntimeError("primary_history_terminal_evidence_corrupt")
    return PrimaryTerminalIdentity(sdk_run_id, raw_id, raw_hash, row["terminal_state"],
                                   row["terminal_receipt_id"], row["receipt_hash"], observed is None,
                                   None if observed is None else observed[0]["evidence_id"])
