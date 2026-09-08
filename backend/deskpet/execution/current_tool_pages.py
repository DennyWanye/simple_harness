"""Current Run pages: settled public effects, never fabricated terminal S1."""
from __future__ import annotations

import json
from collections.abc import Mapping

import aiosqlite

from deskpet.execution.primary_context_pages import (
    PrimaryContextPageUnavailable, _excerpt, _sha,
)
from deskpet.memory.primary_tool_causality import PrimaryToolCausalityUnavailable
from deskpet.task_scope.protocol import canonical_hash, canonical_json, redact_credential_shapes
from deskpet.sdk_adapters.causal_groups import DEFAULT_LARGE_RESULT_BYTES

PREFIX = "primary-effect-page:v1:"
MARKER = "primary_settled_effect_v1"
# These values contain executable context/recall attestations. Retain their
# complete public carriers under the original budget; never turn them into an
# empty typed attestation or a generic content summary.
CONTROL_TOOLS = frozenset({"context_route", "context_page_in", "task_scope_search", "task_scope_update"})
# HM-AC-6 same-Run accumulation bound. History trimming only bounds *history*:
# the current Run's own settled tool results all live in the single open causal
# group, which is never trimmed, so 25 provider turns of small results grow the
# request without limit (native HM-TO-A6 evidence: 29 same-Run tool messages,
# 68 KiB, every one under the 16 KiB per-result threshold). Once this Run's own
# non-control tool bodies exceed this share of effective_input_budget, the
# OLDEST settled bodies travel as the same content-addressed
# primary_settled_effect_v1 summary + context_page_in reference the >16 KiB rule
# already produces, so the model can page any of them back byte-exactly.
CURRENT_TOOL_BUDGET_DIVISOR = 4


def _require(condition, code):
    if not condition:
        raise PrimaryContextPageUnavailable("primary_effect_page_" + code)


def read_effect_facts(uow, run_id, effect_id):
    """Public bounded audit/records establish the actual parent, not an ID recipe."""
    from simple_harness import EffectId, RunId, thaw_json
    from simple_harness.execution.audit import audit_hash, audit_reference
    from simple_harness.execution.provider_invocations import (
        provider_response_from_json, provider_request_from_json, provider_request_fingerprint,
    )
    effect = uow.read_effect(EffectId(effect_id))
    _require(effect is not None and effect.run_id.value == run_id, "effect_missing")
    snapshot = uow.read_run_operation_audit(RunId(run_id), limit=4096)
    _require(snapshot.run_id == run_id and not snapshot.truncated, "audit_incomplete")
    heads = [op for op in snapshot.operations if op.record_type == "head"]
    matches = [op for op in heads if op.kind == "effect"
               and op.effect_id == audit_reference("effect", effect_id)]
    _require(len(matches) == 1, "effect_head_ambiguous")
    head = matches[0]
    _require(head.source_version == effect.version and head.state == effect.state.value
             and head.request_hash == effect.request_hash
             and head.call_id == audit_reference("call", effect.call_id.value)
             and head.call_ordinal == effect.call_ordinal, "effect_head_mismatch")
    parents = [op for op in heads if op.kind == "provider"
               and op.provider_invocation_id == head.provider_invocation_id]
    _require(len(parents) == 1, "parent_ambiguous")
    parent = parents[0]
    found, after = {}, 0
    for _ in range(32):
        receipts = uow.list_provider_projection_receipts(after_sequence=after, limit=256)
        for receipt in receipts:
            _require(receipt.sequence > after, "projection_order")
            after = receipt.sequence
            if (receipt.run_id != run_id or
                    audit_reference("provider", receipt.invocation_id) != head.provider_invocation_id):
                continue
            _require(audit_hash(thaw_json(receipt.payload)) == receipt.payload_hash, "projection_hash")
            invocation = uow.read_provider_invocation(receipt.invocation_id)
            _require(invocation is not None and invocation.run_id.value == run_id, "parent_missing")
            if receipt.invocation_version == invocation.version:
                found[invocation.invocation_id] = invocation
        if len(receipts) < 256:
            break
    else:
        raise PrimaryContextPageUnavailable("primary_effect_page_projection_limit")
    _require(len(found) == 1, "parent_unverifiable")
    invocation = next(iter(found.values()))
    _require(invocation.state.value == parent.state == "succeeded"
             and invocation.version == parent.source_version
             and invocation.response_json is not None and invocation.request_json is not None
             and parent.request_id == audit_reference("request", invocation.request_id.value), "parent_not_settled")
    response_json = thaw_json(invocation.response_json)
    _require(audit_hash(response_json) == parent.result_hash, "parent_result_hash")
    response = provider_response_from_json(response_json)
    _require(0 <= effect.call_ordinal < len(response.tool_calls), "parent_call_missing")
    call = response.tool_calls[effect.call_ordinal]
    _require(call.call_id.value == effect.raw_call_id and call.name == effect.tool_name
             and thaw_json(call.arguments) == thaw_json(effect.arguments), "parent_call_mismatch")
    request = provider_request_from_json(invocation.request_id, thaw_json(invocation.request_json))
    _require(provider_request_fingerprint(request) == invocation.request_fingerprint, "parent_request_hash")
    if effect.result is not None:
        result = effect.result
        raw = dict(call_id=result.call_id.value, outcome=result.outcome.value,
                   value=thaw_json(result.value), error_code=result.error_code,
                   public_message=result.public_message, retryable=result.retryable)
        _require(result.call_id == effect.call_id and audit_hash(raw) == head.result_hash
                 and audit_hash(effect.evidence_ref) == head.evidence_ref_hash, "result_hash")
    return effect, head, invocation, request


