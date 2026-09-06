"""Read exact tool causality from public SDK records, without issuing authority.

This is an internal source projection. It is not a ConversationToolCausalLink or
a tool terminal receipt, and does not by itself make a group indexable.
"""
from __future__ import annotations

from simple_harness import EffectId, RunId
from simple_harness.contracts import thaw_json, canonical_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.execution.audit import audit_hash, audit_reference
from simple_harness.execution.provider_invocations import provider_response_from_json


class PrimaryToolCausalityUnavailable(ValueError):
    pass


def _require(condition, code):
    if not condition:
        raise PrimaryToolCausalityUnavailable("primary_tool_" + code)


def read_tool_causal_sources(uow, run_id, *, current_text, transcript, project, effect_ids):
    """Bounded, exact public-record read for a completed ReAct transcript.

    Caller owns SDK lifetime. The bounded public audit supplies verified proposal/effect
    joins; public records supply original results. No SQL or private SDK helper.
    A source projection never authorizes replay, mutation or context disclosure.
    """
    if not any(item.get("role") == "tool" for item in transcript):
        return ()
    _require(type(effect_ids) is tuple and 0 < len(effect_ids) <= 256
             and len(set(effect_ids)) == len(effect_ids), "effect_identity_input_invalid")
    actual_effects = {audit_reference("effect", value): uow.read_effect(EffectId(value))
                      for value in effect_ids}
    # Public projection IDs originate in the SDK. Audit references are opaque;
    # compare via its public helper, never invert them or derive effect IDs.
    actual_providers, after = {}, 0
    for _ in range(32):
        receipts = uow.list_provider_projection_receipts(after_sequence=after, limit=256)
        for receipt in receipts:
            _require(receipt.sequence > after, "provider_projection_order")
            after = receipt.sequence
            if receipt.run_id != run_id:
                continue
            _require(audit_hash(thaw_json(receipt.payload)) == receipt.payload_hash,
                     "provider_projection_hash")
            invocation = uow.read_provider_invocation(receipt.invocation_id)
            _require(invocation is not None and invocation.run_id.value == run_id,
                     "provider_projection_identity")
            if receipt.invocation_version == invocation.version:
                actual_providers[audit_reference("provider", receipt.invocation_id)] = invocation
        if len(receipts) < 256:
            break
    else:
        raise PrimaryToolCausalityUnavailable("primary_tool_provider_projection_limit")
    # The public paged viewer creates a retained cache per opening. Background
    # source reads must not accumulate viewer caches: use its bounded snapshot
    # and refuse truncation rather than silently dropping causal operations.
    snapshot = uow.read_run_operation_audit(RunId(run_id), limit=4096)
    _require(snapshot.run_id == run_id and not snapshot.truncated,
             "audit_snapshot_incomplete")
    operations = [o for o in snapshot.operations
                  if o.record_type == "head" and o.kind in {"provider", "effect"}]
    _require(len(operations) <= 256, "record_limit")
    providers, effects = {}, {}
    prefix = run_id + ":provider-turn:"
    for op in operations:
        if op.kind == "effect":
            key = (op.provider_invocation_id, op.call_ordinal)
            _require(op.provider_invocation_id and key not in effects, "effect_parent_ambiguous")
            effects[key] = op
            continue
        invocation = actual_providers.get(op.provider_invocation_id)
        _require(invocation is not None
                 and audit_reference("request", invocation.request_id.value) == op.request_id
                 and invocation.request_id.value.startswith(prefix), "provider_turn_unbound")
        suffix = invocation.request_id.value[len(prefix):]
        _require(suffix.isdigit() and str(int(suffix)) == suffix and int(suffix) > 0,
                 "provider_turn_invalid")
        ordinal = int(suffix)
        _require(ordinal not in providers, "provider_turn_duplicate")
        providers[ordinal] = op
    _require(sorted(providers) == list(range(1, len(providers) + 1)), "provider_turn_gap")
    messages = [Message(MessageRole.USER, current_text)]
    sources, used = [], set()
    for turn, op in sorted(providers.items()):
        invocation = actual_providers[op.provider_invocation_id]
        _require(invocation is not None and invocation.run_id.value == run_id
                 and audit_reference("request", invocation.request_id.value) == op.request_id
                 and invocation.state.value == op.state == "succeeded"
                 and invocation.version == op.source_version
                 and invocation.response_json is not None, "provider_not_settled")
        raw = thaw_json(invocation.response_json)
        _require(audit_hash(raw) == op.result_hash, "provider_result_mismatch")
        response = provider_response_from_json(raw)
        messages.append(response.message)
        parent_ordinal = len(messages)
        for ordinal, call in enumerate(response.tool_calls):
            key = (op.provider_invocation_id, ordinal)
            head = effects.get(key)
            _require(head is not None, "effect_missing")
            effect = actual_effects.get(head.effect_id)
            _require(effect is not None and effect.run_id.value == run_id
                     and audit_reference("call", effect.call_id.value) == head.call_id
                     and effect.raw_call_id == call.call_id.value
                     and effect.turn_ordinal == turn and effect.call_ordinal == ordinal
                     and effect.tool_name == call.name and effect.version == head.source_version
                     and effect.request_hash == head.request_hash
                     and effect.state.value == head.state
                     and effect.state.value in {"succeeded", "partial", "failed", "rejected"}
                     and effect.evidence_ref and effect.result is not None,
                     "effect_not_bound_or_settled")
            result = effect.result
            _require(result.call_id == effect.call_id, "effect_result_call_mismatch")
            raw_result = dict(call_id=result.call_id.value, outcome=result.outcome.value,
                              value=thaw_json(result.value), error_code=result.error_code,
                              public_message=result.public_message, retryable=result.retryable)
            _require(audit_hash(raw_result) == head.result_hash
                     and audit_hash(effect.evidence_ref) == head.evidence_ref_hash,
                     "effect_result_mismatch")
            public = {k: raw_result[k] for k in ("outcome", "value", "error_code", "public_message")}
            messages.append(Message(MessageRole.TOOL, canonical_json(public), name=call.name, call_id=call.call_id))
            sources.append(dict(schema_version=1, kind="host-public-tool-causality/v1",
                sdk_run_id=run_id, provider_invocation_id=invocation.invocation_id,
                provider_response_hash=op.result_hash, provider_turn_ordinal=turn,
                call_ordinal=ordinal, raw_call_id=call.call_id.value, internal_call_id=effect.call_id.value,
                effect_id=effect.effect_id.value, effect_version=effect.version,
                state=effect.state.value, result_hash=head.result_hash,
                effect_evidence_ref=effect.evidence_ref, effect_evidence_ref_hash=head.evidence_ref_hash,
                tool_name=call.name, parent_item_ordinal=parent_ordinal, item_ordinal=len(messages)))
            used.add(key)
    _require(used == set(effects) and set(actual_effects) == {h.effect_id for h in effects.values()}, "unconsumed_effect")
    _require(project(tuple(messages), current_text=current_text) == transcript,
             "whole_transcript_mismatch")
    return tuple(sources)
