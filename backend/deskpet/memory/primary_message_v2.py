"""New tool-group message sources and explicit Host settlement attestations.

The observer supplies verified public SDK facts before committing a new terminal.
Legacy terminals are never backfilled; readers only verify persisted children.
"""
from __future__ import annotations

import uuid
import simple_harness as h
from deskpet.task_scope.protocol import canonical_hash

CONTRACT = "primary-message-v2"


def representable(messages, facts):
    if not isinstance(messages, (list, tuple)) or not 3 <= len(messages) <= 256:
        return False
    if messages[0].get("role") != "user" or set(messages[0]) != {"role", "content"}:
        return False
    tools = []
    for ordinal, message in enumerate(messages, 1):
        role = message.get("role")
        keys = {"role", "content", "call_id", "name"} if role == "tool" else {"role", "content"}
        if (set(message) != keys or not isinstance(message["content"], str)
                or (ordinal > 1 and role not in {"assistant", "tool"})):
            return False
        if role == "tool":
            tools.append(ordinal)
    if not tools or not isinstance(facts, (tuple, list)) or len(facts) != len(tools):
        return False
    if [fact.get("item_ordinal") for fact in facts] != tools:
        return False
    for fact in facts:
        ordinal, parent = fact["item_ordinal"], fact.get("parent_item_ordinal")
        if (type(ordinal) is not int or type(parent) is not int or not 1 < parent < ordinal
                or messages[parent - 1]["role"] != "assistant"
                or fact.get("raw_call_id") != messages[ordinal - 1]["call_id"]
                or fact.get("tool_name") != messages[ordinal - 1]["name"]
                or fact.get("state") not in {"succeeded", "partial", "failed", "rejected"}):
            return False
    return len({fact.get("effect_id") for fact in facts}) == len(facts)


def pairs(terminal, terminal_receipt, user, *, host_run_id):
    terminal_receipt.verify(terminal)
    payload = terminal.to_json()["sanitized_payload"]
    messages, facts = payload["messages"], payload["tool_causal_sources"]
    if (payload["message_source_contract"] != CONTRACT or payload["terminal_state"] != "COMPLETED"
            or not representable(messages, facts)
            or messages[0]["content"] != user.sanitized_payload.get("text")
            or user.subject != terminal.subject
            or any(fact["sdk_run_id"] != terminal.run_id for fact in facts)):
        raise RuntimeError("primary_message_v2_source_mismatch")
    tools = {fact["item_ordinal"]: fact for fact in facts}
    refs = (h.EvidenceRef(user.evidence_id, user.envelope_hash, 1),
            h.EvidenceRef(terminal.evidence_id, terminal.envelope_hash, 2))
    result = []
    for ordinal, message in enumerate(messages[1:], 2):
        source = dict(terminal_evidence_id=terminal.evidence_id,
            terminal_envelope_hash=terminal.envelope_hash, sdk_event_id=payload["sdk_event_id"],
            sdk_event_hash=payload["sdk_event_hash"], terminal_json_pointer=f"/messages/{ordinal-1}",
            message=message)
        if ordinal in tools:
            attestation = dict(schema_version=1, kind="host-tool-terminal/v1",
                terminal_evidence_id=terminal.evidence_id, terminal_envelope_hash=terminal.envelope_hash,
                item_ordinal=ordinal, source=tools[ordinal])
            digest = canonical_hash({"domain": "host-tool-terminal/v1", "payload": attestation})
            source["tool_terminal_attestation"] = dict(payload=attestation,
                receipt_id="host-tool-terminal:" + digest, receipt_hash=digest)
        body = dict(schema_version=2, kind="primary_message", host_run_id=host_run_id,
                    sdk_run_id=terminal.run_id, transcript_ordinal=ordinal, source=source)
        evidence_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"primary-message-v2:{terminal.run_id}:{ordinal}"))
        envelope = h.SanitizedEvidenceEnvelope(evidence_id=evidence_id, run_id=terminal.run_id,
            subject=terminal.subject,
            source_kind=h.EvidenceSourceKind.TOOL_RESULT if ordinal in tools else h.EvidenceSourceKind.ASSISTANT_MESSAGE,
            source_ref=f"primary-message-v2:{terminal.run_id}:{ordinal}", source_hash=canonical_hash(source),
            sanitized_payload=body, sanitized_hash=canonical_hash(body),
            filter_policy_version=terminal.filter_policy_version, removed_spans=(),
            disclosure_context=terminal.disclosure_context, evidence_refs=refs)
        receipt = h.SanitizedEvidenceReceipt(receipt_id=f"primary-message-v2-receipt:{evidence_id}",
            run_id=envelope.run_id, subject=envelope.subject, evidence_id=evidence_id,
            envelope_hash=envelope.envelope_hash, source_hash=envelope.source_hash,
            sanitized_hash=envelope.sanitized_hash, filter_policy_version=envelope.filter_policy_version,
            accepted=True, reason_codes=(h.EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
            disclosure_context=envelope.disclosure_context, evidence_refs=refs,
            admitted_at=terminal_receipt.admitted_at)
        result.append((envelope, receipt))
    return tuple(result)


async def verify(db, *, primary_ref, host_run_id, terminal, terminal_receipt, user):
    from deskpet.memory.primary_visibility import read_evidence_pair, PrimaryVisibilityError
    expected = pairs(terminal, terminal_receipt, user, host_run_id=host_run_id)
    for envelope, receipt in expected:
        try:
            actual = await read_evidence_pair(db=db, subject=user.subject,
                primary_ref=primary_ref, evidence_id=envelope.evidence_id)
        except PrimaryVisibilityError as exc:
            if exc.code == "primary_source_missing":
                raise RuntimeError("primary_message_v2_source_missing") from exc
            raise
        if actual != (envelope, receipt):
            raise RuntimeError("primary_message_v2_source_corrupt")
    return expected


def tool_link(envelope):
    payload = envelope.to_json()["sanitized_payload"]["source"]["tool_terminal_attestation"]
    source = payload["payload"]["source"]
    return h.ConversationToolCausalLink(tool_call_id=source["internal_call_id"],
        tool_name=source["tool_name"], parent_item_ordinal=source["parent_item_ordinal"],
        terminal_receipt_id=payload["receipt_id"], terminal_receipt_hash=payload["receipt_hash"])