def source_content(facts, *, min_bytes=DEFAULT_LARGE_RESULT_BYTES):
    """Rebuild one settled effect's exact public tool body plus its descriptor.

    ``min_bytes`` is the *policy* gate that selects which settled results the
    >16 KiB rule may page; it is not an integrity check. Integrity is the
    descriptor: it is derived from public audit/effect facts only, and the
    retained summary must equal ``summary(descriptor, content)`` byte for byte.
    The same-Run accumulation bound pages smaller settled bodies, and
    ``verify_request`` re-derives whatever the request actually retained, so
    both pass ``min_bytes=0``.
    """
    from simple_harness import thaw_json
    from simple_harness.execution.audit import audit_hash
    effect, head, parent, _ = facts
    _require(effect.terminal and effect.state.value == "succeeded" and effect.result is not None
             and effect.result.outcome.value == "succeeded" and effect.evidence_ref
             and effect.tool_name not in CONTROL_TOOLS, "source_not_pageable")
    result = effect.result
    # Exactly the SDK's public tool message fields, with the same existing Host
    # transcript redaction. Private/internal result fields never enter a page.
    content = redact_credential_shapes(canonical_json(dict(outcome=result.outcome.value,
        value=thaw_json(result.value), error_code=result.error_code,
        public_message=result.public_message)))[0]
    _require(len(content.encode()) > int(min_bytes), "source_not_large")
    descriptor = dict(run_id=effect.run_id.value, effect_id=effect.effect_id.value,
        effect_version=effect.version, result_hash=head.result_hash,
        provider_invocation_id=parent.invocation_id, provider_response_hash=audit_hash(thaw_json(parent.response_json)),
        tool_name=effect.tool_name, raw_call_id=effect.raw_call_id,
        content_hash=_sha(content), content_bytes=len(content.encode()))
    return descriptor, content


def reference(descriptor, offset=0):
    return PREFIX + canonical_hash(descriptor) + ":" + str(offset)


def summary(descriptor, content):
    return canonical_json(dict(kind=MARKER, source=descriptor, excerpt=_excerpt(content),
        reference_id=reference(descriptor), source_hash=descriptor["content_hash"], page_tool="context_page_in"))


def verify_request(stack, run_id, messages):
    """Rebuild every retained marker against public authority before use."""
    found = {}
    for message in messages:
        metadata = message.metadata
        if not isinstance(metadata, Mapping) or metadata.get("source") != MARKER:
            continue
        _require(message.role.value == "tool" and isinstance(message.content, str), "summary_shape")
        body = json.loads(message.content)
        claimed = body.get("source", {})
        _require(claimed.get("run_id") == run_id, "foreign_source")
        descriptor, content = source_content(
            stack.read_primary_effect_page_facts(run_id, claimed.get("effect_id")), min_bytes=0)
        _require(message.content == summary(descriptor, content)
                 and message.name == descriptor["tool_name"]
                 and message.call_id.value == descriptor["raw_call_id"], "summary_mismatch")
        found[canonical_hash(descriptor)] = descriptor, content
    return found


