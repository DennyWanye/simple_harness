"""New tool-group message sources and explicit Host settlement attestations.

The observer supplies verified public SDK facts before committing a new terminal.
Legacy terminals are never backfilled; readers only verify persisted children.
"""
from __future__ import annotations

import json
import uuid
import simple_harness as h
from deskpet.task_scope.protocol import canonical_hash

CONTRACT = "primary-message-v2"

_DENIAL_KEYS = {"outcome", "value", "error_code", "public_message"}


def effectless_denial(content):
    """True for a tool item that was refused before any physical dispatch.

    ``EffectExecutor.execute`` records its "requested" audit fact before doing
    anything else, so a provider tool call with no audit head never reached the
    SDK: no effect exists for it and none ever will (Host Procedure/effect-gate/
    foreground denials, and the SDK's own authorization denials, all return
    ``EffectExecution(effect=None, ToolResult.rejected(...))``).

    Such an item stays permanently archived inside the terminal's ``messages``;
    see ``item_ordinals`` for why it is not one of the group's evidence items.
    Refusing the whole group instead (the old behaviour) lost the Run's Procedure
    observation and short indexing because one call was correctly denied.
    """
    if not isinstance(content, str):
        return False
    try:
        value = json.loads(content)
    except ValueError:
        return False
    return (isinstance(value, dict) and set(value) == _DENIAL_KEYS
            and value["outcome"] == "rejected" and value["value"] is None
            and isinstance(value["error_code"], str) and bool(value["error_code"]))


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
    if not tools or not isinstance(facts, (tuple, list)):
        return False
    # Attested ordinals are an ordered subset of the tool items. Every tool item
    # without a settled effect fact must be a pre-dispatch denial; anything else
    # (a tool result claiming an outcome no effect proves) stays unrepresentable.
    attested = [fact.get("item_ordinal") for fact in facts]
    if any(type(ordinal) is not int for ordinal in attested):
        return False
    if attested != sorted(set(attested)) or not set(attested) <= set(tools):
        return False
    if any(ordinal not in set(attested) and not effectless_denial(messages[ordinal - 1]["content"])
           for ordinal in tools):
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


def item_ordinals(messages, facts):
    """Transcript ordinals of the conversation items ``pairs`` produces, in order.

    A tool call denied before dispatch has no settled effect to attest, so it is
    not a tool observation at all: the denial was written by a Host gate, not by
    the tool, and ``EvidenceProvenance.TRUSTED_TOOL`` would misdescribe it. It
    stays permanently archived inside the terminal's ``messages`` and simply
    never becomes its own conversation evidence item (S1: evidence without valid
    causal metadata is kept but not indexed). Every other item is emitted, so a
    group whose calls all settled is byte-identical to before.
    """
    attested = {fact["item_ordinal"] for fact in facts}
    return tuple(ordinal for ordinal, message in enumerate(messages, 1)
                 if ordinal == 1 or message["role"] != "tool" or ordinal in attested)


def pairs(terminal, terminal_receipt, user, *, host_run_id):
    return _pairs(terminal, terminal_receipt, user, host_run_id=host_run_id, contract=CONTRACT, schema_version=2)


def _pairs(terminal, terminal_receipt, user, *, host_run_id, contract, schema_version):
    """Shared encoding; v2 public entry retains its exact original bytes."""
    terminal_receipt.verify(terminal)
    payload = terminal.to_json()["sanitized_payload"]
    messages, facts = payload["messages"], payload["tool_causal_sources"]
    if (payload["message_source_contract"] != contract or payload["terminal_state"] != "COMPLETED"
            or not representable(messages, facts)
            or messages[0]["content"] != user.sanitized_payload.get("text")
            or user.subject != terminal.subject
            or any(fact["sdk_run_id"] != terminal.run_id for fact in facts)):
        raise RuntimeError("primary_message_v2_source_mismatch")
    tools = {fact["item_ordinal"]: fact for fact in facts}
    refs = (h.EvidenceRef(user.evidence_id, user.envelope_hash, 1),
            h.EvidenceRef(terminal.evidence_id, terminal.envelope_hash, 2))
    result = []
    emitted = set(item_ordinals(messages, facts))
    for ordinal, message in enumerate(messages[1:], 2):
        if ordinal not in emitted:
            continue  # A pre-dispatch denial is archived, never conversation evidence.
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
        body = dict(schema_version=schema_version, kind="primary_message", host_run_id=host_run_id,
                    sdk_run_id=terminal.run_id, transcript_ordinal=ordinal, source=source)
        evidence_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{contract}:{terminal.run_id}:{ordinal}"))
        envelope = h.SanitizedEvidenceEnvelope(evidence_id=evidence_id, run_id=terminal.run_id,
            subject=terminal.subject,
            source_kind=h.EvidenceSourceKind.TOOL_RESULT if ordinal in tools else h.EvidenceSourceKind.ASSISTANT_MESSAGE,
            source_ref=f"{contract}:{terminal.run_id}:{ordinal}", source_hash=canonical_hash(source),
            sanitized_payload=body, sanitized_hash=canonical_hash(body),
            filter_policy_version=terminal.filter_policy_version, removed_spans=(),
            disclosure_context=terminal.disclosure_context, evidence_refs=refs)
        receipt = h.SanitizedEvidenceReceipt(receipt_id=f"{contract}-receipt:{evidence_id}",
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


def tool_link(envelope, group_ordinals=None):
    """The fact's parent is a transcript ordinal; the link needs a group ordinal.

    They differ only when the group dropped a pre-dispatch denial ahead of this
    item. A parent is always an assistant message and is therefore always part
    of the group, so the lookup never misses.
    """
    payload = envelope.to_json()["sanitized_payload"]["source"]["tool_terminal_attestation"]
    source = payload["payload"]["source"]
    parent = source["parent_item_ordinal"]
    return h.ConversationToolCausalLink(tool_call_id=source["internal_call_id"],
        tool_name=source["tool_name"],
        parent_item_ordinal=parent if group_ordinals is None else group_ordinals[parent],
        terminal_receipt_id=payload["receipt_id"], terminal_receipt_hash=payload["receipt_hash"])