async def admitted_current_page(*, db, stack, run, sdk_run_id, page_effect, arguments):
    _require(isinstance(arguments, Mapping) and set(arguments) == {"reference_id", "source_hash"}
             and all(isinstance(v, str) for v in arguments.values()), "arguments")
    ref = arguments["reference_id"]
    try:
        digest, raw_offset = ref.removeprefix(PREFIX).split(":")
        offset = int(raw_offset)
    except ValueError as exc:
        raise PrimaryContextPageUnavailable("primary_effect_page_reference") from exc
    _require(ref.startswith(PREFIX) and str(offset) == raw_offset, "reference")
    actual, _, _, parent_request = stack.read_primary_effect_page_facts(sdk_run_id, page_effect.effect_id.value)
    _require(actual == page_effect and actual.tool_name == "context_page_in", "caller_mismatch")
    found = verify_request(stack, sdk_run_id, parent_request.messages)
    _require(digest in found, "not_admitted")
    descriptor, content = found[digest]
    _require(arguments["source_hash"] == descriptor["content_hash"], "hash_mismatch")
    # Immutable Host identities supply ordering, not result authority. The
    # source must precede this page; prefix dependency checks include its output.
    rows = await (await db.execute("SELECT * FROM primary_effect_identities WHERE sdk_run_id=? AND effect_id IN (?,?)",
        (sdk_run_id, descriptor["effect_id"], page_effect.effect_id.value))).fetchall()
    _require(len(rows) == 2, "order_missing")
    order = {}
    for row in rows:
        identity = dict(host_run_id=run["host_run_id"], sdk_run_id=sdk_run_id,
                        effect_id=row["effect_id"], tool_name=row["tool_name"])
        _require(row["identity_json"] == canonical_json(identity) and row["identity_hash"] == canonical_hash(identity), "order_binding")
        order[row["effect_id"]] = row["sequence"]
    _require(order[descriptor["effect_id"]] < order[page_effect.effect_id.value], "source_not_prior")
    page = _excerpt(content, offset)
    end = offset + len(page.encode())
    return dict(ok=True, kind="primary_current_tool_page_v1", reference_id=ref, source=descriptor,
        source_hash=descriptor["content_hash"], offset=offset, content=page, page_hash=_sha(page),
        next_reference_id=reference(descriptor, end) if end < descriptor["content_bytes"] else None)


def _settled_tool_tokens(messages):
    """Tokens this Run's own non-control settled tool bodies currently occupy.

    Only the current Run's results are physically ``role=tool`` here: settled
    history arrives as quoted ``role=user`` groups (``project_history_group``).
    """
    from deskpet.sdk_adapters.context_partitions import text_tokens
    return sum(text_tokens(m.content) for m in messages
               if m.role.value == "tool" and isinstance(m.content, str)
               and m.name not in CONTROL_TOOLS)


def current_tool_allowance(metadata, *, provider_turn_ordinal=0):
    """The current Run's share of the frozen effective input budget.

    Incident N: ``_settled_tool_tokens`` measures bodies with the uncalibrated
    ``text_tokens``, while ``_plan_turn_messages`` now judges the same bodies
    through the per-model calibration.  Divide the allowance by that same ratio
    so the two stay in proportion — otherwise a calibrated model could pass this
    bound and still fail the budget, i.e. fail closed where paging had room left.
    """
    from deskpet.sdk_adapters.context_authority import _resolve_model_id, _resolve_window_tokens
    from deskpet.sdk_adapters.context_partitions import (
        calibration_for_model, effective_input_budget, window_tokens_for,
    )
    mapping = metadata if isinstance(metadata, Mapping) else {}
    model_id = _resolve_model_id(mapping)
    # Same window resolution as _plan_turn_messages, so the two bounds cannot
    # disagree about which tier this Run is in.
    window = window_tokens_for(_resolve_window_tokens(mapping), model_id)
    ratio = calibration_for_model(model_id).ratio(provider_turn_ordinal)
    # Integer arithmetic end to end: a float divide could shift the bound by a
    # token purely from representation.
    scale = max(1, round(CURRENT_TOOL_BUDGET_DIVISOR * ratio * 1000))
    return effective_input_budget(window) * 1000 // scale


class CurrentToolProjector:
    def __init__(self, path, stack_getter):
        self.path, self.stack_getter = path, stack_getter

    async def __call__(self, request, messages):
        large = [m for m in messages if m.role.value == "tool"
                 and isinstance(m.content, str) and len(m.content.encode()) > DEFAULT_LARGE_RESULT_BYTES]
        settled = [m for m in messages if m.role.value == "tool"
                   and isinstance(m.content, str) and m.name not in CONTROL_TOOLS]
        if not large and not settled:
            return None
        pageable = [m for m in large if m.name not in CONTROL_TOOLS]
        stack = self.stack_getter()
        run_id = request.run_id.value
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT r.host_run_id,r.subject FROM foreground_runs r "
                "JOIN foreground_run_sdk_bindings b USING(host_run_id) "
                "WHERE b.sdk_run_id=?", (run_id,))).fetchall()
        start, _ = stack.read_primary_dependency_facts(run_id)
        admission = start.get("input", {})
        metadata = admission.get("context_metadata", {})
        if not rows and "visibility_dependencies" not in metadata:
            # This authority is also composed for non-primary SDK callers.
            # A trusted Host lookup plus absence of primary admission selects
            # their existing planner, not a fabricated primary source grant.
            return None
        allowance = current_tool_allowance(
            metadata,
            provider_turn_ordinal=int(getattr(request, "provider_turn_ordinal", 0) or 0),
        )
        over_bound = _settled_tool_tokens(messages) > allowance
        if not large and not over_bound:
            # Nothing to page: leave the existing generic planner untouched so
            # this turn's request bytes stay exactly what they were before.
            return None
        try:
            return await self._project(messages, stack=stack, run_id=run_id, rows=rows,
                admission=admission, metadata=metadata, pageable=pageable,
                over_bound=over_bound, allowance=allowance)
        except (PrimaryContextPageUnavailable, PrimaryToolCausalityUnavailable):
            if large:
                # A >16 KiB result must never travel raw: this turn already
                # reached here before the bound existed, so keep its original
                # fail-closed behaviour untouched.
                raise
            # Accumulation-bound-only turn — a path that used to return early.
            # Without public causal authority we cannot page anything, but the
            # turn ran fine before, so leave it exactly as it was;
            # _plan_turn_messages still fails closed on a genuine overflow.
            return None

    async def _project(self, messages, *, stack, run_id, rows, admission, metadata,
                       pageable, over_bound, allowance):
        from simple_harness.contracts.messages import Message
        from deskpet.sdk_adapters.composition import project_primary_transcript
        from deskpet.sdk_adapters.context_partitions import text_tokens

        _require(len(rows) == 1 and metadata.get("root_run_id") == rows[0]["host_run_id"], "host_run_missing")
        run = rows[0]
        current_text = admission.get("input", {}).get("text")
        admitted_users = [m.get("content") for m in admission.get("messages", ()) if m.get("role") == "user"]
        _require(isinstance(current_text, str) and current_text and admitted_users
                 and admitted_users[-1] == current_text
                 and admission.get("turn", {}).get("text") == current_text, "current_input_missing")
        if not pageable and not over_bound:
            return messages  # real primary control carriers keep complete bytes
        transcript = project_primary_transcript(messages, current_text=current_text)
        sources = await stack.read_primary_tool_causal_sources(db_path=self.path, host_run_id=run["host_run_id"],
            run_id=run_id, subject=run["subject"], current_text=current_text, messages=transcript)
        # The public causal reader verifies the complete suffix before ordinal
        # mapping. A repeated raw call ID alone never selects an effect.
        anchors = [i for i, m in enumerate(messages) if m.role.value == "user" and m.content == current_text]
        anchor = anchors[-1]
        indices = [i for i in range(anchor, len(messages)) if messages[i].role.value != "system"]
        output = list(messages)

        def replace(index, source, *, min_bytes):
            """Swap one settled body for its content-addressed page summary."""
            message = messages[index]
            descriptor, content = source_content(
                stack.read_primary_effect_page_facts(run_id, source["effect_id"]), min_bytes=min_bytes)
            _require(transcript[source["item_ordinal"] - 1]["content"] == content, "transcript_mismatch")
            body = summary(descriptor, content)
            if len(body.encode()) >= len(content.encode()):
                return 0  # a body smaller than its own summary saves nothing
            output[index] = Message(message.role, body, name=message.name,
                call_id=message.call_id, metadata={"source": MARKER})
            return text_tokens(content) - text_tokens(body)

        bounded = []
        for source in sources:
            index = indices[source["item_ordinal"] - 1]
            message = messages[index]
            if (message.role.value != "tool" or not isinstance(message.content, str)
                    or message.name in CONTROL_TOOLS):
                # Executable context/recall attestations keep their full bytes.
                continue
            if len(message.content.encode()) > DEFAULT_LARGE_RESULT_BYTES:
                replace(index, source, min_bytes=DEFAULT_LARGE_RESULT_BYTES)
            else:
                bounded.append((index, source))
        # Never the results of the newest provider turn: those are what the
        # in-flight tool calls just produced and the model is still acting on.
        # Only a settled *succeeded* effect has a pageable public body, so a
        # failed/rejected/partial result simply keeps its own bytes instead of
        # failing the whole Run.
        newest = max((source["provider_turn_ordinal"] for _, source in bounded), default=0)
        candidates = sorted(((index, source) for index, source in bounded
                             if source["provider_turn_ordinal"] < newest
                             and source["state"] == "succeeded"),
                            key=lambda item: item[0])
        carried = _settled_tool_tokens(output)
        for index, source in candidates:
            if carried <= allowance:
                break
            try:
                carried -= replace(index, source, min_bytes=0)
            except PrimaryContextPageUnavailable:
                continue  # this body is not pageable; it keeps its own bytes
        return tuple(output)
